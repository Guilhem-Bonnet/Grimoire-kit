"""Les réglages « autorisé pour l'usage direct par Grimoire » et la vue qui les montre (épique #1477, lot B1).

**Où ça vit, et pourquoi.** Dans la zone de contrôle de l'humain (:func:`grimoire.missions.mandate.control_dir`),
un fichier par projet, et NON dans ``project-context.yaml`` (où vit ``cockpit.atelier_model``) ni dans le registre :
autoriser un modèle est un geste d'autorité, comme accorder un mandat. Un fichier du dépôt peut être réécrit par un
agent, un dépôt cloné peut arriver déjà réglé ; la zone de contrôle est celle que le frein refuse aux agents et où vit
déjà le journal d'autorité. Aucun secret n'y est jamais écrit (les clés restent dans le magasin de clés, présence seule).

**Défaut : rien d'autorisé.** Sans fichier, sans usage listé, ou avec un fichier illisible, aucun modèle n'est autorisé :
le mode sans modèle reste le défaut du produit.

**Lecture stricte.** Le fichier est borné en taille, jamais suivi à travers un lien, validé champ par champ ; ce qui est
invalide est ignoré (et dit dans ``problems``), jamais deviné. Une clé de modèle qu'aucune détection ne connaît ne
fait JAMAIS autorité : :func:`build_state` ne garde que les modèles de l'inventaire, et l'écriture refuse une clé inconnue.

**Écriture.** :func:`write_usage` remplace la liste ordonnée d'UN usage (autoriser, retirer, réordonner, plafonner en
un geste). Elle ne contrôle pas la clé de contrôle : la route qui l'appelle l'exige.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypeGuard

from grimoire.providers.availability import Inventory, ModelAvailability, detect
from grimoire.providers.direct_use import (
    DEFAULT_MIN,
    USAGE_LABELS,
    USAGES,
    Authorization,
    Decision,
    DirectState,
    choose_for,
)
from grimoire.providers.registry import ProviderRegistryError, read_registry

__all__ = [
    "MAX_CAP_TOKENS",
    "DirectUseError",
    "Settings",
    "build_state",
    "read_settings",
    "settings_path",
    "view",
    "write_usage",
]

SCHEMA = "grimoire-direct-use/v1"
MAX_BYTES = 65_536
MAX_MODELS_PER_USAGE = 32
MAX_CAP_TOKENS = 50_000_000
_MAX_KEY_CHARS = 200


class DirectUseError(ValueError):
    """Une écriture de réglage refusée (usage inconnu, modèle inconnu, plafond invalide), dite en clair."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Ce que le fichier autorise, validé ; ``problems`` dit ce qui a été ignoré."""

    allowed: Mapping[str, tuple[Authorization, ...]]
    problems: tuple[str, ...] = ()


def settings_path(root: Path) -> Path:
    """Le fichier de ce projet dans la zone de contrôle (nom dérivé du chemin du projet, jamais le chemin lui-même)."""
    from grimoire.missions.mandate import control_dir

    digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return control_dir() / "direct-use" / f"{digest}.json"


def _empty() -> dict[str, tuple[Authorization, ...]]:
    return dict.fromkeys(USAGES, ())


def _valid_key(value: Any) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_KEY_CHARS
        and value.isprintable()
        and ":" in value
        and not value.startswith(":")
    )


