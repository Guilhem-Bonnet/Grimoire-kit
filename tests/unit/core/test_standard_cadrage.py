"""``cadrage.*`` dans le standard agentique (issue #173).

Le cadrage (B4) n'était branché sur aucun gate de gouvernance : `cadrage
check` vivait à côté de `standard verify`/`gate`, jamais dans leur sortie.
Ces tests couvrent les quatre états de `_verify_cadrage` (verifiers.py) :
absent (rien à vérifier), incomplet sans need (avis seulement), incomplet
AVEC le need `project-discovery` choisi (FAIL — le projet l'a lui-même
demandé), et complet (rien à signaler).
"""

from __future__ import annotations

from pathlib import Path

from grimoire.cli.cmd_standard import _install_manifest_text
from grimoire.core.agentic_standard import resolve_install_plan, verify_standard_profile
from grimoire.core.cadrage import PHASES, scaffold
from grimoire.core.standard_generation import STANDARD_DIR


def _checks(result, check_id: str) -> list:
    return [c for c in result.checks if c.id == check_id]


def _write_install_manifest(root: Path, needs: list[str]) -> None:
    plan = resolve_install_plan(needs=needs)
    manifest_dir = root / STANDARD_DIR
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "install-manifest.yaml").write_text(
        _install_manifest_text(plan, "demo", "bootstrap"), encoding="utf-8",
    )


def _fill_every_phase(root: Path) -> None:
    """Écrit un cadrage complet — chaque section porte du contenu réel."""
    for phase in PHASES:
        lines = ["---", f"phase: {phase.id}", "projet: demo", "status: draft", "---", ""]
        for section in phase.sections:
            lines += [f"## {section}", "", "Rempli pour le test.", ""]
        (root / "_grimoire" / "cadrage" / phase.filename).write_text(
            "\n".join(lines), encoding="utf-8",
        )


class TestCadrageAbsent:
    def test_no_cadrage_dir_emits_nothing(self, tmp_path: Path) -> None:
        result = verify_standard_profile(tmp_path, profile_id="starter")
        assert _checks(result, "cadrage.gate_incomplete") == []
        assert _checks(result, "cadrage.phase_incomplete") == []


class TestCadrageIncompleteWithoutNeed:
    def test_gate_phases_are_only_a_warning(self, tmp_path: Path) -> None:
        scaffold(tmp_path, project_name="demo")
        result = verify_standard_profile(tmp_path, profile_id="starter")
        gate_checks = _checks(result, "cadrage.gate_incomplete")
        assert gate_checks, "les gabarits fraîchement posés sont incomplets par construction"
        assert all(c.severity == "warning" for c in gate_checks)
        phase_checks = _checks(result, "cadrage.phase_incomplete")
        assert phase_checks
        assert all(c.severity == "info" for c in phase_checks)
        # Un simple avertissement/info ne doit jamais, à lui seul, faire
        # échouer le standard (`is_error` ne regarde que severity == "error").
        assert not any(c.is_error for c in gate_checks + phase_checks)


class TestCadrageIncompleteWithNeedSelected:
    def test_gate_phases_become_a_hard_failure(self, tmp_path: Path) -> None:
        scaffold(tmp_path, project_name="demo")
        _write_install_manifest(tmp_path, ["project-discovery"])
        result = verify_standard_profile(tmp_path, profile_id="starter")
        gate_checks = _checks(result, "cadrage.gate_incomplete")
        assert gate_checks
        assert all(c.severity == "error" for c in gate_checks)
        phase_checks = _checks(result, "cadrage.phase_incomplete")
        assert phase_checks
        assert all(c.severity == "warning" for c in phase_checks)
        # C'est le point d'acceptation de l'issue #173 : le need choisi rend
        # le gate dur, jusqu'à ce que le cadrage soit complété.
        assert all(c.is_error for c in gate_checks)
        assert result.error_count >= len(gate_checks)

    def test_an_unrelated_need_does_not_escalate(self, tmp_path: Path) -> None:
        scaffold(tmp_path, project_name="demo")
        _write_install_manifest(tmp_path, ["solo-prototyping"])
        result = verify_standard_profile(tmp_path, profile_id="starter")
        gate_checks = _checks(result, "cadrage.gate_incomplete")
        assert gate_checks
        assert all(c.severity == "warning" for c in gate_checks)
        assert not any(c.is_error for c in gate_checks)


class TestCadrageComplete:
    def test_a_fully_filled_cadrage_emits_nothing(self, tmp_path: Path) -> None:
        scaffold(tmp_path, project_name="demo")
        _fill_every_phase(tmp_path)
        _write_install_manifest(tmp_path, ["project-discovery"])
        result = verify_standard_profile(tmp_path, profile_id="starter")
        assert _checks(result, "cadrage.gate_incomplete") == []
        assert _checks(result, "cadrage.phase_incomplete") == []
