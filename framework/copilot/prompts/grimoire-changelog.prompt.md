---
description: 'Génère un CHANGELOG structuré depuis git history et les décisions Grimoire'
agent: 'agent'
tools: ['read', 'execute']
---

Génère un CHANGELOG structuré pour le projet Grimoire :

## 1. Collecte git

```bash
cd {project-root} && git log --oneline --no-merges -30 --pretty=format:"%h %ad %s" --date=short
```

## 2. Décisions Grimoire

Lis `{project-root}/_grimoire/_memory/decisions-log.md` pour les décisions architecturales récentes.

## 3. Version actuelle

Lis `{project-root}/project-context.yaml` pour récupérer le nom du projet.
Si un fichier `version.txt` ou `pyproject.toml` existe, extraire la version actuelle.

## 4. CHANGELOG existant

Si `{project-root}/CHANGELOG.md` existe, lis les 30 premières lignes pour comprendre le format utilisé.

## 5. Génération

Génère un CHANGELOG au format **Keep a Changelog** (https://keepachangelog.com) :

```markdown
# Changelog

## [Unreleased]

### Added
- ...

### Changed
- ...

### Fixed
- ...

### Security
- ...
```

Règles de formatage :
- Regroupe les commits par type (feat, fix, chore, etc.)
- Traduis les messages Conventional Commits en langue naturelle (français)
- Inclus les décisions architecturales importantes de `decisions-log.md`
- Ne mets que les changements significatifs (pas les chores de style)
- Formate les références de commits en `[abc1234]`

Propose la mise à jour de `{project-root}/CHANGELOG.md` avec le contenu généré.
