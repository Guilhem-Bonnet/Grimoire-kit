"""Lot 3 — mandat, pilote et fournisseurs de n'importe quel projet enrôlé.

Le lot 1 a ouvert toute écriture de la vue de travail à la racine ENRÔLÉE que
l'hôte résout depuis ``?project=`` ; le lot 2 y a ajouté la confirmation nommée
des gestes qui touchent des fichiers ou lancent un processus. Ce lot-ci nomme
la cible des trois écritures d'AUTORITÉ : octroyer ou révoquer un mandat,
activer ou couper le pilote automatique, changer les fournisseurs. Elles
visent n'importe quel projet enrôlé, écrivent dans CE projet, et refusent un
corps qui nomme un autre projet (400) ou une URL qui n'en nomme aucun (400) /
qui n'est pas enrôlée (404). La garde de la clé de contrôle ne bouge pas :
c'est celle de ``control_key_path()``, la même vérification et les mêmes
refus que sur le projet d'ouverture.

Joué contre un vrai serveur HTTP : le 400/404 vient du transport et de la
route, jamais d'un appel direct qui contournerait la résolution d'URL.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.missions import mandate as md
from grimoire.missions.autopilot import AutopilotStore
from grimoire.missions.mandate import MandateStore
from grimoire.providers.registry import read_registry
from grimoire.tools import project_registry
from grimoire.tools.workspace_routes import PREFIX, workspace_post

from .. import _cockpit_http

GRANT = f"{PREFIX}autonomy/mandate/grant"
REVOKE = f"{PREFIX}autonomy/mandate/revoke"
AUTOPILOT = f"{PREFIX}autonomy/autopilot"
PROVIDERS = f"{PREFIX}providers"

#: Un mandat minimal valide (mêmes champs que la route attend).
MANDATE = {
    "gestures": ["accept"],
    "task_types": ["implementation"],
    "max_sessions_per_day": 2,
    "until": "+1h",
}

#: Un registre de fournisseurs minimal, pour que `disable` ait quelque chose à changer.
REGISTRY = """\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "anthropic"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["claude-sonnet-4.6"]
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: ["secrets"]
      retention_notes: ""
    fallback_order: []
    audit:
      log_prompts: false
      log_metadata: true
routing:
  default_provider: "anthropic"
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
"""

REGISTRY_PATH = Path("_grimoire/standard/llm-provider-registry.yaml")


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    (root / REGISTRY_PATH).parent.mkdir(parents=True)
    (root / REGISTRY_PATH).write_text(REGISTRY, encoding="utf-8")
    return root


def _control_key() -> str:
    return md.ensure_control_key().read_text(encoding="utf-8").strip()


def _enabled(root: Path, provider_id: str = "anthropic") -> bool:
    return next(p for p in read_registry(root) if p.id == provider_id).enabled


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La clé de contrôle et le registre vivent sous HOME : les isoler par test."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))


@pytest.fixture
def duo_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, Path, Path]]:
    """``home`` est le projet d'ouverture, ``foreign`` un second projet enrôlé."""
    home, foreign = _project(tmp_path, "home"), _project(tmp_path, "foreign")
    project_registry.save_registry(
        [
            {"name": "Home", "path": str(home.resolve()), "slug": "home"},
            {"name": "Foreign", "path": str(foreign.resolve()), "slug": "foreign"},
        ]
    )
    project_registry.set_selected_slug("home")
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", "home")
    monkeypatch.setattr(cmd_cockpit, "_HOME_PATH", home.resolve())
    cmd_cockpit._API_CACHE.clear()
    httpd = cmd_cockpit.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(cmd_cockpit._CockpitHandler, directory=str(tmp_path))
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1], home.resolve(), foreign.resolve()
    finally:
        httpd.shutdown()
        httpd.server_close()
        cmd_cockpit._API_CACHE.clear()


