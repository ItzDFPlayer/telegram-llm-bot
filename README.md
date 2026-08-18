# Telegram LLM Bot

A Telegram bot that proxies chat messages to a local **OpenAI-compatible** backend
(Ollama, LM Studio, llama.cpp, etc.), with whitelisting, per-chat memory,
persistent instructions, and an online/offline status heartbeat.

## Features

- **DM + group support** with forum-topic awareness (each topic keeps its own history).
- **Whitelisting** for users, whole groups, and individual forum threads.
- **Per-chat/thread conversation memory**, persisted to disk and auto-trimmed to a token budget.
- **Persistent instructions** via `/remember` — injected into every prompt but never stored in history.
- **Web search** — the model can search the web (DuckDuckGo or Brave) when it needs current or factual information.
- **Dynamic bot description** — shows 🟢 Online / 🔴 Offline and the running model name.
- **Graceful shutdown** — marks the bot offline before the connection closes (ESC in a terminal, or Ctrl+C).
- Optional **MarkdownV2** formatting of replies.

## Requirements

- Python 3.10+
- A running OpenAI-compatible API endpoint (e.g. Ollama at `http://localhost:11434/v1`)

## Installation

```bash
# create and activate a virtual environment (recommended)
python -m venv bot_env
# Windows
bot_env\Scripts\activate
# Linux/macOS
source bot_env/bin/activate

# install dependencies
pip install "python-telegram-bot[job-queue]" openai python-dotenv

# optional, but recommended
pip install tiktoken telegramify-markdown

# search providers that scrape HTML (ddgs also recommended for DuckDuckGo)
pip install ddgs beautifulsoup4
```

- `tiktoken` gives accurate token counting for context trimming.
- `telegramify-markdown` enables MarkdownV2-formatted replies (plain-text fallback otherwise).
- `ddgs` / `beautifulsoup4` enable web search (DuckDuckGo, Bing, SearXNG — all free).

## Configuration

Copy the example below into a `.env` file in the project root:

```dotenv
# Required — get this from @BotFather
BOT_TOKEN=YOUR_BOTFATHER_TOKEN

# OpenAI-compatible backend
OPENAI_BASE=http://localhost:11434/v1
OPENAI_API_KEY=not-needed
OPENAI_TIMEOUT=300

# Web search for the model (duckduckgo | brave | bing | searxng | disabled)
SEARCH_PROVIDER=duckduckgo
# Only needed for SEARCH_PROVIDER=brave — free key from https://brave.com/search/api/
# BRAVE_API_KEY=BSA_xxxxxxxx
# Optional: your own SearXNG instance (falls back to public ones if empty)
# SEARXNG_URL=https://searx.mydomain
SEARCH_MAX_RESULTS=3

# Who may use the bot / run admin commands (comma-separated numeric IDs)
WHITELIST_USERS=123456789
ADMIN_IDS=123456789

# Groups allowed to talk to the bot.
# Each entry is either "-123" (whole group) or "-123:42" (only thread 42).
WHITELIST_GROUPS=-1001234567890,-1001234567890:42

# Optional
SYSTEM_PROMPT=You are a helpful assistant...
BOT_DESCRIPTION_INTRO=AI model running on Xiaomi 15
HEALTH_CHECK_INTERVAL_SECONDS=30
LOG_FILE=bot.log
HISTORY_DIR=./chat_history
REMEMBER_DIR=./remembered

# Memory / context tuning — match MODEL_MAX_CONTEXT to your model's real context
# (important on small-context NPU backends)
# MODEL_MAX_CONTEXT=8000
# RESPONSE_RESERVE=1000
# GROUP_TOKEN_BUDGET=4000
```

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | — | Bot token from @BotFather (**required**) |
| `OPENAI_BASE` | `http://localhost:11434/v1` | OpenAI-compatible base URL |
| `OPENAI_API_KEY` | `not-needed` | API key (often ignored by local backends) |
| `OPENAI_TIMEOUT` | `300` | Completion request timeout in seconds |
| `SEARCH_PROVIDER` | `duckduckgo` | Web search provider: `duckduckgo`, `brave`, `bing`, `searxng`, or `disabled` |
| `BRAVE_API_KEY` | — | Brave Search API key (only for the `brave` provider) |
| `SEARXNG_URL` | — | Your own SearXNG instance (only for `searxng`; public instances are tried if empty) |
| `SEARCH_MAX_RESULTS` | `3` | Max search results fed back to the model per query |
| `SEARCH_TIMEOUT` | `10` | Search request timeout in seconds |
| `SEARCH_RESULT_LIMIT_CHARS` | `1200` | Max characters of results injected into the prompt per query |
| `MODEL_MAX_CONTEXT` | `8000` | Model's total context window (tokens) |
| `RESPONSE_RESERVE` | `1000` | Tokens reserved for the model's reply |
| `GROUP_TOKEN_BUDGET` | `4000` | Group history auto-prunes once it exceeds this |
| `WHITELIST_USERS` | `0` | User IDs allowed to DM the bot |
| `ADMIN_IDS` | `[]` | User IDs allowed to run admin commands |
| `WHITELIST_GROUPS` | — | Groups/threads allowed to use the bot |
| `SYSTEM_PROMPT` | see code | System prompt sent to the model |
| `BOT_DESCRIPTION_INTRO` | `AI model running on Xiaomi 15` | First line of the bot description |
| `HEALTH_CHECK_INTERVAL_SECONDS` | `30` | How often to probe the backend |
| `LOG_FILE` | `bot.log` | Log file path |
| `HISTORY_DIR` | `./chat_history` | Where conversation history is stored |
| `REMEMBER_DIR` | `./remembered` | Where `/remember` instructions are stored |

