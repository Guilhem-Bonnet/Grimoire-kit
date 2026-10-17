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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.providers.quota import QuotaObservation
    from grimoire.providers.registry import ModelSpec, ProviderSpec

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
    # 529 : la plateforme est saturée, pas le forfait. Refroidissement court
    # qui double, jamais une suspension datée.
    "overloaded": timedelta(minutes=2),
}
_DEFAULT_COOLDOWN = timedelta(minutes=5)
_MAX_COOLDOWN = timedelta(hours=1)

_SCHEMA = "grimoire-providers-runtime-state/v1"


@dataclass(frozen=True, slots=True)
class Suspension:
    """Fermeture datée d'un fournisseur (ou d'un modèle) : forfait épuisé.

    ``until`` est la date de réouverture lue (``source`` ``resets_at`` ou
    ``retry_after``) ou ``None`` (``source == "unknown"``) : la date n'a pas pu
    être lue, l'exclusion dure jusqu'à ``grimoire providers probe``.
    """

    until: datetime | None
    source: str

    def active(self, *, now: datetime) -> bool:
        return self.until is None or self.until > now


@dataclass(slots=True)
class ProviderRuntimeState:
    """Ce qu'on sait d'un fournisseur depuis le dernier succès franc.

    ``available``/``probed_at``/``models_seen``/``probe_note`` viennent de
    ``providers.audit`` (issue #330) — un sondage PATH/``--version``/Ollama,
    jamais d'un appel réel. ``available`` par défaut à ``True`` : un
    fournisseur jamais sondé (pas d'entrée, ou entrée écrite avant ce champ)
    reste éligible — compatibilité, pas optimisme, l'audit est ce qui referme
    la porte.
    """

    cooldown_until: datetime | None = None
    last_failure: str | None = None
    failure_count: int = 0
    available: bool = True
    probed_at: str | None = None
    models_seen: tuple[str, ...] = ()
    probe_note: str | None = None
    suspension: Suspension | None = None
    model_suspensions: dict[str, Suspension] = field(default_factory=dict)
    quota_observation: dict[str, Any] | None = None

    def is_cooling_down(self, *, now: datetime) -> bool:
        return self.cooldown_until is not None and self.cooldown_until > now

    def is_suspended(self, *, now: datetime) -> bool:
        return self.suspension is not None and self.suspension.active(now=now)

    def is_blocked(self, *, now: datetime) -> bool:
        """Refroidi **ou** suspendu : hors du choix pour l'instant."""
        return self.is_cooling_down(now=now) or self.is_suspended(now=now)

    def is_model_suspended(self, model_id: str, *, now: datetime) -> bool:
        suspension = self.model_suspensions.get(model_id)
        return suspension is not None and suspension.active(now=now)

    def blocked_row(self, *, now: datetime) -> tuple[float | None, bool]:
        """``(cooldown_ts, available)`` tel que le filtre (Python ou Rust) les lit.

        Une suspension à date se projette sur la date de refroidissement la plus
        lointaine ; une suspension ``unknown`` se projette sur
        ``available=False``. Le filtre Rust reste ainsi identique au Python.
        """
        until = self.cooldown_until
        if self.suspension is not None and self.suspension.active(now=now):
            if self.suspension.until is None:
                return (until.timestamp() if until else None, False)
            if until is None or self.suspension.until > until:
                until = self.suspension.until
        return (until.timestamp() if until else None, self.available)


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_suspension(until: Any, source: Any) -> Suspension | None:
    if not isinstance(source, str) or not source:
        return None
    parsed = _parse_datetime(until)
    # Une date illisible ne rouvre pas le fournisseur : elle devient ``unknown``.
    return Suspension(parsed, source if parsed is not None else "unknown")


