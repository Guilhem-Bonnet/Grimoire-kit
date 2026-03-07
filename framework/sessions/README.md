<p align="right"><a href="../../README.md">README</a> · <a href="../../docs">Docs</a></p>

# <img src="../../docs/assets/icons/grimoire.svg" width="32" height="32" alt=""> Session Branching — Guide

> **BM-16** — Les sessions BMAD peuvent être branchées comme un repo Git.
>
> **Problème résolu** : Impossible d'explorer deux approches différentes en parallèle
> sans écraser les outputs de l'une avec l'autre.
>
> **Principe** : Chaque "branche de session" est un espace d'output isolé.
> Merge = l'utilisateur compare et choisit le meilleur.

<img src="../../docs/assets/divider.svg" width="100%" alt="">


## <img src="../../docs/assets/icons/folder-tree.svg" width="28" height="28" alt=""> Structure

```
_bmad-output/
└── .runs/
    ├── main/                        ← branche par défaut
    │   ├── team-vision-run-001/
    │   │   ├── state.json
    │   │   └── workflow-status.md
    │   └── dev-story-run-002/
    │       ├── state.json
    │       └── workflow-status.md
    │
    ├── feature-auth/                ← branche feature
    │   └── dev-story-run-001/
    │
    └── explore-graphql/             ← branche d'exploration
        └── architecture-run-001/
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/moon.svg" width="28" height="28" alt=""> Créer une Branche de Session

### Via `bmad-init.sh`

```bash
# Créer et switcher sur une branche de session
bash /chemin/vers/bmad-init.sh session-branch --name "explore-graphql"

# Lister les branches actives
bash /chemin/vers/bmad-init.sh session-branch --list

# Merger les outputs d'une branche vers main
bash /chemin/vers/bmad-init.sh session-branch --merge "explore-graphql"
```

### Manuellement

```bash
# Créer le répertoire de la branche
mkdir -p _bmad-output/.runs/explore-graphql/

# Créer le manifest de branche
cat > _bmad-output/.runs/explore-graphql/branch.json << 'EOF'
{
  "branch": "explore-graphql",
  "created_at": "2026-02-27T18:00:00Z",
  "created_by": "architect",
  "purpose": "Explorer migration REST → GraphQL avant de décider",
  "parent_branch": "main",
  "status": "active"
}
EOF
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> Workflow d'Exploration Branché

Scénario typique : on veut comparer deux architectures avant de choisir.

```
main
  │
  ├──► [branch: explore-rest]
  │     → architect lance architecture-workflow
  │     → output : _bmad-output/.runs/explore-rest/arch-rest.md
  │
  ├──► [branch: explore-graphql]
  │     → architect lance architecture-workflow
  │     → output : _bmad-output/.runs/explore-graphql/arch-graphql.md
  │
  └──► [MERGE]
        → PM compare les deux outputs
        → Choisit explore-graphql
        → Copie arch-graphql.md → _bmad-output/planning-artifacts/architecture-final.md
        → Ferme explore-rest
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/wrench.svg" width="28" height="28" alt=""> Commandes de Gestion (dans `bmad-init.sh`)

```bash
# Liste toutes les branches
bmad-init.sh session-branch --list

# Affiche le diff entre deux branches (liste des artefacts différents)
bmad-init.sh session-branch --diff main explore-graphql

# Archive une branche terminée
bmad-init.sh session-branch --archive explore-rest

# Merge sélectif : copier un artefact spécifique vers main
bmad-init.sh session-branch --cherry-pick explore-graphql "_bmad-output/.runs/explore-graphql/arch-graphql.md" "_bmad-output/planning-artifacts/architecture-final.md"
```

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Convention de Nommage

```
{type}-{description-courte}
```

| Exemples valides | Usage |
|---|---|
| `main` | Branche de production — toujours présente |
| `feature-auth` | Développement d'une feature |
| `explore-graphql` | Exploration technique avant décision |
| `hotfix-login-bug` | Fix urgent sur un bug en prod |
| `sprint-3` | Travaux du sprint 3 (merge en fin de sprint) |
| `poc-ai-search` | Proof of concept |

<img src="../../docs/assets/divider.svg" width="100%" alt="">

## <img src="../../docs/assets/icons/team.svg" width="28" height="28" alt=""> Intégration avec les Agents

Les agents reçoivent la branche active dans leur contexte et écrivent leurs outputs dans le bon répertoire :

```yaml
# Dans project-context.yaml (auto-mis à jour par bmad-init.sh)
session_branch: "explore-graphql"
runs_directory: "_bmad-output/.runs/explore-graphql/"
```

L'agent préfixe tous ses outputs avec `{runs_directory}` au lieu de `_bmad-output/` directement.


*BM-16 Session Branching Guide | framework/sessions/README.md*
