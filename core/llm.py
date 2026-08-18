"""
Model backend integration: completions, health probes, the dynamic bot
description, and the web_search tool-calling loop.

When web search is enabled the model gets a `web_search` tool it can invoke;
we execute the search and feed the results back until it produces a final
text reply. Falls back to a plain single-shot completion if the backend or
model doesn't support tool calling.
"""
import asyncio
import json
import logging
import re
from typing import Optional

from telegram.ext import ContextTypes

from config import BOT_DESCRIPTION_INTRO, SYSTEM_PROMPT
from core import state, websearch

logger = logging.getLogger("bot.llm")

# Safety cap on how many tool-call rounds we'll do per user message, so a
# misbehaving model can't loop forever.
MAX_TOOL_ROUNDS = 3

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for up-to-date or factual information. Use this "
            "whenever the answer depends on current events, live data, or facts "
            "you are not confident about. Returns a short list of results with "
            "titles, URLs and snippets."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A concise search query, ideally 3–8 keywords.",
                }
            },
            "required": ["query"],
        },
    },
}

# Marker-based search fallback. Some backends (e.g. NPU runtimes) can't do the
# constrained decoding that function calling requires, so instead the model
# emits a single `[SEARCH: query]` line and we run the search for it.
SEARCH_MARKER_RE = re.compile(r"\[SEARCH:\s*(.+?)\]", re.IGNORECASE)

# Ollama models that can't do constrained decoding (e.g. on NPU) emit tool
# requests as plain text in this native format instead of structured calls:
#   <|tool_call|>call:web_search{queries:[<|"|>query here<|"|>]}<|tool_call|>
# The pattern is tolerant of mangled markers (e.g. <|tool_call> / <tool_call|>).
OLLAMA_TOOL_CALL_RE = re.compile(r"<\|?tool_call\|?>(.*?)<\|?tool_call\|?>", re.DOTALL)

MARKER_NOTE = (
    "\n\nNote: this runtime cannot call tools directly. If you need current "
    "or factual information, output exactly one line like [SEARCH: your query] "
    "and wait for the search results before answering. (Native tool-call "
    "syntax such as <|tool_call|> is also accepted.)"
)

# Session-level: once the backend rejects tools we stop offering them and use
# marker mode for the rest of the run, so the error isn't hit on every message.
_TOOLS_UNSUPPORTED = False


class _ToolsUnsupportedError(Exception):
    """Raised internally when the backend rejects the tools parameter."""


def search_enabled() -> bool:
    """True if the web_search tool should be offered to the model."""
    return websearch.search_enabled()


def build_system_prompt() -> str:
    """
    The system prompt sent to the model, extended with a short hint about the
    web_search tool when web search is enabled.
    """
    if not search_enabled():
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT + (
        "\n\nYou have access to a web_search tool. Use it for current events, "
        "recent information, or facts you are not confident about. Search only "
        "when it would genuinely improve your answer — don't search for casual "
        "small talk. Never invent URLs; cite only the ones the tool returns."
    )


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


def _run_web_search(query: str, mode: str = "") -> str:
    """Run a web search and return the result text for the model."""
    label = f"Web search ({mode})" if mode else "Web search"
    logger.info(f"🔎 {label}: {query!r}")
    result = websearch.search_web(query)
    return result or "No useful results found. Tell the user you couldn't find anything relevant."


def _run_tool_call(tool_call) -> str:
    """Execute a single OpenAI-format function tool call and return the text result."""
    name = tool_call.function.name
    raw_args = tool_call.function.arguments or "{}"
    if name == "web_search":
        try:
            query = json.loads(raw_args).get("query", "")
        except (json.JSONDecodeError, TypeError):
            query = raw_args
        return _run_web_search(query)
    logger.warning(f"⚠️ Model requested unknown tool {name!r}")
    return f"Unknown tool: {name}. Tell the user this tool is unavailable."


def _plain_completion(messages: list[dict]) -> Optional[str]:
    """Single-shot completion without tools; returns text or None."""
    try:
        response = state.client.chat.completions.create(
            model=state.MODEL_NAME, messages=messages
        )
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return None
    content = response.choices[0].message.content
    return content if content is not None else None


