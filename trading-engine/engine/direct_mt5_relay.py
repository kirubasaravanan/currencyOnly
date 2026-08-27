"""Direct-MT5 execution — connects straight to a local MT5 terminal via
the official MetaTrader5 Python package (mt5.order_send()/order_check()/
positions_get()) instead of relaying through a third-party webhook
service. [ADD 2026-08-26, explicit user instruction]

This is a THIRD, purely additive execution path sitting alongside
tradesgnl_relay.py and pineconnector_relay.py -- neither existing module
has a single line changed by this one, and both keep firing on every
trigger exactly as before. Gated entirely by config.DIRECT_MT5_ACCOUNTS
being non-empty; while that list is empty (its default), every function
below is unreachable from orchestrator.py's wiring.

Unlike tradesgnl_relay.py/pineconnector_relay.py (each hardwired to one
account), every function here takes a config.DirectMT5Account as an
explicit first argument, so one module serves however many terminals are
configured.

Ported from the sister Forex/Forex app's engine/mt5_direct.py, with two
real fixes already known from that app's own live testing baked in from
day one rather than left to be rediscovered:
  1. mt5.TRADE_RETCODE_OK does not exist anywhere in the MetaTrader5
     Python package -- referencing it raises AttributeError on every
     single call.
  2. order_check()'s own success indicator is NOT mt5.TRADE_RETCODE_DONE
     (that constant is order_send()'s "trade completed" code) --
     order_check()'s success case is unnamed, it's plain integer 0 with
     comment='Done'. order_check() is validated against literal 0 below;
     order_send() is separately validated against mt5.TRADE_RETCODE_DONE.

Two things deliberately done DIFFERENTLY from that sister app, both
following currencyOnly's own stricter existing conventions instead:
  - send_close()/send_partial_close() verify the position is actually
    gone/reduced via a fresh positions_get() re-read after a short delay,
    rather than trusting order_send()'s retcode alone -- same
    "verify, don't trust" principle as orchestrator.py's own
    close_all_real_positions()/_positions_still_open_sync and
    trade_sync_heartbeat.py's own _verify_ticket_closed_sync. The sister
    app's own trust-the-retcode-alone shortcut has a documented real
    incident behind it: a since-disabled reconciliation job there force-
    closed 28 real positions at a fabricated $0.00 when its connection
    silently went stale.
  - Entries still send a deliberately unreachable "wide TP" in dynamic
    exit mode (same WIDE_TP_MULTIPLIER convention as both webhook
    relays) even though direct MT5 connections COULD modify SL/TP via
    TRADE_ACTION_SLTP (see modify_sl_tp() below). That capability is
    built and exposed here but not yet wired into any trailing/
    breakeven logic -- until it is, paper's own close/partial-close
    commands must remain the only thing that ever closes or trims a
    real position here too, exactly like the two existing relays,
    or the real position would close itself against a bare TP order
    the moment price reaches it, silently desyncing from paper.

Because mt5.order_send() gives a definitive ticket and retcode back
immediately, this module needs none of the _confirmed_open_ids/
_relayed_partial_ids bookkeeping tradesgnl_relay.py/pineconnector_relay.py
need only because a fire-and-forget webhook gives no confirmable ticket
back at all -- ground truth here is always a live positions_get() read.

Every public function is a no-op (returns None/False immediately) if the
account is disabled or has no login configured (account_login == 0),
matching real_giveback_source.py's own inertness convention -- safe to
leave sitting in the repo with zero live effect on any account that
isn't fully configured yet.

The MetaTrader5 package holds ONE connection per process, not per
terminal -- every operation below acquires mt5_ipc_lock.MT5_LOCK for its
full initialize -> verify -> operate -> shutdown sequence. See that
module's docstring for the live incident that makes this non-optional
the moment more than one direct-MT5 account exists.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

import config
from config import DirectMT5Account
from engine.mt5_ipc_lock import MT5_LOCK

CLOSE_VERIFY_DELAY_SECONDS = 5  # same as orchestrator.py's own CLOSE_VERIFY_DELAY_SECONDS
DEVIATION_POINTS = 20

# Same reasoning as tradesgnl_relay.py's/pineconnector_relay.py's own
# WIDE_TP_MULTIPLIER -- see this module's own docstring above for why
# it's still needed here even though modify_sl_tp() exists.
WIDE_TP_MULTIPLIER = 20.0


def _comment_id(account: DirectMT5Account, symbol: str, side: str) -> str:
    is_long = side in ("BUY", "BULLISH")
    return f"{account.comment_prefix}{symbol}-{'L' if is_long else 'S'}"


def _wide_tp_price(trade: Dict) -> float:
    entry = trade["entry_price"]
    return entry + WIDE_TP_MULTIPLIER * (trade["tp2_price"] - entry)


def _terminal_running(terminal_path: str) -> bool:
    """Never auto-launches the terminal -- only ever piggybacks on one
    already running, same convention as every other direct-MT5 module in
    this repo (real_giveback_source.py, real_risk_source.py,
    trade_sync_heartbeat.py)."""
    import psutil
    try:
        for proc in psutil.process_iter(["name", "exe"]):
            if proc.info["name"] == "terminal64.exe" and proc.info["exe"] == terminal_path:
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _connect(mt5mod, account: DirectMT5Account) -> bool:
    if not mt5mod.initialize(path=account.terminal_path):
        return False
    acc = mt5mod.account_info()
    return acc is not None and acc.login == account.account_login


def _pick_filling_mode(mt5mod, symbol_info) -> int:
    """Reads the symbol's filling_mode bitmask and picks deterministically
    -- prefers IOC over FOK over RETURN. Decided once per call from the
    broker's own symbol_info(), not a trial-and-error retry loop. Ported
    from the sister app's proven mt5_direct.py logic."""
    bitmask = symbol_info.filling_mode
    if bitmask & 2:  # SYMBOL_FILLING_IOC
        return mt5mod.ORDER_FILLING_IOC
    if bitmask & 1:  # SYMBOL_FILLING_FOK
        return mt5mod.ORDER_FILLING_FOK
    return mt5mod.ORDER_FILLING_RETURN


