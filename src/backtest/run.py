"""CLI para correr las 3 estrategias sobre un par y timeframe e imprimir comparación.

Uso:
    python -m src.backtest.run --symbol ETH/EUR --timeframe 1h
    python -m src.backtest.run --symbol ETH/EUR --timeframe 1d
"""
from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from ..data.download import load
from ..strategy import breakout_1h, donchian_daily, dca_rsi
from .engine import Costs, run_backtest
from .metrics import buy_and_hold_return, compute_metrics

console = Console()


PERIODS_PER_YEAR = {"1m": 525_600, "5m": 105_120, "15m": 35_040, "1h": 8_760, "4h": 2_190, "1d": 365}


def run_one(name: str, df, strategy_fn, initial: float, costs: Costs, timeframe: str):
    result = run_backtest(df, strategy_fn, initial, costs)
    m = compute_metrics(result, periods_per_year=PERIODS_PER_YEAR[timeframe])
    m.buy_and_hold_return_pct = buy_and_hold_return(df, costs.taker_fee_pct, costs.slippage_pct)
    return name, result, m


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="ETH/EUR")
    parser.add_argument("--timeframe", default="1h", choices=list(PERIODS_PER_YEAR))
    parser.add_argument("--initial", type=float, default=400.0)
    parser.add_argument("--fee", type=float, default=0.10, help="taker fee % por lado")
    parser.add_argument("--slippage", type=float, default=0.05, help="slippage % por lado")
    args = parser.parse_args()

    df = load(args.symbol, args.timeframe)
    costs = Costs(taker_fee_pct=args.fee, slippage_pct=args.slippage)

    console.print(f"\n[bold]Backtest {args.symbol} {args.timeframe}[/bold] — "
                  f"{len(df):,} velas, "
                  f"{df['datetime'].iloc[0].date()} → {df['datetime'].iloc[-1].date()}, "
                  f"capital inicial €{args.initial:.0f}\n")

    runs = []

    if args.timeframe == "1h":
        runs.append(run_one(
            "A - Breakout 1h",
            df, breakout_1h.make_strategy(breakout_1h.BreakoutParams()),
            args.initial, costs, args.timeframe,
        ))
    if args.timeframe == "1d":
        runs.append(run_one(
            "B - Donchian diario",
            df, donchian_daily.make_strategy(donchian_daily.DonchianParams()),
            args.initial, costs, args.timeframe,
        ))
        runs.append(run_one(
            "C - DCA + RSI",
            df, dca_rsi.make_strategy(dca_rsi.DcaRsiParams()),
            args.initial, costs, args.timeframe,
        ))

    # Tabla resumen
    table = Table(title="Resumen comparativo", show_lines=True)
    table.add_column("Métrica", style="cyan")
    for name, _, _ in runs:
        table.add_column(name)

    metric_keys = [
        ("Equity final (€)", lambda m: f"{m.final_equity:,.2f}"),
        ("Retorno total", lambda m: f"{m.total_return_pct:+.2f} %"),
        ("CAGR", lambda m: f"{m.cagr_pct:+.2f} %"),
        ("Buy & hold mismo periodo", lambda m: f"{m.buy_and_hold_return_pct:+.2f} %"),
        ("Max drawdown", lambda m: f"{m.max_drawdown_pct:+.2f} %"),
        ("Sharpe (anualizado)", lambda m: f"{m.sharpe:.2f}"),
        ("# trades", lambda m: f"{m.n_trades}"),
        ("Winrate", lambda m: f"{m.winrate:.1f} %"),
        ("Profit factor", lambda m: f"{m.profit_factor:.2f}" if m.profit_factor != float('inf') else "∞"),
        ("Esperanza/trade", lambda m: f"{m.expectancy_pct:+.2f} %"),
        ("Tiempo en mercado", lambda m: f"{m.bars_in_market_pct:.1f} %"),
    ]

    for label, fn in metric_keys:
        row = [label]
        for _, _, m in runs:
            row.append(fn(m))
        table.add_row(*row)

    console.print(table)

    # Desglose de razones de salida por estrategia
    for name, result, _ in runs:
        if not result.trades:
            continue
        reasons = {}
        for t in result.trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        console.print(f"\n[bold]{name}[/bold] — razones de salida: " +
                      ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))


if __name__ == "__main__":
    main()
