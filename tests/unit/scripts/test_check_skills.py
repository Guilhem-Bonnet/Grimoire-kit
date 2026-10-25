"""Tests du garde scripts/check-skills.py (R30) : format de la spec et evals.json par skill."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check-skills.py"
GOOD = "---\ndescription: 'Une description utile.'\n---\n# Corps\n"


def _run(root: Path, *paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *map(str, paths)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def _skill(root: Path, slug: str, text: str = GOOD, evals: object | None = "ok") -> Path:
    path = root / "framework" / "hosts" / "skills" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if evals is not None:
        payload = (
            {"skill_name": slug, "evals": [{"id": 1, "prompt": "p", "expected_output": "e"}]} if evals == "ok" else evals
        )
        target = root / "evals" / "skills" / slug / "evals.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_conforming_skill_with_evals_passes(tmp_path: Path) -> None:
    _skill(tmp_path, "bon-skill")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_skill_without_frontmatter_fails(tmp_path: Path) -> None:
    _skill(tmp_path, "nu", text="# Pas de frontmatter\n")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "description" in result.stdout


def test_skill_without_evals_fails(tmp_path: Path) -> None:
    _skill(tmp_path, "sans-evals", evals=None)
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "evals.json absent" in result.stdout


def test_evals_without_case_fails(tmp_path: Path) -> None:
    _skill(tmp_path, "vide", evals={"skill_name": "vide", "evals": []})
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "au moins un cas" in result.stdout


def test_evals_with_wrong_skill_name_fails(tmp_path: Path) -> None:
    _skill(tmp_path, "un", evals={"skill_name": "autre", "evals": [{"id": 1, "prompt": "p", "expected_output": "e"}]})
    assert _run(tmp_path).returncode == 1


def test_no_skill_found_is_not_a_pass(tmp_path: Path) -> None:
    assert _run(tmp_path).returncode == 1


def test_every_shipped_skill_passes() -> None:
    result = _run(REPO_ROOT)
    assert result.returncode == 0, result.stdout
