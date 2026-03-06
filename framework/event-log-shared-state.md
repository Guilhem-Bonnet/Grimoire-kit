# Event Log & Shared State (ELSS) — Journal d'Événements et État Partagé

> **BM-59** — Bus d'événements persistant et état partagé observable pour la coordination multi-agents.
>
> **Problème résolu** : Les agents opèrent dans des contextes isolés. Quand un agent prend une
> décision, les autres ne le savent pas. Le résultat : doublons, contradictions, et perte de contexte
> entre étapes d'un workflow.
>
> **Principe** : Chaque action significative émet un événement sur un bus partagé. L'état global
> est reconstruit à partir du log d'événements (event sourcing). Tout agent peut observer l'état
> sans couplage direct avec les autres.
>
> **Implémentation** : S'appuie sur `framework/tools/message-bus.py` (InProcess/Redis/NATS)
> pour le transport et `BMAD_TRACE.md` (BM-28) pour la persistance.

---

## Architecture

```
┌─────────┐  ┌─────────┐  ┌─────────┐
│ Agent A  │  │ Agent B  │  │ Agent C  │
│ (emit)   │  │ (emit)   │  │ (emit)   │
└────┬─────┘  └────┬─────┘  └────┬─────┘
     │emit         │emit         │emit
     ▼             ▼             ▼
┌────────────────────────────────────────┐
│          EVENT BUS (message-bus.py)     │
│  pattern: pub-sub · topic: "events"    │
│  InProcess → Redis → NATS (scalable)   │
├────────────────────────────────────────┤
│         EVENT LOG (append-only)        │
│  _bmad-output/.event-log.jsonl         │
│  ┌─ event_001 ─────────────────────┐  │
│  │ agent: dev/Amelia               │  │
│  │ type: decision                  │  │
│  │ payload: {chose JWT stateless}  │  │
│  │ timestamp: 2026-03-05T14:32:01Z │  │
│  └─────────────────────────────────┘  │
│  ┌─ event_002 ...                  ┐  │
├────────────────────────────────────────┤
│       STATE PROJECTOR (read model)     │
│  Reconstruit l'état courant depuis     │
│  le log d'événements                   │
│  → _bmad-output/.shared-state.yaml    │
└────────────────────────────────────────┘
     │subscribe    │subscribe    │subscribe
     ▼             ▼             ▼
┌─────────┐  ┌─────────┐  ┌─────────┐
│ Agent A  │  │ Agent B  │  │ Agent C  │
│ (observe)│  │ (observe)│  │ (observe)│
└──────────┘  └──────────┘  └──────────┘
```

---

## Types d'Événements

| Type | Description | Émetteur typique | Exemple payload |
|------|------------|------------------|-----------------|
| `decision` | Décision prise | Tout agent | `{topic: "auth approach", choice: "JWT", rationale: "..."}` |
| `artifact_created` | Fichier créé/modifié | dev, architect, tech-writer | `{path: "src/auth.ts", action: "created"}` |
| `task_started` | Tâche démarrée | Tout agent | `{task_id: "US-042", description: "..."}` |
| `task_completed` | Tâche terminée | Tout agent | `{task_id: "US-042", status: "success", cc_result: "PASS"}` |
| `uncertainty_raised` | HUP ROUGE déclenché | Tout agent | `{gap_type: "missing_data", question: "..."}` |
| `question_resolved` | QEC répondue | Orchestrateur | `{question_id: "q-001", answer: "..."}` |
| `trust_scored` | CVTL validé | Validateur | `{artifact: "ADR-042", score: 87, verdict: "approve"}` |
| `conflict_detected` | Contradiction trouvée | Tout agent | `{existing: "REST", proposed: "GraphQL", source: "ADR"}` |
| `huddle_requested` | Huddle demandé (SHP) | Tout agent | `{topic: "perf concern", invited: ["dev","qa"]}` |
| `huddle_completed` | Huddle terminé (SHP) | Orchestrateur | `{huddle_id: "h-003", verdict: "approved", duration: "4min"}` |
| `agent_registered` | Agent enregistré dans le mesh (AMN) | Agent activé | `{agent: "dev/Amelia", capabilities: ["impl","tdd"]}` |
| `agent_deregistered` | Agent retiré du mesh (AMN) | Agent désactivé | `{agent: "dev/Amelia", reason: "deactivated"}` |
| `p2p_message` | Message P2P inter-agents (AMN) | Tout agent | `{from: "dev", to: "architect", type: "ask", subject: "..."}` |
| `assumption_made` | Hypothèse formulée (optionnel) | Tout agent | `{assumption: "<10k users", domain: "performance"}` |
| `dependency_found` | Dépendance externe découverte (optionnel) | Tout agent | `{dependency: "Redis 7+", reason: "pub-sub pattern"}` |
| `state_checkpoint` | Snapshot d'état | Orchestrateur | `{checkpoint_id: "cp-003", step: "4/7"}` |

