# Concepts — Comprendre BMAD Custom Kit

> Ce guide explique **tous les concepts** du kit avec des analogies simples.
> Lisez-le avant de plonger dans le code — tout deviendra limpide.

---

## Vue d'ensemble en 30 secondes

BMAD Custom Kit transforme votre IDE en **entreprise virtuelle** peuplée d'agents IA spécialisés.

```
Votre projet
    └── _bmad/                  ← "Les bureaux" de votre entreprise virtuelle
         ├── agents/            ← Les employés (chacun son rôle)
         ├── workflows/         ← Les processus métier
         ├── _memory/           ← La mémoire collective
         └── _config/           ← Le règlement intérieur
```

Chaque agent a une **persona** (nom, style, expertise), suit des **règles communes** (le protocole), et communique via une **mémoire partagée**. C'est comme une équipe réelle — sauf qu'elle vit dans votre éditeur de code.

---

## Les 3 piliers

### 1. Les Agents — "Qui fait quoi"

Un agent = un rôle spécialisé avec des compétences précises.

| Analogie | Dans le kit |
|----------|-------------|
| Le chef de projet | **Atlas** — navigue, coordonne, a la vision globale |
| Le contrôleur qualité | **Sentinel** — surveille, améliore le framework |
| L'archiviste | **Mnemo** — consolide la mémoire, détecte les contradictions |
| L'expert Go | **Gopher** — ne parle que Go, connaît les idiomes |
| Le DevOps | **Forge** — infrastructure, pipelines, monitoring |

**Archétypes** = des packs d'agents pré-configurés selon le type de projet :
- `minimal` → Atlas + Sentinel + Mnemo (le minimum viable)
- `infra-ops` → + 7 agents infra (Kubernetes, sécurité, monitoring...)
- `web-app` → + agents frontend/backend auto-détectés
- `stack/*` → agents technos (Go, Python, TypeScript, Terraform...)

### 2. Le Protocole — "Les règles du jeu"

Tous les agents héritent d'un **protocole commun** (`agent-base.md`) qui garantit un comportement cohérent :

```
┌─────────────────────────────────────────┐
│          PROTOCOLE AGENT-BASE           │
├─────────────────────────────────────────┤
│ Completion Contract   → jamais "fini"   │
│                         sans preuve     │
│ Plan/Act Mode         → planifier OU    │
│                         exécuter        │
│ Extended Thinking     → réflexion       │
│                         profonde [THINK]│
│ Maximes de Grice      → communication  │
│                         optimale        │
│ Chunking 7±2          → sorties        │
│                         structurées     │
│ Camouflage adaptatif  → s'adapte au    │
│                         niveau user     │
└─────────────────────────────────────────┘
```

### 3. La Mémoire — "Le cerveau collectif"

Les agents partagent une mémoire persistante entre les sessions :

```
_memory/
├── shared-context.md        ← Ce que tout le monde sait
├── decisions-log.md         ← Les décisions prises et pourquoi
├── failure-museum.md        ← Les erreurs passées (pour ne pas les répéter)
├── agent-learnings/         ← Ce que chaque agent a appris
├── contradiction-log.md     ← Quand 2 infos se contredisent
└── session-state.md         ← Où on en était (reprise automatique)
```

---

## Les concepts clés expliqués

### Completion Contract (CC)

> **Analogie** : Un chirurgien ne dit pas "opération terminée" sans vérifier que le patient va bien.

Un agent ne peut JAMAIS dire "fait" sans avoir lancé les vérifications automatiques du stack (tests, lint, build). Si ça échoue → il corrige et relance. Pas de "je pense que c'est bon".

```
Agent dit "terminé" → cc-verify.sh détecte Python → lance pytest + ruff
   → ✅ CC PASS → OK, c'est vraiment fini
   → 🔴 CC FAIL → corrige, relance, boucle jusqu'à PASS
```

### Plan/Act Mode

> **Analogie** : En cuisine — `[PLAN]` = écrire la recette, `[ACT]` = cuisiner directement.

- **[PLAN]** : l'agent structure la solution, liste les fichiers, les risques — mais ne touche à rien. Vous validez, puis il passe en [ACT].
- **[ACT]** (défaut) : l'agent exécute directement, sans demander "tu veux que je continue ?" à chaque étape.

### Extended Thinking [THINK]

> **Analogie** : Demander à quelqu'un de s'asseoir, réfléchir 10 minutes, puis donner sa réponse — au lieu de répondre du tac au tac.

