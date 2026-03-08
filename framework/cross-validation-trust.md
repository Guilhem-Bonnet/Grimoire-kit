<p align="right"><a href="../README.md">README</a> · <a href="../docs">Docs</a></p>

# <img src="../docs/assets/icons/seal.svg" width="32" height="32" alt=""> Cross-Validation & Trust Layer (CVTL) — Vérification Croisée Multi-Agents

> **BM-52** — Protocole de vérification croisée et scoring de confiance pour les outputs critiques.
>
> **Problème résolu** : Un seul agent peut avoir des biais, des angles morts, ou des erreurs
> de raisonnement. La cross-validation soumet les outputs critiques à un regard indépendant.
>
> **Principe** : Les livrables critiques sont vérifiés par un second agent avant d'être
> marqués comme fiables. Chaque output porte un score de confiance composite visible.

<img src="../docs/assets/divider.svg" width="100%" alt="">


## <img src="../docs/assets/icons/seal.svg" width="28" height="28" alt=""> Quand Déclencher la Cross-Validation

### Déclencheurs Automatiques

| Condition | Action |
|-----------|--------|
| Output flaggé JAUNE par HUP | Cross-validation recommandée |
| Décision d'architecture (ADR) | Cross-validation obligatoire |
| Changement de stack/technologie | Cross-validation obligatoire |
| Story > 8 story points | Cross-validation recommandée |
| Output contredit une décision existante | Cross-validation obligatoire |
| Utilisateur demande explicitement | Cross-validation immédiate |

### Déclencheurs Manuels

L'utilisateur ou l'orchestrateur peut demander une cross-validation à tout moment :
```
[CROSS-VALIDATE] ou "vérifie ça" ou "second avis"
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/seal.svg" width="28" height="28" alt=""> Sélection du Validateur

### Règle de Base

Le validateur doit être un agent **différent** de celui qui a produit l'output, avec une expertise **pertinente** :

```yaml
validator_selection:
  rules:
    - name: "independence"
      rule: "Le validateur NE PEUT PAS être le même agent que le producteur"
    
    - name: "expertise_match"
      rule: "Le validateur doit avoir une expertise pertinente pour le domaine de l'output"
      examples:
        - output_type: "code"         → validator: "qa" ou "architect"
        - output_type: "architecture" → validator: "dev" (faisabilité) ou "pm" (alignement produit)
        - output_type: "PRD"          → validator: "architect" (faisabilité) ou "analyst" (marché)
        - output_type: "tests"        → validator: "dev" (couverture réelle)
        - output_type: "story"        → validator: "dev" (implémentabilité) ou "qa" (testabilité)
    
    - name: "perspective_diversity"
      rule: "Privilégier un validateur qui apporte un ANGLE DIFFÉRENT"
      examples:
        - "architect produit ADR → dev valide la faisabilité d'implémentation"
        - "dev produit code → qa valide la testabilité et couverture"
        - "pm produit PRD → architect valide la faisabilité technique"
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/seal.svg" width="28" height="28" alt=""> Protocole de Cross-Validation

### Step 1 — Soumission

L'orchestrateur envoie l'output au validateur avec un mandat clair :

```yaml
cross_validation_request:
  id: "cv-{producer_agent}-{timestamp}"
  producer: "{agent_id}/{agent_name}"
  validator: "{validator_id}/{validator_name}"
  
  output_to_validate:
    type: "code | architecture | prd | story | tests | decision"
    content_reference: "chemin vers l'artefact OU inline pour les courts"
    original_task: "description de la tâche originale"
  
  validation_mandate:
    focus_areas:
      - "Cohérence avec les contraintes du projet"
      - "Faisabilité d'implémentation"
      - "Faits vérifiables vs hypothèses"
      - "Risques non identifiés"
    
    NOT_in_scope:
      - "Style ou préférences personnelles"
      - "Optimisations prématurées"
      - "Refactoring non demandé"
```

### Step 2 — Validation

Le validateur produit un rapport structuré :

