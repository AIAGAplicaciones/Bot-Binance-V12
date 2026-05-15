"""Loop de DCA + take profit opcional.

Comportamiento por tick (cada `check_interval_minutes`):

1. **Buy gate**: si ya se compró hoy o llevamos < `buy_every_n_days` desde la
   última compra → skip buy.
2. **Cap absoluto**: si `invested + amount_per_buy > max_total_eur` → skip buy.
3. **Buy**: market buy `amount_per_buy_eur` (paper o live según broker).
4. **Take profit** (si configurado): si el precio actual está ≥ `take_profit_pct`
   por encima del coste medio Y han pasado ≥ `min_days_between_sells` desde la
   última venta → vender `sell_pct_of_position` % de la posición.

Restart-safe: usa SQLite para idempotencia diaria y para cooldowns. Si el bot
se reinicia, no compra ni vende dos veces el mismo día.

Nota matemática: el coste medio se calcula sobre las compras brutas
(invested / bought_qty) y NO se reduce con las ventas. Esto significa que tras
una venta parcial, las siguientes compras pueden subir el coste medio si se
hacen a precio mayor, lo que hace progresivamente más difícil disparar
take profit. Es un freno natural al "over-selling".
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
    amount_per_buy_eur: float = 10.0
    buy_every_n_days: int = 3
    check_interval_minutes: int = 30
    max_total_eur: float = 10_000.0
    # Take profit. Para deshabilitar pon take_profit_pct = 0.
    take_profit_pct: float = 0.0          # % sobre coste medio para disparar
    sell_pct_of_position: float = 25.0    # % de la posición a vender en TP
    min_days_between_sells: int = 30      # cooldown
    min_qty_to_sell: float = 1e-5         # evita ventas absurdamente pequeñas


class DcaRunner:
    def __init__(self, broker: Broker, store: Store, config: DcaConfig):
        self.broker = broker
        self.store = store
        self.config = config
        self._stop = asyncio.Event()

    async def start(self) -> None:
        tp_state = (f"TP ON @ {self.config.take_profit_pct:.1f}%, vende "
                    f"{self.config.sell_pct_of_position:.0f}%" if self.config.take_profit_pct > 0
                    else "TP OFF (solo acumular)")
        log.info(
            "DcaRunner arrancado | mode=%s symbol=%s amount=€%.2f every=%d días | %s",
            self.broker.mode, self.config.symbol,
            self.config.amount_per_buy_eur, self.config.buy_every_n_days, tp_state,
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

        # ============ BUY ============
        bought_this_tick = False
        if not self.store.already_bought_on(today, self.config.symbol):
            last_buy = self.store.last_buy_date(self.config.symbol)
            ok = True
            if last_buy is not None:
                days_since = (today - last_buy).days
                if days_since < self.config.buy_every_n_days:
                    log.debug("Última compra hace %d días (< %d). Skip buy.",
                              days_since, self.config.buy_every_n_days)
                    ok = False

            if ok:
                summary = self.store.summary(self.config.symbol)
                if summary["invested"] + self.config.amount_per_buy_eur > self.config.max_total_eur:
                    log.warning("Cap alcanzado: €%.2f + €%.2f > €%.2f. No compro.",
                                summary["invested"], self.config.amount_per_buy_eur, self.config.max_total_eur)
                else:
                    log.info("Toca comprar (%s, última %s). Ejecutando.",
                             today.isoformat(), last_buy.isoformat() if last_buy else "nunca")
                    r = self.broker.market_buy_quote(self.config.amount_per_buy_eur)
                    self.store.record_buy(
                        buy_date=today, symbol=self.config.symbol,
                        quote_amount_eur=self.config.amount_per_buy_eur,
                        fill_price=r.fill_price, base_qty=r.base_qty,
                        fee_quote=r.fee_quote, mode=self.broker.mode,
                        order_id=r.order_id, raw_response=r.raw_response,
                    )
                    bought_this_tick = True
                    log.info("Compra registrada: qty %.6f @ %.4f, fee €%.4f",
                             r.base_qty, r.fill_price, r.fee_quote)

        # ============ TAKE PROFIT ============
        if self.config.take_profit_pct > 0:
            await self._maybe_take_profit(today)

    async def _maybe_take_profit(self, today) -> None:
        summary = self.store.summary(self.config.symbol)
        net_qty = summary["net_qty"]
        avg_cost = summary["avg_cost"]
        if net_qty <= self.config.min_qty_to_sell or avg_cost is None:
            return

        last_sell = self.store.last_sell_date(self.config.symbol)
        if last_sell is not None:
            days_since = (today - last_sell).days
            if days_since < self.config.min_days_between_sells:
                log.debug("Última venta hace %d días (< %d). Skip TP check.",
                          days_since, self.config.min_days_between_sells)
                return

        current_price = self.broker.get_price()
        gain_pct = (current_price / avg_cost - 1) * 100
        if gain_pct < self.config.take_profit_pct:
            log.debug("Gain %.2f%% < %.2f%%. No TP.", gain_pct, self.config.take_profit_pct)
            return

        sell_qty = net_qty * self.config.sell_pct_of_position / 100
        if sell_qty < self.config.min_qty_to_sell:
            log.debug("Sell qty %.8f por debajo del mínimo. Skip.", sell_qty)
            return

        log.info(
            "TAKE PROFIT: precio %.4f vs coste medio %.4f (gain %.2f%% >= %.2f%%). "
            "Vendiendo %.6f de %.6f (%.0f%%).",
            current_price, avg_cost, gain_pct, self.config.take_profit_pct,
            sell_qty, net_qty, self.config.sell_pct_of_position,
        )
        r = self.broker.market_sell_base(sell_qty)
        realized = r.quote_proceeds - r.fee_quote - r.base_qty * avg_cost
        self.store.record_sell(
            sell_date=today, symbol=self.config.symbol,
            base_qty=r.base_qty, fill_price=r.fill_price,
            quote_proceeds_eur=r.quote_proceeds, fee_quote=r.fee_quote,
            avg_cost_at_sale=avg_cost, realized_pnl_eur=realized,
            reason="take_profit", mode=self.broker.mode,
            order_id=r.order_id, raw_response=r.raw_response,
        )
        log.info("Venta registrada: realized P&L €%+.2f", realized)
