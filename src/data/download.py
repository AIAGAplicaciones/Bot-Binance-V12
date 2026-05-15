"""Descarga OHLCV histórico de Binance Spot vía ccxt y lo cachea como CSV.

Uso:
    python -m src.data.download --symbol ETH/EUR --timeframe 1h --months 24
    python -m src.data.download --symbol BTC/EUR --timeframe 1d --months 36

Los CSV se guardan en data/cache/{symbol}_{timeframe}.csv con columnas:
    timestamp (UTC, ms), open, high, low, close, volume
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import pandas as pd
from rich.console import Console
from rich.progress import Progress

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
console = Console()


def fetch_ohlcv(symbol: str, timeframe: str, months: int) -> pd.DataFrame:
    """Descarga OHLCV paginando hacia atrás desde ahora hasta `months` meses."""
    exchange = ccxt.binance({"enableRateLimit": True})
    exchange.load_markets()

    if symbol not in exchange.markets:
        raise ValueError(
            f"Símbolo '{symbol}' no existe en Binance. "
            f"Ejemplos válidos: ETH/EUR, BTC/EUR, ETH/USDC."
        )

    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp() * 1000)

    tf_ms = exchange.parse_timeframe(timeframe) * 1000
    limit = 1000  # max por request en Binance

    all_candles: list[list] = []
    cursor = start_ms

    total_expected = (end_ms - start_ms) // tf_ms
    with Progress(console=console) as progress:
        task = progress.add_task(f"[cyan]{symbol} {timeframe}", total=total_expected)
        while cursor < end_ms:
            batch = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=limit)
            if not batch:
                break
            all_candles.extend(batch)
            cursor = batch[-1][0] + tf_ms
            progress.update(task, completed=min(len(all_candles), total_expected))
            time.sleep(exchange.rateLimit / 1000)

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def save(df: pd.DataFrame, symbol: str, timeframe: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_symbol = symbol.replace("/", "")
    path = CACHE_DIR / f"{safe_symbol}_{timeframe}.csv"
    df.to_csv(path, index=False)
    return path


def load(symbol: str, timeframe: str) -> pd.DataFrame:
    """Carga un CSV cacheado. Lanza FileNotFoundError si no existe."""
    safe_symbol = symbol.replace("/", "")
    path = CACHE_DIR / f"{safe_symbol}_{timeframe}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"No hay caché en {path}. Ejecuta: "
            f"python -m src.data.download --symbol {symbol} --timeframe {timeframe}"
        )
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="ej. ETH/EUR")
    parser.add_argument("--timeframe", default="1h", help="1m, 5m, 15m, 1h, 4h, 1d…")
    parser.add_argument("--months", type=int, default=24, help="meses hacia atrás")
    args = parser.parse_args()

    df = fetch_ohlcv(args.symbol, args.timeframe, args.months)
    path = save(df, args.symbol, args.timeframe)
    console.print(
        f"[green]✓[/green] Guardadas {len(df):,} velas en [bold]{path}[/bold] "
        f"(rango: {df['datetime'].min()} → {df['datetime'].max()})"
    )


if __name__ == "__main__":
    main()
