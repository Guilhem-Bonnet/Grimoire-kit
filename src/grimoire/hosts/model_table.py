"""Table palier x hôte x fournisseur → modèle (épique #1281, lot L5).

Les paliers sont ceux du kit : ``cheap``, ``mid``, ``strong`` (``orchestrator_policy.TIERS``,
``dispatch.start_tier_for``). Un palier ne nomme aucun modèle : c'est cette table qui le traduit
pour un hôte et un fournisseur donnés. Elle remplace les alias ``haiku``/``sonnet``/``opus`` que
le routage écrivait en dur (hook ``dispatch-model``, politique de dispatch, worker, route forte).

Trois couches, de la plus forte à la plus faible :

- ``project`` : ``dispatch.models.<hôte>.<fournisseur>.<palier>`` de
  ``_grimoire/standard/orchestration-policy.yaml`` (``*`` pour tout fournisseur ; les paliers
  posés directement sous l'hôte valent ``*``) ;
- ``legacy`` : les alias historiques ``dispatch.copilot_models`` et ``dispatch.cursor_models``,
  toujours lus, clés ``haiku``/``sonnet``/``opus`` ou noms de paliers ;
- ``kit`` : :data:`KIT_DEFAULTS`, seulement Claude Code x Anthropic (``cheap`` → ``haiku``,
  ``mid`` → ``sonnet``, ``strong`` → ``opus``).

Aucune correspondance n'est inventée : un palier qu'aucune couche ne nomme pour cet hôte et ce
fournisseur rend ``None``, et l'appelant omet ``model``.

Capacités (lot L3) : la résolution peut écarter un modèle dont une capacité requise est connue à
« non » (``tools: false`` pour un worker), et passe alors à la couche suivante. Une capacité
inconnue n'écarte jamais rien, et un défaut du kit n'est jamais écarté : une sonde ou un registre
ne peut pas vider la table.

:func:`resolve_model` est pure ; :func:`load_model_table` lit le fichier et ne lève jamais :
une valeur illisible est ignorée et notée dans :attr:`ModelTable.errors`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from grimoire.providers.capabilities import ProviderCapabilities

__all__ = [
    "ANY_PROVIDER",
    "CLAUDE_CODE",
    "KIT_DEFAULTS",
    "LEGACY_KEYS",
    "LEGACY_TIER_ALIASES",
    "SOURCE_KIT",
    "SOURCE_LEGACY",
    "SOURCE_PROJECT",
    "TIERS",
    "ModelChoice",
    "ModelEntry",
    "ModelTable",
    "host_id",
    "kit_model",
    "load_model_table",
    "normalize_tier",
    "resolve_model",
    "resolved_rows",
]

#: Les paliers du kit, du moins cher au plus fort.
TIERS: tuple[str, ...] = ("cheap", "mid", "strong")
#: Les alias historiques (noms Claude) lus comme paliers : clés de ``copilot_models``/``cursor_models``.
LEGACY_TIER_ALIASES: dict[str, str] = {"haiku": "cheap", "sonnet": "mid", "opus": "strong"}
#: Tout fournisseur.
ANY_PROVIDER = "*"

SOURCE_PROJECT = "project"
SOURCE_LEGACY = "legacy"
SOURCE_KIT = "kit"

#: Identifiant d'hôte de Claude Code (valeur de ``bridges.schemas.HostId.CLAUDE_CODE_CLI``).
CLAUDE_CODE = "host-claude-code-cli"
_COPILOT = "host-github-copilot"
_CURSOR = "host-cursor"

#: Les défauts livrés par le kit : seulement là où le nom du modèle est connu de l'hôte.
KIT_DEFAULTS: dict[tuple[str, str], dict[str, str]] = {
    (CLAUDE_CODE, "anthropic"): {"cheap": "haiku", "mid": "sonnet", "strong": "opus"},
}
#: Le fournisseur d'un hôte quand l'appelant n'en nomme pas.
_HOST_DEFAULT_PROVIDER: dict[str, str] = {CLAUDE_CODE: "anthropic"}
#: Clé historique → hôte qu'elle décrit.
LEGACY_KEYS: dict[str, str] = {"copilot_models": _COPILOT, "cursor_models": _CURSOR}

_POLICY_FILE = Path("_grimoire") / "standard" / "orchestration-policy.yaml"
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]/@-]{0,199}")
#: Ce que YAML relirait comme un autre type dans un frontmatter (``model: true``) : refusé comme nom de modèle.
_YAML_SCALARS = frozenset({"null", "nul", "none", "true", "false", "yes", "no", "on", "off", "y", "n"})
_NUMERIC = re.compile(r"[0-9][0-9._]*")


def valid_model_name(model: str) -> bool:
    """Un nom de modèle lisible et sans ambiguïté YAML (ni ``null``, ni ``true``, ni ``1.5``)."""
    return bool(_MODEL.fullmatch(model)) and model.lower() not in _YAML_SCALARS and not _NUMERIC.fullmatch(model)


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """Une ligne déclarée : ``(hôte, fournisseur, palier) → modèle`` et sa couche."""

    host: str
    provider: str
    tier: str
    model: str
    source: str


@dataclass(frozen=True, slots=True)
class ModelTable:
    """Les lignes du projet et des alias historiques ; les défauts du kit restent dans :data:`KIT_DEFAULTS`."""

    entries: tuple[ModelEntry, ...] = ()
    errors: tuple[str, ...] = ()

    def lookup(self, host: str, provider: str, tier: str, source: str) -> str:
        for entry in self.entries:
            if (entry.host, entry.provider, entry.tier, entry.source) == (host, provider, tier, source):
                return entry.model
        return ""


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """Le modèle d'un palier pour un hôte et un fournisseur, ou ``None`` ; ``source`` dit quelle couche l'a donné."""

    tier: str
    host: str
    provider: str
    model: str | None
    source: str = ""
    skipped: tuple[tuple[str, str], ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "host": self.host,
            "provider": self.provider,
            "model": self.model,
            "source": self.source or None,
            "skipped": [{"model": model, "reason": reason} for model, reason in self.skipped],
        }


def normalize_tier(tier: str) -> str:
    """``cheap``/``mid``/``strong`` ; un alias historique (``haiku``…) donne son palier ; sinon ``""``."""
    key = (tier or "").strip().lower()
    if key in TIERS:
        return key
    return LEGACY_TIER_ALIASES.get(key, "")


def _host_id(name: str) -> str:
    key = (name or "").strip().lower()
    if not key or key == CLAUDE_CODE:
        return CLAUDE_CODE if key else ""
    from grimoire.hosts.capabilities import resolve_host

    host = resolve_host(key)
    return host.value if host is not None else ""


def host_id(name: str) -> str:
    """L'identifiant d'hôte (``host-github-copilot``) de *name*, alias accepté ; ``""`` s'il est inconnu."""
    return _host_id(name)


