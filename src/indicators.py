"""Indicadores técnicos básicos. Implementaciones explícitas, sin pandas-ta,
para que sean transparentes y fáciles de testear."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ATR de Wilder."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def donchian_high(high: pd.Series, period: int) -> pd.Series:
    """Máximo de las últimas `period` velas, EXCLUYENDO la vela actual.

    Esto evita lookahead bias: si comparas el cierre de hoy con el máximo que
    INCLUYE el high de hoy, siempre estás comparándolo con un techo que ya
    contiene el dato. Lo correcto es comparar con el techo previo."""
    return high.shift(1).rolling(window=period, min_periods=period).max()


def donchian_low(low: pd.Series, period: int) -> pd.Series:
    return low.shift(1).rolling(window=period, min_periods=period).min()
