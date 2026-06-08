"""Laboratorio de estrategias — prueba muchas ideas con validacion honesta.

Cada estrategia es long-only, all-in (entra con ~95% del cash, sale entera).
Se corre UNA vez sobre el df completo y luego los trades se reparten en
ENTRENAMIENTO (primer 60% del tiempo) vs VALIDACION (ultimo 40%), por fecha
de entrada. Una estrategia solo es creible si gana en AMBAS mitades y en
ambos activos (ETH y BTC). Asi se detecta el sobreajuste.

Uso: python strategy_lab.py
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from src.data.download import load
from src.indicators import atr, bollinger, donchian_high, donchian_low, ema, rsi, sma
from src.backtest.engine import Costs, Order, Position, run_backtest

console = Console()
COSTS = Costs(taker_fee_pct=0.10, slippage_pct=0.05)
INITIAL = 400.0
FRAC = 0.95  # % del cash por entrada


# ---------------------------------------------------------------------------
# Estrategias. Cada make_* devuelve un closure (df, i, position, cash)->orders.
# ---------------------------------------------------------------------------
def make_ema_cross(fast: int, slow: int):
    c: dict = {"id": None}

    def s(df, i, position, cash):
        if c["id"] != id(df):
            c["ef"] = ema(df["close"], fast).to_numpy()
            c["es"] = ema(df["close"], slow).to_numpy()
            c["id"] = id(df)
        if i < slow + 1:
            return []
        ef, es = c["ef"], c["es"]
        up = ef[i] > es[i] and ef[i - 1] <= es[i - 1]
        dn = ef[i] < es[i] and ef[i - 1] >= es[i - 1]
        if position is None and up:
            return [Order(side="buy", fraction_of_cash=FRAC, tag="ema_cross")]
        if position is not None and dn:
            return [Order(side="sell", fraction_of_position=1.0, tag="ema_cross")]
        return []
    return s


def make_sma_trend(period: int):
    c: dict = {"id": None}

    def s(df, i, position, cash):
        if c["id"] != id(df):
            c["m"] = sma(df["close"], period).to_numpy()
            c["id"] = id(df)
        if i < period + 1:
            return []
        m = c["m"]
        cl = df["close"].to_numpy()
        if position is None and cl[i] > m[i] and cl[i - 1] <= m[i - 1]:
            return [Order(side="buy", fraction_of_cash=FRAC, tag="sma_trend")]
        if position is not None and cl[i] < m[i] and cl[i - 1] >= m[i - 1]:
            return [Order(side="sell", fraction_of_position=1.0, tag="sma_trend")]
        return []
    return s


def make_donchian(high_p: int, low_p: int, ema_filter: int = 0):
    c: dict = {"id": None}

    def s(df, i, position, cash):
        if c["id"] != id(df):
            c["dh"] = donchian_high(df["high"], high_p).to_numpy()
            c["dl"] = donchian_low(df["low"], low_p).to_numpy()
            c["ef"] = ema(df["close"], ema_filter).to_numpy() if ema_filter else None
            c["id"] = id(df)
        if i < max(high_p, low_p, ema_filter) + 1:
            return []
        cl = df["close"].to_numpy()
        dh, dl = c["dh"], c["dl"]
        if position is not None:
            if not np.isnan(dl[i]) and cl[i] < dl[i]:
                return [Order(side="sell", fraction_of_position=1.0, tag="donchian")]
            return []
        if np.isnan(dh[i]) or cl[i] <= dh[i]:
            return []
        if c["ef"] is not None and not (cl[i] > c["ef"][i]):
            return []
        return [Order(side="buy", fraction_of_cash=FRAC, tag="donchian")]
    return s


def make_rsi_meanrev(buy_th: float, sell_th: float, period: int = 14):
    c: dict = {"id": None}

    def s(df, i, position, cash):
        if c["id"] != id(df):
            c["r"] = rsi(df["close"], period).to_numpy()
            c["id"] = id(df)
        if i < period + 1:
            return []
        r = c["r"]
        if np.isnan(r[i]):
            return []
        if position is None and r[i] < buy_th:
            return [Order(side="buy", fraction_of_cash=FRAC, tag="rsi_mr")]
        if position is not None and r[i] > sell_th:
            return [Order(side="sell", fraction_of_position=1.0, tag="rsi_mr")]
        return []
    return s


def make_bollinger_mr(period: int = 20, n_std: float = 2.0):
    c: dict = {"id": None}

    def s(df, i, position, cash):
        if c["id"] != id(df):
            mid, up, low = bollinger(df["close"], period, n_std)
            c["mid"] = mid.to_numpy(); c["low"] = low.to_numpy()
            c["id"] = id(df)
        if i < period + 1:
            return []
        cl = df["close"].to_numpy()
        if np.isnan(c["low"][i]):
            return []
        if position is None and cl[i] < c["low"][i]:
            return [Order(side="buy", fraction_of_cash=FRAC, tag="bb_mr")]
        if position is not None and cl[i] > c["mid"][i]:
            return [Order(side="sell", fraction_of_position=1.0, tag="bb_mr")]
        return []
    return s


def make_momentum(lookback: int, sma_filter: int = 100):
    c: dict = {"id": None}

    def s(df, i, position, cash):
        if c["id"] != id(df):
            c["m"] = sma(df["close"], sma_filter).to_numpy()
            c["id"] = id(df)
        if i < max(lookback, sma_filter) + 1:
            return []
        cl = df["close"].to_numpy()
        ret = cl[i] / cl[i - lookback] - 1
        above = cl[i] > c["m"][i]
        if position is None and ret > 0 and above:
            return [Order(side="buy", fraction_of_cash=FRAC, tag="momentum")]
        if position is not None and (ret < 0 or not above):
            return [Order(side="sell", fraction_of_position=1.0, tag="momentum")]
        return []
    return s


def make_atr_breakout(k: float, atr_p: int = 14, trail_k: float = 3.0):
    c: dict = {"id": None}

    def s(df, i, position, cash):
        if c["id"] != id(df):
            c["a"] = atr(df["high"], df["low"], df["close"], atr_p).to_numpy()
            c["id"] = id(df)
        if i < atr_p + 1:
            return []
        cl = df["close"].to_numpy()
        a = c["a"]
        if np.isnan(a[i]) or position is not None:
            return []
        trigger = cl[i - 1] + k * a[i]
        if cl[i] > trigger:
            return [Order(side="buy", fraction_of_cash=FRAC,
                          trailing_distance=trail_k * a[i], tag="atr_brk")]
        return []
    return s


def make_buy_hold():
    c: dict = {"id": None}

    def s(df, i, position, cash):
        if c["id"] != id(df):
            c["id"] = id(df)
        if i == 1 and position is None:
            return [Order(side="buy", fraction_of_cash=FRAC, tag="bh")]
        return []
    return s


STRATEGIES = {
    "Buy&Hold (ref)":      make_buy_hold(),
    "EMA cross 20/50":     make_ema_cross(20, 50),
    "EMA cross 10/30":     make_ema_cross(10, 30),
    "SMA trend 50":        make_sma_trend(50),
    "SMA trend 200":       make_sma_trend(200),
    "Donchian 20/10":      make_donchian(20, 10),
    "Donchian 20/10+EMA200": make_donchian(20, 10, 200),
    "Donchian 55/20":      make_donchian(55, 20),
    "RSI mean-rev 30/55":  make_rsi_meanrev(30, 55),
    "RSI mean-rev 25/60":  make_rsi_meanrev(25, 60),
    "Bollinger mean-rev":  make_bollinger_mr(20, 2.0),
    "Momentum 20d":        make_momentum(20, 100),
    "Momentum 40d":        make_momentum(40, 100),
    "ATR breakout k1.5":   make_atr_breakout(1.5),
}


def equity_mult_from_trades(trades, start, end):
    """Multiplicador de equity (all-in) compuesto de los trades con entrada
    en [start, end). pnl_pct ya es neto de fees."""
    mult = 1.0
    n = 0
    pnls = []
    for t in trades:
        if start <= t.entry_time < end:
            mult *= (1 + t.pnl_pct / 100)
            n += 1
            pnls.append(t.pnl_pct)
    return mult, n, (float(np.mean(pnls)) if pnls else 0.0)


def run_symbol(symbol: str):
    df = load(symbol, "1d")
    t0 = df["datetime"].iloc[0]
    t1 = df["datetime"].iloc[-1]
    split = t0 + (t1 - t0) * 0.6
    days_total = (t1 - t0).days

    table = Table(title=f"{symbol} 1d  —  train (60%) {t0.date()}..{split.date()}  |  test (40%) ..{t1.date()}", show_lines=False)
    table.add_column("Estrategia", style="cyan")
    table.add_column("Ret TRAIN", justify="right")
    table.add_column("Ret TEST", justify="right")
    table.add_column("Esper/trade", justify="right")
    table.add_column("# trades", justify="right")
    table.add_column("Ret TOTAL", justify="right")
    table.add_column("Robusta", justify="center")

    rows = []
    for name, strat in STRATEGIES.items():
        r = run_backtest(df, strat, INITIAL, COSTS)
        m_tr, n_tr, _ = equity_mult_from_trades(r.trades, t0, split)
        m_te, n_te, exp_te = equity_mult_from_trades(r.trades, split, t1 + pd.Timedelta(days=1))
        ret_tr = (m_tr - 1) * 100
        ret_te = (m_te - 1) * 100
        ret_total = (r.final_equity / INITIAL - 1) * 100
        robust = ret_tr > 0 and ret_te > 0 and (n_tr + n_te) >= 4
        rows.append((name, ret_tr, ret_te, exp_te, n_tr + n_te, ret_total, robust))

    rows.sort(key=lambda x: x[2], reverse=True)  # ordenar por retorno en TEST
    for name, ret_tr, ret_te, exp_te, n, ret_total, robust in rows:
        ctr = "green" if ret_tr > 0 else "red"
        cte = "green" if ret_te > 0 else "red"
        table.add_row(
            name,
            f"[{ctr}]{ret_tr:+.1f}%[/{ctr}]",
            f"[{cte}]{ret_te:+.1f}%[/{cte}]",
            f"{exp_te:+.2f}%",
            str(n),
            f"{ret_total:+.1f}%",
            "[bold green]SI[/bold green]" if robust else "no",
        )
    console.print(table)
    console.print(f"[dim]Periodo total: {days_total} dias[/dim]\n")
    return rows


console.print("\n[bold]LABORATORIO DE ESTRATEGIAS[/bold] — 14 ideas, validacion train/test, 2 activos\n")
all_rows = {}
for sym in ["ETH/EUR", "BTC/EUR"]:
    all_rows[sym] = run_symbol(sym)

# Resumen: estrategias robustas en AMBOS activos
console.print("[bold]Estrategias ROBUSTAS (ganan en train y test) por activo:[/bold]")
robust_by_sym = {}
for sym, rows in all_rows.items():
    robust = [r[0] for r in rows if r[6]]
    robust_by_sym[sym] = set(robust)
    console.print(f"  {sym}: {', '.join(robust) if robust else '(ninguna)'}")

both = robust_by_sym["ETH/EUR"] & robust_by_sym["BTC/EUR"]
console.print(f"\n[bold green]Robustas en AMBOS activos:[/bold green] {', '.join(both) if both else '(NINGUNA)'}")
