---
audience: utilisateur
---

# Les limites

Cette page dit ce que Grimoire ne fait pas, et ce qu'il ne prouve pas. Elle se lit avant toute attente.

## Ce que le kit ne fait pas

- **Il ne crée ni branche, ni copie du dépôt, ni PR.** Une session rend son travail dans le dépôt de ton hôte ; la branche ou la PR est l'affaire de l'hôte et de git.
- **Le tableau de bord ne lance pas de session d'agent.** Il affiche et organise ; les sessions démarrent depuis ton hôte. Voir [Le tableau de bord](le-tableau-de-bord.md).
- **La mémoire est celle d'un projet.** Elle ne se partage pas d'un projet à l'autre. Voir [La mémoire du projet](memoire.md).

## Ce qui dépend de ton hôte

- **Le contrôle des preuves refuse pour de bon en profil `governed`, et prévient ailleurs.** Dans les autres profils, il signale le manque sans bloquer.
- **Le blocage en cours de tour suppose un hôte à hooks.** Là où l'hôte n'en a pas, la règle ne s'applique qu'en CI. La table des hôtes est dans [Surfaces hôtes](../reference/hotes.md) ; ce qu'un hôte ne sait pas faire y est déclaré comme dégradation, avec son repli.
- **Le rappel de ce que le projet a décidé au démarrage d'une session suppose un hôte à hooks.** Sur les autres, il se demande à la main avec `grimoire task recall`.
- **Une consigne posée par `task comment` est lue au tour suivant**, pas au milieu du tour en cours.

## Ce qui n'est pas mesuré

Grimoire n'affirme aucun gain de temps, de coût ou de qualité. Les mécanismes décrits ici sont vérifiés ; leur effet sur ton travail ne l'est pas.

## Voir aussi

- [Le refus de preuve, ligne par ligne](le-refus-de-preuve.md)
- [Le changelog](../reference/changelog.md), pour ce qui existe dans quelle version.
