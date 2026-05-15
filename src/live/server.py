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

log = logging.getLogger(__name__)
security = HTTPBasic()


def _load_config() -> tuple[DcaConfig, str]:
    """Lee config.yaml de la raíz del repo."""
    from pathlib import Path
    cfg_path = Path(__file__).resolve().parents[2] / "config.yaml"
    if not cfg_path.exists():
        log.warning("config.yaml no encontrado, usando defaults")
        return DcaConfig(), str(cfg_path)
    with cfg_path.open() as f:
        raw = yaml.safe_load(f) or {}
    live = raw.get("live", {}) or {}
    return DcaConfig(
        symbol=live.get("symbol", "ETH/EUR"),
        weekly_eur=float(live.get("weekly_eur", 25.0)),
        buy_weekday=int(live.get("buy_weekday", 0)),
        check_interval_minutes=int(live.get("check_interval_minutes", 30)),
        max_total_eur=float(live.get("max_total_eur", 10_000.0)),
    ), str(cfg_path)


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
    cfg, cfg_path = _load_config()
    log.info("Config cargada de %s: %s", cfg_path, cfg)

    db_path = os.getenv("DATABASE_PATH", "./data/bot.db")
    store = Store(db_path)
    broker = make_broker(cfg.symbol)
    runner = DcaRunner(broker, store, cfg)

    app.state.config = cfg
    app.state.store = store
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


@app.get("/status")
def status_endpoint(_user: Annotated[str, Depends(_check_auth)]) -> dict:
    cfg: DcaConfig = app.state.config
    store: Store = app.state.store
    broker = app.state.broker

    summary = store.summary(cfg.symbol)
    last_buys = [
        {
            "date": b.buy_date_utc.isoformat(),
            "qty": b.base_qty,
            "price": b.fill_price,
            "amount_eur": b.quote_amount_eur,
            "mode": b.mode,
        }
        for b in store.list_buys(cfg.symbol, limit=10)
    ]
    try:
        current_price = broker.get_price()
    except Exception as e:  # red caída u otra
        current_price = None
        log.exception("No se pudo obtener precio actual: %s", e)

    position_value = summary["qty"] * current_price if current_price else None
    pnl = (position_value - summary["invested"]) if position_value is not None else None

    return {
        "mode": broker.mode,
        "config": {
            "symbol": cfg.symbol,
            "weekly_eur": cfg.weekly_eur,
            "buy_weekday_utc": cfg.buy_weekday,
            "max_total_eur": cfg.max_total_eur,
        },
        "summary": summary,
        "current_price": current_price,
        "position_value_eur": position_value,
        "unrealized_pnl_eur": pnl,
        "last_buys": last_buys,
    }
