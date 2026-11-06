"""Lecture typée de ``llm-provider-registry.yaml`` (issue #310, lot 2).

Le registre lui-même reste un YAML libre, vérifié à la marge par
``standard_checks.verifiers``. Ce module lui donne une forme exploitable en
code : ``ProviderSpec``/``ModelSpec`` pour le routage par palier de coût
(``grimoire providers status``, ``providers.routing.choose``), sans dupliquer
ni contraindre les champs déjà consommés ailleurs (``allowed_capabilities``,
``data_policy``, ``fallback_order``...).

Les champs ``currency``, ``invocation`` et ``models`` sont optionnels : un
registre v1 qui ne les déclare pas se lit toujours, juste sans fournisseur
utilisable par ``choose()`` (aucun modèle affecté à un palier).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.core.exceptions import GrimoireRegistryError
from grimoire.core.standard_generation import STANDARD_DIR

#: Chemin relatif du registre, projet par projet — même constante que
#: ``grimoire.core.agentic_standard.LLM_PROVIDER_REGISTRY_FILE``, redéclarée
#: ici pour ne pas tirer tout ``agentic_standard`` (généré + verify + gates)
#: dans un module qui ne fait que lire.
REGISTRY_FILE = STANDARD_DIR / "llm-provider-registry.yaml"

#: Vocabulaire fermé : une valeur hors de cette liste est une erreur de
#: vérification, pas un avertissement — un routage par coût qui accepte
#: silencieusement un palier inconnu route dans le vide.
SUPPORTED_CURRENCIES: tuple[str, ...] = ("quota", "api", "local")
SUPPORTED_MODEL_TIERS: tuple[str, ...] = ("cheap", "mid", "strong")


class ProviderRegistryError(GrimoireRegistryError):
    """Le registre est illisible ou structurellement invalide."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Un modèle déclaré sous un fournisseur, affecté à un palier de coût."""

    id: str
    tier: str


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Vue typée d'une entrée ``providers[]`` du registre.

    Ne porte que ce dont le routage a besoin. Les champs de gouvernance
    (``audit``, ``data_policy`` en détail...) restent dans le YAML brut ; les
    relire en typé n'apporterait rien à ``choose()``.
    """

    id: str
    enabled: bool
    provider_type: str
    allowed_capabilities: tuple[str, ...]
    default_models: tuple[str, ...]
    fallback_order: tuple[str, ...]
    currency: str | None = None
    invocation: str | None = None
    models: tuple[ModelSpec, ...] = field(default_factory=tuple)

    def models_for_tier(self, tier: str) -> tuple[ModelSpec, ...]:
        """Modèles déclarés pour *tier*, dans l'ordre du registre."""
        return tuple(model for model in self.models if model.tier == tier)


def _yaml() -> YAML:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    return yaml


def _load_registry_mapping(root: Path) -> dict[str, Any]:
    """Le registre comme table ; ``{}`` s'il n'existe pas encore.

    Un projet qui n'a pas encore lancé ``grimoire standard init`` n'a pas de
    registre — ce n'est pas une erreur pour un lecteur, juste zéro
    fournisseur disponible. Un fichier présent mais corrompu, en revanche,
    doit le dire : router silencieusement vers rien masquerait la panne.
    """
    path = root / REGISTRY_FILE
    if not path.is_file():
        return {}
    try:
        data = _yaml().load(path.read_text(encoding="utf-8"))
    except (YAMLError, OSError, UnicodeDecodeError) as exc:
        msg = f"{REGISTRY_FILE} illisible : {type(exc).__name__}: {exc}"
        raise ProviderRegistryError(msg) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = f"{REGISTRY_FILE} doit être une table YAML."
        raise ProviderRegistryError(msg)
    return data


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _model_specs(value: Any) -> tuple[ModelSpec, ...]:
    if not isinstance(value, list):
        return ()
    specs: list[ModelSpec] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        tier = entry.get("tier")
        if not model_id or not tier:
            continue
        specs.append(ModelSpec(id=str(model_id), tier=str(tier)))
    return tuple(specs)


