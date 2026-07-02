# :material-book-open-variant: Concepts — Architecture Grimoire Kit

> Ce guide explique l'architecture complète du kit.
> Lisez-le en premier — tout le reste devient lisible après.

---

## :material-lightning-bolt: Vue d'ensemble

Grimoire Kit transforme votre IDE en **espace de travail agentique orchestré**.

```
Vous (utilisateur)
    │
    ▼
┌──────────────────────────────────┐
│   Grimoire Master (SOG)          │  ← Point d'entrée unique
│   Smart Orchestrator Gateway     │
└──────────────┬───────────────────┘
               │ dispatch invisible
    ┌──────────┼──────────┐
    ▼          ▼          ▼
  analyst    dev        qa        …sub-agents invisibles
  (Mary)    (Amelia)  (Quinn)
```

Un seul agent est visible : **Grimoire Master**. Il orchestre tous les autres en coulisse — vous ne voyez jamais les transitions, jamais les noms des agents internes.

---

## :material-cube-outline: Les 3 piliers

### 1. Les agents — "Qui fait quoi"

Un agent = un rôle spécialisé avec expertise, règles et mémoire.

| Agent | Persona | Spécialité |
|---|---|---|
| **grimoire-master** | Grimoire Master | Orchestration SOG — point d'entrée unique |
| **analyst** | Mary | Business analysis, exigences |
| **pm** | John | PRD, product management |
| **architect** | Winston | Architecture, infrastructure |
| **dev** | Amelia | Implémentation, TDD |
| **qa** | Quinn | Tests, couverture |
| **sm** | Bob | Scrum, backlog, stories |
| **tech-writer** | Paige | Documentation |
| **ux-designer** | Sally | UX/UI design |
| **rodin** | Rodin | Débats socratiques, anti-chambre d'écho |
| **art-director** | Iris | Direction artistique, hero FX, room kits |
| **brainstorming-coach** | Carson | Idéation, brainstorming |
| **agent-builder** | Bond | Création d'agents Grimoire |

**Archétypes** = packs pré-configurés selon le type de projet. Le wizard `grimoire init` en propose 6 :
- `minimal` → base universelle (toujours incluse)
- `web-app` → stack full-stack
- `platform-engineering` → plateforme & infra (architecture, IaC, déploiement ; `infra-ops` auto-détecté pour Terraform/K8s)
- `creative-studio` → création de contenu et design
- `game-dev` → jeux vidéo gouvernés
- `fix-loop` → boucle de correction certifiée

### 2. Le protocole — "Les règles du jeu"

Tous les agents héritent d'un protocole commun (`agent-base.md`) :

```
┌──────────────────────────────────────────┐
│           PROTOCOLE AGENT-BASE           │
├──────────────────────────────────────────┤
│ Completion Contract  → jamais "fini"     │
│                        sans preuve       │
│ Plan/Act Mode        → planifier OU      │
│                        exécuter          │
│ Autonomy Level       → ALS, AORA, PIP   │
│ Anti-hallucination   → HUP, QEC, CVTL   │
│ Maximes de Grice     → communication    │
│                        optimale          │
│ Chunking 7±2         → sorties          │
│                        structurées       │
└──────────────────────────────────────────┘
```

### 3. La mémoire — "Le cerveau collectif"

Mémoire persistante partagée entre sessions :

```
_grimoire-runtime/_memory/
├── shared-context.md        ← Vérité partagée (stack, décisions)
├── decisions-log.md         ← ADRs et décisions prises
├── failure-museum.md        ← Erreurs passées (pour ne pas répéter)
├── agent-learnings/         ← Leçons par agent
├── session-state.md         ← Reprise automatique de session
└── udf-usage-tracker.json   ← Suivi des artefacts dynamiques
```

---

## :material-router: SOG — Smart Orchestrator Gateway

> **BM-53** — Architecture clé de Grimoire depuis la v3.

