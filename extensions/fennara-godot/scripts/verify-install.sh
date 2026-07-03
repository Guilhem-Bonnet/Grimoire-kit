#!/usr/bin/env bash
# Vérification post-installation de l'extension fennara-godot.
# Vérifie les artefacts copiés et la disponibilité de la chaîne Fennara.
# Sortie 0 = installé (avec avertissements éventuels) ; 1 = artefacts manquants.

set -euo pipefail

PROJECT_ROOT="${GRIMOIRE_PROJECT_ROOT:?GRIMOIRE_PROJECT_ROOT requis}"
status=0

if [[ -f "$PROJECT_ROOT/.github/skills/godot-evidence-loop/SKILL.md" ]]; then
  echo "✓ skill godot-evidence-loop copiée"
else
  echo "✗ skill godot-evidence-loop absente"
  status=1
fi

if command -v fennara >/dev/null 2>&1; then
  echo "✓ CLI fennara disponible ($(command -v fennara))"
else
  echo "⚠ CLI fennara absente — grounding indisponible tant qu'elle n'est pas installée"
fi

if [[ -f "$PROJECT_ROOT/opencode.json" ]] && grep -q '"fennara"' "$PROJECT_ROOT/opencode.json"; then
  echo "✓ serveur MCP fennara déclaré dans opencode.json"
else
  echo "⚠ serveur MCP fennara non déclaré (relancer scripts/setup-mcp.sh)"
fi

exit "$status"