def _magic(account: DirectMT5Account) -> int:
    return account.magic if account.magic is not None else account.account_login


def _guard(account: DirectMT5Account):
    return account.enabled and account.account_login


def _send_entry_sync(account: DirectMT5Account, trade: Dict) -> Optional[Dict]:
    if not _guard(account):
        return None
    if not _terminal_running(account.terminal_path):
        return None
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None

    with MT5_LOCK:
        if not _connect(mt5, account):
            mt5.shutdown()
            return None
        try:
            symbol = trade["symbol"]
            is_long = trade["side"] in ("BUY", "BULLISH")
            info = mt5.symbol_info(symbol)
            tick = mt5.symbol_info_tick(symbol)
            if info is None or tick is None:
                return None
            price = tick.ask if is_long else tick.bid
            tp_to_send = trade["tp_price"] if config.state.exit_mode == "static" else _wide_tp_price(trade)

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": round(trade["lots"], 2),
                "type": mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL,
                "price": price,
                "sl": trade["sl_price"],
                "tp": tp_to_send,
                "deviation": DEVIATION_POINTS,
                "magic": _magic(account),
                "comment": _comment_id(account, symbol, trade["side"]),
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": _pick_filling_mode(mt5, info),
            }

            check = mt5.order_check(request)
            # order_check()'s own success is literal 0 ("Done"), NOT
            # mt5.TRADE_RETCODE_DONE (that's order_send()'s success code)
            # and NOT mt5.TRADE_RETCODE_OK (doesn't exist in the package)
            # -- see module docstring.
            if check is None or check.retcode != 0:
                print(f"[direct_mt5_relay:{account.label}] order_check REJECTED {symbol}: "
                      f"{getattr(check, 'retcode', 'none')} {getattr(check, 'comment', '')}")
                return {"status": "rejected", "retcode": getattr(check, "retcode", None),
                        "comment": getattr(check, "comment", "")}

            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                print(f"[direct_mt5_relay:{account.label}] order_send REJECTED {symbol}: "
                      f"{getattr(result, 'retcode', 'none')} {getattr(result, 'comment', '')}")
                return {"status": "rejected", "retcode": getattr(result, "retcode", None),
                        "comment": getattr(result, "comment", "")}

            print(f"[direct_mt5_relay:{account.label}] FILLED {symbol} {trade['side']} "
                  f"{request['volume']} lots @ {result.price} ticket={result.order}")
            return {
                "status": "filled", "symbol": symbol, "side": trade["side"],
                "lots": request["volume"], "fill_price": result.price, "ticket": result.order,
            }
        except Exception as exc:  # noqa: BLE001
            print(f"[direct_mt5_relay:{account.label}] send_entry error: {exc}")
            return None
        finally:
            mt5.shutdown()


