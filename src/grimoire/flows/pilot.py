"""Le pilote : la fonction qui décide combien dépenser par node (transversale T1, issue #209).

Le graphe dit *quoi* (le blueprint) ; le pilote dit *combien* : quel palier
de départ, jusqu'où escalader, et un plafond de coût qui arrête la cascade
avant de le dépasser. Une fonction de décision, appelée par
``flows.dispatch_executor`` avant chaque node dispatché — **jamais une
couche** : pas de concepts propres, pas de fichiers propres au pluriel, pas
de CLI. Une politique de projet optionnelle (``pilot.yaml``) la paramètre ;
sans elle, le comportement de ``missions.dispatch`` est strictement
inchangé.

Ce module ne réécrit rien : :func:`grimoire.missions.dispatch.start_tier_for`
(le plancher par classe de vérifiabilité, déjà pur, déjà porté en Rust —
``grimoire-dispatch-core``) reste la seule source du plancher. La politique
ne peut jamais descendre sous ce plancher — exactement la même garantie que
``run_dispatch`` applique déjà à ``--start-tier`` explicite (« un palier
explicite ne peut jamais descendre sous le plancher de la classe »).

Ce qui est réellement nouveau ici (audit du 2026-09-13 sur #201 : recherche
de ``max_cost``/``cost_cap`` dans le dépôt, zéro résultat) : le plafond de
coût par node. Il est appliqué dans ``missions.dispatch.run_dispatch``
lui-même (paramètre ``max_cost_usd``), pas ici — ce module se contente de le
lire depuis la politique et de le transmettre, comme il transmet le palier
de départ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.missions.dispatch import start_tier_for
from grimoire.missions.verifiability import Verifiability
from grimoire.providers.registry import SUPPORTED_MODEL_TIERS

__all__ = ["PilotDecision", "PilotPolicy", "decide", "load_pilot_policy"]

#: Emplacement de la politique — même dossier que les autres fichiers de
#: gouvernance du projet (``orchestration-policy.yaml``,
#: ``llm-provider-registry.yaml``), pas la racine du projet : un pilote est
#: une politique du standard agentique, pas une préférence utilisateur.
_POLICY_RELPATH = Path("_grimoire/standard/pilot.yaml")

_VALID_CLASSES = frozenset({"V0", "V1", "V2"})


@dataclass(frozen=True, slots=True)
class PilotPolicy:
    """``pilot.yaml`` du projet, résolue — absente : ``PilotPolicy()`` par défaut, sans effet."""

    #: Palier de départ par classe (ex. ``{"V0": "mid"}``). Une classe absente
    #: du dict retombe sur ``start_tier_for`` — jamais un plancher inventé.
    start_tier: dict[str, str] = field(default_factory=dict)
    #: Nombre de paliers au-delà du départ que la cascade peut essayer. ``0``
    #: interdit toute escalade (le départ est aussi le plafond) ; ``None`` :
    #: pas de limite posée par le pilote (celle de ``--max-tier``, si donnée
    #: à l'appel, s'applique quand même).
    max_escalations: int | None = None
    #: Plafond de coût par node, en USD. ``None`` : pas de plafond.
    max_cost_usd_per_node: float | None = None


_DEFAULT_POLICY = PilotPolicy()


def _validate_and_build(data: dict[str, Any]) -> PilotPolicy:
    known_keys = {"start_tier", "max_escalations", "max_cost_usd_per_node"}
    unknown = sorted(set(data) - known_keys)
    if unknown:
        raise GrimoireRuntimeError(
            f"pilot.yaml : clé(s) inconnue(s) {unknown} — attendu un sous-ensemble de {sorted(known_keys)}"
        )

    raw_start_tier = data.get("start_tier", {})
    if not isinstance(raw_start_tier, dict):
        raise GrimoireRuntimeError("pilot.yaml : 'start_tier' doit être un mapping classe -> palier")
    start_tier: dict[str, str] = {}
    for class_name, tier in raw_start_tier.items():
        if class_name not in _VALID_CLASSES:
            raise GrimoireRuntimeError(
                f"pilot.yaml : classe inconnue '{class_name}' dans 'start_tier' — attendu {sorted(_VALID_CLASSES)}"
            )
        if tier not in SUPPORTED_MODEL_TIERS:
            raise GrimoireRuntimeError(
                f"pilot.yaml : palier inconnu '{tier}' pour la classe '{class_name}' — "
                f"attendu {list(SUPPORTED_MODEL_TIERS)}"
            )
        start_tier[str(class_name)] = str(tier)

    max_escalations = data.get("max_escalations")
    if max_escalations is not None and (
        isinstance(max_escalations, bool) or not isinstance(max_escalations, int) or max_escalations < 0
    ):
        raise GrimoireRuntimeError("pilot.yaml : 'max_escalations' doit être un entier positif ou nul")

    max_cost = data.get("max_cost_usd_per_node")
    if max_cost is not None and (
        isinstance(max_cost, bool) or not isinstance(max_cost, (int, float)) or max_cost <= 0
    ):
        raise GrimoireRuntimeError("pilot.yaml : 'max_cost_usd_per_node' doit être un nombre strictement positif")

    return PilotPolicy(
        start_tier=start_tier,
        max_escalations=max_escalations,
        max_cost_usd_per_node=float(max_cost) if max_cost is not None else None,
    )


def load_pilot_policy(project_root: Path) -> PilotPolicy:
    """Charge ``_grimoire/standard/pilot.yaml`` — absent : la politique par défaut, sans effet.

    Contrairement à ``orchestration-policy.yaml`` (best-effort, retombe en
    silence sur un fichier illisible) : un ``pilot.yaml`` malformé refuse
    nommément le chargement. Un plafond de coût est un mécanisme de
    sécurité — le faire taire en silence sur une erreur de frappe laisserait
    une cascade dépenser sans le garde-fou que le projet croit avoir posé.
    """
    path = project_root / _POLICY_RELPATH
    if not path.is_file():
        return _DEFAULT_POLICY
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except (YAMLError, OSError, UnicodeDecodeError) as exc:
        raise GrimoireRuntimeError(f"{path} : illisible ({exc})") from exc
    if data is None:
        return _DEFAULT_POLICY
    if not isinstance(data, dict):
        raise GrimoireRuntimeError(f"{path} : doit être un mapping YAML")
    return _validate_and_build(data)


@dataclass(frozen=True, slots=True)
class PilotDecision:
    """Ce que le pilote transmet à ``run_dispatch`` pour un node donné."""

    #: ``None`` : pas d'avis, laisser ``run_dispatch`` recommander lui-même
    #: (historique des dispatchs passés, ``dispatch_history.recommend_start_tier``).
    start_tier: str | None
    #: ``None`` : pas de plafond posé par le pilote (celui de ``--max-tier``
    #: explicite, s'il existe, s'applique quand même — voir l'appelant).
    max_tier: str | None
    max_cost_usd: float | None


def decide(verifiability: Verifiability, *, policy: PilotPolicy) -> PilotDecision:
    """La décision du pilote pour un node de cette classe — un seul point d'appel.

    V2 : rien à piloter, ``start_tier_for`` rend déjà ``None`` (refus avant
    tout appel, inchangé) — le plafond de coût est transmis quand même, sans
    effet tant qu'aucun appel n'a lieu.
    """
    floor = start_tier_for(verifiability)
    if floor is None:
        return PilotDecision(start_tier=None, max_tier=None, max_cost_usd=policy.max_cost_usd_per_node)

    override = policy.start_tier.get(verifiability.value)
    # Même garantie que ``--start-tier`` explicite dans ``run_dispatch`` :
    # une politique ne peut jamais faire partir une classe sous son plancher.
    start_tier = None
    if override is not None:
        start_tier = override if SUPPORTED_MODEL_TIERS.index(override) >= SUPPORTED_MODEL_TIERS.index(floor) else floor

    max_tier = None
    if policy.max_escalations is not None:
        base_tier = start_tier or floor
        base_idx = SUPPORTED_MODEL_TIERS.index(base_tier)
        capped_idx = min(base_idx + policy.max_escalations, len(SUPPORTED_MODEL_TIERS) - 1)
        max_tier = SUPPORTED_MODEL_TIERS[capped_idx]

    return PilotDecision(start_tier=start_tier, max_tier=max_tier, max_cost_usd=policy.max_cost_usd_per_node)
