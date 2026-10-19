"""Reçu de revue lié à l'empreinte, exigé pour sortir de needs_verification (W3-01).

Avant : une tâche V1 au vert passait en ``needs_verification`` et en sortait
par ``grimoire task close`` sans que rien n'atteste une relecture — en profil
``starter``, le gate ``review_to_accepted`` est consultatif. Ces tests
tiennent la clôture au reçu, et le reçu à l'arbre présent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.evidence import EvidenceItem, EvidenceKind, EvidenceProfile, EvidenceService
from grimoire.missions import review_receipt as rr
from grimoire.missions.gates import GATES_FILE
from grimoire.missions.review_receipt import (
    ReviewFinding,
    ReviewReceiptError,
    check_receipt,
    tally_panel,
    write_receipt,
)
from grimoire.missions.schemas import TaskCheck, TaskState
from grimoire.missions.service import TaskRefusedError, TaskService

STANDARD = Path("_grimoire/standard")
EVIDENCE = Path("_grimoire-runtime-output/evidence")
V1_ACCEPTANCE = "revue par un pair validée"
V0_ACCEPTANCE = "code de sortie 0 de la commande"

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions:
  - id: review_to_accepted
    from: review
    to: accepted
    required_evidence: ["review_gate"]
profile_strictness:
  starter: advisory
  governed: hard_fail
"""


def _projet(root: Path, profile: str = "starter") -> Path:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / GATES_FILE).write_text(GATES, encoding="utf-8")
    (root / STANDARD / "standard-profile.yaml").write_text(f"profile: {profile}\n", encoding="utf-8")
    (root / "src.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root, check=True, capture_output=True, encoding="utf-8",
    )


def _a_relire(root: Path, *, acceptance: str = V1_ACCEPTANCE, v0: bool = False) -> tuple[TaskService, str]:
    """Une tâche en needs_verification — l'état où la dépose ``task dispatch`` au vert."""
    service = TaskService(root)
    mission = service.ledger.create_mission(title="Travaux", origin="test")
    checks = (TaskCheck(cmd="true"),) if v0 else ()
    task = service.ledger.create_task(mission.id, "Relire", acceptance=(acceptance,), owner="amelia", checks=checks)
    for state in (TaskState.READY,):
        service.ledger.transition_task(task.id, state)
    service.ledger.claim_task(task.id, actor_id="amelia", host_id="local")
    service.ledger.transition_task(task.id, TaskState.RUNNING, actor_id="amelia")
    service.ledger.transition_task(task.id, TaskState.NEEDS_VERIFICATION, actor_id="amelia")
    return service, task.id


def _refus(service: TaskService, task_id: str) -> str:
    with pytest.raises(TaskRefusedError) as info:
        service.transition(task_id, TaskState.CLOSED, "amelia")
    return info.value.verdict.refusals[0].evidence


# ── Le reçu est exigé ───────────────────────────────────────────────────────


def test_v1_sans_recu_ne_ferme_pas_meme_en_starter(tmp_path: Path) -> None:
    service, tid = _a_relire(_projet(tmp_path))

    assert _refus(service, tid) == "review.receipt_missing"
    assert service.require(tid).status is TaskState.NEEDS_VERIFICATION


def test_v1_avec_recu_ready_ferme(tmp_path: Path) -> None:
    root = _projet(tmp_path)
    service, tid = _a_relire(root)
    write_receipt(root, tid, "READY", reviewer_id="relecteur", model="sonnet")

    moved = service.transition(tid, TaskState.CLOSED, "amelia")

    assert moved.task.status is TaskState.CLOSED
    data = json.loads((root / "_grimoire-output/reviews" / tid / "review-receipt.json").read_text(encoding="utf-8"))
    assert data["verdict"] == "READY"
    assert data["reviewer_id"] == "relecteur"
    assert data["model"] == "sonnet"
    assert data["tree_fingerprint"]
    assert "evidence_pack_sha256" in data


def test_v0_hors_governed_n_exige_rien(tmp_path: Path) -> None:
    service, tid = _a_relire(_projet(tmp_path), acceptance=V0_ACCEPTANCE, v0=True)

    assert service.transition(tid, TaskState.CLOSED, "amelia").task.status is TaskState.CLOSED


