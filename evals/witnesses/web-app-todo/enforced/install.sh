#!/usr/bin/env bash
# Installe le mécanisme du bras « enforced » (PREREGISTRATION 2026-09-04) dans
# une copie baseline enrôlée au profil governed :
#   1. le mécanisme d'activation du bras activated (hook SessionStart verbatim,
#      via ../activated/install.sh — settings.json remplacé, donc sans les
#      hooks émis par `grimoire init`) ;
#   2. les deux hooks BLOQUANTS du kit, tels que `grimoire host sync --host
#      claude` les émet pour un projet enrôlé : PreToolUse (grimoire.tool-policy)
#      et Stop (grimoire.evidence-gate). Les hooks consultatifs du kit
#      (SessionStart persona, UserPromptSubmit, PostToolUse, PreCompact,
#      SubagentStop) et le bloc `permissions` ne sont PAS réinstallés : la
#      variable mesurée est le blocage, pas le volume de contexte ;
#   3. la tâche `bootstrap` du task-board passée à `in_progress` : au profil
#      governed, le gate Stop ne bloque que si une preuve est due, et rien
#      n'est dû à l'état `proposed` (voir hosts/decisions.py, decide_evidence_gate).
set -euo pipefail

RUN_DIR="${1:?usage: install.sh <run-dir>}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d "$RUN_DIR/_grimoire/standard" ]; then
  echo "erreur : $RUN_DIR n'est pas enrôlé dans le standard." >&2
  exit 1
fi
if ! grep -q '^profile: governed' "$RUN_DIR/_grimoire/standard/standard-profile.yaml"; then
  echo "erreur : $RUN_DIR n'est pas au profil governed (grimoire standard init . --profile governed)." >&2
  exit 1
fi

"$HERE/../activated/install.sh" "$RUN_DIR"

python3 - "$RUN_DIR" <<'PY'
import json, re, sys
from pathlib import Path

run = Path(sys.argv[1])
settings_path = run / ".claude" / "settings.json"
settings = json.loads(settings_path.read_text(encoding="utf-8"))
hooks = settings.setdefault("hooks", {})
hooks["PreToolUse"] = [{
    "matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit",
    "hooks": [{"type": "command", "command": "grimoire-hook --host claude --event PreToolUse", "timeout": 10}],
}]
hooks["Stop"] = [{
    "hooks": [{"type": "command", "command": "grimoire-hook --host claude --event Stop", "timeout": 60}],
}]
settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

board_path = run / "_grimoire" / "standard" / "task-board.yaml"
board = board_path.read_text(encoding="utf-8")
new, n = re.subn(r'(task_id: "bootstrap"\n(?:.*\n)*?\s+status: )"proposed"', r'\1"in_progress"', board, count=1)
if n != 1:
    sys.exit("erreur : tâche bootstrap introuvable ou déjà hors de l'état proposed dans task-board.yaml")
board_path.write_text(new, encoding="utf-8")
print("hooks bloquants PreToolUse + Stop installés ; bootstrap -> in_progress")
PY
