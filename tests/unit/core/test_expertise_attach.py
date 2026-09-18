"""``grimoire expertise add/remove`` (issue #616) : l'override projet qui
rend une expertise effective, et surtout la retire proprement.

Le risque spécifique que ces tests couvrent : détacher une expertise sans
supprimer le fichier de skill la rendrait *transversale* (chargée à chaque
tour de la session par tous les agents, voir
``grimoire.hosts.emitters.claude_code`` — "seules les skills que personne ne
déclare restent transversales") — un état pire que l'attachement qu'on
voulait défaire. Même discipline de validation-puis-rollback que
``grimoire.core.override_drift.convert_override`` (tests dans
``tests/unit/test_agent_override_partial.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireAgentError
from grimoire.core.expertises import attach_expertise, detach_expertise, load_catalog
from grimoire.hosts.collect import collect_agents, collect_skills, parse_frontmatter

_CATALOG = {e.id: e for e in load_catalog()}
_RUST = _CATALOG["rust"]  # porteur: stack-engineer, pas de fallback


def _write_kit_agent(root: Path, name: str, *, skills: list[str] | None = None) -> Path:
    path = root / "_grimoire" / "kit" / "agents" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    skills_line = f"skills: {skills!r}".replace("'", '"') if skills is not None else "skills: []"
    path.write_text(
        f'---\nname: "{name}"\ndescription: "{name} de test"\ntools: "read, edit, execute"\n{skills_line}\n---\nCorps du kit.\n',
        encoding="utf-8",
    )
    # Un slug déclaré doit résoudre (collect_agents est fail-closed, #375) —
    # seed un fichier de skill minimal pour chaque slug déjà attaché par
    # défaut, comme le ferait le scaffolder pour une skill détectée.
    skills_dir = root / "_grimoire" / "kit" / "skills"
    for slug in skills or ():
        skill_path = skills_dir / f"{slug}.md"
        if not skill_path.is_file():
            skill_path.parent.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(
                f'---\ndescription: "{slug} de test"\ntools: ["read"]\n---\nCorps de skill.\n', encoding="utf-8"
            )
    return path


class TestAttach:
    def test_writes_skill_body_and_agent_override(self, tmp_path: Path) -> None:
        _write_kit_agent(tmp_path, "stack-engineer", skills=["stack-python"])

        result = attach_expertise(tmp_path, _RUST)

        assert result.already_attached is False
        assert result.porteur == "stack-engineer"
        skill_path = tmp_path / result.skill_ref
        assert skill_path.is_file()
        assert skill_path.read_text(encoding="utf-8") == _RUST.resolve_skill_path().read_text(encoding="utf-8")

        override_path = tmp_path / result.agent_override_ref
        meta, _ = parse_frontmatter(override_path.read_text(encoding="utf-8"))
        assert meta["extends"] == "kit"
        assert meta["skills"] == ["stack-python", "expertise-rust"]
        assert "kit_source_hash" in meta

    def test_is_idempotent(self, tmp_path: Path) -> None:
        _write_kit_agent(tmp_path, "stack-engineer", skills=[])
        first = attach_expertise(tmp_path, _RUST)
        second = attach_expertise(tmp_path, _RUST)

        assert first.already_attached is False
        assert second.already_attached is True
        override_path = tmp_path / second.agent_override_ref
        meta, _ = parse_frontmatter(override_path.read_text(encoding="utf-8"))
        assert meta["skills"].count("expertise-rust") == 1

    def test_the_surface_resolves_after_attach(self, tmp_path: Path) -> None:
        """Une expertise attachée ne doit jamais laisser un slug non résolu —
        même garantie fail-closed que #375 pour les skills `stack-*`."""
        _write_kit_agent(tmp_path, "stack-engineer", skills=["stack-python"])
        attach_expertise(tmp_path, _RUST)

        skills = collect_skills(tmp_path)
        agents = collect_agents(tmp_path, known_skills=frozenset(s.slug for s in skills))
        agent = next(a for a in agents if a.name == "stack-engineer")
        assert "expertise-rust" in agent.skills

    def test_refuses_when_no_porteur_is_installed(self, tmp_path: Path) -> None:
        with pytest.raises(GrimoireAgentError, match="rust"):
            attach_expertise(tmp_path, _RUST)


class TestDetach:
    def test_removes_the_skill_file_not_just_the_reference(self, tmp_path: Path) -> None:
        _write_kit_agent(tmp_path, "stack-engineer", skills=["stack-python"])
        attach_expertise(tmp_path, _RUST)

        result = detach_expertise(tmp_path, _RUST)

        assert result.was_attached is True
        assert result.skill_removed is True
        assert not (tmp_path / "_grimoire" / "overrides" / "skills" / "expertise-rust.md").exists()

    def test_detached_skill_never_resurfaces_as_transversal(self, tmp_path: Path) -> None:
        """Le vrai risque : un fichier de skill oublié sur disque, sans agent
        qui le référence, devient transversal (chargé à chaque tour)."""
        _write_kit_agent(tmp_path, "stack-engineer", skills=["stack-python"])
        attach_expertise(tmp_path, _RUST)
        detach_expertise(tmp_path, _RUST)

        skills = collect_skills(tmp_path)
        assert "expertise-rust" not in {s.slug for s in skills}

    def test_other_attached_skills_survive_detach(self, tmp_path: Path) -> None:
        aws = _CATALOG["aws"]
        _write_kit_agent(tmp_path, "stack-engineer", skills=["stack-python"])
        attach_expertise(tmp_path, _RUST)
        attach_expertise(tmp_path, aws)  # pas d'ops-engineer -> repli stack-engineer

        detach_expertise(tmp_path, _RUST)

        override_path = tmp_path / "_grimoire" / "overrides" / "agents" / "stack-engineer.md"
        meta, _ = parse_frontmatter(override_path.read_text(encoding="utf-8"))
        assert meta["skills"] == ["stack-python", "expertise-aws"]
        assert (tmp_path / "_grimoire" / "overrides" / "skills" / "expertise-aws.md").is_file()

    def test_detach_when_never_attached_is_a_named_no_op(self, tmp_path: Path) -> None:
        _write_kit_agent(tmp_path, "stack-engineer", skills=[])
        result = detach_expertise(tmp_path, _RUST)
        assert result.was_attached is False
        assert result.skill_removed is False
