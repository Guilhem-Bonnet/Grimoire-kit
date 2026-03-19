<p align="right"><a href="../../README.md">README</a> · <a href="../../docs">Docs</a></p>

# <img src="../../docs/assets/icons/folder-tree.svg" width="32" height="32" alt=""> Repo Map Generator — BM-05

> Génère une carte statique du dépôt consultable par tous les agents, inspiré du Repo Map d'Aider.

## <img src="../../docs/assets/icons/lightbulb.svg" width="28" height="28" alt=""> Concept

La **Repo Map** est un index hiérarchique du projet : arborescence, symboles exportés, relations entre modules. Elle sert de "GPS du code" aux agents, réduisant les hallucinations de chemins et accélérant la navigation.

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/bolt.svg" width="28" height="28" alt=""> Déclenchement

L'agent Atlas peut générer la Repo Map via la commande `[RM]` de son menu, ou automatiquement lors d'un briefing de session si la map a plus de 24h.

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/lightbulb.svg" width="28" height="28" alt=""> Stratégies de génération

### Stratégie 1 — `ctags` (universel, recommandé)

```bash
# Prérequis : universal-ctags
ctags --recurse --output-format=json \
      --fields="{name}{input}{pattern}{line}{kind}{scope}" \
      --languages=Python,Go,TypeScript,JavaScript,Terraform,YAML,Markdown \
      -f - . 2>/dev/null \
  | python3 -c "
import json, sys, collections
tags = [json.loads(l) for l in sys.stdin if l.strip()]
by_file = collections.defaultdict(list)
for t in tags:
    by_file[t['path']].append({'name': t['name'], 'kind': t.get('kind','?'), 'line': t.get('line',0)})
print(json.dumps(dict(by_file), indent=2))
" > _grimoire-output/repo-map.json
```

### Stratégie 2 — `find` + `grep` (sans dépendances)

```bash
# Génère une arborescence annotée sans ctags
find . \( -path "./.git" -o -path "./node_modules" -o -path "./_grimoire-output" \) -prune \
       -o -type f \( -name "*.go" -o -name "*.ts" -o -name "*.py" -o -name "*.tf" -o -name "*.md" \) \
       -print > /tmp/grimoire-files.txt

# Fonctions/classes exportées par fichier
while IFS= read -r f; do
  echo "### $f"
  grep -E "^(func |class |def |export (function|class|const)|variable \"[A-Z])" "$f" 2>/dev/null | head -20
  echo ""
done < /tmp/grimoire-files.txt > _grimoire-output/repo-map.md
```

### Stratégie 3 — Tree-sitter (précision maximale, opt-in)

```bash
# Nécessite tree-sitter CLI + grammaires installées
# npm install -g tree-sitter-cli
# Usage via script grimoire-tree-sitter-map.js (inclus dans framework/mcp/)
node framework/mcp/grimoire-tree-sitter-map.js --output _grimoire-output/repo-map.json
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Format de sortie (`repo-map.md`)

```markdown
# Repo Map — {project_name}
> Généré : {date} | Stratégie : {strategy} | Fichiers : {count}

## Arborescence

```
{project}/
├── cmd/
│ └── server/
│ └── main.go [func main, func setupRoutes]
├── internal/
│ ├── domain/
│ │ ├── job.go [type Job, type JobStatus, func NewJob]
│ │ └── repository.go [interface JobRepository]
│ └── adapters/
│ └── sqlite/
│ └── job_repo.go [type SQLiteJobRepo, func (r) FindByID]
└── tests/
 └── integration/
 └── job_test.go [func TestJobCreation]
```

## Symboles exportés par domaine

| Fichier | Symbole | Type | Ligne |
|---------|---------|------|-------|
| internal/domain/job.go | Job | struct | 12 |
| internal/domain/job.go | NewJob | func | 34 |
| internal/domain/repository.go | JobRepository | interface | 8 |

## Relations inter-modules (imports)

- `cmd/server/main.go` → `internal/adapters/sqlite`, `internal/domain`
- `internal/adapters/sqlite/job_repo.go` → `internal/domain`
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/team.svg" width="28" height="28" alt=""> Intégration dans agent-base.md

Chaque agent qui a besoin de naviguer le code peut appeler :

```
[→Atlas][RM] : demande de Repo Map à jour
```

Atlas répond avec le chemin `_grimoire-output/repo-map.md` ou le régénère si obsolète.

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> Mise à jour automatique

Ajouter dans `.git/hooks/post-commit` (optionnel) :

```bash
#!/usr/bin/env bash
# Invalider la Repo Map après chaque commit
if [[ -f "_grimoire-output/repo-map.md" ]]; then
    echo "<!-- STALE: regenerate with Atlas [RM] -->" >> "_grimoire-output/repo-map.md"
fi
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/brain.svg" width="28" height="28" alt=""> Configuration dans `project-context.yaml`

```yaml
repo_map:
  enabled: true
  strategy: "ctags"          # ctags | find | tree-sitter
  max_symbols_per_file: 30
  languages: [go, typescript, python, terraform]
  exclude_dirs: [node_modules, .git, vendor, dist, _grimoire-output]
  output: "_grimoire-output/repo-map.md"
  stale_after_hours: 24
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/wrench.svg" width="28" height="28" alt=""> Commandes Atlas associées

| Cmd | Description |
|-----|-------------|
| `[RM]` | Générer/afficher la Repo Map |
| `[RM] rebuild` | Forcer la régénération complète |
| `[RM] search <terme>` | Chercher un symbole dans la map |
| `[RM] deps <fichier>` | Afficher les dépendances d'un fichier |
