"""Modèle de coût calibré — source de vérité unique du contexte.

Avant cette tranche (SPEC-ingenierie-contexte, C2 « enforcement calibré »), la
table de coût vivait en dur dans ``web/bp2-cost.js`` (« hypothèses ») et la
simulation de pression utilisait une constante plate. Ce module centralise le
modèle côté serveur pour que **le design (vue COÛT), la simulation de pression,
le Gate(budget) et l'assertion d'éval ``cost-under`` lisent le même coût** —
une seule source de vérité, exposée par ``/api/cost-model``.

Les fenêtres de contexte proviennent de :mod:`grimoire.tools.model_windows`
(issue #39, source unique). Les estimations par pattern (k-tokens) et les taux
$/MTok conservent leurs valeurs historiques de ``bp2-cost.js`` — migration
neutre pour le comportement. Ce sont des ordres de grandeur de planification
locale, jamais une facture.
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

from grimoire.tools.model_windows import MODEL_WINDOWS, resolve_window

COST_MODEL_SCHEMA_VERSION = "grimoire-cost-model/v1"

# Tarifs publics par version de modèle, en $ par million de tokens (in / out).
# Source et date : relevées le 2026-10-16 sur RATE_CARD_SOURCE. Une version non
# listée n'a pas de tarif (``rate_for`` rend None) : jamais de repli sur une
# famille. Haiku 5.5 n'est pas listé : son tarif dépend de la taille du prompt,
# que le total de tokens ne donne pas. Les tarifs cache ne sont pas modélisés.
RATE_CARD_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
RATE_CARD_AS_OF = date(2026, 10, 16)
RATE_CARD_MAX_AGE_DAYS = 90

# Coût par pattern du catalogue : k-tokens ``{in, out, runs}`` par run complet.
# Valeurs historiques portées telles quelles depuis web/bp2-cost.js.
PATTERN_COST: dict[str, dict[str, float]] = {
    "ORC-02": {"in": 2.4, "out": 1.2, "runs": 1},
    "ORC-01": {"in": 4.5, "out": 2.4, "runs": 3},
    "ORC-03": {"in": 1.6, "out": 0.8, "runs": 1},
    "ORC-11": {"in": 14.0, "out": 6.5, "runs": 3},
    "COG-01": {"in": 5.0, "out": 2.2, "runs": 1},
    "COG-03": {"in": 12.0, "out": 5.0, "runs": 2},
    "QUA-04": {"in": 6.0, "out": 2.8, "runs": 1},
    "QUA-15": {"in": 9.0, "out": 1.8, "runs": 1},
    "QUA-13": {"in": 7.0, "out": 1.4, "runs": 1},
    "QUA-05": {"in": 3.5, "out": 0.6, "runs": 1},
    "QUA-03": {"in": 1.8, "out": 0.4, "runs": 1},
    "GOV-01": {"in": 2.0, "out": 0.5, "runs": 1},
    "GOV-02": {"in": 2.5, "out": 0.6, "runs": 1},
    "GOV-04": {"in": 8.0, "out": 2.2, "runs": 1},
    "GOV-12": {"in": 7.5, "out": 2.4, "runs": 1},
    "GOV-15": {"in": 1.2, "out": 0.3, "runs": 1},
    "KNO-02": {"in": 2.2, "out": 1.1, "runs": 1},
    "KNO-06": {"in": 5.0, "out": 1.6, "runs": 1},
    "QUA-14": {"in": 3.0, "out": 0.5, "runs": 1},
}

# Défauts pour un node hors table.
EXT_DEFAULT: dict[str, float] = {"in": 18.0, "out": 7.0, "runs": 1}  # crew/graph externe
CAT_DEFAULT: dict[str, float] = {"in": 5.0, "out": 2.0, "runs": 1}  # node catalogue générique

# Coût par rôle d'agent concret.
ROLE_COST: dict[str, dict[str, float]] = {
    "orchestrateur": {"in": 4.0, "out": 1.5, "runs": 1},
    "agent": {"in": 10.0, "out": 4.5, "runs": 2},
    "sub": {"in": 5.5, "out": 2.2, "runs": 1},
}

_RATE_KEYS = ("in", "out")


def validate_rate_table(table: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """Rend la table si chaque prix est un réel fini et positif ou nul, sinon lève ValueError."""
    for key, row in table.items():
        for field in _RATE_KEYS:
            value = row.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"tarif invalide pour {key!r} : {field}={value!r}")
    return table


# Table unique : clés = identifiants de version normalisés (voir normalize_model_id).
# Ordre : plus récent d'abord dans chaque famille (la vue par famille en dépend).
MODEL_RATES: dict[str, dict[str, float]] = validate_rate_table({
    "opus-5-5": {"in": 4.0, "out": 20.0},
    "opus-5": {"in": 5.0, "out": 25.0},
    "opus-4-8": {"in": 5.0, "out": 25.0},
    "opus-4-7": {"in": 5.0, "out": 25.0},
    "opus-4-6": {"in": 5.0, "out": 25.0},
    "opus-4-5": {"in": 5.0, "out": 25.0},
    "opus-4-1": {"in": 15.0, "out": 75.0},
    "opus-4": {"in": 15.0, "out": 75.0},
    "sonnet-5-5": {"in": 2.0, "out": 10.0},
    "sonnet-5": {"in": 2.0, "out": 10.0},
    "sonnet-4-6": {"in": 3.0, "out": 15.0},
    "sonnet-4-5": {"in": 3.0, "out": 15.0},
    "sonnet-4": {"in": 3.0, "out": 15.0},
    "haiku-4-5": {"in": 1.0, "out": 5.0},
    "haiku-3-5": {"in": 0.8, "out": 4.0},
    "fable-5-1": {"in": 10.0, "out": 50.0},
    "fable-5": {"in": 10.0, "out": 50.0},
})

# Repli de estimate_usd pour un modèle non listé (comportement historique du gate).
_FALLBACK_RATE_ID = "sonnet-4-6"
_FAMILIES = ("opus", "sonnet", "haiku")
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def normalize_model_id(model: str) -> str:
    """``claude-haiku-4.5-20251001[1m]`` → ``haiku-4-5`` : version exacte, sans famille."""
    key = model.strip().lower().split("[", 1)[0].replace(".", "-")
    key = key.removeprefix("claude-")
    return _DATE_SUFFIX.sub("", key)


def rate_for(model: str | None) -> dict[str, float] | None:
    """Tarif ``{in, out}`` ($/MTok) de la version exacte, ou None si non listée."""
    if not model:
        return None
    return MODEL_RATES.get(normalize_model_id(model))


def rate_card_age_warning(today: date) -> str | None:
    """Avertissement si la table date de plus de RATE_CARD_MAX_AGE_DAYS jours."""
    age = (today - RATE_CARD_AS_OF).days
    if age <= RATE_CARD_MAX_AGE_DAYS:
        return None
    return (
        f"tarifs des modèles datés du {RATE_CARD_AS_OF.isoformat()} ({age} jours) : "
        f"à revérifier sur {RATE_CARD_SOURCE}"
    )


def _family_view() -> dict[str, dict[str, float]]:
    """Tarif de la version la plus récente de chaque famille (consommé par le cockpit web)."""
    view: dict[str, dict[str, float]] = {}
    for family in _FAMILIES:
        for key, rate in MODEL_RATES.items():
            if key.startswith(f"{family}-") or key == family:
                view[family] = {"in": rate["in"], "out": rate["out"]}
                break
    return view

# Budget mission (k-tokens) par profil projet.
MISSION_CAPS_K: dict[str, int] = {
    "starter": 80,
    "controlled": 200,
    "orchestrated": 500,
    "governed": 1200,
    "production": 2500,
}

# Tiers de budget de contexte (ORC-08) → plafond d'entrée en fraction de la
# fenêtre du modèle cible. ``deep`` exige une justification (lint R-C3).
TIER_WINDOW_FRACTION: dict[str, float] = {
    "tiny": 0.02,
    "small": 0.10,
    "medium": 0.35,
    "deep": 0.80,
}
DEFAULT_TIER = "medium"


def tier_ceiling(tier: str, model: str | None = None) -> int:
    """Plafond d'entrée calibré (tokens) pour un tier contre la fenêtre modèle."""
    fraction = TIER_WINDOW_FRACTION.get(tier, TIER_WINDOW_FRACTION[DEFAULT_TIER])
    return int(resolve_window(model) * fraction)


