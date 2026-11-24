"""``errorOccurred`` sous le harness Copilot CLI : l'erreur au journal de session (#1280, P3).

Forme : https://docs.github.com/en/copilot/reference/hooks-reference — entrée
``error`` (``message``, ``name``), ``errorContext`` (``model_call`` |
``tool_execution`` | ``system`` | ``user_input``), ``recoverable``. La référence ne
documente pas de champ d'outil sur cet événement : l'outil n'est retenu que si la
charge en porte un (``toolName``/``tool_name``), sinon ``errorContext`` dit s'il
s'agit d'une exécution d'outil.

Observation pure : la décision ne bloque ni ne dit rien à l'hôte (``abstain``,
sortie vide, code 0 — un code non nul refuserait chez Copilot). Son ``detail``
est inscrit par la passerelle au ledger de traces, sous l'identifiant de session.
Le message est tronqué ; la pile (``stack``) n'est jamais copiée. Ne lève jamais.
"""

from __future__ import annotations

from typing import Any

from grimoire.hosts.decisions._shared import Decision, HookInput

DECISION_ID = "grimoire.error-occurred"
MAX_MESSAGE = 200
_MAX_FIELD = 80


def _text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _error_detail(hook: HookInput) -> dict[str, Any]:
    """Les faits de l'erreur à journaliser (message tronqué, pile jamais copiée)."""
    raw = hook.raw or {}
    error = raw.get("error")
    if isinstance(error, dict):
        name, message = _text(error.get("name"), _MAX_FIELD), _text(error.get("message"), MAX_MESSAGE)
    else:  # forme dégradée : une chaîne seule tient lieu de message
        name, message = "", _text(error, MAX_MESSAGE)
    context = _text(raw.get("errorContext") or raw.get("error_context"), _MAX_FIELD)
    tool = hook.tool_name or _text(raw.get("toolName") or raw.get("tool_name"), _MAX_FIELD)
    detail: dict[str, Any] = {"event": hook.event.value, "error_name": name, "error_message": message}
    if context:
        detail["error_context"] = context
    if tool:
        detail["tool"] = tool
    if isinstance(raw.get("recoverable"), bool):
        detail["recoverable"] = raw["recoverable"]
    return detail


def decide_error_occurred(hook: HookInput) -> Decision:
    """Le détail de l'erreur à journaliser, jamais un refus."""
    try:
        return Decision(abstain=True, detail=_error_detail(hook))
    except Exception as exc:
        return Decision(abstain=True, detail={"error_occurred": "error", "error": f"{type(exc).__name__}: {exc}"})
