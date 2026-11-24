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
Le message est caviardé (secrets du kit) puis tronqué, le nom d'outil tronqué ; la pile (``stack``) n'est jamais copiée. Ne lève jamais.
"""

from __future__ import annotations

import re
from typing import Any

from grimoire.hosts.decisions._shared import Decision, HookInput
from grimoire.memory.validation import redact_secrets

DECISION_ID = "grimoire.error-occurred"
MAX_MESSAGE = 200
_MAX_FIELD = 80
_FRAGMENT_MARGIN = 64
_TOKEN_TAIL = re.compile(r"[A-Za-z0-9_\-.+/=~]+\Z")
_SCAN_WINDOW = 2000  # borne du texte passé au caviardage : un message géant ne coûte pas cher


def _clean(value: Any, limit: int) -> str:
    """Le texte caviardé **avant** d'être tronqué à ``limit``.

    Une coupe à ``_SCAN_WINDOW`` peut laisser un bout de jeton sous la longueur
    minimale de son motif ; si un bloc PEM caviardé avant lui le ramène dans les
    ``limit`` caractères gardés, il fuit. On jette donc le dernier fragment
    coupé (jusqu'à la dernière espace) : mieux vaut perdre un mot qu'un secret.
    """
    if not isinstance(value, str):
        return ""
    window = value[:_SCAN_WINDOW]
    if len(value) > _SCAN_WINDOW and not (value[_SCAN_WINDOW].isspace() or window[-1:].isspace()):
        # Coupé au milieu d'un mot : on retire toute la suite finale de caractères
        # de jeton, quel que soit le motif, pour qu'aucun fragment ne survive.
        # Un message d'un seul mot : rien à garder, on recule d'une marge fixe.
        window = _TOKEN_TAIL.sub("", window) or window[:-_FRAGMENT_MARGIN]
    return redact_secrets(window)[0][:limit]


def _text(value: Any, limit: int) -> str:
    return _clean(value, limit)


def _message(value: Any) -> str:
    return _clean(value, MAX_MESSAGE)


def _error_detail(hook: HookInput) -> dict[str, Any]:
    """Les faits de l'erreur à journaliser (message tronqué, pile jamais copiée)."""
    raw = hook.raw or {}
    error = raw.get("error")
    if isinstance(error, dict):
        name, message = _text(error.get("name"), _MAX_FIELD), _message(error.get("message"))
    else:  # forme dégradée : une chaîne seule tient lieu de message
        name, message = "", _message(error)
    context = _text(raw.get("errorContext") or raw.get("error_context"), _MAX_FIELD)
    tool = _text(hook.tool_name or raw.get("toolName") or raw.get("tool_name"), _MAX_FIELD)
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
