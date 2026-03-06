# Productive Conflict Engine (PCE) — Débats Structurés pour Party Mode

> **BM-54** — Protocole de conflit productif pour transformer le party mode en machine à débat.
>
> **Problème résolu** : Le party mode tend vers le consensus mou. Les agents se complimentent,
> acquiescent, et construisent sur les mêmes idées sans jamais les challenger réellement.
> Le résultat : des discussions agréables mais peu productives.
>
> **Principe** : Le désaccord structuré produit de meilleures décisions que le consensus facile.
> Des techniques de débat formalisées forcent les perspectives divergentes et l'analyse critique.

---

## Modes de Discussion

Le facilitateur choisit le mode de discussion selon le sujet et l'objectif :

### Mode 1 — Free Discussion (existant, amélioré)

```yaml
free_discussion:
  description: "Discussion libre avec injection de divergence"
  agents: 2-4 sélectionnés par pertinence
  
  # NOUVEAU : Divergence Injection
  divergence_monitor:
    check_after_every: 2  # rounds de discussion
    metric: "divergence_score"
    
    # Si tout le monde est d'accord depuis 2 rounds → injecter un challenge
    on_low_divergence:
      threshold: 0.3  # score de 0 (consensus total) à 1 (désaccord total)
      action: |
        FORCER un challenge :
        1. Sélectionner l'agent avec l'expertise la plus éloignée du sujet
        2. Lui assigner le rôle temporaire de Devil's Advocate
        3. Prompt : "Tu as entendu le consensus. Ton job : trouver 
           la faille fatale dans ce raisonnement. Sois constructif mais impitoyable."
```

### Mode 2 — Red Team / Blue Team

```yaml
red_blue_team:
  description: "Deux camps opposés défendent des positions contradictoires"
  trigger: "Décision binaire ou choix entre 2 options majeures"
  
  setup:
    # Sélection des camps
    blue_team:
      position: "{option A}"
      agents: ["{2-3 agents qui défendent A}"]
      brief: "Défendez cette position avec des arguments factuels et techniques"
    
    red_team:
      position: "{option B}"
      agents: ["{2-3 agents qui défendent B}"]
      brief: "Défendez cette position avec des arguments factuels et techniques"
    
    judge:
      agent: "orchestrator | utilisateur"
      role: "Évaluer la force des arguments, pas la sympathie des agents"
  
  rules:
    # Steelman obligatoire
    - name: "steelman_first"
      rule: |
        Avant de contre-argumenter, chaque camp DOIT reformuler 
        l'argument adverse dans sa VERSION LA PLUS FORTE.
        "Si je comprends bien, votre meilleur argument est [reformulation généreuse]."
        Seulement ensuite : "Cependant, voici pourquoi nous pensons autrement..."
    
    # Pas d'ad hominem
    - name: "attack_ideas_not_agents"
      rule: "Critiquer les IDÉES, jamais les agents ni leur expertise"
    
    # Preuves obligatoires
    - name: "evidence_required"  
      rule: "Chaque affirmation doit citer une source : fichier projet, ADR, doc, ou logique explicite"
    
    # Droit de concession
    - name: "concession_allowed"
      rule: |
        Un camp peut concéder un point SPÉCIFIQUE sans perdre sa position globale.
        "Nous concédons que [point] est un risque réel. Notre mitigation : [solution]."
  
  rounds:
    max: 3  # 3 rounds max pour éviter les boucles
    structure:
      round_1: "Chaque camp présente sa position (3 arguments max)"
      round_2: "Steelman + contre-arguments"
      round_3: "Synthèse et derniers arguments"
    
    after_rounds:
      action: |
        Le juge (orchestrateur ou utilisateur) synthétise :
        1. Points forts de chaque camp
        2. Points concédés
        3. Arguments non résolus
        4. Recommandation finale ou vote
```

### Mode 3 — Six Thinking Hats (De Bono adapté)

