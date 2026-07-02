---
name: langfuse-tracing-setup
description: Installer et configurer l'export de télémétrie agentique vers Langfuse — variables d'environnement, vérification de connectivité, correspondance events.jsonl vers traces. À utiliser à la mise en place de l'observabilité d'un projet.
created: 2026-07-02
extension: langfuse
---

# Mise en place du tracing Langfuse

Cette skill guide la configuration de l'export de télémétrie locale vers
Langfuse, en respectant les conventions de `langfuse-observability.instructions.md`.

## Prérequis

- Une instance Langfuse (cloud ou self-hosted) et une paire de clés API.
- Le paquet `langfuse` installé dans le `.venv` du projet (fait par l'extension).

## Étapes

1. Renseigner `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` dans l'environnement du projet (jamais en dur dans un fichier versionné).
2. Vérifier la connectivité : `python3 -c "from langfuse import Langfuse; Langfuse().auth_check()"`.
3. Contrôler que le hook `langfuse-trace-export` est enregistré en mode `shadow` dans le registre de sécurité du projet.
4. Générer de l'activité (une session agent) puis vérifier dans Langfuse que les traces portent `mission_id`/`task_id`.

## Correspondance des données

| Source locale | Côté Langfuse |
| --- | --- |
| Ligne `events.jsonl` (hook-runtime) | Span d'une trace |
| `mission_id` | `trace_id` (corrélation) |
| Verdict d'un evidence pack | Score |

## Limites

- L'export est best-effort : une panne Langfuse ne doit produire aucune erreur visible dans le runtime.
- Le hook reste en shadow ; sa promotion est une décision du projet hôte, hors du périmètre de cette skill.
