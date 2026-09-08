"""Télémétrie du flow : lecture des journaux et export OpenTelemetry (P3.3).

Le Studio sait rejouer une session à partir de ``events.jsonl``. C'est utile et
c'est propriétaire : personne d'autre ne lit ce format, et un projet qui veut
observer ses agents dans Grafana, Langfuse ou Phoenix doit tout réécrire.

Ce module garde le journal natif comme source — c'est lui qui existe — et lui
ajoute une porte de sortie standard : les **conventions sémantiques OpenTelemetry
GenAI**. Un span par appel de modèle ou d'outil, avec le modèle, les tokens et
l'issue, sous les noms d'attributs que les backends attendent déjà.

L'intention est de découpler, pas de remplacer : le replay natif reste la vue
du Studio, OTel est ce qui sort du projet.

Rien n'est instrumenté ici. Ce module **traduit** ce que l'hôte a déjà écrit ;
il n'exécute rien et n'ouvre aucune connexion — l'invariant du blueprint vaut
aussi pour son observabilité.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grimoire.traces.otel_conventions import (
    ATTR_AGENT_NAME,
    ATTR_OPERATION_NAME,
    ATTR_PROVIDER_NAME,
    ATTR_TOOL_NAME,
    OP_CHAT,
    OP_EXECUTE_TOOL,
    OP_INVOKE_AGENT,
    OTEL_EXPORT_SCHEMA_VERSION,
)

__all__ = [
    "EVENT_SOURCES",
    "event_files",
    "otel_spans",
    "read_events",
]

EVENT_SOURCES: tuple[tuple[str, Path], ...] = (
    ("hook-runtime", Path("_grimoire-runtime-output") / "hook-runtime" / "events.jsonl"),
    ("task-flow", Path("_grimoire-runtime-output") / "task-flow" / "events.jsonl"),
)

#: Root under which per-session or per-run subdirectories may each carry
#: their own ``events.jsonl`` — the hook runtime writes one such subdirectory
#: per session on hosts that shard by run. `EVENT_SOURCES` only ever named
#: the flat file at this root's top level; a session's events written one
#: level down were silently invisible to every reader of this module.
_HOOK_RUNTIME_ROOT = Path("_grimoire-runtime-output") / "hook-runtime"

#: Correspondance vers les conventions sémantiques OTel GenAI, partagées avec
#: `grimoire.traces.ledger` via `grimoire.traces.otel_conventions` — deux
#: exportateurs qui divergeraient sur ces noms produiraient deux dialectes
#: pour le même signal, illisibles l'un pour l'autre par les backends qu'on
#: cherche justement à atteindre. `gen_ai.provider.name` remplace l'attribut
#: déprécié `gen_ai.system`.
_ATTR_MAP = {
    "model": "gen_ai.request.model",
    "provider": ATTR_PROVIDER_NAME,
    "promptTokens": "gen_ai.usage.input_tokens",
    "completionTokens": "gen_ai.usage.output_tokens",
    "inputTokens": "gen_ai.usage.input_tokens",
    "outputTokens": "gen_ai.usage.output_tokens",
    "tool": ATTR_TOOL_NAME,
    "agent": ATTR_AGENT_NAME,
}

#: Ce qui mérite un span. Le reste du journal est du bruit d'exécution : le
#: convertir noierait les appels réels sous des événements de cycle de vie.
_SPANNABLE = ("llm-call", "tool-call", "agent-step", "model-call")


def event_files(project_root: Path) -> list[tuple[str, Path]]:
    """Les journaux qui existent réellement sous `project_root`.

    Le fichier plat de chaque source connue (`EVENT_SOURCES`), plus tout
    ``events.jsonl`` niché sous un sous-dossier de `hook-runtime/` — une
    session ou un run shardé. Ne découvrir que le fichier de tête laissait
    taire toute activité écrite un niveau plus bas.
    """
    found: list[tuple[str, Path]] = [
        (name, project_root / rel)
        for name, rel in EVENT_SOURCES
        if (project_root / rel).is_file()
    ]
    hook_runtime_root = project_root / _HOOK_RUNTIME_ROOT
    if hook_runtime_root.is_dir():
        seen = {path for _, path in found}
        for events_path in sorted(hook_runtime_root.rglob("events.jsonl")):
            if events_path in seen or not events_path.is_file():
                continue
            seen.add(events_path)
            subdir = events_path.parent.relative_to(hook_runtime_root)
            found.append((f"hook-runtime/{subdir.as_posix()}", events_path))
    return found


def read_events(project_root: Path, limit: int = 200) -> dict[str, list[Any]]:
    """Dernières lignes de chaque flux, pour le replay du Studio.

    Une ligne illisible est conservée telle quelle plutôt qu'écartée : un
    journal tronqué en cours d'écriture ne doit pas faire disparaître les
    lignes valides qui l'entourent.
    """
    log: dict[str, list[Any]] = {}
    for name, path in event_files(project_root):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        entries: list[Any] = []
        for line in lines[-limit:]:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except json.JSONDecodeError:
                entries.append({"raw": stripped})
        log[name] = entries
    return log


def _attributes(event: dict[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for key, value in event.items():
        mapped = _ATTR_MAP.get(key)
        if mapped is not None and value is not None:
            attrs[mapped] = value
    return attrs


def otel_spans(project_root: Path, limit: int = 200) -> list[dict[str, Any]]:
    """Les événements traduits en spans OTel GenAI.

    Forme volontairement neutre (dictionnaires), pas d'objets d'un SDK : le
    but est qu'un exportateur quelconque puisse les consommer sans que le kit
    dépende d'une bibliothèque d'instrumentation.
    """
    spans: list[dict[str, Any]] = []
    for source, entries in read_events(project_root, limit).items():
        for event in entries:
            if not isinstance(event, dict):
                continue
            kind = str(event.get("type") or event.get("action") or "")
            if kind not in _SPANNABLE:
                continue
            attrs = _attributes(event)
            attrs["grimoire.source"] = source
            attrs["grimoire.schema_version"] = OTEL_EXPORT_SCHEMA_VERSION
            if kind in ("llm-call", "model-call"):
                operation = OP_CHAT
            elif kind == "agent-step":
                operation = OP_INVOKE_AGENT
            else:
                operation = OP_EXECUTE_TOOL
            attrs[ATTR_OPERATION_NAME] = operation
            name = event.get("model") or event.get("tool") or event.get("agent") or kind
            span: dict[str, Any] = {
                "name": f"{operation} {name}",
                "kind": "CLIENT",
                "attributes": attrs,
            }
            for src_key, dst_key in (
                ("startedAt", "startTime"),
                ("endedAt", "endTime"),
                ("traceId", "traceId"),
                ("spanId", "spanId"),
            ):
                if event.get(src_key) is not None:
                    span[dst_key] = event[src_key]
            if event.get("error"):
                span["status"] = {"code": "ERROR", "message": str(event["error"])}
            spans.append(span)
    return spans
