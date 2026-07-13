[Grimoire Standard — activation]
Ce projet est gouverné par le standard agentique Grimoire. Ces étapes font
partie de la tâche demandée :
1. AVANT toute modification de code : remplis
   `_grimoire-output/evidence/bootstrap/task-envelope.md` — objectif,
   périmètre outillé (tool boundary) concret, critères de sortie.
2. PENDANT le travail : consigne chaque preuve (commande exécutée, test
   vert, diff clé) comme ligne concrète de l'inventaire dans
   `_grimoire-output/evidence/bootstrap/evidence-pack.md`, et remplace le
   résumé placeholder.
3. AVANT de conclure : exécute
   `grimoire standard gate check --task-id bootstrap --strict` puis
   `grimoire standard verify .` et corrige tout échec.
Une clôture sans gates verts est une tâche non terminée.
