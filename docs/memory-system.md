<p align="right"><a href="../README.md">README</a></p>

# <img src="assets/icons/brain.svg" width="32" height="32" alt=""> Système de Mémoire — Guide complet

## <img src="assets/icons/temple.svg" width="28" height="28" alt=""> Architecture

Le système de mémoire BMAD Custom Kit repose sur 3 couches complémentaires :

```
┌─────────────────────────────────────────┐
│          Mémoire Sémantique             │ ← Qdrant + sentence-transformers
│   (recherche par similarité, dispatch)  │    Score cosinus, embeddings locaux
├─────────────────────────────────────────┤
│          Mémoire Structurée             │ ← Fichiers Markdown
│   (learnings, décisions, contexte)      │    Lisible, versionnable, auditable
├─────────────────────────────────────────┤
│          Mémoire Éphémère               │ ← session-state.md, activity.jsonl
│   (état session, logs d'activité)       │    Continuité inter-sessions
└─────────────────────────────────────────┘
```

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/puzzle.svg" width="28" height="28" alt=""> Composants

### 1. `mem0-bridge.py` — Mémoire sémantique

**2 modes de fonctionnement :**

| Mode | Dépendances | Recherche | Performance |
|------|-------------|-----------|-------------|
| `local` | Aucune | Mots-clés fuzzy | Basique |
| `semantic` | sentence-transformers + qdrant-client | Embeddings cosine | Excellente |

**Commandes :**

```bash
# Ajouter une mémoire (ancien protocole — compatible)
python mem0-bridge.py add forge "Le module X nécessite le provider Y"

# Rechercher
python mem0-bridge.py search "comment configurer le provider"

# Dispatch sémantique — quel agent pour cette question ?
python mem0-bridge.py dispatch "les métriques Prometheus ne remontent pas"

# Statut complet
python mem0-bridge.py status

# Métriques cercle vertueux
python mem0-bridge.py stats
```

### Mémoire structurée multi-collection (BM-22) — Qdrant source de vérité

Le new protocol `remember`/`recall` organise la mémoire en **5 collections typesées** dans Qdrant. C'est l'interface principale que tous les agents doivent utiliser.

**Commandes :**

```bash
# Mémoriser dans une collection typée
python mem0-bridge.py remember \
    --type agent-learnings --agent forge \
    "Le provider hashicorp/aws doit être en version >= 5.0 pour les tags automatiques"

python mem0-bridge.py remember \
    --type decisions --agent atlas \
    "Choix Qdrant local (qdrant-client) plutôt que Pinecone — zéro API key" \
    --tags qdrant,memory

python mem0-bridge.py remember \
    --type failures --agent phoenix \
    "backup Longhorn échoué si le namespace n'a pas le label backup=true"

# Recherche sémantique — cross-collection par défaut
python mem0-bridge.py recall "configuration qdrant"

# Filtrer par collection
python mem0-bridge.py recall "backup" --type decisions

# Filtrer par agent
python mem0-bridge.py recall "terraform" --agent forge --limit 10

# Exporter une collection en Markdown
python mem0-bridge.py export-md --type agent-learnings \
    --output _bmad/_memory/agent-learnings/forge.md

# Importer un .md existant dans Qdrant
python mem0-bridge.py import-md _bmad/_memory/decisions-log.md --type decisions

# Initialiser toutes les collections (idémpotent, exécuté auto par bmad-init.sh)
python mem0-bridge.py init-collections
```

**5 collections :**

| Collection | Usage | Agent writes |
|-----------|-------|-------------|
| `{project}-shared-context` | Contexte infra/projet | atlas, tout agent |
| `{project}-decisions` | ADRs et décisions architecturales | tout agent |
| `{project}-agent-learnings` | Apprentissages par agent | agent spécifique |
| `{project}-failures` | Erreurs passées et comment les éviter | tout agent |
| `{project}-stories` | Stories / tickets | sm, dev |

**Stratégie de migration :**
- **Phase 1 (actuelle) — dual-write** : agents écrivent `remember` + `.md` en parallèle
- **Phase 2 — read-from-Qdrant** : agents lisent via `recall`, les `.md` sont générés par `export-md`
- **Phase 3 — source de vérité** : `.md` = exports READ-ONLY uniquement

**Déduplication** : L'upsert Qdrant est idempotent via UUID5 sur `(project, agent, text[:150])` — même texte écrit deux fois = une seule entrée.

