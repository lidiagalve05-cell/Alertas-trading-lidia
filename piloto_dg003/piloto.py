"""Herramientas del piloto de datos públicos DG-003."""
import argparse
import csv
import fcntl
import io
import json
import os
import tempfile
from collections import Counter
from math import ceil
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

ACTIVOS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
MINUTO = timedelta(minutes=1)
PASO = timedelta(minutes=5)
MARGEN_FINAL = PASO + timedelta(seconds=30)
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


class Duplicados(ValueError):
    def __init__(self, cantidad):
        super().__init__("Claves duplicadas")
        self.cantidad = cantidad


class CacheHTTP(ValueError):
    pass


def descargar(url):
    if urlsplit(url).scheme != "https":
        raise ValueError("El origen de lectura debe usar HTTPS")
    peticion = Request(url, headers={"Cache-Control": "no-cache, no-store, max-age=0", "User-Agent": "DG-003"})
    with urlopen(peticion, timeout=8) as respuesta:
        if urlsplit(respuesta.geturl()).scheme != "https":
            raise ValueError("Redirección a un origen sin HTTPS")
        edad = respuesta.headers.get("Age")
        aviso = respuesta.headers.get("Warning", "")
        if (edad is not None and (not edad.isdecimal() or int(edad) > 0)
                or any(x in aviso for x in ("110 ", "111 "))
                or "STALE" in respuesta.headers.get("X-Cache", "").upper()):
            raise CacheHTTP("Respuesta con indicios de caché")
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


def publicar_pendientes(ruta, privado, estado, reloj):
    pendiente = leer(privado, {"inicio": estado["inicio"], "velas": {}})
    verificar_inicio(pendiente, instante(estado["inicio"]))
    lote = {k: v for k, v in pendiente["velas"].items() if k not in estado["velas"]}
    if not lote:
        return estado
    ahora_utc = reloj()
    for valor in lote.values():
        recibido = instante(valor["recibido_utc"])
        if recibido > ahora_utc:
            raise ValueError("Reloj atrasado respecto a la recepción guardada")
        if "persistido_utc" not in valor:
            valor["persistido_utc"] = ahora_utc.isoformat()
            valor["persistencia_verificada_al_reanudar"] = True
    guardar(privado, {"inicio": estado["inicio"], "velas": lote})
    publicado = {"inicio": estado["inicio"], "velas": estado["velas"] | lote}
    guardar(ruta, publicado, modo=0o644)
    return publicado


def recoger(inicio, programada, carpeta, reloj=ahora, red=descargar):
    entrada = reloj()
    if programada > entrada:
        raise ValueError("Disparo futuro respecto al reloj local")
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
            privado = carpeta / "privado" / f"{activo}.json"
            try:
                estado = publicar_pendientes(ruta, privado, estado, reloj)
            except (OSError, ValueError) as error:
                print(json.dumps({"activo": activo, "fase": "publicacion_pendiente",
                                  "error": type(error).__name__, "utc": reloj().isoformat()}))
                continue
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
                recibido = reloj()
                if recibido < entrada:
                    raise ValueError("El reloj retrocedió durante la descarga")
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
                        "ohlcv": validar(vela[1:6]), "recibido_utc": recibido.isoformat(),
                        "programado_utc": programada.isoformat(), "inicio_ejecucion_utc": entrada.isoformat(),
                        "recuperada": programada >= cierre + PASO,
                        "ejecucion_programada_posterior": programada >= cierre + PASO,
                        "recepcion_fuera_de_plazo": recibido > cierre + PASO,
                    }
                if lote:
                    guardar(privado, {"inicio": inicio.isoformat(), "velas": lote})
                    persistido = reloj()
                    if persistido < recibido:
                        raise ValueError("El reloj retrocedió durante la persistencia")
                    for valor in lote.values():
                        valor["persistido_utc"] = persistido.isoformat()
                    guardar(privado, {"inicio": inicio.isoformat(), "velas": lote})
                    estado = publicar_pendientes(ruta, privado, estado, reloj)
                print(json.dumps({"activo": activo, "programado_utc": programada.isoformat(),
                                  "inicio_ejecucion_utc": entrada.isoformat(), "recibido_utc": recibido.isoformat(),
                                  "publicacion_confirmada_utc": reloj().isoformat(), "filas": len(lote)}))
            except (OSError, ValueError, TypeError, IndexError, KeyError) as error:
                print(json.dumps({"activo": activo, "error": type(error).__name__,
                                  "programado_utc": programada.isoformat(), "utc": reloj().isoformat()}))
        if any(f"{activo}|{c.isoformat()}" not in estado["velas"]
               for c in disponibles for activo in ACTIVOS):
            raise RuntimeError("Quedan velas pendientes; el siguiente ciclo reintentará")


