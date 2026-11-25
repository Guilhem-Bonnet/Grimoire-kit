"""#1342, lot M3 : l'exigence validée par la MOA (tour humain, scellée), la tâche rattachée, le gate ``ready``.

Chaque test porte ``moa_requirement`` dans son nom : ``pytest -k moa_requirement`` les sélectionne tous.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.cadrage import CADRAGE_DIR
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import requirement_gate as rg
from grimoire.missions.mandate import AuthorityJournal, acting_as_orchestrator
from grimoire.missions.schemas import ORIGIN_AGENT, ORIGIN_HUMAN, TaskState
from grimoire.missions.service import TaskRefusedError, TaskService

pytestmark = pytest.mark.covers("cli:cadrage approve")

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
    """Un HOME par test (le journal d'autorité de l'un ne valide rien chez l'autre), sans signal d'agent hérité,
    et un « oui » de la MOA simulé à l'écran : seuls les tests du canal remettent le vrai tour humain."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in _AGENT_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda: True)


def _project(
    root: Path, *, gate: str | None = "block", moa: str | None = "Ada Lovelace", text: str = EXIGENCES
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"project": {"name": root.name}}
    governance: dict[str, Any] = {}
    if moa is not None:
        governance["roles"] = {"moa": moa}
    if gate is not None:
        governance["requirement_gate"] = gate
    if governance:
        data["governance"] = governance
    (root / "project-context.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    cadrage = root / CADRAGE_DIR
    cadrage.mkdir(parents=True, exist_ok=True)
    (cadrage / "04-exigences.md").write_text(text, encoding="utf-8")
    return root


def _approve(root: Path, key: str = "REQ-001") -> dict[str, Any]:
    return rg.approve_requirement(root, key, actor="ada")


def _add(service: TaskService, **kwargs: Any) -> Any:
    return service.add("connexion par lien magique", ("le lien fonctionne",), **kwargs)


# ── La validation : canal humain seulement, scellée ─────────────────────────


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CLAUDECODE", "1"),
        ("GRIMOIRE_UNATTENDED", "1"),
        ("CLAUDE_CODE_SESSION_ID", "sess"),
        ("CODEX_SANDBOX", "1"),
        (SESSION_ENV, "sess"),
    ],
)
def test_moa_requirement_validation_refused_with_agent_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    """L'origine s'établit dans la fonction : un marqueur d'agent refuse, même si l'écran dirait « oui »."""
    root = _project(tmp_path)
    monkeypatch.setenv(name, value)
    with pytest.raises(rg.RequirementRefusedError) as refused:
        _approve(root)
    assert refused.value.code == "human_required"
    assert AuthorityJournal().read() == []  # rien n'est scellé


def test_moa_requirement_validation_refused_without_interactive_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    monkeypatch.setattr(rg, "_confirm_human_turn", _REAL_CONFIRM)  # stdin de pytest : pas un terminal
    with pytest.raises(rg.RequirementRefusedError, match="tour humain non attesté"):
        _approve(root)
    assert AuthorityJournal().read() == []


def test_moa_requirement_validation_refused_when_screen_says_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import sys

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    root = _project(tmp_path)
    monkeypatch.setattr(rg, "_confirm_human_turn", _REAL_CONFIRM)
    monkeypatch.setattr(sys, "stdin", _Tty("n\n"))
    monkeypatch.setattr(sys, "stderr", _Tty())
    with pytest.raises(rg.RequirementRefusedError):
        _approve(root)
    monkeypatch.setattr(sys, "stdin", _Tty("oui\n"))
    assert _approve(root)["requirement"] == "REQ-001"


def test_moa_requirement_validation_refused_to_in_process_orchestrator(tmp_path: Path) -> None:
    """Le tick (orchestrateur en processus, sous mandat) ne valide pas."""
    root = _project(tmp_path)
    with acting_as_orchestrator(), pytest.raises(rg.RequirementRefusedError):
        _approve(root)


