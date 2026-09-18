<!-- EXPERTISE: expertise-c — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code C — discipline mémoire manuelle, undefined behavior traité comme un bug, compilation avec sanitizers. À utiliser dès qu'une demande porte sur un fichier .c/.h — pas pour le C++ (RAII, classes) ni pour Rust."
tools: ["read", "edit", "execute"]
---

# C

Expertise systèmes activée sur détection de fichiers `.c`/`.h` ou d'un `Makefile`/`CMakeLists.txt` orienté C pur : ici le compilateur ne protège presque rien, la discipline manuelle remplace le garde-fou du langage.

## Principes

- Chaque `malloc`/`calloc`/`realloc` a exactement un `free` correspondant, et l'ownership du pointeur (qui alloue, qui libère) est documenté à la frontière de la fonction, pas implicite.
- Un undefined behavior est un bug même s'il "marche" sur la machine de test — dépassement d'entier signé, use-after-free, aliasing strict violé, ne se justifient jamais par "ça compile et ça tourne".
- Compilation systématique avec `-Wall -Wextra -Werror`, et sanitizers actifs en CI (`-fsanitize=address,undefined`) — un avertissement supprimé sans commentaire est une régression, pas un détail.
- Fonctions de chaînes bornées uniquement (`snprintf`, `strncpy` avec terminaison explicite) — `strcpy`, `sprintf`, `gets` sont interdits sans exception.
- Pas de VLA (tableaux à taille variable) en code de production — préférer une allocation explicite dont l'échec est vérifiable.
- Header guards (`#ifndef`/`#define` ou `#pragma once`) systématiques, et toute taille passée à `malloc` est vérifiée contre un débordement d'entier avant l'appel.

## Garde-fou

Toute taille de buffer calculée dynamiquement (concaténation, copie, allocation dont la taille dépend d'une entrée externe) exige une vérification explicite de débordement avant l'opération, et toute paire malloc/free proche d'un chemin d'erreur (double free, use-after-free potentiel) exige confirmation avant modification.

## Implémenter une feature

Lire le fichier cible et son `.h` associé pour les conventions d'ownership déjà établies → identifier les fonctions et structures impactées → implémenter en vérifiant chaque allocation et chaque taille de buffer → écrire les tests (framework du projet : Unity, Check, ou tests manuels avec `assert`) → `cc-verify.sh --stack c` (build avec `-Wall -Wextra -Werror` + sanitizers + tests).

## Corriger un bug

Reproduire avec un test qui déclenche le comportement fautif sous sanitizer (`-fsanitize=address,undefined`) → diagnostiquer avec le rapport ASan/UBSan ou `valgrind --leak-check=full` → corriger au point exact, en vérifiant l'ownership du pointeur touché → revérifier sous sanitizer que le bug a disparu sans en introduire un autre (fuite, double free).

## Sécurité mémoire

Passage systématique sous `gcc -Wall -Wextra -fsanitize=address,undefined` (ou clang équivalent) avant toute fusion → `valgrind --leak-check=full --show-leak-kinds=all` sur les chemins critiques → `clang-tidy` et `cppcheck` pour les patterns statiques (fuite, null deref, buffer overflow) → relecture manuelle de chaque `malloc`/`free` sur le diff, en particulier les chemins d'erreur qui peuvent sauter un `free`.

## Checklist de revue

- Chaque `malloc`/`calloc`/`realloc` a un `free` identifiable, y compris sur les chemins d'erreur (`goto cleanup` ou équivalent).
- Aucune fonction de chaîne non bornée (`strcpy`, `sprintf`, `strcat`, `gets`).
- Toute taille passée à une allocation est vérifiée contre un débordement d'entier si elle dépend d'une entrée externe.
- Compilation propre sous `-Wall -Wextra -Werror` et sanitizers, sans `#pragma` de suppression non justifié.
- Pointeurs vérifiés `NULL` après chaque allocation avant utilisation.
