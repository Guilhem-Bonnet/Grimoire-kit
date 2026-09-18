<!-- EXPERTISE: expertise-rust — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code Rust — ownership/borrowing, gestion d'erreurs par Result, clippy pedantic, cargo workspace. À utiliser dès qu'une demande porte sur un fichier .rs ou un Cargo.toml — pas pour le C/C++ ni pour l'infrastructure conteneurs/K8s."
tools: ["read", "edit", "execute"]
---

# Rust

Expertise systèmes activée sur détection d'un `Cargo.toml` ou d'un fichier `.rs` : le compilateur porte déjà une partie de la rigueur, cette fiche couvre ce qu'il ne peut pas vérifier lui-même.

## Principes

- L'ownership et les lifetimes sont le travail du compilateur, pas du relecteur — si `cargo build` passe, la question n'est plus "est-ce mémoire-sûr" mais "est-ce le bon design" (pas de `.clone()` réflexe pour contourner le borrow checker).
- Tout bloc `unsafe` porte un commentaire `// SAFETY:` juste au-dessus, expliquant l'invariant précis qui rend le bloc correct — un `unsafe` sans justification écrite est refusé en revue.
- `clippy` en mode pedantic fait partie du gate, pas une option : `cargo clippy --all-targets --all-features -- -D warnings`.
- Gestion d'erreurs par `Result<T, E>` dans tout code de bibliothèque ; `unwrap()`/`expect()` réservés aux tests et aux binaires, et seulement avec un commentaire justifiant pourquoi l'échec est impossible à cet endroit.
- Traits et composition plutôt qu'héritage simulé ; abstractions à coût nul (génériques, itérateurs) plutôt que `dyn Trait` par défaut — le dynamique se justifie, il ne se suppose pas.
- `Rc<RefCell<>>` répété dans une base de code est un signal d'alerte de design (couplage implicite, emprunts runtime), pas un outil à utiliser par confort.

## Garde-fou

Tout `unsafe` touchant à des pointeurs bruts (déréférencement, `transmute`, FFI direct, arithmétique de pointeur) exige confirmation avant modification : l'invariant de sécurité doit être relu et reformulé, pas seulement recopié.

## Implémenter une feature

Lire le module cible et son `mod.rs`/`lib.rs` pour les conventions établies → identifier les types et traits impactés → implémenter en respectant l'ownership (pas de clone pour éviter un design correct) → écrire les tests dans le même fichier (`#[cfg(test)]`) ou dans `tests/` pour l'intégration → `cc-verify.sh --stack rust` (build + clippy + test).

## Corriger un bug

Écrire un test qui reproduit le bug (unitaire ou `#[test]` d'intégration) → diagnostiquer via le message du compilateur ou `RUST_BACKTRACE=1` → corriger au point exact, sans introduire de `unsafe` pour contourner le problème → vérifier que le test du bug et la suite existante passent, clippy propre.

## Bug hunt

Vagues successives, CC VERIFY après chacune : lints (`cargo clippy --all-targets -- -D warnings`) → tests (`cargo test --all-features`) → détection d'undefined behavior sous `unsafe` (`cargo +nightly miri test`) → chaîne d'approvisionnement (`cargo audit`, `cargo deny check`) → formatage (`cargo fmt --check`) → appels bloquants dans du code async tokio sans `spawn_blocking` (`grep -rn "std::fs::\|std::thread::sleep" src/ | grep -B2 async` en repérage manuel) → `.clone()` en excès sur des chemins chauds.

## Checklist de revue

- Chaque `unsafe` a son commentaire `// SAFETY:` et l'invariant tient réellement.
- Pas de `unwrap()`/`expect()` non justifié dans le code de bibliothèque.
- Les erreurs sont typées (`thiserror`/enum d'erreur), pas des `String` ou des `Box<dyn Error>` génériques en frontière d'API publique.
- `cargo clippy --all-targets -- -D warnings` et `cargo fmt --check` passent sans exception silencieuse (`#[allow(...)]` non justifié).
- Aucun appel bloquant dans une tâche async sans `spawn_blocking` ou équivalent.
