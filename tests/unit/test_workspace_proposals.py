"""Les propositions d'artefact à travers la route de la vue de travail (issue #395).

Le moteur lui-même (seuil, type, accepter, refuser) est couvert par
``tests/unit/test_proposals.py`` ; ce module prouve seulement que la route
``/api/workspace/proposals`` — celle que le cockpit consomme — branche le
même moteur, dans les deux sens (lecture, puis les deux écritures).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.hosts.decisions import record_agent_miss
from grimoire.tools.workspace_routes import PREFIX, workspace_get, workspace_post


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: test-proposals-route\n  type: webapp\n"
        "user:\n  name: Guilhem\n  language: Français\n  skill_level: expert\n"
        "agents:\n  archetype: minimal\n",
        encoding="utf-8",
    )
    return tmp_path


def test_get_proposals_lists_the_facts_that_ground_it(project: Path) -> None:
    record_agent_miss(project, category="infra", specialty="terraform")
    record_agent_miss(project, category="infra", specialty="terraform")

    payload = workspace_get(project, f"{PREFIX}proposals", {})
    assert payload["proposals"]
    entry = payload["proposals"][0]
    assert entry["specialty"] == "terraform"
    assert entry["count"] == 2
    assert entry["status"] == "pending"
    assert entry["use_when"]


def test_accept_through_the_route_writes_the_agent_file(project: Path) -> None:
    record_agent_miss(project, category="infra", specialty="terraform")
    record_agent_miss(project, category="infra", specialty="terraform")
    slug = workspace_get(project, f"{PREFIX}proposals", {})["proposals"][0]["slug"]

    result = workspace_post(project, f"{PREFIX}proposals/{slug}/accept", {})
    assert result["ok"] is True
    assert (project / "_grimoire" / "overrides" / "agents" / f"{slug}.md").is_file()


def test_reject_through_the_route_marks_it_and_a_third_miss_does_not_bring_it_back(project: Path) -> None:
    record_agent_miss(project, category="infra", specialty="terraform")
    record_agent_miss(project, category="infra", specialty="terraform")
    slug = workspace_get(project, f"{PREFIX}proposals", {})["proposals"][0]["slug"]

    result = workspace_post(project, f"{PREFIX}proposals/{slug}/reject", {})
    assert result["ok"] is True

    record_agent_miss(project, category="infra", specialty="terraform")  # 3e non-choix
    payload = workspace_get(project, f"{PREFIX}proposals", {})
    assert all(p["status"] != "pending" for p in payload["proposals"])
