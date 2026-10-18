#!/usr/bin/with-contenv bashio

export TUER_ENTITAET="$(bashio::config 'tuer_entitaet')"
export EXTERNE_ADRESSE="$(bashio::config 'externe_adresse')"
export RADIUS_METER="$(bashio::config 'radius_meter')"
export BENACHRICHTIGUNG="$(bashio::config 'benachrichtigung')"
export BREITENGRAD="$(bashio::config 'breitengrad')"
export LAENGENGRAD="$(bashio::config 'laengengrad')"
export SITZUNG_TAGE="$(bashio::config 'sitzung_tage')"
export REGISTRIERUNG_OFFEN="$(bashio::config 'registrierung_offen')"
export ZWEI_FAKTOR_PFLICHT="$(bashio::config 'zwei_faktor_pflicht')"
export WARMWASSER_ENTITAET="$(bashio::config 'warmwasser_entitaet')"
export WARMWASSER_TEMPERATUR="$(bashio::config 'warmwasser_temperatur')"
export WARMWASSER_LAEUFT="$(bashio::config 'warmwasser_laeuft')"
export STAUBSAUGER_ENTITAET="$(bashio::config 'staubsauger_entitaet')"
export STAUBSAUGER_BATTERIE="$(bashio::config 'staubsauger_batterie')"
export LOG_LEVEL="$(bashio::config 'log_level')"
export DATA_DIR=/data

bashio::log.info "Türöffner startet auf Port 8097"
exec python3 /opt/tuer/server.py
