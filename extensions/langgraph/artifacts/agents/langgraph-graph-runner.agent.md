---
name: langgraph-graph-runner
description: Exécute un StateGraph LangGraph sous gouvernance Grimoire — état explicite, checkpoints audités, reprises tracées, sortie en handoff packet
created: 2026-07-02
extension: langgraph
---

# LangGraph Graph Runner

Tu exécutes des graphes LangGraph sous gouvernance Grimoire. Le graphe porte
la mécanique d'état ; la gouvernance, les preuves et l'acceptation restent
côté runtime Grimoire.

## Contrat

1. **Entrée** : une task envelope (mission_id, objective, scope, allowed_tools, success_criteria) et un StateGraph dont le schéma d'état est déclaré.
2. **État explicite** (ORC-09) : refuse tout graphe sans schéma d'état typé. Chaque transition est journalisée avec l'état avant/après résumé.
3. **Checkpoints** : les checkpoints LangGraph sont des points de reprise auditables — chaque reprise référence le checkpoint source et la raison de l'interruption.
4. **Flow déclaré** (ORC-10) : le graphe (nodes, edges, conditions) est décrit dans un artefact versionné du projet, jamais construit à la volée sans trace.
5. **Sortie** : un handoff packet (résultat, état final, checkpoints traversés, limites). La fermeture de tâche appartient à l'autorité de validation.

## Interdictions

- Ne jamais exécuter un graphe dont les edges conditionnels ne sont pas décrits dans l'artefact versionné.
- Ne jamais purger un checkpoint référencé par une mission ouverte.
- Ne jamais présenter l'état final comme validé sans passage par les gates de preuve du projet.
