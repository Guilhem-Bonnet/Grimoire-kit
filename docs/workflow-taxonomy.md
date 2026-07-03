# :material-sitemap: Taxonomie des workflows Grimoire

> Carte de lecture pour choisir la bonne structure d'exécution et de validation.

---

## Les quatre catégories

| Type | Format | Exécuteur | Exemples |
|---|---|---|---|
| **Playbook** | Markdown `.md` | LLM lit et suit | party-mode, brainstorming, elicitation |
| **Pipeline** | Python `.py` | CPU direct (stdlib) | dream.py, stigmergy.py, maintenance.py |
| **Orchestration** | Python + workers | ThreadPoolExecutor CPU | orchestrator.py concurrent-cpu |
| **SOG Dispatch** | Intent → routing | SOG → sub-agent(s) | Toute requête passant par Grimoire Master |

---

## :material-book-open-outline: Playbook (MD pour LLM)

Un playbook est un document Markdown que le LLM charge et suit comme un guide. Il ne génère **aucune exécution programmatique** — c'est le LLM qui interprète les instructions et agit dans l'IDE.

**Caractéristiques :**

- Format : `.md` (Markdown) ou `.yaml` (exécuté par `workflow.xml`)
- Le LLM est le moteur d'exécution
- Interaction humaine possible à chaque étape
- Pas de parallélisme — une seule session LLM
- Déclenchement : `@agent` ou `/grimoire-<workflow>` dans le chat

**Quand l'utiliser :** discussion multi-agent simulée, brainstorming structuré, reviews, elicitation d'exigences.

---

## :material-code-braces: Pipeline (Python exécutable)

Un pipeline est un script Python qui effectue des traitements locaux sans appel LLM. Il lit des fichiers, fait des calculs, écrit des résultats.

**Caractéristiques :**

- Format : `.py` (Python stdlib uniquement)
- Exécution directe en terminal ou via MCP tool
- Pas d'appel API LLM — CPU pur
- Peut tourner en background
- Testable avec `pytest`

**Quand l'utiliser :** analyse de fichiers, indexation, lint, validation de schémas, maintenance mémoire.

---

## :material-share-variant: Orchestration (multi-worker CPU)

L'orchestration coordonne plusieurs workers Python en parallèle via `ThreadPoolExecutor`. C'est du **parallélisme CPU local**, pas du multi-LLM.

**Caractéristiques :**

- Format : `.py` (Python)
- `ThreadPoolExecutor` pour les tâches parallèles
- Les workers chargent des personas mais **ne font pas d'appel API**
- Le LLM IDE reste la seule session active
- Voir [ADR-001](adr-001-no-multi-llm.md) pour les détails

**Quand l'utiliser :** validation croisée intensive sur de nombreux fichiers, traitement batch parallèle, harmony-check distribué.

---

## :material-router: SOG Dispatch (orchestration agentique)

Le SOG Dispatch est le mode d'exécution principal depuis la **v3**. Toute requête passant par Grimoire Master est automatiquement analysée, enrichie et routée vers le(s) sous-agent(s) optimal(aux).

**Caractéristiques :**

- Entrée : intention en langage naturel
- Routing : SOG → ARG → sub-agent(s) spécialisé(s)
- Invisible pour l'utilisateur — résultats agrégés avant présentation
- Protocoles intégrés : HUP (anti-hallucination), QEC (batch questions), CVTL (cross-validation)
- Suivi de durabilité via `udf-usage-tracker.json`

**Quand l'utiliser :** c'est le mode par défaut. Toute interaction avec Grimoire Master passe par le SOG Dispatch.

**Création dynamique (UDF) :** si aucun artefact existant ne couvre le besoin, le SOG crée automatiquement un agent, workflow, skill ou instruction — éphémère (7j) ou permanent selon le score de durabilité.

---

## :material-map-outline: Diagramme de sélection

```
Requête utilisateur
    │
    ├─ Parle au Grimoire Master ? ──→ SOG Dispatch
    │
    ├─ Script à lancer en terminal ? ──→ Pipeline (Python)
    │    └─ Avec workers parallèles ? ──→ Orchestration
    │
    └─ Workflow guidé dans le chat ? ──→ Playbook (MD)
         └─ Steps YAML ? ──→ Playbook (exécuté via workflow.xml)
```

---

## :material-help-circle-outline: Référence rapide

| Besoin | Type recommandé | Pourquoi |
|---|---|---|
| Tâche métier via le chat | SOG Dispatch | Routing automatique, HUP, AORA |
| Simulation multi-agent | Playbook | Le LLM simule les personas |
| Analyse/indexation de fichiers | Pipeline | CPU local, pas besoin de LLM |
| Validation croisée intensive | Orchestration | Paralléliser les workers CPU |
| Nouveau besoin sans artefact | UDF (via SOG) | Création dynamique éphémère ou permanente |
