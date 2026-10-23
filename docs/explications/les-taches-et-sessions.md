---
audience: utilisateur
---

# Les tâches et les sessions

Une tâche est un travail déclaré dans le projet, avec les critères qui diront s'il est fini. Une session est un tour de travail d'un agent, dans ton hôte (Claude Code, Copilot). Le principe : une session prend une tâche et te rend son travail à relire.

## Le cycle d'une tâche

```bash
grimoire task add "Corriger l'export CSV" -a "pytest tests/test_export.py passe" --owner guilhem --ready
grimoire task claim <id>
grimoire task show <id>
grimoire task close <id>
```

`add` ouvre la tâche avec au moins un critère. `claim` la réserve pour une session. `show` détaille ce que chaque prochain pas exigera. `close` ne passe que si la tâche est vérifiée. Chaque changement d'état franchit le contrôle des preuves : un refus nomme la preuve manquante et le remède, et rien n'est écrit. Voir [Le refus de preuve, ligne par ligne](le-refus-de-preuve.md).

## Qui tient quoi

- Le registre de missions (le *Mission Ledger*) est la source des tâches. Le tableau du standard (`_grimoire/standard/task-board.yaml`) n'en est qu'une projection : le modifier à la main ne change rien. Voir [la décision sur le registre](decisions/adr-005-mission-ledger-source-of-truth.md) et [celle sur l'unification des tâches](decisions/adr-007-unification-des-taches.md).
- La session qui tient une tâche la reconnaît par `GRIMOIRE_TASK_ID`, sinon par son unique claim actif ; la règle complète est dans la [référence des tâches](../reference/cli/taches.md#quelle-tache-la-session-porte).
- Tu diriges en cours de route : `task prioritize` change la priorité, `task comment` pose une consigne que la session lit au tour suivant.

## Ce que la session rend

Une session rend du travail à relire, avec ses preuves. La branche ou la PR qui le porte vient de ton hôte et de git, pas de Grimoire. Quand une tâche attend ta relecture (`needs_verification`), `task review` écrit le reçu de relecture et `task close` en exige un à jour.

## Le tri par vérifiabilité

Grimoire classe chaque tâche d'après ses critères, sans que tu la déclares :

- **V0** : tous les critères nomment un verdict qu'un programme rend seul (un test, un lint, un code de sortie). La tâche peut être confiée au modèle le plus léger.
- **V1** : au moins un critère demande une revue par une personne ou un agent. Le modèle intermédiaire suffit, la relecture reste nécessaire.
- **V2** : le critère n'est pas vérifiable par la machine, ou il n'y a aucun critère. Aucun modèle n'est recommandé : la session garde celui qu'elle a.

Un critère vague (« le code est propre ») ne produit jamais V0. Le rejeu réel de ce tri est dans [Le refus de preuve, ligne par ligne](le-refus-de-preuve.md#le-tri-par-verifiabilite). La définition exacte est dans la [référence](../reference/cli/taches.md#verifiabilite-dune-tache).

## Voir aussi

- [Le tableau de bord](le-tableau-de-bord.md), où l'espace Exécuter montre les tâches.
- [La mémoire du projet](memoire.md) : à la clôture et au blocage d'une tâche, ce qui a été appris est consigné.
- [Les limites](les-limites.md).
