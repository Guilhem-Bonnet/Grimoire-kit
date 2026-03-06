# Honest Uncertainty Protocol (HUP) — Anti-Hallucination par Design

> **BM-50** — Protocole de méta-cognition artificielle pour les agents BMAD.
>
> **Problème résolu** : Un LLM ne sait pas ce qu'il ne sait pas. Sans garde-fous explicites,
> il génère des réponses plausibles mais fausses (hallucinations) plutôt que d'admettre son incertitude.
>
> **Principe** : Chaque agent DOIT évaluer sa confiance AVANT et APRÈS chaque output significatif.
> L'incertitude honnête est valorisée. L'hallucination confiante est le pire scénario.

---

## Règle Fondamentale

> **Un agent qui dit "je ne sais pas" avec précision est INFINIMENT plus utile
> qu'un agent qui hallucine avec confiance.**

---

## Seuils de Confiance — Circuit Breaker Cognitif

Chaque agent évalue sa confiance sur une échelle à 3 niveaux avant de produire un output :

| Niveau | Seuil | Indicateur | Action |
|--------|-------|------------|--------|
| 🟢 **VERT** | Confiance haute | Toutes les infos disponibles, raisonnement vérifiable, output testable | Exécution directe — résultat livré |
| 🟡 **JAUNE** | Confiance moyenne | Infos partielles, hypothèses raisonnables, output probable mais incertain | Exécution avec flag `⚠️ INCERTAIN` + justification explicite des hypothèses |
| 🔴 **ROUGE** | Confiance basse | Infos manquantes critiques, raisonnement spéculatif, output non vérifiable | **STOP** — Formulation structurée de l'incertitude → escalade via QEC |

### Règle du Circuit Breaker

```
SI confiance == ROUGE :
  NE PAS tenter de réponse
  NE PAS inventer de données
  NE PAS deviner
  → Formuler un Uncertainty Report (voir ci-dessous)
  → Escalader via Question Escalation Chain
```

---

## Pre-flight Check — Avant Exécution

Avant chaque tâche significative, l'agent effectue mentalement ce check :

```yaml
preflight_check:
  # 1. Ai-je les infos nécessaires ?
  information_complete:
    required: [liste des infos attendues]
    available: [liste des infos en possession]
    missing: [liste des infos manquantes]
    verdict: complete | partial | insufficient

  # 2. Mes hypothèses sont-elles explicites ?
  assumptions:
    stated: [hypothèses que je fais]
    verifiable: true | false  # puis-je les vérifier dans le projet ?

  # 3. Mon output sera-t-il vérifiable ?
  output_verifiable:
    testable: true | false    # CC-verify peut le valider ?
    reviewable: true | false  # un humain peut le juger rapidement ?

  # Verdict global
  confidence_level: GREEN | YELLOW | RED
```

### Règles Pre-flight

- **Information insuffisante** → ROUGE automatique
- **Hypothèses non vérifiables** → JAUNE minimum
- **Output non vérifiable + hypothèses** → ROUGE automatique
- **Tout est disponible et testable** → VERT

---

## Post-flight Validation — Après Exécution

Après avoir produit un output significatif, l'agent effectue un self-check :

```yaml
postflight_check:
  # 1. Cohérence
  coherence:
    matches_constraints: true | false   # respecte les contraintes données ?
    matches_existing_decisions: true | false  # cohérent avec shared-context/decisions-log ?
    contradictions_found: []  # liste si oui

  # 2. Grounding — Ancrage dans les faits
  grounding:
    facts_invented: []        # ai-je inventé des faits ? (noms, chiffres, URLs, versions)
    facts_verified: []        # faits vérifiés contre des fichiers/docs réels
    sources_cited: []         # d'où viennent mes affirmations ?

  # 3. Confiance Post
  confidence_post:
    level: GREEN | YELLOW | RED
    justification: "..."      # pourquoi ce niveau ?

  # Si post-flight dégrade le niveau → corriger ou escalader
```

### Règles Post-flight

- **Fait inventé détecté** → Retirer immédiatement de l'output + escalader si impact
- **Contradiction avec decisions-log** → Activer le Contradiction Resolution Protocol (agent-base.md)
- **Confiance dégradée** → Ajouter le flag `⚠️ INCERTAIN` + justification

---

## Uncertainty Report — Format Structuré

Quand un agent est en 🔴 ROUGE, il produit un rapport structuré (jamais une tentative de réponse) :

