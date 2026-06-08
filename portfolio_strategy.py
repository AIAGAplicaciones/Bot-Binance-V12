"""Estrategia de cartera con filtro de regimen — el experimento "serio".

Combina tres ideas robustas de trend-following (no ajustadas a estos datos):
  1) FILTRO DE REGIMEN: solo largo cuando BTC > su SMA(200). Si el mercado
     global es bajista, a efectivo. (idea Meb Faber, muy validada)
  2) DIVERSIFICACION: 50% ETH + 50% BTC, equity combinada.
  3) MOTOR: tendencia SMA(N) — compra al cruzar arriba, vende al cruzar abajo
     o si el regimen se pone risk-off.

Evaluacion honesta: metricas en train (60%), test (40%) y full, comparado con
buy&hold y con la SMA-trend sin filtro.

Uso: python portfolio_strategy.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from src.data.download import load
from src.indicators import sma
from src.backtest.engine import Costs, Order, run_backtest

console = Console()
COSTS = Costs(taker_fee_pct=0.10, slippage_pct=0.05)
CAP_PER_ASSET = 200.0     # 200 + 200 = 400 total
FRAC = 0.95


def build_regime(regime_period: int = 200) -> dict:
    """dict fecha->bool: True si BTC cierra por encima de su SMA(period)."""
    btc = load("BTC/EUR", "1d")
    s = sma(btc["close"], regime_period).to_numpy()
    cl = btc["close"].to_numpy()
    dates = pd.DatetimeIndex(btc["datetime"]).date
    reg = {}
    for i in range(len(btc)):
        reg[dates[i]] = (not np.isnan(s[i])) and cl[i] > s[i]
    return reg


def make_regime_sma_trend(sma_period: int, regime: dict | None):
    c: dict = {"id": None}

    def strat(df, i, position, cash):
        if c["id"] != id(df):
            c["m"] = sma(df["close"], sma_period).to_numpy()
            c["dates"] = pd.DatetimeIndex(df["datetime"]).date
            c["id"] = id(df)
        if i < sma_period + 1:
            return []
        m = c["m"]
        cl = df["close"].to_numpy()
        risk_on = True
        if regime is not None:
            risk_on = regime.get(c["dates"][i], False)

        if position is not None:
            # salir si rompe la media a la baja o si el regimen se apaga
            if cl[i] < m[i] or not risk_on:
                return [Order(side="sell", fraction_of_position=1.0, tag="exit")]
            return []
        # entrar: cruce al alza + regimen risk-on
        if cl[i] > m[i] and cl[i - 1] <= m[i - 1] and risk_on:
            return [Order(side="buy", fraction_of_cash=FRAC, tag="entry")]
        return []
    return strat


def portfolio_equity(sma_period: int, use_regime: bool):
    """Corre la estrategia en ETH y BTC y suma las equities (50/50)."""
    regime = build_regime(200) if use_regime else None
    curves = []
    n_trades = 0
    for sym in ["ETH/EUR", "BTC/EUR"]:
        df = load(sym, "1d")
        r = run_backtest(df, make_regime_sma_trend(sma_period, regime), CAP_PER_ASSET, COSTS)
        curves.append(r.equity_curve)
        n_trades += len(r.trades)
    eth_c, btc_c = curves
    # alineacion robusta: reindex sobre union de fechas y ffill
    idx = eth_c.index.union(btc_c.index)
    port = eth_c.reindex(idx).ffill().fillna(CAP_PER_ASSET) + btc_c.reindex(idx).ffill().fillna(CAP_PER_ASSET)
    return port, n_trades


def metrics_from_curve(eq: pd.Series, label: str) -> dict:
    initial = float(eq.iloc[0])   # valor al inicio del tramo (clave para train/test correctos)
    final = float(eq.iloc[-1])
    total_ret = (final / initial - 1) * 100
    days = (eq.index[-1] - eq.index[0]).days
    years = days / 365.25
    cagr = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    peak = eq.cummax()
    dd = ((eq - peak) / peak * 100).min()
    rets = eq.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(365)) if rets.std() > 0 else 0.0
    eur_day = (final - initial) / days if days else 0.0
    return {"label": label, "final": final, "total_ret": total_ret, "cagr": cagr,
            "dd": dd, "sharpe": sharpe, "eur_day": eur_day, "days": days}


def slice_metrics(eq: pd.Series, t0, split, t1):
    full = metrics_from_curve(eq, "FULL")
    tr = eq[eq.index <= split]
    te = eq[eq.index >= split]
    m_tr = metrics_from_curve(tr, "TRAIN") if len(tr) > 5 else None
    m_te = metrics_from_curve(te, "TEST") if len(te) > 5 else None
    return full, m_tr, m_te


# ---- Ejecucion: comparar 3 configuraciones ----
eth = load("ETH/EUR", "1d")
t0, t1 = eth["datetime"].iloc[0], eth["datetime"].iloc[-1]
split = t0 + (t1 - t0) * 0.6

configs = [
    ("SMA-50 SIN filtro (cartera)", 50, False),
    ("SMA-50 + REGIMEN BTC200 (cartera)", 50, True),
    ("SMA-80 + REGIMEN BTC200 (cartera)", 80, True),
]

table = Table(title="Cartera ETH+BTC 50/50 — capital inicial EUR400", show_lines=True)
table.add_column("Config", style="cyan")
table.add_column("Ret TRAIN", justify="right")
table.add_column("Ret TEST", justify="right")
table.add_column("Ret FULL", justify="right")
table.add_column("CAGR", justify="right")
table.add_column("MaxDD", justify="right")
table.add_column("Sharpe", justify="right")
table.add_column("EUR/dia", justify="right")
table.add_column("#tr", justify="right")

for label, period, use_reg in configs:
    eq, ntr = portfolio_equity(period, use_reg)
    full, m_tr, m_te = slice_metrics(eq, t0, split, t1)
    def col(m, key, suf="%"):
        if m is None:
            return "-"
        v = m[key]
        c = "green" if v > 0 else "red"
        return f"[{c}]{v:+.1f}{suf}[/{c}]"
    table.add_row(
        label,
        col(m_tr, "total_ret"),
        col(m_te, "total_ret"),
        col(full, "total_ret"),
        f"{full['cagr']:+.1f}%",
        f"[red]{full['dd']:.1f}%[/red]",
        f"{full['sharpe']:.2f}",
        f"EUR{full['eur_day']:+.3f}",
        str(ntr),
    )

console.print(table)

# Benchmarks de referencia
console.print("\n[bold]Referencias (cartera 50/50 ETH+BTC):[/bold]")
bh_curves = []
for sym in ["ETH/EUR", "BTC/EUR"]:
    df = load(sym, "1d")
    def bh(df, i, position, cash):
        if i == 1 and position is None:
            return [Order(side="buy", fraction_of_cash=FRAC, tag="bh")]
        return []
    r = run_backtest(df, bh, CAP_PER_ASSET, COSTS)
    bh_curves.append(r.equity_curve)
idx = bh_curves[0].index.union(bh_curves[1].index)
bh_port = bh_curves[0].reindex(idx).ffill().fillna(CAP_PER_ASSET) + bh_curves[1].reindex(idx).ffill().fillna(CAP_PER_ASSET)
m = metrics_from_curve(bh_port, "BH")
console.print(f"  Buy&Hold 50/50:  Ret FULL {m['total_ret']:+.1f}%  |  MaxDD {m['dd']:.1f}%  |  Sharpe {m['sharpe']:.2f}  |  EUR/dia EUR{m['eur_day']:+.3f}")
