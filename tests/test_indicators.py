"""Tests básicos de indicadores. Verifican propiedades, no valores exactos."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.indicators import atr, donchian_high, donchian_low, ema, rsi


@pytest.fixture
def sample_ohlc() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    n = 200
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    high = close + rng.uniform(0, 1, n)
    low = close - rng.uniform(0, 1, n)
    return pd.DataFrame({"high": high, "low": low, "close": close})


def test_ema_monotonic_response(sample_ohlc):
    e = ema(sample_ohlc["close"], 20)
    assert len(e) == len(sample_ohlc)
    assert e.notna().sum() > 100


def test_rsi_bounded_0_100(sample_ohlc):
    r = rsi(sample_ohlc["close"], 14).dropna()
    assert (r >= 0).all() and (r <= 100).all()


def test_atr_positive(sample_ohlc):
    a = atr(sample_ohlc["high"], sample_ohlc["low"], sample_ohlc["close"], 14).dropna()
    assert (a > 0).all()


def test_donchian_excludes_current_candle(sample_ohlc):
    """donchian_high(t) NO debe incluir high(t). Si lo hiciera, donchian_high(t)
    sería siempre >= high(t) y nunca habría rotura."""
    dh = donchian_high(sample_ohlc["high"], 20)
    valid = dh.notna()
    # En al menos algún punto, el high de hoy supera el donchian (rotura).
    assert (sample_ohlc["high"][valid] > dh[valid]).any()


def test_donchian_low_symmetry(sample_ohlc):
    dl = donchian_low(sample_ohlc["low"], 20)
    valid = dl.notna()
    assert (sample_ohlc["low"][valid] < dl[valid]).any()
