---
name: crewai-import-crew
description: Mission pack — importer un flow CrewAI existant comme Recipe Grimoire gouvernée
created: 2026-07-01
extension: crewai
---

# Importer un crew CrewAI

Mission pack manuel : convertir un flow CrewAI existant en Recipe Grimoire
gouvernée, prête à être exécutée par le crew runner.

## Entrées attendues

- La définition du flow CrewAI : dict Python ou YAML avec `name`, `description`, `tasks[]` (id, name, agent, depends_on, expected_output, output_schema) et `output_schema` global.
- Le contexte de mission : objectif, scope, critères de succès.

## Étapes

1. Valider la définition : chaque task doit déclarer un `output_schema`. Lister les tasks non conformes et demander les schémas manquants avant de continuer.
2. Importer via `CrewAIAdapter` (`grimoire.runtime.crewai_adapter`) : les tasks deviennent des RecipeSteps, les agents deviennent des rôles, `depends_on` est tracé dans la description des steps.
3. Contrôler le rapport d'import (`CrewAIImportReport`) : signaler tout avertissement à l'utilisateur.
4. Enregistrer la Recipe et produire le contrat de sortie ci-dessous.

## Contrat de sortie

- La Recipe générée (identifiant, steps, rôles, gates de vérification).
- Le rapport d'import avec les avertissements éventuels.
- La liste des patterns socle mobilisés : ORC-01 (orchestration), ORC-02 (task envelope), ORC-03 (handoff packet).
- Les limites : le flow importé ne peut pas fermer de tâche sans vérification (guardrail NEEDS_VERIFICATION).
