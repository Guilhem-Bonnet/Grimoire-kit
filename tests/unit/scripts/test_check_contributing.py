"""``scripts/check-contributing.py`` — un tenet sans test existant, un gabarit de PR périmé : refusés."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from grimoire.repo_gates import GATE_IDS

REPO = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_contributing", REPO / "scripts" / "check-contributing.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load()


def _tenets(n: int, ref: str = "`tests/unit/test_a.py`") -> str:
    body = "\n".join(f"### Tenet {i} — principe {i}\n\nTenu par : {ref}.\n" for i in range(1, n + 1))
    return f"# C\n\n## Tenets\n\n{body}\n## Suite\n"


def _kit(tmp_path: Path, contributing: str, template: str | None = None, makefile: str = "gates:\n\techo\n") -> Path:
    (tmp_path / "tests/unit").mkdir(parents=True)
    (tmp_path / "tests/unit/test_a.py").write_text("def test_a() -> None:\n    pass\n\nclass TestB:\n    pass\n", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text(contributing, encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/check-x.py").write_text("", encoding="utf-8")
    (tmp_path / "Makefile").write_text(makefile, encoding="utf-8")
    gates = ", ".join(f"`{g}`" for g in GATE_IDS)
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/PULL_REQUEST_TEMPLATE.md").write_text(
        template if template is not None else f"- `grimoire gates repo` : {gates}\n", encoding="utf-8"
    )
    return tmp_path


def test_sound_tenets_and_template_pass(tmp_path: Path) -> None:
    root = _kit(tmp_path, _tenets(3))
    assert MOD.tenet_problems(root) == [] and MOD.template_problems(root) == []


def test_missing_section_is_refused(tmp_path: Path) -> None:
    assert "pas de section" in MOD.tenet_problems(_kit(tmp_path, "# C\n"))[0]


def test_too_few_tenets_are_refused(tmp_path: Path) -> None:
    assert "il en faut au moins" in MOD.tenet_problems(_kit(tmp_path, _tenets(2)))[0]


def test_a_tenet_without_a_test_is_refused(tmp_path: Path) -> None:
    root = _kit(tmp_path, _tenets(3, ref="la bonne volonté"))
    assert len(MOD.tenet_problems(root)) == 3


def test_a_tenet_citing_a_missing_test_is_refused(tmp_path: Path) -> None:
    root = _kit(tmp_path, _tenets(3, ref="`tests/unit/test_disparu.py`"))
    assert all("n'existe pas" in p for p in MOD.tenet_problems(root))


def test_a_named_test_must_exist_in_the_file(tmp_path: Path) -> None:
    ok = _kit(tmp_path / "ok", _tenets(3, ref="`tests/unit/test_a.py::TestB`"))
    ko = _kit(tmp_path / "ko", _tenets(3, ref="`tests/unit/test_a.py::test_fantome`"))
    assert MOD.tenet_problems(ok) == []
    assert all("test_fantome" in p for p in MOD.tenet_problems(ko))


def test_template_citing_a_vanished_script_target_or_gate_is_refused(tmp_path: Path) -> None:
    template = (
        "- `python scripts/disparu.py`\n- `make fantome`\n- `grimoire gates repo --only fantome`\n- `python scripts/check-x.py`\n"
    )
    found = MOD.template_problems(_kit(tmp_path, _tenets(3), template=template))
    joined = "\n".join(found)
    assert "scripts/disparu.py" in joined and "`fantome` n'est pas dans le Makefile" in joined
    assert "la garde `fantome` n'existe pas" in joined
    assert "scripts/check-x.py" not in joined


def test_template_must_name_every_gate(tmp_path: Path) -> None:
    found = MOD.template_problems(_kit(tmp_path, _tenets(3), template="- `grimoire gates repo`\n"))
    assert len(found) == len(GATE_IDS)


def test_the_real_contributing_guide_and_pr_template_hold() -> None:
    assert MOD.tenet_problems() == []
    assert MOD.template_problems() == []
