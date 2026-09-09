"""Un fichier d'équipe livré qui ment sur qui existe (issue #346).

`framework/teams/team-build.yaml`, `team-ops.yaml` et `team-vision.yaml`
référençaient `architect`, `dev`, `qa`, `sm`, `pm`, `analyst`, `ux-designer`,
`tech-writer` et `innovation-strategist` — neuf noms hérités de la pile BMAD
qu'aucun archétype du kit ne livre. Ils sont retirés. Ce test empêche qu'un
manifeste d'équipe reproduise le même mensonge : chaque agent qu'il nomme
doit exister dans au moins un archétype du kit.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.workflows.teams import parse_team

REPO = Path(__file__).resolve().parents[3]


def _shipped_agent_names() -> set[str]:
    """Tous les noms d'agents que le kit livre, tous archétypes confondus."""
    names: set[str] = set()
    for pattern in ("*.md", "*.dna.yaml", "*.tpl.md"):
        for path in (REPO / "archetypes").glob(f"*/agents/{pattern}"):
            stem = path.name.split(".")[0]
            names.add(stem)
    return names


def test_no_shipped_team_manifest_references_an_absent_agent() -> None:
    shipped = _shipped_agent_names()
    assert shipped, "aucun agent trouvé sous archetypes/*/agents/ — le test ne garde rien"

    teams_dir = REPO / "framework" / "teams"
    manifests = sorted(teams_dir.glob("*.yaml")) if teams_dir.is_dir() else []

    offenders: list[str] = []
    for path in manifests:
        team = parse_team(path)
        if team is None:
            continue
        for member in team.agents:
            if member.name not in shipped:
                offenders.append(f"{path.name}: {member.name}")

    assert not offenders, f"agents absents du kit référencés par une équipe livrée : {offenders}"
