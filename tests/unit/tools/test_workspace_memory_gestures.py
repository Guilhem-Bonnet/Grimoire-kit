"""P34 — corriger une entrée mémoire depuis le cockpit : aperçu, application, annulation.

Chaque geste passe par ``MemoryManager`` (journal P14), montre son avant/après,
refuse une application sur une entrée qui a changé depuis l'aperçu, et se
défait depuis l'historique de l'entrée.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.config import GrimoireConfig
from grimoire.memory.journal import JOURNAL_RELPATH
from grimoire.memory.manager import MemoryManager
from grimoire.tools import workspace_memory_gestures as wmg
from grimoire.tools.workspace_knowledge import memory_entry


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project = tmp_path / "demo"
    project.mkdir()
    (project / "project-context.yaml").write_text(
        "project:\n  name: demo\n  type: library\n  stack: [python]\n"
        "user:\n  name: G\n  language: Français\n  skill_level: expert\n"
        "memory:\n  backend: local\nagents:\n  archetype: minimal\n",
        encoding="utf-8",
    )
    (project / "_grimoire" / "_memory").mkdir(parents=True)
    (project / "src").mkdir()
    (project / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    return project


def _mgr(root: Path) -> MemoryManager:
    return MemoryManager.from_config(GrimoireConfig.find_and_load(root), project_root=root)


def _store(root: Path, text: str, tags: tuple[str, ...] = (), **meta: Any) -> str:
    return _mgr(root).store(text, tags=tags, metadata={"type": "decisions", **meta}).id


def _journal(root: Path) -> list[dict[str, Any]]:
    path = root / JOURNAL_RELPATH
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _preview(root: Path, gesture: str, entry_id: str, **params: Any) -> dict[str, Any]:
    status, body = wmg.handle(root, {"gesture": gesture, "id": entry_id, "mode": "preview", "params": params})
    assert status == 200, body
    return body


def _apply(root: Path, gesture: str, entry_id: str, *, reason: str = "", version: str | None = None,
           **params: Any) -> tuple[int, dict[str, Any]]:
    if version is None:
        version = _preview(root, gesture, entry_id, **params)["version"]
    return wmg.handle(root, {
        "gesture": gesture, "id": entry_id, "mode": "apply", "params": params,
        "reason": reason, "version": version, "confirm": True,
    })


def _undo_last(root: Path, entry_id: str, reason: str = "") -> tuple[int, dict[str, Any]]:
    rows = memory_entry(root, entry_id)["entry"]["journal"]
    row = next(r for r in reversed(rows) if r["undoable"])
    return _apply(root, "undo", entry_id, reason=reason, row=row)


def test_tags_preview_then_apply_writes_journal_and_undoes(root: Path) -> None:
    eid = _store(root, "Décision : Qdrant épinglé.", tags=("qdrant",))
    body = _preview(root, "tags", eid, tags="qdrant, infra")
    assert body["changes"] == [{
        "entryId": eid, "entry": "Décision : Qdrant épinglé.", "field": "Étiquettes",
        "before": "qdrant", "after": "qdrant, infra",
    }]
    assert body["requiresReason"] is False and len(body["version"]) == 16

    status, done = _apply(root, "tags", eid, tags=["qdrant", "infra"])
    assert status == 200 and done["ok"] and done["message"] == "Étiquettes corrigées."
    assert _mgr(root).recall(eid).tags == ("qdrant", "infra")
    assert _journal(root)[-1]["op"] == "update_tags" and _journal(root)[-1]["tags_added"] == ["infra"]

    status, undone = _undo_last(root, eid)
    assert status == 200, undone
    assert _mgr(root).recall(eid).tags == ("qdrant",)


def test_unchanged_tags_are_refused(root: Path) -> None:
    eid = _store(root, "Décision.", tags=("a",))
    status, body = wmg.handle(root, {"gesture": "tags", "id": eid, "params": {"tags": ["a"]}})
    assert status == 400 and "Rien ne change" in body["error"]


def test_bad_tag_is_refused(root: Path) -> None:
    eid = _store(root, "Décision.")
    status, body = wmg.handle(root, {"gesture": "tags", "id": eid, "params": {"tags": ["x" * 65]}})
    assert status == 400 and "Étiquette refusée" in body["error"]
    status, body = wmg.handle(root, {"gesture": "tags", "id": eid, "params": {"tags": [f"t{i}" for i in range(40)]}})
    assert status == 400 and "Trop d'étiquettes" in body["error"]


def test_retract_requires_reason_and_undo_restores(root: Path) -> None:
    eid = _store(root, "Décision périmée.")
    preview = _preview(root, "retract", eid)
    assert preview["requiresReason"] is True
    assert preview["changes"][0]["before"] == "en vigueur"
    assert preview["changes"][0]["after"].startswith("retirée")

    status, body = _apply(root, "retract", eid, version=preview["version"])
    assert status == 400 and "raison est obligatoire" in body["error"]
    assert _journal(root)[-1]["op"] == "store", "un refus n'écrit rien"

    status, body = _apply(root, "retract", eid, reason="faux", version=preview["version"])
    assert status == 200, body
    assert _journal(root)[-1]["op"] == "retract" and _journal(root)[-1]["reason"] == "faux"
    status, again = wmg.handle(root, {"gesture": "retract", "id": eid})
    assert status == 400 and "déjà retirée" in again["error"]

    status, body = _undo_last(root, eid)
    assert status == 200, body
    assert _journal(root)[-1]["op"] == "unretract"
    # Annuler le rétablissement : la raison d'origine sert de raison au retrait.
    status, body = _undo_last(root, eid)
    assert status == 200, body
    assert _journal(root)[-1]["op"] == "retract" and _journal(root)[-1]["reason"] == "faux"
    status, body = _apply(root, "unretract", eid)
    assert status == 200 and body["message"] == "Entrée rendue au service."
    status, body = wmg.handle(root, {"gesture": "unretract", "id": eid})
    assert status == 400 and "non retirée" in body["error"]


def test_supersede_with_reason_then_undo(root: Path) -> None:
    old = _store(root, "Qdrant en 1.17.1.")
    new = _store(root, "Qdrant en 1.19.1.")
    preview = _preview(root, "supersede", old, by=new)
    assert preview["changes"][0]["after"] == "remplacée par « Qdrant en 1.19.1. »"
    status, body = _apply(root, "supersede", old, by=new, version=preview["version"])
    assert status == 400 and "raison" in body["error"]
    status, body = _apply(root, "supersede", old, reason="montée", by=new)
    assert status == 200, body
    row = _journal(root)[-1]
    assert (row["op"], row["before_id"], row["after_id"], row["reason"]) == ("supersede", old, new, "montée")

    # L'historique de la remplaçante porte aussi la ligne, annulable de là.
    status, body = _undo_last(root, new)
    assert status == 200, body
    assert not _mgr(root).recall(old).metadata.get("superseded_by")
    assert _journal(root)[-1]["undoes"] == "supersede"
    # Et l'annulation elle-même s'annule : l'entrée redevient remplacée.
    status, body = _undo_last(root, old)
    assert status == 200, body
    assert _mgr(root).recall(old).metadata.get("superseded_by") == new


def test_supersede_refusals(root: Path) -> None:
    a = _store(root, "A.")
    b = _store(root, "B.")
    c = _store(root, "C.")
    for params, expected in (({"by": a}, "elle-même"), ({"by": "ghost"}, "introuvable"), ({"by": ""}, "identifiant")):
        status, body = wmg.handle(root, {"gesture": "supersede", "id": a, "params": params})
        assert status in {400, 404} and expected in body["error"], body
    _apply(root, "supersede", b, reason="r", by=c)
    status, body = wmg.handle(root, {"gesture": "supersede", "id": a, "params": {"by": b}})
    assert status == 400 and "elle-même remplacée" in body["error"]
    _apply(root, "retract", c, reason="r")
    status, body = wmg.handle(root, {"gesture": "supersede", "id": a, "params": {"by": c}})
    assert status == 400 and "retirée" in body["error"]


def test_merge_updates_survivor_then_supersedes_others(root: Path) -> None:
    keep = _store(root, "Snapshots hors volume.", tags=("qdrant",))
    dup1 = _store(root, "Les snapshots sont perdus.", tags=("backup",))
    dup2 = _store(root, "Sauvegarder avant montée.", tags=("qdrant",))
    preview = _preview(root, "merge", keep, others=[dup1, dup2, keep], text="Snapshots hors volume : sauvegarder.")
    fields = [(c["entryId"], c["field"]) for c in preview["changes"]]
    assert fields == [(keep, "Texte"), (keep, "Étiquettes"), (dup1, "État"), (dup2, "État")]
    assert preview["requiresReason"] is True

    status, body = _apply(root, "merge", keep, reason="doublons", others=[dup1, dup2],
                          text="Snapshots hors volume : sauvegarder.")
    assert status == 200, body
    rows = _journal(root)[-3:]
    assert [r["op"] for r in rows] == ["update", "supersede", "supersede"]
    assert rows[0]["gesture"] == "merge" and rows[0]["merged"] == [dup1, dup2] and rows[0]["undo_ref"]
    survivor = _mgr(root).recall(keep)
    assert survivor.text == "Snapshots hors volume : sauvegarder." and survivor.tags == ("qdrant", "backup")
    assert _mgr(root).recall(dup1).metadata["superseded_by"] == keep

    status, body = _apply(root, "undo", keep, row=next(
        r for r in memory_entry(root, keep)["entry"]["journal"] if r["op"] == "update"))
    assert status == 200, body
    assert _mgr(root).recall(keep).text == "Snapshots hors volume." and _mgr(root).recall(keep).tags == ("qdrant",)


def test_merge_refusals(root: Path) -> None:
    keep = _store(root, "Garde.")
    status, body = wmg.handle(root, {"gesture": "merge", "id": keep, "params": {"others": [keep]}})
    assert status == 400 and "au moins une" in body["error"]
    status, body = wmg.handle(root, {"gesture": "merge", "id": keep, "params": {"others": [f"x{i}" for i in range(21)]}})
    assert status == 400 and "Trop" in body["error"]
    other = _store(root, "Autre.")
    status, body = wmg.handle(root, {"gesture": "merge", "id": keep, "params": {"others": [other], "text": "x" * 20_001}})
    assert status == 400 and "trop long" in body["error"]


def test_source_attach_each_kind_and_undo(root: Path) -> None:
    eid = _store(root, "Décision sourcée.")
    for params, label in (
        ({"kind": "task", "value": "GAO-x-001"}, "tâche GAO-x-001"),
        ({"kind": "session", "value": "s-2026"}, "session s-2026"),
        ({"kind": "commit", "value": "ABC1234"}, "commit abc1234"),
        ({"kind": "file", "value": "src/app.py", "line": "1"}, "fichier src/app.py:1"),
    ):
        preview = _preview(root, "source", eid, **params)
        assert preview["changes"][0]["after"] == label
        status, body = _apply(root, "source", eid, **params)
        assert status == 200 and body["message"] == f"Source rattachée : {label}."
    meta = _mgr(root).recall(eid).metadata
    assert meta["source_uri"] == "src/app.py:1" and meta["source_digest"], "le fichier est haché"
    assert _journal(root)[-1]["gesture"] == "source"

    status, body = _undo_last(root, eid)
    assert status == 200, body
    meta = _mgr(root).recall(eid).metadata
    assert meta["source_uri"] == "commit:abc1234" and not meta.get("source_digest")


@pytest.mark.parametrize(("params", "expected"), [
    ({"kind": "task", "value": "a b"}, "Identifiant refusé"),
    ({"kind": "commit", "value": "xyz"}, "Commit refusé"),
    ({"kind": "file", "value": "/etc/passwd"}, "Chemin refusé"),
    ({"kind": "file", "value": "../dehors.py"}, "introuvable"),
    ({"kind": "file", "value": "src/app.py", "line": "0"}, "Ligne refusée"),
    ({"kind": "file", "value": "src/app.py", "line": "deux"}, "Ligne refusée"),
    ({"kind": "url", "value": "x"}, "Source inconnue"),
])
def test_source_refusals(root: Path, params: dict[str, str], expected: str) -> None:
    eid = _store(root, "Décision.")
    status, body = wmg.handle(root, {"gesture": "source", "id": eid, "params": params})
    assert status == 400 and expected in body["error"]


def test_same_source_is_refused(root: Path) -> None:
    eid = _store(root, "Décision.")
    _apply(root, "source", eid, kind="file", value="src/app.py")
    status, body = wmg.handle(root, {"gesture": "source", "id": eid, "params": {"kind": "file", "value": "src/app.py"}})
    assert status == 400 and "déjà sa source" in body["error"]


def test_stale_preview_is_refused_and_recomputed(root: Path) -> None:
    eid = _store(root, "Décision.", tags=("a",))
    preview = _preview(root, "tags", eid, tags="a, b")
    _mgr(root).update_tags(eid, add=["c"])  # quelqu'un d'autre écrit entre-temps
    status, body = _apply(root, "tags", eid, version=preview["version"], tags="a, b")
    assert status == 409 and body["stale"] is True
    assert "a changé depuis l'aperçu" in body["error"]
    assert body["preview"]["changes"][0]["before"] == "a, c"
    assert body["preview"]["version"] != preview["version"]
    assert _mgr(root).recall(eid).tags == ("a", "c"), "rien n'a été appliqué"


def test_apply_requires_confirm_and_known_inputs(root: Path) -> None:
    eid = _store(root, "Décision.")
    assert wmg.handle(root, {"gesture": "tags", "id": eid, "mode": "apply"})[0] == 403
    assert wmg.handle(root, {"gesture": "nope", "id": eid})[0] == 400
    assert wmg.handle(root, {"gesture": "tags", "id": eid, "mode": "maybe"})[0] == 400
    status, body = wmg.handle(root, {"gesture": "tags", "id": "", "params": {"tags": "x"}})
    assert status == 400 and "identifiant requis" in body["error"]
    assert wmg.handle(root, {"gesture": "tags", "id": "ghost", "params": {"tags": "x"}})[0] == 404


def test_projection_and_other_project_are_refused(root: Path) -> None:
    mgr = _mgr(root)
    proj = mgr.store("def main(): ...", metadata={"projection_group": "code", "file_path": "src/app.py",
                                                  "source_kind": "projection"}).id
    status, body = wmg.handle(root, {"gesture": "tags", "id": proj, "params": {"tags": "x"}})
    assert status == 409 and "corrige la source" in body["error"]
    foreign = mgr.backend.store("Souvenir d'ailleurs", metadata={"project_name": "autre"}).id
    status, body = wmg.handle(root, {"gesture": "retract", "id": foreign})
    assert status == 403 and "autre projet" in body["error"]


def test_uninitialised_project_is_refused(tmp_path: Path) -> None:
    status, body = wmg.handle(tmp_path, {"gesture": "tags", "id": "x"})
    assert status == 400 and "non initialisé" in body["error"]


def test_undo_refusals(root: Path) -> None:
    eid = _store(root, "Décision.")
    status, body = wmg.handle(root, {"gesture": "undo", "id": eid, "params": {"row": {"ts": "0", "op": "retract"}}})
    assert status == 404 and "introuvable" in body["error"]
    store_row = memory_entry(root, eid)["entry"]["journal"][0]
    assert store_row["undoable"] is False
    status, body = wmg.handle(root, {"gesture": "undo", "id": eid, "params": {"row": store_row}})
    assert status == 400 and "ne s'annule pas" in body["error"]

    _apply(root, "source", eid, kind="commit", value="abcdef1")
    row = memory_entry(root, eid)["entry"]["journal"][-1]
    (root / wmg.UNDO_RELPATH).write_text("pas du json\n", encoding="utf-8")
    status, body = wmg.handle(root, {"gesture": "undo", "id": eid, "params": {"row": row}})
    assert status == 410 and "plus disponible" in body["error"]


def test_undo_of_superseded_row_no_longer_current(root: Path) -> None:
    a, b, c = (_store(root, t) for t in ("A.", "B.", "C."))
    _apply(root, "supersede", a, reason="r", by=b)
    row = memory_entry(root, a)["entry"]["journal"][-1]
    _mgr(root).update(a, metadata={**_mgr(root).recall(a).metadata, "superseded_by": c})
    status, body = wmg.handle(root, {"gesture": "undo", "id": a, "params": {"row": row}})
    assert status == 400 and "n'est plus remplacée" in body["error"]


def test_restore_without_change_is_refused(root: Path) -> None:
    eid = _store(root, "Décision.")
    _apply(root, "source", eid, kind="commit", value="abcdef1")
    row = memory_entry(root, eid)["entry"]["journal"][-1]
    _undo_last(root, eid)
    status, body = wmg.handle(root, {"gesture": "undo", "id": eid, "params": {"row": row}})
    assert status == 400 and "Rien à annuler" in body["error"], body.get("changes")


def test_partial_merge_is_reported(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.core.exceptions import GrimoireMemoryError

    keep = _store(root, "Garde.")
    other = _store(root, "Doublon.")

    def boom(*_a: Any, **_k: Any) -> None:
        raise GrimoireMemoryError("backend en panne")

    monkeypatch.setattr(MemoryManager, "supersede", boom)
    status, body = _apply(root, "merge", keep, reason="r", others=[other])
    assert status == 400 and "Fusion partielle : 0 entrée(s)" in body["error"] and "backend en panne" in body["error"]


def test_can_undo_rows() -> None:
    assert wmg.can_undo({"op": "update_tags", "tags_added": ["a"]})
    assert not wmg.can_undo({"op": "update_tags"})
    assert wmg.can_undo({"op": "retract", "before_id": "x"})
    assert wmg.can_undo({"op": "supersede", "before_id": "x", "after_id": "y"})
    assert wmg.can_undo({"op": "update", "undo_ref": "r"})
    assert not wmg.can_undo({"op": "update"})
    assert not wmg.can_undo({"op": "delete", "before_id": "x"})
