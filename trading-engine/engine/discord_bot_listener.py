"""Discord bot listener -- lets the user type a command in Discord to
manually close every open REAL position at once, independent of the
give-back breaker. [ADD 2026-08-20, explicit user instruction]

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
specifically -- anyone else typing the command in a shared server/channel
is silently ignored (no reply, no hint the command exists).

Scope -- deliberately different from the give-back breaker: this closes
BOTH real relays unconditionally, including TradeSgnl. The "TradeSgnl
must never be blocked by the give-back breaker" rule was specifically
about an automatic P&L-based circuit breaker overriding a data-source
account without asking. This is the opposite: a direct, in-the-moment
command FROM the account owner. Paper is never touched either way -- it
stays a clean, continuous research baseline regardless of what happens on
the real side, same reasoning as the give-back breaker.
"""

from __future__ import annotations

import asyncio
import os
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_AUTHORIZED_USER_ID = int(os.getenv("DISCORD_AUTHORIZED_USER_ID", "0"))
CLOSE_ALL_COMMAND = "!closeall"

_client = None  # type: Optional["discord.Client"]


async def close_all_real_positions() -> List[str]:
    """Closes every currently open position on BOTH real relays. Paper is
    untouched -- see module docstring. Returns the symbols that closed on
    at least one relay (a trade whose entry was never confirmed on either
    relay -- e.g. today's give-back-suppressed GBPAUD -- correctly no-ops
    here too, same _confirmed_open_ids guard both relays already use for
    the give-back breaker's own close-all path)."""
    from engine.paper_broker import broker
    from engine import tradesgnl_relay, pineconnector_relay

    closed_symbols: List[str] = []
    for t in list(broker.open_positions):
        sent_tradesgnl = await tradesgnl_relay.send_close(t)
        sent_pineconnector = await pineconnector_relay.send_close(t)
        if sent_tradesgnl or sent_pineconnector:
            closed_symbols.append(t["symbol"])
    return closed_symbols


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
        if message.content.strip().lower() != CLOSE_ALL_COMMAND:
            return
        try:
            closed = await close_all_real_positions()
        except Exception as exc:  # noqa: BLE001
            await message.channel.send(f"close-all failed: {exc}")
            return
        if closed:
            await message.channel.send(
                f"Closed {len(closed)} position(s) on both real accounts: {', '.join(closed)}"
            )
        else:
            await message.channel.send("No open real positions to close.")

    async def _run() -> None:
        try:
            await client.start(DISCORD_BOT_TOKEN)
        except Exception as exc:  # noqa: BLE001
            print(f"[discord_bot_listener] failed to start: {exc}")

    asyncio.create_task(_run())
