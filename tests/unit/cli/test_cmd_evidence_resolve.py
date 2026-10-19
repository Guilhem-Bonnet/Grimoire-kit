"""D-13 : ``grimoire evidence resolve <range>`` juge chaque preuve d'une plage git contre git."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

pytestmark = pytest.mark.covers("cli:evidence resolve")

runner = CliRunner()
LEDGER = "_grimoire-output/evidence/T-1/claim-ledger.md"
RUN = "_grimoire-output/evidence/T-1/test-run.json"
HEADER = (
    "# Registre\n\n## Claims\n\n"
    "| ID | Affirmation | Type | Preuve | Statut | Confiance | Décision |\n"
    "|---|---|---|---|---|---|---|\n"
)


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@example.com", "-c", "user.name=T", "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return done.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _commit(root: Path, message: str, files: dict[str, str | None]) -> str:
    for rel, text in files.items():
        if text is None:
            _git(root, "rm", "-q", rel)
        else:
            _write(root, rel, text)
            _git(root, "add", rel)
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _ledger(*rows: tuple[str, str, str]) -> str:
    return HEADER + "".join(f"| {cid} | a | fait | {proof} | {status} | haute | utiliser |\n" for cid, proof, status in rows)


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    base = _commit(root, "init", {"src/real.py": "a = 1\nb = 2\nc = 3\n", "README.md": "demo\n"})
    return root, base


def _resolve(root: Path, spec: str, *, json_out: bool = True, project_root: Path | None = None) -> tuple[int, dict]:
    args = (["-o", "json"] if json_out else []) + ["evidence", "resolve", spec, "--project-root", str(project_root or root)]
    result = runner.invoke(app, args)
    if not json_out or result.exit_code == 2:
        return result.exit_code, {"output": result.output}
    return result.exit_code, json.loads(result.stdout)


def _statuses(payload: dict) -> list[tuple[str, str]]:
    return [(p["token"], p["status"]) for p in payload["proofs"]]


def test_a_cited_line_unchanged_since_the_claim_is_resolved(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    head = _commit(root, "claim", {LEDGER: _ledger(("CL-001", "src/real.py:2", "prouvé"))})
    code, payload = _resolve(root, f"{base}..{head}")
    assert code == 0, payload
    assert payload["verdict"] == "resolved"
    (proof,) = payload["proofs"]
    assert (proof["status"], proof["task_id"], proof["claim"], proof["claim_commit"]) == ("resolved", "T-1", "CL-001", head)
    assert proof["fingerprint"] == "sha256:" + hashlib.sha256(b"b = 2").hexdigest()
    assert payload["anchor"] == payload["anchor"] | {"kind": "git", "base": base, "head": head, "commits": 1}


def test_a_cited_line_changed_later_in_the_range_is_stale(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    _commit(root, "claim", {LEDGER: _ledger(("CL-001", "src/real.py:2", "prouvé"))})
    head = _commit(root, "edit", {"src/real.py": "a = 1\nb = 20\nc = 3\n"})
    code, payload = _resolve(root, f"{base}..{head}")
    assert code == 1
    assert _statuses(payload) == [("src/real.py:2", "stale")]
    assert payload["verdict"] == "unresolved"


def test_a_change_elsewhere_in_the_file_keeps_a_line_reference_resolved(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    _commit(root, "claim", {LEDGER: _ledger(("CL-001", "src/real.py:2", "prouvé"), ("CL-002", "src/real.py", "prouvé"))})
    head = _commit(root, "edit", {"src/real.py": "a = 1\nb = 2\nc = 30\n"})
    code, payload = _resolve(root, f"{base}..{head}")
    assert code == 1
    assert _statuses(payload) == [("src/real.py:2", "resolved"), ("src/real.py", "stale")]


@pytest.mark.parametrize(
    ("proof", "status"),
    [
        ("src/gone.py:1", "missing"),
        ("src/real.py:99", "missing"),
        ("src/real.py:1-9", "missing"),
        ("src/../../outside.py:1", "missing"),
        ("tests/test_x.py::test_y", "missing"),
        ("src/real.py:1@sha256:00000000", "stale"),
        ("mem:decision-42", "unknown"),
        ("https://web.archive.org/web/2026/https://example.com", "unknown"),
    ],
)
def test_each_kind_of_proof_gets_its_own_status(tmp_path: Path, proof: str, status: str) -> None:
    root, base = _repo(tmp_path)
    head = _commit(root, "claim", {LEDGER: _ledger(("CL-001", proof, "prouvé"))})
    code, payload = _resolve(root, f"{base}..{head}")
    assert _statuses(payload) == [(proof, status)]
    assert code == 1


def test_a_pinned_hash_of_the_committed_file_resolves(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    digest = hashlib.sha256(b"a = 1\nb = 2\nc = 3\n").hexdigest()[:16]
    head = _commit(root, "claim", {LEDGER: _ledger(("CL-001", f"src/real.py:1@sha256:{digest}", "prouvé"))})
    assert _resolve(root, f"{base}..{head}")[0] == 0


def test_free_text_is_unknown_never_resolved(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    head = _commit(root, "claim", {LEDGER: _ledger(("CL-001", "src/real.py:1, relu le code", "prouvé"))})
    code, payload = _resolve(root, f"{base}..{head}")
    assert _statuses(payload) == [("src/real.py:1", "resolved"), ("relu le code", "unknown")]
    assert code == 1


def test_a_claim_marked_proven_without_proof_is_missing(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    head = _commit(root, "claim", {LEDGER: _ledger(("CL-001", "", "prouvé"), ("CL-002", "", "hypothèse"))})
    code, payload = _resolve(root, f"{base}..{head}")
    assert [(p["claim"], p["kind"], p["status"]) for p in payload["proofs"]] == [("CL-001", "none", "missing")]
    assert code == 1


def test_the_working_tree_is_never_read_for_the_target(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    head = _commit(root, "claim", {LEDGER: _ledger(("CL-001", "src/new.py:1", "prouvé"), ("CL-002", "src/real.py:2", "prouvé"))})
    _write(root, "src/new.py", "x = 1\n")  # présent sur disque, jamais commité
    _write(root, "src/real.py", "changed\n")  # modifié sur disque après le commit
    code, payload = _resolve(root, f"{base}..{head}")
    assert _statuses(payload) == [("src/new.py:1", "missing"), ("src/real.py:2", "resolved")]
    assert code == 1


def test_a_file_committed_after_the_claim_cannot_be_compared(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    _commit(root, "claim", {LEDGER: _ledger(("CL-001", "src/later.py:1", "prouvé"))})
    head = _commit(root, "code", {"src/later.py": "x = 1\n"})
    code, payload = _resolve(root, f"{base}..{head}")
    assert _statuses(payload) == [("src/later.py:1", "unknown")]
    assert code == 1


def test_only_rows_written_in_the_range_are_judged(tmp_path: Path) -> None:
    root, _ = _repo(tmp_path)
    old = _commit(root, "old claim", {LEDGER: _ledger(("CL-001", "src/gone.py:1", "prouvé"))})
    head = _commit(
        root, "new claim", {LEDGER: _ledger(("CL-001", "src/gone.py:1", "prouvé"), ("CL-002", "src/real.py:3", "prouvé"))}
    )
    code, payload = _resolve(root, f"{old}..{head}")
    assert [p["claim"] for p in payload["proofs"]] == ["CL-002"]
    assert code == 0
    code, payload = _resolve(root, head)  # un seul commit : ses propres changements
    assert [p["claim"] for p in payload["proofs"]] == ["CL-002"]
    code, payload = _resolve(root, f"{head}..{head}")
    assert (code, payload["verdict"], payload["proofs"]) == (0, "empty", [])


def test_a_row_rewritten_in_the_range_is_judged_against_its_new_commit(tmp_path: Path) -> None:
    root, _ = _repo(tmp_path)
    _commit(root, "claim", {LEDGER: _ledger(("CL-001", "src/real.py:2", "hypothèse"))})
    mid = _commit(root, "edit", {"src/real.py": "a = 1\nb = 20\nc = 3\n"})
    head = _commit(root, "proven", {LEDGER: _ledger(("CL-001", "src/real.py:2", "prouvé"))})
    code, payload = _resolve(root, f"{mid}..{head}")
    assert (code, _statuses(payload)) == (0, [("src/real.py:2", "resolved")])


def _run(run_id: str, *, ok: bool = True, fingerprint: bool = True) -> str:
    data: dict[str, object] = {"run_id": run_id, "ok": ok}
    if fingerprint:
        data["tree_fingerprint"] = "abc123"
    return json.dumps(data)


@pytest.mark.parametrize(
    ("recorded", "status"),
    [
        (_run("r1"), "resolved"),
        (_run("r2"), "stale"),
        (_run("r1", ok=False), "stale"),
        (_run("r1", fingerprint=False), "unknown"),
        ("{not json", "unknown"),
        (None, "missing"),
    ],
)
def test_a_recorded_test_run_is_read_from_git(tmp_path: Path, recorded: str | None, status: str) -> None:
    root, base = _repo(tmp_path)
    files: dict[str, str | None] = {LEDGER: _ledger(("CL-001", "test-run.json#r1", "prouvé"))}
    if recorded is not None:
        files[RUN] = recorded
    head = _commit(root, "claim", files)
    _, payload = _resolve(root, f"{base}..{head}")
    assert _statuses(payload) == [("test-run.json#r1", status)]


def test_a_test_run_from_another_folder_does_not_count(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    head = _commit(root, "claim", {LEDGER: _ledger(("CL-001", "notes/test-run.json#r1", "prouvé")), "notes/test-run.json": _run("r1")})
    assert _statuses(_resolve(root, f"{base}..{head}")[1]) == [("notes/test-run.json#r1", "missing")]


def test_a_project_in_a_subdirectory_of_the_repository(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    head = _commit(
        root, "claim", {"app/src/x.py": "x = 1\n", f"app/{LEDGER}": _ledger(("CL-001", "src/x.py:1", "prouvé"))}
    )
    code, payload = _resolve(root, f"{base}..{head}", project_root=root / "app")
    assert (code, _statuses(payload)) == (0, [("src/x.py:1", "resolved")])
    assert payload["proofs"][0]["ledger"] == LEDGER


def test_text_output_names_every_status_and_the_anchor(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    head = _commit(root, "claim", {LEDGER: _ledger(("CL-001", "src/real.py:2, src/gone.py:1, mem:x", "prouvé"))})
    code, payload = _resolve(root, f"{base}..{head}", json_out=False)
    out = payload["output"]
    assert code == 1
    assert "resolved  T-1 CL-001  src/real.py:2" in out
    assert "MISSING" in out and "UNKNOWN" in out
    assert f"Range {base[:12]}..{head[:12]}: 1 commit(s)" in out and "no remote" in out
    assert "3 proof(s): 1 resolved, 0 stale, 1 missing, 1 unknown." in out


@pytest.mark.parametrize(
    ("spec", "message"),
    [("nope..HEAD", "unknown revision in range"), ("HEAD...HEAD", "symmetric range"), ("deadbeef", "unknown revision")],
)
def test_a_bad_range_is_a_usage_error(tmp_path: Path, spec: str, message: str) -> None:
    root, _ = _repo(tmp_path)
    code, payload = _resolve(root, spec)
    assert code == 2 and f"evidence resolve: {message}" in payload["output"]


def test_outside_a_git_repository_is_a_usage_error(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    code, payload = _resolve(tmp_path / "plain", "HEAD")
    assert code == 2 and "not a git repository" in payload["output"]


def test_a_leaked_git_dir_does_not_redirect_the_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, base = _repo(tmp_path)
    head = _commit(root, "claim", {LEDGER: _ledger(("CL-001", "src/real.py:2", "prouvé"))})
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    code, payload = _resolve(root, f"{base}..{head}")
    assert (code, _statuses(payload)) == (0, [("src/real.py:2", "resolved")])


def test_head_published_is_reported(tmp_path: Path) -> None:
    root, base = _repo(tmp_path)
    head = _commit(root, "claim", {LEDGER: _ledger(("CL-001", "src/real.py:2", "prouvé"))})
    _git(root, "update-ref", "refs/remotes/origin/main", base)
    assert _resolve(root, f"{base}..{head}")[1]["anchor"]["head_published"] is False
    _git(root, "update-ref", "refs/remotes/origin/main", head)
    assert _resolve(root, f"{base}..{head}")[1]["anchor"]["head_published"] is True
