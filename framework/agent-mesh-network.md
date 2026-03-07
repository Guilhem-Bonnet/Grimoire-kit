<p align="right"><a href="../README.md">README</a> · <a href="../docs">Docs</a></p>

# <img src="../docs/assets/icons/network.svg" width="32" height="32" alt=""> Agent Mesh Network (AMN) — Réseau Maillé et Communication Peer-to-Peer

> **BM-55** — Architecture de découverte de services et communication directe entre agents
> sans passer systématiquement par l'orchestrateur.
>
> **Problème résolu** : L'orchestrateur est un goulet d'étranglement. Un agent dev qui a besoin
> d'un avis rapide de l'architecte doit remonter sa question au SOG, qui la reformule et la
> dispatche. Pour les échanges ciblés, c'est du overhead inutile.
>
> **Principe** : Chaque agent s'enregistre dans un registry avec ses capabilities et son statut.
> Les agents peuvent communiquer directement en P2P pour les échanges ciblés, tout en respectant
> la gouvernance de l'orchestrateur pour les décisions finales.
>
> **Implémentation** : S'appuie sur `message-bus.py` (transport), `agent-worker.py` (KNOWN_AGENTS),
> `agent-caller.py` (A2A calls), ARG (BM-57) pour le graphe relationnel, et ELSS (BM-59) pour
> l'observabilité.

<img src="../docs/assets/divider.svg" width="100%" alt="">


## <img src="../docs/assets/icons/temple.svg" width="28" height="28" alt=""> Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    AGENT MESH NETWORK                            │
│                                                                  │
│  ┌──────────────────────────────────────────────────────┐       │
│  │            SERVICE REGISTRY (discovery)               │       │
│  │  _bmad-output/.agent-registry.yaml                    │       │
│  │                                                       │       │
│  │  dev/Amelia  [online]  cap:[impl,tdd]  load:2/5      │       │
│  │  qa/Quinn    [online]  cap:[test,sec]  load:1/5      │       │
│  │  arch/Winston[online]  cap:[arch,api]  load:0/5      │       │
│  │  pm/John     [idle]    cap:[prd,req]   load:0/5      │       │
│  └──────────────────────┬───────────────────────────────┘       │
│                          │                                       │
│  ┌─────────────┐  ┌─────┴──────┐  ┌──────────────┐             │
│  │   Agent A    │←─┤ P2P Direct ├─→│   Agent B     │             │
│  │  (request)   │  │  Messages  │  │  (respond)    │             │
│  └──────┬───────┘  └────────────┘  └──────┬───────┘             │
│         │                                  │                     │
│         │ emit events                      │ emit events         │
│         ▼                                  ▼                     │
│  ┌──────────────────────────────────────────────────────┐       │
│  │          EVENT BUS (ELSS BM-59) — observabilité       │       │
│  └──────────────────────────────────────────────────────┘       │
│                          │                                       │
│                          ▼                                       │
│  ┌──────────────────────────────────────────────────────┐       │
│  │          SOG (BM-53) — supervision & gouvernance      │       │
│  │  Observe tout · Intervient si nécessaire              │       │
│  └──────────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────────┘
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/team.svg" width="28" height="28" alt=""> Service Registry — Découverte d'Agents

Fichier : `_bmad-output/.agent-registry.yaml`