def _valid_cap(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_CAP_TOKENS


def read_settings(root: Path) -> Settings:
    """Les autorisations du projet ; ne lève jamais : un fichier absent, hostile ou invalide n'autorise rien."""
    path = settings_path(root)
    allowed = _empty()
    problems: list[str] = []
    try:
        if path.is_symlink() or path.parent.is_symlink():
            return Settings(allowed, ("réglages ignorés : lien symbolique",))
        if not path.is_file():
            return Settings(allowed)
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except OSError:
        return Settings(allowed, ("réglages illisibles : aucun modèle n'est autorisé",))
    if len(raw) > MAX_BYTES:
        return Settings(allowed, ("réglages trop gros : ignorés, aucun modèle n'est autorisé",))
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return Settings(allowed, ("réglages illisibles : aucun modèle n'est autorisé",))
    usages = data.get("usages") if isinstance(data, dict) else None
    if not isinstance(usages, dict):
        return Settings(allowed, ("réglages sans section « usages » : aucun modèle n'est autorisé",))
    for usage, block in usages.items():
        if usage not in allowed:
            problems.append(f"usage inconnu ignoré : {str(usage)[:40]!r}")
            continue
        entries = block.get("models") if isinstance(block, dict) else None
        if not isinstance(entries, list):
            problems.append(f"usage {usage} : liste de modèles invalide, ignorée")
            continue
        kept: list[Authorization] = []
        for entry in entries[:MAX_MODELS_PER_USAGE]:
            key = entry.get("key") if isinstance(entry, dict) else None
            cap = entry.get("daily_cap_tokens") if isinstance(entry, dict) else None
            if not _valid_key(key):
                problems.append(f"usage {usage} : entrée invalide ignorée")
            elif cap is not None and not _valid_cap(cap):
                problems.append(f"usage {usage} : plafond invalide pour {key} (entrée ignorée)")
            elif any(key == item.key for item in kept):
                problems.append(f"usage {usage} : doublon ignoré ({key})")
            else:
                kept.append(Authorization(key, cap))
        if len(entries) > MAX_MODELS_PER_USAGE:
            problems.append(f"usage {usage} : au plus {MAX_MODELS_PER_USAGE} modèles, le reste est ignoré")
        allowed[usage] = tuple(kept)
    return Settings(allowed, tuple(problems))


def write_usage(root: Path, usage: str, entries: Iterable[Any], known_keys: Iterable[str]) -> Settings:
    """Remplace la liste ordonnée des modèles autorisés pour *usage* ; ``DirectUseError`` si elle est invalide.

    Chaque entrée est ``{"key": ..., "daily_cap_tokens": entier ou None}``. Une clé que *known_keys* (l'inventaire
    détecté) ne contient pas est refusée : on n'autorise pas ce qu'on ne voit pas. Les autres usages sont conservés.
    """
    from grimoire.core.fsio import atomic_write, locked

    if usage not in USAGES:
        raise DirectUseError(f"usage inconnu : {usage!r} (attendus : {', '.join(USAGES)})")
    known = set(known_keys)
    items = list(entries)
    if len(items) > MAX_MODELS_PER_USAGE:
        raise DirectUseError(f"au plus {MAX_MODELS_PER_USAGE} modèles par usage")
    kept: list[Authorization] = []
    for entry in items:
        key = entry.get("key") if isinstance(entry, dict) else None
        cap = entry.get("daily_cap_tokens") if isinstance(entry, dict) else None
        if not _valid_key(key):
            raise DirectUseError("clé de modèle invalide")
        if key not in known:
            raise DirectUseError(f"modèle inconnu de la détection : {str(key)[:80]!r}")
        if cap is not None and not _valid_cap(cap):
            raise DirectUseError(f"plafond invalide pour {key} : un entier de jetons entre 1 et {MAX_CAP_TOKENS}")
        if any(key == item.key for item in kept):
            raise DirectUseError(f"modèle en double : {key}")
        kept.append(Authorization(key, cap))
    path = settings_path(root)
    if path.parent.is_symlink() or path.is_symlink():
        raise DirectUseError("réglages en lien symbolique : écriture refusée")
    current = read_settings(root)
    merged = dict(current.allowed)
    merged[usage] = tuple(kept)
    payload = {
        "$schema": SCHEMA,
        "project": root.name,
        "usages": {
            name: {"models": [{"key": a.key, "daily_cap_tokens": a.daily_cap_tokens} for a in auths]}
            for name, auths in merged.items()
            if auths
        },
    }
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with locked(path.with_name(path.name + ".lock"), timeout=5.0):
            atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
            path.chmod(0o600)
    except (OSError, TimeoutError) as exc:
        raise DirectUseError("réglages non enregistrés : fichier verrouillé ou illisible") from exc
    return read_settings(root)


# ── L'état de la décision ───────────────────────────────────────────────────


def _used_today(root: Path, models: Iterable[ModelAvailability]) -> dict[str, int]:
    """Ce que le kit sait de la consommation du jour, par modèle : une BORNE BASSE.

    Seul le compteur de jetons de l'Atelier (:func:`grimoire.tools.atelier_model.usage`) existe aujourd'hui ; il ne
    compte que les appels que l'Atelier a faits à l'API d'Anthropic, tous modèles confondus : il est attribué à chaque
    modèle de ce fournisseur. Rien n'est inventé pour les autres. Le lot B2 branchera les compteurs par modèle.
    """
    try:
        from grimoire.tools.atelier_model import usage

        day = usage(root, "")
    except Exception:  # un compteur illisible ne sait rien : aucune consommation connue
        return {}
    total = day.get("day") if isinstance(day, dict) and day.get("readable") else None
    if not isinstance(total, int) or total <= 0:
        return {}
    return {m.key: total for m in models if m.source == "registre" and m.provider_id == "anthropic"}


def _hold(root: Path) -> str:
    try:
        from grimoire.missions.quota_usage import kit_call_refusal

        return kit_call_refusal(root)
    except Exception:  # échec fermé : une règle qu'on ne peut pas lire retient les gestes payants
        return "règles d'usage illisibles : gestes payants refusés (échec fermé)"


def _data_refusals(root: Path, models: Iterable[ModelAvailability], data_class: str) -> dict[str, str]:
    from grimoire.providers.data_policy import data_policy_refusal, hosted_fallback_refusal

    try:
        specs = {spec.id: spec for spec in read_registry(root)}
    except ProviderRegistryError:
        specs = {}
    refusals: dict[str, str] = {}
    for model in models:
        if model.source == "hote":
            reason = hosted_fallback_refusal(root, data_class)
        elif model.configured and model.provider_id in specs:
            reason = data_policy_refusal(root, specs[model.provider_id], data_class)
        else:
            reason = ""
        if reason:
            refusals[model.key] = reason
    return refusals


def build_state(
    root: Path,
    inventory: Inventory,
    *,
    settings: Settings | None = None,
    data_class: str = "",
    hold: str | None = None,
) -> DirectState:
    """Assemble l'état de la décision : l'inventaire, les autorisations ramenées à ce qui est détecté, la consommation."""
    read = settings if settings is not None else read_settings(root)
    models = inventory.by_key()
    return DirectState(
        models=models,
        allowed=dict(read.allowed),  # une clé inconnue de l'inventaire reste dans la liste : la décision l'ignore, et le dit
        used_today=_used_today(root, inventory.models),
        hold_reason=_hold(root) if hold is None else hold,
        data_refusals=_data_refusals(root, inventory.models, data_class),
    )


def view(
    root: Path,
    *,
    inventory: Inventory | None = None,
    verify: bool = False,
    now: datetime | None = None,
    current: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """La charge utile de la page et du CLI : inventaire, autorisations par usage, décision de maintenant et sa trace."""
    seen = inventory if inventory is not None else detect(root, verify=verify, now=now)
    settings = read_settings(root)
    state = build_state(root, seen, settings=settings)
    known = seen.by_key()
    usages: list[dict[str, Any]] = []
    ignored: list[str] = []
    for usage in USAGES:
        authorized: list[dict[str, Any]] = []
        for auth in settings.allowed.get(usage, ()):
            if auth.key not in known:
                ignored.append(f"{USAGE_LABELS[usage]} : {auth.key} (non détecté)")
            authorized.append(
                {
                    "key": auth.key,
                    "daily_cap_tokens": auth.daily_cap_tokens,
                    "used_today": state.used_today.get(auth.key),
                    "detected": auth.key in known,
                }
            )
        decision: Decision = choose_for(usage, state, current=(current or {}).get(usage))
        usages.append(
            {
                "id": usage,
                "label": USAGE_LABELS[usage],
                "authorized": authorized,
                "decision": decision.to_dict(),
                "min_capability": DEFAULT_MIN[usage].to_dict(),
            }
        )
    return {
        "inventory": seen.to_dict(),
        "usages": usages,
        "any_authorized": any(a["detected"] for u in usages for a in u["authorized"]),
        "problems": list(settings.problems),
        "ignored": ignored,
        "hold": state.hold_reason or None,
        "used_note": "Consommation du jour : borne basse, seulement ce que l'Atelier a compté.",
    }
