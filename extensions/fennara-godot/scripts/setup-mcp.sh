#!/usr/bin/env bash
# Déclare le serveur MCP Fennara dans les hôtes agents du projet cible.
# Non destructif : n'écrase jamais une entrée existante, n'installe pas
# Fennara lui-même (son CLI gère son propre cycle de vie : `fennara install`).

set -euo pipefail

PROJECT_ROOT="${GRIMOIRE_PROJECT_ROOT:?GRIMOIRE_PROJECT_ROOT requis}"

if ! command -v fennara >/dev/null 2>&1; then
  echo "⚠ CLI fennara introuvable — installez-la d'abord :"
  echo "    https://github.com/fennaraOfficial/fennara-godot-ai (install.sh)"
  echo "  puis, dans le projet Godot : fennara install"
  echo "  L'extension reste installée ; relancez scripts/verify-install.sh ensuite."
fi

python3 - "$PROJECT_ROOT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
target = root / "opencode.json"
entry = {"type": "local", "command": ["fennara", "mcp"], "enabled": True}

if target.exists():
    data = json.loads(target.read_text(encoding="utf-8"))
else:
    data = {"$schema": "https://opencode.ai/config.json"}

mcp = data.setdefault("mcp", {})
if "fennara" in mcp:
    print("opencode.json : entrée MCP 'fennara' déjà présente — inchangée.")
else:
    mcp["fennara"] = entry
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"opencode.json : serveur MCP 'fennara' déclaré ({target}).")

print("Pour Claude Code : claude mcp add fennara -- fennara mcp")
PY
