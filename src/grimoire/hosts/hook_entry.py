"""Point d'entrée du script ``grimoire-hook`` : la bibliothèque standard seule, puis le runtime.

La garde ``PreToolUse`` est émise en échec fermé (``"onFailure": "block"``) : un
hook qui plante refuse l'outil. Si le runtime ne s'importe plus (mise à jour
cassée, dépendance absente), le plantage refusait Bash, Edit et Write avec la
seule trace de l'hôte. Ici l'import est tenté à l'abri : en cas d'échec, la garde
rend un refus explicite qui dit quoi faire, et les événements consultatifs un
message système sans rien bloquer.

Ce module n'importe que la bibliothèque standard en tête : le paquet
``grimoire`` lui-même doit encore s'importer (son ``__init__`` ne lit que sa
version). Si le paquet est introuvable, le script lanceur échoue avant d'arriver
ici ; voir « Si toutes les actions sont refusées » dans ``docs/guides/depannage.md``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

#: Ce que l'utilisateur fait d'une garde en panne, du plus doux au plus radical.
REMEDY = (
    "Lance `grimoire doctor` puis `grimoire up` (réinstalle le kit au besoin). En dernier recours, "
    'retire `"onFailure": "block"` du hook PreToolUse de `.claude/settings.json` : la garde repasse '
    "alors en échec ouvert."
)

#: Noms de l'événement ``PreToolUse`` selon l'hôte (Claude Code, Copilot, Gemini CLI).
_GUARD_EVENTS = frozenset({"pretooluse", "pre_tool_use", "beforetool"})


def guard_failure_payload(cause: str) -> dict[str, Any]:
    """Le refus ``PreToolUse`` d'une garde en panne, au format lu par Claude Code et Copilot."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"[Grimoire] garde avant outil en panne ({cause}) : appel refusé par précaution. {REMEDY}"
            ),
        }
    }


def _option(args: list[str], name: str) -> str:
    for index, arg in enumerate(args[:-1]):
        if arg == name:
            return args[index + 1]
    return ""


def _event_name(args: list[str], raw: str) -> str:
    name = _option(args, "--event")
    if not name:
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            name = str(payload.get("hook_event_name") or payload.get("hookEventName") or "")
    return name


def import_failure(args: list[str], raw: str, cause: str) -> int:
    """Ce que rend le hook quand le runtime ne s'importe pas : refus sur la garde, message ailleurs."""
    host = _option(args, "--host").lower()
    guard = _event_name(args, raw).lower() in _GUARD_EVENTS
    message = f"[Grimoire] hook indisponible ({cause}). {REMEDY}"
    if host == "gemini":
        sys.stderr.write(message + "\n")
        # Gemini : le code 2 est le refus documenté sur un événement bloquant.
        return 2 if guard else 0
    if guard:
        sys.stdout.write(json.dumps(guard_failure_payload(cause), ensure_ascii=False))
    else:
        sys.stdout.write(json.dumps({"systemMessage": message}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Lancer le runtime ; si son import échoue, répondre quand même (voir le module)."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        from grimoire.hosts.runtime import main as runtime_main
    except Exception as exc:
        try:
            raw = sys.stdin.read()
        except (OSError, ValueError):
            raw = ""
        return import_failure(args, raw, f"{type(exc).__name__}: {exc}")
    return runtime_main(args)


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
