#!/usr/bin/env bash
# repo-analysis-state.sh — Self-piloting : lecture/écriture de l'état du workflow repo-analysis
# Usage:
#   repo-analysis-state.sh write --step N --repo NAME --status STATUS [--objective "..."] [--project-root PATH]
#   repo-analysis-state.sh read [--project-root PATH]
#   repo-analysis-state.sh clear [--project-root PATH]
#
# Output (read): JSON avec step courant, repo, status, objective
# Ecrit dans: {project-root}/_grimoire-runtime-output/task-flow/repo-analysis-state.json

set -euo pipefail

COMMAND="${1:-read}"
shift 2>/dev/null || true

PROJECT_ROOT="$(pwd)"
STEP=""
REPO=""
STATUS=""
OBJECTIVE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-root) PROJECT_ROOT="$2"; shift 2 ;;
    --step)         STEP="$2";         shift 2 ;;
    --repo)         REPO="$2";         shift 2 ;;
    --status)       STATUS="$2";       shift 2 ;;
    --objective)    OBJECTIVE="$2";    shift 2 ;;
    *)              shift ;;
  esac
done

STATE_DIR="$PROJECT_ROOT/_grimoire-runtime-output/task-flow"
STATE_FILE="$STATE_DIR/repo-analysis-state.json"

mkdir -p "$STATE_DIR"

case "$COMMAND" in
  write)
    if [[ -z "$STEP" || -z "$REPO" || -z "$STATUS" ]]; then
      echo '{"error": "write requires --step, --repo, --status"}' >&2
      exit 1
    fi

    TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    python3 - \
      "$STATE_FILE" "$STEP" "$REPO" "$STATUS" "$OBJECTIVE" "$TIMESTAMP" \
      "$PROJECT_ROOT" <<'PY'
import json, sys, os

state_file = sys.argv[1]
step       = int(sys.argv[2])
repo       = sys.argv[3]
status     = sys.argv[4]
objective  = sys.argv[5]
timestamp  = sys.argv[6]
proj_root  = sys.argv[7]

# Lire l'état existant pour conserver l'historique
existing = {}
if os.path.isfile(state_file):
  try:
    existing = json.loads(open(state_file).read())
  except Exception:
    existing = {}

history = existing.get("history", [])
if existing.get("step") is not None:
  history.append({
    "step": existing["step"],
    "status": existing.get("status"),
    "timestamp": existing.get("updated_at"),
  })

# Limiter l'historique aux 20 dernières entrées
history = history[-20:]

state = {
  # Champs compatibles task-flow/latest.json
  "task":          f"repo-analysis:step-{step:02d}:{repo}",
  "flow":          "repo-analysis",
  "kind":          "workflow",
  "status":        status,
  "event":         "step-update",
  "exitCode":      0 if status in ("completed", "in_progress") else 1,
  "updated_at":    timestamp,
  # Champs spécifiques repo-analysis
  "step":          step,
  "repo":          repo,
  "objective":     objective,
  "project_root":  proj_root,
  "history":       history,
}

with open(state_file, "w", encoding="utf-8") as f:
  json.dump(state, f, indent=2, ensure_ascii=False)

print(json.dumps({"ok": True, "step": step, "repo": repo, "status": status}))
PY
    ;;

  read)
    if [[ -f "$STATE_FILE" ]]; then
      cat "$STATE_FILE"
    else
      echo '{"step": 0, "repo": "", "status": "not_started", "objective": "", "flow": "repo-analysis"}'
    fi
    ;;

  clear)
    if [[ -f "$STATE_FILE" ]]; then
      rm -f "$STATE_FILE"
      echo '{"ok": true, "message": "Etat repo-analysis reinitialise"}'
    else
      echo '{"ok": true, "message": "Aucun etat a reinitialiser"}'
    fi
    ;;

  *)
    echo '{"error": "Commande inconnue. Utiliser: write | read | clear"}' >&2
    exit 1
    ;;
esac
