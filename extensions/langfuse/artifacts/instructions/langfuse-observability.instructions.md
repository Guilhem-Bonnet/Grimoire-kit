---
applyTo: "**"
description: Conventions d'observabilité Langfuse pour la télémétrie agentique
created: 2026-07-02
extension: langfuse
---

# Conventions d'observabilité Langfuse

Ces conventions s'appliquent quand le projet exporte sa télémétrie vers
Langfuse (patterns QUA-02, QUA-08, QUA-10 du catalogue).

## Règles

1. **La source de vérité reste locale** : les flux `events.jsonl` du runtime font foi ; Langfuse est une projection d'analyse, jamais l'inverse.
2. **Corrélation** : chaque trace exportée porte `mission_id` et `task_id` quand ils existent dans l'événement source — sans eux, une trace n'est pas auditable.
3. **Pas de contenu sensible** : n'exporter que les métadonnées d'exécution (événement, outil, durée, statut). Les prompts et sorties complètes restent locaux sauf décision explicite du projet.
4. **Best-effort** : l'export ne doit jamais bloquer ni ralentir le runtime. Si `LANGFUSE_HOST` n'est pas configuré, le hook journalise localement et sort en 0.
5. **Scores et evals** : les verdicts de qualité (evidence packs, gates) peuvent être poussés comme scores Langfuse, mais le verdict d'acceptation reste celui du runtime.

## Configuration attendue

Variables d'environnement : `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`. Voir la skill `langfuse-tracing-setup` pour
l'installation guidée.
