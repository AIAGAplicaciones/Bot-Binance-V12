"""Estrategia S1 — Mean reversion en 5m con filtro de régimen lateral.

Tesis: en mercados sin tendencia clara, los toques de la banda inferior de
Bollinger con sobreventa extrema en RSI(2) tienden a revertir hacia la media.

Reglas de entrada (TODAS):
- ADX(14) < adx_max: mercado lateral.
- close <= banda inferior de Bollinger(20, 2σ).
- RSI(2) < rsi_oversold: sobreventa extrema.
- volumen <= volume_ma × volume_max_multiplier: evita capitulación.

Salidas (cualquiera):
- TP fijo = entry × (1 + tp_pct).
- SL fijo = entry × (1 - sl_pct).
- Time stop: time_stop_bars (default 12 = 60 min en velas de 5m).

Math (con fees 0.20% round-trip y TP 0.7% / SL 0.4%):
- Net win = +0.50%, net loss = -0.60%
- Breakeven winrate = 0.60 / 1.10 = 54.5 %
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from ..backtest.engine import Order, Position
from ..indicators import adx, bollinger, rsi, sma


@dataclass
class MeanReversion5mParams:
    adx_period: int = 14
    adx_max: float = 25.0
    bb_period: int = 20
    bb_std: float = 2.0
    rsi_period: int = 2
    rsi_oversold: float = 10.0
    volume_ma_period: int = 20
    volume_max_multiplier: float = 2.0
    take_profit_pct: float = 0.7
    stop_loss_pct: float = 0.4
    time_stop_bars: int = 12
    position_pct: float = 25.0


def make_strategy(p: MeanReversion5mParams):
    cache: dict = {"df_id": None, "arrs": None, "warmup": 0}

    def _prepare(df: pd.DataFrame) -> dict:
        adx_ = adx(df["high"], df["low"], df["close"], p.adx_period).to_numpy()
        bb_mid, _bb_up, bb_low = bollinger(df["close"], p.bb_period, p.bb_std)
        rsi_ = rsi(df["close"], p.rsi_period).to_numpy()
        vol_ma = sma(df["volume"], p.volume_ma_period).to_numpy()
        return {
            "close": df["close"].to_numpy(dtype=np.float64),
            "volume": df["volume"].to_numpy(dtype=np.float64),
            "adx": adx_,
            "bb_low": bb_low.to_numpy(),
            "rsi": rsi_,
            "vol_ma": vol_ma,
        }

    def strategy(df: pd.DataFrame, i: int, position: Optional[Position], cash: float) -> list[Order]:
        if cache["df_id"] != id(df):
            cache["arrs"] = _prepare(df)
            cache["df_id"] = id(df)
            cache["warmup"] = max(p.adx_period * 2, p.bb_period, p.volume_ma_period)
        if i < cache["warmup"] or position is not None:
            return []

        a = cache["arrs"]
        adx_i = a["adx"][i]
        bb_low_i = a["bb_low"][i]
        rsi_i = a["rsi"][i]
        vol_ma_i = a["vol_ma"][i]
        if np.isnan(adx_i) or np.isnan(bb_low_i) or np.isnan(rsi_i) or np.isnan(vol_ma_i):
            return []

        close_i = a["close"][i]
        if adx_i >= p.adx_max:
            return []
        if close_i > bb_low_i:
            return []
        if rsi_i >= p.rsi_oversold:
            return []
        if a["volume"][i] > vol_ma_i * p.volume_max_multiplier:
            return []

        sl = close_i * (1 - p.stop_loss_pct / 100)
        tp = close_i * (1 + p.take_profit_pct / 100)
        invest = cash * p.position_pct / 100
        if invest < 10:
            return []

        return [Order(
            side="buy",
            fraction_of_cash=invest / cash,
            stop_loss=sl,
            take_profit=tp,
            time_stop_bars=p.time_stop_bars,
            tag="mean_rev_5m",
        )]

    return strategy
