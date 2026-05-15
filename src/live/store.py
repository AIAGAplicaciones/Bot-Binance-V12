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
    order_id TEXT,                        -- id devuelto por Binance (NULL en paper)
    raw_response TEXT                     -- JSON de la respuesta (NULL en paper)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_buys_date_symbol
    ON buys (buy_date_utc, symbol);

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
        with self._conn() as c:
            cur = c.execute(
                """SELECT
                    COUNT(*) AS n,
                    COALESCE(SUM(quote_amount_eur), 0) AS invested,
                    COALESCE(SUM(base_qty), 0) AS qty,
                    COALESCE(SUM(fee_quote), 0) AS fees,
                    MIN(executed_at_utc) AS first_buy,
                    MAX(executed_at_utc) AS last_buy
                FROM buys WHERE symbol = ?""",
                (symbol,),
            )
            row = cur.fetchone()
        out = dict(row)
        out["avg_cost"] = out["invested"] / out["qty"] if out["qty"] > 0 else None
        return out

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
