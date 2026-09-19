"""Le catalogue d'expertises (issue #616) ne doit jamais pointer dans le vide.

Un id sans skill existant, un porteur qui ne correspond à aucun agent livré,
un id dupliqué : trois défauts qu'un simple import ne détecterait pas, et que
`grimoire expertise add <id>` transformerait en échec pour l'utilisateur bien
après la release. Ces tests figent le contrat que
`registry/expertises.yaml` doit tenir en permanence — le même esprit que
`tests/test_scaffold.py::test_la_detection_de_pile_livre_le_generaliste_avec_ses_skills`
pour les sept skills `stack-*` historiques.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.archetypes import bundled_path
from grimoire.core.expertises import FAMILIES, load_catalog
from grimoire.hosts.collect import parse_frontmatter

_CATALOG = load_catalog()


def _archetype_agent_stems(archetype: str) -> set[str]:
    agents_dir = bundled_path() / archetype / "agents"
    if not agents_dir.is_dir():
        return set()
    return {p.stem for p in agents_dir.glob("*.md")}


_KNOWN_AGENTS = _archetype_agent_stems("stack") | _archetype_agent_stems("infra-ops")


class TestCatalogShape:
    def test_catalog_is_not_empty(self) -> None:
        assert len(_CATALOG) >= 20

    def test_every_family_is_recognised(self) -> None:
        for entry in _CATALOG:
            assert entry.family in FAMILIES

    def test_ids_are_unique(self) -> None:
        ids = [e.id for e in _CATALOG]
        assert len(ids) == len(set(ids))

    def test_expected_languages_are_present(self) -> None:
        ids = {e.id for e in _CATALOG if e.family == "languages"}
        for expected in ("python", "typescript", "go", "rust", "c", "cpp", "csharp", "java"):
            assert expected in ids

    def test_expected_cloud_providers_are_present(self) -> None:
        ids = {e.id for e in _CATALOG if e.family == "cloud"}
        for expected in ("aws", "azure", "gcp"):
            assert expected in ids

    def test_the_seven_stack_skills_are_referenced_not_duplicated(self) -> None:
        """python/typescript/go réutilisent `archetypes/stack/skills/stack-*.md`
        tel quel — le catalogue ne duplique jamais leur contenu."""
        by_id = {e.id: e for e in _CATALOG}
        for language, slug in (("python", "stack-python"), ("typescript", "stack-typescript"), ("go", "stack-go")):
            entry = by_id[language]
            assert entry.skill_relpath == f"archetypes/stack/skills/{slug}.md"


class TestEverySkillFileExistsAndIsValid:
    @pytest.mark.parametrize("expertise", _CATALOG, ids=lambda e: e.id)
    def test_skill_path_resolves_to_an_existing_file(self, expertise) -> None:  # type: ignore[no-untyped-def]
        path = expertise.resolve_skill_path()
        assert path.is_file(), f"expertise {expertise.id!r} : skill introuvable ({expertise.skill_relpath})"

    @pytest.mark.parametrize("expertise", _CATALOG, ids=lambda e: e.id)
    def test_skill_frontmatter_has_description_and_tools(self, expertise) -> None:  # type: ignore[no-untyped-def]
        text = expertise.resolve_skill_path().read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        assert meta.get("description"), f"expertise {expertise.id!r} : description manquante"
        assert meta.get("tools"), f"expertise {expertise.id!r} : tools manquant"
        assert body.strip(), f"expertise {expertise.id!r} : corps de skill vide"


class TestEveryPorteurIsAKnownAgent:
    @pytest.mark.parametrize("expertise", _CATALOG, ids=lambda e: e.id)
    def test_porteur_names_an_agent_shipped_by_stack_or_infra_ops(self, expertise) -> None:  # type: ignore[no-untyped-def]
        assert expertise.porteur in _KNOWN_AGENTS, (
            f"expertise {expertise.id!r} : porteur {expertise.porteur!r} inconnu de "
            f"stack/infra-ops ({sorted(_KNOWN_AGENTS)})"
        )

    @pytest.mark.parametrize("expertise", [e for e in _CATALOG if e.porteur_fallback], ids=lambda e: e.id)
    def test_porteur_fallback_names_a_known_agent_too(self, expertise) -> None:  # type: ignore[no-untyped-def]
        assert expertise.porteur_fallback in _KNOWN_AGENTS


class TestResolvePorteur:
    def test_resolves_declared_porteur_when_installed(self, tmp_path: Path) -> None:
        (tmp_path / "_grimoire" / "kit" / "agents").mkdir(parents=True)
        (tmp_path / "_grimoire" / "kit" / "agents" / "ops-engineer.md").write_text(
            '---\nname: "ops-engineer"\n---\ncorps\n', encoding="utf-8"
        )
        entry = next(e for e in _CATALOG if e.id == "aws")
        assert entry.resolve_porteur(tmp_path) == "ops-engineer"

    def test_falls_back_when_declared_porteur_is_absent(self, tmp_path: Path) -> None:
        (tmp_path / "_grimoire" / "kit" / "agents").mkdir(parents=True)
        (tmp_path / "_grimoire" / "kit" / "agents" / "stack-engineer.md").write_text(
            '---\nname: "stack-engineer"\n---\ncorps\n', encoding="utf-8"
        )
        entry = next(e for e in _CATALOG if e.id == "aws")
        assert entry.resolve_porteur(tmp_path) == "stack-engineer"

    def test_refuses_named_when_neither_porteur_is_installed(self, tmp_path: Path) -> None:
        from grimoire.core.exceptions import GrimoireAgentError

        (tmp_path / "_grimoire" / "kit" / "agents").mkdir(parents=True)
        entry = next(e for e in _CATALOG if e.id == "aws")
        with pytest.raises(GrimoireAgentError, match="aws"):
            entry.resolve_porteur(tmp_path)
