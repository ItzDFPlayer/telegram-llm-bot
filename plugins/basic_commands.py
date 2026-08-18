"""
Basic user-facing commands: /start, /clear, /system.
"""
import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import SYSTEM_PROMPT, ADMIN_IDS
from core import state
from core.memory import delete_history_file
from core.messages import resolve_thread_id

logger = logging.getLogger("bot.plugins.basic")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    thread_id = resolve_thread_id(update.message)
    if thread_id:
        await update.message.reply_text(
            f"Chat ID: `{chat_id}`\nThread ID: `{thread_id}`\n\n"
            "Add the chat ID to WHITELIST_GROUPS if this is a group.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"Chat ID: `{chat_id}`\n\nAdd this to your WHITELIST_GROUPS if this is a group.",
            parse_mode="Markdown",
        )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    chat_id = chat.id
    thread_id = resolve_thread_id(update.message)

    # In groups, only admins may clear history
    if chat.type != "private" and user.id not in ADMIN_IDS:
        await update.message.reply_text("Only bot admins can clear history in groups.")
        return

    key = (chat_id, thread_id)
    if key in state.conversation_history:
        del state.conversation_history[key]
        delete_history_file(chat_id, thread_id)
        logger.info(f"🗑️ Cleared conversation history for chat {chat_id} thread {thread_id} by user {user.id}")
        await update.message.reply_text("Memory cleared for this conversation.")
    else:
        await update.message.reply_text("No history to clear.")


async def system_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the currently configured system prompt."""
    chat = update.effective_chat
    user = update.effective_user
    # In groups, don't leak the system prompt to non-admins.
    if chat.type != "private" and user.id not in ADMIN_IDS:
        await update.message.reply_text("Only bot admins can view the system prompt in groups.")
        return
    await update.message.reply_text(f"Current system prompt:\n\n{SYSTEM_PROMPT}")


def register(app):
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("system", system_command))
