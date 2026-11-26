"""Lecteur de journal Copilot : jetons, repli, emplacement, flotte (épique #1280, lot C3).

Aucun Copilot n'est lancé : le journal est composé à partir des fixtures déjà versées sous
``tests/fixtures/hosts/copilot/copilot-harness/`` (``session-shutdown-usage.json``,
``subagent-model.json``, ``session-start.json``). Tout reste « non vérifié sur un vrai Copilot ».
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_fleet_live as fleet
from grimoire.traces.copilot_usage import (
    LOGS_ENV,
    SOURCE_COPILOT_SESSION,
    UNVERIFIED_NOTE,
    confined_path,
    copilot_logs_location,
    fold_copilot_journal,
    list_journals,
    locate_session_journal,
    read_copilot_journal,
    session_task_usage,
)
from grimoire.traces.ledger import TraceLedger
from grimoire.traces.transcript_usage import task_usage

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hosts" / "copilot" / "copilot-harness"
SESSION = "00000000-0000-4000-8000-0000000000c1"
NOW = datetime.now(UTC).replace(microsecond=0)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _events(*, cwd: str, shutdown: bool = True, top_message: int | None = None) -> list[dict[str, Any]]:
    """Un journal au format des fixtures : démarrage, hook, sous-agent, (message), arrêt."""
    hook = _fixture("session-start.json")["payloads"][0]
    events: list[dict[str, Any]] = [
        {"type": "session.start", "data": {"sessionId": SESSION, "copilotVersion": "unknown"}},
        {"type": "hook.start", "data": {"hookType": hook["hookType"], "input": {**hook["input"], "cwd": cwd}}},
        *_fixture("subagent-model.json")["events"],
    ]
    if top_message is not None:
        events.append(
            {"type": "assistant.message", "data": {"messageId": "m-top-1", "model": "claude-sonnet-4.6", "outputTokens": top_message}}
        )
        # Le même message rejoué : compté une fois.
        events.append(events[-1])
    if shutdown:
        events.append(_fixture("session-shutdown-usage.json")["events"][0])
    return events


def _write(path: Path, events: list[dict[str, Any]], *, tail: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events) + tail, encoding="utf-8")
    return path


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    base = tmp_path / "copilot-logs"
    base.mkdir()
    return base


def test_usage_lu_depuis_le_journal_arrete(tmp_path: Path, logs: Path) -> None:
    journal = _write(logs / SESSION / "events.jsonl", _events(cwd=str(tmp_path)))
    session = read_copilot_journal(journal, base=logs)
    assert session.recognized and session.ended and session.shutdowns == 1
    assert session.session_id == SESSION
    assert session.cwd == str(tmp_path)
    assert session.model == "claude-sonnet-4.6"
    (usage,) = session.models
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (1000, 100, 0, 0)
    assert usage.requests == 10
    assert session.premium_requests == 7.5
    assert session.started_at.startswith("2026-")
    # Le sous-agent est rattaché par parentToolCallId, avec son modèle (donné sur ce message).
    (sub,) = session.subagents
    assert (sub.model, sub.output_tokens, sub.messages, sub.completed) == ("claude-haiku-4.5", 120, 1, True)
    counters = session.usage()
    assert counters["source"] == "shutdown"
    assert counters["input_tokens"] == 1000 and counters["cost_usd"] is None and counters["verified"] is False


def test_session_sans_arret_n_a_ni_entree_ni_cache(tmp_path: Path, logs: Path) -> None:
    journal = _write(logs / SESSION / "events.jsonl", _events(cwd=str(tmp_path), shutdown=False, top_message=50))
    session = read_copilot_journal(journal)
    assert not session.ended
    counters = session.usage()
    assert counters["source"] == "messages"
    # Un message rejoué n'est compté qu'une fois ; l'entrée et le cache sont inconnus, pas zéro.
    assert counters["output_tokens"] == 50
    assert counters["input_tokens"] is None and counters["cache_read_tokens"] is None
    unknown = session_task_usage(session)
    assert unknown["total_tokens"] is None and unknown["input_tokens"] is None and unknown["cost_known"] is False


def test_cache_absent_du_format_est_inconnu(tmp_path: Path, logs: Path) -> None:
    shutdown = _fixture("session-shutdown-usage.json")["events"][0]
    del shutdown["data"]["modelMetrics"]["claude-sonnet-4.6"]["usage"]["cacheReadTokens"]
    journal = _write(logs / "j.jsonl", [shutdown])
    session = read_copilot_journal(journal)
    assert session.usage()["cache_read_tokens"] is None
    assert session_task_usage(session)["total_tokens"] is None


def test_repli_idempotent_une_session_une_fois(tmp_path: Path, logs: Path) -> None:
    ledger = TraceLedger(tmp_path / "traces")
    journal = _write(logs / SESSION / "events.jsonl", _events(cwd=str(tmp_path)))
    first = fold_copilot_journal(ledger, journal, task_id="T-1", base=logs)
    assert (first.new_calls, first.already_folded, first.skipped) == (1, 0, "")
    again = fold_copilot_journal(ledger, journal, task_id="T-1", base=logs)
    assert (again.new_calls, again.already_folded) == (0, 1)
    # Une copie du même journal (même session) sous un autre chemin ne compte pas une seconde fois,
    # et la tâche qui le tient déjà est nommée.
    copy = _write(logs / "copie" / "events.jsonl", _events(cwd=str(tmp_path)))
    other = fold_copilot_journal(ledger, copy, task_id="T-2", base=logs)
    assert (other.new_calls, other.already_folded, other.held_by_other_tasks) == (0, 1, {"T-1": 1})
    assert len(ledger.list_traces()) == 1


def test_usage_de_tache_marque_non_verifie_et_cout_inconnu(tmp_path: Path, logs: Path) -> None:
    ledger = TraceLedger(tmp_path / "traces")
    fold_copilot_journal(ledger, _write(logs / "a.jsonl", _events(cwd=str(tmp_path))), task_id="T-1")
    usage = task_usage(ledger.list_traces(task_id="T-1"), host_usage_available=False)
    assert usage["usage_source"] == SOURCE_COPILOT_SESSION
    assert (usage["calls"], usage["input_tokens"], usage["output_tokens"]) == (1, 1000, 100)
    # Aucun tarif en dollars dans le format : inconnu, jamais zéro.
    assert usage["cost_usd"] is None and usage["cost_known"] is False and usage["cost_status"] == "unknown"
    assert usage["verified"] is False and usage["note"] == UNVERIFIED_NOTE


def test_format_tronque_ou_inconnu_donne_inconnu_sans_exception(tmp_path: Path, logs: Path) -> None:
    ledger = TraceLedger(tmp_path / "traces")
    truncated = _write(
        logs / "tronque.jsonl",
        _events(cwd=str(tmp_path), shutdown=False),
        tail='{"type": "session.shutdown", "data": {"modelMetrics": {"m": {"usage": {"inputTokens": ',
    )
    session = read_copilot_journal(truncated)
    assert session.unreadable_lines == 1 and not session.ended
    result = fold_copilot_journal(ledger, truncated, task_id="T-1")
    assert (result.new_calls, result.skipped) == (0, "not_ended")

    foreign = logs / "autre.jsonl"
    foreign.write_text('[1, 2, 3]\n{"foo": 1}\nnot json\n', encoding="utf-8")
    unknown = read_copilot_journal(foreign)
    assert not unknown.recognized and unknown.usage()["output_tokens"] is None
    assert fold_copilot_journal(ledger, foreign, task_id="T-1").skipped == "unrecognized"

    bad = _fixture("session-shutdown-usage.json")["events"][0]
    bad["data"]["modelMetrics"]["claude-sonnet-4.6"]["usage"]["inputTokens"] = "mille"
    invalid = read_copilot_journal(_write(logs / "invalide.jsonl", [bad]))
    assert invalid.invalid_usage == 1 and invalid.models == ()
    assert ledger.list_traces() == []


def test_journal_trop_gros_est_tronque_pas_devine(tmp_path: Path, logs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.traces import copilot_usage

    monkeypatch.setattr(copilot_usage, "MAX_JOURNAL_BYTES", 200)
    journal = _write(logs / "gros.jsonl", _events(cwd=str(tmp_path)))
    session = read_copilot_journal(journal)
    assert session.truncated and not session.ended
    assert fold_copilot_journal(TraceLedger(tmp_path / "t"), journal, task_id="T").skipped == "truncated"


def test_ligne_geante_comptee_illisible(tmp_path: Path, logs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.traces import copilot_usage

    monkeypatch.setattr(copilot_usage, "MAX_LINE_BYTES", 150)
    journal = logs / "ligne.jsonl"
    journal.write_text("x" * 500 + "\n" + json.dumps(_events(cwd="/x")[0]) + "\n", encoding="utf-8")
    session = read_copilot_journal(journal)
    assert session.unreadable_lines == 1 and session.recognized


def test_chemin_confine_et_fichier_ordinaire(tmp_path: Path, logs: Path) -> None:
    outside = _write(tmp_path / "dehors" / "events.jsonl", _events(cwd=str(tmp_path)))
    assert confined_path(outside, logs) is None
    with pytest.raises(ValueError, match="sort du dossier"):
        read_copilot_journal(outside, base=logs)
    link = logs / "lien.jsonl"
    link.symlink_to(outside)
    assert link not in list_journals(logs) and outside not in list_journals(logs)
    with pytest.raises(ValueError, match=r"ordinaire|sort du dossier"):
        read_copilot_journal(link, base=logs)
    assert locate_session_journal(logs, "../etc") is None
    assert locate_session_journal(logs, SESSION) is None
    _write(logs / SESSION / "events.jsonl", _events(cwd=str(tmp_path)))
    assert locate_session_journal(logs, SESSION) == (logs / SESSION / "events.jsonl").resolve()
    with pytest.raises(FileNotFoundError):
        read_copilot_journal(logs / "absent.jsonl")
    assert list_journals(logs) == [(logs / SESSION / "events.jsonl").resolve()]


def test_emplacement_non_configure_message_et_aucune_lecture(tmp_path: Path) -> None:
    location = copilot_logs_location(tmp_path, environ={})
    assert not location.usable and location.path is None and location.source == "none"
    assert "journaux Copilot : emplacement non configuré" in location.message
    assert location.to_dict()["configured"] is False


def test_emplacement_par_variable_puis_par_reglage_du_projet(tmp_path: Path, logs: Path) -> None:
    (tmp_path / "project-context.yaml").write_text(
        f"project:\n  name: x\nhosts:\n  copilot_session_logs: {logs}\n", encoding="utf-8"
    )
    by_setting = copilot_logs_location(tmp_path, environ={})
    assert by_setting.usable and by_setting.source == "project" and by_setting.path == logs.resolve()
    other = tmp_path / "autre"
    other.mkdir()
    by_env = copilot_logs_location(tmp_path, environ={LOGS_ENV: str(other)})
    assert by_env.source == "env" and by_env.path == other.resolve()
    assert UNVERIFIED_NOTE in by_env.message
    relative = copilot_logs_location(tmp_path, environ={LOGS_ENV: "relatif/dossier"})
    assert not relative.usable and "chemin absolu" in relative.message
    missing = copilot_logs_location(tmp_path, environ={LOGS_ENV: str(tmp_path / "nulle-part")})
    assert not missing.usable and "introuvable" in missing.message


def test_reglage_projet_invalide_ignore(tmp_path: Path) -> None:
    (tmp_path / "project-context.yaml").write_text("hosts:\n  copilot_session_logs: [1, 2]\n", encoding="utf-8")
    assert copilot_logs_location(tmp_path, environ={}).path is None


def test_doctor_dit_emplacement_non_configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.cli.doctor_copilot import check_copilot_logs

    monkeypatch.delenv(LOGS_ENV, raising=False)
    assert check_copilot_logs(tmp_path) == []  # Copilot ni activé ni détecté
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "copilot-instructions.md").write_text("x\n", encoding="utf-8")
    (check,) = check_copilot_logs(tmp_path)
    assert check.name == "copilot_logs" and check.level == "info"
    assert "journaux Copilot : emplacement non configuré" in check.detail
    monkeypatch.setenv(LOGS_ENV, "relatif")
    assert check_copilot_logs(tmp_path)[0].level == "warn"


# ── Flotte en direct ────────────────────────────────────────────────────────


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    root = tmp_path / "projet"
    root.mkdir()
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    reg.save_registry([{"slug": "projet", "name": "Projet", "path": str(root.resolve())}])
    fleet.reset_cache()
    return root


def test_flotte_montre_la_session_copilot_avec_hote_modele_et_compteurs(
    project: Path, logs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(logs / SESSION / "events.jsonl", _events(cwd=str(project)))
    # Une session dont le journal ne dit pas où elle travaille n'est rattachée à aucun projet.
    _write(logs / "orpheline" / "events.jsonl", [_fixture("session-shutdown-usage.json")["events"][0]])
    monkeypatch.setenv(LOGS_ENV, str(logs))
    view = fleet.fleet_live_view(project, now=NOW)
    by_id = {s["id"]: s for s in view["sessions"]}
    session = by_id[SESSION]
    assert session["host"] == "host-github-copilot" and session["project"] == "projet"
    assert session["model"]["current"] == "claude-sonnet-4.6"
    assert (session["usage"]["input"], session["usage"]["output"]) == (1000, 100)
    assert session["usage"]["cost_usd"] is None and session["usage_verified"] is False
    assert session["liveness"] == "ended" and session["turns"]["count"] is None
    sub = next(s for s in view["sessions"] if s["role"] == "subagent")
    assert sub["parent_id"] == SESSION and sub["model"]["current"] == "claude-haiku-4.5"
    assert sub["usage"]["output"] == 120 and sub["usage"]["input"] is None
    assert {"from": SESSION, "to": sub["id"], "kind": "spawned"} in view["edges"]
    assert "orpheline" not in by_id and view["totals"]["ignored"] == 1
    assert view["copilot_logs"]["usable"] and view["copilot_logs"]["verified"] is False


def test_flotte_sans_emplacement_ne_lit_rien(project: Path, logs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(logs / SESSION / "events.jsonl", _events(cwd=str(project)))
    monkeypatch.delenv(LOGS_ENV, raising=False)
    view = fleet.fleet_live_view(project, now=NOW)
    assert view["sessions"] == []
    assert view["copilot_logs"]["configured"] is False
    assert "emplacement non configuré" in view["copilot_logs"]["message"]


def test_flotte_ignore_journal_ancien_et_illisible(project: Path, logs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _write(logs / "vieux" / "events.jsonl", _events(cwd=str(project)))
    os.utime(old, (1_000_000, 1_000_000))
    (logs / "casse").mkdir()
    (logs / "casse" / "events.jsonl").write_text("pas du json\n", encoding="utf-8")
    monkeypatch.setenv(LOGS_ENV, str(logs))
    view = fleet.fleet_live_view(project, now=NOW)
    assert view["sessions"] == [] and view["totals"]["ignored"] == 1


def test_flotte_project_toutes_racines_la_plus_profonde_gagne_et_journal_illisible_ecarte(
    project: Path, logs: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nested = project / "sous-projet"
    nested.mkdir()
    reg.save_registry(
        [
            {"slug": "projet", "name": "Projet", "path": str(project.resolve())},
            {"slug": "sous", "name": "Sous", "path": str(nested.resolve())},
        ]
    )
    _write(logs / SESSION / "events.jsonl", _events(cwd=str(nested)))
    locked = _write(logs / "ferme" / "events.jsonl", _events(cwd=str(project)))
    locked.chmod(0)
    monkeypatch.setenv(LOGS_ENV, str(logs))
    try:
        view = fleet.fleet_live_view(project, project="*", now=NOW)
    finally:
        locked.chmod(0o600)
    session = next(s for s in view["sessions"] if s["id"] == SESSION)
    assert session["project"] == "sous"
    assert view["totals"]["ignored"] == 1


def test_travail_de_session_lit_le_modele_copilot(project: Path, logs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.tools.workspace_work import session_work

    _write(logs / SESSION / "events.jsonl", _events(cwd=str(project)))
    monkeypatch.delenv(LOGS_ENV, raising=False)
    assert session_work(project, SESSION, "github-copilot")["source"] == "no_transcript"
    monkeypatch.setenv(LOGS_ENV, str(logs))
    work = session_work(project, SESSION, "github-copilot")
    assert (work["source"], work["model"], work["tier"], work["effort"], work["verified"]) == (
        "copilot_journal",
        "claude-sonnet-4.6",
        "sonnet",
        None,
        False,
    )
    assert session_work(project, "00000000-0000-4000-8000-0000000000ff", "github-copilot")["source"] == "no_transcript"