---

## Format du Event Log

Fichier : `_bmad-output/.event-log.jsonl` (JSON Lines, append-only)

```jsonl
{"id":"evt-a3f9b201","ts":"2026-03-05T14:32:01Z","agent":"dev/Amelia","type":"decision","payload":{"topic":"auth","choice":"JWT stateless","rationale":"scalabilité horizontale"},"trace_id":"session-001","seq":1}
{"id":"evt-a3f9b202","ts":"2026-03-05T14:32:15Z","agent":"dev/Amelia","type":"artifact_created","payload":{"path":"src/auth/jwt.ts","action":"created","lines":142},"trace_id":"session-001","seq":2}
{"id":"evt-a3f9b203","ts":"2026-03-05T14:33:00Z","agent":"qa/Quinn","type":"task_started","payload":{"task_id":"review-auth","description":"Review sécurité module auth"},"trace_id":"session-001","seq":3}
```

### Schéma d'un Événement

```yaml
event_schema:
  id: "evt-{uuid8}"              # identifiant unique
  ts: "{iso_datetime}"            # timestamp UTC
  agent: "{agent_id}/{persona}"   # émetteur
  type: "{event_type}"            # voir table des types
  payload: {}                     # données spécifiques au type
  trace_id: "{session_trace_id}"  # lien avec BMAD_TRACE
  seq: integer                    # numéro de séquence global
  
  # Optionnel
  correlation_id: "{id}"          # lien avec un événement parent
  tags: ["sprint-7", "auth"]      # tags pour filtrage
  visibility: "all | team | private"  # qui peut observer
```

---

## Shared State — État Reconstruit

Fichier : `_bmad-output/.shared-state.yaml`

L'état partagé est **projeté** depuis le log d'événements (event sourcing pattern).
Il n'est JAMAIS modifié directement — seulement reconstruit.

```yaml
# Auto-generated from .event-log.jsonl — DO NOT EDIT MANUALLY
# Last rebuilt: 2026-03-05T14:35:30Z | Events processed: 42

shared_state:
  session:
    id: "session-001"
    started: "2026-03-05T14:00:00Z"
    active_agents: ["dev/Amelia", "qa/Quinn", "architect/Winston"]
    current_step: "implementation"
    checkpoint: "cp-003"
  
  decisions:
    - topic: "auth approach"
      choice: "JWT stateless"
      decided_by: "dev/Amelia"
      validated_by: "architect/Winston"
      trust_score: 91
      timestamp: "2026-03-05T14:32:01Z"
  
  artifacts:
    - path: "src/auth/jwt.ts"
      created_by: "dev/Amelia"
      status: "created"
      cc_result: "PASS"
    - path: "docs/adr-042-auth.md"
      created_by: "architect/Winston"
      status: "validated"
      trust_score: 91
  
  active_tasks:
    - id: "review-auth"
      agent: "qa/Quinn"
      status: "in_progress"
      started: "2026-03-05T14:33:00Z"
  
  open_questions:
    - id: "q-dev-001"
      question: "Timeout refresh token ?"
      from: "dev/Amelia"
      status: "pending"
      priority: "important"
  
  conflicts: []  # contradictions non résolues
  
  metrics:
    events_total: 42
    decisions_count: 3
    trust_score_avg: 87
    questions_resolved: 5
    questions_pending: 1
```

---

## Protocole d'Émission

Chaque agent émet des événements via le message bus :

```yaml
emit_protocol:
  # Quand émettre (obligatoire)
  mandatory_events:
    - trigger: "Décision prise"
      type: decision
      payload_required: [topic, choice, rationale]
    
    - trigger: "Fichier créé ou modifié significativement"
      type: artifact_created
      payload_required: [path, action]
    
    - trigger: "Tâche démarrée"
      type: task_started
      payload_required: [task_id, description]
    
    - trigger: "Tâche terminée"
      type: task_completed
      payload_required: [task_id, status]
    
    - trigger: "HUP ROUGE déclenché"
      type: uncertainty_raised
      payload_required: [gap_type, question]
  
  # Quand émettre (recommandé)
  optional_events:
    - trigger: "Hypothèse formulée"
      type: assumption_made
    - trigger: "Dépendance externe découverte"
      type: dependency_found
  
  # Comment émettre
  emission_method:
    primary: "message-bus.py pub-sub pattern"
    format: |
      bus.send(AgentMessage(
        sender="{agent_id}",
        recipient="*",            # broadcast
        msg_type="event",
        payload={event_dict},
        pattern="pub-sub"
      ))
    
    fallback: |
      Si message-bus non disponible :
      → Append direct au fichier .event-log.jsonl
      → Logger dans BMAD_TRACE.md
```

