"""Análisis de sensibilidad: ¿cuánto depende el resultado del DCA de la fecha
de corte? Corre DCA constante sobre la misma serie pero terminando en varias
fechas, para separar "la estrategia es mala" de "miras en un mal momento".

Uso: python window_sensitivity.py
"""
from __future__ import annotations

import pandas as pd
from rich.console import Console
from rich.table import Table

from src.data.download import load
from src.strategy import dca_constant
from src.backtest.engine import CashSchedule, Costs, run_backtest

console = Console()

END_DATES = ["2024-06-30", "2024-12-31", "2025-06-30", "2025-12-31", "2026-06-08"]
WEEKLY = 25.0
COSTS = Costs(taker_fee_pct=0.10, slippage_pct=0.05)


def dca_profit_pct(df: pd.DataFrame) -> tuple[float, float, float]:
    """Devuelve (invertido, equity_final, profit_pct) del DCA constante."""
    strat = dca_constant.make_strategy(dca_constant.DcaConstantParams())
    sched = CashSchedule(weekly_amount=WEEKLY, weekday=0)
    r = run_backtest(df, strat, 0.0, COSTS, sched)
    invested = r.total_injected
    profit_pct = (r.final_equity / invested - 1) * 100 if invested else 0.0
    return invested, r.final_equity, profit_pct


def buy_hold_pct(df: pd.DataFrame) -> float:
    buy = df["close"].iloc[0] * (1 + 0.15 / 100)
    sell = df["close"].iloc[-1] * (1 - 0.15 / 100)
    return (sell / buy - 1) * 100


for symbol in ["ETH/EUR", "BTC/EUR"]:
    full = load(symbol, "1d")
    table = Table(title=f"Sensibilidad a la fecha de corte — {symbol} (DCA constante, EUR25/sem)", show_lines=True)
    table.add_column("Fin de ventana", style="cyan")
    table.add_column("Invertido")
    table.add_column("Equity final")
    table.add_column("Profit DCA %")
    table.add_column("Buy&hold %")
    for end in END_DATES:
        sliced = full[full["datetime"] <= pd.Timestamp(end, tz="UTC")].reset_index(drop=True)
        if len(sliced) < 30:
            continue
        invested, equity, profit = dca_profit_pct(sliced)
        bh = buy_hold_pct(sliced)
        color = "green" if profit > 0 else "red"
        table.add_row(end, f"EUR {invested:,.0f}", f"EUR {equity:,.0f}",
                      f"[{color}]{profit:+.1f} %[/{color}]", f"{bh:+.1f} %")
    console.print(table)
    console.print()
