<p align="right"><a href="../../README.md">README</a> · <a href="../../docs">Docs</a></p>

# <img src="../../docs/assets/icons/team.svg" width="32" height="32" alt=""> Subagent Orchestration Protocol

> **BM-19** — Architecture native pour spawner des sous-agents en parallèle depuis un workflow Grimoire.
>
> Inspiré de Claude's tool_use, MetaGPT SOP, et Roo-Code Boomerang Tasks.
>
> **Principe** : Un agent orchestrateur décompose une tâche complexe en sous-tâches atomiques,
> les délègue à des agents spécialisés (en parallèle quand possible), puis agrège les résultats.

<img src="../../docs/assets/divider.svg" width="100%" alt="">


## <img src="../../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> Syntaxe dans les Workflows YAML

### Step type `orchestrate`

```yaml
- step: "analyse-codebase"
  type: orchestrate
  description: "Analyse parallèle sécurité + coverage depuis deux agents spécialisés"
  spawn:
    - agent: "dev"
      task: "Analyse le répertoire src/ pour les problèmes de sécurité (OWASP Top 10). Retourne une liste structurée : [{file, line, severity, description}]"
      context:
        - "_grimoire/_memory/shared-context.md"
        - "_grimoire-output/implementation-artifacts/architecture-*.md"
      output_key: "security_findings"

    - agent: "qa"
      task: "Analyse la couverture de tests dans src/. Identifie les fichiers sans tests et les branches non couvertes. Retourne : [{file, coverage_pct, missing_tests}]"
      context:
        - "_grimoire/_memory/shared-context.md"
      output_key: "coverage_findings"

  merge:
    strategy: "summarize"           # summarize | concat | first-wins | vote
    merged_output_key: "analysis_report"
    save_to: "_grimoire-output/implementation-artifacts/analysis-report-{date}.md"
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> Stratégies de Merge

| Stratégie | Description | Cas d'usage |
|---|---|---|
| `summarize` | L'orchestrateur synthétise tous les findings en un rapport cohérent | Analyses multiples — résumé exécutif |
| `concat` | Concaténation brute des outputs, section par section | Documentation, listes exhaustives |
| `first-wins` | Premier sous-agent à terminer décide, les autres valident ou overrident | Decisions rapides avec validation |
| `vote` | Chaque agent vote pour la meilleure option — majorité gagne | Choix techniques controversés |
| `structured` | Chaque output va dans une section prédéfinie du résultat final | Rapports multi-sections |
| `cross-validate` | Un agent produit, un second valide avec Trust Score (BM-52 CVTL) | Outputs critiques, ADRs, décisions irréversibles |

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/team.svg" width="28" height="28" alt=""> Fallback pour LLMs sans support sous-agents natif

Si le LLM ne supporte pas le spawn réel de sous-agents :

```yaml
  fallback:
    mode: "sequential"             # Exécute les tâches séquentiellement, même agent
    note: "Sequential fallback — parallel execution not available in this runtime"
```

L'orchestrateur exécute les tâches une par une dans le même contexte, en simulant le changement de persona entre chaque tâche.

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> Patterns Recommandés

### Pattern 1 — Analyse Parallèle

```yaml
# Contexte : review d'un gros codebase — lancer Dev + QA + Architect en parallèle
type: orchestrate
spawn:
  - agent: dev
    task: "Review code quality dans src/ — retourne top 5 problèmes avec file:line"
    output_key: code_quality
  - agent: qa
    task: "Liste les tests manquants dans src/ — retourne [{file, reason}]"
    output_key: missing_tests
  - agent: architect
    task: "Identifie les violations de l'architecture dans src/ — retourne [{file, violation, fix}]"
    output_key: arch_violations
merge:
  strategy: summarize
  save_to: "_grimoire-output/implementation-artifacts/full-review-{date}.md"
```

### Pattern 2 — Validation Croisée

```yaml
# Contexte : valider une décision depuis plusieurs angles
type: orchestrate
question: "Doit-on migrer de REST vers GraphQL pour l'API ?"
spawn:
  - agent: architect
    task: "Analyse technique : avantages/inconvénients migration REST→GraphQL pour {project}. Décision recommandée avec justification."
    output_key: tech_analysis
  - agent: pm
    task: "Impact produit : la migration REST→GraphQL affecte-t-elle les features Q2 ? Délai estimé vs valeur."
    output_key: product_impact
merge:
  strategy: vote
  save_to: "_grimoire-output/planning-artifacts/adr-rest-vs-graphql.md"
```

### Pattern 3 — Boomerang Tasks (hiérarchique)

```yaml
# Contexte : SM décompose, Dev implémente chaque story, QA valide, SM réagrège
type: orchestrate
mode: sequential-hierarchical      # chaque sous-tâche peut spawner ses propres sous-tâches
spawn:
  - agent: sm
    task: "Décompose la feature 'authentification' en 3 stories atomiques avec ACs. Retourne YAML stories[]."
    output_key: stories

