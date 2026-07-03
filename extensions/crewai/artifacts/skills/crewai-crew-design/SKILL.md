---
name: crewai-crew-design
description: Concevoir un crew CrewAI gouvernable — structure des tasks, schémas de sortie, mapping vers les patterns socle Grimoire. À utiliser avant tout import de flow CrewAI.
created: 2026-07-01
extension: crewai
---

# Conception de crew CrewAI gouvernable

Cette skill guide la conception d'un crew CrewAI qui passera l'import Recipe
sans avertissement et restera auditable en exécution.

## Quand l'utiliser

- Avant d'écrire un nouveau flow CrewAI destiné à un projet Grimoire.
- Quand l'import d'un flow existant remonte des avertissements (`CrewAIImportReport`).

## Règles de conception

1. **Une task, un schéma** : chaque task déclare `output_schema`. Sans schéma, la sortie n'est pas vérifiable et l'import est refusé.
2. **Dépendances explicites** : utiliser `depends_on` plutôt que d'encoder l'ordre dans les descriptions. Les dépendances sont tracées dans les RecipeSteps.
3. **Agents = rôles bornés** : un agent CrewAI correspond à un rôle de RecipeStep, pas à une identité libre. Nommer les agents par responsabilité (researcher, writer, reviewer).
4. **Pas de fermeture autonome** : le flow produit des résultats à vérifier, jamais des tâches fermées. Le guardrail NEEDS_VERIFICATION est appliqué à l'import.
5. **Mapping patterns** : un crew bien conçu matérialise ORC-01 (orchestrateur et subagents), ORC-02 (task envelope) et ORC-03 (handoff packet). Si le flow contourne l'un des trois, le redécouper.

## Processus

1. Lister les résultats attendus du crew et leurs schémas de sortie.
2. Découper en tasks avec `depends_on`, un agent par responsabilité.
3. Écrire le dict de flow (voir le format dans la docstring de `grimoire.runtime.crewai_adapter`).
4. Importer avec le mission pack `crewai-import-crew` et corriger les avertissements.
