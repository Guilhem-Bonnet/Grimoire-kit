"""Tests for the MkDocs signals hook."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_HOOK_PATH = Path(__file__).resolve().parent.parent / "mkdocs_hooks" / "signals.py"
_SPEC = importlib.util.spec_from_file_location("mkdocs_signals_hook", _HOOK_PATH)
signals = importlib.util.module_from_spec(_SPEC)
sys.modules["mkdocs_signals_hook"] = signals
assert _SPEC.loader is not None
_SPEC.loader.exec_module(signals)


def _make_page(*, src_uri: str, url: str) -> SimpleNamespace:
    return SimpleNamespace(file=SimpleNamespace(src_uri=src_uri, url=url))


class TestSignalsHookHelpers(unittest.TestCase):
    def test_relative_source_path_same_level(self) -> None:
        self.assertEqual(signals._relative_source_path("index.md", "signaux.md"), "signaux.md")

    def test_relative_source_path_nested(self) -> None:
        self.assertEqual(signals._relative_source_path("guides/page.md", "signaux.md"), "../signaux.md")

    def test_relative_route_nested_target(self) -> None:
        self.assertEqual(
            signals._relative_route("presentation-decouverte/", "workflow-design-patterns/"),
            "../workflow-design-patterns/",
        )


class TestSignalsHookRendering(unittest.TestCase):
    def setUp(self) -> None:
        self.original_signals = signals._SIGNALS
        self.original_files = signals._FILES_BY_SRC_URI
        signals._SIGNALS = (
            signals.SignalEntry(
                title="Guardrails runtime agentiques",
                path="grimoire-game-runtime-guardrails.md",
                date=signals.date(2026, 4, 8),
                kicker="cadre d'exécution",
                summary="Contrats et garde-fous pour le runtime agentique.",
                featured=True,
                proof=True,
            ),
            signals.SignalEntry(
                title="Observatory API",
                path="observatory-api.md",
                date=signals.date(2026, 4, 7),
                kicker="observabilité locale",
                summary="Exposer les vues et statuts de l'observatoire.",
                featured=False,
                proof=False,
            ),
        )
        signals._FILES_BY_SRC_URI = {
            "grimoire-game-runtime-guardrails.md": SimpleNamespace(
                src_uri="grimoire-game-runtime-guardrails.md",
                url="grimoire-game-runtime-guardrails/",
            ),
            "observatory-api.md": SimpleNamespace(
                src_uri="observatory-api.md",
                url="observatory-api/",
            ),
        }

    def tearDown(self) -> None:
        signals._SIGNALS = self.original_signals
        signals._FILES_BY_SRC_URI = self.original_files

    def test_render_home_signals_includes_archive_link(self) -> None:
        page = _make_page(src_uri="index.md", url="")

        rendered = signals._render_home_signals(page)

        self.assertIn("[Guardrails runtime agentiques](grimoire-game-runtime-guardrails.md)", rendered)
        self.assertIn("[Voir tous les signaux](signaux.md)", rendered)

    def test_render_presentation_strip_uses_relative_routes(self) -> None:
        page = _make_page(src_uri="presentation-decouverte.md", url="presentation-decouverte/")

        rendered = signals._render_presentation_strip(page)

        self.assertIn('class="gp-news-strip__card gp-news-strip__card--proof"', rendered)
        self.assertIn('href="../grimoire-game-runtime-guardrails/"', rendered)
        self.assertIn("Guardrails runtime agentiques", rendered)


if __name__ == "__main__":
    unittest.main()
