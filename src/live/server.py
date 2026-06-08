"""FastAPI con healthcheck + endpoint de status básico (auth).

El runner DCA corre como tarea asyncio dentro del lifespan de FastAPI: un solo
proceso, una sola imagen, un puerto para Railway.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

import yaml
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .broker import make_broker
from .runner import DcaConfig, DcaRunner
from .store import Store
from .trend_runner import TrendConfig, TrendRunner

log = logging.getLogger(__name__)
security = HTTPBasic()


def _read_live_block() -> tuple[dict, str]:
    from pathlib import Path
    cfg_path = Path(__file__).resolve().parents[2] / "config.yaml"
    if not cfg_path.exists():
        log.warning("config.yaml no encontrado, usando defaults")
        return {}, str(cfg_path)
    with cfg_path.open() as f:
        raw = yaml.safe_load(f) or {}
    return raw.get("live", {}) or {}, str(cfg_path)


def _load_config() -> tuple[DcaConfig, str]:
    """Lee la config del runner DCA de config.yaml."""
    live, cfg_path = _read_live_block()
    return DcaConfig(
        symbol=live.get("symbol", "ETH/EUR"),
        amount_per_buy_eur=float(live.get("amount_per_buy_eur", 10.0)),
        buy_every_n_days=int(live.get("buy_every_n_days", 3)),
        check_interval_minutes=int(live.get("check_interval_minutes", 30)),
        max_total_eur=float(live.get("max_total_eur", 10_000.0)),
        take_profit_pct=float(live.get("take_profit_pct", 0.0)),
        sell_pct_of_position=float(live.get("sell_pct_of_position", 25.0)),
        min_days_between_sells=int(live.get("min_days_between_sells", 30)),
    ), cfg_path


def _load_trend_config() -> tuple[TrendConfig, str]:
    """Lee la config del runner de tendencia de config.yaml."""
    live, cfg_path = _read_live_block()
    tr = live.get("trend_runner", {}) or {}
    symbols = tuple(tr.get("symbols", ["ETH/EUR", "BTC/EUR"]))
    return TrendConfig(
        symbols=symbols,
        sma_period=int(tr.get("sma_period", 50)),
        allocation_eur_per_symbol=float(tr.get("allocation_eur_per_symbol", 200.0)),
        timeframe=str(tr.get("timeframe", "1d")),
        check_interval_minutes=int(tr.get("check_interval_minutes", 60)),
    ), cfg_path


def _active_runner_kind() -> str:
    live, _ = _read_live_block()
    return str(live.get("active_runner", "dca")).lower()


def _check_auth(credentials: Annotated[HTTPBasicCredentials, Depends(security)]) -> str:
    user = os.getenv("DASHBOARD_USER", "admin")
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if not password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DASHBOARD_PASSWORD no configurado.",
        )
    if credentials.username != user or credentials.password != password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas.",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@asynccontextmanager
async def lifespan(app: FastAPI):
    kind = _active_runner_kind()
    db_path = os.getenv("DATABASE_PATH", "./data/bot.db")
    store = Store(db_path)
    app.state.store = store
    app.state.runner_kind = kind

    if kind == "trend":
        tcfg, cfg_path = _load_trend_config()
        log.info("Config cargada de %s | runner=trend | %s", cfg_path, tcfg)
        brokers = {sym: make_broker(sym) for sym in tcfg.symbols}
        runner = TrendRunner(brokers=brokers, store=store, config=tcfg)
        app.state.trend_config = tcfg
        app.state.brokers = brokers
        app.state.runner = runner
    else:
        cfg, cfg_path = _load_config()
        log.info("Config cargada de %s | runner=dca | %s", cfg_path, cfg)
        broker = make_broker(cfg.symbol)
        runner = DcaRunner(broker, store, cfg)
        app.state.config = cfg
        app.state.broker = broker
        app.state.runner = runner

    task = asyncio.create_task(runner.start())
    try:
        yield
    finally:
        runner.stop()
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            task.cancel()


app = FastAPI(title="Bot Binance 12 — DCA constante", lifespan=lifespan)


@app.get("/")
def healthcheck() -> dict:
    return {"status": "ok"}


def _symbol_status(store: Store, broker, symbol: str) -> dict:
    """Bloque de estado para un símbolo: posición, precio, P&L."""
    summary = store.summary(symbol)
    last_buys = [
        {"date": b.buy_date_utc.isoformat(), "qty": b.base_qty, "price": b.fill_price,
         "amount_eur": b.quote_amount_eur, "mode": b.mode}
        for b in store.list_buys(symbol, limit=10)
    ]
    last_sells = [
        {"date": s.sell_date_utc.isoformat(), "qty": s.base_qty, "price": s.fill_price,
         "proceeds_eur": s.quote_proceeds_eur, "realized_pnl_eur": s.realized_pnl_eur,
         "reason": s.reason, "mode": s.mode}
        for s in store.list_sells(symbol, limit=10)
    ]
    try:
        current_price = broker.get_price()
    except Exception as e:
        current_price = None
        log.exception("No se pudo obtener precio actual de %s: %s", symbol, e)

    net_qty = summary["net_qty"]
    position_value = net_qty * current_price if current_price else None
    if position_value is not None and summary["avg_cost"] is not None:
        unrealized = (current_price - summary["avg_cost"]) * net_qty
    else:
        unrealized = None

    return {
        "symbol": symbol,
        "summary": summary,
        "current_price": current_price,
        "position_value_eur": position_value,
        "unrealized_pnl_eur": unrealized,
        "realized_pnl_eur": summary["realized_pnl"],
        "total_pnl_eur": (unrealized + summary["realized_pnl"]) if unrealized is not None else None,
        "last_buys": last_buys,
        "last_sells": last_sells,
    }


@app.get("/status")
def status_endpoint(_user: Annotated[str, Depends(_check_auth)]) -> dict:
    store: Store = app.state.store

    # ----- modo TREND: estado por símbolo -----
    if getattr(app.state, "runner_kind", "dca") == "trend":
        tcfg: TrendConfig = app.state.trend_config
        brokers = app.state.brokers
        per_symbol = {sym: _symbol_status(store, brokers[sym], sym) for sym in tcfg.symbols}
        mode = ",".join(sorted({b.mode for b in brokers.values()}))
        total_real = sum(s["realized_pnl_eur"] or 0 for s in per_symbol.values())
        total_unreal = sum((s["unrealized_pnl_eur"] or 0) for s in per_symbol.values())
        return {
            "mode": mode,
            "runner": "trend",
            "config": {
                "symbols": list(tcfg.symbols),
                "sma_period": tcfg.sma_period,
                "allocation_eur_per_symbol": tcfg.allocation_eur_per_symbol,
                "timeframe": tcfg.timeframe,
            },
            "realized_pnl_eur": total_real,
            "unrealized_pnl_eur": total_unreal,
            "total_pnl_eur": total_real + total_unreal,
            "symbols": per_symbol,
        }

    # ----- modo DCA (por defecto) -----
    cfg: DcaConfig = app.state.config
    broker = app.state.broker

    summary = store.summary(cfg.symbol)
    last_buys = [
        {"date": b.buy_date_utc.isoformat(), "qty": b.base_qty, "price": b.fill_price,
         "amount_eur": b.quote_amount_eur, "mode": b.mode}
        for b in store.list_buys(cfg.symbol, limit=10)
    ]
    last_sells = [
        {"date": s.sell_date_utc.isoformat(), "qty": s.base_qty, "price": s.fill_price,
         "proceeds_eur": s.quote_proceeds_eur, "realized_pnl_eur": s.realized_pnl_eur,
         "reason": s.reason, "mode": s.mode}
        for s in store.list_sells(cfg.symbol, limit=10)
    ]
    try:
        current_price = broker.get_price()
    except Exception as e:
        current_price = None
        log.exception("No se pudo obtener precio actual: %s", e)

    net_qty = summary["net_qty"]
    position_value = net_qty * current_price if current_price else None
    # unrealized P&L sobre la posición VIVA, valorada a coste medio.
    if position_value is not None and summary["avg_cost"] is not None:
        unrealized = (current_price - summary["avg_cost"]) * net_qty
    else:
        unrealized = None

    return {
        "mode": broker.mode,
        "runner": "dca",
        "config": {
            "symbol": cfg.symbol,
            "amount_per_buy_eur": cfg.amount_per_buy_eur,
            "buy_every_n_days": cfg.buy_every_n_days,
            "max_total_eur": cfg.max_total_eur,
            "take_profit_pct": cfg.take_profit_pct,
            "sell_pct_of_position": cfg.sell_pct_of_position,
            "min_days_between_sells": cfg.min_days_between_sells,
        },
        "summary": summary,
        "current_price": current_price,
        "position_value_eur": position_value,
        "unrealized_pnl_eur": unrealized,
        "realized_pnl_eur": summary["realized_pnl"],
        "total_pnl_eur": (unrealized + summary["realized_pnl"]) if unrealized is not None else None,
        "last_buys": last_buys,
        "last_sells": last_sells,
    }
