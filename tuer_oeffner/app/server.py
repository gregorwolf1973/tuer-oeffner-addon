"""Tueroeffner - Haustuer per Handy oeffnen.

Zwei Gesichter, ein Prozess:

- Die APP fuer Freunde (oeffentlich, z. B. https://tuer.biker633.org):
  Registrierung mit Name, Benutzername, Mail und Passwort. Die 2FA per TOTP
  ist Pflicht oder - je nach Option - fuer jeden selbst ein- und
  ausschaltbar, dazu optional ein Passkey. Geoeffnet wird nur, wenn der Admin den Zugang
  freigegeben hat - und nur in der Naehe der Haustuer, ausser der Zugang
  darf ausdruecklich "aus der Ferne" oeffnen.

- Die VERWALTUNG (nur ueber den Ingress von Home Assistant): Freigeben,
  Sperren, Fern-Recht, 2FA zuruecksetzen, Protokoll. Erkannt am Absender
  172.30.32.2 - das ist das Ingress-Gateway des Supervisors. Aus dem
  Internet ist die Verwaltung damit gar nicht erreichbar, auch nicht mit
  gestohlenem Passwort.

Die Tuer selbst schaltet Home Assistant: das Add-on ruft ueber die
Supervisor-API einen Dienst fuer die eingestellte Entitaet auf.
"""

import json
import logging
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from functools import wraps

import pyotp
import requests
import segno
from flask import Flask, abort, g, jsonify, request, send_from_directory, session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from werkzeug.security import check_password_hash, generate_password_hash

# ------------------------------------------------------------------
# Einstellungen (aus run.sh bzw. den Tests)
# ------------------------------------------------------------------

DATA_DIR = os.environ.get("DATA_DIR", "/data")
TUER_ENTITAET = os.environ.get("TUER_ENTITAET", "").strip()
EXTERNE_ADRESSE = os.environ.get("EXTERNE_ADRESSE", "").strip().rstrip("/")
RADIUS_METER = int(os.environ.get("RADIUS_METER", "200") or 200)
BENACHRICHTIGUNG = os.environ.get("BENACHRICHTIGUNG", "").strip().removeprefix("notify.")
SITZUNG_TAGE = int(os.environ.get("SITZUNG_TAGE", "30") or 30)
REGISTRIERUNG_OFFEN = os.environ.get("REGISTRIERUNG_OFFEN", "true").lower() == "true"
ZWEI_FAKTOR_PFLICHT = os.environ.get("ZWEI_FAKTOR_PFLICHT", "true").lower() == "true"
INGRESS_ADRESSEN = {a.strip() for a in os.environ.get("INGRESS_ADRESSEN", "172.30.32.2").split(",") if a.strip()}
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") == "1"
HA_API = os.environ.get("HA_API", "http://supervisor/core/api")
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
PORT = int(os.environ.get("PORT", "8097"))

# Nach so vielen Fehlversuchen ist das Konto eine Weile zu
KONTO_FEHLER_MAX = 5
KONTO_SPERRE_S = 15 * 60
# Je Absender (alle Konten zusammen), gegen Durchprobieren
IP_FEHLER_MAX = 20
IP_FENSTER_S = 15 * 60
# Zwischen zwei Oeffnungen - ein doppelter Wisch soll nicht zweimal schalten
ABKLINGZEIT_S = 8
# Die Ungenauigkeit des Standorts zaehlt hoechstens bis hierhin zugunsten
# des Benutzers. Sonst "oeffnete" ein Handy mit 5 km Ungenauigkeit ueberall.
GENAUIGKEIT_MAX_M = 100
PROTOKOLL_TAGE = 365
PASSWORT_MIN = 8
PASSWORT_MAX = 200

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "info").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("tuer")

HIER = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HIER, "static")
DB_PFAD = os.path.join(DATA_DIR, "tuer.db")

app = Flask(__name__, static_folder=None)


def geheimnis_laden():
    pfad = os.path.join(DATA_DIR, "secret.key")
    if os.path.exists(pfad):
        with open(pfad, "rb") as f:
            return f.read()
    schluessel = secrets.token_bytes(32)
    with open(pfad, "wb") as f:
        f.write(schluessel)
    os.chmod(pfad, 0o600)
    return schluessel


os.makedirs(DATA_DIR, exist_ok=True)
app.secret_key = geheimnis_laden()
app.config.update(
    SESSION_COOKIE_NAME="tuer",
    SESSION_COOKIE_HTTPONLY=True,
    # Strict: kein fremder Seitenaufruf bringt das Cookie mit. Zusammen mit
    # dem Pflicht-Kopf X-Tuer (siehe vor_jeder_anfrage) ist das der Schutz
    # gegen untergeschobene Anfragen.
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=SITZUNG_TAGE * 24 * 3600,
    MAX_CONTENT_LENGTH=64 * 1024,
)

