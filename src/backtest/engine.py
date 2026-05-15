"""Motor de backtest event-driven simple para estrategias long-only.

Decisiones de diseño:
- Una posición a la vez por símbolo (sin piramidación).
- Las señales se generan al cierre de la vela t y se ejecutan al OPEN de t+1
  (evita lookahead bias).
- Stops y take profits se chequean intra-bar usando high/low. Si en la misma
  vela se podrían disparar SL y TP a la vez, asumimos que se dispara el SL
  primero (pesimista, no overestimar resultados).
- Fees y slippage se aplican como reducción del precio ejecutado.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd


@dataclass
class Costs:
    taker_fee_pct: float = 0.10      # % por lado
    slippage_pct: float = 0.05       # % por lado, peor que mid


@dataclass
class Order:
    """Una orden que la estrategia quiere ejecutar al próximo open."""
    side: str                                  # "buy" | "sell"
    fraction_of_cash: float = 1.0              # buy: % del cash a usar
    fraction_of_position: float = 1.0          # sell: % de la posición a vender
    stop_loss: Optional[float] = None          # precio absoluto (sólo entry buys)
    take_profit: Optional[float] = None        # precio absoluto
    trailing_distance: Optional[float] = None  # distancia absoluta para trailing
    time_stop_bars: Optional[int] = None
    tag: str = ""                              # etiqueta libre, aparece en el log


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
    pnl_gross: float       # antes de fees
    fees: float            # total de ambas patas
    pnl_net: float
    pnl_pct: float         # sobre notional invertido en la entrada
    bars_held: int
    exit_reason: str       # "sl" | "tp" | "trail" | "time" | "signal" | "end"
    entry_tag: str


# Una estrategia es una función que dado el dataframe y el índice actual
# devuelve órdenes a ejecutar al próximo open. Recibe también la posición
# abierta (si la hay) y el cash disponible.
StrategyFn = Callable[[pd.DataFrame, int, Optional[Position], float], list[Order]]


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: pd.Series  # indexada por datetime
    initial_cash: float
    final_cash: float
    final_equity: float


def _buy_slip(price: float, costs: Costs) -> float:
    return price * (1 + costs.slippage_pct / 100)


def _sell_slip(price: float, costs: Costs) -> float:
    return price * (1 - costs.slippage_pct / 100)


def _fee(notional: float, costs: Costs) -> float:
    return abs(notional) * costs.taker_fee_pct / 100


def run_backtest(
    df: pd.DataFrame,
    strategy: StrategyFn,
    initial_cash: float,
    costs: Costs,
) -> BacktestResult:
    required = {"open", "high", "low", "close", "volume", "datetime"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas en df: {missing}")

    df = df.reset_index(drop=True)
    n = len(df)

    cash = initial_cash
    position: Optional[Position] = None
    pending: list[Order] = []
    trades: list[Trade] = []
    equity_times: list[pd.Timestamp] = []
    equity_values: list[float] = []

    def close_position(pos: Position, exit_price: float, exit_fee: float, bar: pd.Series, exit_idx: int, reason: str) -> None:
        notional_in = pos.qty * pos.entry_price
        notional_out = pos.qty * exit_price
        pnl_gross = notional_out - notional_in
        fees_total = pos.entry_fee + exit_fee
        pnl_net = pnl_gross - fees_total
        pnl_pct = pnl_net / notional_in * 100 if notional_in > 0 else 0.0
        trades.append(Trade(
            entry_time=pos.entry_time,
            exit_time=bar["datetime"],
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
        bar = df.iloc[i]

        # 1) Ejecutar órdenes pendientes al OPEN de esta vela.
        if pending:
            for o in pending:
                if o.side == "buy":
                    fill_price = _buy_slip(float(bar["open"]), costs)
                    invest = cash * o.fraction_of_cash
                    if invest <= 0:
                        continue
                    fee = _fee(invest, costs)
                    qty_new = (invest - fee) / fill_price
                    cash -= invest
                    if position is None:
                        position = Position(
                            qty=qty_new,
                            entry_price=fill_price,
                            entry_idx=i,
                            entry_time=bar["datetime"],
                            entry_fee=fee,
                            stop_loss=o.stop_loss,
                            take_profit=o.take_profit,
                            trailing_distance=o.trailing_distance,
                            time_stop_bars=o.time_stop_bars,
                            highest_seen=fill_price,
                            tag=o.tag,
                        )
                    else:
                        # Promediado: actualiza precio medio ponderado y suma fees.
                        # Mantiene SL/TP/trailing existentes (las estrategias
                        # direccionales no llegan aquí porque no emiten buys con posición abierta).
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
                    fill_price = _sell_slip(float(bar["open"]), costs)
                    qty_out = position.qty * o.fraction_of_position
                    proceeds = qty_out * fill_price
                    fee = _fee(proceeds, costs)
                    cash += proceeds - fee
                    if o.fraction_of_position >= 1.0:
                        close_position(position, fill_price, fee, bar, i, "signal")
                        position = None
                    else:
                        position.qty -= qty_out  # venta parcial: continúa abierta
            pending = []

        # 2) Trailing + chequeo intra-bar de SL/TP/time.
        if position is not None:
            high = float(bar["high"])
            low = float(bar["low"])
            position.highest_seen = max(position.highest_seen, high)

            if position.trailing_distance is not None:
                trail_sl = position.highest_seen - position.trailing_distance
                if position.stop_loss is None or trail_sl > position.stop_loss:
                    position.stop_loss = trail_sl

            exit_price: Optional[float] = None
            exit_reason: Optional[str] = None

            if position.stop_loss is not None and low <= position.stop_loss:
                exit_price = _sell_slip(position.stop_loss, costs)
                exit_reason = "trail" if position.trailing_distance is not None else "sl"
            elif position.take_profit is not None and high >= position.take_profit:
                exit_price = _sell_slip(position.take_profit, costs)
                exit_reason = "tp"
            elif position.time_stop_bars is not None and (i - position.entry_idx) >= position.time_stop_bars:
                exit_price = _sell_slip(float(bar["close"]), costs)
                exit_reason = "time"

            if exit_price is not None:
                proceeds = position.qty * exit_price
                fee = _fee(proceeds, costs)
                cash += proceeds - fee
                close_position(position, exit_price, fee, bar, i, exit_reason)
                position = None

        # 3) Pedir órdenes nuevas al cierre.
        new_orders = strategy(df, i, position, cash)
        if new_orders:
            pending.extend(new_orders)

        # 4) Marcar equity al cierre.
        ev = cash + (position.qty * float(bar["close"]) if position else 0.0)
        equity_times.append(bar["datetime"])
        equity_values.append(ev)

    # Cierre forzado al final.
    if position is not None:
        last = df.iloc[-1]
        exit_price = _sell_slip(float(last["close"]), costs)
        proceeds = position.qty * exit_price
        fee = _fee(proceeds, costs)
        cash += proceeds - fee
        close_position(position, exit_price, fee, last, n - 1, "end")
        position = None

    eq = pd.Series(
        equity_values,
        index=pd.DatetimeIndex(equity_times, name="datetime"),
        name="equity",
    )

    return BacktestResult(
        trades=trades,
        equity_curve=eq,
        initial_cash=initial_cash,
        final_cash=cash,
        final_equity=cash,
    )
