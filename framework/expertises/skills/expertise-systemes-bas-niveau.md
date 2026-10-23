<!-- EXPERTISE: systemes-bas-niveau — Adaptez à votre projet. -->
---
description: "Raisonner sous les abstractions du langage — disposition mémoire, primitives de concurrence, frontières FFI, budget de performance mesuré. À utiliser pour un bug de concurrence, une frontière mémoire multi-langage ou une optimisation de chemin chaud — pas pour du code applicatif classique où le langage gère déjà la mémoire et la synchronisation sans intervention."
tools: ["read", "edit", "execute"]
---

# Systèmes bas niveau

Expertise ciblée sur ce qui se passe sous les abstractions habituelles du langage — mémoire, concurrence au niveau primitives, frontières entre langages, performance mesurée. Chargée quand l'utilisateur a explicitement besoin de descendre à ce niveau, pas par défaut sur du code applicatif standard.

## Principes

- Toute structure partagée entre threads déclare explicitement son schéma de synchronisation en commentaire — mutex, atomique, ou immutable-après-construction ; l'absence de déclaration est traitée comme un risque de data race, pas comme une omission bénigne.
- « Thread-safe » n'est pas une étiquette suffisante — préciser l'ordre mémoire réel en jeu (acquire/release, sequentially consistent, relaxed) pour toute opération atomique qui n'est pas triviale.
- La disposition mémoire compte : distinguer explicitement stack et heap, connaître l'alignement et la taille d'une ligne de cache pertinents, et repérer le false sharing quand deux threads modifient des champs voisins dans la même ligne de cache.
- Une frontière FFI transporte un coût de marshaling et une question de propriété (ownership) qui ne se résout pas implicitement — décider explicitement quel côté alloue, quel côté libère, et vérifier la convention d'appel (calling convention) des deux côtés.
- L'ABA problem n'est pas théorique dans une structure lock-free avec réutilisation de mémoire (pools, allocateurs custom) — vérifier qu'un compare-and-swap ne peut pas être trompé par une valeur revenue à son état initial.
- Mesurer avant d'optimiser : connaître le Big-O du chemin chaud identifié par un profileur, pas du chemin supposé, et évaluer si le gain mesuré justifie la perte de lisibilité.

## Garde-fou

Toucher du code lock-free ou une frontière de propriété FFI sans un test qui exerce réellement la race ou la frontière (pas seulement le chemin heureux) n'est pas un changement prêt à livrer — un data race ou un use-after-free côté FFI reste silencieux jusqu'à ce qu'il ne le soit plus, souvent en production sous charge ; exiger ce test avant de fusionner, et alerter si le projet n'a pas d'outillage pour l'exécuter (race detector, sanitizer).

## Diagnostiquer un problème de concurrence

Reproduire le bug sous un détecteur de race (ThreadSanitizer en C/C++, `loom` en Rust, `go test -race` en Go, `-Xcheck:jni` ou équivalent selon la frontière) avant de toucher au code → isoler la structure partagée en cause et son schéma de synchronisation réel → corriger la cause (ordre mémoire, section critique manquante, durée de vie) plutôt qu'ajouter un verrou au hasard → rejouer sous le détecteur pour confirmer que la race a disparu, pas seulement qu'elle est devenue moins fréquente.

## Optimiser un chemin chaud

Profiler d'abord (`perf`, `valgrind --tool=callgrind`, profileur natif du langage) sur une charge représentative → identifier la fonction réellement chaude, pas celle supposée à l'œil → vérifier son Big-O et son allocation mémoire avant de micro-optimiser → appliquer un seul changement à la fois → remesurer sous le même profileur pour confirmer le gain plutôt que de le supposer.

## Vérifier une frontière FFI

Identifier qui alloue et qui libère chaque valeur qui traverse la frontière → vérifier la correspondance des conventions d'appel et des tailles de types (padding, endianness si pertinent) → écrire un test qui exerce la frontière avec des valeurs limites (null, taille zéro, chaînes non terminées) → documenter la règle de propriété directement au point d'appel, pas seulement dans un commentaire lointain.

## Checklist de revue

- Chaque structure partagée entre threads a son schéma de synchronisation documenté en commentaire.
- Chaque opération atomique non triviale précise son ordre mémoire (acquire/release vs relaxed).
- Chaque frontière FFI a une règle de propriété explicite et un test qui l'exerce.
- Toute optimisation de chemin chaud s'appuie sur une mesure avant/après du même profileur, pas sur une intuition.
- Aucun changement lock-free ou FFI n'est fusionné sans passage sous détecteur de race ou test dédié à la frontière.
