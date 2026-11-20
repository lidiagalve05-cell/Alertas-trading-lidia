"""Herramientas del piloto de datos públicos DG-003."""
import argparse
import csv
import fcntl
import io
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ACTIVOS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
PASO = timedelta(minutes=5)
VENTANA = timedelta(hours=48)
API = "https://data-api.binance.vision/api/v3/klines"
CONTROL = ("https://raw.githubusercontent.com/lidiagalve05-cell/"
           "Alertas-trading-lidia/main/datos-cripto.csv")
MAX_BYTES = 16 * 1024 * 1024


def ahora():
    return datetime.now(timezone.utc)


def instante(texto):
    valor = datetime.fromisoformat(texto)
    if valor.utcoffset() != timedelta(0):
        raise ValueError("Se requiere un timestamp UTC explícito")
    return valor


def ultimo_cierre(hora):
    return hora.replace(minute=hora.minute // 5 * 5, second=0, microsecond=0)


def cierres(inicio, fin):
    cierre = ultimo_cierre(inicio) + PASO
    while cierre <= min(fin, inicio + VENTANA):
        yield cierre
        cierre += PASO


def descargar(url):
    if urlsplit(url).scheme != "https":
        raise ValueError("El origen de lectura debe usar HTTPS")
    peticion = Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "DG-003"})
    with urlopen(peticion, timeout=8) as respuesta:
        if urlsplit(respuesta.geturl()).scheme != "https":
            raise ValueError("Redirección a un origen sin HTTPS")
        datos = respuesta.read(MAX_BYTES + 1)
        if len(datos) > MAX_BYTES:
            raise ValueError("Respuesta demasiado grande")
        return datos, respuesta.headers.get("ETag")


def validar(valores):
    apertura, maximo, minimo, cierre, volumen = map(float, valores)
    if (not all(map(isfinite, (apertura, maximo, minimo, cierre, volumen)))
            or not 0 < minimo <= min(apertura, cierre) <= max(apertura, cierre) <= maximo
            or volumen < 0):
        raise ValueError("OHLCV inválido")
    return [apertura, maximo, minimo, cierre, volumen]


