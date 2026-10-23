<!-- EXPERTISE: expertise-scala — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code Scala — immutabilité par défaut, pattern matching exhaustif, Option/Either, sbt. À utiliser dès qu'une demande porte sur un `build.sbt` ou un fichier `.scala` — pas pour le Ruby, l'Elixir, ni pour un autre stack."
tools: ["read", "edit", "execute"]
---

# Scala

Expertise activée sur détection d'un `build.sbt` ou d'un fichier `.scala` : le compilateur vérifie l'exhaustivité et les types, cette fiche couvre les choix de design qu'il ne tranche pas seul.

## Principes

- Immutabilité par défaut : `val` plutôt que `var`, collections immuables par défaut — une `var` ou une collection mutable se justifie localement, elle ne se suppose pas.
- Pattern matching exhaustif sur `sealed trait`/`case class` : le compilateur doit pouvoir vérifier l'exhaustivité, donc pas de `case _ =>` qui avale un cas qui devrait être nommé explicitement.
- `Option`/`Either` pour tout chemin d'échec attendu, jamais `null` ni exception pour un cas prévisible — l'exception reste réservée à l'imprévu.
- For-comprehensions pour la lisibilité dès que l'enchaînement dépasse deux `flatMap` imbriqués — la pyramide de `flatMap` imbriqués est un signal à refactorer.
- Tests en ScalaTest ou munit, un comportement par test, noms de tests qui décrivent le comportement attendu plutôt que la méthode appelée.
- Builds sbt reproductibles : versions de dépendances et de plugins épinglées, pas de version flottante (`+`, `latest.release`) en dépendance de production.

## Garde-fou

Toute publication ou changement de version binaire incompatible (breaking change d'API publique, changement de version majeure dans `build.sbt`) exige confirmation avant modification : vérifier l'impact sur les consommateurs en aval.

## Implémenter une feature

Lire le module cible et ses `case class`/`sealed trait` pour les conventions établies → identifier les types impactés et les chemins d'échec (`Option`/`Either`) → implémenter en gardant l'immutabilité et l'exhaustivité du pattern matching → écrire les tests en ScalaTest/munit → `sbt test` puis `sbt scalafmtCheck` pour vérifier.

## Corriger un bug

Écrire un test qui reproduit le bug → diagnostiquer via le message du compilateur ou la stacktrace (attention à un `Future` qui avale une exception silencieusement) → corriger au point exact sans introduire de `null` ni de wildcard qui masque le cas → vérifier que le test du bug et la suite existante passent.

## Tests

Vagues successives, vérification après chacune : tests (`sbt test`) → formatage (`sbt scalafmtCheck`) → lint/réécritures (`sbt scalafixAll --check`) → exhaustivité du pattern matching (recherche des `case _ =>` ajoutés récemment sur des `sealed trait`) → appels bloquants dans un `Future` (recherche d'I/O synchrone dans le contexte d'exécution par défaut, qui affame le pool de threads).

## Checklist de revue

- Aucun `var` ni collection mutable sans justification locale.
- Aucun `case _ =>` ajouté pour faire taire le compilateur sur un `sealed trait`/`case class`.
- Chemins d'échec attendus modélisés en `Option`/`Either`, pas en `null` ni exception.
- Aucun appel bloquant dans un `Future` sans contexte d'exécution dédié.
- `sbt scalafmtCheck` et `sbt scalafixAll --check` passent sans exception silencieuse.
