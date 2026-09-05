"""Les manifestes d'équipe avaient un schéma, trois fichiers, et aucun lecteur.

`framework/teams/` décrit la chaîne vision → build → ops : membres, rôles,
contrats, phases, handoff. `grep -r teams src/` ne renvoyait rien. Ces tests
fixent ce que le chargeur en tire et ce qu'il refuse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.workflows import teams


def _manifest(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / "_grimoire" / "kit" / "teams" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


class TestShippedManifests:
    def test_the_three_teams_load(self, tmp_path: Path) -> None:
        names = {team.name for team in teams.load_teams(tmp_path)}

        assert {"team-vision", "team-build", "team-ops"} <= names

    def test_the_handoff_chain_is_readable(self, tmp_path: Path) -> None:
        chain = {team.name: team.handoff_to for team in teams.load_teams(tmp_path)}

        assert chain["team-vision"] == "team-build"
        assert chain["team-build"] == "team-ops"

    def test_a_roster_carries_roles_and_optionality(self, tmp_path: Path) -> None:
        build = teams.load_team(tmp_path, "team-build")

        assert build is not None
        assert "dev" in build.required_agents
        optional = [m.name for m in build.agents if not m.required]
        assert "tech-writer" in optional
        assert all(member.role for member in build.agents)

    def test_delivery_phases_are_exposed(self, tmp_path: Path) -> None:
        build = teams.load_team(tmp_path, "team-build")

        assert build is not None
        assert len(build.phases) >= 4


class TestResolution:
    def test_a_project_manifest_shadows_the_framework_one(self, tmp_path: Path) -> None:
        _manifest(tmp_path, "team-build", 'team:\n  name: "team-build"\n  description: "maison"\n')

        build = teams.load_team(tmp_path, "team-build")

        assert build is not None
        assert build.description == "maison"

    def test_a_team_resolves_by_file_stem(self, tmp_path: Path) -> None:
        _manifest(tmp_path, "revue", 'team:\n  name: "equipe-revue"\n')

        assert teams.load_team(tmp_path, "revue") is not None
        assert teams.load_team(tmp_path, "equipe-revue") is not None

    def test_an_empty_name_resolves_to_nothing(self, tmp_path: Path) -> None:
        assert teams.load_team(tmp_path, "") is None
        assert teams.load_team(tmp_path, "   ") is None

    def test_an_unknown_team_returns_none(self, tmp_path: Path) -> None:
        assert teams.load_team(tmp_path, "team-fantome") is None


class TestMalformedManifests:
    def test_a_file_without_a_team_section_is_skipped(self, tmp_path: Path) -> None:
        path = _manifest(tmp_path, "pas-une-equipe", "autre_chose:\n  cle: valeur\n")

        assert teams.parse_team(path) is None

    def test_broken_yaml_is_reported_not_swallowed(self, tmp_path: Path) -> None:
        """Un manifeste cassé ne doit pas faire tomber `workflows show` — mais il
        ne doit pas non plus disparaître : `None` était la réponse pour « pas
        une équipe » et pour « fichier illisible », et l'équipe manquait au
        catalogue sans une ligne."""
        path = _manifest(tmp_path, "cassee", "team:\n  name: [non fermée\n")

        with pytest.raises(teams.TeamManifestError) as exc:
            teams.parse_team(path)
        assert "cassee.yaml" in str(exc.value)

        catalog = teams.load_team_catalog(tmp_path)
        assert [t.name for t in catalog.teams] == [t.name for t in teams.load_teams(tmp_path)]
        assert len(catalog.unreadable) == 1
        assert catalog.unreadable[0].path == path
        assert "ParserError" in catalog.unreadable[0].reason

    def test_a_member_without_a_name_is_dropped(self, tmp_path: Path) -> None:
        path = _manifest(
            tmp_path, "partielle",
            'team:\n  name: "t"\n  agents:\n    - name: "dev"\n      role: "lead"\n    - role: "sans nom"\n',
        )
        team = teams.parse_team(path)

        assert team is not None
        assert [m.name for m in team.agents] == ["dev"]

    def test_a_team_without_agents_still_loads(self, tmp_path: Path) -> None:
        path = _manifest(tmp_path, "vide", 'team:\n  name: "t"\n  description: "d"\n')
        team = teams.parse_team(path)

        assert team is not None
        assert team.agents == ()
        assert team.required_agents == ()
