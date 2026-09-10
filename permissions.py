"""Regras de permissão: detecção de comando sensível, regra "sempre nesta sessão" e
casamento de regras já aprovadas (mesma sintaxe do --allowedTools do Claude Code)."""

from __future__ import annotations

import json
import re

DANGER = re.compile(
    r"\b(rm|rmdir|drop|truncate|delete|migrate|deploy|push|reset\s+--hard|kill|pkill|"
    r"terraform\s+(apply|destroy)|sudo|shutdown|reboot|mysql|psql|redis-cli|ssh)\b",
    re.I,
)

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
    "make",
}


# Auto-aprovados sem perguntar (o Claude Code não expõe a lista read-only dele quando
# a regra `ask` força o prompt, então mantemos a nossa).
READ_ONLY_RULES = [
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
    "Bash(git status *)",
    "Bash(git log *)",
    "Bash(git diff *)",
    "Bash(git show *)",
    "Bash(git branch *)",
    "Bash(git remote *)",
    "Bash(docker ps *)",
    "Bash(docker compose ps *)",
]

_RULE = re.compile(r"\S+?\([^)]*\)|\S+")


def split_rules(allowed_tools: str) -> list[str]:
    """'Read Bash(git:*) Bash(git diff *)' → ['Read', 'Bash(git:*)', 'Bash(git diff *)']."""
    return _RULE.findall(allowed_tools or "")


def decide(
    rules: list[str],
    explicit: list[str],
    tool_name: str,
    tool_input: dict,
    *,
    can_prompt: bool,
) -> str:
    """'allow' | 'deny' | 'prompt'. Regras explícitas do usuário ("sempre") valem até pra
    comando sensível; as demais (read-only + projeto) não cobrem comando sensível."""
    if any_matches(explicit, tool_name, tool_input):
        return "allow"
    if not is_dangerous(tool_name, tool_input) and any_matches(rules, tool_name, tool_input):
        return "allow"
    return "prompt" if can_prompt else "deny"


def describe(tool_name: str, tool_input: dict) -> str:
    """Texto curto e legível do que está sendo pedido."""
    if tool_name == "Bash":
        return str(tool_input.get("command", "")).strip()
    for key in ("file_path", "path", "pattern", "url", "query", "prompt"):
        if key in tool_input:
            return f"{key}={tool_input[key]}"
    text = json.dumps(tool_input, ensure_ascii=False)
    return text[:600] + ("…" if len(text) > 600 else "")


def is_dangerous(tool_name: str, tool_input: dict) -> bool:
    if tool_name == "Bash":
        return bool(DANGER.search(str(tool_input.get("command", ""))))
    return False


def rule_for(tool_name: str, tool_input: dict) -> str:
    """Regra que cobre esta chamada e as parecidas, na sintaxe do --allowedTools."""
    if tool_name != "Bash":
        return tool_name
    words = str(tool_input.get("command", "")).strip().split()
    if not words:
        return "Bash"
    n = 2 if words[0] in TWO_WORD_PREFIX and len(words) > 1 else 1
    return f"Bash({' '.join(words[:n])} *)"


def matches(rule: str, tool_name: str, tool_input: dict) -> bool:
    m = re.fullmatch(r"(\w+)(?:\((.*)\))?", rule)
    if not m or m.group(1) != tool_name:
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


def any_matches(rules: list[str], tool_name: str, tool_input: dict) -> bool:
    return any(matches(r, tool_name, tool_input) for r in rules)
