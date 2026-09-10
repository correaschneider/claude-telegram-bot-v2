# claude-telegram-bot-v2

Bot de Telegram que repassa mensagens ao **Claude Code** headless e mostra a resposta
**enquanto ela é gerada**, usando os recursos que o Telegram lançou para bots de IA
(Bot API 9.5–10.3). Sucessor do `claude-telegram-bot` (python-telegram-bot), reescrito em
**aiogram 3.31** porque só ele expõe a Bot API 10.x.

## Recursos

| | Recurso | Como |
|---|---|---|
| F1 | **Streaming** | `sendMessageDraft`: placeholder "Thinking…" nativo, texto crescendo, `🔧 Bash · git status` enquanto a ferramenta roda, keepalive (o Telegram apaga rascunho parado em ~30s), botão **Stop** do cliente (`stopped_message_generation`) ou `/cancel` matam o processo e preservam o parcial. |
| F2 | **Tópicos = sessões** | Em chat privado com *Topics* ligado, cada tópico é uma conversa com projeto (`cwd`) e sessão (`--resume`) próprios. `/new <alias>` cria tópico, `/fork` bifurca a sessão atual (`--fork-session`), `/project` troca o projeto do tópico. Tópico criado pelo bot ganha título automático após o 1º turno. |
| F3 | **Rich Messages** | Resposta final em Rich Markdown (headings, tabelas, code, task list); os trechos intermediários entre ferramentas vão num `<details>` colapsável; rodapé em itálico com tempo e tokens (`↓entrada ↑saída`; `FOOTER_COST=true` acrescenta o custo em USD). Fallback automático para HTML e texto puro. |
| F4 | **Aprovação por botão** | `--permission-prompt-tool` → MCP `tg.ask` (bridge stdio→HTTP local) → mensagem com **Aprovar / Negar / Sempre nesta sessão / Copiar comando**. Comando sensível (`rm`, `push`, `DROP`, `deploy`, `ssh`…) exige **dois toques** (`answerCallbackQuery` com alerta). O Claude fica esperando (`MCP_TOOL_TIMEOUT`, default 24h), então dá pra aprovar horas depois. `/yolo [min]` pula tudo por um prazo, por tópico. |
| F5 | **Checklist** | Tool MCP `tg.update_checklist` (instruída no system prompt): o Claude registra o plano e marca etapas; o bot mantém uma Rich Message com task list GFM editada in-place. (A checklist nativa exige `business_connection_id`.) |
| F6 | **Deep link** | `/link <alias>` ou `/link HT-123` gera `t.me/<bot>?start=…`; abrir o link cria o tópico já no projeto certo (prefixo de task → projeto via `projects.json`). |
| F7 | **Grupos / guest mode** | Responde a menção ou reply em grupos autorizados (e a `guest_message` sem ser membro). Sem rascunho (API só permite em privado): "digitando…" + resposta **efêmera** pro autor (`#todos` torna pública). **Nunca pede aprovação em grupo**: o que não está na allowlist do projeto é negado. |

### Política de permissão

O bot passa `--settings '{"permissions":{"ask":["Bash"]}}'`: regras *ask* são avaliadas
**antes** das *allow* (inclusive as globais de `~/.claude/settings.json`), então todo `Bash`
chega à mesa do bot, que decide:

1. regra explícita do usuário ("Sempre nesta sessão", persistida por tópico) → aprova, mesmo sensível;
2. read-only embutido (`ls`, `cat`, `git status`…) ou `allowed_tools` do projeto → aprova, **exceto** comando sensível;
3. senão → pergunta no Telegram (privado) ou nega (grupo/guest).

## Rodar

```bash
uv sync
cp .env.example .env                      # token, ALLOWED_CHAT_IDS, WORKSPACE
cp projects.example.json projects.json    # alias → cwd, allowed_tools, tasks, chats
uv run bot.py
```

No **@BotFather** → `/mybots` → Bot Settings: ligar **Topics in Private Chats** (F2) e, se
quiser usar em grupos sem adicionar o bot, **Guest Mode** (F7).

Comandos: `/status` · `/project` · `/new` · `/fork` · `/reset` · `/cancel` · `/yolo [min]` · `/allow [clear]` · `/link`.

## Desenvolvimento

```bash
uv run ruff format . && uv run ruff check .
uv run python tests/test_core.py    # 13 testes: núcleo com fakes + bridge MCP real
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
| `runner.py` | orquestra um turno: RunSpec, bridge, política, sessão, título do tópico |
| `telegram_sink.py` | `TelegramSink` (privado: draft + rich) e `ReplySink` (grupo/guest) |
| `mcp_bridge.py` | servidor MCP stdio (só stdlib) que o Claude sobe; encaminha `ask`/`update_checklist` ao bot |
| `bridge_server.py` | HTTP loopback do bot (`/ask` bloqueia até a decisão, `/checklist`) |
| `permissions.py` · `permission_desk.py` | regras, comando sensível, `decide()`, UI dos botões |
| `checklist.py` | Rich Message com task list editada in-place |
| `handlers.py` · `callbacks.py` · `group.py` | routers: privado/tópicos, botões+Stop, grupos/guest |
| `store.py` | conversa `(chat, tópico)` → projeto, sessão (TTL), regras, yolo, checklist |
| `formatting.py` | Markdown → Rich Markdown / HTML, chunking fence-aware |

## Fora do escopo (por enquanto)

F8 Mini App (painel com biometria — precisa de hosting HTTPS, projeto à parte) · voz/imagem/agendamentos do v1.
