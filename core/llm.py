"""
Model backend integration: completions, health probes, and the dynamic bot description.
"""
import asyncio
import logging
from typing import Optional

from telegram.ext import ContextTypes

from config import BOT_DESCRIPTION_INTRO
from core import state

logger = logging.getLogger("bot.llm")


def fetch_model_name() -> str:
    """
    Retrieve the first available model from the API.
    Returns a fallback string if the API is unreachable or returns no models.
    """
    try:
        models = state.health_client.models.list()
        if models.data:
            return models.data[0].id
    except Exception as e:
        logger.warning(f"Could not fetch model list: {e}")
    return "llama3"   # fallback


def build_bot_description(online: bool) -> str:
    lines = [BOT_DESCRIPTION_INTRO]
    if online:
        lines.append("Current status: 🟢 Online")
        lines.append(f"Running model: {state.MODEL_NAME}")
    else:
        lines.append("Current status: 🔴 Offline")
    return "\n".join(lines)


def check_model_online() -> bool:
    """
    Blocking network probe against the model backend. Uses a lightweight
    models-list call (works against Ollama/llama.cpp/LM Studio's OpenAI-
    compatible /v1/models endpoint) rather than a real completion, so it
    doesn't burn tokens or wait on generation just to check reachability.
    """
    try:
        state.health_client.models.list()
        return True
    except Exception as e:
        logger.debug(f"🔴 Model health check failed: {e}")
        return False


async def push_description_update(online: bool, force: bool = False):
    """Update the bot's Telegram description if the online/offline state actually changed."""
    if not force and online == state.last_online_state:
        return
    state.last_online_state = online
    if state.bot_instance is None:
        return
    try:
        await state.bot_instance.set_my_description(description=build_bot_description(online))
        await state.bot_instance.set_my_short_description(short_description=build_bot_description(online))
        logger.info(f"📝 Bot description updated: {'🟢 Online' if online else '🔴 Offline'}")
    except Exception as e:
        logger.error(f"⚠️ Failed to update bot description: {e}")


async def refresh_status() -> bool:
    """Probe the backend once and publish a description update. Returns online state."""
    state.MODEL_NAME = await asyncio.to_thread(fetch_model_name)
    online = await asyncio.to_thread(check_model_online)
    logger.info(f"🩺 Health check result: {'🟢 online' if online else '🔴 offline'}")
    await push_description_update(online)
    return online


async def health_check_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Periodic JobQueue callback: probe the backend and push a description update if it changed.
    Wrapped in its own try/except with logger.exception so a failure here is always visible.
    """
    logger.info("🩺 Running scheduled model health check...")
    try:
        await refresh_status()
    except Exception:
        logger.exception("⚠️ health_check_job crashed unexpectedly")


def get_ai_response(messages: list[dict]) -> Optional[str]:
    """Call the model and return its reply, or None if it failed or produced no text."""
    try:
        response = state.client.chat.completions.create(
            model=state.MODEL_NAME,
            messages=messages,
        )
        content = response.choices[0].message.content
        return content if content is not None else None
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return None
