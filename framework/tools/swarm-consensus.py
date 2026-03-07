#!/usr/bin/env python3
"""
swarm-consensus.py — Consensus en essaim BMAD.
================================================

Mécanisme de prise de décision collective multi-agents :

  1. `vote`      — Soumettre un vote sur une proposition
  2. `estimate`  — Planning poker automatique multi-agents
  3. `consensus` — Calculer le consensus actuel
  4. `history`   — Historique des décisions
  5. `report`    — Rapport de consensus

Modes :
  - MAJORITY   : Majorité simple (>50%)
  - SUPERMAJOR : Supermajorité (>66%)
  - UNANIMOUS  : Unanimité requise
  - SWARM      : Convergence itérative (poids par expertise)

Pour l'estimation :
  - Fibonacci modifiée (1, 2, 3, 5, 8, 13, 21)
  - Détection d'outliers
  - Écart-type + intervalle de confiance

Usage :
  python3 swarm-consensus.py vote --topic "Utiliser React" --votes '{"analyst":true,"architect":true,"dev":false}'
  python3 swarm-consensus.py estimate --task "Auth module" --estimates '{"analyst":5,"architect":8,"dev":5,"qa":3}'
  python3 swarm-consensus.py consensus --topic "Utiliser React"
  python3 swarm-consensus.py history --project-root .
  python3 swarm-consensus.py report --project-root .

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("grimoire.swarm_consensus")

# ── Constantes ────────────────────────────────────────────────────────────────

VERSION = "1.0.0"
CONSENSUS_LOG = "swarm-consensus.json"
FIBONACCI_SCALE = [1, 2, 3, 5, 8, 13, 21]

CONSENSUS_MODES = {
    "MAJORITY": {"threshold": 0.5, "description": "Majorité simple (>50%)"},
    "SUPERMAJOR": {"threshold": 0.66, "description": "Supermajorité (>66%)"},
    "UNANIMOUS": {"threshold": 1.0, "description": "Unanimité requise"},
    "SWARM": {"threshold": 0.6, "description": "Convergence pondérée par expertise"},
}

# Poids d'expertise par domaine
AGENT_WEIGHTS = {
    "analyst": {"business": 0.9, "technical": 0.4, "ux": 0.5, "quality": 0.5},
    "architect": {"business": 0.3, "technical": 0.9, "ux": 0.3, "quality": 0.7},
    "dev": {"business": 0.3, "technical": 0.8, "ux": 0.3, "quality": 0.6},
    "pm": {"business": 0.8, "technical": 0.3, "ux": 0.6, "quality": 0.5},
    "qa": {"business": 0.3, "technical": 0.6, "ux": 0.4, "quality": 0.9},
    "ux-designer": {"business": 0.5, "technical": 0.3, "ux": 0.9, "quality": 0.6},
    "sm": {"business": 0.6, "technical": 0.4, "ux": 0.4, "quality": 0.7},
}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Vote:
    agent: str
    value: bool      # True = pour, False = contre
    weight: float = 1.0
    comment: str = ""

@dataclass
class VoteResult:
    topic: str
    mode: str = "MAJORITY"
    votes: list[Vote] = field(default_factory=list)
    consensus: bool = False
    ratio: float = 0.0
    detail: str = ""

@dataclass
class Estimate:
    agent: str
    value: int
    weight: float = 1.0

@dataclass
class EstimateResult:
    task: str
    estimates: list[Estimate] = field(default_factory=list)
    mean: float = 0.0
    median: float = 0.0
    std_dev: float = 0.0
    weighted_mean: float = 0.0
    fibonacci_nearest: int = 0
    outliers: list[str] = field(default_factory=list)
    confidence_interval: tuple[float, float] = (0.0, 0.0)


# ── Vote Engine ──────────────────────────────────────────────────────────────

def process_votes(topic: str, votes_dict: dict[str, bool],
                  mode: str = "MAJORITY", domain: str = "technical") -> VoteResult:
    """Traite les votes et calcule le consensus."""
    result = VoteResult(topic=topic, mode=mode)

    for agent, value in votes_dict.items():
        weight = 1.0
        if mode == "SWARM" and agent in AGENT_WEIGHTS:
            weight = AGENT_WEIGHTS[agent].get(domain, 0.5)
        result.votes.append(Vote(agent=agent, value=value, weight=weight))

    if not result.votes:
        return result

    if mode == "SWARM":
        total_weight = sum(v.weight for v in result.votes)
        weighted_for = sum(v.weight for v in result.votes if v.value)
        result.ratio = weighted_for / total_weight if total_weight > 0 else 0
    else:
        total = len(result.votes)
        favor = sum(1 for v in result.votes if v.value)
        result.ratio = favor / total

    threshold = CONSENSUS_MODES.get(mode, CONSENSUS_MODES["MAJORITY"])["threshold"]
    result.consensus = result.ratio >= threshold
    result.detail = f"{result.ratio:.0%} pour (seuil: {threshold:.0%})"

    return result


# ── Estimate Engine ──────────────────────────────────────────────────────────

def process_estimates(task: str, estimates_dict: dict[str, int],
                      domain: str = "technical") -> EstimateResult:
    """Traite les estimations multi-agents."""
    result = EstimateResult(task=task)

    for agent, value in estimates_dict.items():
        weight = AGENT_WEIGHTS.get(agent, {}).get(domain, 0.5)
        result.estimates.append(Estimate(agent=agent, value=value, weight=weight))

    if not result.estimates:
        return result

    values = [e.value for e in result.estimates]
    n = len(values)

    # Mean
    result.mean = sum(values) / n

    # Median
    sorted_vals = sorted(values)
    if n % 2 == 0:
        result.median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    else:
        result.median = sorted_vals[n // 2]

    # Std dev
    if n > 1:
        variance = sum((v - result.mean) ** 2 for v in values) / (n - 1)
        result.std_dev = math.sqrt(variance)

    # Weighted mean
    total_weight = sum(e.weight for e in result.estimates)
    result.weighted_mean = sum(e.value * e.weight for e in result.estimates) / total_weight if total_weight > 0 else result.mean

    # Nearest Fibonacci
    result.fibonacci_nearest = min(FIBONACCI_SCALE, key=lambda f: abs(f - result.weighted_mean))

    # Outliers (> 2 std dev from mean)
    if result.std_dev > 0:
        for e in result.estimates:
            if abs(e.value - result.mean) > 2 * result.std_dev:
                result.outliers.append(f"{e.agent} ({e.value})")

    # Confidence interval (95% — approximation t-distribution)
    if n > 1:
        margin = 1.96 * result.std_dev / math.sqrt(n)
        result.confidence_interval = (result.mean - margin, result.mean + margin)

    return result


# ── History ──────────────────────────────────────────────────────────────────

def load_history(project_root: Path) -> list[dict]:
    logfile = project_root / "_bmad" / "_memory" / CONSENSUS_LOG
    if logfile.exists():
        try:
            return json.loads(logfile.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as _exc:
            _log.debug("json.JSONDecodeError, OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues
    return []


def save_entry(project_root: Path, entry: dict) -> None:
    logfile = project_root / "_bmad" / "_memory" / CONSENSUS_LOG
    logfile.parent.mkdir(parents=True, exist_ok=True)
    history = load_history(project_root)
    entry["timestamp"] = datetime.now().isoformat()
    history.append(entry)
    history = history[-100:]
    logfile.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Formatters ───────────────────────────────────────────────────────────────

def format_vote(result: VoteResult) -> str:
    icon = "✅" if result.consensus else "❌"
    lines = [
        f"🗳️ Vote : {result.topic}",
        f"   Mode : {result.mode} — {CONSENSUS_MODES.get(result.mode, {}).get('description', '')}",
        f"   Résultat : {icon} {result.detail}",
        "",
    ]
    for v in result.votes:
        vote_icon = "👍" if v.value else "👎"
        weight_info = f" (poids: {v.weight:.1f})" if result.mode == "SWARM" else ""
        lines.append(f"   {vote_icon} {v.agent}{weight_info}")
    return "\n".join(lines)


def format_estimate(result: EstimateResult) -> str:
    lines = [
        f"📊 Estimation : {result.task}",
        f"   Moyenne : {result.mean:.1f}",
        f"   Médiane : {result.median:.1f}",
        f"   Écart-type : {result.std_dev:.1f}",
        f"   Moyenne pondérée : {result.weighted_mean:.1f}",
        f"   ▶ Fibonacci recommandé : {result.fibonacci_nearest}",
        f"   IC 95% : [{result.confidence_interval[0]:.1f} — {result.confidence_interval[1]:.1f}]",
        "",
    ]
    if result.outliers:
        lines.append(f"   ⚠️ Outliers : {', '.join(result.outliers)}")
        lines.append("")
    for e in result.estimates:
        bar = "█" * e.value
        lines.append(f"   {e.agent:15s} {bar} {e.value} (w={e.weight:.1f})")
    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_vote(args: argparse.Namespace) -> int:
    try:
        votes_dict = json.loads(args.votes)
    except json.JSONDecodeError:
        print("❌ Format JSON invalide pour --votes")
        return 1

    result = process_votes(args.topic, votes_dict, args.mode, args.domain)
    save_entry(Path(args.project_root).resolve(), {
        "type": "vote", "topic": args.topic,
        "consensus": result.consensus, "ratio": result.ratio,
    })

    if args.json:
        print(json.dumps({"topic": result.topic, "consensus": result.consensus,
                          "ratio": result.ratio, "mode": result.mode},
                         indent=2, ensure_ascii=False))
    else:
        print(format_vote(result))
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    try:
        estimates_dict = json.loads(args.estimates)
    except json.JSONDecodeError:
        print("❌ Format JSON invalide pour --estimates")
        return 1

    result = process_estimates(args.task, estimates_dict, args.domain)
    save_entry(Path(args.project_root).resolve(), {
        "type": "estimate", "task": args.task,
        "fibonacci": result.fibonacci_nearest, "std_dev": result.std_dev,
    })

    if args.json:
        print(json.dumps({"task": result.task, "fibonacci": result.fibonacci_nearest,
                          "mean": result.mean, "std_dev": result.std_dev,
                          "weighted_mean": result.weighted_mean},
                         indent=2, ensure_ascii=False))
    else:
        print(format_estimate(result))
    return 0


def cmd_consensus(args: argparse.Namespace) -> int:
    history = load_history(Path(args.project_root).resolve())
    topic_entries = [e for e in history if e.get("topic") == args.topic]
    if not topic_entries:
        print(f"📜 Aucun vote trouvé pour « {args.topic} »")
        return 0
    latest = topic_entries[-1]
    if args.json:
        print(json.dumps(latest, indent=2, ensure_ascii=False))
    else:
        icon = "✅" if latest.get("consensus") else "❌"
        print(f"🗳️ Dernier consensus pour « {args.topic} » : {icon} ({latest.get('ratio', 0):.0%})")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    history = load_history(Path(args.project_root).resolve())
    if args.json:
        print(json.dumps(history[-20:], indent=2, ensure_ascii=False))
    else:
        print(f"📜 Historique consensus ({len(history)} entrées)\n")
        for entry in history[-10:]:
            ts = entry.get("timestamp", "?")[:19]
            typ = entry.get("type", "?")
            if typ == "vote":
                icon = "✅" if entry.get("consensus") else "❌"
                print(f"   {ts} [vote] {entry.get('topic', '?')} → {icon}")
            elif typ == "estimate":
                print(f"   {ts} [estimate] {entry.get('task', '?')} → Fib {entry.get('fibonacci', '?')}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    history = load_history(Path(args.project_root).resolve())
    votes = [e for e in history if e.get("type") == "vote"]
    estimates = [e for e in history if e.get("type") == "estimate"]
    if args.json:
        print(json.dumps({"total_decisions": len(history),
                          "votes": len(votes), "estimates": len(estimates)},
                         indent=2, ensure_ascii=False))
    else:
        print("📊 Rapport consensus")
        print(f"   Décisions : {len(history)}")
        print(f"   Votes : {len(votes)}")
        print(f"   Estimations : {len(estimates)}")
        if votes:
            consensus_rate = sum(1 for v in votes if v.get("consensus")) / len(votes)
            print(f"   Taux de consensus : {consensus_rate:.0%}")
    return 0


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BMAD Swarm Consensus — Décision collective multi-agents",
    )
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")

    subs = parser.add_subparsers(dest="command")

    p_vote = subs.add_parser("vote", help="Soumettre un vote")
    p_vote.add_argument("--topic", required=True, help="Sujet du vote")
    p_vote.add_argument("--votes", required=True, help='JSON {agent: true/false}')
    p_vote.add_argument("--mode", choices=list(CONSENSUS_MODES.keys()), default="MAJORITY")
    p_vote.add_argument("--domain", default="technical", help="Domaine d'expertise")
    p_vote.set_defaults(func=cmd_vote)

    p_est = subs.add_parser("estimate", help="Estimation collaborative")
    p_est.add_argument("--task", required=True, help="Tâche à estimer")
    p_est.add_argument("--estimates", required=True, help='JSON {agent: points}')
    p_est.add_argument("--domain", default="technical", help="Domaine")
    p_est.set_defaults(func=cmd_estimate)

    p_cons = subs.add_parser("consensus", help="Consulter un consensus")
    p_cons.add_argument("--topic", required=True)
    p_cons.set_defaults(func=cmd_consensus)

    subs.add_parser("history", help="Historique").set_defaults(func=cmd_history)
    subs.add_parser("report", help="Rapport").set_defaults(func=cmd_report)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
