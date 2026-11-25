"""#1342, lot M4 : la recette de la MOA, prononcée contre l'exigence validée, scellée, écarts renvoyés en MOE.

Chaque test porte ``moa_acceptance`` dans son nom : ``pytest -k moa_acceptance`` les sélectionne tous.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.cadrage import CADRAGE_DIR
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import moa_acceptance as moa
from grimoire.missions import requirement_gate as rg
from grimoire.missions.mandate import AuthorityJournal, acting_as_orchestrator
from grimoire.missions.schemas import ORIGIN_AGENT, ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskService

pytestmark = pytest.mark.covers("cli:cadrage recette")

runner = CliRunner()

EXIGENCES = """# Exigences

## Fonctionnelles

- REQ-001 [Must] : l'utilisateur se connecte par lien magique
  - le lien expire après 15 minutes
- REQ-002 (Should) : export CSV des commandes
  - le fichier s'ouvre dans un tableur
"""

_REAL_CONFIRM = rg._confirm_human_turn
_AGENT_ENVS = ("GRIMOIRE_UNATTENDED", SESSION_ENV, *HOST_SESSION_ENVS, *AGENT_MARKER_ENVS)


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un HOME par test, sans signal d'agent hérité, et un « oui » de la MOA simulé à l'écran (le même point que M3)."""
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


def _approve(root: Path, key: str = "REQ-001") -> None:
    rg.approve_requirement(root, key, actor="ada")


def _task(service: TaskService, key: str = "REQ-001", *, nature: str = "", title: str = "connexion par lien") -> str:
    return service.add(title, ("le lien fonctionne",), requirement=key, nature=nature).task.id


def _deliver(service: TaskService, task_id: str, pr: int = 12) -> None:
    service.ledger.close_merged_externally(task_id, pr=pr, merged_sha="a" * 40)


def _delivered(root: Path, key: str = "REQ-001", *, nature: str = "") -> tuple[TaskService, str]:
    _approve(root, key)
    service = TaskService(root)
    tid = _task(service, key, nature=nature)
    _deliver(service, tid)
    return service, tid


def _verdict(root: Path, verdict: str, motif: str = "", key: str = "REQ-001") -> dict[str, Any]:
    return moa.pronounce(root, key, verdict, motif, actor="ada")


def _state(root: Path, key: str = "REQ-001") -> str:
    return moa.requirement_view(root, key, service=TaskService(root)).state


def _recettes() -> list[dict[str, Any]]:
    return [e for e in AuthorityJournal().read() if e.get("type") == moa.ACCEPTED_ENTRY]


# ── La présentation ─────────────────────────────────────────────────────────


def test_moa_acceptance_view_presents_requirement_criterion_tasks_and_proofs(tmp_path: Path) -> None:
    from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile
    from grimoire.evidence.service import EvidenceService

    root = _project(tmp_path)
    service, tid = _delivered(root, nature="anomalie")
    evidence = EvidenceService(root / "_grimoire-runtime-output" / "evidence")
    pack = evidence.create_pack(tid, EvidenceProfile.STANDARD, [EvidenceItem("i1", next(iter(EvidenceKind)), "x", "d")])
    evidence.verify(pack)
    view = moa.requirement_view(root, "req-001", service=service)
    assert (view.key, view.validated, view.state) == ("REQ-001", True, "livree")
    assert view.criteria == ("le lien expire après 15 minutes",)
    assert view.nature == "anomalie" and moa.acceptance_criterion(view.nature) == "le cas ne se reproduit plus"
    (task,) = view.tasks
    assert task.delivered and task.pack == pack.id and task.verdict
    assert task.merged.startswith("PR #12 fusionnée à aaaaaaaaaaaa")
    data = view.to_dict()
    assert (
        data["acceptance"] == "le cas ne se reproduit plus" and data["state_label"] == "livrée, en attente de recette"
    )
    assert data["last_verdict"] is None


