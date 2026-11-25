"""MOA/MOE (#1342, lot M1) : la section governance.roles, sa validation, le contrôle doctor et l'init."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from grimoire.cli.doctor_moa_moe import moa_moe_verdict
from grimoire.core.config import DEFAULT_MOE, GovernanceConfig, GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError
from grimoire.core.validator import validate_config


def _data(roles: Any = ..., **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"project": {"name": "demo"}, **extra}
    if roles is not ...:
        data["governance"] = {"roles": roles}
    return data


def _write(root: Path, data: dict[str, Any]) -> None:
    (root / "project-context.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_moa_moe_absent_section_is_not_declared() -> None:
    gov = GrimoireConfig.from_dict(_data()).governance
    assert gov == GovernanceConfig()
    assert not gov.declared


def test_moa_moe_declared_with_default_moe() -> None:
    gov = GrimoireConfig.from_dict(_data({"moa": "Ada Lovelace"})).governance
    assert (gov.moa, gov.moe, gov.declared) == ("Ada Lovelace", DEFAULT_MOE, True)


def test_moa_moe_explicit_moe() -> None:
    assert GrimoireConfig.from_dict(_data({"moa": "Ada", "moe": "équipe plateforme"})).governance.moe == "équipe plateforme"


@pytest.mark.parametrize(
    ("roles", "fragment"),
    [
        ({}, "governance.roles.moa' is required"),
        ({"moe": "x"}, "governance.roles.moa' is required"),
        ({"moa": ""}, "governance.roles.moa' must not be empty"),
        ({"moa": "   "}, "governance.roles.moa' must not be empty"),
        ({"moa": 42}, "governance.roles.moa' must be a string"),
        ({"moa": ["Ada"]}, "governance.roles.moa' must be a string"),
        ({"moa": "Ada", "moe": ""}, "governance.roles.moe' must not be empty"),
        ({"moa": "Ada", "moe": 3}, "governance.roles.moe' must be a string"),
        ("Ada", "governance.roles' must be a mapping"),
    ],
)
def test_moa_moe_invalid_roles_rejected_with_clear_message(roles: Any, fragment: str) -> None:
    with pytest.raises(GrimoireConfigError, match=fragment):
        GrimoireConfig.from_dict(_data(roles))
    errors = validate_config(_data(roles))
    assert errors, "le validateur doit signaler la même erreur"


def test_moa_moe_governance_not_a_mapping() -> None:
    with pytest.raises(GrimoireConfigError, match="'governance' must be a mapping"):
        GrimoireConfig.from_dict({"project": {"name": "demo"}, "governance": "x"})


def test_moa_moe_validator_accepts_valid_and_absent() -> None:
    assert validate_config(_data({"moa": "Ada", "moe": "orchestrateur"})) == []
    assert validate_config(_data()) == []


def test_moa_moe_doctor_warns_when_absent(tmp_path: Path) -> None:
    _write(tmp_path, _data())
    verdict = moa_moe_verdict(tmp_path)
    assert verdict is not None
    assert (verdict["name"], verdict["level"], verdict["passed"]) == ("moa_moe", "warn", True)


def test_moa_moe_doctor_ok_names_the_moa(tmp_path: Path) -> None:
    _write(tmp_path, _data({"moa": "Ada Lovelace"}))
    verdict = moa_moe_verdict(tmp_path)
    assert verdict is not None
    assert verdict["level"] == "ok"
    assert "Ada Lovelace" in verdict["detail"]


def test_moa_moe_doctor_silent_without_config_loud_on_bad_governance(tmp_path: Path) -> None:
    assert moa_moe_verdict(tmp_path) is None
    _write(tmp_path, _data({"moa": ""}))
    verdict = moa_moe_verdict(tmp_path)
    assert verdict is not None and verdict["level"] == "warn"
    assert "mal formée" in verdict["detail"]


def test_moa_moe_scaffold_writes_the_section() -> None:
    from grimoire.core.scaffold import _governance_block

    assert _governance_block("") == ""
    parsed = yaml.safe_load("a: 1\n" + _governance_block('Ada "Bug" Lovelace'))
    assert parsed["governance"]["roles"] == {"moa": 'Ada "Bug" Lovelace', "moe": "orchestrateur"}
