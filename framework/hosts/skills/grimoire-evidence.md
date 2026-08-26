---
description: "Protocole de preuve du standard agentique Grimoire. À utiliser dans un projet enrôlé (présence de _grimoire/standard/) dès qu'une tâche modifie du code, de la configuration ou de l'infrastructure : avant la première modification, pendant le travail, et avant toute conclusion."
tools: ["read", "edit", "execute"]
---

# Protocole de preuve

Ce projet est gouverné par le standard agentique Grimoire. La preuve n'est pas
un livrable annexe : c'est la condition de clôture. Une tâche dont les gates
sont rouges est une tâche non terminée, et le hook de fin de tour le fait
respecter sur les profils `governed` et `production`.

## Identifier la tâche

```bash
grimoire -o json standard gate check
```

La sortie porte `task_id`, `profile` et `state`. Toutes les preuves de la
session vont dans `_grimoire-output/evidence/<task_id>/`. Pour travailler sur
une autre tâche que celle résolue par défaut, exporter `GRIMOIRE_TASK_ID` ou
passer `--task-id`.

## Avant la première modification

Remplir `_grimoire-output/evidence/<task_id>/task-envelope.md` :

- **Objectif** — ce que l'utilisateur obtient à la fin, en une phrase vérifiable.
- **Périmètre outillé** — les outils et les chemins que la tâche autorise, et
  ceux qu'elle exclut. Un périmètre qui dit « tout » n'est pas un périmètre.
- **Critères de sortie** — les faits observables qui feront dire « fini » :
  commande verte, comportement constaté, fichier produit.

Une enveloppe écrite après coup n'est plus une enveloppe : elle décrit ce qui a
été fait au lieu de contraindre ce qui va l'être.

## Pendant le travail

Chaque preuve est une ligne concrète dans
`_grimoire-output/evidence/<task_id>/evidence-pack.md` :

| Compte comme preuve | Ne compte pas |
|---|---|
| commande exécutée avec sa sortie utile | « les tests passent » |
| test vert nommé, avec son identifiant | « j'ai vérifié » |
| diff clé, avec le chemin et l'effet obtenu | « refactorisé proprement » |
| capture d'un comportement observé | intention ou plan |

Remplacer le résumé placeholder du pack au lieu de l'entourer de texte : le
gate lit le pack, pas la conversation.

## Avant de conclure

```bash
grimoire standard gate check --strict
grimoire standard verify .
```

Corriger tout échec avant d'annoncer la fin. Le code de sortie 2 signale un
échec bloquant sur profil gouverné.

## Quand un gate reste rouge

Ne pas contourner, ne pas désactiver, ne pas clore malgré tout. Deux issues
seulement :

1. compléter la preuve manquante — c'est le cas normal ;
2. dire explicitement à l'utilisateur ce qui manque et pourquoi la tâche reste
   ouverte, en nommant le gate et le fichier attendu.

`grimoire standard score` donne l'écart chiffré quand il faut arbitrer.