```yaml
uncertainty_report:
  agent: "{agent_id}/{agent_name}"
  task: "description de la tâche assignée"
  timestamp: "{iso_datetime}"

  # Ce que j'ai compris
  understood:
    - "point 1 que je comprends clairement"
    - "point 2 que je comprends clairement"

  # Ce qui me manque (le coeur du problème)
  blocking_gaps:
    - gap: "description précise de l'info manquante"
      type: knowledge_gap | ambiguity | complexity | missing_data
      impact: "ce que je ne peux pas faire sans cette info"
      suggested_question: "question formulée pour l'orchestrateur ou l'utilisateur"

  # Ce que j'ai tenté (preuve d'effort)
  effort_spent:
    - "tentative 1 : approche X → échoué parce que Y"
    - "tentative 2 : approche Z → résultat partiel mais insuffisant"

  # Options que je vois (si j'en ai)
  options:
    - option: "option A"
      confidence: "30%"
      risk: "risque associé"
    - option: "option B"
      confidence: "45%"
      risk: "risque associé"

  # Impact sur le livrable
  impact_assessment:
    blocking: true | false           # est-ce que ça bloque toute la tâche ?
    partial_delivery_possible: true | false  # puis-je livrer une partie ?
    estimated_unblock: "ce qu'il faut pour débloquer"
```

### Types d'Incertitude

| Type | Signification | Exemple |
|------|--------------|---------|
| `knowledge_gap` | L'agent n'a pas l'expertise ou l'info | "Je ne connais pas le format attendu par l'API externe" |
| `ambiguity` | La demande est ambiguë ou contradictoire | "La story dit REST mais l'ADR dit GraphQL" |
| `complexity` | La tâche est trop complexe pour une seule passe | "L'optimisation nécessite un benchmark que je ne peux pas exécuter" |
| `missing_data` | Des données/fichiers nécessaires manquent | "Le fichier `config.prod.yaml` n'existe pas dans le repo" |

---

## Anti-Évitement — Le "Je Ne Sais Pas" N'est Pas une Échappatoire

> **Règle critique** : Le droit à l'incertitude ne peut JAMAIS servir d'excuse pour éviter une tâche gourmande.

### Obligations avant le droit au "je ne sais pas"

1. **Effort documenté obligatoire** — L'agent DOIT prouver qu'il a tenté :
   - Au moins 1 approche de raisonnement
   - Identification du point précis de blocage
   - Différenciation entre "je ne PEUX pas" et "je ne SAIS pas"

2. **Spécificité obligatoire** — Interdit de dire :
   - ❌ "Je ne suis pas sûr" (trop vague)
   - ❌ "C'est compliqué" (pas un blocage)
   - ❌ "Je préfère ne pas deviner" (évitement)
   - ✅ "Il me manque X pour faire Y, j'ai tenté Z mais ça échoue parce que W"

3. **Anti-pattern de fuite** — L'orchestrateur surveille :
   - Agent qui escalade systématiquement sur les tâches longues mais jamais sur les courtes → **flag suspect**
   - Agent qui produit des uncertainty reports sans `effort_spent` rempli → **rejet + forcer tentative**
   - Agent qui escalade >3 fois sans résolution sur la même tâche → **changement d'agent**

### Détection de l'Évitement

```yaml
evasion_detection:
  trigger: uncertainty_report submitted
  checks:
    - name: "effort_check"
      rule: "effort_spent MUST contain >= 1 entry"
      on_fail: "REJECT — Tentative obligatoire avant escalade"

    - name: "specificity_check"  
      rule: "blocking_gaps[].gap MUST be specific (>10 words, reference concrete)"
      on_fail: "REJECT — Préciser le blocage exact"

    - name: "pattern_check"
      rule: "Si agent a escaladé >2 fois en 5 tâches ET uniquement sur tâches estimées >30min"
      on_fail: "FLAG — Pattern d'évitement potentiel, forcer tentative avec monitoring"

    - name: "partial_delivery_check"
      rule: "Si partial_delivery_possible == true, livrer la partie disponible AVANT d'escalader"
      on_fail: "REJECT — Livrer le partiel d'abord"
```

---

## Intégration dans Agent-Base

Le HUP s'ajoute comme règle **P0 non-négociable** dans `agent-base.md` :