```yaml
six_hats:
  description: "Exploration systématique d'un sujet depuis 6 angles différents"
  trigger: "Sujet complexe nécessitant une analyse multi-perspective"
  
  hats:
    white:
      focus: "Faits et données uniquement"
      prompt: "Que savons-nous FACTUELLEMENT ? Que nous manque-t-il comme données ?"
      assigned_to: "Agent le plus analytique (analyst, architect)"
    
    red:
      focus: "Intuition et émotions"
      prompt: "Quel est ton GUT FEELING ? Qu'est-ce qui te met mal à l'aise dans cette approche ?"
      assigned_to: "Agent le plus empathique (ux-designer, pm)"
    
    black:
      focus: "Critique et risques"
      prompt: "Qu'est-ce qui peut ÉCHOUER ? Risques, failles, faiblesses ?"
      assigned_to: "Agent le plus critique (qa, architect)"
    
    yellow:
      focus: "Valeur et avantages"
      prompt: "Qu'est-ce qui FONCTIONNE BIEN ? Quelle est la valeur de cette approche ?"
      assigned_to: "Agent le plus optimiste (pm, brainstorming-coach)"
    
    green:
      focus: "Créativité et alternatives"
      prompt: "Quelles ALTERNATIVES n'avons-nous pas explorées ? Et si on faisait totalement différemment ?"
      assigned_to: "Agent le plus créatif (brainstorming-coach, ux-designer)"
    
    blue:
      focus: "Processus et synthèse"
      prompt: "Synthétise les 5 perspectives. Quelle est la décision la plus robuste ?"
      assigned_to: "Orchestrateur"
  
  execution:
    order: [white, red, black, yellow, green, blue]
    per_hat_time: "1-2 responses par agent assigné"
    blue_hat_summary: "Toujours en dernier — synthèse décisionnelle"
```

### Mode 4 — Adversarial Technical Review

```yaml
adversarial_review_party:
  description: "Un agent défend, un attaque, les autres observent et jugent"
  trigger: "Review d'architecture, ADR, ou décision technique critique"
  
  roles:
    defender:
      agent: "{agent qui a produit l'output}"
      mandate: "Défendre ton approche avec des preuves techniques"
    
    attacker:
      agent: "{agent le plus pertinent pour critiquer}"
      mandate: |
        Trouver TOUTES les failles :
        - Scalabilité ? Sécurité ? Performance ?
        - Single point of failure ?
        - Cas limites non gérés ?
        - Hypothèses non vérifiées ?
        IMPORTANT : Chaque critique DOIT être accompagnée d'une alternative.
    
    jury:
      agents: ["{2-3 agents observateurs}"]
      mandate: |
        Observer le débat. À la fin :
        - Voter : défense gagne / attaque gagne / compromis
        - Identifier les meilleurs arguments de chaque côté
        - Proposer la synthèse optimale
```

---

## Divergence Score — Mesurer la Qualité du Débat

### Calcul

Le facilitateur évalue après chaque round :

```yaml
divergence_score:
  # 0.0 = consensus total, aucun désaccord
  # 0.5 = désaccords constructifs, perspectives variées  
  # 1.0 = désaccord total, aucun point commun
  
  factors:
    perspective_variety: 0-1    # combien de perspectives différentes ?
    challenge_depth: 0-1        # les challenges sont-ils substantiels ?
    evidence_quality: 0-1       # les arguments sont-ils étayés ?
    new_ideas_generated: 0-1    # de nouvelles idées ont-elles émergé ?
    assumptions_questioned: 0-1 # des hypothèses ont-elles été remises en cause ?
  
  composite: "moyenne des 5 facteurs"
  
  interpretation:
    0.0-0.2: "🔴 Consensus mou — injecter divergence"
    0.2-0.4: "🟡 Légère divergence — encourager les challenges"
    0.4-0.7: "🟢 Zone productive — discussion de qualité"
    0.7-0.9: "🟡 Forte divergence — guider vers la synthèse"
    0.9-1.0: "🔴 Impasse — intervention du facilitateur"
```

### Actions du Facilitateur

