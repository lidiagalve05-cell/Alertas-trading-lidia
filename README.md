# Analista y simulador de criptomonedas

La fase 2 lee el histórico de velas cerradas de cinco minutos en `datos-cripto.csv`. Por defecto analiza BTCUSDT, ETHUSDT y SOLUSDT. Los módulos de análisis, simulación e informes usan la biblioteca estándar de Python 3.11 o posterior. El simulador histórico es experimental y opera localmente con capital exclusivamente ficticio y carece de conexión a una bolsa, una cuenta o una API de órdenes.

## Ejecutar

```sh
python -m trading
python -m trading --simular --hasta 2026-11-20T09:35:00+00:00 > informe.md
python -m trading --simular --activo BTCUSDT --activo ETHUSDT --capital 100 \
  --comision 0.001 --deslizamiento 0.001 --fraccion 0.20 --stop 0.03 \
  --limite-drawdown 0.10 --euros-por-usdt 1
python -m trading --simular --hasta 2026-11-20T09:35:00+00:00 \
  --capital 50 --limite-diario 1 --limite-acumulado 5 > informe-50.md
```

`--csv` permite elegir otro archivo con el mismo esquema. Para incorporar otro símbolo cotizado en USDT, incluye sus velas en el CSV y repite `--activo` por cada símbolo deseado. Los porcentajes de configuración se expresan como fracciones, por ejemplo, `0.001` equivale a 0,1 %. El corte incluye las velas que ya cerraron en ese instante. Un cierre queda desactualizado si ya falta la siguiente vela esperada o si supera el margen explícito de `--max-retraso` en minutos (10 por defecto).

## Indicadores y señales

| Indicador o señal | Cálculo o condición |
| --- | --- |
| RSI14 | Suavizado de Wilder. Un tramo completamente plano da 50. |
| Tendencia | SMA9 > SMA21 > SMA50 para alcista. Orden inverso para bajista. Las demás combinaciones son laterales. |
| Extremos | Máximo y mínimo intravela de las 20 velas anteriores. |
| Variación y volumen | Cambio porcentual frente a la vela anterior. El ratio de volumen usa la media de las 20 velas previas. Un denominador cero se muestra como n/d. |
| Volatilidad | Desviación estándar poblacional de los últimos 20 retornos porcentuales de cinco minutos. |
| Posible compra | Ruptura del máximo previo con tendencia alcista y RSI14 entre 50 y 75. |
| Posible venta | Ruptura del mínimo previo con tendencia bajista, o RSI14 de al menos 80. |
| Giro brusco | Cambio de clasificación con una variación absoluta del cierre de al menos 1 %. |
| Volumen anormal | Al menos dos veces la media previa. |
| Riesgo alto | Volatilidad de al menos 0,8 %, movimiento absoluto de al menos 2 %, volumen cero o referencia de volumen ausente. |

Se requieren 50 velas consecutivas con volumen positivo por símbolo para emitir señales. Un hueco o un cierre con volumen cero reinicia el cálculo. Los umbrales son heurísticos y las señales describen condiciones observadas. La presión compradora del recopilador queda disponible en el CSV.

## Reglas de simulación

- Cartera de contado con 100 € ficticios, hasta 20 % del patrimonio por entrada, una posición por activo y límite dado por el efectivo disponible. En cada cierre se procesan primero las salidas y después las compras en orden de símbolo.
- La señal usa solo velas cerradas hasta su hora. La ejecución ordinaria se evalúa al cierre de cinco minutos siguiente, con ese precio más el deslizamiento y la comisión configurados. Las órdenes ordinarias caducan si se pierde ese cierre. Se requieren velas válidas con volumen positivo de todos los activos seleccionados. Las entradas requieren un análisis disponible y se descartan si aparece una señal de riesgo en el cierre de ejecución.
- Durante un hueco, una vela incompleta o un cierre con volumen cero, se pausa la valoración y caducan las órdenes ordinarias cuyo cierre de ejecución se perdió. El informe marca como indeterminados el estado de riesgo al corte, el patrimonio, la rentabilidad y el P/L abierto. Muestra el efectivo, el resultado realizado, las posiciones abiertas, los bloqueos registrados, el número de cierres omitidos y la última valoración válida. Los cierres omitidos se cuentan desde el primer dato observado.
- El stop de posición compara el valor neto estimado con el coste de entrada. `--limite-diario` y `--limite-acumulado` son optativos y se expresan en euros ficticios. Al alcanzar el límite diario se bloquean entradas hasta el siguiente día UTC con valoración válida. Su referencia es el último patrimonio válido antes de ese día, o el capital inicial al arrancar. El límite acumulado mide la caída frente al capital inicial y bloquea compras durante el resto de la reproducción, igual que el límite de drawdown frente al máximo histórico. Los tres límites de cartera ordenan cerrar las posiciones; incluyen costes estimados y P/L abierto.
- Las salidas por riesgo se conservan hasta un cierre conjunto posterior con volumen suficiente, incluso si cambia el día. Un salto tras una interrupción se refleja en el día UTC de recuperación. **Los stops y límites de pérdida no garantizan el precio de salida ni limitan la pérdida final al umbral.** Mientras falten datos o liquidez, la posición sigue expuesta.
- `--importe-minimo` exige 1 € ficticio de nominal de entrada por defecto, después de reservar la comisión. Las salidas pueden cerrar importes menores. `--coste-maximo` rechaza configuraciones cuya fricción estimada de ida y vuelta con precio constante supere el 10 % por defecto. `--participacion-maxima` limita cada ejecución al 1 % del volumen de la vela por defecto. Las órdenes ordinarias sin capacidad caducan y las salidas urgentes se reintentan. El modelo simula ejecuciones completas, carece de libro de órdenes, y el volumen agregado es solo una aproximación de liquidez.
- El informe desglosa por activo entradas, salidas, P/L realizado, comisiones pagadas, euros de deslizamiento, rechazos y posiciones abiertas. Para cada cierre con pérdida separa el movimiento del precio de los costes de ida y vuelta. El patrimonio usa valor neto teórico de liquidación; el informe avisa cuando una posición supera la participación permitida. El drawdown y las pérdidas máximas usan cierres observados y pueden subestimar pérdidas intravela o durante huecos.
- El CSV cotiza en USDT. El cálculo usa un factor fijo configurable con `--euros-por-usdt`. El valor predeterminado de 1 es una equivalencia contable, sin tipo de cambio histórico. Con los mismos datos, corte y parámetros, la reproducción reconstruye la misma cartera.

