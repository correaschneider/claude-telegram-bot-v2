"""Persistência chat_id → session_id do Claude Code (JSON atômico, com TTL)."""

from __future__ import annotations

import json
import os
import tempfile
import time


class SessionStore:
    def __init__(self, path: str, ttl_seconds: int) -> None:
        self._path = path
        self._ttl = ttl_seconds
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        try:
            with open(self._path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        d = os.path.dirname(self._path) or "."
        fd, tmp = tempfile.mkstemp(prefix=".sessions-", dir=d)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self._path)

    def get(self, chat_id: int) -> str | None:
        entry = self._data.get(str(chat_id))
        if not entry:
            return None
        if time.time() - entry.get("updated_at", 0) > self._ttl:
            return None
        return entry.get("session_id")

    def set(self, chat_id: int, session_id: str) -> None:
        self._data[str(chat_id)] = {"session_id": session_id, "updated_at": time.time()}
        self._save()

    def clear(self, chat_id: int) -> None:
        if self._data.pop(str(chat_id), None) is not None:
            self._save()