def _find_position(mt5mod, account: DirectMT5Account, symbol: str):
    positions = mt5mod.positions_get(symbol=symbol) or ()
    ours = [p for p in positions if p.magic == _magic(account)]
    return ours[0] if ours else None


def _send_close_sync(account: DirectMT5Account, trade: Dict, source: str) -> bool:
    if not _guard(account):
        return False
    if not _terminal_running(account.terminal_path):
        return False
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return False

    with MT5_LOCK:
        if not _connect(mt5, account):
            mt5.shutdown()
            return False
        try:
            symbol = trade["symbol"]
            position = _find_position(mt5, account, symbol)
            if position is None:
                print(f"[direct_mt5_relay:{account.label}] SKIPPED close (source={source}) for "
                      f"trade {trade['id']} ({symbol}) -- no matching open position found")
                return False

            is_closing_long = position.type == mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(symbol)
            info = mt5.symbol_info(symbol)
            if tick is None or info is None:
                return False
            price = tick.bid if is_closing_long else tick.ask

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": position.volume,
                "type": mt5.ORDER_TYPE_SELL if is_closing_long else mt5.ORDER_TYPE_BUY,
                "position": position.ticket,
                "price": price,
                "deviation": DEVIATION_POINTS,
                "magic": _magic(account),
                "comment": _comment_id(account, symbol, trade["side"]) + "-close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": _pick_filling_mode(mt5, info),
            }
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                print(f"[direct_mt5_relay:{account.label}] close REJECTED (source={source}) "
                      f"{symbol}: {getattr(result, 'retcode', 'none')} {getattr(result, 'comment', '')}")
                return False
            ticket = position.ticket
        except Exception as exc:  # noqa: BLE001
            print(f"[direct_mt5_relay:{account.label}] send_close error: {exc}")
            return False
        finally:
            mt5.shutdown()

    return _verify_closed_sync(account, symbol, ticket, source)


def _verify_closed_sync(account: DirectMT5Account, symbol: str, ticket: int, source: str) -> bool:
    """Never trusts order_send()'s retcode alone -- re-reads the real
    position after a short delay, same "verify, don't trust" principle as
    orchestrator.py's own _positions_still_open_sync and
    trade_sync_heartbeat.py's own _verify_ticket_closed_sync."""
    import time
    time.sleep(CLOSE_VERIFY_DELAY_SECONDS)
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return False
    with MT5_LOCK:
        if not _connect(mt5, account):
            mt5.shutdown()
            return False
        try:
            still_open = mt5.positions_get(ticket=ticket)
            gone = not still_open
            if not gone:
                print(f"[direct_mt5_relay:{account.label}] close SENT but UNVERIFIED (source={source}) "
                      f"{symbol} ticket={ticket} -- still shows open after {CLOSE_VERIFY_DELAY_SECONDS}s")
            return gone
        except Exception:  # noqa: BLE001
            return False
        finally:
            mt5.shutdown()


def _send_partial_close_sync(account: DirectMT5Account, trade: Dict, source: str) -> bool:
    if not _guard(account):
        return False
    if not _terminal_running(account.terminal_path):
        return False
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return False

    with MT5_LOCK:
        if not _connect(mt5, account):
            mt5.shutdown()
            return False
        try:
            symbol = trade["symbol"]
            position = _find_position(mt5, account, symbol)
            if position is None:
                print(f"[direct_mt5_relay:{account.label}] SKIPPED partial-close (source={source}) for "
                      f"trade {trade['id']} ({symbol}) -- no matching open position found")
                return False

            info = mt5.symbol_info(symbol)
            if info is None:
                return False
            # Paper's own authoritative reduction amount, matching
            # pineconnector_relay.py's own approach -- not a fraction
            # parameter like the sister app's mt5_direct.py.
            volume_to_close = round(trade.get("original_lots", trade["lots"]) - trade["lots"], 2)
            if volume_to_close < info.volume_min or volume_to_close >= position.volume:
                print(f"[direct_mt5_relay:{account.label}] SKIPPED partial-close (source={source}) "
                      f"{symbol} -- computed volume {volume_to_close} out of bounds "
                      f"(min={info.volume_min}, position={position.volume})")
                return False

            is_closing_long = position.type == mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return False
            price = tick.bid if is_closing_long else tick.ask

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume_to_close,
                "type": mt5.ORDER_TYPE_SELL if is_closing_long else mt5.ORDER_TYPE_BUY,
                "position": position.ticket,
                "price": price,
                "deviation": DEVIATION_POINTS,
                "magic": _magic(account),
                "comment": _comment_id(account, symbol, trade["side"]) + "-partial",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": _pick_filling_mode(mt5, info),
            }
            check = mt5.order_check(request)
            if check is None or check.retcode != 0:
                print(f"[direct_mt5_relay:{account.label}] partial-close order_check REJECTED "
                      f"(source={source}) {symbol}: {getattr(check, 'retcode', 'none')}")
                return False
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                print(f"[direct_mt5_relay:{account.label}] partial-close REJECTED (source={source}) "
                      f"{symbol}: {getattr(result, 'retcode', 'none')} {getattr(result, 'comment', '')}")
                return False
            ticket = position.ticket
            expected_remaining = round(position.volume - volume_to_close, 2)
        except Exception as exc:  # noqa: BLE001
            print(f"[direct_mt5_relay:{account.label}] send_partial_close error: {exc}")
            return False
        finally:
            mt5.shutdown()

    return _verify_reduced_sync(account, symbol, ticket, expected_remaining, source)