def test_v0_en_governed_exige_le_recu(tmp_path: Path) -> None:
    service, tid = _a_relire(_projet(tmp_path, "governed"), acceptance=V0_ACCEPTANCE, v0=True)

    assert _refus(service, tid) == "review.receipt_missing"


# ── Le reçu tient à l'arbre ─────────────────────────────────────────────────


@pytest.mark.parametrize("git", [False, True], ids=["fs", "git"])
def test_modification_apres_le_recu_le_perime(tmp_path: Path, git: bool) -> None:
    root = _projet(tmp_path)
    if git:
        _git(root, "init", "-q")
        _git(root, "add", "src.py")
        _git(root, "commit", "-q", "-m", "base")
    service, tid = _a_relire(root)
    write_receipt(root, tid, "READY", reviewer_id="relecteur")
    assert check_receipt(root, service.require(tid)) is None

    (root / "src.py").write_text("x = 2\n", encoding="utf-8")

    assert _refus(service, tid) == "review.receipt_stale"


def test_le_gate_recalcule_l_empreinte_a_chaque_appel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _projet(tmp_path)
    service, tid = _a_relire(root)
    write_receipt(root, tid, "READY", reviewer_id="relecteur")
    calls: list[Path] = []
    real = rr.compute_tree_fingerprint

    def spy(path: Path, **kwargs: object) -> str:
        calls.append(path)
        return real(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(rr, "compute_tree_fingerprint", spy)
    task = service.require(tid)
    for _ in range(3):
        assert not service.gate(task, TaskState.CLOSED).blocked
    assert len(calls) == 3, "une empreinte par appel du gate, jamais un cache"

    (root / "src.py").write_text("x = 3\n", encoding="utf-8")
    assert service.gate(task, TaskState.CLOSED).refusals[0].evidence == "review.receipt_stale"


def test_un_pack_de_preuve_change_apres_le_recu_le_perime(tmp_path: Path) -> None:
    root = _projet(tmp_path)
    service, tid = _a_relire(root)
    write_receipt(root, tid, "READY", reviewer_id="relecteur")

    EvidenceService(root / EVIDENCE).create_pack(
        task_id=tid, profile=EvidenceProfile.STANDARD,
        items=[EvidenceItem(id="e1", kind=EvidenceKind.LOG, uri="log://", digest="d1", summary=V1_ACCEPTANCE)],
        acceptance=(V1_ACCEPTANCE,),
    )

    assert _refus(service, tid) == "review.receipt_stale"


def test_les_ecritures_du_kit_ne_perime_pas_le_recu(tmp_path: Path) -> None:
    """Journal, refus tracés, board reprojeté : le kit écrit entre le reçu et le gate, sans le périmer."""
    root = _projet(tmp_path)
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    service, tid = _a_relire(root)
    autre = service.ledger.create_task(service.require(tid).mission_id, "Autre", acceptance=(V1_ACCEPTANCE,)).id
    write_receipt(root, tid, "READY", reviewer_id="relecteur")

    service.comment(tid, "relu", "amelia")
    service.transition(autre, TaskState.READY, "amelia")  # reprojette task-board.yaml
    assert (root / STANDARD / "task-board.yaml").is_file()

    assert service.transition(tid, TaskState.CLOSED, "amelia").task.status is TaskState.CLOSED


@pytest.mark.parametrize("backend", ["auto", "local"])
def test_la_memoire_ecrite_a_la_cloture_d_une_autre_tache_ne_perime_pas_le_recu(
    tmp_path: Path, backend: str
) -> None:
    """Fermer A consolide la mémoire sous ``_grimoire/_memory`` : le reçu de B doit rester frais (W3-01-S1)."""
    root = _projet(tmp_path)
    memory_section = "" if backend == "auto" else f"memory:\n  backend: {backend}\n"
    (root / "project-context.yaml").write_text(f"project:\n  name: p\n{memory_section}", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    service, premiere = _a_relire(root)
    seconde = service.ledger.create_task(
        service.require(premiere).mission_id, "Seconde", acceptance=(V1_ACCEPTANCE,), owner="amelia"
    ).id
    service.ledger.transition_task(seconde, TaskState.READY)
    service.ledger.claim_task(seconde, actor_id="amelia", host_id="local")
    service.ledger.transition_task(seconde, TaskState.RUNNING, actor_id="amelia")
    service.ledger.transition_task(seconde, TaskState.NEEDS_VERIFICATION, actor_id="amelia")
    write_receipt(root, premiere, "READY", reviewer_id="relecteur")
    write_receipt(root, seconde, "READY", reviewer_id="relecteur")

    service.transition(premiere, TaskState.CLOSED, "amelia")

    ecrits = [p for p in (root / "_grimoire/_memory").rglob("*") if p.is_file()]
    assert ecrits, "la clôture de la première tâche doit avoir écrit la mémoire (sinon le test ne prouve rien)"
    assert check_receipt(root, service.require(seconde)) is None
    assert service.transition(seconde, TaskState.CLOSED, "amelia").task.status is TaskState.CLOSED


@pytest.mark.parametrize("git", [False, True], ids=["fs", "git"])
def test_un_autre_path_au_gate_ne_perime_pas_le_recu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git: bool
) -> None:
    """Le reçu est une empreinte de contenu : le ``PATH`` du processus qui lance le gate n'en fait pas partie (W3-01-S2)."""
    root = _projet(tmp_path)
    (root / "tests").mkdir()
    (root / "tests/test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    if git:
        _git(root, "init", "-q")
        _git(root, "add", ".")
        _git(root, "commit", "-q", "-m", "base")
    service, tid = _a_relire(root)
    git_dir = str(Path(shutil.which("git") or "/usr/bin/git").parent)  # git doit rester joignable dans les deux cas
    monkeypatch.setenv("PATH", os.pathsep.join([str(Path(sys.executable).parent), git_dir]))
    write_receipt(root, tid, "READY", reviewer_id="relecteur")

    monkeypatch.setenv("PATH", git_dir)  # cockpit ou MCP lancé sans le venv

    assert check_receipt(root, service.require(tid)) is None


def test_l_empreinte_de_contenu_seul_ignore_le_path_et_l_autre_pas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le défaut de ``compute_tree_fingerprint`` reste lié à la toolchain (run de test) ; l'option la retire."""
    from grimoire.core.standard_checks.tree_fingerprint import compute_tree_fingerprint

    root = _projet(tmp_path)
    (root / "tests").mkdir()
    (root / "tests/test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    avec, sans = compute_tree_fingerprint(root), compute_tree_fingerprint(root, include_toolchain=False)

    monkeypatch.setenv("PATH", str(tmp_path / "vide"))

    assert compute_tree_fingerprint(root) != avec
    assert compute_tree_fingerprint(root, include_toolchain=False) == sans


def test_commiter_le_meme_contenu_ne_perime_pas_le_recu(tmp_path: Path) -> None:
    """Contenu seul : valider (``git commit``) ce qui a été relu ne change aucun octet de l'arbre (W3-01-S4)."""
    root = _projet(tmp_path)
    _git(root, "init", "-q")
    _git(root, "add", "src.py")
    _git(root, "commit", "-q", "-m", "base")
    service, tid = _a_relire(root)
    (root / "src.py").write_text("x = 2\n", encoding="utf-8")  # modifié, non commité
    (root / "neuf.py").write_text("y = 1\n", encoding="utf-8")  # non suivi
    write_receipt(root, tid, "READY", reviewer_id="relecteur")

    _git(root, "add", "src.py", "neuf.py")
    _git(root, "commit", "-q", "-m", "ce qui a été relu")

    assert check_receipt(root, service.require(tid)) is None
    assert service.transition(tid, TaskState.CLOSED, "amelia").task.status is TaskState.CLOSED


def test_modifier_apres_le_commit_perime_encore_le_recu(tmp_path: Path) -> None:
    """Le contenu reste opposable en mode git : un octet changé après le reçu le périme, commité ou non."""
    root = _projet(tmp_path)
    _git(root, "init", "-q")
    _git(root, "add", "src.py")
    _git(root, "commit", "-q", "-m", "base")
    service, tid = _a_relire(root)
    write_receipt(root, tid, "READY", reviewer_id="relecteur")

    (root / "src.py").write_text("x = 3\n", encoding="utf-8")
    assert _refus(service, tid) == "review.receipt_stale"
    _git(root, "commit", "-q", "-am", "autre contenu")
    assert _refus(service, tid) == "review.receipt_stale"


def test_un_fichier_non_suivi_retouche_perime_le_recu_en_git(tmp_path: Path) -> None:
    root = _projet(tmp_path)
    _git(root, "init", "-q")
    _git(root, "add", "src.py")
    _git(root, "commit", "-q", "-m", "base")
    (root / "neuf.py").write_text("y = 1\n", encoding="utf-8")
    service, tid = _a_relire(root)
    write_receipt(root, tid, "READY", reviewer_id="relecteur")

    (root / "neuf.py").write_text("y = 2\n", encoding="utf-8")

    assert _refus(service, tid) == "review.receipt_stale"


def test_l_empreinte_de_contenu_est_la_meme_avant_et_apres_commit(tmp_path: Path) -> None:
    """``content_only`` : l'empreinte ne dépend ni de HEAD, ni du status, ni du diff ; le défaut les garde (run de test)."""
    from grimoire.core.standard_checks.tree_fingerprint import compute_tree_fingerprint

    root = _projet(tmp_path)
    _git(root, "init", "-q")
    _git(root, "add", "src.py")
    _git(root, "commit", "-q", "-m", "base")
    (root / "src.py").write_text("x = 2\n", encoding="utf-8")
    avant_contenu = compute_tree_fingerprint(root, content_only=True)
    avant_run = compute_tree_fingerprint(root)

    _git(root, "commit", "-q", "-am", "valide")

    assert compute_tree_fingerprint(root, content_only=True) == avant_contenu
    assert compute_tree_fingerprint(root) != avant_run  # l'empreinte d'un run de test garde HEAD


# ── Un constat sans commande reste une suggestion ───────────────────────────


def test_not_ready_sans_evidence_cmd_est_refuse_a_l_ecriture(tmp_path: Path) -> None:
    root = _projet(tmp_path)
    _, tid = _a_relire(root)

    with pytest.raises(ReviewReceiptError) as info:
        write_receipt(root, tid, "NOT-READY", findings=[ReviewFinding("le nommage est confus")], reviewer_id="r")

    assert info.value.code == "review.finding_ungrounded"


def test_not_ready_sans_evidence_cmd_ecrit_a_la_main_est_refuse_au_gate(tmp_path: Path) -> None:
    root = _projet(tmp_path)
    service, tid = _a_relire(root)
    receipt = write_receipt(root, tid, "READY", reviewer_id="r")
    path = root / "_grimoire-output/reviews" / tid / "review-receipt.json"
    data = receipt.to_dict()
    data["verdict"] = "NOT-READY"
    data["findings"] = [{"summary": "le nommage est confus", "evidence_cmd": ""}]
    path.write_text(json.dumps(data), encoding="utf-8")

    assert _refus(service, tid) == "review.finding_ungrounded"


def test_not_ready_fonde_bloque_la_cloture(tmp_path: Path) -> None:
    root = _projet(tmp_path)
    service, tid = _a_relire(root)
    write_receipt(
        root, tid, "NOT-READY", reviewer_id="r",
        findings=[ReviewFinding("régression sur l'import", "python -c 'import src'"), ReviewFinding("style")],
    )

    assert _refus(service, tid) == "review.not_ready"


def test_recu_illisible_est_un_refus(tmp_path: Path) -> None:
    root = _projet(tmp_path)
    service, tid = _a_relire(root)
    path = root / "_grimoire-output/reviews" / tid / "review-receipt.json"
    path.parent.mkdir(parents=True)
    path.write_text("{pas du json", encoding="utf-8")

    assert _refus(service, tid) == "review.receipt_invalid"


# ── Panel : un vote sans verdict est une abstention ─────────────────────────


def _vote(verdict: str | None, fp: str = "f1") -> dict[str, object]:
    return {"pins": {}} if verdict is None else {"verdict": verdict, "tree_fingerprint": fp}


def test_panel_k3_deux_abstentions_est_inconclusive() -> None:
    tally = tally_panel([_vote("READY"), _vote(None), None], "V1")
    assert (tally.verdict, tally.approve, tally.abstain) == ("inconclusive", 1, 2)


def test_panel_v2_deux_contre_un_est_rouge() -> None:
    assert tally_panel([_vote("READY"), _vote("READY"), _vote("NOT-READY")], "V2").verdict == "red"


def test_panel_v1_deux_contre_un_est_vert() -> None:
    assert tally_panel([_vote("READY"), _vote("READY"), _vote("NOT-READY")], "V1").verdict == "green"


def test_panel_fini_sans_verdict_n_est_jamais_un_vert() -> None:
    assert tally_panel([_vote(None), _vote(None), _vote(None)], "V1").verdict == "inconclusive"


def test_panel_empreintes_divergentes_l_invalident() -> None:
    tally = tally_panel([_vote("READY", "a"), _vote("READY", "a"), _vote("READY", "b")], "V1")
    assert tally.verdict == "invalid"


# ── La commande `task review` ───────────────────────────────────────────────


@pytest.mark.covers("cli:task review")
def test_cli_task_review_ecrit_le_recu_qui_ouvre_la_cloture(tmp_path: Path) -> None:
    root = _projet(tmp_path)
    service, tid = _a_relire(root)
    assert _refus(service, tid) == "review.receipt_missing"

    res = CliRunner().invoke(
        app, ["--output", "json", "task", "review", tid, "--verdict", "ready", "--reviewer", "relecteur",
              "--model", "sonnet", "--finding", "style discutable", "--project-root", str(root)],
    )

    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert (out["verdict"], out["reviewer_id"], out["model"], out["task_id"]) == ("READY", "relecteur", "sonnet", tid)
    assert out["tree_fingerprint"] and [f["grounded"] for f in out["findings"]] == [False]
    on_disk = json.loads((root / "_grimoire-output/reviews" / tid / "review-receipt.json").read_text(encoding="utf-8"))
    assert on_disk["tree_fingerprint"] == out["tree_fingerprint"]
    assert TaskService(root).transition(tid, TaskState.CLOSED, "amelia").task.status is TaskState.CLOSED


@pytest.mark.covers("cli:task review")
def test_cli_task_review_not_ready_fonde_bloque_la_cloture_et_le_texte_compte_les_constats(tmp_path: Path) -> None:
    root = _projet(tmp_path)
    _, tid = _a_relire(root)

    res = CliRunner().invoke(
        app, ["task", "review", tid, "--verdict", "NOT-READY", "--finding", "import cassé :: python -c 'import src'",
              "--finding", "nommage", "--project-root", str(root)],
    )

    assert res.exit_code == 0, res.output
    assert "NOT-READY" in res.output and "1 constat(s) vérifiable(s)" in res.output
    assert _refus(TaskService(root), tid) == "review.not_ready"


@pytest.mark.covers("cli:task review")
def test_cli_task_review_refuse_sans_rien_ecrire(tmp_path: Path) -> None:
    root = _projet(tmp_path)
    _, tid = _a_relire(root)
    receipt = root / "_grimoire-output/reviews" / tid / "review-receipt.json"
    base = ["task", "review", tid, "--project-root", str(root)]

    ungrounded = CliRunner().invoke(app, [*base, "--verdict", "NOT-READY", "--finding", "juste une suggestion"])
    assert ungrounded.exit_code == 1 and "NOT-READY sans constat vérifiable" in ungrounded.output
    unknown = CliRunner().invoke(app, [*base, "--verdict", "PEUT-ETRE"])
    assert unknown.exit_code == 1 and "PEUT-ETRE" in unknown.output
    missing = CliRunner().invoke(app, ["task", "review", "GAO-inconnue-001", "--verdict", "READY", "--project-root", str(root)])
    assert missing.exit_code == 1
    assert not receipt.exists()
