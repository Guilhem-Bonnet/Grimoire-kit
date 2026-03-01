#!/usr/bin/env python3
"""Digital Twin — Simulation d'impact pour projets bmad-custom-kit.

Crée un jumeau numérique du projet permettant de simuler l'impact
de changements avant de les appliquer réellement. Game changer décisionnel.

Usage:
    python digital-twin.py --project-root ./mon-projet snapshot
    python digital-twin.py --project-root ./mon-projet simulate --change "remove agent:analyst"
    python digital-twin.py --project-root ./mon-projet diff --snapshot .bmad-twin/snap-001.json
    python digital-twin.py --project-root ./mon-projet impact --target agent:dev
    python digital-twin.py --project-root ./mon-projet scenario --file scenario.yaml
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = "1.0.0"

# ── Modèle de données ──────────────────────────────────────────


@dataclass
class ProjectEntity:
    """Entité du projet (agent, tool, workflow, etc.)."""

    kind: str  # agent, tool, workflow, template, config
    name: str
    path: str
    size: int = 0
    checksum: str = ""
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Snapshot:
    """Cliché complet du projet à un instant T."""

    snapshot_id: str = ""
    timestamp: str = ""
    project_root: str = ""
    entities: list[dict[str, Any]] = field(default_factory=list)
    graph_edges: list[dict[str, str]] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    checksum: str = ""


@dataclass
class SimulationChange:
    """Un changement à simuler."""

    action: str  # add, remove, modify, rename
    target_kind: str  # agent, tool, workflow
    target_name: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImpactResult:
    """Résultat d'analyse d'impact."""

    change: dict[str, Any] = field(default_factory=dict)
    direct_impacts: list[dict[str, Any]] = field(default_factory=list)
    indirect_impacts: list[dict[str, Any]] = field(default_factory=list)
    risk_score: float = 0.0
    risk_level: str = "low"
    recommendations: list[str] = field(default_factory=list)


@dataclass
class ScenarioResult:
    """Résultat d'un scénario multi-changements."""

    scenario_name: str = ""
    changes: list[dict[str, Any]] = field(default_factory=list)
    cumulative_impacts: list[dict[str, Any]] = field(default_factory=list)
    total_risk: float = 0.0
    feasibility: str = "high"
    summary: str = ""


# ── Scanner de projet ───────────────────────────────────────────


def _compute_checksum(filepath: Path) -> str:
    """Calcule le SHA-256 d'un fichier."""
    try:
        content = filepath.read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]
    except (OSError, PermissionError):
        return "unreadable"


def _extract_references(content: str) -> list[str]:
    """Extrait les références à d'autres entités dans le contenu."""
    refs: list[str] = []
    # Références aux agents
    for match in re.finditer(r"(?:agent|persona)[:\s]+['\"]?(\w[\w-]+)", content, re.IGNORECASE):
        refs.append(f"agent:{match.group(1)}")
    # Références aux outils
    for match in re.finditer(r"(?:tool|script)[:\s]+['\"]?(\w[\w-]+\.py)", content, re.IGNORECASE):
        refs.append(f"tool:{match.group(1).replace('.py', '')}")
    # Références aux workflows
    for match in re.finditer(r"(?:workflow|flow)[:\s]+['\"]?(\w[\w-]+)", content, re.IGNORECASE):
        refs.append(f"workflow:{match.group(1)}")
    # Imports Python
    for match in re.finditer(r"from\s+(\w+)\s+import|import\s+(\w+)", content):
        module = match.group(1) or match.group(2)
        if module:
            refs.append(f"module:{module}")
    # Fichiers référencés
    for match in re.finditer(r"['\"]([a-zA-Z][\w/-]+\.(ya?ml|md|py|json))['\"]", content):
        refs.append(f"file:{match.group(1)}")
    return list(set(refs))


def _classify_entity(path: Path, root: Path) -> str | None:
    """Classifie le type d'entité d'un fichier."""
    rel = str(path.relative_to(root))
    if "/agents/" in rel and path.suffix in (".md", ".xml", ".yaml"):
        return "agent"
    if "/tools/" in rel and path.suffix == ".py":
        return "tool"
    if "/workflows/" in rel and path.suffix in (".md", ".yaml", ".yml"):
        return "workflow"
    if path.name.endswith(".tpl.md") or path.name.endswith(".tpl.yaml"):
        return "template"
    if path.name in ("config.yaml", "config.yml", "manifest.yaml"):
        return "config"
    if "/teams/" in rel and path.suffix in (".md", ".yaml"):
        return "team"
    if path.suffix in (".md",) and "/docs/" in rel:
        return "doc"
    return None


