"""L'archétype `infra-ops` refait pour l'issue #375.

Sept agents dont six au même faisceau d'outils (`{read, edit, execute}`,
aucun contexte, aucun skill) : `duplicate_agent_fingerprints` les déclarait
tous indiscernables. La refonte réduit l'archétype à trois agents à faisceau
distinct (`ops-engineer`, `monitoring-specialist`, `systems-debugger`) et
convertit les quatre autres en skills attachés à `ops-engineer`, portés par
`archetypes/infra-ops/skills/*.md` et déclarés dans `archetype.dna.yaml`.

Ces tests couvrent : le scaffolder installe les skills et les résout, l'agent
qui les porte les référence, et la garde de distinction (#372) ne trouve plus
de doublon *au sein de cet archétype* — le reste du corpus (`meta`) est une
dette distincte, non traitée ici.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.archetypes import bundled_path
from grimoire.core.archetype_resolver import ResolvedArchetype
from grimoire.core.scaffold import ProjectScaffolder
from grimoire.core.scanner import ScanResult
from grimoire.hosts.collect import build_surface
from grimoire.hosts.surface import duplicate_agent_fingerprints

_INFRA_OPS_AGENTS = frozenset({"ops-engineer", "monitoring-specialist", "systems-debugger"})
_ATTACHED_SKILLS = (
    "infra-pipeline-cicd",
    "infra-security-hardening",
    "infra-backup-dr",
    "infra-k8s-gitops",
)


def _scaffolder(tmp_path: Path) -> ProjectScaffolder:
    return ProjectScaffolder(
        tmp_path,
        project_name="test-project",
        user_name="Test User",
        language="Français",
        skill_level="intermediate",
        scan=ScanResult(stacks=(), project_type="generic", root=Path("/fake")),
        resolved=ResolvedArchetype(
            archetype="infra-ops",
            stack_agents=(),
            feature_agents=(),
            reason="test",
        ),
        backend="local",
    )


class TestArchetypeSkillsAreInstalled:
    def test_the_four_converted_skills_are_planned(self, tmp_path: Path) -> None:
        plan = _scaffolder(tmp_path).plan()
        planned = {fc.dst.name for fc in plan.copies}
        for slug in _ATTACHED_SKILLS:
            assert f"{slug}.md" in planned

    def test_skills_land_in_the_kit_skills_directory(self, tmp_path: Path) -> None:
        plan = _scaffolder(tmp_path).plan()
        fc = next(fc for fc in plan.copies if fc.dst.name == "infra-pipeline-cicd.md")
        assert fc.dst.parent == tmp_path / "_grimoire" / "kit" / "skills"

    def test_converted_agents_no_longer_exist(self) -> None:
        agents_dir = bundled_path() / "infra-ops" / "agents"
        removed = {"pipeline-architect", "security-hardener", "backup-dr-specialist", "k8s-navigator"}
        present = {p.stem for p in agents_dir.glob("*.md")}
        assert not (removed & present)

    def test_archetype_ships_at_most_three_agents(self) -> None:
        agents_dir = bundled_path() / "infra-ops" / "agents"
        assert len(list(agents_dir.glob("*.md"))) == 3


class TestOpsEngineerCarriesTheConvertedKnowHow:
    def test_ops_engineer_declares_the_four_skills(self, tmp_path: Path) -> None:
        _scaffolder(tmp_path).execute(_scaffolder(tmp_path).plan())
        surface = build_surface(tmp_path)
        ops = next(a for a in surface.agents if a.name == "ops-engineer")
        assert set(ops.skills) == set(_ATTACHED_SKILLS)

    def test_the_four_skills_resolve_against_the_collected_inventory(self, tmp_path: Path) -> None:
        """A dangling ``skills:`` slug is a build error (fail-closed) — this
        must not raise, which is the proof the scaffolder actually installs
        what the agent references."""
        _scaffolder(tmp_path).execute(_scaffolder(tmp_path).plan())
        build_surface(tmp_path)  # ne doit pas lever GrimoireAgentError


class TestDistinctionGuardNoLongerFlagsInfraOps:
    def test_no_duplicate_pair_is_internal_to_infra_ops(self, tmp_path: Path) -> None:
        """Before the rewrite, five of the seven infra-ops agents shared one
        fingerprint — duplicates *within* the archetype. A pair where an
        infra-ops agent collides with a `meta` agent (e.g.
        `art-director`/`monitoring-specialist`) is `meta`'s own debt, out of
        scope for this issue; a pair internal to infra-ops would mean this
        rewrite failed its own goal."""
        _scaffolder(tmp_path).execute(_scaffolder(tmp_path).plan())
        surface = build_surface(tmp_path)
        duplicates = duplicate_agent_fingerprints(surface.agents)
        internal = [pair for pair in duplicates if all(name in _INFRA_OPS_AGENTS for name in pair)]
        assert internal == []

    def test_the_three_kept_agents_have_distinct_fingerprints(self, tmp_path: Path) -> None:
        _scaffolder(tmp_path).execute(_scaffolder(tmp_path).plan())
        surface = build_surface(tmp_path)
        kept = [a for a in surface.agents if a.name in _INFRA_OPS_AGENTS]
        assert len(kept) == 3
        fingerprints = {a.fingerprint() for a in kept}
        assert len(fingerprints) == 3

    def test_build_surface_does_not_raise_for_this_archetype(self, tmp_path: Path) -> None:
        """The strict regime only raises for an override colliding with the
        kit tier (see `build_surface`'s two-regime comment) — none of these
        three agents live in overrides, so this must not raise even though
        `surface.notes` may still carry `meta`'s unrelated debt."""
        _scaffolder(tmp_path).execute(_scaffolder(tmp_path).plan())
        build_surface(tmp_path)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
