"""#1342 M4 — recette MOA : prononcée contre l'exigence, depuis un tour humain attesté, scellée au journal d'autorité.

Ce que les tests tiennent :

- la présentation porte l'exigence, son critère de recette par nature, les tâches livrées et leurs preuves ;
- sans tour humain attesté, rien n'est scellé (le challenge reste ouvert) ; un tour consommé ne sert pas deux fois ;
- ``non conforme`` rouvre une tâche MOE rattachée à l'exigence, le motif en critère ; ``avec réserve`` crée une
  tâche proposée ; seule ``conforme`` close l'exigence ;
- une ligne forgée au ledger de tâches ne vaut pas recette : seul le journal d'autorité fait foi ;
- le cockpit (route d'attention) liste les exigences en attente de recette.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.cadrage import CADRAGE_DIR
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.hosts.decisions import HookInput, run_decision
from grimoire.hosts.events import HookEvent
from grimoire.missions import mandate as md
from grimoire.missions import moa_acceptance as moa
from grimoire.missions.cadrage_plan import covers_of
from grimoire.missions.mandate import AuthorityJournal
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

pytestmark = pytest.mark.covers("cli:cadrage recette")

runner = CliRunner()
ENV = {"GRIMOIRE_SESSION_ID": "S1"}

EXIGENCES = """---
phase: exigences
status: draft
---

# Exigences

- REQ-001 [Must] : l'utilisateur se connecte par lien magique
  - le lien expire après 15 minutes
- REQ-002 [Should] : export CSV des commandes
  - le fichier s'ouvre dans un tableur
"""


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_ACTOR",
        "CLAUDE_CODE_SESSION_ID",
        "GRIMOIRE_UNATTENDED",
        md.PRINCIPAL_ENV,
        md.PROVENANCE_ENV,
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project = (tmp_path / "projet").resolve()
    (project / CADRAGE_DIR).mkdir(parents=True)
    (project / CADRAGE_DIR / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    return project


@pytest.fixture
def service(root: Path) -> TaskService:
    return TaskService(root)


def _human_turn(root: Path, prompt: str = "conforme") -> None:
    """Le vrai chemin du hook ``UserPromptSubmit`` : un tour ``user`` de la session S1."""
    time.sleep(0.02)
    run_decision(
        "grimoire.task-context",
        HookInput(
            event=HookEvent.USER_PROMPT_SUBMIT,
            project_root=root,
            prompt=prompt,
            session_id="S1",
            host="host-claude-code-cli",
        ),
    )
    time.sleep(0.02)


def _deliver(service: TaskService, req: str, title: str = "livraison") -> str:
    """Une tâche rattachée à *req*, menée jusqu'à ``closed``."""
    added = service.add(title, ("le test passe",), extra={"covers": [req]})
    tid = added.task.id
    ledger = service.ledger
    ledger.transition_task(tid, TaskState.READY)
    ledger.claim_task(tid, "amelia", "local")
    ledger.transition_task(tid, TaskState.RUNNING)
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION)
    ledger.transition_task(tid, TaskState.CLOSED)
    return tid


def _pronounce(root: Path, service: TaskService, verdict: str, motif: str = "motif", req: str = "REQ-001"):
    """Demande le prononcé, un tour humain a lieu, la demande est rejouée : le geste en deux temps."""
    first = moa.pronounce(root, service, req, verdict, motif, env=ENV)
    assert not first.sealed  # sans tour humain après le challenge : refusé
    _human_turn(root)
    return moa.pronounce(root, service, req, verdict, motif, env=ENV)


# ── La présentation ─────────────────────────────────────────────────────────


def test_moa_acceptance_brief_carries_requirement_criterion_tasks_and_proofs(root: Path, service: TaskService) -> None:
    tid = _deliver(service, "REQ-001")
    brief = moa.build_brief(root, service, "req-001", nature="anomalie")
    assert brief.requirement == "REQ-001"
    assert "lien magique" in brief.title
    assert brief.criteria == ("le lien expire après 15 minutes",)
    assert brief.recette_criterion == "le cas ne se reproduit plus"
    assert [d.id for d in brief.delivered] == [tid]
    assert brief.delivered[0].proof  # « aucun verdict rangé » se dit, il ne disparaît pas
    assert brief.recettable


