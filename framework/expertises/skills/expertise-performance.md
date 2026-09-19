<!-- EXPERTISE: performance — Adaptez à votre projet. -->
---
description: "Diagnostiquer et corriger la performance par la mesure, pas par l'intuition — profilage, budget de performance, invalidation de cache assumée. À utiliser une fois explicitement attachée, pour une régression ou une optimisation de chemin chaud — pas liée à une extension de fichier, s'applique à tout langage."
tools: ["read", "edit", "execute"]
---

# Performance

Expertise transversale sur le diagnostic et l'optimisation de performance, indépendante du langage ou de la stack. Chargée sur demande explicite de diagnostic de régression ou d'optimisation de chemin chaud.

## Principes

- Mesurer avant d'optimiser : c'est le profileur qui nomme le goulot d'étranglement, jamais l'intuition — règle non négociable, prioritaire sur toutes les autres.
- Un budget de performance se pose avant le travail (latence, débit, mémoire cible chiffrés) — « rendre ça plus rapide » sans cible n'est pas un objectif exploitable.
- La complexité algorithmique (Big-O) ne s'examine que sur le chemin chaud réellement identifié — la complexité du reste du code n'a le plus souvent aucun impact mesurable.
- Un cache n'a le droit d'exister qu'une fois sa stratégie d'invalidation nommée — une donnée périmée servie silencieusement est pire que la lenteur qu'elle remplaçait.
- Tout chemin qui a déjà causé un incident de performance reçoit un benchmark de non-régression intégré en CI, pour ne pas revivre le même incident.

## Garde-fou

Introduire un cache, une dénormalisation, ou un découpage async/job en arrière-plan pour « corriger » une performance sans baseline mesurée au préalable est refusé — ces choix ajoutent une complexité opérationnelle réelle qui doit être justifiée par un chiffre mesuré, pas supposée.

## Diagnostiquer une régression

Reproduire la régression sous profileur → comparer contre la dernière baseline ou le dernier benchmark connu comme bon → identifier le chemin chaud réellement changé, pas celui supposé à l'œil.

## Optimiser un chemin chaud

Mesurer la baseline → changer une seule chose à la fois → remesurer dans les mêmes conditions → ne garder que ce qui a mesurablement aidé, et défaire ce qui n'a rien changé même si ça « aurait dû » aider.

## Checklist de revue

- Vérifier qu'un profileur, pas une intuition, a désigné le chemin chaud concerné.
- Confirmer qu'un budget de performance chiffré existait avant le travail d'optimisation.
- Vérifier que tout nouveau cache déclare explicitement sa stratégie d'invalidation.
- Confirmer qu'un chemin ayant causé un incident dispose d'un benchmark de non-régression en CI.
- Refuser toute complexité ajoutée (cache, dénormalisation, async) sans baseline mesurée à l'appui.
