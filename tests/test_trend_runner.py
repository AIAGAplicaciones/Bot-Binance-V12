"""Tests del TrendRunner sin red: broker mock con fetch_closes configurable."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from src.live.broker import BuyResult, SellResult
from src.live.store import Store
from src.live.trend_runner import TrendConfig, TrendRunner


class MockBroker:
    def __init__(self, symbol: str, price: float = 100.0):
        self.mode = "paper"
        self.symbol = symbol
        self.price = price
        self.closes: list[float] = []
        self.buys_called = 0
        self.sells_called = 0

    def get_price(self) -> float:
        return self.price

    def fetch_closes(self, timeframe: str, limit: int) -> list[float]:
        return self.closes

    def market_buy_quote(self, quote_amount_eur: float) -> BuyResult:
        self.buys_called += 1
        fill = self.price
        return BuyResult(fill_price=fill, base_qty=quote_amount_eur / fill,
                         fee_quote=quote_amount_eur * 0.001, order_id=None, raw_response=None)

    def market_sell_base(self, base_qty: float) -> SellResult:
        self.sells_called += 1
        fill = self.price
        proceeds = base_qty * fill
        return SellResult(fill_price=fill, base_qty=base_qty, quote_proceeds=proceeds,
                          fee_quote=proceeds * 0.001, order_id=None, raw_response=None)


def _at(d: str):
    y, m, dd = map(int, d.split("-"))
    return datetime(y, m, dd, 12, 0, tzinfo=timezone.utc)


def _runner(tmp_path, symbol="ETH/EUR", sma_period=3):
    store = Store(tmp_path / "trend.db")
    broker = MockBroker(symbol)
    cfg = TrendConfig(symbols=(symbol,), sma_period=sma_period, allocation_eur_per_symbol=200.0)
    return TrendRunner(brokers={symbol: broker}, store=store, config=cfg), broker, store


def _tick_at(runner, date):
    with patch("src.live.trend_runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at(date)
        runner._tick()


def test_enters_long_when_close_above_sma(tmp_path):
    runner, broker, _ = _runner(tmp_path)
    # closed = [10,11,15] (último es forming, se descarta). SMA=12, close=15>12 -> LARGO
    broker.closes = [10, 11, 15, 99]
    _tick_at(runner, "2026-01-10")
    assert broker.buys_called == 1
    assert broker.sells_called == 0


def test_stays_flat_when_close_below_sma(tmp_path):
    runner, broker, _ = _runner(tmp_path)
    # closed = [15,11,9]. SMA=11.67, close=9<SMA -> FUERA
    broker.closes = [15, 11, 9, 1]
    _tick_at(runner, "2026-01-10")
    assert broker.buys_called == 0


def test_does_not_rebuy_if_already_holding(tmp_path):
    runner, broker, _ = _runner(tmp_path)
    broker.closes = [10, 11, 15, 99]
    _tick_at(runner, "2026-01-10")
    assert broker.buys_called == 1
    # Mismo objetivo LARGO al día siguiente: ya tiene posición -> no recompra
    _tick_at(runner, "2026-01-11")
    assert broker.buys_called == 1


def test_exits_when_close_drops_below_sma(tmp_path):
    runner, broker, _ = _runner(tmp_path)
    # Entra
    broker.closes = [10, 11, 15, 99]
    _tick_at(runner, "2026-01-10")
    assert broker.buys_called == 1
    # Cae por debajo de la SMA -> vende toda la posición
    broker.closes = [15, 12, 8, 1]
    _tick_at(runner, "2026-01-20")
    assert broker.sells_called == 1


def test_no_exit_if_flat(tmp_path):
    runner, broker, _ = _runner(tmp_path)
    # Objetivo FUERA sin posición previa: no vende nada
    broker.closes = [15, 12, 8, 1]
    _tick_at(runner, "2026-01-10")
    assert broker.sells_called == 0
    assert broker.buys_called == 0


def test_restart_safe_reconciles_from_store(tmp_path):
    """Tras entrar, un runner NUEVO sobre el mismo store no recompra (lee el estado)."""
    runner, broker, store = _runner(tmp_path)
    broker.closes = [10, 11, 15, 99]
    _tick_at(runner, "2026-01-10")
    assert broker.buys_called == 1

    # Simula reinicio: nuevo runner/broker, mismo store y misma DB.
    broker2 = MockBroker("ETH/EUR")
    broker2.closes = [10, 11, 15, 99]  # sigue en objetivo LARGO
    cfg = TrendConfig(symbols=("ETH/EUR",), sma_period=3, allocation_eur_per_symbol=200.0)
    runner2 = TrendRunner(brokers={"ETH/EUR": broker2}, store=store, config=cfg)
    with patch("src.live.trend_runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-01-12")
        runner2._tick()
    assert broker2.buys_called == 0  # ya estaba dentro segun el store


def test_insufficient_data_skips(tmp_path):
    runner, broker, _ = _runner(tmp_path, sma_period=50)
    broker.closes = [100, 101, 102]  # muy pocas velas para SMA50
    _tick_at(runner, "2026-01-10")
    assert broker.buys_called == 0


def test_two_symbols_independent(tmp_path):
    store = Store(tmp_path / "trend.db")
    eth = MockBroker("ETH/EUR")
    btc = MockBroker("BTC/EUR")
    eth.closes = [10, 11, 15, 99]   # LARGO
    btc.closes = [15, 12, 8, 1]     # FUERA
    cfg = TrendConfig(symbols=("ETH/EUR", "BTC/EUR"), sma_period=3, allocation_eur_per_symbol=200.0)
    runner = TrendRunner(brokers={"ETH/EUR": eth, "BTC/EUR": btc}, store=store, config=cfg)
    with patch("src.live.trend_runner.datetime") as mock_dt:
        mock_dt.now.return_value = _at("2026-01-10")
        runner._tick()
    assert eth.buys_called == 1   # ETH entra
    assert btc.buys_called == 0   # BTC se queda fuera
