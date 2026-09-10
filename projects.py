"""Registry de projetos (`projects.json`, mesmo formato do bot v1):

    { "alias": { "path": "/dir", "allowed_tools": "...", "permission_mode": "...",
                 "add_dirs": [...], "default": true, "tasks": ["HT", "HAB"], "chats": [-100…] } }

`tasks` = prefixos de task (deep link `t:HT-123`) que caem neste projeto;
`chats` = ids de grupos que conversam neste projeto por padrão."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Project:
    alias: str
    path: str
    allowed_tools: str = ""
    permission_mode: str = ""
    add_dirs: tuple[str, ...] = ()
    tasks: tuple[str, ...] = ()
    chats: frozenset[int] = field(default_factory=frozenset)


class ProjectRegistry:
    def __init__(self, projects: dict[str, Project], default_alias: str) -> None:
        self._projects = projects
        self._default = default_alias

    @classmethod
    def load(cls, path: str, fallback_workspace: str) -> ProjectRegistry:
        try:
            with open(path, encoding="utf-8") as f:
                raw: dict[str, dict] = json.load(f)
        except FileNotFoundError:
            raw = {}
        projects: dict[str, Project] = {}
        default = ""
        for alias, spec in raw.items():
            projects[alias] = Project(
                alias=alias,
                path=spec["path"],
                allowed_tools=spec.get("allowed_tools", ""),
                permission_mode=spec.get("permission_mode", ""),
                add_dirs=tuple(spec.get("add_dirs", ())),
                tasks=tuple(p.upper() for p in spec.get("tasks", ())),
                chats=frozenset(spec.get("chats", ())),
            )
            if spec.get("default"):
                default = alias
        if not projects:
            projects["main"] = Project(alias="main", path=fallback_workspace)
        return cls(projects, default or next(iter(projects)))

    @property
    def default(self) -> Project:
        return self._projects[self._default]

    def get(self, alias: str) -> Project | None:
        return self._projects.get(alias)

    def get_or_default(self, alias: str | None) -> Project:
        return (self._projects.get(alias) if alias else None) or self.default

    def all(self) -> list[Project]:
        return list(self._projects.values())

    def for_task(self, task_key: str) -> Project | None:
        prefix = task_key.split("-", 1)[0].upper()
        return next((p for p in self._projects.values() if prefix in p.tasks), None)

    def for_chat(self, chat_id: int) -> Project | None:
        return next((p for p in self._projects.values() if chat_id in p.chats), None)