Le SOG est le protocole d'orchestration qui fait de Grimoire Master l'**unique point de contact utilisateur**. Tous les autres agents sont des sub-agents invisibles, dispatchés automatiquement.

### Principes fondateurs

| Principe | Description |
|---|---|
| **Single entrypoint** | L'utilisateur ne parle qu'à Grimoire Master. Jamais aux sub-agents directement |
| **Dispatch intelligent** | Le SOG analyse l'intention, détecte les zones d'ombre, choisit le(s) meilleur(s) agent(s) |
| **ARG routing** | Agent Relationship Graph — graphe de dépendances inter-agents pour le routing optimal |
| **Résultats agrégés** | Le SOG consolide les sorties avant de présenter à l'utilisateur |
| **Invisibilité** | Noms d'agents, handoffs et routing internes jamais exposés |

### Protocoles intégrés

```
HUP  (BM-50) — Anti-hallucination : aucun output de sub-agent ne passe sans validation
QEC  (BM-51) — Questions par batch : jamais d'interruption avec une question unique
CVTL (BM-52) — Cross-validation des outputs critiques via second agent
PCE  (BM-54) — Party mode : débats multi-agents orchestrés
```

### Flux d'une requête

```
Utilisateur → Grimoire Master (SOG)
  │
  ├── 1. Analyse intention + détection zones d'ombre
  ├── 2. Clarification proactive si besoin (batch via QEC)
  ├── 3. Enrichissement du prompt avec contexte complet
  ├── 4. Dispatch vers sub-agent(s) optimal(aux) via ARG
  ├── 5. Validation HUP sur les outputs
  ├── 6. Agrégation et présentation utilisateur
  └── 7. Follow-through L1/L2 si suite logique évidente
```

!!! tip "Activation SOG"
    Si votre premier message contient déjà une demande actionable, Grimoire Master traite directement. Le menu n'apparaît qu'en activation sans tâche explicite.

---

## :material-factory: UDF — Unified Dynamic Factory

> Quand aucun artefact existant ne couvre un besoin, le SOG en crée un dynamiquement.

### Les 4 types d'artefacts

| Type | Builder | Éphémère | Permanent |
|---|---|---|---|
| **Agent** | agent-builder | `_dyn-{slug}.agent.md` | `{slug}.agent.md` |
| **Workflow** | workflow-builder | `_dyn-{slug}.prompt.md` | `{slug}.prompt.md` |
| **Skill** | workflow-builder + dev | `_dyn-{slug}/SKILL.md` | `{slug}/SKILL.md` |
| **Instruction** | tech-writer | `_dyn-{slug}.instructions.md` | `{slug}.instructions.md` |

### Triage de durabilité

Avant de créer, le SOG calcule un score pour décider : éphémère ou permanent ?

| Signal | Score |
|---|---|
| Domaine lié au stack technique du projet | +2 |
| Besoin déjà exprimé dans une session précédente | +2 |
| Domaine transversal (sécurité, perf, accessibilité) | +2 |
| Besoin récurrent dans le cycle de vie produit | +1 |
| Besoin ponctuel/exploratoire | -2 |
| Domaine très niche | -1 |

- **Score ≥ 3** → Création permanente (template `permanent-*.tpl.md`)
- **Score < 3** → Création éphémère (expire dans 7 jours)

!!! note "Promotion automatique"
    Un artefact éphémère réutilisé 3+ fois est promu automatiquement en artefact permanent. Le suivi est dans `_grimoire-runtime/_memory/udf-usage-tracker.json`.

---

## :material-cog-sync: Modèle de routing (task-aware)

Le SOG adapte le modèle LLM à la nature de la tâche :

