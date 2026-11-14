import requests
import csv
import os
from datetime import datetime, timezone

MONEDAS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
ARCHIVO = "datos-cripto.csv"
URL = "https://data-api.binance.vision/api/v3/klines"

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


def obtener_ultima_hora(registros, moneda):
    horas = [
        datetime.fromisoformat(hora)
        for hora, activo in registros
        if activo == moneda
    ]

    return max(horas) if horas else None


def descargar_velas(moneda, ultima_hora):
    nuevas = []
    inicio = None

    if ultima_hora is not None:
        inicio = int(ultima_hora.timestamp() * 1000) + 1

    while True:
        parametros = {
            "symbol": moneda,
            "interval": "5m",
            "limit": 1000
        }

        if inicio is not None:
            parametros["startTime"] = inicio

        respuesta = requests.get(
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

        if len(velas) < 1000:
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


def main():
    registros = leer_datos()
    total_nuevos = 0

    for moneda in MONEDAS:
        ultima_hora = obtener_ultima_hora(
            registros, moneda
        )

        nuevas = descargar_velas(
            moneda, ultima_hora
        )

        for fila in nuevas:
            clave = (fila["hora"], fila["activo"])

            if clave not in registros:
                registros[clave] = fila
                total_nuevos += 1

        print(moneda, "velas descargadas:", len(nuevas))

    guardar_datos(registros)

    print("Registros nuevos:", total_nuevos)
    print("Total de registros:", len(registros))


if __name__ == "__main__":
    main()
