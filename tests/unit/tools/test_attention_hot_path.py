"""Vue « attention » à chaud (#1243) : mêmes réponses, moins de travail, jamais d'état périmé.

Pas de test de durée (les runners de CI sont lents) : on compte les sous-processus git, les relectures
du préfixe couvert du ledger et les parsings, et on prouve qu'une écriture invalide chaque garde.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

from grimoire.core import file_memo
from grimoire.missions import ledger_snapshot, liveness, verifiability
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.tools import project_health
from grimoire.tools import workspace_attention as wa
from grimoire.tools import workspace_portfolio as portfolio

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    file_memo.clear()
    ledger_snapshot.clear_covered_cache()
    project_health._tool_versions.clear()


def _git(root: Path, *args: str, date: str | None = None) -> None:
    env = {**os.environ, **_GIT_ENV, **({"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else {})}
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def _repo_with_branches(root: Path, names: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "commit", "-q", "--allow-empty", "-m", "base", date="2026-01-01T00:00:00+00:00")
    for name, date in names.items():
        _git(root, "checkout", "-q", "-b", f"task/{name}", "main")
        _git(root, "commit", "-q", "--allow-empty", "-m", name, date=date)
    _git(root, "checkout", "-q", "main")


class _Counting:
    """Un ``subprocess.run`` qui compte les appels git."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> Any:
        self.calls.append(cmd)
        return subprocess.run(cmd, **kwargs)


class TestGitBatch:
    def test_meme_reponse_que_git_log_par_tache(self, tmp_path: Path) -> None:
        _repo_with_branches(tmp_path, {"T-1": "2026-03-01T10:00:00+00:00", "T-2": "2026-04-02T11:30:00+00:00"})
        commit_at = liveness.git_commit_times(tmp_path)

        for task_id in ("T-1", "T-2", "T-absent", "--bad", "a b", "task/T-1"):
            assert commit_at(tmp_path, task_id) == liveness.git_commit_at(tmp_path, task_id), task_id

    def test_un_seul_sous_processus_pour_toutes_les_taches(self, tmp_path: Path) -> None:
        _repo_with_branches(tmp_path, {f"T-{i}": f"2026-03-0{i}T10:00:00+00:00" for i in range(1, 6)})
        spy = _Counting()
        commit_at = liveness.git_commit_times(tmp_path, run=spy)

        found = [commit_at(tmp_path, f"T-{i}") for i in range(1, 6)] + [commit_at(tmp_path, "inconnue")]

        assert len(spy.calls) == 1
        assert found[:5] == [liveness.git_commit_at(tmp_path, f"T-{i}") for i in range(1, 6)]
        assert found[5] is None

    def test_aucune_tache_tenue_aucun_git(self, tmp_path: Path) -> None:
        spy = _Counting()
        liveness.git_commit_times(tmp_path, run=spy)
        assert spy.calls == []

    def test_un_git_qui_echoue_donne_none_sans_retenter(self, tmp_path: Path) -> None:
        def broken(*_a: Any, **_k: Any) -> Any:
            raise OSError("git absent")

        commit_at = liveness.git_commit_times(tmp_path, run=broken)
        assert commit_at(tmp_path, "T-1") is None
        assert commit_at(tmp_path, "T-2") is None

    def test_la_vue_ne_lance_qu_un_git_pour_n_taches_tenues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _repo_with_branches(tmp_path, {})
        service = TaskService(tmp_path)
        for i in range(4):
            tid = service.add(f"Tenue {i}", ("ok",), actor="t", owner="g", ready=True).task.id
            service.claim(tid, "agent-a", host="host-claude-code-cli")
            service.transition(tid, TaskState.RUNNING, "agent-a")
        spy = _Counting()
        real = liveness.git_commit_times
        monkeypatch.setattr(portfolio, "git_commit_times", lambda root: real(root, run=spy))

        wa.attention_view(tmp_path, pr_reader=lambda *_a, **_k: [], service_probe=lambda *_a: "online")

        assert len([c for c in spy.calls if "for-each-ref" in c]) == 1
        assert not [c for c in spy.calls if "log" in c]