```yaml
# Auto-maintained by mesh protocol — agents register/deregister on activation
# Last heartbeat check: 2026-03-05T14:35:30Z

registry:
  version: "1.0"
  heartbeat_interval_sec: 30
  stale_threshold_sec: 120    # agent considéré offline après 2 heartbeats manqués
  
  agents:
    dev:
      persona: "Amelia"
      status: online          # online | busy | idle | offline
      registered_at: "2026-03-05T14:00:00Z"
      last_heartbeat: "2026-03-05T14:35:00Z"
      
      # Capabilities publiées (statiques + émergentes ARG)
      capabilities:
        static: ["story-execution", "tdd", "code-implementation"]
        emergent: ["JWT-auth", "PostgreSQL-optimization"]
      
      # Charge de travail
      workload:
        current_tasks: 2
        max_concurrent: 5
        queue_depth: 0       # tâches en attente
        estimated_free_at: "2026-03-05T14:45:00Z"
      
      # Canaux de communication acceptés
      accepts:
        p2p_direct: true     # accepte les messages P2P
        huddle_invite: true  # accepte les invitations de huddle
        broadcast: true      # écoute les broadcasts
      
      # Endpoints (pour modes avancés avec workers réels)
      endpoint:
        type: "in-process"   # in-process | mcp | http
        address: null        # null pour in-process
    
    qa:
      persona: "Quinn"
      status: online
      registered_at: "2026-03-05T14:00:00Z"
      last_heartbeat: "2026-03-05T14:35:15Z"
      capabilities:
        static: ["test-automation", "api-testing", "e2e-testing", "coverage"]
        emergent: ["security-testing-OWASP"]
      workload:
        current_tasks: 1
        max_concurrent: 5
        queue_depth: 0
      accepts:
        p2p_direct: true
        huddle_invite: true
        broadcast: true
      endpoint:
        type: "in-process"
    
    architect:
      persona: "Winston"
      status: idle
      # ... (même structure)
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Protocole d'Enregistrement

```yaml
registration_protocol:
  # À l'activation d'un agent
  on_activate:
    1: "Charger ses capabilities depuis agent-manifest + ARG émergentes"
    2: "S'enregistrer dans le registry avec status=online"
    3: "S'abonner au message bus : p2p-{agent_id}, huddle-*, broadcast"
    4: "Émettre événement ELSS : type=agent_registered"
    5: "Démarrer le heartbeat (toutes les 30 secondes)"
  
  # Heartbeat
  heartbeat:
    method: "Mettre à jour last_heartbeat + workload dans le registry"
    interval: 30  # secondes
    content:
      status: "online | busy (si current_tasks >= max_concurrent)"
      workload: "{current_tasks, queue_depth}"
  
  # À la désactivation
  on_deactivate:
    1: "Mettre status=offline dans le registry"
    2: "Se désabonner du message bus"
    3: "Émettre événement ELSS : type=agent_deregistered"
  
  # Nettoyage des agents fantômes
  stale_cleanup:
    trigger: "last_heartbeat > stale_threshold_sec"
    action: "Marquer status=offline, notifier SOG"
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/network.svg" width="28" height="28" alt=""> Communication Peer-to-Peer (P2P)

### Types de Messages P2P

| Type | Description | Quand utiliser | Requiert SOG ? |
|------|------------|---------------|----------------|
| `ask` | Question ciblée à un expert | Besoin d'un avis rapide et ponctuel | Non |
| `inform` | Notification sans réponse attendue | Partager une info pertinente | Non |
| `request` | Demande de tâche déléguée | Sous-tâche spécifique | Notification SOG |
| `respond` | Réponse à un ask/request | Suite d'un échange | Non |
| `challenge` | Contre-argument sur un output | Désaccord substantiel à résoudre | Notification SOG |
| `offer` | Proposition d'aide non sollicitée | Agent observe un besoin via ELSS | Non |

### Format d'un Message P2P

```yaml
p2p_message:
  id: "p2p-{sender}-{timestamp}-{seq}"
  from: "{agent_id}/{persona}"
  to: "{agent_id}/{persona}"
  type: "ask | inform | request | respond | challenge | offer"
  
  # Contenu
  subject: "Sujet en 1 ligne"
  body: "Message détaillé"
  
  # Contexte (optionnel mais recommandé)
  context:
    task_reference: "{task_id si applicable}"
    relevant_files: ["{paths}"]
    urgency: "low | normal | high"
  
  # Pour les réponses
  in_reply_to: "{message_id}" # null si message initial
  
  # Gouvernance
  governance:
    sog_notified: true | false  # SOG a-t-il été notifié ?
    elss_logged: true           # toujours loggé dans le event bus
    
  # Transport
  transport:
    via: "message-bus"
    channel: "p2p-{to_agent_id}"
    pattern: "request-reply"
```

### Règles de Gouvernance P2P

```yaml
governance_rules:
  # Ce qui est autorisé en P2P sans SOG
  autonomous:
    - type: "ask"
      condition: "Question ponctuelle sans impact sur les décisions"
      example: "Hey Winston, quelle convention pour les noms de tables PG ?"
    
    - type: "inform"
      condition: "Partage d'info sans demande d'action"
      example: "FYI Quinn, j'ai ajouté des edge cases dans auth.ts"
    
    - type: "respond"
      condition: "Réponse à un message P2P existant"
    
    - type: "offer"
      condition: "Proposition d'aide basée sur observation ELSS"
      example: "J'ai vu ton uncertainty_raised sur le caching — je peux aider"
  
  # Ce qui nécessite une notification au SOG
  sog_notify:
    - type: "request"
      reason: "Tâche déléguée → SOG doit tracker"
      notification: "informal — SOG observe via ELSS"
    
    - type: "challenge"
      reason: "Désaccord → peut nécessiter arbitrage"
      escalation: "Si non résolu après 2 échanges → SOG arbitre"
  
  # Limites
  limits:
    max_p2p_without_sog: 5     # messages avant notification SOG obligatoire
    max_challenge_depth: 2      # allers-retours challenge avant arbitrage SOG
    no_decision_in_p2p: true    # les décisions finales passent TOUJOURS par SOG
    
    forbidden_in_p2p:
      - "Prendre une décision d'architecture sans SOG"
      - "Modifier un artefact sans coordination"
      - "Contourner une question bloquante QEC"
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/network.svg" width="28" height="28" alt=""> Découverte de Services

Un agent peut trouver le bon interlocuteur via le registry :

```yaml
discovery_protocol:
  # "Qui peut m'aider avec X ?"
  find_expert:
    input: "{capability ou domaine recherché}"
    process:
      1: "Chercher dans registry.agents[].capabilities (static + emergent)"
      2: "Filtrer par status != offline"
      3: "Enrichir avec ARG : trust_score, synergy_score avec le demandeur"
      4: "Trier par : capability_match × 0.4 + trust × 0.3 + availability × 0.3"
      5: "Retourner le top 3"
    
    result:
      - agent: "architect/Winston"
        match_score: 0.92
        status: "idle"          # disponible immédiatement
        trust: 94
        route: "p2p direct"
      - agent: "dev/Amelia"
        match_score: 0.78
        status: "busy (2/5)"
        trust: 87
        route: "p2p queued"     # réponse asynchrone probable
  
  # "Qui travaille sur le même sujet ?"
  find_related:
    input: "{task_id ou topic}"
    process:
      1: "Chercher dans ELSS les événements liés au topic"
      2: "Identifier les agents ayant émis des events sur ce topic"
      3: "Croiser avec le status actuel dans le registry"
    
    result:
      - agent: "qa/Quinn"
        relation: "Currently testing the same module"
        last_event: "2026-03-05T14:33:00Z"
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/network.svg" width="28" height="28" alt=""> Load Balancing