def test_moa_acceptance_nature_from_interview_or_undeclared(tmp_path: Path) -> None:
    root = _project(tmp_path)
    service, _ = _delivered(root)
    assert moa.requirement_view(root, "REQ-001", service=service).nature == ""
    assert "nature non déclarée" in moa.acceptance_criterion("")
    (root / CADRAGE_DIR / "entretien.json").write_text(json.dumps({"depth": "standard", "kind": "migration"}))
    view = moa.requirement_view(root, "REQ-001", service=service)
    assert (
        view.nature == "migration" and moa.acceptance_criterion(view.nature) == "bascule faite, retour arrière prouvé"
    )
    (root / CADRAGE_DIR / "entretien.json").write_text("pas du json")
    assert moa.requirement_view(root, "REQ-001", service=service).nature == ""


def test_moa_acceptance_cli_json_is_read_only(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root)
    result = runner.invoke(app, ["cadrage", "recette", "REQ-001", "--path", str(root), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["state"] == "livree" and data["tasks"][0]["delivered"] is True
    assert _recettes() == []
    refused = runner.invoke(
        app, ["cadrage", "recette", "REQ-001", "--path", str(root), "--json", "--verdict", "conforme"]
    )
    assert refused.exit_code == 1 and _recettes() == []


def test_moa_acceptance_cli_presentation_without_verdict(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root, nature="anomalie")
    result = runner.invoke(app, ["cadrage", "recette", "REQ-001", "--path", str(root)])
    assert result.exit_code == 0, result.output
    assert "le cas ne se reproduit plus" in result.output and "livrée" in result.output
    assert "--verdict" in result.output and _recettes() == []


# ── Le verdict et ses effets ────────────────────────────────────────────────


def test_moa_acceptance_conforme_seals_and_accepts(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _, tid = _delivered(root)
    done = _verdict(root, "Conforme")
    entry = done["entry"]
    assert done["task_id"] == "" and done["state"] == "recettee"
    assert (entry["project"], entry["chantier"], entry["requirement"]) == (str(root.resolve()), "principal", "REQ-001")
    assert (entry["verdict"], entry["tasks"], entry["moa"], entry["origin"]) == (
        "conforme",
        [tid],
        "Ada Lovelace",
        ORIGIN_HUMAN,
    )
    req = rg.lookup(root, "REQ-001")
    assert req is not None and entry["fingerprint"] == rg.fingerprint("REQ-001", req)
    assert _state(root) == "recettee"


def test_moa_acceptance_non_conforme_opens_moe_task_then_reaccepted(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _, tid = _delivered(root)
    with pytest.raises(rg.RequirementRefusedError) as refused:
        _verdict(root, "non conforme")
    assert refused.value.code == "motif_required" and _recettes() == []
    done = _verdict(root, "non-conforme", "le lien n'expire jamais")
    rework = TaskService(root).require(done["task_id"])
    assert rework.status is TaskState.PROPOSED
    assert rework.acceptance[0] == "le lien n'expire jamais"
    assert (rework.extra or {})["requirement"] == "REQ-001"
    assert (rework.extra or {})["recette"]["reopens"] == [tid]
    assert done["state"] == "non-conforme" and _state(root) == "non-conforme"
    with pytest.raises(rg.RequirementRefusedError, match=done["task_id"]):  # la reprise reste ouverte : pas de recette
        _verdict(root, "conforme")
    _deliver(TaskService(root), done["task_id"], pr=13)
    assert _state(root) == "livree"
    assert moa.pending_acceptance(root, TaskService(root).list_tasks()) == ["REQ-001"]
    assert _verdict(root, "conforme")["state"] == "recettee"
    assert moa.pending_acceptance(root, TaskService(root).list_tasks()) == []


def test_moa_acceptance_reserve_creates_proposed_task_and_distinct_state(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root)
    with pytest.raises(rg.RequirementRefusedError, match="réserve est obligatoire"):
        _verdict(root, "conforme-avec-reserve")
    done = _verdict(root, "conforme avec réserve", "message d'erreur à reformuler")
    assert done["state"] == "recettee-avec-reserve"
    reserve = TaskService(root).require(done["task_id"])
    assert reserve.status is TaskState.PROPOSED and reserve.title.startswith("Réserve de recette REQ-001")
    _deliver(TaskService(root), done["task_id"], pr=14)
    assert _state(root) == "livree"  # la réserve levée se recette à son tour
    assert _verdict(root, "conforme")["state"] == "recettee"


def test_moa_acceptance_follow_up_failure_keeps_seal_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    _delivered(root)

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("disque plein")

    monkeypatch.setattr(TaskService, "add", _boom)
    done = _verdict(root, "non-conforme", "rien ne marche")
    assert "disque plein" in done["error"] and len(_recettes()) == 1
    assert "grimoire task add 'Recette non conforme REQ-001 : rien ne marche'" in done["error"]
    assert "--acceptance 'rien ne marche' --requirement REQ-001" in done["error"]
    assert done["state"] == "suite-a-creer"
    from rich.console import Console

    from grimoire.cli import cmd_cadrage

    monkeypatch.setattr(cmd_cadrage, "console", Console(width=300))
    status = runner.invoke(app, ["cadrage", "status", str(root)])
    assert "suite à créer" in status.output


# ── Les refus : exigence non validée, modifiée, non livrée ──────────────────


def test_moa_acceptance_refused_for_unvalidated_requirement(tmp_path: Path) -> None:
    root = _project(tmp_path)
    service = TaskService(root)
    _deliver(service, _task(service))
    with pytest.raises(rg.RequirementRefusedError) as refused:
        _verdict(root, "conforme")
    assert refused.value.code == "not_validated" and _recettes() == []
    assert _state(root) == "proposee"


def test_moa_acceptance_refused_when_text_changed_since_validation(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root)
    (root / CADRAGE_DIR / "04-exigences.md").write_text(EXIGENCES.replace("15 minutes", "15 jours"), encoding="utf-8")
    with pytest.raises(rg.RequirementRefusedError, match="modifiée depuis sa validation") as refused:
        _verdict(root, "conforme")
    assert refused.value.code == "not_validated" and _recettes() == []


def test_moa_acceptance_refused_while_a_task_stays_open_naming_it(tmp_path: Path) -> None:
    root = _project(tmp_path)
    service, _ = _delivered(root)
    other = _task(service, title="seconde tâche")
    with pytest.raises(rg.RequirementRefusedError, match=f"{other} \\(proposed\\)") as refused:
        _verdict(root, "conforme")
    assert refused.value.code == "not_delivered" and _recettes() == []
    service.cancel(other, "ne sert plus", "cli")  # une tâche annulée ne sert plus l'exigence
    assert _verdict(root, "conforme")["state"] == "recettee"


def test_moa_acceptance_refused_without_any_delivered_task(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _approve(root)
    with pytest.raises(rg.RequirementRefusedError, match="aucune tâche livrée") as refused:
        _verdict(root, "conforme")
    assert refused.value.code == "not_delivered"
    assert _state(root) == "validee"


@pytest.mark.parametrize(
    ("moa_name", "verdict", "code"), [(None, "conforme", "no_moa"), ("Ada Lovelace", "parfait", "bad_verdict")]
)
def test_moa_acceptance_refused_without_moa_or_with_unknown_verdict(
    tmp_path: Path, moa_name: str | None, verdict: str, code: str
) -> None:
    root = _project(tmp_path, moa_name=moa_name, gate=None)
    with pytest.raises(rg.RequirementRefusedError) as refused:
        _verdict(root, verdict)
    assert refused.value.code == code


# ── L'origine : les mêmes contrôles que la validation ───────────────────────


@pytest.mark.parametrize(
    ("name", "value"),
    [("CLAUDECODE", "1"), ("GRIMOIRE_UNATTENDED", "1"), ("CLAUDE_CODE_SESSION_ID", "s"), (SESSION_ENV, "s")],
)
def test_moa_acceptance_refused_with_agent_signal_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    root = _project(tmp_path)
    _delivered(root)
    before = AuthorityJournal().read()
    monkeypatch.setenv(name, value)
    for key in ("REQ-001", "REQ-404"):  # refus d'emblée, avant même de chercher l'exigence
        with pytest.raises(rg.RequirementRefusedError) as refused:
            _verdict(root, "conforme", key=key)
        assert refused.value.code == "human_required" and "recette refusée" in str(refused.value)
    assert AuthorityJournal().read() == before


def test_moa_acceptance_refused_to_in_process_orchestrator(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root)
    with acting_as_orchestrator(), pytest.raises(rg.RequirementRefusedError):
        _verdict(root, "conforme")
    assert _recettes() == []


def test_moa_acceptance_uses_the_shared_human_turn_with_its_own_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    _delivered(root)
    asked: list[str] = []
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda question: asked.append(question) or False)
    with pytest.raises(rg.RequirementRefusedError, match="tour humain non attesté"):
        _verdict(root, "conforme")
    assert asked and "verdict de recette" in asked[0] and _recettes() == []
    monkeypatch.setattr(rg, "_confirm_human_turn", _REAL_CONFIRM)  # stdin de pytest : pas un terminal
    with pytest.raises(rg.RequirementRefusedError):
        _verdict(root, "conforme")
    assert _recettes() == []


def test_moa_acceptance_python_api_refused_in_agent_subprocess(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    root = _project(tmp_path)
    _delivered(root)
    before = AuthorityJournal().read()
    env = {k: v for k, v in os.environ.items() if k not in _AGENT_ENVS}
    env["CLAUDECODE"] = "1"
    code = (
        "import sys; from pathlib import Path; from grimoire.missions.moa_acceptance import pronounce as p\n"
        "try:\n    p(Path(sys.argv[1]), 'REQ-001', 'conforme', actor='moa')\nexcept Exception as e:\n"
        "    print(type(e).__name__); sys.exit(3)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(root)],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 3, done.stdout + done.stderr
    assert "RequirementRefusedError" in done.stdout
    assert AuthorityJournal().read() == before


def test_moa_acceptance_cli_refused_from_agent_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    _delivered(root)
    monkeypatch.setenv("CLAUDECODE", "1")
    result = runner.invoke(app, ["cadrage", "recette", "REQ-001", "--path", str(root), "--verdict", "conforme"])
    assert result.exit_code == 1 and "recette refusée" in result.output
    assert _recettes() == []


def test_moa_acceptance_cli_human_turn_seals_and_opens_rework(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root)
    args = ["cadrage", "recette", "REQ-001", "--path", str(root), "--verdict", "non-conforme", "--motif", "trop lent"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "recette scellée" in result.output and "tâche MOE" in result.output and "non conforme" in result.output
    assert _recettes()[-1]["motif"] == "trop lent"


def test_moa_acceptance_cli_reports_follow_up_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    _delivered(root)
    monkeypatch.setattr(TaskService, "add", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disque plein")))
    args = ["cadrage", "recette", "REQ-001", "--path", str(root), "--verdict", "non-conforme", "--reserve", "x"]
    result = runner.invoke(app, args)
    assert result.exit_code == 1 and "disque plein" in result.output


# ── Seul le sceau compte ────────────────────────────────────────────────────


def test_moa_acceptance_forged_ledger_line_or_file_status_is_not_acceptance(tmp_path: Path) -> None:
    root = _project(tmp_path)
    service, tid = _delivered(root)
    service.ledger.append_event(
        moa.ACCEPTED_ENTRY, "REQ-001", "requirement", "ada", {"verdict": "conforme", "tasks": [tid]}
    )
    service.ledger.append_event("requirement.accepted", tid, "task", "ada", {"requirement": "REQ-001"})
    path = root / CADRAGE_DIR / "04-exigences.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n<!-- REQ-001 recettée : conforme -->\n", encoding="utf-8")
    journal = AuthorityJournal()
    req = rg.lookup(root, "REQ-001")
    assert req is not None
    base = {
        "type": moa.ACCEPTED_ENTRY,
        "project": str(root.resolve()),
        "requirement": "REQ-001",
        "verdict": "conforme",
        "moa": "Ada Lovelace",
        "tasks": [tid],
    }
    good_fp = rg.fingerprint("REQ-001", req)
    journal.append({**base, "fingerprint": good_fp, "origin": ORIGIN_AGENT, "channel": moa.RECETTE_CHANNEL})
    journal.append({**base, "fingerprint": good_fp, "origin": ORIGIN_HUMAN, "channel": "ledger"})
    journal.append({**base, "fingerprint": "0" * 64, "origin": ORIGIN_HUMAN, "channel": moa.RECETTE_CHANNEL})
    journal.append(
        {**base, "fingerprint": good_fp, "origin": ORIGIN_HUMAN, "channel": moa.RECETTE_CHANNEL, "verdict": "ok"}
    )
    assert _state(root) == "livree"
    assert moa.pending_acceptance(root, TaskService(root).list_tasks()) == ["REQ-001"]


def test_moa_acceptance_modified_after_acceptance_loses_it(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root)
    _verdict(root, "conforme")
    (root / CADRAGE_DIR / "04-exigences.md").write_text(EXIGENCES.replace("15 minutes", "5 minutes"), encoding="utf-8")
    assert _state(root) == "proposee"
    _approve(root)  # revalidée : l'ancienne recette portait sur un autre texte
    assert _state(root) == "livree"


def test_moa_acceptance_broken_journal_accepts_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root)
    _verdict(root, "conforme")
    AuthorityJournal().path.write_text("pas du json\n", encoding="utf-8")
    view = moa.requirement_view(root, "REQ-001", service=TaskService(root))
    assert view.state == "proposee" and "illisible" in view.reason
    with pytest.raises(rg.RequirementRefusedError):
        _verdict(root, "conforme")


def test_moa_acceptance_task_of_other_chantier_does_not_serve(tmp_path: Path) -> None:
    from grimoire.core.chantiers import new_chantier

    root = _project(tmp_path)
    service = TaskService(root)
    alpha = service.open_mission("Chantier alpha")
    beta = service.open_mission("Chantier beta")
    new_chantier(root, "Alpha", slug="alpha", mission=alpha.id)
    (root / CADRAGE_DIR / "alpha" / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    _approve(root, "alpha/REQ-001")
    stray = service.add("hors chantier", ("ok",), requirement="alpha/REQ-001", mission_id=beta.id).task.id
    _deliver(service, stray)
    assert moa.requirement_view(root, "alpha/REQ-001", service=service).tasks == []
    good = service.add("dans le chantier", ("ok",), requirement="alpha/REQ-001", mission_id=alpha.id).task.id
    _deliver(service, good)
    done = _verdict(root, "non-conforme", "à reprendre", key="alpha/REQ-001")
    assert done["entry"]["chantier"] == "alpha"
    assert TaskService(root).require(done["task_id"]).mission_id == alpha.id


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("conforme", "conforme"),
        ("Non Conforme", "non-conforme"),
        ("réserve", "conforme-avec-reserve"),
        ("conforme_avec_réserve", "conforme-avec-reserve"),
    ],
)
def test_moa_acceptance_verdict_spellings(raw: str, expected: str) -> None:
    assert moa.parse_verdict(raw) == expected


def test_moa_acceptance_state_rules() -> None:
    done, running = moa.TaskProof("T1", "t", "closed"), moa.TaskProof("T2", "t", "running")
    assert moa.state_of(False, [done], None) == "proposee"
    assert moa.state_of(True, [], None) == "validee"
    assert moa.state_of(True, [done, running], {"verdict": "conforme", "tasks": ["T1"]}) == "recettee-en-cours"
    assert moa.state_of(True, [done, running], {"verdict": "non-conforme", "tasks": ["T1"]}) == "non-conforme"
    assert moa.state_of(True, [done], {"verdict": "non-conforme", "tasks": ["T1"], "seq": 3}) == "suite-a-creer"
    assert moa.state_of(True, [done], {"verdict": "conforme-avec-reserve", "tasks": ["T1"]}) == "suite-a-creer"
    follow = moa.TaskProof("T3", "t", "running", follows=3)
    reserve = {"verdict": "conforme-avec-reserve", "tasks": ["T1"], "seq": 3}
    assert moa.state_of(True, [done, follow], reserve) == "recettee-avec-reserve"
    cancelled_follow = moa.TaskProof("T3", "t", "closed", follows=3)
    assert moa.state_of(True, [done, cancelled_follow], reserve) == "livree"


# ── État lisible : cadrage status, cockpit ──────────────────────────────────


def test_moa_acceptance_cadrage_status_lists_states(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root)
    result = runner.invoke(app, ["cadrage", "status", str(root)])
    assert result.exit_code == 0, result.output
    assert "REQ-001" in result.output and "attente de recette" in result.output
    assert "REQ-002" in result.output and "proposée" in result.output


def test_moa_acceptance_cadrage_status_without_requirements_shows_no_table(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / CADRAGE_DIR / "04-exigences.md").write_text("# Exigences\n\nde la prose\n", encoding="utf-8")
    result = runner.invoke(app, ["cadrage", "status", str(root)])
    assert "validation et recette" not in result.output


class _CountingLedger:
    def __init__(self) -> None:
        self.reads = 0

    def list_events(self) -> list[Any]:
        self.reads += 1
        return []


def test_moa_acceptance_cockpit_reads_nothing_without_governance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from grimoire.tools import workspace_attention as wa

    journal_reads: list[int] = []
    real_read = AuthorityJournal.read
    monkeypatch.setattr(AuthorityJournal, "read", lambda self: journal_reads.append(1) or real_read(self))
    bare = tmp_path / "nu"
    bare.mkdir()
    ledger = _CountingLedger()
    read = SimpleNamespace(row={"slug": "p", "name": "p"}, items=[])
    wa._requirement_item(read, SimpleNamespace(home_root=bare, ledger=ledger), [])  # type: ignore[arg-type]
    assert ledger.reads == 0 and journal_reads == [] and read.items == []


def test_moa_acceptance_cockpit_lists_requirements_awaiting_acceptance(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from grimoire.tools import workspace_attention as wa

    root = _project(tmp_path, gate="warn")
    service, _ = _delivered(root)
    read = SimpleNamespace(row={"slug": "p", "name": "p"}, items=[])
    wa._requirement_item(read, service, service.list_tasks())  # type: ignore[arg-type]
    recette = [i for i in read.items if i["kind"] == wa.REQUIREMENT_RECETTE]
    assert len(recette) == 1 and recette[0]["ref"]["requirements"] == ["REQ-001"]
    assert "grimoire cadrage recette REQ-001" in recette[0]["reason"]
    assert any(wa.REQUIREMENT_RECETTE in kinds for key, _, kinds in wa.SECTIONS if key == "decide")
    _verdict(root, "conforme")
    after = SimpleNamespace(row={"slug": "p", "name": "p"}, items=[])
    wa._requirement_item(after, service, TaskService(root).list_tasks())  # type: ignore[arg-type]
    assert not [i for i in after.items if i["kind"] == wa.REQUIREMENT_RECETTE]


def test_moa_acceptance_cockpit_survives_unreadable_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from grimoire.tools import workspace_attention as wa

    root = _project(tmp_path, gate="warn")
    service, _ = _delivered(root)

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("illisible")

    monkeypatch.setattr(moa, "pending_acceptance", _boom)
    read = SimpleNamespace(row={"slug": "p", "name": "p"}, items=[])
    wa._requirement_item(read, service, service.list_tasks())  # type: ignore[arg-type]
    assert read.items == []
    monkeypatch.setattr(rg, "gate_mode", _boom)
    wa._requirement_item(read, service, service.list_tasks())  # type: ignore[arg-type]
    assert read.items == []


# ── Cas limites (chaque branche d'échec fermé) ──────────────────────────────


def test_moa_acceptance_edge_cases_unknown_unreadable_and_odd_nature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    service, _ = _delivered(root)
    unknown = moa.requirement_view(root, "REQ-404", service=service)
    assert unknown.state == "proposee" and "introuvable" in unknown.reason
    odd = service.add("référence illisible", ("ok",), extra={"requirement": "n'importe quoi"}).task.id
    _deliver(service, odd)
    assert moa.pending_acceptance(root, TaskService(root).list_tasks()) == ["REQ-001"]
    bare = _project(tmp_path / "nu", moa_name=None, gate=None)
    assert moa.pending_acceptance(bare, TaskService(root).list_tasks()) == []
    (root / CADRAGE_DIR / "entretien.json").write_text(json.dumps({"depth": "standard", "kind": "inconnue"}))
    done = _verdict(root, "non-conforme", "x" * 120)  # nature illisible : la tâche de suite part sans nature
    rework = TaskService(root).require(done["task_id"])
    assert "nature" not in (rework.extra or {}) and rework.title.endswith("…")

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("illisible")

    monkeypatch.setattr(type(service.ledger), "events_for", _boom)
    proofs = moa.requirement_view(root, "REQ-001", service=TaskService(root)).tasks
    assert all(p.merged == "" for p in proofs)
    monkeypatch.setattr("grimoire.core.cadrage_requirements.read_requirements", _boom)
    assert "introuvable" in moa.requirement_view(root, "REQ-001", service=TaskService(root)).reason


def test_moa_acceptance_cli_presents_tasks_less_and_last_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    _approve(root, "REQ-002")
    empty = runner.invoke(app, ["cadrage", "recette", "REQ-002", "--path", str(root)])
    assert empty.exit_code == 0 and "aucune tâche rattachée" in empty.output
    _delivered(root)
    _verdict(root, "conforme-avec-reserve", "un libellé à revoir")
    shown = runner.invoke(app, ["cadrage", "recette", "REQ-001", "--path", str(root)])
    assert "Dernier verdict : conforme-avec-reserve" in shown.output and "un libellé à revoir" in shown.output

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise ValueError("chantier illisible")

    monkeypatch.setattr(moa, "requirement_views", _boom)
    status = runner.invoke(app, ["cadrage", "status", str(root)])
    assert "exigences illisibles" in status.output


# ── Relecture de #1368 : texte hostile, preuves étiquetées, état après recette ──

_HOSTILE = "\x1b[31mrouge\x1b]0;titre\x07\u202eenrevne\u2066x\u2069"


def _assert_clean(text: str) -> None:
    for bad in ("\x1b", "\x07", "\u202e", "\u2066", "\u2069"):
        assert bad not in text


def test_moa_acceptance_control_characters_never_reach_the_moa_screen_nor_the_seal(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _approve(root)
    service = TaskService(root)
    tid = service.add("titre " + _HOSTILE, ("ok",), requirement="REQ-001").task.id
    _deliver(service, tid)
    view = moa.requirement_view(root, "REQ-001", service=TaskService(root))
    _assert_clean(view.tasks[0].title)
    assert "rouge" in view.tasks[0].title
    shown = runner.invoke(app, ["cadrage", "recette", "REQ-001", "--path", str(root)])
    _assert_clean(shown.output)
    as_json = runner.invoke(app, ["cadrage", "recette", "REQ-001", "--path", str(root), "--json"])
    _assert_clean(json.loads(as_json.output)["tasks"][0]["title"])
    done = _verdict(root, "non-conforme", "motif " + _HOSTILE)
    _assert_clean(done["entry"]["motif"])
    _assert_clean(TaskService(root).require(done["task_id"]).title)
    for args in (["cadrage", "status", str(root)], ["cadrage", "recette", "REQ-001", "--path", str(root)]):
        _assert_clean(runner.invoke(app, args).output)


def test_moa_acceptance_requirement_text_sanitized_in_approve_and_recette(tmp_path: Path) -> None:
    root = _project(tmp_path)
    text = EXIGENCES.replace("lien magique", "lien magique " + _HOSTILE)
    (root / CADRAGE_DIR / "04-exigences.md").write_text(text, encoding="utf-8")
    approved = runner.invoke(app, ["cadrage", "approve", "REQ-001", "--path", str(root)])
    assert approved.exit_code == 0, approved.output
    _assert_clean(approved.output)
    _assert_clean(AuthorityJournal().read()[-1]["title"])
    _assert_clean(runner.invoke(app, ["cadrage", "status", str(root)]).output)
    assert rg.safe_text("a\nb", newlines=True) == "a\nb" and rg.safe_text("a\tb") == "a b"
    assert rg.safe_text("e\u0301\u200d") == "e\u0301\u200d"  # accents combinants et ZWJ gardés


def test_moa_acceptance_proofs_are_labelled_as_declared_by_moe(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root)
    shown = runner.invoke(app, ["cadrage", "recette", "REQ-001", "--path", str(root)])
    assert moa.PROOF_SOURCE in " ".join(shown.output.split())
    data = json.loads(runner.invoke(app, ["cadrage", "recette", "REQ-001", "--path", str(root), "--json"]).output)
    assert data["tasks"][0]["source"] == moa.PROOF_SOURCE
    service = TaskService(root)
    _task(service, title="sans preuve")
    data = json.loads(runner.invoke(app, ["cadrage", "recette", "REQ-001", "--path", str(root), "--json"]).output)
    assert [t["source"] for t in data["tasks"]] == [moa.PROOF_SOURCE, ""]


def test_moa_acceptance_new_task_after_acceptance_keeps_it_visible(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _delivered(root)
    _verdict(root, "conforme")
    later = _task(TaskService(root), title="amélioration ultérieure")
    assert _state(root) == "recettee-en-cours"
    assert moa.STATE_LABELS["recettee-en-cours"] == "recettée, nouvelle tâche en cours"
    _deliver(TaskService(root), later, pr=20)
    assert _state(root) == "livree"