| Profil | Cas d'usage | Agents par défaut |
|---|---|---|
| `deep_reasoning` | Architecture, debug multi-fichiers, ADR, décisions nuancées | grimoire-master, rodin, architect |
| `general_code` | Implémentation, tests, refactoring | dev, quick-flow-solo-dev, qa |
| `writing_structured` | Docs, PRD, stories, prompts, YAML | pm, tech-writer, agent-builder |
| `fast_iter` | Brainstorming, checks rapides | brainstorming-coach, design-thinking-coach |
| `local_coder` | Usage offline/privé via Ollama | override manuel `/set-model dev qwen3-coder` |

Override de session : `/set-model <agent|all|reset> <model-id|auto>`

---

## :material-shield-check-outline: Protocoles d'autonomie

### ALS — Autonomy Level System

Niveaux de risque pour chaque décision :

| Niveau | Description | Action |
|---|---|---|
| L1 | Local, réversible (edit, test) | Exécuter sans confirmation |
| L2 | Indirect, faible rayon d'impact | Exécuter, signaler |
| L3 | Partagé ou à fort impact | Confirmer avant d'agir |

### AORA — Autonomous Iteration Loop

Boucle d'itération autonome : si une tâche révèle une suite logique L1/L2 alignée avec l'objectif courant, l'agent l'exécute dans le même tour — sans demander "tu veux que je continue ?".

### PIP + DCF

- **PIP** (Proactive Initiative Protocol) — l'agent prend l'initiative sur les gaps évidents
- **DCF** (Dynamic Confidence Framework) — confiance contextuelle ; l'agent calibre son assertivité selon la certitude

---

## :material-handshake: Contrats et modes

### Completion Contract (CC)

> Un chirurgien ne dit pas "opération terminée" sans vérifier que le patient va bien.

Un agent ne peut dire "fait" sans avoir lancé les vérifications automatiques du stack :

```
Agent dit "terminé"
  → cc-verify.sh détecte le stack (Python/Go/TS/...)
  → Lance tests + lint + build
  → ✓ CC PASS → Livraison
  → ✗ CC FAIL → Corrige → Relance → Boucle
```

### Plan/Act Mode

- **[PLAN]** — structure, planifie, liste les risques. Ne touche à rien sans votre accord.
- **[ACT]** — exécute directement. Mode par défaut.

Bascule : tapez `[PLAN]` ou `[ACT]` dans le chat.

---

## :material-brain: Système cognitif

### Maximes de Grice

| Maxime | Règle |
|---|---|
| **Quantité** | Dire exactement ce qu'il faut, ni plus ni moins |
| **Qualité** | Ne rien affirmer sans preuve (`Port 8080 (vérifié via docker ps)`) |
| **Pertinence** | Répondre à la question posée, pas à une autre |
| **Manière** | Être clair, ordonné, étapes numérotées |

### Chunking 7±2

Toute liste = max 7 items. Au-delà → sous-groupes avec titres.
Tout menu = max 7 options visibles.

### Camouflage adaptatif

| Niveau (`skill_level`) | Comportement |
|---|---|
| `beginner` | Explique le POURQUOI, ajoute des commentaires, confirme chaque étape |
| `intermediate` | Équilibre explication et exécution |
| `expert` | Exécute directement, terminologie technique, pas de redondance |

### Priming cognitif

L'agent charge toujours le contexte pertinent **avant** de poser une question ou de proposer une solution.

---

## :material-sitemap: Architecture du projet

```
grimoire-kit/
│
├── src/grimoire/              # SDK Python
│   ├── core/                  # Config, Project, Scanner, Validator
│   ├── cli/                   # CLI Typer (grimoire <cmd>)
│   ├── tools/                 # HarmonyCheck, Preflight, MemoryLint
│   ├── memory/                # Backends (JSON · Qdrant · Ollama)
│   ├── mcp/                   # Serveur MCP
│   └── registry/              # AgentRegistry, LocalRegistry
│
├── _grimoire-runtime/         # Runtime agentique (dans votre projet)
│   ├── bmm/agents/            # Sub-agents BMM (analyst, dev, qa…)
│   ├── core/agents/           # Grimoire Master (SOG)
│   ├── _memory/               # Mémoire persistante
│   └── _config/               # Manifestes, routing, UDF registry
│
├── archetypes/                # Templates de projets
├── framework/tools/           # Outils CLI standalone
└── docs/                      # Cette documentation
```

