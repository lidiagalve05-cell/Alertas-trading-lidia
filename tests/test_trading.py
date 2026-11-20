import csv
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from trading.analista import Analisis, RSI, Senal, historial
from trading.datos import ACTIVOS, COLUMNAS, INTERVALO, Vela, leer_csv
from trading.informe import generar, publicar
from trading.simulador import Cartera, Configuracion, simular

INICIO = datetime(2025, 1, 1, tzinfo=timezone.utc)
RAIZ = Path(__file__).resolve().parents[1]


def vela(i, precio=100, volumen=10):
    return Vela(INICIO + i * INTERVALO, precio, precio, precio, precio, volumen)


def decision(tipo):
    return Analisis("lateral", {}, (Senal(tipo, tipo),))


class IndicadoresTest(unittest.TestCase):
    def test_volumen_cero_reinicia_la_ventana_de_senales(self):
        velas = [vela(i) for i in range(100)]
        velas[50] = vela(50, precio=50, volumen=0)
        resultados = list(historial(velas + [vela(100)]))
        self.assertIsNotNone(resultados[49][1])
        self.assertTrue(all(a is None for _, a in resultados[50:100]))
        self.assertEqual(resultados[100][1].indicadores["RSI14"], 50)

    def test_rsi_wilder(self):
        precios = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10,
                   45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
                   46.28, 46.00]
        rsi = RSI()
        valores = [rsi.agregar(p) for p in precios]
        self.assertEqual(valores[:14], [None] * 14)
        self.assertAlmostEqual(valores[14], 70.46, places=2)
        self.assertAlmostEqual(valores[15], 66.25, places=2)
        plano = RSI()
        self.assertEqual([plano.agregar(100) for _ in range(15)][-1], 50)

    def test_medias_extremos_volumen_y_volatilidad(self):
        # Veinte retornos de cero salvo el último, que es de 10 %.
        velas = [vela(i) for i in range(49)] + [vela(49, 110, 30)]
        analisis = list(historial(velas))[-1][1]
        indicadores = analisis.indicadores
        for n in (9, 21, 50):
            self.assertAlmostEqual(indicadores[f"SMA{n}"], 100 + 10 / n)
        self.assertEqual(indicadores["Máximo previo (20)"], 100)
        self.assertEqual(indicadores["Mínimo previo (20)"], 100)
        self.assertAlmostEqual(indicadores["Volatilidad (20 retornos, %)"], 19 ** 0.5 / 2)
        self.assertAlmostEqual(indicadores["Variación del cierre (%)"], 10)
        self.assertEqual(indicadores["Cambio de volumen (%)"], 200)
        self.assertEqual(indicadores["Volumen / media previa"], 3)
        self.assertEqual({s.tipo for s in analisis.senales}, {"volumen", "riesgo", "venta"})
        self.assertIsNone(list(historial(velas + [vela(51)]))[-1][1])


