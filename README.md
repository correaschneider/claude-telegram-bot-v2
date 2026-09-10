# claude-telegram-bot-v2

Bot de Telegram que repassa mensagens (texto, voz, imagem) ao **Claude Code** headless e
mostra a resposta **enquanto ela é gerada**, usando os recursos que o Telegram lançou para
bots de IA (Bot API 9.5–10.3). Sucessor do `claude-telegram-bot` (python-telegram-bot),
reescrito em **aiogram 3.31** porque só ele expõe a Bot API 10.x.

## Recursos

| | Recurso | Como |
|---|---|---|
| F1 | **Streaming** | `sendMessageDraft`: placeholder "Thinking…" nativo, texto crescendo, `🔧 Bash · git status` enquanto a ferramenta roda, keepalive (o Telegram apaga rascunho parado em ~30s), botão **Stop** do cliente (`stopped_message_generation`) ou `/cancel` matam o processo e preservam o parcial. |
| F2 | **Tópicos = sessões** | Com *Threaded Mode* ligado no BotFather, cada tópico do chat privado é uma conversa com projeto (`cwd`) e sessão (`--resume`) próprios. `/new <alias>` cria tópico, `/fork` bifurca a sessão atual (`--fork-session`), `/project` troca o projeto, `/sessions` retoma uma sessão recente do Claude Code. Tópico criado pelo bot ganha título automático após o 1º turno. |
| F3 | **Rich Messages** | Resposta final em Rich Markdown (headings, tabelas, code, task list); trechos entre ferramentas vão num `<details>` colapsável; rodapé em itálico com tempo e tokens (`↓entrada ↑saída`; `FOOTER_COST=true` acrescenta USD). Fallback automático para HTML e texto puro. |
| F4 | **Aprovação por botão** | `--permission-prompt-tool` → MCP `tg.ask` (bridge stdio→HTTP local) → card com **Aprovar / Negar / Sempre nesta sessão / Copiar comando**. Comando sensível (`rm`, `push`, `DROP`, `deploy`, `ssh`…) exige **dois toques**. O Claude espera (`MCP_TOOL_TIMEOUT`, default 24h). `/yolo [min]` pula tudo por um prazo, por tópico. |
| F5 | **Checklist** | Tool `tg.update_checklist`: o Claude registra o plano e marca etapas; Rich Message com task list editada in-place. |
| F6 | **Deep link** | `/link <alias>` ou `/link HT-123` → `t.me/<bot>?start=…` abre um tópico já no projeto certo. |
| F7 | **Grupos / guest mode** | Menção ou reply em grupos autorizados (e `guest_message` sem ser membro): "digitando…" + resposta **efêmera** pro autor (`#todos` = pública). Sem aprovação em grupo: fora da allowlist é negado. |
| — | **Voz** | Mensagem de voz/áudio → **WhisperX residente** (`deploy/whisperx_server.py`, modelo `large-v3` float16 carregado uma vez, ~0,5 s por áudio; descarrega após 15 min ocioso) → eco `📝 Transcrito:` → turno. Sem o servidor, cai no CLI (~10 s, sobe o modelo a cada chamada). |
| — | **Imagem** | Foto/documento de imagem → baixada pro disco → o Claude abre com `Read`. Legenda vira o pedido. |
| — | **Reply** | Responder a uma mensagem (texto, transcrição, imagem, trecho selecionado) inclui o teor dela no prompt. |
| — | **Perguntas com botões** | Tool `tg.ask_user(question, options, multi)`: o Claude pergunta, você toca, ele continua. |
| — | **Entrega de arquivos** | Mídia (png/jpg/pdf/mp3/mp4…) citada por caminho absoluto na resposta é enviada sozinha; tool `tg.send_file` entrega qualquer arquivo. |
| — | **Agendamentos** | Tool `tg.schedule` (cron 5 campos ou `every_seconds`) / `tg.unschedule`; `/jobs` lista. Cada job dispara no tópico onde nasceu, com sessão do Claude própria. |

