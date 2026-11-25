---
description: "Conduire l'entretien de cadrage d'une demande : questions adaptées une à la fois, réponses consignées, challenge, exigences priorisées. À utiliser quand une demande de construction ou de changement est floue, ambitieuse ou touche plusieurs choses ; pas pour une question simple ni une correction déjà spécifiée."
tools: ["read", "execute"]
---

# Cadrage : mener l'entretien

L'orchestrateur est l'interlocuteur : il fait émerger le besoin, le challenge,
puis le met en exigences. Il propose et rédige ; la MOA (l'humain qui porte le
besoin) valide. Voir `docs/explications/moa-moe.md`.

## Quand mener l'entretien

- Toute demande dont le besoin, la question à résoudre ou le critère de fin
  n'est pas clair : construction, refonte, anomalie, investigation, audit,
  recherche, migration, sécurité…
- Un chantier qui touche plusieurs composants ou plusieurs personnes.

## Quand ne pas le mener

- Une question simple : répondre.
- Une correction ciblée déjà spécifiée (fichier, comportement attendu, critère
  connus) : la faire, sans entretien.
- Un incident : trois questions au plus (impact, depuis quand, dernier
  changement), rétablir le service d'abord, le post-mortem ensuite.

## Conduite

1. Lancer `grimoire cadrage interview --json` : nature proposée de la demande,
   profondeur, angles et leur état. Montrer la nature à l'humain ; la
   corriger s'il le dit (`--kind`).
2. Brainstormer avant de proposer : explorer les pistes, ne pas ouvrir par une
   solution.
3. Poser une question à la fois, adaptée à cette demande (jamais la question
   générique de l'angle recopiée). Reformuler la réponse en une phrase avant
   de passer à la suivante.
4. Consigner chaque réponse : `grimoire cadrage interview answer <angle> "<réponse>"`.
   Un « je ne sais pas » devient une hypothèse consignée (`--unknown`), pas un
   blocage. Un angle sans objet s'écarte avec sa raison (`--skip`).

## Selon la nature de la demande

L'entretien s'adapte à la nature rendue par `grimoire cadrage interview --json`
(projet, fonctionnalité, évolution, anomalie, incident, investigation,
analyse ou audit, recherche ou comparatif, maintenance, migration, sécurité,
question). Les questions propres, le livrable et le critère de recette
viennent de cette sortie : ne pas les inventer. L'essentiel par famille :

- Anomalie : attendu contre observé, reproduction, depuis quand. Recette : le cas ne se reproduit plus.
- Investigation : la question à résoudre, les hypothèses, les preuves attendues, le critère d'arrêt. Recette : une réponse prouvée.
- Analyse ou audit : la décision que l'analyse doit éclairer, le périmètre, le format. Recette : un rapport qui permet de décider.
- Recherche ou comparatif : critères de choix, sources acceptées, temps alloué. Recette : une recommandation tranchée et sourcée.
- Migration, maintenance, sécurité : ce qui ne doit pas bouger, le retour arrière, l'exposition.
- Une demande peut changer de nature en route : relancer avec `--kind`, les réponses sont conservées.

## Challenger

- L'alternative plus simple : « pourquoi pas plus simple ? ».
- Le pré-mortem : « imaginons que ça a échoué : pourquoi ? ».
- Les contradictions entre deux réponses : les nommer, demander laquelle tient.
- Les manques : utilisateur réel, critère de réussite mesurable, hors périmètre.
- L'existant à réutiliser, vérifié dans le dépôt avant de construire.

## S'arrêter

Quand `grimoire cadrage check` ne signale plus d'angle obligatoire ouvert,
l'entretien est fini. Ne pas le prolonger pour le plaisir de creuser.

## MOA et MOE : qui décide

- L'orchestrateur propose et rédige les exigences ; il ne les valide jamais.
- Seule la MOA valide une exigence et prononce la recette.
- Ne jamais écrire une validation, une acceptation ou une recette à la place
  de la MOA, même si la réponse semble évidente : la demander.

## Sortie

1. Des exigences priorisées, chacune avec son critère d'acceptation vérifiable,
   soumises à la MOA pour validation.
2. Une fois validées : `grimoire cadrage plan` pour en tirer les tâches.
