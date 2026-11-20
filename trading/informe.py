"""Informe en español y contrato para futuros canales de alertas."""
from datetime import timedelta
from typing import Protocol

from .analista import historial
from .datos import INTERVALO


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
        if hasta - vela.fin >= INTERVALO or hasta - vela.fin > timedelta(minutes=max_retraso):
            lineas.append("- Riesgo de datos. Vela desactualizada. Los indicadores son históricos.")
        if vela.volumen <= 0:
            lineas.append("- Riesgo de datos. El último cierre tiene volumen cero.")
        if analisis is None:
            lineas.append("- Riesgo de datos. Se requieren 50 velas consecutivas para evaluar señales.")
            continue
        lineas.append(f"- Tendencia {analisis.tendencia}.")
        lineas.extend(f"- {clave} = {numero(valor)}" for clave, valor in analisis.indicadores.items())
        lineas.extend(f"- **{senal.tipo.capitalize()}** · {senal.motivo}" for senal in analisis.senales)
        if not analisis.senales:
            lineas.append("- Sin señales según los umbrales configurados en el analista.")

    if cartera is not None:
        disponible = cartera.valoracion_disponible(hasta, max_retraso)
        lineas.extend(["", "## Simulación histórica",
                       f"- Última valoración conjunta válida · {cartera.ultimo_cierre or 'n/d'}",
                       f"- Cierres omitidos desde el primer dato · {cartera.cierres_omitidos}"])
        if not disponible:
            lineas.append("- Riesgo de datos. Valoración conjunta y estado de riesgo al corte indeterminados. "
                          "Se omiten patrimonio, rentabilidad y P/L de posiciones abiertas.")
        if cartera.cierres_omitidos:
            lineas.append("- El drawdown observado puede subestimar las pérdidas durante interrupciones.")
        lineas.extend(f"- {clave} = {numero(valor)}"
                      for clave, valor in cartera.metricas(hasta, max_retraso).items())
        for motivo in cartera.bloqueos:
            lineas.append(f"- Bloqueo de nuevas compras registrado · {motivo}.")
        for activo, orden in sorted(cartera.pendientes.items()):
            lineas.append(f"- Orden pendiente · {activo} · {orden.lado} · {orden.hora.isoformat()} · {orden.motivo}")

        lineas.extend(diagnostico(series, cartera, disponible))
        lineas.extend(["", "### Operaciones ficticias"])
        if not cartera.operaciones:
            lineas.append("- Sin ejecuciones.")
        for op in cartera.operaciones:
            lineas.append(
                f"- {op.hora.isoformat()} · {op.activo} · {op.orden.lado} · "
                f"{numero(op.cantidad)} unidades a {numero(op.precio)} € ficticios · "
                f"Comisión {numero(op.comision)} € · Deslizamiento {numero(op.deslizamiento)} € · "
                f"Flujo {numero(op.flujo)} € · P/L realizado {numero(op.resultado)} € · "
                f"Señal {op.orden.hora.isoformat()} · {op.orden.motivo}"
            )
    return "\n".join(lineas) + "\n"


def diagnostico(series, cartera, disponible):
    por_activo = {activo: [] for activo in series}
    for op in cartera.operaciones:
        por_activo[op.activo].append(op)
    lineas = ["", "### Diagnóstico por activo"]
    for activo, operaciones in por_activo.items():
        ventas = [op for op in operaciones if op.resultado is not None]
        lineas.extend([
            "", f"#### {activo}",
            f"- Entradas {len(operaciones) - len(ventas)} · Salidas {len(ventas)} · "
            f"P/L realizado {numero(sum(op.resultado for op in ventas))} €",
            f"- Comisiones pagadas {numero(sum(op.comision for op in operaciones))} € · "
            f"Deslizamiento soportado {numero(sum(op.deslizamiento for op in operaciones))} €",
        ])
        for op in ventas:
            if op.resultado < 0:
                friccion = op.movimiento - op.resultado
                causa = "movimiento adverso y costes de ida y vuelta" if op.movimiento < 0 else "costes de ida y vuelta"
                lineas.append(f"- Pérdida del {op.hora.isoformat()} · {causa} · "
                              f"Movimiento bruto {numero(op.movimiento)} € · Fricción {numero(friccion)} € · "
                              f"P/L {numero(op.resultado)} € · Motivo de salida {op.orden.motivo}")
        for (simbolo, motivo), cantidad in sorted(cartera.rechazos.items()):
            if simbolo == activo:
                lineas.append(f"- Órdenes rechazadas o caducadas · {motivo} · {cantidad}")
        posicion = cartera.posiciones.get(activo)
        if posicion:
            marca = cartera.marcas[activo] if disponible else None
            resultado = marca - posicion.coste if marca is not None else None
            lineas.append(f"- Posición abierta desde {posicion.hora.isoformat()} · "
                          f"{numero(posicion.cantidad)} unidades · Coste {numero(posicion.coste)} € · "
                          f"Valor neto estimado {numero(marca)} € · P/L no realizado {numero(resultado)} €")
            if disponible and activo in cartera.salidas_sin_liquidez:
                lineas.append("- Riesgo de liquidez. El valor estimado es teórico. "
                              "La posición supera la participación permitida en el último cierre.")
        else:
            lineas.append("- Sin posición abierta.")
    return lineas


def publicar(texto: str, canal: CanalAlertas) -> None:
    canal.enviar(texto)
