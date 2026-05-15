"""Estrategia C — DCA + RSI dip/peak (referencia / baseline).

NO es trading direccional: es acumulación inteligente. Se incluye como línea
base honesta — si A o B no la baten, no merece la pena la complejidad de live.

Reglas:
- Compra fija de X € cada lunes (vela diaria del lunes).
- Compra extra (×2) cualquier día que RSI(14) diario < 30.
- Venta parcial (25 % de la posición) cualquier día que RSI(14) > 75.
- Nunca cierra todo. La métrica es el equity final vs. buy & hold puro.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..backtest.engine import Order, Position
from ..indicators import rsi


@dataclass
class DcaRsiParams:
    weekly_buy_eur: float = 25.0
    weekly_buy_weekday: int = 0   # 0 = lunes
    rsi_period: int = 14
    rsi_dip_threshold: float = 30.0
    rsi_dip_multiplier: float = 2.0
    rsi_peak_threshold: float = 75.0
    rsi_peak_sell_pct: float = 25.0   # % de la posición que vendemos en pico


def prepare(df: pd.DataFrame, p: DcaRsiParams) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = rsi(df["close"], p.rsi_period)
    df["weekday"] = df["datetime"].dt.weekday
    return df


def make_strategy(p: DcaRsiParams):
    prepared: dict = {"df_id": None, "df": None}
    state = {"last_weekly_buy_date": None}

    def strategy(df: pd.DataFrame, i: int, position: Optional[Position], cash: float) -> list[Order]:
        if prepared["df_id"] is not id(df):
            prepared["df"] = prepare(df, p)
            prepared["df_id"] = id(df)
            state["last_weekly_buy_date"] = None
        d = prepared["df"]

        row = d.iloc[i]
        orders: list[Order] = []
        today = row["datetime"].date()

        # 1) Compra semanal fija el día indicado.
        if (
            int(row["weekday"]) == p.weekly_buy_weekday
            and state["last_weekly_buy_date"] != today
            and cash >= p.weekly_buy_eur * 1.01  # cubre fees
        ):
            orders.append(Order(
                side="buy",
                fraction_of_cash=min(p.weekly_buy_eur / cash, 1.0),
                tag="dca_weekly",
            ))
            state["last_weekly_buy_date"] = today

        # 2) Compra extra en RSI dip.
        if pd.notna(row["rsi"]) and row["rsi"] < p.rsi_dip_threshold:
            extra = p.weekly_buy_eur * p.rsi_dip_multiplier
            if cash >= extra * 1.01 and state.get("last_dip_date") != today:
                orders.append(Order(
                    side="buy",
                    fraction_of_cash=min(extra / cash, 1.0),
                    tag="dca_dip",
                ))
                state["last_dip_date"] = today

        # 3) Venta parcial en RSI peak (sólo si hay posición).
        if (
            position is not None
            and pd.notna(row["rsi"])
            and row["rsi"] > p.rsi_peak_threshold
            and state.get("last_peak_date") != today
        ):
            orders.append(Order(
                side="sell",
                fraction_of_position=p.rsi_peak_sell_pct / 100,
                tag="dca_peak",
            ))
            state["last_peak_date"] = today

        return orders

    return strategy
