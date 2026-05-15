"""Motor de backtest event-driven simple para estrategias long-only.

Optimizado con numpy: el loop principal accede a arrays planos en vez de
`df.iloc[i]`, lo que da ~100× speedup en datasets de 100K+ velas.

Decisiones de diseño:
- Una posición a la vez por símbolo (sin piramidación direccional, pero sí
  promediado para estrategias DCA-like).
- Las señales se generan al cierre de la vela t y se ejecutan al OPEN de t+1
  (evita lookahead bias).
- Stops y take profits se chequean intra-bar usando high/low. Si en la misma
  vela se podrían disparar SL y TP a la vez, asumimos SL primero (pesimista,
  para no sobreestimar resultados).
- Fees y slippage se aplican como reducción del precio ejecutado.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd


@dataclass
class Costs:
    taker_fee_pct: float = 0.10      # % por lado
    slippage_pct: float = 0.05       # % por lado, peor que mid


@dataclass
class CashSchedule:
    """Inyección periódica de cash al portafolio (simula aportaciones recurrentes)."""
    weekly_amount: float = 0.0       # 0 = sin aportaciones recurrentes
    weekday: int = 0                 # 0=lunes, 6=domingo


@dataclass
class CashInjection:
    when: pd.Timestamp
    amount: float


@dataclass
class Order:
    """Una orden que la estrategia quiere ejecutar al próximo open."""
    side: str                                  # "buy" | "sell"
    fraction_of_cash: float = 1.0              # buy: % del cash a usar
    fraction_of_position: float = 1.0          # sell: % de la posición a vender
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trailing_distance: Optional[float] = None
    time_stop_bars: Optional[int] = None
    tag: str = ""


@dataclass
class Position:
    qty: float
    entry_price: float
    entry_idx: int
    entry_time: pd.Timestamp
    entry_fee: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trailing_distance: Optional[float] = None
    time_stop_bars: Optional[int] = None
    highest_seen: float = 0.0
    tag: str = ""


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: float
    pnl_gross: float
    fees: float
    pnl_net: float
    pnl_pct: float
    bars_held: int
    exit_reason: str
    entry_tag: str


# Estrategia: recibe el df completo, el índice actual, la posición (si la hay)
# y el cash. Devuelve una lista de órdenes a ejecutar al próximo open.
StrategyFn = Callable[[pd.DataFrame, int, Optional[Position], float], list[Order]]


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series
    initial_cash: float
    final_cash: float
    final_equity: float
    total_injected: float = 0.0
    injections: list[CashInjection] = None  # type: ignore

    def __post_init__(self):
        if self.injections is None:
            self.injections = []


def _buy_slip(price: float, slip_pct: float) -> float:
    return price * (1 + slip_pct / 100)


def _sell_slip(price: float, slip_pct: float) -> float:
    return price * (1 - slip_pct / 100)


def run_backtest(
    df: pd.DataFrame,
    strategy: StrategyFn,
    initial_cash: float,
    costs: Costs,
    cash_schedule: Optional[CashSchedule] = None,
) -> BacktestResult:
    required = {"open", "high", "low", "close", "volume", "datetime"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas en df: {missing}")

    df = df.reset_index(drop=True)
    n = len(df)

    # Numpy arrays planos para el hot loop. Acceso O(1) sin overhead pandas.
    opens = df["open"].to_numpy(dtype=np.float64)
    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    closes = df["close"].to_numpy(dtype=np.float64)
    times = df["datetime"].to_numpy()  # datetime64[ns, UTC] -> conv a Timestamp on demand

    fee_pct = costs.taker_fee_pct
    slip_pct = costs.slippage_pct

    cash = initial_cash
    position: Optional[Position] = None
    pending: list[Order] = []
    trades: list[Trade] = []
    equity_values = np.empty(n, dtype=np.float64)
    total_injected = initial_cash
    injections: list[CashInjection] = []
    last_injection_date = None
    weekdays = pd.DatetimeIndex(times).weekday.to_numpy() if cash_schedule and cash_schedule.weekly_amount > 0 else None
    dates_arr = pd.DatetimeIndex(times).date if cash_schedule and cash_schedule.weekly_amount > 0 else None

    def close_position(pos: Position, exit_price: float, exit_fee: float, exit_time, exit_idx: int, reason: str) -> None:
        notional_in = pos.qty * pos.entry_price
        notional_out = pos.qty * exit_price
        pnl_gross = notional_out - notional_in
        fees_total = pos.entry_fee + exit_fee
        pnl_net = pnl_gross - fees_total
        pnl_pct = pnl_net / notional_in * 100 if notional_in > 0 else 0.0
        trades.append(Trade(
            entry_time=pos.entry_time,
            exit_time=pd.Timestamp(exit_time),
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=pos.qty,
            pnl_gross=pnl_gross,
            fees=fees_total,
            pnl_net=pnl_net,
            pnl_pct=pnl_pct,
            bars_held=exit_idx - pos.entry_idx,
            exit_reason=reason,
            entry_tag=pos.tag,
        ))

    for i in range(n):
        open_i = opens[i]
        high_i = highs[i]
        low_i = lows[i]
        close_i = closes[i]
        time_i = times[i]

        # 0) Inyección de cash si toca este día.
        if weekdays is not None:
            today = dates_arr[i]
            if weekdays[i] == cash_schedule.weekday and today != last_injection_date:
                cash += cash_schedule.weekly_amount
                total_injected += cash_schedule.weekly_amount
                injections.append(CashInjection(when=pd.Timestamp(time_i), amount=cash_schedule.weekly_amount))
                last_injection_date = today

        # 1) Ejecutar órdenes pendientes al OPEN de esta vela.
        if pending:
            for o in pending:
                if o.side == "buy":
                    fill_price = _buy_slip(open_i, slip_pct)
                    invest = cash * o.fraction_of_cash
                    if invest <= 0:
                        continue
                    fee = invest * fee_pct / 100
                    qty_new = (invest - fee) / fill_price
                    cash -= invest
                    if position is None:
                        position = Position(
                            qty=qty_new,
                            entry_price=fill_price,
                            entry_idx=i,
                            entry_time=pd.Timestamp(time_i),
                            entry_fee=fee,
                            stop_loss=o.stop_loss,
                            take_profit=o.take_profit,
                            trailing_distance=o.trailing_distance,
                            time_stop_bars=o.time_stop_bars,
                            highest_seen=fill_price,
                            tag=o.tag,
                        )
                    else:
                        total_qty = position.qty + qty_new
                        position.entry_price = (
                            position.entry_price * position.qty + fill_price * qty_new
                        ) / total_qty
                        position.qty = total_qty
                        position.entry_fee += fee
                        position.highest_seen = max(position.highest_seen, fill_price)
                elif o.side == "sell":
                    if position is None:
                        continue
                    fill_price = _sell_slip(open_i, slip_pct)
                    qty_out = position.qty * o.fraction_of_position
                    proceeds = qty_out * fill_price
                    fee = proceeds * fee_pct / 100
                    cash += proceeds - fee
                    if o.fraction_of_position >= 1.0:
                        close_position(position, fill_price, fee, time_i, i, "signal")
                        position = None
                    else:
                        position.qty -= qty_out
            pending = []

        # 2) Trailing + chequeo intra-bar de SL/TP/time.
        if position is not None:
            if high_i > position.highest_seen:
                position.highest_seen = high_i

            if position.trailing_distance is not None:
                trail_sl = position.highest_seen - position.trailing_distance
                if position.stop_loss is None or trail_sl > position.stop_loss:
                    position.stop_loss = trail_sl

            exit_price: Optional[float] = None
            exit_reason: Optional[str] = None

            if position.stop_loss is not None and low_i <= position.stop_loss:
                exit_price = _sell_slip(position.stop_loss, slip_pct)
                exit_reason = "trail" if position.trailing_distance is not None else "sl"
            elif position.take_profit is not None and high_i >= position.take_profit:
                exit_price = _sell_slip(position.take_profit, slip_pct)
                exit_reason = "tp"
            elif position.time_stop_bars is not None and (i - position.entry_idx) >= position.time_stop_bars:
                exit_price = _sell_slip(close_i, slip_pct)
                exit_reason = "time"

            if exit_price is not None:
                proceeds = position.qty * exit_price
                fee = proceeds * fee_pct / 100
                cash += proceeds - fee
                close_position(position, exit_price, fee, time_i, i, exit_reason)
                position = None

        # 3) Pedir órdenes nuevas al cierre.
        new_orders = strategy(df, i, position, cash)
        if new_orders:
            pending.extend(new_orders)

        # 4) Marcar equity al cierre.
        equity_values[i] = cash + (position.qty * close_i if position else 0.0)

    # Cierre forzado al final.
    if position is not None:
        last_close = closes[-1]
        exit_price = _sell_slip(last_close, slip_pct)
        proceeds = position.qty * exit_price
        fee = proceeds * fee_pct / 100
        cash += proceeds - fee
        close_position(position, exit_price, fee, times[-1], n - 1, "end")
        position = None

    eq = pd.Series(
        equity_values,
        index=pd.DatetimeIndex(times, name="datetime"),
        name="equity",
    )

    return BacktestResult(
        trades=trades,
        equity_curve=eq,
        initial_cash=initial_cash,
        final_cash=cash,
        final_equity=cash,
        total_injected=total_injected,
        injections=injections,
    )