def test_moa_acceptance_default_nature_is_the_feature_criterion(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    assert moa.build_brief(root, service, "REQ-001").recette_criterion == "l'usage attendu fonctionne"


def test_moa_acceptance_every_nature_of_the_epic_table_has_a_criterion() -> None:
    assert len(moa.RECETTE_CRITERIA) == 11  # les onze natures du tableau, « question » exclue (sans objet)
    assert all(moa.RECETTE_CRITERIA.values())


def test_moa_acceptance_unknown_nature_or_requirement_is_refused(root: Path, service: TaskService) -> None:
    with pytest.raises(GrimoireMissionError, match="nature inconnue"):
        moa.build_brief(root, service, "REQ-001", nature="poésie")
    brief = moa.build_brief(root, service, "REQ-999")
    assert not brief.recettable
    assert any("n'est pas une exigence" in p for p in brief.problems)


def test_moa_acceptance_nothing_delivered_or_still_alive_is_not_recettable(root: Path, service: TaskService) -> None:
    assert any("rien n'est livré" in p for p in moa.build_brief(root, service, "REQ-001").problems)
    _deliver(service, "REQ-001")
    service.add("encore en cours", ("ok",), extra={"covers": ["REQ-001"]})
    assert any("servent encore" in p for p in moa.build_brief(root, service, "REQ-001").problems)
    with pytest.raises(GrimoireMissionError, match="recette impossible"):
        moa.pronounce(root, service, "REQ-001", "conforme", "ok", env=ENV)


# ── Le prononcé : tour humain attesté uniquement ────────────────────────────


def test_moa_acceptance_without_human_turn_nothing_is_sealed(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    out = moa.pronounce(root, service, "REQ-001", "conforme", "tout y est", env=ENV)
    assert not out.sealed
    assert out.attestation.reason in {
        "no_human_turn",
        "human_authority_unprovable",
    }  # aucun hook de prompt n’a écrit de tour
    assert moa.sealed_verdicts(root) == {}
    assert not moa.is_closed(root, "REQ-001")


def test_moa_acceptance_agent_session_without_session_id_cannot_pronounce(
    root: Path, service: TaskService, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deliver(service, "REQ-001")
    monkeypatch.delenv("GRIMOIRE_SESSION_ID")
    out = moa.pronounce(root, service, "REQ-001", "conforme", "ok", env={})
    assert not out.sealed
    assert moa.sealed_verdicts(root) == {}


def test_moa_acceptance_unattended_run_cannot_pronounce(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    moa.pronounce(root, service, "REQ-001", "conforme", "ok", env=ENV)
    _human_turn(root)
    out = moa.pronounce(root, service, "REQ-001", "conforme", "ok", env={**ENV, "GRIMOIRE_UNATTENDED": "1"})
    assert not out.sealed
    assert out.attestation.reason == "unattended"


def test_moa_acceptance_conforme_closes_the_requirement_and_seals_it(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    out = _pronounce(root, service, "conforme", "le lien magique marche et expire")
    assert out.sealed
    assert out.closed
    assert moa.is_closed(root, "REQ-001")
    seal = AuthorityJournal().read()[-1]
    assert seal["type"] == moa.ENTRY_TYPE
    assert seal["requirement"] == "REQ-001"
    assert seal["verdict"] == "conforme"
    assert seal["attestation"] == "attested"
    assert seal["reason"] == "le lien magique marche et expire"
    assert seal["project"] == str(root)


def test_moa_acceptance_a_human_turn_serves_one_pronouncement_only(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    _deliver(service, "REQ-002")
    moa.pronounce(root, service, "REQ-001", "conforme", "ok", env=ENV)
    moa.pronounce(root, service, "REQ-002", "conforme", "ok", env=ENV)
    _human_turn(root)
    first = moa.pronounce(root, service, "REQ-001", "conforme", "ok", env=ENV)
    second = moa.pronounce(root, service, "REQ-002", "conforme", "ok", env=ENV)
    assert first.sealed
    assert not second.sealed  # le même tour ne scelle pas deux exigences
    assert moa.is_closed(root, "REQ-001")
    assert not moa.is_closed(root, "REQ-002")


def test_moa_acceptance_changing_the_pronouncement_needs_a_fresh_turn(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    moa.pronounce(root, service, "REQ-001", "conforme", "ok", env=ENV)
    _human_turn(root, "conforme")
    changed = moa.pronounce(root, service, "REQ-001", "non conforme", "en fait non", env=ENV)
    assert not changed.sealed  # le tour précédait le challenge du nouveau prononcé


def test_moa_acceptance_motif_is_required(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    with pytest.raises(GrimoireMissionError, match="motif"):
        moa.pronounce(root, service, "REQ-001", "conforme", "  ", env=ENV)


def test_moa_acceptance_unknown_verdict_is_refused() -> None:
    with pytest.raises(GrimoireMissionError, match="verdict inconnu"):
        moa.parse_verdict("peut-être")
    assert moa.parse_verdict("Conforme avec réserve") == moa.RESERVE
    assert moa.parse_verdict("NON-CONFORME") == moa.NON_CONFORME


# ── Les suites du prononcé ──────────────────────────────────────────────────


def test_moa_acceptance_non_conforme_reopens_a_moe_task_with_the_motif_as_criterion(
    root: Path, service: TaskService
) -> None:
    _deliver(service, "REQ-001")
    out = _pronounce(root, service, "non_conforme", "le lien n'expire jamais")
    assert out.sealed
    assert not out.closed
    task = service.require(out.task_id)
    assert task.acceptance == ("le lien n'expire jamais",)
    assert covers_of(task) == ["REQ-001"]
    assert task.status in (TaskState.READY, TaskState.PROPOSED)
    assert not moa.is_closed(root, "REQ-001")


def test_moa_acceptance_reserve_creates_a_proposed_task_and_does_not_close(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    out = _pronounce(root, service, "conforme avec réserve", "ajouter un message d'erreur clair")
    assert out.sealed
    assert not out.closed
    task = service.require(out.task_id)
    assert task.status is TaskState.PROPOSED
    assert task.acceptance == ("ajouter un message d'erreur clair",)
    assert covers_of(task) == ["REQ-001"]
    assert not moa.is_closed(root, "REQ-001")


def test_moa_acceptance_a_later_non_conforme_reopens_a_closed_requirement(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    _pronounce(root, service, "conforme", "bon")
    assert moa.is_closed(root, "REQ-001")
    # Le journal fait foi : un prononcé postérieur rouvre l'exigence (le motif est inscrit, une tâche est rouverte).
    moa.pronounce(root, service, "REQ-001", "non conforme", "régression constatée", env=ENV)
    _human_turn(root)
    out = moa.pronounce(root, service, "REQ-001", "non conforme", "régression constatée", env=ENV)
    assert out.sealed
    assert not moa.is_closed(root, "REQ-001")


# ── Une ligne forgée ne vaut pas recette ────────────────────────────────────


def test_moa_acceptance_forged_ledger_line_is_not_a_recette(root: Path, service: TaskService) -> None:
    tid = _deliver(service, "REQ-001")
    service.comment(tid, "recette MOA : conforme", "agent", origin="agent")
    service.add(
        "faux",
        ("x",),
        extra={"covers": ["REQ-001"], "recette": {"verdict": "conforme", "seal_seq": 0}},
    )
    assert not moa.is_closed(root, "REQ-001")
    assert moa.sealed_verdicts(root) == {}


def test_moa_acceptance_forged_authority_line_is_not_a_recette(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    journal = AuthorityJournal()
    journal.append(
        {
            "type": moa.ENTRY_TYPE,
            "project": str(root),
            "requirement": "REQ-001",
            "verdict": "conforme",
            "reason": "x",
            "attestation": "unattested",
        }
    )
    assert not moa.is_closed(root, "REQ-001")  # une ligne non attestée ne ferme rien
    journal.append(
        {
            "type": moa.ENTRY_TYPE,
            "project": "/ailleurs",
            "requirement": "REQ-001",
            "verdict": "conforme",
            "reason": "x",
            "attestation": "attested",
        }
    )
    assert not moa.is_closed(root, "REQ-001")  # ni celle d'un autre projet


def test_moa_acceptance_a_broken_journal_closes_nothing(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    _pronounce(root, service, "conforme", "bon")
    assert moa.is_closed(root, "REQ-001")
    path = AuthorityJournal().path
    path.write_text(path.read_text(encoding="utf-8").replace('"conforme"', '"conforme "', 1), encoding="utf-8")
    assert not moa.is_closed(root, "REQ-001")  # chaîne rompue : échec fermé


# ── Le cockpit : les exigences en attente de recette ────────────────────────


def test_moa_acceptance_pending_lists_delivered_requirements_until_conforme(root: Path, service: TaskService) -> None:
    assert moa.pending_requirements(root, service) == []
    _deliver(service, "REQ-001")
    pending = moa.pending_requirements(root, service)
    assert [p["requirement"] for p in pending] == ["REQ-001"]
    _pronounce(root, service, "conforme", "bon")
    assert moa.pending_requirements(root, service) == []


def test_moa_acceptance_pending_waits_for_the_reopened_task(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    out = _pronounce(root, service, "non conforme", "à reprendre")
    assert moa.pending_requirements(root, service) == []  # la tâche MOE rouverte est vivante
    ledger = service.ledger
    ledger.claim_task(out.task_id, "amelia", "local")
    ledger.transition_task(out.task_id, TaskState.RUNNING)
    ledger.transition_task(out.task_id, TaskState.NEEDS_VERIFICATION)
    ledger.transition_task(out.task_id, TaskState.CLOSED)
    again = moa.pending_requirements(root, service)
    assert [p["requirement"] for p in again] == ["REQ-001"]
    assert again[0]["last_verdict"] == "non_conforme"


def test_moa_acceptance_cockpit_attention_lists_pending_requirements(
    root: Path, service: TaskService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.tools import workspace_attention as wa

    _deliver(service, "REQ-002")
    read = wa._ProjectRead(row={"slug": "p", "name": "p", "state": "ok"}, root=root)
    wa._collect_recette(read)
    assert [i["kind"] for i in read.items] == [wa.RECETTE]
    item = read.items[0]
    assert item["ref"]["requirement"] == "REQ-002"
    assert item["ref"]["gesture"]["command"] == "grimoire cadrage recette REQ-002"
    section = next(s for s in wa.SECTIONS if wa.RECETTE in s[2])
    assert section[0] == "decide"


# ── La commande ─────────────────────────────────────────────────────────────


def test_moa_acceptance_cli_lists_presents_and_refuses_without_turn(root: Path, service: TaskService) -> None:
    tid = _deliver(service, "REQ-001")
    listing = runner.invoke(app, ["cadrage", "recette", "--path", str(root), "-o", "json"])
    assert listing.exit_code == 0, listing.output
    assert [p["requirement"] for p in json.loads(listing.output)] == ["REQ-001"]

    shown = runner.invoke(app, ["cadrage", "recette", "REQ-001", "--path", str(root)])
    assert shown.exit_code == 0, shown.output
    assert tid in shown.output
    assert "l'usage attendu fonctionne" in shown.output

    refused = runner.invoke(
        app, ["cadrage", "recette", "REQ-001", "--path", str(root), "--verdict", "conforme", "--motif", "ok"]
    )
    assert refused.exit_code == 1
    assert "refusé" in refused.output
    assert moa.sealed_verdicts(root) == {}

    _human_turn(root)
    sealed = runner.invoke(
        app, ["cadrage", "recette", "REQ-001", "--path", str(root), "--verdict", "conforme", "--motif", "ok"]
    )
    assert sealed.exit_code == 0, sealed.output
    assert "recette scellée" in sealed.output
    assert moa.is_closed(root, "REQ-001")


def test_moa_acceptance_cli_motif_without_verdict_is_refused(root: Path, service: TaskService) -> None:
    _deliver(service, "REQ-001")
    result = runner.invoke(app, ["cadrage", "recette", "REQ-001", "--path", str(root), "--motif", "ok"])
    assert result.exit_code == 1
