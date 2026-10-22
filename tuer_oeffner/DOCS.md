# Türöffner

Die Haustür per Handy öffnen – für dich und für Freunde, jeder mit eigenem
Zugang. Die Freunde registrieren sich selbst, du gibst frei.

## So funktioniert es

- **App für die Freunde** unter deiner externen Adresse (z. B.
  `https://tuer.biker633.org`). Registrierung mit Name, Benutzername, Mail
  und Passwort (mindestens 8 Zeichen). Anmelden geht mit Benutzername
  **oder** Mailadresse. Auf dem Handy lässt sich die Seite als App auf den
  Startbildschirm legen.
- **2FA** mit einer Authenticator-App: mit `zwei_faktor_pflicht` an (so ist
  es voreingestellt) muss jeder sie einrichten. Aus = das Passwort reicht,
  und jeder kann die 2FA unter „Konto“ selbst ein- und ausschalten.
- **Passkey** (Fingerabdruck/Gesicht) kann jeder danach unter „Konto“
  anlegen. Dann reicht „Mit Passkey anmelden“ – ohne Passwort und Code.
- **Öffnen per Wisch** über den Schieber. Dabei wird der Standort geprüft:
  geöffnet wird nur bis zum eingestellten Radius um die Haustür.
- **Fern-Recht:** Ausgewählte Zugänge bekommen einen zweiten, lila
  Schieber „Aus der Ferne öffnen“ – ohne Standortprüfung.
- **Für die Familie:** Zugänge, die du in der Verwaltung zur **Familie**
  hinzufügst, sehen in der App zusätzlich
  - **Warmwasser:** die Speichertemperatur und einen Knopf „Wasser
    aufheizen“. Er startet die einmalige Ladung der Wärmepumpe. „Heizt
    gerade“ steht nur da, wenn der Kompressor wirklich läuft. Vor der Ladung
    stellt der Knopf die Wärmepumpe auf die **Vorgabe** aus der Verwaltung
    (anfangs 50 °C) – nur dann, nie sonst. Ist das Wasser schon wärmer als
    die Vorgabe, sagt die App das, statt eine wirkungslose Ladung anzustoßen.
  - **Staubsauger:** Status und Akku, dazu „Saugen starten“ und „Zur
    Station“.
  - **Kamera:** ein Livebild in der Haustür-Karte, etwa ein Bild pro
    Sekunde. Abgefragt wird nur, solange die App im Vordergrund und das Bild
    auf dem Bildschirm zu sehen ist.
  - **Wo ist die Familie?** Jeder kann mit „Meinen Standort freigeben“
    seinen Standort für die anderen sichtbar machen – auf einer kleinen
    OpenStreetMap-Karte und als Liste („zu Hause“ bzw. Entfernung, „vor 5
    Min.“, Link zu Google Maps). Gesendet wird alle 2 Minuten, aber nur,
    solange die App offen ist: Eine Web-App kann im Hintergrund keinen
    Standort liefern. „Freigabe beenden“ löscht den letzten Ort sofort;
    Orte, die älter als 7 Tage sind, werden ohnehin gelöscht.
  - **Immer sichtbar mit OwnTracks:** Wer seinen Ort auch bei geschlossener
    App zeigen will, installiert die kostenlose App OwnTracks und tippt
    „OwnTracks einrichten“ → „In OwnTracks übernehmen“. OwnTracks schickt den
    Ort dann im Hintergrund an `<externe Adresse>/owntracks` – direkt nach
    Hause, nicht an einen fremden Dienst. Jeder Zugang hat dafür ein eigenes,
    zufälliges Passwort; „Neu einrichten“ macht das alte ungültig. Ist die
    Freigabe aus, verwirft das Add-on alles, was OwnTracks schickt.
  - **Pollenflug** vom DWD für heute, morgen und übermorgen: belastete
    Arten mit Farbstufe (gering – mittel – hoch), der Rest eingeklappt.

  Beides geht von überall, ohne Standortprüfung.
- **Gäste-WLAN:** Jeder freigegebene Zugang – auch Freunde – hat in der App
  den Knopf „WLAN-QR-Code zeigen“: QR-Code zum Scannen, dazu Name und
  Schlüssel zum Abschreiben oder Kopieren.
