"""Estrategia A — Breakout 1h con filtro EMA200 y stops por ATR.

Reglas:
- Filtro de régimen: precio > EMA(200).
- Entrada: cierre rompe el máximo de las últimas N velas (excluyendo la actual)
  + ATR(14) actual >= 0.7 × ATR media 50  + volumen vela >= 1.3 × media 20.
- Sizing por riesgo: arriesga 1% del capital por trade. La cantidad de ETH se
  calcula a partir de la distancia entry → stop.
- SL = entry - 1.5 × ATR(14). TP = entry + 3.0 × ATR(14). Trailing distance =
  1.5 × ATR(14). Time stop = 48 horas (48 velas).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..backtest.engine import Order, Position
from ..indicators import atr, ema, donchian_high, sma


@dataclass
class BreakoutParams:
    ema_period: int = 200
    lookback_high: int = 20
    atr_period: int = 14
    atr_avg_period: int = 50
    atr_min_ratio: float = 0.7
    volume_ma_period: int = 20
    volume_multiplier: float = 1.3
    atr_stop_mult: float = 1.5
    atr_target_mult: float = 3.0
    trailing_atr_mult: float = 1.5
    time_stop_bars: int = 48
    risk_per_trade_pct: float = 1.0


def prepare(df: pd.DataFrame, p: BreakoutParams) -> pd.DataFrame:
    df = df.copy()
    df["ema"] = ema(df["close"], p.ema_period)
    df["donchian_high"] = donchian_high(df["high"], p.lookback_high)
    df["atr"] = atr(df["high"], df["low"], df["close"], p.atr_period)
    df["atr_avg"] = sma(df["atr"], p.atr_avg_period)
    df["vol_ma"] = sma(df["volume"], p.volume_ma_period)
    return df


def make_strategy(p: BreakoutParams):
    """Devuelve una StrategyFn lista para usar con run_backtest."""
    prepared: dict = {"df_id": None, "df": None}

    def strategy(df: pd.DataFrame, i: int, position: Optional[Position], cash: float) -> list[Order]:
        # Cachea el df enriquecido para no recalcular indicadores cada vela.
        if prepared["df_id"] != id(df):
            prepared["df"] = prepare(df, p)
            prepared["df_id"] = id(df)
        d = prepared["df"]

        if i < max(p.ema_period, p.atr_avg_period):
            return []
        if position is not None:
            return []

        row = d.iloc[i]
        if pd.isna(row["ema"]) or pd.isna(row["donchian_high"]) or pd.isna(row["atr"]):
            return []

        # Filtros
        if row["close"] <= row["ema"]:
            return []
        if row["close"] <= row["donchian_high"]:
            return []
        if row["atr"] < row["atr_avg"] * p.atr_min_ratio:
            return []
        if row["volume"] < row["vol_ma"] * p.volume_multiplier:
            return []

        # Sizing por riesgo
        atr_now = float(row["atr"])
        entry_ref = float(row["close"])
        sl = entry_ref - p.atr_stop_mult * atr_now
        tp = entry_ref + p.atr_target_mult * atr_now
        risk_per_unit = entry_ref - sl
        if risk_per_unit <= 0:
            return []

        risk_quote = cash * p.risk_per_trade_pct / 100
        # cuánto invertir = risk_quote * (entry / risk_per_unit)
        invest = risk_quote * entry_ref / risk_per_unit
        invest = min(invest, cash * 0.95)  # nunca usar más del 95 % del cash
        if invest < 10:  # menos de 10 € no merece la pena
            return []

        return [Order(
            side="buy",
            fraction_of_cash=invest / cash,
            stop_loss=sl,
            take_profit=tp,
            trailing_distance=p.trailing_atr_mult * atr_now,
            time_stop_bars=p.time_stop_bars,
            tag="breakout_1h",
        )]

    return strategy
