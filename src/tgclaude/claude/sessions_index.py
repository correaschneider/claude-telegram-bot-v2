"""Índice das sessões do Claude Code em disco, para o comando /sessions.

Cada sessão é `<config_dir>/projects/<cwd-encodado>/<session_id>.jsonl`; o Claude Code
encoda o cwd trocando todo caractere não-alfanumérico por `-`. Título: `custom-title`
(customTitle) > `summary` > 1ª mensagem do usuário. Módulo puro, sem Telegram."""

from __future__ import annotations

import glob
import json
import os
import re


def config_projects_dir() -> str:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(base, "projects")


def encode_cwd(path: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def _resolve_title(jsonl_path: str, max_bytes: int = 3_000_000) -> str:
    custom = summary = first_user = None
    read = 0
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                read += len(line)
                if read > max_bytes:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = m.get("type")
                if t == "custom-title":
                    custom = m.get("customTitle") or m.get("title")
                    if custom:
                        break
                elif t == "summary" and not summary:
                    summary = m.get("summary")
                elif first_user is None and m.get("message", {}).get("role") == "user":
                    c = m["message"].get("content")
                    if isinstance(c, str):
                        first_user = c
                    elif isinstance(c, list):
                        first_user = " ".join(
                            x.get("text", "")
                            for x in c
                            if isinstance(x, dict) and x.get("type") == "text"
                        )
    except OSError:
        return "(sem título)"
    title = custom or summary or (first_user or "").strip() or "(sem título)"
    return " ".join(title.split())[:80]


def list_recent_sessions(alias_to_cwd: dict[str, str], limit: int = 6) -> list[dict]:
    """`[{alias, cwd, session_id, path, mtime, title}]` ordenado por mtime desc."""
    base = config_projects_dir()
    candidates: list[dict] = []
    seen: set[str] = set()
    for alias, cwd in alias_to_cwd.items():
        d = os.path.join(base, encode_cwd(cwd.rstrip("/")))
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "*.jsonl")):
            sid = os.path.splitext(os.path.basename(path))[0]
            if sid in seen:
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            seen.add(sid)
            candidates.append(
                {"alias": alias, "cwd": cwd, "session_id": sid, "path": path, "mtime": mtime}
            )
    candidates.sort(key=lambda c: c["mtime"], reverse=True)
    top = candidates[:limit]
    for c in top:
        c["title"] = _resolve_title(c["path"])
    return top
