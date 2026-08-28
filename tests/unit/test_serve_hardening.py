"""Ce que la relecture du correctif anti-démo a trouvé de cassé.

Quatre trous : la démo déjà semée sur un poste existant, deux projets homonymes
qui partageaient leur cache, une profondeur de scan venue du réseau sans
plafond, et un navigateur de fichiers exposé aux lectures cross-origin par
rebinding DNS. Chacun de ces tests échoue si l'on retire son correctif — ils
décrivent ce qui était cassé, pas le comportement obtenu.

S'y ajoutent le classement exhaustif des couches embarquées et le cycle de vie
de la génération, deux surfaces qu'aucun test ne couvrait.
"""

from __future__ import annotations

import json
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.data import web_path
from grimoire.tools import project_registry as reg
from grimoire.tools import serve_data
from grimoire.tools.forge_server import ForgeAPI, make_handler

ROOT = Path(__file__).resolve().parents[2]


# ── 1. Mise à jour : la démo déjà semée sur le disque ────────────────────────


def test_sync_purges_a_vitrine_snapshot_left_by_an_earlier_version(tmp_path: Path) -> None:
    """Ne plus amorcer ne suffit pas : il faut retirer ce qui l'a été.

    Sur un poste qui avait déjà lancé le cockpit, « Atlas Ops » et les 141
    entrées mémoire d'un autre dépôt sont sur le disque et continueraient
    d'être servis après la mise à jour.
    """
    serve = tmp_path / "serve"
    (serve / "data").mkdir(parents=True)
    shutil.copytree(web_path() / "data", serve / "data", dirs_exist_ok=True)
    assert (serve / "data" / "projects" / "atlas-ops").is_dir(), "prémisse : la démo est là"

    cmd_cockpit._sync_site(serve)

    assert not (serve / "data" / "projects.json").exists()
    assert not (serve / "data" / "memory.json").exists()
    assert not (serve / "data" / "projects").exists()
    assert (serve / "data" / "catalogue-export.json").is_file(), "référence du kit conservée"


def test_sync_never_purges_generated_data(tmp_path: Path) -> None:
    """Le critère est l'octet près : une couche réelle diffère toujours du bundle."""
    serve = tmp_path / "serve"
    (serve / "data").mkdir(parents=True)
    shutil.copytree(web_path() / "data", serve / "data", dirs_exist_ok=True)
    real = serve / "data" / "memory.json"
    real.write_text('{"store": {"total_entries": 7}}', encoding="utf-8")

    cmd_cockpit._sync_site(serve)

    assert json.loads(real.read_text(encoding="utf-8"))["store"]["total_entries"] == 7


# ── 2. Collision de cache entre projets homonymes ────────────────────────────


def test_two_unregistered_projects_with_the_same_name_do_not_share_a_cache(
    tmp_path: Path,
) -> None:
    """``client-a/web`` et ``client-b/web`` : sans empreinte, le second lisait
    les chiffres du premier — le défaut même que ce module ferme."""
    a = tmp_path / "client-a" / "web"
    b = tmp_path / "client-b" / "web"
    a.mkdir(parents=True)
    b.mkdir(parents=True)

    assert not reg.looks_grimoire(a), "prémisse : aucun marqueur, donc pas d'entrée au registre"
    assert serve_data.data_dir(a) != serve_data.data_dir(b)


def test_a_registered_project_keeps_its_registry_slug(tmp_path: Path) -> None:
    """L'empreinte ne doit pas rendre le dossier de cache illisible quand le
    registre a déjà tranché l'unicité."""
    project = tmp_path / "mon-app"
    (project / "_grimoire").mkdir(parents=True)
    slug = reg.register_project(project)
    assert serve_data.project_slug(project) == slug


# ── 3. Profondeur de scan venue d'une requête ────────────────────────────────


def test_scan_depth_is_capped(tmp_path: Path) -> None:
    """La profondeur vient du réseau : un scan de ``/`` à profondeur 999
    immobiliserait un thread du serveur."""
    assert reg.scan_payload(tmp_path, 999)["depth"] == reg.MAX_SCAN_DEPTH
    assert reg.scan_payload(tmp_path, 0)["depth"] >= 1
    assert reg.scan_payload(tmp_path, -5)["depth"] >= 1


# ── 4. DNS-rebinding sur les lectures ────────────────────────────────────────


