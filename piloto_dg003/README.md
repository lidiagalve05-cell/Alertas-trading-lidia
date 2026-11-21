# DG-003 · Piloto aislado de 48 horas

El piloto compara la disponibilidad de velas públicas de BTCUSDT, ETHUSDT y SOLUSDT en el CSV de `main` y un destino experimental. La ventana empieza únicamente con autorización de activación y un inicio efectivo registrado en UTC, alineado a cinco minutos. La muestra incluye 576 cierres conjuntos en el intervalo abierto a la izquierda y cerrado a la derecha. Se conceden cinco minutos para observar el último cierre y 30 segundos para que termine ese sondeo. El plazo de puntualidad sigue siendo de 300 segundos.

## Componentes e infraestructura

- **Recopilador.** Python 3.11+ en Linux, reloj UTC sincronizado, volumen persistente local y HTTPS saliente al endpoint público de Binance. Usar un planificador que invoque el comando al cierre más 30 segundos. Si entrega la hora programada original, registrarla con `--programado-utc`. Cada ciclo hace como máximo una consulta por activo con pendientes y recupera huecos en los ciclos siguientes. El bloqueo local y la clave activo/cierre evitan duplicados dentro de un único volumen. Usar un solo escritor.
- **Destino.** Servir exclusivamente `publico/velas.json` vía HTTPS con revalidación de caché. Permitir al servidor de lectura acceder al archivo público, con un usuario distinto y sin escritura. Mantener los archivos de trabajo y los logs fuera de la raíz pública. Si se usa un gateway u objeto remoto, hace falta verificar su visibilidad y sus políticas de caché en la prueba previa.
- **Observador.** Instancia separada con Python 3.11+ en Linux, reloj UTC sincronizado, HTTPS saliente, temporizador cada minuto y almacenamiento propio. Lee el CSV de GitHub sin permisos de escritura y el JSON del destino candidato. Guarda una evidencia por fuente y ciclo, incluso si falla la lectura. Conservar logs, ETags y registros de invocaciones.
- **Compatibilidad.** El código usa almacenamiento POSIX persistente y bloqueo local. Un entorno de funciones efímeras con almacenamiento de objetos requiere un adaptador para escritura condicional y un escritor único. La implementación actual encaja directamente en un host Linux persistente con un servidor HTTPS de solo lectura.

## Configuración preparada para revisión

Los archivos en `config/` son plantillas sin instalar. Los temporizadores systemd se sitúan en dos hosts distintos. El del recopilador llama a los 30 segundos de cada cierre y el observador consulta al comienzo de cada minuto. Las unidades esperan un checkout en `/opt/dg003` y usuarios de servicio separados. Antes de cualquier activación, verificar NTP en ambos hosts, crear los usuarios y confirmar que `/var/lib/dg003-recoger` y `/var/lib/dg003-observar` están en volúmenes persistentes con respaldo y espacio para la evidencia. Los archivos de entorno contienen `INICIO_DG003` y, en el observador, `URL_DG003`. El inicio debe provenir del registro de activación autorizado.

La plantilla Nginx requiere sustituir el dominio y las rutas del certificado. Sirve solo el JSON del piloto por HTTPS. El usuario web tendrá lectura del archivo publicado y acceso de recorrido al directorio público. Proteger `privado/` y los logs con permisos separados. Verificar desde el segundo host la respuesta, su política de caché y el rechazo a escrituras antes de comenzar la ventana.

Las unidades comprueban que el reloj esté sincronizado antes de cada proceso. Una ejecución omitida por falta de sincronización queda sin evidencia. La plantilla systemd registra el inicio efectivo y deja la hora nominal como desconocida, porque el servicio carece de una marca inequívoca del disparo original. Para atribuir retrasos al disparo, usar un planificador que proporcione esa marca por invocación. Los temporizadores no recuperan invocaciones al reiniciar. El recopilador reconcilia las velas pendientes en la siguiente ejecución y conserva las recepciones registradas antes de un fallo de publicación.

## Comandos para una futura activación

Desde la raíz del checkout, sustituir las variables por los valores registrados para la ventana autorizada. Programar también el apagado al término del margen final. Antes de iniciar la ventana, verificar en una prueba de humo separada la sincronización de relojes, acceso HTTPS, permisos, respuesta de mercado y persistencia. Usar directorios distintos para cada ensayo.

