"""Política de permissão: listas read-only / sensível / caminhos sensíveis (de
`permissions.json`, com defaults embutidos), regra "sempre nesta sessão" e casamento de
regras na sintaxe do --allowedTools do Claude Code."""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger("claude-bot")

# Auto-aprovados sem perguntar (o Claude Code não expõe a lista read-only dele quando a
# regra `ask` força o prompt, então mantemos a nossa).
DEFAULT_READ_ONLY = [
    "Bash(ls *)",
    "Bash(cat *)",
    "Bash(echo *)",
    "Bash(pwd *)",
    "Bash(head *)",
    "Bash(tail *)",
    "Bash(wc *)",
    "Bash(grep *)",
    "Bash(rg *)",
    "Bash(find *)",
    "Bash(which *)",
    "Bash(date *)",
    "Bash(whoami *)",
    "Bash(uname *)",
    "Bash(df *)",
    "Bash(du *)",
    "Bash(stat *)",
    "Bash(file *)",
    "Bash(env *)",
    "Bash(git status *)",
    "Bash(git log *)",
    "Bash(git diff *)",
    "Bash(git show *)",
    "Bash(git branch *)",
    "Bash(git remote *)",
    "Bash(git fetch *)",
    "Bash(docker ps *)",
    "Bash(docker compose ps *)",
    "Bash(docker logs *)",
    "Bash(systemctl --user status *)",
    "Bash(journalctl *)",
]

# Regexes (case-insensitive) sobre o comando Bash. Sensível = sempre pergunta, mesmo que
# uma regra read-only/projeto case — só "sempre nesta sessão" passa por cima.
DEFAULT_DANGEROUS = [
    r"\brm\b",
    r"\brmdir\b",
    r"\bdd\b",
    r"\bmkfs\b",
    r"\bchmod\b.*\s-R\b|\bchmod\s+-R\b",
    r"\bchown\b.*\s-R\b|\bchown\s+-R\b",
    r"\b(curl|wget)\b.*\|\s*(ba|z)?sh\b",
    r"\bsudo\b",
    r"\bkill\b|\bpkill\b|\bkillall\b",
    r"\bshutdown\b|\breboot\b",
    r"\bsystemctl\b.*\b(stop|disable|restart|mask)\b",
    r"\bcrontab\s+-r\b",
    r"\bgit\b.*\b(push|reset\s+--hard|clean\b|branch\s+-D|checkout\s+\.|restore\s+\.)",
    r"\bgh\s+pr\s+merge\b|\bglab\s+mr\s+merge\b",
    r"\bdocker\b.*\b(rm|rmi|prune|compose\s+down|stop|kill)\b",
    r"\bkubectl\b.*\b(delete|apply|drain)\b",
    r"\bterraform\s+(apply|destroy)\b",
    r"\boci\b.*\b(terminate|delete|update)\b",
    r"\bdrop\s+(table|database|schema|index)\b",
    r"\btruncate\b",
    r"\bdelete\s+from\b",
    r"\b(mysql|psql|redis-cli|mongosh)\b",
    r"\bssh\b|\bscp\b|\brsync\b",
    r"\bdeploy\b|\bmigrate\b",
]

# Edit/Write nestes caminhos sempre perguntam (fnmatch sobre o caminho absoluto; `*` casa `/`).
DEFAULT_SENSITIVE_PATHS = [
    "*/.env",
    "*/.env.*",
    "*/.ssh/*",
    "*/.gnupg/*",
    "*/.aws/*",
    "*/.kube/*",
    "*/.claude/settings*.json",
    "*/.claude.json",
    "*/projects.json",
    "*/permissions.json",
    "*.pem",
    "*.key",
    "*/id_rsa*",
    "*/id_ed25519*",
    "*secret*",
    "*credential*",
    "/etc/*",
    "*/.git/hooks/*",
    "*/.git/config",
]

FILE_TOOLS = {"Edit", "Write", "NotebookEdit"}

# Comandos cujo "sempre" faz sentido no 2º token (git push ≠ git status).
TWO_WORD_PREFIX = {
    "git",
    "docker",
    "npm",
    "pnpm",
    "yarn",
    "uv",
    "gh",
    "glab",
    "php",
    "python",
    "python3",
    "make",
    "systemctl",
    "kubectl",
    "terraform",
}

_RULE = re.compile(r"\S+?\([^)]*\)|\S+")


