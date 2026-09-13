"""Tests pour grimoire.tools.project_setup — le wizard exécute (issue #171).

``execute_setup_plan`` appelle ``grimoire up`` en direct (même mécanique que
la CLI, jamais un sous-processus) : ces tests exercent donc le vrai pipeline
sur un dossier jetable, pas un double. ``tests/conftest.py`` détourne déjà
``HOME`` (fixture session ``_isolate_user_state``) : l'enrôlement cockpit que
``grimoire up`` déclenche ne touche jamais la machine réelle.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from grimoire.tools.ext_manager import ExtensionError, InstallResult
from grimoire.tools.project_setup import execute_setup_plan, needs_catalogue, read_setup_run


def _no_extensions(_source: str) -> InstallResult:
    msg = "aucune extension dans ce test"
    raise ExtensionError(msg)


class TestExecuteSetupPlanRuns:
    def test_runs_grimoire_up_and_reports_a_green_doctor(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        plan = execute_setup_plan(
            target,
            {"name": "demo", "user": "guilhem", "archetype": "minimal", "backend": "local"},
            install=_no_extensions,
        )
        assert plan["executed"] is True
        assert (target / "project-context.yaml").is_file()
        assert (target / "_grimoire" / "standard").is_dir()
        run = plan["run"]
        assert run["ok"] is True
        assert run["doctorOk"] is True
        assert {s["step"] for s in run["steps"]} >= {"init", "standard", "host_sync", "doctor"}
        assert all(s["status"] != "failed" for s in run["steps"])

    def test_writes_a_run_journal_readable_by_polling(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        plan = execute_setup_plan(
            target, {"name": "demo", "backend": "local"}, install=_no_extensions,
        )
        run_path = target / "_grimoire" / "setup-run.json"
        assert run_path.is_file()
        on_disk = json.loads(run_path.read_text(encoding="utf-8"))
        assert on_disk["ok"] == plan["run"]["ok"]
        polled = read_setup_run(target)
        assert polled["available"] is True
        assert polled["ok"] == plan["run"]["ok"]

    def test_no_journal_before_any_run(self, tmp_path: Path) -> None:
        assert read_setup_run(tmp_path / "never-setup") == {"available": False}


class TestExecuteSetupPlanNeeds:
    """B3 rebranché sur B2 : les needs choisis dans le wizard atteignent le
    profil du standard installé — pas seulement le champ `plan["needs"]`."""

    def test_needs_are_transmitted_to_the_standard_install(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        plan = execute_setup_plan(
            target,
            {"name": "demo", "backend": "local", "needs": ["solo-prototyping"]},
            install=_no_extensions,
        )
        assert plan["needs"] == ["solo-prototyping"]
        manifest_path = target / "_grimoire" / "standard" / "install-manifest.yaml"
        assert manifest_path.is_file()
        assert "solo-prototyping" in manifest_path.read_text(encoding="utf-8")

    def test_unknown_need_refuses_before_writing_anything(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        with pytest.raises(ValueError, match="totally-bogus-need"):
            execute_setup_plan(
                target,
                {"name": "demo", "backend": "local", "needs": ["totally-bogus-need"]},
                install=_no_extensions,
            )
        assert not target.exists()

    def test_needs_catalogue_lists_known_ids_and_suggestions(self, tmp_path: Path) -> None:
        catalogue = needs_catalogue(tmp_path)
        ids = {n["id"] for n in catalogue["needs"]}
        assert "solo-prototyping" in ids
        assert "project-discovery" in ids
        # Projet vierge, aucun signal : le fallback recommande un point d'entrée.
        assert any(s["id"] in ("solo-prototyping", "project-discovery") for s in catalogue["suggested"])


class TestExecuteSetupPlanRefusesFailClosed:
    def test_unknown_archetype_refuses_before_writing_anything(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        with pytest.raises(ValueError, match="archetype"):
            execute_setup_plan(
                target, {"archetype": "not-an-archetype"}, install=_no_extensions,
            )
        assert not target.exists()

    def test_unknown_backend_refuses_before_writing_anything(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        with pytest.raises(ValueError, match="backend"):
            execute_setup_plan(
                target, {"backend": "postgres"}, install=_no_extensions,
            )
        assert not target.exists()

    def test_unwritable_path_refuses_and_names_the_cause(self, tmp_path: Path) -> None:
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x : lecture seule
        target = locked / "proj"
        try:
            if os.geteuid() == 0:  # pragma: no cover - jamais en CI
                pytest.skip("root ignore les permissions POSIX")
            with pytest.raises(ValueError, match="chemin non inscriptible"):
                execute_setup_plan(target, {}, install=_no_extensions)
            assert not target.exists()
        finally:
            locked.chmod(stat.S_IRWXU)


class TestExecuteSetupPlanIdempotent:
    def test_second_run_changes_nothing_it_already_wrote(self, tmp_path: Path) -> None:
        target = tmp_path / "proj"
        payload = {"name": "demo", "user": "guilhem", "archetype": "minimal", "backend": "local"}
        execute_setup_plan(target, payload, install=_no_extensions)
        config_path = target / "project-context.yaml"
        before = config_path.read_text(encoding="utf-8")
        before_mtime = config_path.stat().st_mtime_ns

        second = execute_setup_plan(target, payload, install=_no_extensions)

        assert config_path.read_text(encoding="utf-8") == before
        assert config_path.stat().st_mtime_ns == before_mtime
        steps_by_name = {s["step"]: s for s in second["run"]["steps"]}
        assert steps_by_name["init"]["status"] == "skipped"
        assert second["run"]["ok"] is True
