# CLAUDE.md — guia para sessões do Claude Code neste repo

Bot de Telegram (aiogram 3.31) que repassa mensagens ao **Claude Code headless** e faz
streaming da resposta. Pacote `tgclaude` em *src layout*. Docs de produto: `README.md`
(en) / `README.pt-BR.md`; instalação e troubleshooting: `INSTALL.md` / `INSTALL.pt-BR.md`.
**Toda mudança de comportamento visível atualiza os quatro (pt e en).**

## Comandos

```bash
uv sync --group dev            # venv + pacote editável + ruff/pytest
uv run ruff format . && uv run ruff check .
uv run pytest                  # ~23 testes, sem token/rede/GPU (test_bridge sobe o mcp_bridge real)
uv run tgclaude                # rodar na mão (lê .env do cwd)
systemctl --user restart claude-telegram-bot-v2   # produção nesta máquina
journalctl --user -u claude-telegram-bot-v2 -f
systemctl --user restart whisperx-server           # só se deploy/whisperx_server.py mudou
```

Commits em PT-BR, `tipo(escopo): descrição` (feat/fix/docs/refactor/ci/chore). CI (ruff + pytest)
roda em push/PR; `main` protegida.

## Layout

| Pasta | O quê | Regra |
|---|---|---|
| `src/tgclaude/core/` | turn, permissions, audit, projects, store, formatting | **sem** Telegram nem subprocesso — testável puro |
| `src/tgclaude/claude/` | stream (NDJSON → eventos), runner (monta o turno), sessions_index, `mcp_bridge.py` | `mcp_bridge.py` é **stdlib puro** e lê `TG_*` do ambiente ao importar → nunca `import`; caminho via `importlib.util.find_spec` |
| `src/tgclaude/tools/` | server (HTTP loopback), permission_desk, questions, checklist, delivery, scheduler, video | o que a bridge expõe ao Claude |
| `src/tgclaude/telegram/` | sink, handlers, callbacks, group, media, transcriber | adaptadores aiogram |
| `deploy/` | units systemd + `whisperx_server.py` (roda no venv `~/whisperx/.venv`, não no do bot) | |
| `tests/` | pytest; fakes em `helpers.py` | |

Estado e config ficam na **raiz do repo** (cwd do serviço), gitignored: `.env`, `projects.json`,
`permissions.json`, `.store.json`, `.jobs.json`, `.decisions.jsonl`. Exemplos: `*.example.json`,
`.env.example` — atualize-os junto com `config.py`.

## Invariantes (não regredir sem o Pedro pedir)

- **Política de permissão vive na mesa do bot.** O turno passa `--settings '{"permissions":{"ask":[Bash,Edit,Write,NotebookEdit]}}'`: regras *ask* vencem as *allow* globais e o `acceptEdits`, então tudo cai em `PermissionDesk.ask` → `Policy.decide()` (explícito "sempre" > read-only/`allowed_tools` exceto sensível > prompt/deny). `MCP_TOOL_TIMEOUT` (24 h) segura o processo esperando o humano. Toda decisão vai pro `.decisions.jsonl` (`/audit`).
- **Grupo/canal nunca pergunta** (`can_prompt=False` → nega). Efêmera exige bot admin; menção exige admin ou privacy off; `/ask` e reply funcionam sempre; canal = `channel_post`, autorização só pelo id do chat.
- **Núcleo agnóstico**: `core/turn.py` só conhece `RunningClaude` (Init/TextDelta/ToolStart/ToolDone/Result/Exited) e `DraftSink`. Novo agente (ex.: Codex) = driver novo em `claude/`, não mudança no core.
- **Voz/vídeo usam o `whisperx-server` residente** (`/transcribe` devolve `text` + `segments`); cair pro CLI **só** se o servidor estiver inalcançável — com ele no ar, um 2º `large-v3` dá CUDA OOM.
- Vídeo: momentos escolhidos via `claude -p --json-schema` com `--disallowedTools` por **lista explícita** (`"*"` nega a própria saída estruturada); frame vazio = desvio-padrão do ImageMagick (entropia **não** funciona); sem fala → cenas do ffmpeg.
- Rascunho (`sendMessageDraft`) só em chat privado; `draft_id` = `message_id`; keepalive porque o Telegram apaga rascunho parado ~30 s.
- Áudio sem confirmação por botão (decisão de produto antiga do Pedro).

## Pegadinhas operacionais

- **Nunca `pkill -f "python bot.py"`/`pkill -f tgclaude` de dentro de uma sessão do Claude Code** — o `bash -c` do tool contém a string e morre junto. Use `systemctl --user restart` ou mate por PID filtrando `readlink /proc/<pid>/cwd`.
- **Antes de reiniciar, confira se há turno ativo** (`/status` ou `journalctl … | grep "claude start"` sem `handled` depois): restart mata o turno do usuário no meio.
- Id de chat novo com o bot rodando sai do **journal** (`update ignorado: chat=…`), não do `getUpdates` (conflita com o polling).
- `claude -p`: `stdin=DEVNULL` (senão espera 3 s), `limit=16MB` no StreamReader, `--verbose` obrigatório com `stream-json`. `echo`/`ls` são read-only nativos — não servem pra testar permissão.
- Ligar tópicos no BotFather = **"Threaded Mode"** (só no Mini App), propaga em ~5 min.
- Bot API baixa até 20 MB (vídeo maior é recusado; o bot avisa).

## Testar de verdade

Suíte unitária não cobre o Claude real. Pra validar mudança em runner/bridge/permissões, rode um
e2e com `run_turn` + `FakeBot` (padrão nos commits de 10/09: `sh -c 'echo x > /tmp/f'` força
prompt; `rm` força sensível; clicker resolve `desk.pending`). Custa ~10 s e alguns centavos por turno.
