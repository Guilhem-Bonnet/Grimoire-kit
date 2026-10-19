"""Contrôle ``dispatch.cost_slo`` : coût par tâche résolue et pass^k, avec IC95 (issue #442, W4-05).

Extrait de :mod:`grimoire.core.standard_checks.verifiers` pour respecter le
ratchet de taille (``scripts/check-code-ratchet.py``, R2 : 1500 lignes).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grimoire.core.standard_checks.base import StandardVerificationResult, _add_check, _load_yaml_file
from grimoire.core.standard_generation import STANDARD_DIR, TRACES_DIR
from grimoire.traces.ledger import TraceLedger

__all__ = ["_verify_dispatch_cost_slo"]

#: Valeurs par défaut documentées de ``dispatch.cost_slo`` (issue #442, point
#: 5 de l'audit de positionnement du 2026-09-12) quand `llm-provider-
#: registry.yaml` ne déclare pas de clé ``dispatch_cost_slo``, ou en omet
#: une : le contrôle reste actif sans configuration explicite plutôt que de
#: se taire faute de réglage — un projet qui n'a jamais entendu parler de ce
#: contrôle en bénéficie quand même, avec un seuil raisonnable.
DEFAULT_MAX_COST_PER_RESOLVED_TASK_USD = 2.0
DEFAULT_MIN_PASS_K_RATE = 0.8
#: Nombre minimal de dispatchs résolus avant que le coût par tâche résolue ne
#: soit jugé — en dessous, le signal est trop bruité pour un seuil fixe.
DEFAULT_MIN_RESOLVED_OBSERVATIONS = 5
#: Nombre minimal de séries pass^k (nodes rejoués au moins deux fois) avant
#: que son taux ne soit jugé — dénominateur distinct de celui du coût : un
#: projet peut avoir beaucoup de dispatchs résolus sans qu'aucun ne soit
#: jamais rejoué.
DEFAULT_MIN_PASS_K_OBSERVATIONS = 3


def _dispatch_cost_slo_config(data: dict[str, Any]) -> dict[str, Any]:
    """Résout la config ``dispatch_cost_slo`` déclarée, complétée par les défauts documentés."""

    def _positive_number(value: Any, default: float) -> float:
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) and value > 0 else default

    def _positive_int(value: Any, default: int) -> int:
        return int(value) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default

    slo = data.get("dispatch_cost_slo")
    slo = slo if isinstance(slo, dict) else {}
    return {
        "max_cost_per_resolved_task_usd": _positive_number(
            slo.get("max_cost_per_resolved_task_usd"), DEFAULT_MAX_COST_PER_RESOLVED_TASK_USD
        ),
        "min_pass_k_rate": _positive_number(slo.get("min_pass_k_rate"), DEFAULT_MIN_PASS_K_RATE),
        "min_resolved_observations": _positive_int(
            slo.get("min_resolved_observations"), DEFAULT_MIN_RESOLVED_OBSERVATIONS
        ),
        "min_pass_k_observations": _positive_int(
            slo.get("min_pass_k_observations"), DEFAULT_MIN_PASS_K_OBSERVATIONS
        ),
        "enforce": slo.get("enforce") is True,
    }


def _verify_dispatch_cost_slo(root: Path, result: StandardVerificationResult) -> None:
    """``dispatch.cost_slo`` : coût par tâche résolue et pass^k, en continu (issue #442).

    Point 5 de l'audit de positionnement du 2026-09-12 : le kit journalise
    déjà chaque cascade (``dispatch.outcome``, voir
    ``missions.dispatch._record_dispatch_outcome``) mais rien ne le lisait
    depuis un gate avant ce contrôle — les campagnes d'évals manuelles (#308)
    restaient le seul endroit où ce signal existait. Lit
    ``TraceLedger.dispatch_outcome_stats`` (même agrégation que ``grimoire
    dispatch stats``, jamais un second calcul) et compare au SLO déclaré
    dans ``dispatch_cost_slo:`` de ``llm-provider-registry.yaml`` — l'artefact
    du pattern ``provider-cost-slo`` — ou aux valeurs par défaut documentées
    ci-dessus quand cette clé, ou l'une de ses sous-clés, est absente.

    Skip entièrement si ``llm-provider-registry.yaml`` n'existe pas ou n'est
    pas un mapping — même règle que tous les autres ``_verify_*`` de ce
    module : sans artefact, rien à comparer.

    Quatre issues, jamais un ``error`` par défaut :

    - ``info`` quand trop peu de données existent (moins de
      ``min_resolved_observations`` dispatchs résolus pour le coût, moins de
      ``min_pass_k_observations`` séries rejouées pour le pass^k) — chaque
      métrique a son propre plancher de données, jugée indépendamment de
      l'autre.
    - ``warning`` quand le coût par tâche résolue dépasse le SLO, ou que le
      pass^k mesuré est sous le seuil déclaré.
    - ``error`` à la place du ``warning`` ci-dessus, uniquement quand le
      projet déclare ``dispatch_cost_slo.enforce: true`` — un dépassement
      silencieux par défaut, un gate dur seulement sur demande explicite.
    - ``warning`` (jamais ``error``) si des appels n'ont pas de coût : SLO non évalué, pas
      violé ; ``enforce`` ne durcit qu'un dépassement prouvé (un ``trace.v1`` bloquerait à vie).
    """
    rel_path = STANDARD_DIR / "llm-provider-registry.yaml"
    data = _load_yaml_file(root, rel_path, result)
    if not isinstance(data, dict):
        return

    config = _dispatch_cost_slo_config(data)
    severity = "error" if config["enforce"] else "warning"
    ledger = TraceLedger(root / TRACES_DIR)
    stats = ledger.dispatch_outcome_stats()

    if stats.overall.resolved < config["min_resolved_observations"]:
        _add_check(
            result,
            "dispatch.cost_slo",
            "info",
            f"Only {stats.overall.resolved} resolved dispatch(es) observed "
            f"(< {config['min_resolved_observations']}) — cost per resolved task is not judged yet.",
            path=rel_path,
        )
    else:
        cost = stats.overall.cost_per_resolved_task_usd
        max_cost = config["max_cost_per_resolved_task_usd"]
        unpriced = stats.overall.unpriced_calls
        known = "unknown" if cost is None else f"at least ${cost:.4f}"
        # Un coût partiel ou inconnu ne prouve jamais que le SLO est tenu (W1-01, #709).
        interval = None if unpriced else ledger.dispatch_cost_interval()
        if cost is not None and cost > max_cost and interval is not None and interval[0] <= max_cost:
            # IC95 (W4-05) : le plafond reste plausible, un dépassement ponctuel n'est pas une preuve.
            msg = (
                f"Cost per resolved task (${cost:.4f}) is above the declared SLO (${max_cost:.4f}) but "
                f"IC95 [${interval[0]:.4f}, ${interval[1]:.4f}] still contains it: breach not proven."
            )
            _add_check(result, "dispatch.cost_slo", "warning", msg, path=rel_path)
        elif cost is not None and cost > max_cost:
            ci_note = "" if interval is None else f" (IC95 [${interval[0]:.4f}, ${interval[1]:.4f}])"
            msg = f"{'At least: ' if unpriced else ''}Cost per resolved task (${cost:.4f}) exceeds the declared SLO (${max_cost:.4f}){ci_note}."
            _add_check(result, "dispatch.cost_slo", severity, msg, path=rel_path)
        elif unpriced:
            msg = f"Cost per resolved task is {known}: {unpriced} call(s) reported no cost, so the SLO (${max_cost:.4f}) is not evaluated."
            _add_check(result, "dispatch.cost_slo", "warning", msg, path=rel_path)

    if stats.pass_k_observations < config["min_pass_k_observations"]:
        _add_check(
            result,
            "dispatch.cost_slo",
            "info",
            f"Only {stats.pass_k_observations} replayed dispatch series observed "
            f"(< {config['min_pass_k_observations']}) — pass^k is not judged yet.",
            path=rel_path,
        )
    else:
        rate = stats.pass_k_rate
        min_rate = config["min_pass_k_rate"]
        interval = stats.pass_k_ci95
        if rate is not None and interval is not None and rate < min_rate:
            band = f"IC95 [{interval[0]:.0%}, {interval[1]:.0%}]"
            if interval[1] >= min_rate:
                # Le seuil reste plausible : jamais d'erreur sur un échantillon trop petit pour trancher.
                msg = f"pass^k ({rate:.0%}) is below the declared reliability SLO ({min_rate:.0%}) but {band} still contains it: breach not proven."
                _add_check(result, "dispatch.cost_slo", "warning", msg, path=rel_path)
            else:
                msg = f"pass^k ({rate:.0%}, {band}) is below the declared reliability SLO ({min_rate:.0%})."
                _add_check(result, "dispatch.cost_slo", severity, msg, path=rel_path)
