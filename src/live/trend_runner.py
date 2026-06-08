"""Runner de tendencia diversificada (SMA-N sobre una cesta de símbolos).

Estrategia (validada en backtest, ver strategy_lab.py / portfolio_strategy.py):
- Para cada símbolo, calcula la SMA(sma_period) sobre cierres DIARIOS cerrados.
- Si el último cierre cerrado > SMA  -> objetivo = LARGO.
- Si el último cierre cerrado < SMA  -> objetivo = FUERA (efectivo).
- Capital repartido por igual: cada símbolo recibe `allocation_eur_per_symbol`.

DISEÑO BASADO EN ESTADO OBJETIVO (no en eventos de cruce):
Cada tick reconcilia "¿debería estar dentro?" con "¿estoy dentro?" (según el
net_qty del store). Esto lo hace idempotente y restart-safe sin necesidad de
recordar el cruce exacto: si el bot se reinicia, simplemente vuelve a comparar
precio vs SMA y actúa solo si el estado real difiere del objetivo.

- objetivo LARGO y NO tengo posición -> market buy de la asignación.
- objetivo FUERA y SÍ tengo posición -> market sell de toda la posición.
- en cualquier otro caso -> no hace nada.

Nota: las decisiones se toman sobre la última vela DIARIA CERRADA (se descarta
la vela en formación) para replicar el backtest, que decide al cierre de cada
día. Por eso da igual cada cuánto corra el tick: el objetivo solo cambia una
vez al día.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .broker import Broker
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class TrendConfig:
    symbols: tuple[str, ...] = ("ETH/EUR", "BTC/EUR")
    sma_period: int = 50
    allocation_eur_per_symbol: float = 200.0
    timeframe: str = "1d"
    check_interval_minutes: int = 60
    min_qty_to_sell: float = 1e-5


@dataclass
class TrendRunner:
    brokers: dict          # {symbol: Broker}
    store: Store
    config: TrendConfig
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self) -> None:
        modes = {b.mode for b in self.brokers.values()}
        log.info(
            "TrendRunner arrancado | mode=%s | símbolos=%s | SMA%d | €%.2f/símbolo | tf=%s",
            ",".join(sorted(modes)), list(self.config.symbols),
            self.config.sma_period, self.config.allocation_eur_per_symbol,
            self.config.timeframe,
        )
        try:
            while not self._stop.is_set():
                try:
                    self._tick()
                except Exception:
                    log.exception("Error en tick del TrendRunner; continúa en el siguiente intervalo.")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.config.check_interval_minutes * 60)
                except asyncio.TimeoutError:
                    pass
        finally:
            log.info("TrendRunner detenido.")

    def stop(self) -> None:
        self._stop.set()

    def _tick(self) -> None:
        for symbol in self.config.symbols:
            try:
                self._evaluate(symbol)
            except Exception:
                log.exception("Error evaluando %s; sigo con el resto.", symbol)

    def _evaluate(self, symbol: str) -> None:
        broker = self.brokers[symbol]
        p = self.config

        # Necesitamos sma_period cierres CERRADOS. Pedimos +2 y descartamos la
        # última vela (aún en formación).
        closes = broker.fetch_closes(p.timeframe, limit=p.sma_period + 2)
        if len(closes) < p.sma_period + 1:
            log.info("TREND %s: datos insuficientes (%d velas, faltan para SMA%d). Skip.",
                     symbol, len(closes), p.sma_period)
            return

        closed = closes[:-1]                      # descarta vela en formación
        window = closed[-p.sma_period:]
        sma = sum(window) / len(window)
        last_close = closed[-1]
        desired_long = last_close > sma

        summary = self.store.summary(symbol)
        net_qty = summary["net_qty"]
        holding = net_qty > p.min_qty_to_sell

        log.info("TREND %s: close=%.4f SMA%d=%.4f -> objetivo=%s | holding=%s (qty=%.6f)",
                 symbol, last_close, p.sma_period, sma,
                 "LARGO" if desired_long else "FUERA", holding, net_qty)

        today = datetime.now(timezone.utc).date()

        if desired_long and not holding:
            if self.store.already_bought_on(today, symbol):
                log.info("TREND %s: ya se compró hoy; no reentro.", symbol)
                return
            r = broker.market_buy_quote(p.allocation_eur_per_symbol)
            self.store.record_buy(
                buy_date=today, symbol=symbol,
                quote_amount_eur=p.allocation_eur_per_symbol,
                fill_price=r.fill_price, base_qty=r.base_qty,
                fee_quote=r.fee_quote, mode=broker.mode,
                order_id=r.order_id, raw_response=r.raw_response,
            )
            log.info("TREND ENTRY %s: comprado %.6f @ %.4f (€%.2f, fee €%.4f)",
                     symbol, r.base_qty, r.fill_price, p.allocation_eur_per_symbol, r.fee_quote)

        elif not desired_long and holding:
            avg_cost = summary["avg_cost"] or 0.0
            r = broker.market_sell_base(net_qty)
            realized = r.quote_proceeds - r.fee_quote - r.base_qty * avg_cost
            self.store.record_sell(
                sell_date=today, symbol=symbol,
                base_qty=r.base_qty, fill_price=r.fill_price,
                quote_proceeds_eur=r.quote_proceeds, fee_quote=r.fee_quote,
                avg_cost_at_sale=avg_cost, realized_pnl_eur=realized,
                reason="trend_exit", mode=broker.mode,
                order_id=r.order_id, raw_response=r.raw_response,
            )
            log.info("TREND EXIT %s: vendido %.6f @ %.4f, realized P&L €%+.2f",
                     symbol, r.base_qty, r.fill_price, realized)
