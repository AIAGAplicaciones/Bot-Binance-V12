"""Wrapper de Binance: misma interfaz en paper y live.

En paper mode: simula compra a precio mid + fee. Nunca llama a Binance.
En live mode: usa ccxt para ejecutar una market buy con quoteOrderQty.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import ccxt

log = logging.getLogger(__name__)


@dataclass
class BuyResult:
    fill_price: float
    base_qty: float
    fee_quote: float
    order_id: Optional[str]
    raw_response: Optional[str]


@dataclass
class SellResult:
    fill_price: float
    base_qty: float                # qty vendida realmente
    quote_proceeds: float          # eur recibidos brutos
    fee_quote: float
    order_id: Optional[str]
    raw_response: Optional[str]


class Broker:
    """Interfaz común. La instancia concreta decide paper vs live."""
    mode: str
    symbol: str

    def get_price(self) -> float: ...
    def market_buy_quote(self, quote_amount_eur: float) -> BuyResult: ...
    def market_sell_base(self, base_qty: float) -> SellResult: ...


class PaperBroker(Broker):
    mode = "paper"

    def __init__(self, symbol: str):
        self.symbol = symbol
        self._exchange = ccxt.binance({"enableRateLimit": True})
        self._exchange.load_markets()

    def get_price(self) -> float:
        ticker = self._exchange.fetch_ticker(self.symbol)
        return float(ticker["last"])

    def market_buy_quote(self, quote_amount_eur: float) -> BuyResult:
        price = self.get_price()
        fill = price * 1.0005
        fee = quote_amount_eur * 0.001
        qty = (quote_amount_eur - fee) / fill
        log.info("PAPER market_buy_quote: %s €%.2f → fill %.4f, qty %.6f, fee %.4f",
                 self.symbol, quote_amount_eur, fill, qty, fee)
        return BuyResult(fill_price=fill, base_qty=qty, fee_quote=fee, order_id=None, raw_response=None)

    def market_sell_base(self, base_qty: float) -> SellResult:
        price = self.get_price()
        fill = price * 0.9995  # slippage en sells: vendes algo más barato
        proceeds = base_qty * fill
        fee = proceeds * 0.001
        log.info("PAPER market_sell_base: %s qty %.6f → fill %.4f, proceeds €%.2f, fee %.4f",
                 self.symbol, base_qty, fill, proceeds, fee)
        return SellResult(
            fill_price=fill,
            base_qty=base_qty,
            quote_proceeds=proceeds,
            fee_quote=fee,
            order_id=None,
            raw_response=None,
        )


class LiveBroker(Broker):
    mode = "live"

    def __init__(self, symbol: str, api_key: str, api_secret: str):
        self.symbol = symbol
        self._exchange = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        self._exchange.load_markets()
        if self.symbol not in self._exchange.markets:
            raise ValueError(f"Símbolo {self.symbol} no encontrado en Binance Spot.")

    def get_price(self) -> float:
        return float(self._exchange.fetch_ticker(self.symbol)["last"])

    def market_buy_quote(self, quote_amount_eur: float) -> BuyResult:
        market = self._exchange.market(self.symbol)
        min_notional = float(market.get("limits", {}).get("cost", {}).get("min") or 0)
        if quote_amount_eur < min_notional:
            raise ValueError(
                f"Cantidad €{quote_amount_eur:.2f} por debajo del minNotional "
                f"de Binance para {self.symbol} (€{min_notional:.2f})."
            )

        # ccxt expone quoteOrderQty en Binance vía createOrder con params.
        order = self._exchange.create_order(
            symbol=self.symbol,
            type="market",
            side="buy",
            amount=None,
            params={"quoteOrderQty": quote_amount_eur},
        )

        # Parse fills: ccxt normaliza pero no siempre rellena todo.
        filled = float(order.get("filled") or 0)
        cost = float(order.get("cost") or quote_amount_eur)
        fee_quote = 0.0
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        for f in fees:
            if not f:
                continue
            if f.get("currency") == self._exchange.market(self.symbol)["quote"]:
                fee_quote += float(f.get("cost") or 0)
            else:
                # Fee en otra moneda (e.g. BNB). Estimación: 0 — no convertimos.
                pass

        fill_price = cost / filled if filled > 0 else self.get_price()
        log.info("LIVE market_buy_quote: %s €%.2f → fill %.4f, qty %.6f, fee €%.4f (order %s)",
                 self.symbol, quote_amount_eur, fill_price, filled, fee_quote, order.get("id"))
        return BuyResult(
            fill_price=fill_price,
            base_qty=filled,
            fee_quote=fee_quote,
            order_id=str(order.get("id") or ""),
            raw_response=json.dumps(order, default=str),
        )

    def market_sell_base(self, base_qty: float) -> SellResult:
        order = self._exchange.create_order(
            symbol=self.symbol,
            type="market",
            side="sell",
            amount=base_qty,
        )
        filled = float(order.get("filled") or base_qty)
        cost = float(order.get("cost") or 0)
        fee_quote = 0.0
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        for f in fees:
            if not f:
                continue
            if f.get("currency") == self._exchange.market(self.symbol)["quote"]:
                fee_quote += float(f.get("cost") or 0)
        fill_price = cost / filled if filled > 0 else self.get_price()
        log.info("LIVE market_sell_base: %s qty %.6f → fill %.4f, proceeds €%.2f, fee €%.4f (order %s)",
                 self.symbol, filled, fill_price, cost, fee_quote, order.get("id"))
        return SellResult(
            fill_price=fill_price,
            base_qty=filled,
            quote_proceeds=cost,
            fee_quote=fee_quote,
            order_id=str(order.get("id") or ""),
            raw_response=json.dumps(order, default=str),
        )


def make_broker(symbol: str) -> Broker:
    """Decide automáticamente entre paper y live según FORCE_PAPER y las keys."""
    force_paper = os.getenv("FORCE_PAPER", "true").lower() in ("1", "true", "yes")
    api_key = os.getenv("BINANCE_API_KEY") or ""
    api_secret = os.getenv("BINANCE_API_SECRET") or ""

    if force_paper or not (api_key and api_secret):
        if force_paper:
            log.warning("FORCE_PAPER=true: usando PaperBroker (no se envían órdenes reales).")
        else:
            log.warning("BINANCE_API_KEY/SECRET no configuradas: usando PaperBroker.")
        return PaperBroker(symbol)

    log.warning("Modo LIVE activo. Las compras serán reales. Símbolo: %s", symbol)
    return LiveBroker(symbol, api_key, api_secret)
