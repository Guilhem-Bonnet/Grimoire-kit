#!/usr/bin/env python3
"""
rnd_engine.py — R&D Innovation Engine : moteur d'évaluation et RL.
═══════════════════════════════════════════════════════════════════

Phases 2-7 : évaluation, challenge adversarial, simulation,
quality gates, sélection, convergence et apprentissage.

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from rnd_core import (
    CONDITIONAL_THRESHOLD,
    CONVERGENCE_WINDOW,
    DEFAULT_BUDGET,
    DEFAULT_EPOCHS,
    GO_THRESHOLD,
    MIN_REJECT_RATIO,
    MIN_REWARD_DELTA,
    CycleReport,
    Idea,
    Policy,
    TrainReport,
    _ensure_dirs,
    _load_tool,
    load_cycle_reports,
    load_memory,
    load_policy,
    next_cycle_id,
    save_cycle_report,
    save_memory,
    save_policy,
)
from rnd_harvest import harvest

# ── Phase 2 : EVALUATE ──────────────────────────────────────────

def evaluate(ideas: list[Idea], project_root: Path,
             policy: Policy) -> list[Idea]:
    """Phase 2 : Scoring multi-dimensionnel automatique."""
    tools_dir = project_root / "framework" / "tools"
    existing_tools = {f.stem for f in tools_dir.glob("*.py")} if tools_dir.exists() else set()
    memory = load_memory(project_root)

    for idea in ideas:
        scores: dict[str, float] = {}

        action_feas = {"add": 0.7, "improve": 0.8, "simplify": 0.9,
                       "merge": 0.6, "split": 0.5, "remove": 0.95}
        scores["feasibility"] = action_feas.get(idea.action, 0.5)

        domain_impact = {"architecture": 0.9, "tools": 0.7, "resilience": 0.8,
                         "meta": 0.85, "testing": 0.6, "documentation": 0.5,
                         "workflows": 0.7, "agents": 0.75, "integration": 0.65,
                         "performance": 0.7}
        scores["impact"] = domain_impact.get(idea.domain, 0.5)

        title_words = set(idea.title.lower().split())
        overlaps = sum(1 for t in existing_tools if t.replace("-", " ").replace("_", " ")
                       in idea.title.lower() or any(w in t for w in title_words if len(w) > 3))
        scores["uniqueness"] = max(0.1, 1.0 - overlaps * 0.3)

        mentions = sum(1 for t in existing_tools
                       if t in idea.description.lower())
        scores["synergy"] = min(1.0, 0.3 + mentions * 0.15)

        action_risk = {"add": 0.85, "improve": 0.7, "simplify": 0.9,
                       "merge": 0.5, "split": 0.6, "remove": 0.3}
        scores["risk_inverse"] = action_risk.get(idea.action, 0.5)

        domain_history = [m for m in memory if m.get("domain") == idea.domain]
        novelty = max(0.2, 1.0 - len(domain_history) * 0.05)
        scores["novelty"] = min(1.0, novelty)

        total = 0.0
        for dim, weight in policy.scoring_weights.items():
            total += scores.get(dim, 0.5) * weight
        total = min(1.0, max(0.0, total))

        idea.scores = scores
        idea.total_score = round(total, 4)

    ideas.sort(key=lambda x: x.total_score, reverse=True)
    return ideas


# ── Phase 3 : CHALLENGE ─────────────────────────────────────────

def challenge(ideas: list[Idea], project_root: Path) -> list[Idea]:
    """Phase 3 : Adversarial red-team durci — pre-mortem automatique."""
    memory = load_memory(project_root)

    batch_scores = sorted(i.total_score for i in ideas)
    median_score = batch_scores[len(batch_scores) // 2] if batch_scores else 0.5

    for idea in ideas:
        notes: list[str] = []
        go_score = idea.total_score

        # Check 1: Duplication avec l'historique
        past_titles = {m.get("title", "").lower() for m in memory}
        if idea.title.lower() in past_titles:
            notes.append("⚠️ Idée déjà explorée dans un cycle précédent")
            go_score -= 0.15

        # Check 2: Domaine surinvesti
        domain_count = sum(1 for m in memory
                           if m.get("domain") == idea.domain and m.get("merged"))
        if domain_count > 3:
            penalty = min(0.25, domain_count * 0.03)
            notes.append(f"⚠️ Domaine '{idea.domain}' saturé ({domain_count} innovations, -{penalty:.2f})")
            go_score -= penalty

        # Check 3: Pattern d'échec récurrent
        failed_in_domain = sum(1 for m in memory
                               if m.get("domain") == idea.domain
                               and not m.get("merged") and m.get("reward", 0) < 0)
        if failed_in_domain > 2:
            notes.append(f"🔴 Pattern d'échec dans '{idea.domain}' ({failed_in_domain} échecs)")
            go_score -= 0.20

        # Check 4: Complexité vs faisabilité
        if idea.action in ("split", "merge") and idea.scores.get("feasibility", 1) < 0.6:
            notes.append("⚠️ Action complexe avec faible faisabilité")
            go_score -= 0.10

        # Check 5: Pre-mortem
        if idea.scores.get("risk_inverse", 1) < 0.4:
            notes.append("🔴 Pre-mortem: risque élevé de régression")
            go_score -= 0.05
        if idea.scores.get("uniqueness", 1) < 0.3:
            notes.append("⚠️ Pre-mortem: trop proche d'un outil existant")
            go_score -= 0.05

        # Check 6: Sous la médiane
        if idea.total_score < median_score * 0.85:
            gap = median_score - idea.total_score
            notes.append(f"🔴 Score ({idea.total_score:.3f}) < 85% médiane ({median_score:.3f})")
            go_score -= gap * 0.5

        # Check 7: Taux de succès historique de la source
        source_ideas = [m for m in memory if m.get("source") == idea.source]
        if len(source_ideas) >= 3:
            source_success = sum(1 for m in source_ideas if m.get("merged")) / len(source_ideas)
            if source_success < 0.2:
                notes.append(f"⚠️ Source '{idea.source}' historiquement faible ({source_success:.0%})")
                go_score -= 0.10

        # Check 8: Pénalité chaîne de mutations
        if idea.mutation_depth > 1:
            chain_penalty = min(0.30, (idea.mutation_depth - 1) * 0.12)
            notes.append(
                f"⚠️ Chaîne de mutations profondeur {idea.mutation_depth} "
                f"(-{chain_penalty:.2f})"
            )
            go_score -= chain_penalty

        # Check 9: Actionnabilité
        _mut_prefixes = ("transposer '", "escalader:", "inverser:", "fusionner '")
        title_lower = idea.title.lower()
        if idea.source == "mutation":
            nesting = sum(1 for p in _mut_prefixes if p in title_lower)
            if nesting > 1:
                notes.append("🔴 Titre non actionnable — mutation imbriquée")
                go_score -= 0.20
            elif nesting == 1 and idea.mutation_depth > 0:
                for p in _mut_prefixes:
                    idx = title_lower.find(p)
                    if idx >= 0:
                        rest = title_lower[idx + len(p):]
                        if any(p2 in rest for p2 in _mut_prefixes):
                            notes.append("⚠️ Mutation de mutation détectée dans le titre")
                            go_score -= 0.15
                        break

        # Verdict
        idea.challenge_notes = notes
        if go_score >= GO_THRESHOLD:
            idea.challenge_result = "GO"
        elif go_score >= CONDITIONAL_THRESHOLD:
            idea.challenge_result = "CONDITIONAL"
        else:
            idea.challenge_result = "NO-GO"

    # Contrainte dure : au moins MIN_REJECT_RATIO d'idées rejetées
    go_count = sum(1 for i in ideas if i.challenge_result in ("GO", "CONDITIONAL"))
    min_rejects = max(1, int(len(ideas) * MIN_REJECT_RATIO))
    if go_count > len(ideas) - min_rejects:
        conditionals = sorted(
            [i for i in ideas if i.challenge_result == "CONDITIONAL"],
            key=lambda x: x.total_score,
        )
        nogo_count = sum(1 for i in ideas if i.challenge_result == "NO-GO")
        for idea in conditionals:
            if nogo_count >= min_rejects:
                break
            idea.challenge_result = "NO-GO"
            idea.challenge_notes.append("⚫ Rejeté par quota minimum de filtre (20%)")
            nogo_count += 1

        if nogo_count < min_rejects:
            weakest_go = sorted(
                [i for i in ideas if i.challenge_result == "GO"],
                key=lambda x: x.total_score,
            )
            for idea in weakest_go:
                if nogo_count >= min_rejects:
                    break
                idea.challenge_result = "NO-GO"
                idea.challenge_notes.append("⚫ Rejeté par quota (GO le plus faible)")
                nogo_count += 1

    return ideas


# ── Phase 4 : SIMULATE ──────────────────────────────────────────

def simulate(ideas: list[Idea], project_root: Path) -> list[Idea]:
    """Phase 4 : Simulation d'impact via digital-twin."""
    mod = _load_tool("digital-twin")

    for idea in ideas:
        if idea.challenge_result == "NO-GO":
            continue

        if mod is not None:
            try:
                entities, edges = mod.scan_project(project_root)
                action_map = {"add": "add", "improve": "modify",
                              "remove": "remove", "simplify": "modify",
                              "merge": "modify", "split": "add"}
                sim_action = action_map.get(idea.action, "modify")
                change = mod.SimulationChange(
                    action=sim_action,
                    target_kind="tool",
                    target_name=idea.title[:40],
                )
                if hasattr(mod, "simulate_impact"):
                    result = mod.simulate_impact(change, entities, edges)
                    idea.simulation_risk = getattr(result, "risk_score", 0.0)
                    idea.simulation_impacts = len(getattr(result, "direct_impacts", []))
            except Exception:
                idea.simulation_risk = 0.3
                idea.simulation_impacts = 0
        else:
            risk_map = {"add": 0.2, "improve": 0.3, "remove": 0.7,
                        "simplify": 0.15, "merge": 0.5, "split": 0.4}
            idea.simulation_risk = risk_map.get(idea.action, 0.3)

    return ideas