def test_moa_requirement_python_api_refused_in_agent_subprocess(tmp_path: Path) -> None:
    """Régression (relecture de #1362) : l'API Python appelée depuis un processus d'agent ne scelle rien."""
    import os
    import subprocess
    import sys

    root = _project(tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in _AGENT_ENVS}
    env["CLAUDECODE"] = "1"
    code = (
        "import sys; from pathlib import Path; from grimoire.missions.requirement_gate import approve_requirement as a\n"
        "try:\n    a(Path(sys.argv[1]), 'REQ-001', actor='moa')\nexcept Exception as e:\n"
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
    assert AuthorityJournal().read() == []
    service = TaskService(root)
    task = _add(service, requirement="REQ-001").task
    with pytest.raises(TaskRefusedError):
        service.transition(task.id, TaskState.READY, "agent")


def test_moa_requirement_validation_sealed_with_fingerprint(tmp_path: Path) -> None:
    root = _project(tmp_path)
    entry = _approve(root)
    sealed = AuthorityJournal().read()
    assert sealed[-1]["type"] == rg.VALIDATED_ENTRY
    assert (entry["project"], entry["requirement"], entry["moa"]) == (str(root.resolve()), "REQ-001", "Ada Lovelace")
    assert len(entry["fingerprint"]) == 64
    found, reason = rg.validation_of(root, "REQ-001")
    assert found is not None and reason == ""


def test_moa_requirement_validation_refused_without_declared_moa(tmp_path: Path) -> None:
    root = _project(tmp_path, moa=None, gate=None)
    with pytest.raises(rg.RequirementRefusedError) as refused:
        _approve(root)
    assert refused.value.code == "no_moa"


@pytest.mark.parametrize(
    ("key", "code"), [("REQ-404", "unknown_requirement"), ("autre/REQ-001", "unknown_requirement")]
)
def test_moa_requirement_validation_refused_for_unknown_requirement(tmp_path: Path, key: str, code: str) -> None:
    root = _project(tmp_path)
    with pytest.raises(rg.RequirementRefusedError) as refused:
        _approve(root, key)
    assert refused.value.code == code


def test_moa_requirement_cli_refused_from_agent_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """La CLI passe par la même fonction : une session d'agent n'y passe pas, même avec un « oui » simulé."""
    root = _project(tmp_path)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("GRIMOIRE_PRINCIPAL", "orchestrator")  # une revendication ne donne rien
    result = runner.invoke(app, ["cadrage", "approve", "REQ-001", "--path", str(root)])
    assert result.exit_code == 1
    assert "validation refusée" in result.output
    assert AuthorityJournal().read() == []


def test_moa_requirement_cli_human_turn_seals(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = runner.invoke(app, ["cadrage", "approve", "req-002", "--path", str(root)])
    assert result.exit_code == 0, result.output
    assert "export CSV" in result.output  # la MOA lit ce qu'elle valide
    assert rg.validation_of(root, "REQ-002")[0] is not None


# ── Les contournements : forgé, modifié, autre projet, autre chantier ───────


def test_moa_requirement_forged_ledger_event_and_file_status_ignored(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / CADRAGE_DIR / "04-exigences.md").write_text(
        "---\nstatus: validé\nmoa: Ada Lovelace\n---\n" + EXIGENCES + "\n<!-- REQ-001 validée par la MOA -->\n",
        encoding="utf-8",
    )
    service = TaskService(root)
    task = _add(service, requirement="REQ-001").task
    service.ledger.append_event(
        "requirement.validated", "REQ-001", "requirement", "ada", {"requirement": "REQ-001", "moa": "Ada Lovelace"}
    )
    service.ledger.append_event(rg.VALIDATED_ENTRY, task.id, "task", "ada", {"requirement": "REQ-001"})
    with pytest.raises(TaskRefusedError):
        service.transition(task.id, TaskState.READY, "agent")


def test_moa_requirement_forged_journal_entry_without_human_seal_ignored(tmp_path: Path) -> None:
    """Une entrée au journal qui n'est pas celle du canal (origine, canal, empreinte) ne vaut rien."""
    root = _project(tmp_path)
    journal = AuthorityJournal()
    base = {"type": rg.VALIDATED_ENTRY, "project": str(root.resolve()), "requirement": "REQ-001", "moa": "Ada Lovelace"}
    journal.append({**base, "fingerprint": "0" * 64, "origin": ORIGIN_HUMAN, "channel": rg.APPROVE_CHANNEL})
    req = rg.lookup(root, "REQ-001")
    assert req is not None
    journal.append(
        {**base, "fingerprint": rg.fingerprint("REQ-001", req), "origin": ORIGIN_AGENT, "channel": rg.APPROVE_CHANNEL}
    )
    journal.append({**base, "fingerprint": rg.fingerprint("REQ-001", req), "origin": ORIGIN_HUMAN, "channel": "ledger"})
    assert rg.validation_of(root, "REQ-001")[0] is None


def test_moa_requirement_modified_after_validation_loses_it(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _approve(root)
    service = TaskService(root)
    (root / CADRAGE_DIR / "04-exigences.md").write_text(EXIGENCES.replace("15 minutes", "15 jours"), encoding="utf-8")
    task = _add(service, requirement="REQ-001").task
    with pytest.raises(TaskRefusedError, match="modifiée depuis sa validation"):
        service.transition(task.id, TaskState.READY, "agent")


def test_moa_requirement_validated_text_moved_to_other_id_not_validated(tmp_path: Path) -> None:
    """Usurper l'identifiant : recopier le texte validé sous un autre REQ ne lui transfère pas la validation."""
    root = _project(tmp_path)
    _approve(root)
    text = (
        EXIGENCES
        + "- REQ-003 [Must] : l'utilisateur se connecte par lien magique\n  - le lien expire après 15 minutes\n"
    )
    (root / CADRAGE_DIR / "04-exigences.md").write_text(text, encoding="utf-8")
    assert rg.validation_of(root, "REQ-001")[0] is not None
    assert rg.validation_of(root, "REQ-003")[0] is None


def test_moa_requirement_validation_of_other_project_does_not_count(tmp_path: Path) -> None:
    first = _project(tmp_path / "a")
    second = _project(tmp_path / "b")
    _approve(first)
    assert rg.validation_of(second, "REQ-001")[0] is None
    service = TaskService(second)
    task = _add(service, requirement="REQ-001").task
    with pytest.raises(TaskRefusedError):
        service.transition(task.id, TaskState.READY, "agent")


def test_moa_requirement_other_moa_validation_lapses(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _approve(root)
    _project(root, moa="Grace Hopper")
    found, reason = rg.validation_of(root, "REQ-001")
    assert found is None and "autre MOA" in reason


def test_moa_requirement_task_of_other_chantier_refused(tmp_path: Path) -> None:
    from grimoire.core.chantiers import new_chantier

    root = _project(tmp_path)
    service = TaskService(root)
    alpha = service.open_mission("Chantier alpha")
    beta = service.open_mission("Chantier beta")
    new_chantier(root, "Alpha", slug="alpha", mission=alpha.id)
    (root / CADRAGE_DIR / "alpha" / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    _approve(root, "alpha/REQ-001")
    good = _add(service, requirement="alpha/REQ-001", mission_id=alpha.id).task
    assert service.transition(good.id, TaskState.READY, "agent").task.status is TaskState.READY
    stray = _add(service, requirement="alpha/REQ-001", mission_id=beta.id).task
    with pytest.raises(TaskRefusedError, match="chantier"):
        service.transition(stray.id, TaskState.READY, "agent")
    _approve(root, "REQ-001")
    bare = _add(service, requirement="REQ-001", mission_id=alpha.id).task  # mission d'alpha, exigence du principal
    with pytest.raises(TaskRefusedError, match="autre chantier"):
        service.transition(bare.id, TaskState.READY, "agent")


# ── Le gate : block, warn, exemptions, projets existants ────────────────────


def test_moa_requirement_block_refuses_task_without_requirement_and_says_what_to_do(tmp_path: Path) -> None:
    service = TaskService(_project(tmp_path))
    with pytest.raises(TaskRefusedError) as refused:
        _add(service, ready=True)
    message = str(refused.value)
    assert "requirement_validated" in message
    assert "grimoire cadrage interview" in message and "grimoire cadrage approve" in message


def test_moa_requirement_block_passes_once_validated(tmp_path: Path) -> None:
    root = _project(tmp_path)
    service = TaskService(root)
    task = _add(service, requirement="REQ-001").task
    with pytest.raises(TaskRefusedError, match="non validée"):
        service.transition(task.id, TaskState.READY, "agent")
    _approve(root)
    moved = service.transition(task.id, TaskState.READY, "agent")
    assert moved.task.status is TaskState.READY


def test_moa_requirement_block_refuses_claim_of_forged_ready(tmp_path: Path) -> None:
    """Une transition ``ready`` écrite au ledger sans passer par le gate ne se réclame pas en mode bloquant."""
    root = _project(tmp_path)
    service = TaskService(root)
    task = _add(service).task
    service.ledger.transition_task(task.id, TaskState.READY, actor_id="worker")
    with pytest.raises(TaskRefusedError, match="requirement_validated"):
        service.claim(task.id, "worker")


def test_moa_requirement_warn_flags_without_blocking(tmp_path: Path) -> None:
    root = _project(tmp_path, gate="warn")
    service = TaskService(root)
    moved = _add(service, ready=True).task
    assert moved.status is TaskState.READY
    events = [e for e in service.ledger.list_events(moved.id) if e.event_type == rg.FLAGGED_EVENT]
    assert len(events) == 1
    assert "grimoire cadrage approve" in events[0].payload["remedy"]
    assert rg.pending_review(root, service.list_tasks(), service.ledger.list_events())["unvalidated"] == [moved.id]


def test_moa_requirement_warn_is_the_default_when_gate_absent(tmp_path: Path) -> None:
    service = TaskService(_project(tmp_path, gate=None))
    assert _add(service, ready=True).task.status is TaskState.READY


_TITLES = {"question": "pourquoi le lien expire-t-il ?", "incident": "incident : la production est en panne"}


@pytest.mark.parametrize("nature", ["question", "incident", "Incident"])
def test_moa_requirement_exempt_natures_pass_in_block(tmp_path: Path, nature: str) -> None:
    root = _project(tmp_path)
    service = TaskService(root)
    task = service.add(_TITLES[nature.lower()], ("ok",), nature=nature, ready=True).task
    assert task.status is TaskState.READY
    assert [e.event_type for e in service.ledger.list_events(task.id) if e.event_type == rg.EXEMPTED_EVENT]
    pending = rg.pending_review(root, service.list_tasks(), service.ledger.list_events())
    if nature.lower() == "incident":
        assert pending["postmortem"] == [task.id]
        service.ledger.transition_task(task.id, TaskState.CLAIMED, actor_id="w")  # rien ne la retire avant l'exigence
        assert rg.pending_review(root, service.list_tasks(), service.ledger.list_events())["postmortem"] == [task.id]
    else:
        assert pending["questions"] == [task.id]


@pytest.mark.parametrize("nature", ["question", "incident"])
def test_moa_requirement_declared_nature_not_matching_intake_not_exempt(tmp_path: Path, nature: str) -> None:
    """Une nature exemptée ne se déclare pas seule : l'intake doit lire le titre de la même façon."""
    service = TaskService(_project(tmp_path))
    with pytest.raises(TaskRefusedError, match="pas d'exemption"):
        service.add("refondre le module de paiement", ("ok",), nature=nature, ready=True)


def test_moa_requirement_cadrage_anchor_exempt_in_block(tmp_path: Path) -> None:
    """L'ancre des questions du cadrage produit les exigences : le gate ne la bloque pas."""
    from grimoire.missions.cadrage_plan import ANCHOR

    service = TaskService(_project(tmp_path))
    anchor = service.add("questions du cadrage", ("ok",), extra={"cadrage": ANCHOR}, ready=True).task
    assert anchor.status is TaskState.READY
    with pytest.raises(TaskRefusedError):
        service.add("pas une ancre", ("ok",), extra={"cadrage": "autre"}, ready=True)


def test_moa_requirement_other_nature_not_exempt(tmp_path: Path) -> None:
    service = TaskService(_project(tmp_path))
    with pytest.raises(TaskRefusedError):
        _add(service, nature="anomalie", ready=True)
    with pytest.raises(GrimoireMissionError, match="nature inconnue"):
        _add(service, nature="pas-une-nature")


def test_moa_requirement_project_without_governance_never_blocked(tmp_path: Path) -> None:
    root = _project(tmp_path, moa=None, gate="block")  # la bascule seule, sans MOA : jamais bloquant
    service = TaskService(root)
    assert _add(service, ready=True).task.status is TaskState.READY
    bare = tmp_path / "nu"
    bare.mkdir()
    assert _add(TaskService(bare), ready=True).task.status is TaskState.READY
    assert rg.gate_mode(bare).mode == "warn"


def test_moa_requirement_malformed_section_that_asked_block_stays_blocking(tmp_path: Path) -> None:
    root = _project(tmp_path, moa="dependabot[bot]")
    mode = rg.gate_mode(root)
    assert mode.blocking and "mal formée" in mode.problem
    with pytest.raises(rg.RequirementRefusedError) as refused:
        _approve(root)
    assert refused.value.code == "no_moa"


def test_moa_requirement_cadrage_plan_carries_the_reference(tmp_path: Path) -> None:
    from grimoire.missions.cadrage_plan import apply_plan, build_plan

    root = _project(tmp_path)
    service = TaskService(root)
    plan = apply_plan(service, build_plan(root, service), "cli")
    created = {t.title: t for t in service.list_tasks()}
    assert plan.proposals
    assert {(t.extra or {}).get("requirement") for t in created.values()} >= {"REQ-001", "REQ-002"}


def test_moa_requirement_task_add_cli_records_requirement(tmp_path: Path) -> None:
    root = _project(tmp_path, gate="warn")
    result = runner.invoke(
        app,
        [
            "--output",
            "json",
            "task",
            "add",
            "titre",
            "--acceptance",
            "un critère",
            "--requirement",
            "req-002",
            "--nature",
            "anomalie",
            "--project-root",
            str(root),
        ],
    )
    assert result.exit_code == 0, result.output
    task = TaskService(root).list_tasks()[0]
    assert (task.extra or {}).get("requirement") == "REQ-002"
    assert (task.extra or {}).get("nature") == "anomalie"


@pytest.mark.parametrize("raw", ["REQ-", "foo", "../x/REQ-1", "REQ-00001", "A B/REQ-1"])
def test_moa_requirement_invalid_key_refused(raw: str) -> None:
    with pytest.raises(GrimoireMissionError):
        rg.normalize_key(raw)


def test_moa_requirement_key_normalisation() -> None:
    assert rg.normalize_key("req-7") == "REQ-7"
    assert rg.normalize_key("REQ-7", "alpha") == "alpha/REQ-7"
    assert rg.normalize_key("principal/REQ-7") == "REQ-7"


# ── Relecture de #1362 : file, lanceur, cockpit, doctor ─────────────────────


def _forged_ready(service: TaskService, title: str = "connexion par lien magique") -> str:
    task = _add(service).task
    service.ledger.transition_task(task.id, TaskState.READY, actor_id="worker")
    return task.id


def test_moa_requirement_block_excludes_unvalidated_task_from_queue(tmp_path: Path) -> None:
    root = _project(tmp_path)
    service = TaskService(root)
    tid = _forged_ready(service)
    plan = service.queue_plan(routes=False, probe_host=False)
    assert tid not in {a.task_id for a in plan.admitted}
    assert any(t == tid and "exigence non validée" in why for t, why in plan.excluded)
    warn = TaskService(_project(tmp_path / "warn", gate="warn"))
    other = _forged_ready(warn)
    assert other in {a.task_id for a in warn.queue_plan(routes=False, probe_host=False).admitted}


def test_moa_requirement_launcher_skips_before_creating_worktree(tmp_path: Path) -> None:
    from lot2_doubles import FakeHost, make_repo

    from grimoire.missions.admission import Admit
    from grimoire.missions.launcher import WorkerLauncher
    from grimoire.missions.orchestrator_policy import load_orchestrator_policy
    from grimoire.missions.task_worktree import find_task_worktree

    root = make_repo(tmp_path)
    policy_file = root / "_grimoire/standard/orchestration-policy.yaml"
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.write_text(
        "orchestrator:\n  roles:\n    worker:\n      enabled: true\n      wip: 3\n      sessions_per_day: 5\n"
        "      permission_mode: acceptEdits\n",
        encoding="utf-8",
    )
    _project(root)
    service = TaskService(root)
    tid = _forged_ready(service)
    host = FakeHost()
    policy = load_orchestrator_policy(root).policy
    outcomes = WorkerLauncher(service, policy, exec_=host)((Admit(tid),))  # type: ignore[arg-type]
    assert [o.status for o in outcomes] == ["skipped"]
    assert "exigence non validée" in outcomes[0].detail
    assert find_task_worktree(TaskService(root), tid) is None
    assert not host.of("claude", "--bg")
    assert TaskService(root).require(tid).status is TaskState.READY


class _CountingLedger:
    def __init__(self) -> None:
        self.reads = 0

    def list_events(self) -> list[Any]:
        self.reads += 1
        return []


def test_moa_requirement_cockpit_reads_no_event_without_governance(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from grimoire.tools import workspace_attention as wa

    bare = tmp_path / "nu"
    bare.mkdir()
    ledger = _CountingLedger()
    wa._requirement_item(None, SimpleNamespace(home_root=bare, ledger=ledger), [])  # type: ignore[arg-type]
    assert ledger.reads == 0
    declared = _project(tmp_path / "gouverne", gate="warn")
    wa._requirement_item(None, SimpleNamespace(home_root=declared, ledger=ledger), [])  # type: ignore[arg-type]
    assert ledger.reads == 1


def test_moa_requirement_cockpit_item_lists_pending(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from grimoire.tools import workspace_attention as wa

    root = _project(tmp_path, gate="warn")
    service = TaskService(root)
    flagged = _add(service, ready=True).task
    incident = service.add(_TITLES["incident"], ("ok",), nature="incident", ready=True).task
    read = SimpleNamespace(row={"slug": "p", "name": "p"}, items=[])
    wa._requirement_item(read, service, service.list_tasks())  # type: ignore[arg-type]
    (item,) = read.items
    assert item["kind"] == wa.REQUIREMENT_UNVALIDATED
    assert item["ref"]["task_ids"] == [flagged.id] and item["ref"]["postmortem"] == [incident.id]
    assert "post-mortem dû" in item["title"]


def test_moa_requirement_doctor_warns_when_gate_disarmed_after_validation(tmp_path: Path) -> None:
    from grimoire.cli.doctor_moa_moe import moa_moe_verdict

    root = _project(tmp_path)
    _approve(root)
    assert moa_moe_verdict(root)["level"] == "ok"  # type: ignore[index]
    _project(root, gate="warn")
    verdict = moa_moe_verdict(root)
    assert (
        verdict is not None
        and verdict["level"] == "warn"
        and "validation(s) d'exigence scellée(s)" in verdict["detail"]
    )
    _project(root, moa=None, gate="block")
    assert "scellée" in moa_moe_verdict(root)["detail"]  # type: ignore[index]
    assert rg.sealed_validations(root) == 1


# ── Cas limites (chaque branche d'échec fermé) ──────────────────────────────


def _break_journal() -> None:
    journal = AuthorityJournal()
    journal.append({"type": "noise"})
    journal.path.write_text("pas du json\n", encoding="utf-8")


def test_moa_requirement_broken_journal_validates_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _approve(root)
    _break_journal()
    found, reason = rg.validation_of(root, "REQ-001")
    assert found is None and "journal d'autorité illisible" in reason
    assert rg.sealed_validations(root) == 0


def test_moa_requirement_explicit_journal_is_used(tmp_path: Path) -> None:
    root = _project(tmp_path)
    journal = AuthorityJournal(tmp_path / "ailleurs" / "journal.jsonl")
    rg.approve_requirement(root, "REQ-001", actor="ada", journal=journal)
    assert rg.validation_of(root, "REQ-001", journal=journal)[0] is not None
    assert rg.validation_of(root, "REQ-001")[0] is None  # le journal par défaut n'a rien
    assert rg.sealed_validations(root, journal=journal) == 1


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("- REQ-001 : une formulation\n- REQ-001 : une autre formulation\n", "conflict"),
        ("- REQ-001\n  - un critère sans formulation\n", "no_text"),
        ("- REQ-001 [Won't] : plus tard\n", "out_of_scope"),
    ],
)
def test_moa_requirement_unvalidatable_requirement_refused(tmp_path: Path, text: str, code: str) -> None:
    root = _project(tmp_path, text=text)
    with pytest.raises(rg.RequirementRefusedError) as refused:
        _approve(root)
    assert refused.value.code == code


def test_moa_requirement_unreadable_context_or_cadrage_is_warn_and_unvalidated(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "project-context.yaml").write_text("governance: [pas fermé\n", encoding="utf-8")
    mode = rg.gate_mode(root)
    assert mode.mode == "warn" and "illisible" in mode.problem
    found, reason = rg.validation_of(root, "REQ-001")
    assert found is None and "aucune MOA" in reason
    assert rg.lookup(root, "pas-une-cle") is None
    assert rg.validation_of(_project(tmp_path / "b"), "REQ-404")[1].endswith("introuvable dans le cadrage du projet")


def test_moa_requirement_task_reference_fallbacks(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _approve(root)
    service = TaskService(root)
    planned = service.add("x", ("ok",), extra={"covers": ["REQ-001"]}).task  # tâche d'avant M3 : un seul covers
    assert rg.requirement_of(planned) == "REQ-001"
    assert rg.check_task(root, planned).state == "ok"
    bad = service.add("y", ("ok",), extra={"requirement": "n'importe quoi"}).task
    check = rg.check_task(root, bad)
    assert check.gap and "invalide" in check.detail
    assert rg.task_fields("", "") == {}


def test_moa_requirement_record_ready_never_breaks_the_transition(tmp_path: Path) -> None:
    class _Broken:
        def append_event(self, *args: Any) -> None:
            raise OSError("disque plein")

    root = _project(tmp_path, gate="warn")
    task = _add(TaskService(root)).task
    rg.record_ready(_Broken(), root, task, "agent")  # ne lève pas
    assert rg.transition_refusal(root, task, TaskState.CANCELLED) is None


def test_moa_requirement_pending_review_empty_without_moa_and_skips_cancelled(tmp_path: Path) -> None:
    root = _project(tmp_path, gate="warn")
    service = TaskService(root)
    task = _add(service, ready=True).task
    events = service.ledger.list_events()
    service.cancel(task.id, "abandon", "cli")
    assert rg.pending_review(root, service.list_tasks(), events)["unvalidated"] == []
    bare = _project(tmp_path / "nu", moa=None, gate=None)
    assert rg.pending_review(bare, service.list_tasks(), events) == {
        "unvalidated": [],
        "questions": [],
        "postmortem": [],
    }


def test_moa_requirement_doctor_check_prints(tmp_path: Path) -> None:
    from rich.console import Console

    from grimoire.cli.doctor_moa_moe import apply_moa_moe_check

    root = _project(tmp_path)
    results: list[dict[str, Any]] = []
    console = Console(record=True, width=200)
    apply_moa_moe_check(results, root, fmt="text", console=console)
    apply_moa_moe_check(results, tmp_path / "absent", fmt="text", console=console)
    assert [r["level"] for r in results] == ["ok"]
    assert "gate d'exigence : block" in console.export_text()


def test_moa_requirement_cockpit_item_questions_and_unreadable_ledger(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from grimoire.tools import workspace_attention as wa

    root = _project(tmp_path, gate="warn")
    service = TaskService(root)
    question = service.add(_TITLES["question"], ("ok",), nature="question", ready=True).task
    read = SimpleNamespace(row={"slug": "p", "name": "p"}, items=[])
    wa._requirement_item(read, service, service.list_tasks())  # type: ignore[arg-type]
    assert read.items[0]["ref"]["questions"] == [question.id] and "question(s) exemptée(s)" in read.items[0]["title"]

    class _Unreadable:
        def list_events(self) -> list[Any]:
            raise OSError("ledger illisible")

    empty = SimpleNamespace(row={"slug": "p", "name": "p"}, items=[])
    wa._requirement_item(empty, SimpleNamespace(home_root=root, ledger=_Unreadable()), [])  # type: ignore[arg-type]
    assert empty.items == []
    nothing = SimpleNamespace(row={"slug": "p", "name": "p"}, items=[])
    wa._requirement_item(nothing, SimpleNamespace(home_root=root, ledger=_CountingLedger()), [])  # type: ignore[arg-type]
    assert nothing.items == []
