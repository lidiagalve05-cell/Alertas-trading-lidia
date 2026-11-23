# Bot 2 v0.3 — DG-013 / RA-010B

**PR en borrador. Núcleo aislado; NO APTO PARA DESPLIEGUE.**

## Reproducción desde instalación limpia

Requisitos: Python 3.10+ con `sqlite3` incluido. No requiere paquetes de terceros, claves, servicios ni conexión a Internet.

```bash
git clone https://github.com/lidiagalve05-cell/Alertas-trading-lidia.git
cd Alertas-trading-lidia
git switch codex/bot2-v0.3-ra010b
cd bot2_v03
python -m unittest -v test_bot2
```

Los tests usan directorios temporales, datos sintéticos y reloj UTC fijo. No crean automatizaciones, no contactan a Telegram ni escriben en GitHub.

**Evidencia:** las diez pruebas del paquete local anterior pasaron en el entorno de desarrollo. La nueva versión de los archivos incorporados al PR requiere ejecución independiente desde clon limpio y conservación de logs; no se afirma que GitHub Actions haya ejecutado estas pruebas.

## Trazabilidad RA-010B

| ID | Exigencia | Estado |
|---|---|---|
| B01 | Normal <=7m, WARNING >7–10m, HIGH >10–20m, CRITICAL >20m | Implementación y pruebas de límites sintéticos |
| B02 | Heartbeat: latidos esperados, silencio y fallo de supervisor/publicador | Función aislada; **supervisor externo real pendiente** |
| B03 | Dos verificaciones correctas separadas >=5m | Máquina de estados y prueba con reinicio |
| B04 | ESCALATE solo al aumentar severidad; DEESCALATE al disminuir | Implementación y prueba |
| B05 | Clave por activo/tipo/origen y episodio | Implementación y prueba |
| B06 | SQLite transaccional, outbox duradera, reintentos y recuperación | **Parcial**: estados ACK/RETRY/UNKNOWN; faltan dispatcher, backoff y resolución de ACK incierto |

## Riesgos y limitaciones conocidos

- No hay supervisor externo desplegado; si el monitor se detiene por completo, la función local no puede detectar su propia caída.
- `UNKNOWN` es una entrega potencialmente efectuada sin acuse: **no se reenvía automáticamente**, para evitar duplicados. Requiere conciliación con el proveedor o intervención auditada.
- `pending(now)` aplica vencimiento de reintento de 60 s; no hay dispatcher, límites de tasa, backoff exponencial ni circuito de fallo.
- Falta prueba de concurrencia entre procesos y caída durante escritura SQLite.
- `inspect_external` es un validador de respuestas simuladas; no descarga ni comprueba externamente la versión publicada.
- La recuperación exige dos observaciones correctas separadas cinco minutos, pero aún falta demostrar gestión de reloj regresivo y caídas durante recuperación.
- Los tests son locales y sintéticos; no acreditan disponibilidad externa, envío real de Telegram ni publicación en GitHub.
- La integridad del CSV se evalúa sobre el contenido recibido; no acredita la puntualidad real de publicación.

**Prohibiciones:** no fusionar, desplegar, activar automatizaciones, modificar recopilador principal, incluir credenciales reales ni operar con dinero real.

**Siguiente paso:** ejecución desde clon limpio, pruebas de concurrencia y fallos combinados, implementación de supervisor externo simulado y política de reintentos; revisión preliminar DG antes de solicitar RA.


## DG-014: verificación independiente (pendiente)

Se añadieron siete pruebas de regresión en `test_dg014.py` y correcciones de rechazo de reloj regresivo, vela futura y reenvío ambiguo. **Estas siete pruebas NO han sido ejecutadas desde clon limpio**. La ejecución local previa de diez casos corresponde a otro paquete local anterior y no valida el HEAD actual del PR.

Ejecutar desde clon limpio (sin conexión a servicios durante las pruebas):

```bash
git clone https://github.com/lidiagalve05-cell/Alertas-trading-lidia.git
cd Alertas-trading-lidia
git checkout codex/bot2-v0.3-ra010b
cd bot2_v03
python -m unittest discover -s . -p 'test*.py' -v 2>&1 | tee dg014-unittest.log
```

**Criterio:** todas las pruebas deben pasar, y guardar SHA del commit, versión Python, log íntegro y código de salida (con `set -o pipefail` en Bash). La prueba concurrente puede revelar una carrera SQLite; no se presume éxito. Aún falta prueba de terminación forzada entre transacciones, caída del supervisor y fallos simultáneos de ambos canales.

### Supervisor externo: propuesta verificable, no desplegada

Un segundo proceso independiente y con almacenamiento separado observará latidos emitidos por el vigilante, con secuencia monotónica y timestamp UTC, verificará al menos dos latidos ausentes y duración de silencio; identificará por separado fallo de productor, fallo de publicación y fallo del propio supervisor mediante un tercer observador o servicio de comprobación. Simular los tres fallos con relojes controlados antes de aprobar. No activar ninguna programación.

### Matriz de aceptación pendiente

- B01: umbrales implementados; validar límites y timestamp futuro sobre HEAD.
- B02: función simulada; supervisor externo y sus fallos **pendientes**.
- B03: doble observación; probar reloj regresivo y reinicio **pendiente de ejecución**.
- B04: eventos de subida/bajada; prueba de regresión **pendiente de ejecución**.
- B05: claves por origen/episodio; concurrencia entre procesos **pendiente de ejecución**.
- B06: SQLite y outbox; entrega ambigua requiere conciliación manual, falta dispatcher/backoff, caída durante escritura y confirmación de entrega **pendientes**.

## DG-031 / RA-030 — correcciones H01 y H02 (pendientes de ejecución independiente)

- **H01:** una comprobación sin la clave evaluada mientras la incidencia está en RECOVERING cancela la secuencia (OPEN, good_since=NULL). Una observación fallida también la cancela. Solo una nueva pareja de observaciones correctas separadas >=5 minutos puede cerrar el episodio. Una observación ausente no equivale a salud.
- **H02:** UNKNOWN se retiene, no se reintenta automáticamente y tampoco puede convertirse en ACKED mediante `delivery()`. `reconcile_unknown(event, "CONFIRMED_DELIVERED", now, evidence)` requiere evidencia manual no vacía y registra un evento auditable; `KEEP_UNKNOWN` conserva la retención. **No existe reconciliación automática con Telegram ni autorización para reenvío manual de un UNKNOWN.** Los justificantes son texto proporcionado por el operador, no prueba criptográfica de entrega.
- **Riesgos medios pendientes:** supervisor externo independiente, dispatcher/backoff, verificación real de publicación y fallos simultáneos; sin despliegue.
- **Reproducibilidad:** fijar SHA del commit, `set -o pipefail`, `python -m unittest discover -s bot2_v03 -p 'test*.py' -v 2>&1 | tee ra030.log`; conservar `PIPESTATUS[0]`, Python y log completo. La ejecución local o independiente del nuevo HEAD **aún no está acreditada**.
