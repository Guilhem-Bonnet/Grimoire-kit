#!/usr/bin/env python3
"""Sensory Buffer — Mémoire sensorielle court terme pour agents.

Gère un buffer de contexte à décroissance temporelle, simulant
la mémoire sensorielle humaine pour les agents IA.
Les informations récentes sont fortes, les anciennes s'estompent.

Usage:
    python sensory-buffer.py --project-root ./mon-projet capture --agent dev --data '{"task": "implement login"}'
    python sensory-buffer.py --project-root ./mon-projet recall --agent dev
    python sensory-buffer.py --project-root ./mon-projet decay --agent dev
    python sensory-buffer.py --project-root ./mon-projet prioritize --agent dev --top 5
    python sensory-buffer.py --project-root ./mon-projet flush --agent dev --older-than 24h
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("grimoire.sensory_buffer")

VERSION = "1.0.0"

BUFFER_DIR = ".grimoire-sensory"

# ── Modèle de données ──────────────────────────────────────────


@dataclass
class SensoryItem:
    """Élément du buffer sensoriel."""

    item_id: str = ""
    agent: str = ""
    timestamp: str = ""
    category: str = "general"  # context, decision, observation, interaction, error
    data: dict[str, Any] = field(default_factory=dict)
    strength: float = 1.0  # 0.0 à 1.0, décroît avec le temps
    importance: float = 0.5  # 0.0 à 1.0, fixé à l'enregistrement
    tags: list[str] = field(default_factory=list)
    source: str = ""  # quel outil/workflow a généré l'item


@dataclass
class BufferStats:
    """Statistiques du buffer."""

    agent: str = ""
    total_items: int = 0
    active_items: int = 0
    decayed_items: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    avg_strength: float = 0.0
    oldest_item: str = ""
    newest_item: str = ""


# ── Constantes de décroissance ──────────────────────────────────

DECAY_HALF_LIFE_HOURS = 4.0  # La force tombe à 50% après 4h
DECAY_THRESHOLD = 0.05  # En dessous de 5%, l'item est "oublié"
IMPORTANCE_MULTIPLIER = 2.0  # L'importance ralentit la décroissance

# ── Utilitaires ─────────────────────────────────────────────────


def _buffer_path(root: Path, agent: str) -> Path:
    """Chemin du buffer d'un agent."""
    return root / BUFFER_DIR / f"{agent}-buffer.jsonl"


def _load_buffer(root: Path, agent: str) -> list[SensoryItem]:
    """Charge le buffer d'un agent."""
    bp = _buffer_path(root, agent)
    if not bp.exists():
        return []
    items: list[SensoryItem] = []
    for line in bp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            items.append(SensoryItem(**{k: v for k, v in data.items()
                                        if k in SensoryItem.__dataclass_fields__}))
        except (json.JSONDecodeError, TypeError):
            continue
    return items


def _save_buffer(root: Path, agent: str, items: list[SensoryItem]) -> None:
    """Sauvegarde le buffer d'un agent."""
    bp = _buffer_path(root, agent)
    bp.parent.mkdir(parents=True, exist_ok=True)
    with open(bp, "w", encoding="utf-8") as fobj:
        fobj.writelines(json.dumps(asdict(item), default=str) + "\n" for item in items)


def _compute_decay(item: SensoryItem, now: datetime | None = None) -> float:
    """Calcule la force actuelle d'un item après décroissance."""
    if now is None:
        now = datetime.now()
    try:
        created = datetime.fromisoformat(item.timestamp)
    except (ValueError, TypeError):
        return 0.0

    hours_elapsed = (now - created).total_seconds() / 3600
    if hours_elapsed < 0:
        return item.strength

    # Décroissance exponentielle ajustée par l'importance
    effective_half_life = DECAY_HALF_LIFE_HOURS * (1 + item.importance * IMPORTANCE_MULTIPLIER)
    decay_factor = math.pow(0.5, hours_elapsed / effective_half_life)

    return round(item.strength * decay_factor, 4)


