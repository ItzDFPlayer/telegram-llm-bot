"""
Runtime-shared mutable state and long-lived clients.

Kept separate from config.py so features can mutate runtime values (model
name, bot instance, history, online state) without re-reading config.
"""
from typing import Optional

from telegram import Bot
from openai import OpenAI

from config import OPENAI_BASE, OPENAI_API_KEY, OPENAI_TIMEOUT

# Main completion client (generous but finite timeout).
client = OpenAI(base_url=OPENAI_BASE, api_key=OPENAI_API_KEY, timeout=OPENAI_TIMEOUT)

# Lightweight client with a short timeout just for health checks, so a hung
# backend can't block the periodic status job for the full default timeout.
health_client = OpenAI(base_url=OPENAI_BASE, api_key=OPENAI_API_KEY, timeout=5.0)

# Active model name — initialized in bot.py and refreshed by the status feature.
MODEL_NAME = "unknown"

# Set once the Application is built, so any function (job callbacks, the ESC
# listener, the shutdown hook) can push a description update without needing
# a ContextTypes.DEFAULT_TYPE / Update in scope.
bot_instance: Optional[Bot] = None

# Last-known online state so we only call the Telegram API when it changes.
last_online_state: Optional[bool] = None

# Conversation history keyed by (chat_id, thread_id); thread_id is None for main chat.
conversation_history: dict[tuple[int, Optional[int]], list[dict]] = {}

# Persistent /remember instructions keyed by (chat_id, thread_id).
# Value is a list of {"id": int, "text": str} preserving insertion order.
remembered: dict[tuple[int, Optional[int]], list[dict]] = {}
