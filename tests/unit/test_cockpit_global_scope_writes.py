"""Une page en portée « Tous les projets » n'écrit jamais sans projet cible explicite.

Jouée contre un vrai serveur du cockpit : l'en-tête ``X-Grimoire-Scope: all`` que la page pose sur ses écritures
retire à l'hôte tout repli (projet servi, sélection partagée). Aucune garde existante n'est relâchée : jeton de
session, origine et confirmation du slug du corps restent exigés, et l'entretien du registre reste ouvert.
"""

from __future__ import annotations

import json

from grimoire.missions.service import TaskService
from grimoire.tools.request_guard import SCOPE_HEADER
from grimoire.tools.workspace_write_guard import GLOBAL_SCOPE_REFUSAL

from .test_mutation_guard import JSON, Host, cockpit  # noqa: F401

GLOBAL = {**JSON, SCOPE_HEADER: "all"}
TASK = {"title": "Une tâche", "acceptance": ["un critère"]}


def _add(host: Host, query: str, headers: dict[str, str]) -> int:
    return host.request("POST", f"/api/workspace/tasks/add{query}", json.dumps(TASK).encode(), headers)


def test_a_global_scope_write_without_a_project_is_refused_and_writes_nothing(cockpit: Host) -> None:  # noqa: F811
    before = cockpit.snapshot()
    assert _add(cockpit, "", GLOBAL) == 400
    assert cockpit.snapshot() == before
    assert any(line.get("reason") == GLOBAL_SCOPE_REFUSAL for line in cockpit.journal_lines())


def test_the_refusal_is_the_scope_one_not_the_historical_read_only(cockpit: Host) -> None:  # noqa: F811
    # Sans l'en-tête, le refus historique (403 « lecture seule ») est inchangé.
    assert _add(cockpit, "", JSON) == 403


def test_a_global_scope_write_naming_its_project_goes_through(cockpit: Host) -> None:  # noqa: F811
    assert _add(cockpit, f"?project={cockpit.slug}", GLOBAL) == 200
    assert [t.title for t in TaskService(cockpit.root).ledger.list_tasks()] == ["Une tâche"]


def test_a_global_scope_memory_write_must_name_its_project_in_the_body(cockpit: Host) -> None:  # noqa: F811
    body = json.dumps({"action": "delete", "id": "mem-1", "confirm": True}).encode()
    assert cockpit.request("POST", "/api/memory", body, GLOBAL) == 400
    named = json.dumps({"action": "delete", "id": "mem-1", "confirm": True, "project": "inconnu"}).encode()
    # Nommé mais inconnu : refusé pour sa cible, pas pour la portée (le repli n'a pas servi).
    assert cockpit.request("POST", "/api/memory", named, GLOBAL) == 400
    assert not any(line.get("reason") == GLOBAL_SCOPE_REFUSAL for line in cockpit.journal_lines())


def _scope_refusals(host: Host) -> int:
    return sum(1 for line in host.journal_lines() if line.get("reason") == GLOBAL_SCOPE_REFUSAL)


def test_blueprint_computations_are_exempt_but_blueprint_writes_are_not(cockpit: Host) -> None:  # noqa: F811
    body = json.dumps({"nodes": []}).encode()
    for suffix in ("validate", "simulate"):
        cockpit.request("POST", f"/api/blueprints/x/{suffix}", body, GLOBAL)
    assert _scope_refusals(cockpit) == 0
    assert cockpit.request("POST", "/api/blueprints/x/compile", body, GLOBAL) == 400
    assert cockpit.request("PUT", "/api/blueprints/x", body, GLOBAL) == 400
    assert _scope_refusals(cockpit) == 2


def test_portfolio_route_under_the_scope_header(cockpit: Host) -> None:  # noqa: F811
    task = TaskService(cockpit.root).add("À commenter", ("critère",), owner="guilhem", actor="t").task.id
    path = f"/api/workspace/portfolio/tasks/{task}/comment?project={cockpit.slug}"
    named = json.dumps({"text": "bonjour", "kind": "comment", "project": cockpit.slug}).encode()
    assert cockpit.request("POST", path, named, GLOBAL) == 200
    contradicting = json.dumps({"text": "x", "kind": "comment", "project": "autre"}).encode()
    assert cockpit.request("POST", path, contradicting, GLOBAL) == 400
    assert _scope_refusals(cockpit) == 0


def test_registry_maintenance_stays_open_from_a_global_scope_page(cockpit: Host) -> None:  # noqa: F811
    body = json.dumps({"slug": "inconnu", "confirm": False}).encode()
    cockpit.request("POST", "/api/projects/remove", body, GLOBAL)
    assert not any(line.get("reason") == GLOBAL_SCOPE_REFUSAL for line in cockpit.journal_lines())
