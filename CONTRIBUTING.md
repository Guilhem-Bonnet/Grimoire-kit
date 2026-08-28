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
# Setup complet (une seule fois) — crée le venv et installe tout
make dev

# Ou manuellement (venv déjà créé, voir Prérequis ci-dessus)
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# Raccourcis Makefile
make check      # lint + typecheck + tests
make test-cov   # tests avec rapport de couverture
make pre-push   # validation complète avant push

# Commandes individuelles
make lint        # ruff check
make typecheck   # mypy --strict
make test        # pytest unit
make format      # ruff format
```

## Conventions

### Code

- **Dataclasses** avec `frozen=True, slots=True` pour les modèles de données
- **Type hints** sur toutes les fonctions publiques
- **f-strings** (pas de `.format()` ni `%`)
- **Imports** : `from __future__ import annotations` en premier
- **Exceptions** : hériter de `GrimoireError` (voir `grimoire.core.exceptions`)

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

## Vérifier qu'une poussée est bien arrivée

Après un `git push` sur une branche de PR, **la référence de branche est la
source fiable, pas l'objet PR**. Mesuré sur ce dépôt : à `t+0`, `GET /pulls/{n}`
renvoie encore l'ancien `head.sha` pendant que `GET /git/ref/heads/{branche}` a
déjà le nouveau. La convergence prend moins de 20 s, mais dans cet intervalle
`gh pr view` affiche un état périmé — tête d'avance, `mergeable` calculé sur
l'ancien contenu, souvent `CONFLICTING` alors que `git merge-tree` ne trouve
rien.

```bash
scripts/pr-head-check.py <numéro-de-pr>
```

Le script compare la référence de branche, l'objet PR et le SHA local, et dit
laquelle des trois situations vous êtes : synchronisé, décalage de propagation,
ou **écrivain concurrent** — quelqu'un d'autre a poussé sur la même branche
depuis votre `push`.

Ce dernier cas est le piège coûteux. Plusieurs sessions travaillant en parallèle
sur ce dépôt poussent sur les mêmes branches : quatre PR ont été fermées et
rouvertes ici pour un « objet PR cassé » qui n'existait pas — la branche allait
bien, une autre session écrivait dessus. Fermer une PR ne répare rien et lui fait
perdre son historique de revue.

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