def _dump_suspension(suspension: Suspension) -> dict[str, Any]:
    return {"until": suspension.until.isoformat() if suspension.until else None, "source": suspension.source}


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
        available = entry.get("available", True)
        probed_at = entry.get("probed_at")
        models_seen = entry.get("models_seen")
        probe_note = entry.get("probe_note")
        raw_models = entry.get("model_suspensions")
        model_suspensions: dict[str, Suspension] = {}
        if isinstance(raw_models, dict):
            for model_id, item in raw_models.items():
                if isinstance(item, dict):
                    parsed_item = _parse_suspension(item.get("until"), item.get("source"))
                    if parsed_item is not None:
                        model_suspensions[str(model_id)] = parsed_item
        raw_quota = entry.get("quota_observation")
        state[str(provider_id)] = ProviderRuntimeState(
            cooldown_until=_parse_datetime(entry.get("cooldown_until")),
            last_failure=str(last_failure) if isinstance(last_failure, str) and last_failure else None,
            failure_count=int(failure_count) if isinstance(failure_count, int) else 0,
            available=available if isinstance(available, bool) else True,
            probed_at=str(probed_at) if isinstance(probed_at, str) and probed_at else None,
            models_seen=tuple(str(m) for m in models_seen) if isinstance(models_seen, list) else (),
            probe_note=str(probe_note) if isinstance(probe_note, str) and probe_note else None,
            suspension=_parse_suspension(entry.get("suspended_until"), entry.get("suspension_source")),
            model_suspensions=model_suspensions,
            quota_observation=raw_quota if isinstance(raw_quota, dict) else None,
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
                "available": entry.available,
                "probed_at": entry.probed_at,
                "models_seen": list(entry.models_seen),
                "probe_note": entry.probe_note,
                "suspended_until": (
                    entry.suspension.until.isoformat() if entry.suspension and entry.suspension.until else None
                ),
                "suspension_source": entry.suspension.source if entry.suspension else None,
                "model_suspensions": {
                    model_id: _dump_suspension(item) for model_id, item in entry.model_suspensions.items()
                },
                "quota_observation": entry.quota_observation,
            }
            for provider_id, entry in state.items()
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def cooling_down_entry(root: Path, provider_id: str, *, now: datetime | None = None) -> ProviderRuntimeState | None:
    """L'état de *provider_id* s'il est encore en refroidissement, sinon ``None``.

    Combine :func:`load_state` et :meth:`ProviderRuntimeState.is_cooling_down` —
    le même calcul que ``routing.candidates`` fait fournisseur par
    fournisseur, réutilisable par un appelant qui n'a qu'un seul identifiant
    à vérifier avant un appel réel (pas de raison de charger l'état à la
    main pour ça). Ce module ne connaît que des identifiants de chaîne : un
    appelant hors cascade LLM (mémoire vectorielle, assistant local...) peut
    poser ses propres identifiants (``memory:qdrant``, ``source_assist:ollama``)
    sans que ``routing.py``/``registry.py`` en sachent rien — ils ne
    reviennent jamais de ``candidates()``/``choose()``, qui ne lisent que le
    registre déclaratif des fournisseurs LLM.
    """
    effective_now = _now(now)
    entry = load_state(root).get(provider_id)
    if entry is not None and entry.is_cooling_down(now=effective_now):
        return entry
    return None


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
    # `replace` : l'audit, les suspensions et la lecture de forfait ne sont pas
    # touchés par un échec d'appel.
    entry = replace(
        previous,
        cooldown_until=effective_now + duration,
        last_failure=kind,
        failure_count=failure_count,
    )
    state[provider_id] = entry
    save_state(root, state)
    return entry


