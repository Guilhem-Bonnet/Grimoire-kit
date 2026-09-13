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
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import typer

from ruamel.yaml import YAML

from grimoire.cli.cmd_init import KNOWN_BACKENDS
from grimoire.tools.ext_manager import ExtensionError, InstallResult

#: Emplacement du journal d'exécution du wizard (issue #171) — un seul
#: dernier run, lu par polling depuis l'UI (``GET /api/setup/run``).
SETUP_RUN_RELPATH = Path("_grimoire") / "setup-run.json"


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
    archetype = str(payload.get("archetype") or "minimal")
    backend = str(payload.get("backend") or "auto")
    if backend not in KNOWN_BACKENDS:
        msg = (
            f"backend mémoire inconnu : {backend} "
            f"(attendu : {', '.join(sorted(KNOWN_BACKENDS))})"
        )
        raise ValueError(msg)
    # Défenses sur les payloads clients : `null` explicite (get renvoie None,
    # pas le défaut) et types non-listes (une string s'itèrerait caractère par
    # caractère — un `"demo"` installerait d/e/m/o).
    name = str(payload.get("name") or "")
    user = str(payload.get("user") or "")
    needs_raw = payload.get("needs")
    needs = [str(n) for n in needs_raw if n] if isinstance(needs_raw, list) else []
    ext_raw = payload.get("extensions")
    ext_ids = [e for e in ext_raw if e] if isinstance(ext_raw, list) else []
    installed, errors = [], []
    for ext_id in ext_ids:
        try:
            result = install(str(ext_id))
            installed.append(f"{result.extension_id} v{result.version}")
        except ExtensionError as exc:
            errors.append(f"{ext_id} : {exc}")

    up_command = (
        f'grimoire up . --name "{name}" '
        f'--user "{user}" '
        f"--archetype {archetype} --backend {backend}"
    )
    if needs:
        up_command += "".join(f" --needs {n}" for n in needs)

    plan = {
        "plannedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "name": name,
        "user": user,
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


def needs_catalogue(project_root: Path) -> dict[str, Any]:
    """Needs du catalogue + suggestions pour CE projet (pont B2/B3, issue #171).

    Même mécanique que ``grimoire up``'s ``_print_needs_suggestions`` : un
    scan déterministe du projet, des suggestions validées contre le
    needs-catalog — jamais un id inventé — pour que le wizard web précoche
    exactement ce que le CLI recommanderait.
    """
    from grimoire.core.agentic_standard import load_needs_catalog
    from grimoire.core.needs_suggest import suggest_needs
    from grimoire.core.scanner import StackScanner

    catalog = load_needs_catalog()
    known = [
        {
            "id": str(n.get("id")),
            "label": str(n.get("label") or n.get("id")),
            "rationale": str(n.get("rationale") or ""),
            "tier": str(n.get("tier") or ""),
        }
        for n in (catalog.get("needs") or [])
        if isinstance(n, dict) and n.get("id")
    ]
    try:
        scan = StackScanner(project_root).scan()
        suggestions = suggest_needs(scan, catalog)
    except (OSError, ValueError, KeyError):
        suggestions = []
    return {
        "needs": known,
        "suggested": [
            {"id": s.need_id, "reason": s.reason, "evidence": list(s.evidence)}
            for s in suggestions
        ],
    }


def read_setup_run(project_root: Path) -> dict[str, Any]:
    """Dernier journal d'exécution du wizard, pour ``GET /api/setup/run``.

    Lu par polling côté UI (issue #171) : ``available: False`` tant qu'aucune
    exécution n'a encore écrit ``_grimoire/setup-run.json``.
    """
    run_path = project_root / SETUP_RUN_RELPATH
    if not run_path.is_file():
        return {"available": False}
    try:
        data = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False}
    if not isinstance(data, dict):
        return {"available": False}
    return {**data, "available": True}