def _next_item_id(items: list[SensoryItem]) -> str:
    """Génère le prochain ID d'item."""
    if not items:
        return "si-001"
    max_num = 0
    for item in items:
        if item.item_id.startswith("si-"):
            try:
                num = int(item.item_id[3:])
                max_num = max(max_num, num)
            except ValueError as _exc:
                _log.debug("ValueError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues
    return f"si-{max_num + 1:03d}"


# ── Commandes ───────────────────────────────────────────────────


def cmd_capture(root: Path, agent: str, data: dict[str, Any],
                category: str, importance: float, tags: list[str],
                source: str, as_json: bool) -> dict[str, Any]:
    """Capture un nouvel élément dans le buffer sensoriel."""
    items = _load_buffer(root, agent)
    item_id = _next_item_id(items)

    new_item = SensoryItem(
        item_id=item_id,
        agent=agent,
        timestamp=datetime.now().isoformat(),
        category=category,
        data=data,
        strength=1.0,
        importance=min(max(importance, 0.0), 1.0),
        tags=tags,
        source=source,
    )

    items.append(new_item)
    _save_buffer(root, agent, items)

    result = {
        "item_id": item_id,
        "agent": agent,
        "category": category,
        "importance": new_item.importance,
        "buffer_size": len(items),
    }

    if not as_json:
        print(f"📥 Capturé : {item_id}")
        print(f"   Agent : {agent}")
        print(f"   Catégorie : {category}")
        print(f"   Importance : {new_item.importance:.0%}")
        print(f"   Buffer : {len(items)} items")

    return result


def cmd_recall(root: Path, agent: str, category: str | None,
               min_strength: float, as_json: bool) -> dict[str, Any]:
    """Rappelle le contexte récent avec les forces actuelles."""
    items = _load_buffer(root, agent)
    now = datetime.now()

    # Calculer la force actuelle
    recalled: list[dict[str, Any]] = []
    for item in items:
        current_strength = _compute_decay(item, now)
        if current_strength >= min_strength:
            if category and item.category != category:
                continue
            entry = asdict(item)
            entry["current_strength"] = current_strength
            recalled.append(entry)

    # Trier par force décroissante
    recalled.sort(key=lambda x: x["current_strength"], reverse=True)

    result = {
        "agent": agent,
        "total_items": len(items),
        "recalled": len(recalled),
        "min_strength": min_strength,
        "items": recalled,
    }

    if not as_json:
        if not recalled:
            print(f"📭 Aucun item actif pour {agent} (seuil: {min_strength:.0%})")
            return result

        print(f"🧠 Mémoire sensorielle de {agent} ({len(recalled)} actifs)")
        print()
        for entry in recalled[:20]:
            strength = entry["current_strength"]
            bar_len = int(strength * 15)
            bar = "█" * bar_len + "░" * (15 - bar_len)
            cat_icon = {
                "context": "🌍", "decision": "⚖️", "observation": "👁️",
                "interaction": "💬", "error": "⚠️",
            }.get(entry.get("category", ""), "📎")

            print(f"  {cat_icon} [{entry['item_id']}] Force: [{bar}] {strength:.0%}")

            # Afficher les données clés
            data = entry.get("data", {})
            if isinstance(data, dict):
                for key, val in list(data.items())[:3]:
                    print(f"     {key}: {str(val)[:60]}")
            else:
                print(f"     {str(data)[:80]}")

            if entry.get("tags"):
                print(f"     🏷️ {', '.join(entry['tags'])}")
            print()

    return result


def cmd_decay(root: Path, agent: str, as_json: bool) -> dict[str, Any]:
    """Affiche l'état de décroissance du buffer."""
    items = _load_buffer(root, agent)
    now = datetime.now()

    decay_map: list[dict[str, Any]] = []
    active_count = 0
    forgotten_count = 0

    for item in items:
        current = _compute_decay(item, now)
        try:
            created = datetime.fromisoformat(item.timestamp)
            age_hours = (now - created).total_seconds() / 3600
        except (ValueError, TypeError):
            age_hours = 0

        effective_hl = DECAY_HALF_LIFE_HOURS * (1 + item.importance * IMPORTANCE_MULTIPLIER)
        ttl_hours = effective_hl * math.log2(1 / DECAY_THRESHOLD) if current > DECAY_THRESHOLD else 0

        entry = {
            "item_id": item.item_id,
            "category": item.category,
            "original_strength": item.strength,
            "current_strength": current,
            "importance": item.importance,
            "age_hours": round(age_hours, 1),
            "effective_half_life": round(effective_hl, 1),
            "ttl_hours": round(max(ttl_hours - age_hours, 0), 1),
            "status": "active" if current >= DECAY_THRESHOLD else "forgotten",
        }
        decay_map.append(entry)

        if current >= DECAY_THRESHOLD:
            active_count += 1
        else:
            forgotten_count += 1

    result = {
        "agent": agent,
        "total": len(items),
        "active": active_count,
        "forgotten": forgotten_count,
        "decay_config": {
            "half_life_hours": DECAY_HALF_LIFE_HOURS,
            "threshold": DECAY_THRESHOLD,
            "importance_multiplier": IMPORTANCE_MULTIPLIER,
        },
        "items": decay_map,
    }

    if not as_json:
        print(f"⏳ Décroissance — {agent}")
        print(f"   Total : {len(items)} | Actifs : {active_count} | Oubliés : {forgotten_count}")
        print(f"   Demi-vie base : {DECAY_HALF_LIFE_HOURS}h | Seuil : {DECAY_THRESHOLD:.0%}")
        print()
        for entry in decay_map:
            strength = entry["current_strength"]
            bar_len = int(strength * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            status_icon = "🟢" if entry["status"] == "active" else "💀"
            print(f"  {status_icon} [{entry['item_id']}] [{bar}] {strength:.0%} "
                  f"(âge: {entry['age_hours']}h, TTL: {entry['ttl_hours']}h)")

    return result


def cmd_prioritize(root: Path, agent: str, top_n: int,
                   as_json: bool) -> dict[str, Any]:
    """Priorise les items du buffer par pertinence combinée."""
    items = _load_buffer(root, agent)
    now = datetime.now()

    scored: list[dict[str, Any]] = []
    for item in items:
        current_strength = _compute_decay(item, now)
        if current_strength < DECAY_THRESHOLD:
            continue

        # Score combiné : strength * importance * category_weight
        cat_weight = {
            "error": 1.5, "decision": 1.3, "context": 1.0,
            "interaction": 0.8, "observation": 0.7,
        }.get(item.category, 1.0)

        combined_score = current_strength * item.importance * cat_weight

        scored.append({
            "item_id": item.item_id,
            "category": item.category,
            "data": item.data,
            "current_strength": current_strength,
            "importance": item.importance,
            "combined_score": round(combined_score, 4),
            "tags": item.tags,
        })

    scored.sort(key=lambda x: x["combined_score"], reverse=True)
    top_items = scored[:top_n]

    result = {
        "agent": agent,
        "active_items": len(scored),
        "top_n": top_n,
        "prioritized": top_items,
    }

    if not as_json:
        print(f"🎯 Top {top_n} items prioritaires — {agent}")
        print()
        for i, entry in enumerate(top_items, 1):
            score = entry["combined_score"]
            bar_len = int(score * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            print(f"  #{i} [{entry['item_id']}] Score: [{bar}] {score:.2f}")
            print(f"      Catégorie: {entry['category']} | "
                  f"Force: {entry['current_strength']:.0%} | "
                  f"Importance: {entry['importance']:.0%}")

            data = entry.get("data", {})
            if isinstance(data, dict):
                for key, val in list(data.items())[:2]:
                    print(f"      {key}: {str(val)[:60]}")
            print()

    return result


def cmd_flush(root: Path, agent: str, older_than: str | None,
              below_strength: float | None, as_json: bool) -> dict[str, Any]:
    """Purge le buffer selon les critères."""
    items = _load_buffer(root, agent)
    now = datetime.now()

    kept: list[SensoryItem] = []
    flushed = 0

    for item in items:
        should_flush = False

        # Critère d'âge
        if older_than:
            try:
                created = datetime.fromisoformat(item.timestamp)
                hours = _parse_duration(older_than)
                if (now - created).total_seconds() > hours * 3600:
                    should_flush = True
            except (ValueError, TypeError) as _exc:
                _log.debug("ValueError, TypeError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

        # Critère de force
        if below_strength is not None:
            current = _compute_decay(item, now)
            if current < below_strength:
                should_flush = True

        # Par défaut : flush les items oubliés
        if not older_than and below_strength is None:
            current = _compute_decay(item, now)
            if current < DECAY_THRESHOLD:
                should_flush = True

        if should_flush:
            flushed += 1
        else:
            kept.append(item)

    _save_buffer(root, agent, kept)

    result = {
        "agent": agent,
        "flushed": flushed,
        "remaining": len(kept),
        "criteria": {
            "older_than": older_than,
            "below_strength": below_strength,
        },
    }

    if not as_json:
        print(f"🧹 Flush — {agent}")
        print(f"   Purgés : {flushed}")
        print(f"   Restants : {len(kept)}")

    return result


def _parse_duration(duration_str: str) -> float:
    """Parse une durée comme '24h', '2d', '30m' en heures."""
    duration_str = duration_str.strip().lower()
    if duration_str.endswith("h"):
        return float(duration_str[:-1])
    if duration_str.endswith("d"):
        return float(duration_str[:-1]) * 24
    if duration_str.endswith("m"):
        return float(duration_str[:-1]) / 60
    return float(duration_str)


# ── CLI ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Construit le parser CLI."""
    parser = argparse.ArgumentParser(
        prog="sensory-buffer",
        description="Sensory Buffer — Mémoire sensorielle court terme pour agents",
    )
    parser.add_argument("--project-root", type=Path, default=Path(),
                        help="Racine du projet")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Sortie JSON")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    subs = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # capture
    cap = subs.add_parser("capture", help="Capturer un item dans le buffer")
    cap.add_argument("--agent", required=True, help="Agent propriétaire")
    cap.add_argument("--data", required=True, help="Données JSON à capturer")
    cap.add_argument("--category", default="general",
                     choices=["context", "decision", "observation", "interaction", "error", "general"],
                     help="Catégorie")
    cap.add_argument("--importance", type=float, default=0.5,
                     help="Importance 0.0-1.0 (défaut: 0.5)")
    cap.add_argument("--tags", nargs="*", default=[], help="Tags")
    cap.add_argument("--source", default="", help="Source de l'item")

    # recall
    rec = subs.add_parser("recall", help="Rappeler le contexte récent")
    rec.add_argument("--agent", required=True, help="Agent")
    rec.add_argument("--category", help="Filtrer par catégorie")
    rec.add_argument("--min-strength", type=float, default=0.1,
                     help="Force minimale (défaut: 0.1)")

    # decay
    dec = subs.add_parser("decay", help="Afficher l'état de décroissance")
    dec.add_argument("--agent", required=True, help="Agent")

    # prioritize
    pri = subs.add_parser("prioritize", help="Prioriser les items du buffer")
    pri.add_argument("--agent", required=True, help="Agent")
    pri.add_argument("--top", type=int, default=5, help="Nombre d'items (défaut: 5)")

    # flush
    flu = subs.add_parser("flush", help="Purger le buffer")
    flu.add_argument("--agent", required=True, help="Agent")
    flu.add_argument("--older-than", help="Durée max (ex: 24h, 2d)")
    flu.add_argument("--below-strength", type=float,
                     help="Force minimale à garder")

    return parser


def main() -> None:
    """Point d'entrée principal."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    root = args.project_root.resolve()

    result: dict[str, Any] = {}

    if args.command == "capture":
        try:
            data = json.loads(args.data)
        except json.JSONDecodeError:
            data = {"raw": args.data}
        result = cmd_capture(root, args.agent, data, args.category,
                             args.importance, args.tags, args.source, args.as_json)
    elif args.command == "recall":
        result = cmd_recall(root, args.agent, getattr(args, "category", None),
                            args.min_strength, args.as_json)
    elif args.command == "decay":
        result = cmd_decay(root, args.agent, args.as_json)
    elif args.command == "prioritize":
        result = cmd_prioritize(root, args.agent, args.top, args.as_json)
    elif args.command == "flush":
        result = cmd_flush(root, args.agent, args.older_than,
                           getattr(args, "below_strength", None), args.as_json)

    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
