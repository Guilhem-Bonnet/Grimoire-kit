---
description: "Compléter le pack de preuve de la tâche courante"
argument-hint: "[task-id]"
tools: ["read", "edit", "execute"]
---

Mets à jour la preuve de la tâche courante.

1. Résous la tâche : `grimoire -o json standard gate check` (ou
   `--task-id <argument>` si un identifiant est fourni).
2. Ouvre `_grimoire-output/evidence/<task_id>/task-envelope.md` et
   `evidence-pack.md`.
3. Complète le pack avec ce qui a réellement été fait dans cette session :
   commandes exécutées et leur sortie utile, tests verts nommés, diffs clés
   avec leur effet. Une ligne par preuve, chemin exact.
4. Remplace le résumé placeholder s'il est encore là.
5. Termine par `grimoire standard gate check --strict`.

N'invente aucune preuve : une commande que tu n'as pas lancée dans cette
session n'a pas sa place dans le pack.
