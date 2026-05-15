"""Estrategia B — Trend following diario tipo Donchian.

Reglas:
- Filtro de régimen: EMA(50) > EMA(200) en velas diarias.
- Entrada: cierre rompe el máximo de los últimos 20 días (excluyendo el actual).
- Stop inicial: mínimo de los últimos 10 días.
- Salida: cierre rompe a la baja el mínimo de los últimos 10 días (trailing
  por Donchian inferior, que se va recomputando cada vela).
- Sin TP fijo. Riesgo por trade: 1.5 % del capital.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..backtest.engine import Order, Position
from ..indicators import donchian_high, donchian_low, ema


@dataclass
class DonchianParams:
    ema_fast: int = 50
    ema_slow: int = 200
    high_period: int = 20
    low_period: int = 10
    risk_per_trade_pct: float = 1.5


def prepare(df: pd.DataFrame, p: DonchianParams) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = ema(df["close"], p.ema_fast)
    df["ema_slow"] = ema(df["close"], p.ema_slow)
    df["donchian_high"] = donchian_high(df["high"], p.high_period)
    df["donchian_low"] = donchian_low(df["low"], p.low_period)
    return df


def make_strategy(p: DonchianParams):
    prepared: dict = {"df_id": None, "df": None}

    def strategy(df: pd.DataFrame, i: int, position: Optional[Position], cash: float) -> list[Order]:
        if prepared["df_id"] != id(df):
            prepared["df"] = prepare(df, p)
            prepared["df_id"] = id(df)
        d = prepared["df"]

        if i < max(p.ema_slow, p.high_period):
            return []
        row = d.iloc[i]

        # Salida: si hay posición, comprueba rotura del Donchian low.
        if position is not None:
            if pd.notna(row["donchian_low"]) and row["close"] < row["donchian_low"]:
                return [Order(side="sell", fraction_of_position=1.0, tag="donchian_exit")]
            return []

        # Entrada
        if pd.isna(row["ema_fast"]) or pd.isna(row["donchian_high"]):
            return []
        if row["ema_fast"] <= row["ema_slow"]:
            return []
        if row["close"] <= row["donchian_high"]:
            return []

        entry_ref = float(row["close"])
        sl = float(row["donchian_low"])
        risk_per_unit = entry_ref - sl
        if risk_per_unit <= 0:
            return []

        risk_quote = cash * p.risk_per_trade_pct / 100
        invest = min(risk_quote * entry_ref / risk_per_unit, cash * 0.95)
        if invest < 10:
            return []

        # Stop dinámico — el engine lo gestionará pero como aquí lo movemos por
        # Donchian (no por trailing fijo) lo controlamos desde la propia
        # estrategia con ventas explícitas. Pasamos un SL inicial hard como red.
        return [Order(
            side="buy",
            fraction_of_cash=invest / cash,
            stop_loss=sl,
            tag="donchian_daily",
        )]

    return strategy
