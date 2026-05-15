"""Tests del DcaRunner sin red: usa un broker mock."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.live.broker import BuyResult
from src.live.runner import DcaConfig, DcaRunner
from src.live.store import Store


@dataclass
class MockBroker:
    mode: str = "paper"
    symbol: str = "ETH/EUR"
    last_price: float = 2400.0
    buys_called: int = 0

    def get_price(self) -> float:
        return self.last_price

    def market_buy_quote(self, quote_amount_eur: float) -> BuyResult:
        self.buys_called += 1
        return BuyResult(
            fill_price=self.last_price * 1.0005,
            base_qty=quote_amount_eur / (self.last_price * 1.0005),
            fee_quote=quote_amount_eur * 0.001,
            order_id=None,
            raw_response=None,
        )


def _at(d: str):
    """Datetime UTC al mediodía de la fecha YYYY-MM-DD."""
    y, m, dd = map(int, d.split("-"))
    return datetime(y, m, dd, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_buys_first_time_then_respects_n_days(tmp_path):
    store = Store(tmp_path / "test.db")
    broker = MockBroker()
    runner = DcaRunner(broker, store, DcaConfig(amount_per_buy_eur=10.0, buy_every_n_days=3))

    # Primera vez: compra
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-15")
        await runner._tick()
    assert broker.buys_called == 1

    # Mismo día: no recompra
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-15")
        await runner._tick()
    assert broker.buys_called == 1

    # +1 día: skip (< 3 días)
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-16")
        await runner._tick()
    assert broker.buys_called == 1

    # +2 días: skip (todavía < 3)
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-17")
        await runner._tick()
    assert broker.buys_called == 1

    # +3 días: compra
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-18")
        await runner._tick()
    assert broker.buys_called == 2

    # +5 días desde la 2ª (i.e. ya pasaron > 3): compra
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-23")
        await runner._tick()
    assert broker.buys_called == 3


@pytest.mark.asyncio
async def test_daily_buys_with_n_equals_1(tmp_path):
    store = Store(tmp_path / "test.db")
    broker = MockBroker()
    runner = DcaRunner(broker, store, DcaConfig(amount_per_buy_eur=10.0, buy_every_n_days=1))

    for d in ["2026-05-15", "2026-05-16", "2026-05-17"]:
        with patch("src.live.runner.datetime") as mock_dt:
            mock_dt.now.return_value = _at(d)
            await runner._tick()
    assert broker.buys_called == 3


@pytest.mark.asyncio
async def test_respects_max_total_cap(tmp_path):
    store = Store(tmp_path / "test.db")
    broker = MockBroker()
    runner = DcaRunner(broker, store, DcaConfig(amount_per_buy_eur=25.0, max_total_eur=10.0))

    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-15")
        await runner._tick()
    assert broker.buys_called == 0


@pytest.mark.asyncio
async def test_resumes_after_downtime_does_not_catch_up(tmp_path):
    """Si el bot está parado N días, vuelve y compra UNA vez. No backfilea."""
    store = Store(tmp_path / "test.db")
    broker = MockBroker()
    runner = DcaRunner(broker, store, DcaConfig(amount_per_buy_eur=10.0, buy_every_n_days=3))

    # Compra inicial
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-01")
        await runner._tick()
    assert broker.buys_called == 1

    # 30 días después (varios "ciclos" perdidos): compra solo UNA vez
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-31")
        await runner._tick()
    assert broker.buys_called == 2  # NO 10
