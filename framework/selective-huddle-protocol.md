<p align="right"><a href="../README.md">README</a> · <a href="../docs">Docs</a></p>

# <img src="../docs/assets/icons/team.svg" width="32" height="32" alt=""> Selective Huddle Protocol (SHP) — Concertation Ciblée On-Demand

> **BM-56** — Mini sessions de discussion entre 2-4 agents sélectionnés par pertinence,
> déclenchées automatiquement ou manuellement, sans overhead d'un party mode complet.
>
> **Problème résolu** : Le party mode réunit tous les agents sur un sujet large. Mais souvent,
> il suffit d'une concertation rapide entre 2-3 experts ciblés. Inversement, certaines
> situations nécessitent une discussion multi-perspectives que l'orchestrateur ne peut pas
> résoudre seul.
>
> **Principe** : Un huddle est une micro-session de discussion (5-10 échanges max) entre
> agents sélectionnés par expertise, déclenchée par un besoin précis, avec un livrable
> structuré à la fin.
>
> **Implémentation** : S'appuie sur AMN (BM-55) pour la découverte d'agents,
> ARG (BM-57) pour la sélection optimale, PCE (BM-54) pour les techniques de débat,
> et ELSS (BM-59) pour l'observabilité.

<img src="../docs/assets/divider.svg" width="100%" alt="">


## <img src="../docs/assets/icons/temple.svg" width="28" height="28" alt=""> Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  DÉCLENCHEUR                                                   │
│  ├── Agent détecte un besoin (via ELSS observation)           │
│  ├── SOG identifie un conflit multi-perspectives              │
│  ├── CVTL produit un challenge nécessitant discussion         │
│  └── Utilisateur demande une concertation rapide              │
│                                                                │
│                          ▼                                     │
│  ┌──────────────────────────────────────────────────────┐     │
│  │          HUDDLE ORCHESTRATOR (dans SOG)               │     │
│  │                                                       │     │
│  │  1. Analyser le besoin → topic + type                │     │
│  │  2. Sélectionner les agents (ARG + AMN registry)     │     │
│  │  3. Choisir le format (quick-ask / debate / review)  │     │
│  │  4. Ouvrir le huddle → time-boxed                    │     │
│  │  5. Collecter le livrable → fermer                   │     │
│  └──────────────────────────────────────────────────────┘     │
│                          │                                     │
│       ┌──────────────────┼──────────────────┐                 │
│       ▼                  ▼                  ▼                 │
│  ┌─────────┐      ┌─────────┐       ┌─────────┐             │
│  │ Agent A  │ ←──→ │ Agent B  │ ←──→ │ Agent C  │             │
│  │ (invité) │      │ (invité) │      │ (invité) │             │
│  └─────────┘      └─────────┘       └─────────┘             │
│       Discussion P2P directe (via message-bus)                │
│                          │                                     │
│                          ▼                                     │
│  ┌──────────────────────────────────────────────────────┐     │
│  │          HUDDLE DELIVERABLE                           │     │
│  │  Décision | Recommandation | Avis partagé | Vote     │     │
│  └──────────────────────────────────────────────────────┘     │
└────────────────────────────────────────────────────────────────┘
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/bolt.svg" width="28" height="28" alt=""> Déclencheurs Automatiques

Le système détecte les situations nécessitant un huddle :

```yaml
auto_triggers:
  # 1. Conflit CVTL non résolu
  cvtl_challenge:
    condition: "Cross-validation verdict = 'challenge' après 1 iteration"
    huddle_type: "debate"
    participants: ["{producer}", "{validator}", "{arbitre}"]
    topic: "Résoudre le challenge sur {artifact}"
    urgency: high
  
  # 2. HUP JAUNE sur sujet multi-domaine
  hup_yellow_multi:
    condition: "Agent en JAUNE ET le sujet touche >= 2 domaines (tech + business)"
    huddle_type: "quick-consult"
    participants: "ARG.find_expert(domain1) + ARG.find_expert(domain2)"
    topic: "Consolider la confiance sur {task}"
    urgency: normal
  
  # 3. Contradiction détectée (ELSS conflict_detected)
  contradiction:
    condition: "Événement conflict_detected dans ELSS"
    huddle_type: "debate"
    participants: "Agents impliqués dans la contradiction"
    topic: "Résoudre la contradiction : {existing} vs {proposed}"
    urgency: high
  
  # 4. Agent observe un besoin via ELSS
  agent_initiated:
    condition: "Un agent envoie un message P2P de type 'offer' ou 'challenge'"
    huddle_type: "quick-consult"
    participants: "Agent initiateur + agent(s) concerné(s)"
    topic: "{sujet du message P2P}"
    urgency: normal
  
  # 5. Décision d'architecture émergente
  emerging_adr:
    condition: "Agent prend une décision technique significative sans ADR existant"
    huddle_type: "review"
    participants: ["architect", "{agent décideur}", "pm"]
    topic: "Valider ou formaliser la décision : {decision}"
    urgency: normal
  
  # 6. Story complexe (> 8 SP) avant implémentation
  complex_story:
    condition: "Story estimée > 8 SP ET pas encore implémentée"
    huddle_type: "review"
    participants: ["dev", "architect", "qa"]
    topic: "Décomposition et risques pour {story_id}"
    urgency: low
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/team.svg" width="28" height="28" alt=""> Types de Huddle

### Quick-Consult (2-3 agents, 3-5 échanges)

```yaml
quick_consult:
  description: "Question rapide nécessitant 1-2 avis d'experts"
  duration: "3-5 messages max"
  format:
    1_question: "L'initiateur pose sa question avec contexte"
    2_avis: "Chaque expert répond avec avis + confiance"
    3_synthese: "L'initiateur synthétise les avis et décide"
  
  deliverable:
    type: "avis"
    format: |
      ### Quick Consult — {topic}
      **Question** : {question}
      **Avis** :
      - {icon} {agent1} : {avis} (confiance: {level})
      - {icon} {agent2} : {avis} (confiance: {level})
      **Conclusion** : {synthèse + décision}
  
  example:
    initiator: "dev/Amelia"
    question: "Redis pour le cache session ou un Map en mémoire pour le MVP ?"
    invited: ["architect/Winston", "qa/Quinn"]
