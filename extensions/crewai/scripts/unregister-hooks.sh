#!/usr/bin/env bash
# Retire le hook crewai-telemetry-bridge du registre de sécurité du projet.

set -euo pipefail

PROJECT_ROOT="${GRIMOIRE_PROJECT_ROOT:?GRIMOIRE_PROJECT_ROOT requis}"
REGISTRY="${PROJECT_ROOT}/_grimoire-runtime/_config/hook-safety-registry.json"

[[ -f "$REGISTRY" ]] || exit 0

python3 - "$REGISTRY" <<'EOF'
import json
import sys

registry_path = sys.argv[1]
with open(registry_path, encoding="utf-8") as f:
    registry = json.load(f)

if registry.get("hooks", {}).pop("crewai-telemetry-bridge", None) is not None:
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("Hook crewai-telemetry-bridge retiré du registre.")
EOF
