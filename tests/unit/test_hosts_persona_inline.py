"""``host sync`` intègre la fiche de persona au lieu d'un pointeur (#1179).

Trois propriétés, celles de l'issue :

1. la définition générée contient le corps de la fiche, et plus la consigne
   « Lis … en entier » — un sous-agent n'a plus à payer un tour de lecture de
   10 Ko pour recevoir sa propre persona ;
2. le contexte déclaré est présenté « à consulter au besoin », avec la liste et
   la taille des documents — des workers avaient lu des fichiers de 28 à
   782 Ko sans en avoir besoin ;
3. une fiche au-delà du plafond garde le pointeur, et ``grimoire host sync``
   le signale au lieu de le taire.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.app import app
from grimoire.hosts.collect import build_surface, collect_agents
from grimoire.hosts.emitters import apply_plan, emitter_for
from grimoire.hosts.surface import MAX_INLINE_PERSONA_BYTES

AGENT_DIR = Path("_grimoire/_config/custom/agents")
CONTEXT_PATH = "_grimoire/_memory/project-notes.md"

#: Chemin de la définition générée, par hôte. Le texte testé est le même : la
#: règle d'intégration vit dans les émetteurs partagés, pas dans un hôte.
DEFINITION_PATHS = {
    HostId.CLAUDE_CODE_CLI: Path(".claude/agents/scribe.md"),
    HostId.GITHUB_COPILOT: Path(".github/agents/scribe.agent.md"),
    HostId.OPENCODE: Path(".opencode/agent/scribe.md"),
}


def _write_agent(root: Path, name: str, body: str, *, context: tuple[str, ...] = ()) -> None:
    (root / AGENT_DIR).mkdir(parents=True, exist_ok=True)
    header = [
        "---",
        f'name: "{name}"',
        f'description: "{name} — rôle de test"',
        "tools: [read, edit]",
    ]
    if context:
        rendered = ", ".join(f"'{c}'" for c in context)
        header.append(f"context: [{rendered}]")
    header.append("---")
    (root / AGENT_DIR / f"{name}.md").write_text("\n".join(header) + "\n" + body + "\n", encoding="utf-8")


def _emit(root: Path, host: HostId) -> None:
    emitter = emitter_for(host)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(root), root), root)


def _definition(root: Path, host: HostId) -> str:
    return (root / DEFINITION_PATHS[host]).read_text(encoding="utf-8")


@pytest.fixture
def persona_body() -> str:
    return "Tu rédiges la documentation du projet et tu tiens le journal des décisions."


@pytest.mark.parametrize("host", list(DEFINITION_PATHS))
def test_the_definition_embeds_the_persona_body_instead_of_a_pointer(
    tmp_path: Path, persona_body: str, host: HostId
) -> None:
    _write_agent(tmp_path, "scribe", persona_body)

    _emit(tmp_path, host)
    text = _definition(tmp_path, host)

    assert persona_body in text, "le corps de la fiche doit être intégré dans la définition"
    assert f"Lis `{AGENT_DIR}/scribe.md` en entier" not in text, "plus de mandat de lecture intégrale"
    assert f"{AGENT_DIR}/scribe.md" in text, "le fichier source reste cité pour sa mise à jour"
    assert "grimoire host sync" in text


@pytest.mark.parametrize("host", list(DEFINITION_PATHS))
def test_declared_context_is_consulted_on_demand_with_its_size(tmp_path: Path, host: HostId) -> None:
    context_file = tmp_path / CONTEXT_PATH
    context_file.parent.mkdir(parents=True, exist_ok=True)
    # 2 048 octets : la taille doit apparaître sous une forme lisible.
    context_file.write_text("x" * 2048, encoding="utf-8")
    _write_agent(tmp_path, "scribe", "Tu rédiges.", context=(CONTEXT_PATH,))

    _emit(tmp_path, host)
    text = _definition(tmp_path, host)

    assert "au besoin" in text
    assert CONTEXT_PATH in text
    assert "2,0 Ko" in text, "la taille du document déclaré doit être annoncée"
    assert "Lis ton contexte déclaré" not in text, "le contexte déclaré n'est plus un ordre de lecture"


def test_each_declared_document_is_listed_with_its_own_size(tmp_path: Path) -> None:
    """Plusieurs documents : chacun porte sa taille, d'où le choix d'ouvrir ou non."""
    (tmp_path / "small.md").write_text("x" * 2048, encoding="utf-8")
    (tmp_path / "big.md").write_bytes(b"x" * (1536 * 1024))
    _write_agent(tmp_path, "scribe", "Tu rédiges.", context=("small.md", "big.md"))

    _emit(tmp_path, HostId.CLAUDE_CODE_CLI)
    text = _definition(tmp_path, HostId.CLAUDE_CODE_CLI)

    assert "`small.md` (2,0 Ko)" in text
    assert "`big.md` (1,5 Mo)" in text, "au-delà du mégaoctet, la taille reste lisible"
    assert "et seulement si la tâche le demande" in text


def test_an_invalid_inline_persona_setting_is_reported(tmp_path: Path) -> None:
    """``hosts.inline_persona`` n'accepte qu'un booléen : une chaîne est une erreur de config."""
    from grimoire.core.config import HostsConfig
    from grimoire.core.exceptions import GrimoireConfigError

    with pytest.raises(GrimoireConfigError, match="inline_persona"):
        HostsConfig.from_dict({"inline_persona": "oui"})


def test_an_unreadable_project_context_falls_back_to_inlining(tmp_path: Path, persona_body: str) -> None:
    """Un ``project-context.yaml`` illisible ne casse pas la surface : le défaut reste l'intégration."""
    _write_agent(tmp_path, "scribe", persona_body)
    (tmp_path / "project-context.yaml").write_text("hosts: [unclosed\n", encoding="utf-8")

    (agent,) = [a for a in build_surface(tmp_path).agents if a.name == "scribe"]

    assert agent.definition_body == persona_body


