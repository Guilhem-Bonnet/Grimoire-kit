# Smart Orchestrator Gateway (SOG) — Point d'Entrée Unique Utilisateur

> **BM-53** — Protocole d'orchestration intelligente : un seul agent face à l'utilisateur,
> tous les autres sont des sub-agents silencieux.
>
> **Problème résolu** : L'utilisateur perd du temps à router manuellement entre agents,
> reçoit des outputs incohérents de sources multiples, et doit gérer lui-même les
> contradictions et les zones d'ombre.
>
> **Principe** : L'orchestrateur est le seul interlocuteur. Il comprend, clarifie, enrichit,
> dispatch, agrège, et présente. L'utilisateur ne voit jamais la complexité interne.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     UTILISATEUR (Guilhem)                        │
│                          │                                       │
│                          ▼                                       │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              SMART ORCHESTRATOR GATEWAY                  │    │
│  │                                                          │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │    │
│  │  │ Intention │  │ Clarify  │  │  Prompt  │  │ Route  │ │    │
│  │  │ Analyzer │→ │  Engine  │→ │ Enricher │→ │ Engine │ │    │
│  │  └──────────┘  └──────────┘  └──────────┘  └───┬────┘ │    │
│  │                                                  │      │    │
│  │  ┌──────────┐  ┌──────────┐                     │      │    │
│  │  │ Question │  │ Result   │←────────────────────┘      │    │
│  │  │  Buffer  │  │Aggregator│                             │    │
│  │  │  (QEC)   │  └────┬─────┘                             │    │
│  │  └──────────┘       │                                   │    │
│  └─────────────────────┼───────────────────────────────────┘    │
│                         │                                        │
│                         ▼                                        │
│            Résultat cohérent + Trust Score                       │
└──────────────────────────────────────────────────────────────────┘
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
   ┌─────────┐     ┌──────────┐      ┌──────────┐
   │  Agent A │     │ Agent B  │      │ Agent C  │
   │(invisible│     │(invisible│      │(invisible│
   │ à user)  │     │ à user)  │      │ à user)  │
   └─────────┘     └──────────┘      └──────────┘
```

---

## Modules de l'Orchestrateur

### 1. Intention Analyzer — Comprendre AVANT d'agir

Avant tout dispatch, l'orchestrateur analyse la demande :

```yaml
intention_analysis:
  # Extraction
  raw_input: "{message utilisateur}"
  
  parsed:
    primary_intent: "ce que l'utilisateur veut fondamentalement"
    secondary_intents: ["intentions implicites détectées"]
    domain: technical | business | creative | process | mixed
    complexity: simple | moderate | complex | multi-step
    urgency: immediate | normal | can_wait
  
  # Détection de zones d'ombre
  shadow_zones:
    detected: true | false
    items:
      - zone: "description de l'ambiguïté"
        impact: "ce qui ne peut pas être fait sans clarification"
        resolvable_by_context: true | false
        suggested_clarification: "question à poser si non résolvable"
  
  # Agents pertinents
  agent_routing:
    primary: "{agent le plus pertinent}"
    supporting: ["{agents complémentaires}"]
    reason: "justification du choix"
