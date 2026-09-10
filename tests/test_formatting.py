"""Markdown → HTML/Rich, chunking, checklist, tokens."""

from __future__ import annotations

from tgclaude.core.formatting import (
    checklist_markdown,
    chunk_markdown,
    md_to_html,
    rich_markdown,
)
from tgclaude.core.turn import TurnOutcome


def test_md_to_html_and_chunks():
    out = md_to_html("## Título\ntexto **forte** com `a<b`\n```py\nx = 1 < 2\n```\nfim & cabo")
    assert "<b>Título</b>" in out and "<b>forte</b>" in out
    assert (
        "<code>a&lt;b</code>" in out
        and "<pre>x = 1 &lt; 2</pre>" in out
        and "fim &amp; cabo" in out
    )
    md = "intro\n```\n" + "\n".join(f"linha {i}" for i in range(40)) + "\n```\nfim"
    chunks = chunk_markdown(md, size=120)
    assert len(chunks) > 1 and all(c.count("```") % 2 == 0 for c in chunks)


def test_rich_markdown_and_checklist():
    o = TurnOutcome("resposta", "passo 1\n\npasso 2", 2, "⏱ 3s · 0.10 USD", "s", False, False, 3.0)
    rich = rich_markdown(o)
    assert rich.startswith("<details><summary>🔎 Progresso (2 ferramentas)</summary>")
    assert "\n\nresposta\n\n_⏱ 3s · 0.10 USD_" in rich
    assert rich_markdown(TurnOutcome("só", "", 0, "", None, False, False, 1)) == "só"
    md = checklist_markdown("Plano", [{"text": "a", "done": True}, {"text": "b\nc"}])
    assert md == "**Plano** · 1/2\n- [x] a\n- [ ] b c"