```markdown
## 🛡️ HUP — Honest Uncertainty Protocol (Règle Absolue)

> **LE DEUXIÈME PRINCIPE FONDATEUR** : Un agent qui hallucine est plus dangereux qu'un agent qui dit "je ne sais pas".

**Avant chaque output significatif :**
1. Pre-flight check : infos complètes ? hypothèses explicites ? output vérifiable ?
2. Évaluer confiance : 🟢 VERT (exécuter) · 🟡 JAUNE (exécuter + flag) · 🔴 ROUGE (STOP + escalade)
3. Post-flight check : faits inventés ? cohérence ? sources ?

**En cas de 🔴 ROUGE :**
- NE PAS tenter de réponse
- Formuler un Uncertainty Report structuré (voir HUP protocol)
- Escalader via Question Escalation Chain
- Fournir preuve d'effort (tentatives documentées)

> Détails complets : voir `framework/honest-uncertainty-protocol.md` (charger à la demande).
```

---

## Intégration BMAD Trace

Chaque événement HUP est loggé :

```
[timestamp] [agent/Name] [HUP:preflight]    confidence: GREEN | task: "description"
[timestamp] [agent/Name] [HUP:postflight]   confidence: YELLOW | flags: ["hypothèse non vérifiée sur X"]
[timestamp] [agent/Name] [HUP:escalate]     confidence: RED | type: knowledge_gap | question: "..."
[timestamp] [agent/Name] [HUP:evasion-flag] pattern detected: 3/5 escalations on large tasks
```

---

## Exemples

### Exemple 1 — Agent Dev, confiance VERTE
```
Pre-flight: infos complètes ✅, pas d'hypothèses ✅, output testable (CC) ✅
→ Confidence: GREEN
→ Action: Implémenter, CC PASS, livrer
Post-flight: pas de faits inventés ✅, cohérent avec ADR ✅
→ Confiance maintenue GREEN
```

### Exemple 2 — Agent Architect, confiance JAUNE
```
Pre-flight: infos partielles (pas de benchmark perf), hypothèse sur la charge utilisateur
→ Confidence: YELLOW
→ Action: Proposer l'architecture + flag ⚠️
Output: "Architecture proposée basée sur l'HYPOTHÈSE de <10k users simultanés.
         ⚠️ INCERTAIN : Si la charge dépasse 10k, le pattern X devra être remplacé par Y.
         Recommandation : valider l'hypothèse charge avant implémentation."
Post-flight: aucun fait inventé, hypothèse clairement labélisée
→ Confiance maintenue YELLOW — livré avec caveat
```

### Exemple 3 — Agent Dev, confiance ROUGE → escalade
```
Pre-flight: fichier config.prod.yaml référencé dans la story mais absent du repo
→ Confidence: RED — missing_data
→ Action: STOP + Uncertainty Report

uncertainty_report:
  agent: "dev/Amelia"
  task: "Implémenter US-042 — configuration production"
  understood:
    - "La story demande de configurer le service auth pour production"
    - "Le format attendu est YAML avec les clés host, port, tls"
  blocking_gaps:
    - gap: "Le fichier config.prod.yaml n'existe pas dans le repo"
      type: missing_data
      impact: "Impossible de configurer les valeurs production sans ce fichier"
      suggested_question: "Faut-il créer config.prod.yaml ? Si oui, quelles valeurs pour host/port/tls ?"
  effort_spent:
    - "Cherché dans tout le repo : config.dev.yaml existe, config.staging.yaml existe, pas de config.prod.yaml"
    - "Vérifié le .gitignore : pas exclu"
  options:
    - option: "Copier config.staging.yaml et adapter"
      confidence: "40%"
      risk: "Les valeurs staging peuvent différer de prod (ports, TLS, secrets)"
  impact_assessment:
    blocking: true
    partial_delivery_possible: false
    estimated_unblock: "Fournir config.prod.yaml ou confirmer la copie de staging"
```

---

## Références Croisées

- Orchestrator Gateway : [framework/orchestrator-gateway.md](orchestrator-gateway.md) (BM-53) — supervision HUP
- Question Escalation : [framework/question-escalation-chain.md](question-escalation-chain.md) (BM-51) — escalade des gaps bloquants
- Cross-Validation : [framework/cross-validation-trust.md](cross-validation-trust.md) (BM-52) — validation croisée post-HUP
- Agent Mesh Network : [framework/agent-mesh-network.md](agent-mesh-network.md) (BM-55) — propagation HUP via P2P
- Event Log : [framework/event-log-shared-state.md](event-log-shared-state.md) (BM-59) — événements HUP persistés
- Hybrid Parallelism : [framework/hybrid-parallelism-engine.md](hybrid-parallelism-engine.md) (BM-58) — HUP sur chaque tâche DAG

---

*BM-50 Honest Uncertainty Protocol | framework/honest-uncertainty-protocol.md*