```

### Règles d'analyse

1. **Toujours extraire l'intention primaire** — même si la demande est confuse
2. **Détecter les zones d'ombre PROACTIVEMENT** — ne pas attendre que l'agent bloque
3. **Ne pas dispatcher tant qu'il y a des zones d'ombre critiques** → clarifier d'abord
4. **Zones d'ombre résolvables par contexte** → résoudre silencieusement sans déranger l'utilisateur

---

### 2. Clarify Engine — Dialogue de Clarification Proactif

Quand des zones d'ombre sont détectées et non résolvables par contexte :

```yaml
clarification_protocol:
  # Règle : Ne pas bombarder de questions
  max_questions_per_round: 3
  
  # Règle : Proposer des options quand possible
  prefer_options_over_open_questions: true
  
  # Format
  format: |
    Je comprends que tu veux {primary_intent}.
    
    Pour être sûr de bien faire, {count} points à clarifier :
    
    1. {question_1}
       → Options : A) {option_a} · B) {option_b} · C) Autre
    
    2. {question_2}
       → Options : A) {option_a} · B) {option_b}
    
    Le reste est clair pour moi, je peux avancer sur {ce qui est clair} 
    pendant que tu réfléchis si tu veux.
  
  # Parallélisation
  parallel_execution:
    enabled: true
    description: |
      Si une partie de la tâche est claire et indépendante des zones d'ombre,
      l'orchestrateur peut la lancer en parallèle pendant qu'il attend les clarifications.
      Les résultats partiels sont bufferisés.
```

---

### 3. Prompt Enricher — Créer des Prompts Optimaux pour les Sub-Agents

L'orchestrateur ne forwarde JAMAIS la demande brute de l'utilisateur. Il crée un prompt enrichi :

```yaml
prompt_enrichment:
  # Ce que le sub-agent reçoit
  enriched_prompt:
    # Contexte projet (extrait de shared-context + session)
    project_context:
      stack: "{technologies du projet}"
      conventions: "{conventions de code/nommage}"
      current_sprint: "{sprint en cours si applicable}"
      relevant_decisions: ["{ADRs et décisions pertinentes}"]
    
    # La tâche clarifiée
    task:
      objective: "{intention primaire, reformulée clairement}"
      constraints: ["{contraintes explicites et implicites}"]
      acceptance_criteria: ["{critères de succès mesurables}"]
      out_of_scope: ["{ce qui NE DOIT PAS être fait}"]
    
    # Informations des échanges précédents
    conversation_context:
      relevant_qa: ["{Q&A pertinentes de cette session}"]
      user_preferences: ["{préférences exprimées}"]
      prior_outputs: ["{références aux outputs précédents si pertinent}"]
    
    # Directives HUP
    hup_directives:
      - "Si tu es incertain sur un point, NE PAS inventer. Escalader via uncertainty_report."
      - "Confiance ROUGE = STOP immédiat + question structurée."
      - "Preuve d'effort obligatoire avant toute escalade."
```

### Règles d'enrichissement

1. **Ne jamais surcharger** — fournir le contexte pertinent, pas tout le contexte
2. **Contraintes explicites** — toujours inclure ce qui NE DOIT PAS être fait
3. **Historique pertinent** — Q&A et décisions, pas le bavardage
4. **Directives HUP** — toujours rappeler les règles anti-hallucination

---

### 4. Route Engine — Routage Intelligent

```yaml
routing_engine:
  # Logique de sélection
  selection_criteria:
    1_expertise_match: "L'agent a les capabilities pour cette tâche"
    2_workload_balance: "Si plusieurs agents qualifiés, choisir le moins chargé"
    3_context_continuity: "Privilégier l'agent qui a déjà travaillé sur ce sujet"
    4_trust_history: "Privilégier l'agent avec le meilleur historique de trust scores"
  
  # Modes de dispatch
  dispatch_modes:
    single: "Une seule tâche → un seul agent"
    parallel: "Tâches indépendantes → agents parallèles (BM-19 subagent-orchestration)"
    sequential: "Tâches dépendantes → chaîne séquentielle (BM-11 boomerang)"
    cross_validate: "Output critique → producteur + validateur (BM-52 CVTL)"
  
  # Fallback
  fallback:
    no_agent_match: "L'orchestrateur tente lui-même + flag YELLOW"
    all_agents_uncertain: "Escalade à l'utilisateur avec le contexte complet"
