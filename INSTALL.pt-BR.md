<p align="right"><a href="INSTALL.md">🇺🇸 English</a> · 🇧🇷 Português</p>

# Instalação

Guia completo: dependências, bot no Telegram, configuração, execução como serviço,
WhisperX (voz), atualização e troubleshooting. O [README](README.pt-BR.md) explica o que o bot faz.

## 1. Dependências

### Obrigatórias

| O quê | Versão | Por quê |
|---|---|---|
| **Linux** com `systemd --user` | — | os dois serviços (bot e WhisperX) são units de usuário |
| **Python** | ≥ 3.12 | `uv` baixa sozinho se faltar |
| [**uv**](https://docs.astral.sh/uv/) | ≥ 0.11 | cria o venv e instala pelo `uv.lock` (versões exatas) |
| **Claude Code CLI** (`claude`) | ≥ 2.1.259 | é quem executa os turnos (`claude -p --output-format stream-json`); `--permission-prompts` e `--fork-session` precisam dessa versão |
| **git** | — | clonar/atualizar |

O `claude` precisa estar **logado no mesmo usuário que roda o serviço** (`claude --version` e
um `claude -p "oi"` no terminal têm que funcionar). Os servidores MCP configurados nesse
usuário (ClickUp, Atlassian, Notion…) ficam disponíveis pro bot automaticamente; basta
liberar em `allowed_tools` (ex.: `mcp__clickup`).

### Opcionais (só pra mensagem de voz)

| O quê | Por quê |
|---|---|
| **GPU NVIDIA** + driver com CUDA 12.x | WhisperX `large-v3` em float16 usa ~3,9 GB de VRAM; em CPU funciona, mas lento |
| **WhisperX** num venv próprio (`~/whisperx/.venv`) | transcrição (seção 6) |
| **ffmpeg** | o WhisperX usa pra decodificar o `.ogg` do Telegram |
| **`flock`** (util-linux) | serializa transcrições pra não estourar VRAM; se não existir, roda sem lock |

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv
curl -fsSL https://claude.ai/install.sh | bash       # Claude Code (depois: claude  → login)
sudo apt install -y ffmpeg util-linux git
```

## 2. Clonar e instalar

```bash
git clone git@github.com:correaschneider/claude-telegram-bot-v2.git
cd claude-telegram-bot-v2
uv sync                       # cria .venv, instala o pacote tgclaude (editável) e as deps (+ ruff, pytest)
uv run pytest                      # 19 testes, sem token nem rede — confirma que o ambiente está são
```

## 3. Bot no Telegram (@BotFather)

1. `/newbot` → nome e username → guarde o **token**.
2. Abra o **Mini App do BotFather** (botão ao lado da caixa de texto — as opções abaixo **não
   aparecem** no menu de texto do `/mybots`) → seu bot → **Settings**:
   - **Threaded Mode** → ON — tópicos em chat privado (`/new`, `/fork`, deep links).
     Deixe *"Disallow users to create new threads"* OFF.
   - **Guest Chat Mode** → ON — só se quiser usar o bot em grupos sem adicioná-lo como membro.
   - *Group Privacy* pode ficar ligado — o que ele bloqueia e como contornar está na seção 3.2.
3. A propagação leva **~5 min**. Confira:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/getMe" | grep -o '"has_topics_enabled":[a-z]*\|"supports_guest_queries":[a-z]*'
   ```
   Enquanto estiver `false`, `/new` responde *"the chat is not a forum"*.

> Ligar tópicos faz o Telegram reter 15% de compras em **Stars** dentro do bot. Irrelevante
> pra um bot pessoal, mas é por isso que vem desligado.

Os comandos do menu (`/status`, `/new`…) o próprio bot registra ao subir (`setMyCommands`).

### 3.1 Descobrir o seu `chat_id`

**Com o bot parado**, mande qualquer mensagem pra ele e:

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | grep -o '"chat":{"id":[-0-9]*'
```

**Com o bot rodando** o `getUpdates` dá conflito com o polling — use o log: toda mensagem de
chat não autorizado é registrada com o id:

```bash
journalctl --user -u claude-telegram-bot-v2 -n 50 --no-pager -o cat | grep "update ignorado"
# → update ignorado: chat=-1003922999755 user=8778203590
```

Chat privado = id positivo (= seu user id). Grupo = id negativo (`-100…`, muda se um grupo
comum virar supergrupo). Use os dois em `ALLOWED_CHAT_IDS`; o bot só atende quem está lá.

### 3.2 Grupos

1. Adicione o bot ao grupo e **promova-o a admin** (nenhum direito específico é necessário).
   Sem admin: o Telegram não entrega menções (*Group Privacy*) e recusa resposta efêmera
   (`BOT_NOT_ADMIN`) — o bot ainda funciona por `/ask` e reply, respondendo em público.
2. Pegue o id do grupo (3.1) e ponha em `ALLOWED_CHAT_IDS`. Quem pode **disparar** o bot no
   grupo é quem está em `ALLOWED_USER_IDS` (default: só você); os demais são ignorados em silêncio.
3. Opcional: `"chats": [<id>]` no projeto certo do `projects.json` — senão o grupo conversa no
   projeto default.
4. `systemctl --user restart claude-telegram-bot-v2`.

| Gatilho | Funciona com… |
|---|---|
| `/ask <pergunta>` (ou `/claude …`) | sempre |
| **reply** a uma mensagem do bot | sempre |
| menção `@bot …` | bot admin, **ou** *Group Privacy* desligado + bot removido e re-adicionado |
| menção sem o bot ser membro | *Guest Chat Mode* ligado (responde uma vez, via `answerGuestQuery`) |

A resposta é **efêmera** (só quem perguntou vê) quando o bot é admin; `#todos` na mensagem
torna pública. Em grupo **nunca** há card de aprovação: o que não estiver em `allowed_tools`
do projeto é negado (e aparece no rodapé como `permission_denials`).

### 3.3 Canais

O bot só entra em canal como **admin** (com direito de postar) e recebe os posts como
`channel_post`. Post não tem autor, então a autorização é só pelo **id do canal** em
`ALLOWED_CHAT_IDS` (pegue pelo journal, como em 3.1). Um post com `/ask <pergunta>` ou menção
`@bot` dispara um turno e a resposta vem como **post público em reply** ao original — em canal
não existe efêmera nem rascunho. Comentários no grupo de discussão vinculado seguem a regra de
grupo (3.2). Sem card de aprovação: o que não estiver em `allowed_tools` é negado.

## 4. Configuração

### 4.1 `.env`

```bash
cp .env.example .env && chmod 600 .env
```

| Variável | Obrigatória | Default | Notas |
|---|---|---|---|
| `TELEGRAM_TOKEN` | ✅ | — | do BotFather |
| `ALLOWED_CHAT_IDS` | ✅ | — | csv de chats atendidos (privados e grupos) |
| `ALLOWED_USER_IDS` | | ids positivos de `ALLOWED_CHAT_IDS` | quem pode disparar em grupo/guest e **aprovar permissões** |
| `WORKSPACE` | ✅ | — | cwd quando não há `projects.json` |
| `PROJECTS_FILE` | | `./projects.json` | registry de projetos (4.2) |
| `CLAUDE_BIN` | | `claude` | caminho/wrapper do CLI |
| `CLAUDE_PERMISSION_MODE` | | `acceptEdits` | usado quando o projeto não define |
| `CLAUDE_ALLOWED_TOOLS` | | vazio | idem; sintaxe do `--allowedTools` (`Read Edit Bash(git:*) mcp__clickup`) |
| `CLAUDE_ADD_DIRS` | | vazio | `--add-dir` extras, csv |
| `CLAUDE_APPEND_SYSTEM_PROMPT` | | prompt embutido | substitui o texto que ensina as tools do Telegram — só mude sabendo o que faz |
| `STORE_FILE` · `JOBS_FILE` | | `./.store.json` · `./.jobs.json` | conversas/sessões e agendamentos |
| `DEFAULT_TZ` | | `America/Sao_Paulo` | fuso dos agendamentos |
| `SESSION_TTL_SECONDS` | | `21600` (6 h) | sessão parada além disso reseta |
| `YOLO_TTL_SECONDS` | | `3600` | duração padrão do `/yolo` |
| `DRAFT_INTERVAL_SECONDS` · `DRAFT_KEEPALIVE_SECONDS` · `DRAFT_MAX_CHARS` | | `1.5` · `10` · `3500` | ritmo do rascunho vivo |
| `RICH_MESSAGES` | | `true` | `false` = só HTML |
| `FOOTER_COST` | | `false` | mostra USD além dos tokens no rodapé |
| `GROUP_PUBLIC_TAG` | | `#todos` | em grupo, torna a resposta pública (senão é efêmera) |
| `BRIDGE_HOST` · `BRIDGE_PORT` | | `127.0.0.1` · `0` (aleatória) | HTTP loopback da bridge MCP |
| `MCP_TOOL_TIMEOUT_MS` | | `86400000` (24 h) | quanto o Claude espera uma aprovação/resposta sua |
| `WHISPERX_SERVER_URL` | | `http://127.0.0.1:8765` | servidor residente (seção 6); vazio = só CLI |
| `WHISPERX_BIN` · `WHISPERX_MODEL` · `WHISPERX_DEVICE` · `WHISPERX_COMPUTE_TYPE` · `WHISPERX_LANGUAGE` · `WHISPERX_BATCH` · `WHISPERX_LOCK` | | `~/.local/bin/whisperx-cli` · `large-v3` · `cuda` · `float16` · `pt` · `8` · `/tmp/whisperx-pipeline.lock` | CLI de fallback |
| `AUDIO_TMP_DIR` · `IMAGE_TMP_DIR` | | `/tmp/telegram-audio` · `/tmp/telegram-images` | downloads (áudio é apagado após transcrever; imagem fica pra follow-up) |

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

| Campo | Efeito |
|---|---|
| `path` | `cwd` do Claude (carrega o `CLAUDE.md` de lá) |
| `default` | projeto das conversas sem escolha explícita |
| `allowed_tools` | auto-aprovados **sem perguntar** (exceto comando sensível — ver README, "Política de permissão") |
| `permission_mode` · `add_dirs` | sobrescrevem os defaults do `.env` |
| `tasks` | prefixos de task: `/link HT-123` ou deep link `t:HT-123` caem neste projeto |
| `chats` | grupos que conversam neste projeto por padrão |

Troque de projeto por conversa/tópico com `/project`; crie tópicos já no projeto com `/new <alias>`.

### 4.3 Claude Code — permissões globais

O bot passa `--settings '{"permissions":{"ask":["Bash","Edit","Write","NotebookEdit"]}}'` a
cada turno: regras *ask* são avaliadas antes das *allow* e do `acceptEdits`, então **todo
comando e toda escrita de arquivo chegam à mesa do bot** mesmo que o seu
`~/.claude/settings.json` tenha `allow: ["Bash"]`. Não precisa mexer nas suas regras globais;
o que o bot auto-aprova é a lista read-only + `Edit`/`Write` + `allowed_tools` do projeto + o
que você marcou como "Sempre nesta sessão" — nunca o que for sensível.

### 4.4 `permissions.json` e `/audit`

```bash
cp permissions.example.json permissions.json     # opcional: sem o arquivo valem os defaults
```

| Chave | O quê |
|---|---|
| `read_only` | regras (sintaxe `--allowedTools`) auto-aprovadas em qualquer projeto: `Bash(ls *)`, `Bash(git status *)`… |
| `dangerous` | regexes (case-insensitive) sobre o comando Bash que **sempre perguntam**: `\brm\b`, `\bgit\b.*\bpush\b`, `\bdelete\s+from\b`… |
| `sensitive_paths` | globs (`fnmatch`, `*` casa `/`) de caminhos em que `Edit`/`Write` sempre perguntam: `*/.env`, `*/.ssh/*`, `/etc/*`… |

Cada chave presente **substitui** a lista default inteira (copie do example e edite). O arquivo
é recarregado automaticamente quando o mtime muda; JSON inválido mantém a política anterior e
avisa no log.

Toda decisão (auto, aprovada, "sempre", negada) fica em `.decisions.jsonl`. `/audit [n]` resume
as últimas *n* (default 200) do projeto atual e oferece botões **➕** pras regras que você
aprovou à mão repetidas vezes sem nunca negar — o toque grava a regra em `allowed_tools` do
projeto no `projects.json`, sem restart.

## 5. Rodar

### 5.1 Manual (primeiro teste)

```bash
uv run tgclaude
```

Deve logar `bot @seu_bot no ar; projetos=[...]; chats=[...]; topics=True guest=True`. Mande
`/start` no Telegram. `Ctrl+C` encerra. Só **um** processo por token — dois pollers dão
`TelegramConflictError`.

### 5.2 Como serviço (`systemd --user`)

```bash
mkdir -p ~/.config/systemd/user
cp deploy/claude-telegram-bot-v2.service ~/.config/systemd/user/
# se o repo NÃO está em /data/projects/claude-telegram-bot-v2, ajuste WorkingDirectory e ExecStart (.venv/bin/tgclaude) no unit
systemctl --user daemon-reload
systemctl --user enable --now claude-telegram-bot-v2
loginctl enable-linger $USER          # continua rodando sem sessão gráfica / após reboot
```

Operação:

```bash
systemctl --user status claude-telegram-bot-v2
journalctl --user -u claude-telegram-bot-v2 -f
systemctl --user restart claude-telegram-bot-v2
```

> Nunca `pkill -f tgclaude` de dentro de uma sessão do Claude Code: o `bash -c` do
> próprio tool contém a string e morre junto. Use `systemctl --user restart` ou mate por PID.

## 6. Voz — WhisperX (opcional)

Dois modos; o bot usa o servidor e cai pro CLI se ele estiver fora.

### 6.1 Instalar o WhisperX (venv próprio, fora do venv do bot)

```bash
uv venv ~/whisperx/.venv --python 3.12
uv pip install --python ~/whisperx/.venv/bin/python whisperx
# GPU: confira que o torch instalado enxerga CUDA
~/whisperx/.venv/bin/python -c "import torch; print(torch.cuda.is_available())"
```

Na **primeira** execução o WhisperX baixa o modelo (`Systran/faster-whisper-large-v3`, ~3 GB)
do Hugging Face — precisa de rede uma vez; depois pode rodar com `HF_HUB_OFFLINE=1`.

### 6.2 Servidor residente (recomendado — ~0,5 s por áudio)

Carrega o `large-v3` uma vez e transcreve por HTTP em loopback. Sem ele, cada áudio paga
~10 s subindo Python+CUDA+modelo.

```bash
cp deploy/whisperx-server.service ~/.config/systemd/user/
# ajuste ExecStart se o venv não for ~/whisperx/.venv ou o repo não estiver em /data/projects/...
systemctl --user daemon-reload
systemctl --user enable --now whisperx-server
curl -s http://127.0.0.1:8765/health      # {"loaded": true, ...} após ~6 s de preload
journalctl --user -u whisperx-server -f
```

Ajustes no unit (`Environment=`): `WHISPERX_MODEL`, `WHISPERX_COMPUTE_TYPE` (`float16` em GPU,
`int8` em CPU), `WHISPERX_LANGUAGE`, `WHISPERX_IDLE_SECONDS` (default 900: descarrega o modelo
após 15 min sem áudio e libera a VRAM; o próximo áudio paga ~6 s de recarga), `WHISPERX_PORT`.

### 6.3 CLI de fallback

`WHISPERX_BIN` aponta pra um executável compatível com o CLI do WhisperX. Com o WhisperX do
PyPI basta `WHISPERX_BIN=~/whisperx/.venv/bin/whisperx`. O bot passa `--model`, `--device`,
`--compute_type`, `--language`, `--batch_size`, `--no_align`, `--output_format json`,
`--output_dir`, sob `flock` quando ele existe.

> Com o servidor **no ar**, não tente rodar o CLI ao mesmo tempo: o segundo `large-v3` dá
> `CUDA out of memory`. Por isso o bot só cai pro CLI quando o servidor está inalcançável.

### 6.4 Sem GPU

`WHISPERX_DEVICE=cpu` e `WHISPERX_COMPUTE_TYPE=int8` (no `.env` e no unit do servidor). Modelo
menor (`medium`, `small`) se ficar lento demais.

## 7. Verificar que está tudo certo

| Teste | Esperado |
|---|---|
| `/start` | resposta com o projeto atual |
| texto qualquer | rascunho "Thinking…" crescendo, depois a resposta com rodapé `⏱ · ↓tokens ↑tokens` |
| `/new smsfunnel` | tópico criado (se `has_topics_enabled` já for `true`) |
| *"cria /tmp/x.txt com sh -c"* | card 🔐 com botões; **Aprovar** executa |
| *"apaga /tmp/x.txt com rm"* | card ⚠️ sensível; precisa de **2 toques** |
| áudio | eco `📝 Transcrito:` em ~1 s (servidor) e o turno começa |
| *"me pergunta se prefiro A ou B"* | pergunta com botões |
| *"a cada 5 min me diz a hora"* | `/jobs` lista; `/unschedule 1` remove |

## 8. Atualizar

```bash
git -C /data/projects/claude-telegram-bot-v2 pull
uv sync
uv run pytest
systemctl --user restart claude-telegram-bot-v2
# se deploy/whisperx_server.py mudou:
systemctl --user restart whisperx-server
```

## 9. Troubleshooting

| Sintoma | Causa / fix |
|---|---|
| `TelegramConflictError: terminated by other getUpdates request` | dois processos com o mesmo token — pare o outro (`nohup` antigo, v1 com o mesmo token…) |
| `/new` → *"the chat is not a forum"* | Threaded Mode recém-ligado; espere ~5 min e confira `getMe` |
| nunca aparece card 🔐 | `/yolo` ligado nesse tópico (`/status` mostra), ou o comando está em `allowed_tools`/read-only |
| tudo é negado em grupo | esperado: grupo não tem aprovador; ponha o necessário em `allowed_tools` do projeto |
| `❌ Claude encerrou sem resultado (rc=1)` | rode `claude -p "oi"` no terminal do mesmo usuário — normalmente é login/`claude` fora do `PATH` do unit |
| áudio demora ~10 s | `whisperx-server` parado ou `WHISPERX_SERVER_URL` vazio — `systemctl --user status whisperx-server` |
| `CUDA failed with error out of memory` | servidor + CLI ao mesmo tempo, ou outra pipeline na GPU; reduza `WHISPERX_IDLE_SECONDS` ou use `int8` |
| áudio: `whisperx exit 1 … HF_HUB_OFFLINE` | primeira execução precisa baixar o modelo com rede |
| rascunho some após ~30 s parado | comportamento do Telegram; o bot reenvia a cada `DRAFT_KEEPALIVE_SECONDS` — se o Claude está mudo há mais que isso, veja o journal |
| `permission_denials` no rodapé | Claude tentou algo fora da allowlist sem aprovador (grupo/job) — ajuste `allowed_tools` |
| menção `@bot` no grupo não chega (nem aparece no log) | *Group Privacy* ligado: o Telegram não entrega menções. Use `/ask …` ou reply, ou desligue o privacy e re-adicione o bot |
| em grupo a resposta vem pública, não efêmera | o bot não é admin do grupo (`BOT_NOT_ADMIN` no log): promova o bot a admin |
| mudou o `.env` e nada aconteceu | o `.env` é lido só no start: `systemctl --user restart claude-telegram-bot-v2` |
