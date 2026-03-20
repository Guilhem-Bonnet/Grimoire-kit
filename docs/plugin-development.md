# Développement de plugins

Ce guide couvre la création de plugins Grimoire : outils, backends mémoire et archétypes personnalisés.

## Architecture des plugins

Grimoire utilise les [entry points Python](https://packaging.python.org/en/latest/specifications/entry-points/) pour la découverte de plugins. Trois groupes sont disponibles :

| Groupe | Rôle | Exemple |
|---|---|---|
| `grimoire.tools` | Outils CLI et MCP | Linter YAML custom, scanner sécurité |
| `grimoire.backends` | Backends mémoire | Redis, PostgreSQL, ChromaDB |
| `grimoire.archetypes` | Archétypes d'agents | data-science, mobile-app |

---

## Créer un plugin outil

### 1. Structure du projet

```
grimoire-plugin-example/
├── pyproject.toml
├── src/
│   └── grimoire_plugin_example/
│       ├── __init__.py
│       └── tool.py
└── tests/
    └── test_tool.py
```

### 2. Implémenter l'outil

```python
# src/grimoire_plugin_example/tool.py
"""Exemple de plugin outil pour Grimoire."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def run(project_root: Path, **kwargs: Any) -> dict[str, Any]:
    """Point d'entrée du tool — exécuté par grimoire.

    Parameters
    ----------
    project_root:
        Racine du projet Grimoire.
    **kwargs:
        Arguments supplémentaires passés par le CLI.

    Returns
    -------
    dict:
        Résultat structuré de l'outil.
    """
    config_path = project_root / "project-context.yaml"
    return {
        "status": "ok",
        "config_exists": config_path.is_file(),
    }
```

### 3. Déclarer l'entry point

```toml
# pyproject.toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "grimoire-plugin-example"
version = "0.1.0"
dependencies = ["grimoire-kit>=3.0"]

[project.entry-points."grimoire.tools"]
my_example_tool = "grimoire_plugin_example.tool"
```

### 4. Tester

```python
# tests/test_tool.py
from pathlib import Path

from grimoire_plugin_example.tool import run


def test_run_returns_status(tmp_path: Path) -> None:
    result = run(tmp_path)
    assert result["status"] == "ok"
    assert result["config_exists"] is False


def test_run_detects_config(tmp_path: Path) -> None:
    (tmp_path / "project-context.yaml").write_text("project:\n  name: test\n")
    result = run(tmp_path)
    assert result["config_exists"] is True
```

### 5. Vérifier la découverte

```bash
# Activer votre venv si ce n'est pas déjà fait (Ubuntu/Debian: requis, PEP 668)
source .venv/bin/activate
pip install -e .
grimoire plugins list
# → my_example_tool  grimoire_plugin_example.tool
```

---

## Créer un backend mémoire

Les backends implémentent l'interface mémoire Grimoire :

```python
# src/grimoire_plugin_redis/backend.py
from __future__ import annotations

from typing import Any


class RedisBackend:
    """Backend mémoire Redis pour Grimoire."""

    def __init__(self, url: str = "redis://localhost:6379", **kwargs: Any) -> None:
        self.url = url

    def store(self, collection: str, key: str, data: dict[str, Any]) -> None:
        """Stocke un document dans une collection."""
        ...

    def retrieve(self, collection: str, key: str) -> dict[str, Any] | None:
        """Récupère un document par clé."""
        ...

    def search(self, collection: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Recherche sémantique dans une collection."""
        ...
```

```toml
# pyproject.toml
[project.entry-points."grimoire.backends"]
redis = "grimoire_plugin_redis.backend:RedisBackend"
```

---

## Créer un archétype

Un archétype est un dossier contenant :

```
my-archetype/
├── archetype.dna.yaml    # Métadonnées et config
├── agents/               # Définitions d'agents
│   ├── my-agent.md
│   └── ...
├── workflows/            # Workflows optionnels
│   └── main.prompt.md
└── README.md
```

### `archetype.dna.yaml`

```yaml
id: my-archetype
name: "Mon Archétype"
description: "Description de l'archétype."
version: "1.0.0"

agents:
  - id: my-agent
    file: agents/my-agent.md
    persona: "Expert spécialisé"

defaults:
  project:
    type: "webapp"
  memory:
    backend: "auto"
```

Pour distribuer via entry point :

```toml
[project.entry-points."grimoire.archetypes"]
my_archetype = "grimoire_plugin_archetype:get_path"
```

```python
# grimoire_plugin_archetype/__init__.py
from pathlib import Path

def get_path() -> Path:
    return Path(__file__).parent / "archetype-data"
```

---

## Bonnes pratiques

1. **Nommage** : Préfixer le package avec `grimoire-plugin-` ou `grimoire-ext-`
2. **Dépendances** : Déclarer `grimoire-kit>=3.0` comme dépendance minimale
3. **Tests** : Écrire des tests unitaires avec `tmp_path` pour l'isolation
4. **Types** : Utiliser `mypy --strict` pour la vérification de types
5. **Documentation** : Inclure un `README.md` avec exemples d'installation et d'usage
6. **CI** : Utiliser `ruff` pour le linting (compatible avec le monorepo Grimoire)

---

## Commandes utiles

```bash
# Lister les plugins installés
grimoire plugins list

# Vérifier que le plugin est détecté (JSON)
grimoire plugins list --output json

# Valider le projet après installation du plugin
grimoire doctor
```
