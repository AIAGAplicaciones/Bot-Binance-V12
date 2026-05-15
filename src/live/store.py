"""Persistencia mínima en SQLite (stdlib, sin SQLAlchemy).

Tablas:
- buys: cada compra ejecutada (real o paper), una fila por compra.
- bot_state: pares clave/valor para estado del runner (e.g. última fecha
  ejecutada, para idempotencia).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS buys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    executed_at_utc TEXT NOT NULL,        -- ISO8601 UTC
    buy_date_utc TEXT NOT NULL,           -- YYYY-MM-DD UTC (para idempotencia diaria)
    symbol TEXT NOT NULL,
    quote_amount_eur REAL NOT NULL,
    fill_price REAL NOT NULL,
    base_qty REAL NOT NULL,
    fee_quote REAL NOT NULL DEFAULT 0.0,
    mode TEXT NOT NULL,                   -- 'paper' o 'live'
    order_id TEXT,
    raw_response TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_buys_date_symbol
    ON buys (buy_date_utc, symbol);

CREATE TABLE IF NOT EXISTS sells (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    executed_at_utc TEXT NOT NULL,
    sell_date_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    base_qty REAL NOT NULL,
    fill_price REAL NOT NULL,
    quote_proceeds_eur REAL NOT NULL,     -- antes de fees
    fee_quote REAL NOT NULL DEFAULT 0.0,
    avg_cost_at_sale REAL NOT NULL,       -- coste medio en el momento de la venta
    realized_pnl_eur REAL NOT NULL,       -- proceeds - fee - (qty * avg_cost)
    reason TEXT NOT NULL,                 -- 'take_profit' u otra
    mode TEXT NOT NULL,
    order_id TEXT,
    raw_response TEXT
);

CREATE TABLE IF NOT EXISTS bot_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
"""


@dataclass
class Buy:
    id: int
    executed_at_utc: datetime
    buy_date_utc: date
    symbol: str
    quote_amount_eur: float
    fill_price: float
    base_qty: float
    fee_quote: float
    mode: str
    order_id: Optional[str]
    raw_response: Optional[str]


