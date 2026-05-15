"""Tests del DcaRunner sin red: usa un broker mock."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
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


@pytest.mark.asyncio
async def test_buys_only_on_buy_weekday(tmp_path):
    store = Store(tmp_path / "test.db")
    broker = MockBroker()
    runner = DcaRunner(broker, store, DcaConfig(symbol="ETH/EUR", weekly_eur=25.0, buy_weekday=0))

    # Lunes (weekday=0)
    monday = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = monday
        await runner._tick()
    assert broker.buys_called == 1
    assert store.already_bought_on(monday.date(), "ETH/EUR")

    # Tras buy: no recompra el mismo día
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = monday
        await runner._tick()
    assert broker.buys_called == 1

    # Martes: skip
    tuesday = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = tuesday
        await runner._tick()
    assert broker.buys_called == 1


@pytest.mark.asyncio
async def test_respects_max_total_cap(tmp_path):
    store = Store(tmp_path / "test.db")
    broker = MockBroker()
    runner = DcaRunner(broker, store, DcaConfig(weekly_eur=25.0, max_total_eur=10.0))  # cap muy bajo

    monday = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = monday
        await runner._tick()
    # 25€ > 10€ cap, no compra
    assert broker.buys_called == 0
