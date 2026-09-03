"""Mettre à jour un projet depuis l'UI écrit dans le dépôt de quelqu'un.

L'aperçu est donc le défaut, et l'alignement effectif demande un accord
explicite — sur les deux hôtes. Un bouton qui réécrit un projet sur un clic mal
placé n'est pas un cockpit.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.tools import forge_server, project_update
from grimoire.tools import project_registry as reg
from grimoire.tools.forge_server import ForgeAPI, make_handler

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    (root / ".git").mkdir(parents=True)
    return root


def _post(port: int, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — loopback de test
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ── La commande ──────────────────────────────────────────────────────────────


def test_a_dry_run_writes_nothing(project: Path) -> None:
    before = {p.name for p in project.iterdir()}
    report = project_update.update_project(project, dry_run=True)
    assert report["dryRun"] is True
    assert report["output"], "l'aperçu doit rendre un compte rendu lisible"
    assert {p.name for p in project.iterdir()} == before


def test_a_missing_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        project_update.update_project(tmp_path / "nulle-part")


def test_a_failing_command_is_reported_not_raised(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un projet qui refuse de s'aligner est un résultat à afficher, pas une
    panne du serveur."""

    class _Fail:
        returncode = 2
        stdout = ""
        stderr = "refus net"

    monkeypatch.setattr(project_update.subprocess, "run", lambda *a, **k: _Fail())
    report = project_update.update_project(project)
    assert report["ok"] is False
    assert report["error"]
    assert "refus net" in report["output"]


