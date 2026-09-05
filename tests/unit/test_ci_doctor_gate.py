"""Tests for scripts/ci-doctor-gate.py — the CI verdict on `grimoire-init.sh doctor`.

The previous gate was `doctor | tee out || true`, then three `grep -c` counts
subtracted from one another. Any line mentioning Qdrant or a CI-expected path
was subtracted from the error count whether or not it was an error, so the
result could go negative and the step could not fail; and a doctor that never
ran produced an empty file, zero errors, and a green step.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci-doctor-gate.py"

BANNER = "║  Grimoire Doctor — Diagnostic de l'installation\n"


def _load_module():
    name = "ci_doctor_gate_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _verdict(text: str) -> tuple[int, list[str]]:
    return _load_module().verdict(text)


def test_a_doctor_that_never_ran_is_not_a_green_step() -> None:
    code, problems = _verdict("")
    assert code == 1
    assert any("aucune sortie" in p for p in problems)


def test_output_without_the_doctor_banner_is_refused() -> None:
    code, problems = _verdict("bash: grimoire-init.sh: No such file or directory\n")
    assert code == 1
    assert any("bannière" in p for p in problems)


def test_expected_ci_gaps_do_not_fail_the_gate() -> None:
    text = BANNER + (
        "  ✗  _grimoire/_config/ — manquant\n"
        "  ✗  Qdrant injoignable sur localhost:6333\n"
        "  ⚠  project-context.yaml manquant — lancez : grimoire-init.sh --name ...\n"
        "⚠  3 avertissement(s) — fonctionnel mais vérifiez les warnings ci-dessus\n"
    )
    assert _verdict(text) == (0, [])


def test_an_unexpected_error_fails_and_is_named() -> None:
    text = BANNER + "  ✗  python3 — MANQUANT\n  ✗  Qdrant injoignable\n"
    code, problems = _verdict(text)
    assert code == 1
    assert any("python3 — MANQUANT" in p for p in problems)
    assert not any("Qdrant" in p for p in problems)


def test_a_qdrant_mention_does_not_launder_an_unrelated_error() -> None:
    """The old arithmetic subtracted every Qdrant line from the error count."""
    text = BANNER + (
        "  ✓  Qdrant reachable\n  ✓  qdrant collection ok\n  ✗  archetypes/meta/archetype.dna.yaml — YAML invalide\n"
    )
    code, problems = _verdict(text)
    assert code == 1
    assert any("YAML invalide" in p for p in problems)


def test_main_reads_the_file_and_returns_the_verdict(tmp_path: Path, capsys) -> None:
    out = tmp_path / "doctor-output.txt"
    out.write_text(BANNER + "  ✗  git — MANQUANT\n", encoding="utf-8")
    assert _load_module().main([str(out)]) == 1
    assert "git — MANQUANT" in capsys.readouterr().out
    out.write_text(BANNER + "✅  Tout est OK\n", encoding="utf-8")
    assert _load_module().main([str(out)]) == 0
