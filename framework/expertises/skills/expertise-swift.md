<!-- EXPERTISE: expertise-swift — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code Swift — optionals explicites, value types par défaut, composition par protocoles, SwiftLint et XCTest en gate. À utiliser dès qu'une demande porte sur un `Package.swift` ou un fichier .swift — pas pour le Kotlin, ni pour le PHP, ni pour l'infrastructure conteneurs/K8s."
tools: ["read", "edit", "execute"]
---

# Swift

Expertise Apple activée sur détection d'un `Package.swift` ou d'un fichier `.swift` : le compilateur force déjà la déclaration des optionals, cette fiche couvre ce qu'il ne vérifie pas lui-même.

## Principes

- Les optionals se traitent explicitement (`if let`/`guard let`) — le force-unwrap `!` n'est acceptable qu'avec un commentaire prouvant que le nil est impossible à cet endroit, même discipline que le `!!` Kotlin.
- Value types (`struct`/`enum`) par défaut pour tout modèle de données ; `class` réservé aux cas où l'identité ou le partage de mutation est le besoin réel, pas un choix par habitude.
- Composition par protocoles plutôt qu'héritage de classes profond — un protocole avec extension par défaut remplace la plupart des hiérarchies.
- Gestion d'erreurs par `Result` ou `async throws`, jamais par optional qui avale silencieusement l'échec sans distinguer "pas de valeur" de "erreur".
- `SwiftLint` fait partie du gate, pas une option : à exécuter avant toute revue de code.
- Tests via `XCTest`, un test par comportement observable, pas un test qui recompile juste l'implémentation.

## Garde-fou

Toute closure fortement capturante passée à un objet qui survit à `self` (delegate, closure stockée, callback réseau long) exige confirmation avant modification : vérifier `[weak self]`/`[unowned self]` explicitement, un cycle de rétention silencieux ne se voit qu'à l'usage mémoire.

## Implémenter une feature

Lire le module cible et ses conventions de types existantes (struct vs class, protocoles) → identifier les types et protocoles impactés → implémenter en respectant la valeur par défaut et la gestion d'erreurs explicite (pas d'optional pour masquer un design d'erreur incomplet) → écrire les tests dans la target de test XCTest correspondante → `swift test`.

## Corriger un bug

Écrire un test XCTest qui reproduit le bug → diagnostiquer via le débogueur ou les logs (`os_log`) → corriger au point exact, sans force-unwrap pour contourner le problème → vérifier que le test du bug et la suite existante passent, SwiftLint propre.

## Bug hunt

Vagues successives, vérification après chacune : build (`swift build`) → lint (`swiftlint`) → tests (`swift test`) → force-unwraps sur des données de décodage réseau/JSON (`grep -rn "try!\|as!" Sources/`) → cycles de rétention par closures capturant `self` sans `[weak self]` → mutation d'UI depuis un thread de fond sans `@MainActor`/dispatch explicite.

## Checklist de revue

- Chaque `!` (force-unwrap ou `try!`) a sa justification et l'invariant tient réellement.
- Les closures longue durée capturant `self` utilisent `[weak self]` ou `[unowned self]` de façon délibérée.
- Aucune mutation d'état UI depuis un contexte hors `@MainActor` sans dispatch explicite.
- `swiftlint` passe sans désactivation locale non justifiée (`// swiftlint:disable`).
- Les erreurs réseau/décodage remontent via `Result`/`throws`, pas par un optional silencieux.