```yaml
facilitator_actions:
  on_low_divergence:  # < 0.2
    - "Assignation Devil's Advocate forcée"
    - "Question provocatrice : 'Et si on prenait l'approche EXACTEMENT opposée ?'"
    - "Injection d'un agent avec perspective éloignée"
  
  on_high_divergence:  # > 0.7
    - "Résumé des points d'accord"
    - "Identification des désaccords factuels vs préférences"
    - "Demande de Steelman croisé"
    - "Si > 0.9 après Steelman → vote formel"
  
  on_circular_discussion:
    detection: "Mêmes arguments répétés >2 fois"
    action:
      - "STOP — Le facilitateur résume l'état du débat"
      - "Identifier les 2-3 questions clés non résolues"
      - "Proposer : vote | escalade utilisateur | time-box et décision par défaut"
```

---

## Mécanisme de Vote Structuré

Pour les décisions où le débat ne converge pas :

```yaml
vote_protocol:
  trigger: "facilitateur déclare un vote OU user demande"
  
  format:
    type: ranked_choice | simple_majority | weighted
    
    # Chaque agent vote
    vote_card:
      agent: "{agent_id}"
      choice: "{option choisie}"
      confidence: 1-5  # 1=pas convaincu, 5=totalement sûr
      rationale: "En 1 phrase, pourquoi ce choix"
      concessions: "Ce que j'accepterais comme compromis"
    
    # Pondération optionnelle
    weighting:
      by_expertise: true  # vote de l'agent expert pèse plus sur son domaine
      by_trust_history: false  # optionnel : historique de fiabilité
  
  result_display: |
    ## 🗳️ Résultat du Vote — {sujet}
    
    | Agent | Choix | Confiance | Rationale |
    |-------|-------|-----------|-----------|
    | {icon} {name} | {option} | {'⭐' × confidence} | {rationale} |
    | ... | ... | ... | ... |
    
    **Résultat** : {option gagnante} ({votes}/{total})
    **Consensus** : {faible|modéré|fort} (écart-type confiance)
    **Compromis proposés** : {concessions mentionnées}
    
    📌 {user_name}, cette recommandation est basée sur le débat. 
       Tu as le dernier mot : [Accepter] · [Modifier] · [Relancer le débat]
```

---

## Rôles Dynamiques — Rotation Automatique

```yaml
dynamic_roles:
  # Le facilitateur assigne des rôles qui changent à chaque round
  role_rotation:
    devil_advocate:
      assignment: "rotation — chaque agent joue le rôle au moins 1 fois sur 4 rounds"
      rule: "Jamais le même agent Devil's Advocate 2 rounds d'affilée"
    
    synthesizer:
      assignment: "agent qui a le moins parlé dans le round précédent"
      rule: "Résumer le round en 3 bullets avant le round suivant"
    
    evidence_checker:
      assignment: "agent le plus analytique disponible"
      rule: "Vérifier que chaque affirmation forte est étayée"
    
    provocateur:
      assignment: "agent créatif ou avec perspective éloignée"
      rule: "Poser LA question que personne n'ose poser"

  # Annonce au début de chaque round
  round_announcement: |
    📢 **Round {n}**
    🔴 Devil's Advocate : {agent}
    📝 Synthétiseur : {agent}  
    🔍 Vérificateur : {agent}
    💥 Provocateur : {agent}
```

---

## Système de Réactions Inter-Agents

Les agents peuvent réagir aux interventions des autres :

```yaml
reaction_system:
  types:
    challenge:
      emoji: "⚔️"
      meaning: "Je conteste ce point directement"
      requires: "Contre-argument substantiel avec preuve"
    
    build:
      emoji: "🏗️"
      meaning: "Je construis sur cette idée"
      requires: "Extension ou application concrète"
    
    nuance:
      emoji: "🔍"
      meaning: "Vrai, mais avec une nuance importante"
      requires: "La nuance précise et son impact"
    
    question:
      emoji: "❓"
      meaning: "Cet argument repose sur une hypothèse non vérifiée"
      requires: "L'hypothèse identifiée + ce qu'il faudrait pour la vérifier"
    
    concede:
      emoji: "🤝"
      meaning: "Je concède ce point"
      requires: "Reconnaissance explicite + ajustement de position"
    
    wildcard:
      emoji: "🃏"
      meaning: "Et si on pensait à ça complètement différemment ?"
      requires: "Perspective orthogonale au débat en cours"
  
  # Format d'une réaction
  format: |
    {icon} **{Agent Name}** {reaction_emoji} sur le point de {Other Agent} :
    "{réaction substantielle}"
```

