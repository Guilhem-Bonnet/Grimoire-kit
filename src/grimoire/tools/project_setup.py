"""Setup de projet piloté par le wizard web (brique B2).

Extrait de :mod:`grimoire.tools.forge_server` et **modernisé** : le plan de
setup compile désormais vers ``grimoire up`` (le parcours one-command), plus
jamais vers l'installeur shell legacy. Le wizard choisit aussi le **backend
mémoire** du projet (brique B1 — lien projet ↔ BDD), validé contre le
catalogue des backends connus.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from grimoire.cli.cmd_init import KNOWN_BACKENDS
from grimoire.tools.ext_manager import ExtensionError, InstallResult


def archetypes_catalogue(kit_root: Path) -> list[dict[str, Any]]:
    """Les archétypes proposés par le wizard (DNA du kit)."""
    yaml = YAML(typ="safe")
    result: list[dict[str, Any]] = []
    base = kit_root / "archetypes"
    if not base.is_dir():
        return result
    for dna in sorted(base.glob("*/archetype.dna.yaml")):
        data = yaml.load(dna.read_text(encoding="utf-8")) or {}
        result.append(
            {
                "id": data.get("id", dna.parent.name),
                "name": data.get("name", dna.parent.name),
                "description": data.get("description", ""),
                "tags": data.get("tags", []),
            }
        )
    return result


def build_setup_plan(
    project_root: Path,
    payload: dict[str, Any],
    *,
    install: Callable[[str], InstallResult],
) -> dict[str, Any]:
    """Prépare un projet depuis le wizard web et écrit le plan.

    Le plan compile vers ``grimoire up`` (moderne) : nom, user, archétype,
    **backend mémoire** (validé contre :data:`KNOWN_BACKENDS`) et besoins
    éventuels. Les extensions demandées sont installées immédiatement.
    """
    archetype = payload.get("archetype", "minimal")
    backend = str(payload.get("backend") or "auto")
    if backend not in KNOWN_BACKENDS:
        msg = (
            f"backend mémoire inconnu : {backend} "
            f"(attendu : {', '.join(sorted(KNOWN_BACKENDS))})"
        )
        raise ValueError(msg)
    needs = [str(n) for n in payload.get("needs", []) if n]
    installed, errors = [], []
    for ext_id in payload.get("extensions", []):
        try:
            result = install(ext_id)
            installed.append(f"{result.extension_id} v{result.version}")
        except ExtensionError as exc:
            errors.append(f"{ext_id} : {exc}")

    up_command = (
        f'grimoire up . --name "{payload.get("name", "")}" '
        f'--user "{payload.get("user", "")}" '
        f"--archetype {archetype} --backend {backend}"
    )
    if needs:
        up_command += "".join(f" --needs {n}" for n in needs)

    plan = {
        "plannedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "name": payload.get("name", ""),
        "user": payload.get("user", ""),
        "archetype": archetype,
        "backend": backend,
        "needs": needs,
        "extensionsInstalled": installed,
        "extensionErrors": errors,
        "initCommand": up_command,
    }
    plan_path = project_root / "_grimoire" / "setup-plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return plan
