#!/usr/bin/env bash
# Désinstallation : retire l'entrée MCP fennara (si présente) et la skill copiée.
# Ne touche jamais à l'installation Fennara elle-même (CLI, addon Godot).

set -euo pipefail

PROJECT_ROOT="${GRIMOIRE_PROJECT_ROOT:?GRIMOIRE_PROJECT_ROOT requis}"

rm -f "$PROJECT_ROOT/.github/skills/godot-evidence-loop/SKILL.md"
rmdir "$PROJECT_ROOT/.github/skills/godot-evidence-loop" 2>/dev/null || true
echo "skill godot-evidence-loop retirée"

python3 - "$PROJECT_ROOT" <<'PY'
import json
import sys
from pathlib import Path

target = Path(sys.argv[1]) / "opencode.json"
if target.exists():
    data = json.loads(target.read_text(encoding="utf-8"))
    if data.get("mcp", {}).pop("fennara", None) is not None:
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("entrée MCP 'fennara' retirée d'opencode.json")
    else:
        print("opencode.json : pas d'entrée 'fennara' — rien à faire")
else:
    print("opencode.json absent — rien à faire")
PY
