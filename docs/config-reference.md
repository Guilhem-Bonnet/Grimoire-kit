# Référence Configuration

Ce document décrit toutes les clés disponibles dans `project-context.yaml`.

## Structure de base

```yaml
project:
  name: "mon-projet"
  description: "Description du projet"
  type: "webapp"
  stack: [python, docker]
  repos:
    - name: "mon-projet"
      path: "."
      default_branch: "main"

user:
  name: "Dev Name"
  language: "Français"
  document_language: "Français"
  skill_level: "intermediate"

memory:
  backend: "auto"

agents:
  archetype: "minimal"
  custom_agents: []

installed_archetypes: []
```

---

## `project` (requis)

| Clé | Type | Défaut | Description |
|---|---|---|---|
| `name` | `string` | — | **Requis.** Nom du projet. |
| `description` | `string` | `""` | Description courte du projet. |
| `type` | `string` | `"webapp"` | Type de projet. Voir [valeurs valides](#projecttype). |
| `metaphor` | `string` | `""` | Métaphore pour les agents (optionnel). |
| `stack` | `list[string]` | `[]` | Technologies utilisées (ex: `[python, docker, react]`). |
| `repos` | `list[RepoConfig]` | `[]` | Dépôts liés au projet. |

### `project.type`

Valeurs acceptées :

| Valeur | Description |
|---|---|
| `webapp` | Application web (frontend + backend) |
| `api` | Service API REST / GraphQL |
| `service` | Microservice |
| `infrastructure` | Infrastructure as Code |
| `library` | Bibliothèque / SDK |
| `cli` | Outil en ligne de commande |
| `generic` | Autre type de projet |

### `project.repos[]`

| Clé | Type | Défaut | Description |
|---|---|---|---|
| `name` | `string` | — | **Requis.** Nom du dépôt. |
| `path` | `string` | `"."` | Chemin relatif vers le dépôt. |
| `default_branch` | `string` | `"main"` | Branche par défaut. |

---

## `user` (optionnel)

| Clé | Type | Défaut | Description |
|---|---|---|---|
| `name` | `string` | `""` | Nom de l'utilisateur. |
| `language` | `string` | `"Français"` | Langue de communication avec les agents. |
| `document_language` | `string` | `"Français"` | Langue de sortie des documents générés. |
| `skill_level` | `string` | `"intermediate"` | Niveau technique. Voir [valeurs valides](#userskill_level). |

### `user.skill_level`

| Valeur | Comportement des agents |
|---|---|
| `beginner` | Explications détaillées, exemples abondants |
| `intermediate` | Équilibre explications / concision |
| `expert` | Réponses concises, jargon technique |

---

## `memory` (optionnel)

| Clé | Type | Défaut | Description |
|---|---|---|---|
| `backend` | `string` | `"auto"` | Backend de mémoire. Voir [valeurs valides](#memorybackend). |
| `collection_prefix` | `string` | `"grimoire"` | Préfixe des collections mémoire. |
| `embedding_model` | `string` | `""` | Modèle d'embeddings (si applicable). |
| `qdrant_url` | `string` | `""` | URL du serveur Qdrant (requis si `backend: qdrant-server`). |
| `ollama_url` | `string` | `""` | URL du serveur Ollama (requis si `backend: ollama`). |

### `memory.backend`

| Valeur | Description | Dépendances |
|---|---|---|
| `auto` | Détection automatique du meilleur backend disponible | — |
| `local` | Stockage fichier local (JSON) | — |
| `qdrant-local` | Qdrant embarqué en mémoire | `pip install grimoire-kit[qdrant]` |
| `qdrant-server` | Serveur Qdrant distant | `pip install grimoire-kit[qdrant]` + `qdrant_url` |
| `ollama` | Embeddings via Ollama local | `pip install grimoire-kit[ollama]` + `ollama_url` |

---

## `agents` (optionnel)

| Clé | Type | Défaut | Description |
|---|---|---|---|
| `archetype` | `string` | `"minimal"` | Archétype d'agents à utiliser. Voir [valeurs valides](#agentsarchetype). |
| `custom_agents` | `list[string]` | `[]` | Liste d'agents personnalisés à charger. |

### `agents.archetype`

| Valeur | Description |
|---|---|
| `minimal` | Configuration minimale — un seul agent orchestrateur |
| `web-app` | Stack web complète — frontend, backend, DB, DevOps |
| `creative-studio` | Création de contenu — rédaction, design, storytelling |
| `fix-loop` | Boucle de correction — diagnostic, fix, test, validation |
| `infra-ops` | Infrastructure — IaC, monitoring, SRE |
| `meta` | Méta-développement — agents qui créent des agents |
| `stack` | Stack technique personnalisée |
| `features` | Feature factory — product, dev, QA, launch |
| `platform-engineering` | Platform engineering — DX, CI/CD, observabilité |

---

## `installed_archetypes` (optionnel)

Liste des archétypes installés dans le projet :

```yaml
installed_archetypes:
  - minimal
  - web-app
```

---

## Variables d'environnement

Ces variables overrident les valeurs du fichier de config :

| Variable | Description |
|---|---|
| `GRIMOIRE_LOG_LEVEL` | Niveau de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `GRIMOIRE_DEBUG` | Active le mode debug avec tracebacks complets |
| `GRIMOIRE_NO_COLOR` | Désactive la coloration Rich |
| `GRIMOIRE_PROJECT_ROOT` | Répertoire racine du projet (override la recherche auto) |

---

## Validation

Validez votre fichier avec :

```bash
# Validation de base
grimoire validate .

# Lint avancé (sortie structurée)
grimoire lint .

# Lint en JSON (pour CI / scripting)
grimoire lint . --format json
```
