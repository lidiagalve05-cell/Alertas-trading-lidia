"""Indicadores y señales explicables, calculados con velas ya cerradas."""
from dataclasses import dataclass
from statistics import mean, pstdev

from .datos import INTERVALO

VENTANA = 50


def cambio(actual, anterior):
    return (actual / anterior - 1) * 100 if anterior else None


class RSI:
    """RSI de Wilder. Se reinicia si se interrumpe la serie."""
    def __init__(self):
        self.anterior = None
        self.ganancia = self.perdida = 0.0
        self.periodos = 0

    def agregar(self, cierre):
        anterior, self.anterior = self.anterior, cierre
        if anterior is None:
            return None
        delta = cierre - anterior
        ganancia, perdida = max(delta, 0), max(-delta, 0)
        self.periodos += 1
        if self.periodos <= 14:
            self.ganancia += ganancia / 14
            self.perdida += perdida / 14
        else:
            self.ganancia = (13 * self.ganancia + ganancia) / 14
            self.perdida = (13 * self.perdida + perdida) / 14
        if self.periodos < 14:
            return None
        if self.perdida == 0:
            return 100.0 if self.ganancia else 50.0
        return 100 - 100 / (1 + self.ganancia / self.perdida)


@dataclass(frozen=True)
class Senal:
    tipo: str
    motivo: str


@dataclass(frozen=True)
class Analisis:
    tendencia: str
    indicadores: dict
    senales: tuple


def historial(velas):
    ventana, rsi, tendencia = [], RSI(), None
    for vela in velas:
        if ventana and vela.hora - ventana[-1].hora != INTERVALO:
            ventana, rsi, tendencia = [], RSI(), None
        ventana.append(vela)
        ventana = ventana[-VENTANA:]
        oscilador = rsi.agregar(vela.cierre)
        resultado = None
        if len(ventana) == VENTANA:
            resultado = analizar(ventana, oscilador, tendencia)
            tendencia = resultado.tendencia
        yield vela, resultado


def analizar(ventana, rsi, anterior):
    cierres = [vela.cierre for vela in ventana]
    corta, media, larga = (mean(cierres[-n:]) for n in (9, 21, 50))
    vela = ventana[-1]
    previas = ventana[-21:-1]
    maximo = max(v.maximo for v in previas)
    minimo = min(v.minimo for v in previas)
    volumen_medio = mean(v.volumen for v in previas)
    ratio = vela.volumen / volumen_medio if volumen_medio else None
    variacion = cambio(vela.cierre, cierres[-2])
    volatilidad = pstdev(cambio(b, a) for a, b in zip(cierres[-21:-1], cierres[-20:]))
    if corta > media > larga:
        tendencia = "alcista"
    elif corta < media < larga:
        tendencia = "bajista"
    else:
        tendencia = "lateral"

    medias = f"SMA9 {corta:g}, SMA21 {media:g}, SMA50 {larga:g}"
    senales = []
    if tendencia == "alcista" and 50 <= rsi <= 75 and vela.cierre > maximo:
        senales.append(Senal("compra", f"Posible compra. Cierre {vela.cierre:g} supera el máximo previo {maximo:g}. "
                            f"{medias}. RSI14 {rsi:.2f}."))
    if (tendencia == "bajista" and vela.cierre < minimo) or rsi >= 80:
        motivo = (f"RSI14 en {rsi:.2f} (umbral 80)" if rsi >= 80 else
                  f"Cierre {vela.cierre:g} rompe el mínimo previo {minimo:g}. {medias}")
        senales.append(Senal("venta", f"Posible salida. {motivo}."))
    if anterior and tendencia != anterior and abs(variacion) >= 1:
        senales.append(Senal("giro", f"Tendencia de {anterior} a {tendencia} y variación del cierre de {variacion:+.2f} %."))
    if ratio is not None and ratio >= 2:
        senales.append(Senal("volumen", f"Volumen {ratio:.2f} veces la media de las 20 velas anteriores."))
    if volatilidad >= 0.8 or abs(variacion) >= 2 or not vela.volumen or ratio is None:
        senales.append(Senal("riesgo", f"Volatilidad {volatilidad:.2f} %, variación {variacion:+.2f} % y volumen {vela.volumen:g} (media previa {volumen_medio:g})."))

    return Analisis(tendencia, {
        "Cierre (USDT)": vela.cierre, "RSI14": rsi,
        "SMA9": corta, "SMA21": media, "SMA50": larga,
        "Volumen (activo base)": vela.volumen,
        "Cambio de volumen (%)": cambio(vela.volumen, ventana[-2].volumen),
        "Volumen / media previa": ratio,
        "Máximo previo (20)": maximo, "Mínimo previo (20)": minimo,
        "Variación del cierre (%)": variacion, "Volatilidad (20 retornos, %)": volatilidad,
    }, tuple(senales))
