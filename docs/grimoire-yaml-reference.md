# Référence YAML — `project-context.yaml`

> Schéma complet du fichier de configuration Grimoire Kit v3.

## Structure globale

```yaml
project:
  name: "Mon Projet"
  description: "Description courte"
  type: "webapp"
  metaphor: "forteresse"
  stack:
    - Python
    - FastAPI
    - PostgreSQL
  repos:
    - name: backend
      path: "."
      default_branch: main

user:
  name: "Alice"
  language: "Français"
  document_language: "Français"
  skill_level: "intermediate"

memory:
  backend: "auto"
  collection_prefix: "grimoire"
  embedding_model: ""
  qdrant_url: ""
  ollama_url: ""

agents:
  archetype: "minimal"
  custom_agents:
    - my-custom-agent
```

## Section `project`

| Clé | Type | Défaut | Description |
| ----- | ------ | -------- | ------------- |
| `name` | string | **requis** | Nom du projet |
| `description` | string | `""` | Description courte |
| `type` | string | `"webapp"` | Type de projet (`webapp`, `api`, `library`, `infra`, etc.) |
| `metaphor` | string | `""` | Métaphore structurante pour guider les agents |
| `stack` | list[string] | `[]` | Technologies utilisées |
| `repos` | list[repo] | `[]` | Dépôts du projet |

### Entrée `repos`

| Clé | Type | Défaut | Description |
| ----- | ------ | -------- | ------------- |
| `name` | string | **requis** | Nom du dépôt |
| `path` | string | `"."` | Chemin relatif |
| `default_branch` | string | `"main"` | Branche par défaut |

## Section `user`

| Clé | Type | Défaut | Description |
| ----- | ------ | -------- | ------------- |
| `name` | string | `""` | Nom de l'utilisateur |
| `language` | string | `"Français"` | Langue de communication des agents |
| `document_language` | string | `"Français"` | Langue des documents générés |
| `skill_level` | enum | `"intermediate"` | `beginner` \| `intermediate` \| `expert` |

`skill_level` adapte la verbosité : `beginner` = pédagogique, `expert` = concis.

## Section `memory`

| Clé | Type | Défaut | Description |
| ----- | ------ | -------- | ------------- |
| `backend` | enum | `"auto"` | Backend mémoire |
| `collection_prefix` | string | `"grimoire"` | Préfixe des collections |
| `embedding_model` | string | `""` | Modèle d'embedding (optionnel) |
| `qdrant_url` | string | `""` | URL Qdrant (mode `qdrant-server`) |
| `weaviate_url` | string | `""` | URL Weaviate (mode `weaviate-server`) |
| `weaviate_collection` | string | `""` | Collection Weaviate, ou `collection_prefix` si vide |
| `ollama_url` | string | `""` | URL Ollama (mode `ollama`) |
| `neo4j_uri` | string | `""` | URI Bolt Neo4j pour les couches graphe |
| `neo4j_user` | string | `"neo4j"` | Utilisateur Neo4j |
| `neo4j_password_env` | string | `"GRIMOIRE_NEO4J_PASSWORD"` | Variable d'environnement du mot de passe Neo4j |
| `knowledge_graph` | enum | `"sqlite-sidecar"` | Graphe de faits structurés |
| `memory_graph` | enum | `"sqlite-sidecar"` | Projection graphe des souvenirs |
| `code_graph` | enum | `"planned"` | Graphe de code prévu |
| `task_memory` | enum | `"planned"` | Mémoire de tâches prévue |

### Backends disponibles

| Backend | Description |
| --------- | ------------- |
| `auto` | Détection automatique du meilleur backend disponible |
| `local` | Stockage fichier local (pas de dépendance externe) |
| `qdrant-local` | Qdrant en mode local (in-memory) |
| `qdrant-server` | Qdrant serveur distant |
| `weaviate-server` | Weaviate serveur distant |
| `ollama` | Ollama pour les embeddings |

## Section `agents`

| Clé | Type | Défaut | Description |
| ----- | ------ | -------- | ------------- |
| `archetype` | string | `"minimal"` | Archétype de base |
| `custom_agents` | list[string] | `[]` | Agents personnalisés à charger |

### Archétypes

| Archétype | Description |
| ----------- | ------------- |
| `minimal` | Agents de base (orchestrateur, sentinelle, mémoire) |
| `web-app` | Stack web avec agents frontend/backend |
| `infra-ops` | Infrastructure et DevOps |
| `creative-studio` | Création de contenu |
| `fix-loop` | Boucle de correction certifiée |

## Sections supplémentaires

Toute clé non reconnue est préservée dans `extra` et accessible via le SDK :

```yaml
# Sections custom — accessibles via config.extra["mon_extension"]
mon_extension:
  option_a: true
  option_b: "valeur"
```

```python
config = GrimoireConfig.from_yaml("project-context.yaml")
ext = config.extra.get("mon_extension", {})
```

## Validation

```bash
# Valider le fichier YAML
grimoire validate

# Validation programmatique
from grimoire.core.validator import validate_config
errors = validate_config(config)
for e in errors:
    print(f"{e.field}: {e.message}")
```

## Synchronisation avec `grimoire setup`

Les valeurs de la section `user` (et `project.name`) servent de source de vérité.
La commande `grimoire setup` propage ces valeurs dans tous les fichiers de configuration du projet :

| Fichier cible | Champs synchronisés |
| --------------- | -------------------- |
| `_grimoire-runtime/bmm/config.yaml` | `project_name`, `user_name`, `communication_language`, `document_output_language`, `user_skill_level` |
| `_grimoire-runtime/{core,cis,tea,bmb}/config.yaml` | `user_name`, `communication_language`, `document_output_language` |
| `_grimoire-runtime/_memory/config.yaml` | `user_name`, `communication_language`, `document_output_language` |
| `.github/copilot-instructions.md` | Project, User, Communication Language, Document Output Language, User Skill Level |

```bash
# Appliquer la synchronisation
grimoire setup

# Audit sans modification (exit 1 si désynchronisé)
grimoire setup --check

# Passer des valeurs directement
grimoire setup --user "Alice" --lang "English" --skill-level beginner

# Sortie JSON (CI/CD)
grimoire setup --check --json
```

> **Bonne pratique** : après toute modification de `project-context.yaml`, lancer `grimoire setup` pour propager les changements.

## Voir aussi

- [Guide de démarrage](getting-started.md)
- [Guide SDK](sdk-guide.md)
