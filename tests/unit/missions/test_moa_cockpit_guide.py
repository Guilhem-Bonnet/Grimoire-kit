"""#1342, finitions : le parcours MOA lisible (cockpit, `cadrage status --json`, ligne du doctor).

Les helpers viennent du test de la recette (même projet jetable, même « oui » de la MOA simulé à l'écran).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.cli.doctor_moa_moe import apply_moa_moe_check, journey_verdict
from grimoire.core.cadrage import CADRAGE_DIR
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import moa_acceptance as moa
from grimoire.missions import requirement_gate as rg
from grimoire.missions.mandate import AuthorityJournal
from grimoire.missions.service import TaskService
from grimoire.tools import workspace_attention as wa

pytestmark = pytest.mark.covers("cli:cadrage status")

runner = CliRunner()

EXIGENCES = """# Exigences

## Fonctionnelles

- REQ-001 [Must] : l'utilisateur se connecte par lien magique
  - le lien expire après 15 minutes
- REQ-002 (Should) : export CSV des commandes
  - le fichier s'ouvre dans un tableur
"""
_AGENT_ENVS = ("GRIMOIRE_UNATTENDED", SESSION_ENV, *HOST_SESSION_ENVS, *AGENT_MARKER_ENVS)


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in _AGENT_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)


def _project(root: Path, *, gate: str | None = "block", moa_name: str | None = "Ada Lovelace") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"project": {"name": root.name}}
    governance: dict[str, Any] = {}
    if moa_name is not None:
        governance["roles"] = {"moa": moa_name}
    if gate is not None:
        governance["requirement_gate"] = gate
    if governance:
        data["governance"] = governance
    (root / "project-context.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    cadrage = root / CADRAGE_DIR
    cadrage.mkdir(parents=True, exist_ok=True)
    (cadrage / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    return root


def _delivered(root: Path) -> tuple[TaskService, str]:
    rg.approve_requirement(root, "REQ-001", actor="ada")
    service = TaskService(root)
    tid = service.add("connexion par lien", ("le lien fonctionne",), requirement="REQ-001").task.id
    service.ledger.close_merged_externally(tid, pr=12, merged_sha="a" * 40)
    return service, tid


def _verdict(root: Path, verdict: str, motif: str = "") -> dict[str, Any]:
    return moa.pronounce(root, "REQ-001", verdict, motif, actor="ada")


def _read(root: Path, service: TaskService) -> Any:
    read = SimpleNamespace(row={"slug": "p", "name": "p"}, items=[])
    wa._requirement_item(read, service, service.list_tasks())  # type: ignore[arg-type]
    return read


def test_moa_guide_status_json_lists_requirement_states(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _, tid = _delivered(root)  # REQ-001 livrée ; REQ-002 reste proposée
    result = runner.invoke(app, ["cadrage", "status", str(root), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["schema"] == "grimoire-cadrage-status/v1" and data["chantier"] == "principal"
    assert data["phases"] and {"phase", "state", "gate"} <= set(data["phases"][0])
    by_key = {r["requirement"]: r for r in data["requirements"]}
    assert set(by_key) == {"REQ-001", "REQ-002"}
    first = by_key["REQ-001"]
    assert (first["state"], first["chantier"], first["nature"], first["last_verdict"]) == ("livree", "principal", "", None)
    assert first["tasks"] == [{"id": tid, "title": "connexion par lien", "status": "closed", "delivered": True}]
    assert by_key["REQ-002"]["state"] == "proposee" and by_key["REQ-002"]["tasks"] == []
    assert "requirements_error" not in data


def test_moa_guide_status_json_carries_last_verdict_and_stays_read_only(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root)
    _verdict(root, "conforme")
    before = AuthorityJournal().read()
    data = json.loads(runner.invoke(app, ["cadrage", "status", str(root), "--json"]).output)
    (first,) = [r for r in data["requirements"] if r["requirement"] == "REQ-001"]
    assert first["state"] == "recettee" and first["last_verdict"]["verdict"] == "conforme"
    assert AuthorityJournal().read() == before


def test_moa_guide_status_json_not_initialized_exits_one(tmp_path: Path) -> None:
    bare = tmp_path / "nu"
    bare.mkdir()
    result = runner.invoke(app, ["cadrage", "status", str(bare), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["initialized"] is False


def test_moa_guide_attention_items_carry_stage_and_keys(tmp_path: Path) -> None:
    root = _project(tmp_path, gate="warn")
    service, _ = _delivered(root)
    unvalidated = service.add("autre chose", ("ok",), requirement="REQ-002", ready=True).task
    read = _read(root, service)
    by_kind = {i["kind"]: i for i in read.items}
    assert by_kind[wa.REQUIREMENT_RECETTE]["ref"]["stage"] == "recette"
    gap = by_kind[wa.REQUIREMENT_UNVALIDATED]["ref"]
    assert gap["stage"] == "validation" and gap["task_ids"] == [unvalidated.id]
    assert gap["requirement_keys"] == ["REQ-002"]


def test_moa_guide_attention_flags_missing_follow_up_with_exact_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, gate="warn")
    _delivered(root)

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("disque plein")

    with monkeypatch.context() as patch:
        patch.setattr(TaskService, "add", _boom)
        _verdict(root, "non-conforme", "rien ne marche")
    read = _read(root, TaskService(root))
    (suite,) = [i for i in read.items if i["ref"].get("stage") == "suite"]
    assert suite["kind"] == wa.REQUIREMENT_RECETTE and suite["ref"]["requirements"] == ["REQ-001"]
    (command,) = suite["ref"]["commands"]
    assert command.startswith("grimoire task add 'Recette non conforme REQ-001 : rien ne marche'")
    assert "--requirement REQ-001" in command
    assert not [i for i in read.items if i["ref"].get("stage") == "recette"]


def test_moa_guide_follow_up_reads_nothing_without_governance(tmp_path: Path) -> None:
    bare = tmp_path / "nu"
    bare.mkdir()
    assert moa.pending_follow_up(bare, []) == []


def test_moa_guide_doctor_summarises_the_journey(tmp_path: Path) -> None:
    from rich.console import Console

    root = _project(tmp_path)
    _delivered(root)  # REQ-001 livrée (en attente de recette), REQ-002 proposée
    verdict = journey_verdict(root)
    assert verdict is not None
    assert (verdict["proposed"], verdict["validated"], verdict["awaiting_recette"], verdict["accepted"]) == (1, 0, 1, 0)
    assert "1 proposée(s), 0 validée(s), 1 en attente de recette, 0 recettée(s)" in verdict["detail"]
    _verdict(root, "conforme")
    done = journey_verdict(root)
    assert done is not None and (done["awaiting_recette"], done["accepted"]) == (0, 1)
    results: list[dict[str, Any]] = []
    console = Console(record=True, width=200)
    apply_moa_moe_check(results, root, fmt="text", console=console)
    assert [r["name"] for r in results] == ["moa_moe", "moa_journey"]
    assert "Exigences : 1 proposée(s)" in console.export_text()


def test_moa_guide_doctor_reads_nothing_without_governance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rich.console import Console

    reads: list[str] = []
    real_journal = AuthorityJournal.read
    monkeypatch.setattr(AuthorityJournal, "read", lambda self: reads.append("journal") or real_journal(self))
    real_list = TaskService.list_tasks
    monkeypatch.setattr(TaskService, "list_tasks", lambda self: reads.append("tasks") or real_list(self))
    import grimoire.cli.doctor_moa_moe as doctor_moa

    monkeypatch.setattr(doctor_moa, "requirement_journey", lambda *_: reads.append("journey") or None)
    root = _project(tmp_path, moa_name=None, gate=None)
    results: list[dict[str, Any]] = []
    apply_moa_moe_check(results, root, fmt="text", console=Console(record=True, width=200))
    # La synthèse du parcours ne lit ni tâches ni exigences ; la lecture du journal vient du contrôle de désarmement (M3).
    assert [r["name"] for r in results] == ["moa_moe"] and "tasks" not in reads and "journey" not in reads


def test_moa_guide_doctor_is_silent_without_requirement(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "_grimoire" / "cadrage" / "04-exigences.md").write_text("# Exigences\n", encoding="utf-8")
    assert journey_verdict(root) is None


# ── Branches du parcours : états regroupés, chantiers, formats, états illisibles ──


def test_moa_guide_journey_groups_every_state_and_counts_extra_chantiers(tmp_path: Path) -> None:
    from grimoire.cli.doctor_moa_moe import requirement_journey

    root = _project(tmp_path)
    _delivered(root)  # REQ-001 livrée ; REQ-002 proposée
    rg.approve_requirement(root, "REQ-002", actor="ada")  # validée, sans tâche
    other = root / CADRAGE_DIR / "atelier"
    other.mkdir()
    (other / "04-exigences.md").write_text("# Exigences\n\n- REQ-001 [Must] : une autre exigence\n  - ok\n")
    (root / CADRAGE_DIR / "sans-exigences").mkdir()  # un dossier sans fichier d'exigences n'est pas un chantier
    counts = requirement_journey(root)
    assert counts == {"proposed": 1, "validated": 1, "awaiting_recette": 1, "accepted": 0, "to_rework": 0}
    _verdict(root, "conforme")
    counts = requirement_journey(root)
    assert counts is not None and (counts["accepted"], counts["awaiting_recette"]) == (1, 0)


def test_moa_guide_journey_reports_requirements_to_rework(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root)
    _verdict(root, "non-conforme", "le lien n'expire jamais")  # la reprise est ouverte : exigence non conforme
    verdict = journey_verdict(root)
    assert verdict is not None and verdict["to_rework"] == 1
    assert "1 à reprendre (non conforme ou suite à créer)" in verdict["detail"]


def test_moa_guide_journey_without_cadrage_folder_has_nothing_to_say(tmp_path: Path) -> None:
    from grimoire.cli.doctor_moa_moe import _chantiers

    root = _project(tmp_path)
    assert _chantiers(tmp_path / "absent") == [None]  # dossier introuvable : le seul chantier principal
    (root / CADRAGE_DIR / "04-exigences.md").write_text("# Exigences\n", encoding="utf-8")
    assert journey_verdict(root) is None


def test_moa_guide_journey_unreadable_state_gives_no_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.cli.doctor_moa_moe import requirement_journey

    root = _project(tmp_path)

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise ValueError("exigences illisibles")

    monkeypatch.setattr(moa, "requirement_views", _boom)
    assert requirement_journey(root) is None and journey_verdict(root) is None


def test_moa_guide_doctor_json_format_adds_results_without_printing(tmp_path: Path) -> None:
    from rich.console import Console

    root = _project(tmp_path)
    results: list[dict[str, Any]] = []
    console = Console(record=True, width=200)
    apply_moa_moe_check(results, root, fmt="json", console=console)
    assert [r["name"] for r in results] == ["moa_moe", "moa_journey"] and console.export_text() == ""
    assert results[1]["proposed"] == 2


def test_moa_guide_status_json_reports_unreadable_requirements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.core.exceptions import GrimoireError

    root = _project(tmp_path)

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise GrimoireError("exigences illisibles")

    monkeypatch.setattr(moa, "requirement_views", _boom)
    result = runner.invoke(app, ["cadrage", "status", str(root), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["requirements"] == [] and "exigences illisibles" in data["requirements_error"]


def test_moa_guide_cockpit_ignores_unreadable_requirement_reference() -> None:
    tasks = [
        SimpleNamespace(id="T1", extra={"requirement": "pas-une-cle"}),
        SimpleNamespace(id="T2", extra={"requirement": "REQ-004"}),
        SimpleNamespace(id="T3", extra={"requirement": "REQ-009"}),  # hors de la liste demandée
        SimpleNamespace(id="T4", extra={}),
    ]
    assert wa._cited_keys(tasks, ["T1", "T2", "T4"]) == ["REQ-004"]


def test_moa_guide_doctor_adds_no_journey_line_without_requirement(tmp_path: Path) -> None:
    from rich.console import Console

    root = _project(tmp_path)
    (root / CADRAGE_DIR / "04-exigences.md").write_text("# Exigences\n", encoding="utf-8")
    results: list[dict[str, Any]] = []
    apply_moa_moe_check(results, root, fmt="text", console=Console(record=True, width=200))
    assert [r["name"] for r in results] == ["moa_moe"]