def _provider_spec(entry: dict[str, Any]) -> ProviderSpec | None:
    provider_id = entry.get("id")
    if not provider_id:
        return None
    currency = entry.get("currency")
    invocation = entry.get("invocation")
    return ProviderSpec(
        id=str(provider_id),
        enabled=entry.get("enabled") is True,
        provider_type=str(entry.get("provider_type", "")),
        allowed_capabilities=_str_tuple(entry.get("allowed_capabilities")),
        default_models=_str_tuple(entry.get("default_models")),
        fallback_order=_str_tuple(entry.get("fallback_order")),
        currency=str(currency) if isinstance(currency, str) and currency else None,
        invocation=str(invocation) if isinstance(invocation, str) and invocation else None,
        models=_model_specs(entry.get("models")),
    )


def read_registry(root: Path) -> tuple[ProviderSpec, ...]:
    """Fournisseurs déclarés, dans l'ordre du registre.

    Une entrée sans ``id`` est ignorée plutôt que fatale — ``verify``/``gates``
    la signalent déjà comme erreur ; un lecteur de routage n'a pas à
    dupliquer ce diagnostic, seulement à ne pas planter dessus.
    """
    data = _load_registry_mapping(root)
    providers = data.get("providers")
    if not isinstance(providers, list):
        return ()
    specs = (_provider_spec(entry) for entry in providers if isinstance(entry, dict))
    return tuple(spec for spec in specs if spec is not None)


class ProviderWriteError(ProviderRegistryError):
    """Une écriture sur le registre a été refusée (id dupliqué, vocabulaire invalide...)."""


def _yaml_roundtrip() -> YAML:
    """Round-trip (``typ="rt"``) : préserve commentaires et mise en forme du fichier existant.

    Distinct de :func:`_yaml` (``typ="safe"``) — un lecteur n'a pas besoin de
    préserver la forme, une écriture ne doit jamais effacer les commentaires
    qui documentent le reste du registre.
    """
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    return yaml


def _load_registry_for_write(root: Path) -> Any:
    """Document round-trip ; lève si le registre n'existe pas encore.

    Contrairement à :func:`_load_registry_mapping` (lecture, ``{}`` toléré),
    une écriture sur un registre absent n'a pas de sens : il n'y a pas de
    ``routing``/``metadata`` à préserver, et en créer un à la volée devinerait
    une politique que seul ``grimoire standard init`` doit poser.
    """
    path = root / REGISTRY_FILE
    if not path.is_file():
        msg = f"{REGISTRY_FILE} absent — lancer `grimoire standard init` avant d'ajouter un fournisseur."
        raise ProviderRegistryError(msg)
    try:
        data = _yaml_roundtrip().load(path.read_text(encoding="utf-8"))
    except (YAMLError, OSError, UnicodeDecodeError) as exc:
        msg = f"{REGISTRY_FILE} illisible : {type(exc).__name__}: {exc}"
        raise ProviderRegistryError(msg) from exc
    if not isinstance(data, dict):
        msg = f"{REGISTRY_FILE} doit être une table YAML."
        raise ProviderRegistryError(msg)
    return data


def _save_registry(root: Path, data: Any) -> None:
    path = root / REGISTRY_FILE
    with path.open("w", encoding="utf-8") as handle:
        _yaml_roundtrip().dump(data, handle)