### Política de permissão

O bot passa `--settings '{"permissions":{"ask":["Bash"]}}'`: regras *ask* são avaliadas
**antes** das *allow* (inclusive as globais de `~/.claude/settings.json`), então todo `Bash`
chega à mesa do bot, que decide:

1. regra explícita do usuário ("Sempre nesta sessão", persistida por tópico) → aprova, mesmo sensível;
2. read-only embutido (`ls`, `cat`, `git status`…) ou `allowed_tools` do projeto → aprova, **exceto** comando sensível;
3. senão → pergunta no Telegram (privado) ou nega (grupo/guest/job em grupo).

## Rodar

```bash
uv sync
cp .env.example .env                      # token, ALLOWED_CHAT_IDS, WORKSPACE
cp projects.example.json projects.json    # alias → cwd, allowed_tools, tasks, chats
uv run bot.py
```

No **@BotFather → Mini App → bot → Settings**: ligar **Threaded Mode** (F2) e, se quiser
usar em grupos sem adicionar o bot, **Guest Chat Mode** (F7). A propagação leva ~5 min
(`getMe` → `has_topics_enabled`).

### Como serviço (systemd --user)

```bash
cp deploy/claude-telegram-bot-v2.service deploy/whisperx-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now whisperx-server claude-telegram-bot-v2
journalctl --user -u claude-telegram-bot-v2 -u whisperx-server -f
```

O `whisperx-server` roda no venv do WhisperX (`~/whisperx/.venv`), não no do bot.

Comandos: `/status` · `/project` · `/new` · `/fork` · `/sessions` · `/reset` · `/cancel` ·
`/yolo [min]` · `/allow [clear]` · `/jobs` · `/unschedule <id>` · `/link`.

## Desenvolvimento

```bash
uv run ruff format . && uv run ruff check .
uv run python tests/test_core.py    # 18 testes: núcleo com fakes + bridge MCP real
```

## Arquitetura

Portas & adaptadores. O núcleo (`turn.py`) só conhece `RunningClaude` (eventos) e
`DraftSink` (rascunho/entrega), então os testes rodam sem token nem subprocess.

| Módulo | Responsabilidade |
|---|---|
| `app.py` · `bot.py` | composition root / entrypoint |
| `config.py` · `projects.py` | `Config.from_env()`, registry de projetos (`projects.json`) |
| `claude_stream.py` | subprocess do Claude + `parse_line` (NDJSON → eventos) + flags |
| `turn.py` | núcleo do turno: segmentos, throttle, keepalive, stop, resultado |
| `runner.py` | orquestra um turno: RunSpec, bridge, política, sessão, título, entrega de mídia |
| `telegram_sink.py` | `TelegramSink` (privado: draft + rich) e `ReplySink` (grupo/guest) |
| `mcp_bridge.py` | servidor MCP stdio (só stdlib) que o Claude sobe; encaminha as tools ao bot |
| `bridge_server.py` | HTTP loopback do bot (`/ask` bloqueia até a decisão; `/tool/<nome>`) |
| `permissions.py` · `permission_desk.py` | regras, comando sensível, `decide()`, UI dos botões |
| `questions.py` · `checklist.py` · `delivery.py` · `scheduler.py` | tools `ask_user`, `update_checklist`, `send_file`, `schedule` |
| `media.py` · `transcriber.py` · `sessions_index.py` | download voz/imagem, reply-context, WhisperX, índice de sessões |
| `handlers.py` · `callbacks.py` · `group.py` | routers: privado/tópicos, botões+Stop, grupos/guest |
| `store.py` | conversa `(chat, tópico)` → projeto, sessão (TTL), regras, yolo, checklist |
| `formatting.py` | Markdown → Rich Markdown / HTML, chunking fence-aware |

## Fora do escopo

F8 Mini App (painel com biometria) — exige um domínio HTTPS público; projeto à parte.
