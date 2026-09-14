"""L0 — décider une proposition depuis le cockpit, pour tout projet du registre (issue #490).

Constat du premier test réel par le cockpit (projet Terraform) : le flow
`upgrade-flow` lancé par « Mettre à jour » produit des propositions, mais
Accepter/Refuser restaient `disabled` — la garde `_HOME_SLUG` de
`workspace_post` (#356) couvrait aussi ces deux boutons, alors que
`POST /api/projects/update` (le bouton juste au-dessus, dans le même écran)
écrit déjà dans n'importe quel projet du registre. Décision de Guilhem
(option 2, issue #490) : les propositions sont « proposées puis validées,
jamais automatiques », et le cockpit est l'endroit où l'humain valide — donc
cette petite écriture-là doit ouvrir la même porte que la grosse.

Le moteur (seuil, type, accepter, refuser) est couvert par
``tests/unit/test_proposals.py`` ; le branchement pur (sans HTTP) par
``tests/unit/test_workspace_proposals.py``. Ce module est le seul des trois à
passer par ``_CockpitHandler.do_POST`` : c'est la seule couche où la garde
``_HOME_SLUG`` vit, donc la seule où sa dérogation nommée peut casser.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.hosts.decisions import record_agent_miss
from grimoire.proposals import list_proposals
from grimoire.tools import project_registry


def _get(port: int, path: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _post(
    port: int, path: str, payload: dict[str, Any] | None = None, *, host: str | None = None
) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload or {}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _project(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    (p / ".git").mkdir(parents=True)
    (p / "project-context.yaml").write_text(
        "project:\n  name: test-cockpit-proposals\n  type: webapp\n"
        "user:\n  name: Guilhem\n  language: Français\n  skill_level: expert\n"
        "agents:\n  archetype: minimal\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))


@pytest.fixture
def duo_server(tmp_path: Path) -> Any:
    """Deux projets enregistrés, aucun n'est `_HOME_SLUG` — comme la Flotte,
    ou un `?project=` explicite vers un projet qu'on ne fait que regarder."""
    alpha, beta = _project(tmp_path, "alpha"), _project(tmp_path, "beta")
    project_registry.save_registry(
        [
            {"name": "Alpha", "path": str(alpha), "slug": "alpha"},
            {"name": "Beta", "path": str(beta), "slug": "beta"},
        ]
    )
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], alpha, beta
    httpd.shutdown()
    httpd.server_close()


def _seed_pending_proposal(project: Path) -> str:
    """Deux non-choix sur la même spécialité, sans repli observé : le
    déclencheur (#395) propose un agent — le chemin le plus simple à
    accepter, sans dépendre d'un agent de repli déjà installé."""
    record_agent_miss(project, category="infra", specialty="terraform")
    record_agent_miss(project, category="infra", specialty="terraform")
    return list_proposals(project)[0].slug


def test_accept_writes_the_artifact_on_a_non_home_registry_project(duo_server: Any) -> None:
    """Le contrat de #490 : Accepter fonctionne pour Beta bien qu'il ne soit
    jamais `_HOME_SLUG` de ce process — c'est le point que ce module entier
    existe pour prouver, faute de quoi ce test échoue en 403."""
    port, _alpha, beta = duo_server
    slug = _seed_pending_proposal(beta)

    status, body = _post(port, f"/api/workspace/proposals/{slug}/accept?project=beta")
    assert status == 200
    assert body["ok"] is True
    assert (beta / "_grimoire" / "overrides" / "agents" / f"{slug}.md").is_file()


def test_reject_marks_without_writing_an_artifact(duo_server: Any) -> None:
    port, _alpha, beta = duo_server
    slug = _seed_pending_proposal(beta)

    status, body = _post(port, f"/api/workspace/proposals/{slug}/reject?project=beta")
    assert status == 200
    assert body["ok"] is True
    assert not (beta / "_grimoire" / "overrides" / "agents" / f"{slug}.md").is_file()
    assert list_proposals(beta)[0].status == "rejected"


def test_a_project_outside_the_registry_is_404(duo_server: Any) -> None:
    port, _alpha, _beta = duo_server
    status, body = _post(port, "/api/workspace/proposals/whatever/accept?project=ghost")
    assert status == 404
    assert body["ok"] is False


def test_the_write_guard_refuses_a_foreign_host_exactly_like_update(duo_server: Any) -> None:
    """Même garde transport que `/api/projects/update` (`_local_only`, contre
    le rebinding DNS) : aucune porte propre à cette route, elle passe par le
    même `do_POST` que toutes les écritures du cockpit."""
    port, _alpha, beta = duo_server
    slug = _seed_pending_proposal(beta)

    status_update, body_update = _post(
        port, "/api/projects/update", {"project": "beta"}, host="evil.example.com"
    )
    status_proposal, body_proposal = _post(
        port, f"/api/workspace/proposals/{slug}/accept?project=beta", host="evil.example.com"
    )

    assert (status_update, status_proposal) == (403, 403)
    assert body_update["error"] == body_proposal["error"] == "hôte non autorisé"


def test_accept_works_on_a_non_home_project_even_when_another_is_home(
    duo_server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le cas le plus proche du test réel du 2026-09-14 : le cockpit est
    lancé depuis Alpha (`_HOME_SLUG`), mais l'humain décide une proposition
    sur Beta, ouvert depuis la Flotte. Avant #490, cette requête recevait un
    403 — écrire depuis Alpha ne rendait pas Beta inscriptible."""
    port, _alpha, beta = duo_server
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", "alpha")
    slug = _seed_pending_proposal(beta)

    status, body = _post(port, f"/api/workspace/proposals/{slug}/accept?project=beta")
    assert status == 200
    assert body["ok"] is True


def test_other_workspace_writes_stay_home_only(duo_server: Any) -> None:
    """La dérogation de #490 est nommée, pas générale : écrire un fichier —
    ou toute autre écriture de la vue de travail — reste bloqué hors du
    projet de lancement direct, sinon la Flotte deviendrait éditable à
    distance dans son ensemble plutôt que seulement sur ses propositions."""
    port, _alpha, _beta = duo_server
    status, body = _post(
        port, "/api/workspace/file/write?project=beta", {"path": "README.md", "text": "x"}
    )
    assert status == 403
    assert body["error"] == "hôte en lecture seule"
