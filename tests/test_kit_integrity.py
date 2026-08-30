"""The two checks the doctor was missing.

``grimoire doctor`` verified that its own directories existed. It never read the
paths written *inside* the files it had just installed, nor compared the agents
those files route to against the agents installed. A project therefore passed
20/20 while carrying dozens of instructions no agent could follow.

Each test here asserts both directions: the check passes on a sound install, and
fails on a planted defect. A guard that cannot fail proves nothing.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.archetype_resolver import ResolvedArchetype
from grimoire.core.integrity import (
    dead_path_references,
    installed_agent_tags,
    roster_incoherences,
)
from grimoire.core.scaffold import ProjectScaffolder


def _install(target: Path, archetypes: tuple[str, ...] = ("infra-ops",)) -> Path:
    scaffolder = ProjectScaffolder(
        target,
        project_name="integrity-test",
        user_name="Test User",
        language="Français",
        skill_level="expert",
        scan=None,
        resolved=ResolvedArchetype(
            archetype=archetypes[0],
            stack_agents=(),
            feature_agents=(),
            reason="test",
            archetypes=archetypes,
        ),
        backend="local",
    )
    scaffolder.execute(scaffolder.plan())
    return target


def _concierge(root: Path) -> Path:
    return root / "_grimoire" / "kit" / "agents" / "concierge.md"


class TestPathResolution:
    def test_a_sound_install_has_no_dead_reference(self, tmp_path: Path) -> None:
        _install(tmp_path)
        assert dead_path_references(tmp_path) == []

    def test_a_dead_reference_is_reported_with_its_origin(self, tmp_path: Path) -> None:
        _install(tmp_path)
        target = _concierge(tmp_path)
        target.write_text(
            target.read_text(encoding="utf-8") + "\nCharger `_grimoire/kit/absent.md`.\n",
            encoding="utf-8",
        )

        dead = dead_path_references(tmp_path)
        assert len(dead) == 1
        assert dead[0].target == "_grimoire/kit/absent.md"
        assert dead[0].source == "_grimoire/kit/agents/concierge.md"
        assert dead[0].line > 1

    def test_output_artifacts_are_out_of_scope(self, tmp_path: Path) -> None:
        """Naming a file a run will produce is the point, not a defect."""
        _install(tmp_path)
        target = _concierge(tmp_path)
        target.write_text(
            target.read_text(encoding="utf-8") + "\nÉcrire `_grimoire-output/rapport.md`.\n",
            encoding="utf-8",
        )
        assert dead_path_references(tmp_path) == []

    def test_a_name_the_run_completes_is_not_a_dead_path(self, tmp_path: Path) -> None:
        _install(tmp_path)
        target = _concierge(tmp_path)
        target.write_text(
            target.read_text(encoding="utf-8") + "\nÉcrire `_grimoire/_memory/fer-{id}.yaml`.\n",
            encoding="utf-8",
        )
        assert dead_path_references(tmp_path) == []


class TestRosterCoherence:
    def test_a_sound_install_routes_only_to_installed_agents(self, tmp_path: Path) -> None:
        _install(tmp_path, ("platform-engineering", "infra-ops"))
        report = roster_incoherences(tmp_path)
        assert report.maps_found >= 1
        assert report.routed_but_absent == []
        assert report.coherent

    def test_a_ghost_agent_is_named(self, tmp_path: Path) -> None:
        """The exact defect: a map inherited from another archetype family."""
        _install(tmp_path)
        target = _concierge(tmp_path)
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "</agents>",
                '  <agent tag="dev" name="Amelia" role="fantôme"/>\n    </agents>',
                1,
            ),
            encoding="utf-8",
        )
        report = roster_incoherences(tmp_path)
        assert report.routed_but_absent == ["dev"]
        assert not report.coherent

    def test_an_installed_agent_missing_from_every_map_is_reported(self, tmp_path: Path) -> None:
        _install(tmp_path)
        installed = installed_agent_tags(tmp_path)
        assert "ops-engineer" in installed

        # Une carte qui ne garde qu'un agent : elle existe, mais elle en oublie.
        target = _concierge(tmp_path)
        text = target.read_text(encoding="utf-8")
        start, end = text.index("<agents>"), text.index("</agents>")
        kept = '<agents>\n      <agent tag="concierge" name="Marcel" role="triage"/>\n    '
        target.write_text(text[:start] + kept + text[end:], encoding="utf-8")

        report = roster_incoherences(tmp_path)
        assert report.maps_found >= 1
        assert "ops-engineer" in report.installed_but_unrouted
        # Not a failure on its own: an agent can be reached without a map entry.
        assert report.coherent


class TestDoctorReportsThem:
    def test_doctor_passes_on_a_sound_install(self, tmp_path: Path) -> None:
        _install(tmp_path)
        result = CliRunner().invoke(app, ["doctor", str(tmp_path)])
        assert "tous les chemins du kit cités se résolvent" in result.output
        assert "carte de routage cohérente" in result.output

    def test_doctor_fails_on_a_dead_path(self, tmp_path: Path) -> None:
        _install(tmp_path)
        target = _concierge(tmp_path)
        target.write_text(
            target.read_text(encoding="utf-8") + "\nCharger `_grimoire/kit/absent.md`.\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(app, ["doctor", str(tmp_path)])
        assert "chemin(s) du kit cité(s) mais absent(s)" in result.output
        assert "_grimoire/kit/absent.md" in result.output

    def test_doctor_fails_on_a_ghost_agent(self, tmp_path: Path) -> None:
        _install(tmp_path)
        target = _concierge(tmp_path)
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "</agents>",
                '  <agent tag="quick-flow-solo-dev" name="Barry" role="fantôme"/>\n    </agents>',
                1,
            ),
            encoding="utf-8",
        )
        result = CliRunner().invoke(app, ["doctor", str(tmp_path)])
        assert "routé(s) mais non installé(s)" in result.output
        assert "quick-flow-solo-dev" in result.output


class TestScope:
    def test_a_project_without_a_kit_tier_is_left_alone(self, tmp_path: Path) -> None:
        """A hand-made or pre-boundary tree shipped nothing to hold to a promise."""
        from grimoire.core.integrity import has_kit_tier

        (tmp_path / "_grimoire" / "_memory").mkdir(parents=True)
        (tmp_path / "_grimoire-output").mkdir(parents=True)
        (tmp_path / "project-context.yaml").write_text(
            'project:\n  name: "legacy"\nmemory:\n  backend: "local"\n'
            'agents:\n  archetype: "minimal"\n',
            encoding="utf-8",
        )
        legacy_agents = tmp_path / "_grimoire" / "_config" / "custom" / "agents"
        legacy_agents.mkdir(parents=True)
        (legacy_agents / "helper.md").write_text(
            "---\ndescription: Helper\n---\n# helper\n", encoding="utf-8",
        )

        assert not has_kit_tier(tmp_path)
        result = CliRunner().invoke(app, ["doctor", str(tmp_path)])
        # D'autres checks peuvent légitimement échouer sur un tel projet ; ce qui
        # compte est qu'aucun des deux nouveaux ne s'exprime.
        assert "chemin(s) du kit cité(s) mais absent(s)" not in result.output
        assert "routé(s) mais non installé(s)" not in result.output
        assert "tous les chemins du kit cités se résolvent" not in result.output

    def test_a_scaffolded_project_is_checked(self, tmp_path: Path) -> None:
        from grimoire.core.integrity import has_kit_tier

        _install(tmp_path)
        assert has_kit_tier(tmp_path)
        result = CliRunner().invoke(app, ["doctor", str(tmp_path)])
        assert "tous les chemins du kit cités se résolvent" in result.output


class TestNestedRepositories:
    def test_a_vendored_clone_is_not_this_project(self, tmp_path: Path) -> None:
        """A repository checked out inside a project answers to its own tree.

        The Forge carries a clone of the kit; scanning it reported 345 dead
        references from files the Forge never installed — noise nobody can act
        on from `grimoire doctor`, and a check reporting the unfixable is one
        people learn to skip.
        """
        _install(tmp_path)
        assert dead_path_references(tmp_path) == []

        vendored = tmp_path / "vendor" / "some-clone"
        (vendored / ".git").mkdir(parents=True)
        (vendored / "README.md").write_text(
            "Charger `_grimoire/kit/inexistant.md` et `_grimoire/_config/vieux.csv`.\n",
            encoding="utf-8",
        )

        assert dead_path_references(tmp_path) == [], (
            "les chemins d'un dépôt imbriqué sont comptés comme ceux du projet"
        )

    def test_the_projects_own_files_are_still_read(self, tmp_path: Path) -> None:
        """Skipping nested repos must not blind the check to the project."""
        _install(tmp_path)
        (tmp_path / "vendor" / "clone" / ".git").mkdir(parents=True)

        target = _concierge(tmp_path)
        target.write_text(
            target.read_text(encoding="utf-8") + "\nCharger `_grimoire/kit/absent.md`.\n",
            encoding="utf-8",
        )
        dead = dead_path_references(tmp_path)
        assert [ref.target for ref in dead] == ["_grimoire/kit/absent.md"]

    def test_a_nested_repo_does_not_pollute_the_roster(self, tmp_path: Path) -> None:
        _install(tmp_path)
        vendored = tmp_path / "vendor" / "clone"
        (vendored / ".git").mkdir(parents=True)
        (vendored / "agents.md").write_text(
            '<agent tag="dev" name="Amelia" role="d\'un autre dépôt"/>\n', encoding="utf-8",
        )
        assert roster_incoherences(tmp_path).routed_but_absent == []

    def test_the_projects_own_git_directory_is_not_nested(self, tmp_path: Path) -> None:
        """The guard exists for this: a project is not a repository inside itself.

        Without the `root != project_root` test, a project under version control
        — every real one — would exclude its whole tree and the check would
        silently pass on everything.
        """
        _install(tmp_path)
        (tmp_path / ".git").mkdir()

        target = _concierge(tmp_path)
        target.write_text(
            target.read_text(encoding="utf-8") + "\nCharger `_grimoire/kit/absent.md`.\n",
            encoding="utf-8",
        )
        dead = dead_path_references(tmp_path)
        assert [ref.target for ref in dead] == ["_grimoire/kit/absent.md"], (
            "le `.git` du projet lui-même a exclu son propre arbre"
        )


class TestArchives:
    def test_an_archived_agent_does_not_haunt_the_roster(self, tmp_path: Path) -> None:
        """A retired persona keeps its old map; it is a record, not a promise.

        The Forge parks superseded agents under `overrides/agents/_archived/`.
        Its archived concierge still carried the pre-generation roster, so the
        check resurrected nine agents the project had deliberately removed.
        """
        _install(tmp_path)
        archive = tmp_path / "_grimoire" / "overrides" / "agents" / "_archived"
        archive.mkdir(parents=True)
        (archive / "concierge.md").write_text(
            '<agents>\n  <agent tag="dev" name="Amelia" role="retiré"/>\n</agents>\n',
            encoding="utf-8",
        )
        assert roster_incoherences(tmp_path).routed_but_absent == []

    def test_an_archived_file_makes_no_path_promise(self, tmp_path: Path) -> None:
        _install(tmp_path)
        archive = tmp_path / "_grimoire" / "overrides" / "agents" / "_archived"
        archive.mkdir(parents=True)
        (archive / "old.md").write_text(
            "Charger `_grimoire/_config/custom/cc-verify.sh`.\n", encoding="utf-8",
        )
        assert dead_path_references(tmp_path) == []

    def test_a_live_agent_is_still_read(self, tmp_path: Path) -> None:
        """Skipping archives must not blind the check to what is in service."""
        _install(tmp_path)
        (tmp_path / "_grimoire" / "overrides" / "agents" / "_archived").mkdir(parents=True)
        target = _concierge(tmp_path)
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "</agents>", '  <agent tag="dev" name="Amelia" role="vivant"/>\n    </agents>', 1,
            ),
            encoding="utf-8",
        )
        assert roster_incoherences(tmp_path).routed_but_absent == ["dev"]
