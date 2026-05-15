"""DCA constante (baseline): cada semana compra todo el cash que haya, no vende.

Es la línea base contra la que comparar cualquier DCA "inteligente". Si una
DCA con señales no la bate, las señales no aportan valor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..backtest.engine import Order, Position


@dataclass
class DcaConstantParams:
    buy_weekday: int = 0          # 0=lunes
    min_buy_eur: float = 5.0      # no compra si cash < esto


def make_strategy(p: DcaConstantParams):
    cache: dict = {"df_id": None, "weekdays": None, "dates": None}
    state: dict = {"last_buy_date": None}

    def strategy(df: pd.DataFrame, i: int, position: Optional[Position], cash: float) -> list[Order]:
        if cache["df_id"] != id(df):
            idx = pd.DatetimeIndex(df["datetime"])
            cache["weekdays"] = idx.weekday.to_numpy()
            cache["dates"] = idx.date
            cache["df_id"] = id(df)
            state["last_buy_date"] = None

        if cache["weekdays"][i] != p.buy_weekday:
            return []
        today = cache["dates"][i]
        if today == state["last_buy_date"]:
            return []
        if cash < p.min_buy_eur:
            return []

        state["last_buy_date"] = today
        return [Order(side="buy", fraction_of_cash=1.0, tag="dca_constant")]

    return strategy