def _with_marker_note(messages: list[dict]) -> list[dict]:
    """Return a copy of messages with the marker-mode instruction appended to the system message."""
    out = []
    added = False
    for m in messages:
        m = dict(m)
        if m.get("role") == "system" and not added:
            m["content"] = (m.get("content") or "") + MARKER_NOTE
            added = True
        out.append(m)
    return out


def _extract_ollama_tool_calls(text: str) -> list[tuple[str, str]]:
    """
    Extract Ollama's native tool calls from text, e.g.:
        <|tool_call|>call:web_search{queries:[<|"|>Xiaomi 15<|"|>]}<|tool_call|>
    Returns a list of (function_name, raw_args_string).
    """
    calls = []
    for m in OLLAMA_TOOL_CALL_RE.finditer(text):
        body = m.group(1).strip()
        cm = re.match(r"call:(\w+)\{(.*)\}\s*$", body, re.DOTALL)
        if not cm:
            continue
        calls.append((cm.group(1), cm.group(2)))
    return calls


def _query_from_ollama_args(raw_args: str) -> str:
    """
    Extract a search query from Ollama-native args, handling both strict JSON
    ({"query": "x"} / {"queries": ["x"]}) and the unquoted-key form
    (queries:[<|"|>x<|"|>]).
    """
    if not raw_args:
        return ""
    normalized = raw_args.replace('<|"|>', '"')
    # Try strict JSON first (keys quoted, object form).
    try:
        data = json.loads(normalized)
        if isinstance(data, dict):
            for key in ("query", "queries"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
                if isinstance(val, list) and val and str(val[0]).strip():
                    return str(val[0]).strip()
    except (json.JSONDecodeError, TypeError):
        pass
    # Lenient: key: value where value is a quoted string or a string array.
    for key in ("queries", "query"):
        m = re.search(rf'{key}\s*:\s*("(?:[^"\\]|\\.)*"|\[.*?\])', normalized, re.DOTALL)
        if not m:
            continue
        raw_val = m.group(1)
        if raw_val.startswith("["):
            items = re.findall(r'"((?:[^"\\]|\\.)*)"', raw_val)
            if items and items[0].strip():
                return items[0].strip()
        else:
            try:
                val = json.loads(raw_val)
            except (json.JSONDecodeError, TypeError):
                val = raw_val.strip().strip('"')
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _search_intent_from_text(text: str) -> Optional[str]:
    """
    Extract a search query from the model's text via a [SEARCH: …] marker or
    an Ollama-native <|tool_call|>call:<name>{…} block. The model may use any
    search tool name (web_search, _search, …), so we rely on the args carrying
    a `query`/`queries` field rather than the function name.
    """
    m = SEARCH_MARKER_RE.search(text)
    if m:
        query = m.group(1).strip()
        return query or None
    for _name, raw_args in _extract_ollama_tool_calls(text):
        query = _query_from_ollama_args(raw_args)
        if query:
            return query
    return None


def _strip_search_intent(text: str) -> str:
    """Remove [SEARCH: …] markers and Ollama-native tool-call blocks from text."""
    text = SEARCH_MARKER_RE.sub("[search requested]", text)
    text = OLLAMA_TOOL_CALL_RE.sub("[search requested]", text)
    return text


def _scrub_tool_syntax(text: str) -> str:
    """Remove any leftover native tool-call blocks — safety net so raw
    <|tool_call|> syntax can never leak into a user-facing reply."""
    return OLLAMA_TOOL_CALL_RE.sub("", text).strip()


def _search_results_message(query: str, results: str) -> str:
    """Build the user turn that feeds search results back to the model."""
    return (
        f"Search results for '{query}':\n{results}\n\n"
        "Answer the user's question using these results (or say "
        "nothing relevant was found)."
    )


def _tool_loop(messages: list[dict]) -> Optional[str]:
    """
    Tool-based search loop: the model may call web_search as a structured OpenAI
    tool call, or — on backends without constrained decoding — as Ollama's
    native `<|tool_call|>call:web_search{…}` text; we execute the search and feed
    results back until it produces a final text reply.

    Raises _ToolsUnsupportedError if the backend rejects the tools parameter.
    """
    global _TOOLS_UNSUPPORTED
    working = list(messages)
    tools_enabled = True
    for _ in range(MAX_TOOL_ROUNDS):
        kwargs = {"model": state.MODEL_NAME, "messages": working}
        if tools_enabled:
            kwargs["tools"] = [SEARCH_TOOL]
        try:
            # Retries are disabled at the client level (see state.py): a 5xx on
            # a tools request almost always means "tools not supported" (e.g.
            # NPU constrained decoding), so retrying would just waste time.
            response = state.client.chat.completions.create(**kwargs)
        except Exception as e:
            if tools_enabled:
                raise _ToolsUnsupportedError(f"tools rejected: {e}") from e
            logger.error(f"LLM error: {e}")
            return None

        message = response.choices[0].message
        content = message.content or ""
        tool_calls = getattr(message, "tool_calls", None)

        if tool_calls:
            # Structured OpenAI tool calls — execute each one.
            working.append({
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            })
            for tc in tool_calls:
                working.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": _run_tool_call(tc),
                })
            continue

        # No structured calls. Some backends (e.g. NPU) return the tool request
        # as Ollama-native text instead — handle it like marker-mode search.
        query = _search_intent_from_text(content)
        if query is None:
            return _scrub_tool_syntax(content) if content else None
        _TOOLS_UNSUPPORTED = True  # this backend never returns structured calls
        tools_enabled = False
        working.append({"role": "assistant", "content": _strip_search_intent(content)})
        results = _run_web_search(query, mode="tool text")
        working.append({"role": "user", "content": _search_results_message(query, results)})

    logger.warning("⚠️ Tool loop exceeded max rounds; giving up on this turn.")
    return None


