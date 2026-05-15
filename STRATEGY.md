# Estrategias candidatas

Tres estrategias que vamos a implementar y comparar en backtest sobre 18-24 meses
de datos reales de ETHEUR (y BTCEUR como control), con fees Binance reales (0.1%
taker × 2 = 0.2% por ida-vuelta) y slippage estimado (0.05% adicional).

La que tenga **mejor Sharpe ajustada por drawdown y esperanza neta positiva** será
la base del bot live.

---

## Métricas de evaluación

Para cada estrategia mediremos:

- **Esperanza neta por trade** (después de fees y slippage).
- **Winrate**.
- **Profit factor** (suma de ganancias / suma de pérdidas).
- **Max drawdown** (peor caída del equity desde un máximo).
- **Sharpe anualizado**.
- **Número de trades** (relevancia estadística).
- **Tiempo expuesto al mercado** (% del periodo con posición abierta).

Criterio de descarte rápido: si la esperanza neta por trade es negativa, fuera.

---

## Estrategia A — Breakout 1h con ATR

**Tesis**: en crypto las roturas de rango con volumen tienden a continuar mientras
el régimen sea alcista; ATR ajusta los stops a la volatilidad actual evitando
stops fijos que mueren en mercados volátiles.

**Timeframe**: velas de 1 hora.

**Filtro de régimen** (sin esto NO se opera):
- Precio actual > EMA(200) en velas de 1h.

**Entrada (long)** cuando se cumplen TODAS:
1. El cierre de la última vela rompe por encima del máximo de las últimas 20 velas.
2. ATR(14) actual > ATR(14) media de últimas 50 velas × 0.7 (no entrar en mercado muerto).
3. Volumen de la vela de rotura ≥ media de volumen × 1.3.

**Tamaño de posición**: riesgo fijo del 1% del capital por trade.
- `tamaño = (capital × 0.01) / (precio_entrada - stop_loss)`

**Salidas**:
- **Stop loss**: precio_entrada − 1.5 × ATR(14).
- **Take profit**: precio_entrada + 3.0 × ATR(14) (R:R = 1:2).
- **Trailing stop**: una vez el precio sube +1.5×ATR, sube el stop a breakeven; si sigue subiendo, traila a 1.5×ATR por debajo del máximo alcanzado.
- **Time stop**: cierra si lleva 48 horas abierto sin tocar TP ni SL.

**Frecuencia esperada**: 3-8 trades / semana en mercado activo, 0-2 en mercado lateral.

**Por qué puede funcionar**:
- Fees representan ~5% del edge, no 60%.
- ATR adapta automáticamente a la volatilidad.
- Filtro EMA200 evita operar contra-tendencia.

**Por qué puede fallar**:
- Whipsaw en lateralidades (rompe, vuelve, te ejecuta el SL).
- Drawdowns grandes en cambios de régimen.

---

## Estrategia B — Trend following diario (Donchian)

**Tesis**: la versión "tonta y robusta" del trend following. Pocas operaciones,
captura grandes tendencias, ignora ruido.

**Timeframe**: vela diaria.

**Filtro de régimen**:
- EMA(50) > EMA(200) en velas diarias.

**Entrada (long)**:
- Cierre diario rompe el máximo de los últimos 20 días.

**Tamaño de posición**: riesgo fijo del 1.5% del capital por trade.
- Stop inicial = mínimo de los últimos 10 días.

**Salidas**:
- **Stop trailing**: cierra cuando el precio rompe a la baja el mínimo de los últimos 10 días.
- Sin TP fijo. Se deja correr.

**Frecuencia esperada**: 1-3 trades / mes.

**Por qué puede funcionar**:
- Fees son irrelevantes (<0.5% del edge).
- Cero estrés operativo, casi no requiere supervisión.
- Históricamente, trend following en crypto ha capturado ralles de 50-200% sin asfixiar.

**Por qué puede fallar**:
- Drawdowns de 20-30% durante mercados laterales largos.
- Pocas señales = sample size pequeño = difícil saber si funciona o tienes suerte.

---

## Estrategia C — DCA + RSI dip (referencia)

**No es trading direccional**, es acumulación inteligente. La incluimos como
**baseline**: si las estrategias A y B no baten al "comprar y aguantar inteligente",
no merecen existir.

**Timeframe**: vela diaria.

**Comportamiento**:
- Compra fija de X € cada lunes.
- Compra extra (×2) cualquier día que RSI(14) diario < 30.
- Venta parcial (25% de la posición) cualquier día que RSI(14) diario > 75.
- Nunca cierra todo. El equity se compara contra "buy and hold" puro.

**Métrica de éxito**: precio medio de adquisición vs holdear pasivo, y equity final.

**Por qué la incluimos**:
- Si A o B no baten esto, el esfuerzo de mantener un bot trader no se justifica.
- Es la línea base honesta.

---

## Decisión post-backtest

Después de correr las 3 sobre los datos:

1. Si **A o B** baten C en términos de equity final Y tienen drawdown menor al 25% Y esperanza neta positiva → esa es la estrategia del bot.
2. Si **C** gana → el "bot" es realmente un acumulador con DCA + dip-buying, y nos ahorramos toda la complejidad live de stops, trailings, etc.
3. Si **ninguna** tiene esperanza positiva neta → no se construye bot live. Toca repensar.

Esta decisión la tomamos con datos en la mano, no con intuición.