def add_provider(
    root: Path,
    provider_id: str,
    *,
    invocation: str,
    models: tuple[ModelSpec, ...] = (),
    currency: str = "api",
    provider_type: str = "hosted",
    allowed_capabilities: tuple[str, ...] = ("chat", "code"),
    enabled: bool = True,
) -> None:
    """Déclare un nouveau fournisseur — jamais de clé ici, voir ``grimoire.providers.secrets``.

    Refuse un ``id`` déjà présent (utiliser :func:`set_provider_enabled` pour
    une bascule), un ``currency``/``tier`` hors vocabulaire, ou une
    ``invocation`` sans ``{prompt}`` — les mêmes règles que
    ``standard_checks.verifiers._verify_provider_currency_and_tiers``,
    vérifiées ici pour ne jamais écrire une entrée qu'un
    ``grimoire standard verify`` rejetterait ensuite.
    """
    if currency not in SUPPORTED_CURRENCIES:
        msg = f"currency {currency!r} doit être l'une de {SUPPORTED_CURRENCIES}."
        raise ProviderWriteError(msg)
    for model in models:
        if model.tier not in SUPPORTED_MODEL_TIERS:
            msg = f"tier {model.tier!r} (modèle {model.id!r}) doit être l'un de {SUPPORTED_MODEL_TIERS}."
            raise ProviderWriteError(msg)
    if "{prompt}" not in invocation:
        msg = f"invocation {invocation!r} sans placeholder {{prompt}} : le prompt n'atteindrait jamais la commande."
        raise ProviderWriteError(msg)

    data = _load_registry_for_write(root)
    providers = data.setdefault("providers", [])
    if any(isinstance(entry, dict) and entry.get("id") == provider_id for entry in providers):
        msg = f"fournisseur {provider_id!r} déjà déclaré — utiliser `grimoire providers enable/disable`."
        raise ProviderWriteError(msg)

    providers.append(
        {
            "id": provider_id,
            "enabled": enabled,
            "provider_type": provider_type,
            "allowed_capabilities": list(allowed_capabilities),
            "default_models": [model.id for model in models],
            "currency": currency,
            "invocation": invocation,
            "models": [{"id": model.id, "tier": model.tier} for model in models],
            "data_policy": {
                "allowed_data_classes": ["public-docs", "project-source", "generated-artifacts", "non-secret-metadata"],
                "forbidden_data_classes": ["secrets", "credentials", "personal-data", "regulated-data"],
                "retention_notes": (
                    "Ajouté via `grimoire providers add` — clé lue depuis le magasin de secrets "
                    "(grimoire.providers.secrets), jamais dans ce registre."
                ),
            },
            "fallback_order": [],
            "audit": {"log_prompts": False, "log_metadata": True},
        }
    )
    _save_registry(root, data)


def set_provider_enabled(root: Path, provider_id: str, enabled: bool) -> None:
    """Bascule ``enabled`` pour *provider_id* ; lève si l'id n'est pas déclaré."""
    data = _load_registry_for_write(root)
    for entry in data.get("providers") or []:
        if isinstance(entry, dict) and entry.get("id") == provider_id:
            entry["enabled"] = enabled
            _save_registry(root, data)
            return
    msg = f"fournisseur {provider_id!r} introuvable dans le registre."
    raise ProviderWriteError(msg)


def remove_provider(root: Path, provider_id: str) -> None:
    """Retire *provider_id* du registre — le magasin de secrets n'est pas touché."""
    data = _load_registry_for_write(root)
    providers = data.get("providers") or []
    kept = [entry for entry in providers if not (isinstance(entry, dict) and entry.get("id") == provider_id)]
    if len(kept) == len(providers):
        msg = f"fournisseur {provider_id!r} introuvable dans le registre."
        raise ProviderWriteError(msg)
    data["providers"] = kept
    _save_registry(root, data)


def read_default_fallback_chain(root: Path) -> tuple[str, ...]:
    """``routing.default_fallback_chain`` — le filet posé au niveau registre.

    ``choose()`` s'en sert en dernier recours, après l'ordre du registre et
    le ``fallback_order`` de chaque fournisseur, pour atteindre un
    fournisseur activé qu'aucun des deux n'aurait cité.
    """
    data = _load_registry_mapping(root)
    routing = data.get("routing")
    if not isinstance(routing, dict):
        return ()
    return _str_tuple(routing.get("default_fallback_chain"))