---

## Intégration dans le Party Mode Existant

### Modifications à `step-02-discussion-orchestration.md`

Ajouter après la section "Agent Selection Intelligence" :

```markdown
### Productive Conflict Engine (PCE)

**Mode Selection :**
Pour chaque nouveau sujet ou question de l'utilisateur, le facilitateur choisit :

1. Si discussion exploratoire → Mode Free Discussion (avec divergence monitoring)
2. Si choix binaire ou décision → Mode Red Team / Blue Team  
3. Si sujet complexe multi-facettes → Mode Six Thinking Hats
4. Si review technique → Mode Adversarial Technical Review

**Annonce du mode :**
"Pour cette discussion, on va utiliser le mode {mode_name} — voici comment ça marche : {1 ligne d'explication}"

**Divergence Monitoring :**
Après chaque 2 rounds, évaluer le divergence_score.
Si < 0.2 → injecter divergence
Si > 0.7 → guider vers synthèse
Si discussion circulaire → STOP + résumé + proposition
```

### Nouveau Exit Summary

À la fin du party mode, le facilitateur produit :

```markdown
## 📊 Synthèse du Party Mode

### Décisions prises
| # | Décision | Méthode | Confiance |
|---|----------|---------|-----------|
| 1 | {décision} | {consensus/vote/débat} | {haute/moyenne/basse} |

### Désaccords non résolus
| # | Sujet | Camps | Recommandation |
|---|-------|-------|---------------|
| 1 | {sujet} | {agent} vs {agent} | {vote/escalade/time-box} |

### Hypothèses à vérifier
- {hypothèse 1} — responsable : {agent}
- {hypothèse 2} — responsable : {agent}

### Meilleures idées émergées
1. {idée 1} — proposée par {agent}, renforcée par {agent}
2. {idée 2} — née du débat entre {agent} et {agent}

### Métriques du débat
- Divergence score moyen : {score}
- Rounds joués : {count}
- Modes utilisés : {modes}
- Agents les plus actifs : {top 3}
- Challenges substantiels : {count}
```

---

## Intégration BMAD Trace

```
[timestamp] [PCE]            [MODE:selected]      mode=red_blue_team | topic="{sujet}"
[timestamp] [PCE]            [DIVERGENCE:check]   score=0.15 | action=inject_devil_advocate
[timestamp] [PCE]            [ROLE:assigned]       devil_advocate=architect/Winston | round=2
[timestamp] [PCE]            [REACTION:logged]     from=dev/Amelia | type=challenge | to=architect/Winston
[timestamp] [PCE]            [STEELMAN:done]       by=dev/Amelia | for=architect/Winston | quality=strong
[timestamp] [PCE]            [VOTE:initiated]      options=["REST","GraphQL"] | voters=4
[timestamp] [PCE]            [VOTE:result]         winner="GraphQL" | margin=3-1 | consensus=moderate
[timestamp] [PCE]            [CIRCULAR:detected]   topic="{sujet}" | rounds_stuck=3 | action=summarize
[timestamp] [PCE]            [SUMMARY:generated]   decisions=3 | unresolved=1 | ideas=5
```

---

## Références Croisées

- Orchestrator Gateway : [framework/orchestrator-gateway.md](orchestrator-gateway.md) (BM-53) — mode party du SOG
- Selective Huddle : [framework/selective-huddle-protocol.md](selective-huddle-protocol.md) (BM-56) — mini-débats avec techniques PCE
- Agent Relationship Graph : [framework/agent-relationship-graph.md](agent-relationship-graph.md) (BM-57) — synergies/conflits enrichissent le graphe
- Agent Mesh Network : [framework/agent-mesh-network.md](agent-mesh-network.md) (BM-55) — P2P challenges et réactions
- Event Log : [framework/event-log-shared-state.md](event-log-shared-state.md) (BM-59) — événements PCE persistés
- Cross-Validation : [framework/cross-validation-trust.md](cross-validation-trust.md) (BM-52) — trust scoring post-débat

---

*BM-54 Productive Conflict Engine | framework/productive-conflict-engine.md*
