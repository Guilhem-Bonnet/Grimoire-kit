# Contributing — Grimoire Kit v3

## Bienvenue

Tu veux améliorer Grimoire Kit ? Voici comment contribuer.

## Prérequis

- Python 3.12+
- Git

```bash
git clone https://github.com/Guilhem-Bonnet/Grimoire-kit.git
cd Grimoire-kit
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Structure du projet

```
grimoire-kit/
├── src/grimoire/
│   ├── core/           # Config, Project, Scanner, Merge, Validator
│   ├── cli/            # CLI Typer (app.py, cmd_upgrade.py, cmd_merge.py)
│   ├── tools/          # HarmonyCheck, PreflightCheck, MemoryLint, etc.
│   ├── memory/         # MemoryManager + backends
│   ├── mcp/            # Serveur MCP
│   └── registry/       # AgentRegistry, LocalRegistry
├── tests/unit/         # Tests pytest (640+)
├── archetypes/         # Archétypes de projets
├── docs/               # Documentation
└── pyproject.toml      # Configuration du package
```

## Workflow de développement

```bash
# Lancer les tests
pytest tests/unit/ -q --tb=short -x

# Lint
ruff check src/ tests/

# Lint + fix automatique
ruff check src/ tests/ --fix
```

## Conventions

### Code

- **Dataclasses** avec `frozen=True, slots=True` pour les modèles de données
- **Type hints** sur toutes les fonctions publiques
- **f-strings** (pas de `.format()` ni `%`)
- **Imports** : `from __future__ import annotations` en premier
- **Exceptions** : hériter de `GrimoireError` (voir `bmad.core.exceptions`)

### Tests

- Un fichier `test_<module>.py` par module
- Fixtures pytest partagées dans `conftest.py`
- Viser > 90% de couverture sur le code nouveau
- Pattern : `TestClassName.test_specific_behavior`

### Commits

```
type(scope): description courte

Exemples:
feat(cli): add grimoire merge command
fix(core): handle empty YAML gracefully
test(tools): add HarmonyCheck edge cases
docs: update getting-started for v3
```

Types : `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

## Ajouter un outil

1. Créer `src/grimoire/tools/mon_outil.py` avec une classe publique
2. Exporter dans `src/grimoire/tools/__init__.py`
3. Créer `tests/unit/tools/test_mon_outil.py`
4. Documenter dans `docs/sdk-guide.md`

## Ajouter une commande CLI

1. Créer `src/grimoire/cli/cmd_xxx.py` avec les fonctions métier
2. Ajouter la commande dans `src/grimoire/cli/app.py`
3. Créer `tests/unit/cli/test_cmd_xxx.py`
4. Documenter dans `docs/getting-started.md` (table CLI)

## Ajouter un archétype

1. Créer `archetypes/<nom>/` avec `agents/` et `README.md`
2. Ajouter dans le `LocalRegistry`
3. Documenter dans `docs/archetype-guide.md`

## Questions ?

Ouvrir une issue sur GitHub.
