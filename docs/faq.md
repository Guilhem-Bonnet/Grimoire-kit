# FAQ

Questions fréquemment posées sur Grimoire Kit.

---

## Installation & Configuration

### Comment installer Grimoire Kit ?

```bash
pip install grimoire-kit
```

Pour les fonctionnalités optionnelles :

```bash
pip install "grimoire-kit[all]"     # Tout inclus
pip install "grimoire-kit[mcp]"     # Serveur MCP seulement
pip install "grimoire-kit[qdrant]"  # Backend Qdrant seulement
pip install "grimoire-kit[weaviate]" # Backend Weaviate seulement
pip install "grimoire-kit[neo4j]"    # Projection graphe Neo4j
```

### Quelles versions de Python sont supportées ?

Python **3.12+** est requis. Les versions 3.12 et 3.13 sont testées en CI sur Ubuntu, Windows et macOS.

### Comment changer de backend mémoire ?

Éditez `project-context.yaml` ou utilisez la CLI :

```bash
grimoire config show memory.backend   # Voir le backend actuel
```

Backends disponibles : `auto`, `local`, `qdrant-local`, `qdrant-server`, `weaviate-server`, `ollama`.

Modifiez la section `memory:` dans `project-context.yaml` :

```yaml
memory:
  backend: weaviate-server
  weaviate_url: http://localhost:8080
  weaviate_collection: GrimoireKitMemory
```

### Comment vérifier que mon projet est bien configuré ?

```bash
grimoire doctor    # 8 checks automatiques
grimoire validate  # Validation du schéma YAML
grimoire diff      # Drift vs archétype par défaut
```

---

## SOG — Smart Orchestrator Gateway

### C'est quoi le SOG ?

Le SOG (Smart Orchestrator Gateway, BM-53) est le protocole d'orchestration qui fait de **Grimoire Master le seul agent visible**. Tous les autres agents (analyst, dev, qa, rodin…) sont des sub-agents invisibles dispatchés automatiquement selon l'intention détectée.

Vous parlez à Grimoire Master — il fait le reste en coulisse.

### Comment le SOG choisit l'agent à utiliser ?

Via l'**ARG (Agent Relationship Graph)** — un graphe de dépendances inter-agents qui mappe les intentions aux agents les mieux équipés. Le SOG enrichit la requête avec le contexte projet, puis route.

Pour les outputs critiques, il déclenche un **CVTL (cross-validation)** via un second agent indépendant.

### Le SOG peut-il se tromper de routage ?

Rarement — mais si l'intention est ambiguë, le SOG pose des questions en batch (QEC, BM-51) avant de dispatcher. Il ne vous interrompt jamais avec une question isolée ; il regroupe toutes les clarifications en un seul message.

### Comment contourner le SOG pour parler directement à un sub-agent ?

C'est volontairement impossible — c'est le principe du SOG. Si un sub-agent spécifique semble manqué, décrivez plus précisément votre besoin à Grimoire Master ; il affinera le routing.

---

## UDF — Unified Dynamic Factory

### C'est quoi l'UDF ?

La **Unified Dynamic Factory** est le système qui crée automatiquement des agents, workflows, skills ou instructions quand aucun existant ne couvre le besoin détecté par le SOG.

### Comment créer un agent dynamiquement ?

Décrivez simplement votre besoin à Grimoire Master :

```
"J'ai besoin d'un agent spécialisé en optimisation de requêtes SQL"
```

Le SOG analyse, calcule le score de durabilité, et crée l'artefact approprié — éphémère (7 jours) ou permanent.

### Quelle est la différence entre éphémère et permanent ?

| | Éphémère | Permanent |
|---|---|---|
| **Score durabilité** | < 3 | ≥ 3 |
| **Préfixe** | `_dyn-` | nom direct |
| **Durée** | 7 jours | illimitée |
| **Promotion** | Automatique à 3 réutilisations | N/A |

### Comment voir mes artefacts dynamiques actifs ?

```bash
cat _grimoire-runtime/_memory/udf-usage-tracker.json
```

Les artefacts avec `count >= 3` sont marqués `promote: true` — le SOG vous notifiera.

---

## Modèle de routing

### Le SOG choisit-il le modèle LLM automatiquement ?

Oui. En mode `auto` (par défaut), le SOG sélectionne le profil de routing selon la nature de la tâche :

| Tâche | Profil | Raison |
|---|---|---|
| Architecture, ADR, debug critique | `deep_reasoning` | Raisonnement profond + grand contexte |
| Implémentation, tests, refactoring | `general_code` | Vitesse + précision code |
| Docs, PRD, YAML, prompts | `writing_structured` | Stabilité rédactionnelle |
| Brainstorming, checks rapides | `fast_iter` | Latence/coût optimisés |

### Comment forcer un modèle spécifique ?

```
/set-model dev gpt-5.3-codex       # Pour un agent précis
/set-model all claude-sonnet-4.6   # Pour tous les agents
/set-model reset                   # Retour en auto
```

