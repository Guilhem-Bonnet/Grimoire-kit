# ADR-001 : Pourquoi l'orchestration n'est pas multi-LLM

> **Date** : 2025-01-XX  
> **Statut** : Accepté  
> **Contexte** : Grimoire Kit v2.4  
> **Auteur** : Party-mode — Winston (Architecte) + Amelia (Dev)

## Contexte

L'orchestrator.py propose trois modes d'exécution : `simulated`, `sequential`, et `concurrent-cpu` (anciennement nommé `parallel`). Le nom "parallel" suggérait que des LLMs distincts étaient invoqués en parallèle — ce qui n'est **pas** le cas.

## Décision

Le mode `concurrent-cpu` utilise `ThreadPoolExecutor` pour paralléliser des traitements Python (workers), **pas** des appels LLM. Voici la réalité :

| Composant | Comportement réel |
|-----------|------------------|
| `orchestrator.py` mode `concurrent-cpu` | ThreadPoolExecutor local — pas d'appel API LLM |
| `agent-worker.py` | Charge une persona depuis un fichier, produit un résultat string |
| `message-bus.py` | Seul `InProcessBus` (queue mémoire) est implémenté ; Redis/NATS sont des stubs |
| `background-tasks.py` | Écrit des JSON sur disque, ne lance pas de vrais processus background |
| Party Mode | Un seul LLM simule plusieurs personas — c'est le pattern IDE correct |

## Raison

Dans un environnement IDE (VS Code + Copilot), une **seule session LLM** est active à la fois. Le multi-LLM réel nécessiterait :

1. **Plusieurs API keys / sessions simultanées** — impossible dans l'IDE
2. **Un serveur d'orchestration externe** (LangGraph, CrewAI) — hors scope IDE
3. **Ou le MCP Server** — nos tools sont exposés mais appelés séquentiellement par le LLM

## Parallélisme réellement disponible

| Stratégie | Status | Description |
|-----------|--------|-------------|
| MCP batch tools | ✅ Existant | Le LLM Copilot peut appeler plusieurs MCP tools en batch |
| Background CPU (dream, indexing, stigmergy) | ✅ Existant | Scripts Python qui tournent pendant que l'user travaille |
| Multi-IDE via A2A | 🟡 Prototype | 2 IDE ouverts sur le même projet = 2 LLM réels |
| API externe (Claude/OpenAI API) | 🟡 Possible | Spawner des agents via API, coûteux |

## Conséquences

- Le mode `parallel` a été renommé en `concurrent-cpu` pour refléter honnêtement son fonctionnement
- La documentation et les docstrings indiquent clairement "parallélisme CPU, PAS multi-LLM"
- L'investissement futur pour du vrai multi-LLM passera par le MCP proxy (`mcp-proxy.py`)

## Alternatives rejetées

- **Prétendre du multi-LLM** : confus et trompeur
- **Supprimer le mode parallel** : le parallélisme CPU reste utile pour les tâches lourdes (évaporation, indexation, validation)
- **Migrer vers LangGraph/CrewAI** : hors scope, ajoute une dépendance serveur externe