def pattern_cost(ref: str | None, *, is_ext: bool = False) -> dict[str, float]:
    """Coût ``{in, out, runs}`` d'un node par sa référence de pattern."""
    if ref and ref in PATTERN_COST:
        return PATTERN_COST[ref]
    return EXT_DEFAULT if is_ext else CAT_DEFAULT


def node_entry_tokens(ref: str | None, *, is_ext: bool = False) -> int:
    """Coût d'entrée calibré d'un node (tokens) : ``in`` × ``runs`` × 1000.

    Remplace la constante plate ``NODE_BASE_TOKENS`` de la simulation de
    pression : chaque node porte le coût d'entrée de son pattern.
    """
    cost = pattern_cost(ref, is_ext=is_ext)
    return int(cost["in"] * cost.get("runs", 1) * 1000)


def estimate_usd(tokens_in: int, tokens_out: int, model: str = "sonnet") -> float:
    """Coût $ estimé d'un run contre ``MODEL_RATES`` ($/MTok).

    Même source de vérité que la vue COÛT et le Gate(budget) : le design, le
    gate et l'assertion d'éval ``cost-under`` lisent ces taux.
    """
    rate = rate_for(model) or MODEL_RATES[_FALLBACK_RATE_ID]
    return (tokens_in * rate["in"] + tokens_out * rate["out"]) / 1_000_000


def cost_under(
    tokens_in: int, tokens_out: int, cap_usd: float, model: str = "sonnet"
) -> bool:
    """Assertion d'éval ``cost-under`` : le coût estimé tient sous le plafond.

    Vérifiable contre le même modèle de coût que le design et le gate (SPEC C2).
    """
    return estimate_usd(tokens_in, tokens_out, model) <= cap_usd


def cost_model(model: str | None = None) -> dict[str, Any]:
    """Charge utile de ``/api/cost-model`` — le modèle calibré complet.

    Consommé par la vue COÛT (remplace la table statique JS), la simulation de
    pression, le Gate(budget) et l'assertion ``cost-under``.
    """
    return {
        "schemaVersion": COST_MODEL_SCHEMA_VERSION,
        "calibrated": True,
        "window": resolve_window(model),
        "patterns": PATTERN_COST,
        "extDefault": EXT_DEFAULT,
        "catDefault": CAT_DEFAULT,
        "roleCost": ROLE_COST,
        "modelRates": _family_view(),
        "modelRateCard": {
            "asOf": RATE_CARD_AS_OF.isoformat(),
            "source": RATE_CARD_SOURCE,
            "rates": MODEL_RATES,
        },
        "missionCapsK": MISSION_CAPS_K,
        "tierWindowFraction": TIER_WINDOW_FRACTION,
        "modelWindows": MODEL_WINDOWS,
    }
