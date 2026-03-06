# Question Escalation Chain (QEC) — Remontée Structurée des Incertitudes

> **BM-51** — Protocole de canalisation et agrégation des questions des sub-agents.
>
> **Problème résolu** : Sans canal structuré, un sub-agent bloqué a trois mauvais choix :
> 1) halluciner une réponse, 2) poser une question en plein milieu d'un workflow,
> 3) abandonner la tâche. La QEC offre un 4e choix : escalader proprement.
>
> **Principe** : Toute question d'un sub-agent remonte à l'orchestrateur qui collecte,
> trie, tente de résoudre par contexte, et ne dérange l'utilisateur qu'en dernier recours
> avec un lot structuré.

---

## Architecture de la Chaîne

```
Sub-Agent (bloqué)
      │
      │  Uncertainty Report (HUP)
      ▼
┌─────────────────────────────────────┐
│  QUESTION BUFFER (orchestrateur)    │
│                                     │
│  ┌─ Phase 1: Collecte ───────────┐ │
│  │  Recevoir les questions        │ │
│  │  Dé-dupliquer                  │ │
│  │  Classifier (type + priorité)  │ │
│  └────────────────────────────────┘ │
│                                     │
│  ┌─ Phase 2: Auto-résolution ────┐ │
│  │  Chercher dans le contexte     │ │
│  │  Croiser avec l'historique QA  │ │
│  │  Résoudre si confiance ≥ 80%   │ │
│  └────────────────────────────────┘ │
│                                     │
│  ┌─ Phase 3: Présentation ───────┐ │
│  │  Grouper par thème             │ │
│  │  Prioriser (bloquant d'abord) │ │
│  │  Proposer des options          │ │
│  │  Présenter en LOT à l'user    │ │
│  └────────────────────────────────┘ │
└─────────────────────────────────────┘
      │
      │  Questions non résolues (lot structuré)
      ▼
   Utilisateur
      │
      │  Réponses
      ▼
┌─────────────────────────────────────┐
│  REDISTRIBUTION                     │
│  → Router chaque réponse vers       │
│    l'agent qui a posé la question   │
│  → Enrichir le contexte partagé     │
│  → Logger dans l'historique QA      │
└─────────────────────────────────────┘
```

---

## Format d'une Question Escaladée

Chaque question dans le buffer suit ce format :

```yaml
escalated_question:
  id: "q-{agent_id}-{timestamp}-{seq}"
  from_agent: "{agent_id}/{agent_name}"
  task_context: "tâche en cours quand la question est apparue"
  timestamp: "{iso_datetime}"

  question: "La question formulée clairement"
  
  classification:
    type: knowledge_gap | ambiguity | complexity | missing_data
    priority: blocking | important | nice_to_have
    domain: technical | business | process | data
  
  context_provided:
    what_i_know: "ce que l'agent sait déjà"
    what_i_tried: "ce qu'il a tenté"
    options_seen: ["option A", "option B"]
  
  resolution_hint: "ce qui débloquerait l'agent"
  
  status: pending | auto_resolved | user_resolved | expired
  resolution: null  # rempli quand résolu
```

---

## Phase 1 — Collecte et Classification

### Réception

L'orchestrateur reçoit les Uncertainty Reports (HUP) des sub-agents et en extrait les questions :

```
Pour chaque uncertainty_report reçu :
  Pour chaque blocking_gap dans le report :
    Créer une escalated_question avec :
      - question = blocking_gap.suggested_question
      - type = blocking_gap.type
      - priority = blocking si blocking_gap.impact contient "impossible" ou "bloquant"
      - context_provided = {understood + effort_spent du report}
```

### Dé-duplication

Avant d'ajouter une question au buffer, vérifier :
1. **Même question, même agent** → ignorer (doublon strict)
2. **Même question, agent différent** → merger en une seule question avec `from_agents: [agent1, agent2]`
3. **Question similaire (même sujet)** → grouper sous un même thème avec note "questions liées"