def execute_setup_plan(
    project_root: Path,
    payload: dict[str, Any],
    *,
    install: Callable[[str], InstallResult],
) -> dict[str, Any]:
    """Exécute réellement le plan de setup (issue #171 — B2/B3 bout-en-bout).

    Appelle ``grimoire up`` en direct (``cmd_up.run_up_pipeline``) : même
    mécanique, même idempotence, même synchronisation d'hôtes — jamais un
    sous-processus qui forkerait le mécanisme en deux. Refuse AVANT d'écrire
    quoi que ce soit d'autre que le dossier racine lui-même quand le plan ne
    peut pas s'exécuter : archétype, backend ou need inconnu, chemin non
    inscriptible. Le mode « plan seul » (voir :func:`build_setup_plan`, le
    repli « copier-coller la commande ») reste disponible séparément : c'est
    à l'appelant de choisir laquelle des deux invoquer selon
    ``payload.get("planOnly")``.
    """
    archetype = str(payload.get("archetype") or "minimal")
    backend = str(payload.get("backend") or "auto")
    # Mêmes défenses que build_setup_plan : `null` explicite et types non-listes.
    name = str(payload.get("name") or "")
    user = str(payload.get("user") or "")
    needs_raw = payload.get("needs")
    needs = [str(n) for n in needs_raw if n] if isinstance(needs_raw, list) else []
    ext_raw = payload.get("extensions")
    ext_ids = [e for e in ext_raw if e] if isinstance(ext_raw, list) else []

    from grimoire.cli.cmd_up import run_up_pipeline, validate_up_inputs
    from grimoire.core.agentic_standard import load_needs_catalog

    # Refus fail-closed : rien n'est écrit avant ce point.
    validate_up_inputs([archetype] if archetype else [], backend)
    if needs:
        known_needs = {
            str(n.get("id"))
            for n in (load_needs_catalog().get("needs") or [])
            if isinstance(n, dict)
        }
        unknown = [n for n in needs if n not in known_needs]
        if unknown:
            msg = f"need(s) inconnu(s) du catalogue : {', '.join(unknown)}"
            raise ValueError(msg)
    try:
        project_root.mkdir(parents=True, exist_ok=True)
        probe = project_root / ".grimoire-setup-write-check"
        probe.touch()
        probe.unlink()
    except OSError as exc:
        msg = f"chemin non inscriptible : {project_root} ({exc})"
        raise ValueError(msg) from exc

    # `run_up_pipeline` ne lit que `ctx.obj` (voir sa docstring) : un
    # `SimpleNamespace` fait l'affaire sans dépendre de typer au runtime.
    fake_ctx = cast("typer.Context", SimpleNamespace(obj={"yes": True, "output": "json"}))
    state, checks, project_name = run_up_pipeline(
        fake_ctx, project_root,
        name=name, user=user,
        archetypes=[archetype] if archetype else [],
        backend=backend, no_standard=False, needs=needs,
        dry_run=False, no_cockpit=False, quiet=True,
    )

    installed: list[str] = []
    errors: list[str] = []
    for ext_id in ext_ids:
        try:
            result = install(str(ext_id))
            installed.append(f"{result.extension_id} v{result.version}")
        except ExtensionError as exc:
            errors.append(f"{ext_id} : {exc}")

    up_command = (
        f'grimoire up . --name "{name}" '
        f'--user "{user}" '
        f"--archetype {archetype} --backend {backend}"
    )
    if needs:
        up_command += "".join(f" --needs {n}" for n in needs)

    steps_payload = [
        {"step": s.step, "status": s.status, "detail": s.detail} for s in state.steps
    ]
    env_payload = [
        {
            "name": c.name, "passed": c.passed, "level": c.level,
            "detail": c.detail, "remedy": c.remedy,
        }
        for c in checks
    ]
    doctor_ok = bool(checks) and all(c.passed for c in checks)
    run_report = {
        "executedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "project": project_name,
        "ok": not state.failed,
        "steps": steps_payload,
        "doctorOk": doctor_ok,
        "env": env_payload,
        "extensionsInstalled": installed,
        "extensionErrors": errors,
    }
    run_path = project_root / SETUP_RUN_RELPATH
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(
        json.dumps(run_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    plan = {
        "plannedAt": run_report["executedAt"],
        "name": name,
        "user": user,
        "archetype": archetype,
        "backend": backend,
        "needs": needs,
        "extensionsInstalled": installed,
        "extensionErrors": errors,
        "initCommand": up_command,
        "executed": True,
        "run": run_report,
    }
    plan_path = project_root / "_grimoire" / "setup-plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return plan