### Installation dans un projet

```
votre-projet/
├── votre-code/
├── _grimoire-runtime/         # Installé par grimoire init
│   ├── _config/               # Manifestes agents/workflows
│   ├── _memory/               # Mémoire persistante
│   └── bmm/                   # Module méthodologie
├── _grimoire-runtime-output/  # Artefacts produits
│   ├── planning-artifacts/    # PRD, épics, brainstorms
│   └── implementation-artifacts/
└── project-context.yaml       # Identité du projet
```

---

## :material-play-circle: Flux d'une session

```
1. Ouvrir l'IDE
   │
2. Parler au Grimoire Master (SOG)
   │
   ├── Chargement protocole + mémoire session
   ├── Activation SOG : analyse l'intention
   ├── Si demande actionable → traitement direct (pas de menu)
   └── Sinon → menu numéroté
   │
3. Demander une tâche
   │
   ├── SOG analyse + enrichit le contexte
   ├── Dispatch vers sub-agent optimal (invisible)
   ├── Sub-agent exécute → CC vérifié
   ├── HUP : validation output avant retour
   └── SOG agrège et présente le résultat
   │
4. Follow-through AORA
   │
   └── Si suite logique L1/L2 → exécution dans le même tour
   │
5. Fin de session
   │
   ├── Exit Summary
   ├── Sauvegarde session-state.md
   └── Mise à jour mémoire
```

---

## :material-book-alphabet: Glossaire

| Terme | Définition |
|---|---|
| **SOG** | Smart Orchestrator Gateway — point d'entrée unique, dispatche invisiblement |
| **UDF** | Unified Dynamic Factory — crée agents/workflows/skills/instructions à la volée |
| **ARG** | Agent Relationship Graph — graphe de routage inter-agents |
| **HUP** | Anti-hallucination protocol — validation de tous les outputs sub-agents |
| **QEC** | Question batch protocol — jamais d'interruption avec une question isolée |
| **CVTL** | Cross-validation trigger — second agent pour les outputs critiques |
| **PCE** | Party mode — débats multi-agents orchestrés |
| **ALS** | Autonomy Level System — niveaux de risque L1/L2/L3 |
| **AORA** | Autonomous iteration loop — exécution de la suite logique sans friction |
| **PIP** | Proactive initiative — prise d'initiative sur les gaps évidents |
| **DCF** | Dynamic confidence — calibration de l'assertivité |
| **CC** | Completion Contract — vérification automatique avant tout "terminé" |
| **MCP** | Model Context Protocol — standard d'interop IDE |
| **ADR** | Architecture Decision Record — décision documentée |
| **BMM** | Méthode Grimoire — module méthodologie (analyst, pm, dev, qa…) |
| **Archétype** | Pack d'agents pré-configuré pour un type de projet |
| **Persona** | Identité, nom et style de communication d'un agent |
| **Session Momentum** | Maintien de la dynamique entre requêtes (AORA + CC) |
| **Friction Budget** | Limite de sollicitations non nécessaires par session |
| **NSO** | *Ancienne appellation* — Nervous System Orchestrator, remplacé par SOG en v3 |

---

## :material-arrow-right-circle: Pour aller plus loin

<div class="grid cards" markdown>

- :material-rocket-launch-outline: [Guide de démarrage](getting-started.md)
- :material-account-hard-hat-outline: [Créer un agent](creating-agents.md)
- :material-cube-outline: [Guide des archétypes](archetype-guide.md)
- :material-brain: [Système de mémoire](memory-system.md)
- :material-sitemap: [Patterns de workflows](workflow-design-patterns.md)
- :material-wrench: [Dépannage](troubleshooting.md)

</div>