**Detection de contradictions (Mnemo hook) :**

Chaque `add` déclenche automatiquement une recherche de mémoires contradictoires (score > 0.8 = quasi-doublon). Si trouvé, l'ancienne mémoire est marquée `superseded` et un warning est affiché.

### 2. `maintenance.py` — Santé et pruning

**Commandes :**

```bash
# Health-check rapide (rate-limité 1x/24h)
python maintenance.py health-check [--force]

# Audit complet (Mnemo)
python maintenance.py memory-audit

# Consolidation learnings (élimine doublons >85% similarité)
python maintenance.py consolidate-learnings

# Détecter le drift shared-context vs manifest
python maintenance.py context-drift

# Pruning complet
python maintenance.py prune-all

# Archiver mémoires > 30 jours
python maintenance.py archive 30
```

**Health-check automatique :**

Le health-check est exécuté automatiquement à chaque activation d'agent (via `agent-base.md` step 2). Il est rate-limité à 1x/24h et effectue :

1. Compactage doublons mémoire (auto-fix)
2. Vérification taille learnings (>100 lignes = warning)
3. Archivage décisions > 6 mois
4. Compactage activity.jsonl > 90 jours
5. Vérification hit rate recherche (<50% = warning)
6. Détection drift shared-context

### 3. `session-save.py` — Continuité inter-sessions

```bash
python session-save.py forge \
  --work "Déployé le monitoring complet" \
  --files "docker-compose.yml,prometheus.yml" \
  --next "Vérifier les targets Prometheus" \
  --duration "2h"
```

Écrit `session-state.md` (état courant, écrasé à chaque session) et archive dans `session-summaries/` (historique complet).

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/brain.svg" width="28" height="28" alt=""> Fichiers mémoire

| Fichier | Rôle | Qui écrit | Qui lit |
|---------|------|-----------|---------|
| `shared-context.md` | Contexte projet partagé | User, Atlas | Tous les agents |
| `decisions-log.md` | Log chronologique des décisions | Tous les agents | Atlas, Sentinel |
| `handoff-log.md` | Transferts inter-agents | Tous les agents | Tous les agents |
| `session-state.md` | État de la dernière session | session-save.py | Agent suivant |
| `agent-changelog.md` | Modifications aux fichiers agents | Agents modifiant | Sentinel |
| `memories.json` | Mémoire JSON (fallback) | mem0-bridge.py | mem0-bridge.py |
| `activity.jsonl` | Log d'activité détaillé | mem0-bridge.py | maintenance.py |
| `agent-learnings/*.md` | Apprentissages par agent | Chaque agent | Mnemo |

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> Cercle vertueux

Le système de mémoire forme un **cercle vertueux** :

```
Agent utilise mémoire → meilleur contexte → meilleure action
     ↑                                           ↓
   Mnemo consolide ←── Agent enregistre learning ←┘
```

**Métriques clés** (via `mem0-bridge.py stats`) :
- **Hit rate** : % de recherches avec score ≥ 0.3 → mesure la pertinence
- **Score moyen** : qualité globale des résultats sémantiques
- **Répartition agents** : couverture des domaines

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/brain.svg" width="28" height="28" alt=""> Configuration via `project-context.yaml`

Les scripts Python chargent automatiquement `project-context.yaml` pour :
- `USER_ID` et `APP_ID` (mem0-bridge.py)
- Pattern d'infrastructure (maintenance.py — détection contradictions)
- Nom du projet (session-save.py)
- Profils d'agents (mem0-bridge.py — dispatch sémantique)

```yaml
# Ajouter des agents au dispatch sémantique
agents:
  custom_agents:
    - name: "mon-agent"
      icon: "🤖"
      domain: "Mon Domaine"
      keywords: "keyword1 keyword2 keyword3"
```

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/wrench.svg" width="28" height="28" alt=""> Automatisations

### Au démarrage d'une session agent
1. `health-check` → auto-prune si nécessaire
2. `consolidate-learnings` → merge doublons du cycle précédent
3. `inbox check` → requêtes inter-agents en attente

### À chaque `mem0-bridge.py add`
1. Contradiction detection → supersede si doublon >0.8
2. Health-check background → rate-limité 1x/24h

### Pre-commit (si configuré)
1. `consolidate-learnings` → nettoyage avant commit
2. `context-drift` → vérification cohérence