# ── Phase 5 : IMPLEMENT (quality gates check) ───────────────────

def check_quality_gates(project_root: Path) -> dict[str, Any]:
    """Phase 5 : Vérification des quality gates du projet."""
    gates: dict[str, Any] = {}

    smoke_test = project_root / "tests" / "smoke-test.sh"
    gates["smoke_test_exists"] = smoke_test.exists()

    tools_dir = project_root / "framework" / "tools"
    py_files = list(tools_dir.glob("*.py")) if tools_dir.exists() else []
    gates["tool_count"] = len(py_files)

    mod = _load_tool("harmony-check")
    if mod is not None:
        try:
            scan = mod.scan_project(project_root)
            high_diss = [d for d in scan.dissonances if d.severity == "HIGH"]
            gates["harmony_high_issues"] = len(high_diss)
            gates["harmony_pass"] = len(high_diss) == 0
        except Exception:
            gates["harmony_pass"] = True
    else:
        gates["harmony_pass"] = True

    mod_af = _load_tool("antifragile-score")
    if mod_af is not None:
        try:
            if hasattr(mod_af, "compute_score"):
                score = mod_af.compute_score(project_root)
                gates["antifragile_score"] = getattr(score, "total", 50)
            else:
                gates["antifragile_score"] = 50
        except Exception:
            gates["antifragile_score"] = 50
    else:
        gates["antifragile_score"] = 50

    gates["all_pass"] = gates.get("harmony_pass", True) and gates.get("antifragile_score", 0) >= 30
    return gates


