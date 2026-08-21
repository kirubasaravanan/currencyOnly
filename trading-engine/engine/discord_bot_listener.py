"""Discord bot listener -- lets the user type a command in Discord to
manually act on PineConnector's (FundedNext) open real position(s),
independent of the give-back breaker. [ADD 2026-08-20, extended
2026-08-21, explicit user instruction]

Why a separate bot connection: DISCORD_WEBHOOK_URL (discord_alerts.py) is
outbound-only -- a webhook can send messages but Discord never delivers
anything back through one. Receiving a command requires an actual bot
identity connected to Discord's Gateway (a persistent WebSocket), which is
what this module sets up.

Convention matches every other optional integration in this repo: leave
DISCORD_BOT_TOKEN blank and this listener simply never starts -- zero
impact on the rest of the engine. Set it (plus DISCORD_AUTHORIZED_USER_ID)
to activate. A bad token, a dropped connection, or any Gateway error must
never take down the trading engine itself -- every failure path here only
ever logs and returns, same fail-safe convention as discord_alerts.py.

Security: only reacts to a message from DISCORD_AUTHORIZED_USER_ID
specifically -- anyone else typing a command in a shared server/channel
is silently ignored (no reply, no hint the command exists).

Three commands, three different scopes -- all PineConnector/FundedNext
only:
  !closeall     -- close every open PineConnector position right now.
                  Does NOT stop new entries from resuming on the next
                  qualifying signal (any symbol, including the ones just
                  closed).
  !stopday      -- close every open PineConnector position AND pause new
                  PineConnector entries for the rest of today (see
                  orchestrator.manual_stop_for_today()'s own docstring
                  for why this is a separate flag from the give-back
                  breaker's).
  !close SYMBOL -- close only that one symbol's PineConnector position,
                  e.g. "!close USDCHF". Nothing else is touched -- every
                  other open symbol keeps running exactly as before, and
                  this same symbol can open a fresh trade again on the
                  very next qualifying signal. No block flag is set; this
                  is strictly narrower than !closeall, not a variant of
                  !stopday.

[NARROWED 2026-08-21, explicit user instruction] None of these three ever
touch TradeSgnl -- it runs on a demo account purely as a continuous data
feed, so there's no real money for any of these to protect there. Only
PineConnector, the account actually carrying real risk, is ever affected.
Paper is untouched by all three either way -- it stays a clean,
continuous research baseline regardless of what happens on the real
side, same reasoning as the give-back breaker.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_AUTHORIZED_USER_ID = int(os.getenv("DISCORD_AUTHORIZED_USER_ID", "0"))
CLOSE_ALL_COMMAND = "!closeall"
STOP_DAY_COMMAND = "!stopday"
CLOSE_SYMBOL_PREFIX = "!close "

_client = None  # type: Optional["discord.Client"]


def start() -> None:
    """Fire-and-forget: launches the bot as a background asyncio task if
    configured, no-ops otherwise. Called once from main.py's startup
    event, alongside orchestrator.start()."""
    global _client
    if not DISCORD_BOT_TOKEN or not DISCORD_AUTHORIZED_USER_ID:
        return

    try:
        import discord
    except ImportError:
        print("[discord_bot_listener] discord.py not installed -- listener disabled")
        return

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    _client = client

    @client.event
    async def on_ready() -> None:
        print(f"[discord_bot_listener] connected as {client.user}")

    @client.event
    async def on_message(message) -> None:  # noqa: ANN001
        if message.author.id != DISCORD_AUTHORIZED_USER_ID:
            return
        content = message.content.strip().lower()

        symbol: Optional[str] = None
        if content in (CLOSE_ALL_COMMAND, STOP_DAY_COMMAND):
            pass
        elif content.startswith(CLOSE_SYMBOL_PREFIX):
            from config import PAIRS

            symbol = content[len(CLOSE_SYMBOL_PREFIX):].strip().upper()
            if symbol not in PAIRS:
                await message.channel.send(f"Unknown symbol {symbol!r} -- must be one of: {', '.join(PAIRS)}")
                return
        else:
            return

        from engine import orchestrator

        try:
            if content == STOP_DAY_COMMAND:
                result = await orchestrator.manual_stop_for_today()
                suffix = " New PineConnector entries are paused for the rest of today (TradeSgnl unaffected)."
            else:
                result = await orchestrator.close_all_real_positions(symbol=symbol)
                suffix = "" if symbol is None else " Everything else is untouched -- resumes normally."
        except Exception as exc:  # noqa: BLE001
            await message.channel.send(f"{content} failed: {exc}")
            return

        closed = result["closed_symbols"]
        still_open = result["still_open"]
        orphans = {name: syms for name, syms in still_open.items() if syms}

        if closed:
            base = f"Sent close for {len(closed)} position(s) on PineConnector: {', '.join(closed)}."
        else:
            base = "No open PineConnector positions to close."

        if orphans:
            orphan_lines = "; ".join(f"{name}: {', '.join(syms)}" for name, syms in orphans.items())
            await message.channel.send(
                f"{base}{suffix}\n⚠️ STILL OPEN after verification -- check manually: {orphan_lines}"
            )
        else:
            confirmed = " Verified clear on FundedNext." if closed else ""
            await message.channel.send(f"{base}{suffix}{confirmed}")

    async def _run() -> None:
        try:
            await client.start(DISCORD_BOT_TOKEN)
        except Exception as exc:  # noqa: BLE001
            print(f"[discord_bot_listener] failed to start: {exc}")

    asyncio.create_task(_run())
