"""Log de decisões de permissão (`.decisions.jsonl`) e o agregado do comando /audit —
é o que transforma "ajustar as listas conforme o uso" em dado em vez de chute."""

from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass

log = logging.getLogger("claude-bot")

MAX_BYTES = 5 * 1024 * 1024  # rotação simples: acima disso mantém só a metade mais recente


def record(
    path: str,
    *,
    chat_id: int,
    project: str,
    tool: str,
    detail: str,
    rule: str,
    verdict: str,  # allow | deny | prompt
    outcome: str,  # auto | allow | always | deny | cancel | denied-no-prompter
    matched: str | None,
    dangerous: bool,
) -> None:
    entry = {
        "ts": round(time.time(), 1),
        "chat": chat_id,
        "project": project,
        "tool": tool,
        "detail": detail[:300],
        "rule": rule,
        "verdict": verdict,
        "outcome": outcome,
        "matched": matched,
        "dangerous": dangerous,
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if os.path.getsize(path) > MAX_BYTES:
            _rotate(path)
    except OSError as e:
        log.warning("não gravou decisão: %s", e)


def _rotate(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines[len(lines) // 2 :])


def load(path: str, *, project: str | None = None, limit: int = 200) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if project and e.get("project") != project:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return list(reversed(out))


@dataclass
class Candidate:
    rule: str
    approved: int
    denied: int
    dangerous: bool
    example: str


@dataclass
class Summary:
    total: int
    candidates: list[Candidate]  # perguntadas e aprovadas à mão, sem ser sensível
    sensitive_approved: Counter
    denied: Counter
    auto: Counter


def summarize(entries: list[dict]) -> Summary:
    approved: Counter = Counter()
    denied: Counter = Counter()
    sens: Counter = Counter()
    auto: Counter = Counter()
    example: dict[str, str] = {}
    danger: dict[str, bool] = defaultdict(bool)
    for e in entries:
        rule, out = e.get("rule", "?"), e.get("outcome")
        example.setdefault(rule, e.get("detail", ""))
        if e.get("dangerous"):
            danger[rule] = True
        if out == "auto":
            auto[e.get("matched") or rule] += 1
        elif out in ("allow", "always"):
            (sens if e.get("dangerous") else approved)[rule] += 1
        elif out in ("deny", "denied-no-prompter"):
            denied[rule] += 1
    candidates = [
        Candidate(rule, n, denied.get(rule, 0), danger[rule], example.get(rule, ""))
        for rule, n in approved.most_common()
        if n >= 2 and denied.get(rule, 0) == 0
    ]
    return Summary(len(entries), candidates, sens, denied, auto)


def render(summary: Summary, project: str) -> str:
    if not summary.total:
        return f"Sem decisões registradas ainda para {project}."
    lines = [f"🔎 Últimas {summary.total} decisões · projeto {project}"]
    if summary.candidates:
        lines.append("\n✅ Perguntadas e sempre aprovadas → candidatas a regra do projeto:")
        for i, c in enumerate(summary.candidates[:8], 1):
            lines.append(f"  {i}. {c.rule} — {c.approved}×  (ex.: {c.example[:50]})")
    if summary.sensitive_approved:
        lines.append("\n⚠️ Sensíveis aprovadas à mão (continuam perguntando):")
        lines += [f"  • {r} — {n}×" for r, n in summary.sensitive_approved.most_common(5)]
    if summary.denied:
        lines.append("\n❌ Negadas:")
        lines += [f"  • {r} — {n}×" for r, n in summary.denied.most_common(5)]
    if summary.auto:
        lines.append("\n🤖 Auto-aprovadas por regra (top):")
        lines += [f"  • {r} — {n}×" for r, n in summary.auto.most_common(5)]
    return "\n".join(lines)
