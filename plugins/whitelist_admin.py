"""
Admin-only whitelist management commands.

Commands: /allow_user, /remove_user, /allow_group, /remove_group,
/allow_thread, /remove_thread, /whitelist_status
"""
import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

import config
from core.messages import resolve_thread_id

logger = logging.getLogger("bot.plugins.whitelist")


async def _require_admin(update: Update) -> bool:
    user = update.effective_user
    if not config.is_admin(user.id):
        logger.info(f"❌ Non-admin {user.id} (@{user.username}) tried to use an admin command.")
        await update.message.reply_text("You're not authorized to use this command.")
        return False
    return True


async def allow_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return

    # Resolve the target user: explicit ID, or the user whose message was replied to.
    if context.args:
        try:
            uid = int(context.args[0])
        except ValueError:
            await update.message.reply_text("That doesn't look like a valid numeric user ID.")
            return
    else:
        replied = update.message.reply_to_message if update.message else None
        if replied and replied.from_user:
            uid = replied.from_user.id
        else:
            await update.message.reply_text(
                "Usage: /allow_user <user_id>\n"
                "or reply to a user's message with /allow_user to whitelist them."
            )
            return

    if uid in config.WHITELIST_USERS:
        await update.message.reply_text(f"User {uid} is already whitelisted.")
        return
    config.WHITELIST_USERS.append(uid)
    config.persist_id_list_env("WHITELIST_USERS", config.WHITELIST_USERS)
    logger.info(f"✅ Admin {update.effective_user.id} whitelisted user {uid}")
    await update.message.reply_text(f"User {uid} whitelisted.")


async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove_user <user_id>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("That doesn't look like a valid numeric user ID.")
        return

    if uid not in config.WHITELIST_USERS:
        await update.message.reply_text(f"User {uid} isn't whitelisted.")
        return
    config.WHITELIST_USERS.remove(uid)
    config.persist_id_list_env("WHITELIST_USERS", config.WHITELIST_USERS)
    logger.info(f"🗑️ Admin {update.effective_user.id} removed user {uid} from whitelist")
    await update.message.reply_text(f"User {uid} removed from whitelist.")


async def allow_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Adds a group to the full whitelist (any thread).
    Usage: /allow_group <group_id>  (or run in the group itself without argument)
    """
    if not await _require_admin(update):
        return
    gid = _resolve_group_id(update, context)
    if gid is None:
        await update.message.reply_text(
            "Usage: /allow_group <group_id>\n(or run this command inside the group itself, with no argument)"
        )
        return

    if gid in config.full_groups:
        await update.message.reply_text(f"Group {gid} is already fully whitelisted.")
        return
    config.full_groups.add(gid)
    # Remove any thread-specific entries for this group (if any) to avoid duplication
    if gid in config.thread_groups:
        del config.thread_groups[gid]
    config.persist_allow_groups()
    logger.info(f"✅ Admin {update.effective_user.id} whitelisted group {gid} (any thread)")
    await update.message.reply_text(f"Group {gid} whitelisted for all threads.")


async def remove_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Removes a group from the whitelist entirely (both full and thread-specific).
    Usage: /remove_group <group_id>  (or run in the group itself without argument)
    """
    if not await _require_admin(update):
        return
    gid = _resolve_group_id(update, context)
    if gid is None:
        await update.message.reply_text(
            "Usage: /remove_group <group_id>\n(or run this command inside the group itself, with no argument)"
        )
        return

    removed = False
    if gid in config.full_groups:
        config.full_groups.remove(gid)
        removed = True
    if gid in config.thread_groups:
        del config.thread_groups[gid]
        removed = True
    if not removed:
        await update.message.reply_text(f"Group {gid} is not whitelisted.")
        return
    config.persist_allow_groups()
    logger.info(f"🗑️ Admin {update.effective_user.id} removed group {gid} from whitelist")
    await update.message.reply_text(f"Group {gid} removed from whitelist.")


async def allow_thread_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Whitelists just the specific forum topic thread this command is run in,
    without whitelisting the whole group. Must be run inside that thread.
    """
    if not await _require_admin(update):
        return
    chat = update.effective_chat
    message = update.message
    if chat.type not in ("group", "supergroup"):
        await message.reply_text("Run this command inside the group thread you want to whitelist.")
        return

    thread_id = resolve_thread_id(message)
    if thread_id is None:
        await message.reply_text(
            "This command must be run inside a forum topic thread (not the group's main chat). "
            "Use /allow_group instead to whitelist the whole group."
        )
        return

    gid = chat.id
    if gid in config.full_groups:
        await message.reply_text(f"Group {gid} is already fully whitelisted — all its threads are already allowed.")
        return

    threads = config.thread_groups.setdefault(gid, set())
    if thread_id in threads:
        await message.reply_text(f"Thread {thread_id} in group {gid} is already whitelisted.")
        return
    threads.add(thread_id)
    config.persist_allow_groups()
    logger.info(f"✅ Admin {update.effective_user.id} whitelisted thread {thread_id} in group {gid}")
    await message.reply_text(f"Thread {thread_id} in group {gid} whitelisted.")


async def remove_thread_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes just this specific forum topic thread from the whitelist. Must be run inside that thread."""
    if not await _require_admin(update):
        return
    chat = update.effective_chat
    message = update.message
    if chat.type not in ("group", "supergroup"):
        await message.reply_text("Run this command inside the group thread you want to unwhitelist.")
        return

    thread_id = resolve_thread_id(message)
    if thread_id is None:
        await message.reply_text("This command must be run inside a forum topic thread (not the group's main chat).")
        return

    gid = chat.id
    if gid not in config.thread_groups or thread_id not in config.thread_groups[gid]:
        await message.reply_text(f"Thread {thread_id} in group {gid} isn't whitelisted.")
        return
    config.thread_groups[gid].discard(thread_id)
    if not config.thread_groups[gid]:
        del config.thread_groups[gid]
    config.persist_allow_groups()
    logger.info(f"🗑️ Admin {update.effective_user.id} removed thread {thread_id} in group {gid} from whitelist")
    await message.reply_text(f"Thread {thread_id} in group {gid} removed from whitelist.")


def _resolve_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Use an explicit argument if given, otherwise fall back to the current chat if it's a group."""
    if context.args:
        try:
            return int(context.args[0])
        except ValueError:
            return None
    if update.effective_chat.type in ("group", "supergroup"):
        return update.effective_chat.id
    return None


async def whitelist_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    lines = ["**Current whitelist:**"]
    lines.append(f"**Users:** {config.WHITELIST_USERS}")
    lines.append("**Groups:**")
    if config.full_groups:
        lines.append(f"  Full (any thread): {sorted(config.full_groups)}")
    if config.thread_groups:
        for gid, threads in config.thread_groups.items():
            threads_str = ", ".join(str(t) for t in sorted(threads) if t is not None)
            lines.append(f"  Group {gid} -> threads: {threads_str}")
    if not config.full_groups and not config.thread_groups:
        lines.append("  (none)")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def register(app):
    commands = {
        "allow_user": allow_user_command,
        "remove_user": remove_user_command,
        "allow_group": allow_group_command,
        "remove_group": remove_group_command,
        "allow_thread": allow_thread_command,
        "remove_thread": remove_thread_command,
        "whitelist_status": whitelist_status_command,
    }
    for name, handler in commands.items():
        app.add_handler(CommandHandler(name, handler))
