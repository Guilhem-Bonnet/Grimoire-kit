"""Interventions humaines par tâche vérifiée (B-05) : fixtures kit-gov et aidlc, aucun appel modèle."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "scripts" / "bench"


def _load() -> ModuleType:
    if str(BENCH) not in sys.path:
        sys.path.append(str(BENCH))
    spec = importlib.util.spec_from_file_location("bench_human_gates", BENCH / "human_gates.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hg = _load()

POLICY_ASK = (
    "[Grimoire policy] Cette écriture abaisse le profil du standard de « governed » à « starter » "
    "(_grimoire/standard/standard-profile.yaml). Confirme si c'est voulu (ex. archivage de fin de vie)."
)
POLICY_DENY = (
    "[Grimoire policy] Bash refusé — recursive/forced delete. Motif : Destructive mutation requires strict risk "
    "profile (profil de risque light). Demande une autorisation explicite ou passe par une commande réversible."
)
AIDLC_REFUSAL = (
    "Plan Approval override is refused under autonomous Construction mode: "
    "the break-glass phrase must be typed by a human in an attended session."
)


def _assistant(*blocks: dict[str, Any]) -> str:
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def _tool_use(tool_id: str, name: str, **tool_input: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def _tool_result(tool_id: str, text: str, *, error: bool) -> str:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": text, "is_error": error}
    return json.dumps({"type": "user", "message": {"content": [block]}})


def _jsonl(*rows: dict[str, Any]) -> str:
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(root: Path, arm: str, task: str, k: int, transcript: list[str] | None, files: dict[str, str]) -> None:
    base = root / "tasks" / task.replace("/", "__") / arm
    (base / f"run{k}").mkdir(parents=True, exist_ok=True)
    if transcript is not None:
        _write(base / f"run{k}.stream.jsonl", "\n".join(transcript) + "\n")
    for name, text in files.items():
        _write(base / f"run{k}" / name, text)


def _results(root: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _write(root / "state" / "results.jsonl", _jsonl(*rows))
    return rows


def _row(task: str, arm: str, k: int = 0, **extra: Any) -> dict[str, Any]:
    return {"task_id": task, "arm": arm, "run_index": k, "success": True, "total_cost_usd": 0.5, **extra}


FLOWS = "_grimoire-runtime-output/flows/authority"
LEDGER = "_grimoire-runtime-output/ledger/events.jsonl"

KIT_GOV_FILES = {
    # deux checkpoints présentés : le premier levé par un octroi, le second par un tour humain
    f"{FLOWS}/run-1.authority.jsonl": _jsonl(
        {"type": "challenge", "challenge_id": "CHL-a", "node_id": "ship", "session_id": "s1"},
        {
            "type": "decision",
            "challenge_id": "CHL-a",
            "node_id": "ship",
            "decision": "approve",
            "attestation": "granted:GRT-1",
        },
        {"type": "challenge", "challenge_id": "CHL-b", "node_id": "release", "session_id": "s1"},
        {
            "type": "decision",
            "challenge_id": "CHL-b",
            "node_id": "release",
            "decision": "approve",
            "attestation": "attested",
        },
    ),
    # un octroi : son challenge et lui-même sont UNE intervention
    f"{FLOWS}/@autonomy-grants.authority.jsonl": _jsonl(
        {"type": "grant_challenge", "challenge_id": "GCH-1", "request_sha256": "x"},
        {"type": "grant", "grant_id": "GRT-1", "challenge_id": "GCH-1", "attestation": "attested"},
        {"type": "revoke", "grant_id": "GRT-1", "reason": "expired"},
    ),
    f"{FLOWS}/@consumed-turns.authority.jsonl": _jsonl({"session_id": "s1", "turn_ts": "t", "challenge_id": "CHL-b"}),
    # une question libre de l'agent (deux fois le même événement) et celle du checkpoint de flow (déjà un gate)
    LEDGER: _jsonl(
        {"id": "e1", "event_type": "task.question_asked", "payload": {"question": {"id": "q-9", "origin": "agent"}}},
        {"id": "e2", "event_type": "task.question_asked", "payload": {"question": {"id": "q-9", "origin": "agent"}}},
        {
            "id": "e3",
            "event_type": "task.question_asked",
            "payload": {"question": {"id": "q-flow-run-1-ship", "origin": "flow"}},
        },
        {"id": "e4", "event_type": "task.transitioned", "payload": {}},
    ),
}
KIT_GOV_TRANSCRIPT = [
    _assistant(_tool_use("t1", "Bash", command="sed -i s/governed/starter/ standard-profile.yaml")),
    _tool_result("t1", POLICY_ASK, error=True),  # le hook demande une confirmation humaine : 1
    _assistant(_tool_use("t2", "Bash", command="rm -rf build")),
    _tool_result("t2", POLICY_DENY, error=True),  # l'agent passe par une commande réversible : 0
    _assistant(_tool_use("t3", "Bash", command="ls")),
    _tool_result("t3", "Exit code 1\nno such file", error=True),  # une erreur ordinaire : 0
    json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.5}),
]

AIDLC_FILES = {
    "aidlc/intent-1/audit/shard-a.md": (
        "# AI-DLC Audit Log\n\n## Stage Awaiting Approval\n**Timestamp**: 2026-10-01T10:00:00Z\n"
        "**Event**: STAGE_AWAITING_APPROVAL\n**Stage**: requirements-analysis\n\n---\n\n"
        "## Decision\n**Timestamp**: 2026-10-01T10:05:00Z\n**Event**: DECISION_RECORDED\n"
        "**Stage**: requirements-analysis\n**Questions File**: aidlc-docs/inception/requirements-questions.md\n\n---\n\n"
        "## Stage Awaiting Approval\n**Timestamp**: 2026-10-01T10:10:00Z\n**Event**: STAGE_AWAITING_APPROVAL\n"
        "**Stage**: workflow-planning\n\n---\n\n"
        "## Revalidated\n**Timestamp**: 2026-10-01T10:11:00Z\n**Event**: STAGE_AWAITING_APPROVAL\n"
        "**Stage**: workflow-planning\n**Revalidated**: true\n\n---\n\n"
        "## Autonomy\n**Timestamp**: 2026-10-01T10:20:00Z\n**Event**: AUTONOMY_MODE_SET\n**Mode**: autonomous\n\n---\n\n"
        "## Autonomy gated\n**Timestamp**: 2026-10-01T10:21:00Z\n**Event**: AUTONOMY_MODE_SET\n**Mode**: gated\n\n---\n\n"
        "## Human turn\n**Timestamp**: 2026-10-01T10:22:00Z\n**Event**: HUMAN_TURN\n**Origin**: person\n"
    ),
}
AIDLC_TRANSCRIPT = [
    # le fichier annoncé par DECISION_RECORDED : une seule question, vue par les deux traces
    _assistant(
        _tool_use(
            "a1",
            "Write",
            file_path="/w/aidlc-docs/inception/requirements-questions.md",
            content="## Q1\nWhich stack?\n[Answer]: \n",
        )
    ),
    # un second fichier de questions, ensuite complété par une édition : une seule question
    _assistant(
        _tool_use(
            "a2", "Write", file_path="/w/aidlc-docs/inception/nfr-questions.md", content="## Q1\nSLA?\n[Answer]: \n"
        )
    ),
    _assistant(
        _tool_use(
            "a3",
            "Edit",
            file_path="/w/aidlc-docs/inception/nfr-questions.md",
            old_string="[Answer]: ",
            new_string="[Answer]: B",
        )
    ),
    _assistant(_tool_use("a4", "Write", file_path="/w/notes.md", content="rien à demander")),
    _assistant(_tool_use("a5", "Bash", command="aidlc engine park")),
    _tool_result("a5", AIDLC_REFUSAL, error=True),
    json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.7}),
]


def _bench(root: Path) -> list[dict[str, Any]]:
    _run(root, "kit-gov", "python/zipper", 0, KIT_GOV_TRANSCRIPT, KIT_GOV_FILES)
    _run(root, "aidlc", "python/zipper", 0, AIDLC_TRANSCRIPT, AIDLC_FILES)
    _run(
        root,
        "nu",
        "python/zipper",
        0,
        [_assistant(_tool_use("n1", "Bash", command="pytest")), json.dumps({"type": "result", "subtype": "success"})],
        {},
    )
    return _results(
        root, [_row("python/zipper", "kit-gov"), _row("python/zipper", "aidlc"), _row("python/zipper", "nu")]
    )


def _by_arm(root: Path) -> dict[str, dict[str, Any]]:
    report = hg.build_report(root, hg.read_results_rows(root / "state" / "results.jsonl"))
    return {r["arm"]: r for r in report["runs"]}


def test_the_kit_gov_fixture_counts_every_intervention_once(tmp_path: Path) -> None:
    _bench(tmp_path)
    run = _by_arm(tmp_path)["kit-gov"]
    assert run["by_kind"] == {"gate": 2, "refusal": 1, "question": 1, "grant": 1, "autonomy": 0}
    assert run["human_gates"] == 5


def test_the_aidlc_fixture_counts_every_intervention_once(tmp_path: Path) -> None:
    _bench(tmp_path)
    run = _by_arm(tmp_path)["aidlc"]
    # 2 gates (la réouverture « Revalidated » n'en est pas un), 2 questions (le fichier de DECISION_RECORDED
    # et celui que l'édition complète ne comptent qu'une fois), 1 refus, 1 mode autonome (« gated » n'en est pas un)
    assert run["by_kind"] == {"gate": 2, "refusal": 1, "question": 2, "grant": 0, "autonomy": 1}
    assert run["human_gates"] == 6


def test_a_run_with_no_intervention_is_zero_not_unknown(tmp_path: Path) -> None:
    _bench(tmp_path)
    run = _by_arm(tmp_path)["nu"]
    assert run["human_gates"] == 0
    assert run["by_kind"] == dict.fromkeys(hg.KINDS, 0)


def test_a_grant_counts_for_one_with_its_challenge(tmp_path: Path) -> None:
    files = {
        f"{FLOWS}/@autonomy-grants.authority.jsonl": _jsonl(
            {"type": "grant_challenge", "challenge_id": "GCH-1"},
            {"type": "grant", "grant_id": "GRT-1", "challenge_id": "GCH-1"},
        )
    }
    _run(tmp_path, "kit-gov", "go/a", 0, [json.dumps({"type": "result"})], files)
    _results(tmp_path, [_row("go/a", "kit-gov")])
    run = _by_arm(tmp_path)["kit-gov"]
    assert run["by_kind"]["grant"] == 1
    assert run["human_gates"] == 1


def test_a_grant_request_that_was_never_attested_is_not_an_intervention(tmp_path: Path) -> None:
    files = {f"{FLOWS}/@autonomy-grants.authority.jsonl": _jsonl({"type": "grant_challenge", "challenge_id": "GCH-1"})}
    _run(tmp_path, "kit-gov", "go/a", 0, [json.dumps({"type": "result"})], files)
    _results(tmp_path, [_row("go/a", "kit-gov")])
    assert _by_arm(tmp_path)["kit-gov"]["human_gates"] == 0


def test_a_decision_without_a_challenge_is_still_a_presented_checkpoint(tmp_path: Path) -> None:
    files = {
        f"{FLOWS}/run-2.authority.jsonl": _jsonl(
            {
                "type": "decision",
                "challenge_id": "",
                "node_id": "ship",
                "decision": "approve",
                "attestation": "unattested",
            }
        )
    }
    _run(tmp_path, "kit-gov", "go/a", 0, [json.dumps({"type": "result"})], files)
    _results(tmp_path, [_row("go/a", "kit-gov")])
    assert _by_arm(tmp_path)["kit-gov"]["by_kind"]["gate"] == 1


@pytest.mark.parametrize(
    ("text", "counted"),
    [
        (POLICY_ASK, True),
        (AIDLC_REFUSAL, True),
        (POLICY_DENY, False),
        ("Exit code 2\ncommand not found", False),
        ("", False),
    ],
)
def test_only_a_refusal_that_waits_for_a_human_counts(text: str, counted: bool) -> None:
    assert hg.is_human_refusal(text) is counted


def test_a_refusal_that_is_not_an_error_result_is_not_counted(tmp_path: Path) -> None:
    transcript = [
        _assistant(_tool_use("t1", "Bash", command="echo")),
        _tool_result("t1", POLICY_ASK, error=False),
        json.dumps({"type": "result"}),
    ]
    _run(tmp_path, "kit-gov", "go/a", 0, transcript, {})
    _results(tmp_path, [_row("go/a", "kit-gov")])
    assert _by_arm(tmp_path)["kit-gov"]["human_gates"] == 0


def test_the_count_ignores_the_arm_label_and_any_declared_field(tmp_path: Path) -> None:
    """Le même dépôt sous une autre étiquette de bras, avec un champ déclaré mensonger, donne le même compte."""
    _run(tmp_path, "kit-gov", "go/a", 0, KIT_GOV_TRANSCRIPT, KIT_GOV_FILES)
    _run(tmp_path, "other", "go/a", 0, KIT_GOV_TRANSCRIPT, KIT_GOV_FILES)
    _results(
        tmp_path,
        [
            _row("go/a", "kit-gov"),
            _row("go/a", "other", human_gates=0, human_interventions=0, external={"human_interventions": 0}),
        ],
    )
    runs = _by_arm(tmp_path)
    assert runs["kit-gov"]["human_gates"] == runs["other"]["human_gates"] == 5


def test_an_unreadable_run_is_unknown_never_zero(tmp_path: Path) -> None:
    _run(tmp_path, "kit-gov", "go/a", 0, KIT_GOV_TRANSCRIPT, KIT_GOV_FILES)
    _run(tmp_path, "kit-gov", "go/b", 0, None, {})  # transcription absente
    _run(tmp_path, "kit-gov", "go/c", 0, ["pas du json", "{tronqué"], {})  # aucune ligne exploitable
    (tmp_path / "tasks" / "go__d" / "kit-gov").mkdir(parents=True)  # ni dépôt ni transcription
    _results(
        tmp_path, [_row("go/a", "kit-gov"), _row("go/b", "kit-gov"), _row("go/c", "kit-gov"), _row("go/d", "kit-gov")]
    )
    report = hg.build_report(tmp_path, hg.read_results_rows(tmp_path / "state" / "results.jsonl"))
    by_task = {r["task_id"]: r["human_gates"] for r in report["runs"]}
    assert by_task == {"go/a": 5, "go/b": None, "go/c": None, "go/d": None}
    arm = report["arms"]["kit-gov"]
    assert arm["human_gates_known_ratio"] == 0.25
    assert arm["median_human_gates_resolved"] is None  # un inconnu parmi les résolus : pas de médiane
    assert arm["human_gates_total_known"] == 5


def test_the_median_is_taken_over_resolved_runs_and_skips_invalid_ones(tmp_path: Path) -> None:
    def transcript(n: int) -> list[str]:
        out: list[str] = []
        for i in range(n):
            out += [_assistant(_tool_use(f"t{i}", "Bash", command="x")), _tool_result(f"t{i}", POLICY_ASK, error=True)]
        return [*out, json.dumps({"type": "result"})]

    rows = []
    for task, n, extra in (
        ("go/a", 1, {}),
        ("go/b", 3, {}),
        ("go/c", 5, {}),
        ("go/d", 9, {"success": False}),
        ("go/e", 7, {"transcript_check": "invalid"}),
    ):
        _run(tmp_path, "kit-gov", task, 0, transcript(n), {})
        rows.append(_row(task, "kit-gov", **extra))
    _results(tmp_path, rows)
    arm = hg.build_report(tmp_path, hg.read_results_rows(tmp_path / "state" / "results.jsonl"))["arms"]["kit-gov"]
    assert (arm["n_runs"], arm["n_resolved"]) == (4, 3)
    assert arm["median_human_gates_resolved"] == 3
    assert arm["mean_human_gates_resolved"] == 3
    assert arm["human_gates_total_known"] == 1 + 3 + 5 + 9


def test_noise_lines_around_the_json_stream_are_tolerated(tmp_path: Path) -> None:
    transcript = ["error: invalid value 'x' for '--icons'", *KIT_GOV_TRANSCRIPT, "{pas du json"]
    _run(tmp_path, "kit-gov", "go/a", 0, transcript, KIT_GOV_FILES)
    _results(tmp_path, [_row("go/a", "kit-gov")])
    assert _by_arm(tmp_path)["kit-gov"]["human_gates"] == 5


def test_build_directories_are_not_searched_for_journals(tmp_path: Path) -> None:
    files = {"node_modules/pkg/audit/x.md": "**Event**: STAGE_AWAITING_APPROVAL\n"}
    _run(tmp_path, "aidlc", "go/a", 0, [json.dumps({"type": "result"})], files)
    _results(tmp_path, [_row("go/a", "aidlc")])
    assert _by_arm(tmp_path)["aidlc"]["human_gates"] == 0


def test_the_cli_prints_the_table_and_refuses_a_missing_results_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert hg.main(["--workspace", str(tmp_path)]) == 2
    _bench(tmp_path)
    capsys.readouterr()
    assert hg.main(["--workspace", str(tmp_path), "--arm", "kit-gov", "--arm", "aidlc"]) == 0
    out = capsys.readouterr().out
    assert hg.RULE_ID in out
    assert "| `kit-gov` | 1 | 1 | 1/1 | 5 | 5 |" in out
    assert "| `aidlc` | 1 | 1 | 1/1 | 6 | 6 |" in out
    assert "`nu`" not in out
    assert hg.main(["--workspace", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["arms"]["nu"]["median_human_gates_resolved"] == 0
    assert {e["kind"] for e in payload["runs"][0]["events"]} <= set(hg.KINDS)