# ── Phase 5b : HEALTH SNAPSHOT (closed-loop) ────────────────────

def _snapshot_project_health(project_root: Path) -> dict[str, Any]:
    """Capture un instantané de la santé du projet pour closed-loop reward."""
    health: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
    }

    tools_dir = project_root / "framework" / "tools"
    health["tool_count"] = len(list(tools_dir.glob("*.py"))) if tools_dir.exists() else 0

    tests_dir = project_root / "tests"
    health["test_count"] = len(list(tests_dir.glob("test_*.py"))) if tests_dir.exists() else 0

    docs_dir = project_root / "docs"
    health["doc_count"] = len(list(docs_dir.glob("*.md"))) if docs_dir.exists() else 0

    mod = _load_tool("harmony-check")
    if mod is not None:
        try:
            scan = mod.scan_project(project_root)
            health["harmony_dissonances"] = len(scan.dissonances)
            health["harmony_high"] = len([d for d in scan.dissonances
                                          if d.severity == "HIGH"])
        except Exception:
            health["harmony_dissonances"] = 0
            health["harmony_high"] = 0
    else:
        health["harmony_dissonances"] = 0
        health["harmony_high"] = 0

    mod_af = _load_tool("antifragile-score")
    if mod_af is not None:
        try:
            if hasattr(mod_af, "compute_score"):
                score = mod_af.compute_score(project_root)
                health["antifragile_score"] = getattr(score, "total", 50)
            else:
                health["antifragile_score"] = 50
        except Exception:
            health["antifragile_score"] = 50
    else:
        health["antifragile_score"] = 50

    if health["tool_count"] > 0:
        health["test_ratio"] = round(health["test_count"] / health["tool_count"], 3)
    else:
        health["test_ratio"] = 0.0

    h_score = 50.0
    h_score += min(20, health["tool_count"] * 0.5)
    h_score += min(10, health["test_ratio"] * 10)
    h_score -= min(15, health["harmony_high"] * 5)
    h_score += min(15, (health["antifragile_score"] - 50) * 0.3)
    health["composite_score"] = round(max(0, min(100, h_score)), 2)

    return health