Pour les décisions critiques (architecture, sécurité, stack), l'agent explore 3+ options, simule les échecs, documente sa décision dans un ADR.

### Team of Teams

> **Analogie** : Une entreprise avec des départements qui collaborent via des contrats.

```
Team Vision (stratégie)  ──Delivery Contract──→  Team Build (construction)
         ↑                                              │
         └───────────Delivery Contract──────────────────┘
                                                        │
                                              Team Ops (opérations)
```

Aucune team ne passe le relais sans un **Delivery Contract** signé — un artefact qui garantit que tout le contexte est transmis.

### Boomerang Orchestration

> **Analogie** : Le chef d'orchestre envoie la partition au violoniste, qui joue et renvoie le résultat.

Le Scrum Master (SM) délègue une tâche à un agent spécialiste, attend le résultat, et enchaîne — comme un boomerang.

```
SM → "Dev, implémente cette story" → Dev travaille → résultat revient au SM
SM → "QA, teste ce code" → QA teste → résultat revient au SM
```

---

## Le système cognitif (Vague 1)

Ces principes sont intégrés dans le protocole que TOUS les agents suivent :

### Maximes de Grice — Comment l'agent communique

| Maxime | Règle | Exemple |
|--------|-------|---------|
| **Quantité** | Dire exactement ce qu'il faut | "3 fichiers modifiés : a.py, b.py, c.py" |
| **Qualité** | Ne rien affirmer sans preuve | "Port 8080 (vérifié via `docker ps`)" |
| **Pertinence** | Répondre à la question posée | "Quel port ?" → "3000" (pas l'histoire de Grafana) |
| **Manière** | Être clair et ordonné | Étapes numérotées, termes précis |

### Chunking 7±2 — Structure des sorties

> **Analogie** : Un numéro de téléphone est découpé en groupes de 2-3 chiffres, pas 10 chiffres d'affilée.

Toute liste = max 7 items. Au-delà → sous-groupes avec titres.
Tout menu = max 7 options visibles. Au-delà → "Plus d'options...".

### Camouflage Adaptatif

L'agent ajuste sa communication selon votre niveau (`skill_level` dans project-context.yaml) :

| Niveau | Comportement |
|--------|-------------|
| `beginner` | Explique le POURQUOI, ajoute des commentaires, confirme chaque étape |
| `intermediate` | Équilibre explication/exécution |
| `expert` | Exécute directement, pas de commentaires superflus, terminologie technique |

### Priming Cognitif

> **Analogie** : Avant de demander "quel nom pour le nouveau service ?", d'abord montrer les noms existants.

L'agent charge toujours le contexte pertinent AVANT de poser une question.

### Wabi-sabi — Accepter l'imperfection

> **Analogie** : Mieux vaut livrer un pont qui fonctionne avec des finitions à peaufiner, qu'un pont parfait jamais construit.

Un MVP livré vaut mieux qu'un produit parfait jamais terminé. Les imperfections cosmétiques sont documentées en `Known Limitations`, pas bloquantes.

---

## Les 41 outils — Carte de navigation

### Outils fondamentaux (pré-existants)

| Outil | Pour quoi faire | Analogie |
|-------|----------------|----------|
| `context-guard` | Mesurer le budget LLM d'un agent | Le compteur kilométrique |
| `agent-forge` | Générer un nouvel agent | L'imprimante 3D à agents |
| `agent-bench` | Mesurer la performance | Le tableau de scores |
| `dna-evolve` | Faire évoluer la config | L'ADN qui mute et s'adapte |
| `dream` | Insights émergents hors-session | Le sommeil qui consolide |
| `adversarial-consensus` | Décision collective critique | Le tribunal avec avocat du diable |
| `antifragile-score` | Résilience du système | Le check-up médical |
| `reasoning-stream` | Tracer le raisonnement | Le journal de bord du capitaine |
| `cross-migrate` | Migrer des artefacts entre projets | Le déménageur |
| `agent-darwinism` | Sélection naturelle des agents | La sélection de Darwin |
| `stigmergy` | Coordination par signaux | Les phéromones des fourmis |
| `memory-lint` | Valider la cohérence mémoire | Le correcteur orthographique |
| `nso` | Orchestrateur nerveux central | Le système nerveux |
| `auto-doc` | Synchroniser README et code | Le traducteur automatique |
| `schema-validator` | Valider les fichiers YAML | Le contrôleur qualité |
| `gen-tests` | Scaffolder des tests | Le générateur de filets de sécurité |

### Intelligence contextuelle (Vague 2)

| Outil | Pour quoi faire | Analogie |
|-------|----------------|----------|
| `context-router` | Router le contexte vers le bon agent | Le standard téléphonique |
| `preflight-check` | Vérifier avant d'exécuter une story | Le checklist du pilote avant décollage |
| `nudge-engine` | Suggestions non-intrusives | Le GPS qui propose des détours |
| `desire-paths` | Détecter les usages réels vs conçus | Les chemins de traverse dans l'herbe |
| `early-warning` | Alertes précoces (dette, complexité) | Le détecteur de fumée |
| `confidence-scores` | Score de confiance sur les décisions | Le baromètre de certitude |

### Intégrité & résilience (Vague 3)

| Outil | Pour quoi faire | Analogie |
|-------|----------------|----------|
| `semantic-chain` | Tracer la chaîne sémantique | La chaîne du froid alimentaire |
| `rosetta` | Glossaire unifié cross-domaine | La pierre de Rosette |
| `immune-system` | Détecter les anomalies | Le système immunitaire |
| `self-healing` | Auto-diagnostic et réparation | Le médecin de garde |
| `dark-matter` | Révéler les connaissances implicites | Le télescope à matière noire |
| `oracle` | CTO virtuel introspectif | L'oracle de Delphes |

### Évolution & adaptation (Vague 4)

| Outil | Pour quoi faire | Analogie |
|-------|----------------|----------|
| `workflow-adapt` | Adapter les workflows à l'usage | La route qui s'élargit au trafic |
| `bias-toolkit` | Détecter et gérer les biais cognitifs | Les lunettes anti-illusion |
| `crescendo` | Progression beginner → expert | L'escalier pédagogique |
| `quorum` | Modes collectifs (security, speed, quality) | Le thermostat d'équipe |
| `new-game-plus` | Héritage intelligent cross-projets | Le New Game+ jeu vidéo |
| `swarm-consensus` | Estimation multi-agent | Le vote de l'essaim |
| `incubator` | Faire mûrir des idées | La couveuse à idées |

### Écosystème & visualisation (Vague 5)

| Outil | Pour quoi faire | Analogie |
|-------|----------------|----------|
| `project-graph` | Graphe de dépendances du projet | La carte satellite |
| `dashboard` | Tableau de bord santé complet | Le cockpit d'avion |
| `mycelium` | Partager des patterns entre projets | Le réseau de champignons |
| `distill` | Condenser un document (5 niveaux) | L'alambic à idées |
| `workflow-snippets` | Composer des workflows modulaires | Les LEGO du workflow |
| `harmony-check` | Score d'harmonie architecturale | L'accordeur de piano |
| `digital-twin` | Simulation d'impact de changements | Le simulateur de vol |
| `quantum-branch` | Timelines parallèles de configuration | Les mondes parallèles |
| `time-travel` | Checkpoints et archéologie temporelle | La machine à remonter le temps |
| `crispr` | Édition chirurgicale de workflows | Le scalpel génétique |
| `decision-log` | Blockchain légère de décisions | Le registre notarié |
| `mirror-agent` | Apprentissage inter-agents par mimétisme | Les neurones miroirs |
| `sensory-buffer` | Mémoire court terme à décroissance | La mémoire de travail |
| `r-and-d` | Innovation Engine avec reinforcement learning | Le laboratoire de R&D autonome |

---

## Architecture du projet

```
bmad-custom-kit/
│
├── bmad-init.sh                 ← Point d'entrée : installe et configure tout
├── project-context.tpl.yaml     ← Template : identité du projet
│
├── framework/                   ← Le "système d'exploitation" (jamais modifié par projet)
│   ├── agent-base.md            ← Protocole commun à tous les agents
│   ├── cc-verify.sh             ← Completion Contract vérifieur
│   ├── tools/                   ← 49 outils Python (stdlib only)
│   ├── teams/                   ← Définitions des 3 teams
│   ├── workflows/               ← Workflows de base (boomerang, subagent, etc.)
│   ├── memory/                  ← Scripts mémoire (mem0-bridge, maintenance)
│   └── mcp/                     ← Serveur MCP pour cross-IDE
│
├── archetypes/                  ← Packs d'agents pré-configurés
│   ├── minimal/                 ← 3 agents de base
│   ├── infra-ops/               ← 7 agents infrastructure
│   ├── web-app/                 ← Agents front/back
│   └── stack/                   ← Agents par techno (Go, Python, TS...)
│
├── docs/                        ← Documentation
└── tests/                       ← Tests (114 smoke + tests unitaires)
```

### Comment ça s'installe dans un projet

```
votre-projet/
├── votre-code/
├── _bmad/                       ← Créé par bmad-init.sh
│   ├── _config/
│   │   └── custom/
│   │       ├── agents/          ← Vos agents (copiés depuis les archétypes)
│   │       ├── cc-verify.sh     ← Completion Contract
│   │       └── agent-manifest.csv
│   ├── _memory/
│   │   ├── shared-context.md
│   │   ├── decisions-log.md
│   │   ├── failure-museum.md
│   │   └── agent-learnings/
│   └── project-context.yaml     ← L'identité de votre projet
└── .github/
    └── copilot-instructions.md  ← Instructions pour GitHub Copilot
```

---

## Flux typique d'une session

```
1. Ouvrir l'IDE
   │
2. Activer un agent (ex: @dev)
   │
   ├── L'agent charge le protocole (agent-base.md)
   ├── Health-check automatique
   ├── Vérifie les tâches en cours (Zeigarnik)
   ├── Vérifie la boîte de réception inter-agents
   └── Affiche le menu + greeting personnalisé
   │
3. Demander une tâche ("implémente le cache Redis")
   │
   ├── [ACT par défaut] L'agent exécute directement
   ├── Modifie les fichiers
   ├── Lance cc-verify.sh → tests + lint
   ├── ✅ CC PASS → "Fait"
   │   ou
   ├── 🔴 CC FAIL → corrige → relance → boucle
   │
4. Fin de session
   │
   ├── Exit Summary (Peak-End Rule)
   ├── Sauvegarde session-state.md
   └── Consolidation mémoire
```

---

## Glossaire rapide

| Terme | Signification |
|-------|--------------|
| **CC** | Completion Contract — vérification automatique avant tout "terminé" |
| **DNA** | `archetype.dna.yaml` — configuration génétique d'un archétype |
| **SIL** | Self-Improvement Loop — boucle d'auto-amélioration |
| **NSO** | Nervous System Orchestrator — cycle complet de maintenance |
| **MCP** | Model Context Protocol — standard d'interop IDE |
| **ADR** | Architecture Decision Record — décision documentée |
| **BMAD_TRACE** | Variable d'environnement pour tracer l'exécution |
| **Archétype** | Pack d'agents pré-configuré pour un type de projet |
| **Persona** | Identité et style de communication d'un agent |
| **Delivery Contract** | Artefact contractuel entre 2 teams |
| **Failure Museum** | Collection d'erreurs passées pour apprentissage |
| **Chunking** | Découper l'info en groupes de 7±2 (loi de Miller) |
| **Camouflage** | Adapter la communication au niveau de l'utilisateur |
| **Priming** | Charger le contexte avant une question |
| **Wabi-sabi** | Accepter l'imperfection pragmatiquement |
| **Stigmergy** | Coordination indirecte par signaux persistants |
| **Digital Twin** | Jumeau numérique — simule l'impact de changements |
| **Quantum Branch** | Fork de configurations pour explorer des alternatives |
| **CRISPR** | Édition chirurgicale précise de workflows |
| **Decision Chain** | Chaîne immuable de décisions (blockchain légère) |
| **Sensory Buffer** | Mémoire court terme qui décroît exponentiellement |
| **R&D Engine** | Moteur d'innovation autonome avec reinforcement learning |
| **Reinforcement Learning** | Ajustement automatique des poids par récompense/pénalité |
| **Epoch** | Un cycle complet d'innovation (harvest → select → converge) |
| **Policy** | Poids adaptatifs qui guident les choix du moteur R&D |
| **Convergence** | Point où l'innovation n'apporte plus de gain significatif |

---

## Pour aller plus loin

- [getting-started.md](getting-started.md) — Guide d'installation pas à pas
- [creating-agents.md](creating-agents.md) — Créer un agent custom
- [archetype-guide.md](archetype-guide.md) — Comprendre les archétypes
- [memory-system.md](memory-system.md) — Le système de mémoire en détail
- [workflow-design-patterns.md](workflow-design-patterns.md) — Patterns de workflows
- [troubleshooting.md](troubleshooting.md) — Résoudre les problèmes courants
