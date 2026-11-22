# Bot 2 v0.3 — DG-012 / RA-010B
**Borrador técnico aislado. No desplegar.**

La implementación completa y los resultados reproducibles están pendientes de incorporación a esta rama. Este documento fija el alcance y la trazabilidad; no acredita las pruebas.

| Corrección | Criterio obligatorio | Evidencia exigida |
|---|---|---|
| B01 | Frescura normal <=7m; WARNING >7–10m; HIGH >10–20m; CRITICAL >20m | Pruebas de límites exactos por activo |
| B02 | Heartbeat con latidos ausentes, duración del silencio y supervisor externo | Fallos separados de vigilante, publicación y supervisor |
| B03 | Dos verificaciones satisfactorias separadas >=5m para resolver | Persistencia y reinicio entre ambas |
| B04 | ESCALATE solo si aumenta severidad; DEESCALATE si disminuye | Pruebas de transiciones |
| B05 | Clave por activo, tipo y origen, con episodio independiente | Pruebas de colisiones y reapertura |
| B06 | SQLite transaccional, outbox persistente y entrega ambigua | Fallos de Telegram, reinicio y reintentos |

**Restricciones:** no conectar Telegram/GitHub reales, no incluir secretos, no modificar el recopilador, no desplegar, no crear automatizaciones activas, no fusionar PR.

**Bloqueos:** pruebas de integración completas, supervisor externo, backoff, política de entrega ambigua, seguridad de credenciales y revisión RA.

**Nota:** los siete casos originales de v0.2 eran sintéticos y no certifican v0.3.