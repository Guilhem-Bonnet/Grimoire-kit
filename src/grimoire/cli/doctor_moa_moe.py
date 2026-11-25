"""Le contrôle ``moa_moe`` de `grimoire doctor` (#1342, lot M1) : les rôles de gouvernance sont-ils déclarés ?

Hors de ``cli/app.py`` : ce fichier est sous un cliquet de taille qui ne le laisse que rétrécir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError


def moa_moe_verdict(project_root: Path) -> dict[str, Any] | None:
    """Verdict du contrôle, ou ``None`` quand la configuration est absente ou illisible (d'autres contrôles le disent).

    Jamais FAIL : une section absente est un avertissement, un projet existant ne casse pas.
    """
    config_path = project_root / "project-context.yaml"
    if not config_path.is_file():
        return None
    try:
        gov = GrimoireConfig.from_yaml(config_path).governance
    except GrimoireConfigError as exc:
        # Une section governance mal formée ne disparaît pas en silence ; toute autre erreur reste aux autres contrôles.
        if "governance" not in str(exc):
            return None
        return {
            "name": "moa_moe", "passed": True, "level": "warn",
            "detail": f"governance mal formée dans project-context.yaml : {exc}",
        }
    if not gov.declared:
        return {
            "name": "moa_moe", "passed": True, "level": "warn",
            "detail": (
                "governance.roles absent de project-context.yaml : nommez la MOA (l'humain qui porte le "
                "besoin et prononce la recette) — governance: {roles: {moa: \"Prénom Nom\"}}"
            ),
        }
    return {
        "name": "moa_moe", "passed": True, "level": "ok", "moa": gov.moa, "moe": gov.moe,
        "detail": f"MOA : {gov.moa} — MOE : {gov.moe}",
    }


def apply_moa_moe_check(results: list[dict[str, Any]], project_root: Path, *, fmt: str, console: Console) -> None:
    verdict = moa_moe_verdict(project_root)
    if verdict is None:
        return
    results.append(verdict)
    if fmt != "json":
        tag = "[green]OK[/green]" if verdict["level"] == "ok" else "[yellow]WARN[/yellow]"
        console.print(f"  {tag}  {verdict['detail']}")