### Classification automatique

```yaml
priority_rules:
  blocking:
    - uncertainty_report.impact_assessment.blocking == true
    - uncertainty_report.impact_assessment.partial_delivery_possible == false
  important:
    - uncertainty_report.impact_assessment.blocking == false
    - uncertainty_report.impact_assessment.partial_delivery_possible == true
  nice_to_have:
    - type == ambiguity AND agent peut continuer avec hypothèse raisonnable
```

---

## Phase 2 — Auto-résolution par Contexte

Avant de déranger l'utilisateur, l'orchestrateur tente de résoudre chaque question :

### Sources de résolution

```yaml
auto_resolution_sources:
  # 1. Mémoire projet
  - source: "shared-context.md"
    description: "Contexte partagé du projet — décisions, stack, contraintes"
  
  # 2. Historique des décisions
  - source: "decisions-log.md"
    description: "Décisions passées qui pourraient répondre à la question"
  
  # 3. Historique QA (questions-réponses précédentes)
  - source: "_bmad-output/.qa-history.yaml"
    description: "Questions déjà posées et réponses de l'utilisateur"
  
  # 4. Contexte de la conversation en cours
  - source: "session_context"
    description: "Ce que l'utilisateur a dit dans cette session"
  
  # 5. Fichiers projet
  - source: "project_files"
    description: "README, docs, configs — infos factuelles"
```

### Critères d'auto-résolution

```
POUR CHAQUE question dans le buffer :
  Chercher une réponse dans les sources (dans l'ordre)
  
  SI réponse trouvée ET confiance ≥ 80% :
    → Marquer status = "auto_resolved"
    → Remplir resolution avec la réponse + source citée
    → Redistribuer immédiatement à l'agent
    → Logger dans BMAD_TRACE : [HUP:auto-resolved]
  
  SI réponse trouvée mais confiance < 80% :
    → Garder dans le buffer
    → Ajouter en note : "Réponse possible : {réponse} (confiance: {%})"
    → L'utilisateur verra la suggestion et pourra confirmer/infirmer
  
  SI aucune réponse trouvée :
    → Garder dans le buffer pour Phase 3
```

---

## Phase 3 — Présentation Groupée à l'Utilisateur

### Déclencheur de présentation

La présentation est déclenchée quand :
1. **Un agent est bloqué** (question `blocking`) → présentation immédiate
2. **Fin d'un cycle de tâches** → lot de toutes les questions en attente
3. **Accumulation ≥ 3 questions** → présentation proactive

### Format de présentation

```markdown
## ❓ Questions en attente — {count} questions de {agents_count} agents

### 🔴 Bloquantes ({blocking_count})

**Q1** — De {agent_icon} **{agent_name}** (tâche : {task_context})
> {question}
> 💡 Suggestion auto : {auto_suggestion si disponible}
> Options vues par l'agent : A) {option_a} · B) {option_b}

**Q2** — De {agent_icon} **{agent_name}** ...

### 🟡 Importantes ({important_count})

**Q3** — ...

### 🟢 Nice-to-have ({nice_count})

**Q4** — ...

---
📌 Répondez par numéro : `Q1: votre réponse` ou `Q1-Q3: réponse groupée si même sujet`
   Ou tapez `skip Q4` pour que l'agent utilise son meilleur jugement.
```

### Règles de présentation

- **Max 7 questions par lot** (chunking 7±2) — au-delà, prioriser et reporter les `nice_to_have`
- **Bloquantes toujours en premier**
- **Grouper par thème** quand possible (ex: 3 questions sur la config → un seul bloc)
- **Proposer des options** quand l'agent en a identifié → l'utilisateur peut juste dire "option A"
- **Permettre le skip** pour les non-bloquantes → l'agent utilise son meilleur jugement (JAUNE)

---

## Redistribution des Réponses

Quand l'utilisateur répond :

