"""Tests del Store: idempotencia + summary."""
from __future__ import annotations

from datetime import date

import pytest

from src.live.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


def test_record_buy_and_idempotency(store):
    today = date(2026, 5, 11)
    assert not store.already_bought_on(today, "ETH/EUR")

    store.record_buy(
        buy_date=today, symbol="ETH/EUR",
        quote_amount_eur=25.0, fill_price=2400.0, base_qty=0.0104,
        fee_quote=0.025, mode="paper", order_id=None, raw_response=None,
    )

    assert store.already_bought_on(today, "ETH/EUR")
    # Otro símbolo el mismo día sí permitido
    assert not store.already_bought_on(today, "BTC/EUR")


def test_double_buy_same_day_same_symbol_raises(store):
    today = date(2026, 5, 11)
    store.record_buy(
        buy_date=today, symbol="ETH/EUR",
        quote_amount_eur=25.0, fill_price=2400.0, base_qty=0.0104,
        fee_quote=0.025, mode="paper", order_id=None, raw_response=None,
    )
    with pytest.raises(Exception):  # IntegrityError o derivada
        store.record_buy(
            buy_date=today, symbol="ETH/EUR",
            quote_amount_eur=25.0, fill_price=2400.0, base_qty=0.0104,
            fee_quote=0.025, mode="paper", order_id=None, raw_response=None,
        )


def test_summary_aggregates_correctly(store):
    for d, qty in [(date(2026, 5, 4), 0.01), (date(2026, 5, 11), 0.012), (date(2026, 5, 18), 0.011)]:
        store.record_buy(
            buy_date=d, symbol="ETH/EUR",
            quote_amount_eur=25.0, fill_price=25.0 / qty, base_qty=qty,
            fee_quote=0.025, mode="paper", order_id=None, raw_response=None,
        )
    s = store.summary("ETH/EUR")
    assert s["n"] == 3
    assert s["invested"] == pytest.approx(75.0)
    assert s["qty"] == pytest.approx(0.033)
    assert s["avg_cost"] == pytest.approx(75.0 / 0.033)


def test_state_kv(store):
    assert store.get_state("foo") is None
    store.set_state("foo", "bar")
    assert store.get_state("foo") == "bar"
    store.set_state("foo", "baz")
    assert store.get_state("foo") == "baz"
