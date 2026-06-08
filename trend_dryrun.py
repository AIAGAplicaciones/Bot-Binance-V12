"""Dry-run del TrendRunner en paper contra Binance real: un solo tick.

Verifica el circuito completo: fetch de velas diarias reales -> SMA -> decision
-> compra/venta paper -> persistencia en SQLite. NO envia ordenes reales.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")

from src.live.broker import PaperBroker
from src.live.store import Store
from src.live.trend_runner import TrendConfig, TrendRunner

SYMBOLS = ("ETH/EUR", "BTC/EUR")
SMA = 50

brokers = {s: PaperBroker(s) for s in SYMBOLS}
store = Store(Path(tempfile.gettempdir()) / "trend_dryrun.db")
cfg = TrendConfig(symbols=SYMBOLS, sma_period=SMA, allocation_eur_per_symbol=200.0)
runner = TrendRunner(brokers=brokers, store=store, config=cfg)

print("\n=== TICK 1 (decision inicial) ===")
runner._tick()

print("\n=== Estado tras el tick ===")
for s in SYMBOLS:
    summ = store.summary(s)
    print(f"{s}: net_qty={summ['net_qty']:.6f}  invertido=€{summ['invested']:.2f}  "
          f"coste_medio={summ['avg_cost']}  realized=€{summ['realized_pnl']:.2f}")

print("\n=== TICK 2 (debe ser idempotente: no repite si el objetivo no cambia) ===")
runner._tick()
for s in SYMBOLS:
    summ = store.summary(s)
    print(f"{s}: n_compras={summ['n']}  n_ventas={summ['n_sells']}  net_qty={summ['net_qty']:.6f}")
