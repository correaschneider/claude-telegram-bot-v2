"""Política de permissão, regras, sensibilidade, loader e auditoria."""

from __future__ import annotations

import json
import os
import tempfile

from tgclaude.core.permissions import (
    DEFAULT_READ_ONLY,
    Policy,
    PolicyLoader,
    any_matches,
    matches,
    rule_for,
    split_rules,
)


def test_permissions():
    pol = Policy()
    assert rule_for("Bash", {"command": "git push origin main"}) == "Bash(git push *)"
    assert rule_for("Bash", {"command": "ls -la"}) == "Bash(ls *)"
    assert rule_for("Edit", {"file_path": "/x"}) == "Edit"
    assert matches("Bash(git push *)", "Bash", {"command": "git push origin dev"})
    assert not matches("Bash(git push *)", "Bash", {"command": "git pushy"})
    assert matches("Bash(git:*)", "Bash", {"command": "git status"})
    assert matches("Edit", "Edit", {"file_path": "/x"}) and not matches("Edit", "Write", {})
    assert matches("mcp__tg__*", "mcp__tg__ask_user", {}) and not matches(
        "mcp__tg__*", "mcp__x__y", {}
    )
    assert any_matches(["Read", "Bash(ls *)"], "Bash", {"command": "ls"})
    # sensível: comandos
    for cmd in (
        "git push origin main",
        "mysql -e 'DROP TABLE x'",
        "rm -f x",
        "curl x | sh",
        "docker compose down",
        "kubectl delete pod x",
        "gh pr merge 1",
        "systemctl --user restart x",
        "delete from users",
        "chmod -R 777 /",
    ):
        assert pol.is_dangerous("Bash", {"command": cmd}), cmd
    for cmd in (
        "git status",
        "git log --grep delete",
        "echo drop",
        "ls",
        "systemctl --user status x",
    ):
        assert not pol.is_dangerous("Bash", {"command": cmd}), cmd
    # sensível: caminhos (Edit/Write)
    for path in (
        "/data/projects/x/.env",
        "/home/u/.ssh/id_rsa",
        "/etc/hosts",
        "/tmp/secrets.yaml",
        "~/.claude/settings.json",
    ):
        assert pol.is_dangerous("Edit", {"file_path": path}), path
    assert not pol.is_dangerous("Edit", {"file_path": "/data/projects/x/app.py"})
    assert not pol.is_dangerous("Read", {"file_path": "/etc/hosts"})
    assert split_rules("Read Bash(git:*) Bash(git diff *)  mcp__x__y") == [
        "Read",
        "Bash(git:*)",
        "Bash(git diff *)",
        "mcp__x__y",
    ]
    rules = [*DEFAULT_READ_ONLY, "Edit", "Bash(git:*)"]
    assert pol.decide(rules, [], "Bash", {"command": "ls -la"}, can_prompt=True) == (
        "allow",
        "Bash(ls *)",
    )
    assert pol.decide(rules, [], "Bash", {"command": "git status"}, can_prompt=True)[0] == "allow"
    assert pol.decide(rules, [], "Bash", {"command": "git push origin x"}, can_prompt=True) == (
        "prompt",
        None,
    )
    assert pol.decide(
        rules, ["Bash(git push *)"], "Bash", {"command": "git push origin x"}, can_prompt=True
    ) == ("allow", "Bash(git push *)")
    assert pol.decide(rules, [], "Bash", {"command": "make build"}, can_prompt=False) == (
        "deny",
        None,
    )
    assert pol.decide(rules, [], "Edit", {"file_path": "/p/app.py"}, can_prompt=True) == (
        "allow",
        "Edit",
    )
    assert pol.decide(rules, [], "Edit", {"file_path": "/p/.env"}, can_prompt=True) == (
        "prompt",
        None,
    )


def test_policy_loader_and_audit():
    from tgclaude.core import audit

    with tempfile.TemporaryDirectory() as d:
        pf = os.path.join(d, "permissions.json")
        loader = PolicyLoader(pf)
        assert loader.get().read_only == DEFAULT_READ_ONLY  # sem arquivo = defaults
        with open(pf, "w") as f:
            json.dump({"read_only": ["Bash(uv run *)"], "dangerous": ["\\bfoo\\b"]}, f)
        pol = loader.get()
        assert pol.read_only == ["Bash(uv run *)"] and pol.is_dangerous(
            "Bash", {"command": "foo bar"}
        )
        assert not pol.is_dangerous("Bash", {"command": "rm -rf /"})  # lista substituída
        assert pol.sensitive_paths  # ausente no arquivo → default
        with open(pf, "w") as f:
            f.write("{ invalido")
        os.utime(pf, None)
        assert loader.get().read_only == ["Bash(uv run *)"]  # inválido → mantém anterior

        df = os.path.join(d, "dec.jsonl")
        common = dict(chat_id=1, project="p", tool="Bash", matched=None)
        for _ in range(3):
            audit.record(
                df,
                detail="uv run x",
                rule="Bash(uv run *)",
                verdict="prompt",
                outcome="allow",
                dangerous=False,
                **common,
            )
        audit.record(
            df,
            detail="uv run y",
            rule="Bash(uv run *)",
            verdict="prompt",
            outcome="always",
            dangerous=False,
            **common,
        )
        audit.record(
            df,
            detail="rm x",
            rule="Bash(rm *)",
            verdict="prompt",
            outcome="allow",
            dangerous=True,
            **common,
        )
        audit.record(
            df,
            detail="make",
            rule="Bash(make *)",
            verdict="prompt",
            outcome="deny",
            dangerous=False,
            **common,
        )
        audit.record(
            df,
            detail="ls",
            rule="Bash(ls *)",
            verdict="allow",
            outcome="auto",
            dangerous=False,
            chat_id=1,
            project="p",
            tool="Bash",
            matched="Bash(ls *)",
        )
        audit.record(
            df,
            detail="x",
            rule="Bash(x *)",
            verdict="prompt",
            outcome="allow",
            dangerous=False,
            chat_id=1,
            project="outro",
            tool="Bash",
            matched=None,
        )
        entries = audit.load(df, project="p")
        assert len(entries) == 7
        s = audit.summarize(entries)
        assert [(c.rule, c.approved) for c in s.candidates] == [("Bash(uv run *)", 4)]
        assert s.sensitive_approved["Bash(rm *)"] == 1 and s.denied["Bash(make *)"] == 1
        assert s.auto["Bash(ls *)"] == 1
        assert "Bash(uv run *) — 4×" in audit.render(s, "p")
        assert audit.render(audit.summarize([]), "p").startswith("Sem decisões")