### Puis-je utiliser un modèle local (Ollama) ?

Oui, via le profil `local_coder` :

```
/set-model dev qwen3-coder
```

Grimoire supporte Ollama sur `localhost:11434` avec 256K de contexte, compatible AMD ROCm.

---

## Agents & Archétypes

### Quels agents sont disponibles par défaut ?

Les agents BMM (méthode Grimoire) : analyst (Mary), pm (John), architect (Winston), dev (Amelia), qa (Quinn), sm (Bob), tech-writer (Paige), ux-designer (Sally).

Les agents CIS (créativité et innovation) : brainstorming-coach (Carson), rodin (Rodin), art-director (Iris), creative-problem-solver (Dr. Quinn), innovation-strategist (Victor), presentation-master (Caravaggio), storyteller (Sophia).

Les builders UDF : agent-builder (Bond), workflow-builder (Wendy), module-builder (Morgan).

### C'est quoi l'agent Rodin ?

**Rodin** est l'agent de débat socratique et d'anti-chambre d'écho. Il vous présente des contre-arguments structurés, remet en question les présupposés, et évite les biais de confirmation dans les décisions importantes.

Activé automatiquement par le SOG pour les décisions critiques ou via le mode PCE (Party mode).

### C'est quoi l'agent Art Director (Iris) ?

**Iris** est la directrice artistique — elle gère le style visuel, les hero sections, les room kits pixel art, et les reviews de cohérence de style pour les projets créatifs et les UIs.

### Quels archétypes sont disponibles ?

| Archétype | Description |
|---|---|
| `minimal` | Base universelle — agents core + template vierge |
| `web-app` | Application web full-stack |
| `creative-studio` | Création de contenu et design |
| `fix-loop` | Boucle de correction de bugs |
| `infra-ops` | Infrastructure et DevOps |

### Comment créer un agent personnalisé ?

Voir le guide [Créer un agent](creating-agents.md). La voie recommandée est l'UDF :

1. Décrivez votre besoin à Grimoire Master
2. Le SOG crée l'agent via agent-builder
3. Reviewez et complétez les prompts métier (`[TODO]`)
4. L'agent est disponible immédiatement

---

## Protocoles d'autonomie

### Qu'est-ce que le Completion Contract ?

Le CC est la règle fondatrice : un agent qui dit "terminé" doit prouver que c'est vrai. Avant chaque "fait", le stack est détecté et les vérifications (tests, lint, build) sont lancées automatiquement. Un CC FAIL déclenche une boucle de correction automatique.

### Pourquoi l'agent continue-t-il sans me demander ?

Le protocole **AORA** (Autonomous iteration loop) est actif : si une suite logique est évidente et reste en risque L1/L2 (local, réversible), l'agent l'exécute dans le même tour. Vous n'êtes sollicité que pour les décisions L3+ (impact partagé, irréversible).

### Comment forcer un mode [PLAN] ?

Tapez `[PLAN]` dans le chat. L'agent structure la solution et liste les risques sans toucher à rien. Tapez `[ACT]` pour reprendre le mode d'exécution directe.

---

## Plugins & Extensions

### Comment fonctionne le système de plugins ?

Grimoire utilise les [entry points](https://packaging.python.org/en/latest/specifications/entry-points/) Python :

- `grimoire.tools` — plugins d'outils
- `grimoire.backends` — plugins de backends mémoire

Vérifiez les plugins installés : `grimoire plugins list`

### Comment créer un plugin ?

Ajoutez un entry point dans le `pyproject.toml` de votre package :

```toml
[project.entry-points."grimoire.tools"]
mon-outil = "mon_package.module:ma_fonction"
```

---

## Migration & Compatibilité

### Comment migrer de v2 à v3 ?

Voir le guide de [Migration v2 → v3](migration-v2-v3.md).

```bash
grimoire upgrade --dry-run  # Voir le plan
grimoire upgrade            # Exécuter la migration
```

### L'ancienne commande NSO fonctionne-t-elle encore ?

Le NSO (Nervous System Orchestrator) a été remplacé par le **SOG** en v3. L'API Python est disponible via une couche de compatibilité temporaire, mais elle sera retirée en v4. Migrez vers le SOG via Grimoire Master.

### Où trouver le changelog complet ?

Consultez le [Changelog](changelog.md) ou sur GitHub dans `CHANGELOG.md`.

---

## Dépannage

### `grimoire doctor` signale des erreurs

Suivez les indications affichées. Les causes fréquentes :

- `project-context.yaml` manquant → `grimoire init`
- Répertoires manquants → `grimoire up`
- Backend configuré sans URL → vérifiez la section `memory:`

### L'auto-complétion ne fonctionne pas

```bash
grimoire completion install --shell bash
source ~/.bashrc
```

Pour plus d'aide, consultez le guide [Dépannage](troubleshooting.md).
