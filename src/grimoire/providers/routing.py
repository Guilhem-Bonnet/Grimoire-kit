"""``choose()`` — le fournisseur à appeler pour un palier de coût donné.

Combine le registre (qui est activé, qui a un modèle de ce palier) et l'état
runtime (qui est en refroidissement) pour rendre une seule décision, dans
l'ordre où un budget agentique doit la lire : d'abord ce que le registre
préfère, puis ses filets de secours, jamais un fournisseur désactivé ou hors
capacité pour ce palier.

Backend
-------
``_candidate_order`` et le filtre de refroidissement/disponibilité de
:func:`candidates` ont un second port, optionnel, en Rust compilé par PyO3
(``rust/grimoire-dispatch-core/``, issue #354, même crate que
``grimoire.missions.dispatch``). Même bascule que ce module,
``GRIMOIRE_DISPATCH_BACKEND`` — voir le docstring de ``dispatch.py`` pour le
détail : ce module ne peut pas l'importer directement (``dispatch.py``
importe déjà ``routing.py``, un import inverse créerait un cycle), donc la
résolution du backend est dupliquée ici à l'identique plutôt que partagée.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from grimoire.core.exceptions import GrimoireRegistryError
from grimoire.providers.registry import ProviderSpec, read_default_fallback_chain, read_registry
from grimoire.providers.state import load_state

try:
    import grimoire_dispatch_core as _rust_core
except ImportError:  # pragma: no cover - exercised by the dedicated Rust CI job
    _rust_core = None


def rust_backend_available() -> bool:
    """Whether the compiled ``grimoire_dispatch_core`` module is importable."""
    return _rust_core is not None


def _use_rust_backend() -> bool:
    """Resolve which backend this call should use — see ``dispatch._use_rust_backend``."""
    override = os.environ.get("GRIMOIRE_DISPATCH_BACKEND", "auto").strip().lower()
    if override == "python":
        return False
    if override == "rust":
        if _rust_core is None:
            raise GrimoireRegistryError(
                "GRIMOIRE_DISPATCH_BACKEND=rust demande le coeur Rust, mais "
                "grimoire_dispatch_core est introuvable. Construire l'extension "
                "localement (voir CONTRIBUTING.md, `maturin develop` dans "
                "rust/grimoire-dispatch-core/) ou revenir a auto/python."
            )
        return True
    if override not in ("auto", ""):
        raise GrimoireRegistryError(f"GRIMOIRE_DISPATCH_BACKEND invalide: {override!r} (attendu auto/python/rust)")
    return _rust_core is not None


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
    default_fallback_chain = read_default_fallback_chain(root)
    state = load_state(root)

    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        provider_rows = [
            (provider.id, provider.enabled, list(provider.fallback_order), bool(provider.models_for_tier(tier)))
            for provider in providers
        ]
        cooldown_rows = [
            (
                provider_id,
                runtime.cooldown_until.timestamp() if runtime.cooldown_until is not None else None,
                runtime.available,
            )
            for provider_id, runtime in state.items()
        ]
        ids = _rust_core.candidate_provider_ids_py(
            provider_rows, list(default_fallback_chain), cooldown_rows, effective_now.timestamp()
        )
        return tuple(by_id[provider_id] for provider_id in ids if provider_id in by_id)

    order = _candidate_order(providers, default_fallback_chain)
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
