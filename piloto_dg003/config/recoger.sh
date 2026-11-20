#!/bin/sh
set -eu
actual=$(date -u +%s)
cierre=$(( (actual - 30) / 300 * 300 ))
programada=$(date -u -d "@$((cierre + 30))" +%FT%TZ)
exec /usr/bin/python3 -m piloto_dg003.piloto recoger \
  --inicio-utc "${INICIO_DG003:?Falta el inicio UTC autorizado}" \
  --programado-utc "$programada" --carpeta /var/lib/dg003-recoger