- **Benachrichtigung** an dich bei jedem Öffnen und jeder neuen
  Registrierung.
- **Verwaltung** nur hier in Home Assistant (Seitenleiste „Türöffner“):
  jeder Zugang als Karte mit Schaltern für „Aus der Ferne“ und „Familie“,
  dazu die **Warmwasser-Vorgabe** (Zieltemperatur beim Aufheizen) zum
  Einstellen. Außerdem:
  freigeben, Fern-Recht, Familie, sperren, 2FA zurücksetzen, löschen, Protokoll und
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
7. **Familie:** Jeder registriert sich genauso. In der Verwaltung freigeben
   und in der Spalte **„Familie“** auf „Hinzufügen“ drücken – auch bei dir
   selbst. Die Optionen für die Viessmann-Wärmepumpe (ViCare) und den
   Roborock sind schon ausgefüllt.

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
| `zwei_faktor_pflicht` | an = jeder muss die 2FA einrichten; aus = freiwillig, jeder schaltet sie unter „Konto“ selbst |
| `warmwasser_entitaet` | was das Aufheizen startet (gleiche Typen wie bei der Tür), leer = keine Warmwasser-Funktion |
| `warmwasser_temperatur` | Sensor für die Temperaturanzeige in der App, leer = keine Anzeige |
| `warmwasser_laeuft` | binary_sensor, der an ist, solange aufgeheizt wird – verhindert doppeltes Starten |
| `warmwasser_heizt` | binary_sensor für den Kompressor – nur dann zeigt die App „heizt gerade“ |
| `warmwasser_soll` | number-Entität der Zieltemperatur – der Knopf stellt sie beim Aufheizen auf die Vorgabe aus der Verwaltung |
| `staubsauger_entitaet` | der Saugroboter (`vacuum.…`), leer = keine Staubsauger-Karte |
| `staubsauger_batterie` | Sensor für den Akkustand, leer = Akku aus dem Staubsauger selbst |
| `kamera_entitaet` | Kamera für das Livebild (nur Familie), leer = kein Livebild |
| `pollen_praefix` | Anfang der IDs der DWD-Pollenflug-Sensoren (`sensor.pollenflug_`), leer = keine Pollen-Karte |
| `wlan_name`, `wlan_schluessel`, `wlan_verschluesselung` | Gäste-WLAN für den QR-Code-Knopf; ohne Name kein Knopf. `WPA` gilt auch für WPA2/WPA3 |

## Sicherheit

- Ein neuer Zugang kann erst öffnen, wenn du ihn freigegeben hast.
- Nach 5 Fehlversuchen ist ein Konto 15 Minuten gesperrt. Unabhängig davon
  wird ein Absender nach 20 Fehlversuchen in 15 Minuten gebremst.
- Ein Code aus der Authenticator-App gilt nur einmal.
- Ohne 2FA-Pflicht schützt bei Zugängen ohne 2FA nur das Passwort. Wer die
  2FA ausschaltet, braucht dafür Passwort und Code, und du bekommst eine
  Nachricht. Schaltest du die Pflicht später ein, muss jeder ohne 2FA sie
  bei der nächsten Anfrage einrichten.
- Sperren, „2FA zurücksetzen“ und ein Passwortwechsel melden alle Geräte des
  Zugangs sofort ab. Nach „2FA zurücksetzen“ ist wieder deine Freigabe nötig.
- Passwort vergessen: den Zugang löschen, der Freund registriert sich neu.
- Zwischen zwei Öffnungen liegen mindestens 8 Sekunden, zwischen zweimal
  Aufheizen mindestens eine Minute.
- Das Familien-Recht gilt nur für freigegebene Zugänge. Sperren nimmt es
  sofort mit weg.
- Die Standortprüfung schützt vor **versehentlichem** Öffnen von unterwegs.
  Gegen jemanden, der seinen Standort fälscht, schützt sie nicht – dagegen
  helfen Freigabe, 2FA und das Protokoll.

## Zum vorhandenen Webhook

Das Add-on braucht den Webhook nicht. Wenn du ihn behältst: `GET` und `HEAD`
aus `allowed_methods` herausnehmen. Vorschaufunktionen von Messengern und
Browsern rufen Links per GET ab – ein im Heimnetz geteilter Link könnte
sonst die Tür öffnen.
