"""D-07 : ``host_sync.ok`` est exigé — un fichier préservé ou absent n'est pas une synchronisation réussie."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grimoire.cli.cmd_up import _step_host_sync, _UpState
from grimoire.hosts.sync import sync_host_surfaces
from grimoire.tools import project_upgrade


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Demo Sync"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["claude"]\n',
        encoding="utf-8",
    )
    return tmp_path


def _hand_edit(project: Path) -> None:
    readme = project / ".claude" / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nnote perso\n", encoding="utf-8")


class TestHostSyncOk:
    def test_un_fichier_preserve_n_est_pas_une_synchronisation_reussie(self, project: Path) -> None:
        assert sync_host_surfaces(project).ok
        _hand_edit(project)
        outcome = sync_host_surfaces(project)
        assert not outcome.ok
        assert outcome.conflicts == (".claude/README.md",)
        assert ".claude/README.md" in outcome.warning
        assert outcome.to_dict()["ok"] is False

    def test_up_ne_declare_pas_a_jour_un_hote_dont_un_fichier_est_preserve(self, project: Path) -> None:
        sync_host_surfaces(project)
        _hand_edit(project)
        state = _UpState()
        _step_host_sync(state, project, dry_run=False, blocked=False)
        assert state.steps[-1].status == "failed"
        assert ".claude/README.md" in state.steps[-1].detail
        assert state.failed

    def test_up_ignore_les_conflits_d_un_hote_desactive(self, project: Path) -> None:
        sync_host_surfaces(project)
        (project / "GEMINI.md").write_text("# a moi\n", encoding="utf-8")  # gemini n'est pas dans hosts.enabled
        state = _UpState()
        _step_host_sync(state, project, dry_run=False, blocked=False)
        assert state.steps[-1].status == "done"

    def _apply(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> project_upgrade.ApplyResult:
        ok = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        monkeypatch.setattr(project_upgrade, "_run_grimoire", lambda *a, **k: ok)
        monkeypatch.setattr(project_upgrade, "_doctor_checks", lambda _t: [])
        monkeypatch.setattr(project_upgrade, "probe_hook", lambda *a, **k: project_upgrade.ProbeResult(True, "ok"))
        return project_upgrade.apply_upgrade(project)

    def test_apply_refuse_quand_les_surfaces_hotes_manquent_apres_up(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = self._apply(project, monkeypatch)  # `up` simulé « réussi » sans avoir rien écrit
        assert not result.ok
        assert "host_sync" in result.failing_checks
        assert ".claude/settings.json" in result.host_drift

    def test_apply_passe_quand_les_surfaces_sont_a_jour(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sync_host_surfaces(project)
        result = self._apply(project, monkeypatch)
        assert result.ok and result.host_drift == ()
        assert result.to_dict()["host_drift"] == []
