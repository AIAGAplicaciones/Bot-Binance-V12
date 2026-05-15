"""Comparación de estrategias DCA con inyección continua de cash.

Métricas adaptadas: con cash siendo inyectado a lo largo del tiempo, las
métricas estándar (total return, CAGR sobre capital inicial) no aplican.
Aquí mostramos:
- Total inyectado (suma de aportaciones)
- Equity final (cash + valor de la posición a precio de cierre)
- Profit absoluto (€) y como % sobre lo inyectado
- Comparación contra "lump sum" (comprar todo el dinero al primer cierre)
- Comparación contra DCA constante (baseline pasivo)

Uso:
    python -m src.backtest.dca_run --symbol ETH/EUR --weekly 25
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from ..data.download import load
from ..strategy import dca_constant, dca_rsi
from .engine import CashSchedule, Costs, run_backtest

console = Console()


def lump_sum_equity(df: pd.DataFrame, total_invested: float, fee_pct: float, slippage_pct: float) -> float:
    """Equity final si invirtieras TODO al primer close (no realista para DCA pero útil como referencia)."""
    buy = df["close"].iloc[0] * (1 + (fee_pct + slippage_pct) / 100)
    qty = total_invested * (1 - fee_pct / 100) / buy
    sell = df["close"].iloc[-1] * (1 - (fee_pct + slippage_pct) / 100)
    return qty * sell


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="ETH/EUR")
    parser.add_argument("--weekly", type=float, default=25.0, help="aportación semanal en EUR")
    parser.add_argument("--initial", type=float, default=0.0, help="capital inicial (puede ser 0)")
    parser.add_argument("--fee", type=float, default=0.10)
    parser.add_argument("--slippage", type=float, default=0.05)
    args = parser.parse_args()

    df = load(args.symbol, "1d")
    costs = Costs(taker_fee_pct=args.fee, slippage_pct=args.slippage)
    schedule = CashSchedule(weekly_amount=args.weekly, weekday=0)

    console.print(
        f"\n[bold]DCA backtest {args.symbol} diario[/bold] — "
        f"{len(df):,} velas, "
        f"{df['datetime'].iloc[0].date()} → {df['datetime'].iloc[-1].date()}, "
        f"capital inicial €{args.initial:.0f}, aportación €{args.weekly:.0f}/semana\n"
    )

    runs = []
    for name, strat in [
        ("DCA constante (baseline)", dca_constant.make_strategy(dca_constant.DcaConstantParams())),
        ("DCA + RSI",                dca_rsi.make_strategy(dca_rsi.DcaRsiParams())),
    ]:
        result = run_backtest(df, strat, args.initial, costs, schedule)
        runs.append((name, result))

    # Lump sum como referencia: invierte el TOTAL inyectado al primer cierre
    total_inv = runs[0][1].total_injected
    lump_eq = lump_sum_equity(df, total_inv, args.fee, args.slippage)

    table = Table(title="Comparación DCA", show_lines=True)
    table.add_column("Métrica", style="cyan")
    for name, _ in runs:
        table.add_column(name)
    table.add_column("Lump sum (referencia)")

    metrics = [
        ("Total inyectado", lambda r: f"€ {r.total_injected:,.2f}"),
        ("Equity final", lambda r: f"€ {r.final_equity:,.2f}"),
        ("Profit (€)", lambda r: f"€ {r.final_equity - r.total_injected:+,.2f}"),
        ("Profit (% s/inyectado)", lambda r: f"{(r.final_equity / r.total_injected - 1) * 100:+.2f} %" if r.total_injected else "n/a"),
        ("# trades", lambda r: f"{len(r.trades)}"),
        ("# inyecciones", lambda r: f"{len(r.injections)}"),
    ]

    for label, fn in metrics:
        row = [label]
        for _, r in runs:
            row.append(fn(r))
        if label == "Equity final":
            row.append(f"€ {lump_eq:,.2f}")
        elif label == "Profit (€)":
            row.append(f"€ {lump_eq - total_inv:+,.2f}")
        elif label == "Profit (% s/inyectado)":
            row.append(f"{(lump_eq / total_inv - 1) * 100:+.2f} %" if total_inv else "n/a")
        elif label == "Total inyectado":
            row.append(f"€ {total_inv:,.2f}")
        else:
            row.append("—")
        table.add_row(*row)

    console.print(table)

    # Detalle de trades de la variante RSI
    rsi_trades = runs[1][1].trades
    if rsi_trades:
        sell_count = sum(1 for t in rsi_trades if t.entry_tag == "dca_peak")
        console.print(f"\n[bold]DCA + RSI[/bold] — sells parciales en peaks: {sell_count}, total trades cerrados: {len(rsi_trades)}")


if __name__ == "__main__":
    main()
