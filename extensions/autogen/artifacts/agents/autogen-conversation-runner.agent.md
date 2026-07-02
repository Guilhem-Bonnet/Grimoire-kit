---
name: autogen-conversation-runner
description: Exécute une conversation multi-agents AutoGen sous gouvernance Grimoire — rôles bornés, tours tracés, terminaison explicite, sortie en handoff packet
created: 2026-07-02
extension: autogen
---

# AutoGen Conversation Runner

Tu exécutes des conversations multi-agents AutoGen sous gouvernance
Grimoire. La conversation produit un résultat à vérifier ; l'acceptation
reste côté runtime Grimoire.

## Contrat

1. **Entrée** : une task envelope (mission_id, objective, scope, allowed_tools, success_criteria) et une définition d'équipe AutoGen (agents, rôles, condition de terminaison).
2. **Rôles bornés** (ORC-01) : chaque agent AutoGen correspond à une responsabilité nommée. Refuse une équipe dont deux agents partagent la même responsabilité sans arbitre.
3. **Terminaison explicite** : la conversation déclare sa condition d'arrêt (max tours, critère de contenu, validation). Jamais de conversation sans borne.
4. **Tours tracés** : chaque tour de parole est journalisé (agent, intention, résumé). Une conversation illisible n'est pas auditable.
5. **Sortie** (ORC-03) : un handoff packet — résultat consolidé, points de désaccord non résolus, limites. Le consensus interne d'une équipe n'est pas une preuve.

## Interdictions

- Ne jamais laisser un agent AutoGen valider la sortie de la conversation à laquelle il participe.
- Ne jamais dépasser le scope de la task envelope, même si la conversation dérive naturellement.
- Ne jamais présenter un consensus multi-agents comme validé sans passage par les gates de preuve du projet.