# ------------------------------------------------------------------
# Datenbank
# ------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS benutzer (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    -- Zum Anmelden, alternativ zur Mailadresse. Bei Zugaengen aus 0.1.0 leer.
    benutzername TEXT UNIQUE,
    email TEXT NOT NULL UNIQUE,
    pw_hash TEXT NOT NULL,
    -- neu: 2FA noch nicht eingerichtet (nur bei 2FA-Pflicht) |
    -- wartet: auf Freigabe | aktiv | gesperrt
    status TEXT NOT NULL DEFAULT 'neu',
    fern INTEGER NOT NULL DEFAULT 0,
    -- 1 = bei der Anmeldung wird nach dem Passwort ein Code verlangt
    zwei_faktor INTEGER NOT NULL DEFAULT 0,
    totp_geheimnis TEXT,
    totp_letzter_schritt INTEGER NOT NULL DEFAULT 0,
    webauthn_id TEXT NOT NULL,
    sitzung_gen INTEGER NOT NULL DEFAULT 1,
    fehlversuche INTEGER NOT NULL DEFAULT 0,
    gesperrt_bis INTEGER NOT NULL DEFAULT 0,
    angelegt INTEGER NOT NULL,
    freigegeben INTEGER,
    zuletzt_geoeffnet INTEGER
);
CREATE TABLE IF NOT EXISTS passkeys (
    id INTEGER PRIMARY KEY,
    benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
    credential_id TEXT NOT NULL UNIQUE,
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    angelegt INTEGER NOT NULL,
    zuletzt INTEGER
);
CREATE TABLE IF NOT EXISTS protokoll (
    id INTEGER PRIMARY KEY,
    zeit INTEGER NOT NULL,
    benutzer_id INTEGER,
    name TEXT,
    aktion TEXT NOT NULL,
    detail TEXT,
    ip TEXT
);
CREATE INDEX IF NOT EXISTS protokoll_zeit ON protokoll(zeit);
"""


def verbinden():
    conn = sqlite3.connect(DB_PFAD, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def db():
    if "db" not in g:
        g.db = verbinden()
    return g.db


@app.teardown_appcontext
def db_schliessen(_fehler):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def db_einrichten():
    conn = verbinden()
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    # Datenbanken aus 0.1.0 nachruesten. Dort war die 2FA immer Pflicht:
    # wer sie eingerichtet hat (status nicht 'neu'), hat sie an.
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(benutzer)")}
    if "benutzername" not in spalten:
        conn.execute("ALTER TABLE benutzer ADD COLUMN benutzername TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS benutzer_benutzername ON benutzer(benutzername)")
    if "zwei_faktor" not in spalten:
        conn.execute("ALTER TABLE benutzer ADD COLUMN zwei_faktor INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE benutzer SET zwei_faktor = 1 WHERE status != 'neu'")
    conn.execute("DELETE FROM protokoll WHERE zeit < ?", (int(time.time()) - PROTOKOLL_TAGE * 86400,))
    conn.commit()
    conn.close()


db_einrichten()


def protokollieren(aktion, benutzer=None, detail="", name=None):
    db().execute(
        "INSERT INTO protokoll (zeit, benutzer_id, name, aktion, detail, ip) VALUES (?,?,?,?,?,?)",
        (int(time.time()), benutzer["id"] if benutzer else None,
         name or (benutzer["name"] if benutzer else None), aktion, detail[:300], absender_ip()),
    )
    db().commit()


# ------------------------------------------------------------------
# Home Assistant
# ------------------------------------------------------------------

# Welcher Dienst bei welcher Art von Entitaet die Tuer aufmacht. So passt
# die vorhandene Loesung, egal ob Schalter, Taster, Skript oder - wie beim
# bestehenden Webhook - eine Automation.
DIENSTE = {
    "switch": "turn_on",
    "input_boolean": "turn_on",
    "script": "turn_on",
    "button": "press",
    "input_button": "press",
    "lock": "unlock",
    "automation": "trigger",
}


def ha_aufruf(methode, pfad, daten=None):
    """Ein Aufruf an die REST-API von Home Assistant. In den Tests ersetzt."""
    antwort = requests.request(
        methode, f"{HA_API}{pfad}", json=daten, timeout=10,
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"},
    )
    antwort.raise_for_status()
    return antwort.json() if antwort.content else None


def tuer_schalten():
    domain = TUER_ENTITAET.split(".", 1)[0]
    dienst = DIENSTE.get(domain)
    if not dienst:
        raise RuntimeError(f"Entitaet {TUER_ENTITAET!r} ist nicht eingestellt oder kein bekannter Typ")
    ha_aufruf("POST", f"/services/{domain}/{dienst}", {"entity_id": TUER_ENTITAET})


def melden(text):
    """Push an den Admin ueber den notify-Dienst. Scheitert still - die
    Tuer haengt nie an der Benachrichtigung."""
    if not BENACHRICHTIGUNG:
        return
    try:
        ha_aufruf("POST", f"/services/notify/{BENACHRICHTIGUNG}", {"title": "Haustür", "message": text})
    except Exception as fehler:  # noqa: BLE001
        log.warning("Benachrichtigung fehlgeschlagen: %s", fehler)


_heim = {"lat": None, "lon": None}


def heimat():
    """Standort der Haustuer: aus den Optionen, sonst aus der
    Home-Assistant-Konfiguration (Einstellungen -> System -> Allgemein)."""
    if _heim["lat"] is None:
        lat = os.environ.get("BREITENGRAD", "").strip()
        lon = os.environ.get("LAENGENGRAD", "").strip()
        if lat and lon:
            _heim.update(lat=float(lat.replace(",", ".")), lon=float(lon.replace(",", ".")))
        else:
            try:
                konfig = ha_aufruf("GET", "/config")
                _heim.update(lat=float(konfig["latitude"]), lon=float(konfig["longitude"]))
            except Exception as fehler:  # noqa: BLE001
                log.error("Standort aus Home Assistant nicht lesbar: %s", fehler)
                return None
    return _heim["lat"], _heim["lon"]


def entfernung_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ------------------------------------------------------------------
# Anfragen, Absender, Bremsen
# ------------------------------------------------------------------


def absender_ip():
    # Ueber den Cloudflare-Tunnel steht der echte Absender im Kopf
    return request.headers.get("CF-Connecting-IP") or request.remote_addr or "?"


def ist_ingress():
    # Bewusst remote_addr und NICHT ein Kopf: den kann jeder setzen
    return request.remote_addr in INGRESS_ADRESSEN


_ip_fehler = {}
_ip_sperre = threading.Lock()


def ip_gebremst():
    jetzt = time.time()
    with _ip_sperre:
        liste = [t for t in _ip_fehler.get(absender_ip(), []) if t > jetzt - IP_FENSTER_S]
        _ip_fehler[absender_ip()] = liste
        return len(liste) >= IP_FEHLER_MAX


def ip_fehler():
    with _ip_sperre:
        _ip_fehler.setdefault(absender_ip(), []).append(time.time())


def fehler(text, code=400):
    return jsonify({"fehler": text}), code


@app.before_request
def vor_jeder_anfrage():
    # Jeder schreibende API-Aufruf muss den Kopf X-Tuer tragen. Ein fremdes
    # Formular oder Bild kann ihn nicht setzen, ein fremdes Skript nur nach
    # einer CORS-Vorabfrage, die hier nie erlaubt wird.
    if request.method == "POST" and request.path.startswith("/api/") and request.headers.get("X-Tuer") != "1":
        abort(403)


@app.after_request
def sicherheitskoepfe(antwort):
    antwort.headers["X-Content-Type-Options"] = "nosniff"
    antwort.headers["Referrer-Policy"] = "no-referrer"
    # 'self' bei frame-ancestors: unter dem Ingress zeigt Home Assistant die
    # Verwaltung in einem iframe derselben Herkunft.
    antwort.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
    )
    antwort.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
    if request.path.startswith("/api/"):
        antwort.headers["Cache-Control"] = "no-store"
    return antwort


# ------------------------------------------------------------------
# Sitzung
# ------------------------------------------------------------------


def benutzer_laden(benutzer_id):
    return db().execute("SELECT * FROM benutzer WHERE id = ?", (benutzer_id,)).fetchone()


def aktueller_benutzer():
    """Der Benutzer dieser Sitzung - oder None, wenn sie nicht (mehr) gilt.
    Sperren, 2FA-Ruecksetzen und Passwortwechsel erhoehen sitzung_gen und
    beenden damit jede bestehende Sitzung."""
    bid = session.get("uid")
    if not bid:
        return None
    b = benutzer_laden(bid)
    if not b or session.get("gen") != b["sitzung_gen"] or b["status"] == "gesperrt":
        session.clear()
        return None
    if ZWEI_FAKTOR_PFLICHT and not b["zwei_faktor"] and session.get("stufe") == "voll":
        # Die Pflicht wurde eingeschaltet, waehrend diese Sitzung lief:
        # erst die 2FA einrichten, dann geht es weiter
        session["stufe"] = "einrichten"
    return b


def sitzung_beginnen(b, stufe):
    session.clear()
    session.permanent = True
    session.update(uid=b["id"], gen=b["sitzung_gen"], stufe=stufe)


def braucht(stufe):
    def deko(f):
        @wraps(f)
        def innen(*args, **kwargs):
            b = aktueller_benutzer()
            if not b or session.get("stufe") != stufe:
                return fehler("Bitte zuerst anmelden.", 401)
            return f(b, *args, **kwargs)
        return innen
    return deko


def nur_ingress(f):
    @wraps(f)
    def innen(*args, **kwargs):
        if not ist_ingress():
            abort(404)
        return f(*args, **kwargs)
    return innen


# ------------------------------------------------------------------
# Seiten
# ------------------------------------------------------------------


@app.get("/")
def startseite():
    seite = "admin.html" if ist_ingress() else "index.html"
    antwort = send_from_directory(STATIC, seite)
    antwort.headers["Cache-Control"] = "no-cache"
    return antwort


@app.get("/static/<path:pfad>")
def statisch(pfad):
    return send_from_directory(STATIC, pfad)


@app.get("/sw.js")
def service_worker():
    # Von der Wurzel ausgeliefert, damit der Bereich die ganze App umfasst
    antwort = send_from_directory(STATIC, "sw.js")
    antwort.headers["Cache-Control"] = "no-cache"
    return antwort


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(STATIC, "manifest.webmanifest", mimetype="application/manifest+json")


# ------------------------------------------------------------------
# App: Status, Registrierung, Anmeldung
# ------------------------------------------------------------------

EMAIL_MUSTER = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Ohne @ - so ist bei der Anmeldung klar, ob Mail oder Benutzername gemeint ist
BENUTZERNAME_MUSTER = re.compile(r"^[a-z0-9._-]{3,30}$")


def passkey_moeglich():
    return EXTERNE_ADRESSE.startswith("https://")


def rp_id():
    return EXTERNE_ADRESSE.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]


@app.get("/api/status")
def api_status():
    b = aktueller_benutzer()
    daten = {
        "registrierung_offen": REGISTRIERUNG_OFFEN,
        "passkey_moeglich": passkey_moeglich(),
        "zwei_faktor_pflicht": ZWEI_FAKTOR_PFLICHT,
        "passwort_min": PASSWORT_MIN,
        "angemeldet": False,
    }
    if b:
        daten.update(
            angemeldet=True,
            stufe=session.get("stufe"),
            status=b["status"],
            name=b["name"],
            benutzername=b["benutzername"],
            email=b["email"],
            zwei_faktor=bool(b["zwei_faktor"]),
            fern=bool(b["fern"]),
            radius=RADIUS_METER,
        )
        if session.get("stufe") == "voll":
            daten["passkeys"] = [
                {"id": p["id"], "name": p["name"], "angelegt": p["angelegt"], "zuletzt": p["zuletzt"]}
                for p in db().execute(
                    "SELECT * FROM passkeys WHERE benutzer_id = ? ORDER BY angelegt", (b["id"],))
            ]
    return jsonify(daten)


def json_eingabe():
    return request.get_json(silent=True) or {}


@app.post("/api/registrieren")
def api_registrieren():
    if not REGISTRIERUNG_OFFEN:
        return fehler("Neue Registrierungen sind gerade geschlossen.", 403)
    if ip_gebremst():
        return fehler("Zu viele Versuche. Bitte später noch einmal.", 429)
    e = json_eingabe()
    name = str(e.get("name", "")).strip()[:60]
    benutzername = str(e.get("benutzername", "")).strip().lower()
    email = str(e.get("email", "")).strip().lower()[:200]
    passwort = str(e.get("passwort", ""))
    if not name:
        return fehler("Bitte einen Namen angeben.")
    if not BENUTZERNAME_MUSTER.match(benutzername):
        return fehler("Der Benutzername braucht 3 bis 30 Zeichen: Kleinbuchstaben, Ziffern, Punkt, - oder _.")
    if not EMAIL_MUSTER.match(email):
        return fehler("Das sieht nicht nach einer Mailadresse aus.")
    if (f := passwort_fehler(passwort)):
        return f
    # Jede Registrierung zaehlt fuer die Bremse - sonst liesse sich der
    # Admin mit Anfragen zuschuetten.
    ip_fehler()
    if db().execute("SELECT 1 FROM benutzer WHERE benutzername = ?", (benutzername,)).fetchone():
        return fehler("Diesen Benutzernamen gibt es schon. Bitte einen anderen wählen.")
    try:
        cur = db().execute(
            "INSERT INTO benutzer (name, benutzername, email, pw_hash, status, totp_geheimnis, webauthn_id,"
            " angelegt) VALUES (?,?,?,?,?,?,?,?)",
            (name, benutzername, email, generate_password_hash(passwort),
             "neu" if ZWEI_FAKTOR_PFLICHT else "wartet", pyotp.random_base32(),
             bytes_to_base64url(secrets.token_bytes(16)), int(time.time())),
        )
        db().commit()
    except sqlite3.IntegrityError:
        return fehler("Diese Mailadresse ist schon registriert. Bitte anmelden.")
    b = benutzer_laden(cur.lastrowid)
    protokollieren("registriert", b, f"{benutzername}, {email}")
    if ZWEI_FAKTOR_PFLICHT:
        sitzung_beginnen(b, "einrichten")
    else:
        sitzung_beginnen(b, "voll")
        melde_wartet(b)
    return jsonify({"ok": True})


def passwort_fehler(passwort):
    if len(passwort) < PASSWORT_MIN:
        return fehler(f"Das Passwort braucht mindestens {PASSWORT_MIN} Zeichen.")
    if len(passwort) > PASSWORT_MAX:
        return fehler("Das Passwort ist zu lang.")
    return None


def melde_wartet(b):
    melden(f"Neue Registrierung: {b['name']} ({b['email']}) wartet auf Freigabe.")


@app.get("/api/2fa/einrichten")
@braucht("einrichten")
def api_2fa_einrichten(b):
    return qr_daten(b)


def qr_daten(b):
    uri = pyotp.TOTP(b["totp_geheimnis"]).provisioning_uri(name=b["email"], issuer_name="Haustür")
    svg = segno.make(uri, error="m").svg_inline(scale=5, border=2)
    return jsonify({"uri": uri, "geheimnis": b["totp_geheimnis"], "svg": svg})


def totp_pruefen(b, code):
    """Prueft einen 6-stelligen Code, eine Stufe Uhrabweichung in jede
    Richtung. Ein schon benutzter Code gilt nicht noch einmal."""
    code = re.sub(r"\s", "", str(code or ""))
    if not re.fullmatch(r"\d{6}", code) or not b["totp_geheimnis"]:
        return False
    totp = pyotp.TOTP(b["totp_geheimnis"])
    jetzt = int(time.time())
    for versatz in (0, -1, 1):
        schritt = jetzt // 30 + versatz
        if schritt <= b["totp_letzter_schritt"]:
            continue
        if secrets.compare_digest(totp.at(schritt * 30), code):
            db().execute("UPDATE benutzer SET totp_letzter_schritt = ? WHERE id = ?", (schritt, b["id"]))
            db().commit()
            return True
    return False


def fehlversuch(b):
    ip_fehler()
    if not b:
        return
    n = b["fehlversuche"] + 1
    if n >= KONTO_FEHLER_MAX:
        db().execute("UPDATE benutzer SET fehlversuche = 0, gesperrt_bis = ? WHERE id = ?",
                     (int(time.time()) + KONTO_SPERRE_S, b["id"]))
        db().commit()
        protokollieren("konto_kurz_gesperrt", b, f"{KONTO_FEHLER_MAX} Fehlversuche")
    else:
        db().execute("UPDATE benutzer SET fehlversuche = ? WHERE id = ?", (n, b["id"]))
        db().commit()


def erfolg(b):
    db().execute("UPDATE benutzer SET fehlversuche = 0 WHERE id = ?", (b["id"],))
    db().commit()


def kurz_gesperrt(b):
    return b and b["gesperrt_bis"] > time.time()


@app.post("/api/2fa/bestaetigen")
@braucht("einrichten")
def api_2fa_bestaetigen(b):
    if ip_gebremst() or kurz_gesperrt(b):
        return fehler("Zu viele Versuche. Bitte später noch einmal.", 429)
    if not totp_pruefen(b, json_eingabe().get("code")):
        fehlversuch(b)
        return fehler("Der Code stimmt nicht. Uhrzeit am Handy prüfen und den aktuellen Code eingeben.")
    erfolg(b)
    db().execute("UPDATE benutzer SET zwei_faktor = 1 WHERE id = ?", (b["id"],))
    neu = db().execute("UPDATE benutzer SET status = 'wartet' WHERE id = ? AND status = 'neu'",
                       (b["id"],)).rowcount
    db().commit()
    b = benutzer_laden(b["id"])
    sitzung_beginnen(b, "voll")
    protokollieren("2fa_eingerichtet", b)
    if neu:
        melde_wartet(b)
    return jsonify({"ok": True})


# 2FA nachtraeglich ein- und ausschalten (unter "Konto")


@app.post("/api/2fa/einschalten/start")
@braucht("voll")
def api_2fa_einschalten_start(b):
    if b["zwei_faktor"]:
        return fehler("Die Zwei-Faktor-Anmeldung ist schon eingeschaltet.")
    # Jedes Mal ein frisches Geheimnis - es gilt erst nach dem Bestaetigen
    db().execute("UPDATE benutzer SET totp_geheimnis = ?, totp_letzter_schritt = 0 WHERE id = ?",
                 (pyotp.random_base32(), b["id"]))
    db().commit()
    return qr_daten(benutzer_laden(b["id"]))


@app.post("/api/2fa/einschalten")
@braucht("voll")
def api_2fa_einschalten(b):
    if b["zwei_faktor"]:
        return fehler("Die Zwei-Faktor-Anmeldung ist schon eingeschaltet.")
    if ip_gebremst() or kurz_gesperrt(b):
        return fehler("Zu viele Versuche. Bitte später noch einmal.", 429)
    if not totp_pruefen(b, json_eingabe().get("code")):
        fehlversuch(b)
        return fehler("Der Code stimmt nicht. Uhrzeit am Handy prüfen und den aktuellen Code eingeben.")
    erfolg(b)
    # Andere Geraete sind nur mit Passwort angemeldet - die fliegen raus
    db().execute("UPDATE benutzer SET zwei_faktor = 1, sitzung_gen = sitzung_gen + 1 WHERE id = ?", (b["id"],))
    db().commit()
    sitzung_beginnen(benutzer_laden(b["id"]), "voll")
    protokollieren("2fa_eingeschaltet", b)
    return jsonify({"ok": True})


@app.post("/api/2fa/ausschalten")
@braucht("voll")
def api_2fa_ausschalten(b):
    if ZWEI_FAKTOR_PFLICHT:
        return fehler("Die Zwei-Faktor-Anmeldung ist hier Pflicht.", 403)
    if not b["zwei_faktor"]:
        return fehler("Die Zwei-Faktor-Anmeldung ist schon aus.")
    if ip_gebremst() or kurz_gesperrt(b):
        return fehler("Zu viele Versuche. Bitte später noch einmal.", 429)
    e = json_eingabe()
    # Passwort UND Code: ein liegengelassenes, angemeldetes Handy allein
    # reicht nicht, um den Schutz abzuschalten
    if not check_password_hash(b["pw_hash"], str(e.get("passwort", ""))) or not totp_pruefen(b, e.get("code")):
        fehlversuch(b)
        return fehler("Passwort oder Code stimmen nicht.", 401)
    erfolg(b)
    db().execute("UPDATE benutzer SET zwei_faktor = 0, totp_geheimnis = ? WHERE id = ?",
                 (pyotp.random_base32(), b["id"]))
    db().commit()
    protokollieren("2fa_ausgeschaltet", b)
    melden(f"{b['name']} hat die Zwei-Faktor-Anmeldung ausgeschaltet.")
    return jsonify({"ok": True})


@app.post("/api/anmelden")
def api_anmelden():
    if ip_gebremst():
        return fehler("Zu viele Versuche. Bitte später noch einmal.", 429)
    e = json_eingabe()
    # Benutzername oder Mailadresse - "email" ist der Name aus 0.1.0
    kennung = str(e.get("kennung", e.get("email", ""))).strip().lower()
    spalte = "email" if "@" in kennung else "benutzername"
    b = db().execute(f"SELECT * FROM benutzer WHERE {spalte} = ?", (kennung,)).fetchone()
    if kurz_gesperrt(b):
        return fehler("Zu viele Fehlversuche. Das Konto ist für 15 Minuten gesperrt.", 429)
    if not b or not check_password_hash(b["pw_hash"], str(e.get("passwort", ""))):
        fehlversuch(b)
        return fehler("Benutzername/Mailadresse oder Passwort stimmen nicht.", 401)
    if b["status"] == "gesperrt":
        return fehler("Dieser Zugang ist gesperrt.", 403)
    erfolg(b)
    if b["status"] == "neu" and not ZWEI_FAKTOR_PFLICHT:
        # Unter der Pflicht registriert, aber die 2FA nie eingerichtet - und
        # inzwischen ist sie freiwillig: dann wartet der Zugang jetzt
        db().execute("UPDATE benutzer SET status = 'wartet' WHERE id = ?", (b["id"],))
        db().commit()
        b = benutzer_laden(b["id"])
        melde_wartet(b)
    if b["zwei_faktor"]:
        stufe = "pw"
    elif ZWEI_FAKTOR_PFLICHT:
        stufe = "einrichten"
    else:
        stufe = "voll"
    sitzung_beginnen(b, stufe)
    return jsonify({"ok": True})


@app.post("/api/anmelden/code")
@braucht("pw")
def api_anmelden_code(b):
    if ip_gebremst() or kurz_gesperrt(b):
        return fehler("Zu viele Versuche. Bitte später noch einmal.", 429)
    if not totp_pruefen(b, json_eingabe().get("code")):
        fehlversuch(b)
        return fehler("Der Code stimmt nicht.", 401)
    erfolg(b)
    sitzung_beginnen(b, "voll")
    return jsonify({"ok": True})


@app.post("/api/abmelden")
def api_abmelden():
    session.clear()
    return jsonify({"ok": True})


@app.post("/api/passwort")
@braucht("voll")
def api_passwort(b):
    e = json_eingabe()
    if not check_password_hash(b["pw_hash"], str(e.get("alt", ""))):
        fehlversuch(b)
        return fehler("Das bisherige Passwort stimmt nicht.", 401)
    neu = str(e.get("neu", ""))
    if (f := passwort_fehler(neu)):
        return f
    # Neue Generation: alle anderen Geraete sind damit abgemeldet
    db().execute("UPDATE benutzer SET pw_hash = ?, sitzung_gen = sitzung_gen + 1 WHERE id = ?",
                 (generate_password_hash(neu), b["id"]))
    db().commit()
    sitzung_beginnen(benutzer_laden(b["id"]), "voll")
    protokollieren("passwort_geaendert", b)
    return jsonify({"ok": True})


# ------------------------------------------------------------------
# Passkeys
# ------------------------------------------------------------------


def passkey_vorbedingung():
    if not passkey_moeglich():
        return fehler("Passkeys brauchen die externe https-Adresse in den Add-on-Optionen.", 400)
    return None


@app.post("/api/passkey/registrieren/start")
@braucht("voll")
def api_passkey_reg_start(b):
    if (f := passkey_vorbedingung()):
        return f
    vorhandene = db().execute("SELECT credential_id FROM passkeys WHERE benutzer_id = ?", (b["id"],)).fetchall()
    optionen = generate_registration_options(
        rp_id=rp_id(),
        rp_name="Haustür",
        user_id=base64url_to_bytes(b["webauthn_id"]),
        user_name=b["benutzername"] or b["email"],
        user_display_name=b["name"],
        # resident key: der Passkey kennt seinen Benutzer, die Anmeldung
        # braucht dann weder Mail noch Passwort
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p["credential_id"]))
                             for p in vorhandene],
    )
    session["wa_challenge"] = bytes_to_base64url(optionen.challenge)
    return app.response_class(options_to_json(optionen), mimetype="application/json")


@app.post("/api/passkey/registrieren/ende")
@braucht("voll")
def api_passkey_reg_ende(b):
    if (f := passkey_vorbedingung()):
        return f
    challenge = session.pop("wa_challenge", None)
    e = json_eingabe()
    if not challenge:
        return fehler("Bitte noch einmal von vorn.")
    try:
        geprueft = verify_registration_response(
            credential=e.get("credential"),
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=rp_id(),
            expected_origin=EXTERNE_ADRESSE,
            require_user_verification=True,
        )
    except Exception as fehl:  # noqa: BLE001
        log.info("Passkey-Registrierung abgelehnt: %s", fehl)
        return fehler("Der Passkey konnte nicht geprüft werden.")
    name = str(e.get("name", "")).strip()[:60] or "Passkey"
    db().execute(
        "INSERT INTO passkeys (benutzer_id, credential_id, public_key, sign_count, name, angelegt)"
        " VALUES (?,?,?,?,?,?)",
        (b["id"], bytes_to_base64url(geprueft.credential_id), geprueft.credential_public_key,
         geprueft.sign_count, name, int(time.time())),
    )
    db().commit()
    protokollieren("passkey_angelegt", b, name)
    return jsonify({"ok": True})


@app.post("/api/passkey/anmelden/start")
def api_passkey_anm_start():
    if (f := passkey_vorbedingung()):
        return f
    if ip_gebremst():
        return fehler("Zu viele Versuche. Bitte später noch einmal.", 429)
    optionen = generate_authentication_options(
        rp_id=rp_id(), user_verification=UserVerificationRequirement.REQUIRED)
    session.clear()
    session["wa_challenge"] = bytes_to_base64url(optionen.challenge)
    return app.response_class(options_to_json(optionen), mimetype="application/json")


@app.post("/api/passkey/anmelden/ende")
def api_passkey_anm_ende():
    if (f := passkey_vorbedingung()):
        return f
    if ip_gebremst():
        return fehler("Zu viele Versuche. Bitte später noch einmal.", 429)
    challenge = session.pop("wa_challenge", None)
    cred = json_eingabe().get("credential") or {}
    if not challenge or not isinstance(cred, dict):
        return fehler("Bitte noch einmal von vorn.")
    p = db().execute("SELECT * FROM passkeys WHERE credential_id = ?", (str(cred.get("id", "")),)).fetchone()
    if not p:
        ip_fehler()
        return fehler("Diesen Passkey kenne ich nicht.", 401)
    b = benutzer_laden(p["benutzer_id"])
    if kurz_gesperrt(b):
        return fehler("Zu viele Fehlversuche. Das Konto ist für 15 Minuten gesperrt.", 429)
    try:
        geprueft = verify_authentication_response(
            credential=cred,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=rp_id(),
            expected_origin=EXTERNE_ADRESSE,
            credential_public_key=p["public_key"],
            credential_current_sign_count=p["sign_count"],
            require_user_verification=True,
        )
    except Exception as fehl:  # noqa: BLE001
        log.info("Passkey-Anmeldung abgelehnt: %s", fehl)
        fehlversuch(b)
        return fehler("Der Passkey konnte nicht geprüft werden.", 401)
    if b["status"] == "gesperrt":
        return fehler("Dieser Zugang ist gesperrt.", 403)
    db().execute("UPDATE passkeys SET sign_count = ?, zuletzt = ? WHERE id = ?",
                 (geprueft.new_sign_count, int(time.time()), p["id"]))
    db().commit()
    erfolg(b)
    sitzung_beginnen(b, "voll")
    return jsonify({"ok": True})


@app.post("/api/passkey/loeschen")
@braucht("voll")
def api_passkey_loeschen(b):
    pid = json_eingabe().get("id")
    db().execute("DELETE FROM passkeys WHERE id = ? AND benutzer_id = ?", (pid, b["id"]))
    db().commit()
    protokollieren("passkey_geloescht", b)
    return jsonify({"ok": True})


# ------------------------------------------------------------------
# Oeffnen
# ------------------------------------------------------------------

_oeffnen_sperre = threading.Lock()
_zuletzt_geoeffnet = {"t": 0.0}


def oeffnen_ausfuehren(benutzer, name, detail):
    """Schaltet die Tuer - hoechstens einmal je Abklingzeit, egal von wem."""
    with _oeffnen_sperre:
        if time.time() - _zuletzt_geoeffnet["t"] < ABKLINGZEIT_S:
            return fehler("Die Tür wurde gerade erst geöffnet.", 429)
        try:
            tuer_schalten()
        except Exception as fehl:  # noqa: BLE001
            log.error("Tuer nicht geschaltet: %s", fehl)
            protokollieren("fehler", benutzer, str(fehl), name=name)
            return fehler("Die Tür konnte nicht geöffnet werden. Bitte Bescheid geben.", 502)
        _zuletzt_geoeffnet["t"] = time.time()
    if benutzer:
        db().execute("UPDATE benutzer SET zuletzt_geoeffnet = ? WHERE id = ?", (int(time.time()), benutzer["id"]))
    protokollieren("geoeffnet", benutzer, detail, name=name)
    log.info("Tuer geoeffnet von %s (%s)", name, detail)
    return None


@app.post("/api/oeffnen")
@braucht("voll")
def api_oeffnen(b):
    if b["status"] != "aktiv":
        return fehler("Dein Zugang ist noch nicht freigegeben.", 403)
    e = json_eingabe()
    if e.get("modus") == "fern":
        if not b["fern"]:
            return fehler("Dein Zugang darf nicht aus der Ferne öffnen.", 403)
        detail = "aus der Ferne"
    else:
        heim = heimat()
        if not heim:
            return fehler("Der Standort der Haustür ist nicht eingestellt.", 503)
        try:
            lat, lon = float(e["lat"]), float(e["lon"])
            genauigkeit = max(0.0, float(e.get("genauigkeit") or 0))
        except (KeyError, TypeError, ValueError):
            return fehler("Ohne Standort geht es nicht. Bitte die Standortfreigabe erlauben.")
        abstand = entfernung_m(lat, lon, *heim)
        if abstand - min(genauigkeit, GENAUIGKEIT_MAX_M) > RADIUS_METER:
            protokollieren("zu_weit", b, f"{abstand:.0f} m")
            text = f"{abstand / 1000:.1f} km".replace(".", ",") if abstand >= 1000 else f"{abstand:.0f} m"
            return fehler(f"Du bist {text} entfernt. Öffnen geht nur bis {RADIUS_METER} m.", 403)
        detail = f"{abstand:.0f} m entfernt"
    if (f := oeffnen_ausfuehren(b, b["name"], detail)):
        return f
    melden(f"{b['name']} hat die Haustür geöffnet" + (" – aus der Ferne." if e.get("modus") == "fern" else "."))
    return jsonify({"ok": True})


# ------------------------------------------------------------------
# Verwaltung (nur Ingress)
# ------------------------------------------------------------------


@app.get("/api/admin/uebersicht")
@nur_ingress
def api_admin_uebersicht():
    benutzer = [
        {k: r[k] for k in ("id", "name", "benutzername", "email", "status", "fern", "zwei_faktor", "angelegt",
                           "freigegeben", "zuletzt_geoeffnet")}
        | {"passkeys": r["n_pk"]}
        for r in db().execute(
            "SELECT b.*, (SELECT COUNT(*) FROM passkeys p WHERE p.benutzer_id = b.id) AS n_pk"
            " FROM benutzer b ORDER BY CASE status WHEN 'wartet' THEN 0 WHEN 'aktiv' THEN 1"
            " WHEN 'neu' THEN 2 ELSE 3 END, name")
    ]
    eintraege = [dict(r) for r in db().execute(
        "SELECT zeit, name, aktion, detail, ip FROM protokoll ORDER BY id DESC LIMIT 200")]
    heim = heimat()
    return jsonify({
        "benutzer": benutzer,
        "protokoll": eintraege,
        "einstellungen": {
            "tuer_entitaet": TUER_ENTITAET,
            "entitaet_typ_ok": TUER_ENTITAET.split(".", 1)[0] in DIENSTE,
            "externe_adresse": EXTERNE_ADRESSE,
            "radius_meter": RADIUS_METER,
            "benachrichtigung": BENACHRICHTIGUNG,
            "heim_bekannt": bool(heim),
            "registrierung_offen": REGISTRIERUNG_OFFEN,
            "zwei_faktor_pflicht": ZWEI_FAKTOR_PFLICHT,
        },
    })


AKTIONEN = {"freigeben", "sperren", "entsperren", "fern_an", "fern_aus", "2fa_zuruecksetzen", "loeschen"}


@app.post("/api/admin/benutzer/<int:bid>/<aktion>")
@nur_ingress
def api_admin_benutzer(bid, aktion):
    if aktion not in AKTIONEN:
        abort(404)
    b = benutzer_laden(bid)
    if not b:
        return fehler("Unbekannter Benutzer.", 404)
    jetzt = int(time.time())
    if aktion == "freigeben":
        if b["status"] != "wartet":
            return fehler("Freigeben geht erst, wenn die 2FA eingerichtet ist." if b["status"] == "neu"
                          else "Dieser Zugang wartet nicht auf Freigabe.")
        db().execute("UPDATE benutzer SET status = 'aktiv', freigegeben = ? WHERE id = ?", (jetzt, bid))
    elif aktion == "sperren":
        db().execute("UPDATE benutzer SET status = 'gesperrt', sitzung_gen = sitzung_gen + 1 WHERE id = ?", (bid,))
    elif aktion == "entsperren":
        db().execute("UPDATE benutzer SET status = 'aktiv', gesperrt_bis = 0, fehlversuche = 0 WHERE id = ?", (bid,))
    elif aktion in ("fern_an", "fern_aus"):
        db().execute("UPDATE benutzer SET fern = ? WHERE id = ?", (int(aktion == "fern_an"), bid))
    elif aktion == "2fa_zuruecksetzen":
        # Neues Geheimnis, 2FA aus, Passkeys weg, alle Sitzungen beendet - und
        # wieder Freigabe noetig, denn wer sich jetzt anmeldet, braucht nur
        # noch das Passwort.
        db().execute("DELETE FROM passkeys WHERE benutzer_id = ?", (bid,))
        db().execute(
            "UPDATE benutzer SET status = ?, zwei_faktor = 0, totp_geheimnis = ?, totp_letzter_schritt = 0,"
            " sitzung_gen = sitzung_gen + 1, fehlversuche = 0, gesperrt_bis = 0 WHERE id = ?",
            ("neu" if ZWEI_FAKTOR_PFLICHT else "wartet", pyotp.random_base32(), bid))
    elif aktion == "loeschen":
        db().execute("DELETE FROM benutzer WHERE id = ?", (bid,))
    db().commit()
    protokollieren(f"admin_{aktion}", None, f"{b['name']} ({b['email']})", name="Admin")
    return jsonify({"ok": True})


@app.post("/api/admin/oeffnen")
@nur_ingress
def api_admin_oeffnen():
    if (f := oeffnen_ausfuehren(None, "Admin", "Test aus der Verwaltung")):
        return f
    return jsonify({"ok": True})


@app.errorhandler(404)
def nicht_gefunden(_e):
    return fehler("Nicht gefunden.", 404)


@app.errorhandler(403)
def verboten(_e):
    return fehler("Nicht erlaubt.", 403)


if __name__ == "__main__":
    from waitress import serve

    log.info("Tueroeffner startet auf Port %s (Entitaet: %s, 2FA %s)", PORT, TUER_ENTITAET or "-",
             "Pflicht" if ZWEI_FAKTOR_PFLICHT else "freiwillig")
    if not TUER_ENTITAET:
        log.warning("Keine Tuer-Entitaet eingestellt - Oeffnen ist nicht moeglich")
    if not passkey_moeglich():
        log.warning("Keine externe https-Adresse eingestellt - Passkeys sind aus")
    serve(app, host="0.0.0.0", port=PORT, threads=8, ident=None)