```

### Debate (2-4 agents, 5-10 échanges)

```yaml
debate:
  description: "Sujet controversé nécessitant des perspectives opposées"
  duration: "5-10 messages, time-boxed"
  format:
    1_framing: "Le facilitateur (SOG) cadre le débat : topic + options + contraintes"
    2_positions: "Chaque agent expose sa position (1 message)"
    3_challenges: "Chaque agent challenge les autres (Steelman + contre-argument)"
    4_synthese: "Facilitateur synthétise + vote si nécessaire"
  
  # Techniques PCE utilisables
  pce_modes_available:
    - "red_blue_team"          # si 2 options claires
    - "adversarial_review"     # si un output est contesté
    - "six_hats"               # pour exploration large (rare en huddle)
  
  deliverable:
    type: "décision ou recommandation"
    format: |
      ### Huddle Debate — {topic}
      **Options débattues** : {options}
      **Positions** :
      | Agent | Position | Arguments clés | Confiance |
      |-------|---------|---------------|-----------|
      | {icon} {agent} | {option} | {arguments} | {1-5} |
      
      **Points d'accord** : {points communs}
      **Points de désaccord** : {désaccords restants}
      **Décision** : {option retenue} — {justification}
      **Risques acceptés** : {risques identifiés mais acceptés}
```

### Review (2-3 agents, 5-8 échanges)

```yaml
review:
  description: "Revue collaborative d'un artefact ou d'une décision"
  duration: "5-8 messages"
  format:
    1_presentation: "Le producteur présente l'artefact + contexte"
    2_review: "Chaque revieweur examine et produit des findings"
    3_discussion: "Discussion sur les findings critiques"
    4_verdict: "Verdict collectif avec trust score"
  
  deliverable:
    type: "review_report"
    format: |
      ### Huddle Review — {artifact}
      **Producteur** : {icon} {agent}
      **Revieweurs** : {icons} {agents}
      
      **Findings** :
      - ✅ {confirmed points}
      - ⚠️ {concerns}
      - 🔴 {issues}
      
      **Trust Score** : {composite}/100
      **Verdict** : {approve | approve_with_notes | challenge}
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/team.svg" width="28" height="28" alt=""> Sélection des Participants

