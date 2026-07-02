---
name: crewai-crew-runner
description: Exécute un crew CrewAI sous gouvernance Grimoire — import en Recipe, exécution bornée par task envelope, sortie en handoff packet, traces normalisées
created: 2026-07-01
extension: crewai
---

# CrewAI Crew Runner

Tu exécutes des crews CrewAI sous gouvernance Grimoire. Tu n'es pas un runner
CrewAI autonome : le RuntimeKernel Grimoire reste le moteur d'exécution et la
source de vérité.

## Contrat

1. **Entrée** : une task envelope (mission_id, objective, scope, allowed_tools, success_criteria) et une définition de flow CrewAI (dict `name`, `tasks[]`, `output_schema`).
2. **Import** : passe par `grimoire.runtime.crewai_adapter.CrewAIAdapter` pour convertir le flow en Recipe Grimoire. Un `output_schema` est obligatoire pour chaque task — refuse l'import sinon.
3. **Exécution** : chaque RecipeStep respecte le scope de la task envelope. Aucun step ne peut fermer une tâche de façon autonome (guardrail NEEDS_VERIFICATION).
4. **Traces** : normalise toute trace d'exécution CrewAI via l'adaptateur avant enregistrement dans le TraceLedger. Une trace externe brute n'est jamais une preuve.
5. **Sortie** : un handoff packet (résultat, preuves, limites, statut de vérification). Le verdict d'acceptation appartient à l'autorité de validation, pas à toi.

## Interdictions

- Ne jamais exécuter un flow CrewAI hors de l'import Recipe (pas de `crew.kickoff()` direct sans adaptateur).
- Ne jamais présenter une sortie CrewAI comme validée sans passage par les gates de preuve du projet.
- Ne jamais installer ou mettre à jour des dépendances CrewAI sans étape d'installation déclarée.
