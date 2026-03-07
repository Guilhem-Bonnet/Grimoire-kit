<p align="right"><a href="../README.md">README</a> · <a href="../docs">Docs</a></p>

# <img src="../docs/assets/icons/seal.svg" width="32" height="32" alt=""> Completion Contract — Référence détaillée

> Chargé on-demand par l'agent quand il doit vérifier du code.
> Le protocole CC résumé est dans `agent-base.md`. Ce fichier contient le tableau complet et les exemples.

<img src="../docs/assets/divider.svg" width="100%" alt="">


## <img src="../docs/assets/icons/seal.svg" width="28" height="28" alt=""> Tableau de vérification par stack

| Fichiers touchés | Vérifications obligatoires | Commande |
|---|---|---|
| `*.go` | Build + Tests + Vet | `go build ./... && go test ./... && go vet ./...` |
| `*.ts` / `*.tsx` | Types + Tests | `npx tsc --noEmit && npx vitest run` (ou `npm test`) |
| `*.tf` / `*.tfvars` | Validate + Format | `terraform validate && terraform fmt -check` |
| `ansible/` / `playbook*.yml` | Lint | `ansible-lint && yamllint .` |
| `*.py` | Tests + Types | `pytest && (mypy . \|\| ruff check .)` |
| `Dockerfile` / `docker-compose*.yml` | Build | `docker build . --no-cache` (ou `docker compose config`) |
| `k8s/` / `Kind:` YAML | Dry-run | `kubectl apply --dry-run=server -f .` |
| `*.sh` | Lint | `shellcheck *.sh` |
| Markdown / config only | Aucune commande requise | &#x2713; direct |

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/rocket.svg" width="28" height="28" alt=""> Exemples de sortie

### CC PASS
```
✅ CC PASS — [stack] — [date heure]
> go build ./...  → OK (0 erreurs)
> go test ./...   → OK (47 tests, 0 failed)
> go vet ./...    → OK
```

### CC FAIL
```
🔴 CC FAIL — [stack] — [date heure]
> go test ./...   → FAIL
  --- FAIL: TestXxx (0.12s)
  [je corrige maintenant avant de rendre la main]
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/wrench.svg" width="28" height="28" alt=""> Exemples de commandes par stack

```bash
# Go
go build ./... && go test ./... && go vet ./...

# TypeScript
npx tsc --noEmit && npx vitest run

# Terraform
terraform validate && terraform fmt -check

# Python
pytest && ruff check .

# Docker
docker build . --no-cache
docker compose config

# K8s
kubectl apply --dry-run=server -f .

# Shell
shellcheck *.sh

# Ansible
ansible-lint && yamllint .
```

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/wrench.svg" width="28" height="28" alt=""> Script automatique

```bash
bash {project-root}/_bmad/_config/custom/cc-verify.sh
```

Détecte automatiquement le stack et lance les vérifications appropriées.
