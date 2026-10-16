"""Issue #710 — claims du Mission Ledger : expiration, héritage entre sessions, atomicité.

Constat sur 7c172595 (3.62.1, après #696 et #697) :

1. ``TaskClaim.is_expired`` n'était lu ni par ``_active_claims`` ni par
   ``resolve_active_task`` : un claim expiré restait la tâche active.
2. Règle 3 de ``resolve_active_task`` : une session sans claim propre prenait
   le claim unique du projet, même rattaché à une *autre* session.
3. ``MissionLedger.transition_task`` validait l'état lu avant le verrou, et
   ``_atomic_append`` ne verrouillait que l'écriture : deux claims simultanés
   réussissaient tous les deux.

Hors de ce fichier (et de la PR) : renouvellement de bail (``task renew``) et
récolte (``expired_claims()``, lot #721).
"""

from __future__ import annotations

import multiprocessing
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.standard_state import resolve_active_task
from grimoire.hosts.decisions import Outcome
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskClaim, TaskState
from grimoire.missions.service import TaskService

SESSION_A = "aaaaaaaa-0000-4000-8000-00000000071a"
SESSION_B = "bbbbbbbb-0000-4000-8000-00000000071b"
ACCEPTATION = "le claim respecte son bail et sa session"
LEDGER = "_grimoire-runtime-output/ledger"


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    return tmp_path


def _ready(root: Path, title: str) -> str:
    service = TaskService(root)
    tid = service.add(title, (ACCEPTATION,), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    return tid


def _claimed(root: Path, title: str, *, session: str = "", expired: bool = False, actor: str = "claude") -> str:
    """Un claim — posé il y a 5 h (TTL 4 h) quand *expired*."""
    tid = _ready(root, title)
    service = TaskService(root)
    now = datetime.now(UTC) - timedelta(hours=5) if expired else None
    claim = TaskClaim.new(actor_id=actor, host_id="local", now=now)
    assert claim.is_expired() is expired
    service.transition(tid, TaskState.CLAIMED, actor, claim=claim)
    if session:
        service.attach_session(tid, session, session_host="claude-code-cli", actor=actor)
    return tid


def _hook(root: Path, event: HookEvent, session: str, **extra: Any) -> tuple[dict[str, Any], Any]:
    payload = {"session_id": session, "hook_event_name": event.value, "cwd": str(root), **extra}
    rendered, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)
    return rendered, decision


def _write(root: Path, session: str) -> None:
    _hook(
        root,
        HookEvent.POST_TOOL_USE,
        session,
        tool_name="Edit",
        tool_input={"file_path": str(root / "src/app.py"), "old_string": "a", "new_string": "b"},
        tool_response={"filePath": str(root / "src/app.py"), "success": True},
    )


# ── (a) un claim expiré n'est plus la tâche active ──────────────────────────


def test_un_claim_expire_sans_session_n_est_plus_la_tache_active(governed: Path) -> None:
    tid = _claimed(governed, "Bail perdu", expired=True)
    for session in ("", SESSION_B):
        active = resolve_active_task(governed, env={}, session_id=session)
        assert active.task_id == "bootstrap", session
        assert active.source == "bootstrap"
        assert active.expired == (tid,)


def test_un_claim_expire_rattache_a_la_session_n_est_plus_sa_tache(governed: Path) -> None:
    tid = _claimed(governed, "Bail perdu", session=SESSION_A, expired=True)
    active = resolve_active_task(governed, env={}, session_id=SESSION_A)
    assert (active.task_id, active.source) == ("bootstrap", "bootstrap")
    assert active.expired == (tid,)


def test_un_claim_expire_ne_compte_pas_parmi_les_candidates(governed: Path) -> None:
    vivant = _claimed(governed, "Vivant")
    _claimed(governed, "Mort", expired=True)
    active = resolve_active_task(governed, env={}, session_id=SESSION_B)
    assert (active.task_id, active.source) == (vivant, "ledger_claim")