## Running

```bash
python bot.py
```

The bot checks the model status and sets its description immediately at startup,
then re-checks periodically. Press **ESC** (interactive terminal only) or **Ctrl+C**
to shut down gracefully — the description is set to 🔴 Offline on exit.

## Commands

### Everyone

| Command | Description |
|---|---|
| `/start` | Show the current chat/thread ID (for whitelisting) |

### Whitelisted users (DM) / admins (groups)

| Command | Description |
|---|---|
| `/clear` | Clear this chat/thread's conversation history |
| `/system` | Show the current system prompt |
| `/remember <text>` | Save an instruction for this chat/thread (returns an ID) |
| `/forget <id>` | Remove a remembered instruction |
| `/remember_list` | List remembered instructions |

### Admins only

| Command | Description |
|---|---|
| `/allow_user <id>` | Whitelist a user (or reply to their message with `/allow_user`) |
| `/remove_user <id>` | Remove a user from the whitelist |
| `/allow_group <id>` | Whitelist a whole group (or run it inside the group) |
| `/remove_group <id>` | Remove a group from the whitelist |
| `/allow_thread` | Whitelist only the current forum topic (run inside the thread) |
| `/remove_thread` | Un-whitelist the current forum topic |
| `/whitelist_status` | Show the current whitelist |

## Web search

The model gets a `web_search` tool it can invoke whenever an answer depends on
current events or facts it isn't sure about. The bot runs a small loop: the
model calls the tool → the bot searches → results are injected → the model
writes the final reply. Only the final reply is saved to chat history.

On backends that can't do function calling (e.g. NPU runtimes that don't
support constrained decoding), the bot automatically falls back to **marker
mode**: the model writes a single line like `[SEARCH: query]` — or Ollama's
native `<|tool_call|>call:web_search{...}` syntax, which is detected too — the
bot runs the search, and feeds the results back — no tool support needed.

Four free providers are supported:

- **DuckDuckGo** (`SEARCH_PROVIDER=duckduckgo`, default) — no API key. Uses
  the `ddgs` package, and falls back to scraping DuckDuckGo's HTML endpoint if
  `ddgs` isn't installed.
- **Bing** (`SEARCH_PROVIDER=bing`) — no API key. Scrapes Bing's search results
  directly (needs `beautifulsoup4`).
- **SearXNG** (`SEARCH_PROVIDER=searxng`) — no API key. Queries a SearXNG
  meta-search instance. Set `SEARXNG_URL` to your own for dependable results;
  without it, a few public instances are probed with a short (bounded) timeout,
  but public instances are unreliable and often disable the JSON API.
  Self-hosting SearXNG is the most robust fully-free option.
- **Brave** (`SEARCH_PROVIDER=brave`) — free tier, needs a key from
  <https://brave.com/search/api/>.

> Scraping providers rely on each site's public HTML, which can change at any
> time. They degrade gracefully — if a scrape fails the bot simply reports no
> results rather than crashing.

Set `SEARCH_PROVIDER=disabled` to turn web search off. On backends that don't
support tool calling, the bot automatically switches to marker mode, so web
search keeps working without function calling.

## Group behavior

In groups the bot only responds when it is **@mentioned** or **replied to**.
When replying to another user's message while mentioning the bot, the bot quotes
that user's text for context.

> **Privacy mode:** if the bot never logs group messages that don't mention it,
> group privacy mode is likely ON. Disable it via @BotFather →
> `/mybots` → select bot → **Bot Settings → Group Privacy → Turn off**,
> then remove and re-add the bot to the group.

## Project structure

```
tgbot/
├── bot.py                    # entry point: builds app, initializes state, registers plugins
├── config.py                 # .env loading, logging setup, whitelist parsing + persistence
├── core/
│   ├── state.py              # shared runtime state (clients, model name, bot instance, memory)
│   ├── memory.py             # token counting, history + instruction persistence
│   ├── llm.py                # completions, health probes, dynamic description, web_search tool loop
│   ├── websearch.py          # web search backend (DuckDuckGo / Brave)
│   └── messages.py           # thread-id resolution + mention helpers
└── plugins/
    ├── __init__.py           # plugin registry (register_all)
    ├── basic_commands.py     # /start, /clear, /system
    ├── whitelist_admin.py    # whitelist management commands
    ├── chat_handler.py       # DM/group message routing + reply pipeline
    ├── remember.py           # /remember, /forget, /remember_list
    └── status.py             # health-check job + shutdown hook
```

### Adding a feature

Each plugin module exposes a single `register(app)` function. To add a feature:

1. Create `plugins/my_feature.py` with a `register(app)` that adds its handlers/jobs.
2. Import it in `plugins/__init__.py` and append it to `PLUGINS`.

That's it — `register_all(app)` wires it up automatically at startup.
