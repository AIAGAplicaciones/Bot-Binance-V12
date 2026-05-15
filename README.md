# Bot Binance 12

Bot de **DCA constante** para Binance Spot, desplegado en Railway.

> ⚠️ La validación con backtest descartó el trading activo (5 estrategias TA-based, todas con esperanza negativa). Lo que se despliega aquí es un **acumulador**: compra €X de un activo cada semana, sin señales de timing.

## Estado

✅ Fase 1-3 — backtest validado (no funciona ninguna TA simple sobre ETH/EUR).
✅ Fase 4 — DCA constante desplegable.
🚧 Fase 5 — dashboard web (pendiente).

## Cómo funciona

- Cada `check_interval_minutes` (default 30 min) el bot mira la fecha UTC actual.
- Si han pasado al menos `buy_every_n_days` desde la última compra (default 3) y aún no ha comprado hoy:
  - Coge el precio actual de Binance.
  - Lanza una market buy de `amount_per_buy_eur` (default €10) sobre `symbol` (default ETH/EUR).
  - Persiste la operación en SQLite con clave única `(buy_date_utc, symbol)`.
- Si reinicia, no compra dos veces el mismo día (idempotencia por fecha).
- Si está parado varios días y vuelve, **no recupera compras perdidas** — compra una vez al volver y reanuda el ritmo. No acumula gasto inesperado.
- Hard cap absoluto: `max_total_eur` (default €10 000). Si la suma histórica lo supera, deja de comprar y solo loggea.

**Frecuencias típicas** (con `amount_per_buy_eur = €10`):

| `buy_every_n_days` | Compras/mes | Gasto/mes |
|---|---|---|
| 1 (diario) | ~30 | ~€300 |
| 2 | ~15 | ~€150 |
| 3 (default) | ~10 | ~€100 |
| 7 (semanal) | ~4 | ~€40 |

**Modos**:
- `FORCE_PAPER=true` (default): simula compras, no llama a Binance trading API.
- `FORCE_PAPER=false` + keys API válidas: ejecuta market buys reales.

## Setup local

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edita .env: por defecto FORCE_PAPER=true (paper mode)
python -m src.main
# Abre http://localhost:8000 → {"status":"ok"}
# http://localhost:8000/status → JSON con resumen (requiere auth básica)
```

## Despliegue en Railway

### 1. Push del repo

```bash
git push origin main
```

Railway detecta el `Dockerfile` y `railway.toml` y arranca un build.

### 2. Variables de entorno (Railway → Variables)

| Variable | Valor recomendado inicial | Cuando flipas a live |
|---|---|---|
| `FORCE_PAPER` | `true` | `false` |
| `BINANCE_API_KEY` | (vacío) | tu key (solo permiso Spot Trading) |
| `BINANCE_API_SECRET` | (vacío) | tu secret |
| `DASHBOARD_USER` | `admin` | `admin` |
| `DASHBOARD_PASSWORD` | password fuerte | password fuerte |
| `DATABASE_PATH` | `/app/data/bot.db` | igual |
| `LOG_LEVEL` | `INFO` | `INFO` |

### 3. Volumen persistente (imprescindible)

Railway → **+ New** → **Volume**. Mount path: `/app/data`. Asocia al servicio.
Sin esto, cada redeploy borra el histórico de compras y el bot pierde la idempotencia.

### 4. Configurar el cap

Edita `config.yaml` antes de pushear (no env var):

```yaml
live:
  symbol: "ETH/EUR"
  amount_per_buy_eur: 10.0
  buy_every_n_days: 3
  max_total_eur: 10000.0
```

`max_total_eur` es la red de seguridad final: si por bug el bot intentara comprar más de eso en total histórico, se para. Pónselo a algo realista para tu horizonte (e.g., €100/mes × 60 meses = €6 000).

### 5. Permisos de la API key de Binance

En Binance → API Management:
- ✅ Enable Spot & Margin Trading.
- ❌ NO marques Enable Withdrawals.
- Restringe por IP (Railway te da una IP estática en planes de pago, o sin restricción si usas free tier).

### 6. Checklist antes de quitar `FORCE_PAPER`

- [ ] El bot lleva ≥1 mes corriendo en `FORCE_PAPER=true`.
- [ ] El histórico de "compras paper" en SQLite tiene buenos timestamps (revisa `/status`).
- [ ] La API key no tiene permiso de retirada.
- [ ] El volumen `/app/data` está montado y persiste.
- [ ] `max_total_eur` está puesto a un valor que estás cómodo perdiendo.

## Endpoints

- `GET /` — healthcheck público (Railway lo usa).
- `GET /status` — JSON con modo, config, summary, último precio, P&L sobre coste medio. **Requiere HTTP Basic Auth**.

## Backtest local

Aunque el bot live es DCA constante, el repo conserva el backtester para que puedas validar nuevas ideas sin tocar producción:

```bash
# DCA comparison (constante vs RSI):
python -m src.backtest.dca_run --symbol ETH/EUR --weekly 25

# Estrategia activa S1 (5m mean reversion) — solo como referencia, perdió:
python -m src.backtest.run --symbol ETH/EUR --timeframe 5m
```

Spec detallada de las estrategias probadas y por qué se descartaron: [`STRATEGY.md`](STRATEGY.md).

## Estructura

```
Bot binance 12/
├── pyproject.toml
├── config.yaml              # parámetros del bot live
├── Dockerfile
├── railway.toml
├── .env.example
├── STRATEGY.md              # análisis de estrategias probadas
├── src/
│   ├── main.py              # entry point: arranca uvicorn
│   ├── live/
│   │   ├── server.py        # FastAPI + lifespan que arranca el runner
│   │   ├── runner.py        # loop DCA
│   │   ├── broker.py        # Binance wrapper (paper + live)
│   │   └── store.py         # SQLite
│   ├── data/download.py     # descarga OHLCV histórico (para backtest)
│   ├── indicators.py
│   ├── strategy/            # estrategias probadas en backtest
│   └── backtest/            # motor + métricas + runners
└── tests/                   # 11 tests, paper + idempotencia + indicadores
```

## Avisos

- Trading automatizado puede llevar a pérdida de capital.
- **No es asesoramiento financiero.** Pruébalo en paper antes de meter dinero real.
- DCA pasivo no garantiza rentabilidad — solo es una estrategia de gestión de riesgo emocional / de timing.
