"""DCA + señales RSI sobre el mismo cash schedule que la baseline.

Diferencias respecto a DCA constante:
- Compra semanal SOLO si RSI <= rsi_skip_threshold (no compra cuando ETH está
  caliente, lo que deja cash acumulándose como buffer).
- Día con RSI < rsi_dip: deploy del 100 % del cash (incluido el buffer
  acumulado por skips). Máx una vez por semana.
- Día con RSI > rsi_peak: vende rsi_peak_sell_pct % de la posición. El cash
  vuelve al pool y se redeploya en el próximo lunes/dip.

Es "DCA con disciplina": compras los lunes baratos, te saltas los lunes caros,
y tomas profit parcial en máximos. Si esto no bate al DCA constante, las
señales RSI no añaden valor real.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from ..backtest.engine import Order, Position
from ..indicators import rsi


@dataclass
class DcaRsiParams:
    buy_weekday: int = 0
    rsi_period: int = 14
    rsi_skip_threshold: float = 70.0   # no compra el lunes si RSI > esto
    rsi_dip: float = 30.0              # RSI < esto -> dip buy (100 % cash)
    rsi_peak: float = 75.0             # RSI > esto -> peak sell parcial
    rsi_peak_sell_pct: float = 25.0
    min_action_gap_days: int = 7       # no más de un dip o peak por semana
    min_buy_eur: float = 5.0


def make_strategy(p: DcaRsiParams):
    cache: dict = {"df_id": None, "weekdays": None, "dates": None, "rsi": None}
    state: dict = {"last_buy_date": None, "last_dip_date": None, "last_peak_date": None}

    def strategy(df: pd.DataFrame, i: int, position: Optional[Position], cash: float) -> list[Order]:
        if cache["df_id"] != id(df):
            idx = pd.DatetimeIndex(df["datetime"])
            cache["weekdays"] = idx.weekday.to_numpy()
            cache["dates"] = idx.date
            cache["rsi"] = rsi(df["close"], p.rsi_period).to_numpy()
            cache["df_id"] = id(df)
            state["last_buy_date"] = None
            state["last_dip_date"] = None
            state["last_peak_date"] = None

        today = cache["dates"][i]
        rsi_i = cache["rsi"][i]
        if np.isnan(rsi_i):
            return []

        orders: list[Order] = []

        # 1) Dip buy: RSI muy bajo, deployea TODO el cash (incluido buffer).
        if rsi_i < p.rsi_dip and cash >= p.min_buy_eur:
            if state["last_dip_date"] is None or (today - state["last_dip_date"]).days >= p.min_action_gap_days:
                orders.append(Order(side="buy", fraction_of_cash=1.0, tag="dca_dip"))
                state["last_dip_date"] = today
                state["last_buy_date"] = today
                return orders  # no más acciones hoy

        # 2) Peak sell: RSI alto, venta parcial.
        if (
            position is not None
            and rsi_i > p.rsi_peak
            and (state["last_peak_date"] is None or (today - state["last_peak_date"]).days >= p.min_action_gap_days)
        ):
            orders.append(Order(
                side="sell",
                fraction_of_position=p.rsi_peak_sell_pct / 100,
                tag="dca_peak",
            ))
            state["last_peak_date"] = today

        # 3) Compra semanal el día indicado, SOLO si RSI no está muy alto.
        if (
            cache["weekdays"][i] == p.buy_weekday
            and today != state["last_buy_date"]
            and rsi_i <= p.rsi_skip_threshold
            and cash >= p.min_buy_eur
        ):
            orders.append(Order(side="buy", fraction_of_cash=1.0, tag="dca_weekly"))
            state["last_buy_date"] = today

        return orders

    return strategy
