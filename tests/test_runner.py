"""Tests del DcaRunner sin red: usa un broker mock."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.live.broker import BuyResult, SellResult
from src.live.runner import DcaConfig, DcaRunner
from src.live.store import Store


@dataclass
class MockBroker:
    mode: str = "paper"
    symbol: str = "ETH/EUR"
    last_price: float = 2400.0
    buys_called: int = 0
    sells_called: int = 0

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

    def market_sell_base(self, base_qty: float) -> SellResult:
        self.sells_called += 1
        fill = self.last_price * 0.9995
        proceeds = base_qty * fill
        return SellResult(
            fill_price=fill,
            base_qty=base_qty,
            quote_proceeds=proceeds,
            fee_quote=proceeds * 0.001,
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
async def test_take_profit_triggers_after_gain(tmp_path):
    """Compra única a 2400, precio sube +31%, TP debe dispararse. buy_every_n_days
    muy alto para aislar la lógica de TP de la de compra recurrente."""
    store = Store(tmp_path / "test.db")
    broker = MockBroker(last_price=2400.0)
    runner = DcaRunner(broker, store, DcaConfig(
        amount_per_buy_eur=10.0, buy_every_n_days=999,
        take_profit_pct=30.0, sell_pct_of_position=25.0,
        min_days_between_sells=30,
    ))

    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-01")
        await runner._tick()
    assert broker.buys_called == 1
    assert broker.sells_called == 0

    broker.last_price = 2400.0 * 1.31  # +31% sobre coste medio
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-06-05")
        await runner._tick()
    assert broker.sells_called == 1


@pytest.mark.asyncio
async def test_take_profit_skipped_if_gain_below_threshold(tmp_path):
    store = Store(tmp_path / "test.db")
    broker = MockBroker(last_price=2400.0)
    runner = DcaRunner(broker, store, DcaConfig(
        amount_per_buy_eur=10.0, take_profit_pct=30.0,
    ))

    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-01")
        await runner._tick()

    # +10% no es suficiente
    broker.last_price = 2400.0 * 1.10
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-06-05")
        await runner._tick()
    assert broker.sells_called == 0


@pytest.mark.asyncio
async def test_take_profit_respects_cooldown(tmp_path):
    store = Store(tmp_path / "test.db")
    broker = MockBroker(last_price=2400.0)
    runner = DcaRunner(broker, store, DcaConfig(
        amount_per_buy_eur=10.0, buy_every_n_days=999,
        take_profit_pct=30.0, min_days_between_sells=30,
    ))

    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-01")
        await runner._tick()

    # Día 35: sube +40%, vende
    broker.last_price = 2400.0 * 1.40
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-06-05")
        await runner._tick()
    assert broker.sells_called == 1

    # Día 40 (cooldown 30 días, falta poco): NO vende otra vez aunque sube más
    broker.last_price = 2400.0 * 1.60
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-06-10")
        await runner._tick()
    assert broker.sells_called == 1


@pytest.mark.asyncio
async def test_take_profit_disabled_when_zero(tmp_path):
    store = Store(tmp_path / "test.db")
    broker = MockBroker(last_price=2400.0)
    runner = DcaRunner(broker, store, DcaConfig(
        amount_per_buy_eur=10.0, take_profit_pct=0.0,  # OFF
    ))

    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-05-01")
        await runner._tick()

    broker.last_price = 2400.0 * 2.0  # +100% no dispara nada
    with patch("src.live.runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-06-05")
        await runner._tick()
    assert broker.sells_called == 0


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
