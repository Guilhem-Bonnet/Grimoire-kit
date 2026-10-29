"""P90 — la vivacité d'une tâche : un champ ``presence`` calculé côté serveur, cinq états exclusifs."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.missions.lease import LeaseFile, lease_path, write_lease
from grimoire.missions.liveness import PRESENCE_STATES, last_sign, presence
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.tools.workspace_api import tasks_view

SESSION = "aaaaaaaa-0000-4000-8000-00000000000a"
#: Le ledger refuse une horloge antérieure à son dernier événement : « maintenant » est le vrai maintenant.
NOW = datetime.now(UTC)


@pytest.fixture
def governed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    global NOW
    NOW = datetime.now(UTC) + timedelta(seconds=5)  # relu par test : la collecte peut précéder l'exécution de plusieurs minutes
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    return tmp_path


def _task(root: Path, title: str, *, ready: bool = True) -> str:
    service = TaskService(root)
    added = service.add(title, ("fait",), owner="guilhem", actor="claude", ready=ready)
    service.context(added.task.id)
    return added.task.id


def _held(root: Path, title: str, renewed_ago_s: int, *, now: datetime, session: str = SESSION) -> str:
    tid = _task(root, title)
    service = TaskService(root)
    service.claim(tid, "claude", host="local")
    service.attach_session(tid, session, session_host="claude-code-cli", actor="claude")
    task = service.ledger.get_task(tid)
    assert task is not None and task.claim is not None
    write_lease(
        lease_path(service.ledger._root, tid),
        LeaseFile(tid, task.claim.lease_epoch, (now - timedelta(seconds=renewed_ago_s)).isoformat(), ttl_seconds=1200),
    )
    return tid


def _presence(root: Path, tid: str, *, probe: dict[str, str] | None = None, now: datetime | None = None) -> dict:
    view = tasks_view(root, probe=(lambda: probe), now=now or NOW)
    return next(t for t in view["tasks"] if t["id"] == tid)["presence"]


def test_les_cinq_etats_sont_rendus_par_le_tableau_et_jamais_deux_a_la_fois(governed: Path) -> None:
    # none : prête, sans agent
    none_id = _task(governed, "Sans agent")
    # working : sonde occupée
    other = "bbbbbbbb-0000-4000-8000-00000000000b"
    working_id = _held(governed, "Au travail", 600, now=NOW, session=other)
    # blocked : écrite bloquée avec sa cause
    blocked_id = _task(governed, "Bloquee")
    TaskService(governed).ledger.transition_task(blocked_id, TaskState.BLOCKED, reason="attend la CI")
    # resume : bail perdu puis rendu par la récolte
    resume_id = _held(governed, "A reprendre", 3600, now=NOW)
    TaskService(governed).ledger.reap(fenced_hosts=frozenset({"host-claude-code-cli"}), now=NOW)
    view = tasks_view(governed, probe=lambda: {other: "busy"}, now=NOW)
    cards = {t["id"]: t["presence"] for t in view["tasks"]}
    assert cards[none_id]["state"] == "none"
    assert cards[blocked_id]["state"] == "blocked" and "CI" in cards[blocked_id]["cause"]
    assert all(c["state"] in PRESENCE_STATES for c in cards.values())
    # le claim de « Au travail » et celui de « A reprendre » portent la même session : on les isole
    assert cards[working_id]["state"] == "working"
    assert cards[resume_id]["state"] == "resume"
    silent_id = _held(governed, "Silencieuse", 600, now=NOW, session="cccccccc-0000-4000-8000-00000000000c")
    assert _presence(governed, silent_id, probe={})["state"] == "silent"
    assert {c["state"] for c in cards.values()} == {"none", "blocked", "working", "resume"}


def test_silent_working_et_resume_isoles(governed: Path) -> None:
    tid = _held(governed, "Isolee", 600, now=NOW)  # bail renouvelé il y a 10 min, valide encore 10 min
    silent = _presence(governed, tid, probe=None)
    assert silent["state"] == "silent"
    assert silent["lease_left_s"] == 600 and silent["resume_at"]
    assert _presence(governed, tid, probe={SESSION: "busy"})["state"] == "working"
    assert _presence(governed, tid, probe={SESSION: "ended"})["state"] == "resume"
    recent = _held(governed, "Battement recent", 60, now=NOW)
    assert _presence(governed, recent, probe=None)["state"] == "working"


def test_un_bail_perdu_rend_a_reprendre_avec_tentative_et_echeance(governed: Path) -> None:
    tid = _held(governed, "Perdue", 3600, now=NOW)
    TaskService(governed).ledger.reap(fenced_hosts=frozenset({"host-claude-code-cli"}), now=NOW)
    got = _presence(governed, tid)
    assert got["state"] == "resume" and got["attempt"] == 1 and got["retry_at"]


def test_le_dernier_signe_unifie_prend_le_plus_recent() -> None:
    old, mid, new = NOW - timedelta(minutes=30), NOW - timedelta(minutes=10), NOW - timedelta(minutes=2)
    assert last_sign(sonde=old, outil=new, battement=mid) == (new, "outil")
    assert last_sign(sonde=new, outil=mid, battement=old) == (new, "sonde")
    assert last_sign(sonde=None, outil=old, battement=mid) == (mid, "battement")
    assert last_sign(sonde=None, outil=None) == (None, "")


def test_le_signe_de_la_carte_vient_de_l_outil_trace_ou_du_battement(governed: Path) -> None:
    tid = _held(governed, "Signe", 900, now=NOW)  # battement il y a 15 min
    from grimoire.tools.workspace_sessions import TRACES_RELPATH

    trace = governed / TRACES_RELPATH
    trace.parent.mkdir(parents=True, exist_ok=True)
    tool_at = NOW - timedelta(minutes=1)
    trace.write_text(
        json.dumps({"run_id": SESSION, "tool_calls": [{"tool": "Edit", "timestamp": tool_at.isoformat()}]}) + "\n",
        encoding="utf-8",
    )
    got = _presence(governed, tid, probe=None)
    assert got["state"] == "working" and got["sign_source"] == "outil" and got["sign_at"] == tool_at.isoformat()


def test_une_tache_sans_agent_est_none() -> None:
    assert presence(now=NOW, status="ready").state == "none"


def test_une_session_terminee_rend_la_tache_au_tick_avant_la_fin_du_bail(governed: Path) -> None:
    from grimoire.missions.launcher import LAUNCHED_EVENT, ExecResult, release_ended_workers

    tid = _held(governed, "Session finie", 60, now=NOW)  # bail valide 19 min de plus
    service = TaskService(governed)
    task = service.ledger.get_task(tid)
    assert task is not None and task.claim is not None
    service.ledger._append_event(  # type: ignore[attr-defined]
        LAUNCHED_EVENT, tid, "task", "tick", {"session": "abcd1234", "lease_epoch": task.claim.lease_epoch}
    )
    agents = json.dumps([{"id": "abcd1234", "name": f"grimoire-{tid}", "kind": "background", "state": "done"}])

    def exec_(argv, cwd, env):  # type: ignore[no-untyped-def]
        return ExecResult(0, agents, "")

    released = release_ended_workers(service, exec_, now=NOW)
    assert released == (tid,)
    after = TaskService(governed).ledger.get_task(tid)
    assert after is not None and after.status.value == "ready"
    assert _presence(governed, tid)["state"] == "resume"
    assert _presence(governed, tid)["cause"].startswith("bail perdu (session_ended)")
