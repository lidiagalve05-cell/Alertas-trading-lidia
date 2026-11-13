import requests
import csv
import os
from datetime import datetime, timezone

MONEDAS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
ARCHIVO = "datos-cripto.csv"

def obtener_precios():
    datos = []

    for moneda in MONEDAS:
        url = "https://data-api.binance.vision/api/v3/klines"

        parametros = {
            "symbol": moneda,
            "interval": "5m",
            "limit": 2
        }

        respuesta = requests.get(
            url, params=parametros, timeout=15
        )
        respuesta.raise_for_status()

        vela = respuesta.json()[-2]

        volumen = float(vela[5])
        compras = float(vela[9])

        presion = (
            compras / volumen * 100
            if volumen > 0 else 0
        )

        datos.append([
            datetime.fromtimestamp(
                vela[0] / 1000, timezone.utc
            ).isoformat(),
            moneda,
            vela[1],
            vela[2],
            vela[3],
            vela[4],
            volumen,
            round(presion, 2)
        ])

    return datos


def guardar_datos(datos):
    existe = os.path.exists(ARCHIVO)

    with open(
        ARCHIVO, "a", newline="", encoding="utf-8"
    ) as archivo:
        escritor = csv.writer(archivo)

        if not existe or os.path.getsize(ARCHIVO) == 0:
            escritor.writerow([
                "hora", "activo", "apertura",
                "maximo", "minimo", "cierre",
                "volumen", "presion_compradora"
            ])

        escritor.writerows(datos)


if __name__ == "__main__":
    datos = obtener_precios()
    guardar_datos(datos)
    print("Datos recopilados correctamente")