def _marker_search_loop(messages: list[dict]) -> Optional[str]:
    """
    Search fallback for backends without function calling: the model signals a
    search via a `[SEARCH: query]` line or an Ollama-native `<|tool_call|>`
    block; we run the search and feed results back until it produces an answer.
    """
    working = list(messages)
    last_results = None  # (query, digest) from the most recent search
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = state.client.chat.completions.create(
                model=state.MODEL_NAME, messages=working
            )
        except Exception as e:
            logger.error(f"LLM error: {e}")
            # Best effort: if a search already produced results, surface them
            # rather than replying with nothing.
            if last_results:
                query, results = last_results
                return (
                    f"⚠️ I found some information about “{query}” but had "
                    f"trouble summarizing it:\n\n{results}"
                )
            return None

        text = response.choices[0].message.content
        if not text:
            return None
        query = _search_intent_from_text(text)
        if query is None:
            return _scrub_tool_syntax(text) if text else None
        results = _run_web_search(query, mode="marker mode")
        last_results = (query, results)
        working.append({"role": "assistant", "content": _strip_search_intent(text)})
        working.append({"role": "user", "content": _search_results_message(query, results)})

    logger.warning("⚠️ Marker search loop exceeded max rounds; giving up on this turn.")
    return None


def get_ai_response(messages: list[dict]) -> Optional[str]:
    """
    Call the model and return its reply, or None on failure.

    Web search runs in one of two modes:
      1. Tool mode — the model gets a web_search function it can call
         (backends that support function calling).
      2. Marker mode — if the backend rejects tools (e.g. NPU runtimes that
         can't do constrained decoding), the model is asked to emit a
         `[SEARCH: query]` line instead; we run the search and feed results back.

    The mode is chosen automatically on the first call and remembered for the
    session, so an unsupported backend never errors repeatedly.
    """
    # We assign to this flag below (marker mode switch), so declare it global
    # or Python would treat the earlier read as a local and raise UnboundLocalError.
    global _TOOLS_UNSUPPORTED

    if not search_enabled():
        return _plain_completion(messages)

    if _TOOLS_UNSUPPORTED:
        return _marker_search_loop(_with_marker_note(messages))

    try:
        return _tool_loop(messages)
    except _ToolsUnsupportedError as e:
        _TOOLS_UNSUPPORTED = True
        logger.warning(
            "⚠️ Backend rejects tool calling — switching to [SEARCH: …] "
            f"marker mode for this session ({e})."
        )
        return _marker_search_loop(_with_marker_note(messages))
