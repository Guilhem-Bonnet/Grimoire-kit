<!-- EXPERTISE: expertise-elixir — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code Elixir — OTP, supervision, pattern matching en tête de fonction, GenServer. À utiliser dès qu'une demande porte sur un `mix.exs` ou un fichier `.ex` — pas pour le Ruby, le Scala, ni pour un autre stack."
tools: ["read", "edit", "execute"]
---

# Elixir

Expertise activée sur détection d'un `mix.exs` ou d'un fichier `.ex` : la supervision OTP porte déjà une partie de la résilience, cette fiche couvre ce qui reste au design du code.

## Principes

- « Let it crash » sous supervision OTP : un processus supervisé qui redémarre est la stratégie de récupération, pas du code défensif qui essaie de tout anticiper — le `try/rescue` systématique autour de chaque appel est un signal d'alerte, pas une bonne pratique.
- Arbres de supervision explicites et nommés : chaque processus longue durée a un superviseur identifié et une stratégie de redémarrage (`:one_for_one`, `:rest_for_one`, etc.) choisie consciemment, pas par défaut sans réflexion.
- Pattern matching en tête de fonction plutôt que conditionnelles dans le corps — plusieurs clauses de fonction avec matching explicite valent mieux qu'un `case`/`if` imbriqué à l'intérieur d'une seule clause.
- Immutabilité comme modèle de données par défaut : toute transformation retourne une nouvelle structure, jamais de mutation en place.
- Changements d'état d'un `GenServer` toujours via passage de message (`handle_call`/`handle_cast`), jamais d'état mutable partagé entre processus par un autre mécanisme.
- Tests en ExUnit, y compris les doctests quand la documentation porte un exemple exécutable — un exemple de doc qui n'est pas vérifié dérive silencieusement du comportement réel.

## Garde-fou

Tout `Process.flag(:trap_exit, true)` ou `try/rescue` qui intercepte une exception avant qu'elle atteigne le superviseur exige confirmation avant modification : vérifier que le crash ne doit pas plutôt se propager pour déclencher le redémarrage prévu.

## Implémenter une feature

Lire le module cible et son arbre de supervision pour les conventions établies → identifier les processus et messages impactés → implémenter en pattern matching en tête de fonction, état du `GenServer` uniquement via message → écrire les tests ExUnit (et doctests si la fonction est documentée avec un exemple) → `mix test` (build + tests).

## Corriger un bug

Écrire un test ExUnit qui reproduit le bug → diagnostiquer via les logs de crash et le rapport du superviseur (le point de redémarrage indique souvent le vrai point de défaillance) → corriger au point exact sans ajouter de `try/rescue` qui masquerait un futur crash légitime → vérifier que le test du bug et la suite existante passent.

## Bug hunt

Vagues successives, vérification après chacune : tests (`mix test`) → style et bonnes pratiques (`mix credo --strict`) → discipline de typage (`mix dialyzer`) → formatage (`mix format --check-formatted`) → processus qui piège les exits et avale des crashs qui auraient dû remonter au superviseur → appel synchrone lent bloquant la boîte aux lettres d'un `GenServer` → croissance non bornée d'une boîte aux lettres quand un producteur est plus rapide que son consommateur.

## Checklist de revue

- Aucun `try/rescue` ou `trap_exit` qui masque un crash qui devrait remonter au superviseur.
- Arbre de supervision et stratégie de redémarrage explicites pour tout nouveau processus longue durée.
- Aucun appel synchrone lent dans un `handle_call` qui bloquerait la boîte aux lettres.
- État de `GenServer` modifié uniquement par message, jamais par état partagé mutable.
- `mix credo --strict`, `mix dialyzer` et `mix format --check-formatted` passent sans exception silencieuse.
