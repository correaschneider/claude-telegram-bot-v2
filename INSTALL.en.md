<p align="right">🇺🇸 English · <a href="INSTALL.md">🇧🇷 Português</a></p>

# Installation

Complete guide: dependencies, the Telegram bot, configuration, running as a service,
WhisperX (voice), updating and troubleshooting. The [README](README.en.md) explains what the
bot does.

## 1. Dependencies

### Required

| What | Version | Why |
|---|---|---|
| **Linux** with `systemd --user` | — | both services (bot and WhisperX) are user units |
| **Python** | ≥ 3.12 | `uv` downloads it if missing |
| [**uv**](https://docs.astral.sh/uv/) | ≥ 0.11 | creates the venv and installs from `uv.lock` (exact versions) |
| **Claude Code CLI** (`claude`) | ≥ 2.1.259 | runs the turns (`claude -p --output-format stream-json`); `--permission-prompts` and `--fork-session` need this version |
| **git** | — | clone/update |

`claude` must be **logged in as the same user that runs the service** (`claude --version`
and a `claude -p "hi"` in the terminal must work). MCP servers configured for that user
(ClickUp, Atlassian, Notion…) are automatically available to the bot; just allow them in
`allowed_tools` (e.g. `mcp__clickup`).

### Optional (voice messages only)

| What | Why |
|---|---|
| **NVIDIA GPU** + driver with CUDA 12.x | WhisperX `large-v3` in float16 uses ~3.9 GB of VRAM; CPU works, but slowly |
| **WhisperX** in its own venv (`~/whisperx/.venv`) | transcription (section 6) |
| **ffmpeg** | WhisperX uses it to decode Telegram's `.ogg` |
| **`flock`** (util-linux) | serializes transcriptions so they don't blow the VRAM; without it, runs unlocked |

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv
curl -fsSL https://claude.ai/install.sh | bash       # Claude Code (then: claude → login)
sudo apt install -y ffmpeg util-linux git
```

## 2. Clone and install

```bash
git clone git@github.com:correaschneider/claude-telegram-bot-v2.git
cd claude-telegram-bot-v2
uv sync                       # creates .venv, installs aiogram, APScheduler, python-dotenv (+ ruff)
uv run python tests/test_core.py   # 18 tests, no token, no network — confirms the environment is sane
```

## 3. The Telegram bot (@BotFather)

1. `/newbot` → name and username → keep the **token**.
2. Open the **BotFather Mini App** (button next to the text box — the options below are
   **not** in the `/mybots` text menu) → your bot → **Settings**:
   - **Threaded Mode** → ON — topics in private chats (`/new`, `/fork`, deep links).
     Leave *"Disallow users to create new threads"* OFF.
   - **Guest Chat Mode** → ON — only if you want to use the bot in groups without adding it as a member.
   - *Allow Groups* / *Group Privacy* can stay as they are (the bot only reacts to mentions/replies).
3. Propagation takes **~5 min**. Check:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getMe" | grep -o '"has_topics_enabled":[a-z]*\|"supports_guest_queries":[a-z]*'
   ```
   While it's `false`, `/new` answers *"the chat is not a forum"*.

> Enabling topics makes Telegram withhold 15% of **Stars** purchases inside the bot.
> Irrelevant for a personal bot, but that's why it ships disabled.

The menu commands (`/status`, `/new`…) are registered by the bot itself on startup
(`setMyCommands`).

### 3.1 Find your `chat_id`

Send any message to the bot and:

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | grep -o '"chat":{"id":[-0-9]*'
```

Private chat = positive id (= your user id). Group = negative id (`-100…`). Put both in
`ALLOWED_CHAT_IDS`; the bot only serves what's listed there.

## 4. Configuration

### 4.1 `.env`

```bash
cp .env.example .env && chmod 600 .env
```

| Variable | Required | Default | Notes |
|---|---|---|---|
| `TELEGRAM_TOKEN` | ✅ | — | from BotFather |
| `ALLOWED_CHAT_IDS` | ✅ | — | csv of served chats (private and groups) |
| `ALLOWED_USER_IDS` | | positive ids from `ALLOWED_CHAT_IDS` | who may trigger turns in groups/guest mode and **approve permissions** |
| `WORKSPACE` | ✅ | — | cwd when there is no `projects.json` |
| `PROJECTS_FILE` | | `./projects.json` | project registry (4.2) |
| `CLAUDE_BIN` | | `claude` | CLI path/wrapper |
| `CLAUDE_PERMISSION_MODE` | | `acceptEdits` | used when the project doesn't define one |
| `CLAUDE_ALLOWED_TOOLS` | | empty | same; `--allowedTools` syntax (`Read Edit Bash(git:*) mcp__clickup`) |
| `CLAUDE_ADD_DIRS` | | empty | extra `--add-dir`, csv |
| `CLAUDE_APPEND_SYSTEM_PROMPT` | | built-in prompt | replaces the text that teaches Claude the Telegram tools — change only if you know what you're doing |
| `STORE_FILE` · `JOBS_FILE` | | `./.store.json` · `./.jobs.json` | conversations/sessions and schedules |
| `DEFAULT_TZ` | | `America/Sao_Paulo` | timezone for schedules |
| `SESSION_TTL_SECONDS` | | `21600` (6 h) | an idle session resets after this |
| `YOLO_TTL_SECONDS` | | `3600` | default `/yolo` duration |
| `DRAFT_INTERVAL_SECONDS` · `DRAFT_KEEPALIVE_SECONDS` · `DRAFT_MAX_CHARS` | | `1.5` · `10` · `3500` | live-draft pacing |
| `RICH_MESSAGES` | | `true` | `false` = HTML only |
| `FOOTER_COST` | | `false` | shows USD besides tokens in the footer |
| `GROUP_PUBLIC_TAG` | | `#todos` | in groups, makes the answer public (otherwise ephemeral) |
| `BRIDGE_HOST` · `BRIDGE_PORT` | | `127.0.0.1` · `0` (random) | MCP bridge loopback HTTP |
| `MCP_TOOL_TIMEOUT_MS` | | `86400000` (24 h) | how long Claude waits for your approval/answer |
| `WHISPERX_SERVER_URL` | | `http://127.0.0.1:8765` | resident server (section 6); empty = CLI only |
| `WHISPERX_BIN` · `WHISPERX_MODEL` · `WHISPERX_DEVICE` · `WHISPERX_COMPUTE_TYPE` · `WHISPERX_LANGUAGE` · `WHISPERX_BATCH` · `WHISPERX_LOCK` | | `~/.local/bin/whisperx-cli` · `large-v3` · `cuda` · `float16` · `pt` · `8` · `/tmp/whisperx-pipeline.lock` | fallback CLI |
| `AUDIO_TMP_DIR` · `IMAGE_TMP_DIR` | | `/tmp/telegram-audio` · `/tmp/telegram-images` | downloads (audio is deleted after transcription; images stay for follow-ups) |

### 4.2 `projects.json`

```bash
cp projects.example.json projects.json
```

```json
{
  "smsfunnel": {
    "path": "/data/projects/Otimiza/SMSFunnel",
    "default": true,
    "allowed_tools": "Read Edit Write Glob Grep Bash(git:*) Bash(docker:*) mcp__clickup",
    "permission_mode": "acceptEdits",
    "add_dirs": ["/data/projects/Obsidian"],
    "tasks": ["CU"],
    "chats": [-1001234567890]
  }
}
```

| Field | Effect |
|---|---|
| `path` | Claude's `cwd` (loads the `CLAUDE.md` from there) |
| `default` | project for conversations without an explicit choice |
| `allowed_tools` | auto-approved **without asking** (except sensitive commands — see README, "Permission policy") |
| `permission_mode` · `add_dirs` | override the `.env` defaults |
| `tasks` | task prefixes: `/link HT-123` or the deep link `t:HT-123` land in this project |
| `chats` | groups that talk to this project by default |

Switch projects per conversation/topic with `/project`; create topics already in a project
with `/new <alias>`.

### 4.3 Claude Code — global permissions

The bot passes `--settings '{"permissions":{"ask":["Bash"]}}'` on every turn: *ask* rules
are evaluated before *allow* rules, so **every `Bash` call reaches the bot's desk** even if
your `~/.claude/settings.json` has `allow: ["Bash"]`. You don't need to touch your global
rules; what the bot auto-approves is the built-in read-only set + the project's
`allowed_tools` + whatever you marked as "Always in this session".

## 5. Running

### 5.1 Manually (first test)

```bash
uv run bot.py
```

It should log `bot @your_bot up; projects=[...]; chats=[...]; topics=True guest=True`. Send
`/start` on Telegram. `Ctrl+C` stops it. Only **one** process per token — two pollers raise
`TelegramConflictError`.

### 5.2 As a service (`systemd --user`)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/claude-telegram-bot-v2.service ~/.config/systemd/user/
# if the repo is NOT at /data/projects/claude-telegram-bot-v2, adjust WorkingDirectory and ExecStart in the unit
systemctl --user daemon-reload
systemctl --user enable --now claude-telegram-bot-v2
loginctl enable-linger $USER          # keeps running without a graphical session / after reboot
```

Operations:

```bash
systemctl --user status claude-telegram-bot-v2
journalctl --user -u claude-telegram-bot-v2 -f
systemctl --user restart claude-telegram-bot-v2
```

> Never `pkill -f "python bot.py"` from inside a Claude Code session: the tool's own
> `bash -c` contains the string and gets killed too. Use `systemctl --user restart` or kill by PID.

## 6. Voice — WhisperX (optional)

Two modes; the bot uses the server and falls back to the CLI if the server is unreachable.

### 6.1 Install WhisperX (its own venv, separate from the bot's)

```bash
uv venv ~/whisperx/.venv --python 3.12
uv pip install --python ~/whisperx/.venv/bin/python whisperx
# GPU: check that the installed torch sees CUDA
~/whisperx/.venv/bin/python -c "import torch; print(torch.cuda.is_available())"
```

On the **first** run WhisperX downloads the model (`Systran/faster-whisper-large-v3`, ~3 GB)
from Hugging Face — it needs network once; afterwards it can run with `HF_HUB_OFFLINE=1`.

### 6.2 Resident server (recommended — ~0.5 s per audio)

Loads `large-v3` once and transcribes over loopback HTTP. Without it, every audio pays
~10 s spinning up Python+CUDA+model.

```bash
cp deploy/whisperx-server.service ~/.config/systemd/user/
# adjust ExecStart if the venv isn't ~/whisperx/.venv or the repo isn't at /data/projects/...
systemctl --user daemon-reload
systemctl --user enable --now whisperx-server
curl -s http://127.0.0.1:8765/health      # {"loaded": true, ...} after ~6 s of preload
journalctl --user -u whisperx-server -f
```

Unit tweaks (`Environment=`): `WHISPERX_MODEL`, `WHISPERX_COMPUTE_TYPE` (`float16` on GPU,
`int8` on CPU), `WHISPERX_LANGUAGE`, `WHISPERX_IDLE_SECONDS` (default 900: unloads the model
after 15 min without audio and frees the VRAM; the next audio pays ~6 s to reload),
`WHISPERX_PORT`.

### 6.3 Fallback CLI

`WHISPERX_BIN` points to an executable compatible with the WhisperX CLI. With WhisperX from
PyPI, `WHISPERX_BIN=~/whisperx/.venv/bin/whisperx` is enough. The bot passes `--model`,
`--device`, `--compute_type`, `--language`, `--batch_size`, `--no_align`,
`--output_format json`, `--output_dir`, under `flock` when available.

> With the server **up**, don't run the CLI at the same time: a second `large-v3` raises
> `CUDA out of memory`. That's why the bot only falls back to the CLI when the server is unreachable.

### 6.4 Without a GPU

`WHISPERX_DEVICE=cpu` and `WHISPERX_COMPUTE_TYPE=int8` (in `.env` and in the server unit). A
smaller model (`medium`, `small`) if it gets too slow.

## 7. Verify everything works

| Test | Expected |
|---|---|
| `/start` | reply showing the current project |
| any text | "Thinking…" draft growing, then the answer with a `⏱ · ↓tokens ↑tokens` footer |
| `/new smsfunnel` | topic created (once `has_topics_enabled` is `true`) |
| *"create /tmp/x.txt with sh -c"* | 🔐 card with buttons; **Approve** runs it |
| *"delete /tmp/x.txt with rm"* | ⚠️ sensitive card; needs **2 taps** |
| voice note | `📝 Transcribed:` echo in ~1 s (server) and the turn starts |
| *"ask me whether I prefer A or B"* | question with buttons |
| *"every 5 min tell me the time"* | `/jobs` lists it; `/unschedule 1` removes it |

## 8. Updating

```bash
git -C /data/projects/claude-telegram-bot-v2 pull
uv sync
uv run python tests/test_core.py
systemctl --user restart claude-telegram-bot-v2
# if deploy/whisperx_server.py changed:
systemctl --user restart whisperx-server
```

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `TelegramConflictError: terminated by other getUpdates request` | two processes with the same token — stop the other one (old `nohup`, v1 with the same token…) |
| `/new` → *"the chat is not a forum"* | Threaded Mode just enabled; wait ~5 min and check `getMe` |
| 🔐 card never shows up | `/yolo` is on in that topic (`/status` shows it), or the command is in `allowed_tools`/read-only |
| everything is denied in a group | expected: groups have no approver; put what's needed in the project's `allowed_tools` |
| `❌ Claude exited without a result (rc=1)` | run `claude -p "hi"` in a terminal as the same user — usually login, or `claude` missing from the unit's `PATH` |
| audio takes ~10 s | `whisperx-server` is down or `WHISPERX_SERVER_URL` is empty — `systemctl --user status whisperx-server` |
| `CUDA failed with error out of memory` | server + CLI at the same time, or another pipeline on the GPU; lower `WHISPERX_IDLE_SECONDS` or use `int8` |
| audio: `whisperx exit 1 … HF_HUB_OFFLINE` | the first run needs network to download the model |
| draft disappears after ~30 s idle | Telegram behavior; the bot resends every `DRAFT_KEEPALIVE_SECONDS` — if Claude has been silent longer than that, check the journal |
| `permission_denials` in the footer | Claude tried something outside the allowlist with no approver (group/job) — adjust `allowed_tools` |
| changed `.env` and nothing happened | `.env` is read only at start: `systemctl --user restart claude-telegram-bot-v2` |