def test_a_timeout_is_reported(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise project_update.subprocess.TimeoutExpired(cmd="grimoire up", timeout=1)

    monkeypatch.setattr(project_update.subprocess, "run", _boom)
    report = project_update.update_project(project)
    assert report["ok"] is False
    assert "délai" in str(report["error"])


# ── L'atelier ────────────────────────────────────────────────────────────────


@pytest.fixture
def atelier(project: Path) -> Any:
    api = ForgeAPI(project, ROOT, None)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_the_atelier_previews_unless_told_otherwise(atelier: int) -> None:
    code, body = _post(atelier, "/api/projects/update", {})
    assert code == 200
    assert body["dryRun"] is True, "sans accord explicite, on n'écrit pas"


def test_the_atelier_writes_only_on_explicit_confirmation(
    atelier: int, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le serveur importe le symbole : c'est là qu'il faut le remplacer."""
    seen: list[bool] = []

    def _spy(root: Path, *, dry_run: bool = True) -> dict[str, Any]:
        seen.append(dry_run)
        return {"ok": True, "dryRun": dry_run, "path": str(root), "code": 0,
                "output": "", "error": None}

    monkeypatch.setattr(forge_server, "update_project", _spy)
    _post(atelier, "/api/projects/update", {})
    _post(atelier, "/api/projects/update", {"confirm": True})
    assert seen == [True, False]


def test_a_real_update_is_journalled(
    atelier: int, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Écrire dans un dépôt laisse une trace ; le simple aperçu, non."""
    monkeypatch.setattr(
        forge_server, "update_project",
        lambda root, *, dry_run=True: {"ok": True, "dryRun": dry_run, "path": str(root),
                                       "code": 0, "output": "", "error": None},
    )
    ledger = project / "_grimoire-runtime-output" / "hook-runtime" / "serve-mutations.jsonl"

    _post(atelier, "/api/projects/update", {})
    assert not ledger.exists(), "un aperçu n'écrit pas dans le projet"

    _post(atelier, "/api/projects/update", {"confirm": True})
    assert ledger.is_file()
    assert "project.update" in ledger.read_text(encoding="utf-8")


# ── Le cockpit ───────────────────────────────────────────────────────────────


@pytest.fixture
def cockpit(tmp_path: Path) -> Any:
    serve_dir = tmp_path / "serve"
    (serve_dir / "data").mkdir(parents=True)
    handler = partial(cmd_cockpit._CockpitHandler, directory=str(serve_dir))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_the_cockpit_refuses_an_unknown_project(cockpit: int) -> None:
    code, body = _post(cockpit, "/api/projects/update", {"project": "jamais-vu"})
    assert code == 404
    assert body["ok"] is False


def test_the_cockpit_previews_a_registered_project(cockpit: int, project: Path) -> None:
    slug = reg.register_project(project)
    code, body = _post(cockpit, "/api/projects/update", {"project": slug})
    assert code == 200
    assert body["dryRun"] is True
    assert body["path"] == str(project.resolve())


# ── La cible : l'UI est partagée, le serveur ne l'est pas ───────────────────


def test_the_atelier_updates_the_project_it_was_asked_for(
    atelier: int, project: Path, tmp_path: Path
) -> None:
    """Le défaut le plus grave de cette branche, trouvé en relecture.

    Le portefeuille liste TOUS les projets de la machine et il est servi par
    l'atelier comme par le cockpit. La route ignorait la cible demandée :
    cliquer « mettre à jour » sur un projet lançait `grimoire up` dans le dépôt
    servi. Avec confirmation, cela écrivait dans le mauvais dépôt.
    """
    other = tmp_path / "autre"
    (other / ".git").mkdir(parents=True)
    slug = reg.register_project(other)

    _, body = _post(atelier, "/api/projects/update", {"project": slug})
    assert body["path"] == str(other.resolve()), "l'atelier a traité un autre projet"

    _, served = _post(atelier, "/api/projects/update", {})
    assert served["path"] == str(project.resolve()), "sans cible, le projet servi"


def test_an_unknown_target_is_refused_not_silently_redirected(atelier: int) -> None:
    """Se replier sur le projet servi ferait écrire ailleurs sans le dire."""
    code, _ = _post(atelier, "/api/projects/update", {"project": "jamais-vu"})
    assert code == 404


def test_a_path_target_must_exist(atelier: int, tmp_path: Path) -> None:
    code, _ = _post(atelier, "/api/projects/update", {"path": str(tmp_path / "nulle-part")})
    assert code == 404


def test_two_updates_of_the_same_project_do_not_overlap(project: Path) -> None:
    """`grimoire up` est idempotente, pas réentrante.

    Le serveur est multi-thread et le bouton est cliquable : deux exécutions
    concurrentes écriraient les mêmes fichiers en même temps.
    """
    entered = threading.Event()
    release = threading.Event()
    second: dict[str, Any] = {}

    def _slow(*_a: object, **_k: object) -> Any:
        entered.set()
        release.wait(timeout=5)

        class _Ok:
            returncode = 0
            stdout = "fini"
            stderr = ""

        return _Ok()

    import unittest.mock as _mock

    with _mock.patch.object(project_update.subprocess, "run", _slow):
        first = threading.Thread(target=lambda: project_update.update_project(project))
        first.start()
        assert entered.wait(timeout=5)
        second.update(project_update.update_project(project))
        release.set()
        first.join(timeout=10)

    assert second["ok"] is False
    assert "déjà en cours" in str(second["error"])


def test_a_process_that_never_starts_is_reported_not_raised(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interpréteur absent, descripteurs épuisés : un échec à afficher.

    Laisser remonter l'``OSError`` donnait un 500 sans explication à une UI qui
    attend un compte rendu — et le handler HTTP ne l'attrapait pas.
    """

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("cassé")

    monkeypatch.setattr(project_update.subprocess, "run", _boom)
    report = project_update.update_project(project)
    assert report["ok"] is False
    assert "lancement impossible" in str(report["error"])


def test_the_lock_is_released_after_a_failure(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un verrou gardé après une erreur bloquerait le projet pour toujours."""
    calls: list[int] = []

    def _boom(*_a: object, **_k: object) -> None:
        calls.append(1)
        raise OSError("cassé")

    monkeypatch.setattr(project_update.subprocess, "run", _boom)
    project_update.update_project(project)
    project_update.update_project(project)
    assert len(calls) == 2, "le second appel n'a pas pu prendre le verrou"