```yaml
cross_validation_report:
  id: "cv-{id}"
  validator: "{validator_id}/{validator_name}"
  timestamp: "{iso_datetime}"
  
  verdict: approve | approve_with_notes | challenge | reject
  
  # Score de confiance composite
  trust_score:
    factual_accuracy: 0-100    # les faits sont-ils vérifiables ?
    logical_coherence: 0-100   # le raisonnement tient-il ?
    constraint_alignment: 0-100 # cohérent avec les contraintes projet ?
    implementation_feasibility: 0-100  # réalisable techniquement ?
    composite: 0-100           # moyenne pondérée
  
  # Findings
  findings:
    confirmed:
      - "point 1 validé — source/preuve"
      - "point 2 validé — cohérent avec ADR-X"
    
    concerns:
      - finding: "Le timeout de 30s est arbitraire"
        severity: low | medium | high | critical
        suggestion: "Benchmark avec charge réelle, 30s semble conservateur"
    
    issues:
      - finding: "La story référence un endpoint qui n'existe pas"
        severity: critical
        suggestion: "Créer l'endpoint ou mettre à jour la story"
    
    hypotheses_detected:
      - "Hypothèse : <10k users simultanés (non vérifié)"
      - "Hypothèse : PostgreSQL gère la charge (pas de benchmark)"
  
  # Résumé en 1 ligne
  summary: "Output solide, 2 concerns mineures, 1 hypothèse à vérifier"
```

### Step 3 — Résolution

```yaml
resolution_rules:
  approve:
    action: "Output marqué ✅ VALIDÉ + trust_score affiché"
    trace: "[CVTL:approved] composite={score}"
  
  approve_with_notes:
    action: "Output livré + notes de concerns ajoutées en annexe"
    trace: "[CVTL:approved-with-notes] concerns={count}"
  
  challenge:
    action: "Retour au producteur avec les findings → correction → re-validation"
    max_iterations: 2  # après 2 allers-retours → escalade à l'utilisateur
    trace: "[CVTL:challenged] issues={count}"
  
  reject:
    action: "Output bloqué → escalade à l'orchestrateur → présentation à l'utilisateur"
    trace: "[CVTL:rejected] reason={summary}"
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/chart.svg" width="28" height="28" alt=""> Trust Score — Score de Confiance Composite

### Calcul

```
composite = (factual_accuracy × 0.35) 
          + (logical_coherence × 0.25) 
          + (constraint_alignment × 0.25) 
          + (implementation_feasibility × 0.15)
```

### Affichage

Chaque output validé affiche son trust score :

```markdown
---
🛡️ Trust Score: 87/100 — Validé par {validator_icon} {validator_name}
   Exactitude: 92 · Cohérence: 85 · Alignement: 88 · Faisabilité: 78
   Note: 1 hypothèse à vérifier (charge utilisateur)
---
```

### Seuils

| Score | Signification | Action |
|-------|--------------|--------|
| **90-100** | Haute confiance | Livrer directement |
| **70-89** | Confiance solide | Livrer + notes visibles |
| **50-69** | Confiance modérée | Review utilisateur recommandé |
| **<50** | Confiance basse | Blocage → escalade utilisateur |

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> Adversarial Review Pattern

Pour les décisions les plus critiques, activer le mode **adversarial** :

```yaml
adversarial_review:
  trigger: "Décision avec impact irréversible OU coût > estimation XL"
  
  protocol:
    # Step 1 — L'avocat du diable
    - role: "devil_advocate"
      agent: "{agent avec expertise opposée}"
      mandate: |
        Ton UNIQUE objectif : trouver TOUTES les raisons pour lesquelles 
        cette approche va échouer. Sois impitoyable mais intellectuellement honnête.
        Pas de critique pour la critique — chaque point doit être substantiel.
    
    # Step 2 — La défense
    - role: "defender"
      agent: "{agent producteur original}"
      mandate: |
        Réponds à CHAQUE point de l'avocat du diable.
        Pour chaque critique : réfute avec preuve OU accepte et propose une mitigation.
    
    # Step 3 — Le verdict
    - role: "judge"
      agent: "orchestrator"
      mandate: |
        Synthétise le débat. Décision finale basée sur :
        1. Critiques non réfutées → risques à accepter ou à mitiger
        2. Mitigations proposées → suffisantes ?
        3. Verdict : GO / NO-GO / GO-WITH-CONDITIONS
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/moon.svg" width="28" height="28" alt=""> Rapport d'Intégrité de Session

