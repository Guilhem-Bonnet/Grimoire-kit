# Hybrid Parallelism Engine (HPE) — Orchestration Parallèle Adaptative

> **BM-58** — Moteur de parallélisme hybride qui combine exécution séquentielle, parallèle,
> et opportuniste selon la nature des tâches et la disponibilité des agents.
>
> **Problème résolu** : L'orchestration actuelle (BM-19 subagent, BM-11 boomerang) offre
> soit du parallèle pur soit du séquentiel pur. En réalité, la plupart des workflows combinent
> les deux : certaines tâches sont indépendantes (parallélisables), d'autres dépendent de
> résultats antérieurs, et certaines peuvent démarrer tôt avec des résultats partiels.
>
> **Principe** : Un DAG (Directed Acyclic Graph) de tâches avec dépendances explicites.
> Le moteur exécute en parallèle tout ce qui peut l'être, séquentialise ce qui doit l'être,
> et lance de façon opportuniste ce qui peut bénéficier de résultats partiels.
>
> **Implémentation** : Étend `subagent-orchestration.md` (BM-19) et utilise AMN (BM-55) pour
> le dispatch, ARG (BM-57) pour l'assignation, ELSS (BM-59) pour la coordination, et
> `agent-worker.py` pour l'exécution isolée.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                  HYBRID PARALLELISM ENGINE                    │
│                                                               │
│  ┌────────────────────────────────┐                          │
│  │        DAG BUILDER             │                          │
│  │  Parse workflow → build DAG    │                          │
│  │  Identify critical path        │                          │
│  └────────────┬───────────────────┘                          │
│               │                                               │
│  ┌────────────▼───────────────────┐                          │
│  │        SCHEDULER               │                          │
│  │  Ready queue → dispatch        │                          │
│  │  Modes: parallel | sequential  │                          │
│  │        | opportunistic         │                          │
│  └────────────┬───────────────────┘                          │
│               │                                               │
│  ┌────────────▼───────────────────┐                          │
│  │        EXECUTOR                │                          │
│  │  AMN dispatch → agent workers  │                          │
│  │  Monitor via ELSS events       │                          │
│  │  Collect results               │                          │
│  └────────────┬───────────────────┘                          │
│               │                                               │
│  ┌────────────▼───────────────────┐                          │
│  │        RESULT COLLECTOR        │                          │
│  │  Merge strategies              │                          │
│  │  Trust scoring                 │                          │
│  │  Checkpoint management         │                          │
│  └────────────────────────────────┘                          │
└──────────────────────────────────────────────────────────────┘
```

---

## DAG de Tâches — Définition

### Syntaxe YAML

```yaml
# Extension du step type 'orchestrate' (BM-19)
- step: "feature-implementation"
  type: hybrid-orchestrate
  description: "Implémentation complète d'une feature avec analyse, code, tests, docs"
  
  dag:
    tasks:
      # ── Couche 0 : Indépendantes (parallèle) ────────────
      - id: "analyze-requirements"
        agent: "analyst"
        task: "Analyser les requirements de la feature {description}"
        depends_on: []                     # aucune dépendance → exécutable immédiatement
        output_key: "requirements"
        priority: high
      
      - id: "analyze-codebase"
        agent: "dev"
        task: "Scanner le codebase pour les fichiers impactés par {description}"
        depends_on: []                     # indépendant → parallèle avec analyze-requirements
        output_key: "codebase_scan"
        priority: high
      
      # ── Couche 1 : Dépend des résultats de couche 0 ────
      - id: "design-architecture"
        agent: "architect"
        task: "Concevoir l'architecture pour {description} basé sur {requirements} et {codebase_scan}"
        depends_on: ["analyze-requirements", "analyze-codebase"]
        output_key: "architecture"
        priority: high
      
      - id: "write-tests"
        agent: "qa"
        task: "Écrire les tests d'acceptance basés sur {requirements}"
        depends_on: ["analyze-requirements"]    # besoin des requirements, PAS du codebase scan
        mode: opportunistic                      # peut démarrer dès qu'il a ses dépendances
        output_key: "test_specs"
        priority: medium
      
      # ── Couche 2 : Implémentation ──────────────────────
      - id: "implement"
        agent: "dev"
        task: "Implémenter selon {architecture}. Tests à respecter : {test_specs}"
        depends_on: ["design-architecture", "write-tests"]
        output_key: "implementation"
        priority: high
      
      # ── Couche 3 : Validation (parallèle) ──────────────
      - id: "run-tests"
        agent: "qa"
        task: "Exécuter les tests sur {implementation}. CC PASS obligatoire."
        depends_on: ["implement"]
        output_key: "test_results"
        priority: high
      
      - id: "security-review"
        agent: "qa"
        task: "Review sécurité OWASP sur {implementation}"
        depends_on: ["implement"]          # parallèle avec run-tests
        output_key: "security_report"
        priority: medium
      
      - id: "arch-review"
        agent: "architect"
        task: "Valider la conformité de {implementation} avec {architecture}"
        depends_on: ["implement"]          # parallèle avec tests et security
        output_key: "arch_review"
        mode: cross-validate               # CVTL automatique
        priority: medium
      
      # ── Couche 4 : Documentation ──────────────────────
      - id: "write-docs"
        agent: "tech-writer"
        task: "Documenter {implementation} avec {architecture} pour référence"
        depends_on: ["implement", "arch-review"]
        output_key: "documentation"
        priority: low
        mode: opportunistic                # peut commencer avec impl même si arch-review pas fini
    
    # Configuration globale
    config:
      max_parallel: 5                      # limite de workers simultanés
      checkpoint_after: ["design-architecture", "implement"]  # checkpoints intermédiaires
      on_failure: "pause-and-escalate"     # stop-all | continue-others | pause-and-escalate
      timeout_per_task_sec: 300
