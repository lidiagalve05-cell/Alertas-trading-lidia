import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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