## Verificación

```sh
python -m pip install requests  # dependencia de las pruebas del recopilador
CSV_BASE="$(mktemp)"
export CSV_BASE
git show main:datos-cripto.csv > "$CSV_BASE"
python -m unittest discover -s tests -v
```

La suite contiene 27 pruebas, seis del recopilador y 21 de la fase 2. Las regresiones comprueban el reinicio tras una vela sin volumen y la activación del stop y del drawdown en el umbral exacto. Se conservan las comprobaciones de indicadores, lectura, duplicados, costes, límites, orden causal e independencia de la red. Los casos nuevos cubren exposición durante una interrupción y su recuperación, un corte atrasado, bloqueo diario y acumulado con 50 € de capital y límites de 1 € y 5 €, importes mínimos, liquidez, y atribución de pérdidas. El flujo existente descubre ambos conjuntos y comprueba la conservación del CSV. El PR #1 se fusionó en el commit `b2213f6`, con 5.532 velas de referencia.

Con el corte del 20 de noviembre de 2026 a las 09:35 UTC, el perfil de 100 € registró 52 ejecuciones, con 93,62 € de patrimonio, −6,38 % de rentabilidad, 6,49 % de drawdown máximo y 12 % de aciertos sobre 25 salidas. Quedaron dos posiciones abiertas.

Con 50 € y límites de 1 € diarios y 5 € acumulados se registraron 27 entradas y 25 salidas, con 46,81 € de patrimonio y −6,38 % de rentabilidad. La pérdida diaria máxima observada fue 0,984 € y la acumulada fue 3,243 €, por debajo de ambos límites. Las pruebas sintéticas sí activaron los dos umbrales exactos y comprobaron el desbloqueo diario y la persistencia del acumulado.

| Activo | Entradas | Salidas | P/L realizado | Comisiones | Deslizamiento |
| --- | ---: | ---: | ---: | ---: | ---: |
| BTCUSDT | 7 | 6 | −0,571 € | 0,125 € | 0,125 € |
| ETHUSDT | 10 | 9 | −1,367 € | 0,184 € | 0,184 € |
| SOLUSDT | 10 | 10 | −1,057 € | 0,193 € | 0,193 € |

Los cuatro límites comparan pérdidas en euros ficticios. Para absorber el redondeo se admite una diferencia de hasta ocho unidades del último lugar (ULP) del importe de referencia, con un tope de `1e-10` veces el umbral. La referencia es el coste de entrada para el stop, el pico de patrimonio para el drawdown, el patrimonio de inicio del día para el límite diario y el capital inicial para el acumulado.

### Riesgos pendientes

| Riesgo | Impacto y limitación |
| --- | --- |
| R01 · Ejecución optimista | El cierre, el volumen agregado y las comprobaciones de riesgo de esa vela están disponibles en el modelo después del cierre. El simulador usa ese mismo precio de cierre como referencia para una ejecución ficticia. Sin cotizaciones posteriores, libro de órdenes ni hora real de recepción de cada vela, el precio ejecutable, la capacidad y los retrasos históricos siguen sin verificarse. Una vela incorporada tarde al CSV puede aparecer en la reproducción a su hora nominal. |
| R02 · Pérdida superior al stop | Los stops y límites se evalúan en cierres conjuntos y la salida puede quedar pendiente por falta de datos o volumen. La recuperación puede ejecutarse a un precio peor que el umbral. Las pruebas comprueban esa exposición y la conservación de la orden urgente, pero no acotan la pérdida. |
| R03 · EUR/USDT fijo | El factor de conversión constante sirve como unidad contable. Puede ocultar el efecto del tipo de cambio sobre el patrimonio, los resultados y los umbrales en euros. |
| R04 · Validación estadística insuficiente | Las pruebas verifican el comportamiento del código y los escenarios de pérdida. El histórico de referencia es breve y carece de evaluación fuera de muestra. No permite inferir rentabilidad ni generalizar el riesgo observado. |

## Ejecución periódica y Telegram

Para operar periódicamente con cartera ficticia falta un flujo separado tras una publicación correcta del recopilador. Ese flujo deberá verificar la hora real y la disponibilidad de todas las velas antes de procesarlas, persistir de forma transaccional la cartera, las órdenes y el último cierre procesado, y serializar ejecuciones concurrentes. Conviene persistir también un identificador por alerta para que un reintento no vuelva a enviarla. El comando histórico vuelve a reproducir el CSV desde el inicio.

El informe ya se entrega por salida estándar y `trading.informe.CanalAlertas` permite conectar un adaptador de mensajería sin acoplarlo a la simulación. Para activar Telegram falta implementar ese adaptador y configurar el token y el destino como secretos del entorno de ejecución, con redacción de errores y mensajes dimensionados para el canal.
