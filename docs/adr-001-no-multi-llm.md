<p align="right"><a href="../README.md">README</a></p>

# <img src="assets/icons/clipboard.svg" width="32" height="32" alt=""> ADR-001 : Pourquoi l'orchestration n'est pas multi-LLM

> **Date** : 2025-01-XX 
> **Statut** : Accepté 
> **Contexte** : Grimoire Kit v2.4 
> **Auteur** : Party-mode — Winston (Architecte) + Amelia (Dev)

## <img src="assets/icons/brain.svg" width="28" height="28" alt=""> Contexte

L'orchestrator.py propose trois modes d'exécution : `simulated`, `sequential`, et `concurrent-cpu` (anciennement nommé `parallel`). Le nom "parallel" suggérait que des LLMs distincts étaient invoqués en parallèle — ce qui n'est **pas** le cas.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/clipboard.svg" width="28" height="28" alt=""> Décision

Le mode `concurrent-cpu` utilise `ThreadPoolExecutor` pour paralléliser des traitements Python (workers), **pas** des appels LLM. Voici la réalité :

| Composant | Comportement réel |
|-----------|------------------|
| `orchestrator.py` mode `concurrent-cpu` | ThreadPoolExecutor local — pas d'appel API LLM |
| `agent-worker.py` | Charge une persona depuis un fichier, produit un résultat string |
| `message-bus.py` | Seul `InProcessBus` (queue mémoire) est implémenté ; Redis/NATS sont des stubs |
| `background-tasks.py` | Écrit des JSON sur disque, ne lance pas de vrais processus background |
| Party Mode | Un seul LLM simule plusieurs personas — c'est le pattern IDE correct |

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/lightbulb.svg" width="28" height="28" alt=""> Raison

Dans un environnement IDE (VS Code + Copilot), une **seule session LLM** est active à la fois. Le multi-LLM réel nécessiterait :

1. **Plusieurs API keys / sessions simultanées** — impossible dans l'IDE
2. **Un serveur d'orchestration externe** (LangGraph, CrewAI) — hors scope IDE
3. **Ou le MCP Server** — nos tools sont exposés mais appelés séquentiellement par le LLM

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/cognition.svg" width="28" height="28" alt=""> Parallélisme réellement disponible

| Stratégie | Status | Description |
|-----------|--------|-------------|
| MCP batch tools | &#x2713; Existant | Le LLM Copilot peut appeler plusieurs MCP tools en batch |
| Background CPU (dream, indexing, stigmergy) | &#x2713; Existant | Scripts Python qui tournent pendant que l'user travaille |
| Multi-IDE via A2A | Prototype | 2 IDE ouverts sur le même projet = 2 LLM réels |
| API externe (Claude/OpenAI API) | Possible | Spawner des agents via API, coûteux |

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/clipboard.svg" width="28" height="28" alt=""> Conséquences

- Le mode `parallel` a été renommé en `concurrent-cpu` pour refléter honnêtement son fonctionnement
- La documentation et les docstrings indiquent clairement "parallélisme CPU, PAS multi-LLM"
- L'investissement futur pour du vrai multi-LLM passera par le MCP proxy (`mcp-proxy.py`)

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/clipboard.svg" width="28" height="28" alt=""> Alternatives rejetées

- **Prétendre du multi-LLM** : confus et trompeur
- **Supprimer le mode parallel** : le parallélisme CPU reste utile pour les tâches lourdes (évaporation, indexation, validation)
- **Migrer vers LangGraph/CrewAI** : hors scope, ajoute une dépendance serveur externe
