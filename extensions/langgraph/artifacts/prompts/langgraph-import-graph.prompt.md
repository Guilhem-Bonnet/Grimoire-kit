---
name: langgraph-import-graph
description: Mission pack — décrire un StateGraph LangGraph existant comme workflow Grimoire gouverné
created: 2026-07-02
extension: langgraph
---

# Importer un graphe LangGraph

Mission pack manuel : documenter un StateGraph LangGraph existant comme
workflow gouverné, prêt à être exécuté par le graph runner.

## Entrées attendues

- Le code ou la définition du StateGraph : schéma d'état, nodes, edges (dont conditionnels), configuration des checkpoints.
- Le contexte de mission : objectif, scope, critères de succès.

## Étapes

1. Extraire le schéma d'état : lister les champs, leurs types et qui les écrit. Un champ sans écrivain identifié est un défaut à corriger avant import.
2. Cartographier le graphe : nodes, edges, conditions de branchement. Toute condition implicite (lambda non nommée) doit être nommée et documentée.
3. Identifier les points de reprise : quels checkpoints existent, à quelle granularité, et ce qu'une reprise doit rejouer.
4. Produire le contrat de sortie ci-dessous et l'enregistrer comme artefact versionné du projet.

## Contrat de sortie

- La description du graphe : schéma d'état, table nodes/edges/conditions, points de reprise.
- Le mapping patterns : ORC-09 (état explicite et reprises), ORC-10 (flow déclaré).
- Les limites : ce que le graphe ne gère pas (erreurs externes, effets partiels) et qui doit le gérer à sa place.
