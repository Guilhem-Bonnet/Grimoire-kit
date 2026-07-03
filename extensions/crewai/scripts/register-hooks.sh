#!/usr/bin/env bash
# Enregistre le hook crewai-telemetry-bridge dans le registre de sécurité
# du projet cible, toujours en mode shadow (règle marketplace : aucun hook
# d'extension ne démarre bloquant).

set -euo pipefail

MODE="shadow"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [[ "$MODE" != "shadow" ]]; then
    echo "Refus : un hook d'extension s'enregistre uniquement en mode shadow." >&2
    exit 1
fi

PROJECT_ROOT="${GRIMOIRE_PROJECT_ROOT:?GRIMOIRE_PROJECT_ROOT requis}"
REGISTRY="${PROJECT_ROOT}/_grimoire-runtime/_config/hook-safety-registry.json"

if [[ ! -f "$REGISTRY" ]]; then
    echo "Avertissement : registre de sécurité absent (${REGISTRY})."
    echo "Le hook est copié mais non enregistré ; il restera inactif."
    exit 0
fi

python3 - "$REGISTRY" "$MODE" <<'EOF'
import json
import sys

registry_path, mode = sys.argv[1], sys.argv[2]
with open(registry_path, encoding="utf-8") as f:
    registry = json.load(f)

hooks = registry.setdefault("hooks", {})
hooks["crewai-telemetry-bridge"] = {
    "mode": mode,
    "script": ".github/hooks/scripts/crewai-telemetry-bridge.sh",
    "control_file": ".github/hooks/crewai-telemetry-bridge.json",
    "origin": "extension:crewai",
}

with open(registry_path, "w", encoding="utf-8") as f:
    json.dump(registry, f, ensure_ascii=False, indent=2)
    f.write("\n")

print(f"Hook crewai-telemetry-bridge enregistré en mode {mode}.")
EOF
