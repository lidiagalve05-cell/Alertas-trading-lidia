# Analista y simulador de criptomonedas

La fase 2 lee el histórico de velas cerradas de cinco minutos en `datos-cripto.csv`. Por defecto analiza BTCUSDT, ETHUSDT y SOLUSDT. Los módulos de análisis, simulación e informes usan la biblioteca estándar de Python 3.11 o posterior. El simulador opera localmente con capital ficticio y carece de conexión a una bolsa, una cuenta o una API de órdenes.

## Ejecutar

```sh
python -m trading
python -m trading --simular --hasta 2026-11-20T09:35:00+00:00 > informe.md
python -m trading --simular --activo BTCUSDT --activo ETHUSDT --capital 100 \
  --comision 0.001 --deslizamiento 0.001 --fraccion 0.20 --stop 0.03 \
  --limite-drawdown 0.10 --euros-por-usdt 1
```

`--csv` permite elegir otro archivo con el mismo esquema. Para incorporar otro símbolo cotizado en USDT, incluye sus velas en el CSV y repite `--activo` por cada símbolo deseado. Los porcentajes de configuración se expresan como fracciones, por ejemplo, `0.001` equivale a 0,1 %. El corte incluye las velas que ya cerraron en ese instante. El informe marca un cierre desactualizado al superar `--max-retraso` minutos, con 10 minutos por defecto.

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

Se requieren 50 velas consecutivas por símbolo para emitir señales. Un hueco reinicia el cálculo. Los umbrales son heurísticos y las señales describen condiciones observadas. La presión compradora del recopilador queda disponible en el CSV.

## Reglas de simulación

- Cartera de contado con 100 € ficticios, hasta 20 % del patrimonio por entrada, una posición por activo y límite dado por el efectivo disponible. En cada cierre se procesan primero las salidas y después las compras en orden de símbolo.
- La señal usa solo velas cerradas hasta su hora. La ejecución ordinaria se evalúa al cierre de cinco minutos siguiente, con ese precio más el deslizamiento y la comisión configurados. Las órdenes ordinarias caducan si se pierde ese cierre. Se requieren velas válidas con volumen positivo de todos los activos seleccionados. Las entradas requieren un análisis disponible y se descartan si aparece una señal de riesgo en el cierre de ejecución.
- El stop evalúa el valor neto de liquidación frente al coste de entrada. Al alcanzar el drawdown máximo permitido, se bloquean las compras durante el resto de la simulación y se ordena liquidar. Las salidas por riesgo esperan el siguiente cierre conjunto válido. Los saltos de precio y la falta de datos pueden hacer que la pérdida supere el umbral.
- El patrimonio valora posiciones abiertas a precio de liquidación neto de costes. Los aciertos son ventas con beneficio entre todas las ventas cerradas. El registro del informe incluye horas de señal y ejecución, unidades, coste, flujo, resultado y motivo. Cada reproducción reconstruye la misma cartera si recibe los mismos datos, corte y parámetros.
- El CSV cotiza en USDT. El cálculo en euros ficticios usa un factor fijo configurable con `--euros-por-usdt`. El valor predeterminado de 1 es una equivalencia contable para la simulación. El modelo no incorpora tipo de cambio histórico, libro de órdenes ni ejecución intravela.

## Verificación

```sh
python -m pip install requests  # dependencia de las pruebas del recopilador
CSV_BASE="$(mktemp)"
export CSV_BASE
git show main:datos-cripto.csv > "$CSV_BASE"
python -m unittest discover -s tests -v
```

La ejecución de referencia pasó 17 pruebas, seis del recopilador y once de la fase 2. Cubren indicadores con valores esperados, lectura del CSV, duplicados, costes, rentabilidad, límites de pérdida, cortes causales, datos atrasados e incompletos, e independencia de la red y del recopilador. El flujo de pruebas existente descubre ambos conjuntos y comprueba la conservación del histórico. El PR #1 ya estaba fusionado en el commit `b2213f6`, con 5.532 velas en el CSV de referencia.

Con el corte del 20 de noviembre de 2026 a las 09:35 UTC y los valores predeterminados se registraron 52 ejecuciones ficticias, con 93,62 € de patrimonio final, −6,38 % de rentabilidad neta, 6,49 % de drawdown máximo y 12 % de aciertos sobre 25 ventas. Quedaron dos posiciones abiertas. La muestra es corta y el resultado describe únicamente esta reproducción.

## Ejecución periódica y Telegram

Para operar periódicamente con cartera ficticia falta un flujo separado tras una publicación correcta del recopilador. Ese flujo deberá verificar la hora real y la disponibilidad de todas las velas antes de procesarlas, persistir de forma transaccional la cartera, las órdenes y el último cierre procesado, y serializar ejecuciones concurrentes. Conviene persistir también un identificador por alerta para que un reintento no vuelva a enviarla. El comando histórico vuelve a reproducir el CSV desde el inicio.

El informe ya se entrega por salida estándar y `trading.informe.CanalAlertas` permite conectar un adaptador de mensajería sin acoplarlo a la simulación. Para activar Telegram falta implementar ese adaptador y configurar el token y el destino como secretos del entorno de ejecución, con redacción de errores y mensajes dimensionados para el canal.
