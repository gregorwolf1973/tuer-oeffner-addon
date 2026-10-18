# Änderungen

## 0.10.0

- Standort im Hintergrund mit **OwnTracks**: „OwnTracks einrichten“ erzeugt
  eigene Zugangsdaten und einen Link, der OwnTracks fertig einrichtet.
  OwnTracks schickt den Ort dann auch bei geschlossener App an
  `/owntracks`; er erscheint in „Wo ist die Familie?“. Die Freigabe in der
  App bleibt der Hauptschalter.

## 0.9.0

- „Wo ist die Familie?“: Jeder Familien-Zugang kann seinen Standort selbst
  freigeben. Die anderen sehen ihn auf einer kleinen OpenStreetMap-Karte und
  in einer Liste mit Entfernung von zu Hause, Alter und Link zu Google Maps.
  Gesendet wird alle 2 Minuten, solange die App offen ist. Freigabe beenden
  löscht den Ort sofort, alte Orte verschwinden nach 7 Tagen.

## 0.8.0

- Pollenflug für die Familie: Karte mit der DWD-Vorhersage für heute,
  morgen und übermorgen. Belastete Arten stehen mit Farbstufe oben, die
  übrigen eingeklappt. Neue Option `pollen_praefix`, voreingestellt die
  DWD-Pollenflug-Integration.

## 0.7.0

- Warmwasser ehrlicher: „Heizt gerade“ nur, wenn der Kompressor läuft,
  sonst „angefordert“. Ist das Wasser schon wärmer als die Zieltemperatur,
  ist der Knopf gesperrt und die App sagt warum. Die Zieltemperatur steht
  mit in der Karte.
- Verwaltung übersichtlicher: jeder Zugang als Karte mit Schaltern für „Aus
  der Ferne“ und „Familie“, Seltenes unter „Mehr“. Neu: die
  Warmwasser-Zieltemperatur ist dort mit − / + einstellbar.
- Neue Optionen `warmwasser_heizt` und `warmwasser_soll`.

## 0.6.0

- Gäste-WLAN: Knopf „WLAN-QR-Code zeigen“ für jeden freigegebenen Zugang,
  mit QR-Code zum Verbinden, WLAN-Name und Schlüssel (mit Kopieren). Neue
  Optionen `wlan_name`, `wlan_schluessel` und `wlan_verschluesselung`.

## 0.5.0

- Die Haustür hat jetzt eine eigene Karte wie Warmwasser und Staubsauger.
- Livebild der Kamera in der Haustür-Karte, nur für Zugänge der Familie.
  Etwa ein Bild pro Sekunde – und nur, solange die App im Vordergrund und
  das Bild zu sehen ist. Neue Option `kamera_entitaet`, voreingestellt die
  T62. Mehrere offene Apps teilen sich ein Bild, die Kamera wird höchstens
  gut einmal pro Sekunde gefragt.

## 0.4.1

- Die App ist jetzt dunkel. Unter „Konto“ → „Darstellung“ lässt sich auf
  Hell oder „Wie das Handy“ umstellen; die Wahl merkt sich jedes Gerät.

## 0.4.0

- Staubsauger für die Familie: Status und Akku in der App, dazu „Saugen
  starten“ und „Zur Station“. Neue Optionen `staubsauger_entitaet` und
  `staubsauger_batterie`, voreingestellt der Roborock S7 MaxV.
- Aus dem Recht „Warmwasser“ wird **„Familie“**: es schaltet Warmwasser und
  Staubsauger zusammen frei. Wer das Warmwasser-Recht hatte, gehört
  automatisch zur Familie.
- Verwaltung: eigene Spalte „Familie“ mit Knopf zum Hinzufügen und Entfernen,
  dazu ein Hinweis, solange noch niemand zur Familie gehört.

## 0.3.0

- Warmwasser für die Familie: Zugänge mit dem neuen Recht „Warmwasser“
  sehen in der App die Speichertemperatur und einen großen Knopf „Wasser
  aufheizen“. Er löst die eingestellte Entität aus, voreingestellt die
  einmalige Ladung der Vitocal-Wärmepumpe. Läuft schon eine Ladung, ist der
  Knopf gesperrt.
- Neue Optionen `warmwasser_entitaet`, `warmwasser_temperatur` und
  `warmwasser_laeuft`.
- Verwaltung: Spalte und Knopf für das Warmwasser-Recht, Aufheizen steht im
  Protokoll.

## 0.2.0

- Anmelden mit Benutzername oder Mailadresse. Neue Zugänge wählen bei der
  Registrierung einen Benutzernamen; bestehende melden sich weiter mit der
  Mailadresse an.
- Neue Option `zwei_faktor_pflicht`. Aus = Passwort reicht, jeder schaltet
  die 2FA unter „Konto“ selbst ein oder aus (Ausschalten mit Passwort und
  Code). Voreinstellung bleibt: Pflicht.
- Passwort mindestens 8 statt 10 Zeichen.
- Verwaltung zeigt Benutzername und ob die 2FA an ist.

## 0.1.0

- Erste Fassung: App für Freunde mit Registrierung, Pflicht-2FA (TOTP) und
  Passkeys, Öffnen per Wisch mit Standortprüfung, Fern-Recht je Zugang,
  Benachrichtigung bei jedem Öffnen, Verwaltung nur über den Ingress.