---

## Protocole d'Observation

Chaque agent peut observer l'état partagé :

```yaml
observe_protocol:
  # Lecture de l'état courant
  read_state:
    method: "Charger _bmad-output/.shared-state.yaml"
    frequency: "Au début de chaque tâche + après chaque subscription reçue"
    cache: "Valide 30 secondes — rechargement lazy après événement reçu"
  
  # Souscription aux événements
  subscribe:
    method: "bus.subscribe(agent_id, 'event')"
    filter_by_type: true  # chaque agent choisit les types d'événements qui l'intéressent
    examples:
      dev: ["decision", "artifact_created", "conflict_detected", "question_resolved"]
      qa: ["task_completed", "artifact_created", "trust_scored"]
      architect: ["decision", "conflict_detected", "uncertainty_raised"]
  
  # Réaction automatique
  on_event:
    conflict_detected:
      action: "Vérifier si le conflit me concerne → si oui, émettre un avis"
    decision:
      action: "Mettre à jour mon contexte local"
    uncertainty_raised:
      action: "Si je peux aider → offrir expertise via huddle (SHP BM-56)"
```

---

## State Projection — Reconstruction

Le projecteur reconstruit `.shared-state.yaml` depuis `.event-log.jsonl` :

```yaml
projection_rules:
  decision:
    # Chaque décision crée/met à jour une entrée dans shared_state.decisions[]
    key: payload.topic
    action: upsert  # la plus récente gagne
  
  artifact_created:
    key: payload.path
    action: upsert  # statut le plus récent
  
  task_started:
    key: payload.task_id
    action: add_to  # shared_state.active_tasks[]
  
  task_completed:
    key: payload.task_id
    action: move_to  # de active_tasks[] vers completed_tasks[]
  
  uncertainty_raised:
    key: auto_generated
    action: add_to  # shared_state.open_questions[]
  
  question_resolved:
    key: payload.question_id
    action: resolve  # open_questions[] → resolved + enrichit decisions si applicable
  
  conflict_detected:
    key: auto_generated
    action: add_to  # shared_state.conflicts[]
  
  huddle_requested:
    key: auto_generated
    action: add_to  # shared_state.active_huddles[] (retiré à huddle_completed)
  
  huddle_completed:
    key: payload.huddle_id
    action: resolve  # active_huddles[] → completed, enrichit decisions si verdict
  
  state_checkpoint:
    key: payload.checkpoint_id
    action: update  # shared_state.session.checkpoint

  # Trigger de reconstruction
  rebuild_triggers:
    - "Requête explicite : [REBUILD-STATE]"
    - "Toutes les 50 événements"
    - "Début de session"
    - "Après un state_checkpoint"
```

---

## Garbage Collection et Rétention

```yaml
retention_policy:
  event_log:
    max_events: 10000
    max_age_days: 30
    archive_strategy: "Archiver dans _bmad-output/.event-log-archive/{date}.jsonl"
  
  shared_state:
    rebuild_on_archive: true  # reconstruire après archivage
    keep_decisions: always    # les décisions ne sont jamais archivées
    keep_artifacts: always    # idem pour les artefacts
    prune_tasks: completed_older_than_7_days
    prune_questions: resolved_older_than_7_days
```

---

## Intégration BMAD Trace

Chaque événement est aussi loggé dans BMAD_TRACE.md :

```
[timestamp] [agent/Name]     [ELSS:emit]      type=decision | topic="auth approach"
[timestamp] [orchestrator]   [ELSS:project]   state rebuilt | events=42 | decisions=3
[timestamp] [agent/Name]     [ELSS:observe]   subscribed to [decision, conflict_detected]
[timestamp] [orchestrator]   [ELSS:gc]        archived 500 events older than 30d
```

---

## Référence Croisée

- Message Bus : [framework/tools/message-bus.py](tools/message-bus.py) — transport des événements
- BMAD Trace : [framework/bmad-trace.md](bmad-trace.md) — persistance audit trail
- State Checkpoint : `framework/workflows/state-checkpoint.md` (BM-06)
- Shared Context : `_bmad/_memory/shared-context.md` — enrichi par les projections ELSS
- Agent Mesh Network : [framework/agent-mesh-network.md](agent-mesh-network.md) (BM-55)
- Selective Huddle : [framework/selective-huddle-protocol.md](selective-huddle-protocol.md) (BM-56)

---

*BM-59 Event Log & Shared State | framework/event-log-shared-state.md*
