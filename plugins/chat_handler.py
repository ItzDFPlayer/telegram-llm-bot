"""
Main DM/group message handling and the reply pipeline.

Decides whether a message is addressed to the bot, prepares the prompt
(context quoting + sender tagging), then calls the model and replies.
"""
import asyncio
import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes, MessageHandler, filters

import config
from core import state
from core.llm import get_ai_response
from core.memory import add_to_history, DM_TOKEN_BUDGET, remembered_text
from core.messages import resolve_thread_id, find_bot_mention, strip_mention

logger = logging.getLogger("bot.plugins.chat")

try:
    import telegramify_markdown
except ImportError:
    telegramify_markdown = None


async def _fetch_user_about(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Optional[str]:
    """Fetch a user's Telegram bio ('about'), or None if unavailable."""
    try:
        chat = await context.bot.get_chat(user_id)
        return getattr(chat, "bio", None)
    except Exception as e:
        logger.debug(f"Could not fetch bio for user {user_id}: {e}")
        return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    chat = update.effective_chat
    message = update.message
    # Telegram puts the text of a photo message in `caption`, not `text`.
    # The current LLM is text-only, so the caption is what gets sent to it.
    text = message.text or message.caption
    thread_id = resolve_thread_id(message)  # None for main chat or non-forum reply chains

    # ----- PRIVATE CHATS -----
    if chat.type == "private":
        if user.id not in config.WHITELIST_USERS:
            logger.info(f"❌ Non-whitelisted user {user.id} (@{user.username}) tried to DM.")
            return

        if text:
            if message.photo:
                logger.info(f"🖼️ DM photo from {user.id} (@{user.username}) with caption: {text[:200]}")
            else:
                logger.info(f"💬 DM from {user.id} (@{user.username}): {text[:200]}")

            # On the first message of a DM (or right after /clear), prepend the
            # sender's name and Telegram bio so the model has profile context.
            key = (chat.id, thread_id)
            if not state.conversation_history.get(key):
                name = user.full_name or (f"@{user.username}" if user.username else f"user{user.id}")
                about = await _fetch_user_about(context, user.id)
                profile = f"[User profile]\nName: {name}"
                if about:
                    profile += f"\nAbout: {about}"
                text = f"{profile}\n\n{text}"

            await respond(chat.id, thread_id, text, budget=DM_TOKEN_BUDGET, reply_target=message)
        else:
            await message.reply_text("I can't process media without a text/caption.")
            logger.info(f"📎 DM from {user.id} (@{user.username}) sent media without text – replied with fallback.")
        return

    # ----- GROUPS / SUPERGROUPS -----
    if chat.type in ("group", "supergroup"):
        logger.info(
            f"📨 Group message in chat {chat.id} (title={chat.title!r}) thread {thread_id} "
            f"from {user.id} (@{user.username})"
        )

        # Check whitelist
        if chat.id in config.full_groups:
            allowed = True
        elif chat.id in config.thread_groups:
            allowed_threads = config.thread_groups[chat.id]
            allowed = thread_id is not None and thread_id in allowed_threads
        else:
            allowed = False

        if not allowed:
            logger.info(f"⛔ Chat {chat.id} thread {thread_id} is not whitelisted – ignoring.")
            return

        # Process normal text and photos with captions.
        if not text:
            logger.info("⏩ Message without text/caption in group – ignoring.")
            return

        bot = await context.bot.get_me()
        mention_entity = find_bot_mention(message, bot.username, bot.id)
        mentioned = mention_entity is not None

        replied_msg = message.reply_to_message
        reply_to_bot = (
            replied_msg
            and replied_msg.from_user
            and replied_msg.from_user.id == context.bot.id
        )
        reply_to_other_user = replied_msg is not None and not reply_to_bot

        logger.info(
            f"🔎 Mentioned? {mentioned}, Reply to bot? {reply_to_bot}, "
            f"Reply to other user? {reply_to_other_user}"
        )

        # Respond only when @mentioned or directly replied to the bot.
        if mentioned or reply_to_bot:
            outgoing_text = strip_mention(text, mention_entity) if mentioned else text
            if not outgoing_text:
                outgoing_text = ""

            # Determine the target to reply to and possibly include quoted context.
            if mentioned and reply_to_other_user:
                target_message = replied_msg

                replied_text = replied_msg.text or replied_msg.caption

                if replied_text:
                    quoted_author = (
                        replied_msg.from_user.first_name
                        if replied_msg.from_user
                        else "someone"
                    )
                    outgoing_text = (
                        f'{quoted_author} said: "{replied_text}"\n\n'
                        f'{outgoing_text}'
                    )
            else:
                target_message = message

            # Tag normal group messages with the current sender's name.
            # When quoting another user, their name is already included in
            # "Alice said:", so don't add another prefix.
            if not (mentioned and reply_to_other_user):
                sender_name = user.first_name or (
                    f"@{user.username}" if user.username else f"user{user.id}"
                )
                outgoing_text = f"{sender_name} said: {outgoing_text}"

            await respond(chat.id, thread_id, outgoing_text, budget=config.GROUP_TOKEN_BUDGET, reply_target=target_message)
        else:
            logger.info("⏩ Not addressed to bot – ignoring.")
        return

    logger.debug(f"Unhandled chat type: {chat.type}")


async def respond(chat_id: int, thread_id: Optional[int], user_text: str, budget: int, reply_target):
    """Add the user's message to history, call the model, store and send the reply."""
    add_to_history(chat_id, thread_id, "user", user_text, budget)
    key = (chat_id, thread_id)
    messages = [{"role": "system", "content": config.SYSTEM_PROMPT}]
    remembered = remembered_text(chat_id, thread_id)
    if remembered:
        messages.append({"role": "system", "content": remembered})
    messages += state.conversation_history.get(key, [])
    # Run the blocking HTTP call in a worker thread so a slow model doesn't
    # stall the event loop (health checks, other messages, shutdown, etc.).
    response_text = await asyncio.to_thread(get_ai_response, messages)
    if response_text is None:
        # Don't persist the error fallback into history — keep only the user's message.
        await send_reply(reply_target, "Sorry, I'm having trouble thinking right now.")
        return
    add_to_history(chat_id, thread_id, "assistant", response_text, budget)
    await send_reply(reply_target, response_text)


async def send_reply(reply_target, text: str):
    """
    Send the model's reply rendered as Telegram MarkdownV2 when possible.
    Falls back to plain text if conversion fails.
    """
    if telegramify_markdown is not None:
        try:
            formatted = telegramify_markdown.markdownify(text)
            await reply_target.reply_text(formatted, parse_mode=ParseMode.MARKDOWN_V2)
            return
        except BadRequest as e:
            logger.warning(f"⚠️ MarkdownV2 send failed ({e}); falling back to plain text.")
        except Exception as e:
            logger.warning(f"⚠️ Markdown conversion failed ({e}); falling back to plain text.")

    await reply_target.reply_text(text)


def register(app):
    # Handle both normal text and photos that contain a caption.
    # Telegram photo captions are exposed as message.caption.
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.TEXT & ~filters.PHOTO, handle_message))