def test_an_oversized_persona_keeps_the_pointer_and_warns_on_sync(tmp_path: Path) -> None:
    oversized = "Tu rédiges. " + ("détail " * (MAX_INLINE_PERSONA_BYTES // 7 + 1))
    assert len(oversized.encode("utf-8")) > MAX_INLINE_PERSONA_BYTES
    _write_agent(tmp_path, "scribe", oversized)

    surface = build_surface(tmp_path)
    notes = " ".join(surface.notes)
    assert "scribe" in notes
    assert "plafond" in notes
    assert "pointeur" in notes

    _emit(tmp_path, HostId.CLAUDE_CODE_CLI)
    text = _definition(tmp_path, HostId.CLAUDE_CODE_CLI)
    assert f"Lis `{AGENT_DIR}/scribe.md` en entier" in text, "au-delà du plafond, le pointeur reste"
    assert "détail détail détail" not in text, "le corps trop lourd n'est pas recopié"


def test_host_sync_prints_the_oversize_warning(tmp_path: Path) -> None:
    oversized = "Tu rédiges. " + ("détail " * (MAX_INLINE_PERSONA_BYTES // 7 + 1))
    _write_agent(tmp_path, "scribe", oversized)

    result = CliRunner().invoke(
        app,
        ["host", "sync", "--host", "claude", "--force-host", "--project-root", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert "plafond" in result.output
    assert "scribe" in result.output


def test_host_sync_json_output_carries_the_oversize_note(tmp_path: Path) -> None:
    oversized = "Tu rédiges. " + ("détail " * (MAX_INLINE_PERSONA_BYTES // 7 + 1))
    _write_agent(tmp_path, "scribe", oversized)

    result = CliRunner().invoke(
        app,
        [
            "-o",
            "json",
            "host",
            "sync",
            "--host",
            "claude",
            "--force-host",
            "--project-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    notes = " ".join(payload["notes"])
    assert "plafond" in notes
    assert "scribe" in notes


def test_the_collected_body_is_the_persona_text_without_frontmatter(tmp_path: Path, persona_body: str) -> None:
    _write_agent(tmp_path, "scribe", persona_body)

    (agent,) = [a for a in collect_agents(tmp_path) if a.name == "scribe"]

    assert agent.definition_body == persona_body
    assert agent.inline_body() == persona_body


def test_inline_persona_can_be_switched_off(tmp_path: Path, persona_body: str) -> None:
    """``hosts.inline_persona: false`` restaure le pointeur seul (option de config)."""
    _write_agent(tmp_path, "scribe", persona_body)
    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: test\nhosts:\n  inline_persona: false\n",
        encoding="utf-8",
    )

    (agent,) = [a for a in build_surface(tmp_path).agents if a.name == "scribe"]
    assert agent.definition_body == "", "le réglage `hosts.inline_persona: false` désactive l'intégration"

    _emit(tmp_path, HostId.CLAUDE_CODE_CLI)
    text = _definition(tmp_path, HostId.CLAUDE_CODE_CLI)
    assert f"Lis `{AGENT_DIR}/scribe.md` en entier" in text
    assert persona_body not in text