```

---

## Modes d'Exécution

### Parallel (défaut pour tâches sans dépendances)

```yaml
parallel_mode:
  description: "Exécution simultanée de toutes les tâches prêtes"
  condition: "depends_on = [] OU toutes les dépendances satisfaites"
  
  scheduling:
    1: "Identifier toutes les tâches avec dépendances satisfaites (ready queue)"
    2: "Trier par priorité (high > medium > low)"
    3: "Dispatcher via AMN (BM-55) aux agents disponibles"
    4: "Limiter au max_parallel de agent-worker.py (défaut: 5)"
    5: "Monitorer via ELSS events"
  
  example:
    # Couche 0 : ces deux tâches partent en même temps
    parallel_wave_0: ["analyze-requirements", "analyze-codebase"]
    # Couche 3 : ces trois tâches partent en même temps
    parallel_wave_3: ["run-tests", "security-review", "arch-review"]
```

### Sequential (pour dépendances strictes)

```yaml
sequential_mode:
  description: "Exécution ordonnée — chaque tâche attend ses dépendances"
  condition: "La tâche a des depends_on non satisfaits"
  
  scheduling:
    1: "Attendre que TOUTES les dépendances soient satisfaites"
    2: "Vérifier le résultat de chaque dépendance : success ? HUP ROUGE ?"
    3: "Si dépendance en échec → appliquer on_failure strategy"
    4: "Si dépendance HUP ROUGE → escalader via QEC AVANT dispatch"
    5: "Dispatcher quand tout est prêt"
```

### Opportunistic (pour démarrage anticipé)

```yaml
opportunistic_mode:
  description: "Lancer la tâche dès qu'un sous-ensemble de dépendances est disponible"
  condition: "mode: opportunistic dans la définition de tâche"
  
  scheduling:
    # La tâche a des dépendances [A, B] mais peut commencer avec [A] seul
    1: "Identifier les dépendances minimales (hard deps vs soft deps)"
    2: "Dès que les hard deps sont satisfaites → lancer avec données partielles"
    3: "Quand les soft deps arrivent → enrichir le contexte de l'agent en cours"
    4: "L'agent adapte son output avec les nouvelles données"
  
  rules:
    hard_dependency: "Données sans lesquelles la tâche n'a aucun sens"
    soft_dependency: "Données qui améliorent le résultat mais ne sont pas bloquantes"
    enrichment: |
      Quand une soft dep arrive pendant l'exécution :
      → Envoyer un message P2P à l'agent : "Contexte additionnel : {data}"
      → L'agent intègre sans recommencer depuis zéro
  
  example:
    task: "write-tests"
    hard_deps: ["analyze-requirements"]     # besoin des requirements
    soft_deps: ["analyze-codebase"]          # utile mais pas bloquant
    # → QA peut écrire les tests fonctionnels dès les requirements
    # → Quand le scan codebase arrive, QA ajuste les tests techniques
