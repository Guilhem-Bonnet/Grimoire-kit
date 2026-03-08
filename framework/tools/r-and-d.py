#!/usr/bin/env python3
"""
r-and-d.py — Innovation Engine avec Reinforcement Learning Grimoire.
==================================================================

Moteur d'innovation autonome qui exécute des cycles de R&D et apprend
de ses résultats via un système de renforcement. Peut lancer N cycles
intensifs en continu — seules les meilleures innovations survivent.

Architecture du cycle (7 phases) :
  1. HARVEST   — Récolte d'idées depuis dream, oracle, early-warning, DNA drift
  2. EVALUATE  — Scoring multi-dimensionnel automatique
  3. CHALLENGE — Adversarial red-team / pre-mortem
  4. SIMULATE  — Impact simulation via digital-twin
  5. IMPLEMENT — Quality gates (tests, lint, harmony)
  6. SELECT    — Tournament sélection darwinienne
  7. CONVERGE  — Critères d'arrêt et apprentissage

Reinforcement Learning :
  - Chaque idée produit un reward signal (fitness delta)
  - La policy (poids des sources, domaines, paramètres) s'ajuste
  - Epsilon-greedy : 80% exploit best patterns, 20% explore
  - Mémoire à long terme : quelles sources/domaines/patterns produisent
    les meilleures innovations

Modes :
  cycle     — Un cycle d'innovation
  train     — N cycles intensifs (reinforcement learning)
  harvest   — Phase 1 seule (récolte d'idées)
  evaluate  — Phase 2 seule (scoring)
  status    — État du moteur
  history   — Historique des cycles
  dashboard — Tableau de bord markdown
  tune      — Ajuster les paramètres manuellement
  reset     — Reset du moteur (garde l'historique)

Usage :
  python3 r-and-d.py --project-root . cycle                    # 1 cycle complet
  python3 r-and-d.py --project-root . cycle --quick            # harvest+evaluate only
  python3 r-and-d.py --project-root . train --epochs 5         # 5 cycles intensifs
  python3 r-and-d.py --project-root . train --epochs 10 --budget 3  # 10 cycles, 3 idées/cycle
  python3 r-and-d.py --project-root . train --epochs 20 --auto-stop # Stop si convergence
  python3 r-and-d.py --project-root . harvest                  # Phase 1 seule
  python3 r-and-d.py --project-root . status                   # État actuel
  python3 r-and-d.py --project-root . history                  # Historique
  python3 r-and-d.py --project-root . dashboard                # Dashboard markdown
  python3 r-and-d.py --project-root . tune --epsilon 0.3       # Ajuster exploration
  python3 r-and-d.py --project-root . reset                    # Reset

Structure modulaire :
  rnd_core.py    — Constantes, data classes, persistence
  rnd_harvest.py — Phase 1 : récolte d'idées
  rnd_engine.py  — Phases 2-7 : évaluation, challenge, simulation, RL
  r-and-d.py     — CLI et commandes (ce fichier)

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import json

# ── Re-export de tous les symboles publics ───────────────────────
# Permet la rétro-compatibilité : les tests et outils qui font
# `import r_and_d; r_and_d.harvest(...)` continuent de fonctionner.
import logging
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any  # noqa: F401

from rnd_core import (  # noqa: F401
    ACTIONS,
    CONDITIONAL_THRESHOLD,
    CONVERGENCE_WINDOW,
    DEFAULT_BUDGET,
    DEFAULT_EPOCHS,
    DEFAULT_EPSILON,
    DEFAULT_LEARNING_RATE,
    DOMAINS,
    FOSSIL_DIR,
    GO_THRESHOLD,
    HARVEST_SOURCES,
    HISTORY_DIR,
    MAX_CYCLE_REPORTS_LOAD,
    MAX_MEMORY_SIZE,
    MEMORY_FILE,
    MIN_REJECT_RATIO,
    MIN_REWARD_DELTA,
    POLICY_FILE,
    PROTOTYPE_DIR,
    RND_DIR,
    SCORING_DIMS,
    VERSION,
    CycleReport,
    Idea,
    Policy,
    TrainReport,
    _ensure_dirs,
    _load_tool,
    _rnd_dir,
    load_cycle_reports,
    load_memory,
    load_policy,
    next_cycle_id,
    save_cycle_report,
    save_memory,
    save_policy,
)
from rnd_engine import (  # noqa: F401
    _compute_health_delta,
    _print_cycle_summary,
    _print_train_summary,
    _snapshot_project_health,
    challenge,
    check_convergence,
    check_quality_gates,
    evaluate,
    run_cycle,
    select_winners,
    simulate,
    train,
    update_policy,
)
from rnd_harvest import (  # noqa: F401
    _classify_action,
    _classify_domain,
    _gap_driven_ideas,
    _generate_synthetic_ideas,
    _harvest_from_dream,
    _harvest_from_early_warning,
    _harvest_from_harmony,
    _harvest_from_incubator,
    _harvest_from_oracle,
    _harvest_from_project_scan,
    _harvest_from_stigmergy,
    _mutate_past_winners,
    harvest,
)

_log = logging.getLogger("grimoire.r_and_d")

# ── FACADE — TOUT LE CODE LOGIQUE EST DANS LES MODULES ──────────
# Ce fichier ne contient plus que les commandes CLI et main().
# Voir : rnd_core.py, rnd_harvest.py, rnd_engine.py


# ── Commands ─────────────────────────────────────────────────────

def cmd_cycle(args: argparse.Namespace) -> int:
    """Commande: un cycle d'innovation."""
    project_root = Path(args.project_root).resolve()
    _ensure_dirs(project_root)
    policy = load_policy(project_root)

    report = run_cycle(project_root, policy, budget=args.budget,
                       quick=args.quick)
    save_cycle_report(project_root, report)
    save_policy(project_root, policy)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_cycle_summary(report, 1, 1)
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    """Commande: N cycles intensifs avec reinforcement learning."""
    project_root = Path(args.project_root).resolve()
    _ensure_dirs(project_root)

    report = train(
        project_root,
        epochs=args.epochs,
        budget=args.budget,
        auto_stop=args.auto_stop,
        verbose=not args.json,
    )

    if args.json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))

    # Sauvegarder le rapport d'entraînement
    train_path = _rnd_dir(project_root) / f"train-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    train_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False),
                          encoding="utf-8")

    return 0