class CSVTest(unittest.TestCase):
    def test_lector_en_csv_del_repositorio(self):
        series = leer_csv(RAIZ / "datos-cripto.csv")
        for activo in ACTIVOS:
            velas = series[activo]
            self.assertGreater(len(velas), 50)
            self.assertEqual([v.hora for v in velas], sorted({v.hora for v in velas}))

    def test_orden_deduplicacion_y_rechazo_de_datos_invalidos(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "velas.csv"
            primera = [INICIO.isoformat(), "btcusdt", 100, 101, 99, 100, 10]
            segunda = [(INICIO + INTERVALO).isoformat(), "BTCUSDT", 101, 102, 100, 101, 11]
            casos = (None, primera[:-1] + [12], segunda[:-1] + ["nan"], segunda[:-1])
            for mala in casos:
                with self.subTest(fila=mala):
                    with ruta.open("w", encoding="utf-8", newline="") as archivo:
                        escritor = csv.writer(archivo)
                        escritor.writerow(COLUMNAS)
                        escritor.writerows([segunda, primera, primera])
                        if mala is not None:
                            escritor.writerow(mala)
                    if mala is None:
                        self.assertEqual([v.cierre for v in leer_csv(ruta)["BTCUSDT"]], [100, 101])
                    else:
                        with self.assertRaises(ValueError):
                            leer_csv(ruta)


class SimuladorTest(unittest.TestCase):
    def procesar(self, cartera, i, precio=100, tipo=None):
        v = vela(i, precio, volumen=100)
        cartera.procesar(v.fin, {"BTCUSDT": (v, decision(tipo) if tipo else None)}, {"BTCUSDT"})

    def test_comisiones_deslizamiento_y_resultado(self):
        cartera = Cartera(Configuracion(comision=.01, deslizamiento=.02, fraccion=.5))
        self.procesar(cartera, 0, tipo="compra")
        self.assertFalse(cartera.operaciones)
        self.procesar(cartera, 1, tipo="venta")
        self.procesar(cartera, 2, precio=120)
        compra, venta = cartera.operaciones
        cantidad = 50 / 1.01 / 102
        bruto_venta = cantidad * 117.6
        self.assertAlmostEqual(compra.cantidad, cantidad)
        self.assertAlmostEqual(compra.comision, (50 / 1.01) * .01)
        self.assertAlmostEqual(venta.comision, bruto_venta * .01)
        self.assertAlmostEqual(venta.resultado, bruto_venta * .99 - 50)
        self.assertAlmostEqual(cartera.caja, 50 + bruto_venta * .99)
        self.assertAlmostEqual(cartera.metricas()["Rentabilidad neta (%)"], venta.resultado)
        self.assertEqual(cartera.metricas()["Aciertos (%)"], 100)

    def test_stop_y_bloqueo_por_drawdown(self):
        for precio, bloqueada in [(94, False), (70, True)]:
            with self.subTest(precio=precio):
                cartera = Cartera(Configuracion(fraccion=.8, stop=.03))
                self.procesar(cartera, 0, tipo="compra")
                self.procesar(cartera, 1, tipo="compra")
                self.procesar(cartera, 2, precio, "compra")
                self.procesar(cartera, 3, precio, "compra")
                self.procesar(cartera, 4, precio, "compra")
                self.assertEqual(cartera.bloqueada, bloqueada)
                self.assertEqual([op.orden.lado for op in cartera.operaciones],
                                 ["compra", "venta"] if bloqueada else ["compra", "venta", "compra"])
                self.assertEqual(cartera.operaciones[1].hora, vela(3).fin)
                self.assertIn("Límite", cartera.operaciones[1].orden.motivo)

    def test_ignora_cierres_repetidos_o_fuera_de_orden(self):
        cartera = Cartera(Configuracion())
        self.procesar(cartera, 0, tipo="compra")
        self.procesar(cartera, 1, tipo="venta")
        self.procesar(cartera, 0, tipo="compra")
        self.procesar(cartera, 1, tipo="compra")
        self.procesar(cartera, 2, tipo="venta")
        self.assertEqual([op.orden.lado for op in cartera.operaciones], ["compra", "venta"])

    def test_omite_velas_incompletas_y_ordenes_caducadas(self):
        cartera = Cartera(Configuracion())
        self.procesar(cartera, 0, tipo="compra")
        incompleta = vela(1, volumen=0)
        cartera.procesar(incompleta.fin, {"BTCUSDT": (incompleta, decision("compra"))}, {"BTCUSDT"})
        self.procesar(cartera, 2, tipo="compra")
        cartera.procesar(vela(3).fin, {"BTCUSDT": (vela(3), decision("compra"))}, set(ACTIVOS))
        self.procesar(cartera, 4, tipo="compra")
        self.assertFalse(cartera.operaciones)

    def test_replay_sin_duplicados_ni_datos_futuros(self):
        datos = leer_csv(RAIZ / "datos-cripto.csv")
        series = {a: datos[a][:350] for a in ACTIVOS}
        corte = series[ACTIVOS[0]][250].fin
        fin = max(v[-1].fin for v in series.values())
        config = Configuracion()
        prefijo = {a: [v for v in velas if v.fin <= corte] for a, velas in series.items()}
        pasado = simular(prefijo, config, corte)
        con_futuro = simular(series, config, corte)
        completo = simular(series, config, fin)
        repetido = simular(series, config, fin)
        self.assertTrue(pasado.operaciones)
        self.assertEqual(pasado.metricas(), con_futuro.metricas())
        self.assertEqual(pasado.operaciones, [op for op in completo.operaciones if op.hora <= corte])
        self.assertEqual(pasado.operaciones, con_futuro.operaciones)
        self.assertEqual(completo.operaciones, repetido.operaciones)
        claves = {(op.activo, op.hora) for op in completo.operaciones}
        self.assertEqual(len(claves), len(completo.operaciones))
        self.assertTrue(all(op.orden.hora < op.hora for op in completo.operaciones))

    def test_informe_atrasado_y_canal_inyectable(self):
        texto = generar({"BTCUSDT": [vela(0)]}, vela(4).fin, 10)
        self.assertIn("desactualizada", texto)
        self.assertIn("50 velas consecutivas", texto)

        class CanalMemoria:
            mensajes = []

            def enviar(self, mensaje):
                self.mensajes.append(mensaje)

        canal = CanalMemoria()
        publicar(texto, canal)
        self.assertEqual(canal.mensajes, [texto])

    def test_cli_aislada_de_red_y_recopilador(self):
        prueba = """
import runpy
import sys

def aislar(evento, args):
    if evento.startswith(('socket.', 'subprocess.', 'os.exec', 'os.system')):
        raise AssertionError(evento)
    if evento == 'import' and args[0].split('.')[0] in {'requests', 'recopilar_datos', 'ccxt', 'binance'}:
        raise AssertionError(args[0])

sys.addaudithook(aislar)
sys.argv = ['trading', '--simular', '--hasta', '2026-11-15T00:00:00+00:00']
runpy.run_module('trading', run_name='__main__')
"""
        entorno = {clave: os.environ[clave] for clave in ("PATH", "LANG") if clave in os.environ}
        salida = subprocess.run([sys.executable, "-c", prueba], cwd=RAIZ, env=entorno,
                                capture_output=True, text=True, check=True)
        self.assertIn("Operaciones ficticias", salida.stdout)
        self.assertIn("unidades a", salida.stdout)


class ControlesRiesgoTest(unittest.TestCase):
    def procesar(self, cartera, i, precio=100, tipo=None, volumen=100, completo=True):
        v = vela(i, precio, volumen)
        datos = {"BTCUSDT": (v, decision(tipo or "sin señal"))}
        if completo:
            datos["ETHUSDT"] = (v, None)
        cartera.procesar(v.fin, datos, {"BTCUSDT", "ETHUSDT"})

    def test_posicion_abierta_durante_interrupcion_y_recuperacion(self):
        cartera = Cartera(Configuracion())
        self.procesar(cartera, 0, tipo="compra")
        self.procesar(cartera, 1)
        self.procesar(cartera, 2, precio=93)  # programa el stop
        self.procesar(cartera, 3, precio=60, completo=False)
        self.procesar(cartera, 4, precio=65, volumen=0)
        self.assertEqual(len(cartera.posiciones), 1)
        self.assertEqual(len(cartera.operaciones), 1)
        self.assertFalse(cartera.valoracion_disponible())
        self.assertIsNone(cartera.metricas()["Rentabilidad neta (%)"])
        self.assertEqual(cartera.cierres_omitidos, 2)
        texto = generar({"BTCUSDT": [], "ETHUSDT": []}, vela(4).fin, 10, cartera)
        self.assertIn("estado de riesgo al corte indeterminados", texto)
        self.assertIn("P/L no realizado n/d", texto)
        self.assertIn("Orden pendiente · BTCUSDT · riesgo", texto)

        self.procesar(cartera, 6, precio=85)  # también falta todo el cierre 5
        self.assertTrue(cartera.valoracion_disponible())
        self.assertEqual(cartera.cierres_omitidos, 3)
        self.assertFalse(cartera.posiciones)
        venta = cartera.operaciones[-1]
        self.assertEqual(venta.orden.hora, vela(2).fin)
        self.assertEqual(venta.hora, vela(6).fin)
        self.assertLess(venta.resultado, -3)
        self.assertIsNotNone(cartera.metricas()["Rentabilidad neta (%)"])

    def test_drawdown_se_activa_en_el_umbral_exacto(self):
        cartera = Cartera(Configuracion(
            fraccion=1, comision=0, deslizamiento=0, limite_drawdown=.1, stop=.9,
        ))
        self.procesar(cartera, 0, tipo="compra")
        self.procesar(cartera, 1)
        self.procesar(cartera, 2, precio=90)
        self.assertEqual(cartera.bloqueos, ["Límite de drawdown de cartera"])
        self.assertEqual(cartera.metricas()["Drawdown máximo observado (%)"], 10)
        self.procesar(cartera, 3, precio=88)
        self.assertEqual(cartera.operaciones[-1].resultado, -12)

    def test_stop_se_activa_en_el_umbral_exacto(self):
        cartera = Cartera(Configuracion(comision=0, deslizamiento=0, stop=.03))
        self.procesar(cartera, 0, tipo="compra")
        self.procesar(cartera, 1)
        self.procesar(cartera, 2, precio=97)
        self.assertEqual(cartera.pendientes["BTCUSDT"].lado, "riesgo")
        self.procesar(cartera, 3, precio=95)
        self.assertAlmostEqual(cartera.operaciones[-1].resultado, -1)

    def test_corte_atrasado_sin_eventos_nuevos_con_exposicion(self):
        datos = leer_csv(RAIZ / "datos-cripto.csv")
        fin = datetime(2026, 11, 20, 9, 35, tzinfo=timezone.utc)
        series = {a: [v for v in datos[a] if v.fin <= fin] for a in ACTIVOS}
        config = Configuracion()
        reciente = simular(series, config, fin)
        atrasada = simular(series, config, fin + INTERVALO)
        self.assertTrue(reciente.posiciones)
        self.assertEqual(atrasada.posiciones, reciente.posiciones)
        self.assertEqual(atrasada.operaciones, reciente.operaciones)
        self.assertIsNone(atrasada.metricas()["Patrimonio neto estimado (€ ficticios)"])
        self.assertEqual(atrasada.cierres_omitidos, reciente.cierres_omitidos + 1)

    def test_limites_de_1_y_5_euros_con_capital_50(self):
        cartera = Cartera(Configuracion(
            capital=50, limite_diario=1, limite_acumulado=5, limite_drawdown=.9,
            fraccion=1, stop=.9, comision=0, deslizamiento=0,
        ))
        self.procesar(cartera, 0, tipo="compra")
        self.procesar(cartera, 1)
        self.procesar(cartera, 2, precio=98)  # patrimonio 49; umbral diario exacto
        self.assertEqual(cartera.bloqueos, ["Límite de pérdida diaria"])
        self.procesar(cartera, 3, precio=98, tipo="compra")
        self.procesar(cartera, 4, tipo="compra")
        self.assertEqual(len(cartera.operaciones), 2)
        self.assertEqual(cartera.caja, 49)

        self.procesar(cartera, 287, tipo="compra")  # 00:00 UTC del día siguiente
        self.assertFalse(cartera.bloqueada)
        self.assertEqual(cartera.base_diaria, 49)
        self.procesar(cartera, 288)
        self.procesar(cartera, 289, precio=100 * 45 / 49)
        self.assertIn("Límite de pérdida acumulada", cartera.bloqueos)
        self.procesar(cartera, 290, precio=100 * 45 / 49)
        self.assertAlmostEqual(cartera.caja, 45)
        self.procesar(cartera, 575, tipo="compra")
        self.procesar(cartera, 576, tipo="compra")
        self.assertEqual(cartera.bloqueos, ["Límite de pérdida acumulada"])
        self.assertEqual(len(cartera.operaciones), 4)

    def test_importe_minimo_y_validacion_de_costes_y_liquidez(self):
        for invalida in ({"importe_minimo": 0}, {"importe_minimo": 51},
                         {"participacion_maxima": 1.1}, {"comision": .08},
                         {"coste_maximo": 0}, {"limite_diario": -1},
                         {"limite_acumulado": float("nan")}):
            with self.subTest(invalida=invalida), self.assertRaises(ValueError):
                Configuracion(capital=50, **invalida)
        cartera = Cartera(Configuracion(capital=50, fraccion=.1, importe_minimo=5))
        self.procesar(cartera, 0, tipo="compra")
        self.procesar(cartera, 1)
        self.assertFalse(cartera.operaciones)  # tras la comisión, el nominal es inferior a 5
        self.assertEqual(cartera.rechazos["BTCUSDT", "Importe de entrada inferior al mínimo"], 1)

    def test_liquidez_limita_entradas_y_aplaza_salidas_urgentes(self):
        cartera = Cartera(Configuracion(capital=50, fraccion=1))
        self.procesar(cartera, 0, tipo="compra")
        self.procesar(cartera, 1, tipo="compra", volumen=1)
        self.assertFalse(cartera.operaciones)
        self.procesar(cartera, 2)
        self.procesar(cartera, 3, precio=95)
        self.procesar(cartera, 4, precio=90, volumen=1)
        self.assertEqual(len(cartera.operaciones), 1)
        self.assertIn("BTCUSDT", cartera.pendientes)
        texto = generar({"BTCUSDT": [], "ETHUSDT": []}, vela(4).fin, 10, cartera)
        self.assertIn("El valor estimado es teórico", texto)
        self.procesar(cartera, 5, precio=1)
        self.assertEqual(cartera.operaciones[-1].hora, vela(5).fin)
        self.assertEqual(cartera.operaciones[-1].orden.hora, vela(3).fin)
        self.assertLess(cartera.operaciones[-1].flujo, cartera.config.importe_minimo)
        self.assertEqual(cartera.rechazos["BTCUSDT", "Liquidez insuficiente para compra"], 1)
        self.assertEqual(cartera.rechazos["BTCUSDT", "Liquidez insuficiente para venta"], 1)

    def test_diagnostico_separa_friccion_de_movimiento_y_muestra_exposicion(self):
        cartera = Cartera(Configuracion())
        self.procesar(cartera, 0, tipo="compra")
        self.procesar(cartera, 1, tipo="venta")
        self.procesar(cartera, 2, tipo="compra")
        self.procesar(cartera, 3)
        compra, venta, _ = cartera.operaciones
        self.assertEqual(venta.movimiento, 0)
        self.assertAlmostEqual(-venta.resultado, compra.comision + venta.comision
                               + compra.deslizamiento + venta.deslizamiento)
        texto = generar({"BTCUSDT": [], "ETHUSDT": []}, vela(3).fin, 10, cartera)
        for detalle in ("Entradas 2 · Salidas 1", "Comisiones pagadas",
                        "Deslizamiento soportado", "costes de ida y vuelta",
                        "Posición abierta desde", "P/L no realizado"):
            self.assertIn(detalle, texto)