```

---

## Chemin Critique — Optimisation

```yaml
critical_path:
  description: "Le plus long chemin du DAG — détermine la durée minimale"
  
  calculation:
    method: "Longest path algorithm sur le DAG pondéré par estimated_duration"
    update: "Recalculé à chaque completion de tâche"
  
  optimization:
    # Prioriser les tâches sur le chemin critique
    priority_boost: "Tâches sur le critical path → priority += 1 niveau"
    
    # Assignation agents : meilleur agent pour le critical path
    agent_assignment: |
      Pour les tâches du critical path :
      → Utiliser ARG pour sélectionner l'agent avec le meilleur trust_score
      → Même si un agent moins optimal est plus disponible
    
    # Alertes
    alerts:
      - "Tâche du critical path en retard → alerte SOG"
      - "Tâche du critical path en HUP JAUNE → cross-validation immédiate"
      - "Tâche du critical path en HUP ROUGE → huddle d'urgence"
```

---

## Gestion des Échecs

```yaml
failure_handling:
  strategies:
    # stop-all : tout s'arrête (pour workflows critiques)
    stop-all:
      on_task_failure: "Envoyer cancel à tous les agents en cours"
      escalation: "SOG présente l'erreur à l'utilisateur"
    
    # continue-others : les tâches indépendantes continuent
    continue-others:
      on_task_failure: |
        1. Marquer la tâche comme failed
        2. Annuler les tâches qui en dépendent directement 
        3. Les tâches indépendantes continuent
        4. SOG agrège ce qui a réussi + signale ce qui a échoué
    
    # pause-and-escalate (défaut) : pause + escalade + reprise possible
    pause-and-escalate:
      on_task_failure: |
        1. Marquer la tâche comme failed
        2. Suspendre les tâches dépendantes (pas annuler)
        3. Les tâches indépendantes continuent
        4. SOG escalade à l'utilisateur :
           "La tâche {id} a échoué. Tâches impactées : {deps}.
            Options : [Retry] · [Skip + continuer] · [Annuler tout]"
        5. Si retry → re-dispatcher avec plus de contexte
        6. Si skip → marquer comme skip + débloquer les dépendances avec caveat
  
  # Retry intelligent
  retry_policy:
    max_retries: 2
    retry_with:
      - "Contexte additionnel (résultats partiels des autres tâches)"
      - "Agent différent si le premier a échoué (ARG fallback agent)"
      - "Huddle de clarification si HUP ROUGE persistant"
```

---

## Checkpoints et Reprise

```yaml
checkpoints:
  # Sauvegarder l'état du DAG à des points clés
  save:
    trigger: "Après chaque tâche dans config.checkpoint_after[]"
    content:
      dag_state:
        completed: ["{task_ids complétées}"]
        in_progress: ["{task_ids en cours}"]
        pending: ["{task_ids en attente}"]
        failed: ["{task_ids échouées}"]
      outputs: "{tous les output_keys disponibles}"
      timestamp: "{iso_datetime}"
    save_to: "_bmad-output/.hpe-checkpoint-{step_id}.yaml"
  
  # Reprendre depuis un checkpoint
  resume:
    command: "[HPE-RESUME checkpoint_id]"
    process:
      1: "Charger le checkpoint"
      2: "Skip les tâches completed"
      3: "Re-dispatcher les tâches in_progress (lost workers)"
      4: "Continuer les tâches pending avec les outputs déjà collectés"
      5: "Revoir les tâches failed → retry ou skip"
```

---

## Visualisation du DAG

L'utilisateur peut demander une vue du DAG :

```markdown
## Commandes HPE

