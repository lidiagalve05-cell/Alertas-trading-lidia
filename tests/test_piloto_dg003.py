import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from piloto_dg003 import piloto
from piloto_dg003.piloto import ACTIVOS, CONTROL, PASO, VENTANA, cierres, observar, recoger, resumen

INICIO = datetime(2026, 11, 20, tzinfo=timezone.utc)
PILOTO = "https://piloto.example/velas.json"


def respuesta(url):
    q = parse_qs(urlsplit(url).query)
    desde, hasta = (int(q[k][0]) for k in ("startTime", "endTime"))
    return [[i, 10, 11, 9, 10, 1, i + 299999] for i in range(desde, hasta, 300000)]


class PilotoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.carpeta = Path(tmp.name)
        salida = contextlib.redirect_stdout(io.StringIO())
        salida.__enter__()
        self.addCleanup(salida.__exit__, None, None, None)

    def recoger(self, minutos, red=lambda url: (json.dumps(respuesta(url)).encode(), None)):
        fecha = INICIO + timedelta(minutes=minutos, seconds=30)
        recoger(INICIO, fecha, self.carpeta, reloj=lambda: fecha, red=red)

    def leer(self):
        return json.loads((self.carpeta / "publico/velas.json").read_text())["velas"]

    def test_repetir_cierre_conserva_marcas_y_evitar_duplicados(self):
        self.recoger(5)
        original = self.leer()
        self.recoger(5, lambda _: self.fail("No debe volver a pedir velas publicadas"))
        self.assertEqual(self.leer(), original)
        self.assertEqual(len(original), 3)

    def test_rechaza_respuesta_duplicada(self):
        with self.assertRaises(RuntimeError):
            self.recoger(5, lambda url: (json.dumps(respuesta(url) * 2).encode(), None))
        self.assertFalse((self.carpeta / "publico/velas.json").exists())

    def test_caida_parcial_y_recuperacion_en_el_siguiente_ciclo(self):
        def red(url):
            if "SOLUSDT" in url:
                raise OSError("Interrupción simulada")
            return json.dumps(respuesta(url)).encode(), None

        with self.assertRaises(RuntimeError):
            self.recoger(5, red)
        original = self.leer()
        self.assertEqual(len(original), 2)
        self.recoger(10)
        final = self.leer()
        self.assertEqual(len(final), 6)
        self.assertEqual({k: final[k] for k in original}, original)
        recuperada = final[f"SOLUSDT|{(INICIO + PASO).isoformat()}"]
        self.assertTrue(recuperada["recuperada"])
        self.assertGreater(recuperada["recibido_utc"], recuperada["cierre_utc"])

    def test_primera_observacion_y_latencia_despues_de_interrupcion(self):
        csv = b"activo,hora,apertura,maximo,minimo,cierre,volumen\n"
        csv += "".join(f"{a},{INICIO.isoformat()},10,11,9,10,1\n" for a in ACTIVOS).encode()
        self.recoger(5)

        def red(url):
            if url == CONTROL:
                return csv, "control"
            if momento.minute == 6:
                raise OSError("Interrupción simulada de lectura")
            return (self.carpeta / "publico/velas.json").read_bytes(), "piloto"

        observaciones = self.carpeta / "observador"
        for minutos in (6, 12, 13):
            momento = INICIO + timedelta(minutes=minutos)
            observar(INICIO, observaciones, PILOTO, reloj=lambda: momento, red=red)
        with contextlib.redirect_stdout(io.StringIO()) as salida:
            resumen(INICIO, observaciones)
        registros = [json.loads(l) for l in salida.getvalue().splitlines()]
        for fuente, latencia in (("control", 60), ("piloto", 420)):
            r = next(x for x in registros if x["fuente"] == fuente)
            self.assertEqual(r["latencia_s"], latencia)
            self.assertEqual(r["puntual_comprobado"], fuente == "control")
            self.assertEqual(r["primera_observacion_utc"]["conjunto"],
                             (INICIO + PASO + timedelta(seconds=latencia)).isoformat())

    def test_ventana_contiene_576_cierres_y_excluye_los_posteriores(self):
        lista = list(cierres(INICIO, INICIO + VENTANA + PASO))
        self.assertEqual(len(lista), 576)
        self.assertEqual(lista[0], INICIO + PASO)
        self.assertEqual(lista[-1], INICIO + VENTANA)

    def test_reinicio_publica_persistencia_parcial_sin_red(self):
        guardar = piloto.guardar
        def falla_publicacion(ruta, datos, modo=0o600):
            if ruta.parent.name == "publico":
                raise OSError("Destino de publicación caído")
            guardar(ruta, datos, modo)

        with patch.object(piloto, "guardar", side_effect=falla_publicacion):
            with self.assertRaises(RuntimeError):
                self.recoger(5)
        respaldos = sorted((self.carpeta / "privado").glob("*.json"))
        self.assertEqual(len(respaldos), 3)
        self.assertFalse((self.carpeta / "publico/velas.json").exists())
        self.recoger(5, lambda _: self.fail("El estado persistido debe publicarse sin red"))
        publicado = self.leer()
        for ruta in respaldos:
            for clave, vela in json.loads(ruta.read_text())["velas"].items():
                self.assertEqual(publicado[clave], vela)
                self.assertFalse(vela["recepcion_fuera_de_plazo"])

    def test_reinicio_tras_caida_entre_escrituras_del_lote(self):
        guardar = piloto.guardar
        def falla_metadatos(ruta, datos, modo=0o600):
            if ruta.parent.name == "privado" and any("persistido_utc" in v for v in datos["velas"].values()):
                raise OSError("Caída antes de guardar confirmación UTC")
            guardar(ruta, datos, modo)
        with patch.object(piloto, "guardar", side_effect=falla_metadatos):
            with self.assertRaises(RuntimeError):
                self.recoger(5)
        self.recoger(5, lambda _: self.fail("El lote previo debe recuperarse sin red"))
        for vela in self.leer().values():
            self.assertTrue(vela["persistencia_verificada_al_reanudar"])

    def test_interrupcion_prolongada_distingue_las_tres_marcas(self):
        self.recoger(5)
        self.recoger(55)
        observado = INICIO + timedelta(minutes=56)
        def red(url):
            if url == CONTROL:
                raise OSError("Control no disponible")
            return (self.carpeta / "publico/velas.json").read_bytes(), "piloto"
        observar(INICIO, self.carpeta, PILOTO, reloj=lambda: observado, red=red)
        registros = piloto.evidencia(INICIO, self.carpeta, "piloto")
        filas = list(piloto.detalle(INICIO, registros, "piloto"))
        etapa_primera = filas[0]["etapas"]["BTCUSDT"]
        self.assertFalse(etapa_primera["ejecucion_programada_posterior"])
        self.assertFalse(etapa_primera["recepcion_fuera_de_plazo"])
        self.assertTrue(etapa_primera["primera_observacion_fuera_de_plazo"])
        self.assertEqual(filas[0]["clasificacion"], "desconocido")
        etapa_recuperada = filas[1]["etapas"]["BTCUSDT"]
        self.assertTrue(etapa_recuperada["ejecucion_programada_posterior"])
        self.assertTrue(etapa_recuperada["recepcion_fuera_de_plazo"])
        metricas = piloto.informe(INICIO, self.carpeta)["fuentes"]["piloto"]
        self.assertEqual(metricas["frescura_minutos"], {"fresco": 1, "atrasado": 0, "desconocido": 2879})
        self.assertEqual(metricas["incidentes"][0]["minutos"], 56)
        self.assertEqual(metricas["resultado"], "inconcluso")
        self.assertEqual(metricas["latencia_s"]["conjunto"], {"p50": 1560, "p95": 3060, "p99": 3060})

    def test_reloj_desfasado_no_da_evidencia_ni_hace_peticion_anticipada(self):
        with self.assertRaises(ValueError):
            recoger(INICIO, INICIO + PASO, self.carpeta, reloj=lambda: INICIO,
                    red=lambda _: self.fail("Disparo futuro"))
        self.recoger(5)
        antes = INICIO + PASO
        observar(INICIO, self.carpeta, PILOTO, reloj=lambda: antes,
                 red=lambda _: ((self.carpeta / "publico/velas.json").read_bytes(), None))
        r = piloto.evidencia(INICIO, self.carpeta, "piloto")[0]
        self.assertEqual(r["estado"], "error")
        self.assertEqual(piloto.informe(INICIO, self.carpeta)["fuentes"]["piloto"]["cobertura"], 0)

    def test_respuestas_http_con_indicios_de_cache_se_rechazan(self):
        from unittest.mock import MagicMock
        for headers in ({"Age": "10"}, {"Warning": '110 proxy "Response is stale"'}, {"X-Cache": "STALE"}):
            with self.subTest(headers=headers):
                respuesta_http = MagicMock()
                respuesta_http.geturl.return_value = PILOTO
                respuesta_http.headers = headers
                with patch.object(piloto, "urlopen") as abrir:
                    abrir.return_value.__enter__.return_value = respuesta_http
                    with self.assertRaises(piloto.CacheHTTP):
                        piloto.descargar(PILOTO)
                    self.assertIn("no-cache", abrir.call_args.args[0].get_header("Cache-control"))
                    respuesta_http.read.assert_not_called()

    def test_control_duplicado_se_cuenta_como_error_y_minuto_desconocido(self):
        fecha = INICIO + PASO
        fila = f"BTCUSDT,{INICIO.isoformat()},10,11,9,10,1\n"
        cuerpo = ("activo,hora,apertura,maximo,minimo,cierre,volumen\n" + fila * 2).encode()
        observar(INICIO, self.carpeta, PILOTO, reloj=lambda: fecha, red=lambda _: (cuerpo, None))
        control = piloto.informe(INICIO, self.carpeta)["fuentes"]["control"]
        self.assertEqual(control["duplicados_en_lecturas"], 1)
        self.assertEqual(control["frescura_minutos"]["desconocido"], 2880)
        self.assertEqual(control["puntualidad"]["desconocido"], 576)

    def test_informe_completo_e_integridad_en_ambos_cortes(self):
        registros = []
        presentes = {}
        for minuto in range(48 * 60 + 6):
            observado = INICIO + timedelta(minutes=minuto)
            if minuto and minuto % 5 == 1 and minuto <= 48 * 60 + 1:
                presentes[(observado - timedelta(minutes=1)).isoformat()] = list(ACTIVOS)
            registros.append({"estado": "ok", "observado_utc": observado.isoformat(),
                              "presentes": presentes.copy(), "antiguedad_s": (minuto % 5 + 1) * 60})
        informe = piloto.medir(INICIO, registros, "piloto")
        self.assertEqual(informe["resultado"], "cumple")
        self.assertEqual(informe["puntualidad"]["puntual"], 576)
        self.assertEqual(informe["latencia_s"]["conjunto"], {"p50": 60, "p95": 60, "p99": 60})
        self.assertEqual([c["velas_faltantes"] for c in informe["integridad"]], [0, 0])
        self.assertEqual(informe["cobertura"], 1)
        sin_corte_final = piloto.medir(INICIO, registros[:-1], "piloto")
        self.assertEqual(sin_corte_final["resultado"], "inconcluso")
        self.assertIsNone(sin_corte_final["integridad"][-1]["velas_faltantes"])

    def test_disparo_desconocido_conserva_recepcion_y_arranque(self):
        entrada = INICIO + timedelta(minutes=11)
        recoger(INICIO, None, self.carpeta, reloj=lambda: entrada,
                red=lambda url: (json.dumps(respuesta(url)).encode(), None))
        publicado = self.leer()
        primera = publicado[f"BTCUSDT|{(INICIO + PASO).isoformat()}"]
        self.assertIsNone(primera["programado_utc"])
        self.assertIsNone(primera["ejecucion_programada_posterior"])
        self.assertTrue(primera["recepcion_fuera_de_plazo"])
        self.assertEqual(primera["inicio_ejecucion_utc"], entrada.isoformat())
        cuerpo = (self.carpeta / "publico/velas.json").read_bytes()
        observar(INICIO, self.carpeta, PILOTO, reloj=lambda: entrada, red=lambda _: (cuerpo, None))
        registros = piloto.evidencia(INICIO, self.carpeta, "piloto")
        etapa = next(piloto.detalle(INICIO, registros, "piloto"))["etapas"]["BTCUSDT"]
        self.assertIsNone(etapa["ejecucion_programada_posterior"])
        self.assertTrue(etapa["primera_observacion_fuera_de_plazo"])

    def test_sondeos_piden_recursos_con_identificadores_distintos(self):
        with patch.object(piloto, "descargar", return_value=(b"{}", None)) as red:
            piloto.descargar_observacion(PILOTO + "?origen=prueba")
            piloto.descargar_observacion(PILOTO + "?origen=prueba")
        primera, segunda = [parse_qs(urlsplit(c.args[0]).query) for c in red.call_args_list]
        self.assertEqual(primera["origen"], ["prueba"])
        self.assertNotEqual(primera["dg003_observacion"], segunda["dg003_observacion"])