# → SM retourne les stories → pour chaque story, Dev est spawné
  - agent: dev
    task: "Implémente la story {stories[0]}. CC PASS obligatoire. Retourne : {files_changed, tests_added, cc_result}"
    output_key: story_1_impl
    depends_on: stories

  - agent: qa
    task: "Valide story_1 : vérifie les ACs, coverage, edge cases. Retourne : {acs_covered[], gaps[]}"
    output_key: story_1_qa
    depends_on: story_1_impl

merge:
  strategy: structured
  template: "framework/workflows/boomerang-report.tpl.md"
```

### Pattern 4 — Cross-Validation avec Trust Score (BM-52)

```yaml
# Contexte : un output critique doit être vérifié par un second agent
type: orchestrate
spawn:
  - agent: dev
    task: "Implémenter le module auth JWT selon ADR-042. CC PASS obligatoire."
    output_key: implementation
  - agent: architect
    task: "Valider l'implémentation {implementation}. Produire un cross_validation_report avec trust_score."
    output_key: validation
    depends_on: implementation
merge:
  strategy: cross-validate         # NOUVELLE STRATÉGIE (BM-52)
  primary_agent: dev
  validator_agent: architect
  trust_threshold: 70              # score minimum pour accepter
  on_below_threshold: escalate_to_user
  save_to: "_grimoire-output/implementation-artifacts/auth-validated.md"
```

> Référence complète : `framework/cross-validation-trust.md` (BM-52)

### Pattern 5 — Orchestration avec HUP + QEC (BM-50/51)

```yaml
# Contexte : tâche incertaine où les agents peuvent avoir besoin de clarifications
type: orchestrate
spawn:
  - agent: dev
    task: "Implémenter la feature {description}. HUP actif : si confiance ROUGE, produire uncertainty_report au lieu d'halluciner."
    output_key: implementation
    # HUP + QEC activés automatiquement sur chaque sub-agent

on_escalation:
  strategy: batch                  # batch | immediate
  batch_trigger: end_of_spawn      # quand présenter le lot
  auto_resolve: true               # tenter l'auto-résolution par contexte
  auto_resolve_sources:
    - shared-context.md
    - decisions-log.md
    - _grimoire-output/.qa-history.yaml
  max_questions_per_lot: 7         # chunking 7±2
```

> Référence complète : `framework/honest-uncertainty-protocol.md` (BM-50) · `framework/question-escalation-chain.md` (BM-51)

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Règles Obligatoires

1. **Chaque sous-agent reçoit un contexte minimal** — uniquement les fichiers nécessaires à SA tâche
2. **Chaque sous-agent retourne du JSON ou Markdown structuré** — jamais du prose non-parseable
3. **L'orchestrateur ne modifie JAMAIS les fichiers pendant le spawn** — il attend les résultats
4. **Si un sous-agent échoue**, l'orchestrateur le signale, n'annule pas les autres, et agrège ce qui a réussi
5. **Le merge produit toujours un artefact persisté** — jamais un résultat éphémère
6. **HUP actif sur chaque sub-agent** — confiance ROUGE = uncertainty_report, pas d'hallucination (BM-50)
7. **Escalations QEC agrégées** — les questions des sub-agents sont collectées et présentées en lot (BM-51)
8. **Dispatch via AMN** — les tâches sont dispatchées via le mesh (BM-55), avec discovery et load balancing
9. **Assignation via ARG** — l'agent optimal est sélectionné par le graphe relationnel (BM-57)
10. **Events ELSS émis** — chaque spawn, completion, et failure émet un événement dans l'event bus (BM-59)
11. **Tool Resolution pré-step** — l'orchestrateur (v1.2+) appelle `tool-resolver.py resolve` automatiquement avant chaque step pour identifier et vérifier les outils nécessaires. Les outils résolus sont injectés dans le contexte du sub-agent via `resolved_tools`.

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/boomerang.svg" width="28" height="28" alt=""> Extension — Hybrid Orchestrate (BM-58)

Pour les workflows complexes avec dépendances mixtes, utiliser `type: hybrid-orchestrate` :

```yaml
- step: "feature-complete"
  type: hybrid-orchestrate
  dag:
    tasks:
      - id: "analyze"
        agent: "analyst"
        depends_on: []
        output_key: "analysis"
      - id: "design"
        agent: "architect"
        depends_on: ["analyze"]
        output_key: "architecture"
      - id: "implement"
        agent: "dev"
        depends_on: ["design"]
        output_key: "code"
      - id: "test"
        agent: "qa"
        depends_on: ["implement"]
        output_key: "tests"
        mode: opportunistic  # peut commencer les test specs dès analyze
    config:
      max_parallel: 5
      on_failure: pause-and-escalate
```

Le HPE construit le DAG, identifie le chemin critique, et exécute en parallèle
tout ce qui peut l'être. Voir `framework/hybrid-parallelism-engine.md` (BM-58) pour les détails.


*BM-19 Subagent Orchestration Protocol | framework/workflows/subagent-orchestration.md*