def cmd_harvest(args: argparse.Namespace) -> int:
    """Commande: phase 1 seule."""
    project_root = Path(args.project_root).resolve()
    policy = load_policy(project_root)
    ideas = harvest(project_root, policy, budget=args.budget)

    if args.json:
        print(json.dumps([i.to_dict() for i in ideas], indent=2, ensure_ascii=False))
    else:
        print(f"\n🌾 Récolte : {len(ideas)} idée(s)\n")
        for idea in ideas:
            print(f"  [{idea.domain}] {idea.title}")
            print(f"    Source: {idea.source} | Action: {idea.action}")
            if idea.description:
                print(f"    {idea.description[:100]}")
            print()
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Commande: phase 2 seule."""
    project_root = Path(args.project_root).resolve()
    policy = load_policy(project_root)
    ideas = harvest(project_root, policy, budget=args.budget)
    ideas = evaluate(ideas, project_root, policy)

    if args.json:
        print(json.dumps([i.to_dict() for i in ideas], indent=2, ensure_ascii=False))
    else:
        print(f"\n📊 Évaluation : {len(ideas)} idée(s)\n")
        for idea in ideas:
            bar_len = 20
            bar_fill = int(idea.total_score * bar_len)
            bar = "█" * bar_fill + "░" * (bar_len - bar_fill)
            print(f"  [{bar}] {idea.total_score:.3f} — {idea.title[:60]}")
            print(f"    {' | '.join(f'{k}={v:.2f}' for k, v in idea.scores.items())}")
            print()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Commande: état du moteur."""
    project_root = Path(args.project_root).resolve()
    policy = load_policy(project_root)
    reports = load_cycle_reports(project_root)
    memory = load_memory(project_root)

    status = {
        "version": VERSION,
        "cycles_completed": len(reports),
        "total_ideas_explored": policy.total_ideas,
        "total_merged": policy.total_merged,
        "generations": policy.generations,
        "epsilon": policy.epsilon,
        "learning_rate": policy.learning_rate,
        "memory_entries": len(memory),
    }

    if reports:
        last = reports[-1]
        status["last_cycle"] = {
            "id": last.cycle_id,
            "timestamp": last.timestamp,
            "verdict": last.verdict,
            "best_reward": last.best_reward,
        }

    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print(f"\n🔬 R&D Engine v{VERSION}")
        print(f"  Cycles: {len(reports)}")
        print(f"  Idées explorées: {policy.total_ideas}")
        print(f"  Innovations mergées: {policy.total_merged}")
        print(f"  Epsilon (exploration): {policy.epsilon:.3f}")
        print(f"  Learning rate: {policy.learning_rate:.3f}")
        print(f"  Mémoire: {len(memory)} entrées")
        if reports:
            last = reports[-1]
            print(f"\n  Dernier cycle: #{last.cycle_id}"
                  f" ({last.timestamp[:10]})"
                  f" — {last.verdict}")
            print(f"  Best reward: {last.best_reward:.4f}")

        # Top policy weights
        if policy.source_weights:
            top = sorted(policy.source_weights.items(),
                         key=lambda x: x[1], reverse=True)[:3]
            print(f"\n  🧠 Top sources: {', '.join(f'{s}({w:.3f})' for s, w in top)}")
        if policy.domain_weights:
            top = sorted(policy.domain_weights.items(),
                         key=lambda x: x[1], reverse=True)[:3]
            print(f"  🧠 Top domaines: {', '.join(f'{d}({w:.3f})' for d, w in top)}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """Commande: historique des cycles."""
    project_root = Path(args.project_root).resolve()
    reports = load_cycle_reports(project_root)

    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2, ensure_ascii=False))
    else:
        if not reports:
            print("\n📜 Aucun cycle dans l'historique.")
            return 0

        print(f"\n📜 Historique : {len(reports)} cycle(s)\n")
        for r in reports:
            bar_len = 15
            bar_fill = int(r.best_reward * bar_len) if r.best_reward > 0 else 0
            bar = "█" * bar_fill + "░" * (bar_len - bar_fill)
            print(f"  #{r.cycle_id:04d} [{bar}] {r.best_reward:.3f}"
                  f" | {r.ideas_harvested}→{r.ideas_merged} merged"
                  f" | {r.verdict}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Commande: dashboard markdown."""
    project_root = Path(args.project_root).resolve()
    policy = load_policy(project_root)
    reports = load_cycle_reports(project_root)
    memory = load_memory(project_root)

    lines = [
        "# 🔬 R&D Innovation Engine — Dashboard",
        "",
        f"*Généré le {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
        "## Métriques globales",
        "",
        "| Métrique | Valeur |",
        "|---|---|",
        f"| Cycles complétés | {len(reports)} |",
        f"| Idées explorées | {policy.total_ideas} |",
        f"| Innovations mergées | {policy.total_merged} |",
        f"| Taux de succès | {policy.total_merged / max(1, policy.total_ideas):.0%} |",
        f"| Epsilon (exploration) | {policy.epsilon:.3f} |",
        f"| Learning rate | {policy.learning_rate:.3f} |",
        "",
    ]

    # Courbe de reward
    if reports:
        lines.append("## Courbe de Reward")
        lines.append("")
        lines.append("```")
        max_r = max((r.best_reward for r in reports), default=1) or 1
        for r in reports:
            bar_len = 40
            bar_fill = int((r.best_reward / max_r) * bar_len) if max_r > 0 else 0
            bar = "█" * bar_fill + "░" * (bar_len - bar_fill)
            lines.append(f"  C{r.cycle_id:03d} [{bar}] {r.best_reward:.4f} {r.verdict}")
        lines.append("```")
        lines.append("")

    # Policy apprise
    lines.append("## Policy apprise (Reinforcement)")
    lines.append("")
    if policy.source_weights:
        lines.append("### Sources (top → bottom)")
        lines.append("")
        sorted_src = sorted(policy.source_weights.items(),
                            key=lambda x: x[1], reverse=True)
        for s, w in sorted_src:
            bar_fill = int(w * 100)
            lines.append(f"- **{s}**: `{'█' * bar_fill}{'░' * (10 - min(10, bar_fill))}` {w:.4f}")
        lines.append("")

    if policy.domain_weights:
        lines.append("### Domaines (top → bottom)")
        lines.append("")
        sorted_dom = sorted(policy.domain_weights.items(),
                            key=lambda x: x[1], reverse=True)
        for d, w in sorted_dom:
            lines.append(f"- **{d}**: {w:.4f}")
        lines.append("")

    # Innovations sélectionnées
    winners = [m for m in memory if m.get("merged")]
    if winners:
        lines.append("## Innovations sélectionnées")
        lines.append("")
        lines.append("| # | Domaine | Titre | Reward | Cycle |")
        lines.append("|---|---|---|---|---|")
        for i, w in enumerate(winners[-20:], 1):
            lines.append(f"| {i} | {w.get('domain', '?')} | "
                         f"{w.get('title', '?')[:50]} | "
                         f"{w.get('reward', 0):.3f} | "
                         f"{w.get('cycle_id', '?')} |")
        lines.append("")

    # Fossils / rejetées
    rejects = [m for m in memory if not m.get("merged")]
    if rejects:
        lines.append(f"## Fossil Record ({len(rejects)} idées rejetées)")
        lines.append("")
        # Distribution par raison
        domains = defaultdict(int)
        for r in rejects:
            domains[r.get("domain", "?")] += 1
        for d, c in sorted(domains.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- {d}: {c} rejetée(s)")
        lines.append("")

    output = "\n".join(lines)

    if args.json:
        print(json.dumps({"dashboard": output}, indent=2, ensure_ascii=False))
    else:
        # Write to file
        dash_path = _rnd_dir(project_root) / "DASHBOARD.md"
        _ensure_dirs(project_root)
        dash_path.write_text(output, encoding="utf-8")
        print(output)
        print(f"\n💾 Dashboard sauvé dans {dash_path}")
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    """Commande: ajuster les paramètres du moteur."""
    project_root = Path(args.project_root).resolve()
    policy = load_policy(project_root)

    changed = False
    if args.epsilon is not None:
        old = policy.epsilon
        policy.epsilon = max(0.0, min(1.0, args.epsilon))
        print(f"  Epsilon: {old:.3f} → {policy.epsilon:.3f}")
        changed = True
    if args.learning_rate is not None:
        old = policy.learning_rate
        policy.learning_rate = max(0.001, min(1.0, args.learning_rate))
        print(f"  Learning rate: {old:.3f} → {policy.learning_rate:.3f}")
        changed = True

    if changed:
        save_policy(project_root, policy)
        print("  ✅ Policy mise à jour")
    else:
        print(f"  Epsilon: {policy.epsilon:.3f}")
        print(f"  Learning rate: {policy.learning_rate:.3f}")
        print("  (utiliser --epsilon ou --learning-rate pour modifier)")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Commande: reset du moteur (garde l'historique)."""
    project_root = Path(args.project_root).resolve()

    policy = Policy.default()
    save_policy(project_root, policy)

    print("  🔄 Policy remise à zéro (poids uniformes)")
    print("  📜 L'historique et la mémoire sont préservés")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    """Commande: ensemencer la mémoire pour alimenter les sources réelles.

    Analyse le projet et crée :
      - Des idées incubateur à partir des gaps détectés
      - Des phéromones stigmergy à partir des patterns
      - Des entrées mémoire à partir de l'état du projet
    """
    project_root = Path(args.project_root).resolve()
    _ensure_dirs(project_root)
    seeded = 0

    # 1. Seed l'incubateur avec des idées gap-driven
    mod_incubator = _load_tool("incubator")
    if mod_incubator is not None:
        gaps = _gap_driven_ideas(project_root, max_ideas=10)
        try:
            all_ideas = mod_incubator.load_incubator(project_root)
            existing_titles = {i.title.lower() for i in all_ideas}
            for gap in gaps:
                if gap["title"].lower() not in existing_titles:
                    if hasattr(mod_incubator, "add_idea"):
                        mod_incubator.add_idea(
                            project_root,
                            title=gap["title"],
                            description=gap["description"],
                            domain=gap.get("domain", "tools"),
                        )
                        seeded += 1
        except Exception as _exc:
            _log.debug("Exception suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues
        if not args.json:
            print(f"  🌱 Incubateur: {seeded} idées ensemencées")

    # 2. Seed stigmergy avec des phéromones OPPORTUNITY
    mod_stigmergy = _load_tool("stigmergy")
    stig_count = 0
    if mod_stigmergy is not None:
        health = _snapshot_project_health(project_root)
        opportunities = []

        if health.get("test_ratio", 1) < 0.5:
            opportunities.append(
                f"NEED: Ratio tests/outils faible ({health['test_ratio']:.0%}). "
                f"Augmenter la couverture."
            )
        if health.get("harmony_high", 0) > 0:
            opportunities.append(
                f"NEED: {health['harmony_high']} dissonances HIGH détectées. "
                f"Résoudre les conflits architecturaux."
            )
        if health.get("antifragile_score", 100) < 60:
            opportunities.append(
                f"OPPORTUNITY: Score antifragile bas ({health['antifragile_score']}). "
                f"Renforcer la résilience."
            )

        # Déposer les phéromones
        for opp_text in opportunities:
            try:
                if hasattr(mod_stigmergy, "deposit_pheromone"):
                    ptype = "NEED" if opp_text.startswith("NEED") else "OPPORTUNITY"
                    mod_stigmergy.deposit_pheromone(
                        project_root, ptype=ptype, text=opp_text,
                    )
                    stig_count += 1
            except Exception as _exc:
                _log.debug("Exception suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues
        if not args.json:
            print(f"  🐜 Stigmergy: {stig_count} phéromones déposées")

    # 3. Seed la mémoire R&D avec des learnings de baseline
    memory = load_memory(project_root)
    health = _snapshot_project_health(project_root)
    baseline_learnings = [
        {
            "title": "Baseline: état du projet au moment du seed",
            "description": f"Health snapshot: {json.dumps(health)}",
            "source": "seed",
            "domain": "meta",
            "action": "add",
            "merged": False,
            "reward": 0.0,
            "cycle_id": 0,
            "created_at": datetime.now().isoformat(),
        },
    ]

    existing_titles = {m.get("title", "").lower() for m in memory}
    seed_mem = 0
    for learning in baseline_learnings:
        if learning["title"].lower() not in existing_titles:
            memory.append(learning)
            seed_mem += 1
    if seed_mem > 0:
        save_memory(project_root, memory)
    if not args.json:
        print(f"  🧠 Mémoire: {seed_mem} entrée(s) baseline")

    total = seeded + stig_count + seed_mem
    if args.json:
        print(json.dumps({
            "seeded": total,
            "incubator": seeded,
            "stigmergy": stig_count,
            "memory": seed_mem,
            "health": health,
        }, indent=2, ensure_ascii=False))
    else:
        print(f"\n  ✅ Total ensemencé: {total} entrées")
        print("  Relancer 'cycle' ou 'train' pour utiliser les nouvelles graines.")
    return 0


def _generate_prototype(idea: Idea, project_root: Path) -> Path | None:
    """Génère un squelette Python pour une idée d'innovation.

    Crée un fichier minimal mais fonctionnel dans .grimoire-rnd/prototypes/
    suivant le pattern Grimoire : argparse, --project-root, --json, stdlib only.
    Retourne le chemin du fichier généré, ou None si non applicable.
    """
    if idea.domain not in ("tools", "meta", "testing", "integration"):
        return None  # Seuls certains domaines produisent des prototypes Python

    proto_dir = _rnd_dir(project_root) / PROTOTYPE_DIR
    proto_dir.mkdir(parents=True, exist_ok=True)

    slug = idea.id.lower().replace(" ", "-")
    tool_name = f"proto-{slug}"
    proto_path = proto_dir / f"{tool_name}.py"

    # Ne pas écraser un prototype existant
    if proto_path.exists():
        return proto_path

    # Description nettoyée pour le docstring
    desc = idea.description.replace('"""', "'''")[:200]
    title_clean = idea.title.replace('"', "'")[:80]

    skeleton = f'''#!/usr/bin/env python3
"""
{tool_name}.py — Prototype auto-généré par R&D Engine v{VERSION}
{'=' * 60}

{title_clean}

{desc}

Origine: {idea.source} | Domaine: {idea.domain} | Action: {idea.action}
Cycle: {idea.cycle_id} | Score: {idea.total_score:.3f} | Reward: {idea.reward:.3f}

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def analyze(project_root: Path) -> dict[str, Any]:
    """Analyse principale — à implémenter."""
    results: dict[str, Any] = {{
        "status": "prototype",
        "idea": "{title_clean}",
        "domain": "{idea.domain}",
        "action": "{idea.action}",
    }}

    # Logique métier à compléter lors de l'implémentation
    # Basé sur: {desc[:100]}

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="{title_clean}")
    parser.add_argument("--project-root", required=True, help="Racine du projet")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    results = analyze(project_root)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print(f"\\n🔬 Prototype: {title_clean}")
        for k, v in results.items():
            print(f"  {{k}}: {{v}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

    try:
        proto_path.write_text(skeleton, encoding="utf-8")
        return proto_path
    except OSError:
        return None


def cmd_prototype(args: argparse.Namespace) -> int:
    """Commande: générer des prototypes pour les idées gagnantes."""
    project_root = Path(args.project_root).resolve()
    _ensure_dirs(project_root)
    memory = load_memory(project_root)

    # Filtrer les idées éligibles
    if args.idea_id:
        # Prototype pour une idée spécifique
        candidates = [m for m in memory if m.get("id") == args.idea_id]
    else:
        # Toutes les idées mergées non encore prototypées
        proto_dir = _rnd_dir(project_root) / PROTOTYPE_DIR
        existing_protos = {f.stem for f in proto_dir.glob("*.py")} if proto_dir.exists() else set()
        candidates = [
            m for m in memory
            if m.get("merged") and f"proto-{m.get('id', '').lower().replace(' ', '-')}"
            not in existing_protos
        ]

    if not candidates:
        if not args.json:
            print("  Aucune idée éligible pour prototypage.")
        return 0

    generated = []
    for m in candidates:
        idea = Idea.from_dict(m)
        proto_path = _generate_prototype(idea, project_root)
        if proto_path is not None:
            generated.append({
                "idea_id": idea.id,
                "title": idea.title,
                "prototype": str(proto_path.relative_to(project_root)),
            })

    if args.json:
        print(json.dumps({"prototypes": generated}, indent=2, ensure_ascii=False))
    else:
        print(f"\n  🏗️  {len(generated)} prototype(s) générés:")
        for g in generated:
            print(f"    • {g['idea_id']}: {g['prototype']}")
        if generated:
            print(f"\n  Fichiers dans {_rnd_dir(project_root) / PROTOTYPE_DIR}/")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    """Commande: afficher la santé du projet (closed-loop metrics)."""
    project_root = Path(args.project_root).resolve()
    health = _snapshot_project_health(project_root)

    if args.json:
        print(json.dumps(health, indent=2, ensure_ascii=False))
    else:
        print("\n  🏥 Santé du projet")
        print(f"  ├── Outils: {health['tool_count']}")
        print(f"  ├── Tests: {health['test_count']} "
              f"(ratio: {health['test_ratio']:.0%})")
        print(f"  ├── Docs: {health['doc_count']}")
        print(f"  ├── Harmony: {health['harmony_dissonances']} dissonances "
              f"({health['harmony_high']} HIGH)")
        print(f"  ├── Antifragile: {health['antifragile_score']}")
        print(f"  └── Score composite: {health['composite_score']:.1f}/100")

    # Comparer avec le dernier cycle si disponible
    reports = load_cycle_reports(project_root, last_n=1)
    if reports and not args.json:
        last = reports[-1]
        if last.health_before:
            delta = _compute_health_delta(last.health_before, health)
            trend = "↑" if delta > 0 else "↓" if delta < 0 else "→"
            print(f"\n  Tendance depuis cycle #{last.cycle_id}: "
                  f"{trend} (Δ{'+' if delta > 0 else ''}{delta:.3f})")
    return 0


# ── Main ─────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grimoire R&D Innovation Engine — Reinforcement Learning",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  %(prog)s --project-root . cycle                     # 1 cycle complet
  %(prog)s --project-root . train --epochs 5          # 5 cycles intensifs
  %(prog)s --project-root . train --epochs 10 --auto-stop  # Avec auto-stop
  %(prog)s --project-root . seed                      # Ensemencer les sources
  %(prog)s --project-root . health                    # Santé du projet
  %(prog)s --project-root . prototype                 # Générer des squelettes
  %(prog)s --project-root . dashboard                 # Tableau de bord
  %(prog)s --project-root . status                    # État du moteur
""",
    )
    parser.add_argument("--project-root", required=True,
                        help="Racine du projet Grimoire")
    parser.add_argument("--json", action="store_true",
                        help="Sortie JSON")

    subs = parser.add_subparsers(dest="command")

    # cycle
    p_cycle = subs.add_parser("cycle", help="Un cycle d'innovation complet")
    p_cycle.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                         help=f"Nombre max d'idées (default: {DEFAULT_BUDGET})")
    p_cycle.add_argument("--quick", action="store_true",
                         help="Mode rapide (harvest+evaluate seulement)")
    p_cycle.set_defaults(func=cmd_cycle)

    # train
    p_train = subs.add_parser("train",
                              help="N cycles intensifs (reinforcement learning)")
    p_train.add_argument("--epochs", type=int, default=5,
                         help="Nombre de cycles (default: 5)")
    p_train.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                         help=f"Idées par cycle (default: {DEFAULT_BUDGET})")
    p_train.add_argument("--auto-stop", action="store_true",
                         help="Arrêt automatique si convergence")
    p_train.set_defaults(func=cmd_train)

    # harvest
    p_harvest = subs.add_parser("harvest", help="Phase 1 seule : récolte d'idées")
    p_harvest.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    p_harvest.set_defaults(func=cmd_harvest)

    # evaluate
    p_eval = subs.add_parser("evaluate", help="Harvest + Phase 2 : scoring")
    p_eval.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    p_eval.set_defaults(func=cmd_evaluate)

    # status
    p_status = subs.add_parser("status", help="État du moteur R&D")
    p_status.set_defaults(func=cmd_status)

    # history
    p_hist = subs.add_parser("history", help="Historique des cycles")
    p_hist.set_defaults(func=cmd_history)

    # dashboard
    p_dash = subs.add_parser("dashboard", help="Tableau de bord markdown")
    p_dash.set_defaults(func=cmd_dashboard)

    # tune
    p_tune = subs.add_parser("tune", help="Ajuster les paramètres")
    p_tune.add_argument("--epsilon", type=float, default=None,
                        help="Taux d'exploration (0.0-1.0)")
    p_tune.add_argument("--learning-rate", type=float, default=None,
                        dest="learning_rate",
                        help="Vitesse d'apprentissage (0.001-1.0)")
    p_tune.set_defaults(func=cmd_tune)

    # reset
    p_reset = subs.add_parser("reset", help="Reset policy (garde historique)")
    p_reset.set_defaults(func=cmd_reset)

    # seed
    p_seed = subs.add_parser("seed",
                             help="Ensemencer les sources (incubateur, stigmergy, mémoire)")
    p_seed.set_defaults(func=cmd_seed)

    # prototype
    p_proto = subs.add_parser("prototype",
                              help="Générer des squelettes Python pour les idées gagnantes")
    p_proto.add_argument("--idea-id", default=None,
                         help="ID d'une idée spécifique (ex: RND-0001-01)")
    p_proto.set_defaults(func=cmd_prototype)

    # health
    p_health = subs.add_parser("health",
                               help="Santé du projet (closed-loop metrics)")
    p_health.set_defaults(func=cmd_health)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
