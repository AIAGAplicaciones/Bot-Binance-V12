"""Runner 'scale-in trend': entra a TROZOS en la subida, sale entero al girarse.

Idea (validada solo en backtest, NO probada fuera de muestra — usar en PAPER):
- Decide sobre velas DIARIAS cerradas (se descarta la vela en formación).
- Régimen ALCISTA: último cierre cerrado > SMA(sma_period).
- Mientras esté alcista Y siga subiendo (cierre > cierre anterior), compra UN
  trozo por día (allocation / n_chunks) hasta completar n_chunks trozos.
- Cuando el cierre cae por debajo de la SMA -> vende TODA la posición.

Restart-safe e idempotente (igual que DcaRunner / TrendRunner):
- Un solo trozo por día gracias a `already_bought_on`.
- Los "trozos de esta pierna" se cuentan como las compras posteriores a la
  última venta -> sobrevive a reinicios sin estado en memoria.

Como decide sobre la vela diaria cerrada, da igual cada cuántos minutos corra
el tick: el objetivo solo cambia una vez al día. El check_interval solo afecta
a lo rápido que reacciona tras el cierre diario.
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
class ScaleinConfig:
    symbols: tuple[str, ...] = ("ETH/USDC", "BTC/USDC")
    sma_period: int = 50
    n_chunks: int = 5
    allocation_eur_per_symbol: float = 100.0   # objetivo total por símbolo (en la quote)
    timeframe: str = "1d"
    check_interval_minutes: int = 20
    min_qty_to_sell: float = 1e-5


@dataclass
class ScaleinRunner:
    brokers: dict          # {symbol: Broker}
    store: Store
    config: ScaleinConfig
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(self) -> None:
        modes = {b.mode for b in self.brokers.values()}
        log.info(
            "ScaleinRunner arrancado | mode=%s | símbolos=%s | SMA%d | %d trozos | "
            "€%.2f/símbolo | tf=%s | cada %d min",
            ",".join(sorted(modes)), list(self.config.symbols),
            self.config.sma_period, self.config.n_chunks,
            self.config.allocation_eur_per_symbol, self.config.timeframe,
            self.config.check_interval_minutes,
        )
        try:
            while not self._stop.is_set():
                try:
                    self._tick()
                except Exception:
                    log.exception("Error en tick del ScaleinRunner; continúa en el siguiente intervalo.")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.config.check_interval_minutes * 60)
                except asyncio.TimeoutError:
                    pass
        finally:
            log.info("ScaleinRunner detenido.")

    def stop(self) -> None:
        self._stop.set()

    def _tick(self) -> None:
        for symbol in self.config.symbols:
            try:
                self._evaluate(symbol)
            except Exception:
                log.exception("Error evaluando %s; sigo con el resto.", symbol)

    def _chunks_done_this_leg(self, symbol: str) -> int:
        """Trozos comprados desde la última venta (= pierna actual)."""
        last_sell = self.store.last_sell_date(symbol)
        buys = self.store.list_buys(symbol, limit=1000)
        if last_sell is None:
            return len(buys)
        return sum(1 for b in buys if b.buy_date_utc > last_sell)

    def _evaluate(self, symbol: str) -> None:
        broker = self.brokers[symbol]
        p = self.config

        closes = broker.fetch_closes(p.timeframe, limit=p.sma_period + 2)
        if len(closes) < p.sma_period + 1:
            log.info("SCALEIN %s: datos insuficientes (%d velas). Skip.", symbol, len(closes))
            return

        closed = closes[:-1]                      # descarta vela en formación
        window = closed[-p.sma_period:]
        sma = sum(window) / len(window)
        last_close = closed[-1]
        prev_close = closed[-2]
        uptrend = last_close > sma
        rising = last_close > prev_close

        summary = self.store.summary(symbol)
        net_qty = summary["net_qty"]
        holding = net_qty > p.min_qty_to_sell
        chunks_done = self._chunks_done_this_leg(symbol)

        log.info("SCALEIN %s: close=%.4f SMA%d=%.4f uptrend=%s rising=%s | holding=%s trozos=%d/%d",
                 symbol, last_close, p.sma_period, sma, uptrend, rising,
                 holding, chunks_done, p.n_chunks)

        today = datetime.now(timezone.utc).date()

        # ===== SALIDA: tendencia rota y tengo posición -> vender todo =====
        if holding and not uptrend:
            avg_cost = summary["avg_cost"] or 0.0
            r = broker.market_sell_base(net_qty)
            realized = r.quote_proceeds - r.fee_quote - r.base_qty * avg_cost
            self.store.record_sell(
                sell_date=today, symbol=symbol,
                base_qty=r.base_qty, fill_price=r.fill_price,
                quote_proceeds_eur=r.quote_proceeds, fee_quote=r.fee_quote,
                avg_cost_at_sale=avg_cost, realized_pnl_eur=realized,
                reason="scalein_exit", mode=broker.mode,
                order_id=r.order_id, raw_response=r.raw_response,
            )
            log.info("SCALEIN EXIT %s: vendido %.6f @ %.4f, realized P&L €%+.2f",
                     symbol, r.base_qty, r.fill_price, realized)
            return

        # ===== ENTRADA escalonada: alcista + subiendo + quedan trozos =====
        if uptrend and rising and chunks_done < p.n_chunks:
            if self.store.already_bought_on(today, symbol):
                log.info("SCALEIN %s: ya se compró hoy; un trozo por día.", symbol)
                return
            chunk_eur = p.allocation_eur_per_symbol / p.n_chunks
            r = broker.market_buy_quote(chunk_eur)
            self.store.record_buy(
                buy_date=today, symbol=symbol, quote_amount_eur=chunk_eur,
                fill_price=r.fill_price, base_qty=r.base_qty, fee_quote=r.fee_quote,
                mode=broker.mode, order_id=r.order_id, raw_response=r.raw_response,
            )
            log.info("SCALEIN ENTRY %s: trozo %d/%d comprado %.6f @ %.4f (€%.2f)",
                     symbol, chunks_done + 1, p.n_chunks, r.base_qty, r.fill_price, chunk_eur)
