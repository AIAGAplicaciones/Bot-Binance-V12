"""Tests del renderizador del dashboard (función pura, sin red)."""
from __future__ import annotations

from src.live.server import _render_dashboard, _eur, _pnl_span


def test_eur_formatting():
    assert _eur(10) == "€10.00"
    assert _eur(1234.5) == "€1,234.50"
    assert _eur(None) == "—"


def test_pnl_span_color():
    assert "pos" in _pnl_span(5.0) and "+" in _pnl_span(5.0)
    assert "neg" in _pnl_span(-5.0)
    assert "muted" in _pnl_span(None)


def _dca_data():
    return {
        "mode": "paper", "runner": "dca",
        "config": {"symbol": "ETH/EUR", "amount_per_buy_eur": 10.0,
                   "buy_every_n_days": 3, "take_profit_pct": 30.0},
        "summary": {"n": 26, "n_sells": 0, "invested": 260.0,
                    "net_qty": 0.153, "avg_cost": 1698.8, "realized_pnl": 0},
        "current_price": 1433.87, "position_value_eur": 219.45,
        "unrealized_pnl_eur": -40.55, "realized_pnl_eur": 0, "total_pnl_eur": -40.55,
        "last_buys": [{"date": "2026-06-11", "price": 1412.08, "qty": 0.00707, "amount_eur": 10.0}],
        "last_sells": [],
    }


def test_render_dca_dashboard():
    html = _render_dashboard(_dca_data())
    assert "<!doctype html>" in html
    assert "ETH/EUR" in html
    assert "PAPER" in html              # modo paper mostrado
    assert "Modo DCA" in html
    assert "2026-06-11" in html         # la compra aparece
    assert "Sin ventas" in html         # no hay ventas
    assert "neg" in html                # P&L negativo coloreado


def test_render_trend_dashboard():
    data = {
        "mode": "paper", "runner": "trend",
        "config": {"symbols": ["ETH/EUR", "BTC/EUR"], "sma_period": 50,
                   "allocation_eur_per_symbol": 200.0, "timeframe": "1d"},
        "total_pnl_eur": 12.3,
        "symbols": {
            "ETH/EUR": {"symbol": "ETH/EUR", "summary": {"n": 2, "n_sells": 0, "invested": 200.0,
                        "net_qty": 0.1, "avg_cost": 1500.0, "realized_pnl": 0},
                        "current_price": 1600.0, "position_value_eur": 160.0,
                        "unrealized_pnl_eur": 10.0, "realized_pnl_eur": 0,
                        "last_buys": [], "last_sells": []},
            "BTC/EUR": {"symbol": "BTC/EUR", "summary": {"n": 0, "n_sells": 0, "invested": 0,
                        "net_qty": 0, "avg_cost": None, "realized_pnl": 0},
                        "current_price": 55000.0, "position_value_eur": 0,
                        "unrealized_pnl_eur": None, "realized_pnl_eur": 0,
                        "last_buys": [], "last_sells": []},
        },
    }
    html = _render_dashboard(data)
    assert "Modo TREND" in html
    assert "ETH/EUR" in html and "BTC/EUR" in html
    assert "Sin compras" in html        # BTC sin compras


def test_render_live_mode_warning():
    data = _dca_data()
    data["mode"] = "live"
    html = _render_dashboard(data)
    assert "LIVE" in html and "DINERO REAL" in html
