"""Métricas de rendimiento a partir de un BacktestResult."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .engine import BacktestResult


@dataclass
class Metrics:
    n_trades: int
    winrate: float                # %
    avg_win_pct: float            # % medio sobre notional
    avg_loss_pct: float           # % medio sobre notional (negativo)
    expectancy_pct: float         # % medio neto por trade
    profit_factor: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float       # negativo
    sharpe: float                 # anualizado
    bars_in_market_pct: float
    final_equity: float
    initial_equity: float
    period_days: float
    buy_and_hold_return_pct: Optional[float] = None  # se rellena externamente

    def as_table(self) -> list[tuple[str, str]]:
        def pct(x: float) -> str:
            return f"{x:+.2f} %"
        rows = [
            ("Equity inicial", f"€ {self.initial_equity:,.2f}"),
            ("Equity final", f"€ {self.final_equity:,.2f}"),
            ("Retorno total", pct(self.total_return_pct)),
            ("CAGR (anualizado)", pct(self.cagr_pct)),
            ("Max drawdown", pct(self.max_drawdown_pct)),
            ("Sharpe (anualizado)", f"{self.sharpe:.2f}"),
            ("# trades", str(self.n_trades)),
            ("Winrate", f"{self.winrate:.1f} %"),
            ("Profit factor", f"{self.profit_factor:.2f}" if self.profit_factor != float("inf") else "∞"),
            ("Esperanza por trade", pct(self.expectancy_pct)),
            ("Win medio / Loss medio", f"{pct(self.avg_win_pct)} / {pct(self.avg_loss_pct)}"),
            ("Tiempo en mercado", f"{self.bars_in_market_pct:.1f} %"),
            ("Periodo", f"{self.period_days:.0f} días"),
        ]
        if self.buy_and_hold_return_pct is not None:
            rows.append(("Buy & hold mismo periodo", pct(self.buy_and_hold_return_pct)))
        return rows


def compute_metrics(result: BacktestResult, periods_per_year: int) -> Metrics:
    """`periods_per_year`: 8760 para 1h, 365 para diario."""
    eq = result.equity_curve
    if len(eq) == 0:
        raise ValueError("Equity curve vacío.")

    n_trades = len(result.trades)
    wins = [t for t in result.trades if t.pnl_net > 0]
    losses = [t for t in result.trades if t.pnl_net <= 0]
    winrate = len(wins) / n_trades * 100 if n_trades else 0.0

    avg_win_pct = float(np.mean([t.pnl_pct for t in wins])) if wins else 0.0
    avg_loss_pct = float(np.mean([t.pnl_pct for t in losses])) if losses else 0.0
    expectancy_pct = float(np.mean([t.pnl_pct for t in result.trades])) if result.trades else 0.0

    sum_wins = sum(t.pnl_net for t in wins)
    sum_losses = abs(sum(t.pnl_net for t in losses))
    profit_factor = sum_wins / sum_losses if sum_losses > 0 else float("inf")

    total_return = (eq.iloc[-1] / result.initial_cash - 1) * 100

    period_days = (eq.index[-1] - eq.index[0]).total_seconds() / 86400
    years = period_days / 365.25 if period_days > 0 else 0
    cagr = ((eq.iloc[-1] / result.initial_cash) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    peak = eq.cummax()
    dd = (eq - peak) / peak * 100
    max_dd = float(dd.min())

    rets = eq.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(periods_per_year)) if rets.std() > 0 else 0.0

    bars_in_mkt = sum(t.bars_held for t in result.trades)
    bars_total = len(eq)
    bars_in_mkt_pct = bars_in_mkt / bars_total * 100 if bars_total else 0.0

    return Metrics(
        n_trades=n_trades,
        winrate=winrate,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        expectancy_pct=expectancy_pct,
        profit_factor=profit_factor,
        total_return_pct=float(total_return),
        cagr_pct=float(cagr),
        max_drawdown_pct=max_dd,
        sharpe=sharpe,
        bars_in_market_pct=float(bars_in_mkt_pct),
        final_equity=float(eq.iloc[-1]),
        initial_equity=float(result.initial_cash),
        period_days=float(period_days),
    )


def buy_and_hold_return(df: pd.DataFrame, fee_pct: float, slippage_pct: float) -> float:
    """Retorno de comprar al primer close y vender al último, con fees y slippage."""
    buy = df["close"].iloc[0] * (1 + (fee_pct + slippage_pct) / 100)
    sell = df["close"].iloc[-1] * (1 - (fee_pct + slippage_pct) / 100)
    return (sell / buy - 1) * 100