Quand plusieurs agents sont éligibles :

```yaml
load_balancing:
  strategy: "weighted-round-robin"  # pas de random — déterministe et traçable
  
  factors:
    availability: 0.40     # (max_concurrent - current_tasks) / max_concurrent
    expertise: 0.35        # ARG trust_score + capability_match
    recency: 0.15          # dernier heartbeat → favorise les agents actifs
    synergy: 0.10          # ARG synergy_score avec les autres agents du workflow
  
  rules:
    - "Ne jamais assigner à un agent en status=busy avec queue_depth > 0"
    - "Distribuer équitablement sur les sessions longues"
    - "Si expertise >> availability → notifier SOG du compromis"
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/chart.svg" width="28" height="28" alt=""> Observabilité

Toute communication mesh est observable via ELSS :

```yaml
observability:
  # Chaque message P2P émet un événement ELSS
  on_p2p_send:
    event_type: "p2p_message"
    payload:
      from: "{sender}"
      to: "{recipient}"
      type: "{message_type}"
      subject: "{subject}"
      # body NON inclus dans l'event (vie privée des échanges)
  
  # Le SOG peut observer tous les événements P2P
  sog_monitor:
    subscribe_to: "p2p_message"
    alert_conditions:
      - "challenge non résolu après 2 échanges"
      - "Agent offline contacté"
      - "Nombre P2P > max_p2p_without_sog"
      - "Décision prise en P2P (interdit)"
  
  # Dashboard mesh (commande)
  introspection:
    - "[MESH-STATUS]" : "Agents online, workload, derniers messages"
    - "[MESH-TOPOLOGY]" : "Graphe visuel des connexions actives"
    - "[MESH-AGENT dev]" : "Détail d'un agent : messages envoyés/reçus, latence"
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/integration.svg" width="28" height="28" alt=""> Intégration BMAD Trace

```
[timestamp] [dev/Amelia]     [AMN:register]   status=online | cap=[impl,tdd,JWT-auth]
[timestamp] [dev/Amelia]     [AMN:p2p-ask]    to=architect/Winston | subject="PG naming convention"
[timestamp] [architect]      [AMN:p2p-respond] to=dev/Amelia | in_reply_to=p2p-dev-001
[timestamp] [orchestrator]   [AMN:discover]   query="security-testing" | result=qa/Quinn(0.92)
[timestamp] [dev/Amelia]     [AMN:heartbeat]  status=busy(3/5) | queue=1
[timestamp] [orchestrator]   [AMN:alert]      challenge-unresolved: dev↔architect after 2 rounds
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Référence Croisée

- Message Bus : [framework/tools/message-bus.py](tools/message-bus.py) — transport P2P
- Agent Worker : [framework/tools/agent-worker.py](tools/agent-worker.py) — worker lifecycle
- Agent Caller : [framework/tools/agent-caller.py](tools/agent-caller.py) — A2A tool calling
- Agent Relationship Graph : [framework/agent-relationship-graph.md](agent-relationship-graph.md) (BM-57)
- Event Log : [framework/event-log-shared-state.md](event-log-shared-state.md) (BM-59)
- Orchestrator Gateway : [framework/orchestrator-gateway.md](orchestrator-gateway.md) (BM-53)
- Selective Huddle : [framework/selective-huddle-protocol.md](selective-huddle-protocol.md) (BM-56)


*BM-55 Agent Mesh Network | framework/agent-mesh-network.md*