def claves_control(cuerpo):
    claves = set()
    duplicados = 0
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
            duplicados += 1
        claves.add(clave)
    if duplicados:
        raise Duplicados(duplicados)
    return claves


def claves_piloto(cuerpo, inicio, observado=None):
    def sin_duplicados(pares):
        objeto = dict(pares)
        if len(objeto) != len(pares):
            raise Duplicados(len(pares) - len(objeto))
        return objeto

    datos = json.loads(cuerpo, object_pairs_hook=sin_duplicados)
    verificar_inicio(datos, inicio)
    claves = set()
    for clave, fila in datos["velas"].items():
        cierre = instante(fila["cierre_utc"])
        activo = fila["activo"]
        if (activo not in ACTIVOS or clave != f"{activo}|{cierre.isoformat()}"
                or cierre != ultimo_cierre(cierre) or not inicio < cierre <= inicio + VENTANA
                or not cierre <= instante(fila["recibido_utc"]) <= instante(fila["persistido_utc"])):
            raise ValueError("Metadatos inválidos en el piloto")
        if observado is not None and instante(fila["persistido_utc"]) > observado:
            raise ValueError("Persistencia futura respecto al reloj del observador")
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
        for nombre, url in (
            ("control", CONTROL),
            ("piloto", destino),
        ):
            registro = {"inicio": inicio.isoformat(), "ciclo_utc": ciclo.isoformat(),
                        "fuente": nombre}
            try:
                cuerpo, version = red(url)
                visto = reloj()
                if visto < ciclo:
                    raise ValueError("El reloj del observador retrocedió")
                datos = json.loads(cuerpo)["velas"] if nombre == "piloto" else {}
                claves = (claves_control(cuerpo) if nombre == "control"
                          else claves_piloto(cuerpo, inicio, visto))
                marcas = {
                    clave: {campo: fila[campo] for campo in
                            ("programado_utc", "recibido_utc", "persistido_utc")
                            if campo in fila}
                    for clave, fila in datos.items()
                }
                if any(cierre > visto for _, cierre in claves):
                    raise ValueError("Cierre futuro respecto al reloj del observador")
                presentes = {}
                for activo, cierre in sorted(claves):
                    if inicio < cierre <= min(visto, inicio + VENTANA):
                        presentes.setdefault(cierre.isoformat(), []).append(activo)
                comunes = set.intersection(*({c for a, c in claves if a == activo and c <= visto}
                                             for activo in ACTIVOS))
                ultimo = max(comunes, default=None)
                registro.update(estado="ok", version=version, presentes=presentes, marcas=marcas,
                                observado_utc=visto.isoformat(),
                                ultimo_conjunto_utc=ultimo.isoformat() if ultimo else None,
                                antiguedad_s=(visto - ultimo).total_seconds() if ultimo else None)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, csv.Error) as error:
                registro.update(estado="error", error=type(error).__name__,
                                duplicados=getattr(error, "cantidad", 0),
                                observado_utc=reloj().isoformat())
            guardar(carpeta / nombre / f"{ciclo.strftime('%Y%m%dT%H%M%S.%fZ')}.json", registro)


