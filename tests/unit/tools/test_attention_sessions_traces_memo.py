"""Vue « attention » à chaud (#1243, troisième lot) : traces récentes, usage par tâche et journaux de session.

Défaut empêché : à chaque requête, 2 Mo de queue de ``traces.jsonl`` reparsés par projet, l'usage de chaque tâche
recalculé depuis ses traces, et chaque journal de session transformé en objets ``Path``. Défaut à ne pas rouvrir :
une valeur périmée après une écriture, ou une réponse qui change.

Pas de mesure de durée serrée (les runners de CI sont lents) : on compte les parsings et les recalculs, et une borne
de durée très large garde contre un retour à plusieurs secondes.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import file_memo
from grimoire.missions.lease import parse_instant
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.tools import workspace_attention as wa
from grimoire.tools import workspace_portfolio as portfolio
from grimoire.tools import workspace_sessions as sessions
from grimoire.traces import transcript_usage
from grimoire.traces.schemas import TokenUsage, TraceOutcome, TraceRecord

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    _forget()


def _forget() -> None:
    file_memo.clear()
    sessions._tail_memo.clear()
    transcript_usage._usage_memo.clear()


def _age(path: Path, seconds: float = 3600) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _trace(n: int, task_id: str = "T-1", **tags: str) -> TraceRecord:
    return TraceRecord(
        id=f"tr-{n}", run_id=f"r{n}", workflow_instance_id="", mission_id="", task_id=task_id,
        recipe_id="recipe.claude.transcript", outcome=TraceOutcome.SUCCESS, started_at="2026-11-20T10:00:00Z",
        model="claude-opus-4-7",
        token_usage=TokenUsage(prompt_tokens=100 + n, completion_tokens=20, total_tokens=120 + n, estimated_cost_usd=0.01),
        tags=("usage.source:claude-code-transcript", "usage.cache_read:50", *(f"{k}:{v}" for k, v in tags.items())),
    )


class TestUsageMemo:
    def test_meme_reponse_que_le_calcul_direct(self) -> None:
        traces = [_trace(n) for n in range(5)]
        direct = transcript_usage._task_usage_uncached(traces, host_usage_available=True)
        assert transcript_usage.task_usage(traces, host_usage_available=True) == direct
        assert transcript_usage.task_usage(traces, host_usage_available=True) == direct  # servi de la mémoire

    def test_un_seul_calcul_tant_que_les_traces_sont_les_memes_objets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []
        real = transcript_usage._task_usage_uncached
        monkeypatch.setattr(transcript_usage, "_task_usage_uncached", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
        traces = [_trace(n) for n in range(3)]
        for _ in range(3):
            transcript_usage.task_usage(list(traces))  # une autre liste, les mêmes objets
        assert len(calls) == 1

    def test_la_reponse_rendue_peut_etre_modifiee_sans_polluer_la_memoire(self) -> None:
        traces = [_trace(0)]
        first = transcript_usage.task_usage(traces)
        first["calls"] = 99
        first["reported"] = {"x": 1}
        assert transcript_usage.task_usage(traces)["calls"] == 1
        assert transcript_usage.task_usage(traces)["reported"] is None

    def test_une_trace_ajoutee_ou_remplacee_recalcule(self) -> None:
        traces = [_trace(0), _trace(1)]
        assert transcript_usage.task_usage(traces)["calls"] == 2
        assert transcript_usage.task_usage([*traces, _trace(2)])["calls"] == 3
        replaced = [traces[0], _trace(1, **{"usage.cache_read": "9"})]  # même id, autre objet
        assert transcript_usage.task_usage(replaced) == transcript_usage._task_usage_uncached(replaced)

    def test_la_disponibilite_de_l_hote_fait_partie_de_la_cle(self) -> None:
        traces = [_trace(0)]
        for flag in (True, False, None, True):
            got = transcript_usage.task_usage(traces, host_usage_available=flag)
            assert got == transcript_usage._task_usage_uncached(traces, host_usage_available=flag)

    def test_un_registre_de_tarifs_n_est_jamais_memorise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []
        real = transcript_usage._task_usage_uncached
        monkeypatch.setattr(transcript_usage, "_task_usage_uncached", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
        traces = [_trace(0)]
        for _ in range(2):
            transcript_usage.task_usage(traces, pricing_for=lambda _model: None)
        assert len(calls) == 2

    def test_la_memoire_est_bornee(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(transcript_usage, "_USAGE_MEMO_MAX", 4)
        for n in range(20):
            transcript_usage.task_usage([_trace(n)])
        assert len(transcript_usage._usage_memo) == 4


def _legacy_recent(project_root: Path, now: datetime, tail_bytes: int) -> dict[str, Any] | None:
    """Le ``recent_tool_calls`` d'avant : la queue relue et parsée à chaque appel, rien de gardé."""
    path = project_root / sessions.TRACES_RELPATH
    if not path.is_file():
        return None
    lines = sessions._tail_lines(path, tail_bytes)
    if lines is None:
        return None
    window_start = now - sessions.ACTIVITY_WINDOW
    slice_s = sessions.ACTIVITY_WINDOW.total_seconds() / sessions.ACTIVITY_BUCKETS
    out: dict[str, sessions._Activity] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or not record.get("run_id"):
            continue
        act = out.setdefault(str(record["run_id"]), sessions._Activity())
        raw_agent = record.get("agent")
        agent: dict[str, Any] = raw_agent if isinstance(raw_agent, dict) else {}
        act.agent = str(agent.get("agent_id") or "") or act.agent
        act.host = str(agent.get("host_id") or "") or act.host
        act.model = str(agent.get("model") or "") or act.model
        raw_calls = record.get("tool_calls")
        calls: list[Any] = raw_calls if isinstance(raw_calls, list) else []
        for call in calls:
            if not isinstance(call, dict):
                continue
            at = parse_instant(call.get("timestamp"))
            if at is None:
                continue
            if act.last_at is None or at >= act.last_at:
                act.last_at, act.last_tool = at, str(call.get("tool") or "")
            if window_start <= at <= now:
                index = min(int((at - window_start).total_seconds() // slice_s), sessions.ACTIVITY_BUCKETS - 1)
                act.buckets[index] += 1
    return {k: (v.buckets, v.last_at, v.last_tool, v.agent, v.host, v.model) for k, v in out.items()}


def _as_tuples(activity: dict[str, Any] | None) -> dict[str, Any] | None:
    if activity is None:
        return None
    return {k: (v.buckets, v.last_at, v.last_tool, v.agent, v.host, v.model) for k, v in activity.items()}


def _write_traces(root: Path) -> Path:
    path = root / sessions.TRACES_RELPATH
    path.parent.mkdir(parents=True)
    stamp = lambda minutes: (NOW - timedelta(minutes=minutes)).isoformat()  # noqa: E731
    rows: list[Any] = [
        {"run_id": "run-a", "agent": {"agent_id": "dev", "host_id": "claude-code-cli", "model": "opus"},
         "tool_calls": [{"timestamp": stamp(1), "tool": "Edit"}, {"timestamp": stamp(7), "tool": "Bash"},
                        {"timestamp": "pas une date", "tool": "X"}, "pas un appel", {"tool": "sans date"}]},
        {"run_id": "run-a", "agent": {"agent_id": "", "host_id": "", "model": ""},
         "tool_calls": [{"timestamp": stamp(30), "tool": "Read"}]},
        {"run_id": "run-b", "agent": "pas un objet", "tool_calls": "pas une liste"},
        {"run_id": "", "tool_calls": []},
        [1, 2, 3],
        {"run_id": "run-c", "agent": {"model": "sonnet"}, "tool_calls": [{"timestamp": stamp(0), "tool": None}]},
    ]
    text = "\n".join(json.dumps(r) for r in rows) + "\n\n{pas du json\n"
    path.write_text(text)
    _age(path)
    return path


class TestTailMemo:
    def test_meme_activite_que_l_ancien_calcul_pour_chaque_heure(self, tmp_path: Path) -> None:
        _write_traces(tmp_path)
        for shift in (0, 3, 20, 120):
            now = NOW + timedelta(minutes=shift)
            got = _as_tuples(sessions.recent_tool_calls(tmp_path, now=now))
            assert got == _legacy_recent(tmp_path, now, sessions.TRACE_TAIL_BYTES)
        assert got is not None and set(got) == {"run-a", "run-b", "run-c"}

    def test_la_queue_n_est_parsee_qu_une_fois_et_la_fenetre_suit_l_heure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_traces(tmp_path)
        parsed: list[int] = []
        real = sessions._parse_tail
        monkeypatch.setattr(sessions, "_parse_tail", lambda lines: (parsed.append(len(lines)), real(lines))[1])

        early = sessions.recent_tool_calls(tmp_path, now=NOW)
        late = sessions.recent_tool_calls(tmp_path, now=NOW + timedelta(minutes=14))

        assert len(parsed) == 1
        assert early is not None and late is not None
        assert sum(early["run-a"].buckets) != sum(late["run-a"].buckets)  # les appels sortent de la fenêtre

    def test_un_fichier_recent_n_est_jamais_garde(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _write_traces(tmp_path)
        _age(path, 0)  # écrit à l'instant : une réécriture dans le même pas d'horloge garderait la signature
        parsed: list[int] = []
        real = sessions._parse_tail
        monkeypatch.setattr(sessions, "_parse_tail", lambda lines: (parsed.append(1), real(lines))[1])
        for _ in range(3):
            sessions.recent_tool_calls(tmp_path, now=NOW)
        assert len(parsed) == 3

    def test_une_ecriture_est_vue(self, tmp_path: Path) -> None:
        path = _write_traces(tmp_path)
        before = sessions.recent_tool_calls(tmp_path, now=NOW)
        assert before is not None and "run-new" not in before
        row = {"run_id": "run-new", "tool_calls": [{"timestamp": NOW.isoformat(), "tool": "Edit"}]}
        with path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        _age(path)

        after = sessions.recent_tool_calls(tmp_path, now=NOW)

        assert after is not None and after["run-new"].last_tool == "Edit"

    def test_journal_absent_ou_dossier_illisible(self, tmp_path: Path) -> None:
        assert sessions.recent_tool_calls(tmp_path, now=NOW) is None

    def test_la_memoire_est_bornee(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sessions, "_TAIL_MEMO_MAX", 2)
        for n in range(5):
            root = tmp_path / f"p{n}"
            _write_traces(root)
            sessions.recent_tool_calls(root, now=NOW)
        assert len(sessions._tail_memo) <= 2


class TestSessionsIndexScan:
    def test_dossier_sous_un_nom_de_journal_et_fichiers_etrangers_sont_ignores(self, tmp_path: Path) -> None:
        runs = tmp_path / portfolio.RUNS_RELPATH
        runs.mkdir(parents=True)
        (runs / "session-a.json").write_text(json.dumps({"session_id": "a", "task_id": "T-1"}))
        (runs / "session-dossier.json").mkdir()
        (runs / "autre.json").write_text(json.dumps({"session_id": "z"}))
        (runs / "session-b.txt").write_text(json.dumps({"session_id": "b"}))

        index = portfolio.sessions_index(tmp_path)

        assert list(index) == ["a"]
        assert index["a"]["journal"] == f"{portfolio.RUNS_RELPATH.as_posix()}/session-a.json"
        assert index["a"]["journal"] == (runs / "session-a.json").relative_to(tmp_path).as_posix()


class TestAttentionViewHot:
    def _project(self, root: Path) -> None:
        service = TaskService(root)
        ids = [service.add(f"Tâche {i}", ("`pytest -q` passe",), actor="t", owner="g", ready=True).task.id
               for i in range(8)]
        for tid in ids[:4]:
            service.claim(tid, "agent-a", host="host-claude-code-cli")
            service.transition(tid, TaskState.RUNNING, "agent-a")
        runs = root / portfolio.RUNS_RELPATH
        runs.mkdir(parents=True)
        for j in range(60):
            path = runs / f"session-s{j}.json"
            path.write_text(json.dumps({
                "session_id": f"s{j}", "started_at": "2026-11-20T10:00:00+00:00", "ended_at": "2026-11-20T11:00:00+00:00",
                "task_id": ids[j % len(ids)], "host": "claude-code-cli",
            }))
            _age(path)
        traces = root / "_grimoire-output" / "traces"
        traces.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(_trace(n, ids[n % len(ids)]).to_dict()) for n in range(200)]
        (traces / "traces.jsonl").write_text("\n".join(lines) + "\n")
        _age(traces / "traces.jsonl")

    @staticmethod
    def _view(root: Path) -> dict[str, Any]:
        view = wa.attention_view(root, now=NOW, pr_reader=lambda *_a, **_k: [], service_probe=lambda *_a: "online")
        view.pop("generated_at")
        return view

    def test_chaud_egal_froid_et_sans_nouveau_parsing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._project(tmp_path)
        cold = self._view(tmp_path)
        parsed: list[int] = []
        real = sessions._parse_tail
        monkeypatch.setattr(sessions, "_parse_tail", lambda lines: (parsed.append(1), real(lines))[1])
        journals: list[int] = []
        real_journal = portfolio._journal_fields
        monkeypatch.setattr(portfolio, "_journal_fields", lambda data: (journals.append(1), real_journal(data))[1])

        asked: list[int] = []
        real_recent = sessions.recent_tool_calls
        monkeypatch.setattr(sessions, "recent_tool_calls", lambda *a, **k: (asked.append(1), real_recent(*a, **k))[1])

        started = time.perf_counter()
        hot = self._view(tmp_path)
        elapsed = time.perf_counter() - started

        assert hot == cold
        assert asked  # la vue lit bien les traces récentes : « aucun parsing » n'est pas un test vide
        assert parsed == []
        assert journals == []
        assert elapsed < 10  # borne large : seul un retour à des relectures complètes la dépasserait

        _forget()
        assert self._view(tmp_path) == cold  # et la réponse d'une lecture sans aucune mémoire est la même
