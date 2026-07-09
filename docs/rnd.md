# R&D expérimental

Ces features fonctionnent et sont testées, mais leur surface est **exploratoire** :
API et formats peuvent évoluer sans garantie de compatibilité. Le cœur mûr du kit
(standard gouverné, mémoire, cockpit, MCP) est documenté dans le
[README](../README.md) et les guides.

## Features expérimentales

| Feature | Description |
|---|---|
| **Session Branching** | Explorer plusieurs approches en parallèle — comme des branches Git, mais pour les sessions d'agents. Diff, merge, cherry-pick |
| **Agent Darwinism** | Sélection naturelle des agents : fitness multi-dimensionnelle, évolution par générations, leaderboard, hybridation |
| **Stigmergy** *(promue beta)* | Coordination indirecte par phéromones : émission, détection, renfort, évaporation. Désormais en canal **beta** — CLI `grimoire stigmergy`, hooks opt-in, métriques d'usage (`grimoire features`). |
| **Dream Mode** | Consolidation hors-session : croise mémoire, trace, décisions et failure museum pour produire des insights émergents |
| **R&D Engine v2.1** | Boucle d'innovation : bandit ε-greedy à reward closed-loop, prototypage automatique, seed memory, gap-analysis |
| **Adversarial Consensus** | Quorum à 3 votants + 1 avocat du diable pour les décisions critiques |
| **Anti-Fragile Score** | Mesure la résilience adaptative (recovery, learning velocity, signal trend) |
| **Reasoning Stream** | Flux structuré : HYPOTHESIS, DOUBT, ASSUMPTION, ALTERNATIVE |
| **Cross-Project Migration** | Exporte/importe learnings, rules, DNA, agents entre projets |
| **Digital Twin** | Jumeau numérique : snapshot, simulation d'impact, scénarios "what if" |
| **Quantum Branch** | Timelines parallèles : fork, compare, merge de configurations alternatives |
| **Time-Travel** | Archéologie temporelle : checkpoints, replay, restore, bisect |
| **CRISPR** | Édition chirurgicale de workflows : scan, splice, excise, transplant |
| **Decision Log** | Journal de décisions architecturales hash-chaîné (sha256, `prev_hash`) avec vérification d'intégrité |
| **Mirror Agent** | Neurones miroirs : observation et transfert de patterns inter-agents |
| **Sensory Buffer** | Mémoire sensorielle court terme à décroissance exponentielle |
| **Self-Improvement Loop** | Analyse les patterns d'échec pour améliorer le framework automatiquement |
| **Context Budget Guard** | Mesure le budget LLM consommé par chaque agent |
| **Harmony Check** | Score d'harmonie architecturale et détection de dissonances |
| **Dashboard** | Santé, entropie Shannon, Pareto Gini, activité git — en un coup d'œil |

## Statut et gouvernance

- Chaque feature expérimentale est marquée par l'icône *flask* dans la documentation.
- Les outils correspondants vivent dans `framework/tools/` et sont couverts par la
  suite de tests, mais restent hors du contrat de stabilité SemVer du SDK.
- Une feature expérimentale est promue dans le cœur quand son usage est récurrent,
  son API stabilisée, et sa valeur démontrée (voir la politique dans
  [CONTRIBUTING.md](../CONTRIBUTING.md)).