def scan_project(root: Path) -> tuple[list[ProjectEntity], list[dict[str, str]]]:
    """Scanne le projet et construit le graphe d'entités."""
    entities: list[ProjectEntity] = []
    entity_map: dict[str, ProjectEntity] = {}

    # Scan récursif
    for dirpath, _dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        # Ignorer les dossiers non pertinents
        rel_dir = str(dp.relative_to(root))
        if any(skip in rel_dir for skip in ("__pycache__", ".git", "node_modules", ".bmad-twin")):
            continue
        for fname in filenames:
            fpath = dp / fname
            kind = _classify_entity(fpath, root)
            if kind is None:
                continue
            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                content = ""

            name = fpath.stem
            refs = _extract_references(content)
            entity = ProjectEntity(
                kind=kind,
                name=name,
                path=str(fpath.relative_to(root)),
                size=fpath.stat().st_size if fpath.exists() else 0,
                checksum=_compute_checksum(fpath),
                dependencies=refs,
                metadata={"lines": content.count("\n") + 1 if content else 0},
            )
            entity_map[f"{kind}:{name}"] = entity
            entities.append(entity)

    # Résoudre les dépendants (reverse dependencies)
    edges: list[dict[str, str]] = []
    for entity in entities:
        key = f"{entity.kind}:{entity.name}"
        for dep in entity.dependencies:
            if dep in entity_map:
                entity_map[dep].dependents.append(key)
                edges.append({"from": key, "to": dep, "type": "depends_on"})

    return entities, edges


# ── Commandes ───────────────────────────────────────────────────


def cmd_snapshot(root: Path, output_dir: Path | None, as_json: bool) -> dict[str, Any]:
    """Capture un cliché complet du projet."""
    entities, edges = scan_project(root)

    # Stats
    kind_counts: dict[str, int] = {}
    for ent in entities:
        kind_counts[ent.kind] = kind_counts.get(ent.kind, 0) + 1

    snap_id = datetime.now().strftime("snap-%Y%m%d-%H%M%S")
    snap = Snapshot(
        snapshot_id=snap_id,
        timestamp=datetime.now().isoformat(),
        project_root=str(root),
        entities=[asdict(e) for e in entities],
        graph_edges=edges,
        stats=kind_counts,
    )

    # Checksum du snapshot entier
    snap_data = json.dumps(asdict(snap), sort_keys=True, default=str)
    snap.checksum = hashlib.sha256(snap_data.encode()).hexdigest()[:16]

    # Sauvegarde
    save_dir = output_dir or root / ".bmad-twin"
    save_dir.mkdir(parents=True, exist_ok=True)
    snap_file = save_dir / f"{snap_id}.json"
    snap_file.write_text(json.dumps(asdict(snap), indent=2, default=str), encoding="utf-8")

    result = {
        "snapshot_id": snap_id,
        "file": str(snap_file),
        "entities": len(entities),
        "edges": len(edges),
        "stats": kind_counts,
        "checksum": snap.checksum,
    }

    if not as_json:
        print(f"📸 Snapshot capturé : {snap_id}")
        print(f"   Fichier : {snap_file}")
        print(f"   Entités : {len(entities)}")
        print(f"   Connexions : {len(edges)}")
        print(f"   Checksum : {snap.checksum}")
        print("\n   Répartition :")
        for kind, count in sorted(kind_counts.items()):
            print(f"     {kind}: {count}")

    return result


def _parse_change(change_str: str) -> SimulationChange:
    """Parse une chaîne de changement. Format: 'action kind:name [key=val]'."""
    parts = change_str.strip().split()
    if len(parts) < 2:
        raise ValueError(f"Format attendu: 'action kind:name' — reçu: '{change_str}'")

    action = parts[0].lower()
    if action not in ("add", "remove", "modify", "rename"):
        raise ValueError(f"Action inconnue: {action}. Valides: add, remove, modify, rename")

    target = parts[1]
    if ":" not in target:
        raise ValueError(f"Target doit être 'kind:name' — reçu: '{target}'")

    kind, name = target.split(":", 1)
    details: dict[str, Any] = {}
    for part in parts[2:]:
        if "=" in part:
            k, v = part.split("=", 1)
            details[k] = v

    return SimulationChange(action=action, target_kind=kind, target_name=name, details=details)


def _analyze_impact(change: SimulationChange, entities: list[ProjectEntity],
                    edges: list[dict[str, str]]) -> ImpactResult:
    """Analyse l'impact d'un changement unique."""
    target_key = f"{change.target_kind}:{change.target_name}"

    direct: list[dict[str, Any]] = []
    indirect: list[dict[str, Any]] = []
    risk = 0.0

    # Trouver l'entité ciblée
    target_entity = None
    for ent in entities:
        if f"{ent.kind}:{ent.name}" == target_key:
            target_entity = ent
            break

    if change.action == "remove":
        if target_entity:
            # Impact direct : tous les dépendants cassent
            for dep_key in target_entity.dependents:
                direct.append({
                    "entity": dep_key,
                    "severity": "high",
                    "description": f"Dépend directement de {target_key} — cassera si supprimé",
                })
                risk += 15.0

            # Impact indirect : dépendants des dépendants
            visited = set()
            queue = list(target_entity.dependents)
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                for ent in entities:
                    if f"{ent.kind}:{ent.name}" == current:
                        for dep_dep in ent.dependents:
                            if dep_dep not in visited and dep_dep != target_key:
                                indirect.append({
                                    "entity": dep_dep,
                                    "severity": "medium",
                                    "description": f"Impacté indirectement via {current}",
                                })
                                risk += 5.0
                                queue.append(dep_dep)
        else:
            risk += 2.0  # Entité inconnue, risque faible

    elif change.action == "modify":
        if target_entity:
            for dep_key in target_entity.dependents:
                direct.append({
                    "entity": dep_key,
                    "severity": "medium",
                    "description": f"Pourrait être affecté par la modification de {target_key}",
                })
                risk += 8.0
        risk += 5.0

    elif change.action == "add":
        # Ajouter quelque chose est généralement safe
        risk += 2.0
        direct.append({
            "entity": target_key,
            "severity": "low",
            "description": "Nouvel ajout — vérifier l'intégration avec l'existant",
        })

    elif change.action == "rename":
        if target_entity:
            for dep_key in target_entity.dependents:
                direct.append({
                    "entity": dep_key,
                    "severity": "high",
                    "description": f"Référence à {target_key} devra être mise à jour",
                })
                risk += 12.0

    # Normaliser le risque
    risk = min(risk, 100.0)
    risk_level = "low" if risk < 20 else "medium" if risk < 50 else "high" if risk < 80 else "critical"

    # Recommandations
    recommendations: list[str] = []
    if risk >= 50:
        recommendations.append("⚠️ Risque élevé — tester dans un environnement isolé d'abord")
    if len(direct) > 5:
        recommendations.append("📊 Beaucoup de dépendants — prévoir une migration progressive")
    if change.action == "remove":
        recommendations.append("🗑️ Vérifier qu'aucune référence résiduelle ne subsiste après suppression")
    if change.action == "rename":
        recommendations.append("🔄 Utiliser un refactoring global (search & replace) pour les références")
    if not recommendations:
        recommendations.append("✅ Changement à faible risque — procéder normalement")

    return ImpactResult(
        change=asdict(change),
        direct_impacts=direct,
        indirect_impacts=indirect,
        risk_score=round(risk, 1),
        risk_level=risk_level,
        recommendations=recommendations,
    )


def cmd_simulate(root: Path, changes: list[str], as_json: bool) -> dict[str, Any]:
    """Simule l'impact d'un ou plusieurs changements."""
    entities, edges = scan_project(root)
    results: list[dict[str, Any]] = []

    for change_str in changes:
        change = _parse_change(change_str)
        impact = _analyze_impact(change, entities, edges)
        results.append(asdict(impact))

    # Risque cumulé
    total_risk = min(sum(r["risk_score"] for r in results), 100.0)
    total_level = "low" if total_risk < 20 else "medium" if total_risk < 50 else "high" if total_risk < 80 else "critical"

    output = {
        "simulation": {
            "changes_count": len(changes),
            "total_risk_score": round(total_risk, 1),
            "total_risk_level": total_level,
        },
        "impacts": results,
    }

    if not as_json:
        print(f"🧪 Simulation — {len(changes)} changement(s)")
        print(f"   Risque cumulé : {total_risk:.1f}/100 [{total_level.upper()}]")
        print()
        for i, res in enumerate(results, 1):
            chg = res["change"]
            print(f"  [{i}] {chg['action'].upper()} {chg['target_kind']}:{chg['target_name']}")
            print(f"      Risque : {res['risk_score']}/100 [{res['risk_level']}]")
            print(f"      Impacts directs : {len(res['direct_impacts'])}")
            for imp in res["direct_impacts"][:5]:
                sev_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(imp["severity"], "⚪")
                print(f"        {sev_icon} {imp['entity']} — {imp['description']}")
            if len(res["direct_impacts"]) > 5:
                print(f"        ... et {len(res['direct_impacts']) - 5} autres")
            if res["indirect_impacts"]:
                print(f"      Impacts indirects : {len(res['indirect_impacts'])}")
            for rec in res["recommendations"]:
                print(f"      {rec}")
            print()

    return output


def cmd_diff(root: Path, snapshot_path: str, as_json: bool) -> dict[str, Any]:
    """Compare l'état actuel du projet avec un snapshot."""
    # Charger le snapshot
    snap_file = Path(snapshot_path)
    if not snap_file.is_absolute():
        snap_file = root / snap_file
    if not snap_file.exists():
        # Chercher dans .bmad-twin
        twin_dir = root / ".bmad-twin"
        candidates = list(twin_dir.glob(f"*{snapshot_path}*")) if twin_dir.exists() else []
        if candidates:
            snap_file = candidates[0]
        else:
            print(f"❌ Snapshot introuvable : {snapshot_path}", file=sys.stderr)
            return {"error": f"Snapshot not found: {snapshot_path}"}

    snap_data = json.loads(snap_file.read_text(encoding="utf-8"))

    # Scanner l'état actuel
    current_entities, _ = scan_project(root)
    current_map = {f"{e.kind}:{e.name}": e for e in current_entities}

    # Construire le map du snapshot
    snap_map: dict[str, dict[str, Any]] = {}
    for ent in snap_data.get("entities", []):
        snap_map[f"{ent['kind']}:{ent['name']}"] = ent

    # Calculer les différences
    added: list[str] = []
    removed: list[str] = []
    modified: list[dict[str, Any]] = []
    unchanged = 0

    all_keys = set(current_map.keys()) | set(snap_map.keys())
    for key in sorted(all_keys):
        in_current = key in current_map
        in_snap = key in snap_map

        if in_current and not in_snap:
            added.append(key)
        elif not in_current and in_snap:
            removed.append(key)
        elif in_current and in_snap:
            curr = current_map[key]
            prev = snap_map[key]
            if curr.checksum != prev.get("checksum", ""):
                modified.append({
                    "entity": key,
                    "old_checksum": prev.get("checksum", ""),
                    "new_checksum": curr.checksum,
                    "old_size": prev.get("size", 0),
                    "new_size": curr.size,
                })
            else:
                unchanged += 1

    result = {
        "snapshot": snap_data.get("snapshot_id", "unknown"),
        "snapshot_date": snap_data.get("timestamp", "unknown"),
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged": unchanged,
        "total_changes": len(added) + len(removed) + len(modified),
    }

    if not as_json:
        print(f"📊 Diff vs snapshot {result['snapshot']}")
        print(f"   Date snapshot : {result['snapshot_date'][:19]}")
        print(f"   Total changements : {result['total_changes']}")
        print()
        if added:
            print(f"  ➕ Ajoutés ({len(added)}) :")
            for item in added:
                print(f"     {item}")
        if removed:
            print(f"  ➖ Supprimés ({len(removed)}) :")
            for item in removed:
                print(f"     {item}")
        if modified:
            print(f"  ✏️ Modifiés ({len(modified)}) :")
            for mod in modified:
                delta = mod["new_size"] - mod["old_size"]
                sign = "+" if delta >= 0 else ""
                print(f"     {mod['entity']} ({sign}{delta} bytes)")
        if unchanged:
            print(f"  ═ Inchangés : {unchanged}")

    return result


def cmd_impact(root: Path, target: str, as_json: bool) -> dict[str, Any]:
    """Analyse les dépendances et dépendants d'une entité."""
    entities, edges = scan_project(root)

    # Trouver l'entité
    target_entity = None
    for ent in entities:
        key = f"{ent.kind}:{ent.name}"
        if key == target or ent.name == target:
            target_entity = ent
            break

    if not target_entity:
        msg = f"Entité introuvable : {target}"
        if not as_json:
            print(f"❌ {msg}", file=sys.stderr)
        return {"error": msg}

    target_key = f"{target_entity.kind}:{target_entity.name}"

    # Calculer la profondeur d'impact (BFS)
    impact_depth: dict[str, int] = {}
    queue: list[tuple[str, int]] = [(target_key, 0)]
    visited: set[str] = set()

    while queue:
        current, depth = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        impact_depth[current] = depth

        for ent in entities:
            if f"{ent.kind}:{ent.name}" == current:
                for dep in ent.dependents:
                    if dep not in visited:
                        queue.append((dep, depth + 1))

    # Criticité : combinaison du nombre de dépendants et de la profondeur
    max_depth = max(impact_depth.values()) if impact_depth else 0
    fan_out = len(target_entity.dependents)
    total_reach = len(impact_depth) - 1  # Exclure soi-même
    criticality = min(100, (fan_out * 15) + (total_reach * 5) + (max_depth * 10))
    crit_level = "low" if criticality < 25 else "medium" if criticality < 50 else "high" if criticality < 75 else "critical"

    result = {
        "target": target_key,
        "path": target_entity.path,
        "dependencies": target_entity.dependencies,
        "dependents": target_entity.dependents,
        "impact_reach": total_reach,
        "max_depth": max_depth,
        "criticality_score": criticality,
        "criticality_level": crit_level,
        "impact_map": {k: v for k, v in sorted(impact_depth.items(), key=lambda x: x[1]) if k != target_key},
    }

    if not as_json:
        print(f"🎯 Analyse d'impact : {target_key}")
        print(f"   Fichier : {target_entity.path}")
        print(f"   Criticité : {criticality}/100 [{crit_level.upper()}]")
        print(f"   Portée d'impact : {total_reach} entités, profondeur max {max_depth}")
        print()
        if target_entity.dependencies:
            print(f"  📥 Dépendances ({len(target_entity.dependencies)}) :")
            for dep in target_entity.dependencies[:10]:
                print(f"     → {dep}")
        if target_entity.dependents:
            print(f"  📤 Dépendants ({len(target_entity.dependents)}) :")
            for dep in target_entity.dependents[:10]:
                print(f"     ← {dep}")
        if impact_depth:
            print("\n  🌊 Carte de propagation :")
            for entity, depth in sorted(impact_depth.items(), key=lambda x: x[1]):
                if entity == target_key:
                    continue
                indent = "  " * depth
                print(f"     {indent}[depth {depth}] {entity}")

    return result


