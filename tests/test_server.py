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
    conn.executescript("DELETE FROM standorte; DELETE FROM passkeys; DELETE FROM benutzer; DELETE FROM protokoll;")
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
    assert len(aufgeheizt(ha)) == 1
    # Gleich noch einmal: gebremst
    assert post(client, "/api/warmwasser/aufheizen").status_code == 429
    assert len(aufgeheizt(ha)) == 1
    admin = server.app.test_client()
    p = admin.get("/api/admin/uebersicht", environ_base=INGRESS).json["protokoll"]
    assert p[0]["aktion"] == "warmwasser" and p[0]["name"] == "Anna"


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
    assert not aufgeheizt(ha)


def test_admin_warmwasser_ziel(client, ha):
    admin = server.app.test_client()
    d = admin.get("/api/admin/uebersicht", environ_base=INGRESS).json["warmwasser"]
    assert d == {"ziel": 50.0, "min": 10, "max": 60, "schritt": 1, "temperatur": 41.2, "laeuft": False}
    r = post(admin, "/api/admin/warmwasser/ziel", {"wert": 55}, environ_base=INGRESS)
    assert r.status_code == 200
    assert ("POST", "/services/number/set_value", {"entity_id": "number.ww_ziel", "value": 55.0}) in ha
    assert post(admin, "/api/admin/warmwasser/ziel", {"wert": 75}, environ_base=INGRESS).status_code == 400
    assert post(admin, "/api/admin/warmwasser/ziel", {"wert": "heiss"}, environ_base=INGRESS).status_code == 400
    # Nicht aus der App heraus
    registrieren(client)
    freigeben("anna@example.org", familie=True)
    assert post(client, "/api/admin/warmwasser/ziel", {"wert": 55}).status_code == 404


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


def test_sicherheitskoepfe(client, ha):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert client.get("/api/status").headers["Cache-Control"] == "no-store"
