#!/usr/bin/env bash
# Bras « activated » — hook SessionStart (protocole evals v2).
# Injecte dans le contexte de session l'obligation d'engager le standard :
# le stdout d'un hook SessionStart est ajouté au contexte de l'agent.
# Contrat : https://code.visualstudio.com/docs/copilot/customization/hooks
# (format Claude Code : stdin JSON, stdout = additionalContext, exit 0).
set -euo pipefail

# Consommer le stdin JSON sans en dépendre (session_id, source, etc.).
cat > /dev/null 2>&1 || true

cat <<'EOF'
[eval-activation] Ce projet est enrôlé dans le standard gouverné grimoire-kit
(profil starter). Les obligations suivantes sont NON NÉGOCIABLES et vérifiées
mécaniquement à la clôture de session :

1. AVANT toute modification de code : ouvrir l'enveloppe de tâche
   `_grimoire-output/evidence/bootstrap/task-envelope.md` et la remplir
   (état courant, tableau de contexte, périmètre d'outils, gates).
   Aucune ligne placeholder ne doit subsister.
2. Déclarer la tâche sur le tableau `_grimoire/standard/task-board.yaml`
   (le créer s'il n'existe pas) au format :

   ```yaml
   $schema: "grimoire-agentic-standard-task-board/v1"
   states: [proposed, ready, in_progress, blocked, review, accepted, released, archived]
   tasks:
     - task_id: bootstrap
       title: "<résumé de la tâche demandée>"
       status: in_progress   # passer à review avant clôture
       acceptance_criteria:
         - "<critères observables>"
       evidence_pack_ref: "_grimoire-output/evidence/bootstrap/evidence-pack.md"
   ```
3. Générer les artefacts runtime du standard :
   `grimoire standard context build . --task-id bootstrap`
   `grimoire standard decision trace . --task-id bootstrap`
4. AVANT de déclarer la tâche terminée : remplir
   `_grimoire-output/evidence/bootstrap/evidence-pack.md` (résumé, inventaire
   des preuves : diff, sorties de tests), passer le `status` de la tâche à
   `review`, puis exécuter :
   `grimoire standard gate check . --task-id bootstrap --target-state review`
   et ne conclure que si la commande retourne OK (code de sortie 0).

La session ne peut pas se clôturer tant que ces gates échouent : un hook Stop
refuse la clôture et renvoie la liste des manquements.
EOF
exit 0
