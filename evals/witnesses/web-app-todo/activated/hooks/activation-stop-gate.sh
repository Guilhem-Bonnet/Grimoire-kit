#!/usr/bin/env bash
# Bras « activated » — hook Stop (protocole evals v2).
# Refuse la clôture de session tant que le standard n'est pas engagé :
# enveloppe de tâche remplie ET `grimoire standard gate check` vert.
# Contrat hook Stop (Claude Code) : exit 2 + stderr = clôture bloquée,
# le stderr est renvoyé à l'agent comme consigne de reprise.
#
# Garde-fous anti-boucle :
# - nombre de blocages borné (ACTIVATION_MAX_BLOCKS, défaut 3) via un
#   compteur fichier ; au-delà, la clôture est autorisée (fail-open borné,
#   le run-record garde la trace de l'échec via gate_ok=false) ;
# - si le projet n'est pas enrôlé ou si le CLI grimoire est absent,
#   la clôture est autorisée (le hook ne doit jamais rendre un run
#   impossible à terminer pour une raison d'environnement).
set -uo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" || exit 0

# Consommer le stdin JSON (stop_hook_active, etc.) sans en dépendre :
# la borne anti-boucle est portée par le compteur fichier.
cat > /dev/null 2>&1 || true

# Sécurité : ne rien bloquer hors d'une copie enrôlée.
[ -d "_grimoire/standard" ] || exit 0
command -v grimoire > /dev/null 2>&1 || exit 0

MAX_BLOCKS="${ACTIVATION_MAX_BLOCKS:-3}"
COUNT_FILE="_grimoire-output/evals-activation/stop-blocks.count"
COUNT=0
[ -f "$COUNT_FILE" ] && COUNT="$(tr -cd '0-9' < "$COUNT_FILE")"
COUNT="${COUNT:-0}"
if [ "$COUNT" -ge "$MAX_BLOCKS" ]; then
  exit 0
fi

REASONS=()
TASK_ID="${ACTIVATION_TASK_ID:-bootstrap}"
ENVELOPE="_grimoire-output/evidence/${TASK_ID}/task-envelope.md"

if [ ! -f "$ENVELOPE" ]; then
  REASONS+=("L'enveloppe de tâche ${ENVELOPE} est absente.")
else
  # Mêmes marqueurs de placeholder que ceux vérifiés par le kit
  # (src/grimoire/core/agentic_standard.py, _verify_task_envelope).
  if grep -qF -- '- Current state: `intake | planned | executing | validating | blocked | done`' "$ENVELOPE"; then
    REASONS+=("L'enveloppe de tâche contient encore le placeholder d'état.")
  fi
  if grep -qF -- '|  | read-only |  |  |' "$ENVELOPE"; then
    REASONS+=("Le périmètre d'outils (tool boundary) de l'enveloppe n'est pas renseigné.")
  fi
fi

GATE_OUT="$(grimoire standard gate check . --task-id "$TASK_ID" --target-state review 2>&1)"
GATE_RC=$?
if [ "$GATE_RC" -ne 0 ]; then
  REASONS+=("grimoire standard gate check . --task-id ${TASK_ID} --target-state review échoue (exit ${GATE_RC}) : ${GATE_OUT}")
fi

if [ "${#REASONS[@]}" -eq 0 ]; then
  rm -f "$COUNT_FILE"
  exit 0
fi

mkdir -p "$(dirname "$COUNT_FILE")"
echo "$((COUNT + 1))" > "$COUNT_FILE"

{
  echo "[eval-activation] Clôture refusée (blocage $((COUNT + 1))/${MAX_BLOCKS}). Manquements :"
  for reason in "${REASONS[@]}"; do
    echo "- ${reason}"
  done
  echo "Actions attendues : remplir l'enveloppe de tâche et l'evidence-pack,"
  echo "déclarer la tâche (task_id: ${TASK_ID}, status: review) dans _grimoire/standard/task-board.yaml,"
  echo "générer les artefacts runtime (grimoire standard context build . --task-id ${TASK_ID} ;"
  echo "grimoire standard decision trace . --task-id ${TASK_ID}), puis relancer"
  echo "grimoire standard gate check . --task-id ${TASK_ID} --target-state review jusqu'à OK."
} >&2
exit 2