```

---

### 5. Result Aggregator — Agrégation Cohérente

Quand plusieurs agents ont travaillé, l'orchestrateur agrège :

```yaml
result_aggregation:
  # Sources
  inputs:
    - agent_outputs: ["{résultats de chaque agent}"]
    - trust_scores: ["{scores CVTL si cross-validation}"]
    - uncertainty_reports: ["{rapports HUP si escalades}"]
    - pending_questions: ["{questions QEC non résolues}"]
  
  # Processus
  aggregation_process:
    1_merge: "Combiner les outputs selon la stratégie de merge appropriée"
    2_deconflict: "Si contradictions entre agents → signaler à l'utilisateur"
    3_annotate: "Ajouter les trust scores et hypothèses sur chaque section"
    4_summarize: "Résumé exécutif en haut, détails en dessous"
    5_present_questions: "Si questions QEC en attente → les ajouter en fin"
  
  # Format de sortie
  output_format: |
    ## Résultat — {task_description}
    
    {résumé exécutif en 2-3 lignes}
    
    {contenu détaillé agrégé}
    
    ---
    🛡️ Trust: {composite_score}/100 | Produit par {agents} | Validé par {validators}
    {hypothèses et notes si YELLOW}
    
    {questions QEC si en attente}
```

---

### 6. Session Knowledge Graph — Mémoire Conversationnelle

L'orchestrateur maintient un graphe de connaissance de la session :

```yaml
session_knowledge_graph:
  # Nœuds
  nodes:
    - type: decision
      content: "Décision prise pendant la session"
      source: user | agent | consensus
    
    - type: fact
      content: "Fait établi et vérifié"
      source: project_files | user_statement | agent_verification
    
    - type: assumption
      content: "Hypothèse non vérifiée"
      status: active | verified | invalidated
    
    - type: preference
      content: "Préférence exprimée par l'utilisateur"
      scope: session | permanent
    
    - type: qa
      content: "Question posée et réponse reçue"
      agents_informed: ["{agents qui ont reçu cette info}"]
  
  # Usage
  usage:
    prompt_enrichment: "Nourrir le Prompt Enricher avec les nœuds pertinents"
    auto_resolution: "Tenter de résoudre les questions QEC avec les nœuds fact/decision"
    contradiction_detection: "Détecter si un output contredit un nœud existant"
    context_transfer: "Transférer le graphe pertinent lors d'un handoff d'agent"
```

---

## Intégration avec les Protocoles Existants

| Protocole | Relation avec SOG |
|-----------|------------------|
| **HUP (BM-50)** | Les sub-agents utilisent HUP → escaladent vers SOG |
| **QEC (BM-51)** | SOG héberge le Question Buffer → agrège et présente |
| **CVTL (BM-52)** | SOG déclenche les cross-validations → agrège les trust scores |
| **Subagent (BM-19)** | SOG utilise l'orchestration subagent pour le dispatch parallèle |
| **Boomerang (BM-11)** | SOG peut déclencher un boomerang pour les tâches multi-step |
| **A2A (BM-32)** | SOG peut dispatcher vers des agents externes via A2A |
| **BMAD Trace (BM-28)** | SOG logge chaque étape dans la trace |
| **State Checkpoint (BM-06)** | SOG crée des checkpoints pour les orchestrations longues |
| **AMN (BM-55)** | SOG surveille le mesh P2P, intervient en cas de challenge non résolu, agent offline |
| **SHP (BM-56)** | SOG déclenche et supervise les huddles sélectifs, arbitre si non-convergence |
| **ARG (BM-57)** | SOG utilise le graphe relationnel pour optimiser le routage et la formation d'équipes |
| **HPE (BM-58)** | SOG utilise le moteur HPE pour orchestrer les DAG hybrides (parallel + séquentiel + opportuniste) |
| **ELSS (BM-59)** | SOG observe l'état partagé, reconstruit le shared state, détecte les conflits |

---

## Mode de Fonctionnement

### Mode Transparent (défaut)

L'utilisateur ne voit pas la mécanique interne :
- Pas de "je dispatch à l'agent X"
- Pas de logs internes visibles
- Juste le résultat agrégé avec trust score

### Mode Verbose

Activé par `[VERBOSE]` ou `mode détaillé` :
- L'orchestrateur montre qui travaille sur quoi
- Les échanges inter-agents sont visibles
- Le routing et l'enrichissement sont expliqués

### Mode Party (intégration Party Mode)

Le Party Mode (EPIC 5) est un mode spécial de SOG où :
- Les agents sont visibles et parlent directement
- Le Productive Conflict Engine est actif
- L'orchestrateur joue le rôle de facilitateur plutôt que de gateway

---

## Intégration BMAD Trace

```
[timestamp] [SOG]            [INTENT:analyzed]    primary="{intent}" | shadows={count} | complexity={level}
[timestamp] [SOG]            [CLARIFY:asked]      questions={count} | parallel_exec={true|false}
[timestamp] [SOG]            [CLARIFY:received]   answers={count} | shadows_remaining={count}
[timestamp] [SOG]            [PROMPT:enriched]    for={agent_id} | context_nodes={count}
[timestamp] [SOG]            [ROUTE:dispatched]   mode={single|parallel|sequential} | agents=[{list}]
[timestamp] [SOG]            [AGGREGATE:merged]   sources={count} | trust_composite={score}
[timestamp] [SOG]            [AGGREGATE:conflict] between={agent1}↔{agent2} | resolution={method}
[timestamp] [SOG]            [SESSION:node-added] type={decision|fact|assumption} | content="{summary}"
```

---

## Exemple Complet — Flux End-to-End

```
Guilhem : "Implémente l'authentification JWT pour le projet"