def evidencia(inicio, carpeta, fuente):
    limite = inicio + VENTANA + MARGEN_FINAL
    registros = []
    for ruta in (carpeta / fuente).glob("*.json"):
        registro = json.loads(ruta.read_text())
        verificar_inicio(registro, inicio)
        ciclo = instante(registro["ciclo_utc"])
        visto = instante(registro["observado_utc"])
        if inicio <= ciclo <= visto <= limite:
            registros.append(registro)
    return sorted(registros, key=lambda r: instante(r["observado_utc"]))


def detalle(inicio, registros, fuente):
    primera, marcas, ausencias = {}, {}, set()
    esperados = list(cierres(inicio, inicio + VENTANA))
    for r in registros:
        if r["estado"] != "ok":
            continue
        observado = instante(r["observado_utc"])
        for cierre in esperados:
            texto = cierre.isoformat()
            if cierre + PASO > observado:
                break
            if ("conjunto", texto) not in primera and set(r["presentes"].get(texto, ())) != set(ACTIVOS):
                ausencias.add(texto)
        for cierre, activos in r["presentes"].items():
            for activo in activos:
                primera.setdefault((activo, cierre), r["observado_utc"])
                marcas.setdefault((activo, cierre), r.get("marcas", {}).get(f"{activo}|{cierre}", {}))
            if set(activos) == set(ACTIVOS):
                primera.setdefault(("conjunto", cierre), r["observado_utc"])
    for cierre in esperados:
        texto = cierre.isoformat()
        vistos = {a: primera.get((a, texto)) for a in (*ACTIVOS, "conjunto")}
        latencias = {a: (instante(v) - cierre).total_seconds() if v else None for a, v in vistos.items()}
        latencia = latencias["conjunto"]
        puntual = latencia is not None and 0 <= latencia <= 300
        clasificacion = "puntual" if puntual else "desconocido"
        if not puntual and texto in ausencias:
            clasificacion = "tardio" if vistos["conjunto"] else "ausente"
        etapas = {}
        for activo in ACTIVOS:
            registro = marcas.get((activo, texto), {})
            etapas[activo] = registro | {
                "ejecucion_programada_posterior": (
                    instante(registro["programado_utc"]) >= cierre + PASO if "programado_utc" in registro else None),
                "recepcion_fuera_de_plazo": (
                    instante(registro["recibido_utc"]) > cierre + PASO if "recibido_utc" in registro else None),
                "primera_observacion_fuera_de_plazo": (
                    latencias[activo] > 300 if latencias[activo] is not None else None),
            }
        yield {"fuente": fuente, "cierre_utc": texto, "primera_observacion_utc": vistos,
               "latencia_s": latencia, "latencias_por_activo_s": latencias, "etapas": etapas,
               "clasificacion": clasificacion, "puntual_comprobado": puntual}


def resumen(inicio, carpeta):
    for fuente in ("control", "piloto"):
        for fila in detalle(inicio, evidencia(inicio, carpeta, fuente), fuente):
            print(json.dumps(fila))


def percentiles(muestras):
    orden = sorted(muestras)
    return {f"p{p}": orden[ceil(len(orden) * p / 100) - 1] if orden else None
            for p in (50, 95, 99)}


