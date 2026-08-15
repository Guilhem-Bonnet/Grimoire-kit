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

#: Correspondance vers les conventions sémantiques OTel GenAI. Les noms sont
#: ceux que les backends indexent : s'en écarter rendrait les spans illisibles
#: par les outils mêmes qu'on cherche à atteindre.
_ATTR_MAP = {
    "model": "gen_ai.request.model",
    "provider": "gen_ai.system",
    "promptTokens": "gen_ai.usage.input_tokens",
    "completionTokens": "gen_ai.usage.output_tokens",
    "inputTokens": "gen_ai.usage.input_tokens",
    "outputTokens": "gen_ai.usage.output_tokens",
    "tool": "gen_ai.tool.name",
    "agent": "gen_ai.agent.name",
}

#: Ce qui mérite un span. Le reste du journal est du bruit d'exécution : le
#: convertir noierait les appels réels sous des événements de cycle de vie.
_SPANNABLE = ("llm-call", "tool-call", "agent-step", "model-call")


def event_files(project_root: Path) -> list[tuple[str, Path]]:
    """Les journaux qui existent réellement sous `project_root`."""
    return [
        (name, project_root / rel)
        for name, rel in EVENT_SOURCES
        if (project_root / rel).is_file()
    ]


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
            operation = "chat" if kind in ("llm-call", "model-call") else "execute_tool"
            attrs["gen_ai.operation.name"] = operation
            name = event.get("model") or event.get("tool") or kind
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
