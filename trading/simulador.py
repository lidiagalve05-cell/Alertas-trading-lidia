"""Cartera ficticia de contado. Solo consume velas locales."""
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from math import isfinite

from .analista import historial
from .datos import INTERVALO


@dataclass(frozen=True)
class Configuracion:
    capital: float = 100
    comision: float = 0.001
    deslizamiento: float = 0.001
    fraccion: float = 0.2
    stop: float = 0.03
    limite_drawdown: float = 0.10
    euros_por_usdt: float = 1.0

    def __post_init__(self):
        if not all(isfinite(valor) for valor in vars(self).values()):
            raise ValueError("La configuración debe contener valores finitos")
        if not (self.capital > 0 and self.euros_por_usdt > 0
                and 0 < self.fraccion <= 1 and 0 < self.stop < 1
                and 0 < self.limite_drawdown < 1
                and 0 <= self.comision < 1 and 0 <= self.deslizamiento < 1):
            raise ValueError("Capital, costes o límites fuera de rango")


@dataclass(frozen=True)
class Orden:
    hora: datetime
    lado: str
    motivo: str


@dataclass(frozen=True)
class Operacion:
    activo: str
    orden: Orden
    hora: datetime
    cantidad: float
    precio: float
    comision: float
    flujo: float
    resultado: float | None


@dataclass(frozen=True)
class Posicion:
    cantidad: float
    coste: float


class Cartera:
    def __init__(self, config):
        self.config = config
        self.caja = self.pico = self.patrimonio = config.capital
        self.drawdown = 0.0
        self.posiciones = {}
        self.operaciones = []
        self.bloqueada = False
        self.pendientes = {}
        self.ultimo_cierre = None

    def precio(self, vela, lado):
        ajuste = self.config.deslizamiento * (1 if lado == "compra" else -1)
        return vela.cierre * self.config.euros_por_usdt * (1 + ajuste)

    def liquidacion(self, posicion, vela):
        return posicion.cantidad * self.precio(vela, "venta") * (1 - self.config.comision)

    def valorar(self, velas):
        self.patrimonio = self.caja + sum(
            self.liquidacion(posicion, velas[activo])
            for activo, posicion in self.posiciones.items()
        )
        self.pico = max(self.pico, self.patrimonio)
        caida = 1 - self.patrimonio / self.pico
        self.drawdown = max(self.drawdown, caida)
        if caida >= self.config.limite_drawdown:
            self.bloqueada = True

    def ejecutar(self, activo, orden, vela):
        precio = self.precio(vela, orden.lado)
        resultado = None
        if orden.lado == "compra":
            if self.bloqueada or activo in self.posiciones:
                return
            coste = min(self.caja, self.patrimonio * self.config.fraccion)
            if coste <= 0:
                return
            bruto = coste / (1 + self.config.comision)
            cantidad = bruto / precio
            flujo = -coste
            self.posiciones[activo] = Posicion(cantidad, coste)
        else:
            posicion = self.posiciones.pop(activo, None)
            if posicion is None:
                return
            cantidad = posicion.cantidad
            bruto = cantidad * precio
            flujo = bruto * (1 - self.config.comision)
            resultado = flujo - posicion.coste
        self.caja += flujo
        self.operaciones.append(Operacion(
            activo, orden, vela.fin, cantidad, precio,
            bruto * self.config.comision, flujo, resultado,
        ))

    def procesar(self, hora, datos, activos):
        if self.ultimo_cierre is not None and hora <= self.ultimo_cierre:
            return
        if set(datos) != activos or any(
            vela.fin != hora or vela.volumen <= 0 for vela, _ in datos.values()
        ):
            return
        velas = {activo: vela for activo, (vela, _) in datos.items()}
        self.ultimo_cierre = hora
        self.valorar(velas)
        for activo, orden in sorted(self.pendientes.items(), key=lambda item: (
            item[1].lado == "compra", item[0],
        )):
            urgente = orden.lado == "riesgo"
            analisis = datos[activo][1]
            vigente = hora - orden.hora == INTERVALO and analisis is not None
            if urgente:
                self.ejecutar(activo, Orden(orden.hora, "venta", orden.motivo), velas[activo])
            elif vigente and (orden.lado == "venta"
                               or not any(s.tipo == "riesgo" for s in analisis.senales)):
                self.ejecutar(activo, orden, velas[activo])
            self.valorar(velas)

        self.pendientes = {}
        for activo, (vela, analisis) in datos.items():
            posicion = self.posiciones.get(activo)
            if posicion and self.bloqueada:
                self.pendientes[activo] = Orden(hora, "riesgo", "Límite de drawdown de cartera")
            elif posicion and self.liquidacion(posicion, vela) <= posicion.coste * (1 - self.config.stop):
                self.pendientes[activo] = Orden(hora, "riesgo", "Límite de pérdida de la posición")
            elif analisis and (posicion or not self.bloqueada):
                lado = "venta" if posicion else "compra"
                for senal in analisis.senales:
                    if senal.tipo == lado and (posicion or not any(
                        s.tipo == "riesgo" for s in analisis.senales
                    )):
                        self.pendientes[activo] = Orden(hora, lado, senal.motivo)
                        break

    def metricas(self):
        cerradas = [op.resultado for op in self.operaciones if op.resultado is not None]
        total = self.patrimonio - self.config.capital
        return {
            "Patrimonio neto estimado (€ ficticios)": self.patrimonio,
            "Efectivo (€ ficticios)": self.caja,
            "Resultado neto total (€ ficticios)": total,
            "Resultado realizado (€ ficticios)": sum(cerradas),
            "Rentabilidad neta (%)": 100 * total / self.config.capital,
            "Drawdown máximo (%)": 100 * self.drawdown,
            "Aciertos (%)": 100 * sum(r > 0 for r in cerradas) / len(cerradas) if cerradas else None,
            "Posiciones abiertas": len(self.posiciones),
        }


def simular(series, config, hasta):
    """Reproduce la cartera desde cero hasta el instante indicado, en orden causal."""
    eventos = sorted(
        (vela.fin, activo, vela, analisis)
        for activo, velas in series.items()
        for vela, analisis in historial(v for v in velas if v.fin <= hasta)
    )
    cartera = Cartera(config)
    activos = set(series)
    for hora, grupo in groupby(eventos, key=lambda evento: evento[0]):
        cartera.procesar(hora, {activo: (vela, analisis)
                               for _, activo, vela, analisis in grupo}, activos)
    return cartera
