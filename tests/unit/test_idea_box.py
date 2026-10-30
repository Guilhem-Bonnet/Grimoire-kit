"""I13 : la boîte à idées — journal, CLI, promotion, routes du cockpit."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions.ideas import IDEA_MAX_CHARS, IdeaError, IdeaStore
from grimoire.missions.service import TaskService
from grimoire.tools.workspace_routes import GET_ROUTES, POST_ROUTES, PREFIX, workspace_get, workspace_post

runner = CliRunner()
IDEE = "Corriger l'export CSV et ajouter un test"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


def _cli(root: Path, *args: str):
    return runner.invoke(app, ["idea", *args, "--project-root", str(root)])


def _journal(root: Path) -> Path:
    return root / "_grimoire-runtime-output" / "ledger" / "ideas.jsonl"


def test_add_deux_fois_puis_list_json_dans_l_ordre(tmp_path: Path) -> None:
    assert _cli(tmp_path, "add", "première").exit_code == 0
    assert _cli(tmp_path, "add", "deuxième").exit_code == 0
    listed = _cli(tmp_path, "list", "--json")
    ideas = json.loads(listed.output)
    assert [(i["text"], i["state"], i["source"]) for i in ideas] == [
        ("première", "open", "cli"),
        ("deuxième", "open", "cli"),
    ]
    assert ideas[0]["id"] != ideas[1]["id"] and ideas[0]["tasks"] == []


def test_drop_passe_une_idee_a_dropped(tmp_path: Path) -> None:
    _cli(tmp_path, "add", "a")
    _cli(tmp_path, "add", "b")
    first = json.loads(_cli(tmp_path, "list", "--json").output)[0]["id"]
    assert _cli(tmp_path, "drop", first).exit_code == 0
    states = [i["state"] for i in json.loads(_cli(tmp_path, "list", "--json").output)]
    assert states == ["dropped", "open"]
    assert _cli(tmp_path, "drop", first).exit_code == 1  # déjà écartée
    assert _cli(tmp_path, "drop", "IDEA-9999").exit_code == 1


def test_une_idee_vide_ou_trop_longue_est_refusee(tmp_path: Path) -> None:
    store = IdeaStore(tmp_path)
    with pytest.raises(IdeaError):
        store.add("   ")
    with pytest.raises(IdeaError):
        store.add("x" * (IDEA_MAX_CHARS + 1))
    assert not _journal(tmp_path).exists()


def _writer(root: str, tag: str) -> None:
    store = IdeaStore(Path(root))
    for n in range(50):
        store.add(f"{tag}-{n}")


def test_deux_processus_en_parallele_ne_perdent_aucune_ligne(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(str(tmp_path), tag)) for tag in ("a", "b")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0
    lines = _journal(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100
    ids = [json.loads(line)["id"] for line in lines]
    assert len(set(ids)) == 100
    assert len(IdeaStore(tmp_path).list()) == 100


def test_promote_cree_les_taches_marque_promoted_et_refuse_la_seconde(tmp_path: Path) -> None:
    idea = IdeaStore(tmp_path).add(IDEE)
    done = _cli(tmp_path, "promote", idea.id, "--json")
    assert done.exit_code == 0, done.output
    body = json.loads(done.output)
    created = [c["id"] for c in body["created"]]
    assert created and body["idea"]["state"] == "promoted" and body["idea"]["tasks"] == created
    stored = IdeaStore(tmp_path).get(idea.id)
    assert stored.state == "promoted" and stored.tasks == created
    assert set(created) <= {t.id for t in TaskService(tmp_path).list_tasks()}
    before = _journal(tmp_path).read_text(encoding="utf-8")
    again = _cli(tmp_path, "promote", idea.id)
    assert again.exit_code == 1 and "promoted" in again.output
    assert _journal(tmp_path).read_text(encoding="utf-8") == before


# ── cockpit ─────────────────────────────────────────────────────────────────


def test_les_routes_sont_branchees() -> None:
    for name in ("add", "drop", "promote"):
        assert f"{PREFIX}ideas/{name}" in POST_ROUTES
    assert f"{PREFIX}ideas" in GET_ROUTES


def test_routes_add_drop_undo_n_ajoutent_que_des_evenements(tmp_path: Path) -> None:
    added = workspace_post(tmp_path, f"{PREFIX}ideas/add", {"text": "garder"})
    assert added["source"] == "cockpit"
    workspace_post(tmp_path, f"{PREFIX}ideas/drop", {"id": added["id"]})
    assert workspace_get(tmp_path, f"{PREFIX}ideas", {})["ideas"][0]["state"] == "dropped"
    workspace_post(tmp_path, f"{PREFIX}ideas/drop", {"id": added["id"], "undo": True})
    assert workspace_get(tmp_path, f"{PREFIX}ideas", {})["ideas"][0]["state"] == "open"
    events = [json.loads(x)["event"] for x in _journal(tmp_path).read_text(encoding="utf-8").splitlines()]
    assert events == ["add", "drop", "restore"]


def test_route_promote_avec_taches_marque_seulement(tmp_path: Path) -> None:
    added = workspace_post(tmp_path, f"{PREFIX}ideas/add", {"text": "garder"})
    out = workspace_post(tmp_path, f"{PREFIX}ideas/promote", {"id": added["id"], "tasks": ["T-1"]})
    assert out["idea"]["state"] == "promoted" and out["idea"]["tasks"] == ["T-1"] and out["created"] == []
    with pytest.raises(ValueError):
        workspace_post(tmp_path, f"{PREFIX}ideas/promote", {"id": added["id"], "tasks": ["T-2"]})
