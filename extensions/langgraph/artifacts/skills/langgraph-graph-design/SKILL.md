---
name: langgraph-graph-design
description: Concevoir un StateGraph LangGraph gouvernable — schéma d'état typé, edges conditionnels nommés, checkpoints auditables. À utiliser avant d'écrire ou d'importer un graphe LangGraph.
created: 2026-07-02
extension: langgraph
---

# Conception de graphe LangGraph gouvernable

Cette skill guide la conception d'un StateGraph qui restera auditable en
exécution et passera l'import sans correction.

## Quand l'utiliser

- Avant d'écrire un nouveau graphe LangGraph destiné à un projet Grimoire.
- Quand un graphe existant est illisible : état fourre-tout, branchements implicites, reprises hasardeuses.

## Règles de conception

1. **État typé et minimal** : chaque champ du state a un type, un écrivain identifié et une raison d'exister. Un état fourre-tout est l'anti-pattern « effet partiel oublié » en préparation.
2. **Edges conditionnels nommés** : une condition de branchement est une fonction nommée et testable, jamais une lambda anonyme.
3. **Checkpoints aux frontières de risque** : placer les checkpoints avant chaque action externe ou coûteuse, pas au hasard des nodes.
4. **Un graphe, une mission** : le graphe implémente le flow d'une mission bornée (ORC-10) ; s'il grossit au point de couvrir plusieurs objectifs, le découper.
5. **Sortie contractuelle** : le node terminal produit un résultat conforme au handoff packet, pas un état brut.

## Processus

1. Écrire le schéma d'état (champs, types, écrivains) avant tout node.
2. Dessiner la table nodes/edges/conditions ; nommer chaque condition.
3. Placer les checkpoints aux frontières de risque identifiées.
4. Implémenter, puis documenter avec le mission pack `langgraph-import-graph`.
