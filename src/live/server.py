"""FastAPI con healthcheck + endpoint de status básico (auth).

El runner DCA corre como tarea asyncio dentro del lifespan de FastAPI: un solo
proceso, una sola imagen, un puerto para Railway.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import yaml
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .broker import make_broker
from .runner import DcaConfig, DcaRunner
from .store import Store
from .trend_runner import TrendConfig, TrendRunner
from .scalein_runner import ScaleinConfig, ScaleinRunner

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


def _dca_symbols() -> list[str]:
    """Monedas que acumula el DCA. Acepta `symbols` (lista) o `symbol` (única)."""
    live, _ = _read_live_block()
    syms = live.get("symbols")
    if syms:
        return [str(s) for s in syms]
    return [str(live.get("symbol", "ETH/EUR"))]


def _load_config_for(symbol: str) -> DcaConfig:
    """DcaConfig para un símbolo concreto (mismos parámetros para todos)."""
    live, _ = _read_live_block()
    return DcaConfig(
        symbol=symbol,
        amount_per_buy_eur=float(live.get("amount_per_buy_eur", 10.0)),
        buy_every_n_days=int(live.get("buy_every_n_days", 3)),
        check_interval_minutes=int(live.get("check_interval_minutes", 30)),
        max_total_eur=float(live.get("max_total_eur", 10_000.0)),
        take_profit_pct=float(live.get("take_profit_pct", 0.0)),
        sell_pct_of_position=float(live.get("sell_pct_of_position", 25.0)),
        min_days_between_sells=int(live.get("min_days_between_sells", 30)),
    )


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


def _load_scalein_config() -> tuple[ScaleinConfig, str]:
    """Lee la config del runner scale-in de config.yaml."""
    live, cfg_path = _read_live_block()
    sc = live.get("scalein_runner", {}) or {}
    symbols = tuple(sc.get("symbols", ["ETH/USDC", "BTC/USDC"]))
    return ScaleinConfig(
        symbols=symbols,
        sma_period=int(sc.get("sma_period", 50)),
        n_chunks=int(sc.get("n_chunks", 5)),
        allocation_eur_per_symbol=float(sc.get("allocation_eur_per_symbol", 100.0)),
        timeframe=str(sc.get("timeframe", "1d")),
        check_interval_minutes=int(sc.get("check_interval_minutes", 20)),
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

    runners = []
    if kind == "trend":
        tcfg, cfg_path = _load_trend_config()
        log.info("Config cargada de %s | runner=trend | %s", cfg_path, tcfg)
        brokers = {sym: make_broker(sym) for sym in tcfg.symbols}
        app.state.trend_config = tcfg
        app.state.brokers = brokers
        runners.append(TrendRunner(brokers=brokers, store=store, config=tcfg))
    elif kind == "scalein":
        sccfg, cfg_path = _load_scalein_config()
        log.info("Config cargada de %s | runner=scalein | %s", cfg_path, sccfg)
        brokers = {sym: make_broker(sym) for sym in sccfg.symbols}
        app.state.scalein_config = sccfg
        app.state.brokers = brokers
        runners.append(ScaleinRunner(brokers=brokers, store=store, config=sccfg))
    else:
        # DCA: un runner independiente por cada moneda, compartiendo el store.
        symbols = _dca_symbols()
        brokers, configs = {}, {}
        for sym in symbols:
            cfg = _load_config_for(sym)
            brokers[sym] = make_broker(sym)
            configs[sym] = cfg
            runners.append(DcaRunner(brokers[sym], store, cfg))
            log.info("Config cargada de %s | runner=dca | %s", cfg_path_safe(), cfg)
        app.state.dca_symbols = symbols
        app.state.dca_brokers = brokers
        app.state.dca_configs = configs

    app.state.runners = runners
    tasks = [asyncio.create_task(r.start()) for r in runners]
    try:
        yield
    finally:
        for r in runners:
            r.stop()
        for t in tasks:
            try:
                await asyncio.wait_for(t, timeout=5)
            except asyncio.TimeoutError:
                t.cancel()


def cfg_path_safe() -> str:
    _, p = _read_live_block()
    return p


app = FastAPI(title="Bot Binance 12 — DCA constante", lifespan=lifespan)


@app.get("/")
def root() -> RedirectResponse:
    """La raíz lleva directa al dashboard. El healthcheck de Railway vive en /health."""
    return RedirectResponse(url="/dashboard")


@app.get("/health")
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


def _status_payload() -> dict:
    """Construye el dict de estado (lo usan /status en JSON y /dashboard en HTML)."""
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

    # ----- modo SCALEIN: estado por símbolo -----
    if getattr(app.state, "runner_kind", "dca") == "scalein":
        sccfg: ScaleinConfig = app.state.scalein_config
        brokers = app.state.brokers
        per_symbol = {sym: _symbol_status(store, brokers[sym], sym) for sym in sccfg.symbols}
        mode = ",".join(sorted({b.mode for b in brokers.values()}))
        total_real = sum(s["realized_pnl_eur"] or 0 for s in per_symbol.values())
        total_unreal = sum((s["unrealized_pnl_eur"] or 0) for s in per_symbol.values())
        return {
            "mode": mode,
            "runner": "scalein",
            "config": {
                "symbols": list(sccfg.symbols),
                "sma_period": sccfg.sma_period,
                "n_chunks": sccfg.n_chunks,
                "allocation_eur_per_symbol": sccfg.allocation_eur_per_symbol,
                "timeframe": sccfg.timeframe,
            },
            "realized_pnl_eur": total_real,
            "unrealized_pnl_eur": total_unreal,
            "total_pnl_eur": total_real + total_unreal,
            "symbols": per_symbol,
        }

    # ----- modo DCA (uno o varios símbolos) -----
    symbols = app.state.dca_symbols
    brokers = app.state.dca_brokers
    configs = app.state.dca_configs
    per_symbol = {sym: _symbol_status(store, brokers[sym], sym) for sym in symbols}
    mode = ",".join(sorted({b.mode for b in brokers.values()}))
    total_real = sum(s["realized_pnl_eur"] or 0 for s in per_symbol.values())
    total_unreal = sum((s["unrealized_pnl_eur"] or 0) for s in per_symbol.values())
    c0 = configs[symbols[0]]
    return {
        "mode": mode,
        "runner": "dca",
        "config": {
            "symbols": symbols,
            "amount_per_buy_eur": c0.amount_per_buy_eur,
            "buy_every_n_days": c0.buy_every_n_days,
            "max_total_eur": c0.max_total_eur,
            "take_profit_pct": c0.take_profit_pct,
            "sell_pct_of_position": c0.sell_pct_of_position,
            "min_days_between_sells": c0.min_days_between_sells,
        },
        "realized_pnl_eur": total_real,
        "unrealized_pnl_eur": total_unreal,
        "total_pnl_eur": total_real + total_unreal,
        "symbols": per_symbol,
    }


@app.get("/status")
def status_endpoint(_user: Annotated[str, Depends(_check_auth)]) -> dict:
    return _status_payload()


# ---------------------------------------------------------------------------
# Dashboard web (HTML, sin build step) — estado del bot de forma visual.
# Sirve en /dashboard con la misma auth básica. No toca el healthcheck público.
# ---------------------------------------------------------------------------
_DASHBOARD_CSS = """
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         background:#0d1117; color:#e6edf3; padding:24px; }
  .wrap { max-width:980px; margin:0 auto; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#8b949e; font-size:13px; margin-bottom:18px; }
  .badges { margin-bottom:18px; }
  .badge { display:inline-block; padding:4px 10px; border-radius:999px; font-size:12px;
           font-weight:600; margin-right:8px; }
  .hero { background:#161b22; border:1px solid #30363d; border-radius:14px;
          padding:22px; margin-bottom:18px; text-align:center; }
  .hero .label { color:#8b949e; font-size:13px; }
  .hero .big { font-size:40px; font-weight:700; margin-top:4px; }
  .card { background:#161b22; border:1px solid #30363d; border-radius:14px;
          padding:18px 20px; margin-bottom:18px; }
  .card-h { display:flex; justify-content:space-between; align-items:baseline; }
  .card-h h2 { margin:0; font-size:17px; }
  .price { font-size:18px; font-weight:600; color:#58a6ff; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
          gap:10px; margin:16px 0; }
  .kv { background:#0d1117; border:1px solid #21262d; border-radius:10px; padding:10px 12px; }
  .kv span { display:block; color:#8b949e; font-size:11px; margin-bottom:3px; }
  .kv b { font-size:15px; }
  h3 { font-size:13px; color:#8b949e; margin:16px 0 6px; text-transform:uppercase; letter-spacing:.4px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:6px 8px; border-bottom:1px solid #21262d; }
  th { color:#8b949e; font-weight:500; }
  .pos { color:#3fb950; } .neg { color:#f85149; } .muted { color:#8b949e; }
  .foot { color:#8b949e; font-size:12px; text-align:center; margin-top:8px; }
</style>
"""


def _eur(x) -> str:
    return f"€{x:,.2f}" if isinstance(x, (int, float)) else "—"


def _pnl_span(x) -> str:
    if not isinstance(x, (int, float)):
        return '<span class="muted">—</span>'
    cls = "pos" if x >= 0 else "neg"
    sign = "+" if x >= 0 else ""
    return f'<span class="{cls}">{sign}{x:,.2f} €</span>'


def _render_symbol_block(s: dict) -> str:
    summary = s.get("summary", {}) or {}
    sym = s.get("symbol", "?")
    net_qty = summary.get("net_qty", 0) or 0
    avg_cost = summary.get("avg_cost")
    buys = (s.get("last_buys") or [])[:8]
    sells = (s.get("last_sells") or [])[:5]

    buy_rows = "".join(
        f"<tr><td>{b.get('date','')}</td><td>{_eur(b.get('price'))}</td>"
        f"<td>{(b.get('qty') or 0):.6f}</td><td>{_eur(b.get('amount_eur'))}</td></tr>"
        for b in buys
    ) or '<tr><td colspan="4" class="muted">Sin compras todavía</td></tr>'
    sell_rows = "".join(
        f"<tr><td>{x.get('date','')}</td><td>{_eur(x.get('price'))}</td>"
        f"<td>{(x.get('qty') or 0):.6f}</td><td>{_pnl_span(x.get('realized_pnl_eur'))}</td></tr>"
        for x in sells
    ) or '<tr><td colspan="4" class="muted">Sin ventas (el bot solo ha comprado)</td></tr>'

    return f"""
    <div class="card">
      <div class="card-h"><h2>{sym}</h2><span class="price">{_eur(s.get('current_price'))}</span></div>
      <div class="grid">
        <div class="kv"><span>Invertido</span><b>{_eur(summary.get('invested'))}</b></div>
        <div class="kv"><span>Valor posición</span><b>{_eur(s.get('position_value_eur'))}</b></div>
        <div class="kv"><span>Unidades</span><b>{net_qty:.6f}</b></div>
        <div class="kv"><span>Coste medio</span><b>{_eur(avg_cost) if avg_cost else '—'}</b></div>
        <div class="kv"><span>Ganancia latente</span><b>{_pnl_span(s.get('unrealized_pnl_eur'))}</b></div>
        <div class="kv"><span>Ganancia realizada</span><b>{_pnl_span(s.get('realized_pnl_eur'))}</b></div>
        <div class="kv"><span>Compras</span><b>{summary.get('n', 0)}</b></div>
        <div class="kv"><span>Ventas</span><b>{summary.get('n_sells', 0)}</b></div>
      </div>
      <h3>Últimas compras</h3>
      <table><thead><tr><th>Fecha</th><th>Precio</th><th>Unidades</th><th>€</th></tr></thead>
      <tbody>{buy_rows}</tbody></table>
      <h3>Últimas ventas</h3>
      <table><thead><tr><th>Fecha</th><th>Precio</th><th>Unidades</th><th>Resultado</th></tr></thead>
      <tbody>{sell_rows}</tbody></table>
    </div>"""


def _render_dashboard(data: dict) -> str:
    mode = data.get("mode", "?")
    runner = data.get("runner", "dca")
    is_live = "live" in mode
    mode_color = "#f85149" if is_live else "#3fb950"
    mode_text = "LIVE · DINERO REAL" if is_live else "PAPER · simulado"
    cfg = data.get("config", {}) or {}

    if runner == "trend":
        cfg_line = (f"Modo TREND · SMA-{cfg.get('sma_period')} · "
                    f"€{cfg.get('allocation_eur_per_symbol')}/símbolo · {cfg.get('timeframe')}")
    elif runner == "scalein":
        cfg_line = (f"Modo SCALE-IN · SMA-{cfg.get('sma_period')} · "
                    f"{cfg.get('n_chunks')} trozos · €{cfg.get('allocation_eur_per_symbol')}/símbolo · "
                    f"{cfg.get('timeframe')} · {', '.join(cfg.get('symbols', []))}")
    else:
        syms = cfg.get("symbols") or [cfg.get("symbol")]
        cfg_line = (f"Modo DCA · €{cfg.get('amount_per_buy_eur')} cada "
                    f"{cfg.get('buy_every_n_days')} días · TP {cfg.get('take_profit_pct')}% · "
                    f"{', '.join(str(s) for s in syms if s)}")

    if data.get("symbols"):       # estado por símbolo (trend o DCA multi-moneda)
        blocks = "".join(_render_symbol_block(s) for s in data["symbols"].values())
    else:                          # DCA de un solo símbolo en formato plano (compat)
        blocks = _render_symbol_block({
            "symbol": cfg.get("symbol"),
            "summary": data.get("summary", {}),
            "current_price": data.get("current_price"),
            "position_value_eur": data.get("position_value_eur"),
            "unrealized_pnl_eur": data.get("unrealized_pnl_eur"),
            "realized_pnl_eur": data.get("realized_pnl_eur"),
            "last_buys": data.get("last_buys", []),
            "last_sells": data.get("last_sells", []),
        })

    total = data.get("total_pnl_eur")
    hero_val = _pnl_span(total) if isinstance(total, (int, float)) else '<span class="muted">—</span>'
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    head = ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="refresh" content="60">'
            '<title>Bot Binance 12 — Dashboard</title>' + _DASHBOARD_CSS + '</head><body>')
    body = f"""
    <div class="wrap">
      <h1>Bot Binance 12</h1>
      <div class="sub">{cfg_line}</div>
      <div class="badges">
        <span class="badge" style="background:{mode_color};color:#0d1117;">{mode_text}</span>
        <span class="badge" style="background:#21262d;color:#8b949e;">runner: {runner}</span>
      </div>
      <div class="hero">
        <div class="label">Resultado total (realizado + latente)</div>
        <div class="big">{hero_val}</div>
      </div>
      {blocks}
      <div class="foot">Actualizado {now} · se refresca solo cada 60 s</div>
    </div></body></html>"""
    return head + body


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_endpoint(_user: Annotated[str, Depends(_check_auth)]) -> HTMLResponse:
    return HTMLResponse(_render_dashboard(_status_payload()))
