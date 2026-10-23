---
audience: référence
---

# Référence CLI

Toutes les commandes disponibles via `grimoire [commande]`, rangées par outil, dans l'ordre où le manuel présente les cinq outils.

!!! tip "Auto-complétion"
    Installez l'auto-complétion : `grimoire completion install --shell bash` (ou `zsh`, `fish`).

!!! tip "Raccourcis"
    Des aliases courts sont disponibles : `i`=init, `d`=doctor, `s`=status, `v`=validate, `l`=lint, `ck`=check, `u`=up, `c`=config, `r`=registry.

| Page | Ce qu'elle décrit |
|---|---|
| [Le projet](projet.md) | création, état et diagnostic d'un projet ; surfaces hôtes ; gestion de projet |
| [Le tableau de bord](tableau-de-bord.md) | le cockpit local et la commande `serve` |
| [Les tâches](taches.md) | `grimoire task`, la comptabilité du dispatch |
| [La mémoire du projet](memoire.md) | `grimoire memory` |
| [Le contrôle des preuves](preuve.md) | `grimoire standard`, les gardes du dépôt, les hooks git |
| [La mise à jour](mise-a-jour.md) | `grimoire self update`, canaux et bundle hors ligne |
| [Les flows](flows.md) | blueprints, flows, workflows, besoins d'exécution |
| [Fournisseurs, routeur, file, pause et tick](fournisseurs.md) | le routage des modèles et l'exécution différée |
| [Configuration, registre et divers](divers.md) | `config`, `self`, `registry`, options globales, sortie JSON, codes d'erreur |
