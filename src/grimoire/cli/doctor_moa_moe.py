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
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    from grimoire.core.config import GovernanceConfig

    try:
        data = YAML(typ="safe").load(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, YAMLError):
        return ""
    if not isinstance(data, dict) or "governance" not in data:
        return ""
    try:
        GovernanceConfig.from_dict(data.get("governance"))
    except GrimoireConfigError as exc:
        return str(exc)
    return ""


def _disarmed(project_root: Path, mode: str) -> str:
    """#1342 (M3) : des validations scellées existent pour ce projet alors que le gate n'est plus bloquant."""
    if mode == "block":
        return ""
    from grimoire.missions.requirement_gate import sealed_validations

    count = sealed_validations(project_root)  # journal illisible : 0, sans lever
    if not count:
        return ""
    return (
        f" ; {count} validation(s) d'exigence scellée(s) pour ce projet alors que le gate est en {mode} : "
        "repassé de block à warn ou section governance retirée ? Vérifiez que c'est voulu"
    )


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
    mode = gov.requirement_mode
    disarmed = _disarmed(project_root, mode)
    if not gov.declared:
        return {
            "name": "moa_moe", "passed": True, "level": "warn",
            "detail": (
                "governance.roles absent de project-context.yaml : nommez la MOA (l'humain qui porte le "
                "besoin et prononce la recette) — governance: {roles: {moa: \"Prénom Nom\"}}"
            ) + disarmed,
        }
    return {
        "name": "moa_moe", "passed": True, "level": "warn" if disarmed else "ok", "moa": gov.moa, "moe": gov.moe,
        "requirement_gate": mode,
        "detail": f"MOA : {gov.moa} — MOE : {gov.moe} — gate d'exigence : {mode}" + disarmed,
    }


#: Les états regroupés pour la ligne de synthèse : proposées, validées (en cours de réalisation), livrées en attente de
#: recette, recettées ; le reste (non conforme, suite à créer) est à reprendre par la MOE.
_PROPOSED = ("proposee",)
_VALIDATED = ("validee", "recettee-en-cours")
_DELIVERED = ("livree",)
_ACCEPTED = ("recettee", "recettee-avec-reserve")


def _chantiers(project_root: Path) -> list[str | None]:
    """Le chantier principal, puis chaque sous-dossier de ``_grimoire/cadrage/`` qui porte un fichier d'exigences."""
    from grimoire.core.cadrage import CADRAGE_DIR

    base = project_root / CADRAGE_DIR
    found: list[str | None] = [None]
    try:
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and (entry / "04-exigences.md").is_file():
                found.append(entry.name)
    except OSError:
        pass
    return found


def requirement_journey(project_root: Path) -> dict[str, int] | None:
    """Les exigences du projet par étape du parcours, ou ``None`` si l'état est illisible.

    Appelée seulement quand une MOA est déclarée : un projet sans gouvernance ne paie aucune lecture.
    """
    from grimoire.core.exceptions import GrimoireError
    from grimoire.missions.moa_acceptance import requirement_views
    from grimoire.missions.requirement_gate import origin_root
    from grimoire.missions.service import TaskService

    counts = {"proposed": 0, "validated": 0, "awaiting_recette": 0, "accepted": 0, "to_rework": 0}
    try:
        service = TaskService(origin_root(project_root))
        for chantier in _chantiers(project_root):
            for view in requirement_views(project_root, chantier, service=service):
                if view.state in _PROPOSED:
                    counts["proposed"] += 1
                elif view.state in _VALIDATED:
                    counts["validated"] += 1
                elif view.state in _DELIVERED:
                    counts["awaiting_recette"] += 1
                elif view.state in _ACCEPTED:
                    counts["accepted"] += 1
                else:
                    counts["to_rework"] += 1
    except (ValueError, OSError, GrimoireError):
        return None
    return counts


def journey_verdict(project_root: Path) -> dict[str, Any] | None:
    """La ligne de synthèse du parcours MOA ; ``None`` sans exigence identifiée ou état illisible."""
    counts = requirement_journey(project_root)
    if counts is None or not sum(counts.values()):
        return None
    detail = (
        f"Exigences : {counts['proposed']} proposée(s), {counts['validated']} validée(s), "
        f"{counts['awaiting_recette']} en attente de recette, {counts['accepted']} recettée(s)"
    )
    if counts["to_rework"]:
        detail += f", {counts['to_rework']} à reprendre (non conforme ou suite à créer)"
    return {"name": "moa_journey", "passed": True, "level": "ok", "detail": detail, **counts}


def apply_moa_moe_check(results: list[dict[str, Any]], project_root: Path, *, fmt: str, console: Console) -> None:
    verdict = moa_moe_verdict(project_root)
    if verdict is None:
        return
    results.append(verdict)
    if fmt != "json":
        tag = "[green]OK[/green]" if verdict["level"] == "ok" else "[yellow]WARN[/yellow]"
        console.print(f"  {tag}  {verdict['detail']}")
    if "moa" not in verdict:  # sans MOA déclarée, le parcours n'a pas de synthèse : aucune lecture de plus
        return
    journey = journey_verdict(project_root)
    if journey is not None:
        results.append(journey)
        if fmt != "json":
            console.print(f"  [green]OK[/green]  {journey['detail']}")
