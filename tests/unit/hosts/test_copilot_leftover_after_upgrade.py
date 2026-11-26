"""Montée de version : les fichiers de hook Copilot hérités d'une ancienne installation (rejeu 3.61.1 -> 3.122.0).

Un projet créé en 3.61.1 déclare ``hosts.enabled: ["claude"]`` mais y avait reçu des hooks Copilot au format
d'alors (``timeout: 10``, sans ``version``). ``grimoire up`` ne réécrit plus les fichiers d'un hôte déclaré hors
liste ; ``doctor`` les comptait pourtant comme une panne (``passed=False``) avec la consigne « lancez
``grimoire up`` », sans effet : ``upgrade-flow`` s'arrêtait à l'étape ``up`` pour toujours. Un hôte activé, lui,
voit le fichier géré régénéré à la valeur courante.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.cli.doctor_copilot import check_copilot_hook_format
from grimoire.cli.doctor_hooks import check_copilot_guard_timeout, copilot_declared_off
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import apply_plan, emitter_for
from grimoire.hosts.emitters.copilot import GUARD_TIMEOUT

OLD_GUARD = {
    "hooks": {
        "PreToolUse": [{"type": "command", "command": "grimoire-hook --host copilot --event PreToolUse", "timeout": 10}]
    }
}


def _project(root: Path, hosts: str | None) -> Path:
    lines = ['project:', '  name: "rejeu"', "agents:", '  archetype: "minimal"']
    if hosts is not None:
        lines += ["hosts:", f"  enabled: {hosts}"]
    (root / "project-context.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    hooks = root / ".github" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "grimoire-pre-tool-use.json").write_text(json.dumps(OLD_GUARD, indent=2) + "\n", encoding="utf-8")
    return root


def test_hote_declare_hors_liste_un_garde_herite_n_est_pas_une_panne(tmp_path: Path) -> None:
    project = _project(tmp_path, '["claude"]')
    assert copilot_declared_off(project)
    (check,) = check_copilot_guard_timeout(project)
    assert check.name == "hook_timeout_copilot"
    assert check.passed and check.level == "info"  # ne fait plus échouer `upgrade-flow`
    assert "not in hosts.enabled" in check.detail and "10 s" in check.detail
    # La consigne agit : élaguer, ou activer l'hôte puis `up` (et non un `up` sans effet).
    assert "--prune-disabled" in check.remedy and "hosts.enabled" in check.remedy
    (fmt,) = check_copilot_hook_format(project)
    assert fmt.passed and fmt.level == "info" and "older format" in fmt.detail and "--prune-disabled" in fmt.remedy


def test_hote_active_le_fichier_gere_est_regenere_puis_le_doctor_est_vert(tmp_path: Path) -> None:
    project = _project(tmp_path, '["claude", "copilot"]')
    assert not copilot_declared_off(project)
    (before,) = check_copilot_guard_timeout(project)
    assert not before.passed and before.level == "warn" and "grimoire up" in before.remedy
    plan = emitter_for(HostId.GITHUB_COPILOT).plan(build_surface(project, write_cache=False), project)
    result = apply_plan(plan, project)
    assert ".github/hooks/grimoire-pre-tool-use.json" in result.written
    entry = json.loads((project / ".github/hooks/grimoire-pre-tool-use.json").read_text(encoding="utf-8"))["hooks"][
        "PreToolUse"
    ][0]
    assert entry["timeoutSec"] == GUARD_TIMEOUT
    (after,) = check_copilot_guard_timeout(project)
    assert after.passed and after.level == "info" and "fails open" in after.detail


def test_sans_declaration_la_detection_decide_et_l_avertissement_reste(tmp_path: Path) -> None:
    project = _project(tmp_path, None)
    assert not copilot_declared_off(project)
    (check,) = check_copilot_guard_timeout(project)
    assert not check.passed and check.level == "warn"


def test_config_illisible_ne_masque_rien(tmp_path: Path) -> None:
    project = _project(tmp_path, '["claude"]')
    (project / "project-context.yaml").write_text("hosts: [", encoding="utf-8")
    assert not copilot_declared_off(project)
    (check,) = check_copilot_guard_timeout(project)
    assert not check.passed


@pytest.mark.parametrize("declared", ['["copilot"]', '["claude", "copilot", "gemini"]'])
def test_copilot_dans_la_liste_n_est_jamais_hors_liste(tmp_path: Path, declared: str) -> None:
    assert not copilot_declared_off(_project(tmp_path, declared))
