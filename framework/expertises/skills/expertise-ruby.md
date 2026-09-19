<!-- EXPERTISE: expertise-ruby — Adaptez à votre projet. -->
---
description: "Écrire ou faire évoluer du code Ruby — convention over configuration, RSpec, Rubocop, ActiveRecord. À utiliser dès qu'une demande porte sur un `Gemfile` ou un `.ruby-version` — pas pour le Scala, l'Elixir, ni pour un autre stack."
tools: ["read", "edit", "execute"]
---

# Ruby

Expertise activée sur détection d'un `Gemfile` ou d'un `.ruby-version` : la convention porte déjà une partie des décisions, cette fiche couvre ce qui reste à la discipline du développeur.

## Principes

- Convention over configuration : respecter la structure et les noms attendus par le framework (Rails ou équivalent) plutôt que réinventer un layout — un contrôleur, un modèle ou un service qui s'écarte de la convention sans raison documentée est un signal d'alerte.
- Tests en RSpec avec `describe`/`context`/`it`, un seul comportement vérifié par exemple (`it`) — un `it` qui vérifie trois choses est un test à scinder.
- `Rubocop` fait partie du gate, pas une option : `bundle exec rubocop` doit passer avant toute fusion, pour la cohérence de style sur l'ensemble de la base.
- `# frozen_string_literal: true` en tête de fichier dès qu'aucune mutation de chaîne n'est voulue — la mutation implicite d'une constante partagée est un bug classique et silencieux.
- Pas de monkey-patching de classes du cœur (`String`, `Array`, `Hash`, etc.) hors d'un cas clairement scopé et documenté (raison, portée, alternative écartée) — le patch global non documenté casse ailleurs sans message d'erreur clair.
- Vigilance N+1 sur tout ORM de type ActiveRecord : `includes`/`eager_load` dès qu'une boucle déclenche une requête par ligne, jamais de lazy-loading en boucle non justifié.

## Garde-fou

Toute migration de base de données irréversible (`drop_table`, suppression de colonne, changement de type destructif) exige confirmation avant exécution : vérifier qu'un rollback existe ou qu'une sauvegarde a été prise.

## Implémenter une feature

Lire le modèle et le contrôleur concernés pour les conventions déjà en place → identifier les associations et validations impactées → implémenter en restant dans la convention du framework → écrire les specs RSpec (`describe`/`context`/`it`, un comportement par exemple) → `bundle exec rspec` puis `bundle exec rubocop` pour vérifier.

## Corriger un bug

Écrire une spec qui reproduit le bug → diagnostiquer via le stacktrace et les logs (attention au `NoMethodError` qui cache un `nil` implicite plus haut dans la chaîne) → corriger au point exact sans élargir la portée → vérifier que la spec du bug et la suite existante passent, Rubocop propre.

## Bug hunt

Vagues successives, vérification après chacune : tests (`bundle exec rspec`) → style (`bundle exec rubocop`) → sécurité (`bundle exec brakeman`) → mass-assignment non contrôlé (paramètres non explicitement permis dans les contrôleurs) → I/O bloquant dans le cycle de requête (appel réseau ou fichier synchrone qui devrait être un job en arrière-plan).

## Checklist de revue

- Aucun `nil` implicite non géré sur un chemin qui remonte jusqu'à la vue ou l'API.
- Paramètres explicitement permis avant tout assignment de masse, jamais de `params` bruts.
- Pas de N+1 introduit : les associations chargées en boucle passent par `includes`/`eager_load`.
- `bundle exec rubocop` et `bundle exec brakeman` passent sans exception silencieuse.
- Tout traitement bloquant long est déporté vers un job en arrière-plan, pas exécuté dans la requête.
