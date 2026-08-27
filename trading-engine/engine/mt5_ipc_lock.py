"""Process-wide lock for MetaTrader5 connections.

[ADD 2026-08-26, explicit user instruction -- direct-MT5 execution
infrastructure] The MetaTrader5 Python package holds ONE connection per
process, not per terminal. Every existing direct-MT5 module in this repo
(real_giveback_source.py, real_risk_source.py, trade_sync_heartbeat.py)
has only ever talked to one real terminal at a time from any given call,
so this race has never been possible here before. The sister Forex app
hit this exact bug live once a second terminal existed: concurrent
initialize() calls targeting different terminals silently attached the
caller to the WRONG terminal -- a different wrong account on each of two
consecutive attempts, no code change between them. The moment a second
direct-MT5 account exists in this app too, the same race becomes
possible -- so every operation in engine/direct_mt5_relay.py acquires
this lock for its full initialize -> verify -> operate -> shutdown
sequence, serializing all direct-MT5 access across every configured
account.
"""

import threading

MT5_LOCK = threading.Lock()
