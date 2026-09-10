<p align="right"><a href="README.md">🇺🇸 English</a> · 🇧🇷 Português</p>

<h1 align="center">claude-telegram-bot-v2</h1>

<p align="center">
  <b>Claude Code no bolso.</b><br>
  Fale por texto, voz ou imagem com o Claude Code rodando na sua máquina — e veja a resposta
  nascer em tempo real no Telegram, aprove comandos com um toque e deixe tarefas agendadas
  trabalhando enquanto você está na rua.
</p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="aiogram 3.31" src="https://img.shields.io/badge/aiogram-3.31-2CA5E0?logo=telegram&logoColor=white">
  <img alt="Bot API 10.3" src="https://img.shields.io/badge/Telegram%20Bot%20API-10.3-26A5E4?logo=telegram&logoColor=white">
  <img alt="Claude Code" src="https://img.shields.io/badge/Claude%20Code-headless-D97757">
  <a href="https://github.com/correaschneider/claude-telegram-bot-v2/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/correaschneider/claude-telegram-bot-v2/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="license" src="https://img.shields.io/badge/uso-pessoal-lightgrey">
</p>

---

## ✨ Como é usar

```
você     🎤 (áudio de 12 s)
bot      📝 Transcrito: vê se o MR 1580 do backend tá verde e me diz o que falta pra mergear
bot      ┆ Thinking…                                  ← rascunho nativo, cresce sozinho
bot      ┆ Vou olhar o pipeline do !1580.
         ┆ 🔧 Bash · glab mr view 1580 --repo …        ← ferramenta em execução
bot      🔐 Permissão · Bash
         ┃ glab ci retry 1580
         [ ✅ Aprovar ] [ ❌ Negar ]
         [ 🔁 Sempre nesta sessão ] [ 📋 Copiar comando ]
você     ✅
bot      ▸ 🔎 Progresso (3 ferramentas)               ← colapsável (Rich Message)
         O !1580 está verde. Falta só o approve do Bruno — o job de lint passou
         na 2ª tentativa.
         ⏱ 41s · ↓96k ↑312 tokens · 4 turnos
```

Cada **tópico** do chat é um projeto com sessão própria. Um `/new habilis` abre outro
contexto, sem misturar. Pediu *"todo dia 9h me manda os MRs abertos"*? Vira um agendamento
que dispara no mesmo tópico. Precisa decidir entre A e B? O Claude pergunta com **botões**.

## 🚀 Por que existe

Operar o Claude Code pelo celular sempre teve três dores: **não saber se travou**, **um só
projeto por bot** e **`--dangerously-skip-permissions` como única política**. Entre dez/2025
e ago/2026 o Telegram lançou primitivos nativos pra bots de IA — rascunho em streaming com
botão *Stop*, tópicos em chat privado, Rich Messages, mensagens efêmeras, guest mode — e este
bot usa todos eles. Foi reescrito em **aiogram 3.31** porque é a única lib Python que expõe a
Bot API 10.x.

## 🧩 O que ele faz

