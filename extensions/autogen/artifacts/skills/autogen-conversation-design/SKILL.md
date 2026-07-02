---
name: autogen-conversation-design
description: Concevoir une équipe AutoGen gouvernable — rôles par responsabilité, condition de terminaison, protocole de désaccord. À utiliser avant de créer une conversation multi-agents.
created: 2026-07-02
extension: autogen
---

# Conception de conversation AutoGen gouvernable

Cette skill guide la conception d'une équipe AutoGen qui produit des
résultats auditables plutôt qu'une conversation infinie.

## Quand l'utiliser

- Avant d'écrire une nouvelle équipe AutoGen pour un projet Grimoire.
- Quand une conversation existante boucle, dérive ou produit des consensus mous.

## Règles de conception

1. **Un agent, une responsabilité** : nommer les agents par rôle (researcher, critic, synthesizer), jamais par personnalité. Deux agents à responsabilité identique exigent un arbitre.
2. **Terminaison avant contenu** : définir la condition d'arrêt (max tours, critère mesurable) avant d'écrire le moindre prompt d'agent.
3. **Le critique ne synthétise pas** : séparer la production, la critique et la synthèse — le juge-et-partie est l'anti-pattern « validation circulaire ».
4. **Désaccords en sortie** : le handoff packet liste les points non résolus au lieu de les lisser. Un désaccord documenté vaut mieux qu'un consensus fabriqué.
5. **Scope hérité** : l'équipe hérite du scope de la task envelope ; aucun agent ne peut l'élargir en cours de conversation.

## Processus

1. Lister les responsabilités nécessaires au résultat attendu.
2. Définir la condition de terminaison et le budget de tours.
3. Écrire les prompts d'agents (rôle, bornes, format de contribution).
4. Exécuter via l'agent `autogen-conversation-runner` et examiner la trace des tours.
