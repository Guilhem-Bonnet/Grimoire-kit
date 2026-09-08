"""État runtime des fournisseurs LLM : refroidissement après échec.

Le registre (``registry.py``) dit ce qu'un projet *a le droit* d'appeler ;
ce module dit ce qui *marche encore maintenant*. Un 429 sur Anthropic ne
change rien au registre — le fournisseur reste activé, éligible — mais doit
retirer temporairement Anthropic du choix tant que le fournisseur n'a pas eu
le temps de se calmer. C'est un état, pas une politique : il vit dans
``_grimoire-output/`` (jetable, jamais commité), pas dans
``_grimoire/standard/`` (déclaratif, versionné).

Le refroidissement double à chaque récidive et plafonne à une heure, pour
qu'un fournisseur qui rate-limite en rafale ne soit pas retesté toutes les
5 minutes indéfiniment — mais qu'un incident isolé ne l'exile pas pour la
journée.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

#: ``_grimoire-output/`` est la sortie jetable du projet — voir EVENT_DIR,
#: KNOWLEDGE_DIR dans ``grimoire.core.agentic_standard`` pour le même choix
#: d'emplacement. Rien sous ``_grimoire/standard/`` : cet état n'a rien de
#: déclaratif, il est reconstruit par l'usage et perdable sans dommage.
PROVIDERS_STATE_FILE = Path("_grimoire-output/providers-state.json")

#: Durée de refroidissement de base par type d'échec. Un timeout et un
#: rate-limit méritent la même prudence initiale ; seule la récidive les
#: distingue (le compteur, partagé, double la durée à chaque fois).
_BASE_COOLDOWN: dict[str, timedelta] = {
    "rate_limit": timedelta(minutes=5),
    "timeout": timedelta(minutes=5),
}
_DEFAULT_COOLDOWN = timedelta(minutes=5)
_MAX_COOLDOWN = timedelta(hours=1)

_SCHEMA = "grimoire-providers-runtime-state/v1"


@dataclass(slots=True)
class ProviderRuntimeState:
    """Ce qu'on sait d'un fournisseur depuis le dernier succès franc."""

    cooldown_until: datetime | None = None
    last_failure: str | None = None
    failure_count: int = 0

    def is_cooling_down(self, *, now: datetime) -> bool:
        return self.cooldown_until is not None and self.cooldown_until > now


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def load_state(root: Path) -> dict[str, ProviderRuntimeState]:
    """État persistant, ou ``{}`` si le fichier est absent ou corrompu.

    Un état illisible ne doit pas bloquer le routage : au pire, un
    fournisseur récemment refroidi est retesté un peu tôt. C'est un
    dégradé acceptable ; refuser de router n'en serait pas un.
    """
    path = root / PROVIDERS_STATE_FILE
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    providers = raw.get("providers") if isinstance(raw, dict) else None
    if not isinstance(providers, dict):
        return {}
    state: dict[str, ProviderRuntimeState] = {}
    for provider_id, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        last_failure = entry.get("last_failure")
        failure_count = entry.get("failure_count", 0)
        state[str(provider_id)] = ProviderRuntimeState(
            cooldown_until=_parse_datetime(entry.get("cooldown_until")),
            last_failure=str(last_failure) if isinstance(last_failure, str) and last_failure else None,
            failure_count=int(failure_count) if isinstance(failure_count, int) else 0,
        )
    return state


def save_state(root: Path, state: dict[str, ProviderRuntimeState]) -> None:
    """Écrit l'état complet — remplacement, pas fusion (l'appelant a déjà lu)."""
    path = root / PROVIDERS_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "$schema": _SCHEMA,
        "providers": {
            provider_id: {
                "cooldown_until": entry.cooldown_until.isoformat() if entry.cooldown_until else None,
                "last_failure": entry.last_failure,
                "failure_count": entry.failure_count,
            }
            for provider_id, entry in state.items()
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def record_failure(
    root: Path,
    provider_id: str,
    kind: str = "rate_limit",
    *,
    now: datetime | None = None,
) -> ProviderRuntimeState:
    """Enregistre un échec et pose (ou allonge) le refroidissement.

    *kind* est descriptif (``"rate_limit"``, ``"timeout"``, ou tout autre
    libellé appelant) — le calcul de durée ne distingue que par la table de
    base ci-dessus, avec un repli sur 5 minutes pour un type non recensé,
    plutôt que refuser l'appel : un hook qui vient de voir échouer un appel
    a besoin d'enregistrer l'échec maintenant, pas de connaître d'abord tout
    le vocabulaire.
    """
    effective_now = _now(now)
    state = load_state(root)
    previous = state.get(provider_id, ProviderRuntimeState())
    failure_count = previous.failure_count + 1
    base = _BASE_COOLDOWN.get(kind, _DEFAULT_COOLDOWN)
    duration = min(base * (2 ** (failure_count - 1)), _MAX_COOLDOWN)
    entry = ProviderRuntimeState(
        cooldown_until=effective_now + duration,
        last_failure=kind,
        failure_count=failure_count,
    )
    state[provider_id] = entry
    save_state(root, state)
    return entry


def record_success(root: Path, provider_id: str) -> None:
    """Efface le refroidissement — un appel a réussi, on repart à zéro.

    Remet le compteur de récidive à zéro plutôt que de retirer l'entrée : un
    ``last_failure`` gardé à ``None`` distingue "jamais échoué" de "a
    échoué puis guéri" pour qui lit l'état brut, sans que ça change quoi que
    ce soit au routage.
    """
    state = load_state(root)
    if provider_id not in state:
        return
    state[provider_id] = ProviderRuntimeState()
    save_state(root, state)
