"""#1342 / #1197 : la traçabilité d'une exigence, de bout en bout, en lecture seule (``grimoire cadrage trace``).

Un vrai dépôt git par test (SHA réels), un HOME par test (le journal d'autorité est celui du test).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.cadrage import CADRAGE_DIR
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import cadrage_trace as ct
from grimoire.missions import requirement_gate as rg
from grimoire.missions.mandate import AuthorityJournal
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService

pytestmark = pytest.mark.covers("cli:cadrage trace")

runner = CliRunner()

EXIGENCES = """# Exigences

- REQ-001 [Must] : l'utilisateur se connecte par lien magique
  - le lien expire après 15 minutes
- REQ-002 (Should) : export CSV des commandes
  - le fichier s'ouvre dans un tableur
- REQ-003 (Could) : journal d'audit
"""
_AGENT_ENVS = ("GRIMOIRE_UNATTENDED", SESSION_ENV, *HOST_SESSION_ENVS, *AGENT_MARKER_ENVS)


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in _AGENT_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _write(repo: Path, name: str, text: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str, **files: str) -> str:
    for name, text in files.items():
        _write(repo, name.replace("__", "/").replace("_py", ".py").replace("_md", ".md"), text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _project(root: Path, *, text: str = EXIGENCES) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    data = {"project": {"name": root.name}, "governance": {"roles": {"moa": "Ada"}, "requirement_gate": "warn"}}
    (root / "project-context.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    (root / CADRAGE_DIR).mkdir(parents=True, exist_ok=True)
    (root / CADRAGE_DIR / "04-exigences.md").write_text(text, encoding="utf-8")
    (root / ".gitignore").write_text("_grimoire*/\nproject-context.yaml\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "base", README__md="x")
    return root


def _task(service: TaskService, key: str = "REQ-001", title: str = "connexion") -> str:
    return service.add(title, ("ça marche",), requirement=key).task.id


def _feature_branch(root: Path) -> tuple[str, str]:
    base = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", "-b", "feat")
    _commit(root, "feat: lien magique", src__login_py="a")
    head = _commit(root, "test: lien magique", tests__test_login_py="b")
    _git(root, "checkout", "-q", "main")
    return base, head


def _trace(root: Path, chantier: str | None = None) -> ct.CadrageTrace:
    return ct.build_trace(root, TaskService(root), chantier)


def _req(trace: ct.CadrageTrace, key: str) -> ct.RequirementTrace:
    return next(r for r in trace.requirements if r.key == key)


def _cli(root: Path, *args: str) -> Any:
    return runner.invoke(app, ["cadrage", "trace", *args, "--path", str(root)])


# ── la chaîne ───────────────────────────────────────────────────────────────


def test_cadrage_trace_chain_from_requirement_to_files_and_tests(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    rg.approve_requirement(root, "REQ-001", actor="ada")
    service = TaskService(root)
    tid = _task(service)
    base, head = _feature_branch(root)
    service.ledger.append_event(
        "task.merged", tid, "task", "x", {"base_sha": base, "head_sha": head, "merged_sha": head}
    )
    req = _req(_trace(root), "REQ-001")
    assert (req.stage, req.validated, req.validated_by) == ("validated", True, "Ada")
    (task,) = req.tasks
    assert task.files == ["src/login.py", "tests/test_login.py"]
    assert task.tests == ["tests/test_login.py"] and req.tests == ["tests/test_login.py"]
    assert [c.subject for c in task.commits] == ["test: lien magique", "feat: lien magique"]
    assert task.merged_sha == head[:12]
    data = ct.build_trace(root, service).to_dict()
    assert data["schema"] == ct.SCHEMA and data["chantier"] == "principal"
    assert json.loads(json.dumps(data)) == data


def test_cadrage_trace_external_merge_with_merge_commit_and_squash(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    rg.approve_requirement(root, "REQ-001", actor="ada")
    rg.approve_requirement(root, "REQ-002", actor="ada")
    service = TaskService(root)
    t1, t2 = _task(service), _task(service, "REQ-002", "export")
    _feature_branch(root)
    _git(root, "merge", "-q", "--no-ff", "-m", "Merge PR 7", "feat")
    merge = _git(root, "rev-parse", "HEAD")
    squash = _commit(root, "feat: export CSV (#8)", src__export_py="c")
    service.ledger.close_merged_externally(t1, pr=7, merged_sha=merge, branch="task/x")
    service.ledger.close_merged_externally(t2, pr=8, merged_sha=squash)
    trace = _trace(root)
    first, second = _req(trace, "REQ-001").tasks[0], _req(trace, "REQ-002").tasks[0]
    assert first.pr == 7 and first.files == ["src/login.py", "tests/test_login.py"] and len(first.commits) == 2
    assert second.pr == 8 and second.files == ["src/export.py"]
    assert [c.subject for c in second.commits] == ["feat: export CSV (#8)"]
    assert _req(trace, "REQ-001").stage == "delivered" and not _req(trace, "REQ-001").gaps


def test_cadrage_trace_live_branch_and_unknown_shas_are_said_not_guessed(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    rg.approve_requirement(root, "REQ-001", actor="ada")
    service = TaskService(root)
    live, lost, bare = _task(service), _task(service, title="b"), _task(service, title="c")
    base, _ = _feature_branch(root)
    service.ledger.append_event("task.worktree_created", live, "task", "x", {"branch": "feat", "base_sha": base})
    service.ledger.append_event(
        "task.worktree_created", bare, "task", "x", {"branch": "task/disparue", "base_sha": base}
    )
    service.ledger.append_event(
        "task.merged", lost, "task", "x", {"base_sha": "1" * 40, "head_sha": "2" * 40, "merged_sha": "2" * 40}
    )
    by_id = {t.id: t for t in _req(_trace(root), "REQ-001").tasks}
    assert by_id[live].files == ["src/login.py", "tests/test_login.py"] and not by_id[live].notes
    assert by_id[lost].files == [] and "absents du dépôt local" in by_id[lost].notes[0]
    assert "task/disparue absente du dépôt local" in by_id[bare].notes[0]
    service.ledger.close_merged_externally(live, pr=1, merged_sha="3" * 40)
    gone = next(t for t in _req(_trace(root), "REQ-001").tasks if t.id == live)
    assert gone.pr == 1 and "SHA de fusion" in gone.notes[0]


def test_cadrage_trace_stages(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    service = TaskService(root)
    assert _req(_trace(root), "REQ-001").stage == "proposed"
    rg.approve_requirement(root, "REQ-001", actor="ada")
    tid = _task(service)
    assert _req(_trace(root), "REQ-001").stage == "validated"
    service.transition(tid, TaskState.READY, "ada")
    service.claim(tid, "ada")
    assert _req(_trace(root), "REQ-001").stage == "in_progress"


# ── les trous ───────────────────────────────────────────────────────────────


def test_cadrage_trace_gaps_validated_without_task_and_requirement_without_criterion(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    rg.approve_requirement(root, "REQ-001", actor="ada")
    trace = _trace(root)
    assert [g.kind for g in _req(trace, "REQ-001").gaps] == [ct.NO_TASK]
    assert [g.kind for g in _req(trace, "REQ-003").gaps] == [ct.NO_CRITERION]
    assert _req(trace, "REQ-002").gaps == []
    assert {k for k, _ in trace.gaps} == {"REQ-001", "REQ-003"}


def test_cadrage_trace_gap_delivered_without_proof() -> None:
    bare = ct.TaskTrace("T-1", "t", "closed", True)
    proved = ct.TaskTrace("T-2", "t", "closed", True, merged="PR #1 fusionnée à abc")
    open_ = ct.TaskTrace("T-3", "t", "running", False)
    req = ct.RequirementTrace("REQ-001", "x", "", ("c",), "delivered", "livree", True, tasks=[bare, proved, open_])
    view = type("V", (), {"criteria": ("c",)})()
    (gap,) = ct._gaps(req, view)  # type: ignore[arg-type]
    assert gap.kind == ct.NO_PROOF and gap.tasks == ("T-1",) and gap.label == "tâche livrée sans preuve"
    assert bare.to_dict()["proof"]["source"] == "" and proved.to_dict()["proof"]["source"]


# ── la dérive ───────────────────────────────────────────────────────────────


def test_cadrage_trace_drift_criteria_then_title(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    rg.approve_requirement(root, "REQ-001", actor="ada")
    service = TaskService(root)
    tid = _task(service)
    base, head = _feature_branch(root)
    service.ledger.append_event(
        "task.merged", tid, "task", "x", {"base_sha": base, "head_sha": head, "merged_sha": head}
    )
    assert _req(_trace(root), "REQ-001").drift is None
    path = root / CADRAGE_DIR / "04-exigences.md"
    path.write_text(EXIGENCES.replace("15 minutes", "5 minutes"), encoding="utf-8")
    drift = _req(_trace(root), "REQ-001").drift
    assert drift is not None and not drift.title_changed and "texte validé n'est pas conservé" in drift.what
    assert drift.tasks == (tid,) and drift.files == ("src/login.py", "tests/test_login.py")
    assert drift.to_dict()["message"] == "modifiée depuis la validation"
    path.write_text(EXIGENCES.replace("lien magique", "lien par SMS"), encoding="utf-8")
    drift = _req(_trace(root), "REQ-001").drift
    assert drift is not None and drift.title_changed and drift.what == "formulation"
    assert drift.validated_title.endswith("lien magique") and drift.current_title.endswith("lien par SMS")
    assert _req(_trace(root), "REQ-001").stage == "proposed"
    path.write_text(EXIGENCES, encoding="utf-8")  # revenir au texte validé : plus de dérive
    assert _req(_trace(root), "REQ-001").drift is None
    rg.approve_requirement(root, "REQ-002", actor="ada")
    assert _req(_trace(root), "REQ-002").drift is None


def test_cadrage_trace_never_validated_is_not_drift(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    assert all(r.drift is None for r in _trace(root).requirements)


def test_cadrage_trace_reads_only(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    rg.approve_requirement(root, "REQ-001", actor="ada")
    journal = AuthorityJournal().path.read_bytes()
    before = _git(root, "status", "--porcelain")
    _trace(root)
    _cli(root, "--json")
    assert AuthorityJournal().path.read_bytes() == journal and _git(root, "status", "--porcelain") == before


# ── le sens inverse ─────────────────────────────────────────────────────────


def test_cadrage_trace_file_to_requirements(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    rg.approve_requirement(root, "REQ-001", actor="ada")
    service = TaskService(root)
    tid = _task(service)
    base, head = _feature_branch(root)
    service.ledger.append_event(
        "task.merged", tid, "task", "x", {"base_sha": base, "head_sha": head, "merged_sha": head}
    )
    trace = _trace(root)
    served = trace.for_file("src/login.py")
    assert served.served and [r["requirement"] for r in served.requirements] == ["REQ-001"]
    assert served.requirements[0]["tasks"][0]["id"] == tid
    assert not trace.for_file("README.md").served
    assert ct.normalize_file(root, str(root / "src" / "login.py")) == "src/login.py"
    assert ct.normalize_file(root, "./src\\login.py") == "src/login.py" and ct.normalize_file(root, " ") == ""


# ── la commande ─────────────────────────────────────────────────────────────


def test_cadrage_trace_cli_json_text_and_file(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    rg.approve_requirement(root, "REQ-001", actor="ada")
    service = TaskService(root)
    tid = _task(service)
    base, head = _feature_branch(root)
    service.ledger.append_event(
        "task.merged", tid, "task", "x", {"base_sha": base, "head_sha": head, "merged_sha": head}
    )
    data = json.loads(_cli(root, "--json").stdout)
    assert [r["requirement"] for r in data["requirements"]] == ["REQ-001", "REQ-002", "REQ-003"]
    one = json.loads(_cli(root, "req-001", "--json").stdout)
    assert [r["requirement"] for r in one["requirements"]] == ["REQ-001"]
    text = _cli(root)
    assert text.exit_code == 0 and "REQ-001" in text.stdout and "src/login.py" in text.stdout
    assert "trou" in text.stdout and "sans critère" in text.stdout
    inverse = json.loads(_cli(root, "--file", "src/login.py", "--json").stdout)
    assert inverse["served"] and inverse["requirements"][0]["requirement"] == "REQ-001"
    assert "servi par 1 exigence" in _cli(root, "--file", str(root / "src/login.py")).stdout
    assert "aucune exigence ne sert ce fichier" in _cli(root, "--file", "README.md").stdout
    assert json.loads(_cli(root, "--file", "README.md", "--json").stdout)["served"] is False
    assert "aucune exigence ne sert" in _cli(root, "REQ-002", "--file", "src/login.py").stdout


def test_cadrage_trace_cli_text_shows_drift_and_unknown_requirement_fails(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    rg.approve_requirement(root, "REQ-001", actor="ada")
    (root / CADRAGE_DIR / "04-exigences.md").write_text(
        EXIGENCES.replace("lien magique", "lien par SMS"), encoding="utf-8"
    )
    out = _cli(root, "REQ-001").stdout
    assert "modifiée depuis la validation" in out and "formulation" in out and "lien par SMS" in out
    assert "modifiée(s) depuis la validation : REQ-001" in out
    missing = _cli(root, "REQ-009")
    assert missing.exit_code == 1 and "introuvable" in missing.stdout
    assert _cli(root, "nimporte-quoi").exit_code == 1


def test_cadrage_trace_without_identified_requirements_says_so(tmp_path: Path) -> None:
    root = _project(tmp_path / "p", text="# Exigences\n\nde la prose seulement\n")
    result = _cli(root)
    assert result.exit_code == 0 and "aucune exigence identifiée" in result.stdout


def test_cadrage_trace_named_chantier_keys(tmp_path: Path) -> None:
    from grimoire.core.chantiers import new_chantier

    root = _project(tmp_path / "p")
    new_chantier(root, "Atelier", slug="atelier")
    (root / CADRAGE_DIR / "chantiers" / "atelier").mkdir(parents=True, exist_ok=True)
    for candidate in (root / CADRAGE_DIR).rglob("04-exigences.md"):
        if "atelier" in candidate.parts:
            candidate.write_text("- REQ-001 : un atelier\n  - `pytest`\n", encoding="utf-8")
    trace = _trace(root, "atelier")
    assert [r.key for r in trace.requirements] == ["atelier/REQ-001"] and trace.chantier == "atelier"
    assert json.loads(_cli(root, "REQ-001", "--chantier", "atelier", "--json").stdout)["chantier"] == "atelier"


def test_cadrage_trace_repo_git_is_defensive(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    git = ct.RepoGit(root)
    assert git.resolve("-x") == "" and git.resolve("") == "" and git.resolve("0" * 40) == ""
    assert git.resolve("HEAD") and git.files("0" * 40, "1" * 40) is None
    assert git.parents("0" * 40) == [] and git.commits("0" * 40, "1" * 40) == []
    assert git.subject("HEAD") == "base"
    missing = ct.RepoGit(tmp_path / "nope")
    assert missing.resolve("HEAD") == ""


class _NoGit:
    """Un dépôt qui ne répond rien : tout est inconnu."""

    def resolve(self, ref: str) -> str:
        return ""

    def commits(self, base: str, head: str) -> list[tuple[str, str]]:
        return []

    def files(self, base: str, head: str) -> list[str] | None:
        return None

    def parents(self, sha: str) -> list[str]:
        return ["p" * 40]

    def subject(self, sha: str) -> str:
        return ""


def test_cadrage_trace_unreadable_git_and_bare_delivered_task_are_noted() -> None:
    from grimoire.missions.schemas import LedgerEvent

    def event(kind: str, **payload: Any) -> LedgerEvent:
        return LedgerEvent("e", kind, "T", "task", "x", "now", payload=payload)

    bare = ct.TaskTrace("T", "t", "closed", True)
    ct._fill_code(bare, [], _NoGit())
    assert bare.files == [] and "aucune fusion ni branche" in bare.notes[0]
    cut = ct.TaskTrace("T", "t", "closed", True)
    ct._fill_code(cut, [event("task.merged_externally", pr=3, merged_sha="a" * 40)], _NoGit())
    assert cut.pr == 3 and "illisible" in cut.notes[0]
    empty = ct.TaskTrace("T", "t", "closed", True)
    ct._fill_code(empty, [event("task.merged", base_sha="a" * 40, head_sha="b" * 40)], _NoGit())
    assert "absents du dépôt local" in empty.notes[0]


def test_cadrage_trace_stage_of_each_recette_state() -> None:
    from grimoire.missions import moa_acceptance as moa

    def view(state: str, validated: bool = True) -> Any:
        return type("V", (), {"validated": validated, "state": state, "tasks": []})()

    assert ct._stage(view(moa.PROPOSED, False)) == "proposed"
    assert ct._stage(view(moa.ACCEPTED_RESERVE)) == "accepted" and ct._stage(view(moa.ACCEPTED_WIP)) == "accepted"
    assert ct._stage(view(moa.REJECTED)) == "rework" and ct._stage(view(moa.FOLLOW_UP_MISSING)) == "rework"
    assert ct._stage(view(moa.DELIVERED)) == "delivered" and ct._stage(view(moa.VALIDATED)) == "validated"


def test_cadrage_trace_unreadable_journal_and_events_are_noted_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path / "p")
    rg.approve_requirement(root, "REQ-001", actor="ada")
    service = TaskService(root)
    _task(service)

    def boom(_: str) -> list[Any]:
        from grimoire.core.exceptions import GrimoireMissionError

        raise GrimoireMissionError("ledger cassé")

    monkeypatch.setattr(service.ledger, "events_for", boom)
    AuthorityJournal().path.write_text("pas du json\n", encoding="utf-8")
    trace = ct.build_trace(root, service)
    assert any("journal d'autorité illisible" in n for n in trace.notes)
    assert "événements illisibles" in trace.requirements[0].tasks[0].notes[0]
    assert all(r.drift is None for r in trace.requirements)


def test_cadrage_trace_file_outside_project_is_kept_as_given(tmp_path: Path) -> None:
    assert ct.normalize_file(tmp_path / "p", "/ailleurs/x.py") == "/ailleurs/x.py"


def test_cadrage_trace_cli_text_full_chain_with_proofs_notes_recette_and_drift_scope(tmp_path: Path) -> None:
    from grimoire.missions import moa_acceptance as moa

    root = _project(tmp_path / "p")
    rg.approve_requirement(root, "REQ-001", actor="ada")
    service = TaskService(root)
    done, lost = _task(service), _task(service, title="perdue")
    _feature_branch(root)
    _git(root, "merge", "-q", "--no-ff", "-m", "Merge PR 7", "feat")
    service.ledger.close_merged_externally(done, pr=7, merged_sha=_git(root, "rev-parse", "HEAD"))
    service.ledger.append_event(
        "task.merged", lost, "task", "x", {"base_sha": "1" * 40, "head_sha": "2" * 40, "merged_sha": "2" * 40}
    )
    service.cancel(lost, "test", "ada", force=True)
    other = _task(service, title="encore")
    service.ledger.append_event(
        "task.merged", other, "task", "x", {"base_sha": "1" * 40, "head_sha": "2" * 40, "merged_sha": "2" * 40}
    )
    out = _cli(root, "REQ-001").stdout
    assert "PR #7" in out and "(test)" in out and "preuves : PR #7 fusionnée" in out and "non vérifié par le kit" in out
    assert "absents du dépôt local" in out
    service.cancel(other, "test", "ada", force=True)
    moa.pronounce(root, "REQ-001", "conforme", "", actor="ada")
    recetted = _cli(root, "REQ-001")
    assert "recette prononcée" in recetted.stdout and "recette : conforme" in recetted.stdout
    (root / CADRAGE_DIR / "04-exigences.md").write_text(EXIGENCES.replace("15 minutes", "5 minutes"), encoding="utf-8")
    drifted = _cli(root, "REQ-001").stdout
    assert "tâches concernées" in drifted and "fichiers concernés : src/login.py" in drifted
    assert "critères, priorité ou périmètre" in drifted