- `[HPE-STATUS]` — État actuel du DAG : complétées, en cours, en attente
- `[HPE-CRITICAL]` — Chemin critique actuel avec estimations
- `[HPE-RESUME id]` — Reprendre depuis un checkpoint
- `[HPE-REPLAN]` — Recalculer le DAG après changement de priorités
```

### Exemple de visualisation status

```
📊 HPE Status — feature-implementation (6/8 tasks)

✅ analyze-requirements     (analyst/Mary)      [0.8s]
✅ analyze-codebase          (dev/Amelia)        [1.2s]
✅ design-architecture       (architect/Winston) [2.1s]  🛡️87
✅ write-tests               (qa/Quinn)          [1.5s]
🔄 implement                 (dev/Amelia)        [running 45s...]  ◀ CRITICAL PATH
⏳ run-tests                 (qa/Quinn)          [waiting: implement]
⏳ security-review           (qa/Quinn)          [waiting: implement]
⏳ arch-review               (architect/Winston) [waiting: implement]
⏳ write-docs                (tech-writer/Paige) [waiting: implement+arch-review]

Critical path: analyze → design → implement → run-tests
Estimated remaining: ~120s
Parallel workers: 1/5 active
```

---

## Intégration avec les Protocoles Existants

| Protocole | Utilisation dans HPE |
|-----------|---------------------|
| **Subagent (BM-19)** | HPE étend le `type: orchestrate` avec `type: hybrid-orchestrate` |
| **Boomerang (BM-11)** | Le mode `sequential` de HPE remplace le boomerang simple |
| **AMN (BM-55)** | Dispatch des tâches via le mesh + discovery |
| **ARG (BM-57)** | Sélection optimale des agents par tâche |
| **ELSS (BM-59)** | Monitoring temps réel + coordination |
| **HUP (BM-50)** | Chaque tâche du DAG applique HUP |
| **QEC (BM-51)** | Questions agrégées depuis toutes les tâches parallèles |
| **CVTL (BM-52)** | `mode: cross-validate` déclenche la validation croisée |
| **SHP (BM-56)** | Huddle déclenché automatiquement sur critical path JAUNE/ROUGE |
| **SOG (BM-53)** | HPE est un module interne du SOG |

---

## Intégration BMAD Trace

```
[timestamp] [HPE]            [HPE:build-dag]   tasks=8 | layers=5 | critical_path=4
[timestamp] [HPE]            [HPE:dispatch]    parallel_wave_0=[analyze-req,analyze-code] | workers=2/5
[timestamp] [HPE]            [HPE:complete]    task=analyze-requirements | duration=0.8s | status=success
[timestamp] [HPE]            [HPE:ready]       task=write-tests unlocked (opportunistic, 1/2 deps)
[timestamp] [HPE]            [HPE:dispatch]    task=design-architecture | agent=architect | critical_path=true
[timestamp] [HPE]            [HPE:checkpoint]  id=cp-impl | completed=4/8 | outputs=4
[timestamp] [HPE]            [HPE:failure]     task=implement | strategy=pause-and-escalate
[timestamp] [HPE]            [HPE:critical]    path_update: remaining=3 tasks | est=120s
```

---

## Référence Croisée

- Subagent Orchestration : [framework/workflows/subagent-orchestration.md](workflows/subagent-orchestration.md) (BM-19)
- Boomerang Orchestration : [framework/workflows/boomerang-orchestration.md](workflows/boomerang-orchestration.md) (BM-11)
- Agent Mesh Network : [framework/agent-mesh-network.md](agent-mesh-network.md) (BM-55)
- Agent Relationship Graph : [framework/agent-relationship-graph.md](agent-relationship-graph.md) (BM-57)
- Event Log : [framework/event-log-shared-state.md](event-log-shared-state.md) (BM-59)
- Agent Worker : [framework/tools/agent-worker.py](tools/agent-worker.py) — MAX_PARALLEL_WORKERS
- Orchestrator Gateway : [framework/orchestrator-gateway.md](orchestrator-gateway.md) (BM-53)

---

*BM-58 Hybrid Parallelism Engine | framework/hybrid-parallelism-engine.md*
