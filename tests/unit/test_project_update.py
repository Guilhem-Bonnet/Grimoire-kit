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


#: Ce que le nœud `backup` (mécanique, tourne même sous `--dry-run` — issue
#: #490) et le rapport `preview.md` peuvent seuls faire apparaître : jamais
#: une réécriture d'un fichier déjà là, seulement des dossiers additifs.
_UPGRADE_FLOW_OUTPUT_DIRS = frozenset({"_archive", "_grimoire-output", "_grimoire-runtime-output"})


def test_a_dry_run_only_adds_archive_and_report_dirs(project: Path) -> None:
    """L'aperçu est le flow `upgrade-flow --dry-run` (#490), pas `up --dry-run` seul :

    son premier nœud (`backup`, mécanique) écrit un tarball + un manifeste
    sous `_archive/`, et `preview` un rapport sous `_grimoire-output/` —
    tous deux additifs, jamais une réécriture d'un fichier déjà présent. Rien
    de ce qu'un projet avait avant l'aperçu ne doit changer.
    """
    before = {p.name: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    report = project_update.update_project(project, dry_run=True)
    assert report["dryRun"] is True
    assert report["output"], "l'aperçu doit rendre un compte rendu lisible"

    after_names = {p.name for p in project.iterdir()}
    assert after_names - {".git"} <= _UPGRADE_FLOW_OUTPUT_DIRS
    for name, content in before.items():
        found = next((p for p in project.rglob("*") if p.is_file() and p.name == name), None)
        assert found is not None and found.read_bytes() == content, f"{name} a été modifié par un aperçu"


def test_a_missing_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        project_update.update_project(tmp_path / "nulle-part")


def test_a_dry_run_surfaces_the_preview_report(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La réponse porte le texte du rapport, jamais un résumé reformulé (#490)."""
    from grimoire.tools.project_upgrade import run_output_dir

    preview_text = "# Aperçu\n\nRien n'a été écrit.\n"
    run_output_dir(project).joinpath("preview.md").write_text(preview_text, encoding="utf-8")

    class _Ok:
        returncode = 0
        stdout = json.dumps({"ok": True, "run_id": "r1", "done": ["backup", "preview"], "stopped_at": "orphans"})
        stderr = ""

    monkeypatch.setattr(project_update.subprocess, "run", lambda *a, **k: _Ok())
    report = project_update.update_project(project, dry_run=True)
    assert report["ok"] is True
    assert report["preview"] == preview_text
    assert report["runId"] == "r1"
    assert report["done"] == ["backup", "preview"]
    assert "report" not in report, "un aperçu n'a pas de rapport final — seul un flow complet en écrit un"
    assert "proposals" not in report


def test_a_confirmed_run_surfaces_the_final_report_and_pending_proposals(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Après confirmation, la réponse porte le rapport final et les propositions en attente."""
    from grimoire.proposals import create_manual_proposal
    from grimoire.tools.project_upgrade import run_output_dir

    report_text = "# Rapport de mise à niveau\n\nVerdict : OK\n"
    run_output_dir(project).joinpath("report.md").write_text(report_text, encoding="utf-8")
    pending = create_manual_proposal(
        project, slug="hosts-declare-enabled", specialty="hosts.enabled non déclaré",
        artifact_type="needs-hosts", carrier_reason="déclarer hosts.enabled: [claude]",
    )
    accepted = create_manual_proposal(
        project, slug="memory-link-deja-traitee", specialty="fiche déjà traitée",
        artifact_type="memory-link", target_agent="concierge",
    )
    from grimoire.proposals import reject_proposal

    reject_proposal(project, accepted.slug)

    class _Ok:
        returncode = 0
        stdout = json.dumps({
            "ok": True, "run_id": "r2",
            "done": ["backup", "preview", "orphans", "apply", "overrides", "memory", "needs-hosts", "verify"],
            "stopped_at": "destructive",
        })
        stderr = ""

    monkeypatch.setattr(project_update.subprocess, "run", lambda *a, **k: _Ok())
    report = project_update.update_project(project, dry_run=False)
    assert report["ok"] is True
    assert report["report"] == report_text
    assert report["stoppedAt"] == "destructive"
    slugs = {p["slug"] for p in report["proposals"]}
    assert slugs == {pending.slug}, "seule la proposition encore pending doit apparaître"


def test_a_confirmed_run_surfaces_the_backup_path_and_node_statuses(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #506, PR B : la réponse porte désormais `backupPath`
    (jusqu'ici jeté après avoir servi de `detail` au contrat) et un statut
    par nœud — `fait` pour le mécanique, `proposition` pour le jugement
    (V1), `checkpoint en attente` pour `destructive`."""
    class _Ok:
        returncode = 0
        stdout = json.dumps({
            "ok": True, "run_id": "r3",
            "done": ["backup", "preview", "orphans", "apply", "overrides", "memory", "needs-hosts", "verify"],
            "stopped_at": "destructive",
            "backup_path": "/tmp/projet/_archive/2026-09-14-pre-3.50.2",
            "repairs_proposed": 2,
        })
        stderr = ""

    monkeypatch.setattr(project_update.subprocess, "run", lambda *a, **k: _Ok())
    report = project_update.update_project(project, dry_run=False)
    assert report["backupPath"] == "/tmp/projet/_archive/2026-09-14-pre-3.50.2"
    assert report["repairsProposed"] == 2
    by_id = {n["id"]: n["status"] for n in report["nodes"]}
    assert by_id["backup"] == "fait"
    assert by_id["apply"] == "fait"
    assert by_id["overrides"] == "proposition"
    assert by_id["memory"] == "proposition"
    assert by_id["needs-hosts"] == "proposition"
    assert by_id["verify"] == "fait"
    assert by_id["destructive"] == "checkpoint en attente"


def test_a_dry_run_marks_unstarted_nodes_as_skipped_not_an_error(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un aperçu s'arrête après `preview`, avant même de lancer `orphans` —
    ni une erreur ni un checkpoint, juste jamais atteint."""
    class _Ok:
        returncode = 0
        stdout = json.dumps({
            "ok": True, "run_id": "r4", "done": ["backup", "preview"],
            "stopped_at": "orphans (--dry-run : arrêté après preview)",
        })
        stderr = ""

    monkeypatch.setattr(project_update.subprocess, "run", lambda *a, **k: _Ok())
    report = project_update.update_project(project, dry_run=True)
    by_id = {n["id"]: n["status"] for n in report["nodes"]}
    assert by_id["backup"] == "fait"
    assert by_id["preview"] == "fait"
    assert by_id["orphans"] == "sauté"
    assert by_id["destructive"] == "sauté"


def test_a_node_failure_marks_it_as_erreur_and_the_rest_as_skipped(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_fail_run` (cmd_upgrade_flow.py) porte désormais `done`/`failed_node`
    même sur un refus — cette réponse doit se lire comme un déroulé, pas
    seulement comme un message d'erreur."""
    class _Fail:
        returncode = 1
        stdout = json.dumps({
            "ok": False, "error": "node apply refusé : ['doctor a échoué']",
            "run_id": "r5", "done": ["backup", "preview", "orphans"], "failed_node": "apply",
        })
        stderr = ""

    monkeypatch.setattr(project_update.subprocess, "run", lambda *a, **k: _Fail())
    report = project_update.update_project(project, dry_run=False)
    assert report["ok"] is False
    by_id = {n["id"]: n["status"] for n in report["nodes"]}
    assert by_id["backup"] == "fait"
    assert by_id["orphans"] == "fait"
    assert by_id["apply"] == "erreur"
    assert by_id["overrides"] == "sauté"
    assert by_id["destructive"] == "sauté"


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


def test_a_failing_command_surfaces_the_flows_own_error(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quand `--json` a rendu un refus nommé, la réponse le porte — jamais le générique."""

    class _Fail:
        returncode = 1
        stdout = json.dumps({"ok": False, "error": "node apply : doctor a échoué"})
        stderr = ""

    monkeypatch.setattr(project_update.subprocess, "run", lambda *a, **k: _Fail())
    report = project_update.update_project(project, dry_run=False)
    assert report["ok"] is False
    assert report["error"] == "node apply : doctor a échoué"


def test_a_failed_apply_surfaces_done_state_and_backup_path(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #510 (point 3) : quand `apply` refuse après que `up` a déjà tourné,
    l'état réel est « mis à niveau, flow en échec » — jamais `done: []`/
    `stoppedAt: null` sans explication."""
    from grimoire.tools.project_upgrade import run_output_dir

    report_text = "Mis à niveau, flow en échec sur agents_referenced.\n\n(...)\n"
    run_output_dir(project).joinpath("report.md").write_text(report_text, encoding="utf-8")
    backup_path = str(project / "_archive" / "2026-09-14-pre-3.50.2" / "grimoire-state.tar.gz")

    class _Fail:
        returncode = 1
        stdout = json.dumps({
            "ok": False, "run_id": "r3",
            "done": ["backup", "preview", "orphans"],
            "stopped_at": "apply",
            "state": "upgraded-but-failed",
            "failing_checks": ["agents_referenced"],
            "backup_path": backup_path,
            "repairs_proposed": 0,
            "error": "apply refusé : doctor=[...] hook=hook rejoué sans erreur",
        })
        stderr = ""

    monkeypatch.setattr(project_update.subprocess, "run", lambda *a, **k: _Fail())
    report = project_update.update_project(project, dry_run=False)
    assert report["ok"] is False
    assert report["done"] == ["backup", "preview", "orphans"]
    assert report["stoppedAt"] == "apply"
    assert report["state"] == "upgraded-but-failed"
    assert report["backupPath"] == backup_path
    assert report["report"] == report_text
    assert report["error"] == "apply refusé : doctor=[...] hook=hook rejoué sans erreur"


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