def medir(inicio, registros, fuente):
    filas = list(detalle(inicio, registros, fuente))
    conteo = Counter(f["clasificacion"] for f in filas)
    minutos = int(VENTANA / MINUTO)
    estados = {}
    errores = Counter()
    duplicados = 0
    invalidos = 0
    validos = []
    for r in registros:
        en = instante(r["observado_utc"])
        minuto = int((en - inicio) // MINUTO)
        if r["estado"] == "ok":
            validos.append(r)
            edad = r["antiguedad_s"]
            estados[minuto] = "fresco" if edad is not None and edad <= 600 else "atrasado"
        else:
            errores[r["error"]] += 1
            duplicados += r.get("duplicados", 0)
            invalidos += r["error"] in ("ValueError", "TypeError", "KeyError", "AttributeError", "Error", "Duplicados")
            estados.setdefault(minuto, "desconocido")
    frescura = Counter()
    incidentes = []
    for minuto in range(minutos):
        estado = estados.get(minuto, "desconocido")
        frescura[estado] += 1
        if estado == "fresco":
            continue
        desde = (inicio + minuto * MINUTO).isoformat()
        hasta = (inicio + (minuto + 1) * MINUTO).isoformat()
        if incidentes and incidentes[-1]["estado"] == estado and incidentes[-1]["hasta_utc"] == desde:
            incidentes[-1]["hasta_utc"] = hasta
            incidentes[-1]["minutos"] += 1
        else:
            incidentes.append({"estado": estado, "desde_utc": desde, "hasta_utc": hasta, "minutos": 1})

    pct = {a: percentiles(f["latencias_por_activo_s"][a] for f in filas
                          if f["latencias_por_activo_s"][a] is not None)
           for a in (*ACTIVOS, "conjunto")}
    cortes = []
    for hora in (24, 48):
        corte = inicio + timedelta(hours=hora)
        candidatos = [r for r in validos if corte + PASO <= instante(r["observado_utc"])
                      <= corte + MARGEN_FINAL]
        presentes = candidatos[-1]["presentes"] if candidatos else None
        faltantes = (sum(activo not in presentes.get(c.isoformat(), ())
                         for c in cierres(inicio, corte) for activo in ACTIVOS)
                     if presentes is not None else None)
        cortes.append({"corte_utc": corte.isoformat(), "velas_faltantes": faltantes})
    clasificados = minutos - frescura["desconocido"]
    cobertura = clasificados / minutos
    proporcion_fresca = frescura["fresco"] / clasificados if clasificados else None
    pendiente = any(c["velas_faltantes"] is None for c in cortes)
    max_incidente = max((i["minutos"] for i in incidentes if i["estado"] == "atrasado"), default=0)
    cumple = (conteo["puntual"] * 100 >= 99 * len(filas) and pct["conjunto"]["p95"] is not None
              and pct["conjunto"]["p95"] <= 180 and proporcion_fresca is not None
              and proporcion_fresca >= .995 and max_incidente <= 30
              and not duplicados and not invalidos and all(c["velas_faltantes"] == 0 for c in cortes))
    return {"fuente": fuente, "cierres_esperados": len(filas),
            "puntualidad": {k: conteo[k] for k in ("puntual", "tardio", "ausente", "desconocido")},
            "fraccion_puntual": conteo["puntual"] / len(filas), "latencia_s": pct,
            "minutos_esperados": minutos, "cobertura": cobertura,
            "frescura_minutos": {k: frescura[k] for k in ("fresco", "atrasado", "desconocido")},
            "fraccion_fresca_entre_clasificados": proporcion_fresca,
            "incidentes": incidentes, "integridad": cortes, "errores_de_observacion": dict(errores),
            "duplicados_en_lecturas": duplicados, "lecturas_invalidas": invalidos,
            "resultado": "inconcluso" if cobertura < .995 or pendiente else "cumple" if cumple else "falla"}


def informe(inicio, carpeta):
    return {"inicio_utc": inicio.isoformat(), "fin_utc": (inicio + VENTANA).isoformat(),
            "fuentes": {f: medir(inicio, evidencia(inicio, carpeta, f), f) for f in ("control", "piloto")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modo", choices=("recoger", "observar", "resumen", "informe"))
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
    if args.modo == "informe":
        print(json.dumps(informe(args.inicio_utc, args.carpeta), indent=2))
        return
    if not args.inicio_utc <= ahora() <= args.inicio_utc + VENTANA + MARGEN_FINAL:
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
