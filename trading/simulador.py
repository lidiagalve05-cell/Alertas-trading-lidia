"""Cartera ficticia de contado. Solo consume velas locales."""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import groupby
from math import isclose, isfinite

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
    limite_diario: float | None = None
    limite_acumulado: float | None = None
    importe_minimo: float = 1.0
    participacion_maxima: float = 0.01
    coste_maximo: float = 0.10

    def __post_init__(self):
        if not all(isfinite(v) for v in vars(self).values() if v is not None):
            raise ValueError("La configuración debe contener valores finitos")
        if not (self.capital > 0 and self.euros_por_usdt > 0
                and 0 < self.fraccion <= 1 and 0 < self.stop < 1
                and 0 < self.limite_drawdown < 1
                and 0 <= self.comision < 1 and 0 <= self.deslizamiento < 1
                and 0 < self.importe_minimo <= self.capital
                and 0 < self.participacion_maxima <= 1 and 0 <= self.coste_maximo < 1):
            raise ValueError("Capital, costes, liquidez o límites fuera de rango")
        for limite in (self.limite_diario, self.limite_acumulado):
            if limite is not None and not 0 < limite <= self.capital:
                raise ValueError("Los límites monetarios deben ser positivos y no superar el capital")
        factor_entrada = (1 + self.deslizamiento) * (1 + self.comision)
        retorno_salida = (1 - self.deslizamiento) * (1 - self.comision)
        if 1 - retorno_salida / factor_entrada > self.coste_maximo + 1e-12:
            raise ValueError("El coste estimado de ida y vuelta supera el máximo configurado")


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
    deslizamiento: float
    movimiento: float | None


@dataclass(frozen=True)
class Posicion:
    cantidad: float
    coste: float
    hora: datetime
    referencia: float


