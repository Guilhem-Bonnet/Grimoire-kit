<!-- EXPERTISE: expertise-kotlin — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code Kotlin — null-safety du langage, coroutines structurées, data/sealed classes, ktlint/detekt en gate. À utiliser dès qu'une demande porte sur un fichier .kt ou .kts — pas pour le Swift, ni pour le PHP, ni pour l'infrastructure conteneurs/K8s."
tools: ["read", "edit", "execute"]
---

# Kotlin

Expertise JVM activée sur détection d'un fichier `.kt` ou `.kts` : le compilateur garantit déjà la null-safety statique, cette fiche couvre ce qu'il n'empêche pas d'écrire.

## Principes

- La null-safety est une garantie du langage, pas une convention — `?.`/`?:`/smart cast d'abord ; un `!!` exige un commentaire juste au-dessus expliquant pourquoi le null est provablement impossible à cet endroit.
- Concurrence structurée par coroutines : tout `launch`/`async` est rattaché à un `CoroutineScope` lié à un cycle de vie (ViewModel, requête, service), jamais à `GlobalScope.launch`.
- `data class` par défaut pour tout porteur de données — pas d'`equals`/`hashCode`/`toString` écrits à la main quand le compilateur les génère correctement.
- Modélisation d'état par `sealed class`/`sealed interface` avec `when` exhaustif, plutôt que des hiérarchies de classes ouvertes où un cas manquant compile silencieusement.
- `ktlint` et `detekt` font partie du gate, pas une option : `./gradlew ktlintCheck` et `./gradlew detekt` avant toute revue.
- Build reproductible via Gradle Kotlin DSL (`build.gradle.kts`) — versions figées, pas de résolution dynamique (`+`) sur les dépendances de production.

## Garde-fou

Toute introduction d'état mutable partagé entre coroutines (var au niveau classe, collection mutable partagée) exige confirmation avant modification : l'accès concurrent doit passer par un `Mutex`, un acteur ou une structure immuable, jamais supposé thread-safe par défaut.

## Implémenter une feature

Lire le module cible et ses conventions de packages/DI existantes → identifier les types, sealed classes et coroutines scopes impactés → implémenter en respectant la null-safety et la portée de concurrence (pas de scope global pour éviter un design correct) → écrire les tests dans `src/test/kotlin` (JUnit5/Kotest) → `./gradlew test`.

## Corriger un bug

Écrire un test qui reproduit le bug → diagnostiquer via la stack trace ou les logs de coroutine (`kotlinx-coroutines-debug`) → corriger au point exact, sans `!!` ni `lateinit` pour contourner le problème → vérifier que le test du bug et la suite existante passent, lint propre.

## Bug hunt

Vagues successives, vérification après chacune : lints/style (`./gradlew ktlintCheck`) → analyse statique (`./gradlew detekt`) → tests (`./gradlew test`) → coroutines fuyant leur scope (`GlobalScope.launch` en recherche manuelle, `grep -rn "GlobalScope" src/`) → `lateinit var` masquant une vraie nullabilité → état mutable partagé entre coroutines sans `Mutex`/actor.

## Checklist de revue

- Chaque `!!` a son commentaire justificatif et l'invariant tient réellement.
- Aucun `GlobalScope.launch` — toute coroutine a un scope lié à un cycle de vie identifiable.
- `ktlintCheck` et `detekt` passent sans suppression silencieuse (`@Suppress` non justifié).
- Les états modélisés en `sealed class` couvrent tous les cas dans les `when` (pas de `else` fourre-tout qui masque un cas oublié).
- Pas d'accès concurrent à une collection ou variable mutable partagée sans synchronisation explicite.
