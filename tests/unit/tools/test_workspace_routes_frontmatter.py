"""Comment preservation for the agent-frontmatter round-trip helpers.

``grimoire.tools.workspace_routes._load_agent_frontmatter`` /
``_dump_agent_frontmatter`` are the fourth write point audited alongside
``tools/_common.py`` while fixing grimoire-kit#430 (PR for #431 already
listed them as "already ruamel round-trip + preserve_quotes=True — verified
sound by reproduction", but that verification had no automated test). They
back ``_apply_agent_updates`` (the cockpit's "assign a skill" / "edit
fields" routes), so a silent comment loss here would strip comments from a
project's agent overrides on every skill/field edit — the same bug class as
#430, just on a different file.

These two helpers work purely on frontmatter text (no project on disk is
needed to exercise them), so this tests them directly rather than through
the full ``workspace_post`` route, which requires a real ``grimoire init``
project (see ``tests/unit/test_workspace_agents.py``) — disproportionate
machinery for what is, at heart, a YAML round-trip question answered
already for ``_common.py`` in ``tests/unit/tools/test_common.py``.
"""

from __future__ import annotations

from grimoire.tools.workspace_routes import _dump_agent_frontmatter, _load_agent_frontmatter


def test_roundtrip_with_no_mutation_is_byte_identical(tmp_path):
    agent_md = tmp_path / "agent.md"
    agent_md.write_text(
        "<!-- archetype: meta -->\n"
        "---\n"
        "name: mon-agent\n"
        'description: "Un agent de test"  # commentaire inline\n'
        "tools: [Read, Edit]  # outils autorisés\n"
        "\n"
        "# skills assignables par le cockpit\n"
        "skills: [grimoire-agent-dispatch]\n"
        "---\n"
        "Corps de l'agent, jamais touché.\n",
        encoding="utf-8",
    )
    original = agent_md.read_text(encoding="utf-8")

    bom, comment, data, body = _load_agent_frontmatter(agent_md)
    rewritten = _dump_agent_frontmatter(bom, comment, data, body)

    assert rewritten == original


def test_editing_one_field_keeps_every_comment_and_the_archetype_marker(tmp_path):
    agent_md = tmp_path / "agent.md"
    agent_md.write_text(
        "<!-- archetype: meta -->\n"
        "---\n"
        "name: mon-agent\n"
        'description: "Un agent de test"  # commentaire inline\n'
        "tools: [Read, Edit]  # outils autorisés\n"
        "\n"
        "# skills assignables par le cockpit\n"
        "skills: [grimoire-agent-dispatch]\n"
        "---\n"
        "Corps de l'agent, jamais touché.\n",
        encoding="utf-8",
    )

    bom, comment, data, body = _load_agent_frontmatter(agent_md)
    data["skills"] = [*data["skills"], "grimoire-memory"]
    rewritten = _dump_agent_frontmatter(bom, comment, data, body)
    agent_md.write_text(rewritten, encoding="utf-8")

    text = agent_md.read_text(encoding="utf-8")
    assert "<!-- archetype: meta -->" in text
    assert '# commentaire inline' in text
    assert "# outils autorisés" in text
    assert "# skills assignables par le cockpit" in text
    assert "grimoire-memory" in text
    assert "Corps de l'agent, jamais touché." in text
    # Untouched lines stay byte-identical.
    for line in (
        "name: mon-agent",
        'description: "Un agent de test"  # commentaire inline',
        "tools: [Read, Edit]  # outils autorisés",
        "# skills assignables par le cockpit",
    ):
        assert line in text.splitlines()
