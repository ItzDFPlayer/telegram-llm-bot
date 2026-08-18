"""
Central configuration.

Loads environment variables from a .env file and exposes typed config values,
the whitelist state, and persistence helpers used across the bot.
"""
import os
import sys
import logging
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv, find_dotenv, set_key

# Resolve the .env file being used so admin commands can persist changes to the
# same file load_dotenv() read from. Falls back to "./.env" if none exists yet
# (set_key will create it).
ENV_PATH = find_dotenv() or ".env"
load_dotenv(ENV_PATH)

# ------------------- LOGGING CONFIGURATION -------------------
# Logs are written both to the terminal and to a separate file.
# Set LOG_FILE in .env to change the file location/name.
LOG_FILE = os.getenv("LOG_FILE", "bot.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("bot")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# ------------------- CORE CONFIGURATION -------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOTFATHER_TOKEN")
OPENAI_BASE = os.getenv("OPENAI_BASE", "http://localhost:11434/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "not-needed")

# Generous but finite timeout so a hung backend can't block message handling
# for the OpenAI SDK's default (which is very long).
try:
    OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "300"))
except ValueError:
    logger.warning("⚠️ Invalid OPENAI_TIMEOUT value; falling back to 300 seconds.")
    OPENAI_TIMEOUT = 300.0

# ------------------- WEB SEARCH CONFIGURATION -------------------
# Provider used by the model's web_search tool:
#   "duckduckgo" – free, no API key (requires: pip install ddgs)
#   "brave"      – free tier, needs BRAVE_API_KEY (https://brave.com/search/api/)
#   "disabled"   – turns web search off entirely
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "duckduckgo").strip().lower()
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
# Optional: point at your own SearXNG instance (e.g. "https://searx.mydomain").
# When empty, a few public instances are tried as a fallback.
SEARXNG_URL = os.getenv("SEARXNG_URL", "").strip()
SEARCH_MAX_RESULTS = int(os.getenv("SEARCH_MAX_RESULTS", "3"))
SEARCH_TIMEOUT = float(os.getenv("SEARCH_TIMEOUT", "10"))
# Cap how many characters of search results are fed back to the model per query,
# so a large result set can't blow up the context window. Kept small because NPU
# backends have limited KV caches and error out on long prompts.
SEARCH_RESULT_LIMIT_CHARS = int(os.getenv("SEARCH_RESULT_LIMIT_CHARS", "1200"))

# ------------------- SYSTEM PROMPT -------------------
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful, concise assistant chatting with users on Telegram. "
    "Keep answers clear and to the point. In group chats, user messages are "
    "prefixed with the sender's name like 'Alice: message' so you can tell "
    "different speakers apart — this is just labeling, not part of what they "
    "said, so don't include a similar 'Name:' prefix in your own replies."
    "Reply in the same language as sender uses.",
)

# ------------------- MEMORY CONFIGURATION -------------------
# Tunable via .env — important on small-context backends (e.g. NPU): set
# MODEL_MAX_CONTEXT to match the model's real KV-cache size to avoid
# "KV-cache update out of range" errors during long conversations.
MODEL_MAX_CONTEXT = int(os.getenv("MODEL_MAX_CONTEXT", "8000"))  # total context window of the model
RESPONSE_RESERVE = int(os.getenv("RESPONSE_RESERVE", "1000"))  # tokens left free for the model's reply
GROUP_TOKEN_BUDGET = int(os.getenv("GROUP_TOKEN_BUDGET", "4000"))  # groups auto-prune once history exceeds this

# ------------------- STATUS / DESCRIPTION -------------------
BOT_DESCRIPTION_INTRO = os.getenv("BOT_DESCRIPTION_INTRO", "AI model running on Xiaomi 15")
HEALTH_CHECK_INTERVAL_SECONDS = int(os.getenv("HEALTH_CHECK_INTERVAL_SECONDS", "30"))

# ------------------- HISTORY STORAGE -------------------
HISTORY_DIR = Path(os.getenv("HISTORY_DIR", "./chat_history"))
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# ------------------- REMEMBER STORAGE -------------------
REMEMBER_DIR = Path(os.getenv("REMEMBER_DIR", "./remembered"))
REMEMBER_DIR.mkdir(parents=True, exist_ok=True)

# ------------------- WHITELIST PARSING -------------------
def _parse_id_list(env_var: str, default: list[int]) -> list[int]:
    """Parse a comma-separated list of integer IDs from an env var."""
    raw = os.getenv(env_var)
    if not raw:
        return default
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            logger.warning(f"⚠️ Ignoring invalid ID {part!r} in {env_var}")
    return ids


def _parse_allow_groups(env_var: str) -> list[str]:
    """
    Parse WHITELIST_GROUPS into a list of strings.
    Each entry is either a plain group ID (e.g. "-123") or "group_id:thread_id" (e.g. "-123:42").
    """
    raw = os.getenv(env_var)
    if not raw:
        return []
    entries = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        entries.append(part)
    return entries


WHITELIST_USERS = _parse_id_list("WHITELIST_USERS", [0])

# WHITELIST_GROUPS is a list of strings, each may be group_id or group_id:thread_id
_allow_group_entries = _parse_allow_groups("WHITELIST_GROUPS")

# Build structures for quick lookup:
# - full_groups: set of group IDs that are fully whitelisted (any thread)
# - thread_groups: dict mapping group_id -> set of allowed thread_ids (including None for main chat)
full_groups: set[int] = set()
thread_groups: dict[int, set[Optional[int]]] = {}

for entry in _allow_group_entries:
    if ":" in entry:
        parts = entry.split(":", 1)
        try:
            gid = int(parts[0])
            tid = int(parts[1]) if parts[1].strip() else None
        except ValueError:
            logger.warning(f"⚠️ Ignoring malformed whitelist entry: {entry}")
            continue
        thread_groups.setdefault(gid, set()).add(tid)
    else:
        try:
            gid = int(entry)
            full_groups.add(gid)
        except ValueError:
            logger.warning(f"⚠️ Ignoring invalid group ID: {entry}")

# Users allowed to run admin commands
ADMIN_IDS = _parse_id_list("ADMIN_IDS", [])


def persist_allow_groups():
    """
    Write the current whitelist group entries (full_groups and thread_groups)
    back to the .env file as a comma-separated string.
    """
    entries = []
    for gid in full_groups:
        entries.append(str(gid))
    for gid, threads in thread_groups.items():
        for tid in threads:
            if tid is None:
                continue
            entries.append(f"{gid}:{tid}")
    value = ",".join(entries)
    try:
        set_key(ENV_PATH, "WHITELIST_GROUPS", value)
        logger.info(f"💾 Persisted WHITELIST_GROUPS to {ENV_PATH}: {value}")
    except OSError as e:
        logger.error(f"⚠️ Failed to persist WHITELIST_GROUPS: {e}")


def persist_id_list_env(env_var: str, values: list[int]):
    """Write an updated ID list back to the .env file so it survives restarts."""
    try:
        set_key(ENV_PATH, env_var, ",".join(str(v) for v in values))
        logger.info(f"💾 Persisted {env_var} to {ENV_PATH}: {values}")
    except OSError as e:
        logger.error(f"⚠️ Failed to persist {env_var} to {ENV_PATH}: {e}")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS
