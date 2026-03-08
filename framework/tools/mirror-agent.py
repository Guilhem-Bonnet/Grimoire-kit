#!/usr/bin/env python3
"""Mirror Agent — Neurones miroirs pour apprentissage inter-agents.

Observe comment un agent performe, extrait les patterns gagnants,
et les applique aux autres agents. Apprentissage par mimétisme.

Usage:
    python mirror-agent.py --project-root ./mon-projet observe --agent dev
    python mirror-agent.py --project-root ./mon-projet learn --source dev --pattern delivery
    python mirror-agent.py --project-root ./mon-projet mirror --from dev --to qa
    python mirror-agent.py --project-root ./mon-projet catalog
    python mirror-agent.py --project-root ./mon-projet diff --agents dev,qa
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

MIRROR_DIR = ".grimoire-mirror"
PATTERNS_FILE = "learned-patterns.json"

# ── Modèle de données ──────────────────────────────────────────


@dataclass
class AgentProfile:
    """Profil observé d'un agent."""

    name: str = ""
    path: str = ""
    persona: str = ""
    capabilities: list[str] = field(default_factory=list)
    menu_items: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    patterns: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    total_lines: int = 0
    checksum: str = ""


@dataclass
class LearnedPattern:
    """Pattern appris par observation."""

    pattern_id: str = ""
    source_agent: str = ""
    pattern_type: str = ""  # structure, communication, menu, workflow, greeting
    name: str = ""
    description: str = ""
    content: str = ""
    effectiveness: float = 0.0  # 0-1
    timestamp: str = ""
    applicable_to: list[str] = field(default_factory=list)


@dataclass
class MirrorSuggestion:
    """Suggestion de transfert de pattern."""

    from_agent: str = ""
    to_agent: str = ""
    pattern: str = ""
    description: str = ""
    difficulty: str = "medium"  # easy, medium, hard
    expected_impact: str = "medium"  # low, medium, high


# ── Utilitaires ─────────────────────────────────────────────────


def _find_agents(root: Path) -> dict[str, Path]:
    """Trouve tous les agents du projet."""
    agents: dict[str, Path] = {}
    for search_dir in ("framework", "archetypes"):
        for dirpath, _dirs, filenames in os.walk(root / search_dir):
            if "/agents/" not in dirpath:
                continue
            for fname in filenames:
                if fname.endswith((".md", ".xml", ".yaml")):
                    name = Path(fname).stem
                    agents[name] = Path(dirpath) / fname
    return agents


def _observe_agent(root: Path, agent_path: Path) -> AgentProfile:
    """Observe un agent et construit son profil."""
    content = agent_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    name = agent_path.stem

    profile = AgentProfile(
        name=name,
        path=str(agent_path.relative_to(root)),
        total_lines=len(lines),
        checksum=hashlib.sha256(content.encode()).hexdigest()[:16],
    )

    # Extraire la persona
    persona_match = re.search(r"(?:persona|name|title)[:\s]+['\"]?(.+?)(?:['\"]|\n)", content, re.IGNORECASE)
    if persona_match:
        profile.persona = persona_match.group(1).strip()

    # Extraire les capacités
    cap_section = re.findall(r"(?:capabilities?|skills?|can)[:\s]+(.+?)(?:\n\n|\Z)", content,
                             re.IGNORECASE | re.DOTALL)
    for cap in cap_section:
        for line in cap.splitlines():
            line = line.strip().lstrip("-•*").strip()
            if line and len(line) > 3:
                profile.capabilities.append(line[:100])

    # Extraire les items de menu
    menu_items = re.findall(r"(?:^|\n)\s*\d+[\.\)]\s+\*?\*?(.+?)(?:\*\*)?(?:\n|$)", content)
    profile.menu_items = [item.strip()[:80] for item in menu_items[:20]]

    # Extracter les sections (headers)
    for line in lines:
        header_match = re.match(r"^#{1,4}\s+(.+)", line)
        if header_match:
            profile.sections.append(header_match.group(1).strip())

    # Patterns structurels
    profile.patterns = {
        "has_greeting": bool(re.search(r"(?:greeting|welcome|bonjour|hello)", content, re.IGNORECASE)),
        "has_menu": bool(re.search(r"(?:menu|options|choose|choisir)", content, re.IGNORECASE)),
        "has_exit": bool(re.search(r"(?:exit|goodbye|au revoir|wrap.?up)", content, re.IGNORECASE)),
        "has_context_load": bool(re.search(r"(?:load|charge|context|mémoire)", content, re.IGNORECASE)),
        "has_error_handling": bool(re.search(r"(?:error|erreur|fallback|recover)", content, re.IGNORECASE)),
        "has_validation": bool(re.search(r"(?:valid|check|verify|vérif)", content, re.IGNORECASE)),
        "has_collaboration": bool(re.search(r"(?:collab|team|agent|delegate)", content, re.IGNORECASE)),
        "has_output_format": bool(re.search(r"(?:format|output|template|livrable)", content, re.IGNORECASE)),
        "uses_skill_level": bool(re.search(r"skill.?level", content, re.IGNORECASE)),
        "uses_metaphor": bool(re.search(r"metaphor|métaphore", content, re.IGNORECASE)),
    }

    # Métriques
    pattern_score = sum(1 for v in profile.patterns.values() if v)
    profile.metrics = {
        "completeness": round(pattern_score / max(len(profile.patterns), 1), 2),
        "section_count": len(profile.sections),
        "menu_items": len(profile.menu_items),
        "capabilities": len(profile.capabilities),
        "code_density": round(content.count("```") / max(len(lines), 1) * 100, 2),
    }

    return profile


def _load_patterns(root: Path) -> list[LearnedPattern]:
    """Charge les patterns appris."""
    pfile = root / MIRROR_DIR / PATTERNS_FILE
    if not pfile.exists():
        return []
    try:
        data = json.loads(pfile.read_text(encoding="utf-8"))
        return [LearnedPattern(**{k: v for k, v in p.items()
                                  if k in LearnedPattern.__dataclass_fields__})
                for p in data]
    except (json.JSONDecodeError, TypeError):
        return []


def _save_patterns(root: Path, patterns: list[LearnedPattern]) -> None:
    """Sauvegarde les patterns appris."""
    pdir = root / MIRROR_DIR
    pdir.mkdir(parents=True, exist_ok=True)
    pfile = pdir / PATTERNS_FILE
    pfile.write_text(json.dumps([asdict(p) for p in patterns], indent=2,
                                ensure_ascii=False, default=str), encoding="utf-8")


# ── Commandes ───────────────────────────────────────────────────


def cmd_observe(root: Path, agent_name: str | None, as_json: bool) -> dict[str, Any]:
    """Observe un agent ou tous les agents."""
    agents = _find_agents(root)

    if agent_name:
        if agent_name not in agents:
            return {"error": f"Agent '{agent_name}' introuvable. "
                    f"Disponibles : {', '.join(sorted(agents.keys())[:10])}"}
        targets = {agent_name: agents[agent_name]}
    else:
        targets = agents

    profiles: dict[str, dict[str, Any]] = {}
    for aname, apath in sorted(targets.items()):
        profile = _observe_agent(root, apath)
        profiles[aname] = asdict(profile)

    # Classement par completeness
    ranked = sorted(profiles.items(), key=lambda x: x[1].get("metrics", {}).get("completeness", 0),
                    reverse=True)

    result = {
        "agents_observed": len(profiles),
        "profiles": profiles,
        "ranking": [{"agent": name, "completeness": data["metrics"]["completeness"]}
                    for name, data in ranked],
    }

    if not as_json:
        print(f"👁️ Observation — {len(profiles)} agent(s)")
        print()
        for name, data in ranked:
            comp = data["metrics"]["completeness"]
            bar_len = int(comp * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            print(f"  🤖 {name}")
            print(f"     Complétude : [{bar}] {comp:.0%}")
            print(f"     Sections : {data['metrics']['section_count']} | "
                  f"Menu : {data['metrics']['menu_items']} | "
                  f"Capacités : {data['metrics']['capabilities']}")

            # Patterns manquants
            missing = [k for k, v in data.get("patterns", {}).items() if not v]
            if missing:
                print(f"     ⚠️ Manquant : {', '.join(missing[:5])}")
            print()

    return result


def cmd_learn(root: Path, source: str, pattern_type: str | None,
              as_json: bool) -> dict[str, Any]:
    """Extrait et apprend les patterns d'un agent."""
    agents = _find_agents(root)
    if source not in agents:
        return {"error": f"Agent source '{source}' introuvable"}

    profile = _observe_agent(root, agents[source])
    existing = _load_patterns(root)
    existing_ids = {p.pattern_id for p in existing}
    new_patterns: list[LearnedPattern] = []

    # Extraire les patterns
    content = agents[source].read_text(encoding="utf-8", errors="replace")

    pattern_extractors = [
        ("greeting", r"(?:greeting|welcome|activation)[:\s]*\n((?:.*\n){1,10})", "Comment l'agent se présente"),
        ("menu", r"(?:menu|options)[:\s]*\n((?:.*\n){1,15})", "Structure du menu"),
        ("error_handling", r"(?:error|fallback|recover)[:\s]*\n((?:.*\n){1,8})", "Gestion des erreurs"),
        ("validation", r"(?:valid|check|verify)[:\s]*\n((?:.*\n){1,8})", "Validation et vérification"),
        ("output_format", r"(?:output|format|template|livrable)[:\s]*\n((?:.*\n){1,10})", "Format de sortie"),
        ("collaboration", r"(?:collab|delegate|handoff)[:\s]*\n((?:.*\n){1,8})", "Collaboration inter-agents"),
    ]

    for ptype, regex, desc in pattern_extractors:
        if pattern_type and ptype != pattern_type:
            continue
        matches = re.findall(regex, content, re.IGNORECASE)
        for match in matches:
            pid = f"pat-{source}-{ptype}-{hashlib.md5(match.encode()).hexdigest()[:6]}"
            if pid not in existing_ids:
                lp = LearnedPattern(
                    pattern_id=pid,
                    source_agent=source,
                    pattern_type=ptype,
                    name=f"{ptype} de {source}",
                    description=desc,
                    content=match.strip()[:500],
                    effectiveness=profile.metrics.get("completeness", 0.5),
                    timestamp=datetime.now().isoformat(),
                    applicable_to=["all"],
                )
                new_patterns.append(lp)

    # Sauvegarder
    all_patterns = existing + new_patterns
    _save_patterns(root, all_patterns)

    result = {
        "source": source,
        "new_patterns": len(new_patterns),
        "total_patterns": len(all_patterns),
        "patterns": [asdict(p) for p in new_patterns],
    }

    if not as_json:
        print(f"🧠 Apprentissage depuis {source}")
        print(f"   Nouveaux patterns : {len(new_patterns)}")
        print(f"   Total catalogue : {len(all_patterns)}")
        for pat in new_patterns:
            print(f"   📎 {pat.pattern_id} [{pat.pattern_type}] — {pat.description}")

    return result


def cmd_mirror(root: Path, from_agent: str, to_agent: str,
               as_json: bool) -> dict[str, Any]:
    """Analyse comment améliorer un agent en s'inspirant d'un autre."""
    agents = _find_agents(root)
    if from_agent not in agents:
        return {"error": f"Agent source '{from_agent}' introuvable"}
    if to_agent not in agents:
        return {"error": f"Agent cible '{to_agent}' introuvable"}

    prof_from = _observe_agent(root, agents[from_agent])
    prof_to = _observe_agent(root, agents[to_agent])

    suggestions: list[dict[str, Any]] = []

    # Comparer les patterns
    for pattern_key, has_source in prof_from.patterns.items():
        has_target = prof_to.patterns.get(pattern_key, False)
        if has_source and not has_target:
            suggestions.append(asdict(MirrorSuggestion(
                from_agent=from_agent,
                to_agent=to_agent,
                pattern=pattern_key,
                description=f"L'agent {from_agent} a '{pattern_key}' — manquant chez {to_agent}",
                difficulty="medium",
                expected_impact="medium",
            )))

    # Comparer les métriques
    for metric, val_from in prof_from.metrics.items():
        val_to = prof_to.metrics.get(metric, 0)
        if isinstance(val_from, (int, float)) and isinstance(val_to, (int, float)):
            if val_from > val_to * 1.5 and val_from > 0:
                suggestions.append(asdict(MirrorSuggestion(
                    from_agent=from_agent,
                    to_agent=to_agent,
                    pattern=metric,
                    description=f"{from_agent} a {metric}={val_from} vs {to_agent}={val_to} — écart significatif",
                    difficulty="easy",
                    expected_impact="high" if metric == "completeness" else "medium",
                )))

    result = {
        "from": from_agent,
        "to": to_agent,
        "suggestions": suggestions,
        "total_suggestions": len(suggestions),
        "source_completeness": prof_from.metrics.get("completeness", 0),
        "target_completeness": prof_to.metrics.get("completeness", 0),
    }

    if not as_json:
        print(f"🪞 Mirroring : {from_agent} → {to_agent}")
        print(f"   Complétude source : {prof_from.metrics.get('completeness', 0):.0%}")
        print(f"   Complétude cible : {prof_to.metrics.get('completeness', 0):.0%}")
        print(f"   Suggestions : {len(suggestions)}")
        print()
        for sug in suggestions:
            impact_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(sug["expected_impact"], "⚪")
            diff_icon = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}.get(sug["difficulty"], "⚪")
            print(f"  {impact_icon} {sug['pattern']}")
            print(f"     {sug['description']}")
            print(f"     Difficulté : {diff_icon} {sug['difficulty']} | Impact : {sug['expected_impact']}")
            print()

    return result


def cmd_catalog(root: Path, as_json: bool) -> dict[str, Any]:
    """Affiche le catalogue de patterns appris."""
    patterns = _load_patterns(root)

    # Regrouper par type
    by_type: dict[str, list[LearnedPattern]] = {}
    for pat in patterns:
        by_type.setdefault(pat.pattern_type, []).append(pat)

    result = {
        "total_patterns": len(patterns),
        "by_type": {k: len(v) for k, v in by_type.items()},
        "patterns": [asdict(p) for p in patterns],
    }

    if not as_json:
        if not patterns:
            print("📭 Aucun pattern appris.")
            print("   Utilisez 'mirror-agent learn --source <agent>' pour apprendre.")
            return result

        print(f"📚 Catalogue de patterns ({len(patterns)})")
        print()
        for ptype, pats in sorted(by_type.items()):
            print(f"  📂 {ptype} ({len(pats)})")
            for pat in pats:
                eff_bar = "█" * int(pat.effectiveness * 10) + "░" * (10 - int(pat.effectiveness * 10))
                print(f"     [{pat.pattern_id}] de {pat.source_agent}")
                print(f"       Efficacité : [{eff_bar}] {pat.effectiveness:.0%}")
                if pat.content:
                    preview = pat.content[:80].replace("\n", " ")
                    print(f"       Aperçu : {preview}...")
            print()

    return result


def cmd_diff(root: Path, agent_names: list[str], as_json: bool) -> dict[str, Any]:
    """Compare les comportements de plusieurs agents."""
    agents = _find_agents(root)
    profiles: dict[str, AgentProfile] = {}

    for name in agent_names:
        if name not in agents:
            return {"error": f"Agent '{name}' introuvable"}
        profiles[name] = _observe_agent(root, agents[name])

    # Matrice de comparaison
    all_patterns = set()
    for prof in profiles.values():
        all_patterns.update(prof.patterns.keys())

    comparison: dict[str, dict[str, bool]] = {}
    for pat in sorted(all_patterns):
        comparison[pat] = {name: prof.patterns.get(pat, False)
                           for name, prof in profiles.items()}

    # Trouver les patterns universels vs uniques
    universal = [pat for pat, vals in comparison.items() if all(vals.values())]
    unique: dict[str, list[str]] = {}
    for pat, vals in comparison.items():
        owners = [name for name, has in vals.items() if has]
        if len(owners) == 1:
            unique.setdefault(owners[0], []).append(pat)

    result = {
        "agents": agent_names,
        "comparison": comparison,
        "universal_patterns": universal,
        "unique_patterns": unique,
        "metrics": {name: prof.metrics for name, prof in profiles.items()},
    }

    if not as_json:
        print(f"📊 Comparaison : {' vs '.join(agent_names)}")
        print()

        # Table de patterns
        header = f"  {'Pattern':<25s}" + "".join(f" {name:<12s}" for name in agent_names)
        print(header)
        print("  " + "─" * (25 + 13 * len(agent_names)))

        for pat in sorted(all_patterns):
            row = f"  {pat:<25s}"
            for name in agent_names:
                has = comparison[pat].get(name, False)
                row += f" {'✅':^12s}" if has else f" {'❌':^12s}"
            print(row)

        print()
        if universal:
            print(f"  🌐 Patterns universels : {', '.join(universal)}")
        for name, pats in unique.items():
            print(f"  ⭐ Unique à {name} : {', '.join(pats)}")

    return result


# ── CLI ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Construit le parser CLI."""
    parser = argparse.ArgumentParser(
        prog="mirror-agent",
        description="Mirror Agent — Neurones miroirs pour apprentissage inter-agents",
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Sortie JSON")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    subs = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # observe
    obs = subs.add_parser("observe", help="Observer un agent")
    obs.add_argument("--agent", help="Nom de l'agent (tous si omis)")

    # learn
    lrn = subs.add_parser("learn", help="Apprendre les patterns d'un agent")
    lrn.add_argument("--source", required=True, help="Agent source")
    lrn.add_argument("--pattern", help="Type de pattern (tous si omis)")

    # mirror
    mir = subs.add_parser("mirror", help="Transférer des patterns entre agents")
    mir.add_argument("--from", dest="from_agent", required=True, help="Agent source")
    mir.add_argument("--to", dest="to_agent", required=True, help="Agent cible")

    # catalog
    subs.add_parser("catalog", help="Afficher le catalogue de patterns")

    # diff
    dif = subs.add_parser("diff", help="Comparer les comportements des agents")
    dif.add_argument("--agents", required=True,
                     help="Noms séparés par virgule (ex: dev,qa,architect)")

    return parser


def main() -> None:
    """Point d'entrée principal."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    root = args.project_root.resolve()

    result: dict[str, Any] = {}

    if args.command == "observe":
        result = cmd_observe(root, getattr(args, "agent", None), args.as_json)
    elif args.command == "learn":
        result = cmd_learn(root, args.source, getattr(args, "pattern", None), args.as_json)
    elif args.command == "mirror":
        result = cmd_mirror(root, args.from_agent, args.to_agent, args.as_json)
    elif args.command == "catalog":
        result = cmd_catalog(root, args.as_json)
    elif args.command == "diff":
        agent_list = [a.strip() for a in args.agents.split(",")]
        result = cmd_diff(root, agent_list, args.as_json)

    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