def cmd_scenario(root: Path, scenario_file: str | None, scenario_name: str,
                 changes: list[str], as_json: bool) -> dict[str, Any]:
    """Exécute un scénario complet de changements."""
    scenario_changes: list[str] = []

    if scenario_file:
        sf = Path(scenario_file)
        if not sf.is_absolute():
            sf = root / sf
        if sf.exists():
            content = sf.read_text(encoding="utf-8")
            # Parse YAML-like format simple
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("name:"):
                    if line.startswith("- "):
                        scenario_changes.append(line[2:].strip())
                    elif any(line.startswith(act) for act in ("add ", "remove ", "modify ", "rename ")):
                        scenario_changes.append(line)
        else:
            return {"error": f"Fichier scénario introuvable : {scenario_file}"}

    scenario_changes.extend(changes)

    if not scenario_changes:
        return {"error": "Aucun changement défini dans le scénario"}

    entities, edges = scan_project(root)
    impacts: list[dict[str, Any]] = []

    for change_str in scenario_changes:
        change = _parse_change(change_str)
        impact = _analyze_impact(change, entities, edges)
        impacts.append(asdict(impact))

    total_risk = min(sum(imp["risk_score"] for imp in impacts), 100.0)
    total_direct = sum(len(imp["direct_impacts"]) for imp in impacts)
    total_indirect = sum(len(imp["indirect_impacts"]) for imp in impacts)

    # Évaluer la faisabilité
    if total_risk < 20:
        feasibility = "high"
    elif total_risk < 50:
        feasibility = "medium"
    elif total_risk < 80:
        feasibility = "low"
    else:
        feasibility = "very_low"

    # Résumé intelligent
    high_risk_changes = [imp for imp in impacts if imp["risk_level"] in ("high", "critical")]
    summary_parts: list[str] = []
    summary_parts.append(f"{len(scenario_changes)} changements analysés")
    summary_parts.append(f"risque cumulé {total_risk:.0f}/100")
    summary_parts.append(f"{total_direct} impacts directs, {total_indirect} indirects")
    if high_risk_changes:
        summary_parts.append(f"⚠️ {len(high_risk_changes)} changement(s) à haut risque")

    result = ScenarioResult(
        scenario_name=scenario_name or "unnamed",
        changes=[{"change": c} for c in scenario_changes],
        cumulative_impacts=impacts,
        total_risk=round(total_risk, 1),
        feasibility=feasibility,
        summary=". ".join(summary_parts),
    )

    output = asdict(result)

    if not as_json:
        print(f"🎬 Scénario : {result.scenario_name}")
        print(f"   Changements : {len(scenario_changes)}")
        print(f"   Risque total : {total_risk:.1f}/100")
        print(f"   Faisabilité : {feasibility.upper()}")
        print(f"\n   Résumé : {result.summary}")
        print()
        for i, imp in enumerate(impacts, 1):
            chg = imp["change"]
            risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "⛔"}.get(imp["risk_level"], "⚪")
            print(f"  [{i}] {risk_icon} {chg['action']} {chg['target_kind']}:{chg['target_name']} "
                  f"— risque {imp['risk_score']}")
            for rec in imp["recommendations"]:
                print(f"      {rec}")
        print()
        if feasibility in ("low", "very_low"):
            print("  ⚠️ ATTENTION : Ce scénario présente des risques élevés.")
            print("  Recommandation : diviser en étapes plus petites et tester incrémentalement.")

    return output


# ── CLI ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Construit le parser CLI."""
    parser = argparse.ArgumentParser(
        prog="digital-twin",
        description="Digital Twin — Simulation d'impact pour projets bmad-custom-kit",
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Sortie JSON")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    subs = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # snapshot
    snap = subs.add_parser("snapshot", help="Capturer un cliché du projet")
    snap.add_argument("--output-dir", type=Path, help="Dossier de sortie")

    # simulate
    sim = subs.add_parser("simulate", help="Simuler l'impact de changements")
    sim.add_argument("--change", action="append", required=True,
                     help="Changement à simuler (ex: 'remove agent:analyst')")

    # diff
    diff_cmd = subs.add_parser("diff", help="Comparer avec un snapshot")
    diff_cmd.add_argument("--snapshot", required=True,
                          help="Chemin ou ID du snapshot")

    # impact
    imp = subs.add_parser("impact", help="Analyser l'impact d'une entité")
    imp.add_argument("--target", required=True,
                     help="Entité cible (ex: 'agent:dev' ou 'tool:oracle')")

    # scenario
    scen = subs.add_parser("scenario", help="Exécuter un scénario multi-changements")
    scen.add_argument("--file", dest="scenario_file",
                      help="Fichier de scénario")
    scen.add_argument("--name", default="unnamed",
                      help="Nom du scénario")
    scen.add_argument("--change", action="append", default=[],
                      help="Changement additionnel")

    return parser


def main() -> None:
    """Point d'entrée principal."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    root = args.project_root.resolve()
    if not root.exists():
        print(f"❌ Répertoire introuvable : {root}", file=sys.stderr)
        sys.exit(1)

    result: dict[str, Any] = {}

    if args.command == "snapshot":
        result = cmd_snapshot(root, getattr(args, "output_dir", None), args.as_json)
    elif args.command == "simulate":
        result = cmd_simulate(root, args.change, args.as_json)
    elif args.command == "diff":
        result = cmd_diff(root, args.snapshot, args.as_json)
    elif args.command == "impact":
        result = cmd_impact(root, args.target, args.as_json)
    elif args.command == "scenario":
        result = cmd_scenario(root, args.scenario_file, args.name,
                              args.change, args.as_json)

    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