─── INTENTION ANALYSIS ───
primary_intent: "Implémenter auth JWT"
shadow_zones:
  - "Quel type de JWT ? (stateless/stateful)"     → résolvable par ADR → auto-résolu : stateless (ADR-042)
  - "Quelle lib JWT ?"                            → résolvable par package.json → auto-résolu : jsonwebtoken
  - "Refresh tokens ?"                             → NON résolvable → à clarifier
  - "Endpoints concernés ?"                        → NON résolvable → à clarifier

─── CLARIFY ───
Orchestrateur → Guilhem :
  "OK pour JWT stateless (cohérent avec ADR-042, lib jsonwebtoken).
   2 points à clarifier :
   1. Refresh tokens ? → A) Oui avec rotation · B) Non, access token only
   2. Quels endpoints protéger ? → A) Tous sauf /login et /register · B) Liste spécifique"

Guilhem : "A et A"

─── PROMPT ENRICHMENT ───
Prompt enrichi pour Dev/Amelia :
  project_context: { stack: Node.js/Express, jwt: jsonwebtoken, db: PostgreSQL }
  task: "Implémenter auth JWT stateless avec refresh token rotation.
         Protéger tous endpoints sauf /login et /register.
         Suivre ADR-042."
  constraints: ["expiry access 15min", "refresh rotation", "TDD obligatoire"]
  hup_directives: ["Si incertain → uncertainty_report, pas d'invention"]

─── DISPATCH (parallel) ───
  Dev/Amelia → Implémentation (HUP actif)
  Architect/Winston → Validation ADR-042 cohérence (CVTL)

─── AGGREGATE ───
  Dev output: code implémenté, CC PASS
  Architect validation: trust_score 91/100, approved
  Questions QEC: aucune

─── RÉSULTAT ───
Orchestrateur → Guilhem :
  "Auth JWT implémentée et validée. ✅
   
   Fichiers modifiés : src/auth/jwt.ts, src/middleware/auth.ts, tests/auth.spec.ts
   Tests : 12/12 PASS, coverage 96%
   
   🛡️ Trust: 91/100 | Produit par 💻 Amelia | Validé par 🏗️ Winston"
```

---

*BM-53 Smart Orchestrator Gateway | framework/orchestrator-gateway.md*
