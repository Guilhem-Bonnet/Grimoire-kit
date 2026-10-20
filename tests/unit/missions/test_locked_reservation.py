"""W1-07 : la réservation de fichiers se juge sous le verrou d'écriture du ledger, et nul claim n'est éternel."""

from __future__ import annotations

import json
import multiprocessing
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import DEFAULT_CLAIM_TTL_SECONDS, TaskClaim, TaskState
from grimoire.missions.service import TaskRefusedError, TaskService

STANDARD = Path("_grimoire/standard")
FICHIER = "src/pkg/partage.py"


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    (tmp_path / STANDARD).mkdir(parents=True)
    return tmp_path


def _deux_taches(root: Path) -> tuple[str, str]:
    ledger = TaskService(root).ledger
    mission = ledger.create_mission("Réservation", origin="user")
    ids = []
    for titre in ("A", "B"):
        task = ledger.create_task(mission.id, titre, acceptance=("ok",), owner="dev")
        ledger.transition_task(task.id, TaskState.READY, actor_id="dev")
        ids.append(task.id)
    return ids[0], ids[1]


def _reserver(root: str, task_id: str, actor: str, barrier: Any, out: Any) -> None:
    service = TaskService(Path(root))
    service.require(task_id)  # état chargé AVANT la barrière : la fenêtre de la course
    barrier.wait()
    try:
        service.claim(task_id, actor, files=(FICHIER,))
        out.put((actor, "ok"))
    except (GrimoireMissionError, TaskRefusedError) as exc:
        out.put((actor, f"refused: {exc}"))


def test_le_ledger_refuse_sous_son_verrou_un_fichier_pris_entre_le_controle_et_l_ecriture(projet: Path) -> None:
    """Le contrôle du service passe (rien n'est encore pris), un autre claim s'intercale, le ledger refuse."""
    a, b = _deux_taches(projet)
    service = TaskService(projet)
    original = service._check_exclusive_files

    def _controle_puis_concurrent(task: Any, target: Any, claim: Any) -> None:
        original(task, target, claim)  # passe : B voit le fichier libre
        TaskService(projet).claim(a, "amelia", files=(FICHIER,))  # A s'intercale avant l'écriture de B

    service._check_exclusive_files = _controle_puis_concurrent  # type: ignore[method-assign]
    with pytest.raises(GrimoireMissionError) as refus:
        service.claim(b, "marcus", files=(FICHIER,))
    assert FICHIER in str(refus.value) and a in str(refus.value)
    fresh = TaskService(projet)
    assert fresh.require(b).status is TaskState.READY
    assert fresh.require(a).status is TaskState.CLAIMED


def test_deux_threads_meme_fichier_un_seul_reussit(projet: Path) -> None:
    a, b = _deux_taches(projet)
    services = {a: TaskService(projet), b: TaskService(projet)}
    for tid, s in services.items():
        s.require(tid)
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def run(tid: str) -> None:
        barrier.wait()
        try:
            services[tid].claim(tid, f"agent-{tid}", files=(FICHIER,))
            results[tid] = "ok"
        except (GrimoireMissionError, TaskRefusedError):
            results[tid] = "refused"

    threads = [threading.Thread(target=run, args=(t,)) for t in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values()) == ["ok", "refused"], results


@pytest.mark.skipif(not hasattr(os, "fork"), reason="processus forkés : POSIX")
def test_deux_processus_meme_fichier_un_seul_reussit(projet: Path) -> None:
    ctx = multiprocessing.get_context("fork")
    for essai in range(10):
        root = projet / f"essai{essai}"
        (root / STANDARD).mkdir(parents=True)
        a, b = _deux_taches(root)
        barrier, out = ctx.Barrier(2), ctx.Queue()
        procs = [ctx.Process(target=_reserver, args=(str(root), t, f"agent-{t}", barrier, out)) for t in (a, b)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)
        got = [out.get(timeout=5) for _ in procs]
        assert sorted(v == "ok" for _, v in got) == [False, True], (essai, got)


def test_un_claim_expire_libere_le_fichier(projet: Path) -> None:
    a, b = _deux_taches(projet)
    ledger = TaskService(projet).ledger
    perime = TaskClaim.new("amelia", "local", (FICHIER,), now=datetime.now(UTC) - timedelta(hours=9))
    ledger.transition_task(a, TaskState.CLAIMED, actor_id="amelia", claim=perime)
    TaskService(projet).claim(b, "marcus", files=(FICHIER,))
    assert TaskService(projet).require(b).status is TaskState.CLAIMED


def _poser_claim_herite(root: Path, task_id: str, *, age: timedelta) -> None:
    """Un claim d'avant les baux : réécrit dans le journal sans `expires_at`, horodaté il y a *age*."""
    path = root / "_grimoire-runtime-output" / "ledger" / "events.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for rec in lines:
        claim = rec.get("payload", {}).get("claim")
        if rec["event_type"] == "task.transitioned" and rec["payload"].get("task_id") == task_id and claim:
            claim.pop("expires_at", None)
            rec["created_at"] = (datetime.now(UTC) - age).isoformat()
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in lines), encoding="utf-8")


def test_un_claim_herite_sans_expires_at_ancien_est_expire_et_libere_le_fichier(projet: Path) -> None:
    a, b = _deux_taches(projet)
    TaskService(projet).claim(a, "amelia", files=(FICHIER,))
    _poser_claim_herite(projet, a, age=timedelta(days=30))

    held = TaskService(projet).require(a)
    assert held.claim is not None and held.claim.expires_at, "le TTL par défaut est dérivé au rejeu"
    assert held.claim.is_expired()
    TaskService(projet).claim(b, "marcus", files=(FICHIER,))


def test_un_claim_herite_sans_expires_at_recent_tient_jusqu_a_son_ttl(projet: Path) -> None:
    a, b = _deux_taches(projet)
    TaskService(projet).claim(a, "amelia", files=(FICHIER,))
    _poser_claim_herite(projet, a, age=timedelta(minutes=5))

    held = TaskService(projet).require(a).claim
    assert held is not None and not held.is_expired()
    expires = datetime.fromisoformat(held.expires_at)
    assert abs((expires - datetime.now(UTC)).total_seconds() - (DEFAULT_CLAIM_TTL_SECONDS - 300)) < 60
    with pytest.raises(TaskRefusedError):
        TaskService(projet).claim(b, "marcus", files=(FICHIER,))


def test_un_claim_a_expiration_explicite_n_est_pas_touche(projet: Path) -> None:
    a, _ = _deux_taches(projet)
    TaskService(projet).claim(a, "amelia")
    first = TaskService(projet).require(a).claim
    assert first is not None
    ledger = MissionLedger(projet / "_grimoire-runtime-output" / "ledger")
    claim = ledger.get_task(a).claim
    assert claim is not None and claim.expires_at == first.expires_at
