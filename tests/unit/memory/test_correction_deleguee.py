"""P42 — correction mémoire déléguée : demande → proposition → application par MemoryManager."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.correction import (
    OUTCOMES,
    acceptance_criterion,
    apply_correction,
    correction_status,
    propose_correction,
    request_correction,
)
from grimoire.memory.journal import JOURNAL_RELPATH
from grimoire.memory.manager import MemoryManager
from grimoire.missions.service import TaskService
from grimoire.proposals import accept_proposal, list_proposals
from grimoire.tools import workspace_api, workspace_routes


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def mgr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryManager:
    root = tmp_path / "proj"
    (root / "_grimoire-output").mkdir(parents=True)
    manager = MemoryManager(LocalMemoryBackend(root / "mem.json"), project_name="proj", project_root=root)
    monkeypatch.setattr("grimoire.memory.correction._open_manager", lambda _root: manager)
    return manager


def _root(mgr: MemoryManager) -> Path:
    return mgr._project_root


def _rows(root: Path) -> list[dict]:
    path = root / JOURNAL_RELPATH
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_criterion_depends_on_outcome() -> None:
    assert acceptance_criterion("replace", ["m1"]) == "l'entrée m1 est remplacée par une entrée qui porte corrects: m1"
    texts = {acceptance_criterion(o, ["m1", "m2"]) for o in OUTCOMES}
    assert len(texts) == len(OUTCOMES)
    assert "corrects: m1, m2" in acceptance_criterion("merge", ["m1", "m2"])
    with pytest.raises(ValueError):
        acceptance_criterion("autre", ["m1"])


def test_e2e_request_propose_accept(mgr: MemoryManager) -> None:
    root = _root(mgr)
    old = mgr.store("Le backend retenu est Weaviate.")
    asked = request_correction(root, entry_ids=[old.id], outcome="replace", description="Faux : c'est Qdrant.", manager=mgr)

    # La demande crée une tâche avec description et critère généré.
    task = next(t for t in TaskService(root).list() if t.id == asked["task_id"]) if hasattr(TaskService, "list") else None
    assert asked["criterion"] == f"l'entrée {old.id} est remplacée par une entrée qui porte corrects: {old.id}"
    if task is not None:
        assert asked["criterion"] in task.acceptance

    # L'agent propose : la mémoire ne bouge pas.
    before = [e.id for e in mgr.get_all()]
    propose_correction(root, asked["task_id"], action="replace", text="Le backend retenu est Qdrant.", proposed_by="agent-x")
    assert [e.id for e in mgr.get_all()] == before
    pending = [p for p in list_proposals(root) if p.artifact_type == "memory-correction"]
    assert len(pending) == 1 and pending[0].status == "pending"
    view = workspace_api.proposals_view(root)
    row = next(r for r in view["proposals"] if r["artifact_type"] == "memory-correction")
    assert row["correction"]["stale"] is False
    assert row["correction"]["preview"]["current"][old.id] == "Le backend retenu est Weaviate."

    # Accepter applique par MemoryManager.
    result = accept_proposal(root, pending[0].slug)
    assert result["ok"], result
    created = mgr.recall(result["created"])
    assert created is not None and created.metadata["corrects"] == old.id
    assert mgr.recall(old.id).metadata["superseded_by"] == created.id
    sup = next(r for r in _rows(root) if r["op"] == "supersede")
    assert sup["proposed_by"] == "agent-x" and sup["applied_by"] == "cockpit"


def test_stale_proposal_is_refused_and_flagged(mgr: MemoryManager) -> None:
    root = _root(mgr)
    old = mgr.store("Version initiale.")
    asked = request_correction(root, entry_ids=[old.id], outcome="replace", manager=mgr)
    propose_correction(root, asked["task_id"], action="replace", text="Version corrigée.", proposed_by="a")
    mgr.update(old.id, text="Quelqu'un l'a déjà modifiée.")

    status = correction_status(root, asked["task_id"], manager=mgr)
    assert status["stale"] and status["stale_ids"] == [old.id]
    assert status["preview"]["current"][old.id] == "Quelqu'un l'a déjà modifiée."
    with pytest.raises(GrimoireMemoryError, match="périmée"):
        apply_correction(root, asked["task_id"], applied_by="cockpit", manager=mgr)
    slug = f"correction-{asked['task_id'].lower()}"
    assert accept_proposal(root, slug)["ok"] is False


def test_merge_and_attach_source(mgr: MemoryManager) -> None:
    root = _root(mgr)
    a, b = mgr.store("Fait A."), mgr.store("Fait A bis.")
    merge = request_correction(root, entry_ids=[a.id, b.id], outcome="merge", manager=mgr)
    propose_correction(root, merge["task_id"], action="merge", text="Fait A (fusionné).", proposed_by="a")
    out = apply_correction(root, merge["task_id"], applied_by="cockpit", manager=mgr)
    assert mgr.recall(out["created"]).metadata["corrects"] == f"{a.id},{b.id}"

    c = mgr.store("Fait C.")
    src = request_correction(root, entry_ids=[c.id], outcome="attach_source", manager=mgr)
    propose_correction(root, src["task_id"], action="attach_source", source="docs/c.md:3", proposed_by="a")
    apply_correction(root, src["task_id"], applied_by="cockpit", manager=mgr)
    assert mgr.recall(c.id).metadata["source"] == "docs/c.md:3"


def test_route_requires_entries(mgr: MemoryManager) -> None:
    with pytest.raises(ValueError):
        workspace_routes._memory_correct(_root(mgr), {"outcome": "replace"})
