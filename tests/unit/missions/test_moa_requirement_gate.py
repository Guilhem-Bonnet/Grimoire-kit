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
from grimoire.missions import requirement_gate as rg
from grimoire.missions.mandate import AuthorityJournal, acting_as_orchestrator
from grimoire.missions.schemas import ORIGIN_AGENT, ORIGIN_HUMAN, ORIGIN_ORCHESTRATOR, ORIGIN_UNVERIFIED, TaskState
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


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un HOME par test : le journal d'autorité de l'un ne valide rien chez l'autre."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


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


def _approve(root: Path, key: str = "REQ-001", origin: str = ORIGIN_HUMAN) -> dict[str, Any]:
    return rg.approve_requirement(root, key, origin=origin, actor="ada")


def _add(service: TaskService, **kwargs: Any) -> Any:
    return service.add("connexion par lien magique", ("le lien fonctionne",), **kwargs)


# ── La validation : canal humain seulement, scellée ─────────────────────────


@pytest.mark.parametrize("origin", [ORIGIN_AGENT, ORIGIN_UNVERIFIED, ORIGIN_ORCHESTRATOR, "mcp", ""])
def test_moa_requirement_validation_refused_outside_human_channel(tmp_path: Path, origin: str) -> None:
    root = _project(tmp_path)
    with pytest.raises(rg.RequirementRefusedError) as refused:
        _approve(root, origin=origin)
    assert refused.value.code == "human_required"
    assert AuthorityJournal().read() == []  # rien n'est scellé


def test_moa_requirement_validation_refused_to_in_process_orchestrator(tmp_path: Path) -> None:
    """Le tick (orchestrateur en processus, sous mandat) ne valide pas, même en se disant humain."""
    root = _project(tmp_path)
    with acting_as_orchestrator(), pytest.raises(rg.RequirementRefusedError):
        _approve(root, origin=ORIGIN_HUMAN)


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
    """La CLI prend l'origine du canal ``task comment`` : une session d'agent n'y passe pas."""
    root = _project(tmp_path)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("GRIMOIRE_PRINCIPAL", "orchestrator")  # une revendication ne donne rien
    result = runner.invoke(app, ["cadrage", "approve", "REQ-001", "--path", str(root)])
    assert result.exit_code == 1
    assert "validation refusée" in result.output
    assert AuthorityJournal().read() == []


def test_moa_requirement_cli_human_turn_seals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.cli import cmd_task

    root = _project(tmp_path)
    monkeypatch.setattr(cmd_task, "_comment_origin", lambda: ORIGIN_HUMAN)
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


@pytest.mark.parametrize("nature", ["question", "incident", "Incident"])
def test_moa_requirement_exempt_natures_pass_in_block(tmp_path: Path, nature: str) -> None:
    root = _project(tmp_path)
    service = TaskService(root)
    task = _add(service, nature=nature, ready=True).task
    assert task.status is TaskState.READY
    assert [e.event_type for e in service.ledger.list_events(task.id) if e.event_type == rg.EXEMPTED_EVENT]
    pending = rg.pending_review(root, service.list_tasks(), service.ledger.list_events())
    assert pending["exempted"] == ([task.id] if nature.lower() == "incident" else [])


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
