"""R32 : le pattern ``plan-then-execute`` existe, a ses checks, et ceux-ci échouent sur une entrée fautive."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.agentic_standard import (
    load_capability_map,
    resolve_install_plan,
    setup_standard_profile,
    verify_standard_profile,
)
from grimoire.core.standard_checks.registry import CHECK_DIMENSIONS


def _ids(root: Path) -> set[str]:
    return {c.id for c in verify_standard_profile(root).checks if c.id.startswith("firewall.plan")}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    return tmp_path


def _firewall(root: Path) -> Path:
    return root / "_grimoire/standard/prompt-firewall.yaml"


def test_le_pattern_est_dans_la_carte_avec_des_checks_connus() -> None:
    spec = load_capability_map()["patterns"]["plan-then-execute"]
    assert spec["checks"]
    assert all(check in CHECK_DIMENSIONS for check in spec["checks"])


def test_le_besoin_web_selectionne_le_pattern() -> None:
    install = resolve_install_plan(needs=["web"])
    assert "plan-then-execute" in install.patterns


def test_le_gabarit_livre_declare_le_bloc_et_est_conforme(project: Path) -> None:
    text = _firewall(project).read_text(encoding="utf-8")
    assert "plan_then_execute:" in text  # absent du gabarit sur main : le contrôle ne portait sur rien
    assert "freeze_plan_before_untrusted_read: true" in text
    assert _ids(project) == set()


def test_plan_non_fige_echoue(project: Path) -> None:
    path = _firewall(project)
    text = path.read_text(encoding="utf-8")
    assert "freeze_plan_before_untrusted_read: true" in text
    path.write_text(text.replace("freeze_plan_before_untrusted_read: true", "freeze_plan_before_untrusted_read: false"), encoding="utf-8")
    result = verify_standard_profile(project)
    found = [c for c in result.checks if c.id == "firewall.plan_not_frozen"]
    assert found
    assert found[0].severity == "error"


def test_action_hors_plan_acceptee_echoue(project: Path) -> None:
    path = _firewall(project)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("actions_outside_plan: ask", "actions_outside_plan: allow"), encoding="utf-8")
    assert "firewall.plan_deviation_allowed" in _ids(project)


def _without_block(root: Path) -> None:
    path = _firewall(root)
    text = path.read_text(encoding="utf-8")
    start = text.index("# Pattern plan-then-execute")
    end = text.index("untrusted_sources:")
    path.write_text(text[:start] + text[end:], encoding="utf-8")


def test_bloc_absent_alors_que_le_pattern_est_installe_avertit(project: Path) -> None:
    _without_block(project)
    result = verify_standard_profile(project)
    found = [c for c in result.checks if c.id == "firewall.plan_block_missing"]
    assert found
    assert found[0].severity == "warning"


def test_bloc_absent_sous_un_profil_qui_n_installe_pas_le_pattern_reste_muet(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="starter", project_name="Demo")
    firewall = _firewall(tmp_path)
    firewall.parent.mkdir(parents=True, exist_ok=True)
    firewall.write_text(
        "isolate_external_content: true\ninstruction_override_blocked: true\n"
        "untrusted_sources:\n  - {id: web, quarantine: true}\n",
        encoding="utf-8",
    )
    assert _ids(tmp_path) == set()


def test_le_pattern_est_installe_des_controlled() -> None:
    from grimoire.core.agentic_standard import load_profile_map

    owners = [
        p["id"] for p in load_profile_map()["profiles"] if "plan-then-execute" in (p.get("mapped_capabilities") or ())
    ]
    assert owners == ["controlled"]
