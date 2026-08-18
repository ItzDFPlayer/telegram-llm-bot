"""
Per-chat/thread conversation memory: token counting, trimming, and disk persistence.
"""
import json
import logging
from pathlib import Path
from typing import Optional

from config import (
    HISTORY_DIR,
    REMEMBER_DIR,
    SYSTEM_PROMPT,
    MODEL_MAX_CONTEXT,
    RESPONSE_RESERVE,
)
from core import state

logger = logging.getLogger("bot.memory")

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
except ImportError:
    _ENC = None


def count_tokens(messages: list[dict]) -> int:
    """
    Approximate token count for a list of chat messages.
    Uses tiktoken's cl100k_base encoding as a stand-in.
    """
    if _ENC is None:
        # crude fallback: ~4 chars per token
        return sum(len(m.get("content") or "") for m in messages) // 4

    total = 0
    for m in messages:
        total += len(_ENC.encode(m.get("content") or "")) + 4  # small per-message overhead
    return total


# DMs: remember everything until /clear, but never exceed the model's real context window.
SYSTEM_PROMPT_TOKENS = count_tokens([{"content": SYSTEM_PROMPT}])
DM_TOKEN_BUDGET = MODEL_MAX_CONTEXT - RESPONSE_RESERVE - SYSTEM_PROMPT_TOKENS


def _history_path(chat_id: int, thread_id: Optional[int]) -> Path:
    """Generate filename: <chat_id>.json for main, <chat_id>_<thread_id>.json for threads."""
    if thread_id is None:
        return HISTORY_DIR / f"{chat_id}.json"
    return HISTORY_DIR / f"{chat_id}_{thread_id}.json"


def load_all_history(budget: int):
    """Load every saved per-chat/thread history file into memory at startup."""
    loaded = 0
    for path in HISTORY_DIR.glob("*.json"):
        stem = path.stem
        parts = stem.split("_")
        if len(parts) == 1:
            try:
                chat_id = int(parts[0])
                thread_id = None
            except ValueError:
                continue
        elif len(parts) == 2:
            try:
                chat_id = int(parts[0])
                thread_id = int(parts[1])
            except ValueError:
                continue
        else:
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                state.conversation_history[(chat_id, thread_id)] = data
                loaded += 1
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"⚠️ Could not load history for {chat_id}/{thread_id} from {path}: {e}")

    # Enforce the token budget on anything restored from disk so an old,
    # oversized file can't blow past the model's context on the first message.
    for key in list(state.conversation_history):
        before = len(state.conversation_history.get(key, []))
        trim_history(key[0], key[1], budget)
        if len(state.conversation_history.get(key, [])) != before:
            save_history(key[0], key[1])

    logger.info(f"💾 Loaded conversation history for {loaded} chat(s)/thread(s) from {HISTORY_DIR}")


def save_history(chat_id: int, thread_id: Optional[int]):
    """Atomically write a single chat/thread history to disk."""
    path = _history_path(chat_id, thread_id)
    tmp_path = path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state.conversation_history.get((chat_id, thread_id), []), f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
    except OSError as e:
        logger.error(f"⚠️ Failed to save history for chat {chat_id} thread {thread_id}: {e}")


def delete_history_file(chat_id: int, thread_id: Optional[int]):
    path = _history_path(chat_id, thread_id)
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.error(f"⚠️ Failed to delete history file for chat {chat_id} thread {thread_id}: {e}")


def trim_history(chat_id: int, thread_id: Optional[int], budget: int):
    """Drop oldest messages until the stored history fits within budget tokens."""
    key = (chat_id, thread_id)
    history = state.conversation_history.get(key, [])
    while history and count_tokens(history) > budget:
        removed = history.pop(0)
        logger.debug(
            f"🧹 Trimmed oldest message from {chat_id}/{thread_id} "
            f"({removed['role']}): {removed['content'][:40]!r}"
        )
    state.conversation_history[key] = history


def add_to_history(chat_id: int, thread_id: Optional[int], role: str, content: str, budget: int):
    key = (chat_id, thread_id)
    history = state.conversation_history.setdefault(key, [])
    history.append({"role": role, "content": content})
    trim_history(chat_id, thread_id, budget)
    save_history(chat_id, thread_id)


# ------------------- REMEMBER INSTRUCTIONS -------------------
def _remember_path(chat_id: int, thread_id: Optional[int]) -> Path:
    """Generate filename for saved /remember instructions."""
    if thread_id is None:
        return REMEMBER_DIR / f"{chat_id}.json"
    return REMEMBER_DIR / f"{chat_id}_{thread_id}.json"


def load_all_remembered():
    """Load all saved /remember instructions into memory at startup."""
    loaded = 0
    for path in REMEMBER_DIR.glob("*.json"):
        stem = path.stem
        parts = stem.split("_")
        if len(parts) == 1:
            try:
                chat_id = int(parts[0])
                thread_id = None
            except ValueError:
                continue
        elif len(parts) == 2:
            try:
                chat_id = int(parts[0])
                thread_id = int(parts[1])
            except ValueError:
                continue
        else:
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = []
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "id" in item and "text" in item:
                        items.append({"id": int(item["id"]), "text": str(item["text"])})
            if items:
                state.remembered[(chat_id, thread_id)] = items
                loaded += 1
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"⚠️ Could not load remembered instructions for {chat_id}/{thread_id}: {e}")

    logger.info(f"🧠 Loaded remembered instructions for {loaded} chat(s)/thread(s) from {REMEMBER_DIR}")


def save_remembered(chat_id: int, thread_id: Optional[int]):
    """Atomically write a chat/thread's remembered instructions to disk."""
    path = _remember_path(chat_id, thread_id)
    tmp_path = path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state.remembered.get((chat_id, thread_id), []), f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
    except OSError as e:
        logger.error(f"⚠️ Failed to save remembered instructions for chat {chat_id} thread {thread_id}: {e}")


def add_remembered(chat_id: int, thread_id: Optional[int], text: str) -> int:
    """Store a new instruction and return its unique (per chat/thread) id."""
    key = (chat_id, thread_id)
    items = state.remembered.setdefault(key, [])
    next_id = max((item["id"] for item in items), default=0) + 1
    items.append({"id": next_id, "text": text})
    save_remembered(chat_id, thread_id)
    return next_id


def remove_remembered(chat_id: int, thread_id: Optional[int], instruction_id: int) -> bool:
    """Remove an instruction by id. Returns True if it existed and was removed."""
    key = (chat_id, thread_id)
    items = state.remembered.get(key, [])
    for item in items:
        if item["id"] == instruction_id:
            items.remove(item)
            if items:
                save_remembered(chat_id, thread_id)
            else:
                del state.remembered[key]
                try:
                    _remember_path(chat_id, thread_id).unlink(missing_ok=True)
                except OSError as e:
                    logger.error(f"⚠️ Failed to delete remembered file for chat {chat_id} thread {thread_id}: {e}")
            return True
    return False


def remembered_text(chat_id: int, thread_id: Optional[int]) -> Optional[str]:
    """Render the chat/thread's remembered instructions as a system-style block."""
    items = state.remembered.get((chat_id, thread_id))
    if not items:
        return None
    lines = ["[Remembered instructions]"]
    for item in items:
        lines.append(f"- {item['text']}")
    return "\n".join(lines)
