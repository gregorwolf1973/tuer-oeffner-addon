# Änderungen

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