def _provider_id(name: str) -> str:
    key = (name or "").strip().lower()
    if key in ("", ANY_PROVIDER, "anthropic"):
        return key
    from grimoire.providers.capabilities import canonical_provider

    return canonical_provider(key)


def kit_model(tier: str) -> str:
    """Le défaut du kit pour Claude Code x Anthropic (``cheap`` → ``haiku``…) ; ``""`` pour un palier inconnu."""
    return KIT_DEFAULTS[(CLAUDE_CODE, "anthropic")].get(normalize_tier(tier), "")


def _rejection(model: str, capabilities: ProviderCapabilities | None, requires: Iterable[str]) -> str:
    """La capacité requise que *model* n'a pas (connue à ``False``), ou ``""`` ; une capacité inconnue ne compte pas."""
    if capabilities is None:
        return ""
    entry = capabilities.model(model)
    for name in requires:
        try:
            capability = entry.get(name)
        except KeyError:
            continue
        if capability.value is False:
            return f"{name}: non ({capability.provenance})"
    return ""


def resolve_model(
    tier: str,
    host: str = CLAUDE_CODE,
    provider: str = "",
    *,
    table: ModelTable | None = None,
    capabilities: ProviderCapabilities | None = None,
    requires: Iterable[str] = (),
) -> ModelChoice:
    """Le modèle de *tier* pour *host* et *provider* — pure, ne lit rien.

    *host* : identifiant (``host-github-copilot``) ou alias (``copilot``). *provider* vide : le
    fournisseur par défaut de l'hôte (Anthropic pour Claude Code), sinon ``*``. Ordre : projet
    (fournisseur exact, puis ``*``), alias historique, défaut du kit. Un candidat dont une
    capacité de *requires* est connue à ``False`` dans *capabilities* est écarté (``skipped``).
    """
    tier_id = normalize_tier(tier)
    host_id = _host_id(host)
    provider_id = _provider_id(provider) or _HOST_DEFAULT_PROVIDER.get(host_id, ANY_PROVIDER)
    if not tier_id or not host_id:
        return ModelChoice(tier=tier_id or tier, host=host_id or host, provider=provider_id, model=None)
    table = table or ModelTable()
    providers = (provider_id, ANY_PROVIDER) if provider_id != ANY_PROVIDER else (ANY_PROVIDER,)
    candidates: list[tuple[str, str]] = [
        (table.lookup(host_id, p, tier_id, SOURCE_PROJECT), SOURCE_PROJECT) for p in providers
    ]
    candidates.append((table.lookup(host_id, ANY_PROVIDER, tier_id, SOURCE_LEGACY), SOURCE_LEGACY))
    candidates += [(KIT_DEFAULTS.get((host_id, p), {}).get(tier_id, ""), SOURCE_KIT) for p in providers]
    requires = tuple(requires)
    skipped: list[tuple[str, str]] = []
    for model, source in candidates:
        if not model:
            continue
        # Les défauts du kit ne sont jamais écartés : une capacité lue dans une sonde ou un registre ne vide pas la table.
        if source != SOURCE_KIT and (reason := _rejection(model, capabilities, requires)):
            skipped.append((model, reason))
            continue
        return ModelChoice(tier_id, host_id, provider_id, model, source, tuple(skipped))
    return ModelChoice(tier_id, host_id, provider_id, None, "", tuple(skipped))


