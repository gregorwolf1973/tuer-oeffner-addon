# Türöffner

Die Haustür per Handy öffnen – für dich und für Freunde, jeder mit eigenem
Zugang. Die Freunde registrieren sich selbst, du gibst frei.

## So funktioniert es

- **App für die Freunde** unter deiner externen Adresse (z. B.
  `https://tuer.biker633.org`). Registrierung mit Name, Mail und Passwort,
  danach **Pflicht-2FA** mit einer Authenticator-App. Auf dem Handy lässt
  sich die Seite als App auf den Startbildschirm legen.
- **Passkey** (Fingerabdruck/Gesicht) kann jeder danach unter „Konto“
  anlegen. Dann reicht „Mit Passkey anmelden“ – ohne Passwort und Code.
- **Öffnen per Wisch** über den Schieber. Dabei wird der Standort geprüft:
  geöffnet wird nur bis zum eingestellten Radius um die Haustür.
- **Fern-Recht:** Ausgewählte Zugänge bekommen einen zweiten, lila
  Schieber „Aus der Ferne öffnen“ – ohne Standortprüfung.
- **Benachrichtigung** an dich bei jedem Öffnen und jeder neuen
  Registrierung.
- **Verwaltung** nur hier in Home Assistant (Seitenleiste „Türöffner“):
  freigeben, Fern-Recht, sperren, 2FA zurücksetzen, löschen, Protokoll und
  ein Knopf zum Testen. Aus dem Internet ist die Verwaltung nicht
  erreichbar.

## Einrichtung

1. **Tür-Entität** eintragen. Am einfachsten die vorhandene Automation, z. B.
   `automation.door_open_for_5_seconds_for_use_intern_only` – sie wird dann
   ausgelöst. Oder direkt den Schalter, z. B. `switch.haustuer`. Die
   Entitäts-ID steht unter Einstellungen → Geräte & Dienste → Entitäten.
2. **Benachrichtigung** eintragen: der notify-Dienst deines Handys, z. B.
   `mobile_app_pixel_8` (Entwicklerwerkzeuge → Aktionen → „notify.“ tippen).
3. Der **Standort der Haustür** kommt aus Home Assistant (Einstellungen →
   System → Allgemein). Stimmt er nicht genau, Breiten- und Längengrad in den
   Optionen eintragen.
4. Add-on starten, in der Verwaltung **„Tür jetzt öffnen“** zum Test.
5. **Cloudflare:** eine Route `tuer.biker633.org` → `http://<IP von Home
   Assistant>:8097` anlegen (z. B. mit dem Cloudflare Route Manager).
   **Keine „Managed Challenge“** auf diesen Namen legen – die App ruft ihre
   Schnittstelle per Skript auf, eine Browserprüfung dazwischen bricht das
   Öffnen ab.
6. Selbst registrieren, in der Verwaltung freigeben und dir das Fern-Recht
   geben. Dann den Freunden die Adresse schicken.

## Optionen

| Option | Bedeutung |
| --- | --- |
| `tuer_entitaet` | Was die Tür öffnet: `switch`, `input_boolean`, `script` (einschalten), `button`, `input_button` (drücken), `lock` (aufschließen), `automation` (auslösen) |
| `externe_adresse` | https-Adresse der App, nötig für Passkeys |
| `radius_meter` | bis zu dieser Entfernung darf ohne Fern-Recht geöffnet werden |
| `benachrichtigung` | notify-Dienst für die Push-Nachrichten |
| `breitengrad`, `laengengrad` | Standort der Tür, leer = aus Home Assistant |
| `sitzung_tage` | so lange bleibt man angemeldet |
| `registrierung_offen` | aus = niemand Neues kann sich registrieren |

## Sicherheit

- Ein neuer Zugang kann erst öffnen, wenn du ihn freigegeben hast.
- Nach 5 Fehlversuchen ist ein Konto 15 Minuten gesperrt. Unabhängig davon
  wird ein Absender nach 20 Fehlversuchen in 15 Minuten gebremst.
- Ein Code aus der Authenticator-App gilt nur einmal.
- Sperren, „2FA zurücksetzen“ und ein Passwortwechsel melden alle Geräte des
  Zugangs sofort ab. Nach „2FA zurücksetzen“ ist wieder deine Freigabe nötig.
- Passwort vergessen: den Zugang löschen, der Freund registriert sich neu.
- Zwischen zwei Öffnungen liegen mindestens 8 Sekunden.
- Die Standortprüfung schützt vor **versehentlichem** Öffnen von unterwegs.
  Gegen jemanden, der seinen Standort fälscht, schützt sie nicht – dagegen
  helfen Freigabe, 2FA und das Protokoll.

## Zum vorhandenen Webhook

Das Add-on braucht den Webhook nicht. Wenn du ihn behältst: `GET` und `HEAD`
aus `allowed_methods` herausnehmen. Vorschaufunktionen von Messengern und
Browsern rufen Links per GET ab – ein im Heimnetz geteilter Link könnte
sonst die Tür öffnen.
