"""La fabrique des moteurs de la conversation : qui parle à ce tour, décidé à chaque tour (épique #1477, lot B2).

Branchée par :func:`grimoire.tools.workspace_atelier_conversation.set_moteur`. À CHAQUE appel (donc à chaque tour et à chaque
lecture) elle recalcule, pour chaque usage, la décision de :func:`grimoire.providers.direct_use.choose_for`, sans rien garder
d'un tour à l'autre : un modèle devenu indisponible, ou une autorisation retirée, change le moteur au tour suivant sans
casser la conversation.

Règles non négociables :

- **Aucun modèle n'est appelé s'il n'est pas AUTORISÉ pour l'usage** par l'humain, avec une empreinte qui désigne encore ce
  que le nom désigne (``choose_for`` ignore une autorisation périmée). Sans aucune autorisation, la fabrique rend le moteur
  sans modèle SANS rien détecter : aucun réseau, aucun processus.
- **Jamais de repli automatique vers plus coûteux ou plus exposé** : le modèle « courant » de chaque usage est le PREMIER de la
  liste de l'humain (sa préférence) ; s'il tombe, ``choose_for`` ne bascule que vers un modèle ni plus coûteux ni plus exposé,
  sinon elle PROPOSE : on retombe sur le moteur sans modèle et la vue dit pourquoi et propose (``proposition``). Un modèle
  « trop léger » pour le geste est de même proposé, jamais substitué.
- **La politique de données s'applique** (``build_state`` : classe de données du projet, fournisseurs hébergés fermés si la
  classe n'est pas admise) ; la règle d'usage ``hold``/``pause`` et les plafonds de jetons aussi.
- **Ce qui est dit** : chaque usage porte ``local`` et ``sortie_de_texte`` d'après la ligne détectée (jamais déclarés par le moteur).

Adaptateurs : Ollama (boucle locale reconnue), serveur au format OpenAI (passerelle admise, ou serveur local sans clé),
API Messages d'Anthropic (client existant de l'Atelier), API Gemini de Google (``google-gemini``, adresse officielle fixe). L'agent installé sur la machine (source « hôte ») n'a pas d'adaptateur :
le kit ne sait que sonder sa connexion, pas lui poser une question sans lancer un agent autonome ; il n'est donc jamais appelé.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from grimoire.core.cadrage_conversation_engine import MoteurSansModele
from grimoire.core.cadrage_conversation_moteur_vue import Presentation, Rendu, UsageVue, sans_modele
from grimoire.providers.availability import Inventory, ModelAvailability, detect, fingerprint, key_command
from grimoire.providers.direct_use import Decision, choose_for
from grimoire.providers.direct_use_settings import Settings, build_state, read_settings
from grimoire.tools.atelier_engines.moteur import MoteurModeles
from grimoire.tools.atelier_engines.transport import Backend, EngineError, Transport

__all__ = ["DELAI_DEFAUT_S", "USAGES_MOTEUR", "composer", "delai_moteur", "fabrique"]

#: Les usages que la conversation fait décider (ceux de ``direct_use`` ; « rédaction » n'a pas encore de geste).
USAGES_MOTEUR: tuple[str, ...] = ("conversation", "brainstorm")
#: Délai d'un appel : plus court que celui du gardien (8 s), pour que l'appel finisse avant d'être abandonné.
DELAI_DEFAUT_S = 6.0
DELAI_MIN_S, DELAI_MAX_S = 0.5, 7.5
_CONFIG = ("cockpit", "atelier_conversation")
_ID_MAX = 40


def delai_moteur(root: Path) -> float:
    """``cockpit.atelier_conversation.moteur_delai_s`` (0,5 à 7,5 s) ; toute autre valeur retombe sur le défaut."""
    from grimoire.tools import workspace_atelier as wa

    try:
        node: Any = wa._load(root)
        for key in _CONFIG:
            node = node.get(key) if hasattr(node, "get") else None
        value = node.get("moteur_delai_s") if hasattr(node, "get") else None
    except Exception:
        return DELAI_DEFAUT_S
    if isinstance(value, bool) or not isinstance(value, int | float) or not DELAI_MIN_S <= value <= DELAI_MAX_S:
        return DELAI_DEFAUT_S
    return float(value)


def _ident(key: str) -> str:
    return "modele-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _backend(
    row: ModelAvailability, root: Path, slug: str, inventory: Inventory, delai: float, transport: Transport | None,
    cle_anthropic: str | None,
) -> tuple[Backend | None, str]:
    """Le client de cette ligne détectée, ou ``(None, pourquoi il n'y en a pas)`` ; jamais de valeur de clé dans le motif."""
    from grimoire.providers.capabilities import DOCUMENTED_DEFAULTS, LITELLM, PROVIDER_ALIASES, canonical_provider
    from grimoire.providers.registry import ProviderRegistryError, read_registry
    from grimoire.providers.secrets import env_for_provider, provider_key_var
    from grimoire.tools import atelier_model as am
    from grimoire.tools.atelier_engines.anthropic import AnthropicBackend
    from grimoire.tools.atelier_engines.gemini import GeminiBackend
    from grimoire.tools.atelier_engines.ollama import OllamaBackend
    from grimoire.tools.atelier_engines.openai_compat import OpenAICompatBackend

    try:
        if row.source == "hote":
            return None, "un agent installé ne se pose pas une question sans lancer un agent autonome : aucun adaptateur"
        if row.source == "ollama":
            if not inventory.ollama.reachable:
                return None, "Ollama ne répond pas"
            return OllamaBackend(inventory.ollama.url, row.model_id, timeout_s=delai, transport=transport), ""
        try:
            spec = next((s for s in read_registry(root) if s.id == row.provider_id), None)
        except ProviderRegistryError:
            spec = None
        if spec is None or not row.model_id:
            return None, "fournisseur ou modèle introuvable dans le registre"
        # UNE source de vérité : la spec lue ICI sert à la fois à la vérification et à l'adresse. Son empreinte doit être
        # celle que l'autorisation (et donc la détection) désigne ; sinon le registre a changé depuis : rien ne part.
        courante = fingerprint(
            "registre", spec.id, spec.provider_type, spec.currency or "", spec.invocation or "", spec.base_url or "",
            spec.anthropic_base_url or "", row.model_id,
        )
        if courante != row.fingerprint:
            return None, "ce modèle a changé depuis ton autorisation : ré-autorise-le par le geste protégé"
        if row.provider_id == "anthropic" and not spec.base_url and not spec.anthropic_base_url:
            key = cle_anthropic if cle_anthropic is not None else am.api_key()
            if not key:
                return None, "aucune clé posée pour ce fournisseur"
            return AnthropicBackend(
                row.model_id, key, timeout_s=delai, transport=transport,
                on_response=lambda raw: am.account_response(root, slug, row.model_id, raw),
            ), ""
        if canonical_provider(spec.id, spec.provider_type) == "google-gemini" and not spec.base_url and not spec.anthropic_base_url:
            # Adresse officielle CONSTRUITE par l'adaptateur. Clé : le MAGASIN du kit seulement, sous la variable que la détection
            # attend (``<ID>_API_KEY``), jamais l'environnement : une variable générique exportée pour un autre service Google
            # ne part pas vers Gemini. (La détection regarde aussi l'environnement : elle peut dire « clé présente » là où ce
            # tour répond « saisis la clé » ; elle vit sous providers/, hors de ce lot.)
            key = env_for_provider(spec.id).get(provider_key_var(spec.id), "").strip()
            if not key:
                return None, f"aucune clé dans le magasin du kit : saisis-la avec `{key_command(spec.id)}`"
            return GeminiBackend(row.model_id, key, timeout_s=delai, transport=transport), ""
        if spec.base_url and spec.provider_type == "local":
            return OpenAICompatBackend(spec.base_url, row.model_id, timeout_s=delai, transport=transport), ""
        canon = canonical_provider(spec.id)
        # Même règle que la sonde de capacités : l'id « litellm » désigne une passerelle, jamais un fournisseur documenté.
        documented = canon != LITELLM and (spec.id.lower() in PROVIDER_ALIASES or canon in DOCUMENTED_DEFAULTS)
        if spec.base_url and spec.provider_type == "litellm" and not documented:
            key = env_for_provider(spec.id).get(provider_key_var(spec.id), "").strip()
            if not key:
                return None, "aucune clé posée pour cette passerelle"
            return OpenAICompatBackend(spec.base_url, row.model_id, key=key, timeout_s=delai, transport=transport), ""
        return None, "aucun adaptateur pour ce fournisseur (Ollama, serveur OpenAI-compatible admis, Anthropic et Gemini seulement)"
    except EngineError as exc:
        return None, str(exc)


def _vue(usage: str, decision: Decision, state_models: dict[str, ModelAvailability], why_none: str, row: ModelAvailability | None) -> UsageVue:
    if decision.kind == "use" and row is not None and not why_none:
        warning = f" ({decision.warning})" if decision.warning else ""
        return UsageVue(
            usage, _ident(row.key), row.label, row.egress == "rien", row.egress == "texte", decision.reason + warning, None)
    if decision.kind == "use" and row is not None:
        return sans_modele(f"{row.label} : {why_none}", usage=usage)
    if decision.kind == "propose":
        shown = state_models.get(decision.key)
        proposal = {"key": decision.key, "libelle": decision.label or decision.key, "raison": decision.reason}
        extra = "" if shown is None else f" ({'texte envoyé hors de la machine' if shown.egress == 'texte' else 'sur ta machine'})"
        return sans_modele(f"{decision.reason}{extra}", proposal, usage=usage)
    return sans_modele(decision.reason, usage=usage)


def composer(
    root: Path, slug: str, *, detecter: Callable[[Path], Inventory] | None = None, transport: Transport | None = None,
    reglages: Settings | None = None, cle_anthropic: str | None = None, delai_s: float | None = None,
) -> Rendu:
    """Le moteur de CE tour et ce que la vue en dira ; recalculé à chaque appel, sans mémoire d'un tour à l'autre."""
    settings = reglages
    if settings is None:
        try:
            settings = read_settings(root)
        except Exception:  # des réglages qu'on ne sait pas lire n'autorisent rien
            return Rendu(MoteurSansModele(), Presentation((sans_modele("réglages des modèles illisibles : aucun modèle autorisé"),)))
    if not any(settings.allowed.get(u) for u in USAGES_MOTEUR):  # le défaut : rien d'autorisé, rien n'est détecté ni appelé
        return Rendu(MoteurSansModele(), Presentation((
            sans_modele("Aucun modèle n'est autorisé pour la conversation : je fonctionne sans modèle."),
            sans_modele("Aucun modèle n'est autorisé pour le brainstorm : je fonctionne sans modèle.", usage="brainstorm"),
        )))
    delai = delai_moteur(root) if delai_s is None else delai_s
    try:
        inventory = (detecter or detect)(root)
        state = build_state(root, inventory, settings=settings, data_class="")
    except Exception:
        return Rendu(MoteurSansModele(), Presentation((sans_modele("détection des modèles impossible : je fonctionne sans modèle"),)))
    vues: list[UsageVue] = []
    backends: dict[str, Backend | None] = {}
    # Un agent installé n'a pas d'adaptateur : il ne retient pas la décision, les autres modèles autorisés parlent à sa place.
    ecartes = {u: [a.key for a in settings.allowed.get(u, ()) if a.key.startswith("hote:")] for u in USAGES_MOTEUR}
    state = replace(state, allowed={u: tuple(a for a in auths if not a.key.startswith("hote:")) for u, auths in state.allowed.items()})
    for usage in USAGES_MOTEUR:
        allowed = state.allowed.get(usage, ())
        first = allowed[0].key if allowed else None  # la préférence de l'humain : c'est d'elle que l'on ne s'éloigne qu'à moindre coût
        decision = choose_for(usage, state, current=first)
        row = state.models.get(decision.key) if decision.kind == "use" else None
        backend, why = (None, "") if row is None else _backend(row, root, slug, inventory, delai, transport, cle_anthropic)
        backends[usage] = backend
        vue = _vue(usage, decision, dict(state.models), why, row)
        if ecartes[usage]:
            noms = ", ".join(state.models[k].label if k in state.models else k for k in ecartes[usage])
            note = f"« {noms} » n'a pas d'adaptateur (aucun adaptateur : il n'est jamais appelé)"
            vue = replace(vue, raison=f"{note} ; {vue.raison}" if not allowed else f"{vue.raison} ; {note}")
        vues.append(vue)
    if not any(backends.values()):
        return Rendu(MoteurSansModele(), Presentation(tuple(vues)))
    speaking = next(v for u, v in zip(USAGES_MOTEUR, vues, strict=True) if backends[u] is not None)
    cle = (str(root.resolve()), slug)
    moteur = MoteurModeles(
        speaking.id[:_ID_MAX], speaking.libelle, backends, cle=cle, duree_reflexion_s=delai + 2.0,
        sortie_de_texte=any(v.sortie_de_texte for u, v in zip(USAGES_MOTEUR, vues, strict=True) if backends[u] is not None),
    )
    return Rendu(moteur, Presentation(tuple(vues), cle))


def fabrique(root: Path, slug: str) -> Rendu:
    """Ce que :func:`set_moteur` reçoit : ``factory(root, slug)``."""
    return composer(root, slug)
