"""Informe en español y contrato para futuros canales de alertas."""
from datetime import timedelta
from typing import Protocol

from .analista import historial


class CanalAlertas(Protocol):
    def enviar(self, texto: str) -> None: ...


def numero(valor):
    return "n/d" if valor is None else f"{valor:.8g}"


def generar(series, hasta, max_retraso, cartera=None):
    lineas = [f"# Informe de análisis · {hasta.isoformat()}"]
    for activo, velas in series.items():
        ultimo = None
        for ultimo in historial(v for v in velas if v.fin <= hasta):
            pass
        lineas.extend(["", f"## {activo}"])
        if ultimo is None:
            lineas.append("- Riesgo de datos. Sin velas cerradas.")
            continue
        vela, analisis = ultimo
        lineas.append(f"- Último cierre disponible a las {vela.fin.isoformat()}.")
        if hasta - vela.fin > timedelta(minutes=max_retraso):
            lineas.append("- Riesgo de datos. Vela desactualizada. Los indicadores son históricos.")
        if analisis is None:
            lineas.append("- Riesgo de datos. Se requieren 50 velas consecutivas para evaluar señales.")
            continue
        lineas.append(f"- Tendencia {analisis.tendencia}.")
        lineas.extend(f"- {clave} = {numero(valor)}" for clave, valor in analisis.indicadores.items())
        lineas.extend(f"- **{senal.tipo.capitalize()}** · {senal.motivo}" for senal in analisis.senales)
        if not analisis.senales:
            lineas.append("- Sin señales según los umbrales configurados en el analista.")

    if cartera is not None:
        lineas.extend(["", "## Simulación histórica", f"- Última valoración conjunta · {cartera.ultimo_cierre or 'n/d'}"])
        lineas.extend(f"- {clave} = {numero(valor)}" for clave, valor in cartera.metricas().items())
        if cartera.ultimo_cierre is not None and hasta - cartera.ultimo_cierre > timedelta(minutes=max_retraso):
            lineas.append("- Riesgo de datos. La valoración conjunta está desactualizada.")
        if cartera.bloqueada:
            lineas.append("- Riesgo de cartera. Se alcanzó el límite de drawdown y se bloquearon nuevas compras.")
        if cartera.pendientes:
            lineas.append(f"- Órdenes pendientes al final del período = {len(cartera.pendientes)}")
        lineas.extend(["", "### Operaciones ficticias"])
        if not cartera.operaciones:
            lineas.append("- Sin ejecuciones.")
        for op in cartera.operaciones:
            lineas.append(
                f"- {op.hora.isoformat()} · {op.activo} · {op.orden.lado} · "
                f"{numero(op.cantidad)} unidades a {numero(op.precio)} € ficticios · "
                f"Comisión {numero(op.comision)} € · Flujo {numero(op.flujo)} € · "
                f"P/L realizado {numero(op.resultado)} € · Señal {op.orden.hora.isoformat()} · {op.orden.motivo}"
            )
    return "\n".join(lineas) + "\n"


def publicar(texto: str, canal: CanalAlertas) -> None:
    canal.enviar(texto)
