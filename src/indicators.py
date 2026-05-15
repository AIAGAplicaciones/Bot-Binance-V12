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


def bollinger(close: pd.Series, period: int = 20, n_std: float = 2.0):
    """Devuelve (middle, upper, lower)."""
    middle = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std()
    upper = middle + n_std * std
    lower = middle - n_std * std
    return middle, upper, lower


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index con smoothing de Wilder.

    ADX < 20: mercado sin tendencia clara (lateral).
    ADX > 25: tendencia presente.
    ADX > 40: tendencia fuerte.
    """
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )

    tr = true_range(high, low, close)
    atr_ = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
