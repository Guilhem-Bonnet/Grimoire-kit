#!/usr/bin/env bash
# quick-check — validation locale rapide (pre-push).
#
# Vérifie en quelques secondes que le kit est sain : import du paquet,
# syntaxe des scripts d'entrée, lint des sources. Pensé pour le hook
# pre-push ; les suites complètes restent l'affaire de la CI.

set -euo pipefail
KIT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$KIT_ROOT"

echo "quick-check: ${KIT_ROOT}"

# 1. Syntaxe bash des points d'entrée
for f in grimoire.sh grimoire-init.sh install.sh; do
    [[ -f "$f" ]] && bash -n "$f" && echo "  OK bash -n $f"
done

# 2. Import du paquet (venv local prioritaire)
PY="python3"
[[ -x ".venv/bin/python3" ]] && PY=".venv/bin/python3"
if "$PY" -c "import grimoire" 2>/dev/null; then
    echo "  OK import grimoire"
else
    PYTHONPATH="src${PYTHONPATH:+:${PYTHONPATH}}" "$PY" -c "import grimoire" && echo "  OK import grimoire (src)"
fi

# 3. Lint rapide si ruff disponible (non bloquant s'il est absent)
if command -v ruff >/dev/null 2>&1; then
    ruff check src/grimoire --quiet && echo "  OK ruff src/grimoire"
elif "$PY" -m ruff --version >/dev/null 2>&1; then
    "$PY" -m ruff check src/grimoire --quiet && echo "  OK ruff src/grimoire"
else
    echo "  -- ruff absent : lint sauté"
fi

echo "quick-check: OK"
