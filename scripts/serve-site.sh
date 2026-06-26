#!/usr/bin/env bash
# Grimoire Kit — site en « mode vue » local.
#
# Régénère le data layer (web/data/*.json) à partir d'un PROJET RÉEL — métriques,
# task-board.yaml, et `observatory.py export` (traces / agents / relations / events) —
# puis sert le site web/ en lecture seule. C'est la version « 100% fonctionnelle » :
# Observability, Game-UI et Kanban affichent les vraies données du projet ciblé.
#
# Usage :
#   scripts/serve-site.sh                 # port 8420, données = ce dépôt
#   scripts/serve-site.sh 8420            # port custom
#   scripts/serve-site.sh 8420 /chemin/projet-instrumente   # cibler un autre projet
#
# (La vitrine GitHub Pages, elle, sert le snapshot démo committé — voir docs.yml.)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${1:-8420}"
TARGET="$(cd "${2:-$HERE}" && pwd)"

PY="python3"
[ -x "$HERE/.venv/bin/python" ] && PY="$HERE/.venv/bin/python"

echo "→ Régénération du data layer depuis : $TARGET"
if ! "$PY" "$HERE/scripts/gen-site-data.py" --root "$TARGET" --out-dir "$HERE/web/data" --with-tests; then
  echo "  (collecte pytest indisponible — comptages de tests en repli)"
  "$PY" "$HERE/scripts/gen-site-data.py" --root "$TARGET" --out-dir "$HERE/web/data"
fi

echo ""
echo "  ┌─ Grimoire Kit · mode vue (lecture seule)"
echo "  │  http://127.0.0.1:$PORT/"
echo "  │  / · documentation · kanban · observability · game-ui · anatomy"
echo "  └─ Ctrl+C pour arrêter."
echo ""
cd "$HERE/web" && exec "$PY" -m http.server "$PORT"