@dataclass
class Sell:
    id: int
    executed_at_utc: datetime
    sell_date_utc: date
    symbol: str
    base_qty: float
    fill_price: float
    quote_proceeds_eur: float
    fee_quote: float
    avg_cost_at_sale: float
    realized_pnl_eur: float
    reason: str
    mode: str
    order_id: Optional[str]
    raw_response: Optional[str]


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def already_bought_on(self, buy_date: date, symbol: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "SELECT 1 FROM buys WHERE buy_date_utc = ? AND symbol = ? LIMIT 1",
                (buy_date.isoformat(), symbol),
            )
            return cur.fetchone() is not None

    def last_buy_date(self, symbol: str) -> Optional[date]:
        """Última fecha en que se compró el símbolo. None si nunca."""
        with self._conn() as c:
            cur = c.execute(
                "SELECT MAX(buy_date_utc) AS d FROM buys WHERE symbol = ?",
                (symbol,),
            )
            row = cur.fetchone()
            if not row or not row["d"]:
                return None
            return date.fromisoformat(row["d"])

    def record_buy(
        self,
        buy_date: date,
        symbol: str,
        quote_amount_eur: float,
        fill_price: float,
        base_qty: float,
        fee_quote: float,
        mode: str,
        order_id: Optional[str],
        raw_response: Optional[str],
    ) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO buys
                (executed_at_utc, buy_date_utc, symbol, quote_amount_eur,
                 fill_price, base_qty, fee_quote, mode, order_id, raw_response)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    buy_date.isoformat(),
                    symbol,
                    quote_amount_eur,
                    fill_price,
                    base_qty,
                    fee_quote,
                    mode,
                    order_id,
                    raw_response,
                ),
            )

    def record_sell(
        self,
        sell_date: date,
        symbol: str,
        base_qty: float,
        fill_price: float,
        quote_proceeds_eur: float,
        fee_quote: float,
        avg_cost_at_sale: float,
        realized_pnl_eur: float,
        reason: str,
        mode: str,
        order_id: Optional[str],
        raw_response: Optional[str],
    ) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO sells
                (executed_at_utc, sell_date_utc, symbol, base_qty, fill_price,
                 quote_proceeds_eur, fee_quote, avg_cost_at_sale, realized_pnl_eur,
                 reason, mode, order_id, raw_response)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    sell_date.isoformat(),
                    symbol,
                    base_qty,
                    fill_price,
                    quote_proceeds_eur,
                    fee_quote,
                    avg_cost_at_sale,
                    realized_pnl_eur,
                    reason,
                    mode,
                    order_id,
                    raw_response,
                ),
            )

    def last_sell_date(self, symbol: str) -> Optional[date]:
        with self._conn() as c:
            cur = c.execute(
                "SELECT MAX(sell_date_utc) AS d FROM sells WHERE symbol = ?",
                (symbol,),
            )
            row = cur.fetchone()
            if not row or not row["d"]:
                return None
            return date.fromisoformat(row["d"])

    def list_sells(self, symbol: Optional[str] = None, limit: int = 100) -> list[Sell]:
        with self._conn() as c:
            if symbol:
                cur = c.execute(
                    "SELECT * FROM sells WHERE symbol = ? ORDER BY executed_at_utc DESC LIMIT ?",
                    (symbol, limit),
                )
            else:
                cur = c.execute(
                    "SELECT * FROM sells ORDER BY executed_at_utc DESC LIMIT ?",
                    (limit,),
                )
            return [self._row_to_sell(r) for r in cur.fetchall()]

    def list_buys(self, symbol: Optional[str] = None, limit: int = 100) -> list[Buy]:
        with self._conn() as c:
            if symbol:
                cur = c.execute(
                    "SELECT * FROM buys WHERE symbol = ? ORDER BY executed_at_utc DESC LIMIT ?",
                    (symbol, limit),
                )
            else:
                cur = c.execute(
                    "SELECT * FROM buys ORDER BY executed_at_utc DESC LIMIT ?",
                    (limit,),
                )
            return [self._row_to_buy(r) for r in cur.fetchall()]

    def summary(self, symbol: str) -> dict:
        """Resumen agregado: buys + sells.

        - avg_cost: coste medio sobre los buys (NO se reduce con sells).
        - net_qty: qty bruta de buys menos qty bruta de sells.
        - realized_pnl: suma de realized_pnl_eur de los sells."""
        with self._conn() as c:
            cur = c.execute(
                """SELECT
                    COUNT(*) AS n,
                    COALESCE(SUM(quote_amount_eur), 0) AS invested,
                    COALESCE(SUM(base_qty), 0) AS bought_qty,
                    COALESCE(SUM(fee_quote), 0) AS fees,
                    MIN(executed_at_utc) AS first_buy,
                    MAX(executed_at_utc) AS last_buy
                FROM buys WHERE symbol = ?""",
                (symbol,),
            )
            row = cur.fetchone()
            buy_summary = dict(row)

            cur = c.execute(
                """SELECT
                    COUNT(*) AS n_sells,
                    COALESCE(SUM(base_qty), 0) AS sold_qty,
                    COALESCE(SUM(quote_proceeds_eur), 0) AS sold_proceeds,
                    COALESCE(SUM(fee_quote), 0) AS sell_fees,
                    COALESCE(SUM(realized_pnl_eur), 0) AS realized_pnl
                FROM sells WHERE symbol = ?""",
                (symbol,),
            )
            sell_row = dict(cur.fetchone())

        avg_cost = buy_summary["invested"] / buy_summary["bought_qty"] if buy_summary["bought_qty"] > 0 else None
        net_qty = buy_summary["bought_qty"] - sell_row["sold_qty"]

        return {
            "n": buy_summary["n"],
            "n_sells": sell_row["n_sells"],
            "invested": buy_summary["invested"],
            "bought_qty": buy_summary["bought_qty"],
            "sold_qty": sell_row["sold_qty"],
            "net_qty": net_qty,
            "fees": buy_summary["fees"] + sell_row["sell_fees"],
            "first_buy": buy_summary["first_buy"],
            "last_buy": buy_summary["last_buy"],
            "avg_cost": avg_cost,
            "realized_pnl": sell_row["realized_pnl"],
            "sold_proceeds": sell_row["sold_proceeds"],
            # Compat con código antiguo que usaba "qty"
            "qty": net_qty,
        }

    def get_state(self, key: str) -> Optional[str]:
        with self._conn() as c:
            cur = c.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO bot_state (key, value, updated_at_utc) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at_utc = excluded.updated_at_utc""",
                (key, value, datetime.now(timezone.utc).isoformat()),
            )

    @staticmethod
    def _row_to_sell(row: sqlite3.Row) -> Sell:
        return Sell(
            id=row["id"],
            executed_at_utc=datetime.fromisoformat(row["executed_at_utc"]),
            sell_date_utc=date.fromisoformat(row["sell_date_utc"]),
            symbol=row["symbol"],
            base_qty=row["base_qty"],
            fill_price=row["fill_price"],
            quote_proceeds_eur=row["quote_proceeds_eur"],
            fee_quote=row["fee_quote"],
            avg_cost_at_sale=row["avg_cost_at_sale"],
            realized_pnl_eur=row["realized_pnl_eur"],
            reason=row["reason"],
            mode=row["mode"],
            order_id=row["order_id"],
            raw_response=row["raw_response"],
        )

    @staticmethod
    def _row_to_buy(row: sqlite3.Row) -> Buy:
        return Buy(
            id=row["id"],
            executed_at_utc=datetime.fromisoformat(row["executed_at_utc"]),
            buy_date_utc=date.fromisoformat(row["buy_date_utc"]),
            symbol=row["symbol"],
            quote_amount_eur=row["quote_amount_eur"],
            fill_price=row["fill_price"],
            base_qty=row["base_qty"],
            fee_quote=row["fee_quote"],
            mode=row["mode"],
            order_id=row["order_id"],
            raw_response=row["raw_response"],
        )