def record_suspension(
    root: Path,
    provider_id: str,
    *,
    until: datetime | None,
    source: str,
    model: str | None = None,
    now: datetime | None = None,
) -> ProviderRuntimeState:
    """Ferme un fournisseur (ou un seul de ses modèles) jusqu'à une date lue.

    ``until=None`` (``source="unknown"``) : date illisible, l'exclusion dure
    jusqu'à :func:`clear_unknown_suspensions` (``grimoire providers probe``).
    Une suspension à date **remplace** le refroidissement : on respecte la
    date du fournisseur, on ne l'allonge pas d'un délai de notre cru. Le
    compteur d'échecs, lui, avance.
    """
    del now  # signature symétrique de record_failure ; la date vient du signal
    state = load_state(root)
    previous = state.get(provider_id, ProviderRuntimeState())
    suspension = Suspension(until, source if until is not None else "unknown")
    if model is None:
        entry = replace(
            previous,
            cooldown_until=None,
            last_failure="quota",
            failure_count=previous.failure_count + 1,
            suspension=suspension,
        )
    else:
        entry = replace(
            previous,
            last_failure="quota",
            failure_count=previous.failure_count + 1,
            model_suspensions={**previous.model_suspensions, model: suspension},
        )
    state[provider_id] = entry
    save_state(root, state)
    return entry


def record_quota_observation(root: Path, provider_id: str, observation: QuotaObservation) -> None:
    """Retient la fenêtre de forfait lue dans le flux (un minorant, daté)."""
    state = load_state(root)
    previous = state.get(provider_id, ProviderRuntimeState())
    state[provider_id] = replace(
        previous,
        quota_observation={
            "window": observation.window,
            "utilization": observation.utilization,
            "resets_at": observation.resets_at.isoformat() if observation.resets_at else None,
            "observed_at": observation.observed_at.isoformat(),
        },
    )
    save_state(root, state)


def clear_unknown_suspensions(root: Path, provider_id: str | None = None) -> tuple[str, ...]:
    """``providers probe`` : rouvre ce qu'une date illisible avait fermé.

    Rend les identifiants rouverts (fournisseur, ou ``fournisseur/modèle``).
    Les suspensions à date ne sont pas touchées : la date fait foi.
    """
    state = load_state(root)
    cleared: list[str] = []
    for pid, entry in list(state.items()):
        if provider_id is not None and pid != provider_id:
            continue
        suspension = entry.suspension
        if suspension is not None and suspension.until is None:
            suspension = None
            cleared.append(pid)
        models = {m: sus for m, sus in entry.model_suspensions.items() if sus.until is not None}
        cleared.extend(f"{pid}/{m}" for m, sus in entry.model_suspensions.items() if sus.until is None)
        state[pid] = replace(entry, suspension=suspension, model_suspensions=models)
    if cleared:
        save_state(root, state)
    return tuple(cleared)


def available_models(
    provider: ProviderSpec, tier: str, state: dict[str, ProviderRuntimeState], *, now: datetime
) -> tuple[ModelSpec, ...]:
    """Modèles de *tier* non suspendus : l'intra-palier saute un modèle fermé."""
    runtime = state.get(provider.id)
    models = provider.models_for_tier(tier)
    if runtime is None:
        return models
    return tuple(model for model in models if not runtime.is_model_suspended(model.id, now=now))


def record_success(root: Path, provider_id: str, model: str | None = None) -> None:
    """Efface le refroidissement — un appel a réussi, on repart à zéro.

    Remet le compteur de récidive à zéro plutôt que de retirer l'entrée : un
    ``last_failure`` gardé à ``None`` distingue "jamais échoué" de "a
    échoué puis guéri" pour qui lit l'état brut, sans que ça change quoi que
    ce soit au routage. Un succès rouvre aussi le fournisseur suspendu, et le
    modèle appelé si *model* est donné.
    """
    state = load_state(root)
    if provider_id not in state:
        return
    previous = state[provider_id]
    models = {m: sus for m, sus in previous.model_suspensions.items() if m != model}
    state[provider_id] = replace(
        previous,
        cooldown_until=None,
        last_failure=None,
        failure_count=0,
        suspension=None,
        model_suspensions=models,
    )
    save_state(root, state)
