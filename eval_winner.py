"""Evaluacion completa de la mejor candidata (SMA trend 50) con metricas reales."""
from __future__ import annotations

import pandas as pd
from rich.console import Console
from rich.table import Table

from src.data.download import load
from src.backtest.engine import Costs, run_backtest
from src.backtest.metrics import buy_and_hold_return, compute_metrics
from strategy_lab import make_sma_trend, make_ema_cross, INITIAL, COSTS

console = Console()

CANDIDATES = {
    "SMA trend 50": make_sma_trend(50),
    "EMA cross 10/30": make_ema_cross(10, 30),
}

for name, strat in CANDIDATES.items():
    table = Table(title=f"{name} — metricas completas (capital inicial EUR{INITIAL:.0f})")
    table.add_column("Metrica", style="cyan")
    table.add_column("ETH/EUR", justify="right")
    table.add_column("BTC/EUR", justify="right")
    data = {}
    for sym in ["ETH/EUR", "BTC/EUR"]:
        df = load(sym, "1d")
        r = run_backtest(df, strat, INITIAL, COSTS)
        m = compute_metrics(r, periods_per_year=365)
        bh = buy_and_hold_return(df, COSTS.taker_fee_pct, COSTS.slippage_pct)
        days = m.period_days
        eur_per_day = (m.final_equity - INITIAL) / days
        data[sym] = (m, bh, eur_per_day, days)
    rows = [
        ("Equity final", lambda d: f"EUR {d[0].final_equity:,.0f}"),
        ("Retorno total", lambda d: f"{d[0].total_return_pct:+.1f}%"),
        ("Buy&hold mismo periodo", lambda d: f"{d[1]:+.1f}%"),
        ("CAGR (anualizado)", lambda d: f"{d[0].cagr_pct:+.1f}%"),
        ("Max drawdown", lambda d: f"{d[0].max_drawdown_pct:+.1f}%"),
        ("Sharpe", lambda d: f"{d[0].sharpe:.2f}"),
        ("# trades", lambda d: f"{d[0].n_trades}"),
        ("Winrate", lambda d: f"{d[0].winrate:.0f}%"),
        ("Profit factor", lambda d: f"{d[0].profit_factor:.2f}"),
        ("Tiempo en mercado", lambda d: f"{d[0].bars_in_market_pct:.0f}%"),
        ("EUR/dia (media) con EUR400", lambda d: f"EUR {d[2]:+.3f}"),
    ]
    for label, fn in rows:
        table.add_row(label, fn(data["ETH/EUR"]), fn(data["BTC/EUR"]))
    console.print(table)
    # cuanto capital para 1 EUR/dia
    for sym in ["ETH/EUR", "BTC/EUR"]:
        m, bh, epd, days = data[sym]
        if epd > 0:
            cap_for_1 = 400 * (1.0 / epd)
            console.print(f"  [dim]{sym}: para ~EUR1/dia de media necesitarias ~EUR{cap_for_1:,.0f} de capital (a este ritmo).[/dim]")
    console.print()