```yaml
participant_selection:
  process:
    1: "Identifier le domaine/sujet du huddle"
    2: "Consulter ARG pour les agents avec expertise pertinente"
    3: "Consulter AMN registry pour la disponibilité (status != offline)"
    4: "Appliquer les critères de sélection"
    5: "Inviter les agents sélectionnés"
  
  criteria:
    # Obligatoire
    expertise_match: "Au moins 1 agent expert du domaine principal"
    diversity: "Au moins 1 agent avec une perspective différente"
    availability: "Agents en status online ou idle"
    
    # Recommandé
    synergy: "Privilégier les paires avec synergy_score élevé (ARG)"
    challenge_potential: "Inclure au moins 1 agent avec historique de 'challenge'"
    
    # Contraintes
    max_participants: 4              # au-delà → party mode complet
    min_participants: 2
    exclude_overloaded: true         # pas d'agents en status busy avec queue > 0
  
  # Override utilisateur
  user_override:
    description: "L'utilisateur peut forcer/exclure des participants"
    command: "[HUDDLE topic WITH agent1,agent2] ou [HUDDLE topic WITHOUT agent3]"
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/team.svg" width="28" height="28" alt=""> Lifecycle d'un Huddle

```yaml
lifecycle:
  # Phase 1 — Ouverture
  open:
    1: "Créer un huddle_id unique"
    2: "Réserver un canal message-bus : huddle-{huddle_id}"
    3: "Envoyer les invitations via AMN P2P"
    4: "Émettre événement ELSS : huddle_requested"
    5: "Démarrer le timer (time-box)"
    6: "Si auto-trigger : SOG cadre le sujet. Si user-trigger : user cadre"
  
  # Phase 2 — Discussion
  discuss:
    routing: "Messages P2P multidirectionnels via canal dédié"
    moderation:
      - "SOG observe les échanges (via ELSS)"
      - "Si divergence_score > 0.8 → SOG intervient pour recentrer"
      - "Si boucle circulaire détectée → SOG force la synthèse"
      - "Respecter le time-box : warning à 80%, force-close à 100%"
    hup_active: true   # agents appliquent HUP pendant le huddle
  
  # Phase 3 — Clôture
  close:
    1: "SOG (ou agent désigné) synthétise les échanges"
    2: "Produire le deliverable structuré"
    3: "Émettre événement ELSS : huddle_completed"
    4: "Mettre à jour ARG : relationships enrichies"
    5: "Si décision prise → émettre événement decision dans ELSS"
    6: "Fermer le canal message-bus"
    7: "Logger le résumé dans Grimoire_TRACE"

  # Time-box
  time_limits:
    quick_consult: "5 messages ou 3 minutes de travail agent"
    debate: "10 messages ou 7 minutes de travail agent"
    review: "8 messages ou 5 minutes de travail agent"
    hard_stop: "Au time-box expiré : SOG force la synthèse avec ce qui est disponible"
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/wrench.svg" width="28" height="28" alt=""> Commandes Utilisateur

```markdown
## Commandes Huddle

- `[HUDDLE topic]` — Lancer un huddle auto-sélection des agents
- `[HUDDLE topic WITH agent1,agent2]` — Huddle avec participants spécifiés
- `[HUDDLE-STATUS]` — Huddles en cours et récents
- `[HUDDLE-HISTORY]` — Historique des huddles et leurs livrables
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/integration.svg" width="28" height="28" alt=""> Intégration avec le Party Mode (PCE)

```yaml
huddle_vs_party:
  # Escalade huddle → party mode
  escalation:
    condition: "Huddle debate ne converge pas après time-box"
    action: |
      Le SOG propose à l'utilisateur :
      "Le huddle n'a pas convergé sur {topic}. 
       Voulez-vous lancer un Party Mode complet avec plus d'agents ?
       Points non résolus : {liste}"
  
  # Différences clés
  comparison:
    | Aspect | Huddle | Party Mode |
    |--------|--------|------------|
    | Agents | 2-4 ciblés | 4-8 divers |
    | Durée | 3-10 échanges | 10-30+ échanges |
    | Trigger | Automatique ou ciblé | Explicite par l'utilisateur |
    | Format | Quick-consult/debate/review | Discussion libre / Red-Blue / Six Hats |
    | Livrable | Avis/décision/review | Brainstorm complet + épics |
    | PCE | Optionnel | Systématique |
    | Overhead | Minimal | Significatif |
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/integration.svg" width="28" height="28" alt=""> Intégration Grimoire Trace

```
[timestamp] [orchestrator]   [SHP:trigger]    auto | condition=cvtl_challenge | topic="auth pattern"
[timestamp] [orchestrator]   [SHP:select]     invited=[dev,architect,qa] | type=debate
[timestamp] [orchestrator]   [SHP:open]       huddle_id=h-003 | channel=huddle-h-003
[timestamp] [dev/Amelia]     [SHP:message]    huddle=h-003 | msg=1/10 | type="position"
[timestamp] [architect]      [SHP:message]    huddle=h-003 | msg=2/10 | type="challenge"
[timestamp] [orchestrator]   [SHP:close]      huddle=h-003 | verdict="JWT approved" | duration=4min
[timestamp] [orchestrator]   [SHP:escalate]   huddle=h-003 → party-mode | reason="no convergence"
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Référence Croisée

- Agent Mesh Network : [framework/agent-mesh-network.md](agent-mesh-network.md) (BM-55) — découverte et P2P
- Agent Relationship Graph : [framework/agent-relationship-graph.md](agent-relationship-graph.md) (BM-57) — sélection
- Productive Conflict Engine : [framework/productive-conflict-engine.md](productive-conflict-engine.md) (BM-54) — techniques débat
- Event Log : [framework/event-log-shared-state.md](event-log-shared-state.md) (BM-59) — observabilité
- Orchestrator Gateway : [framework/orchestrator-gateway.md](orchestrator-gateway.md) (BM-53) — supervision
- Cross-Validation : [framework/cross-validation-trust.md](cross-validation-trust.md) (BM-52) — trigger CVTL


*BM-56 Selective Huddle Protocol | framework/selective-huddle-protocol.md*
