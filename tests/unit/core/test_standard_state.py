"""Quelle tâche une session porte — la règle de résolution (#138).

Ordre : ``GRIMOIRE_TASK_ID``, puis le claim actif du Mission Ledger (restreint à
``GRIMOIRE_ACTOR`` s'il est nommé), puis l'unique carte ``in_progress`` du board,
puis ``bootstrap``. Une ambiguïté saute le niveau au lieu de deviner.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.standard_state import (
    LEDGER_RELPATH,
    TASK_BOARD_RELPATH,
    active_task_id,
    claimed_task_ids,
    resolve_active_task,
)
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskState


def _ledger(root: Path) -> MissionLedger:
    return MissionLedger(root / LEDGER_RELPATH)


def _tache(ledger: MissionLedger, titre: str) -> str:
    missions = ledger.list_missions()
    mission = missions[0] if missions else ledger.create_mission(title="Travaux", origin="test")
    task = ledger.create_task(mission.id, titre, acceptance=("ok",), owner="x")
    ledger.transition_task(task.id, TaskState.READY)
    return task.id


def _board(root: Path, *statuts: tuple[str, str]) -> None:
    lignes = ["tasks:"]
    for tid, statut in statuts:
        lignes += [f"  - task_id: {tid}", f"    status: {statut}"]
    (root / TASK_BOARD_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    (root / TASK_BOARD_RELPATH).write_text("\n".join(lignes) + "\n", encoding="utf-8")


def test_sans_rien_c_est_bootstrap(tmp_path: Path) -> None:
    assert resolve_active_task(tmp_path, env={}).source == "bootstrap"
    assert active_task_id(tmp_path, env={}) == "bootstrap"


def test_lire_ne_cree_pas_de_ledger(tmp_path: Path) -> None:
    """Le hook tourne à chaque appel d'outil : il ne doit pas semer un dossier
    de ledger dans chaque projet qu'il inspecte."""
    resolve_active_task(tmp_path, env={})
    assert not (tmp_path / LEDGER_RELPATH).exists()


def test_le_claim_du_ledger_prime_sur_le_board(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _tache(ledger, "Une")
    ledger.claim_task(tid, "claude", "local")
    _board(tmp_path, ("autre", "in_progress"))
    active = resolve_active_task(tmp_path, env={})
    assert (active.task_id, active.source) == (tid, "ledger_claim")


def test_une_tache_en_cours_compte_comme_reclamee(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _tache(ledger, "Une")
    ledger.claim_task(tid, "claude", "local")
    ledger.transition_task(tid, TaskState.RUNNING)
    assert active_task_id(tmp_path, env={}) == tid


def test_deux_claims_sont_ambigus_et_le_board_tranche(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    a, b = _tache(ledger, "A"), _tache(ledger, "B")
    ledger.claim_task(a, "claude", "local")
    ledger.claim_task(b, "copilot", "local")
    _board(tmp_path, (b, "in_progress"))
    active = resolve_active_task(tmp_path, env={})
    assert (active.task_id, active.source) == (b, "board")


def test_l_acteur_nomme_ne_voit_que_ses_claims(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    a, b = _tache(ledger, "A"), _tache(ledger, "B")
    ledger.claim_task(a, "claude", "local")
    ledger.claim_task(b, "copilot", "local")
    assert claimed_task_ids(tmp_path) == [a, b]
    assert claimed_task_ids(tmp_path, actor="copilot") == [b]
    assert active_task_id(tmp_path, env={"GRIMOIRE_ACTOR": "copilot"}) == b
    # Un acteur sans claim ne se fait pas prêter celui d'un autre.
    assert active_task_id(tmp_path, env={"GRIMOIRE_ACTOR": "gemini"}) == "bootstrap"


def test_l_environnement_prime_sur_tout(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _tache(ledger, "Une")
    ledger.claim_task(tid, "claude", "local")
    active = resolve_active_task(tmp_path, env={"GRIMOIRE_TASK_ID": "sprint-9"})
    assert (active.task_id, active.source) == ("sprint-9", "env")


def test_une_tache_close_n_est_plus_courante(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    tid = _tache(ledger, "Une")
    ledger.claim_task(tid, "claude", "local")
    ledger.transition_task(tid, TaskState.READY)
    assert active_task_id(tmp_path, env={}) == "bootstrap"


def test_un_ledger_illisible_ne_casse_pas_le_hook(tmp_path: Path) -> None:
    (tmp_path / LEDGER_RELPATH).mkdir(parents=True)
    (tmp_path / LEDGER_RELPATH / "events.jsonl").write_text("{pas du json\n", encoding="utf-8")
    assert active_task_id(tmp_path, env={}) == "bootstrap"
