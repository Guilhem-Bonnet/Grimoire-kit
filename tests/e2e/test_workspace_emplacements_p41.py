"""Emplacements mémoire (P41), de bout en bout contre un vrai ``grimoire serve``.

Le chemin complet d'une requête : gardes d'écriture (``Origin``, ``Sec-Fetch-Site``),
routes ``data/actions/<id>/{plan,apply,rollback}``, journal d'actions, fichiers du projet.

- ``memory.slot`` (classe A) : attribuer puis retirer, configuration seulement, aperçu
  sans écriture, retour arrière à l'octet près ;
- ``memory.split`` (classe D) : séparer un projet d'un store partagé par ``memory split``
  (P28) — confirmation exigée, export, copie, bascule, source intacte ; Neo4j refusé.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from .conftest import _alive, _free_port, _wait_ready

_PREFIX = "/api/workspace/data/actions"


def _grimoire(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "grimoire", *args], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, timeout=180,
    )


def _seed(root: Path) -> None:
    from grimoire.core.config import GrimoireConfig
    from grimoire.memory.manager import MemoryManager

    manager = MemoryManager.from_config(GrimoireConfig.from_yaml(root / "project-context.yaml"), project_root=root)
    for entry_id, project in (("e1", "projet-emplacements"), ("e2", "projet-emplacements"), ("e3", "autre-projet")):
        manager.upsert(entry_id, f"souvenir {entry_id}", metadata={"project_name": project})


@pytest.fixture(scope="module")
def served_slots(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    root = tmp_path_factory.mktemp("emplacements") / "projet-emplacements"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    created = _grimoire(root, "init", ".", "-y", "--name", "projet-emplacements", "--backend", "local")
    if not (root / "project-context.yaml").is_file():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")
    _seed(root)
    port = _free_port()
    env = dict(os.environ, GRIMOIRE_COCKPIT_HOME=str(tmp_path_factory.mktemp("cockpit-home-slots")), NO_COLOR="1")
    process = subprocess.Popen(
        [sys.executable, "-m", "grimoire", "serve", "--project-root", str(root), "--port", str(port), "--no-open"],
        cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(port, time.monotonic() + 60)
        yield f"http://127.0.0.1:{port}", root
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert not _alive(process.pid), f"serveur survivant : pid {process.pid}"


def _token(base: str) -> str:
    """Le jeton de session, lu comme le fait la page : dans le HTML servi."""
    for page in ("/workspace/", "/"):
        with urllib.request.urlopen(base + page, timeout=30) as response:  # noqa: S310 - loopback
            found = re.search(r'name="grimoire-session" content="([^"]+)"', response.read().decode("utf-8", "replace"))
        if found:
            return found.group(1)
    raise AssertionError("jeton de session absent des pages servies")


def _post(base: str, action: str, verb: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 - loopback
        f"{base}{_PREFIX}/{action}/{verb}", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "Origin": base, "Sec-Fetch-Site": "same-origin", "X-Grimoire-Session": _token(base)},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read() or b"{}") | {"httpStatus": exc.code}


def test_attribuer_puis_retirer_est_une_action_a_apercu_et_retour_arriere(served_slots: tuple[str, Path]) -> None:
    base, root = served_slots
    config = root / "project-context.yaml"
    before = config.read_text(encoding="utf-8")
    params = {"kind": "qdrant", "location": "http://localhost:6333"}

    plan = _post(base, "memory.slot", "plan", {"params": params})
    assert plan["ok"] and plan["class"] == "A", plan
    assert config.read_text(encoding="utf-8") == before  # l'aperçu n'écrit pas
    assert any("qdrant_url" in effect for effect in plan["plan"]["effects"])

    applied = _post(base, "memory.slot", "apply", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"]})
    assert applied["ok"] and applied["rollback"], applied
    assert "qdrant_url: http://localhost:6333" in config.read_text(encoding="utf-8")
    assert (root / "_grimoire" / "_memory").is_dir()  # aucun souvenir touché
    assert _post(base, "memory.slot", "rollback", {"run_id": plan["plan_id"]})["ok"]
    assert config.read_text(encoding="utf-8") == before

    after_add = _post(base, "memory.slot", "plan", {"params": params})
    _post(base, "memory.slot", "apply", {"plan_id": after_add["plan_id"], "empreinte": after_add["empreinte"]})
    removal = _post(base, "memory.slot", "plan", {"params": {"op": "remove", "kind": "qdrant"}})
    assert removal["ok"] and any("ni copiés ni effacés" in note for note in removal["plan"]["notes"])
    done = _post(base, "memory.slot", "apply", {"plan_id": removal["plan_id"], "empreinte": removal["empreinte"]})
    assert done["ok"], done
    assert _post(base, "memory.slot", "rollback", {"run_id": removal["plan_id"]})["ok"]
    assert _post(base, "memory.slot", "rollback", {"run_id": after_add["plan_id"]})["ok"]
    assert config.read_text(encoding="utf-8") == before


def test_separer_passe_par_memory_split_en_action_d(served_slots: tuple[str, Path]) -> None:
    base, root = served_slots
    config = root / "project-context.yaml"
    before = config.read_text(encoding="utf-8")

    plan = _post(base, "memory.split", "plan", {"params": {"kind": "local"}})
    assert plan["ok"] and plan["class"] == "D", plan
    assert plan["plan"]["needsConfirmation"] is True and plan["plan"]["facts"]["count"] == 2
    assert not list((root / "_grimoire" / "_memory").glob("migration/split-*.json"))  # aperçu : rien d'écrit

    refused = _post(base, "memory.split", "apply", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"]})
    assert not refused["ok"] and "confirm" in refused["raison"]
    assert config.read_text(encoding="utf-8") == before

    done = _post(base, "memory.split", "apply", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"], "confirm": True})
    assert done["ok"], done
    export = Path(done["verification"]["export"])
    assert json.loads(export.read_text(encoding="utf-8"))["count"] == 3  # toute la source, avant la copie
    target = plan["plan"]["facts"]["target"]
    assert f"collection_prefix: {target}" in config.read_text(encoding="utf-8")
    memory_dir = root / "_grimoire" / "_memory"
    assert (memory_dir / f"{target}.json").is_file()  # copie
    assert any(p.suffix == ".json" and p.stem != target and "split-" not in p.stem for p in memory_dir.glob("*.json")), (
        "la source n'est pas supprimée : le nettoyage est un geste séparé et différé"
    )
    assert _post(base, "memory.split", "rollback", {"run_id": plan["plan_id"]})["ok"]
    assert config.read_text(encoding="utf-8") == before


def test_neo4j_est_affiche_non_separable(served_slots: tuple[str, Path]) -> None:
    base, _root = served_slots
    plan = _post(base, "memory.split", "plan", {"params": {"kind": "neo4j"}})
    assert not plan["ok"] and "non séparable" in plan["blockers"][0] and "P15" in plan["blockers"][0]