class Cartera:
    def __init__(self, config):
        self.config = config
        self.caja = self.pico = self.patrimonio = config.capital
        self.drawdown = 0.0
        self.perdida_diaria_maxima = self.perdida_acumulada_maxima = 0.0
        self.posiciones = {}
        self.operaciones = []
        self.rechazos = Counter()
        self.bloqueos_permanentes = set()
        self.bloqueo_diario = False
        self.dia = None
        self.base_diaria = config.capital
        self.pendientes = {}
        self.ultimo_cierre = None
        self.ultimo_evento = None
        self.corte = None
        self.cierres_omitidos = 0
        self.marcas = {}
        self.salidas_sin_liquidez = set()

    @property
    def bloqueos(self):
        motivos = sorted(self.bloqueos_permanentes)
        if self.bloqueo_diario:
            motivos.append("Límite de pérdida diaria")
        return motivos

    @property
    def bloqueada(self):
        return bool(self.bloqueos)

    def valoracion_disponible(self, hasta=None, max_retraso=10):
        if self.ultimo_cierre is None or self.ultimo_cierre != self.ultimo_evento:
            return False
        hasta = hasta if hasta is not None else self.corte
        if hasta is None:
            return True
        edad = hasta - self.ultimo_cierre
        return timedelta(0) <= edad < INTERVALO and edad <= timedelta(minutes=max_retraso)

    def descartar_ordinarias(self):
        for activo, orden in list(self.pendientes.items()):
            if orden.lado != "riesgo":
                del self.pendientes[activo]
                self.rechazos[activo, "Orden ordinaria caducada por falta de un cierre válido"] += 1

    def cerrar_periodo(self, hasta):
        self.corte = hasta
        if self.ultimo_evento is not None:
            faltan = (hasta - self.ultimo_evento) // INTERVALO
            self.cierres_omitidos += faltan
            if faltan:
                self.descartar_ordinarias()

    def precio(self, vela, lado):
        ajuste = self.config.deslizamiento * (1 if lado == "compra" else -1)
        return vela.cierre * self.config.euros_por_usdt * (1 + ajuste)

    def liquidacion(self, posicion, vela):
        return posicion.cantidad * self.precio(vela, "venta") * (1 - self.config.comision)

    def hay_liquidez(self, cantidad, vela):
        return cantidad <= vela.volumen * self.config.participacion_maxima

    def valorar(self, velas):
        self.marcas = {activo: self.liquidacion(posicion, velas[activo])
                       for activo, posicion in self.posiciones.items()}
        self.salidas_sin_liquidez = {
            activo for activo, posicion in self.posiciones.items()
            if not self.hay_liquidez(posicion.cantidad, velas[activo])
        }
        self.patrimonio = self.caja + sum(self.marcas.values())
        self.pico = max(self.pico, self.patrimonio)
        caida = (self.pico - self.patrimonio) / self.pico
        self.drawdown = max(self.drawdown, caida)
        if caida >= self.config.limite_drawdown or isclose(caida, self.config.limite_drawdown):
            self.bloqueos_permanentes.add("Límite de drawdown de cartera")
        diario, acumulado = self.config.limite_diario, self.config.limite_acumulado
        perdida_diaria = self.base_diaria - self.patrimonio
        perdida_acumulada = self.config.capital - self.patrimonio
        self.perdida_diaria_maxima = max(self.perdida_diaria_maxima, perdida_diaria)
        self.perdida_acumulada_maxima = max(self.perdida_acumulada_maxima, perdida_acumulada)
        if diario is not None and (perdida_diaria >= diario or isclose(perdida_diaria, diario)):
            self.bloqueo_diario = True
        if acumulado is not None and (perdida_acumulada >= acumulado or isclose(perdida_acumulada, acumulado)):
            self.bloqueos_permanentes.add("Límite de pérdida acumulada")

    def ejecutar(self, activo, orden, vela):
        referencia = vela.cierre * self.config.euros_por_usdt
        precio = self.precio(vela, orden.lado)
        resultado = movimiento = None
        if orden.lado == "compra":
            if self.bloqueada or activo in self.posiciones:
                self.rechazos[activo, "Entrada bloqueada por riesgo o posición existente"] += 1
                return False
            coste = min(self.caja, self.patrimonio * self.config.fraccion)
            bruto = coste / (1 + self.config.comision)
            if bruto < self.config.importe_minimo:
                self.rechazos[activo, "Importe de entrada inferior al mínimo"] += 1
                return False
            cantidad = bruto / precio
            flujo = -coste
        else:
            posicion = self.posiciones.get(activo)
            if posicion is None:
                return False
            cantidad = posicion.cantidad
            bruto = cantidad * precio
            flujo = bruto * (1 - self.config.comision)
            resultado = flujo - posicion.coste
            movimiento = cantidad * (referencia - posicion.referencia)

        if not self.hay_liquidez(cantidad, vela):
            self.rechazos[activo, f"Liquidez insuficiente para {orden.lado}"] += 1
            return False

        if orden.lado == "compra":
            self.posiciones[activo] = Posicion(cantidad, coste, vela.fin, referencia)
        else:
            del self.posiciones[activo]
        self.caja += flujo
        self.operaciones.append(Operacion(
            activo, orden, vela.fin, cantidad, precio,
            bruto * self.config.comision, flujo, resultado,
            cantidad * abs(precio - referencia), movimiento,
        ))
        return True

    def procesar(self, hora, datos, activos):
        if self.ultimo_evento is not None:
            if hora <= self.ultimo_evento:
                return
            self.cierres_omitidos += max(0, (hora - self.ultimo_evento) // INTERVALO - 1)
        self.ultimo_evento = hora
        if set(datos) != activos or any(
            vela.fin != hora or vela.volumen <= 0 for vela, _ in datos.values()
        ):
            self.cierres_omitidos += 1
            self.descartar_ordinarias()
            return

        dia = hora.astimezone(timezone.utc).date()
        if dia != self.dia:
            self.dia = dia
            self.base_diaria = self.patrimonio
            self.bloqueo_diario = False
        velas = {activo: vela for activo, (vela, _) in datos.items()}
        self.ultimo_cierre = hora
        self.valorar(velas)

        anteriores, self.pendientes = self.pendientes, {}
        for activo, orden in sorted(anteriores.items(), key=lambda item: (
            item[1].lado != "riesgo", item[1].lado == "compra", item[0],
        )):
            analisis = datos[activo][1]
            if orden.lado == "riesgo":
                venta = Orden(orden.hora, "venta", orden.motivo)
                if not self.ejecutar(activo, venta, velas[activo]):
                    self.pendientes[activo] = orden
            elif hora - orden.hora != INTERVALO or analisis is None:
                self.rechazos[activo, "Orden ordinaria caducada o sin análisis disponible"] += 1
            elif orden.lado == "compra" and any(s.tipo == "riesgo" for s in analisis.senales):
                self.rechazos[activo, "Entrada descartada por riesgo del activo"] += 1
            else:
                self.ejecutar(activo, orden, velas[activo])
            self.valorar(velas)

        for activo, (_, analisis) in datos.items():
            if activo in self.pendientes:
                continue
            posicion = self.posiciones.get(activo)
            perdida = (posicion.coste - self.marcas[activo]) / posicion.coste if posicion else 0
            if posicion and self.bloqueada:
                self.pendientes[activo] = Orden(hora, "riesgo", " / ".join(self.bloqueos))
            elif posicion and (perdida >= self.config.stop or isclose(perdida, self.config.stop)):
                self.pendientes[activo] = Orden(hora, "riesgo", "Límite de pérdida de la posición")
            elif analisis and (posicion or not self.bloqueada):
                lado = "venta" if posicion else "compra"
                for senal in analisis.senales:
                    if senal.tipo == lado and (posicion or not any(
                        s.tipo == "riesgo" for s in analisis.senales
                    )):
                        self.pendientes[activo] = Orden(hora, lado, senal.motivo)
                        break

    def metricas(self, hasta=None, max_retraso=10):
        cerradas = [op.resultado for op in self.operaciones if op.resultado is not None]
        disponible = self.valoracion_disponible(hasta, max_retraso)
        total = self.patrimonio - self.config.capital
        return {
            "Patrimonio neto estimado (€ ficticios)": self.patrimonio if disponible else None,
            "Efectivo (€ ficticios)": self.caja,
            "Resultado neto total (€ ficticios)": total if disponible else None,
            "Resultado realizado (€ ficticios)": sum(cerradas),
            "Rentabilidad neta (%)": 100 * total / self.config.capital if disponible else None,
            "Drawdown máximo observado (%)": 100 * self.drawdown if self.ultimo_cierre else None,
            "Pérdida diaria máxima observada (€ ficticios)": self.perdida_diaria_maxima if self.ultimo_cierre else None,
            "Pérdida acumulada máxima observada (€ ficticios)": self.perdida_acumulada_maxima if self.ultimo_cierre else None,
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
    cartera.cerrar_periodo(hasta)
    return cartera
