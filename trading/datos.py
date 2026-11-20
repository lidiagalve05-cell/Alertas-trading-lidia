"""Lectura de velas cerradas del recopilador."""
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite

INTERVALO = timedelta(minutes=5)
ACTIVOS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
COLUMNAS = ("hora", "activo", "apertura", "maximo", "minimo", "cierre", "volumen")


@dataclass(frozen=True)
class Vela:
    hora: datetime
    apertura: float
    maximo: float
    minimo: float
    cierre: float
    volumen: float

    @property
    def fin(self):
        return self.hora + INTERVALO


def leer_csv(ruta):
    """Devuelve velas ordenadas por activo. Un CSV inválido falla de forma explícita."""
    registros = {}
    with open(ruta, encoding="utf-8-sig", newline="") as archivo:
        lector = csv.DictReader(archivo, strict=True)
        if not set(COLUMNAS).issubset(lector.fieldnames or ()):
            raise ValueError("Faltan columnas obligatorias en el CSV")
        for fila in lector:
            try:
                hora = datetime.fromisoformat(fila["hora"])
                if hora.utcoffset() is None:
                    raise ValueError("Hora sin zona")
                hora = hora.astimezone(timezone.utc)
                if hora.minute % 5 or hora.second or hora.microsecond:
                    raise ValueError("Hora fuera del intervalo de cinco minutos")
                activo = fila["activo"].strip().upper()
                valores = tuple(float(fila[c]) for c in COLUMNAS[2:])
                vela = Vela(hora, *valores)
                if (None in fila or not activo or not all(map(isfinite, valores))
                        or vela.minimo <= 0 or vela.volumen < 0
                        or not vela.minimo <= min(vela.apertura, vela.cierre)
                        <= max(vela.apertura, vela.cierre) <= vela.maximo):
                    raise ValueError("OHLCV inválido")
                serie = registros.setdefault(activo, {})
                if hora in serie and serie[hora] != vela:
                    raise ValueError("Duplicado contradictorio")
                serie[hora] = vela
            except (ValueError, TypeError, AttributeError, OverflowError) as error:
                raise ValueError(f"CSV inválido en la línea {lector.line_num}") from error
    return {activo: sorted(serie.values(), key=lambda v: v.hora)
            for activo, serie in registros.items()}
