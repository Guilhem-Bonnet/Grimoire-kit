<!-- EXPERTISE: expertise-cpp — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code C++ moderne — RAII par défaut, rule of zero/five, CMake avec sanitizers. À utiliser dès qu'une demande porte sur un fichier .cpp/.hpp/.cc/.hh — pas pour le C pur (pas de RAII/classes) ni pour Rust."
tools: ["read", "edit", "execute"]
---

# C++

Expertise systèmes activée sur détection de fichiers `.cpp`/`.hpp`/`.cc`/`.hh` ou d'un `CMakeLists.txt` C++ : le langage donne les outils de sécurité mémoire (RAII, smart pointers), à condition de ne pas repasser en C-avec-des-classes.

## Principes

- RAII par défaut pour toute ressource (mémoire, fichier, verrou, socket) — pas de `new`/`delete` brut dans le code applicatif, `std::unique_ptr`/`std::shared_ptr` ou sémantique de valeur à la place.
- Rule of zero en priorité (laisser le compilateur générer copie/déplacement via des membres RAII) ; rule of five seulement quand la classe gère explicitement une ressource brute.
- `const`-correctness systématique — méthodes, paramètres, membres marqués `const` dès qu'ils ne mutent rien, y compris `const&` pour les paramètres d'objets non triviaux non modifiés.
- Pas de pointeur brut comme référence propriétaire — un pointeur brut n'observe jamais que de la mémoire dont un autre est responsable (non-owning view), jamais l'inverse.
- Idiomes C++17/20 par défaut (`std::optional`, `std::variant`, structured bindings, ranges) plutôt que des patterns C-avec-classes hérités.
- CMake comme système de build avec un preset debug qui active les sanitizers (`-fsanitize=address,undefined`) par défaut, pas en option cachée.

## Garde-fou

Tout `new`/`delete` brut introduit hors d'une classe RAII dédiée (allocateur, wrapper bas niveau) exige confirmation avant modification, de même que toute gestion mémoire manuelle qui contourne délibérément un smart pointer déjà en place dans le fichier.

## Implémenter une feature

Lire le fichier cible et les headers inclus pour les conventions RAII déjà en place → identifier classes, interfaces et invariants impactés → implémenter en sémantique de valeur ou smart pointers, jamais de `new`/`delete` nu → écrire les tests (framework du projet : GoogleTest, Catch2) en couvrant les cas de copie/déplacement si la classe les définit → `cc-verify.sh --stack cpp` (build avec sanitizers + tests + clang-tidy).

## Corriger un bug

Reproduire avec un test qui déclenche le comportement fautif, sanitizers activés (`-fsanitize=address,undefined`) → diagnostiquer via le rapport ASan/UBSan ou l'invalidation d'itérateur suspectée (mutation de conteneur pendant itération) → corriger au point exact en restaurant l'invariant RAII plutôt qu'en ajoutant une libération manuelle → revérifier sous sanitizer et relire les points de slicing potentiels si une hiérarchie de classes est impliquée.

## Sécurité mémoire

`-Wall -Wextra -Wpedantic` plus sanitizers (`-fsanitize=address,undefined`) sur chaque build de test → `clang-tidy` avec les checks `cppcoreguidelines-*` et `modernize-*` → `cppcheck` en complément statique → audit RAII/smart pointer sur le diff (tout `new`/`delete` visible est une alerte) → vérification des invalidations d'itérateur après mutation de conteneur et des exceptions qui pourraient traverser une frontière ABI (`extern "C"`) sans être capturées.

## Checklist de revue

- Aucun `new`/`delete` brut hors d'une classe RAII dédiée.
- Rule of zero respectée par défaut ; rule of five explicite et complète si une ressource brute est gérée.
- Pas de pointeur brut utilisé comme propriétaire d'une ressource.
- Compilation propre sous `-Wall -Wextra -Wpedantic` et sanitizers activés en debug.
- Pas d'exception non capturée susceptible de traverser une frontière `extern "C"`/ABI.
