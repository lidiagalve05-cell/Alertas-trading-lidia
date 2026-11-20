"""Punto de entrada del analista y del simulador histórico."""
import argparse
import csv
from datetime import datetime, timezone
from math import isfinite

from .datos import ACTIVOS, leer_csv
from .informe import generar
from .simulador import Configuracion, simular


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default="datos-cripto.csv")
    parser.add_argument("--activo", action="append", help="Símbolo USDT, repetible")
    parser.add_argument("--simular", action="store_true", help="Reproducir el histórico desde cero")
    parser.add_argument("--hasta", help="Corte ISO 8601 con zona horaria")
    parser.add_argument("--max-retraso", type=float, default=10, help="Minutos para marcar los datos como atrasados")
    for nombre, valor in vars(Configuracion()).items():
        parser.add_argument("--" + nombre.replace("_", "-"), type=float, default=valor)
    args = parser.parse_args()
    try:
        ahora = datetime.now(timezone.utc)
        hasta = datetime.fromisoformat(args.hasta) if args.hasta else ahora
        if hasta.utcoffset() is None or hasta > ahora:
            raise ValueError("El corte requiere zona horaria y debe ser igual o anterior al momento actual")
        if not isfinite(args.max_retraso) or args.max_retraso < 0:
            raise ValueError("El retraso máximo debe ser finito y no negativo")
        activos = tuple(a.strip().upper() for a in (args.activo or ACTIVOS))
        if any(len(a) <= 4 or not a.endswith("USDT") for a in activos):
            raise ValueError("Se requieren símbolos cotizados en USDT")
        datos = leer_csv(args.csv)
        series = {activo: datos.get(activo, []) for activo in activos}
        config = Configuracion(**{nombre: getattr(args, nombre) for nombre in vars(Configuracion())})
    except (ValueError, OSError, csv.Error) as error:
        parser.error(str(error))
    cartera = simular(series, config, hasta) if args.simular else None
    print(generar(series, hasta, args.max_retraso, cartera), end="")


if __name__ == "__main__":
    main()
