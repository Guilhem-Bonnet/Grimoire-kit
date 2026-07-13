"""Claude Code session activation for the governed standard.

The 2026-07-09 evals campaign (evals/reports/2026-07-09/report.md)
validated the mechanism 40/40: a Claude Code ``SessionStart`` hook that
prints an activation directive turns passive standard artifacts into
engaged protocol (task envelope filled, evidence inventory, gates run).
The 2026-07-03 campaign measured the counterfactual: without the hook,
0/40 runs engaged the standard.

``grimoire standard init`` installs the hook by default:

- ``.claude/activation-context.md`` — the directive, editable per
  project (never overwritten if present);
- ``.claude/settings.json`` — a ``SessionStart`` command hook running
  ``grimoire standard activation-context``, which prints the project
  file when present and the built-in directive otherwise. Using the CLI
  as the hook command keeps the mechanism portable (no ``cat`` on
  Windows) and versioned with the kit.

The settings merge is fail-safe: an unreadable or unexpectedly shaped
``settings.json`` is left byte-for-byte untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ACTIVATION_CONTEXT_RELPATH = Path(".claude") / "activation-context.md"
SETTINGS_RELPATH = Path(".claude") / "settings.json"
HOOK_COMMAND = "grimoire standard activation-context"

_DIRECTIVE_TEMPLATE = """[Grimoire Standard — activation]
Ce projet est gouverné par le standard agentique Grimoire. Ces étapes font
partie de la tâche demandée :
1. AVANT toute modification de code : remplis
   `_grimoire-output/evidence/{task_id}/task-envelope.md` — objectif,
   périmètre outillé (tool boundary) concret, critères de sortie.
2. PENDANT le travail : consigne chaque preuve (commande exécutée, test
   vert, diff clé) comme ligne concrète de l'inventaire dans
   `_grimoire-output/evidence/{task_id}/evidence-pack.md`, et remplace le
   résumé placeholder.
3. AVANT de conclure : exécute
   `grimoire standard gate check --task-id {task_id} --strict` puis
   `grimoire standard verify .` et corrige tout échec.
Une clôture sans gates verts est une tâche non terminée.
"""


def default_activation_directive(task_id: str = "bootstrap") -> str:
    """Directive validated by the 2026-07-09 activated-arm campaign."""
    return _DIRECTIVE_TEMPLATE.format(task_id=task_id)


def activation_context_text(project_root: Path, task_id: str = "bootstrap") -> str:
    """Project-level directive if present, built-in default otherwise."""
    context_path = project_root / ACTIVATION_CONTEXT_RELPATH
    if context_path.is_file():
        return context_path.read_text(encoding="utf-8")
    return default_activation_directive(task_id)


@dataclass(frozen=True)
class ClaudeActivationResult:
    """Outcome of an activation install attempt."""

    status: str  # installed | already-installed | skipped-invalid-settings
    written: list[Path] = field(default_factory=list)
    message: str = ""


def _hook_entry() -> dict[str, object]:
    return {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}


def _hook_already_present(session_start: list[object]) -> bool:
    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if isinstance(hook, dict) and hook.get("command") == HOOK_COMMAND:
                return True
    return False


def install_claude_activation(
    project_root: Path, task_id: str = "bootstrap"
) -> ClaudeActivationResult:
    """Install the SessionStart activation hook into *project_root*.

    Idempotent; never destroys existing settings content. On a malformed
    ``settings.json`` the file is left untouched and the caller gets a
    ``skipped-invalid-settings`` status to surface.
    """
    written: list[Path] = []
    context_path = project_root / ACTIVATION_CONTEXT_RELPATH
    if not context_path.exists():
        context_path.parent.mkdir(parents=True, exist_ok=True)
        context_path.write_text(default_activation_directive(task_id), encoding="utf-8")
        written.append(ACTIVATION_CONTEXT_RELPATH)

    settings_path = project_root / SETTINGS_RELPATH
    data: dict[str, object] = {}
    if settings_path.exists():
        try:
            loaded: object = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ClaudeActivationResult(
                status="skipped-invalid-settings",
                written=written,
                message=f"{SETTINGS_RELPATH} illisible — hook non installé, fichier préservé",
            )
        if not isinstance(loaded, dict):
            return ClaudeActivationResult(
                status="skipped-invalid-settings",
                written=written,
                message=f"{SETTINGS_RELPATH} n'est pas un objet JSON — fichier préservé",
            )
        data = loaded

    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
        data["hooks"] = hooks
    if not isinstance(hooks, dict):
        return ClaudeActivationResult(
            status="skipped-invalid-settings",
            written=written,
            message=f"champ 'hooks' inattendu dans {SETTINGS_RELPATH} — fichier préservé",
        )

    session_start = hooks.get("SessionStart")
    if session_start is None:
        session_start = []
        hooks["SessionStart"] = session_start
    if not isinstance(session_start, list):
        return ClaudeActivationResult(
            status="skipped-invalid-settings",
            written=written,
            message=f"champ 'hooks.SessionStart' inattendu dans {SETTINGS_RELPATH} — fichier préservé",
        )

    if _hook_already_present(session_start):
        status = "installed" if written else "already-installed"
        return ClaudeActivationResult(status=status, written=written)

    session_start.append(_hook_entry())
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    written.append(SETTINGS_RELPATH)
    return ClaudeActivationResult(status="installed", written=written)
