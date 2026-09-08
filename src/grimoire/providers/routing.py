"""``choose()`` — le fournisseur à appeler pour un palier de coût donné.

Combine le registre (qui est activé, qui a un modèle de ce palier) et l'état
runtime (qui est en refroidissement) pour rendre une seule décision, dans
l'ordre où un budget agentique doit la lire : d'abord ce que le registre
préfère, puis ses filets de secours, jamais un fournisseur désactivé ou hors
capacité pour ce palier.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from grimoire.providers.registry import ProviderSpec, read_default_fallback_chain, read_registry
from grimoire.providers.state import load_state


def _candidate_order(providers: tuple[ProviderSpec, ...], default_fallback_chain: tuple[str, ...]) -> list[str]:
    """L'ordre du registre, complété par les filets de secours non déjà cités.

    "L'ordre du registre puis fallback_order" : la liste ``providers[]``
    donne l'ordre primaire ; le ``fallback_order`` propre à chaque
    fournisseur et le ``routing.default_fallback_chain`` du registre
    n'ajoutent que les identifiants pas encore vus, sans réordonner ce que
    le registre a déjà décidé.
    """
    order: list[str] = []
    seen: set[str] = set()
    for provider in providers:
        if provider.id not in seen:
            order.append(provider.id)
            seen.add(provider.id)
    for provider in providers:
        for candidate_id in provider.fallback_order:
            if candidate_id not in seen:
                order.append(candidate_id)
                seen.add(candidate_id)
    for candidate_id in default_fallback_chain:
        if candidate_id not in seen:
            order.append(candidate_id)
            seen.add(candidate_id)
    return order


def candidates(root: Path, tier: str, *, now: datetime | None = None) -> tuple[ProviderSpec, ...]:
    """Tous les fournisseurs activés, disponibles et outillés pour *tier*, dans l'ordre.

    ``choose()`` ne rend que le premier ; une cascade de dispatch (issue #323)
    a besoin de la liste entière pour retomber sur le suivant du même palier
    après un échec d'appel, sans reconsulter le registre à chaque tentative.
    Un fournisseur en refroidissement, ou que ``providers audit`` (issue #330)
    a jugé indisponible (exécutable absent du PATH), est absent de la
    liste — c'est la même notion de « disponible maintenant » que
    ``choose()``, pas une politique séparée qui pourrait diverger. Un
    fournisseur jamais audité reste candidat : ``available`` par défaut à
    ``True`` (voir ``ProviderRuntimeState``).
    """
    effective_now = now if now is not None else datetime.now(UTC)
    providers = read_registry(root)
    by_id = {provider.id: provider for provider in providers}
    order = _candidate_order(providers, read_default_fallback_chain(root))
    state = load_state(root)

    out: list[ProviderSpec] = []
    for provider_id in order:
        provider = by_id.get(provider_id)
        if provider is None or not provider.enabled:
            continue
        if not provider.models_for_tier(tier):
            continue
        runtime = state.get(provider_id)
        if runtime is not None and runtime.is_cooling_down(now=effective_now):
            continue
        if runtime is not None and not runtime.available:
            continue
        out.append(provider)
    return tuple(out)


def choose(root: Path, tier: str, *, now: datetime | None = None) -> ProviderSpec | None:
    """Premier fournisseur activé, disponible, et outillé pour *tier*.

    ``None`` si aucun ne convient — appelant fermé, pas d'exception : un
    routeur de coût qui ne trouve personne doit pouvoir le dire tranquillement
    (par exemple pour retomber sur un mode dégradé), pas planter le budget.
    """
    found = candidates(root, tier, now=now)
    return found[0] if found else None
