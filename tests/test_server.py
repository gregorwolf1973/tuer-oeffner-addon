"""Tests fuer den Tueroeffner. Home Assistant wird nachgebildet, die
Datenbank ist echt (SQLite in einem Wegwerf-Verzeichnis)."""

import importlib
import os
import sys
import tempfile
import time

import pyotp
import pytest

HEIM = (48.137154, 11.576124)  # irgendein Punkt, nur fuer die Rechnung
INGRESS = {"REMOTE_ADDR": "172.30.32.2"}

os.environ.update(
    DATA_DIR=tempfile.mkdtemp(prefix="tuer-test-"),
    COOKIE_SECURE="0",
    TUER_ENTITAET="switch.haustuer",
    EXTERNE_ADRESSE="https://tuer.example.org",
    BENACHRICHTIGUNG="notify.mobile_app_test",
    BREITENGRAD=str(HEIM[0]),
    LAENGENGRAD=str(HEIM[1]),
    RADIUS_METER="200",
    WARMWASSER_ENTITAET="button.einmalige_ladung",
    WARMWASSER_TEMPERATUR="sensor.ww_temperatur",
    WARMWASSER_LAEUFT="binary_sensor.ww_laedt",
    WARMWASSER_HEIZT="binary_sensor.kompressor",
    WARMWASSER_SOLL="number.ww_ziel",
    STAUBSAUGER_ENTITAET="vacuum.sauger",
    STAUBSAUGER_BATTERIE="sensor.sauger_akku",
    KAMERA_ENTITAET="camera.haustuer",
    POLLEN_PRAEFIX="sensor.pollenflug_",
    WLAN_NAME="Gast",
    WLAN_SCHLUESSEL="geheim;123",
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tuer_oeffner", "app"))
server = importlib.import_module("server")


@pytest.fixture()
def ha(monkeypatch):
    """Zeichnet jeden Aufruf an Home Assistant auf."""
    aufrufe = []

    def nachgebildet(methode, pfad, daten=None):
        aufrufe.append((methode, pfad, daten))
        if pfad == "/config":
            return {"latitude": HEIM[0], "longitude": HEIM[1]}
        if pfad == "/states":
            return [{"entity_id": k} | v for k, v in ZUSTAENDE.items()]
        if pfad.startswith("/states/"):
            return ZUSTAENDE.get(pfad.removeprefix("/states/"))
        return []

    monkeypatch.setattr(server, "ha_aufruf", nachgebildet)
    return aufrufe


ZUSTAENDE = {}


@pytest.fixture(autouse=True)
def sauber():
    conn = server.verbinden()
    conn.executescript("DELETE FROM hd_bewertungen; DELETE FROM hd_anfragen; DELETE FROM hd_anrufe; DELETE FROM hd_telefon; DELETE FROM hd_antraege; DELETE FROM hd_pausen; DELETE FROM hd_abweichungen;"
                       " DELETE FROM hd_plan; DELETE FROM hd_aufgaben; DELETE FROM hd_personen;"
                       " DELETE FROM standorte; DELETE FROM passkeys; DELETE FROM benutzer; DELETE FROM protokoll;"
                       " DELETE FROM einstellungen;")
    conn.commit()
    conn.close()
    server._ip_fehler.clear()
    server._zuletzt_geoeffnet["t"] = 0.0
    server._zuletzt_aufgeheizt["t"] = 0.0
    ZUSTAENDE.clear()
    ZUSTAENDE.update({
        "sensor.ww_temperatur": {"state": "41.25", "attributes": {"unit_of_measurement": "°C"}},
        "binary_sensor.ww_laedt": {"state": "off", "attributes": {}},
        "binary_sensor.kompressor": {"state": "off", "attributes": {}},
        "number.ww_ziel": {"state": "50", "attributes": {"min": 10, "max": 60, "step": 1}},
        "vacuum.sauger": {"state": "docked", "attributes": {"battery_level": 50}},
        "sensor.sauger_akku": {"state": "87", "attributes": {}},
    })
    server._zuletzt_gesaugt["t"] = 0.0
    server._kamera.update(t=0.0, bild=None)
    server._pollen.update(t=0.0, daten=None)
    yield


@pytest.fixture()
def client():
    return server.app.test_client()


def post(c, pfad, daten=None, **kw):
    return c.post(pfad, json=daten or {}, headers={"X-Tuer": "1"}, **kw)


def geheimnis(email):
    conn = server.verbinden()
    try:
        return conn.execute("SELECT totp_geheimnis FROM benutzer WHERE email = ?", (email,)).fetchone()[0]
    finally:
        conn.close()


def benutzer_id(email):
    conn = server.verbinden()
    try:
        return conn.execute("SELECT id FROM benutzer WHERE email = ?", (email,)).fetchone()[0]
    finally:
        conn.close()


def naechster_code(email, schritte=1):
    """Ein Code aus einem spaeteren Zeitschritt - der aktuelle ist nach dem
    Einrichten schon verbraucht (Schutz vor Wiederverwendung)."""
    return pyotp.TOTP(geheimnis(email)).at(int(time.time()) + 30 * schritte)


def anmeldung(name="anna", email="anna@example.org", passwort="geheim-genug-123", **mehr):
    return {"name": name.capitalize(), "benutzername": name, "email": email, "passwort": passwort} | mehr


def registrieren(c, email="anna@example.org", name="Anna"):
    r = post(c, "/api/registrieren", anmeldung(name.lower(), email))
    assert r.status_code == 200, r.json
    code = pyotp.TOTP(geheimnis(email)).now()
    r = post(c, "/api/2fa/bestaetigen", {"code": code})
    assert r.status_code == 200, r.json


def freigeben(email, fern=False, familie=False):
    admin = server.app.test_client()
    bid = benutzer_id(email)
    assert post(admin, f"/api/admin/benutzer/{bid}/freigeben", environ_base=INGRESS).status_code == 200
    if fern:
        assert post(admin, f"/api/admin/benutzer/{bid}/fern_an", environ_base=INGRESS).status_code == 200
    if familie:
        assert post(admin, f"/api/admin/benutzer/{bid}/familie_an", environ_base=INGRESS).status_code == 200


def nah():
    return {"modus": "nah", "lat": HEIM[0] + 0.0005, "lon": HEIM[1], "genauigkeit": 15}  # ~55 m


# ------------------------------------------------------------------


def test_registrierung_wartet_auf_freigabe_und_meldet_den_admin(client, ha):
    registrieren(client)
    s = client.get("/api/status").json
    assert s["angemeldet"] and s["stufe"] == "voll" and s["status"] == "wartet"
    assert any(p == "/services/notify/mobile_app_test" and "wartet auf Freigabe" in d["message"]
               for _, p, d in ha)
    r = post(client, "/api/oeffnen", nah())
    assert r.status_code == 403
    assert not any(p.startswith("/services/switch") for _, p, _ in ha)


def test_falscher_code_beim_einrichten(client, ha):
    post(client, "/api/registrieren", anmeldung("aaa", "a@example.org"))
    r = post(client, "/api/2fa/bestaetigen", {"code": "000000"})
    assert r.status_code == 400
    assert client.get("/api/status").json["stufe"] == "einrichten"


def test_qr_nur_beim_einrichten(client, ha):
    post(client, "/api/registrieren", anmeldung("aaa", "a@example.org"))
    d = client.get("/api/2fa/einrichten").json
    assert d["uri"].startswith("otpauth://totp/") and "<svg" in d["svg"]
    post(client, "/api/2fa/bestaetigen", {"code": pyotp.TOTP(geheimnis("a@example.org")).now()})
    # Danach gibt es das Geheimnis nicht mehr
    assert client.get("/api/2fa/einrichten").status_code == 401


def test_kurzes_passwort_und_doppelte_mail(client, ha):
    r = post(client, "/api/registrieren", anmeldung("aaa", "a@example.org", passwort="1234567"))
    assert r.status_code == 400 and "mindestens 8" in r.json["fehler"]
    registrieren(client, "a@example.org")
    r = post(server.app.test_client(), "/api/registrieren", anmeldung("bbb", "A@Example.org"))
    assert r.status_code == 400 and "schon registriert" in r.json["fehler"]


def test_acht_zeichen_reichen(client, ha):
    r = post(client, "/api/registrieren", anmeldung(passwort="12345678"))
    assert r.status_code == 200, r.json
    post(client, "/api/2fa/bestaetigen", {"code": pyotp.TOTP(geheimnis("anna@example.org")).now()})
    assert post(client, "/api/passwort", {"alt": "12345678", "neu": "1234567"}).status_code == 400
    assert post(client, "/api/passwort", {"alt": "12345678", "neu": "abcdefgh"}).status_code == 200


def test_benutzername_pruefen(client, ha):
    for falsch in ("ab", "mit leer", "a@b", "x" * 31):
        r = post(client, "/api/registrieren", anmeldung(falsch))
        assert r.status_code == 400 and "Benutzername" in r.json["fehler"], falsch
    registrieren(client)
    r = post(server.app.test_client(), "/api/registrieren", anmeldung("Anna", "andere@example.org"))
    assert r.status_code == 400 and "Benutzernamen gibt es schon" in r.json["fehler"]


def test_anmelden_mit_benutzername(client, ha):
    registrieren(client)
    for kennung in ("anna", " Anna ", "anna@example.org"):
        c = server.app.test_client()
        r = post(c, "/api/anmelden", {"kennung": kennung, "passwort": "geheim-genug-123"})
        assert r.status_code == 200, kennung
        assert c.get("/api/status").json["stufe"] == "pw"
    r = post(server.app.test_client(), "/api/anmelden", {"kennung": "anna", "passwort": "falsch-falsch"})
    assert r.status_code == 401 and "Benutzername/Mailadresse" in r.json["fehler"]


def test_oeffnen_in_der_naehe(client, ha):
    registrieren(client)
    freigeben("anna@example.org")
    r = post(client, "/api/oeffnen", nah())
    assert r.status_code == 200, r.json
    assert ("POST", "/services/switch/turn_on", {"entity_id": "switch.haustuer"}) in ha
    assert any("Anna hat die Haustür geöffnet." == d.get("message") for _, p, d in ha if "notify" in p)


def test_zu_weit_weg(client, ha):
    registrieren(client)
    freigeben("anna@example.org")
    r = post(client, "/api/oeffnen", {"modus": "nah", "lat": HEIM[0] + 0.02, "lon": HEIM[1], "genauigkeit": 10})
    assert r.status_code == 403
    assert "2,2 km" in r.json["fehler"]
    assert not any(p.startswith("/services/switch") for _, p, _ in ha)


def test_ungenauigkeit_hilft_nur_begrenzt(client, ha):
    registrieren(client)
    freigeben("anna@example.org")
    # 1,1 km weg, aber 5 km "ungenau" - zaehlt nur bis 100 m
    r = post(client, "/api/oeffnen", {"modus": "nah", "lat": HEIM[0] + 0.01, "lon": HEIM[1], "genauigkeit": 5000})
    assert r.status_code == 403


def test_ohne_standort_kein_oeffnen(client, ha):
    registrieren(client)
    freigeben("anna@example.org")
    assert post(client, "/api/oeffnen", {"modus": "nah"}).status_code == 400


def test_fern_nur_mit_recht(client, ha):
    registrieren(client)
    freigeben("anna@example.org")
    assert post(client, "/api/oeffnen", {"modus": "fern"}).status_code == 403
    freigeben_fern = server.app.test_client()
    bid = benutzer_id("anna@example.org")
    post(freigeben_fern, f"/api/admin/benutzer/{bid}/fern_an", environ_base=INGRESS)
    r = post(client, "/api/oeffnen", {"modus": "fern"})
    assert r.status_code == 200
    assert any("aus der Ferne" in d.get("message", "") for _, p, d in ha if "notify" in p)


def test_abklingzeit(client, ha):
    registrieren(client)
    freigeben("anna@example.org")
    assert post(client, "/api/oeffnen", nah()).status_code == 200
    assert post(client, "/api/oeffnen", nah()).status_code == 429


def test_fehler_von_home_assistant(client, monkeypatch):
    def kaputt(methode, pfad, daten=None):
        if pfad.startswith("/services/switch"):
            raise RuntimeError("HA weg")
        return []

    monkeypatch.setattr(server, "ha_aufruf", kaputt)
    registrieren(client)
    freigeben("anna@example.org")
    r = post(client, "/api/oeffnen", nah())
    assert r.status_code == 502


def test_ohne_kopf_kein_post(client, ha):
    r = client.post("/api/anmelden", json={"email": "x", "passwort": "y"})
    assert r.status_code == 403


def test_anmelden_mit_passwort_und_code(client, ha):
    registrieren(client)
    neu = server.app.test_client()
    r = post(neu, "/api/anmelden", {"email": "anna@example.org", "passwort": "geheim-genug-123"})
    assert r.status_code == 200
    assert neu.get("/api/status").json["stufe"] == "pw"
    # Ohne Code kein Oeffnen
    assert post(neu, "/api/oeffnen", nah()).status_code == 401
    code = naechster_code("anna@example.org")
    assert post(neu, "/api/anmelden/code", {"code": code}).status_code == 200
    assert neu.get("/api/status").json["stufe"] == "voll"
    # Derselbe Code ein zweites Mal: abgelehnt
    dritter = server.app.test_client()
    post(dritter, "/api/anmelden", {"email": "anna@example.org", "passwort": "geheim-genug-123"})
    assert post(dritter, "/api/anmelden/code", {"code": code}).status_code == 401


def test_konto_sperre_nach_fehlversuchen(client, ha):
    registrieren(client)
    c = server.app.test_client()
    for _ in range(server.KONTO_FEHLER_MAX):
        assert post(c, "/api/anmelden", {"email": "anna@example.org", "passwort": "falsch"}).status_code == 401
    r = post(c, "/api/anmelden", {"email": "anna@example.org", "passwort": "geheim-genug-123"})
    assert r.status_code == 429


def test_ip_bremse(client, ha):
    c = server.app.test_client()
    for i in range(server.IP_FEHLER_MAX):
        post(c, "/api/anmelden", {"kennung": f"x{i}", "passwort": "falsch"})
    r = post(c, "/api/anmelden", {"email": "y@example.org", "passwort": "falsch"})
    assert r.status_code == 429


def test_sperren_beendet_die_sitzung(client, ha):
    registrieren(client)
    freigeben("anna@example.org")
    bid = benutzer_id("anna@example.org")
    post(server.app.test_client(), f"/api/admin/benutzer/{bid}/sperren", environ_base=INGRESS)
    assert client.get("/api/status").json["angemeldet"] is False
    assert post(client, "/api/oeffnen", nah()).status_code == 401
    r = post(server.app.test_client(), "/api/anmelden", {"email": "anna@example.org", "passwort": "geheim-genug-123"})
    assert r.status_code == 403


def test_2fa_zuruecksetzen_braucht_neue_freigabe(client, ha):
    registrieren(client)
    freigeben("anna@example.org")
    bid = benutzer_id("anna@example.org")
    post(server.app.test_client(), f"/api/admin/benutzer/{bid}/2fa_zuruecksetzen", environ_base=INGRESS)
    assert client.get("/api/status").json["angemeldet"] is False
    c = server.app.test_client()
    post(c, "/api/anmelden", {"email": "anna@example.org", "passwort": "geheim-genug-123"})
    assert c.get("/api/status").json["stufe"] == "einrichten"
    post(c, "/api/2fa/bestaetigen", {"code": pyotp.TOTP(geheimnis("anna@example.org")).now()})
    assert c.get("/api/status").json["status"] == "wartet"


def test_passwort_aendern_meldet_andere_geraete_ab(client, ha):
    registrieren(client)
    zweites = server.app.test_client()
    post(zweites, "/api/anmelden", {"email": "anna@example.org", "passwort": "geheim-genug-123"})
    post(zweites, "/api/anmelden/code", {"code": naechster_code("anna@example.org")})
    r = post(client, "/api/passwort", {"alt": "geheim-genug-123", "neu": "noch-geheimer-456"})
    assert r.status_code == 200
    assert client.get("/api/status").json["angemeldet"] is True
    assert zweites.get("/api/status").json["angemeldet"] is False


def test_verwaltung_nur_ueber_ingress(client, ha):
    assert client.get("/api/admin/uebersicht").status_code == 404
    assert post(client, "/api/admin/oeffnen").status_code == 404
    # Ein gesetzter Kopf hilft nicht - entscheidend ist der Absender
    r = client.get("/api/admin/uebersicht", headers={"X-Ingress-Path": "/api/hassio_ingress/x"})
    assert r.status_code == 404
    assert client.get("/api/admin/uebersicht", environ_base=INGRESS).status_code == 200


def test_startseite_je_nach_absender(client, ha):
    assert b"Verwaltung" not in client.get("/").data
    assert b"Verwaltung" in client.get("/", environ_base=INGRESS).data


def test_admin_test_oeffnen(client, ha):
    r = post(client, "/api/admin/oeffnen", environ_base=INGRESS)
    assert r.status_code == 200
    assert ("POST", "/services/switch/turn_on", {"entity_id": "switch.haustuer"}) in ha


def test_automation_wird_ausgeloest(client, ha, monkeypatch):
    monkeypatch.setattr(server, "TUER_ENTITAET", "automation.door_open")
    post(client, "/api/admin/oeffnen", environ_base=INGRESS)
    assert ("POST", "/services/automation/trigger", {"entity_id": "automation.door_open"}) in ha


def test_freigeben_erst_nach_2fa(client, ha):
    post(client, "/api/registrieren", anmeldung("aaa", "a@example.org"))
    bid = benutzer_id("a@example.org")
    r = post(server.app.test_client(), f"/api/admin/benutzer/{bid}/freigeben", environ_base=INGRESS)
    assert r.status_code == 400


def test_passkey_optionen(client, ha):
    registrieren(client)
    o = post(client, "/api/passkey/registrieren/start").json
    assert o["rp"]["id"] == "tuer.example.org"
    assert o["authenticatorSelection"]["residentKey"] == "required"
    o = post(server.app.test_client(), "/api/passkey/anmelden/start").json
    assert o["rpId"] == "tuer.example.org" and o["userVerification"] == "required"


def test_unbekannter_passkey(client, ha):
    c = server.app.test_client()
    post(c, "/api/passkey/anmelden/start")
    r = post(c, "/api/passkey/anmelden/ende", {"credential": {"id": "AAAA", "rawId": "AAAA", "type": "public-key",
                                                               "response": {}}})
    assert r.status_code == 401
    assert c.get("/api/status").json["angemeldet"] is False


def test_ohne_pflicht_reicht_das_passwort(client, ha, monkeypatch):
    monkeypatch.setattr(server, "ZWEI_FAKTOR_PFLICHT", False)
    assert post(client, "/api/registrieren", anmeldung()).status_code == 200
    s = client.get("/api/status").json
    assert s["stufe"] == "voll" and s["status"] == "wartet" and s["zwei_faktor"] is False
    assert any("wartet auf Freigabe" in d.get("message", "") for _, p, d in ha if "notify" in p)
    freigeben("anna@example.org")
    c = server.app.test_client()
    assert post(c, "/api/anmelden", {"kennung": "anna", "passwort": "geheim-genug-123"}).status_code == 200
    assert c.get("/api/status").json["stufe"] == "voll"
    assert post(c, "/api/oeffnen", nah()).status_code == 200


def test_2fa_selbst_ein_und_ausschalten(client, ha, monkeypatch):
    monkeypatch.setattr(server, "ZWEI_FAKTOR_PFLICHT", False)
    post(client, "/api/registrieren", anmeldung())
    zweites = server.app.test_client()
    post(zweites, "/api/anmelden", {"kennung": "anna", "passwort": "geheim-genug-123"})
    # Einschalten: QR holen, Code bestaetigen
    d = post(client, "/api/2fa/einschalten/start").json
    assert "<svg" in d["svg"]
    assert post(client, "/api/2fa/einschalten", {"code": "000000"}).status_code == 400
    r = post(client, "/api/2fa/einschalten", {"code": pyotp.TOTP(geheimnis("anna@example.org")).now()})
    assert r.status_code == 200
    assert client.get("/api/status").json["zwei_faktor"] is True
    # Das andere Geraet war nur mit Passwort drin - jetzt abgemeldet
    assert zweites.get("/api/status").json["angemeldet"] is False
    c = server.app.test_client()
    post(c, "/api/anmelden", {"kennung": "anna", "passwort": "geheim-genug-123"})
    assert c.get("/api/status").json["stufe"] == "pw"
    # Ausschalten braucht Passwort UND Code
    code = naechster_code("anna@example.org")
    assert post(client, "/api/2fa/ausschalten", {"passwort": "falsch-falsch", "code": code}).status_code == 401
    assert post(client, "/api/2fa/ausschalten", {"passwort": "geheim-genug-123", "code": "000000"}).status_code == 401
    r = post(client, "/api/2fa/ausschalten", {"passwort": "geheim-genug-123", "code": code})
    assert r.status_code == 200
    assert client.get("/api/status").json["zwei_faktor"] is False
    assert any("ausgeschaltet" in d.get("message", "") for _, p, d in ha if "notify" in p)
    c = server.app.test_client()
    post(c, "/api/anmelden", {"kennung": "anna", "passwort": "geheim-genug-123"})
    assert c.get("/api/status").json["stufe"] == "voll"


def test_ausschalten_geht_nicht_bei_pflicht(client, ha):
    registrieren(client)
    r = post(client, "/api/2fa/ausschalten",
             {"passwort": "geheim-genug-123", "code": naechster_code("anna@example.org")})
    assert r.status_code == 403
    assert client.get("/api/status").json["zwei_faktor"] is True


def test_pflicht_nachtraeglich_eingeschaltet(client, ha, monkeypatch):
    monkeypatch.setattr(server, "ZWEI_FAKTOR_PFLICHT", False)
    post(client, "/api/registrieren", anmeldung())
    freigeben("anna@example.org")
    monkeypatch.setattr(server, "ZWEI_FAKTOR_PFLICHT", True)
    # Die laufende Sitzung muss erst die 2FA einrichten
    assert client.get("/api/status").json["stufe"] == "einrichten"
    assert post(client, "/api/oeffnen", nah()).status_code == 401
    post(client, "/api/2fa/bestaetigen", {"code": pyotp.TOTP(geheimnis("anna@example.org")).now()})
    s = client.get("/api/status").json
    assert s["stufe"] == "voll" and s["status"] == "aktiv" and s["zwei_faktor"] is True


def test_alte_datenbank_wird_nachgeruestet(monkeypatch, tmp_path):
    import sqlite3
    pfad = tmp_path / "tuer.db"
    conn = sqlite3.connect(pfad)
    conn.executescript("""
        CREATE TABLE benutzer (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
            pw_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'neu', fern INTEGER NOT NULL DEFAULT 0,
            totp_geheimnis TEXT, totp_letzter_schritt INTEGER NOT NULL DEFAULT 0, webauthn_id TEXT NOT NULL,
            sitzung_gen INTEGER NOT NULL DEFAULT 1, fehlversuche INTEGER NOT NULL DEFAULT 0,
            gesperrt_bis INTEGER NOT NULL DEFAULT 0, angelegt INTEGER NOT NULL, freigegeben INTEGER,
            zuletzt_geoeffnet INTEGER);
        INSERT INTO benutzer (name, email, pw_hash, status, webauthn_id, angelegt) VALUES
            ('A', 'a@x.org', '-', 'aktiv', 'w1', 0), ('B', 'b@x.org', '-', 'neu', 'w2', 0);
    """)
    conn.close()
    monkeypatch.setattr(server, "DB_PFAD", str(pfad))
    server.db_einrichten()
    conn = sqlite3.connect(pfad)
    assert conn.execute("SELECT email, zwei_faktor, benutzername, familie FROM benutzer ORDER BY id").fetchall() == [
        ("a@x.org", 1, None, 0), ("b@x.org", 0, None, 0)]
    conn.close()


def test_registrierung_geschlossen(client, ha, monkeypatch):
    monkeypatch.setattr(server, "REGISTRIERUNG_OFFEN", False)
    r = post(client, "/api/registrieren", anmeldung("aaa", "a@example.org"))
    assert r.status_code == 403


# ------------------------------------------------------------------
# Warmwasser


def aufgeheizt(ha):
    return [d for _, p, d in ha if p == "/services/button/press" and d["entity_id"] == "button.einmalige_ladung"]


def test_warmwasser_nur_mit_recht(client, ha):
    registrieren(client)
    freigeben("anna@example.org")
    assert client.get("/api/status").json["warmwasser"] is False
    assert client.get("/api/warmwasser").status_code == 403
    assert post(client, "/api/warmwasser/aufheizen").status_code == 403
    assert not aufgeheizt(ha)


def test_warmwasser_braucht_freigabe(client, ha):
    registrieren(client)
    # Recht schon erteilt, aber noch nicht freigegeben
    admin = server.app.test_client()
    post(admin, f"/api/admin/benutzer/{benutzer_id('anna@example.org')}/familie_an", environ_base=INGRESS)
    assert client.get("/api/status").json["warmwasser"] is False
    assert post(client, "/api/warmwasser/aufheizen").status_code == 403


def test_warmwasser_anzeige_und_aufheizen(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    assert client.get("/api/status").json["warmwasser"] is True
    assert client.get("/api/warmwasser").json == {
        "temperatur": 41.2, "einheit": "°C", "laeuft": False, "heizt": False, "soll": 50.0}
    r = post(client, "/api/warmwasser/aufheizen")
    assert r.status_code == 200, r.json
    assert r.json["ziel"] == 50.0
    assert len(aufgeheizt(ha)) == 1
    # Die Waermepumpe stand schon auf der Vorgabe: kein unnoetiger Aufruf an ViCare
    assert not ziel_gesetzt(ha)
    # Gleich noch einmal: gebremst
    assert post(client, "/api/warmwasser/aufheizen").status_code == 429
    assert len(aufgeheizt(ha)) == 1
    admin = server.app.test_client()
    p = admin.get("/api/admin/uebersicht", environ_base=INGRESS).json["protokoll"]
    assert p[0]["aktion"] == "warmwasser" and p[0]["name"] == "Anna" and p[0]["detail"] == "auf 50 °C"


def test_warmwasser_laeuft_schon(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    ZUSTAENDE["binary_sensor.ww_laedt"]["state"] = "on"
    assert client.get("/api/warmwasser").json["laeuft"] is True
    assert post(client, "/api/warmwasser/aufheizen").status_code == 409
    assert not aufgeheizt(ha)


def test_warmwasser_heizt_nur_mit_kompressor(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    ZUSTAENDE["binary_sensor.ww_laedt"]["state"] = "on"
    d = client.get("/api/warmwasser").json
    assert d["laeuft"] is True and d["heizt"] is False
    ZUSTAENDE["binary_sensor.kompressor"]["state"] = "on"
    assert client.get("/api/warmwasser").json["heizt"] is True


def test_warmwasser_schon_warm_genug(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    ZUSTAENDE["sensor.ww_temperatur"]["state"] = "51.2"
    r = post(client, "/api/warmwasser/aufheizen")
    assert r.status_code == 409 and "51,2 °C" in r.json["fehler"] and "50 °C" in r.json["fehler"]
    assert not aufgeheizt(ha) and not ziel_gesetzt(ha)


def ziel_gesetzt(ha):
    return [d["value"] for _, p, d in ha if p == "/services/number/set_value" and d["entity_id"] == "number.ww_ziel"]


def test_vorgabe_statt_eingestelltem_ziel(client, ha):
    """Massgeblich ist die Vorgabe, nicht was gerade in der Waermepumpe steht:
    45 °C eingestellt, 47 °C im Speicher - der Knopf heizt trotzdem auf 50 °C."""
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    ZUSTAENDE["number.ww_ziel"]["state"] = "45"
    ZUSTAENDE["sensor.ww_temperatur"]["state"] = "47"
    assert client.get("/api/warmwasser").json["soll"] == 50.0
    # Nur anzeigen setzt nichts
    assert not ziel_gesetzt(ha)
    assert post(client, "/api/warmwasser/aufheizen").status_code == 200
    assert ziel_gesetzt(ha) == [50.0]
    # Erst das Ziel, dann die Ladung
    reihenfolge = [p for _, p, _ in ha if p in ("/services/number/set_value", "/services/button/press")]
    assert reihenfolge == ["/services/number/set_value", "/services/button/press"]
    admin = server.app.test_client()
    p = admin.get("/api/admin/uebersicht", environ_base=INGRESS).json["protokoll"]
    assert p[0]["detail"] == "auf 50 °C (Ziel vorher 45 °C)"


def test_ziel_nicht_setzbar_heizt_nicht(client, ha, monkeypatch):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    ZUSTAENDE["number.ww_ziel"]["state"] = "45"
    nachgebildet = server.ha_aufruf

    def scheitert(methode, pfad, daten=None):
        if pfad == "/services/number/set_value":
            raise RuntimeError("ViCare nicht erreichbar")
        return nachgebildet(methode, pfad, daten)

    monkeypatch.setattr(server, "ha_aufruf", scheitert)
    r = post(client, "/api/warmwasser/aufheizen")
    assert r.status_code == 502 and "Zieltemperatur" in r.json["fehler"]
    assert not aufgeheizt(ha)


def test_admin_warmwasser_vorgabe(client, ha):
    admin = server.app.test_client()
    ZUSTAENDE["number.ww_ziel"]["state"] = "45"
    d = admin.get("/api/admin/uebersicht", environ_base=INGRESS).json["warmwasser"]
    assert d == {"vorgabe": 50.0, "eingestellt": 45.0, "min": 10, "max": 60, "schritt": 1, "temperatur": 41.2,
                 "laeuft": False}
    r = post(admin, "/api/admin/warmwasser/vorgabe", {"wert": 55}, environ_base=INGRESS)
    assert r.status_code == 200
    # Die Vorgabe wird nur gespeichert - die Waermepumpe bleibt unberuehrt
    assert not ziel_gesetzt(ha)
    d = admin.get("/api/admin/uebersicht", environ_base=INGRESS).json["warmwasser"]
    assert d["vorgabe"] == 55.0 and d["eingestellt"] == 45.0
    assert admin.get("/api/admin/uebersicht", environ_base=INGRESS).json["protokoll"][0]["detail"] == "50 °C → 55 °C"
    assert post(admin, "/api/admin/warmwasser/vorgabe", {"wert": 75}, environ_base=INGRESS).status_code == 400
    assert post(admin, "/api/admin/warmwasser/vorgabe", {"wert": "heiss"}, environ_base=INGRESS).status_code == 400
    # Das alte Setzen direkt an der Waermepumpe gibt es nicht mehr
    assert post(admin, "/api/admin/warmwasser/ziel", {"wert": 55}, environ_base=INGRESS).status_code in (404, 405)
    # Nicht aus der App heraus
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    assert post(client, "/api/admin/warmwasser/vorgabe", {"wert": 55}).status_code == 404
    # Beim Aufheizen gilt die neue Vorgabe
    assert client.get("/api/warmwasser").json["soll"] == 55.0
    assert post(client, "/api/warmwasser/aufheizen").status_code == 200
    assert ziel_gesetzt(ha) == [55.0]


def test_vorgabe_bleibt_in_den_grenzen_der_waermepumpe(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    ZUSTAENDE["number.ww_ziel"]["attributes"]["max"] = 48
    assert client.get("/api/warmwasser").json["soll"] == 48


def test_warmwasser_sensor_nicht_verfuegbar(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    ZUSTAENDE["sensor.ww_temperatur"] = {"state": "unavailable", "attributes": {}}
    del ZUSTAENDE["binary_sensor.ww_laedt"]
    assert client.get("/api/warmwasser").json == {
        "temperatur": None, "einheit": None, "laeuft": None, "heizt": None, "soll": 50.0}
    # Ohne Lade-Anzeige wird trotzdem aufgeheizt
    assert post(client, "/api/warmwasser/aufheizen").status_code == 200
    assert len(aufgeheizt(ha)) == 1


def test_warmwasser_aus_ohne_entitaet(client, ha, monkeypatch):
    monkeypatch.setattr(server, "WARMWASSER_ENTITAET", "")
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    assert client.get("/api/status").json["warmwasser"] is False
    assert post(client, "/api/warmwasser/aufheizen").status_code == 404
    # Der Staubsauger bleibt davon unberuehrt
    assert client.get("/api/status").json["staubsauger"] is True


def test_warmwasser_recht_entziehen_und_sperren(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    admin = server.app.test_client()
    bid = benutzer_id("anna@example.org")
    b = admin.get("/api/admin/uebersicht", environ_base=INGRESS).json["benutzer"][0]
    assert b["familie"] == 1
    post(admin, f"/api/admin/benutzer/{bid}/familie_aus", environ_base=INGRESS)
    assert client.get("/api/status").json["warmwasser"] is False
    post(admin, f"/api/admin/benutzer/{bid}/familie_an", environ_base=INGRESS)
    post(admin, f"/api/admin/benutzer/{bid}/sperren", environ_base=INGRESS)
    assert post(client, "/api/warmwasser/aufheizen").status_code == 401


def test_warmwasser_admin_nur_ingress(client, ha):
    registrieren(client)
    r = post(client, f"/api/admin/benutzer/{benutzer_id('anna@example.org')}/familie_an")
    assert r.status_code == 404


def test_familie_aus_0_3_0_uebernommen(monkeypatch, tmp_path):
    import sqlite3
    pfad = tmp_path / "tuer.db"
    conn = sqlite3.connect(pfad)
    conn.executescript(server.SCHEMA.replace("familie INTEGER", "warmwasser INTEGER"))
    conn.execute("INSERT INTO benutzer (name, email, pw_hash, webauthn_id, angelegt, warmwasser) VALUES"
                 " ('A','a@x.org','-','w1',0,1), ('B','b@x.org','-','w2',0,0)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(server, "DB_PFAD", str(pfad))
    server.db_einrichten()
    conn = sqlite3.connect(pfad)
    assert conn.execute("SELECT email, familie FROM benutzer ORDER BY id").fetchall() == [("a@x.org", 1), ("b@x.org", 0)]
    conn.close()


# ------------------------------------------------------------------
# Staubsauger


def sauger_befehle(ha):
    return [p.removeprefix("/services/vacuum/") for _, p, d in ha
            if p.startswith("/services/vacuum/") and d["entity_id"] == "vacuum.sauger"]


def test_staubsauger_nur_familie(client, ha):
    registrieren(client)
    freigeben("anna@example.org")
    assert client.get("/api/status").json["staubsauger"] is False
    assert client.get("/api/staubsauger").status_code == 403
    assert post(client, "/api/staubsauger/start").status_code == 403
    assert not sauger_befehle(ha)


def test_staubsauger_anzeige_starten_und_zurueck(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    s = client.get("/api/status").json
    assert s["familie"] is True and s["staubsauger"] is True and s["warmwasser"] is True
    # Akku aus dem eigenen Sensor, nicht aus dem Attribut
    assert client.get("/api/staubsauger").json == {"zustand": "docked", "batterie": 87}
    assert post(client, "/api/staubsauger/start").status_code == 200
    # Gleich danach gebremst
    assert post(client, "/api/staubsauger/zur_station").status_code == 429
    server._zuletzt_gesaugt["t"] = 0.0
    assert post(client, "/api/staubsauger/zur_station").status_code == 200
    assert sauger_befehle(ha) == ["start", "return_to_base"]
    admin = server.app.test_client()
    aktionen = [p["aktion"] for p in admin.get("/api/admin/uebersicht", environ_base=INGRESS).json["protokoll"]]
    assert aktionen[:2] == ["staubsauger_zur_station", "staubsauger_start"]


def test_staubsauger_unbekannter_befehl(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    assert post(client, "/api/staubsauger/turbo").status_code == 404
    assert not sauger_befehle(ha)


def test_staubsauger_akku_aus_attribut_und_nicht_erreichbar(client, ha, monkeypatch):
    monkeypatch.setattr(server, "STAUBSAUGER_BATTERIE", "")
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    assert client.get("/api/staubsauger").json == {"zustand": "docked", "batterie": 50}
    ZUSTAENDE["vacuum.sauger"] = {"state": "unavailable", "attributes": {}}
    assert client.get("/api/staubsauger").json == {"zustand": None, "batterie": None}


# ------------------------------------------------------------------
# Kamera


@pytest.fixture()
def kamera(monkeypatch):
    abrufe = []

    def bild(entitaet):
        abrufe.append(entitaet)
        return b"\xff\xd8JPEG" + str(len(abrufe)).encode()

    monkeypatch.setattr(server, "ha_bild", bild)
    return abrufe


def test_kamera_nur_familie(client, ha, kamera):
    registrieren(client)
    freigeben("anna@example.org")
    assert client.get("/api/status").json["kamera"] is False
    assert client.get("/api/kamera").status_code == 403
    assert not kamera


def test_kamera_bild_und_gemeinsamer_zwischenspeicher(client, ha, kamera):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    assert client.get("/api/status").json["kamera"] is True
    r = client.get("/api/kamera")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data.startswith(b"\xff\xd8")
    assert r.headers["Cache-Control"] == "no-store"
    # Gleich danach: dasselbe Bild, die Kamera wird nicht noch einmal gefragt
    assert client.get("/api/kamera").data == r.data
    assert kamera == ["camera.haustuer"]
    server._kamera["t"] = 0.0
    assert client.get("/api/kamera").data != r.data
    assert len(kamera) == 2


def test_kamera_ohne_anmeldung_und_fehler(client, ha, monkeypatch):
    assert client.get("/api/kamera").status_code == 401
    registrieren(client)
    freigeben("anna@example.org", familie=True)

    def kaputt(_e):
        raise RuntimeError("Zeitueberschreitung")

    monkeypatch.setattr(server, "ha_bild", kaputt)
    assert client.get("/api/kamera").status_code == 502


# ------------------------------------------------------------------
# Gaeste-WLAN


def test_wlan_fuer_jeden_freigegebenen(client, ha):
    registrieren(client)
    # Noch nicht freigegeben: kein Schluessel
    assert client.get("/api/status").json["wlan"] is False
    assert client.get("/api/wlan").status_code == 403
    freigeben("anna@example.org")  # ohne Familie
    assert client.get("/api/status").json["wlan"] is True
    d = client.get("/api/wlan").json
    assert d["name"] == "Gast" and d["schluessel"] == "geheim;123" and "<svg" in d["svg"]


def test_wlan_nicht_ohne_anmeldung_und_nicht_in_der_verwaltung(client, ha):
    assert client.get("/api/wlan").status_code == 401
    admin = server.app.test_client()
    e = admin.get("/api/admin/uebersicht", environ_base=INGRESS).json["einstellungen"]
    assert e["wlan_name"] == "Gast" and "geheim" not in str(e)


def test_wlan_offen_und_aus(client, ha, monkeypatch):
    registrieren(client)
    freigeben("anna@example.org")
    monkeypatch.setattr(server, "WLAN_VERSCHLUESSELUNG", "offen")
    assert client.get("/api/wlan").json["schluessel"] == ""
    monkeypatch.setattr(server, "WLAN_NAME", "")
    assert client.get("/api/status").json["wlan"] is False
    assert client.get("/api/wlan").status_code == 404


# ------------------------------------------------------------------
# Pollenflug


def pollen(art, heute, morgen, uebermorgen):
    ZUSTAENDE[f"sensor.pollenflug_{art}_121"] = {"state": str(heute), "attributes": {
        "state_tomorrow": morgen, "state_in_2_days": uebermorgen, "last_update": "2026-10-18T11:00:00+02:00"}}


def test_pollen_nur_familie(client, ha):
    registrieren(client)
    freigeben("anna@example.org")
    assert client.get("/api/status").json["pollen"] is False
    assert client.get("/api/pollen").status_code == 403


def test_pollen_sortiert_und_benannt(client, ha):
    pollen("birke", 0, 0, 0)
    pollen("graeser", 1, 1.5, 2)
    pollen("beifuss", 0, 0, 3)
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    d = client.get("/api/pollen").json
    assert d["stand"] == "2026-10-18T11:00:00+02:00"
    # Staerkste Belastung zuerst, Umlaute im Namen
    assert [p["name"] for p in d["arten"]] == ["Beifuß", "Gräser", "Birke"]
    assert d["arten"][1] == {"name": "Gräser", "heute": 1.0, "morgen": 1.5, "uebermorgen": 2.0}


def test_pollen_wird_gemerkt(client, ha):
    pollen("hasel", 0, 0, 0)
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    client.get("/api/pollen")
    client.get("/api/pollen")
    assert sum(1 for _, p, _ in ha if p == "/states") == 1


# ------------------------------------------------------------------
# Standorte der Familie


def test_standort_nur_mit_eigener_freigabe(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    s = client.get("/api/status").json
    assert s["standorte"] is True and s["standort_teilen"] is False
    # Ohne Freigabe wird nichts gespeichert
    assert post(client, "/api/standort", {"lat": 48.1, "lon": 11.5}).status_code == 403
    assert post(client, "/api/standort/teilen", {"an": True}).status_code == 200
    assert post(client, "/api/standort", {"lat": HEIM[0], "lon": HEIM[1], "genauigkeit": 12}).status_code == 200
    d = client.get("/api/standorte").json
    assert d["heim"] == {"lat": HEIM[0], "lon": HEIM[1]}
    assert len(d["leute"]) == 1
    p = d["leute"][0]
    assert p["name"] == "Anna" and p["ich"] is True and p["von_zuhause_m"] == 0


def test_standort_familie_sieht_einander_freunde_nicht(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    post(client, "/api/standort/teilen", {"an": True})
    post(client, "/api/standort", {"lat": 48.2, "lon": 11.6})
    bert = server.app.test_client()
    registrieren(bert, "bert@example.org", "Bert")
    freigeben("bert@example.org", familie=True)
    d = bert.get("/api/standorte").json
    assert [(p["name"], p["ich"]) for p in d["leute"]] == [("Anna", False)]
    assert d["leute"][0]["von_zuhause_m"] > 5000
    # Ein Freund ohne Familie sieht nichts und darf nichts senden
    carl = server.app.test_client()
    registrieren(carl, "carl@example.org", "Carl")
    freigeben("carl@example.org")
    assert carl.get("/api/standorte").status_code == 403
    assert post(carl, "/api/standort/teilen", {"an": True}).status_code == 403


def test_standort_aus_loescht_sofort(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    post(client, "/api/standort/teilen", {"an": True})
    post(client, "/api/standort", {"lat": 48.2, "lon": 11.6})
    assert post(client, "/api/standort/teilen", {"an": False}).status_code == 200
    assert client.get("/api/standorte").json["leute"] == []
    conn = server.verbinden()
    assert conn.execute("SELECT COUNT(*) FROM standorte").fetchone()[0] == 0
    conn.close()


def test_standort_verschwindet_aus_der_familie(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    post(client, "/api/standort/teilen", {"an": True})
    post(client, "/api/standort", {"lat": 48.2, "lon": 11.6})
    bert = server.app.test_client()
    registrieren(bert, "bert@example.org", "Bert")
    freigeben("bert@example.org", familie=True)
    admin = server.app.test_client()
    post(admin, f"/api/admin/benutzer/{benutzer_id('anna@example.org')}/familie_aus", environ_base=INGRESS)
    assert bert.get("/api/standorte").json["leute"] == []


def test_standort_ungueltig(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    post(client, "/api/standort/teilen", {"an": True})
    assert post(client, "/api/standort", {"lat": 200, "lon": 11}).status_code == 400
    assert post(client, "/api/standort", {"lat": "hier"}).status_code == 400


# ------------------------------------------------------------------
# OwnTracks


def owntracks_daten(client):
    r = post(client, "/api/owntracks/einrichten")
    assert r.status_code == 200, r.json
    return r.json


def ot_post(daten, benutzer, passwort):
    import base64 as b64
    c = server.app.test_client()
    kopf = {"Authorization": "Basic " + b64.b64encode(f"{benutzer}:{passwort}".encode()).decode()}
    return c.post("/owntracks", json=daten, headers=kopf)


def ort(lat=48.2, lon=11.6, tst=None):
    return {"_type": "location", "lat": lat, "lon": lon, "acc": 25, "tst": tst or int(time.time())}


def test_owntracks_einrichten_und_melden(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    d = owntracks_daten(client)
    assert d["url"] == "https://tuer.example.org/owntracks" and d["link"].startswith("owntracks:///config?inline=")
    import base64 as b64
    import json as js
    konfig = js.loads(b64.urlsafe_b64decode(d["link"].split("inline=")[1]))
    assert konfig["mode"] == 3 and konfig["username"] == d["benutzer"] and konfig["password"] == d["passwort"]
    # Einrichten schaltet die Freigabe ein
    s = client.get("/api/status").json
    assert s["owntracks"] is True and s["standort_teilen"] is True and s["owntracks_zuletzt"] is None
    r = ot_post(ort(), d["benutzer"], d["passwort"])
    assert r.status_code == 200 and r.json == []
    p = client.get("/api/standorte").json["leute"][0]
    assert p["quelle"] == "owntracks" and p["lat"] == 48.2
    assert client.get("/api/status").json["owntracks_zuletzt"]


def test_owntracks_falsches_passwort_und_neu_einrichten(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    alt = owntracks_daten(client)
    assert ot_post(ort(), alt["benutzer"], "falsch").status_code == 401
    assert ot_post(ort(), "tuer-999", alt["passwort"]).status_code == 401
    neu = owntracks_daten(client)
    assert ot_post(ort(), alt["benutzer"], alt["passwort"]).status_code == 401
    assert ot_post(ort(), neu["benutzer"], neu["passwort"]).status_code == 200


def test_owntracks_freigabe_aus_verwirft(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    d = owntracks_daten(client)
    post(client, "/api/standort/teilen", {"an": False})
    assert ot_post(ort(), d["benutzer"], d["passwort"]).status_code == 200
    conn = server.verbinden()
    assert conn.execute("SELECT COUNT(*) FROM standorte").fetchone()[0] == 0
    conn.close()


def test_owntracks_alte_meldung_ueberschreibt_nichts(client, ha):
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    d = owntracks_daten(client)
    jetzt = int(time.time())
    ot_post(ort(48.3, 11.7, jetzt), d["benutzer"], d["passwort"])
    ot_post(ort(48.0, 11.0, jetzt - 3600), d["benutzer"], d["passwort"])
    assert client.get("/api/standorte").json["leute"][0]["lat"] == 48.3


def test_owntracks_trennen_und_nur_familie(client, ha):
    registrieren(client)
    freigeben("anna@example.org")
    assert post(client, "/api/owntracks/einrichten").status_code == 403
    admin = server.app.test_client()
    post(admin, f"/api/admin/benutzer/{benutzer_id('anna@example.org')}/familie_an", environ_base=INGRESS)
    d = owntracks_daten(client)
    assert post(client, "/api/owntracks/trennen").status_code == 200
    assert ot_post(ort(), d["benutzer"], d["passwort"]).status_code == 401
    assert client.get("/api/status").json["owntracks"] is False


def test_sicherheitskoepfe(client, ha):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert client.get("/api/status").headers["Cache-Control"] == "no-store"


# ------------------------------------------------------------------
# Hausdienst
# ------------------------------------------------------------------

MONTAG = server.datetime.date(2026, 11, 2)


@pytest.fixture()
def hausdienst(monkeypatch, ha):
    """Anna und Bert in der Familie, Küche Montag bei Anna, Flur Dienstag bei Bert."""
    monkeypatch.setattr(server, "heute", lambda: MONTAG)
    anna, bert = server.app.test_client(), server.app.test_client()
    registrieren(anna)
    registrieren(bert, "bert@example.org", "Bert")
    freigeben("anna@example.org", familie=True)
    freigeben("bert@example.org", familie=True)
    admin = server.app.test_client()
    for name, ansage in (("Küche", "Küchendienst"), ("Flur", "Flurdienst")):
        assert post(admin, "/api/admin/hausdienst/aufgabe", {"name": name, "ansage": ansage}, environ_base=INGRESS).status_code == 200
    for name, email in (("Anna", "anna@example.org"), ("Bert", "bert@example.org")):
        r = post(admin, "/api/admin/hausdienst/person", {"name": name, "benutzer_id": benutzer_id(email)},
                 environ_base=INGRESS)
        assert r.status_code == 200, r.json
    d = admin.get("/api/admin/hausdienst", environ_base=INGRESS).json
    auf = {a["name"]: a["id"] for a in d["aufgaben"]}
    ids = {p["name"].lower(): p["id"] for p in d["personen"]}
    for tag, wer, was in ((0, "anna", "Küche"), (1, "bert", "Flur")):
        r = post(admin, "/api/admin/hausdienst/plan", {"wochentag": tag, "person_id": ids[wer], "aufgabe_id": auf[was]},
                 environ_base=INGRESS)
        assert r.status_code == 200, r.json
    return {"anna": anna, "bert": bert, "admin": admin, "auf": auf, "ids": ids}


def test_hausdienst_heute_und_mein_plan(hausdienst):
    d = hausdienst["anna"].get("/api/hausdienst").json
    assert d["heute"] == "Montag, 2.11."
    assert d["dienste"] == [{"name": "Anna", "aufgabe": "Küche", "ich": True}]
    # Der Plan wiederholt sich jede Woche
    assert [m["datum"] for m in d["meine"]] == ["2026-11-02", "2026-11-09", "2026-11-16", "2026-11-23"]
    assert d["familie"] == [{"id": hausdienst["ids"]["bert"], "name": "Bert"}]
    assert hausdienst["anna"].get("/api/status").json["hausdienst"] is True


def test_hausdienst_nur_fuer_die_familie(hausdienst):
    gast = server.app.test_client()
    registrieren(gast, "gast@example.org", "Gast")
    freigeben("gast@example.org")
    assert gast.get("/api/hausdienst").status_code == 403
    assert post(gast, "/api/hausdienst/antrag", {"art": "pause", "datum": "2026-11-03", "tage": 3}).status_code == 403
    # Die Verwaltung gibt es nur ueber den Ingress
    assert gast.get("/api/admin/hausdienst").status_code == 404


def test_tausch_anfragen_und_in_der_app_annehmen(hausdienst, ha):
    anna, bert, admin, auf, ids = (hausdienst[k] for k in ("anna", "bert", "admin", "auf", "ids"))
    anfrage = {"datum": "2026-11-09", "aufgabe_id": auf["Küche"], "an_id": ids["bert"]}
    # Einen Dienst, den man nicht hat, kann man nicht abgeben
    assert post(anna, "/api/hausdienst/anfrage", anfrage | {"datum": "2026-11-10"}).status_code == 400
    assert post(anna, "/api/hausdienst/anfrage", anfrage | {"an_id": ids["anna"]}).status_code == 400
    assert post(anna, "/api/hausdienst/anfrage", anfrage | {"datum": "2026-10-26"}).status_code == 400
    assert post(anna, "/api/hausdienst/anfrage", anfrage).status_code == 200
    assert post(anna, "/api/hausdienst/anfrage", anfrage).status_code == 400  # laeuft schon
    # Der alte Umplanen-Antrag an die Verwaltung ist ersetzt
    assert post(anna, "/api/hausdienst/antrag", anfrage | {"art": "umplanen"}).status_code == 400

    (a,) = bert.get("/api/hausdienst").json["anfragen_an_mich"]
    assert a["von_name"] == "Anna" and a["dienst"] == "Küche am Montag, 9.11."
    assert anna.get("/api/hausdienst").json["meine_anfragen"][0]["status"] == "offen"
    # Nur wer gefragt wurde, antwortet
    assert post(anna, f"/api/hausdienst/anfrage/{a['id']}/ja").status_code == 404
    assert post(bert, f"/api/hausdienst/anfrage/{a['id']}/zurueckziehen").status_code == 404
    assert post(bert, f"/api/hausdienst/anfrage/{a['id']}/ja").status_code == 200
    glocke = [d for _, p, d in ha if p == "/services/persistent_notification/create"]
    assert "Tausch: Bert übernimmt Küche am Montag, 9.11. von Anna." in glocke[-1]["message"]
    assert "2026-11-09" not in [m["datum"] for m in anna.get("/api/hausdienst").json["meine"]]
    assert {"datum": "2026-11-09", "aufgabe": "Küche", "geaendert": True} in [
        {k: m[k] for k in ("datum", "aufgabe", "geaendert")} for m in bert.get("/api/hausdienst").json["meine"]]
    assert anna.get("/api/hausdienst").json["meine_anfragen"][0]["status"] == "ja"
    assert post(bert, f"/api/hausdienst/anfrage/{a['id']}/nein").status_code == 400  # schon beantwortet
    assert admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["anfragen"][0]["status"] == "ja"


def test_tausch_ablehnen_zurueckziehen_und_ablaufen(hausdienst, monkeypatch):
    anna, bert, auf, ids = (hausdienst[k] for k in ("anna", "bert", "auf", "ids"))
    for datum in ("2026-11-09", "2026-11-16", "2026-11-23"):
        post(anna, "/api/hausdienst/anfrage", {"datum": datum, "aufgabe_id": auf["Küche"], "an_id": ids["bert"]})
    gefragt = bert.get("/api/hausdienst").json["anfragen_an_mich"]
    assert post(bert, f"/api/hausdienst/anfrage/{gefragt[-1]['id']}/nein").status_code == 200
    assert post(anna, f"/api/hausdienst/anfrage/{gefragt[-2]['id']}/zurueckziehen").status_code == 200
    assert len(bert.get("/api/hausdienst").json["anfragen_an_mich"]) == 1
    # Ist der Tag vorbei, verschwindet die unbeantwortete Anfrage
    monkeypatch.setattr(server, "heute", lambda: MONTAG + server.datetime.timedelta(days=22))
    assert bert.get("/api/hausdienst").json["anfragen_an_mich"] == []
    assert anna.get("/api/hausdienst").json["meine_anfragen"] == []
    assert post(bert, f"/api/hausdienst/anfrage/{gefragt[0]['id']}/ja").status_code == 400


def test_pause_beantragen_und_genehmigen(hausdienst):
    anna, admin = hausdienst["anna"], hausdienst["admin"]
    assert post(anna, "/api/hausdienst/antrag", {"art": "pause", "datum": "2026-11-02", "tage": 61}).status_code == 400
    assert post(anna, "/api/hausdienst/antrag", {"art": "pause", "datum": "2026-11-02", "tage": 8}).status_code == 200
    a = admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["antraege"][0]
    assert "Pause von Montag, 2.11. bis Montag, 9.11. (8 Tage)" in a["text"]
    post(admin, f"/api/admin/hausdienst/antrag/{a['id']}/genehmigen", environ_base=INGRESS)
    d = anna.get("/api/hausdienst").json
    assert d["dienste"] == [] and [m["datum"] for m in d["meine"]] == ["2026-11-16", "2026-11-23"]
    tag = admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["tage"][0]
    assert tag["dienste"][0]["pausiert"] is True


def test_antrag_zurueckziehen(hausdienst):
    anna, bert = hausdienst["anna"], hausdienst["bert"]
    post(anna, "/api/hausdienst/antrag", {"art": "pause", "datum": "2026-11-05", "tage": 2})
    aid = anna.get("/api/hausdienst").json["antraege"][0]["id"]
    # Fremde Antraege kann man nicht zurueckziehen
    assert post(bert, f"/api/hausdienst/antrag/{aid}/zurueckziehen").status_code == 404
    assert post(anna, f"/api/hausdienst/antrag/{aid}/zurueckziehen").status_code == 200
    assert anna.get("/api/hausdienst").json["antraege"][0]["status"] == "zurueckgezogen"


def test_einzelnen_tag_umplanen(hausdienst):
    admin, auf, ids = hausdienst["admin"], hausdienst["auf"], hausdienst["ids"]
    d = admin.get("/api/admin/hausdienst", environ_base=INGRESS).json
    plan_id = d["tage"][0]["dienste"][0]["plan_id"]
    # Planeintrag vom Montag passt nicht auf einen Dienstag
    assert post(admin, "/api/admin/hausdienst/tag", {"datum": "2026-11-03", "plan_id": plan_id, "person_id": ids["bert"]},
                environ_base=INGRESS).status_code == 400
    post(admin, "/api/admin/hausdienst/tag", {"datum": "2026-11-02", "plan_id": plan_id, "person_id": None}, environ_base=INGRESS)
    post(admin, "/api/admin/hausdienst/tag", {"datum": "2026-11-02", "person_id": ids["bert"], "aufgabe_id": auf["Flur"]},
         environ_base=INGRESS)
    heute = hausdienst["anna"].get("/api/hausdienst").json["dienste"]
    assert heute == [{"name": "Bert", "aufgabe": "Flur", "ich": False}]
    # Zurueck auf die Person aus dem Plan hebt die Abweichung auf
    post(admin, "/api/admin/hausdienst/tag", {"datum": "2026-11-02", "plan_id": plan_id, "person_id": ids["anna"]},
         environ_base=INGRESS)
    tag = admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["tage"][0]
    kueche = next(x for x in tag["dienste"] if x["aufgabe"] == "Küche")
    assert kueche["name"] == "Anna" and kueche["abweichung_id"] is None


def test_aufgabe_loeschen_raeumt_den_plan_auf(hausdienst):
    admin, auf = hausdienst["admin"], hausdienst["auf"]
    assert post(admin, "/api/admin/hausdienst/aufgabe", {"name": "Küche", "ansage": "x"}, environ_base=INGRESS).status_code == 400
    post(admin, f"/api/admin/hausdienst/aufgabe/{auf['Küche']}/loeschen", environ_base=INGRESS)
    assert [p["aufgabe"] for p in admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["plan"]] == ["Flur"]
    assert post(admin, "/api/admin/hausdienst/unsinn/1/loeschen", environ_base=INGRESS).status_code == 404


# ------------------------------------------------------------------
# Hausdienst-Anrufe
# ------------------------------------------------------------------


def um(stunde, minute=0, tag=MONTAG):
    return server.datetime.datetime.combine(tag, server.datetime.time(stunde, minute))


def waehlen(ha):
    return [d["input"] for _, p, d in ha if p == "/services/hassio/addon_stdin"]


@pytest.fixture()
def anrufe(hausdienst, monkeypatch):
    monkeypatch.setattr(server, "SIP_ADDON", "c7744bff_ha-sip")
    monkeypatch.setattr(server, "SIP_SERVER", "192.168.178.1")
    monkeypatch.setattr(server, "EIGENER_SLUG", "0afc2581_tuer_oeffner")
    admin, ids = hausdienst["admin"], hausdienst["ids"]
    assert post(admin, "/api/admin/hausdienst/anrufe", {"an": True}, environ_base=INGRESS).status_code == 200
    r = post(admin, "/api/admin/hausdienst/telefon",
             {"person_id": ids["anna"], "nummer": "0170 / 123-4567", "zeit": "06:30", "tage": {"0": "07:15"}},
             environ_base=INGRESS)
    assert r.status_code == 200, r.json
    return hausdienst


def pruefen(jetzt):
    with server.app.app_context():
        server.anrufe_pruefen(jetzt)


def test_anruf_zur_uhrzeit_des_wochentags(anrufe, ha):
    pruefen(um(6, 45))          # Standard 06:30, montags aber 07:15
    assert waehlen(ha) == []
    pruefen(um(7, 15))
    (anruf,) = waehlen(ha)
    assert anruf["command"] == "dial" and anruf["sip_account"] == 2
    assert anruf["number"] == "sip:01701234567@192.168.178.1"
    menue = anruf["menu"]
    assert menue["message"] == ("Hallo Anna, guten Morgen – heute ist dein großer Tag, du hast Küchendienst. "
                                "Drücke die Eins, wenn alles klar ist.")
    # Zweimal wiederholt (fuer die Mailbox), dann aufgelegt
    zweite = menue["choices"]["timeout"]
    assert zweite["message"].startswith("Ich wiederhole: Hallo Anna")
    assert zweite["choices"]["timeout"]["choices"]["timeout"]["post_action"] == "hangup"
    taste = menue["choices"]["1"]["action"]
    assert taste["service_data"]["addon"] == "0afc2581_tuer_oeffner"
    pruefen(um(7, 20))
    assert len(waehlen(ha)) == 1  # nicht doppelt


def test_zweiter_anruf_nur_ohne_taste_1(anrufe, ha):
    pruefen(um(7, 15))
    pruefen(um(7, 44))
    assert len(waehlen(ha)) == 1
    pruefen(um(7, 45))
    assert len(waehlen(ha)) == 2
    pruefen(um(8, 30))
    assert len(waehlen(ha)) == 2  # mehr als zwei gibt es nicht


def test_taste_1_ueber_stdin_verhindert_den_zweiten(anrufe, ha):
    pruefen(um(7, 15))
    kennung = waehlen(ha)[0]["menu"]["choices"]["1"]["action"]["service_data"]["input"]["hausdienst_bestaetigt"]
    server.stdin_lesen(["kein json\n", '{"anderes": 1}\n', server.json.dumps({"hausdienst_bestaetigt": kennung}) + "\n"])
    pruefen(um(7, 50))
    assert len(waehlen(ha)) == 1
    liste = anrufe["admin"].get("/api/admin/hausdienst", environ_base=INGRESS).json["anrufliste"]
    assert liste[0]["bestaetigt"] and liste[0]["versuch"] == 1


def test_kein_anruf_wenn_aus_zu_spaet_pausiert_oder_kein_dienst(anrufe, ha):
    pruefen(um(9, 16))  # mehr als zwei Stunden zu spaet (z. B. nach einem Neustart)
    pruefen(um(7, 15, MONTAG + server.datetime.timedelta(days=2)))  # Mittwoch: kein Dienst
    assert waehlen(ha) == []
    post(anrufe["admin"], "/api/admin/hausdienst/anrufe", {"an": False}, environ_base=INGRESS)
    pruefen(um(7, 15))
    assert waehlen(ha) == []
    post(anrufe["admin"], "/api/admin/hausdienst/anrufe", {"an": True}, environ_base=INGRESS)
    post(anrufe["admin"], "/api/admin/hausdienst/pause",
         {"person_id": anrufe["ids"]["anna"], "von": "2026-11-02", "bis": "2026-11-02"}, environ_base=INGRESS)
    pruefen(um(7, 15))
    assert waehlen(ha) == []


def test_mehrere_dienste_und_abstand_zwischen_anrufen(anrufe, ha):
    admin, auf, ids = anrufe["admin"], anrufe["auf"], anrufe["ids"]
    post(admin, "/api/admin/hausdienst/plan", {"wochentag": 0, "person_id": ids["anna"], "aufgabe_id": auf["Flur"]},
         environ_base=INGRESS)
    post(admin, "/api/admin/hausdienst/plan", {"wochentag": 0, "person_id": ids["bert"], "aufgabe_id": auf["Flur"]},
         environ_base=INGRESS)
    post(admin, "/api/admin/hausdienst/telefon", {"person_id": ids["bert"], "nummer": "0171555", "zeit": "07:15"},
         environ_base=INGRESS)
    pruefen(um(7, 15))
    assert "du hast Flurdienst und Küchendienst." in waehlen(ha)[0]["menu"]["message"]
    pruefen(um(7, 16))
    assert len(waehlen(ha)) == 1  # Bert erst, wenn Annas Anruf vorbei ist
    pruefen(um(7, 18))
    assert waehlen(ha)[1]["number"] == "sip:0171555@192.168.178.1"


def test_telefon_und_ansage_pruefen(anrufe):
    admin, ids = anrufe["admin"], anrufe["ids"]
    for falsch in ({"nummer": "abc"}, {"zeit": "6:30"}, {"zeit": "06:30", "tage": {"7": "06:00"}},
                   {"zeit": "06:30", "tage": {"1": "25:00"}}):
        r = post(admin, "/api/admin/hausdienst/telefon", {"person_id": ids["anna"]} | falsch, environ_base=INGRESS)
        assert r.status_code == 400, falsch
    r = post(admin, "/api/admin/hausdienst/anrufe", {"ansage": "Hallo {name}"}, environ_base=INGRESS)
    assert r.status_code == 400
    post(admin, "/api/admin/hausdienst/anrufe", {"ansage": "Moin {name}, {tag}: {dienste}."}, environ_base=INGRESS)
    with server.app.app_context():
        assert server.ansage_bauen("Levin Wolf", ["Klodienst"], MONTAG) == "Moin Levin, Montag: Klodienst."


def test_testanruf(anrufe, ha):
    admin, ids = anrufe["admin"], anrufe["ids"]
    r = post(admin, "/api/admin/hausdienst/testanruf", {"person_id": ids["anna"]}, environ_base=INGRESS)
    assert r.status_code == 200 and "Küchendienst" in r.json["ansage"]
    assert len(waehlen(ha)) == 1
    # Ein Testanruf zaehlt nicht als Anruf des Tages. (Er lief "jetzt" -
    # fuer den Morgen im Test liegt er weit zurueck.)
    conn = server.verbinden()
    conn.execute("UPDATE hd_anrufe SET zeit = 0")
    conn.commit()
    conn.close()
    pruefen(um(7, 15) + server.datetime.timedelta(minutes=5))
    assert len(waehlen(ha)) == 2
    r = post(admin, "/api/admin/hausdienst/testanruf", {"person_id": ids["bert"]}, environ_base=INGRESS)
    assert r.status_code == 400  # keine Nummer


def test_antrag_grenzen(hausdienst):
    anna, admin, ids, auf = hausdienst["anna"], hausdienst["admin"], hausdienst["ids"], hausdienst["auf"]
    # Weit in der Zukunft geht nicht (und darf die Verwaltung nicht lahmlegen)
    assert post(anna, "/api/hausdienst/antrag", {"art": "pause", "datum": "9999-12-31", "tage": 2}).status_code == 400
    assert admin.get("/api/admin/hausdienst", environ_base=INGRESS).status_code == 200
    assert post(anna, "/api/hausdienst/antrag", {"art": "pause", "datum": "2026-11-05", "tage": 2}).status_code == 200
    assert post(anna, "/api/hausdienst/antrag", {"art": "pause", "datum": "2026-11-05", "tage": 3}).status_code == 400
    # Wer Pause hat, kann nichts uebernehmen
    post(admin, "/api/admin/hausdienst/pause", {"person_id": ids["bert"], "von": "2026-11-09", "bis": "2026-11-09"},
         environ_base=INGRESS)
    r = post(anna, "/api/hausdienst/anfrage", {"datum": "2026-11-09", "aufgabe_id": auf["Küche"], "an_id": ids["bert"]})
    assert r.status_code == 400 and "Pause" in r.json["fehler"]


def test_person_ohne_zugang_planen_und_anrufen(anrufe, ha):
    admin, auf = anrufe["admin"], anrufe["auf"]
    assert post(admin, "/api/admin/hausdienst/person", {"name": "Sven"}, environ_base=INGRESS).status_code == 200
    assert post(admin, "/api/admin/hausdienst/person", {"name": "Sven"}, environ_base=INGRESS).status_code == 400
    d = admin.get("/api/admin/hausdienst", environ_base=INGRESS).json
    sven = next(p for p in d["personen"] if p["name"] == "Sven")
    assert sven["benutzer_id"] is None and sven["zugang"] is None
    post(admin, "/api/admin/hausdienst/plan", {"wochentag": 0, "person_id": sven["id"], "aufgabe_id": auf["Flur"]},
         environ_base=INGRESS)
    post(admin, "/api/admin/hausdienst/telefon", {"person_id": sven["id"], "nummer": "0172999", "zeit": "07:15"},
         environ_base=INGRESS)
    # Anna kann auch jemanden ohne Zugang fragen (der wird angerufen) -
    # nur wer weder Nummer noch Zugang hat, ist nicht erreichbar
    post(admin, "/api/admin/hausdienst/person", {"name": "Otto"}, environ_base=INGRESS)
    otto = next(p["id"] for p in admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["personen"]
                if p["name"] == "Otto")
    tausch = {"datum": "2026-11-09", "aufgabe_id": auf["Küche"]}
    assert post(anrufe["anna"], "/api/hausdienst/anfrage", tausch | {"an_id": otto}).status_code == 400
    r = post(anrufe["anna"], "/api/hausdienst/anfrage", tausch | {"an_id": sven["id"]})
    assert r.status_code == 200, r.json
    pruefen(um(7, 15))
    pruefen(um(7, 18))
    assert [a["number"] for a in waehlen(ha)][:2] == ["sip:01701234567@192.168.178.1", "sip:0172999@192.168.178.1"]
    # Loeschen nimmt Plan und Telefon mit
    post(admin, f"/api/admin/hausdienst/person/{sven['id']}/loeschen", environ_base=INGRESS)
    d = admin.get("/api/admin/hausdienst", environ_base=INGRESS).json
    assert [p["name"] for p in d["plan"]] == ["Anna", "Bert"] and str(sven["id"]) not in d["telefon"]


def test_zugang_verknuepfen(hausdienst):
    admin, ids = hausdienst["admin"], hausdienst["ids"]
    cleo = server.app.test_client()
    registrieren(cleo, "cleo@example.org", "Cleo")
    cid = benutzer_id("cleo@example.org")
    # Nur Zugaenge mit Haken "Familie" - auch wenn sie noch nicht freigegeben sind
    assert post(admin, "/api/admin/hausdienst/person", {"name": "Cleo", "benutzer_id": cid}, environ_base=INGRESS).status_code == 400
    post(admin, f"/api/admin/benutzer/{cid}/familie_an", environ_base=INGRESS)
    d = admin.get("/api/admin/hausdienst", environ_base=INGRESS).json
    assert {"id": cid, "name": "Cleo", "status": "wartet"} in d["zugaenge"]
    assert post(admin, "/api/admin/hausdienst/person", {"name": "Cleo", "benutzer_id": cid}, environ_base=INGRESS).status_code == 200
    # Ein Zugang gehoert zu hoechstens einer Person
    r = post(admin, "/api/admin/hausdienst/person", {"id": ids["bert"], "name": "Bert", "benutzer_id": cid},
             environ_base=INGRESS)
    assert r.status_code == 400
    # Bert ohne Zugang: in der App nur noch "heute", keine Antraege
    post(admin, "/api/admin/hausdienst/person", {"id": ids["bert"], "name": "Bert", "benutzer_id": None}, environ_base=INGRESS)
    d = hausdienst["bert"].get("/api/hausdienst").json
    assert d["eingetragen"] is False and d["meine"] == [] and d["familie"] == []
    assert d["dienste"] == [{"name": "Anna", "aufgabe": "Küche", "ich": False}]
    r = post(hausdienst["bert"], "/api/hausdienst/antrag", {"art": "pause", "datum": "2026-11-05", "tage": 2})
    assert r.status_code == 403
    # Ein geloeschter Zugang laesst die Person stehen
    conn = server.verbinden()
    conn.execute("DELETE FROM benutzer WHERE id = ?", (benutzer_id("anna@example.org"),))
    conn.commit()
    conn.close()
    anna = next(p for p in admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["personen"] if p["name"] == "Anna")
    assert anna["benutzer_id"] is None


def test_umstellung_aus_0_12_0(tmp_path):
    """0.12.0 plante mit den Zugaengen - beim Start werden daraus Personen."""
    alt = tmp_path / "alt.db"
    conn = server.sqlite3.connect(alt)
    conn.executescript("""
        CREATE TABLE benutzer (id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, pw_hash TEXT, status TEXT,
                               familie INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE hd_aufgaben (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, ansage TEXT NOT NULL);
        CREATE TABLE hd_plan (id INTEGER PRIMARY KEY, wochentag INTEGER NOT NULL,
            benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE,
            aufgabe_id INTEGER NOT NULL REFERENCES hd_aufgaben(id) ON DELETE CASCADE,
            UNIQUE (wochentag, benutzer_id, aufgabe_id));
        CREATE TABLE hd_abweichungen (id INTEGER PRIMARY KEY, datum TEXT NOT NULL,
            plan_id INTEGER REFERENCES hd_plan(id) ON DELETE CASCADE,
            aufgabe_id INTEGER NOT NULL REFERENCES hd_aufgaben(id) ON DELETE CASCADE,
            benutzer_id INTEGER REFERENCES benutzer(id) ON DELETE CASCADE);
        CREATE UNIQUE INDEX hd_abweichung_plan ON hd_abweichungen(datum, plan_id) WHERE plan_id IS NOT NULL;
        CREATE TABLE hd_pausen (id INTEGER PRIMARY KEY, benutzer_id INTEGER NOT NULL REFERENCES benutzer(id)
            ON DELETE CASCADE, von TEXT NOT NULL, bis TEXT NOT NULL);
        CREATE TABLE hd_telefon (benutzer_id INTEGER PRIMARY KEY REFERENCES benutzer(id) ON DELETE CASCADE,
            nummer TEXT NOT NULL DEFAULT '', zeit TEXT NOT NULL DEFAULT '', tage TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE hd_anrufe (id INTEGER PRIMARY KEY, datum TEXT NOT NULL,
            benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE, versuch INTEGER NOT NULL,
            zeit INTEGER NOT NULL, kennung TEXT NOT NULL UNIQUE, bestaetigt INTEGER, fehler TEXT);
        CREATE TABLE hd_antraege (id INTEGER PRIMARY KEY,
            benutzer_id INTEGER NOT NULL REFERENCES benutzer(id) ON DELETE CASCADE, art TEXT NOT NULL,
            datum TEXT NOT NULL, aufgabe_id INTEGER REFERENCES hd_aufgaben(id) ON DELETE CASCADE,
            an_id INTEGER REFERENCES benutzer(id) ON DELETE CASCADE, tage INTEGER,
            status TEXT NOT NULL DEFAULT 'offen', angelegt INTEGER NOT NULL, entschieden INTEGER);
        INSERT INTO benutzer (id, name, email, status, familie) VALUES
            (1, 'Gregor', 'g@x', 'aktiv', 1), (2, 'Levin', 'l@x', 'aktiv', 1), (3, 'Kai', 'k@x', 'neu', 1),
            (4, 'Gast', 'x@x', 'aktiv', 0);
        INSERT INTO hd_aufgaben VALUES (1, 'Küche', 'Küchendienst');
        INSERT INTO hd_plan VALUES (1, 0, 2, 1);
        INSERT INTO hd_abweichungen VALUES (1, '2026-11-09', 1, 1, 1);
        INSERT INTO hd_pausen VALUES (1, 1, '2026-11-10', '2026-11-12');
        INSERT INTO hd_telefon VALUES (2, '0170', '06:00', '{}');
        INSERT INTO hd_antraege (id, benutzer_id, art, datum, aufgabe_id, an_id, angelegt)
            VALUES (1, 2, 'umplanen', '2026-11-16', 1, 1, 1);
    """)
    conn.commit()
    conn.close()
    pfad = server.DB_PFAD
    server.DB_PFAD = str(alt)
    try:
        server.db_einrichten()
        server.db_einrichten()  # ein zweiter Start aendert nichts mehr
        conn = server.verbinden()
        conn.row_factory = None
        assert conn.execute("SELECT id, name, benutzer_id FROM hd_personen ORDER BY id").fetchall() == [
            (1, "Gregor", 1), (2, "Levin", 2), (3, "Kai", 3)]
        assert conn.execute("SELECT wochentag, person_id, aufgabe_id FROM hd_plan").fetchall() == [(0, 2, 1)]
        assert conn.execute("SELECT person_id FROM hd_abweichungen").fetchall() == [(1,)]
        assert conn.execute("SELECT person_id, von FROM hd_pausen").fetchall() == [(1, "2026-11-10")]
        assert conn.execute("SELECT person_id, nummer FROM hd_telefon").fetchall() == [(2, "0170")]
        assert conn.execute("SELECT person_id, an_id FROM hd_antraege").fetchall() == [(2, 1)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert not [r for r in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%_alt'")]
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.close()
    finally:
        server.DB_PFAD = pfad


def test_tausch_anruf_tagsueber_mit_taste(anrufe, ha):
    anna, bert, auf, ids = (anrufe[k] for k in ("anna", "bert", "auf", "ids"))
    # Bert fragt Anna (nur Anna hat eine Nummer) wegen seines Flurs am Dienstag
    r = post(bert, "/api/hausdienst/anfrage", {"datum": "2026-11-03", "aufgabe_id": auf["Flur"], "an_id": ids["anna"]})
    assert r.status_code == 200, r.json
    pruefen(um(6, 50))   # vor 7 Uhr ruft keine Anfrage an
    assert waehlen(ha) == []
    pruefen(um(7, 15))   # zuerst der Anruf am Morgen fuer Annas Dienst
    pruefen(um(7, 16))
    assert len(waehlen(ha)) == 1
    pruefen(um(7, 18))
    anruf = waehlen(ha)[1]
    assert anruf["number"] == "sip:01701234567@192.168.178.1"
    assert anruf["menu"]["message"] == ("Hallo Anna, hier ist der Hausdienst. Bert fragt, ob du morgen Flurdienst "
                                        "übernehmen kannst. Drücke die Eins für ja oder die Zwei für nein.")
    ja = anruf["menu"]["choices"]["1"]["action"]["service_data"]["input"]
    assert anruf["menu"]["choices"]["2"]["action"]["service_data"]["input"] == ja | {"ja": False}
    pruefen(um(7, 30))
    assert len(waehlen(ha)) == 2  # je Anfrage nur ein Anruf
    server.stdin_lesen([server.json.dumps(ja) + "\n"])
    assert "2026-11-03" in [m["datum"] for m in anna.get("/api/hausdienst").json["meine"]]
    liste = anrufe["admin"].get("/api/admin/hausdienst", environ_base=INGRESS).json["anrufliste"]
    assert liste[0]["versuch"] == -1 and liste[0]["bestaetigt"]


def test_tausch_nachts_erst_am_morgen(anrufe, ha, monkeypatch):
    bert, auf, ids = anrufe["bert"], anrufe["auf"], anrufe["ids"]
    post(anrufe["admin"], "/api/admin/hausdienst/telefon", {"person_id": ids["anna"], "nummer": "0170", "zeit": ""},
         environ_base=INGRESS)
    post(bert, "/api/hausdienst/anfrage", {"datum": "2026-11-03", "aufgabe_id": auf["Flur"], "an_id": ids["anna"]})
    pruefen(um(21, 0))
    pruefen(um(23, 30))
    assert waehlen(ha) == []
    dienstag = MONTAG + server.datetime.timedelta(days=1)
    monkeypatch.setattr(server, "heute", lambda: dienstag)
    pruefen(um(7, 0, dienstag))
    assert len(waehlen(ha)) == 1 and "heute Flurdienst" in waehlen(ha)[0]["menu"]["message"]


def test_anrufzeit_selbst_einstellen(anrufe):
    anna, admin, ids = anrufe["anna"], anrufe["admin"], anrufe["ids"]
    assert anna.get("/api/hausdienst").json["anrufzeit"] == {"zeit": "06:30", "tage": {"0": "07:15"}, "nummer": True}
    assert post(anna, "/api/hausdienst/anrufzeit", {"zeit": "6:45"}).status_code == 400
    assert post(anna, "/api/hausdienst/anrufzeit", {"zeit": "06:45", "tage": {"5": "09:00"}}).status_code == 200
    t = admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["telefon"][str(ids["anna"])]
    assert t == {"nummer": "01701234567", "zeit": "06:45", "tage": {"5": "09:00"}}
    # Ohne Nummer geht es auch - die traegt dann die Verwaltung nach
    assert post(anrufe["bert"], "/api/hausdienst/anrufzeit", {"zeit": "07:00"}).status_code == 200
    assert anrufe["bert"].get("/api/hausdienst").json["anrufzeit"]["nummer"] is False
    gast = server.app.test_client()
    registrieren(gast, "gast@example.org", "Gast")
    freigeben("gast@example.org", familie=True)
    assert post(gast, "/api/hausdienst/anrufzeit", {"zeit": "07:00"}).status_code == 403


# ------------------------------------------------------------------
# Hausdienst: Bewertung
# ------------------------------------------------------------------

SONNTAG = server.datetime.date(2026, 11, 1)


@pytest.fixture()
def bewertung(hausdienst, monkeypatch):
    """Montag 17 Uhr; Küche bei Anna, Flur bei Bert (zusätzlich am Montag)."""
    monkeypatch.setattr(server, "jetzt", lambda: um(17))
    admin, auf, ids = hausdienst["admin"], hausdienst["auf"], hausdienst["ids"]
    post(admin, "/api/admin/hausdienst/plan", {"wochentag": 0, "person_id": ids["bert"], "aufgabe_id": auf["Flur"]},
         environ_base=INGRESS)
    plan = {(p["name"], p["aufgabe"]): p["id"] for p in admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["plan"]
            if p["wochentag"] == 0}
    return hausdienst | {"kueche": f"p{plan[('Anna', 'Küche')]}", "flur": f"p{plan[('Bert', 'Flur')]}", "plan": plan}


def bewertung_eintragen(datum, dienst, person_id, aufgabe_id, von_id, wert):
    conn = server.verbinden()
    conn.execute("INSERT INTO hd_bewertungen (datum, dienst, person_id, aufgabe_id, von_person_id, von_admin, wert, zeit)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?, 0)", (datum, dienst, person_id, aufgabe_id, von_id, int(von_id is None), wert))
    conn.commit()
    conn.close()


def glocken(ha):
    return [d for m, p, d in ha if p == "/services/persistent_notification/create"]


def test_bewerten_erst_ab_16_uhr(bewertung, monkeypatch):
    anna, bert = bewertung["anna"], bewertung["bert"]
    monkeypatch.setattr(server, "jetzt", lambda: um(15, 59))
    b = bert.get("/api/hausdienst").json["bewerten"]
    assert b == {"offen": False, "ab": 16, "dienste": []}
    r = post(bert, "/api/hausdienst/bewertung", {"dienst": bewertung["kueche"], "wert": 3})
    assert r.status_code == 400 and "16 Uhr" in r.json["fehler"]
    assert post(bewertung["admin"], "/api/admin/hausdienst/bewertung", {"dienst": bewertung["kueche"], "wert": 3},
                environ_base=INGRESS).status_code == 400
    monkeypatch.setattr(server, "jetzt", lambda: um(16))
    assert post(bert, "/api/hausdienst/bewertung", {"dienst": bewertung["kueche"], "wert": 3}).status_code == 200
    monkeypatch.setattr(server, "jetzt", lambda: um(23, 59))
    assert post(bert, "/api/hausdienst/bewertung", {"dienst": bewertung["kueche"], "wert": 2}).status_code == 200


def test_nur_fremde_dienste_und_nur_mit_person(bewertung):
    anna, bert = bewertung["anna"], bewertung["bert"]
    assert [(x["name"], x["aufgabe"]) for x in anna.get("/api/hausdienst").json["bewerten"]["dienste"]] == [("Bert", "Flur")]
    r = post(anna, "/api/hausdienst/bewertung", {"dienst": bewertung["kueche"], "wert": 3})
    assert r.status_code == 400 and "eigenen" in r.json["fehler"]
    assert post(anna, "/api/hausdienst/bewertung", {"dienst": bewertung["flur"], "wert": 4}).status_code == 400
    assert post(anna, "/api/hausdienst/bewertung", {"dienst": "p99999", "wert": 3}).status_code == 400
    assert post(anna, "/api/hausdienst/bewertung", {"dienst": bewertung["flur"], "wert": 0}).status_code == 200
    gast = server.app.test_client()
    registrieren(gast, "gast@example.org", "Gast")
    freigeben("gast@example.org", familie=True)
    assert post(gast, "/api/hausdienst/bewertung", {"dienst": bewertung["flur"], "wert": 3}).status_code == 403
    assert post(bert, "/api/admin/hausdienst/bewertung", {"dienst": bewertung["flur"], "wert": 3}).status_code == 404


def test_entfallene_und_pausierte_dienste_nicht_bewertbar(bewertung):
    anna, bert, admin, ids = bewertung["anna"], bewertung["bert"], bewertung["admin"], bewertung["ids"]
    post(admin, "/api/admin/hausdienst/tag", {"datum": "2026-11-02", "plan_id": bewertung["plan"][("Anna", "Küche")],
                                              "person_id": None}, environ_base=INGRESS)
    r = post(bert, "/api/hausdienst/bewertung", {"dienst": bewertung["kueche"], "wert": 3})
    assert r.status_code == 400 and "ausgefallen" in r.json["fehler"]
    post(admin, "/api/admin/hausdienst/pause", {"person_id": ids["bert"], "von": "2026-11-02", "bis": "2026-11-03"},
         environ_base=INGRESS)
    assert post(anna, "/api/hausdienst/bewertung", {"dienst": bewertung["flur"], "wert": 3}).status_code == 400
    assert anna.get("/api/hausdienst").json["bewerten"]["dienste"] == []


def test_bewertung_aendern_statt_doppelt(bewertung):
    bert, admin = bewertung["bert"], bewertung["admin"]
    for wert in (3, 1):
        assert post(bert, "/api/hausdienst/bewertung", {"dienst": bewertung["kueche"], "wert": wert}).status_code == 200
    assert bert.get("/api/hausdienst").json["bewerten"]["dienste"] == [
        {"dienst": bewertung["kueche"], "name": "Anna", "aufgabe": "Küche", "wert": 1}]
    # Die Verwaltung ist ein eigener Bewerter und zaehlt gleich
    for wert in (3, 2):
        assert post(admin, "/api/admin/hausdienst/bewertung", {"dienst": bewertung["kueche"], "wert": wert},
                    environ_base=INGRESS).status_code == 200
    d = admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["bewertungen"]
    assert sorted((r["von"], r["wert"]) for r in d["einzeln"]) == [("Admin", 2), ("Bert", 1)]
    anna = next(p for p in d["personen"] if p["name"] == "Anna")
    assert anna["woche"]["anzahl"] == 2 and anna["woche"]["durchschnitt"] == "1,5"
    assert d["heute"]["dienste"][0]["wert"] == 2 or d["heute"]["dienste"][1]["wert"] == 2
    assert protokoll_aktionen(admin).count("hd_bewertung") == 2


def protokoll_aktionen(admin):
    return [p["aktion"] for p in admin.get("/api/admin/uebersicht", environ_base=INGRESS).json["protokoll"]]


def test_app_zeigt_keine_bewerter(bewertung):
    anna, bert, admin = bewertung["anna"], bewertung["bert"], bewertung["admin"]
    post(bert, "/api/hausdienst/bewertung", {"dienst": bewertung["kueche"], "wert": 3})
    post(admin, "/api/admin/hausdienst/bewertung", {"dienst": bewertung["kueche"], "wert": 1}, environ_base=INGRESS)
    d = anna.get("/api/hausdienst").json
    assert d["bewertung"]["woche"]["anzahl"] == 2 and d["bewertung"]["woche"]["durchschnitt"] == "2,0"
    assert d["bewertung"]["woche"]["verteilung"] == [1, 0, 1, 0]
    text = server.json.dumps(d)
    assert "von" not in d["bewertung"]["woche"] and "Admin" not in text
    # Annas Liste der zu bewertenden Dienste kennt nur die eigene Wahl (hier keine)
    assert d["bewerten"]["dienste"] == [{"dienst": bewertung["flur"], "name": "Bert", "aufgabe": "Flur", "wert": None}]
    # Bert sieht bei sich nur das, was andere zu seinem Dienst sagen: nichts
    assert bert.get("/api/hausdienst").json["bewertung"]["woche"]["anzahl"] == 0


def test_auswertung_woche_monat_insgesamt(bewertung):
    anna, admin, ids, auf = bewertung["anna"], bewertung["admin"], bewertung["ids"], bewertung["auf"]
    k = bewertung["kueche"]
    einstellung_setzen("hd_bw_seit", "2026-08-01")
    for datum, von, wert in (("2026-11-02", ids["bert"], 3), ("2026-11-02", None, 1),
                             ("2026-10-26", ids["bert"], 2), ("2026-10-05", ids["bert"], 2)):
        bewertung_eintragen(datum, k, ids["anna"], auf["Küche"], von, wert)
    w = anna.get("/api/hausdienst").json["bewertung"]
    assert (w["woche"]["durchschnitt"], w["woche"]["anzahl"], w["woche"]["verteilung"]) == ("2,0", 2, [1, 0, 1, 0])
    assert (w["monat"]["anzahl"], w["monat"]["unbewertet"]) == (2, 0)
    assert w["woche"]["unbewertet"] == 0
    # Seit 2.8.: 14 Montage mit Küche, davon 3 bewertet
    assert (w["gesamt"]["durchschnitt"], w["gesamt"]["anzahl"], w["gesamt"]["verteilung"]) == ("2,0", 4, [1, 2, 1, 0])
    assert w["gesamt"]["unbewertet"] == 11
    d = admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["bewertungen"]
    assert [p["name"] for p in d["personen"]] == ["Anna", "Bert"]
    assert d["personen"][0]["gesamt"]["anzahl"] == 4
    # Bert: Flur am Montag (14) und am Dienstag (13), nie bewertet; diese Woche nur der heutige
    assert (d["personen"][1]["woche"]["unbewertet"], d["personen"][1]["gesamt"]["unbewertet"]) == (1, 27)
    assert d["personen"][1]["gesamt"]["durchschnitt"] is None
    # Einzelliste: nur die letzten 14 Tage
    assert sorted(r["datum"] for r in d["einzeln"]) == ["2026-10-26", "2026-11-02", "2026-11-02"]


def test_bewertungen_nach_drei_monaten_loeschen(bewertung):
    ids, auf = bewertung["ids"], bewertung["auf"]
    for datum in ("2026-07-31", "2026-08-01", "2026-08-02", "2026-11-02"):
        bewertung_eintragen(datum, bewertung["kueche"], ids["anna"], auf["Küche"], ids["bert"], 3)
    with server.app.app_context():
        server.bewertung_tagesarbeit(um(10))
        rest = [r[0] for r in server.db().execute("SELECT datum FROM hd_bewertungen ORDER BY datum")]
        assert rest == ["2026-08-02", "2026-11-02"]
        # Einmal pro Tag: nochmal am selben Tag fasst nichts an, am naechsten Tag schon
        bewertung_eintragen("2026-07-01", bewertung["kueche"], ids["anna"], auf["Küche"], ids["bert"], 3)
        server.bewertung_tagesarbeit(um(12))
        assert server.db().execute("SELECT COUNT(*) FROM hd_bewertungen").fetchone()[0] == 3
        server.bewertung_tagesarbeit(um(0, 5, MONTAG + server.datetime.timedelta(days=1)))
        assert [r[0] for r in server.db().execute("SELECT datum FROM hd_bewertungen")] == ["2026-11-02"]


def test_geloeschte_person_nimmt_bewertungen_mit(bewertung):
    admin, ids, auf = bewertung["admin"], bewertung["ids"], bewertung["auf"]
    bewertung_eintragen("2026-11-02", bewertung["kueche"], ids["anna"], auf["Küche"], ids["bert"], 3)
    assert post(admin, f"/api/admin/hausdienst/person/{ids['bert']}/loeschen", environ_base=INGRESS).status_code == 200
    assert admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["bewertungen"]["einzeln"] == []


def test_glocke_woche_und_monat_nur_einmal(bewertung, ha):
    ids, auf = bewertung["ids"], bewertung["auf"]
    bewertung_eintragen("2026-10-26", bewertung["kueche"], ids["anna"], auf["Küche"], ids["bert"], 3)
    bewertung_eintragen("2026-10-27", bewertung["flur"], ids["bert"], auf["Flur"], ids["anna"], 2)
    bewertung_eintragen("2026-10-28", bewertung["flur"], ids["bert"], auf["Flur"], None, 1)

    def lauf(zeitpunkt):
        with server.app.app_context():
            server.bewertung_tagesarbeit(zeitpunkt)

    lauf(um(20, 59, SONNTAG))
    assert glocken(ha) == []
    lauf(um(21, 0, SONNTAG))
    (g,) = glocken(ha)
    assert "Woche" in g["message"] and "Anna Ø 3,0 (1)" in g["message"] and "Bert Ø 1,5 (2)" in g["message"]
    lauf(um(22, 0, SONNTAG))
    lauf(um(21, 30, SONNTAG))
    assert len(glocken(ha)) == 1
    # Der Merker steht in den Einstellungen, ein Neustart verschickt nichts doppelt
    lauf(um(21, 0, MONTAG))
    assert len(glocken(ha)) == 1  # kein Sonntag, kein Monatsende
    bewertung_eintragen("2026-11-02", bewertung["kueche"], ids["anna"], auf["Küche"], ids["bert"], 2)
    ende = server.datetime.date(2026, 11, 30)
    lauf(um(20, 0, ende))
    lauf(um(21, 0, ende))
    lauf(um(23, 0, ende))
    meldungen = glocken(ha)
    assert len(meldungen) == 2 and "November 2026" in meldungen[1]["message"] and "Anna Ø 2,0 (1)" in meldungen[1]["message"]


def einstellung_setzen(schluessel, wert):
    conn = server.verbinden()
    conn.execute("INSERT OR REPLACE INTO einstellungen (schluessel, wert) VALUES (?, ?)", (schluessel, wert))
    conn.commit()
    conn.close()


def test_unbewertet_erst_seit_es_die_bewertung_gibt(bewertung, monkeypatch):
    admin = bewertung["admin"]
    # Ohne Startdatum zaehlt nur heute (ab 16 Uhr) - nicht die drei Monate davor
    bert = lambda: next(p for p in admin.get("/api/admin/hausdienst", environ_base=INGRESS).json["bewertungen"]["personen"]
                        if p["name"] == "Bert")["gesamt"]["unbewertet"]
    assert bert() == 1
    monkeypatch.setattr(server, "jetzt", lambda: um(10))
    assert bert() == 0