@pytest.fixture
def server(tmp_path: Path) -> Any:
    project = tmp_path / "servi"
    (project / "_grimoire").mkdir(parents=True)
    api = ForgeAPI(project, ROOT, ROOT / "web")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _get(port: int, path: str, host: str | None = None) -> tuple[int, str]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — loopback de test
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_reads_refuse_a_foreign_host(server: int) -> None:
    """Le rebinding DNS rend la page attaquante same-origin : CORS ne protège
    plus la lecture. Un ``GET`` qui liste des dossiers devient un oracle sur la
    machine — le garde d'hôte doit couvrir les lectures, pas seulement les
    mutations."""
    for path in ("/api/fs/browse", "/api/status", "/api/projects"):
        code, body = _get(server, path, host="evil.example.com")
        assert code == 403, f"{path} a répondu {code} à un Host étranger"
        assert "hôte non autorisé" in body


def test_reads_still_answer_the_loopback(server: int) -> None:
    """Le garde ne doit pas casser l'UI légitime."""
    for path in ("/api/status", "/api/projects", "/api/fs/browse", "/api/data/status"):
        code, _ = _get(server, path)
        assert code == 200, f"{path} refusé en loopback"


def test_the_new_routes_are_actually_wired(server: int) -> None:
    """Les autres tests appellent ``ForgeAPI`` directement : sans celui-ci, un
    renommage de route passerait au vert."""
    code, body = _get(server, "/api/projects")
    assert code == 200
    assert "served" in json.loads(body)
    code, body = _get(server, "/api/data/status")
    assert json.loads(body)["state"] in {"idle", "generating", "ready", "failed"}


# ── 5. Classement exhaustif des couches embarquées ───────────────────────────


def test_every_bundled_data_layer_is_classified() -> None:
    """Le partage projet/kit est écrit à deux endroits — ils doivent couvrir tout.

    Une couche ajoutée à ``web/data/`` sans être classée retomberait par défaut
    du côté « référence du kit » et serait servie telle quelle : c'est
    exactement par là que l'instantané de la vitrine passait.
    """
    bundled = {p.name for p in (ROOT / "web" / "data").glob("*.json")}
    classified = (
        set(serve_data.PROJECT_LAYERS)
        | {serve_data.REGISTRY_LAYER}
        | set(cmd_cockpit._KIT_DATA_LAYERS)
    )
    assert bundled - classified == set(), "couche embarquée non classée"


# ── 6. Cycle de vie de la génération ─────────────────────────────────────────


def test_a_failed_generation_is_reported_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un état « prête » alors que la génération a échoué ferait lire une couche
    périmée en croyant qu'elle est à jour."""
    project = tmp_path / "projet"
    (project / "_grimoire").mkdir(parents=True)
    layer = serve_data.DataLayer(project)

    def _boom(_root: Path) -> Path:
        msg = "générateur cassé"
        raise OSError(msg)

    monkeypatch.setattr(layer, "generate_sync", _boom)
    layer._generate(project)

    status = layer.status()
    assert status["state"] == "failed"
    assert "générateur cassé" in str(status["error"])


def test_a_generation_started_for_another_project_is_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changer de projet pendant une génération ne doit pas marquer « prête » la
    couche du nouveau projet avec le résultat de l'ancien."""
    first = tmp_path / "premier"
    second = tmp_path / "second"
    for p in (first, second):
        (p / "_grimoire").mkdir(parents=True)

    layer = serve_data.DataLayer(first)
    monkeypatch.setattr(layer, "generate_sync", lambda root: serve_data.data_dir(root))
    layer.retarget(second)
    layer._generate(first)  # le thread de l'ancienne racine termine après coup

    assert layer.status()["state"] == "idle", "résultat périmé appliqué au nouveau projet"


def test_refresh_does_not_stack_generations(tmp_path: Path) -> None:
    project = tmp_path / "projet"
    (project / "_grimoire").mkdir(parents=True)
    layer = serve_data.DataLayer(project)

    started = threading.Event()
    release = threading.Event()

    def _slow(_root: Path) -> Path:
        started.set()
        release.wait(timeout=5)
        return serve_data.data_dir(_root)

    layer.generate_sync = _slow  # type: ignore[method-assign]
    assert layer.refresh()["started"] is True
    assert started.wait(timeout=5)
    assert layer.refresh()["started"] is False
    release.set()


def test_refresh_route_is_wired(server: int) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{server}/api/data/refresh", data=b"{}", method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — loopback de test
        payload = json.loads(resp.read().decode("utf-8"))
    assert "started" in payload
    assert payload["state"] in {"generating", "ready", "failed", "idle"}
