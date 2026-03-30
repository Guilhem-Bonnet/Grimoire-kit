---
description: 'Validation pre-push — intégrité agents, qualité code, mémoire, tests si disponibles'
agent: 'agent'
tools: ['read', 'search', 'execute']
---

Checklist de validation avant push Grimoire :

## 1. Intégrité des agents

Vérifie que les fichiers agents sont cohérents :
```bash
cd {project-root} && for f in .github/agents/*.agent.md; do echo "--- $f ---"; head -5 "$f"; done
```

Chaque agent doit avoir :
- Un frontmatter YAML avec `description:`
- Au moins une instruction de chargement du fichier interne

## 2. Configuration projet

Vérifie que `project-context.yaml` est valide :
```bash
cd {project-root} && python3 -c "import yaml; yaml.safe_load(open('project-context.yaml'))" && echo "YAML OK" || echo "YAML INVALIDE"
```

## 3. Mémoire cohérente

Vérifie que la mémoire n'a pas de données corrompues :
- `_grimoire/_memory/config.yaml` — YAML valide ?
- `_grimoire/_memory/shared-context.md` — non vide ?

## 4. Tests (si présents)

Si un dossier `tests/` existe :
```bash
cd {project-root} && python3 -m pytest tests/ -q --tb=line -x 2>/dev/null || echo "Tests non disponibles"
```

## 5. Lint (si configuré)

Si `pyproject.toml` ou `ruff.toml` existe :
```bash
cd {project-root} && python3 -m ruff check . --statistics -q 2>/dev/null || echo "Ruff non configuré"
```

## 6. Git diff

```bash
cd {project-root} && git diff --stat HEAD 2>/dev/null | head -20
```

Présente un résumé :
- ✅ **Prêt à push** — tous les checks passent
- ❌ **Bloquant** — liste des problèmes critiques à corriger
- ⚠️  **Avertissements** — points d'attention non bloquants
