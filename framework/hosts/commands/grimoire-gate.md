---
description: "Vérifier les gates de preuve de la tâche courante"
argument-hint: "[task-id]"
tools: ["read", "execute"]
---

Vérifie les gates de preuve du standard agentique.

1. Exécute `grimoire -o json standard gate check --strict`. Si un
   identifiant de tâche est fourni en argument, ajoute `--task-id <argument>`.
2. Si les gates sont verts : dis-le en une ligne, avec le profil et la tâche.
3. Si les gates sont rouges : liste chaque élément manquant avec son chemin
   attendu, puis indique la plus petite action qui le rend vert.

Un code de sortie 2 signale un échec bloquant sur profil gouverné : la tâche ne
peut pas être déclarée terminée dans cet état.
