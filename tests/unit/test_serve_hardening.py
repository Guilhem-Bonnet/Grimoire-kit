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
from functools import partial
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


def _post(port: int, path: str, payload: dict[str, Any]) -> tuple[int, str]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — loopback de test
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


# ── 7. Le cache de l'atelier n'est pas dans la racine web du cockpit ─────────


def test_the_atelier_cache_lives_outside_the_cockpit_web_root(tmp_path: Path) -> None:
    """``cockpit serve`` publie ``serve/`` tel quel.

    Y ranger le cache de l'atelier exposerait la couche générée de chaque projet
    de la machine à ``/<slug>/data/…``, et un projet dont le slug est ``data``
    écrirait dans le dossier de données du cockpit lui-même.
    """
    cockpit_web_root = cmd_cockpit._serve_dir().resolve()
    for name in ("un-projet", "data"):
        cache = serve_data.data_dir(tmp_path / name).resolve()
        assert not cache.is_relative_to(cockpit_web_root), f"{name} : cache publié par le cockpit"


# ── 8. Le sélecteur fonctionne aussi sous le cockpit ─────────────────────────


def _cockpit_server(tmp_path: Path) -> Any:
    serve_dir = tmp_path / "serve"
    (serve_dir / "data").mkdir(parents=True)
    handler = partial(cmd_cockpit._CockpitHandler, directory=str(serve_dir))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_the_picker_is_usable_on_the_cockpit_host(tmp_path: Path) -> None:
    """Les pages Mémoire / Kanban / Observatoire portent le chrome de l'atelier
    et sont servies par le cockpit : le sélecteur s'y affiche. Ses trois entrées
    doivent y répondre, sinon deux boutons sur trois renvoient un 404.
    """
    project = tmp_path / "un-projet"
    (project / "_grimoire").mkdir(parents=True)
    httpd = _cockpit_server(tmp_path)
    port = httpd.server_address[1]
    try:
        code, body = _get(port, "/api/projects")
        assert code == 200, "liste des projets"

        code, body = _get(port, f"/api/fs/browse?path={tmp_path}")
        assert code == 200, "navigation manuelle"
        assert "un-projet" in {e["name"] for e in json.loads(body)["entries"]}

        code, body = _post(port, "/api/projects/scan", {"root": str(tmp_path), "depth": 3})
        assert code == 200, "scan"
        assert [c["name"] for c in json.loads(body)["candidates"]] == ["un-projet"]

        code, body = _post(port, "/api/projects/add", {"path": str(project)})
        assert code == 200, "enrôlement"
        assert json.loads(body)["slug"]
        assert str(project) in {p["path"] for p in reg.load_registry()}
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_cockpit_refuses_an_unknown_path(tmp_path: Path) -> None:
    httpd = _cockpit_server(tmp_path)
    port = httpd.server_address[1]
    try:
        code, _ = _post(port, "/api/projects/add", {"path": str(tmp_path / "nulle-part")})
        assert code == 404
        code, _ = _post(port, "/api/projects/scan", {"root": str(tmp_path / "absent")})
        assert code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


# ── 9. Un seul sélecteur pour les deux hôtes ────────────────────────────────

WEB = ROOT / "web"

#: Fragments que seule l'implémentation du sélecteur doit contenir. Les voir
#: ailleurs signifie qu'une seconde copie est née.
PICKER_MARKERS = ("SCANNER UNE RACINE", "PARCOURIR UN DOSSIER", "pk-scan-go", "at-proj-modal")


def test_the_picker_has_exactly_one_implementation() -> None:
    """L'atelier et le portefeuille posent la même question au même serveur.

    Deux copies de cette UI divergeraient à la première correction — c'est déjà
    ce qui était arrivé au registre de projets, dupliqué entre le cockpit et
    l'atelier.
    """
    picker = (WEB / "project-picker.js").read_text(encoding="utf-8")
    for marker in PICKER_MARKERS:
        assert marker in picker, f"{marker} devrait vivre dans project-picker.js"

    for host in ("atelier-nav.js", "portfolio.html"):
        source = (WEB / host).read_text(encoding="utf-8")
        assert "project-picker.js" in source, f"{host} devrait charger le module partagé"
        for marker in PICKER_MARKERS:
            assert marker not in source, f"{host} porte une seconde copie du sélecteur ({marker})"


def test_the_picker_does_not_reach_into_its_host() -> None:
    """Le module est chargé par deux pages très différentes : il ne doit
    connaître ni le chrome de l'atelier ni celui du portefeuille."""
    picker = (WEB / "project-picker.js").read_text(encoding="utf-8")
    for symbol in ("Atelier.", "window.Atelier", "ENRICH", "pf-card"):
        assert symbol not in picker, f"le sélecteur dépend de son hôte via {symbol}"
