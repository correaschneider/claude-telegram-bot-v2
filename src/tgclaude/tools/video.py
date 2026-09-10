"""Digest de vídeo, sem depender de skill instalada.

Partes determinísticas ficam aqui (ffmpeg/whisperx/identify); as de julgamento vão pro
Claude: (1) uma chamada curta com `--json-schema` escolhe os momentos-chave na transcrição;
(2) o turno normal do tópico recebe transcrição + prints e escreve o digest.

Saídas ficam ao lado do vídeo: `<stem>.transcript.txt/.json` e `<stem>.moments/*.jpg`."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass

log = logging.getLogger("claude-bot")

MOMENTS_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "moments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "t": {"type": "number", "description": "segundos, = start do segment"},
                        "label": {"type": "string", "description": "3-6 palavras, o que acontece"},
                        "quote": {"type": "string", "description": "texto do segment"},
                    },
                    "required": ["t", "label", "quote"],
                },
            }
        },
        "required": ["moments"],
    }
)

SELECT_PROMPT = """Abaixo está a transcrição de um vídeo ({duration}), um segment por linha no formato
`[MM:SS] texto`. Escolha os {max_moments} momentos mais importantes (menos se o vídeo for curto ou
monótono; mínimo 1), priorizando nesta ordem:
1. mudança de tópico / abertura de assunto novo;
2. decisão, definição ou acordo (prazo, número, nome específico);
3. demonstração ou apresentação de tela ("olha aqui", "esse é o…", "essa tela…");
4. pergunta-chave que orientou o resto;
5. menção a nome próprio importante (pessoa, task, MR, cliente, valor).
Evite saudações, despedidas, filler e pausas; espace os momentos (não dois na mesma cena).
Para cada momento: `t` = o MM:SS da linha convertido em segundos (float), `label` curta em
PT-BR dizendo o que ESTÁ ACONTECENDO (não parafraseie a fala), `quote` = o texto da linha.

