"""Telegram bridge for Shelby (Hermes-inspired multi-platform gateway).

Lets Captain text Shelby from anywhere and get the reply back as a
voice note + text. Shelby runs the whole conversation on the local
machine, the bridge just forwards messages over Telegram.

STATUS: Scaffold. The plumbing is here but the runtime is opt-in via
SHELBY_TELEGRAM_TOKEN. Without that token set, run_bridge() is a no-op
and the bridge stays dormant. This keeps the main shelby-demo voice
loop unaffected unless Captain explicitly enables remote access.

Setup when ready:
  1. Talk to @BotFather on Telegram, /newbot, get a token.
  2. Get your own chat_id by messaging the bot, then visiting
     https://api.telegram.org/bot<TOKEN>/getUpdates
  3. Set both:
       SHELBY_TELEGRAM_TOKEN=<bot_token_from_botfather>
       SHELBY_TELEGRAM_CHAT_ID=<your_numeric_chat_id>
     (CHAT_ID acts as an allow-list — Shelby only responds to that
     user, so a stranger who finds your bot can't drive it.)
  4. pip install python-telegram-bot
  5. The web_cli boot path will start the bridge alongside the voice
     loop on next launch.
"""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Optional


TOKEN_ENV = "SHELBY_TELEGRAM_TOKEN"
CHAT_ID_ENV = "SHELBY_TELEGRAM_CHAT_ID"


def is_configured() -> bool:
    return bool(os.environ.get(TOKEN_ENV) and os.environ.get(CHAT_ID_ENV))


ProcessFn = Callable[[str], Awaitable[str]]


async def run_bridge(process: ProcessFn) -> None:
    """Run the Telegram polling loop until cancelled.

    `process(user_text)` is the function the bridge calls for each
    incoming message. It should return the assistant's text reply,
    which the bridge then sends back. Conventionally this wraps a
    HybridBrain.process_stream() collect + speak_async() so the reply
    is also heard on the local machine.
    """
    token = os.environ.get(TOKEN_ENV)
    allowed_chat_id = os.environ.get(CHAT_ID_ENV)
    if not token or not allowed_chat_id:
        print("[telegram] disabled (no token / chat_id)", flush=True)
        return

    try:
        from telegram import Update
        from telegram.ext import (
            Application,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )
    except ImportError:
        print(
            "[telegram] python-telegram-bot not installed; "
            "pip install python-telegram-bot to enable",
            flush=True,
        )
        return

    try:
        allowed_int = int(allowed_chat_id)
    except ValueError:
        print(f"[telegram] {CHAT_ID_ENV} must be an integer chat id", flush=True)
        return

    async def _guard_and_process(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.effective_chat or update.effective_chat.id != allowed_int:
            # Silent drop. A stranger finding the bot username gets no signal
            # back that the bot is alive.
            return
        text = (update.message.text or "").strip() if update.message else ""
        if not text:
            return
        try:
            reply = await process(text)
        except Exception as exc:
            reply = f"error: {exc}"
        if reply:
            # Telegram caps messages at 4096 chars.
            await update.message.reply_text(reply[:4000])

    async def _start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.effective_chat or update.effective_chat.id != allowed_int:
            return
        await update.message.reply_text(
            "Shelby online, Captain. Send me a message and I'll handle it on "
            "your machine."
        )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", _start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _guard_and_process))

    print(
        f"[telegram] bridge online for chat_id={allowed_int}",
        flush=True,
    )
    try:
        # run_polling is sync-blocking on its own loop; use run_polling
        # in a task and shield cancellation.
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        # Idle until the surrounding task is cancelled.
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception:
            pass