| | |
|---|---|
| ⚡ **Streaming de verdade** | `sendMessageDraft`: placeholder "Thinking…" nativo, texto crescendo, `🔧 Bash · git status` enquanto a ferramenta roda, keepalive automático. Botão **Stop** do cliente (ou `/cancel`) mata o processo e preserva o parcial. |
| 🧵 **Tópicos = sessões** | Cada tópico tem projeto (`cwd`) e sessão do Claude próprios. `/new <alias>`, `/fork` (bifurca a conversa), `/project`, `/sessions` (retoma qualquer sessão recente do Claude Code). Título automático após o 1º turno. |
| 🔐 **Aprovação por toque** | `--permission-prompt-tool` → bridge MCP → card com **Aprovar / Negar / Sempre nesta sessão / Copiar**. Comando sensível (`rm`, `push`, `DROP`, `deploy`, `ssh`…) exige **dois toques**. O Claude espera até 24 h pela sua decisão. `/yolo [min]` desliga tudo por um prazo, só naquele tópico. |
| 📝 **Rich Messages** | Headings, tabelas, code, task list. O "raciocínio" entre ferramentas vai num `<details>` colapsável; a resposta fica limpa. Fallback automático pra HTML. |
| ☑️ **Checklist viva** | Tool `update_checklist`: o Claude registra o plano e vai marcando etapas numa mensagem editada in-place. |
| 🎤 **Voz** | WhisperX `large-v3` **residente** (modelo carregado uma vez, ~0,5 s por áudio, descarrega quando ocioso). Eco `📝 Transcrito:` antes de responder. |
| 🎬 **Vídeo** | Vídeo/video note/documento → áudio extraído (trilhas mixadas), transcrito no WhisperX residente com timestamps, o Claude escolhe os momentos-chave (saída estruturada), o ffmpeg tira um print por momento (frame uniforme/vazio é detectado pelo desvio-padrão via ImageMagick e re-tentado em ±5/10 s), e o turno normal recebe transcrição + prints e responde com resumo + seção por momento; os prints chegam sozinhos. Sem fala (sem trilha ou mudo) → os momentos vêm da detecção de cena do ffmpeg e o Claude descreve os prints. Limite da Bot API: 20 MB por arquivo. |
| 🖼️ **Imagem** | Foto ou documento de imagem → o Claude abre com `Read`. A legenda é o pedido. |
| ↩️ **Reply** | Responder a uma mensagem (texto, transcrição, imagem, trecho selecionado) põe o teor dela no prompt. |
| ❓ **Perguntas com botões** | Tool `ask_user(question, options, multi)`: escolha única ou múltipla, o Claude continua com a resposta. |
| 📎 **Arquivos** | png/pdf/mp3/mp4 citados por caminho absoluto chegam sozinhos; tool `send_file` entrega qualquer arquivo. |
| ⏰ **Agendamentos** | Tool `schedule` (cron de 5 campos ou `every_seconds`): dispara no tópico de origem com sessão própria. `/jobs`, `/unschedule`. |
| 🔗 **Deep link** | `/link habilis` ou `/link HT-123` → `t.me/<bot>?start=…` abre um tópico já no projeto certo. |
| 👥 **Grupos / guest mode** | Em grupos autorizados, dispara por **reply** a uma mensagem do bot, por **`/ask <pergunta>`** ou por menção (`@bot` só chega se o *Group Privacy* estiver desligado — o Telegram não entrega menções em privacy mode). Sem ser membro, via guest mode. Resposta **efêmera** só pra quem perguntou (`#todos` = pública; efêmera exige o bot como **admin** do grupo — senão responde em público, como reply). Em grupo nunca há aprovação: fora da allowlist é negado. Em **canal** (bot admin), post com `/ask …` recebe resposta pública em reply. |

## 🛡️ Política de permissão

Regras *ask* do Claude Code são avaliadas **antes** das *allow* — inclusive das globais do
seu `~/.claude/settings.json` — e antes do `acceptEdits`. O bot passa
`--settings '{"permissions":{"ask":["Bash","Edit","Write","NotebookEdit"]}}'` e todo comando
ou escrita de arquivo cai na mesa dele, que decide em três degraus:

1. **Você já aprovou "sempre nesta sessão"** → executa, mesmo se sensível.
2. **Read-only embutido** (`ls`, `cat`, `git status`…), **`Edit`/`Write`** ou **`allowed_tools` do projeto** → executa, **exceto** se for sensível: comando que casa um padrão (`rm`, `git push`, `DROP TABLE`, `curl | sh`, `kubectl delete`…) ou escrita em caminho sensível (`.env`, `~/.ssh`, `/etc`, `*.pem`, `settings.json`…).
3. Senão → **pergunta** (chat privado) ou **nega** (grupo, guest, job em grupo).

As três listas vivem em **`permissions.json`** (copie de `permissions.example.json`; recarregado
sozinho quando muda). Toda decisão vai pro `.decisions.jsonl`, e **`/audit`** resume: o que foi
perguntado e sempre aprovado vira botão *"➕ adicionar ao projeto"*, que grava a regra no
`projects.json`.

## ⚙️ Quick start

```bash
git clone git@github.com:correaschneider/claude-telegram-bot-v2.git && cd claude-telegram-bot-v2
uv sync
cp .env.example .env                      # TELEGRAM_TOKEN, ALLOWED_CHAT_IDS, WORKSPACE
cp projects.example.json projects.json    # alias → cwd, allowed_tools, tasks, chats
uv run tgclaude
```

No **@BotFather → Mini App → seu bot → Settings**: ligue **Threaded Mode** (tópicos) e, se
quiser grupos sem adicionar o bot, **Guest Chat Mode**. Propaga em ~5 min.

Pra rodar como serviço, instalar o WhisperX e resolver problemas: **[INSTALL.pt-BR.md](INSTALL.pt-BR.md)**.

