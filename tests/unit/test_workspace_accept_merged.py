"""GAO-cockpit-tout-001 : « Tout accepter » ferme en un geste les tâches que le merger a laissées à l'humain.

Le trio de conditions est éprouvé dans les deux sens : une tâche fusionnée, relue READY et dont la
dernière question ouverte est celle du merger est candidate ; une tâche encore en revue ne l'est pas.
La route exige la clé de contrôle : sans elle, le transport répond 403 et rien n'est écrit.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from grimoire.evidence.schemas import EvidenceItem, EvidenceKind, EvidenceProfile
from grimoire.evidence.service import EvidenceService
from grimoire.missions import mandate as md
from grimoire.missions.merger import MERGE_ESCALATED_EVENT
from grimoire.missions.questions import QuestionSpec
from grimoire.missions.review_receipt import READY, write_receipt
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import MERGED_EVENT
from grimoire.tools import workspace_accept_merged
from grimoire.tools.workspace_routes import PREFIX, workspace_get, workspace_post

from .test_mutation_guard import JSON, Host, cockpit  # noqa: F401

if TYPE_CHECKING:
    from collections.abc import Iterator

ACC = ("la PR est fusionnée",)
EVIDENCE = Path("_grimoire-runtime-output/evidence")


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "home-control"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    yield home


def _in_review(root: Path, title: str) -> str:
    """Une tâche en revue avec son pack déclaratif — la mise en place de test_task_accept."""
    service = TaskService(root)
    tid = service.add(title, ACC, owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude")
    service.transition(tid, TaskState.RUNNING, "claude")
    (root / "preuve.md").write_text("# PR fusionnée\n", encoding="utf-8")
    item = EvidenceItem("doc-01", EvidenceKind.DOC, "file:preuve.md", f"sha256-{sha256(b'preuve').hexdigest()}", "PR relue")
    EvidenceService(root / EVIDENCE).create_pack(tid, EvidenceProfile.STANDARD, [item], acceptance=ACC)
    service.transition(tid, TaskState.NEEDS_VERIFICATION, "claude")
    return tid


def _merged_candidate(root: Path, title: str, pr: int = 42) -> str:
    """Une candidate complète : en revue, reçu READY, ``task.merged`` et la question d'acceptation du merger."""
    service = TaskService(root)
    tid = _in_review(root, title)
    write_receipt(root, tid, READY, reviewer_id="reviewer")
    spec = QuestionSpec(
        f"Accepter {tid} (PR #{pr} fusionnée) ? Le merger ne l'accepte pas : chemins réservés. "
        f"Accepte-la toi-même : `grimoire task accept {tid}`.",
        kind="choice",
        blocking=False,
        key=f"merger:accept:{tid}",
    )
    asked = service.ask_batch(tid, [spec], "merger")
    question_id = asked.plan.asked[0].id
    service.ledger.append_event(
        MERGE_ESCALATED_EVENT, tid, "task", "merger",
        {"gesture": "accept", "question_id": question_id, "pr": pr, "blocking": False, "anchor": "sha"},
    )
    service.ledger.append_event(MERGED_EVENT, tid, "task", "merger", {"pr": pr, "merged_sha": "b" * 40, "into": "main"})
    return tid


def _text_only_candidate(root: Path, title: str, pr: int = 43) -> str:
    """Une candidate sans ``task.merge_escalated`` : le texte de la question suffit à la reconnaître."""
    tid = _in_review(root, title)
    write_receipt(root, tid, READY, reviewer_id="reviewer")
    service = TaskService(root)
    spec = QuestionSpec(
        f"Accepter {tid} (PR #{pr} fusionnée) ? Le merger ne l'accepte pas : chemins réservés.",
        kind="choice",
        blocking=False,
        key=f"merger:accept:{tid}",
    )
    service.ask_batch(tid, [spec], "merger")
    service.ledger.append_event(MERGED_EVENT, tid, "task", "merger", {"pr": pr, "merged_sha": "b" * 40, "into": "main"})
    return tid


def _post(host: Host, body: dict[str, Any]) -> int:
    path = f"{PREFIX}tasks/accept-merged?project={host.slug}"
    return host.request("POST", path, json.dumps(body).encode(), JSON)


def _key() -> str:
    return md.ensure_control_key().read_text(encoding="utf-8").strip()


def test_seules_les_taches_fusionnees_relues_ready_sont_candidates(tmp_path: Path) -> None:
    repo = tmp_path / "projet"
    repo.mkdir()
    merged = _merged_candidate(repo, "Fusionnée et relue READY")
    _in_review(repo, "Encore en revue")

    candidates = workspace_accept_merged.accept_merged_candidates(repo)

    assert [row["task_id"] for row in candidates] == [merged]
    assert candidates[0]["pr"] == 42


