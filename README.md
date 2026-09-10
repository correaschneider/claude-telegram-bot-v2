# claude-telegram-bot-v2

Bot de Telegram que repassa mensagens ao **Claude Code** headless e mostra a resposta
**enquanto ela é gerada**, usando o rascunho vivo do Telegram (`sendMessageDraft`,
Bot API 9.5+). Sucessor do `claude-telegram-bot` (python-telegram-bot), reescrito em
**aiogram 3.31** porque só ele expõe a Bot API 10.x.

## O que o MVP faz (F1)

- Texto → `claude -p --output-format stream-json --include-partial-messages` no `WORKSPACE`.
- Rascunho começa com o placeholder nativo "Thinking…" e vai crescendo com o texto do Claude
  (janela deslizante de 3500 chars), atualizado no máximo a cada `DRAFT_INTERVAL_SECONDS`.
- Enquanto uma ferramenta roda, o rascunho mostra `🔧 Bash · git status`; sem eventos, o bot
  reenvia o rascunho a cada `DRAFT_KEEPALIVE_SECONDS` (o Telegram apaga rascunho parado ~30s).
- Botão **Stop** do cliente (ou `/cancel`) mata o processo e preserva o texto parcial como mensagem.
- Resposta final em HTML (code fence, `code`, **bold**, headings) com rodapé de tempo/custo e aviso
  de ferramentas negadas por permissão.
- Sessão por chat (`--resume`) com TTL; `/reset` zera; `/yolo` liga `--dangerously-skip-permissions`.

## Rodar

```bash
uv sync
cp .env.example .env   # token, ALLOWED_CHAT_IDS, WORKSPACE
uv run bot.py
```

Comandos: `/status` · `/reset` · `/cancel` · `/yolo`.

## Desenvolvimento

```bash
uv run ruff format . && uv run ruff check .
uv run python tests/test_core.py
```

## Arquitetura

Portas & adaptadores, igual ao bot v1. O núcleo (`turn.py`) só conhece as portas
`RunningClaude` (eventos do Claude) e `DraftSink` (rascunho/mensagem), então os testes rodam
sem token nem subprocess.

| Módulo | Responsabilidade |
|---|---|
| `app.py` · `bot.py` | composition root / entrypoint |
| `config.py` | `Config.from_env()` |
| `claude_stream.py` | subprocess do Claude + `parse_line` (NDJSON → eventos) |
| `turn.py` | núcleo do turno: buffer, throttle, keepalive, stop, mensagem final |
| `telegram_sink.py` | adaptador aiogram da porta `DraftSink` |
| `handlers.py` | router do aiogram + allowlist middleware |
| `sessions.py` | `chat_id → session_id` em JSON atômico com TTL |
| `formatting.py` | Markdown → HTML do Telegram, chunking fence-aware |

## Próximos passos (do briefing)

F2 tópicos em chat privado = sessões · F4 aprovação por botão (`PreToolUse` + `defer`) ·
F3 Rich Messages na resposta final · F5 checklist via tool MCP · voz/imagem do v1.