def _grant(port: int, slug: str, *, key: str | None = None, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    return _cockpit_http.post(
        port, f"{GRANT}?project={slug}", {**MANDATE, "project": slug, "control_key": key or _control_key(), **(body or {})}
    )


# ── Mandat : l'octroi et la révocation visent le projet nommé ───────────────


def test_un_mandat_accorde_sur_un_second_projet_s_ecrit_dans_ce_projet(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, home, foreign = duo_server

    status, body = _grant(port, "foreign")

    assert status == 200, body
    assert MandateStore(foreign).state().active is True
    assert MandateStore(home).state().active is False, "l'octroi ne doit pas retomber sur le projet d'ouverture"


def test_une_revocation_sur_un_second_projet_ne_touche_que_lui(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, home, foreign = duo_server
    assert _grant(port, "home")[0] == 200
    assert _grant(port, "foreign")[0] == 200

    status, body = _cockpit_http.post(
        port, f"{REVOKE}?project=foreign", {"reason": "lot 3", "project": "foreign"}
    )

    assert status == 200, body
    assert MandateStore(foreign).state().active is False
    assert MandateStore(home).state().active is True, "révoquer foreign ne doit pas révoquer home"


# ── Pilote : l'activation vise le projet nommé ──────────────────────────────


def test_le_pilote_active_sur_un_second_projet_s_ecrit_dans_ce_projet(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, home, foreign = duo_server
    assert _grant(port, "foreign")[0] == 200

    status, body = _cockpit_http.post(
        port,
        f"{AUTOPILOT}?project=foreign",
        {"on": True, "control_key": _control_key(), "project": "foreign"},
    )

    assert status == 200, body
    assert AutopilotStore(foreign).state().active is True
    assert AutopilotStore(home).state().active is False, "le pilote ne doit pas s'activer sur le projet d'ouverture"


# ── Fournisseurs : l'écriture vise le projet nommé ──────────────────────────


def test_les_fournisseurs_d_un_second_projet_s_ecrivent_dans_ce_projet(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, home, foreign = duo_server

    status, body = _cockpit_http.post(
        port,
        f"{PROVIDERS}?project=foreign",
        {"action": "disable", "provider_id": "anthropic", "control_key": _control_key(), "project": "foreign"},
    )

    assert status == 200, body
    assert _enabled(foreign) is False
    assert _enabled(home) is True, "le registre du projet d'ouverture ne doit pas bouger"


# ── Refus : slug non enrôlé, slug d'URL contradictoire, clé fausse ──────────


def test_un_slug_non_enrole_est_refuse(duo_server: tuple[int, Path, Path]) -> None:
    port, home, foreign = duo_server

    status, body = _grant(port, "ghost")

    assert status == 404, body
    assert MandateStore(home).state().active is False
    assert MandateStore(foreign).state().active is False


def test_un_corps_qui_nomme_un_autre_projet_est_refuse(duo_server: tuple[int, Path, Path]) -> None:
    port, home, foreign = duo_server

    status, body = _grant(port, "foreign", body={"project": "home"})

    assert status == 400, body
    assert "ne correspond pas" in body["error"]
    assert MandateStore(home).state().active is False
    assert MandateStore(foreign).state().active is False


def test_un_slug_d_url_absent_est_refuse(duo_server: tuple[int, Path, Path]) -> None:
    port, home, _foreign = duo_server

    status, body = _cockpit_http.post(port, GRANT, {**MANDATE, "control_key": _control_key()})

    assert status == 400, body
    assert MandateStore(home).state().active is False


def test_une_cle_de_controle_fausse_est_refusee_sur_le_second_projet(
    duo_server: tuple[int, Path, Path],
) -> None:
    port, home, foreign = duo_server

    status, body = _grant(port, "foreign", key="fausse")

    assert status == 403, body
    assert MandateStore(home).state().active is False
    assert MandateStore(foreign).state().active is False


@pytest.mark.parametrize("route", [GRANT, REVOKE, AUTOPILOT, PROVIDERS, f"{PREFIX}providers/set-key"])
def test_la_route_refuse_un_corps_qui_nomme_un_autre_projet(
    duo_server: tuple[int, Path, Path], route: str
) -> None:
    """Le transport compare le corps à l'URL ; la route fait de même pour
    l'appel direct (atelier, test), avec le registre seul comme résolution."""
    _port, _home, foreign = duo_server

    with pytest.raises(ValueError, match="visait"):
        workspace_post(foreign, route, {"project": "home", "control_key": _control_key()})

    assert _enabled(foreign) is True