@dataclass
class Policy:
    read_only: list[str] = field(default_factory=lambda: list(DEFAULT_READ_ONLY))
    dangerous: list[str] = field(default_factory=lambda: list(DEFAULT_DANGEROUS))
    sensitive_paths: list[str] = field(default_factory=lambda: list(DEFAULT_SENSITIVE_PATHS))

    def __post_init__(self) -> None:
        self._danger_re = [re.compile(p, re.I) for p in self.dangerous]

    def is_dangerous(self, tool_name: str, tool_input: dict) -> bool:
        if tool_name == "Bash":
            cmd = str(tool_input.get("command", ""))
            return any(r.search(cmd) for r in self._danger_re)
        if tool_name in FILE_TOOLS:
            path = os.path.abspath(os.path.expanduser(str(tool_input.get("file_path", ""))))
            return any(fnmatch.fnmatch(path, pat) for pat in self.sensitive_paths)
        return False

    def decide(
        self,
        rules: list[str],
        explicit: list[str],
        tool_name: str,
        tool_input: dict,
        *,
        can_prompt: bool,
    ) -> tuple[str, str | None]:
        """('allow'|'deny'|'prompt', regra que casou). Regras explícitas do usuário ("sempre")
        valem até pra sensível; as demais (read-only + projeto) não cobrem sensível."""
        hit = first_match(explicit, tool_name, tool_input)
        if hit:
            return "allow", hit
        if not self.is_dangerous(tool_name, tool_input):
            hit = first_match(rules, tool_name, tool_input)
            if hit:
                return "allow", hit
        return ("prompt" if can_prompt else "deny"), None

    def to_json(self) -> dict:
        return {
            "read_only": self.read_only,
            "dangerous": self.dangerous,
            "sensitive_paths": self.sensitive_paths,
        }


class PolicyLoader:
    """Recarrega `permissions.json` quando o mtime muda; sem arquivo/JSON inválido = defaults."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._mtime: float | None = None
        self._policy = Policy()

    @property
    def path(self) -> str:
        return self._path

    def get(self) -> Policy:
        try:
            mtime = os.path.getmtime(self._path)
        except OSError:
            if self._mtime is not None:
                self._policy, self._mtime = Policy(), None
            return self._policy
        if mtime != self._mtime:
            try:
                with open(self._path, encoding="utf-8") as f:
                    raw = json.load(f)
                self._policy = Policy(
                    read_only=list(raw.get("read_only") or DEFAULT_READ_ONLY),
                    dangerous=list(raw.get("dangerous") or DEFAULT_DANGEROUS),
                    sensitive_paths=list(raw.get("sensitive_paths") or DEFAULT_SENSITIVE_PATHS),
                )
                log.info(
                    "permissions.json recarregado (%d read-only, %d sensíveis)",
                    len(self._policy.read_only),
                    len(self._policy.dangerous),
                )
            except (OSError, ValueError, re.error) as e:
                log.warning("permissions.json inválido (%s); mantendo política anterior", e)
            self._mtime = mtime
        return self._policy


def describe(tool_name: str, tool_input: dict) -> str:
    """Texto curto e legível do que está sendo pedido."""
    if tool_name == "Bash":
        return str(tool_input.get("command", "")).strip()
    for key in ("file_path", "path", "pattern", "url", "query", "prompt"):
        if key in tool_input:
            return f"{key}={tool_input[key]}"
    text = json.dumps(tool_input, ensure_ascii=False)
    return text[:600] + ("…" if len(text) > 600 else "")


def rule_for(tool_name: str, tool_input: dict) -> str:
    """Regra que cobre esta chamada e as parecidas, na sintaxe do --allowedTools."""
    if tool_name != "Bash":
        return tool_name
    words = str(tool_input.get("command", "")).strip().split()
    if not words:
        return "Bash"
    n = 2 if words[0] in TWO_WORD_PREFIX and len(words) > 1 else 1
    return f"Bash({' '.join(words[:n])} *)"


def split_rules(allowed_tools: str) -> list[str]:
    """'Read Bash(git:*) Bash(git diff *)' → ['Read', 'Bash(git:*)', 'Bash(git diff *)']."""
    return _RULE.findall(allowed_tools or "")


def matches(rule: str, tool_name: str, tool_input: dict) -> bool:
    m = re.fullmatch(r"([\w*]+)(?:\((.*)\))?", rule)
    if not m:
        return False
    name = m.group(1)
    if name.endswith("*"):
        if not tool_name.startswith(name[:-1]):
            return False
    elif name != tool_name:
        return False
    spec = m.group(2)
    if spec is None:
        return True
    if tool_name != "Bash":
        return False
    command = str(tool_input.get("command", "")).strip()
    if spec.endswith(" *"):
        prefix = spec[:-2]
        return command == prefix or command.startswith(prefix + " ")
    if spec.endswith(":*"):
        return command.startswith(spec[:-2])
    return command == spec


def first_match(rules: list[str], tool_name: str, tool_input: dict) -> str | None:
    return next((r for r in rules if matches(r, tool_name, tool_input)), None)


def any_matches(rules: list[str], tool_name: str, tool_input: dict) -> bool:
    return first_match(rules, tool_name, tool_input) is not None