def _verify_reduced_sync(account: DirectMT5Account, symbol: str, ticket: int, expected_remaining: float, source: str) -> bool:
    import time
    time.sleep(CLOSE_VERIFY_DELAY_SECONDS)
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return False
    with MT5_LOCK:
        if not _connect(mt5, account):
            mt5.shutdown()
            return False
        try:
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                # Fully closed instead of partially reduced -- report as
                # unverified rather than success, since the caller asked
                # for a PARTIAL reduction specifically.
                print(f"[direct_mt5_relay:{account.label}] partial-close UNVERIFIED (source={source}) "
                      f"{symbol} ticket={ticket} -- position fully gone, expected a partial reduction")
                return False
            reduced = abs(positions[0].volume - expected_remaining) < 0.01
            if not reduced:
                print(f"[direct_mt5_relay:{account.label}] partial-close UNVERIFIED (source={source}) "
                      f"{symbol} ticket={ticket} -- volume {positions[0].volume}, expected {expected_remaining}")
            return reduced
        except Exception:  # noqa: BLE001
            return False
        finally:
            mt5.shutdown()


def _modify_sl_tp_sync(account: DirectMT5Account, trade: Dict, new_sl: float, new_tp: Optional[float] = None) -> Optional[Dict]:
    """TRADE_ACTION_SLTP -- the capability neither webhook relay has.
    Exposed here but NOT called from anywhere in the trading logic yet
    (see module docstring) -- wiring this into trade_manager.py's
    trailing/breakeven logic is deliberately deferred, separate work."""
    if not _guard(account):
        return None
    if not _terminal_running(account.terminal_path):
        return None
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None

    with MT5_LOCK:
        if not _connect(mt5, account):
            mt5.shutdown()
            return None
        try:
            symbol = trade["symbol"]
            position = _find_position(mt5, account, symbol)
            if position is None:
                return None
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": symbol,
                "position": position.ticket,
                "sl": new_sl,
                "tp": new_tp if new_tp is not None else position.tp,
            }
            check = mt5.order_check(request)
            if check is None or check.retcode != 0:
                return {"status": "rejected", "retcode": getattr(check, "retcode", None)}
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                return {"status": "rejected", "retcode": getattr(result, "retcode", None)}
            return {"status": "modified", "symbol": symbol, "ticket": position.ticket, "sl": new_sl, "tp": request["tp"]}
        except Exception as exc:  # noqa: BLE001
            print(f"[direct_mt5_relay:{account.label}] modify_sl_tp error: {exc}")
            return None
        finally:
            mt5.shutdown()


async def send_entry(account: DirectMT5Account, trade: Dict) -> Optional[Dict]:
    return await asyncio.to_thread(_send_entry_sync, account, trade)


async def send_close(account: DirectMT5Account, trade: Dict, source: str = "unknown") -> bool:
    return await asyncio.to_thread(_send_close_sync, account, trade, source)


async def send_partial_close(account: DirectMT5Account, trade: Dict, source: str = "unknown") -> bool:
    return await asyncio.to_thread(_send_partial_close_sync, account, trade, source)


async def modify_sl_tp(account: DirectMT5Account, trade: Dict, new_sl: float, new_tp: Optional[float] = None) -> Optional[Dict]:
    return await asyncio.to_thread(_modify_sl_tp_sync, account, trade, new_sl, new_tp)
