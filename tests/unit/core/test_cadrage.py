"""Tests du cadrage produit (B4) — comprendre avant de construire."""

from __future__ import annotations

from pathlib import Path

from grimoire.core import cadrage as c


def _fill_phase(root: Path, phase: c.Phase) -> None:
    """Remplit toutes les sections d'une phase avec du vrai contenu."""
    path = root / c.CADRAGE_DIR / phase.filename
    text = path.read_text(encoding="utf-8")
    for section in phase.sections:
        text = text.replace(
            f"{c.PLACEHOLDER} {section.lower()}.",
            f"Contenu réel pour {section}.",
        )
    path.write_text(text, encoding="utf-8")


class TestScaffold:
    def test_creates_five_phases(self, tmp_path: Path) -> None:
        written = c.scaffold(tmp_path, project_name="demo")
        assert len(written) == 5
        names = sorted(p.name for p in written)
        assert names[0] == "01-brief.md"
        assert names[-1] == "05-cahier-des-charges.md"
        brief = (tmp_path / c.CADRAGE_DIR / "01-brief.md").read_text(encoding="utf-8")
        assert "projet: demo" in brief
        assert "## L'utilisateur" in brief

    def test_idempotent_without_force(self, tmp_path: Path) -> None:
        c.scaffold(tmp_path, project_name="demo")
        assert c.scaffold(tmp_path, project_name="demo") == []
        assert len(c.scaffold(tmp_path, project_name="demo", force=True)) == 5


class TestStatusAndGate:
    def test_fresh_scaffold_all_empty(self, tmp_path: Path) -> None:
        c.scaffold(tmp_path, project_name="demo")
        report = c.status(tmp_path)
        assert report["progress"] == "0/5"
        assert all(r["state"] == "vide" for r in report["phases"])

    def test_partial_section_detection(self, tmp_path: Path) -> None:
        c.scaffold(tmp_path, project_name="demo")
        brief = c.PHASES[0]
        path = tmp_path / c.CADRAGE_DIR / brief.filename
        text = path.read_text(encoding="utf-8").replace(
            f"{c.PLACEHOLDER} le problème.", "Les setups partent à l'aveugle."
        )
        path.write_text(text, encoding="utf-8")
        report = c.phase_report(tmp_path, brief)
        assert report["state"] == "partiel"
        assert report["sections"]["Le problème"] == "rempli"

    def test_gate_requires_exigences_and_cdc(self, tmp_path: Path) -> None:
        c.scaffold(tmp_path, project_name="demo")
        errors, warnings = c.check(tmp_path)
        assert len(errors) == 2  # exigences + cahier des charges (gate)
        assert len(warnings) == 3  # brief, brainstorm, compréhension
        for phase in c.PHASES:
            if phase.gate:
                _fill_phase(tmp_path, phase)
        errors2, warnings2 = c.check(tmp_path)
        assert errors2 == []
        assert len(warnings2) == 3

    def test_full_cadrage_is_clean(self, tmp_path: Path) -> None:
        c.scaffold(tmp_path, project_name="demo")
        for phase in c.PHASES:
            _fill_phase(tmp_path, phase)
        assert c.check(tmp_path) == ([], [])
        assert c.status(tmp_path)["progress"] == "5/5"

    def test_missing_dir_reports_missing(self, tmp_path: Path) -> None:
        report = c.status(tmp_path)
        assert report["initialized"] is False


class TestNeedsCatalogEntry:
    def test_project_discovery_resolves(self) -> None:
        # Le nouveau need se résout en plan d'install valide.
        from grimoire.core.agentic_standard import (
            load_needs_catalog,
            resolve_install_plan,
        )

        catalog = load_needs_catalog()
        assert any(n["id"] == "project-discovery" for n in catalog["needs"])
        plan = resolve_install_plan(needs=["project-discovery"])
        assert plan.profile  # un profil est résolu, pas d'exception
