"""Tests for core/onboarding_panel.py — the dynamic 'Next Steps' panel body.

Onboarding audit 2026-09-18, constat #3: the panel printed by `grimoire init`/
`up` was 100% identical across 6 runs (3 stacks x plain/express), regardless
of archetype, backend or memory profile. These tests build the panel as a
pure data structure (no Rich rendering involved) so the "it must differ"
acceptance bar is checked directly on content, not on printed text.

Decision 2026-09-18 (Guilhem, product owner): `resolve()` never installs
`minimal` automatically — an ambiguous stack still gets a named, applied,
specialized archetype (`platform-engineering` for a naked backend language,
`web-app` for a naked Node/JS project). The panel headline/actions reflect
that: they name the applied archetype and, when it was a best guess, how to
change it — never a bare "minimal" default.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from grimoire.core.archetype_resolver import ArchetypeResolver
from grimoire.core.onboarding_panel import build_next_steps
from grimoire.core.scanner import ScanResult, StackDetection


def _scan(*stacks: str, root: Path) -> ScanResult:
    return ScanResult(
        stacks=tuple(StackDetection(name=s, confidence=0.9, evidence=(f"{s}-marker",)) for s in stacks),
        project_type="generic",
        root=root,
    )


def _resolve(scan: ScanResult):
    return ArchetypeResolver().resolve(scan)


class TestBuildNextSteps:
    def _panel(self, tmp_path: Path, *stacks: str, backend: str = "lexical", no_cockpit: bool = False):
        scan = _scan(*stacks, root=tmp_path)
        resolved = _resolve(scan)
        with patch("grimoire.core.onboarding_panel.cockpit_registered", return_value=False):
            return build_next_steps(
                tmp_path,
                resolved=resolved,
                backend=backend,
                no_cockpit=no_cockpit,
            )

    def test_never_more_than_three_actions(self, tmp_path: Path) -> None:
        panel = self._panel(tmp_path, "python")
        assert len(panel.actions) <= 3

    def test_headline_names_the_applied_archetype_never_minimal(self, tmp_path: Path) -> None:
        panel = self._panel(tmp_path, "python")
        assert "platform-engineering" in panel.headline
        assert "minimal" not in panel.headline.lower()

    def test_python_naked_and_node_naked_panels_differ(self, tmp_path: Path) -> None:
        py_dir = tmp_path / "py"
        node_dir = tmp_path / "node"
        py_dir.mkdir()
        node_dir.mkdir()
        py_panel = self._panel(py_dir, "python")
        node_panel = self._panel(node_dir, "javascript")
        assert py_panel.headline != node_panel.headline
        assert "platform-engineering" in py_panel.headline
        assert "web-app" in node_panel.headline

    def test_empty_repo_panel_differs_from_naked_python(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        py_dir = tmp_path / "py2"
        empty_dir.mkdir()
        py_dir.mkdir()
        empty_panel = self._panel(empty_dir)
        py_panel = self._panel(py_dir, "python")
        assert empty_panel.headline != py_panel.headline
        assert "minimal" not in empty_panel.headline.lower()

    def test_specialized_archetype_has_no_best_guess_action(self, tmp_path: Path) -> None:
        """A confident rule match (react → web-app) is not a guess — no
        'change it' action is needed."""
        panel = self._panel(tmp_path, "react")
        assert not any("discovery" in a.lower() or "--interactive" in a for a in panel.actions)

    def test_best_guess_action_names_override_command(self, tmp_path: Path) -> None:
        panel = self._panel(tmp_path, "python")
        assert any("grimoire up -a" in a or "--interactive" in a for a in panel.actions)

    def test_unexploited_line_present_on_fresh_project(self, tmp_path: Path) -> None:
        panel = self._panel(tmp_path, "python")
        assert panel.unexploited_line is not None
        assert "grimoire --all --help" in panel.unexploited_line

    def test_unexploited_line_absent_when_nothing_left(self, tmp_path: Path) -> None:
        scan = _scan("react", root=tmp_path)
        resolved = _resolve(scan)
        manifest = tmp_path / "_grimoire" / "standard" / "standard-profile.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("profile: governed\n", encoding="utf-8")
        with patch("grimoire.core.onboarding_panel.cockpit_registered", return_value=True):
            panel = build_next_steps(
                tmp_path,
                resolved=resolved,
                backend="qdrant-local",
                no_cockpit=False,
            )
        assert panel.unexploited_line is None
