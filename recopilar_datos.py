import requests
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

MONEDAS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
ARCHIVO = "datos-cripto.csv"
URL = "https://data-api.binance.vision/api/v3/klines"

INTERVALO = timedelta(minutes=5)
RETRASO_MAXIMO = timedelta(minutes=10)

COLUMNAS = [
    "hora", "activo", "apertura", "maximo",
    "minimo", "cierre", "volumen", "presion_compradora"
]


def leer_datos():
    registros = {}

    if not os.path.exists(ARCHIVO):
        return registros

    with open(ARCHIVO, "r", encoding="utf-8", newline="") as archivo:
        lector = csv.DictReader(archivo)

        for fila in lector:
            if not fila.get("hora") or not fila.get("activo"):
                continue

            clave = (fila["hora"], fila["activo"])
            registros[clave] = fila

    return registros


def horas_por_moneda(registros, moneda):
    return sorted(datetime.fromisoformat(hora) for hora, activo in registros if activo == moneda)


def huecos(horas):
    for anterior, siguiente in zip(horas, horas[1:]):
        if siguiente - anterior > INTERVALO:
            yield anterior + INTERVALO, siguiente - INTERVALO


def crear_sesion():
    reintentos = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods={"GET"},
    )
    sesion = requests.Session()
    sesion.mount(URL, HTTPAdapter(max_retries=reintentos))
    return sesion


def descargar_velas(sesion, moneda, desde=None, hasta=None):
    nuevas = []
    inicio = int(desde.timestamp() * 1000) if desde is not None else None

    while True:
        parametros = {
            "symbol": moneda,
            "interval": "5m",
            "limit": 1000
        }

        if inicio is not None:
            parametros["startTime"] = inicio

        if hasta is not None:
            parametros["endTime"] = int(hasta.timestamp() * 1000)

        respuesta = sesion.get(
            URL, params=parametros, timeout=20
        )
        respuesta.raise_for_status()
        velas = respuesta.json()

        if not velas:
            break

        ahora_ms = int(
            datetime.now(timezone.utc).timestamp() * 1000
        )

        for vela in velas:
            if int(vela[6]) >= ahora_ms:
                continue

            volumen = float(vela[5])
            compras = float(vela[9])

            presion = (
                compras / volumen * 100
                if volumen > 0 else 0
            )

            hora = datetime.fromtimestamp(
                vela[0] / 1000, timezone.utc
            ).isoformat()

            nuevas.append({
                "hora": hora,
                "activo": moneda,
                "apertura": vela[1],
                "maximo": vela[2],
                "minimo": vela[3],
                "cierre": vela[4],
                "volumen": volumen,
                "presion_compradora": round(presion, 2)
            })

        inicio = int(velas[-1][0]) + 1

        if len(velas) < 1000 or (hasta is not None and inicio > parametros["endTime"]):
            break

    return nuevas


def guardar_datos(registros):
    filas = sorted(
        registros.values(),
        key=lambda fila: (fila["hora"], fila["activo"])
    )

    temporal = ARCHIVO + ".tmp"

    with open(
        temporal, "w", newline="", encoding="utf-8"
    ) as archivo:
        escritor = csv.DictWriter(
            archivo, fieldnames=COLUMNAS
        )
        escritor.writeheader()
        escritor.writerows(filas)

    os.replace(temporal, ARCHIVO)


def avisar(mensaje):
    prefijo = "::warning::" if os.getenv("GITHUB_ACTIONS") == "true" else "Aviso: "
    print(prefijo + mensaje, flush=True)


def comprobar_frescura(registros):
    ahora = datetime.now(timezone.utc)
    atrasadas = []
    for moneda in MONEDAS:
        horas = horas_por_moneda(registros, moneda)
        if not horas:
            atrasadas.append(f"{moneda} sin velas")
            continue
        retraso = ahora - (horas[-1] + INTERVALO)
        if retraso > RETRASO_MAXIMO:
            atrasadas.append(f"{moneda}: última vela cerrada hace {retraso.total_seconds() / 60:.1f} min")
    return atrasadas


def main():
    registros = leer_datos()
    total_anterior = len(registros)
    for incidencia in comprobar_frescura(registros):
        avisar(f"CSV atrasado antes de recopilar: {incidencia}")

    with crear_sesion() as sesion:
        for moneda in MONEDAS:
            horas = horas_por_moneda(registros, moneda)
            inicio = horas[-1] + INTERVALO if horas else None
            nuevas = descargar_velas(sesion, moneda, desde=inicio)
            for desde, hasta in huecos(horas):
                avisar(f"{moneda}: hueco entre {desde.isoformat()} y {hasta.isoformat()}")
                nuevas.extend(descargar_velas(sesion, moneda, desde=desde, hasta=hasta))

            for fila in nuevas:
                registros.setdefault((fila["hora"], fila["activo"]), fila)

            for desde, hasta in huecos(horas_por_moneda(registros, moneda)):
                avisar(f"{moneda}: siguen faltando velas entre {desde.isoformat()} y {hasta.isoformat()}")

            print(moneda, "velas descargadas:", len(nuevas))

    if len(registros) > total_anterior:
        guardar_datos(registros)

    print("Registros nuevos:", len(registros) - total_anterior)
    print("Total de registros:", len(registros))


if __name__ == "__main__":
    if sys.argv[1:] == ["--comprobar-frescura"]:
        incidencias = comprobar_frescura(leer_datos())
        for incidencia in incidencias:
            avisar(f"CSV atrasado después de recopilar: {incidencia}")
        sys.exit(bool(incidencias))
    main()
