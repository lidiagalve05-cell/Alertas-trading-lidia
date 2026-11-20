import csv
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import recopilar_datos as bot


class PruebasRecopilador(unittest.TestCase):
    def test_descarga_solo_velas_cerradas_y_calcula_presion(self):
        apertura = 1_700_000_100_000
        intervalo = 300_000
        sesion = Mock()
        sesion.get.return_value.json.return_value = [
            [apertura, "100", "110", "90", "105", "20", apertura + intervalo - 1, "0", 10, "7"],
            [apertura + intervalo, "105", "110", "100", "106", "10",
             apertura + 2 * intervalo - 1, "0", 5, "3"],
        ]
        with patch.object(bot, "datetime", wraps=datetime) as reloj:
            reloj.now.return_value = datetime.fromtimestamp(
                (apertura + intervalo + 1_000) / 1_000, timezone.utc
            )
            velas = bot.descargar_velas(sesion, "BTCUSDT")

        sesion.get.assert_called_once_with(
            bot.URL, params={"symbol": "BTCUSDT", "interval": "5m", "limit": 1000}, timeout=20
        )
        self.assertEqual(len(velas), 1)
        self.assertEqual(velas[0], {
            "hora": datetime.fromtimestamp(apertura / 1_000, timezone.utc).isoformat(),
            "activo": "BTCUSDT",
            "apertura": "100",
            "maximo": "110",
            "minimo": "90",
            "cierre": "105",
            "volumen": 20.0,
            "presion_compradora": 35.0,
        })

    def test_detecta_hueco(self):
        inicio = datetime(2026, 11, 14, tzinfo=timezone.utc)
        self.assertEqual(
            list(bot.huecos([inicio, inicio + 3 * bot.INTERVALO])),
            [(inicio + bot.INTERVALO, inicio + 2 * bot.INTERVALO)],
        )

    def test_detecta_atraso_y_activo_sin_velas(self):
        ahora = datetime(2026, 11, 20, 9, tzinfo=timezone.utc)
        registros = {
            ((ahora - bot.INTERVALO).isoformat(), "BTCUSDT"): {},
            ((ahora - 4 * bot.INTERVALO).isoformat(), "ETHUSDT"): {},
        }
        with patch.object(bot, "datetime", wraps=datetime) as reloj:
            reloj.now.return_value = ahora
            avisos = bot.comprobar_frescura(registros)
        self.assertEqual(avisos, [
            "ETHUSDT: última vela cerrada hace 15.0 min",
            "SOLUSDT sin velas",
        ])

    def test_reintentos_http_acotados(self):
        with bot.crear_sesion() as sesion:
            reintentos = sesion.get_adapter(bot.URL).max_retries
        self.assertEqual(reintentos.total, 3)
        self.assertTrue(reintentos.is_retry("GET", 503))
        self.assertFalse(reintentos.is_retry("GET", 404))


class PruebasCSV(unittest.TestCase):
    def leer_csv(self, ruta):
        with open(ruta, encoding="utf-8", newline="") as archivo:
            lector = csv.DictReader(archivo)
            self.assertEqual(lector.fieldnames, bot.COLUMNAS)
            filas = {}
            for fila in lector:
                self.assertNotIn(None, fila)
                self.assertTrue(all(fila.values()), fila)
                clave = (fila["hora"], fila["activo"])
                self.assertNotIn(clave, filas)
                filas[clave] = fila
            return filas

    def test_csv_tiene_filas_unicas_de_los_tres_activos(self):
        filas = self.leer_csv(bot.ARCHIVO)
        self.assertEqual({activo for _, activo in filas}, set(bot.MONEDAS))

    @unittest.skipUnless(os.getenv("CSV_BASE"), "CSV_BASE se proporciona en el pull request")
    def test_csv_conserva_todas_las_filas_de_la_rama_base(self):
        base = self.leer_csv(os.environ["CSV_BASE"])
        actual = self.leer_csv(bot.ARCHIVO)
        for clave, fila in base.items():
            self.assertEqual(actual.get(clave), fila, f"Fila eliminada o modificada: {clave}")
