"""Loop de DCA constante: chequea cada cierto tiempo si toca comprar.

Comportamiento:
- Cada `check_interval_minutes`, mira la fecha UTC actual.
- Si es el día de la semana configurado y aún no se ha comprado hoy → compra.
- Si es paper, simula. Si es live, envía market buy a Binance.
- Persiste en SQLite. Usa `(buy_date_utc, symbol)` como clave única para
  garantizar idempotencia: aunque el bot reinicie, no compra dos veces el
  mismo día.
- Tiene un cap absoluto de gasto (`max_total_eur`): si la suma histórica supera
  este número, deja de comprar y solo loggea.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .broker import Broker
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class DcaConfig:
    symbol: str = "ETH/EUR"
    weekly_eur: float = 25.0
    buy_weekday: int = 0          # 0=lunes UTC
    check_interval_minutes: int = 30
    max_total_eur: float = 10_000.0   # cap absoluto histórico


class DcaRunner:
    def __init__(self, broker: Broker, store: Store, config: DcaConfig):
        self.broker = broker
        self.store = store
        self.config = config
        self._stop = asyncio.Event()

    async def start(self) -> None:
        log.info(
            "DcaRunner arrancado | mode=%s symbol=%s weekly=€%.2f weekday=%d (UTC)",
            self.broker.mode, self.config.symbol, self.config.weekly_eur, self.config.buy_weekday,
        )
        try:
            while not self._stop.is_set():
                try:
                    await self._tick()
                except Exception:
                    log.exception("Error en tick del runner; continúa en el siguiente intervalo.")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.config.check_interval_minutes * 60)
                except asyncio.TimeoutError:
                    pass
        finally:
            log.info("DcaRunner detenido.")

    def stop(self) -> None:
        self._stop.set()

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        today = now.date()

        if today.weekday() != self.config.buy_weekday:
            log.debug("Hoy %s (weekday %d) no es buy day (%d). Skip.",
                      today.isoformat(), today.weekday(), self.config.buy_weekday)
            return

        if self.store.already_bought_on(today, self.config.symbol):
            log.debug("Ya comprado hoy %s para %s. Skip.", today.isoformat(), self.config.symbol)
            return

        # Cap absoluto.
        summary = self.store.summary(self.config.symbol)
        if summary["invested"] + self.config.weekly_eur > self.config.max_total_eur:
            log.warning(
                "Cap alcanzado: invertido €%.2f + €%.2f > max €%.2f. No compro.",
                summary["invested"], self.config.weekly_eur, self.config.max_total_eur,
            )
            return

        log.info("Es día de compra (%s, weekday %d) y no hay buy registrada. Ejecutando.",
                 today.isoformat(), today.weekday())
        result = self.broker.market_buy_quote(self.config.weekly_eur)
        self.store.record_buy(
            buy_date=today,
            symbol=self.config.symbol,
            quote_amount_eur=self.config.weekly_eur,
            fill_price=result.fill_price,
            base_qty=result.base_qty,
            fee_quote=result.fee_quote,
            mode=self.broker.mode,
            order_id=result.order_id,
            raw_response=result.raw_response,
        )
        log.info("Compra registrada: qty %.6f @ %.4f, fee €%.4f", result.base_qty, result.fill_price, result.fee_quote)
