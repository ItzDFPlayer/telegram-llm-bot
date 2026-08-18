# Telegram LLM Bot

A Telegram bot that proxies chat messages to a local **OpenAI-compatible** backend
(Ollama, LM Studio, llama.cpp, etc.), with whitelisting, per-chat memory,
persistent instructions, and an online/offline status heartbeat.

## Features

- **DM + group support** with forum-topic awareness (each topic keeps its own history).
- **Whitelisting** for users, whole groups, and individual forum threads.
- **Per-chat/thread conversation memory**, persisted to disk and auto-trimmed to a token budget.
- **Persistent instructions** via `/remember` — injected into every prompt but never stored in history.
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
```

- `tiktoken` gives accurate token counting for context trimming.
- `telegramify-markdown` enables MarkdownV2-formatted replies (plain-text fallback otherwise).

## Configuration

Copy the example below into a `.env` file in the project root:

```dotenv
# Required — get this from @BotFather
BOT_TOKEN=YOUR_BOTFATHER_TOKEN

# OpenAI-compatible backend
OPENAI_BASE=http://localhost:11434/v1
OPENAI_API_KEY=not-needed
OPENAI_TIMEOUT=300

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
```

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | — | Bot token from @BotFather (**required**) |
| `OPENAI_BASE` | `http://localhost:11434/v1` | OpenAI-compatible base URL |
| `OPENAI_API_KEY` | `not-needed` | API key (often ignored by local backends) |
| `OPENAI_TIMEOUT` | `300` | Completion request timeout in seconds |
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
│   ├── llm.py                # completions, health probes, dynamic description
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