```
POUR CHAQUE réponse reçue :
  1. Identifier la question correspondante par son ID
  2. Mettre à jour : status = "user_resolved", resolution = {réponse}
  3. Redistribuer la réponse à l'agent source avec contexte :
     "Réponse à ta question Q{id} : {réponse}. Tu peux continuer ta tâche."
  4. Ajouter au QA History pour référence future
  5. Si la réponse enrichit le contexte général → mettre à jour shared-context.md
  6. Logger dans BMAD_TRACE : [QEC:user-resolved] agent={id} question={summary}
```

---

## QA History — Mémoire des Questions-Réponses

Fichier : `_bmad-output/.qa-history.yaml`

```yaml
# QA History — Auto-generated, do not edit manually
entries:
  - id: "q-dev-20260305-001"
    question: "Faut-il créer config.prod.yaml ?"
    answer: "Oui, copier config.staging.yaml et changer host=prod.example.com, tls=true"
    answered_by: user
    date: "2026-03-05"
    used_by_auto_resolve: 0  # compteur de fois où cette réponse a servi

  - id: "q-arch-20260305-002"
    question: "PostgreSQL ou MySQL pour le nouveau service ?"
    answer: "PostgreSQL — cohérent avec ADR-003"
    answered_by: auto  # résolu par contexte (decisions-log.md)
    date: "2026-03-05"
    used_by_auto_resolve: 3
```

---

## Intégration BMAD Trace

```
[timestamp] [orchestrator]  [QEC:received]       from=dev/Amelia | type=missing_data | priority=blocking
[timestamp] [orchestrator]  [QEC:auto-resolved]  q-id=q-arch-001 | source=decisions-log.md | confidence=92%
[timestamp] [orchestrator]  [QEC:presented]      count=3 | blocking=1 | important=2
[timestamp] [orchestrator]  [QEC:user-resolved]  q-id=q-dev-001 | answer_length=42chars
[timestamp] [orchestrator]  [QEC:redistributed]  q-id=q-dev-001 | to=dev/Amelia
```

---

## Intégration dans les Workflows

### Dans un step `orchestrate` (subagent-orchestration.md)

```yaml
- step: "implement-feature"
  type: orchestrate
  spawn:
    - agent: dev
      task: "Implémenter US-042..."
      output_key: implementation
      # HUP + QEC actifs automatiquement
  
  # Nouveau : gestion des escalations
  on_escalation:
    strategy: batch          # batch | immediate
    batch_trigger: end_of_spawn  # quand présenter le lot
    auto_resolve: true       # tenter l'auto-résolution d'abord
    max_questions_per_lot: 7
```

### Dans le boomerang workflow

```yaml
# Chaque step qui dépend d'un autre peut recevoir les réponses aux questions du step précédent
- step: 3
  name: "Implémentation"
  depends_on: [stories, architecture]
  escalation_context:
    include_qa_history: true  # charger les Q&A des steps précédents
    auto_resolve_from: [shared-context.md, decisions-log.md]
```

---

## Références Croisées

- Orchestrator Gateway : [framework/orchestrator-gateway.md](orchestrator-gateway.md) (BM-53) — héberge le Question Buffer
- Honest Uncertainty : [framework/honest-uncertainty-protocol.md](honest-uncertainty-protocol.md) (BM-50) — source des escalades
- Cross-Validation : [framework/cross-validation-trust.md](cross-validation-trust.md) (BM-52) — questions de validation croisée
- Selective Huddle : [framework/selective-huddle-protocol.md](selective-huddle-protocol.md) (BM-56) — huddle déclenché sur question complexe
- Event Log : [framework/event-log-shared-state.md](event-log-shared-state.md) (BM-59) — événements QEC persistés
- Hybrid Parallelism : [framework/hybrid-parallelism-engine.md](hybrid-parallelism-engine.md) (BM-58) — agrégation questions parallèles

---

*BM-51 Question Escalation Chain | framework/question-escalation-chain.md*