class TestCoveredEventsCache:
    def _snapshotted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TaskService, Path]:
        service = TaskService(tmp_path)
        root = service.ledger._root
        for i in range(5):
            service.add(f"Tache {i}", ("ok",), actor="t", owner="g", ready=True)
        monkeypatch.setattr(ledger_snapshot, "SNAPSHOT_AFTER_MS", -1.0)
        MissionLedger(root).list_tasks()  # écrit l'instantané
        monkeypatch.setattr(ledger_snapshot, "SNAPSHOT_AFTER_MS", 1e9)
        assert (root / ledger_snapshot.SNAPSHOT_FILENAME).is_file()
        return service, root

    def test_le_prefixe_couvert_n_est_parse_qu_une_fois(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _, root = self._snapshotted(tmp_path, monkeypatch)
        parsed: list[int] = []
        original = ledger_snapshot.parse_jsonl
        monkeypatch.setattr(
            ledger_snapshot, "parse_jsonl", lambda data, *a: (parsed.append(len(data)), original(data, *a))[1]
        )

        first = [e.to_dict() for e in MissionLedger(root).list_events()]
        second = [e.to_dict() for e in MissionLedger(root).list_events()]

        assert first == second
        assert len([n for n in parsed if n > 0]) == 1  # le préfixe, une fois ; les suites sont vides
        full = [e.to_dict() for e in _without_snapshot(root, monkeypatch).list_events()]
        assert first == full

    def test_une_ecriture_apres_l_instantane_est_vue(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        service, root = self._snapshotted(tmp_path, monkeypatch)
        before = {e.id for e in MissionLedger(root).list_events()}

        service.add("Nouvelle", ("ok",), actor="t", owner="g", ready=True)

        after = {e.id for e in MissionLedger(root).list_events()}
        assert before < after

    def test_un_prefixe_reecrit_n_est_jamais_servi_depuis_le_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, root = self._snapshotted(tmp_path, monkeypatch)
        stale = [e.id for e in MissionLedger(root).list_events()]  # remplit le cache
        events = root / "events.jsonl"
        data = events.read_bytes()
        # Même taille, un octet du préfixe change : l'empreinte ne tient plus, l'instantané est refusé.
        events.write_bytes(data.replace(b"Tache 0", b"Tache X", 1))

        fresh = MissionLedger(root).list_events()

        assert {e.payload.get("title") for e in fresh if e.event_type == "task.created"} >= {"Tache X"}
        assert [e.id for e in fresh] == stale


def _without_snapshot(root: Path, monkeypatch: pytest.MonkeyPatch) -> MissionLedger:
    monkeypatch.setattr(ledger_snapshot, "_read_valid", lambda *_a: None)
    return MissionLedger(root)


class TestClassifyMemo:
    CASES: ClassVar[list[tuple[tuple[str, ...], tuple[str, ...]]]] = [
        (("`pytest -q` passe",), ("pytest -q",)),
        (("revue du rapport de tests",), ()),
        (("ça marche",), ()),
        ((), ()),
        (("la sortie 0 de `ruff check`", "relecture humaine"), ("ruff check",)),
    ]

    def test_meme_verdict_que_le_calcul_direct(self) -> None:
        for criteria, commands in self.CASES:
            direct = verifiability.classify_criteria(criteria, commands)
            assert verifiability._classify_text(criteria, commands) is direct
            assert verifiability._classify_text(criteria, commands) is direct  # servi du cache
            for text in criteria:
                assert verifiability._classer_critere(text, commands) == verifiability._classer_critere_pur.__wrapped__(
                    text, tuple(commands)
                )

    def test_une_commande_declaree_change_le_verdict_malgre_le_cache(self) -> None:
        text = "la suite `pytest -q` passe"
        assert verifiability._classer_critere(text, ())
        with_command = verifiability._classer_critere(text, ("pytest -q",))
        assert with_command == verifiability._classer_critere_pur.__wrapped__(text, ("pytest -q",))


class TestProposalsMemo:
    def test_une_reecriture_dans_le_meme_pas_d_horloge_est_vue(self, tmp_path: Path) -> None:
        from grimoire.proposals import create_manual_proposal, list_proposals

        create_manual_proposal(tmp_path, slug="relecteur", specialty="relecture A", artifact_type="agent")
        first = [p.specialty for p in list_proposals(tmp_path, sync=False)]
        path = next((tmp_path).rglob("relecteur.yaml"))
        path.write_text(path.read_text(encoding="utf-8").replace("relecture A", "relecture B"), encoding="utf-8")

        second = [p.specialty for p in list_proposals(tmp_path, sync=False)]

        assert first == ["relecture A"]
        assert second == ["relecture B"]

    def test_un_fichier_abime_n_est_pas_garde(self, tmp_path: Path) -> None:
        from grimoire.proposals import create_manual_proposal, list_proposals

        create_manual_proposal(tmp_path, slug="relecteur", specialty="relecture A", artifact_type="agent")
        path = next((tmp_path).rglob("relecteur.yaml"))
        good = path.read_text(encoding="utf-8")
        old = datetime.now(UTC) - timedelta(hours=1)
        os.utime(path, (old.timestamp(), old.timestamp()))
        assert len(list_proposals(tmp_path, sync=False)) == 1

        path.write_text("{ pas du yaml", encoding="utf-8")
        assert list_proposals(tmp_path, sync=False) == []
        path.write_text(good, encoding="utf-8")
        assert len(list_proposals(tmp_path, sync=False)) == 1


class TestToolVersionMemo:
    def _venv(self, root: Path, version: str) -> Path:
        binary = root / ".venv" / "bin" / "grimoire"
        binary.parent.mkdir(parents=True, exist_ok=True)
        (root / ".venv" / "lib" / "python3.12" / "site-packages").mkdir(parents=True, exist_ok=True)
        binary.write_text(f"#!/bin/sh\necho 'grimoire-kit {version}'\n", encoding="utf-8")
        binary.chmod(0o755)
        old = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
        for path in (binary, root / ".venv" / "lib" / "python3.12" / "site-packages"):
            os.utime(path, (old, old))
        return binary

    def test_un_seul_lancement_tant_que_rien_ne_bouge(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._venv(tmp_path, "3.1.0")
        runs: list[Path] = []
        real = project_health._run_tool_version
        monkeypatch.setattr(project_health, "_run_tool_version", lambda b: (runs.append(b), real(b))[1])

        versions = [project_health.project_tool_version(tmp_path)["version"] for _ in range(3)]

        assert versions == ["3.1.0"] * 3
        assert len(runs) == 1

    def test_une_mise_a_jour_du_binaire_est_vue(self, tmp_path: Path) -> None:
        self._venv(tmp_path, "3.1.0")
        assert project_health.project_tool_version(tmp_path)["version"] == "3.1.0"

        self._venv(tmp_path, "3.2.0")  # même chemin, contenu et taille différents

        assert project_health.project_tool_version(tmp_path)["version"] == "3.2.0"

    def test_une_mise_a_jour_du_venv_est_vue(self, tmp_path: Path) -> None:
        binary = self._venv(tmp_path, "3.1.0")
        assert project_health.project_tool_version(tmp_path)["version"] == "3.1.0"
        stat = binary.stat()

        # pip remplace un dossier dist-info : site-packages change de date ; le binaire, lui, garde date et taille.
        binary.write_text("#!/bin/sh\necho 'grimoire-kit 3.9.9'\n", encoding="utf-8")
        os.utime(binary, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        (tmp_path / ".venv" / "lib" / "python3.12" / "site-packages" / "grimoire_kit-3.9.9.dist-info").mkdir()

        assert project_health.project_tool_version(tmp_path)["version"] == "3.9.9"

    def test_un_echec_n_est_pas_garde(self, tmp_path: Path) -> None:
        binary = self._venv(tmp_path, "3.1.0")
        binary.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
        old = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
        os.utime(binary, (old, old))
        assert project_health.project_tool_version(tmp_path)["version"] is None

        binary.write_text("#!/bin/sh\necho 'grimoire-kit 3.1.0'\n", encoding="utf-8")
        os.utime(binary, (old, old))
        (tmp_path / ".venv" / "lib" / "python3.12" / "site-packages" / "x.dist-info").mkdir()

        assert project_health.project_tool_version(tmp_path)["version"] == "3.1.0"


class TestViewEquivalence:
    """La vue du chemin optimisé (caches pleins) égale celle du chemin d'origine (caches vides, git par tâche)."""

    def _project(self, root: Path) -> list[str]:
        _repo_with_branches(root, {})
        service = TaskService(root)
        ids = []
        for i in range(6):
            ids.append(service.add(f"Tâche {i}", ("`pytest -q` passe",), actor="t", owner="g", ready=True).task.id)
        for tid in ids[:4]:
            service.claim(tid, "agent-a", host="host-claude-code-cli")
            service.transition(tid, TaskState.RUNNING, "agent-a")
        _git(root, "branch", f"task/{ids[0]}")
        service.transition(ids[1], TaskState.NEEDS_VERIFICATION, "agent-a")
        service.transition(ids[2], TaskState.BLOCKED, "agent-a", reason="attend une clé")
        service.ask(ids[3], "On fusionne ?", "agent-a", kind="merge", options=("oui", "non"))
        return ids

    @staticmethod
    def _view(root: Path, now: datetime) -> dict[str, Any]:
        view = wa.attention_view(root, now=now, pr_reader=lambda *_a, **_k: [], service_probe=lambda *_a: "online")
        view.pop("generated_at")
        return view

    def _cold(self) -> None:
        file_memo.clear()
        ledger_snapshot.clear_covered_cache()
        project_health._tool_versions.clear()
        verifiability._classify_text.cache_clear()
        verifiability._classer_critere_pur.cache_clear()

    def test_chaud_egal_froid_et_egal_a_l_ancien_git(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._project(tmp_path)
        now = datetime.now(UTC)
        optimized_cold = self._view(tmp_path, now)
        optimized_hot = self._view(tmp_path, now)

        self._cold()
        monkeypatch.setattr(
            portfolio, "git_commit_times", lambda root: lambda _r, task_id: liveness.git_commit_at(root, task_id)
        )
        legacy = self._view(tmp_path, now)

        assert optimized_hot == optimized_cold == legacy
        assert optimized_hot["count"] >= 2

    def test_une_ecriture_au_ledger_change_la_vue_chaude(self, tmp_path: Path) -> None:
        ids = self._project(tmp_path)
        now = datetime.now(UTC)
        self._view(tmp_path, now)  # remplit les caches
        before = self._view(tmp_path, now)

        TaskService(tmp_path).transition(ids[0], TaskState.BLOCKED, "agent-a", reason="nouveau blocage")
        after = self._view(tmp_path, now)

        assert after != before
        assert after["count"] == before["count"] + 1
