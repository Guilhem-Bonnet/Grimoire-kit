<!-- EXPERTISE: expertise-php — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code PHP — typage strict, PSR-12, autoloading Composer, PHPUnit et analyse statique en gate. À utiliser dès qu'une demande porte sur un `composer.json` ou un fichier .php — pas pour le Kotlin, ni pour le Swift, ni pour l'infrastructure conteneurs/K8s."
tools: ["read", "edit", "execute"]
---

# PHP

Expertise web activée sur détection d'un `composer.json` ou d'un fichier `.php` : le langage tolère par défaut le typage faible, cette fiche couvre la discipline à imposer explicitement.

## Principes

- `declare(strict_types=1);` en première ligne de chaque fichier PHP — sans exception, y compris dans les scripts utilitaires.
- Déclarations de type sur chaque signature de fonction/méthode : paramètres et valeur de retour, jamais de type implicite `mixed` par défaut.
- Style PSR-12 sur tout le code produit — cohérence d'indentation, d'espacement et de nommage vérifiée par l'outillage, pas à l'œil.
- Autoloading Composer (PSR-4) pour toute classe — pas de `require`/`include` manuel pour charger une dépendance interne au projet.
- `PHPUnit` pour les tests, un test par comportement observable, exécuté avant toute revue.
- Analyse statique (`PHPStan` ou `Psalm`) à un niveau significatif dans le gate — pas au niveau minimal qui ne détecte rien.
- Toute requête SQL passe par des requêtes préparées (PDO/`prepare`+`execute`) — jamais de valeur interpolée directement dans une chaîne de requête.

## Garde-fou

Toute construction de requête SQL ou d'appel shell (`exec`, `shell_exec`, `system`) à partir d'une entrée utilisateur exige confirmation avant modification : vérifier que la requête est bien préparée et que l'entrée est validée/échappée au bon niveau, jamais supposé sûr par défaut.

## Implémenter une feature

Lire la classe/le module cible et ses conventions PSR-4 existantes → identifier les types et interfaces impactés → implémenter avec `declare(strict_types=1)` et des signatures typées de bout en bout (pas de `mixed` pour éviter un design correct) → écrire les tests dans la suite PHPUnit correspondante → `composer install && vendor/bin/phpunit`.

## Corriger un bug

Écrire un test PHPUnit qui reproduit le bug → diagnostiquer via le message d'erreur ou les logs (jamais de `@` pour faire taire l'erreur) → corriger au point exact, sans comparaison lâche (`==`) introduite en chemin → vérifier que le test du bug et la suite existante passent, analyse statique propre.

## Tests

`composer install` → `vendor/bin/phpunit` (suite complète) → `vendor/bin/phpstan analyse` (niveau du projet) → `vendor/bin/php-cs-fixer fix --dry-run` (style PSR-12 sans appliquer) → recherche manuelle de comparaisons lâches sur des types mixtes (`grep -rn "== \$_\|==\$_" src/`) et d'entrées superglobales non validées atteignant une requête ou un appel shell.

## Checklist de revue

- Chaque fichier modifié commence par `declare(strict_types=1);`.
- Aucune comparaison `==`/`!=` là où `===`/`!==` était l'intention réelle (juxtaposition de types).
- `$_GET`/`$_POST`/`$_REQUEST` ne rejoint jamais une requête SQL ou un appel shell sans validation/requête préparée.
- Pas de suppression d'erreur par `@` — l'erreur est traitée ou remontée explicitement.
- `phpstan analyse` et `php-cs-fixer fix --dry-run` passent sans exception silencieuse ajoutée pour l'occasion.