def _tier_block(
    block: Mapping[str, Any], host: str, provider: str, source: str, where: str
) -> tuple[list[ModelEntry], list[str]]:
    entries: list[ModelEntry] = []
    errors: list[str] = []
    for raw_tier, raw_model in block.items():
        tier = normalize_tier(str(raw_tier))
        if not tier:
            errors.append(f"{where}.{raw_tier} : palier inconnu (attendu {', '.join(TIERS)})")
            continue
        if raw_model in (None, ""):
            continue
        model = raw_model.strip() if isinstance(raw_model, str) else ""
        if not valid_model_name(model):
            errors.append(f"{where}.{raw_tier} : nom de modèle illisible")
            continue
        entries.append(ModelEntry(host, provider, tier, model, source))
    return entries, errors


def _project_entries(models: Any) -> tuple[list[ModelEntry], list[str]]:
    if models is None:
        return [], []
    if not isinstance(models, dict):
        return [], ["dispatch.models : attendu un bloc hôte → fournisseur → palier"]
    entries: list[ModelEntry] = []
    errors: list[str] = []
    for raw_host, by_provider in models.items():
        where = f"dispatch.models.{raw_host}"
        host = _host_id(str(raw_host))
        if not host:
            errors.append(f"{where} : hôte inconnu")
            continue
        if not isinstance(by_provider, dict):
            errors.append(f"{where} : attendu un bloc fournisseur → palier")
            continue
        shorthand = {k: v for k, v in by_provider.items() if normalize_tier(str(k))}
        found, bad = _tier_block(shorthand, host, ANY_PROVIDER, SOURCE_PROJECT, where)
        entries += found
        errors += bad
        for raw_provider, tiers in by_provider.items():
            if raw_provider in shorthand:
                continue
            sub = f"{where}.{raw_provider}"
            if not isinstance(tiers, dict):
                errors.append(f"{sub} : attendu un bloc palier → modèle")
                continue
            found, bad = _tier_block(tiers, host, _provider_id(str(raw_provider)), SOURCE_PROJECT, sub)
            entries += found
            errors += bad
    return entries, errors


def table_from_dispatch(block: Any) -> ModelTable:
    """La table d'un bloc ``dispatch:`` déjà lu (``dispatch.models`` puis les alias historiques)."""
    if not isinstance(block, dict):
        return ModelTable()
    entries, errors = _project_entries(block.get("models"))
    for key, host in LEGACY_KEYS.items():
        legacy = block.get(key)
        if legacy is None:
            continue
        if not isinstance(legacy, dict):
            errors.append(f"dispatch.{key} : attendu un bloc palier → modèle")
            continue
        found, bad = _tier_block(legacy, host, ANY_PROVIDER, SOURCE_LEGACY, f"dispatch.{key}")
        entries += found
        errors += bad
    return ModelTable(tuple(entries), tuple(errors))


def load_model_table(project_root: Path) -> ModelTable:
    """La table du projet (politique d'orchestration du standard) ; fichier absent ou illisible : table vide."""
    path = project_root / _POLICY_FILE
    if not path.is_file():
        return ModelTable()
    try:
        from ruamel.yaml import YAML

        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return ModelTable(errors=(f"{_POLICY_FILE.as_posix()} illisible : {type(exc).__name__}",))
    return table_from_dispatch(data.get("dispatch") if isinstance(data, dict) else None)


def resolved_rows(table: ModelTable) -> tuple[ModelChoice, ...]:
    """Chaque couple (hôte, fournisseur) connu d'une couche, résolu pour les trois paliers."""
    pairs: list[tuple[str, str]] = list(KIT_DEFAULTS)
    for entry in table.entries:
        if (entry.host, entry.provider) not in pairs:
            pairs.append((entry.host, entry.provider))
    return tuple(resolve_model(tier, host, provider, table=table) for host, provider in pairs for tier in TIERS)
