"""
Shared Telegram message helpers (thread resolution + mention handling).
"""
from typing import Optional

from telegram import MessageEntity


def resolve_thread_id(message) -> Optional[int]:
    """
    Returns the forum topic ID for this message, or None.

    Telegram sets message.message_thread_id on ANY reply message — even in
    ordinary (non-forum) groups and DMs, where it just mirrors the reply
    chain and isn't a real topic. Only message.is_topic_message == True
    means it's an actual forum topic; otherwise this is a false thread_id
    that must be ignored.
    """
    if message and message.is_topic_message:
        return message.message_thread_id
    return None


def find_bot_mention(message, bot_username: str, bot_id: int) -> Optional[MessageEntity]:
    """
    UTF-16-safe mention detection.
    Returns the matching MessageEntity (which carries the exact UTF-16
    offset/length needed to strip it), or None.
    """
    # Text messages carry entities in `entities`; photo captions in
    # `caption_entities`. Both must be checked or mentions in captions are missed.
    if message.text:
        entities = message.parse_entities(types=[MessageEntity.MENTION, MessageEntity.TEXT_MENTION])
    elif message.caption:
        entities = message.parse_caption_entities(types=[MessageEntity.MENTION, MessageEntity.TEXT_MENTION])
    else:
        return None

    for entity, entity_text in entities.items():
        if entity.type == MessageEntity.MENTION and entity_text.lstrip("@").lower() == bot_username.lower():
            return entity
        if entity.type == MessageEntity.TEXT_MENTION and entity.user and entity.user.id == bot_id:
            return entity
    return None


def _utf16_index_to_py(s: str, utf16_index: int) -> int:
    """Convert a UTF-16 code-unit offset (Telegram's indexing) to a Python str index."""
    i = 0
    units = 0
    while i < len(s) and units < utf16_index:
        units += 2 if ord(s[i]) > 0xFFFF else 1
        i += 1
    return i


def strip_mention(text: str, entity: MessageEntity) -> str:
    """Remove the bot's @mention (identified by its entity) and clean whitespace."""
    start = _utf16_index_to_py(text, entity.offset)
    end = _utf16_index_to_py(text, entity.offset + entity.length)
    cleaned = text[:start] + text[end:]
    return " ".join(cleaned.split())