def _compute_health_delta(before: dict[str, Any],
                          after: dict[str, Any]) -> float:
    """Calcule le delta de santé entre deux snapshots."""
    if not before or not after:
        return 0.0

    delta = 0.0
    comp_before = before.get("composite_score", 50)
    comp_after = after.get("composite_score", 50)
    delta += (comp_after - comp_before) / 100.0 * 2.0

    h_before = before.get("harmony_high", 0)
    h_after = after.get("harmony_high", 0)
    if h_before > h_after:
        delta += 0.1
    elif h_after > h_before:
        delta -= 0.1

    af_before = before.get("antifragile_score", 50)
    af_after = after.get("antifragile_score", 50)
    delta += (af_after - af_before) / 100.0

    return max(-1.0, min(1.0, round(delta, 4)))


# ── Phase 6 : SELECT (Tournament) ───────────────────────────────

def select_winners(ideas: list[Idea],
                   quality_gates: dict[str, Any],
                   health_delta: float = 0.0) -> list[Idea]:
    """Phase 6 : Tournament selection — seuls les meilleurs survivent."""
    candidates = [i for i in ideas if i.challenge_result in ("GO", "CONDITIONAL")]

    for idea in candidates:
        score = idea.total_score
        risk_penalty = idea.simulation_risk * 0.3
        complexity_penalty = 0.1 if idea.action in ("merge", "split") else 0.0
        gate_bonus = 0.1 if quality_gates.get("all_pass") else 0.0
        health_bonus = health_delta * 0.2

        idea.reward = round(
            score - risk_penalty - complexity_penalty + gate_bonus + health_bonus,
            4,
        )

    candidates.sort(key=lambda x: x.reward, reverse=True)

    n_winners = max(1, len(candidates) // 2)
    for i, idea in enumerate(candidates):
        idea.merged = i < n_winners

    return candidates


# ── Phase 7 : CONVERGE + LEARN ───────────────────────────────────

def update_policy(policy: Policy, ideas: list[Idea]) -> Policy:
    """Reinforcement : met à jour la policy basé sur les rewards."""
    if not ideas:
        return policy

    lr = policy.learning_rate

    winners = [i for i in ideas if i.merged and i.reward > 0]
    losers = [i for i in ideas if not i.merged or i.reward <= 0]

    for idea in winners:
        r = idea.reward
        src = idea.source
        dom = idea.domain
        act = idea.action

        if src in policy.source_weights:
            policy.source_weights[src] += lr * r
        if dom in policy.domain_weights:
            policy.domain_weights[dom] += lr * r
        if act in policy.action_weights:
            policy.action_weights[act] += lr * r

    for idea in losers:
        r = abs(idea.reward) if idea.reward != 0 else 0.1
        src = idea.source
        dom = idea.domain
        act = idea.action

        if src in policy.source_weights:
            policy.source_weights[src] = max(0.01, policy.source_weights[src] - lr * r * 0.5)
        if dom in policy.domain_weights:
            policy.domain_weights[dom] = max(0.01, policy.domain_weights[dom] - lr * r * 0.5)
        if act in policy.action_weights:
            policy.action_weights[act] = max(0.01, policy.action_weights[act] - lr * r * 0.5)

    for weights in (policy.source_weights, policy.domain_weights, policy.action_weights):
        total = sum(weights.values())
        if total > 0:
            for k in weights:
                weights[k] = round(weights[k] / total, 6)

    policy.total_ideas += len(ideas)
    policy.total_merged += len(winners)
    policy.total_rewards += sum(i.reward for i in ideas)
    policy.generations += 1

    return policy


def check_convergence(project_root: Path,
                      current_reward: float) -> tuple[str, float]:
    """Vérifie si le système a convergé."""
    reports = load_cycle_reports(project_root, last_n=CONVERGENCE_WINDOW * 2)
    if len(reports) < CONVERGENCE_WINDOW:
        return "CONTINUE", current_reward

    recent = reports[-CONVERGENCE_WINDOW:]
    recent_rewards = [r.best_reward for r in recent]
    avg_recent = sum(recent_rewards) / len(recent_rewards) if recent_rewards else 0

    if len(recent_rewards) >= 2:
        deltas = [recent_rewards[i + 1] - recent_rewards[i]
                  for i in range(len(recent_rewards) - 1)]
        avg_delta = sum(deltas) / len(deltas) if deltas else 0

        if avg_delta < -MIN_REWARD_DELTA:
            return "STOP", avg_recent
        if abs(avg_delta) < MIN_REWARD_DELTA:
            if len(reports) > CONVERGENCE_WINDOW * 2:
                return "CONSOLIDATE", avg_recent
            return "SLOW_DOWN", avg_recent
        if avg_delta > MIN_REWARD_DELTA * 2:
            return "CONTINUE", avg_recent

    if len(recent_rewards) >= 3:
        ups = sum(1 for i in range(len(recent_rewards) - 1)
                  if recent_rewards[i + 1] > recent_rewards[i])
        downs = len(recent_rewards) - 1 - ups
        if ups > 0 and downs > 0 and abs(ups - downs) <= 1:
            return "SLOW_DOWN", avg_recent

    return "CONTINUE", avg_recent


# ── Cycle principal ──────────────────────────────────────────────

def run_cycle(project_root: Path, policy: Policy,
              budget: int = DEFAULT_BUDGET,
              epoch: int = 1,
              quick: bool = False) -> CycleReport:
    """Exécute un cycle complet d'innovation (7 phases)."""
    start = time.monotonic()
    cycle_id = next_cycle_id(project_root)

    report = CycleReport(
        cycle_id=cycle_id,
        epoch=epoch,
        timestamp=datetime.now().isoformat(),
    )

    health_before = _snapshot_project_health(project_root)
    report.health_before = health_before

    # Phase 1: HARVEST
    ideas = harvest(project_root, policy, budget=budget)
    report.ideas_harvested = len(ideas)

    if not ideas:
        report.verdict = "NO_IDEAS"
        report.duration_ms = int((time.monotonic() - start) * 1000)
        return report

    # Phase 2: EVALUATE
    ideas = evaluate(ideas, project_root, policy)
    report.ideas_evaluated = len(ideas)

    if quick:
        report.ideas = [i.to_dict() for i in ideas]
        report.verdict = "QUICK_STOP"
        report.duration_ms = int((time.monotonic() - start) * 1000)
        return report

    # Phase 3: CHALLENGE
    ideas = challenge(ideas, project_root)
    go_ideas = [i for i in ideas if i.challenge_result in ("GO", "CONDITIONAL")]
    rejected = [i for i in ideas if i.challenge_result == "NO-GO"]
    report.ideas_challenged = len(ideas)
    report.ideas_go = len(go_ideas)
    report.ideas_rejected = len(rejected)

    if not go_ideas:
        report.ideas = [i.to_dict() for i in ideas]
        report.verdict = "ALL_REJECTED"
        report.duration_ms = int((time.monotonic() - start) * 1000)
        return report

    # Phase 4: SIMULATE
    go_ideas = simulate(go_ideas, project_root)
    report.ideas_simulated = len(go_ideas)

    # Phase 5: QUALITY GATES
    quality_gates = check_quality_gates(project_root)

    health_after = _snapshot_project_health(project_root)
    report.health_after = health_after
    h_delta = _compute_health_delta(health_before, health_after)
    report.health_delta = h_delta

    # Phase 6: SELECT
    candidates = select_winners(go_ideas, quality_gates, health_delta=h_delta)
    winners = [c for c in candidates if c.merged]
    report.ideas_merged = len(winners)

    # Phase 7: CONVERGE + LEARN
    best_reward = max((i.reward for i in candidates), default=0.0)
    avg_reward = (sum(i.reward for i in candidates) / len(candidates)) if candidates else 0.0
    report.best_reward = round(best_reward, 4)
    report.avg_reward = round(avg_reward, 4)

    verdict, conv_metric = check_convergence(project_root, best_reward)
    report.convergence_metric = round(conv_metric, 4)
    report.verdict = verdict

    update_policy(policy, candidates)
    report.policy_snapshot = policy.to_dict()

    memory = load_memory(project_root)
    for idea in candidates:
        memory.append(idea.to_dict())
    save_memory(project_root, memory)

    report.ideas = [i.to_dict() for i in ideas]
    report.duration_ms = int((time.monotonic() - start) * 1000)
    return report


# ── Mode Train (multi-cycles intensif) ──────────────────────────

def train(project_root: Path, epochs: int = DEFAULT_EPOCHS,
          budget: int = DEFAULT_BUDGET,
          auto_stop: bool = False,
          verbose: bool = True) -> TrainReport:
    """Mode intensif : N cycles avec reinforcement learning."""
    train_start = time.monotonic()
    policy = load_policy(project_root)
    _ensure_dirs(project_root)

    train_report = TrainReport(
        epochs_requested=epochs,
    )

    reward_curve: list[float] = []
    best_idea_ever: dict[str, Any] = {}
    best_reward_ever = -1.0

    for epoch in range(1, epochs + 1):
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"  EPOCH {epoch}/{epochs}")
            print(f"  Epsilon: {policy.epsilon:.3f} | "
                  f"LR: {policy.learning_rate:.3f} | "
                  f"Budget: {budget}")
            print(f"{'=' * 60}")

        report = run_cycle(project_root, policy, budget=budget, epoch=epoch)
        save_cycle_report(project_root, report)
        save_policy(project_root, policy)

        reward_curve.append(report.best_reward)
        train_report.cycles.append(report.to_dict())
        train_report.total_ideas += report.ideas_harvested
        train_report.total_merged += report.ideas_merged

        if report.best_reward > best_reward_ever:
            best_reward_ever = report.best_reward
            winners = [i for i in report.ideas if i.get("merged")]
            if winners:
                best_idea_ever = winners[0]

        if verbose:
            _print_cycle_summary(report, epoch, epochs)

        policy.epsilon = max(0.05, policy.epsilon * 0.95)

        if len(reward_curve) >= 3:
            last3 = reward_curve[-3:]
            if last3[0] > last3[1] < last3[2]:
                policy.learning_rate = max(0.01, policy.learning_rate * 0.8)

        if auto_stop and report.verdict in ("STOP", "CONSOLIDATE"):
            if verbose:
                print(f"\n🛑 Auto-stop: {report.verdict} à l'epoch {epoch}")
            train_report.convergence_reached = True
            train_report.convergence_epoch = epoch
            break

    train_report.epochs_completed = len(reward_curve)
    train_report.reward_curve = [round(r, 4) for r in reward_curve]
    train_report.best_idea = best_idea_ever
    train_report.final_policy = policy.to_dict()
    train_report.duration_ms = int((time.monotonic() - train_start) * 1000)

    if train_report.convergence_reached:
        train_report.verdict = "CONVERGED"
    elif reward_curve and reward_curve[-1] > reward_curve[0]:
        train_report.verdict = "IMPROVING"
    elif reward_curve and reward_curve[-1] < reward_curve[0]:
        train_report.verdict = "DEGRADING"
    else:
        train_report.verdict = "STABLE"

    if verbose:
        _print_train_summary(train_report)

    return train_report