À la fin d'une session ou d'un workflow, l'orchestrateur peut générer un rapport :

```markdown
# 🛡️ Rapport d'Intégrité — Session {date}

## Résumé
- Outputs produits : {count}
- Cross-validations effectuées : {cv_count}
- Trust Score moyen : {avg_score}/100

## Détail par Output
| Output | Producteur | Validateur | Trust Score | Verdict |
|--------|-----------|-----------|-------------|---------|
| ADR-042 | 🏗️ Winston | 💻 Amelia | 91/100 | ✅ Approuvé |
| US-042 impl | 💻 Amelia | 🧪 Quinn | 85/100 | ✅ Avec notes |
| Config prod | 💻 Amelia | 🏗️ Winston | 62/100 | ⚠️ Challengé |

## Hypothèses Non Vérifiées
1. Charge <10k users (source: ADR-042, impact: architecture scaling)
2. PostgreSQL handle la charge (source: US-042, impact: performance)

## Faits Confirmés
1. JWT stateless compatible avec le stack actuel (vérifié: package.json)
2. Tests unitaires couvrent 95% du module auth (vérifié: coverage report)
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/boomerang.svg" width="28" height="28" alt=""> Intégration dans Subagent Orchestration

Nouvelle stratégie de merge dans `subagent-orchestration.md` :

```yaml
merge:
  strategy: "cross-validate"    # NOUVELLE STRATÉGIE
  primary_agent: "dev"          # agent qui produit
  validator_agent: "qa"         # agent qui valide
  trust_threshold: 70           # score minimum pour accepter
  on_below_threshold: "escalate_to_user"  # ou "retry" ou "challenge"
  save_to: "_grimoire-output/implementation-artifacts/{output}-validated.md"
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/integration.svg" width="28" height="28" alt=""> Intégration Grimoire Trace

```
[timestamp] [orchestrator]    [CVTL:requested]   producer=dev/Amelia | validator=qa/Quinn | type=code
[timestamp] [qa/Quinn]        [CVTL:validating]  cv-id=cv-dev-001 | focus=testability+coverage
[timestamp] [qa/Quinn]        [CVTL:verdict]     cv-id=cv-dev-001 | verdict=approve_with_notes | score=85
[timestamp] [orchestrator]    [CVTL:resolved]    cv-id=cv-dev-001 | action=deliver_with_notes
[timestamp] [orchestrator]    [CVTL:adversarial] trigger=irréversible | devil=architect | defender=dev
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Références Croisées

- Orchestrator Gateway : [framework/orchestrator-gateway.md](orchestrator-gateway.md) (BM-53) — déclenche les validations
- Honest Uncertainty : [framework/honest-uncertainty-protocol.md](honest-uncertainty-protocol.md) (BM-50) — confiance des validateurs
- Agent Relationship Graph : [framework/agent-relationship-graph.md](agent-relationship-graph.md) (BM-57) — trust scores alimentent le graphe
- Selective Huddle : [framework/selective-huddle-protocol.md](selective-huddle-protocol.md) (BM-56) — huddle déclenché si trust < seuil
- Event Log : [framework/event-log-shared-state.md](event-log-shared-state.md) (BM-59) — événements CVTL persistés
- Hybrid Parallelism : [framework/hybrid-parallelism-engine.md](hybrid-parallelism-engine.md) (BM-58) — mode cross-validate dans DAG


*BM-52 Cross-Validation & Trust Layer | framework/cross-validation-trust.md*