TRANSCRIÇÃO:
{lines}"""

# Frame "vazio" = uniforme (preto, corte, tela carregando). A métrica é o desvio-padrão do
# ImageMagick (escala 0–65535): preto=0, slide branco com texto≈8000, screenshot≈6500.
# Entropia NÃO serve (slide com texto dá 0,05 e seria descartado).
STDDEV_GOOD = 655.0  # ≥1 %: aceita na hora
STDDEV_MIN = 200.0  # entre isso e GOOD: só se nenhuma tentativa for melhor
JPG_MIN_BYTES = 20 * 1024  # fallback sem ImageMagick (JPEG uniforme em 720p ≈ 3 KB)
RETRY_OFFSETS = (5.0, 10.0, -5.0)


@dataclass
class Moment:
    t: float
    label: str
    quote: str
    image: str | None = None


def fmt_ts(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def transcript_lines(segments: list[dict]) -> str:
    return "\n".join(
        f"[{fmt_ts(float(s.get('start', 0)))}] {str(s.get('text', '')).strip()}"
        for s in segments
        if str(s.get("text", "")).strip()
    )


def write_transcripts(video: str, text: str, segments: list[dict]) -> tuple[str, str]:
    stem = os.path.splitext(video)[0]
    txt, js = f"{stem}.transcript.txt", f"{stem}.transcript.json"
    with open(txt, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    with open(js, "w", encoding="utf-8") as f:
        json.dump({"segments": segments}, f, ensure_ascii=False, indent=1)
    return txt, js


def parse_moments(payload: dict | None, duration: float) -> list[Moment]:
    """Valida a saída estruturada: t dentro do vídeo, sem duplicatas, ordem cronológica."""
    out: list[Moment] = []
    seen: set[int] = set()
    raw = (payload or {}).get("moments") if isinstance(payload, dict) else None
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        try:
            t = float(m.get("t"))
        except (TypeError, ValueError):
            continue
        if not (0 <= t <= max(duration, 0.5)) or int(t) in seen:
            continue
        seen.add(int(t))
        out.append(Moment(t, str(m.get("label", "")).strip()[:60], str(m.get("quote", "")).strip()))
    return sorted(out, key=lambda x: x.t)


# ---- subprocessos ----


async def _run(*cmd: str, timeout: float = 120, cwd: str | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "CLAUDE_BOT_SKIP_HOOKS": "1"},
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def probe(video: str) -> tuple[float, int]:
    """(duração em segundos, nº de trilhas de áudio)."""
    _, out, _ = await _run(
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video
    )
    duration = float(out.strip() or 0)
    _, out, _ = await _run(
        "ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
        "-of", "csv=p=0", video,
    )  # fmt: skip
    return duration, len([ln for ln in out.splitlines() if ln.strip()])


async def extract_audio(video: str, wav: str, audio_tracks: int) -> None:
    """16 kHz mono; várias trilhas (gravação de call) são mixadas."""
    if audio_tracks > 1:
        args = ["-filter_complex", f"amix=inputs={audio_tracks}:duration=longest:normalize=0"]
    else:
        args = ["-vn"]
    rc, _, err = await _run(
        "ffmpeg", "-y", "-loglevel", "error", "-i", video, *args, "-ac", "1", "-ar", "16000", wav,
        timeout=600,
    )  # fmt: skip
    if rc != 0:
        raise RuntimeError(f"ffmpeg (áudio) exit {rc}: {err[-300:]}")


async def frame_score(path: str) -> float:
    """Quanto conteúdo visual o frame tem: desvio-padrão (ImageMagick) ou, sem ele, um
    proxy pelo tamanho do JPEG. 0 = uniforme/inexistente."""
    if not os.path.isfile(path):
        return 0.0
    if shutil.which("identify"):
        rc, out, _ = await _run("identify", "-format", "%[standard-deviation]", path, timeout=30)
        with contextlib.suppress(ValueError):
            if rc == 0:
                return float(out.strip())
    size = os.path.getsize(path)
    return 0.0 if size < JPG_MIN_BYTES else STDDEV_GOOD


async def _grab(video: str, t: float, out: str) -> float:
    rc, _, _ = await _run(
        "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(t, 0):.3f}", "-i", video,
        "-frames:v", "1", "-q:v", "2", out, timeout=60,
    )  # fmt: skip
    return await frame_score(out) if rc == 0 else 0.0


async def extract_frames(video: str, moments: list[Moment], duration: float) -> list[Moment]:
    """1 jpg por momento, em paralelo. Frame uniforme → tenta t±offset; fica o melhor."""
    out_dir = f"{os.path.splitext(video)[0]}.moments"
    os.makedirs(out_dir, exist_ok=True)

    async def one(i: int, m: Moment) -> None:
        best_path, best_score = None, 0.0
        for off in (0.0, *RETRY_OFFSETS):
            t = m.t + off
            if not (0 <= t <= duration):
                continue
            path = os.path.join(out_dir, f"moment_{i:02d}_{fmt_ts(t).replace(':', '')}.jpg")
            score = await _grab(video, t, path)
            if score >= STDDEV_GOOD:
                best_path, best_score = path, score
                break
            if score > best_score:
                if best_path:
                    with contextlib.suppress(OSError):
                        os.remove(best_path)
                best_path, best_score = path, score
            else:
                with contextlib.suppress(OSError):
                    os.remove(path)
        if best_path and best_score >= STDDEV_MIN:
            m.image = best_path
        elif best_path:
            with contextlib.suppress(OSError):
                os.remove(best_path)

    await asyncio.gather(*(one(i, m) for i, m in enumerate(moments, 1)))
    return moments


SCENE_THRESHOLD = 0.3  # sensibilidade do detector de corte do ffmpeg (0–1)
_PTS_RE = re.compile(r"pts_time:([0-9.]+)")


def parse_scene_times(showinfo_stderr: str) -> list[float]:
    return sorted({round(float(t), 3) for t in _PTS_RE.findall(showinfo_stderr)})


def spread_moments(times: list[float], duration: float, max_moments: int) -> list[Moment]:
    """Sem fala: cortes de cena viram momentos; poucos cortes → amostragem uniforme."""
    times = [t for t in times if 0.5 <= t <= duration]
    if len(times) < 2:
        n = max(1, min(max_moments, int(duration // 10) or 1))
        times = [round(duration * (i + 0.5) / n, 3) for i in range(n)]
    elif len(times) > max_moments:
        step = len(times) / max_moments
        times = [times[int(i * step)] for i in range(max_moments)]
    return [Moment(t, f"cena {i}", "") for i, t in enumerate(times, 1)]


async def scene_moments(video: str, duration: float, max_moments: int) -> list[Moment]:
    _, _, err = await _run(
        "ffmpeg", "-loglevel", "info", "-i", video,
        "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-",
        timeout=600,
    )  # fmt: skip
    return spread_moments(parse_scene_times(err), duration, max_moments)


async def select_moments(
    claude_bin: str, segments: list[dict], duration: float, *, max_moments: int, cwd: str
) -> list[Moment]:
    """Chamada curta ao Claude, sem ferramentas, com saída estruturada."""
    prompt = SELECT_PROMPT.format(
        duration=fmt_ts(duration), max_moments=max_moments, lines=transcript_lines(segments)
    )
    # cwd neutro (pasta do vídeo): não carrega o CLAUDE.md do projeto numa chamada que só
    # precisa da transcrição. `--disallowedTools "*"` NÃO serve: a saída estruturada do
    # `--json-schema` é uma tool interna e seria negada junto — lista explícita.
    rc, out, err = await _run(
        claude_bin, "-p", "--output-format", "json", "--json-schema", MOMENTS_SCHEMA,
        "--permission-mode", "dontAsk",
        "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Agent",
        "--", prompt,
        timeout=300, cwd=cwd,
    )  # fmt: skip
    if rc != 0:
        raise RuntimeError(f"claude (momentos) exit {rc}: {err[-300:]}")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"claude (momentos) não devolveu JSON: {out[:200]}") from e
    return parse_moments(data.get("structured_output"), duration)


def digest_prompt(
    caption: str, video: str, duration: float, txt_path: str | None, moments: list[Moment]
) -> str:
    """`txt_path=None` = vídeo sem fala: o digest é só visual, pelos prints."""
    lines = [
        caption or "Faça o digest deste vídeo.",
        "",
        f"[Vídeo enviado pelo usuário: {video} — duração {fmt_ts(duration)}]",
    ]
    if txt_path:
        lines += [
            f"Transcrição completa do áudio (leia com Read): {txt_path}",
            "Momentos-chave já escolhidos e com print extraído (abra cada .jpg com Read):",
        ]
    else:
        lines += [
            "O vídeo NÃO tem fala (sem trilha de áudio ou sem voz). Os prints abaixo foram tirados "
            "nos cortes de cena, em ordem; abra cada .jpg com Read:",
        ]
    for i, m in enumerate(moments, 1):
        img = m.image or "(sem print útil neste instante)"
        quote = f' — "{m.quote}"' if m.quote else ""
        lines.append(f"{i}. [{fmt_ts(m.t)}] {m.label}{quote} → {img}")
    if txt_path:
        lines += [
            "",
            "Responda com: (a) resumo do vídeo em 3-6 frases (pauta, decisões, próximos passos), "
            "com base na transcrição inteira; (b) uma seção por momento, em ordem cronológica, no "
            "formato `### [MM:SS] label` + 1-2 frases descrevendo o que aparece no print "
            "(aplicação, elementos, textos legíveis, quem apresenta) + a quote em blockquote.",
        ]
    else:
        lines += [
            "",
            "Responda com: (a) o que o vídeo mostra, em 3-6 frases, reconstruindo a sequência de "
            "ações/telas a partir dos prints; (b) uma seção por print, em ordem, no formato "
            "`### [MM:SS] <título curto que você der>` + 1-2 frases do que aparece (aplicação, "
            "elementos, textos legíveis).",
        ]
    lines.append(
        "Cite o caminho absoluto de cada .jpg usado — o bot envia as imagens automaticamente. "
        "Não invente nada que não esteja na transcrição ou nas imagens."
    )
    return "\n".join(lines)
