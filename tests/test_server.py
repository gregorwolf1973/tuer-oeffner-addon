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
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tuer_oeffner", "app"))
server = importlib.import_module("server")


@pytest.fixture()
def ha(monkeypatch):
    """Zeichnet jeden Aufruf an Home Assistant auf."""
    aufrufe = []

    def nachgebildet(methode, pfad, daten=None):
        aufrufe.append((methode, pfad, daten))
        return {"latitude": HEIM[0], "longitude": HEIM[1]} if pfad == "/config" else []

    monkeypatch.setattr(server, "ha_aufruf", nachgebildet)
    return aufrufe


@pytest.fixture(autouse=True)
def sauber():
    conn = server.verbinden()
    conn.executescript("DELETE FROM passkeys; DELETE FROM benutzer; DELETE FROM protokoll;")
    conn.commit()
    conn.close()
    server._ip_fehler.clear()
    server._zuletzt_geoeffnet["t"] = 0.0
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


def registrieren(c, email="anna@example.org", name="Anna"):
    r = post(c, "/api/registrieren", {"name": name, "email": email, "passwort": "geheim-genug-123"})
    assert r.status_code == 200, r.json
    code = pyotp.TOTP(geheimnis(email)).now()
    r = post(c, "/api/2fa/bestaetigen", {"code": code})
    assert r.status_code == 200, r.json


def freigeben(email, fern=False):
    admin = server.app.test_client()
    bid = benutzer_id(email)
    assert post(admin, f"/api/admin/benutzer/{bid}/freigeben", environ_base=INGRESS).status_code == 200
    if fern:
        assert post(admin, f"/api/admin/benutzer/{bid}/fern_an", environ_base=INGRESS).status_code == 200


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
    post(client, "/api/registrieren", {"name": "A", "email": "a@example.org", "passwort": "geheim-genug-123"})
    r = post(client, "/api/2fa/bestaetigen", {"code": "000000"})
    assert r.status_code == 400
    assert client.get("/api/status").json["stufe"] == "einrichten"


def test_qr_nur_beim_einrichten(client, ha):
    post(client, "/api/registrieren", {"name": "A", "email": "a@example.org", "passwort": "geheim-genug-123"})
    d = client.get("/api/2fa/einrichten").json
    assert d["uri"].startswith("otpauth://totp/") and "<svg" in d["svg"]
    post(client, "/api/2fa/bestaetigen", {"code": pyotp.TOTP(geheimnis("a@example.org")).now()})
    # Danach gibt es das Geheimnis nicht mehr
    assert client.get("/api/2fa/einrichten").status_code == 401


def test_kurzes_passwort_und_doppelte_mail(client, ha):
    r = post(client, "/api/registrieren", {"name": "A", "email": "a@example.org", "passwort": "kurz"})
    assert r.status_code == 400
    registrieren(client, "a@example.org")
    r = post(server.app.test_client(), "/api/registrieren",
             {"name": "B", "email": "A@Example.org", "passwort": "geheim-genug-123"})
    assert r.status_code == 400 and "schon registriert" in r.json["fehler"]


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
        post(c, "/api/anmelden", {"email": f"x{i}@example.org", "passwort": "falsch"})
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
    post(client, "/api/registrieren", {"name": "A", "email": "a@example.org", "passwort": "geheim-genug-123"})
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


def test_registrierung_geschlossen(client, ha, monkeypatch):
    monkeypatch.setattr(server, "REGISTRIERUNG_OFFEN", False)
    r = post(client, "/api/registrieren", {"name": "A", "email": "a@example.org", "passwort": "geheim-genug-123"})
    assert r.status_code == 403


def test_sicherheitskoepfe(client, ha):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert client.get("/api/status").headers["Cache-Control"] == "no-store"