# ── Affichage ────────────────────────────────────────────────────

def _print_cycle_summary(report: CycleReport, epoch: int, total: int) -> None:
    """Affiche un résumé de cycle compact."""
    bar_len = 20
    bar_fill = int(report.best_reward * bar_len)
    bar = "█" * bar_fill + "░" * (bar_len - bar_fill)

    print(f"\n  📊 Cycle {report.cycle_id} (epoch {epoch}/{total})")
    print(f"  ├── Idées: {report.ideas_harvested} récoltées"
          f" → {report.ideas_go} GO / {report.ideas_rejected} rejetées"
          f" → {report.ideas_merged} merged")
    print(f"  ├── Reward: [{bar}] {report.best_reward:.3f} "
          f"(avg: {report.avg_reward:.3f})")
    print(f"  ├── Verdict: {report.verdict}")
    if report.health_delta != 0:
        sign = "+" if report.health_delta > 0 else ""
        print(f"  ├── Santé projet: Δ{sign}{report.health_delta:.3f}")
    print(f"  └── Durée: {report.duration_ms}ms")

    winners = [i for i in report.ideas if i.get("merged")]
    if winners:
        print("\n  🏆 Innovation(s) sélectionnée(s):")
        for w in winners[:3]:
            print(f"     • [{w.get('domain', '?')}] {w.get('title', '?')[:60]}"
                  f" (reward: {w.get('reward', 0):.3f})")


