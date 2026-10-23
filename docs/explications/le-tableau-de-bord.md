---
audience: utilisateur
---

# Le tableau de bord

Le tableau de bord est une page locale, servie par ta machine, où tu vois tes projets Grimoire, leurs tâches, leur mémoire et l'état de leur installation. Dans le code et dans les commandes, il s'appelle le *cockpit*.

## Ce qu'on y fait

```bash
grimoire cockpit start
grimoire cockpit open
```

La coque s'ouvre sur six espaces, un par intention :

| Espace | Pour quoi faire |
|---|---|
| Piloter | voir les projets inscrits et l'état de chacun |
| Concevoir | dessiner les flows (les blueprints) |
| Exécuter | suivre les tâches, leurs critères, leurs preuves |
| Observer | relire la trace de ce qui s'est passé |
| Mémoire | consulter la mémoire du projet |
| Source | parcourir les fichiers du projet et leur écart avec le kit |

Le choix d'un projet se fait dans la page même ; lancé depuis un dossier Grimoire, le tableau de bord s'ouvre directement sur ce projet.

## Ce qu'il ne fait pas

- Il ne sort pas de ta machine : il est servi sur `127.0.0.1` uniquement.
- Il ne cherche pas tes projets sur le disque. Il lit un registre (`~/.grimoire/cockpit/registry.json`) où `grimoire init` inscrit le projet ; `grimoire cockpit add` ou `grimoire cockpit scan` en ajoutent d'autres, à ta demande.
- Il ne démarre pas de session d'agent : les sessions se lancent depuis ton hôte (Claude Code, Copilot). Le tableau de bord montre et organise le travail, il ne le fait pas à ta place.

## Pour aller plus loin

- Les commandes : [référence du tableau de bord](../reference/cli/tableau-de-bord.md).
- Pourquoi une seule coque plutôt que plusieurs pages : [la décision sur la vue de travail](decisions/adr-006-vue-de-travail.md).
- Les tâches qu'on y suit : [Les tâches et les sessions](les-taches-et-sessions.md).
- Ce qui reste hors de portée : [Les limites](les-limites.md).
