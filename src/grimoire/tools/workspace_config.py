"""Lectures de l'espace Configuration : hooks et fournisseurs (P66).

Deux vues en lecture seule, les mêmes sources que ``grimoire hooks status`` côté
registre de sûreté des hooks et ``grimoire providers status`` :

- :func:`hooks_view` lit ``_grimoire-runtime/_config/hook-safety-registry.json`` ;
- :func:`providers_view` lit le registre des fournisseurs et son état runtime.

Aucune écriture : passer un hook en observation, le promouvoir ou remettre un
budget à zéro restent des commandes de la CLI, que le cockpit affiche à copier.
Un fichier absent n'est pas une panne (le projet n'a rien déclaré) ; un fichier
illisible est dit dans ``error`` plutôt que rendu comme une liste vide.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["HOOK_MODES", "REGISTRY_RELPATH", "hooks_view", "providers_view"]

REGISTRY_RELPATH = Path("_grimoire-runtime") / "_config" / "hook-safety-registry.json"

#: Mode du registre de sûreté → mot affiché et teinte de badge. Un mode que le
#: registre invente plus tard reste lisible tel quel, en teinte neutre.
HOOK_MODES: dict[str, tuple[str, str]] = {
    "enforced": ("appliqué", "ok"),
    "canary": ("essai", "acc"),
    "shadow": ("observation", ""),
}

#: Où chaque hôte déclare ses hooks (fichiers ou dossiers JSON relatifs au projet).
_HOST_FILES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("claude", "Claude Code", (".claude/settings.json",)),
    ("copilot", "Copilot", (".github/hooks",)),
    ("cursor", "Cursor", (".cursor/hooks.json",)),
)


def _host_texts(root: Path) -> dict[str, list[tuple[str, str]]]:
    """Par hôte, les ``(nom de fichier, contenu)`` de ses fichiers de hooks lisibles."""
    found: dict[str, list[tuple[str, str]]] = {}
    for host, _label, relpaths in _HOST_FILES:
        texts: list[tuple[str, str]] = []
        for rel in relpaths:
            path = root / rel
            files = sorted(path.glob("*.json")) if path.is_dir() else [path]
            for file in files:
                try:
                    texts.append((file.stem, file.read_text(encoding="utf-8")))
                except (OSError, UnicodeDecodeError):
                    continue
        found[host] = texts
    return found


def _hosts_of(hook_id: str, texts: dict[str, list[tuple[str, str]]]) -> list[str]:
    """Les hôtes dont un fichier de hooks nomme explicitement *hook_id* (``--hook-id <id>`` ou nom de fichier)."""
    pattern = re.compile(rf"--hook-id\s+{re.escape(hook_id)}(?![\w-])")
    return [
        host
        for host, files in texts.items()
        if any(stem == hook_id or pattern.search(content) for stem, content in files)
    ]


def hooks_view(project_root: Path) -> dict[str, Any]:
    """Les hooks du registre de sûreté : identifiant, événement, mode, hôtes, comptes.

    ``registry`` dit si le fichier existe ; ``error`` porte le motif quand il est
    présent mais illisible. Les hôtes ne sont attribués que sur un nom explicite
    (``--hook-id`` ou nom de fichier) : un hook qu'aucun fichier ne nomme n'est
    rattaché à aucun hôte plutôt qu'à tous.
    """
    root = project_root.resolve()
    path = root / REGISTRY_RELPATH
    empty: dict[str, Any] = {"registry": False, "error": None, "hooks": [], "counts": {}, "hosts": [], "total": 0}
    if not path.is_file():
        return empty
    empty["registry"] = True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {**empty, "error": f"{REGISTRY_RELPATH.as_posix()} illisible : {type(exc).__name__}"}
    entries = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {**empty, "error": f"{REGISTRY_RELPATH.as_posix()} sans table « hooks »"}

    texts = _host_texts(root)
    hooks: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for hook_id, entry in entries.items():
        entry = entry if isinstance(entry, dict) else {}
        mode = str(entry.get("mode") or "")
        label, tone = HOOK_MODES.get(mode, (mode or "inconnu", ""))
        counts[label] = counts.get(label, 0) + 1
        hooks.append(
            {
                "id": str(hook_id),
                "event": str(entry.get("event") or ""),
                "mode": mode,
                "label": label,
                "tone": tone,
                "hosts": _hosts_of(str(hook_id), texts),
            }
        )
    # Ordre stable, celui de la maquette : appliqué, essai, observation, puis le reste.
    ordered = [label for label, _tone in HOOK_MODES.values()]
    counts = dict(sorted(counts.items(), key=lambda kv: (ordered.index(kv[0]) if kv[0] in ordered else len(ordered), kv[0])))
    labels = {host: label for host, label, _ in _HOST_FILES}
    present = [host for host, _label, _rel in _HOST_FILES if any(host in h["hosts"] for h in hooks)]
    return {
        "registry": True,
        "error": None,
        "hooks": hooks,
        "counts": counts,
        "hosts": [{"id": host, "label": labels[host]} for host in present],
        "total": len(hooks),
    }


def providers_view(project_root: Path) -> dict[str, Any]:
    """Les fournisseurs de modèles : état, palier, modèles, et le prochain choix par palier.

    Reprend la charge utile de ``grimoire providers status --json`` (même
    ``_provider_json``) et y ajoute un ``status`` lisible (``disponible``,
    ``refroidi``, ``suspendu``, ``désactivé``).
    """
    from grimoire.cli.cmd_providers import _provider_json
    from grimoire.providers.registry import SUPPORTED_MODEL_TIERS, ProviderRegistryError, read_registry
    from grimoire.providers.routing import choose
    from grimoire.providers.state import load_state

    root = project_root.resolve()
    try:
        specs = read_registry(root)
    except ProviderRegistryError as exc:
        return {"registry": True, "error": str(exc), "providers": [], "next_choice": {}}
    now = datetime.now(UTC)
    state = load_state(root)
    providers: list[dict[str, Any]] = []
    for spec in specs:
        item = _provider_json(spec, state, now=now)
        if not item["enabled"]:
            status, tone = "désactivé", ""
        elif item["suspended"]:
            status, tone = "suspendu", "bad"
        elif item["cooling_down"]:
            status, tone = "refroidi", "warn"
        else:
            status, tone = "disponible", "ok"
        item["status"], item["tone"] = status, tone
        item["tiers"] = sorted({m["tier"] for m in item["models"] if m.get("tier")})
        providers.append(item)
    next_choice = {tier: (spec.id if (spec := choose(root, tier, now=now)) else None) for tier in SUPPORTED_MODEL_TIERS}
    return {"registry": bool(specs), "error": None, "providers": providers, "next_choice": next_choice}