```sh
python -m piloto_dg003.piloto recoger --inicio-utc "$INICIO_DG003" \
  --carpeta "$DATOS_DG003"
python -m piloto_dg003.piloto observar --inicio-utc "$INICIO_DG003" \
  --url-piloto "$URL_DG003" --carpeta "$EVIDENCIA_DG003"
python -m piloto_dg003.piloto resumen --inicio-utc "$INICIO_DG003" \
  --carpeta "$EVIDENCIA_DG003" > resumen-dg003.jsonl
python -m piloto_dg003.piloto informe --inicio-utc "$INICIO_DG003" \
  --carpeta "$EVIDENCIA_DG003" > informe-dg003.json
```

El cierre corresponde al intervalo nominal. La recepción se mide tras completar la lectura de la respuesta. La persistencia se registra tras sincronizar el lote privado en disco. La confirmación local del archivo público aparece por separado en logs. Solo el observador mide la primera disponibilidad externa por activo y conjunta. Un fallo de lectura se conserva como evidencia, y una primera lectura tardía sin constancia de ausencia a partir del plazo se clasifica como desconocida. El detalle conserva tres indicadores independientes por activo: ejecución programada a partir del siguiente cierre, recepción efectiva después del plazo y primera observación externa después del plazo. Una recepción puntual puede publicarse tarde. Si el proceso cayó entre las dos escrituras privadas, se marca la hora de confirmación al reanudar como cota de persistencia, conservando la recepción original.

## Validación y costes

Evaluar 576 cierres por destino. Aceptar con **571 o más cierres puntuales** dentro de 300 segundos, p95 conjunto ≤180 segundos, frescura ≤600 segundos en al menos el 99,5 % de las observaciones válidas y ningún incidente continuo superior a 30 minutos. Revisar a las 24 horas y al cierre que haya cero duplicados, datos inválidos o cierres pendientes de reconciliación. Exigir al menos 99,5 % de cobertura del observador sobre los minutos esperados. Una evidencia insuficiente deja la validación inconclusa. Un cierre tardío recuperado cuenta para integridad y queda fuera del numerador de puntualidad.

El informe JSON calcula los criterios por fuente, incluidos p50/p95/p99 por rango más próximo sobre primeras observaciones disponibles, integridad en los sondeos de las 24 h 05 min y las 48 h 05 min, duplicados detectados por lectura y los incidentes agrupados por minuto. Cada lectura incorpora un identificador de consulta único y pide revalidación. Una respuesta con errores o indicios de caché da evidencia desconocida. Verificar que ambos destinos aceptan parámetros de consulta y comprobar desde el observador que una nueva escritura se vuelva visible. Estos controles no garantizan que un intermediario respete la política de caché. Los minutos sin respuesta válida no cuentan como frescos. La cobertura usa los 2.880 minutos de la ventana completa; la fracción fresca usa solo minutos clasificables y se muestra junto con la cobertura y el número de desconocidos. La evaluación queda inconclusa si la cobertura es insuficiente o falta una lectura válida en un punto de conciliación. Revisar el JSONL si hace falta evidencia por cierre. Conservar el cierre, la recepción, la persistencia y la primera observación para separar retrasos del proveedor, planificador, escritura y publicación. Comparar ambos destinos con el mismo calendario. Se esperan 1.728 consultas iniciales de mercado y 2.880 ciclos de observación de dos destinos, más el margen final.

Coste orientativo de un host persistente para el recopilador de **6–20 €/mes**, más **2–10 €/mes** para el observador. Si se factura por tiempo de uso, 48 horas equivalen aproximadamente a **0,55–2 €** entre ambos, antes de impuestos, mínimos, tráfico y trabajo operativo. Verificar el presupuesto con la tarifa efectiva, las transferencias del CSV, el almacenamiento y los logs. La alternativa gestionada se estimó en **2–15 €/mes** para el recopilador, además del observador y del trabajo de adaptación. El objetivo provisional es **≤25 €/mes** para ambos componentes.

## Riesgos y verificación

La caché, el desfase del reloj, las caídas del observador y los límites del endpoint pueden sesgar la medición. Un minuto de sondeo limita la resolución. La recuperación posterior no cambia la primera observación. El servidor HTTPS, los temporizadores, la identidad de ejecución y la visibilidad desde un segundo entorno deben verificarse antes de una ventana real. El programa usa datos públicos y carece de integración con órdenes.

Las pruebas locales inyectan respuestas y relojes simulados para comprobar reinicio, persistencia parcial, fallo de publicación, interrupción prolongada, duplicados, desfase horario, caché, latencias y cobertura desconocida. Ejecutar desde la raíz con `python -m unittest discover -s tests -v`. El workflow de pruebas existente descubre la suite sin cambios.
