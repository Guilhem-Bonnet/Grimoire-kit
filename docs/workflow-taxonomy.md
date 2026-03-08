<p align="right"><a href="../README.md">README</a></p>

# <img src="assets/icons/workflow.svg" width="32" height="32" alt=""> Taxonomie des workflows Grimoire

> **ADR-002** — Clarification des trois types d'exécution dans le Grimoire Kit.

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> Les trois catégories

| Type | Format | Exécuteur | Exemple |
|------|--------|-----------|---------|
| **Playbook** | Markdown (.md) | Le LLM lit et suit les instructions | party-mode, brainstorming, advanced-elicitation |
| **Pipeline** | Python (.py) | Exécution CPU directe (stdlib) | dream.py, stigmergy.py, r-and-d.py, session-lifecycle.py |
| **Orchestration** | Python + workers | ThreadPoolExecutor CPU (PAS multi-LLM) | orchestrator.py mode concurrent-cpu |

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> Playbook (MD pour LLM)

Un playbook est un document Markdown que le LLM charge et suit comme un guide. Il ne génère **aucune exécution programmatique** — c'est le LLM qui interprète les instructions et agit dans l'IDE.

**Caractéristiques :**
- Format : `.md` (Markdown) ou `.yaml` (exécuté par `workflow.xml`)
- Le LLM est le "moteur d'exécution"
- Interaction humaine possible à chaque étape
- Pas de parallélisme — une seule session LLM
- Exemples : party-mode, brainstorming, elicitation, reviews

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/workflow.svg" width="28" height="28" alt=""> Pipeline (Python exécutable)

Un pipeline est un script Python qui effectue des traitements locaux sans appel LLM. Il lit des fichiers, fait des calculs, écrit des résultats.

**Caractéristiques :**
- Format : `.py` (Python stdlib)
- Exécution directe en terminal ou via MCP tool
- Pas d'appel API LLM
- Peut être lancé en background
- Exemples : dream.py, stigmergy.py, rag-indexer.py, maintenance.py

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/boomerang.svg" width="28" height="28" alt=""> Orchestration (multi-worker CPU)

L'orchestration coordonne plusieurs workers Python en parallèle via `ThreadPoolExecutor`. Ce n'est **PAS** du multi-LLM — c'est du parallélisme CPU local.

**Caractéristiques :**
- Format : `.py` (Python)
- `ThreadPoolExecutor` pour les tâches parallèles
- Les workers chargent des personas mais ne font PAS d'appel API
- Le LLM IDE reste la seule session active
- Voir [ADR-001](adr-001-no-multi-llm.md) pour les détails

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/chart.svg" width="28" height="28" alt=""> Diagramme

```
Utilisateur
    │
    ├── Parle au LLM → LLM suit un PLAYBOOK (MD)
    │                     └── Appelle des MCP tools → PIPELINE (Python)
    │
    └── Lance un script → PIPELINE directe (terminal)
                            └── Peut lancer une ORCHESTRATION (ThreadPool workers)
```

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/lightbulb.svg" width="28" height="28" alt=""> Quand utiliser quoi ?

| Besoin | Type | Pourquoi |
|--------|------|----------|
| Discussion multi-agent simulée | Playbook | Le LLM simule les personas |
| Analyse/indexation de fichiers | Pipeline | Traitement CPU, pas besoin de LLM |
| Validation croisée intensive | Orchestration | Paralléliser les workers CPU |
| Vrai multi-LLM | Futur | Via MCP proxy externe (pas encore implémenté) |
