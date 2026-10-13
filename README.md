# Türöffner – Home-Assistant-Add-on

Die Haustür per Handy öffnen: eigene Zugänge für Freunde mit Passwort, 2FA
(Pflicht oder freiwillig) und Passkey, Freigabe durch dich, Öffnen nur in der Nähe der Tür (außer mit
Fern-Recht), Benachrichtigung bei jedem Öffnen.

[![Add-on-Repository hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fgregorwolf1973%2Ftuer-oeffner-addon)

Einrichtung und Optionen: [tuer_oeffner/DOCS.md](tuer_oeffner/DOCS.md).

## Tests

```bash
pip install -r tuer_oeffner/requirements.txt pytest
pytest tests
```
