"""Loop de DCA constante: chequea cada cierto tiempo si toca comprar.

Comportamiento:
- Cada `check_interval_minutes`, mira la fecha UTC actual.
- Si ya se compró hoy → skip (idempotencia diaria).
- Si la última compra fue hace menos de `buy_every_n_days` → skip.
- Si no, compra `amount_per_buy_eur` (paper o live según broker).
- Persiste en SQLite. Restart-safe.
- Cap absoluto: si la suma histórica + esta compra > `max_total_eur` → skip.

Reentrada tras downtime: si el bot está parado N días y vuelve, NO intenta
recuperar las compras perdidas. Compra una vez al volver y reanuda el ritmo
desde esa nueva fecha. Esto es lo más seguro (no acumula).
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
    amount_per_buy_eur: float = 10.0      # cuánto compra cada vez
    buy_every_n_days: int = 3             # frecuencia (1=diario, 7=semanal)
    check_interval_minutes: int = 30
    max_total_eur: float = 10_000.0       # cap histórico absoluto


class DcaRunner:
    def __init__(self, broker: Broker, store: Store, config: DcaConfig):
        self.broker = broker
        self.store = store
        self.config = config
        self._stop = asyncio.Event()

    async def start(self) -> None:
        log.info(
            "DcaRunner arrancado | mode=%s symbol=%s amount=€%.2f every=%d días",
            self.broker.mode, self.config.symbol,
            self.config.amount_per_buy_eur, self.config.buy_every_n_days,
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

        # 1) Ya compré hoy?
        if self.store.already_bought_on(today, self.config.symbol):
            log.debug("Ya comprado hoy %s para %s. Skip.", today.isoformat(), self.config.symbol)
            return

        # 2) Han pasado N días desde la última compra?
        last = self.store.last_buy_date(self.config.symbol)
        if last is not None:
            days_since = (today - last).days
            if days_since < self.config.buy_every_n_days:
                log.debug("Última compra fue hace %d días (< %d). Skip.",
                          days_since, self.config.buy_every_n_days)
                return

        # 3) Cap absoluto.
        summary = self.store.summary(self.config.symbol)
        if summary["invested"] + self.config.amount_per_buy_eur > self.config.max_total_eur:
            log.warning(
                "Cap alcanzado: invertido €%.2f + €%.2f > max €%.2f. No compro.",
                summary["invested"], self.config.amount_per_buy_eur, self.config.max_total_eur,
            )
            return

        # 4) Comprar.
        log.info("Toca comprar (%s, última fue %s). Ejecutando.",
                 today.isoformat(), last.isoformat() if last else "nunca")
        result = self.broker.market_buy_quote(self.config.amount_per_buy_eur)
        self.store.record_buy(
            buy_date=today,
            symbol=self.config.symbol,
            quote_amount_eur=self.config.amount_per_buy_eur,
            fill_price=result.fill_price,
            base_qty=result.base_qty,
            fee_quote=result.fee_quote,
            mode=self.broker.mode,
            order_id=result.order_id,
            raw_response=result.raw_response,
        )
        log.info("Compra registrada: qty %.6f @ %.4f, fee €%.4f",
                 result.base_qty, result.fill_price, result.fee_quote)
