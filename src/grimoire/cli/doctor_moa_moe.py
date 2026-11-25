"""Le contrôle ``moa_moe`` de `grimoire doctor` (#1342, lot M1) : les rôles de gouvernance sont-ils déclarés ?

Hors de ``cli/app.py`` : ce fichier est sous un cliquet de taille qui ne le laisse que rétrécir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError


def _governance_problem(config_path: Path) -> str:
    """L'erreur de la section ``governance`` seule, quand c'est elle qui rend la configuration illisible ; sinon ``""``."""
    import yaml

    from grimoire.core.config import GovernanceConfig

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return ""
    if not isinstance(data, dict) or "governance" not in data:
        return ""
    try:
        GovernanceConfig.from_dict(data.get("governance"))
    except GrimoireConfigError as exc:
        return str(exc)
    return ""


def moa_moe_verdict(project_root: Path) -> dict[str, Any] | None:
    """Verdict du contrôle, ou ``None`` quand la configuration est absente ou illisible pour une autre raison.

    Jamais FAIL : une section absente est un avertissement, un projet existant ne casse pas. Une section
    ``governance`` mal formée reste visible ici (#1342, relecture de M1) au lieu de disparaître derrière l'erreur
    générale de configuration : c'est elle qui fixe le gate d'exigence (lot M3).
    """
    config_path = project_root / "project-context.yaml"
    if not config_path.is_file():
        return None
    try:
        gov = GrimoireConfig.from_yaml(config_path).governance
    except GrimoireConfigError:
        problem = _governance_problem(config_path)
        if not problem:
            return None
        return {
            "name": "moa_moe", "passed": True, "level": "warn",
            "detail": f"section governance mal formée : {problem} — la MOA n'est pas reconnue, aucune exigence "
            "ne peut être validée",
        }
    if not gov.declared:
        return {
            "name": "moa_moe", "passed": True, "level": "warn",
            "detail": (
                "governance.roles absent de project-context.yaml : nommez la MOA (l'humain qui porte le "
                "besoin et prononce la recette) — governance: {roles: {moa: \"Prénom Nom\"}}"
            ),
        }
    mode = gov.requirement_mode
    return {
        "name": "moa_moe", "passed": True, "level": "ok", "moa": gov.moa, "moe": gov.moe,
        "requirement_gate": mode,
        "detail": f"MOA : {gov.moa} — MOE : {gov.moe} — gate d'exigence : {mode}",
    }


def apply_moa_moe_check(results: list[dict[str, Any]], project_root: Path, *, fmt: str, console: Console) -> None:
    verdict = moa_moe_verdict(project_root)
    if verdict is None:
        return
    results.append(verdict)
    if fmt != "json":
        tag = "[green]OK[/green]" if verdict["level"] == "ok" else "[yellow]WARN[/yellow]"
        console.print(f"  {tag}  {verdict['detail']}")
