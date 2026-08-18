"""
Telegram bot entry point.

Thin launcher: builds the Application, initializes runtime state, registers
feature plugins, and starts polling. All actual logic lives in config.py,
core/, and plugins/.
"""
import os
import signal
import sys
import threading

from telegram.ext import ApplicationBuilder

import config
from core import state, llm, memory
from plugins import register_all, chat_handler
from plugins.status import on_stop

logger = config.logger


def _listen_for_esc():
    """
    Background thread: waits for a single ESC keypress on the controlling
    terminal, then sends SIGINT to this process to trigger the same graceful
    shutdown path Ctrl+C uses (which runs on_stop()).

    Only works when stdin is an actual interactive terminal — if the bot is
    running as a service/daemon with no TTY attached, this silently does
    nothing rather than erroring, since there's no keyboard to listen to
    in that case anyway.
    """
    if not sys.stdin.isatty():
        logger.info("⌨️  No interactive terminal detected — ESC-to-quit is unavailable in this environment.")
        return

    try:
        if os.name == "nt":
            import msvcrt
            while True:
                ch = msvcrt.getch()
                if ch == b"\x1b":
                    break
        else:
            import termios
            import tty
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                while True:
                    ch = sys.stdin.read(1)
                    if ch == "\x1b":
                        break
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except Exception as e:
        logger.warning(f"⚠️ ESC-key listener stopped unexpectedly: {e}")
        return

    logger.info("⌨️  ESC pressed — shutting down...")
    os.kill(os.getpid(), signal.SIGINT)


async def startup_status(app):
    """
    Runs once at startup (post_init), before polling begins: checks whether the
    model backend is reachable and immediately sets the bot's description, so
    users see the real status right away instead of waiting for the first
    scheduled health check.
    """
    logger.info("🩺 Running initial model health check at startup...")
    try:
        await llm.refresh_status()
    except Exception:
        logger.exception("⚠️ Initial health check failed")


def main():
    if config.BOT_TOKEN == "YOUR_BOTFATHER_TOKEN":
        logger.error("Please set your BOT_TOKEN (via environment variable or hardcoded).")
        sys.exit(1)

    logger.info(f"👤 WHITELIST_USERS: {config.WHITELIST_USERS}")
    logger.info(f"👥 Full groups (any thread): {sorted(config.full_groups)}")
    logger.info(f"👥 Thread-specific groups: {config.thread_groups}")
    logger.info(f"🔑 ADMIN_IDS: {config.ADMIN_IDS}")

    memory.load_all_history(memory.DM_TOKEN_BUDGET)
    memory.load_all_remembered()

    if memory._ENC is None:
        logger.warning(
            "tiktoken not installed – falling back to a rough char-count token estimate. "
            "Run `pip install tiktoken` for more accurate context budgeting."
        )

    if chat_handler.telegramify_markdown is None:
        logger.warning(
            "telegramify-markdown not installed – replies will be sent as plain text. "
            "Run `pip install telegramify-markdown` to enable MarkdownV2 formatting."
        )

    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(startup_status)
        .post_stop(on_stop)
        .build()
    )
    state.bot_instance = app.bot

    register_all(app)

    threading.Thread(target=_listen_for_esc, daemon=True, name="esc-listener").start()

    logger.info("🤖 Bot is running... (press ESC in this terminal to shut down gracefully)")
    logger.info(
        "⚠️  Reminder: if the bot never even logs group messages that don't @mention it or "
        "reply to it, group PRIVACY MODE is likely ON. Disable it via @BotFather -> "
        "/mybots -> select bot -> Bot Settings -> Group Privacy -> Turn off, "
        "then remove and re-add the bot to the group."
    )
    app.run_polling()


if __name__ == "__main__":
    main()