def test_le_prompt_dit_claim_expire(governed: Path) -> None:
    tid = _claimed(governed, "Bail perdu", session=SESSION_A, expired=True)
    rendered, _ = _hook(governed, HookEvent.USER_PROMPT_SUBMIT, SESSION_A, prompt="continue")
    context = rendered["hookSpecificOutput"]["additionalContext"]
    assert tid in context and "expiré" in context


def test_stop_apres_ecriture_dit_claim_expire(governed: Path) -> None:
    tid = _claimed(governed, "Bail perdu", session=SESSION_A, expired=True)
    _write(governed, SESSION_A)
    _, decision = _hook(governed, HookEvent.STOP, SESSION_A, stop_hook_active=False)
    assert decision.outcome is Outcome.BLOCK
    assert tid in decision.reason and "expiré" in decision.reason


def test_stop_sans_ecriture_dit_claim_expire_sans_bloquer(governed: Path) -> None:
    tid = _claimed(governed, "Bail perdu", session=SESSION_A, expired=True)
    rendered, decision = _hook(governed, HookEvent.STOP, SESSION_A, stop_hook_active=False)
    assert decision.outcome is Outcome.ALLOW
    assert tid in decision.context and "expiré" in decision.context
    assert "encore en état" not in decision.context
    assert tid in rendered["systemMessage"]


# ── (b) pas d'héritage du claim d'une autre session ─────────────────────────


def test_une_session_n_herite_pas_du_claim_rattache_a_une_autre(governed: Path) -> None:
    tid_a = _claimed(governed, "Tache de A", session=SESSION_A)
    b = resolve_active_task(governed, env={}, session_id=SESSION_B)
    assert b.task_id != tid_a
    assert (b.task_id, b.source) == ("bootstrap", "bootstrap")
    # A garde la sienne.
    a = resolve_active_task(governed, env={}, session_id=SESSION_A)
    assert (a.task_id, a.source) == (tid_a, "session_claim")


def test_le_hook_de_b_ne_rattache_ni_ne_juge_la_tache_de_a(governed: Path) -> None:
    tid_a = _claimed(governed, "Tache de A", session=SESSION_A)
    _, decision = _hook(governed, HookEvent.USER_PROMPT_SUBMIT, SESSION_B, prompt="corrige")
    assert TaskService(governed).require(tid_a).claim.session_id == SESSION_A  # type: ignore[union-attr]
    assert decision.detail.get("task_id", "bootstrap") != tid_a
    _write(governed, SESSION_B)
    _, stop = _hook(governed, HookEvent.STOP, SESSION_B, stop_hook_active=False)
    assert stop.outcome is Outcome.BLOCK  # écriture hors tâche, pas un gate de la tâche de A
    assert stop.detail.get("blocked_on") == "no_active_task"


def test_sans_session_identifiable_le_claim_unique_reste_la_tache(governed: Path) -> None:
    """CLI lancé hors hôte : on ne sait pas qui l'on est, le claim unique vivant reste désigné."""
    tid_a = _claimed(governed, "Tache de A", session=SESSION_A)
    active = resolve_active_task(governed, env={}, session_id="")
    assert (active.task_id, active.source) == (tid_a, "ledger_claim")


# ── (c) claim atomique : un seul gagnant ────────────────────────────────────


def test_deux_services_charges_avant_toute_ecriture_un_seul_claim_gagne(governed: Path) -> None:
    """La reproduction de l'issue : deux TaskService chargés, puis deux claims."""
    tid = _ready(governed, "Disputee")
    service_a, service_b = TaskService(governed), TaskService(governed)
    assert service_a.require(tid).status is TaskState.READY
    assert service_b.require(tid).status is TaskState.READY

    service_a.claim(tid, "agent-a")
    with pytest.raises(GrimoireMissionError) as refused:
        service_b.claim(tid, "agent-b")

    assert "agent-a" in str(refused.value)
    task = TaskService(governed).require(tid)
    assert task.claim is not None and task.claim.actor_id == "agent-a"
    events = (governed / LEDGER / "events.jsonl").read_text(encoding="utf-8")
    assert events.count('"to_state": "claimed"') == 1