def test_la_question_du_merger_se_reconnait_a_son_texte(tmp_path: Path) -> None:
    """Le brief admet le texte seul : « Accepter <id> (PR #N fusionnée) ? » suffit, sans événement."""
    repo = tmp_path / "projet"
    repo.mkdir()
    tid = _text_only_candidate(repo, "Candidate reconnue par le texte")

    candidates = workspace_accept_merged.accept_merged_candidates(repo)

    assert [row["task_id"] for row in candidates] == [tid]
    assert candidates[0]["pr"] == 43


def test_un_recu_illisible_n_est_pas_un_ready(tmp_path: Path) -> None:
    repo = tmp_path / "projet"
    repo.mkdir()
    tid = _merged_candidate(repo, "Candidate au reçu illisible")
    receipt = repo / "_grimoire-output/reviews" / tid / "review-receipt.json"
    receipt.write_text("{pas du json", encoding="utf-8")

    assert workspace_accept_merged.accept_merged_candidates(repo) == []


def test_un_projet_sans_ledger_n_offre_aucune_candidate(tmp_path: Path) -> None:
    assert workspace_accept_merged.accept_merged_candidates(tmp_path / "vide") == []


def test_la_lecture_liste_les_candidates_pour_le_compteur(tmp_path: Path) -> None:
    repo = tmp_path / "projet"
    repo.mkdir()
    merged = _merged_candidate(repo, "Fusionnée et relue READY")

    view = workspace_get(repo, f"{PREFIX}tasks/accept-merged", {})

    assert view["count"] == 1
    assert view["candidates"][0]["task_id"] == merged


def test_sans_cle_de_controle_la_route_repond_403_et_n_ecrit_rien(cockpit: Host) -> None:  # noqa: F811
    tid = _merged_candidate(cockpit.root, "Candidate sans clé")

    assert _post(cockpit, {}) == 403
    assert _post(cockpit, {"control_key": "faux"}) == 403
    assert TaskService(cockpit.root).require(tid).status is TaskState.NEEDS_VERIFICATION
    assert EvidenceService(cockpit.root / EVIDENCE).list_verdicts(tid) == []


def test_avec_la_cle_seules_les_candidates_sont_closes(cockpit: Host) -> None:  # noqa: F811
    merged = _merged_candidate(cockpit.root, "Candidate acceptée")
    not_merged = _in_review(cockpit.root, "Encore en revue")

    assert _post(cockpit, {"control_key": _key()}) == 200

    assert TaskService(cockpit.root).require(merged).status is TaskState.CLOSED
    assert TaskService(cockpit.root).require(not_merged).status is TaskState.NEEDS_VERIFICATION


def test_la_route_rend_les_acceptees_et_les_refusees(tmp_path: Path) -> None:
    repo = tmp_path / "projet"
    repo.mkdir()
    merged = _merged_candidate(repo, "Candidate acceptée")

    result = workspace_accept_merged.accept_merged(repo, {"control_key": _key()})

    assert result == {"accepted": [merged], "refused": []}
    assert TaskService(repo).require(merged).status is TaskState.CLOSED


def test_une_tache_qui_echoue_est_rapportee_les_autres_continuent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "projet"
    repo.mkdir()
    good = _merged_candidate(repo, "Candidate acceptée")
    broken = _merged_candidate(repo, "Candidate dont la preuve échoue")
    real = workspace_accept_merged.accept_task

    def flaky(task: Any, *args: Any, **kwargs: Any) -> Any:
        if task.id == broken:
            raise RuntimeError("preuve illisible")
        return real(task, *args, **kwargs)

    monkeypatch.setattr(workspace_accept_merged, "accept_task", flaky)

    result = workspace_accept_merged.accept_merged(repo, {"control_key": _key()})

    assert result["accepted"] == [good]
    assert [row["task_id"] for row in result["refused"]] == [broken]
    assert "preuve illisible" in result["refused"][0]["reason"]
    assert TaskService(repo).require(broken).status is TaskState.NEEDS_VERIFICATION


def test_sans_cle_la_route_leve_permission_error(tmp_path: Path) -> None:
    """Le transport traduit ``PermissionError`` en 403 ; le geste direct ne peut pas l'oublier."""
    repo = tmp_path / "projet"
    repo.mkdir()
    with pytest.raises(PermissionError, match="clé de contrôle"):
        workspace_post(repo, f"{PREFIX}tasks/accept-merged", {})