## 🏗️ Arquitetura

```mermaid
flowchart LR
    TG[("Telegram")] <-- "updates · drafts · rich messages · botões" --> H["telegram/handlers · callbacks · group<br/>(aiogram)"]
    H --> R["claude/runner.py<br/>monta o turno"]
    R --> T["core/turn.py<br/>buffer · throttle · keepalive · stop"]
    T -- "DraftSink" --> S["telegram/sink.py"]
    S --> TG
    R -- "claude -p --output-format stream-json<br/>--permission-prompt-tool mcp__tg__ask" --> C["Claude Code<br/>(subprocesso)"]
    C -- "NDJSON" --> T
    C -- "MCP stdio" --> B["claude/mcp_bridge.py<br/>(stdlib)"]
    B -- "HTTP loopback" --> BS["tools/server.py"]
    BS --> PD["tools/permission_desk<br/>questions · checklist<br/>delivery · scheduler"]
    PD --> TG
    H --> W["whisperx_server<br/>(modelo residente)"]
```

Portas & adaptadores: o núcleo (`core/turn.py`) só conhece `RunningClaude` (eventos) e
`DraftSink` (rascunho/entrega). Por isso os 19 testes rodam sem token, sem rede e sem GPU —
e os fluxos reais (aprovar, negar, "sempre", grupo, perguntas, agendamento, arquivos) foram
validados de ponta a ponta contra o Claude de verdade.

Layout padrão de aplicação Python: pacote instalável em **`src/tgclaude/`** (*src layout*),
entrypoint declarado em `pyproject.toml` (`uv run tgclaude` ou `python -m tgclaude`), testes em
`tests/` com pytest, e o que não é código do pacote em `deploy/`. Subpacotes por camada:
`core/` (sem Telegram nem subprocesso), `claude/` (lado Claude Code), `tools/` (o que a bridge
expõe ao Claude) e `telegram/` (adaptadores aiogram).

| Módulo | Responsabilidade |
|---|---|
| `app.py` · `__main__.py` | composition root / entrypoint (`tgclaude`) |
| `config.py` · `services.py` | `Config.from_env()`, container de dependências |
| `core/turn.py` | núcleo do turno: segmentos, throttle, keepalive, stop, resultado |
| `core/permissions.py` · `core/audit.py` | política (read-only / sensível / caminhos), `decide()`, log de decisões |
| `core/projects.py` · `core/store.py` · `core/formatting.py` | registry de projetos, conversa `(chat, tópico)` persistida, Markdown → Rich/HTML |
| `claude/stream.py` · `claude/runner.py` | subprocesso do Claude (NDJSON → eventos, flags) e orquestração do turno |
| `claude/mcp_bridge.py` · `claude/sessions_index.py` | servidor MCP stdio (só stdlib) spawnado pelo Claude; índice de sessões |
| `tools/server.py` | HTTP loopback do bot (`/ask`, `/tool/<nome>`) |
| `tools/permission_desk.py` · `tools/questions.py` · `tools/checklist.py` · `tools/delivery.py` · `tools/scheduler.py` | o que a bridge expõe: aprovação, `ask_user`, `update_checklist`, `send_file`, `schedule` |
| `telegram/sink.py` · `telegram/handlers.py` · `telegram/callbacks.py` · `telegram/group.py` | adaptadores aiogram: rascunho/entrega, comandos, botões+Stop, grupos/guest |
| `telegram/media.py` · `telegram/transcriber.py` | download voz/imagem, reply-context, WhisperX (servidor + CLI) |
| `deploy/` | units `systemd --user` do bot e do `whisperx_server.py` |
## 🧪 Desenvolvimento

```bash
uv run ruff format . && uv run ruff check .
uv run pytest
```

## 🗺️ Roadmap

- [ ] Mini App do Telegram: painel de sessões, diff com highlight e **confirmação biométrica** pra deploy em produção (precisa de um domínio HTTPS público).
- [ ] Ephemeral + guest em canais/comunidades.
- [ ] Ajuste fino das listas read-only/sensível conforme o uso (`/audit`).
- [ ] Codex CLI como agente alternativo: o núcleo é agnóstico; falta um driver `agents/codex.py` (`codex exec --json`, `resume`, MCP via `-c`). Aprovação por botão (F4) não portaria — o `exec` não tem hook externo de aprovação, só níveis de sandbox.

---

<p align="center">Feito por um dev que queria acompanhar o trabalho do Claude Code do celular sem abrir SSH.</p>
