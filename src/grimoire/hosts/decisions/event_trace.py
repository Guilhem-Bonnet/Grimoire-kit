"""Décision par défaut des événements déclarés mais non émis (R31) : une trace, rien de plus.

``TaskCreated``, ``TaskCompleted`` et ``InstructionsLoaded`` sont dans
:class:`~grimoire.hosts.events.HookEvent` pour que le vocabulaire du kit soit
complet, mais aucun émetteur ne les écrit (voir ``NOT_EMITTED_EVENTS``) : le
kit n'a aucune décision à prendre sur eux. Si un opérateur les branche à la
main (``grimoire-hook --event task_completed``), ils ne sont ni ignorés ni
bloquants : ils laissent une ligne dans ``traces.jsonl``, dans les mêmes
conditions que les autres événements journalisés (racine reconnue).

Seul l'identifiant de tâche de l'hôte est retenu (tronqué), plus le nom de
l'événement : ni sujet, ni raison de chargement, ni chemin, ni prompt, textes
libres que le journal, écrit sur disque et exporté, n'a pas à copier.
"""

from __future__ import annotations

from typing import Any

from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome

#: Clés de la charge utile de l'hôte copiées dans le détail (nom -> clés acceptées).
_KEPT: dict[str, tuple[str, ...]] = {
    "host_task_id": ("task_id", "taskId"),
}
_MAX_VALUE = 200


def _facts(raw: dict[str, Any]) -> dict[str, Any]:
    """Les identifiants de la charge utile qui valent d'être gardés, tronqués."""
    kept: dict[str, Any] = {}
    for name, keys in _KEPT.items():
        for key in keys:
            value = raw.get(key)
            if isinstance(value, str) and value:
                kept[name] = value[:_MAX_VALUE]
                break
    return kept


def decide_event_trace(hook: HookInput) -> Decision:
    """Ne décide rien : ``ALLOW`` avec un détail non vide, que le gateway journalise."""
    return Decision(outcome=Outcome.ALLOW, detail={"event": hook.event.value, **_facts(hook.raw)})