def test_deux_threads_un_seul_claim_gagne(governed: Path) -> None:
    tid = _ready(governed, "Disputee")
    services = [TaskService(governed), TaskService(governed)]
    for s in services:
        s.require(tid)
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def run(i: int) -> None:
        barrier.wait()
        try:
            services[i].claim(tid, f"agent-{i}")
            results[f"agent-{i}"] = "ok"
        except GrimoireMissionError as exc:
            results[f"agent-{i}"] = f"refused: {exc}"

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(v == "ok" for v in results.values()) == [False, True], results


def _claim_in_process(root: str, task_id: str, actor: str, barrier: Any, out: Any) -> None:
    ledger = MissionLedger(Path(root))
    ledger.get_task(task_id)  # état chargé AVANT la barrière : la fenêtre du défaut
    barrier.wait()
    try:
        ledger.claim_task(task_id, actor, "local")
        out.put((actor, "ok"))
    except GrimoireMissionError as exc:
        out.put((actor, f"refused: {exc}"))


@pytest.mark.skipif(not hasattr(os, "fork"), reason="processus forkés : POSIX")
def test_deux_processus_cinquante_essais_exactement_un_gagnant(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("fork")
    for trial in range(50):
        root = tmp_path / f"trial-{trial}"
        ledger = MissionLedger(root)
        mission = ledger.create_mission("M", origin="test")
        task = ledger.create_task(mission.id, "T", acceptance=("a",))
        ledger.transition_task(task.id, TaskState.READY)
        barrier, out = ctx.Barrier(2), ctx.Queue()
        procs = [
            ctx.Process(target=_claim_in_process, args=(str(root), task.id, f"agent-{i}", barrier, out))
            for i in range(2)
        ]
        for p in procs:
            p.start()
        results = dict(out.get(timeout=30) for _ in procs)
        for p in procs:
            p.join(timeout=30)
        winners = [a for a, r in results.items() if r == "ok"]
        assert len(winners) == 1, (trial, results)
        loser = next(a for a in results if a not in winners)
        assert winners[0] in results[loser], (trial, results)
        replayed = MissionLedger(root).get_task(task.id)
        assert replayed is not None and replayed.claim is not None
        assert replayed.claim.actor_id == winners[0]


def test_un_claim_sans_verrou_disponible_est_refuse(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-closed : sans verrou inter-processus, un claim n'est pas un compare-and-set — refusé."""
    from grimoire.missions import ledger as ledger_mod

    tid = _ready(governed, "Sans verrou")
    monkeypatch.setattr(ledger_mod, "_acquire_os_lock", lambda fh: False)
    with pytest.raises(GrimoireMissionError, match="verrou"):
        TaskService(governed).claim(tid, "agent-a")
    assert TaskService(governed).require(tid).status is TaskState.READY


# ── (d) non-régression ──────────────────────────────────────────────────────


def test_l_attach_explicite_sur_un_claim_expire_cede_au_claim_vivant_de_la_session(governed: Path) -> None:
    mort = _claimed(governed, "Mort", session=SESSION_A, expired=True)
    vivant = _claimed(governed, "Vivant", session=SESSION_A, actor="lot-l1")
    TaskService(governed).attach_session(mort, SESSION_A, actor="claude", explicit=True)
    active = resolve_active_task(governed, env={}, session_id=SESSION_A)
    assert (active.task_id, active.source) == (vivant, "session_claim")


def test_grimoire_task_id_prime_meme_sur_un_claim_expire(governed: Path) -> None:
    tid = _claimed(governed, "Bail perdu", session=SESSION_A, expired=True)
    active = resolve_active_task(governed, env={"GRIMOIRE_TASK_ID": tid}, session_id=SESSION_A)
    assert (active.task_id, active.source) == (tid, "env")
