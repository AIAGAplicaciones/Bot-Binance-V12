# Bot Binance 12

Bot de trading para Binance Spot. **Filosofía: validar antes de operar.**

## Estado del proyecto

🚧 Fase 1 de 5 — Skeleton + descarga de datos. Sin lógica live todavía.

## Roadmap

| Fase | Qué se construye | Estado |
|------|------------------|--------|
| 1 | Skeleton + descarga de OHLCV histórico de Binance | en curso |
| 2 | Motor de backtest con fees y slippage reales | pendiente |
| 3 | Implementación + comparación de 3 estrategias candidatas | pendiente |
| 4 | Loop live + paper trading sobre la estrategia ganadora | pendiente |
| 5 | Dashboard + despliegue | pendiente |

Las fases 4-5 **no se empiezan** hasta que la fase 3 produzca una estrategia con esperanza positiva neta de fees, validada en al menos 18 meses de datos.

## Estrategias que vamos a comparar

Ver [`STRATEGY.md`](STRATEGY.md) para detalles. Resumen:

- **A — Breakout 1h con ATR**: rotura de máximos + filtro EMA200, stops dinámicos por ATR.
- **B — Trend following diario (Donchian)**: rotura de máximos de 20 días, salida por mínimos de 10 días.
- **C — DCA + RSI dip**: referencia de acumulación pasiva, no es trading direccional.

## Setup local

```bash
cd "Bot binance 12"
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # solo necesario para fase 4+
```

## Descargar datos históricos

```bash
python -m src.data.download --symbol ETHEUR --timeframe 1h --months 24
python -m src.data.download --symbol ETHEUR --timeframe 1d --months 36
python -m src.data.download --symbol BTCEUR --timeframe 1h --months 24
```

Los CSV se guardan en `data/cache/`.

## Estructura

```
Bot binance 12/
├── pyproject.toml
├── config.yaml              # parámetros de estrategia (vivo)
├── STRATEGY.md              # spec detallada de las 3 estrategias
├── src/
│   ├── data/
│   │   └── download.py      # descarga OHLCV de Binance vía ccxt
│   ├── indicators.py        # ATR, EMA, Donchian, RSI
│   ├── strategy/            # (fase 3)
│   ├── backtest/            # (fase 2)
│   └── live/                # (fase 4)
├── data/cache/              # CSVs descargados (gitignored)
└── tests/
```

## Avisos

- Trading automatizado puede llevar a pérdida total del capital.
- No es asesoramiento financiero.
- Empieza siempre con paper trading. Mete capital real solo después de meses de paper rentable.
