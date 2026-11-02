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

Dazu, fuer die Familie: Wer vom Admin das Recht "Familie" bekommt, sieht
in der App die Speichertemperatur mit einem Knopf, der z. B. die einmalige
Ladung der Waermepumpe ausloest, den Staubsauger (starten, zur Station) und
ein Livebild der Kamera an der Haustuer und den Pollenflug.

Und den Hausdienst: Der Admin plant in der Verwaltung, wer an welchem
Wochentag welche Aufgabe hat. Die Familie sieht in der App, wer heute dran
ist und wann sie selbst wieder dran ist, und beantragt Umplanungen oder eine
Pause. Antraege landen in der Glocke von Home Assistant.
"""

import base64
import datetime
import hashlib
import json
import logging
import math
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
from functools import wraps

import pyotp
import requests
import segno
import segno.helpers
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
WARMWASSER_ENTITAET = os.environ.get("WARMWASSER_ENTITAET", "").strip()
WARMWASSER_TEMPERATUR = os.environ.get("WARMWASSER_TEMPERATUR", "").strip()
WARMWASSER_LAEUFT = os.environ.get("WARMWASSER_LAEUFT", "").strip()
WARMWASSER_HEIZT = os.environ.get("WARMWASSER_HEIZT", "").strip()
WARMWASSER_SOLL = os.environ.get("WARMWASSER_SOLL", "").strip()
STAUBSAUGER_ENTITAET = os.environ.get("STAUBSAUGER_ENTITAET", "").strip()
STAUBSAUGER_BATTERIE = os.environ.get("STAUBSAUGER_BATTERIE", "").strip()
KAMERA_ENTITAET = os.environ.get("KAMERA_ENTITAET", "").strip()
POLLEN_PRAEFIX = os.environ.get("POLLEN_PRAEFIX", "").strip()
WLAN_NAME = os.environ.get("WLAN_NAME", "").strip()
WLAN_SCHLUESSEL = os.environ.get("WLAN_SCHLUESSEL", "")
WLAN_VERSCHLUESSELUNG = os.environ.get("WLAN_VERSCHLUESSELUNG", "WPA").strip() or "WPA"
# Hausdienst-Anrufe ueber das Add-on ha-sip
SIP_ADDON = os.environ.get("SIP_ADDON", "").strip()
SIP_KONTO = int(os.environ.get("SIP_KONTO", "2") or 2)
SIP_SERVER = os.environ.get("SIP_SERVER", "").strip()
# Unter diesem Namen schickt ha-sip "Taste 1 gedrueckt" zurueck
EIGENER_SLUG = os.environ.get("EIGENER_SLUG", "").strip() or socket.gethostname().replace("-", "_")
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
# Die Waermepumpe braucht einen Moment, bis die Ladung als "laeuft" sichtbar
# ist - so loest ein ungeduldiges Doppeltippen nicht zweimal aus
WARMWASSER_ABKLINGZEIT_S = 60
# Zieltemperatur, auf die der Knopf aufheizt, solange in der Verwaltung
# nichts anderes eingestellt ist
WARMWASSER_VORGABE_STANDARD = 50.0
STAUBSAUGER_ABKLINGZEIT_S = 5
# So lange gilt ein Kamerabild fuer alle - mehrere offene Apps fragen die
# Kamera damit trotzdem nur etwa einmal pro Sekunde
KAMERA_BILD_S = 0.8
# Die Ungenauigkeit des Standorts zaehlt hoechstens bis hierhin zugunsten
# des Benutzers. Sonst "oeffnete" ein Handy mit 5 km Ungenauigkeit ueberall.
GENAUIGKEIT_MAX_M = 100
PROTOKOLL_TAGE = 365
# Aeltere Standorte werden geloescht - wer so lange nichts sendet, ist nicht "gerade" dort
STANDORT_TAGE = 7
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
    -- 1 = Familie: Warmwasser und Staubsauger
    familie INTEGER NOT NULL DEFAULT 0,
    -- 1 = zeigt der Familie den eigenen Standort (selbst eingeschaltet)
    standort_teilen INTEGER NOT NULL DEFAULT 0,
    -- OwnTracks (Standort im Hintergrund): Pruefsumme des Passworts, letzte Meldung
    owntracks_hash TEXT,
    owntracks_zuletzt INTEGER,
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
-- Einstellungen aus der Verwaltung, z. B. die Warmwasser-Vorgabe
CREATE TABLE IF NOT EXISTS einstellungen (
    schluessel TEXT PRIMARY KEY,
    wert TEXT NOT NULL
);
-- Letzter freigegebener Standort je Familienmitglied
CREATE TABLE IF NOT EXISTS standorte (
    benutzer_id INTEGER PRIMARY KEY REFERENCES benutzer(id) ON DELETE CASCADE,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    genauigkeit REAL,
    zeit INTEGER NOT NULL,
    -- app (Browser, solange offen) | owntracks (im Hintergrund)
    quelle TEXT NOT NULL DEFAULT 'app'
);
-- Hausdienst: Aufgaben, Wochenplan (wiederholt sich jede Woche) und was an
-- einzelnen Tagen davon abweicht
-- Wer mitmacht - mit oder ohne Zugang zum Tueroeffner. Nur mit
-- verknuepftem Zugang sieht die Person ihren Plan in der App.
CREATE TABLE IF NOT EXISTS hd_personen (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    benutzer_id INTEGER UNIQUE REFERENCES benutzer(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS hd_aufgaben (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    -- So heisst es in der Ansage: "du hast Kuechendienst"
    ansage TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hd_plan (
    id INTEGER PRIMARY KEY,
    -- 0 = Montag ... 6 = Sonntag
    wochentag INTEGER NOT NULL,
    person_id INTEGER NOT NULL REFERENCES hd_personen(id) ON DELETE CASCADE,
    aufgabe_id INTEGER NOT NULL REFERENCES hd_aufgaben(id) ON DELETE CASCADE,
    UNIQUE (wochentag, person_id, aufgabe_id)
);
-- Mit plan_id: dieser Planeintrag an diesem Tag geht an person_id (NULL =
-- entfaellt). Ohne plan_id: ein zusaetzlicher Dienst nur an diesem Tag.
CREATE TABLE IF NOT EXISTS hd_abweichungen (
    id INTEGER PRIMARY KEY,
    datum TEXT NOT NULL,
    plan_id INTEGER REFERENCES hd_plan(id) ON DELETE CASCADE,
    aufgabe_id INTEGER NOT NULL REFERENCES hd_aufgaben(id) ON DELETE CASCADE,
    person_id INTEGER REFERENCES hd_personen(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS hd_abweichung_plan ON hd_abweichungen(datum, plan_id) WHERE plan_id IS NOT NULL;
-- In einer Pause entfallen die Dienste der Person
CREATE TABLE IF NOT EXISTS hd_pausen (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES hd_personen(id) ON DELETE CASCADE,
    von TEXT NOT NULL,
    bis TEXT NOT NULL
);
-- Erinnerungsanruf: Nummer und Uhrzeit je Person; tage = JSON
-- {"0": "06:15"} fuer eine andere Uhrzeit an einem Wochentag
CREATE TABLE IF NOT EXISTS hd_telefon (
    person_id INTEGER PRIMARY KEY REFERENCES hd_personen(id) ON DELETE CASCADE,
    nummer TEXT NOT NULL DEFAULT '',
    zeit TEXT NOT NULL DEFAULT '',
    tage TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS hd_anrufe (
    id INTEGER PRIMARY KEY,
    datum TEXT NOT NULL,
    person_id INTEGER NOT NULL REFERENCES hd_personen(id) ON DELETE CASCADE,
    -- 1 = zur eingestellten Zeit, 2 = 30 min spaeter ohne Taste 1, 0 = Testanruf
    versuch INTEGER NOT NULL,
    zeit INTEGER NOT NULL,
    kennung TEXT NOT NULL UNIQUE,
    -- Zeitpunkt von Taste 1
    bestaetigt INTEGER,
    fehler TEXT
);
CREATE TABLE IF NOT EXISTS hd_antraege (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES hd_personen(id) ON DELETE CASCADE,
    -- umplanen: an datum uebernimmt an_id die aufgabe_id | pause: ab datum fuer tage
    art TEXT NOT NULL,
    datum TEXT NOT NULL,
    aufgabe_id INTEGER REFERENCES hd_aufgaben(id) ON DELETE CASCADE,
    an_id INTEGER REFERENCES hd_personen(id) ON DELETE CASCADE,
    tage INTEGER,
    -- offen | genehmigt | abgelehnt | zurueckgezogen
    status TEXT NOT NULL DEFAULT 'offen',
    angelegt INTEGER NOT NULL,
    entschieden INTEGER
);
-- Tauschanfrage: von_id fragt an_id, ob sie am datum die aufgabe_id
-- uebernimmt. Antwort per Taste im Anruf oder in der App.
CREATE TABLE IF NOT EXISTS hd_anfragen (
    id INTEGER PRIMARY KEY,
    von_id INTEGER NOT NULL REFERENCES hd_personen(id) ON DELETE CASCADE,
    an_id INTEGER NOT NULL REFERENCES hd_personen(id) ON DELETE CASCADE,
    datum TEXT NOT NULL,
    aufgabe_id INTEGER NOT NULL REFERENCES hd_aufgaben(id) ON DELETE CASCADE,
    -- offen | ja | nein | zurueckgezogen
    status TEXT NOT NULL DEFAULT 'offen',
    angelegt INTEGER NOT NULL,
    entschieden INTEGER,
    -- Der Anruf bei an_id; mit der Kennung kommt die Taste zurueck
    angerufen INTEGER,
    kennung TEXT UNIQUE
);
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


HD_TABELLEN_ALT = ("hd_plan", "hd_abweichungen", "hd_pausen", "hd_telefon", "hd_anrufe", "hd_antraege")


def hd_umstellen(conn):
    """0.12.0 plante mit den Zugaengen. Jetzt gibt es eigene Personen; die
    Familien-Zugaenge werden zu Personen mit derselben Nummer, damit alle
    Eintraege passen."""
    conn.execute("PRAGMA foreign_keys = OFF")
    for t in HD_TABELLEN_ALT:
        conn.execute(f"ALTER TABLE {t} RENAME TO {t}_alt")
    conn.execute("DROP INDEX IF EXISTS hd_abweichung_plan")
    conn.executescript(SCHEMA)
    benutzt = set()
    for t in HD_TABELLEN_ALT:
        benutzt |= {r[0] for r in conn.execute(f"SELECT benutzer_id FROM {t}_alt")}
    benutzt |= {r[0] for r in conn.execute("SELECT an_id FROM hd_antraege_alt")}
    namen = set()
    for r in conn.execute("SELECT id, name, familie FROM benutzer ORDER BY id").fetchall():
        if r[2] or r[0] in benutzt:
            name = r[1] if r[1] not in namen else f"{r[1]} ({r[0]})"
            namen.add(name)
            conn.execute("INSERT INTO hd_personen (id, name, benutzer_id) VALUES (?, ?, ?)", (r[0], name, r[0]))
    for t in HD_TABELLEN_ALT:
        spalten = [r[1] for r in conn.execute(f"PRAGMA table_info({t}_alt)")]
        neu = ", ".join("person_id" if x == "benutzer_id" else x for x in spalten)
        conn.execute(f"INSERT INTO {t} ({neu}) SELECT {', '.join(spalten)} FROM {t}_alt")
    for t in reversed(HD_TABELLEN_ALT):
        conn.execute(f"DROP TABLE {t}_alt")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def db_einrichten():
    conn = verbinden()
    conn.execute("PRAGMA journal_mode = WAL")
    if "benutzer_id" in {r[1] for r in conn.execute("PRAGMA table_info(hd_plan)")}:
        hd_umstellen(conn)
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
    if "familie" not in spalten:
        conn.execute("ALTER TABLE benutzer ADD COLUMN familie INTEGER NOT NULL DEFAULT 0")
        # 0.3.0 kannte nur das Recht "warmwasser" - es wird zu "Familie"
        if "warmwasser" in spalten:
            conn.execute("UPDATE benutzer SET familie = warmwasser")
    if "standort_teilen" not in spalten:
        conn.execute("ALTER TABLE benutzer ADD COLUMN standort_teilen INTEGER NOT NULL DEFAULT 0")
    if "owntracks_hash" not in spalten:
        conn.execute("ALTER TABLE benutzer ADD COLUMN owntracks_hash TEXT")
        conn.execute("ALTER TABLE benutzer ADD COLUMN owntracks_zuletzt INTEGER")
    if "quelle" not in {r[1] for r in conn.execute("PRAGMA table_info(standorte)")}:
        conn.execute("ALTER TABLE standorte ADD COLUMN quelle TEXT NOT NULL DEFAULT 'app'")
    conn.execute("DELETE FROM standorte WHERE zeit < ?", (int(time.time()) - STANDORT_TAGE * 86400,))
    conn.execute("DELETE FROM protokoll WHERE zeit < ?", (int(time.time()) - PROTOKOLL_TAGE * 86400,))
    conn.commit()
    conn.close()


db_einrichten()


def einstellung_lesen(schluessel, standard=None):
    r = db().execute("SELECT wert FROM einstellungen WHERE schluessel = ?", (schluessel,)).fetchone()
    return r["wert"] if r else standard


def einstellung_setzen(schluessel, wert):
    db().execute("INSERT INTO einstellungen (schluessel, wert) VALUES (?, ?)"
                 " ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert", (schluessel, str(wert)))
    db().commit()


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


def ausloesen(entitaet):
    domain = entitaet.split(".", 1)[0]
    dienst = DIENSTE.get(domain)
    if not dienst:
        raise RuntimeError(f"Entitaet {entitaet!r} ist nicht eingestellt oder kein bekannter Typ")
    ha_aufruf("POST", f"/services/{domain}/{dienst}", {"entity_id": entitaet})


def ha_bild(entitaet):
    """Ein aktuelles JPEG der Kamera ueber Home Assistant. In den Tests ersetzt."""
    antwort = requests.get(
        f"{HA_API}/camera_proxy/{entitaet}", timeout=10,
        headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"},
    )
    antwort.raise_for_status()
    return antwort.content


def tuer_schalten():
    ausloesen(TUER_ENTITAET)


def zustand_lesen(entitaet):
    """Zustand und Attribute einer Entitaet - oder None, wenn nicht
    eingestellt oder nicht lesbar. Die Anzeige haengt nie daran."""
    if not entitaet:
        return None
    try:
        return ha_aufruf("GET", f"/states/{entitaet}")
    except Exception as fehler:  # noqa: BLE001
        log.warning("Zustand von %s nicht lesbar: %s", entitaet, fehler)
        return None


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
    # Die Kartenkacheln der Standort-Karte kommen von OpenStreetMap
    antwort.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; style-src 'self'; "
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
            familie=ist_familie(b),
            warmwasser=ist_familie(b) and bool(WARMWASSER_ENTITAET),
            staubsauger=ist_familie(b) and bool(STAUBSAUGER_ENTITAET),
            kamera=ist_familie(b) and bool(KAMERA_ENTITAET),
            pollen=ist_familie(b) and bool(POLLEN_PRAEFIX),
            standorte=ist_familie(b),
            hausdienst=ist_familie(b),
            standort_teilen=ist_familie(b) and bool(b["standort_teilen"]),
            owntracks=ist_familie(b) and bool(b["owntracks_hash"]),
            owntracks_zuletzt=b["owntracks_zuletzt"] if ist_familie(b) else None,
            owntracks_moeglich=EXTERNE_ADRESSE.startswith("https://"),
            wlan=b["status"] == "aktiv" and bool(WLAN_NAME),
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
# Familie: Warmwasser und Staubsauger
# ------------------------------------------------------------------

_warmwasser_sperre = threading.Lock()
_zuletzt_aufgeheizt = {"t": 0.0}


def ist_familie(b):
    return bool(b["familie"] and b["status"] == "aktiv")


def familie_fehler(b, entitaet):
    if not entitaet:
        return fehler("Das ist hier nicht eingerichtet.", 404)
    if not ist_familie(b):
        return fehler("Das dürfen nur Zugänge der Familie.", 403)
    return None


def zahl_lesen(entitaet):
    z = zustand_lesen(entitaet)
    try:
        return round(float(z.get("state")), 1) if z else None
    except (TypeError, ValueError):
        return None  # "unavailable"


def an_aus_lesen(entitaet):
    z = zustand_lesen(entitaet)
    return z["state"] == "on" if z and z.get("state") in ("on", "off") else None


def grad(wert):
    return f"{wert:g} °C".replace(".", ",")


def soll_grenzen(z):
    """Erlaubter Bereich der Zieltemperatur laut number-Entitaet."""
    a = (z or {}).get("attributes") or {}
    return a.get("min", 30), a.get("max", 60), a.get("step", 1)


def warmwasser_vorgabe(z_soll=None):
    """Zieltemperatur, auf die der Knopf aufheizt. Sie steht in der
    Verwaltung und geht erst beim Aufheizen an die Waermepumpe - nie vorher.
    Ohne number-Entitaet gibt es keine."""
    if not WARMWASSER_SOLL:
        return None
    try:
        wert = float(einstellung_lesen("warmwasser_vorgabe", WARMWASSER_VORGABE_STANDARD))
    except ValueError:
        wert = WARMWASSER_VORGABE_STANDARD
    unten, oben, _ = soll_grenzen(z_soll)
    return min(max(wert, unten), oben)


def warmwasser_ziel_setzen(wert):
    """Stellt die Waermepumpe auf die Vorgabe - nur aus dem Knopf heraus.
    Gibt (vorher, geaendert) zurueck. Steht sie schon richtig, unterbleibt der
    Aufruf: er geht ueber die ViCare-Cloud, die Aufrufe je Tag begrenzt."""
    vorher = zahl_lesen(WARMWASSER_SOLL)
    if vorher == wert:
        return vorher, False
    ha_aufruf("POST", "/services/number/set_value", {"entity_id": WARMWASSER_SOLL, "value": wert})
    return vorher, True


def warmwasser_zustand():
    temperatur = einheit = None
    z = zustand_lesen(WARMWASSER_TEMPERATUR)
    if z:
        try:
            temperatur = round(float(z.get("state")), 1)
            einheit = (z.get("attributes") or {}).get("unit_of_measurement") or "°C"
        except (TypeError, ValueError):
            pass  # "unavailable" - dann eben keine Anzeige
    laeuft = an_aus_lesen(WARMWASSER_LAEUFT)
    # "Ladung an" heisst noch nicht, dass geheizt wird: ist das Wasser schon
    # warm genug, wartet die Waermepumpe. Geheizt wird, wenn dazu der
    # Kompressor laeuft.
    heizt = laeuft
    if laeuft and WARMWASSER_HEIZT:
        heizt = an_aus_lesen(WARMWASSER_HEIZT)
    return {
        "temperatur": temperatur, "einheit": einheit, "laeuft": laeuft, "heizt": heizt,
        # Das Ziel des Knopfs, nicht was gerade in der Waermepumpe steht
        "soll": warmwasser_vorgabe(zustand_lesen(WARMWASSER_SOLL)) if WARMWASSER_SOLL else None,
    }


@app.get("/api/warmwasser")
@braucht("voll")
def api_warmwasser(b):
    if (f := familie_fehler(b, WARMWASSER_ENTITAET)):
        return f
    return jsonify(warmwasser_zustand())


@app.post("/api/warmwasser/aufheizen")
@braucht("voll")
def api_warmwasser_aufheizen(b):
    if (f := familie_fehler(b, WARMWASSER_ENTITAET)):
        return f
    with _warmwasser_sperre:
        if time.time() - _zuletzt_aufgeheizt["t"] < WARMWASSER_ABKLINGZEIT_S:
            return fehler("Das Aufheizen wurde gerade erst gestartet.", 429)
        z = warmwasser_zustand()
        if z["laeuft"]:
            return fehler("Das Aufheizen ist schon angefordert.", 409)
        # Eine Ladung ueber dem Ziel bewirkt nichts - lieber gleich sagen
        if z["temperatur"] is not None and z["soll"] is not None and z["temperatur"] >= z["soll"]:
            return fehler(f"Das Wasser hat schon {grad(z['temperatur'])} – mehr als die vorgegebenen "
                          f"{grad(z['soll'])}. Aufheizen bringt gerade nichts.", 409)
        detail = ""
        if z["soll"] is not None:
            # Erst die Vorgabe setzen, dann laden - sonst heizt die Ladung auf
            # das, was zufaellig gerade eingestellt ist
            try:
                vorher, geaendert = warmwasser_ziel_setzen(z["soll"])
            except Exception as fehl:  # noqa: BLE001
                log.error("Warmwasser-Ziel nicht gesetzt: %s", fehl)
                protokollieren("fehler", b, f"Warmwasser-Ziel: {fehl}")
                return fehler("Die Zieltemperatur ließ sich nicht setzen. Bitte Bescheid geben.", 502)
            detail = f"auf {grad(z['soll'])}"
            if geaendert:
                detail += f" (Ziel vorher {grad(vorher) if vorher is not None else 'unbekannt'})"
        try:
            ausloesen(WARMWASSER_ENTITAET)
        except Exception as fehl:  # noqa: BLE001
            log.error("Warmwasser nicht ausgeloest: %s", fehl)
            protokollieren("fehler", b, f"Warmwasser: {fehl}")
            return fehler("Das Aufheizen ließ sich nicht starten. Bitte Bescheid geben.", 502)
        _zuletzt_aufgeheizt["t"] = time.time()
    protokollieren("warmwasser", b, detail)
    log.info("Warmwasser aufgeheizt von %s %s", b["name"], detail)
    return jsonify({"ok": True, "ziel": z["soll"]})


_staubsauger_sperre = threading.Lock()
_zuletzt_gesaugt = {"t": 0.0}

# Befehl in der App -> Dienst der vacuum-Domain
STAUBSAUGER_BEFEHLE = {"start": "start", "zur_station": "return_to_base"}


def staubsauger_zustand():
    zustand = batterie = None
    z = zustand_lesen(STAUBSAUGER_ENTITAET)
    if z:
        zustand = z.get("state")
        batterie = (z.get("attributes") or {}).get("battery_level")
    z = zustand_lesen(STAUBSAUGER_BATTERIE)
    if z:
        batterie = z.get("state")
    try:
        batterie = round(float(batterie))
    except (TypeError, ValueError):
        batterie = None
    if zustand in ("unavailable", "unknown"):
        zustand = None
    return {"zustand": zustand, "batterie": batterie}


@app.get("/api/staubsauger")
@braucht("voll")
def api_staubsauger(b):
    if (f := familie_fehler(b, STAUBSAUGER_ENTITAET)):
        return f
    return jsonify(staubsauger_zustand())


@app.post("/api/staubsauger/<befehl>")
@braucht("voll")
def api_staubsauger_befehl(b, befehl):
    if befehl not in STAUBSAUGER_BEFEHLE:
        abort(404)
    if (f := familie_fehler(b, STAUBSAUGER_ENTITAET)):
        return f
    with _staubsauger_sperre:
        if time.time() - _zuletzt_gesaugt["t"] < STAUBSAUGER_ABKLINGZEIT_S:
            return fehler("Einen Moment – der Staubsauger hat gerade erst einen Befehl bekommen.", 429)
        try:
            ha_aufruf("POST", f"/services/vacuum/{STAUBSAUGER_BEFEHLE[befehl]}",
                      {"entity_id": STAUBSAUGER_ENTITAET})
        except Exception as fehl:  # noqa: BLE001
            log.error("Staubsauger nicht geschaltet: %s", fehl)
            protokollieren("fehler", b, f"Staubsauger: {fehl}")
            return fehler("Der Staubsauger reagiert nicht. Bitte Bescheid geben.", 502)
        _zuletzt_gesaugt["t"] = time.time()
    protokollieren(f"staubsauger_{befehl}", b)
    log.info("Staubsauger %s von %s", befehl, b["name"])
    return jsonify({"ok": True})


_kamera_sperre = threading.Lock()
_kamera = {"t": 0.0, "bild": None}


@app.get("/api/kamera")
@braucht("voll")
def api_kamera(b):
    """Das aktuelle Bild der Kamera an der Haustuer. Die App fragt es nur
    ab, solange sie offen und das Bild zu sehen ist."""
    if (f := familie_fehler(b, KAMERA_ENTITAET)):
        return f
    with _kamera_sperre:
        if time.time() - _kamera["t"] >= KAMERA_BILD_S:
            try:
                _kamera.update(bild=ha_bild(KAMERA_ENTITAET), t=time.time())
            except Exception as fehl:  # noqa: BLE001
                log.warning("Kamerabild nicht lesbar: %s", fehl)
                return fehler("Die Kamera liefert gerade kein Bild.", 502)
        bild = _kamera["bild"]
    return app.response_class(bild, mimetype="image/jpeg")


# ------------------------------------------------------------------
# Pollenflug (DWD): aendert sich einmal am Tag
# ------------------------------------------------------------------

POLLEN_MERKEN_S = 10 * 60
_pollen = {"t": 0.0, "daten": None}
_pollen_sperre = threading.Lock()
# Die Entitaeten heissen z. B. sensor.pollenflug_graeser_121
POLLEN_NAMEN = {"graeser": "Gräser", "beifuss": "Beifuß"}


def pollen_wert(wert):
    try:
        return float(wert)
    except (TypeError, ValueError):
        return None


def pollen_lesen():
    arten = []
    stand = None
    for z in ha_aufruf("GET", "/states") or []:
        eid = z.get("entity_id", "")
        if not eid.startswith(POLLEN_PRAEFIX):
            continue
        a = z.get("attributes") or {}
        kurz = re.sub(r"_\d+$", "", eid[len(POLLEN_PRAEFIX):])
        arten.append({
            "name": POLLEN_NAMEN.get(kurz, kurz.replace("_", " ").capitalize()),
            "heute": pollen_wert(z.get("state")),
            "morgen": pollen_wert(a.get("state_tomorrow")),
            "uebermorgen": pollen_wert(a.get("state_in_2_days")),
        })
        stand = stand or a.get("last_update")
    # Am staerksten belastete Art zuerst, sonst nach Namen
    arten.sort(key=lambda p: (-max(v or 0 for v in (p["heute"], p["morgen"], p["uebermorgen"])), p["name"]))
    return {"stand": stand, "arten": arten}


@app.get("/api/pollen")
@braucht("voll")
def api_pollen(b):
    if (f := familie_fehler(b, POLLEN_PRAEFIX)):
        return f
    with _pollen_sperre:
        if _pollen["daten"] is None or time.time() - _pollen["t"] >= POLLEN_MERKEN_S:
            try:
                _pollen.update(daten=pollen_lesen(), t=time.time())
            except Exception as fehl:  # noqa: BLE001
                log.warning("Pollenflug nicht lesbar: %s", fehl)
                if _pollen["daten"] is None:
                    return fehler("Der Pollenflug ist gerade nicht abrufbar.", 502)
        return jsonify(_pollen["daten"])


# ------------------------------------------------------------------
# Standorte der Familie: jeder schaltet die Freigabe selbst ein. Gesendet
# wird nur, solange die App offen ist - eine Web-App kann im Hintergrund
# keinen Standort liefern.
# ------------------------------------------------------------------


@app.post("/api/standort/teilen")
@braucht("voll")
def api_standort_teilen(b):
    if not ist_familie(b):
        return fehler("Das dürfen nur Zugänge der Familie.", 403)
    an = bool(json_eingabe().get("an"))
    db().execute("UPDATE benutzer SET standort_teilen = ? WHERE id = ?", (int(an), b["id"]))
    if not an:
        # Aus heisst aus: der letzte Ort verschwindet sofort
        db().execute("DELETE FROM standorte WHERE benutzer_id = ?", (b["id"],))
    db().commit()
    protokollieren("standort_an" if an else "standort_aus", b)
    return jsonify({"ok": True})


@app.post("/api/standort")
@braucht("voll")
def api_standort(b):
    if not ist_familie(b) or not b["standort_teilen"]:
        return fehler("Die Standort-Freigabe ist aus.", 403)
    e = json_eingabe()
    try:
        lat, lon = float(e["lat"]), float(e["lon"])
        genauigkeit = max(0.0, float(e.get("genauigkeit") or 0))
    except (KeyError, TypeError, ValueError):
        return fehler("Ungültiger Standort.")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return fehler("Ungültiger Standort.")
    standort_speichern(b["id"], lat, lon, genauigkeit, int(time.time()), "app")
    return jsonify({"ok": True})


def standort_speichern(bid, lat, lon, genauigkeit, zeit, quelle):
    """Merkt sich den Ort - nur wenn er neuer ist als der bisherige. So
    ueberschreibt eine nachgereichte alte OwnTracks-Meldung nichts."""
    db().execute(
        "INSERT INTO standorte (benutzer_id, lat, lon, genauigkeit, zeit, quelle) VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(benutzer_id) DO UPDATE SET lat = excluded.lat, lon = excluded.lon,"
        " genauigkeit = excluded.genauigkeit, zeit = excluded.zeit, quelle = excluded.quelle"
        " WHERE excluded.zeit >= standorte.zeit",
        (bid, lat, lon, genauigkeit, zeit, quelle))
    db().commit()


# ------------------------------------------------------------------
# OwnTracks: kleine App, die den Standort auch im Hintergrund schickt.
# Sie meldet sich per HTTP Basic an (Benutzer "tuer-<id>", eigenes
# Passwort je Zugang) - nicht mit dem Passwort des Tueroeffners.
# ------------------------------------------------------------------


def owntracks_pruefsumme(passwort):
    return hashlib.sha256(passwort.encode()).hexdigest()


def owntracks_kuerzel(name):
    teile = [t for t in re.split(r"\s+", name.strip()) if t]
    kurz = "".join(t[0] for t in teile[:2]) if len(teile) > 1 else name.strip()[:2]
    return (kurz or "??").upper()


@app.post("/api/owntracks/einrichten")
@braucht("voll")
def api_owntracks_einrichten(b):
    if not ist_familie(b):
        return fehler("Das dürfen nur Zugänge der Familie.", 403)
    if not EXTERNE_ADRESSE.startswith("https://"):
        return fehler("Dafür braucht es die externe https-Adresse in den Add-on-Optionen.", 503)
    # Jedes Einrichten macht ein neues Passwort - ein altes Handy faellt damit raus
    passwort = secrets.token_urlsafe(24)
    db().execute("UPDATE benutzer SET owntracks_hash = ?, owntracks_zuletzt = NULL, standort_teilen = 1"
                 " WHERE id = ?", (owntracks_pruefsumme(passwort), b["id"]))
    db().commit()
    protokollieren("owntracks_eingerichtet", b)
    konfig = {
        "_type": "configuration",
        "mode": 3,  # HTTP
        "url": f"{EXTERNE_ADRESSE}/owntracks",
        "auth": True,
        "username": f"tuer-{b['id']}",
        "password": passwort,
        "deviceId": "handy",
        "tid": owntracks_kuerzel(b["name"]),
        "monitoring": 1,  # "signifikant": spart Akku, meldet bei Ortswechsel
    }
    inline = base64.urlsafe_b64encode(json.dumps(konfig).encode()).decode()
    return jsonify({
        "link": f"owntracks:///config?inline={inline}",
        "url": konfig["url"], "benutzer": konfig["username"], "passwort": passwort,
    })


@app.post("/api/owntracks/trennen")
@braucht("voll")
def api_owntracks_trennen(b):
    db().execute("UPDATE benutzer SET owntracks_hash = NULL, owntracks_zuletzt = NULL WHERE id = ?", (b["id"],))
    db().commit()
    protokollieren("owntracks_getrennt", b)
    return jsonify({"ok": True})


@app.post("/owntracks")
def owntracks_empfang():
    """Hierhin schickt OwnTracks jeden neuen Ort. Antwort ist immer eine
    (leere) JSON-Liste - das erwartet die App."""
    if ip_gebremst():
        return jsonify([]), 429
    auth = request.authorization
    b = None
    if auth and auth.username and auth.username.startswith("tuer-") and auth.username[5:].isdigit():
        b = benutzer_laden(int(auth.username[5:]))
    if not b or not b["owntracks_hash"] or not secrets.compare_digest(
            b["owntracks_hash"], owntracks_pruefsumme(auth.password or "")):
        ip_fehler()
        return jsonify([]), 401
    e = json_eingabe()
    if e.get("_type") != "location":
        return jsonify([])  # Wegpunkte, Status usw. brauchen wir nicht
    db().execute("UPDATE benutzer SET owntracks_zuletzt = ? WHERE id = ?", (int(time.time()), b["id"]))
    db().commit()
    # Freigabe aus, gesperrt oder nicht mehr Familie: Ort wird verworfen
    if not ist_familie(b) or not b["standort_teilen"]:
        return jsonify([])
    try:
        lat, lon = float(e["lat"]), float(e["lon"])
        genauigkeit = max(0.0, float(e.get("acc") or 0))
        zeit = int(e.get("tst") or time.time())
    except (KeyError, TypeError, ValueError):
        return jsonify([])
    jetzt = int(time.time())
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or zeit < jetzt - STANDORT_TAGE * 86400:
        return jsonify([])
    standort_speichern(b["id"], lat, lon, genauigkeit, min(zeit, jetzt), "owntracks")
    return jsonify([])


@app.get("/api/standorte")
@braucht("voll")
def api_standorte(b):
    if not ist_familie(b):
        return fehler("Das dürfen nur Zugänge der Familie.", 403)
    heim = heimat()
    leute = []
    # Nur wer noch zur Familie gehoert, freigegeben ist und teilt
    for r in db().execute(
            "SELECT b.id, b.name, s.lat, s.lon, s.genauigkeit, s.zeit, s.quelle FROM standorte s"
            " JOIN benutzer b ON b.id = s.benutzer_id"
            " WHERE b.familie = 1 AND b.status = 'aktiv' AND b.standort_teilen = 1 ORDER BY b.name"):
        eintrag = {k: r[k] for k in ("name", "lat", "lon", "genauigkeit", "zeit", "quelle")} | {"ich": r["id"] == b["id"]}
        if heim:
            eintrag["von_zuhause_m"] = round(entfernung_m(r["lat"], r["lon"], *heim))
        leute.append(eintrag)
    return jsonify({
        "heim": {"lat": heim[0], "lon": heim[1]} if heim else None,
        "leute": leute,
        "jetzt": int(time.time()),
    })


# ------------------------------------------------------------------
# Hausdienst: Der Admin plant, wer an welchem Wochentag welche Aufgabe hat.
# Die Familie sieht den Plan und beantragt Umplanungen oder eine Pause - der
# Antrag landet in der Glocke von Home Assistant, der Admin entscheidet.
# ------------------------------------------------------------------

WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MONATE = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
          "November", "Dezember")
HD_MEIN_PLAN_TAGE = 28
HD_VERWALTUNG_TAGE = 14
HD_PAUSE_MAX_TAGE = 60
# So weit im Voraus darf beantragt werden
HD_ANTRAG_MAX_TAGE = 366
HD_OFFENE_ANTRAEGE_MAX = 10
# Tauschanfragen rufen nur tagsueber an, nachts gestellte am Morgen
HD_ANFRAGE_VON, HD_ANFRAGE_BIS = 7, 21


def heute():
    """Das Datum im Haus (Zeitzone des Add-ons). In den Tests ersetzt."""
    return datetime.date.today()


def datum_lesen(text):
    try:
        return datetime.date.fromisoformat(str(text))
    except ValueError:
        return None


def datum_text(tag):
    return f"{WOCHENTAGE[tag.weekday()]}, {tag.day}.{tag.month}."


def ganzzahl(wert):
    try:
        return int(wert)
    except (TypeError, ValueError):
        return None


def dienste_am(tag):
    """Alle Dienste eines Tages: der Wochenplan, darauf die Abweichungen.
    Entfallene und pausierte bleiben drin (fuer die Verwaltung), markiert."""
    iso = tag.isoformat()
    abw = {r["plan_id"]: r for r in db().execute(
        "SELECT id, plan_id, person_id FROM hd_abweichungen WHERE datum = ? AND plan_id IS NOT NULL", (iso,))}
    roh = []
    for p in db().execute("SELECT id, person_id, aufgabe_id FROM hd_plan WHERE wochentag = ?", (tag.weekday(),)):
        a = abw.get(p["id"])
        roh.append((a["person_id"] if a else p["person_id"], p["aufgabe_id"], p["id"], a["id"] if a else None))
    for a in db().execute("SELECT id, person_id, aufgabe_id FROM hd_abweichungen"
                          " WHERE datum = ? AND plan_id IS NULL", (iso,)):
        roh.append((a["person_id"], a["aufgabe_id"], None, a["id"]))
    pausiert = {r[0] for r in db().execute(
        "SELECT person_id FROM hd_pausen WHERE von <= ? AND bis >= ?", (iso, iso))}
    namen = {r["id"]: r["name"] for r in db().execute("SELECT id, name FROM hd_personen")}
    aufgaben = {r["id"]: r for r in db().execute("SELECT * FROM hd_aufgaben")}
    dienste = [{
        "person_id": pid, "name": namen.get(pid), "aufgabe_id": aid,
        "aufgabe": aufgaben[aid]["name"], "ansage": aufgaben[aid]["ansage"],
        "plan_id": plan_id, "abweichung_id": abw_id,
        "entfaellt": pid is None, "pausiert": pid in pausiert,
    } for pid, aid, plan_id, abw_id in roh]
    dienste.sort(key=lambda d: (d["name"] or "", d["aufgabe"]))
    return dienste


def pausiert_am(pid, tag):
    return bool(db().execute("SELECT 1 FROM hd_pausen WHERE person_id = ? AND von <= ? AND bis >= ?",
                             (pid, tag.isoformat(), tag.isoformat())).fetchone())


def faellige_dienste(tag):
    return [d for d in dienste_am(tag) if not d["entfaellt"] and not d["pausiert"]]


def hd_personen():
    return [dict(r) for r in db().execute("SELECT id, name FROM hd_personen ORDER BY name")]


def hd_erreichbar(pid):
    """Wen man um einen Tausch fragen kann: mit Telefonnummer oder mit
    freigegebenem Familien-Zugang (antwortet dann in der App)."""
    return [dict(r) for r in db().execute(
        "SELECT p.id, p.name FROM hd_personen p LEFT JOIN hd_telefon t ON t.person_id = p.id"
        " LEFT JOIN benutzer b ON b.id = p.benutzer_id"
        " WHERE p.id != ? AND (t.nummer != '' OR (b.familie = 1 AND b.status = 'aktiv')) ORDER BY p.name", (pid,))]


def person_von(b):
    """Die Person im Hausdienst, mit der dieser Zugang verknuepft ist."""
    r = db().execute("SELECT id FROM hd_personen WHERE benutzer_id = ?", (b["id"],)).fetchone()
    return r["id"] if r else None


def glocke(text, kennung):
    """Eine Meldung in der Glocke von Home Assistant. Scheitert still."""
    try:
        ha_aufruf("POST", "/services/persistent_notification/create",
                  {"title": "Hausdienst", "message": text, "notification_id": kennung})
    except Exception as fehl:  # noqa: BLE001
        log.warning("Meldung in die Glocke fehlgeschlagen: %s", fehl)


def glocke_weg(kennung):
    try:
        ha_aufruf("POST", "/services/persistent_notification/dismiss", {"notification_id": kennung})
    except Exception as fehl:  # noqa: BLE001
        log.warning("Meldung nicht aus der Glocke entfernt: %s", fehl)


def antrag_text(a):
    tag = datum_lesen(a["datum"])
    if a["art"] == "pause":
        bis = tag + datetime.timedelta(days=a["tage"] - 1)
        return f"{a['name']}: Pause von {datum_text(tag)} bis {datum_text(bis)} ({a['tage']} Tage)"
    return f"{a['name']}: {a['aufgabe']} am {datum_text(tag)} übernimmt {a['an_name']}"


def antraege_laden(bedingung="1", werte=(), grenze=30):
    return [dict(r) | {"text": antrag_text(r)} for r in db().execute(
        "SELECT h.*, b.name, an.name AS an_name, g.name AS aufgabe FROM hd_antraege h"
        " JOIN hd_personen b ON b.id = h.person_id LEFT JOIN hd_personen an ON an.id = h.an_id"
        " LEFT JOIN hd_aufgaben g ON g.id = h.aufgabe_id"
        f" WHERE {bedingung} ORDER BY h.status = 'offen' DESC, h.id DESC LIMIT ?", (*werte, grenze))]


def anfragen_laden(bedingung="1", werte=(), grenze=30):
    erg = []
    for r in db().execute(
            "SELECT f.*, v.name AS von_name, n.name AS an_name, g.name AS aufgabe, g.ansage FROM hd_anfragen f"
            " JOIN hd_personen v ON v.id = f.von_id JOIN hd_personen n ON n.id = f.an_id"
            " JOIN hd_aufgaben g ON g.id = f.aufgabe_id"
            f" WHERE {bedingung} ORDER BY f.id DESC LIMIT ?", (*werte, grenze)):
        a = dict(r) | {"dienst": f"{r['aufgabe']} am {datum_text(datum_lesen(r['datum']))}"}
        # Ist der Tag vorbei, ist eine unbeantwortete Anfrage erledigt
        if a["status"] == "offen" and a["datum"] < heute().isoformat():
            a["status"] = "abgelaufen"
        erg.append(a)
    return erg


def dienst_uebergeben(tag, dienst, an_id):
    if dienst["plan_id"]:
        abweichung_setzen(tag, db().execute("SELECT * FROM hd_plan WHERE id = ?", (dienst["plan_id"],)).fetchone(), an_id)
    else:
        db().execute("UPDATE hd_abweichungen SET person_id = ? WHERE id = ?", (an_id, dienst["abweichung_id"]))


def anfrage_beantworten(a, ja):
    """Antwort auf eine Tauschanfrage (Zeile aus hd_anfragen). Bei Ja geht
    der Dienst sofort an die gefragte Person. Gibt einen Fehlertext zurueck."""
    tag = datum_lesen(a["datum"])
    if a["status"] != "offen" or tag < heute():
        return "Diese Anfrage ist nicht mehr offen."
    jetzt = int(time.time())
    if ja:
        dienst = next((d for d in faellige_dienste(tag)
                       if d["person_id"] == a["von_id"] and d["aufgabe_id"] == a["aufgabe_id"]), None)
        if not dienst:
            return "Diesen Dienst gibt es so nicht mehr – vielleicht hat ihn schon jemand übernommen."
        if pausiert_am(a["an_id"], tag):
            return "Du hast an dem Tag Pause."
        dienst_uebergeben(tag, dienst, a["an_id"])
        # Wer sonst noch fuer denselben Dienst gefragt wurde, muss nicht mehr antworten
        db().execute("UPDATE hd_anfragen SET status = 'zurueckgezogen', entschieden = ? WHERE status = 'offen'"
                     " AND id != ? AND von_id = ? AND datum = ? AND aufgabe_id = ?",
                     (jetzt, a["id"], a["von_id"], a["datum"], a["aufgabe_id"]))
    db().execute("UPDATE hd_anfragen SET status = ?, entschieden = ? WHERE id = ?", ("ja" if ja else "nein", jetzt, a["id"]))
    db().commit()
    if ja:
        f = anfragen_laden("f.id = ?", (a["id"],))[0]
        glocke(f"Tausch: {f['an_name']} übernimmt {f['dienst']} von {f['von_name']}.", f"hausdienst_tausch_{a['id']}")
    return None


def anrufzeiten_lesen(e):
    """Uhrzeit fuer jeden Tag und abweichende Wochentage pruefen."""
    zeit = str(e.get("zeit", "")).strip()
    tage = e.get("tage") or {}
    if not isinstance(tage, dict) or any(k not in {str(i) for i in range(7)} for k in tage):
        return None, None, "Ungültige Wochentage."
    tage = {k: str(v).strip() for k, v in tage.items() if str(v).strip()}
    if any(not UHRZEIT_MUSTER.match(z) for z in [zeit, *tage.values()] if z):
        return None, None, "Uhrzeiten bitte als HH:MM, z. B. 06:30."
    return zeit, json.dumps(tage, sort_keys=True), None


@app.get("/api/hausdienst")
@braucht("voll")
def api_hausdienst(b):
    if not ist_familie(b):
        return fehler("Das dürfen nur Zugänge der Familie.", 403)
    tag = heute()
    # Ohne verknuepfte Person sieht man nur, wer heute dran ist
    pid = person_von(b)
    meine = []
    for i in range(HD_MEIN_PLAN_TAGE if pid else 0):
        t = tag + datetime.timedelta(days=i)
        meine += [{"datum": t.isoformat(), "tag": datum_text(t), "aufgabe_id": d["aufgabe_id"],
                   "aufgabe": d["aufgabe"], "geaendert": d["abweichung_id"] is not None}
                  for d in faellige_dienste(t) if d["person_id"] == pid]
    return jsonify({
        "heute": datum_text(tag),
        "eingetragen": pid is not None,
        "dienste": [{"name": d["name"], "aufgabe": d["aufgabe"], "ich": pid is not None and d["person_id"] == pid}
                    for d in faellige_dienste(tag)],
        "meine": meine,
        "pausen": [{"von": datum_text(datum_lesen(r["von"])), "bis": datum_text(datum_lesen(r["bis"]))}
                   for r in db().execute("SELECT von, bis FROM hd_pausen WHERE person_id = ? AND bis >= ?"
                                         " ORDER BY von", (pid, tag.isoformat()))],
        "antraege": [{k: a[k] for k in ("id", "status", "text", "angelegt")}
                     for a in antraege_laden("h.person_id = ?", (pid,), 10)],
        "familie": hd_erreichbar(pid) if pid else [],
        "anfragen_an_mich": [{k: a[k] for k in ("id", "von_name", "dienst")} for a in anfragen_laden(
            "f.an_id = ? AND f.status = 'offen' AND f.datum >= ?", (pid, tag.isoformat()))],
        "meine_anfragen": [{k: a[k] for k in ("id", "an_name", "dienst", "status")} for a in anfragen_laden(
            "f.von_id = ? AND f.datum >= ?", (pid, tag.isoformat()), 10)],
        "anrufzeit": anrufzeit_zeigen(pid),
        "pause_max": HD_PAUSE_MAX_TAGE,
    })


def anrufzeit_zeigen(pid):
    t = db().execute("SELECT * FROM hd_telefon WHERE person_id = ?", (pid,)).fetchone() if pid else None
    return {"zeit": t["zeit"] if t else "", "tage": json.loads(t["tage"] or "{}") if t else {},
            "nummer": bool(t and t["nummer"])}


@app.post("/api/hausdienst/anrufzeit")
@braucht("voll")
def api_hausdienst_anrufzeit(b):
    """Die eigene Uhrzeit fuer den Anruf am Morgen. Die Nummer pflegt die Verwaltung."""
    pid = person_von(b) if ist_familie(b) else None
    if not pid:
        return fehler("Du stehst noch nicht im Hausdienst-Plan.", 403)
    zeit, tage, fehl = anrufzeiten_lesen(json_eingabe())
    if fehl:
        return fehler(fehl)
    db().execute("INSERT INTO hd_telefon (person_id, zeit, tage) VALUES (?, ?, ?)"
                 " ON CONFLICT(person_id) DO UPDATE SET zeit = excluded.zeit, tage = excluded.tage", (pid, zeit, tage))
    db().commit()
    return jsonify({"ok": True})


@app.post("/api/hausdienst/anfrage")
@braucht("voll")
def api_hausdienst_anfrage(b):
    """Eine andere Person fragen, ob sie einen eigenen Dienst uebernimmt."""
    pid = person_von(b) if ist_familie(b) else None
    if not pid:
        return fehler("Du stehst noch nicht im Hausdienst-Plan.", 403)
    e = json_eingabe()
    tag = datum_lesen(e.get("datum"))
    aid, an_id = ganzzahl(e.get("aufgabe_id")), ganzzahl(e.get("an_id"))
    if not tag or not heute() <= tag <= heute() + datetime.timedelta(days=HD_ANTRAG_MAX_TAGE):
        return fehler("Bitte einen Tag ab heute und höchstens ein Jahr im Voraus wählen.")
    if not any(d["person_id"] == pid and d["aufgabe_id"] == aid for d in faellige_dienste(tag)):
        return fehler("Diesen Dienst hast du an dem Tag nicht.")
    if an_id not in {f["id"] for f in hd_erreichbar(pid)}:
        return fehler("Bitte wählen, wen du fragst.")
    if pausiert_am(an_id, tag):
        return fehler("Wer übernehmen soll, hat an dem Tag Pause.")
    if db().execute("SELECT COUNT(*) FROM hd_anfragen WHERE von_id = ? AND status = 'offen' AND datum >= ?",
                    (pid, heute().isoformat())).fetchone()[0] >= HD_OFFENE_ANTRAEGE_MAX:
        return fehler("Du hast schon genug offene Anfragen – warte erst die Antworten ab.")
    if db().execute("SELECT 1 FROM hd_anfragen WHERE von_id = ? AND an_id = ? AND datum = ? AND aufgabe_id = ?"
                    " AND status = 'offen'", (pid, an_id, tag.isoformat(), aid)).fetchone():
        return fehler("Diese Anfrage läuft schon.")
    neu = db().execute("INSERT INTO hd_anfragen (von_id, an_id, datum, aufgabe_id, angelegt) VALUES (?, ?, ?, ?, ?)",
                       (pid, an_id, tag.isoformat(), aid, int(time.time())))
    db().commit()
    a = anfragen_laden("f.id = ?", (neu.lastrowid,))[0]
    protokollieren("hd_anfrage", b, f"{a['dienst']} an {a['an_name']}")
    return jsonify({"ok": True})


@app.post("/api/hausdienst/anfrage/<int:fid>/<antwort>")
@braucht("voll")
def api_hausdienst_anfrage_antwort(b, fid, antwort):
    if antwort not in ("ja", "nein", "zurueckziehen"):
        abort(404)
    pid = person_von(b) if ist_familie(b) else None
    a = db().execute("SELECT * FROM hd_anfragen WHERE id = ?", (fid,)).fetchone()
    # Antworten darf nur, wer gefragt wurde - zurueckziehen nur, wer gefragt hat
    if not pid or not a or a["von_id" if antwort == "zurueckziehen" else "an_id"] != pid:
        return fehler("Diese Anfrage gibt es nicht.", 404)
    if antwort == "zurueckziehen":
        if a["status"] != "offen":
            return fehler("Diese Anfrage ist nicht mehr offen.")
        db().execute("UPDATE hd_anfragen SET status = 'zurueckgezogen', entschieden = ? WHERE id = ?",
                     (int(time.time()), fid))
        db().commit()
    elif (fehl := anfrage_beantworten(a, antwort == "ja")):
        return fehler(fehl)
    return jsonify({"ok": True})


@app.post("/api/hausdienst/antrag")
@braucht("voll")
def api_hausdienst_antrag(b):
    if not ist_familie(b):
        return fehler("Das dürfen nur Zugänge der Familie.", 403)
    pid = person_von(b)
    if not pid:
        return fehler("Du stehst noch nicht im Hausdienst-Plan.", 403)
    e = json_eingabe()
    tag = datum_lesen(e.get("datum"))
    if not tag or not heute() <= tag <= heute() + datetime.timedelta(days=HD_ANTRAG_MAX_TAGE):
        return fehler("Bitte einen Tag ab heute und höchstens ein Jahr im Voraus wählen.")
    if db().execute("SELECT COUNT(*) FROM hd_antraege WHERE person_id = ? AND status = 'offen'",
                    (pid,)).fetchone()[0] >= HD_OFFENE_ANTRAEGE_MAX:
        return fehler("Du hast schon genug offene Anträge – warte erst die Entscheidung ab.")
    jetzt = int(time.time())
    if e.get("art") == "pause":
        tage = ganzzahl(e.get("tage"))
        if not tage or not 1 <= tage <= HD_PAUSE_MAX_TAGE:
            return fehler(f"Eine Pause dauert 1 bis {HD_PAUSE_MAX_TAGE} Tage.")
        if db().execute("SELECT 1 FROM hd_antraege WHERE person_id = ? AND art = 'pause' AND datum = ?"
                        " AND status = 'offen'", (pid, tag.isoformat())).fetchone():
            return fehler("Dafür läuft schon ein Antrag.")
        neu = db().execute("INSERT INTO hd_antraege (person_id, art, datum, tage, angelegt)"
                           " VALUES (?, 'pause', ?, ?, ?)", (pid, tag.isoformat(), tage, jetzt))
    else:
        return fehler("Unbekannte Art von Antrag.")
    db().commit()
    a = antraege_laden("h.id = ?", (neu.lastrowid,))[0]
    glocke(f"{a['text']}. Entscheiden in der Verwaltung des Türöffners.", f"hausdienst_antrag_{a['id']}")
    protokollieren("hd_antrag", b, a["text"])
    return jsonify({"ok": True})


@app.post("/api/hausdienst/antrag/<int:aid>/zurueckziehen")
@braucht("voll")
def api_hausdienst_zurueckziehen(b, aid):
    n = db().execute("UPDATE hd_antraege SET status = 'zurueckgezogen', entschieden = ?"
                     " WHERE id = ? AND person_id = ? AND status = 'offen'",
                     (int(time.time()), aid, person_von(b))).rowcount
    if not n:
        return fehler("Diesen offenen Antrag gibt es nicht.", 404)
    db().commit()
    glocke_weg(f"hausdienst_antrag_{aid}")
    return jsonify({"ok": True})


# ------------------------------------------------------------------
# Hausdienst-Anrufe: Wer heute Dienst hat, wird zu seiner Uhrzeit ueber
# ha-sip angerufen. Die Ansage wiederholt sich, damit sie auch vollstaendig
# auf der Mailbox landet. Taste 1 = verstanden; ohne Taste 1 folgt nach
# 30 Minuten ein zweiter Anruf.
# ------------------------------------------------------------------

HD_ANSAGE_STANDARD = ("Hallo {name}, guten Morgen – heute ist dein großer Tag, du hast {dienste}. "
                      "Drücke die Eins, wenn alles klar ist.")
HD_ZWEITER_ANRUF_S = 30 * 60
# Nach einem Neustart nicht mittags noch "guten Morgen" anrufen
HD_ANRUF_FENSTER_S = 2 * 3600
# Nie zwei Hausdienst-Anrufe gleichzeitig - eine Leitung bleibt frei
HD_ANRUF_ABSTAND_S = 150
HD_KLINGELN_S = 45
# Ansage plus Zeit fuer die Taste, dann kommt die Wiederholung
HD_WARTEN_S = 25
UHRZEIT_MUSTER = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
NUMMER_MUSTER = re.compile(r"^\+?\d{3,20}$")


def aufzaehlen(teile):
    return teile[0] if len(teile) == 1 else ", ".join(teile[:-1]) + " und " + teile[-1]


def ansage_bauen(name, ansagen, tag):
    vorlage = einstellung_lesen("hd_ansage") or HD_ANSAGE_STANDARD
    return (vorlage.replace("{name}", name.split()[0]).replace("{dienste}", aufzaehlen(ansagen))
            .replace("{tag}", WOCHENTAGE[tag.weekday()]))


def rueckmeldung(text, eingabe):
    """Was nach einer Taste gesagt wird; die Taste geht ueber
    hassio.addon_stdin an dieses Add-on zurueck."""
    return {"message": text, "language": "de", "cache_audio": True, "post_action": "hangup",
            "action": {"domain": "hassio", "service": "addon_stdin",
                       "service_data": {"addon": EIGENER_SLUG, "input": eingabe}}}


def wiederholen(ansage, tasten, schluss="Tschüss."):
    """Ansage, zweimal "Ich wiederhole" (damit sie auch auf der Mailbox
    landet), dann auflegen."""
    stufe = {"message": schluss, "language": "de", "cache_audio": True, "post_action": "hangup"}
    for text in ("Ich wiederhole: " + ansage, "Ich wiederhole: " + ansage, ansage):
        stufe = {"message": text, "language": "de", "timeout": HD_WARTEN_S, "choices": tasten | {"timeout": stufe}}
    return stufe


def anruf_menue(ansage, kennung):
    return wiederholen(ansage, {"1": rueckmeldung("Danke, einen schönen Tag!", {"hausdienst_bestaetigt": kennung})})


def anfrage_menue(ansage, kennung):
    return wiederholen(ansage, {
        "1": rueckmeldung("Danke, der Dienst ist jetzt bei dir.", {"hausdienst_antwort": kennung, "ja": True}),
        "2": rueckmeldung("Alles klar, danke.", {"hausdienst_antwort": kennung, "ja": False}),
    }, "Du kannst auch in der Türöffner-App antworten. Tschüss.")


def datum_gesprochen(tag):
    if tag == heute():
        return "heute"
    if tag == heute() + datetime.timedelta(days=1):
        return "morgen"
    return f"am {WOCHENTAGE[tag.weekday()]}, den {tag.day}. {MONATE[tag.month - 1]}"


def anfrage_ansage(a):
    return (f"Hallo {a['an_name'].split()[0]}, hier ist der Hausdienst. {a['von_name'].split()[0]} fragt, ob du "
            f"{datum_gesprochen(datum_lesen(a['datum']))} {a['ansage']} übernehmen kannst. "
            "Drücke die Eins für ja oder die Zwei für nein.")


def anrufen(pid, versuch, ansage, nummer, tag, zeitpunkt, menue=anruf_menue, kennung=None):
    """versuch: 1/2 = Anruf am Morgen, 0 = Testanruf, -1 = Tauschanfrage."""
    kennung = kennung or secrets.token_hex(12)
    fehl = None
    try:
        ha_aufruf("POST", "/services/hassio/addon_stdin", {"addon": SIP_ADDON, "input": {
            "command": "dial", "number": nummer if nummer.startswith("sip:") else f"sip:{nummer}@{SIP_SERVER}",
            "sip_account": SIP_KONTO, "ring_timeout": HD_KLINGELN_S, "menu": menue(ansage, kennung),
        }})
    except Exception as e:  # noqa: BLE001
        # Kein neuer Versuch im Halbminutentakt - der zweite Anruf holt es nach
        fehl = str(e)[:200]
        log.warning("Hausdienst-Anruf fehlgeschlagen: %s", e)
    db().execute("INSERT INTO hd_anrufe (datum, person_id, versuch, zeit, kennung, fehler) VALUES (?, ?, ?, ?, ?, ?)",
                 (tag.isoformat(), pid, versuch, int(zeitpunkt), kennung, fehl))
    db().commit()
    return fehl


def anrufzeit(telefon, tag):
    try:
        abweichend = json.loads(telefon["tage"] or "{}").get(str(tag.weekday()))
    except ValueError:
        abweichend = None
    return abweichend or telefon["zeit"]


def anrufe_pruefen(jetzt=None):
    if not SIP_ADDON or einstellung_lesen("hd_anrufe") != "1":
        return
    jetzt = jetzt or datetime.datetime.now()
    tag = jetzt.date()
    zuletzt = db().execute("SELECT MAX(zeit) FROM hd_anrufe").fetchone()[0] or 0
    if jetzt.timestamp() - zuletzt < HD_ANRUF_ABSTAND_S:
        return
    je_person = {}
    for d in faellige_dienste(tag):
        je_person.setdefault(d["person_id"], []).append(d)
    for pid, dienste in sorted(je_person.items()):
        t = db().execute("SELECT * FROM hd_telefon WHERE person_id = ?", (pid,)).fetchone()
        if not t or not t["nummer"] or not anrufzeit(t, tag):
            continue
        bisher = db().execute("SELECT * FROM hd_anrufe WHERE datum = ? AND person_id = ? AND versuch > 0"
                              " ORDER BY versuch", (tag.isoformat(), pid)).fetchall()
        if not bisher:
            faellig, versuch = datetime.datetime.combine(tag, datetime.time.fromisoformat(anrufzeit(t, tag))), 1
        elif len(bisher) == 1 and not bisher[0]["bestaetigt"]:
            faellig = datetime.datetime.fromtimestamp(bisher[0]["zeit"]) + datetime.timedelta(seconds=HD_ZWEITER_ANRUF_S)
            versuch = 2
        else:
            continue
        if faellig <= jetzt < faellig + datetime.timedelta(seconds=HD_ANRUF_FENSTER_S):
            anrufen(pid, versuch, ansage_bauen(dienste[0]["name"], [d["ansage"] for d in dienste], tag), t["nummer"],
                    tag, jetzt.timestamp())
            return  # der naechste fruehestens nach HD_ANRUF_ABSTAND_S
    # Danach die Tauschanfragen - nur tagsueber, je Anfrage ein Anruf
    if not HD_ANFRAGE_VON <= jetzt.hour < HD_ANFRAGE_BIS:
        return
    for a in anfragen_laden("f.status = 'offen' AND f.angerufen IS NULL AND f.datum >= ?", (tag.isoformat(),)):
        t = db().execute("SELECT nummer FROM hd_telefon WHERE person_id = ?", (a["an_id"],)).fetchone()
        if not t or not t["nummer"]:
            continue
        kennung = secrets.token_hex(12)
        db().execute("UPDATE hd_anfragen SET angerufen = ?, kennung = ? WHERE id = ?",
                     (int(jetzt.timestamp()), kennung, a["id"]))
        anrufen(a["an_id"], -1, anfrage_ansage(a), t["nummer"], tag, jetzt.timestamp(), anfrage_menue, kennung)
        return


def anruf_bestaetigen(kennung):
    conn = verbinden()
    try:
        conn.execute("UPDATE hd_anrufe SET bestaetigt = ? WHERE kennung = ? AND bestaetigt IS NULL",
                     (int(time.time()), kennung))
        conn.commit()
    finally:
        conn.close()


def anfrage_per_taste(kennung, ja):
    anruf_bestaetigen(kennung)
    with app.app_context():
        a = db().execute("SELECT * FROM hd_anfragen WHERE kennung = ?", (kennung,)).fetchone()
        if a and (fehl := anfrage_beantworten(a, ja)):
            log.info("Tauschanfrage %s: %s", a["id"], fehl)


def stdin_lesen(quelle):
    """ha-sip meldet Taste 1 ueber hassio.addon_stdin - je Zeile ein JSON."""
    for zeile in quelle:
        try:
            daten = json.loads(zeile)
        except ValueError:
            continue
        if not isinstance(daten, dict):
            continue
        if isinstance(daten.get("hausdienst_bestaetigt"), str):
            log.info("Hausdienst: Taste 1 gemeldet")
            anruf_bestaetigen(daten["hausdienst_bestaetigt"])
        elif isinstance(daten.get("hausdienst_antwort"), str):
            log.info("Hausdienst: Antwort auf eine Tauschanfrage")
            anfrage_per_taste(daten["hausdienst_antwort"], daten.get("ja") is True)
    log.warning("stdin ist zu - Taste 1 aus ha-sip kommt nicht mehr an")


def anruf_schleife():
    while True:
        try:
            with app.app_context():
                anrufe_pruefen()
        except Exception:  # noqa: BLE001
            log.exception("Hausdienst-Anrufe")
        time.sleep(30)


# ------------------------------------------------------------------
# Gaeste-WLAN: fuer jeden freigegebenen Zugang
# ------------------------------------------------------------------


@app.get("/api/wlan")
@braucht("voll")
def api_wlan(b):
    if not WLAN_NAME:
        return fehler("Das ist hier nicht eingerichtet.", 404)
    if b["status"] != "aktiv":
        return fehler("Dein Zugang ist noch nicht freigegeben.", 403)
    offen = WLAN_VERSCHLUESSELUNG == "offen"
    qr = segno.helpers.make_wifi(
        ssid=WLAN_NAME, password=None if offen else WLAN_SCHLUESSEL,
        security="nopass" if offen else WLAN_VERSCHLUESSELUNG)
    return jsonify({
        "name": WLAN_NAME,
        "schluessel": "" if offen else WLAN_SCHLUESSEL,
        "svg": qr.svg_inline(scale=6, border=2),
    })


# ------------------------------------------------------------------
# Verwaltung (nur Ingress)
# ------------------------------------------------------------------


@app.get("/api/admin/uebersicht")
@nur_ingress
def api_admin_uebersicht():
    benutzer = [
        {k: r[k] for k in ("id", "name", "benutzername", "email", "status", "fern", "familie", "zwei_faktor",
                           "angelegt", "freigegeben", "zuletzt_geoeffnet")}
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
        "warmwasser": warmwasser_ziel_daten(),
        "einstellungen": {
            "tuer_entitaet": TUER_ENTITAET,
            "entitaet_typ_ok": TUER_ENTITAET.split(".", 1)[0] in DIENSTE,
            "externe_adresse": EXTERNE_ADRESSE,
            "radius_meter": RADIUS_METER,
            "benachrichtigung": BENACHRICHTIGUNG,
            "heim_bekannt": bool(heim),
            "registrierung_offen": REGISTRIERUNG_OFFEN,
            "zwei_faktor_pflicht": ZWEI_FAKTOR_PFLICHT,
            "warmwasser_entitaet": WARMWASSER_ENTITAET,
            "warmwasser_typ_ok": not WARMWASSER_ENTITAET or WARMWASSER_ENTITAET.split(".", 1)[0] in DIENSTE,
            "warmwasser_temperatur": WARMWASSER_TEMPERATUR,
            "warmwasser_laeuft": WARMWASSER_LAEUFT,
            "warmwasser_soll": WARMWASSER_SOLL,
            "warmwasser_heizt": WARMWASSER_HEIZT,
            "staubsauger_entitaet": STAUBSAUGER_ENTITAET,
            "staubsauger_typ_ok": not STAUBSAUGER_ENTITAET or STAUBSAUGER_ENTITAET.startswith("vacuum."),
            "staubsauger_batterie": STAUBSAUGER_BATTERIE,
            "kamera_entitaet": KAMERA_ENTITAET,
            "pollen_praefix": POLLEN_PRAEFIX,
            # Der Schluessel selbst steht nur in den Optionen
            "wlan_name": WLAN_NAME,
        },
    })


AKTIONEN = {"freigeben", "sperren", "entsperren", "fern_an", "fern_aus", "familie_an", "familie_aus",
            "2fa_zuruecksetzen", "loeschen"}


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
    elif aktion in ("familie_an", "familie_aus"):
        db().execute("UPDATE benutzer SET familie = ? WHERE id = ?", (int(aktion == "familie_an"), bid))
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


def warmwasser_ziel_daten():
    """Fuer die Verwaltung: Speichertemperatur, die Vorgabe fuers Aufheizen
    mit ihren Grenzen und was gerade in der Waermepumpe eingestellt ist."""
    if not WARMWASSER_SOLL:
        return None
    z = zustand_lesen(WARMWASSER_SOLL) or {}
    try:
        eingestellt = float(z.get("state"))
    except (TypeError, ValueError):
        eingestellt = None
    unten, oben, schritt = soll_grenzen(z)
    return {
        "vorgabe": warmwasser_vorgabe(z), "eingestellt": eingestellt,
        "min": unten, "max": oben, "schritt": schritt,
        "temperatur": zahl_lesen(WARMWASSER_TEMPERATUR) if WARMWASSER_TEMPERATUR else None,
        "laeuft": an_aus_lesen(WARMWASSER_LAEUFT) if WARMWASSER_LAEUFT else None,
    }


@app.post("/api/admin/warmwasser/vorgabe")
@nur_ingress
def api_admin_warmwasser_vorgabe():
    """Speichert nur die Vorgabe. An die Waermepumpe geht sie erst, wenn
    jemand in der App aufheizt."""
    daten = warmwasser_ziel_daten()
    if not daten:
        return fehler("Keine Entität für die Zieltemperatur eingestellt.", 404)
    try:
        wert = float(json_eingabe().get("wert"))
    except (TypeError, ValueError):
        return fehler("Bitte eine Zahl angeben.")
    if not daten["min"] <= wert <= daten["max"]:
        return fehler(f"Erlaubt sind {grad(daten['min'])} bis {grad(daten['max'])}.")
    einstellung_setzen("warmwasser_vorgabe", wert)
    protokollieren("admin_warmwasser_vorgabe", None, f"{grad(daten['vorgabe'])} → {grad(wert)}", name="Admin")
    return jsonify({"ok": True})


@app.post("/api/admin/oeffnen")
@nur_ingress
def api_admin_oeffnen():
    if (f := oeffnen_ausfuehren(None, "Admin", "Test aus der Verwaltung")):
        return f
    return jsonify({"ok": True})


@app.get("/api/admin/hausdienst")
@nur_ingress
def api_admin_hausdienst():
    tag = heute()
    tage = [tag + datetime.timedelta(days=i) for i in range(HD_VERWALTUNG_TAGE)]
    return jsonify({
        "wochentage": WOCHENTAGE,
        "personen": [dict(r) for r in db().execute(
            "SELECT p.id, p.name, p.benutzer_id, b.name AS zugang FROM hd_personen p"
            " LEFT JOIN benutzer b ON b.id = p.benutzer_id ORDER BY p.name")],
        # Verknuepfen lassen sich die Zugaenge mit Haken "Familie"
        "zugaenge": [dict(r) for r in db().execute(
            "SELECT id, name, status FROM benutzer WHERE familie = 1 ORDER BY name")],
        "aufgaben": [dict(r) for r in db().execute("SELECT * FROM hd_aufgaben ORDER BY name")],
        "plan": [dict(r) for r in db().execute(
            "SELECT p.id, p.wochentag, p.person_id, b.name, p.aufgabe_id, a.name AS aufgabe FROM hd_plan p"
            " JOIN hd_personen b ON b.id = p.person_id JOIN hd_aufgaben a ON a.id = p.aufgabe_id"
            " ORDER BY p.wochentag, b.name, a.name")],
        "tage": [{"datum": t.isoformat(), "tag": datum_text(t), "dienste": dienste_am(t)} for t in tage],
        "pausen": [dict(r) | {"text": f"{datum_text(datum_lesen(r['von']))} bis {datum_text(datum_lesen(r['bis']))}"}
                   for r in db().execute(
                       "SELECT p.id, p.von, p.bis, b.name FROM hd_pausen p JOIN hd_personen b ON b.id = p.person_id"
                       " WHERE p.bis >= ? ORDER BY p.von", (tag.isoformat(),))],
        "antraege": antraege_laden(),
        "anfragen": [{k: a[k] for k in ("id", "von_name", "an_name", "dienst", "status", "angelegt")}
                     for a in anfragen_laden("f.datum >= ?", ((tag - datetime.timedelta(days=7)).isoformat(),), 15)],
        "anrufe": {
            "moeglich": bool(SIP_ADDON and SIP_SERVER),
            "an": einstellung_lesen("hd_anrufe") == "1",
            "ansage": einstellung_lesen("hd_ansage") or HD_ANSAGE_STANDARD,
            "standard": HD_ANSAGE_STANDARD,
            "sip_addon": SIP_ADDON, "sip_konto": SIP_KONTO, "sip_server": SIP_SERVER,
        },
        "telefon": {r["person_id"]: {"nummer": r["nummer"], "zeit": r["zeit"], "tage": json.loads(r["tage"] or "{}")}
                    for r in db().execute("SELECT * FROM hd_telefon")},
        "anrufliste": [dict(r) for r in db().execute(
            "SELECT a.datum, a.versuch, a.zeit, a.bestaetigt, a.fehler, b.name FROM hd_anrufe a"
            " JOIN hd_personen b ON b.id = a.person_id ORDER BY a.id DESC LIMIT 30")],
    })


def person_id(wert):
    pid = ganzzahl(wert)
    return pid if pid and db().execute("SELECT 1 FROM hd_personen WHERE id = ?", (pid,)).fetchone() else None


def aufgabe_id(wert):
    aid = ganzzahl(wert)
    return aid if aid and db().execute("SELECT 1 FROM hd_aufgaben WHERE id = ?", (aid,)).fetchone() else None


@app.post("/api/admin/hausdienst/person")
@nur_ingress
def api_admin_hd_person():
    """Eine Person anlegen oder aendern, optional mit einem Zugang verknuepft."""
    e = json_eingabe()
    name = str(e.get("name", "")).strip()[:40]
    bid = ganzzahl(e.get("benutzer_id")) if e.get("benutzer_id") not in (None, "") else None
    if not name:
        return fehler("Bitte einen Namen angeben.")
    if bid and not db().execute("SELECT 1 FROM benutzer WHERE id = ? AND familie = 1", (bid,)).fetchone():
        return fehler("Verknüpfen lassen sich nur Zugänge mit Haken „Familie“.")
    try:
        if e.get("id"):
            db().execute("UPDATE hd_personen SET name = ?, benutzer_id = ? WHERE id = ?", (name, bid, ganzzahl(e["id"])))
        else:
            db().execute("INSERT INTO hd_personen (name, benutzer_id) VALUES (?, ?)", (name, bid))
    except sqlite3.IntegrityError:
        return fehler("Diesen Namen oder diesen Zugang gibt es im Hausdienst schon.")
    db().commit()
    return jsonify({"ok": True})


@app.post("/api/admin/hausdienst/aufgabe")
@nur_ingress
def api_admin_hd_aufgabe():
    e = json_eingabe()
    name, ansage = str(e.get("name", "")).strip()[:40], str(e.get("ansage", "")).strip()[:60]
    if not name or not ansage:
        return fehler("Bitte Name und Wort für die Ansage angeben.")
    try:
        if e.get("id"):
            db().execute("UPDATE hd_aufgaben SET name = ?, ansage = ? WHERE id = ?", (name, ansage, ganzzahl(e["id"])))
        else:
            db().execute("INSERT INTO hd_aufgaben (name, ansage) VALUES (?, ?)", (name, ansage))
    except sqlite3.IntegrityError:
        return fehler("Diese Aufgabe gibt es schon.")
    db().commit()
    return jsonify({"ok": True})


@app.post("/api/admin/hausdienst/plan")
@nur_ingress
def api_admin_hd_plan():
    e = json_eingabe()
    wochentag, pid, aid = ganzzahl(e.get("wochentag")), person_id(e.get("person_id")), aufgabe_id(e.get("aufgabe_id"))
    if wochentag not in range(7) or not pid or not aid:
        return fehler("Bitte Tag, Person und Aufgabe wählen.")
    db().execute("INSERT OR IGNORE INTO hd_plan (wochentag, person_id, aufgabe_id) VALUES (?, ?, ?)",
                 (wochentag, pid, aid))
    db().commit()
    return jsonify({"ok": True})


@app.post("/api/admin/hausdienst/tag")
@nur_ingress
def api_admin_hd_tag():
    """Einen einzelnen Tag umplanen: einen Planeintrag an jemand anderen
    geben oder ausfallen lassen (person_id leer) - oder ohne plan_id einen
    zusaetzlichen Dienst nur fuer diesen Tag eintragen."""
    e = json_eingabe()
    tag = datum_lesen(e.get("datum"))
    if not tag:
        return fehler("Bitte ein Datum angeben.")
    pid = person_id(e.get("person_id"))
    if e.get("person_id") not in (None, "") and not pid:
        return fehler("Diese Person gibt es im Hausdienst nicht.")
    if e.get("plan_id"):
        p = db().execute("SELECT * FROM hd_plan WHERE id = ?", (ganzzahl(e["plan_id"]),)).fetchone()
        if not p or p["wochentag"] != tag.weekday():
            return fehler("Dieser Planeintrag gilt an dem Tag nicht.")
        abweichung_setzen(tag, p, pid)
    else:
        aid = aufgabe_id(e.get("aufgabe_id"))
        if not pid or not aid:
            return fehler("Bitte Person und Aufgabe wählen.")
        db().execute("INSERT INTO hd_abweichungen (datum, aufgabe_id, person_id) VALUES (?, ?, ?)",
                     (tag.isoformat(), aid, pid))
    db().commit()
    return jsonify({"ok": True})


def abweichung_setzen(tag, p, pid):
    db().execute("DELETE FROM hd_abweichungen WHERE datum = ? AND plan_id = ?", (tag.isoformat(), p["id"]))
    # Wieder die Person aus dem Plan: dann ist es keine Abweichung mehr
    if pid != p["person_id"]:
        db().execute("INSERT INTO hd_abweichungen (datum, plan_id, aufgabe_id, person_id) VALUES (?, ?, ?, ?)",
                     (tag.isoformat(), p["id"], p["aufgabe_id"], pid))


@app.post("/api/admin/hausdienst/pause")
@nur_ingress
def api_admin_hd_pause():
    e = json_eingabe()
    pid, von, bis = person_id(e.get("person_id")), datum_lesen(e.get("von")), datum_lesen(e.get("bis"))
    if not pid or not von or not bis or bis < von:
        return fehler("Bitte Person und einen gültigen Zeitraum angeben.")
    db().execute("INSERT INTO hd_pausen (person_id, von, bis) VALUES (?, ?, ?)", (pid, von.isoformat(), bis.isoformat()))
    db().commit()
    return jsonify({"ok": True})


@app.post("/api/admin/hausdienst/anrufe")
@nur_ingress
def api_admin_hd_anrufe():
    e = json_eingabe()
    if "an" in e:
        einstellung_setzen("hd_anrufe", "1" if e["an"] else "0")
    if "ansage" in e:
        ansage = str(e["ansage"]).strip()[:400]
        if ansage and "{dienste}" not in ansage:
            return fehler("In der Ansage fehlt {dienste} – sonst hört niemand, was zu tun ist.")
        einstellung_setzen("hd_ansage", ansage)
    return jsonify({"ok": True})


@app.post("/api/admin/hausdienst/telefon")
@nur_ingress
def api_admin_hd_telefon():
    e = json_eingabe()
    pid = person_id(e.get("person_id"))
    nummer = re.sub(r"[\s/()-]", "", str(e.get("nummer", "")))
    if not pid:
        return fehler("Diese Person gibt es im Hausdienst nicht.")
    if nummer and not NUMMER_MUSTER.match(nummer):
        return fehler("Die Telefonnummer bitte nur aus Ziffern, z. B. 01701234567.")
    zeit, tage, fehl = anrufzeiten_lesen(e)
    if fehl:
        return fehler(fehl)
    db().execute("INSERT INTO hd_telefon (person_id, nummer, zeit, tage) VALUES (?, ?, ?, ?)"
                 " ON CONFLICT(person_id) DO UPDATE SET nummer = excluded.nummer, zeit = excluded.zeit,"
                 " tage = excluded.tage", (pid, nummer, zeit, tage))
    db().commit()
    return jsonify({"ok": True})


@app.post("/api/admin/hausdienst/testanruf")
@nur_ingress
def api_admin_hd_testanruf():
    """Ruft sofort an - mit den Diensten von heute oder, wenn heute keiner
    ansteht, mit dem naechsten."""
    if not SIP_ADDON or not SIP_SERVER:
        return fehler("ha-sip ist in den Optionen nicht eingestellt.", 404)
    pid = person_id(json_eingabe().get("person_id"))
    t = db().execute("SELECT * FROM hd_telefon WHERE person_id = ?", (pid,)).fetchone() if pid else None
    if not t or not t["nummer"]:
        return fehler("Für diese Person ist keine Telefonnummer eingetragen.")
    tag = heute()
    for i in range(HD_MEIN_PLAN_TAGE):
        dienste = [d for d in faellige_dienste(tag + datetime.timedelta(days=i)) if d["person_id"] == pid]
        if dienste:
            break
    else:
        return fehler("Diese Person hat in den nächsten vier Wochen keinen Dienst.")
    ansage = ansage_bauen(dienste[0]["name"], [d["ansage"] for d in dienste], tag + datetime.timedelta(days=i))
    if (fehl := anrufen(pid, 0, ansage, t["nummer"], tag, time.time())):
        return fehler(f"Anruf ging nicht raus: {fehl}", 502)
    return jsonify({"ok": True, "ansage": ansage})


HD_TABELLEN = {"person": "hd_personen", "aufgabe": "hd_aufgaben", "plan": "hd_plan", "tag": "hd_abweichungen", "pause": "hd_pausen"}


@app.post("/api/admin/hausdienst/<art>/<int:eid>/loeschen")
@nur_ingress
def api_admin_hd_loeschen(art, eid):
    if art not in HD_TABELLEN:
        abort(404)
    db().execute(f"DELETE FROM {HD_TABELLEN[art]} WHERE id = ?", (eid,))
    db().commit()
    return jsonify({"ok": True})


@app.post("/api/admin/hausdienst/antrag/<int:aid>/<entscheidung>")
@nur_ingress
def api_admin_hd_antrag(aid, entscheidung):
    if entscheidung not in ("genehmigen", "ablehnen"):
        abort(404)
    gefunden = antraege_laden("h.id = ? AND h.status = 'offen'", (aid,))
    if not gefunden:
        return fehler("Diesen offenen Antrag gibt es nicht.", 404)
    a = gefunden[0]
    tag = datum_lesen(a["datum"])
    if entscheidung == "genehmigen":
        if a["art"] == "pause":
            db().execute("INSERT INTO hd_pausen (person_id, von, bis) VALUES (?, ?, ?)",
                         (a["person_id"], a["datum"], (tag + datetime.timedelta(days=a["tage"] - 1)).isoformat()))
        else:
            dienst = next((d for d in faellige_dienste(tag)
                           if d["person_id"] == a["person_id"] and d["aufgabe_id"] == a["aufgabe_id"]), None)
            if not dienst:
                return fehler("Dieser Dienst steht so nicht mehr im Plan – bitte den Tag selbst umplanen.")
            if pausiert_am(a["an_id"], tag):
                return fehler(f"{a['an_name']} hat an dem Tag inzwischen Pause – bitte ablehnen oder selbst umplanen.")
            dienst_uebergeben(tag, dienst, a["an_id"])
    db().execute("UPDATE hd_antraege SET status = ?, entschieden = ? WHERE id = ?",
                 ("genehmigt" if entscheidung == "genehmigen" else "abgelehnt", int(time.time()), aid))
    db().commit()
    glocke_weg(f"hausdienst_antrag_{aid}")
    protokollieren(f"admin_hd_{entscheidung}", None, a["text"], name="Admin")
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
    if WARMWASSER_ENTITAET:
        log.info("Warmwasser: %s", WARMWASSER_ENTITAET)
    if STAUBSAUGER_ENTITAET:
        log.info("Staubsauger: %s", STAUBSAUGER_ENTITAET)
    if KAMERA_ENTITAET:
        log.info("Kamera: %s", KAMERA_ENTITAET)
    if not passkey_moeglich():
        log.warning("Keine externe https-Adresse eingestellt - Passkeys sind aus")
    if SIP_ADDON:
        log.info("Hausdienst-Anrufe ueber %s, Konto %s (Rueckmeldung an %s)", SIP_ADDON, SIP_KONTO, EIGENER_SLUG)
    threading.Thread(target=anruf_schleife, name="hausdienst", daemon=True).start()
    threading.Thread(target=stdin_lesen, args=(sys.stdin,), name="stdin", daemon=True).start()
    serve(app, host="0.0.0.0", port=PORT, threads=8, ident=None)
