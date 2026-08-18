"""
Persistent per-chat/thread instructions: /remember, /forget, /remember_list.

Instructions are stored per (chat, thread), given a unique id, and injected
before every model prompt without being written into conversation history.
"""
import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

import config
from core import state
from core.memory import add_remembered, remove_remembered
from core.messages import resolve_thread_id

logger = logging.getLogger("bot.plugins.remember")


async def _authorized(update: Update) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if chat.type == "private":
        return user.id in config.WHITELIST_USERS
    # In groups, only admins may manage instructions.
    return user.id in config.ADMIN_IDS


async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        await update.message.reply_text("You're not authorized to use this command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /remember <instruction>")
        return

    text = " ".join(context.args).strip()
    chat = update.effective_chat
    thread_id = resolve_thread_id(update.message)
    instruction_id = add_remembered(chat.id, thread_id, text)
    logger.info(f"🧠 User {update.effective_user.id} added instruction #{instruction_id} for chat {chat.id} thread {thread_id}")
    await update.message.reply_text(f"Remembered instruction #{instruction_id}.")


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        await update.message.reply_text("You're not authorized to use this command.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /forget <id>")
        return
    try:
        instruction_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("That doesn't look like a valid instruction ID.")
        return

    chat = update.effective_chat
    thread_id = resolve_thread_id(update.message)
    if remove_remembered(chat.id, thread_id, instruction_id):
        await update.message.reply_text(f"Instruction #{instruction_id} forgotten.")
    else:
        await update.message.reply_text(f"No instruction #{instruction_id} found.")


async def remember_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _authorized(update):
        await update.message.reply_text("You're not authorized to use this command.")
        return

    chat = update.effective_chat
    thread_id = resolve_thread_id(update.message)
    items = state.remembered.get((chat.id, thread_id), [])
    if not items:
        await update.message.reply_text("No remembered instructions for this chat.")
        return

    lines = ["**Remembered instructions:**"]
    for item in items:
        lines.append(f"#{item['id']} — {item['text']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def register(app):
    app.add_handler(CommandHandler("remember", remember_command))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(CommandHandler("remember_list", remember_list_command))