def _print_train_summary(report: TrainReport) -> None:
    """Affiche le résumé d'entraînement final."""
    print(f"\n{'═' * 60}")
    print("  🎓 ENTRAÎNEMENT TERMINÉ")
    print(f"{'═' * 60}")
    print(f"  Epochs: {report.epochs_completed}/{report.epochs_requested}")
    print(f"  Idées totales: {report.total_ideas}")
    print(f"  Innovations mergées: {report.total_merged}")
    print(f"  Verdict: {report.verdict}")
    if report.convergence_reached:
        print(f"  Convergence à l'epoch: {report.convergence_epoch}")
    print(f"  Durée totale: {report.duration_ms}ms")

    if report.reward_curve:
        print("\n  📈 Courbe de reward:")
        max_r = max(report.reward_curve) if report.reward_curve else 1
        for i, r in enumerate(report.reward_curve):
            bar_len = 30
            bar_fill = int((r / max_r) * bar_len) if max_r > 0 else 0
            bar = "█" * bar_fill + "░" * (bar_len - bar_fill)
            marker = " ←best" if r == max_r else ""
            print(f"    E{i + 1:02d} [{bar}] {r:.4f}{marker}")

    if report.best_idea:
        print("\n  🏆 Meilleure innovation globale:")
        print(f"     {report.best_idea.get('title', '?')}")
        print(f"     Domaine: {report.best_idea.get('domain', '?')}"
              f" | Reward: {report.best_idea.get('reward', 0):.4f}")

    fp = report.final_policy
    if fp.get("source_weights"):
        top_sources = sorted(fp["source_weights"].items(),
                             key=lambda x: x[1], reverse=True)[:3]
        print("\n  🧠 Policy apprise (top sources):")
        for s, w in top_sources:
            print(f"     • {s}: {w:.4f}")
    if fp.get("domain_weights"):
        top_domains = sorted(fp["domain_weights"].items(),
                             key=lambda x: x[1], reverse=True)[:3]
        print("  🧠 Policy apprise (top domaines):")
        for d, w in top_domains:
            print(f"     • {d}: {w:.4f}")
