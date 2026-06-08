"""Chequeo de estabilidad: la cartera SMA-N (ETH+BTC 50/50, sin filtro de
regimen) para varios N. Si el resultado fuera de muestra (TEST) es positivo
para un rango amplio de N, la ventaja es real; si solo para N=50, fue suerte.
"""
from __future__ import annotations

import pandas as pd
from rich.console import Console
from rich.table import Table

from src.data.download import load
from portfolio_strategy import portfolio_equity, metrics_from_curve

console = Console()

eth = load("ETH/EUR", "1d")
t0, t1 = eth["datetime"].iloc[0], eth["datetime"].iloc[-1]
split = t0 + (t1 - t0) * 0.6

table = Table(title="Estabilidad del parametro — cartera SMA-N (ETH+BTC 50/50, sin filtro)")
table.add_column("SMA N", style="cyan", justify="right")
table.add_column("Ret TRAIN", justify="right")
table.add_column("Ret TEST", justify="right")
table.add_column("Ret FULL", justify="right")
table.add_column("Sharpe", justify="right")
table.add_column("MaxDD", justify="right")
table.add_column("#trades", justify="right")

pos_test = 0
total = 0
for N in [20, 30, 40, 50, 60, 70, 80, 100]:
    eq, ntr = portfolio_equity(N, use_regime=False)
    full = metrics_from_curve(eq, "FULL")
    tr = eq[eq.index <= split]; te = eq[eq.index >= split]
    m_tr = metrics_from_curve(tr, "TR")
    m_te = metrics_from_curve(te, "TE")
    total += 1
    if m_te["total_ret"] > 0:
        pos_test += 1
    ctr = "green" if m_tr["total_ret"] > 0 else "red"
    cte = "green" if m_te["total_ret"] > 0 else "red"
    table.add_row(
        str(N),
        f"[{ctr}]{m_tr['total_ret']:+.1f}%[/{ctr}]",
        f"[{cte}]{m_te['total_ret']:+.1f}%[/{cte}]",
        f"{full['total_ret']:+.1f}%",
        f"{full['sharpe']:.2f}",
        f"{full['dd']:.1f}%",
        str(ntr),
    )

console.print(table)
console.print(f"\n[bold]Positivas fuera de muestra (TEST): {pos_test}/{total}[/bold]")
if pos_test >= total * 0.7:
    console.print("[green]-> Robusto: gana en un rango amplio de N, no es un parametro de suerte.[/green]")
else:
    console.print("[red]-> Fragil: solo gana en N concretos. Sospechoso de sobreajuste.[/red]")