def guardar(ruta, datos, modo=0o600):
    """Publica un JSON completo mediante reemplazo atómico en el mismo volumen."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=ruta.parent, delete=False) as archivo:
        temporal = Path(archivo.name)
        try:
            os.fchmod(archivo.fileno(), modo)
            json.dump(datos, archivo, allow_nan=False)
            archivo.flush()
            os.fsync(archivo.fileno())
            os.replace(temporal, ruta)
            directorio = os.open(ruta.parent, os.O_DIRECTORY)
            try:
                os.fsync(directorio)
            finally:
                os.close(directorio)
        finally:
            temporal.unlink(missing_ok=True)


def leer(ruta, inicial):
    if ruta.exists():
        return json.loads(ruta.read_text())
    return inicial


def verificar_inicio(estado, inicio):
    if estado["inicio"] != inicio.isoformat():
        raise ValueError("El archivo pertenece a otra ventana de medición")


def recoger(inicio, programada, carpeta, reloj=ahora, red=descargar):
    entrada = reloj()
    disponibles = list(cierres(inicio, entrada - timedelta(seconds=30)))
    if not disponibles:
        return
    carpeta.mkdir(parents=True, exist_ok=True)
    with (carpeta / "escritor.lock").open("a") as bloqueo:
        fcntl.flock(bloqueo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ruta = carpeta / "publico" / "velas.json"
        estado = leer(ruta, {"inicio": inicio.isoformat(), "velas": {}})
        verificar_inicio(estado, inicio)
        for activo in ACTIVOS:
            faltantes = [c for c in disponibles if f"{activo}|{c.isoformat()}" not in estado["velas"]]
            if not faltantes:
                continue
            argumentos = urlencode({
                "symbol": activo, "interval": "5m", "limit": 1000,
                "startTime": int((faltantes[0] - PASO).timestamp() * 1000),
                "endTime": int(faltantes[-1].timestamp() * 1000) - 1,
            })
            try:
                cuerpo, _ = red(f"{API}?{argumentos}")
                recibido = reloj().isoformat()
                esperados = {int((c - PASO).timestamp() * 1000): c for c in faltantes}
                lote = {}
                for vela in json.loads(cuerpo):
                    cierre = esperados.get(vela[0])
                    if cierre is None or vela[6] != int(cierre.timestamp() * 1000) - 1:
                        continue
                    clave = f"{activo}|{cierre.isoformat()}"
                    if clave in lote:
                        raise ValueError("Vela duplicada en la respuesta")
                    lote[clave] = {
                        "activo": activo, "cierre_utc": cierre.isoformat(),
                        "ohlcv": validar(vela[1:6]), "recibido_utc": recibido,
                        "programado_utc": programada.isoformat(), "inicio_ejecucion_utc": entrada.isoformat(),
                        "recuperada": programada >= cierre + PASO,
                    }
                lote_privado = carpeta / "privado" / f"{activo}.json"
                pendiente = leer(lote_privado, {"inicio": inicio.isoformat(), "velas": {}})
                verificar_inicio(pendiente, inicio)
                lote.update({k: v for k, v in pendiente["velas"].items()
                             if k not in estado["velas"]})
                if lote:
                    guardar(lote_privado, {"inicio": inicio.isoformat(), "velas": lote})
                    persistido = reloj().isoformat()
                    for valor in lote.values():
                        valor["persistido_utc"] = persistido
                    publicado = {"inicio": estado["inicio"], "velas": estado["velas"] | lote}
                    guardar(ruta, publicado, modo=0o644)
                    estado = publicado
                print(json.dumps({"activo": activo, "programado_utc": programada.isoformat(),
                                  "inicio_ejecucion_utc": entrada.isoformat(), "recibido_utc": recibido,
                                  "publicacion_confirmada_utc": reloj().isoformat(), "filas": len(lote)}))
            except (OSError, ValueError, TypeError, IndexError, KeyError) as error:
                print(json.dumps({"activo": activo, "error": type(error).__name__,
                                  "programado_utc": programada.isoformat(), "utc": reloj().isoformat()}))
        if any(f"{activo}|{c.isoformat()}" not in estado["velas"]
               for c in disponibles for activo in ACTIVOS):
            raise RuntimeError("Quedan velas pendientes; el siguiente ciclo reintentará")


def claves_control(cuerpo):
    claves = set()
    lector = csv.DictReader(io.StringIO(cuerpo.decode("utf-8-sig")), strict=True)
    if not {"activo", "hora", "apertura", "maximo", "minimo", "cierre", "volumen"}.issubset(lector.fieldnames or ()):
        raise ValueError("Cabecera del control incompleta")
    for fila in lector:
        if fila["activo"] not in ACTIVOS:
            continue
        apertura = instante(fila["hora"])
        if apertura != ultimo_cierre(apertura):
            raise ValueError("Apertura fuera de cuadrícula")
        validar(fila[k] for k in ("apertura", "maximo", "minimo", "cierre", "volumen"))
        clave = (fila["activo"], apertura + PASO)
        if clave in claves:
            raise ValueError("Duplicado en el control")
        claves.add(clave)
    return claves


def claves_piloto(cuerpo, inicio):
    datos = json.loads(cuerpo)
    verificar_inicio(datos, inicio)
    claves = set()
    for clave, fila in datos["velas"].items():
        cierre = instante(fila["cierre_utc"])
        activo = fila["activo"]
        if (activo not in ACTIVOS or clave != f"{activo}|{cierre.isoformat()}"
                or cierre != ultimo_cierre(cierre) or not inicio < cierre <= inicio + VENTANA
                or not cierre <= instante(fila["recibido_utc"]) <= instante(fila["persistido_utc"])):
            raise ValueError("Metadatos inválidos en el piloto")
        validar(fila["ohlcv"])
        claves.add((activo, cierre))
    return claves


def observar(inicio, carpeta, destino, reloj=ahora, red=descargar):
    if reloj() < inicio:
        return
    carpeta.mkdir(parents=True, exist_ok=True)
    with (carpeta / "observador.lock").open("a") as bloqueo:
        fcntl.flock(bloqueo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ciclo = reloj()
        for nombre, url, interpretar in (
            ("control", CONTROL, claves_control),
            ("piloto", destino, lambda cuerpo: claves_piloto(cuerpo, inicio)),
        ):
            registro = {"inicio": inicio.isoformat(), "ciclo_utc": ciclo.isoformat(),
                        "fuente": nombre}
            try:
                cuerpo, version = red(url)
                visto = reloj()
                claves = interpretar(cuerpo)
                presentes = {}
                for activo, cierre in sorted(claves):
                    if inicio < cierre <= min(visto, inicio + VENTANA):
                        presentes.setdefault(cierre.isoformat(), []).append(activo)
                comunes = set.intersection(*({c for a, c in claves if a == activo and c <= visto}
                                             for activo in ACTIVOS))
                ultimo = max(comunes, default=None)
                registro.update(estado="ok", version=version, presentes=presentes,
                                observado_utc=visto.isoformat(),
                                ultimo_conjunto_utc=ultimo.isoformat() if ultimo else None,
                                antiguedad_s=(visto - ultimo).total_seconds() if ultimo else None)
            except (OSError, ValueError, TypeError, KeyError, csv.Error) as error:
                registro.update(estado="error", error=type(error).__name__,
                                observado_utc=reloj().isoformat())
            guardar(carpeta / nombre / f"{ciclo.strftime('%Y%m%dT%H%M%S.%fZ')}.json", registro)


def resumen(inicio, carpeta):
    for fuente in ("control", "piloto"):
        primera = {}
        ausencias = set()
        for ruta in sorted((carpeta / fuente).glob("*.json")):
            r = json.loads(ruta.read_text())
            verificar_inicio(r, inicio)
            if r["estado"] != "ok":
                continue
            observado = instante(r["observado_utc"])
            for cierre in cierres(inicio, observado - PASO):
                texto = cierre.isoformat()
                if ("conjunto", texto) not in primera and set(r["presentes"].get(texto, ())) != set(ACTIVOS):
                    ausencias.add(texto)
            for cierre, activos in r["presentes"].items():
                for activo in activos:
                    primera.setdefault((activo, cierre), r["observado_utc"])
                if set(activos) == set(ACTIVOS):
                    primera.setdefault(("conjunto", cierre), r["observado_utc"])
        for cierre in cierres(inicio, inicio + VENTANA):
            texto = cierre.isoformat()
            marcas = {a: primera.get((a, texto)) for a in (*ACTIVOS, "conjunto")}
            visto = marcas["conjunto"]
            latencia = (instante(visto) - cierre).total_seconds() if visto else None
            puntual = latencia is not None and 0 <= latencia <= 300
            clasificacion = "puntual" if puntual else "indeterminado"
            if not puntual and texto in ausencias:
                clasificacion = "tardio" if visto else "ausente"
            print(json.dumps({"fuente": fuente, "cierre_utc": texto,
                              "primera_observacion_utc": marcas, "latencia_s": latencia,
                              "clasificacion": clasificacion, "puntual_comprobado": puntual}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modo", choices=("recoger", "observar", "resumen"))
    parser.add_argument("--inicio-utc", required=True, type=instante)
    parser.add_argument("--carpeta", required=True, type=Path)
    parser.add_argument("--programado-utc", type=instante)
    parser.add_argument("--url-piloto")
    args = parser.parse_args()
    if args.inicio_utc != ultimo_cierre(args.inicio_utc):
        parser.error("El inicio debe coincidir con un cierre de cinco minutos")
    if args.modo == "resumen":
        resumen(args.inicio_utc, args.carpeta)
        return
    if not args.inicio_utc <= ahora() <= args.inicio_utc + VENTANA + timedelta(minutes=5):
        parser.error("Fuera de la ventana de 48 horas y su margen final de 5 minutos")
    if args.modo == "recoger":
        if args.programado_utc is None or not args.inicio_utc <= args.programado_utc <= ahora():
            parser.error("Se requiere una hora programada UTC válida")
        recoger(args.inicio_utc, args.programado_utc, args.carpeta)
    else:
        if not args.url_piloto:
            parser.error("Se requiere la URL de lectura del piloto")
        observar(args.inicio_utc, args.carpeta, args.url_piloto)


if __name__ == "__main__":
    main()
