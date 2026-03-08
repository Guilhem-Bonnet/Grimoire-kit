# Migration v2 → v3

> Guide pour migrer un projet BMAD existant (v2) vers la structure v3.

## Quoi de neuf en v3 ?

| Aspect | v2 | v3 |
|--------|----|----|
| Installation | `git clone` + `bmad-init.sh` | `pip install bmad-kit` |
| Configuration | `project-context.yaml` brut | `project-context.yaml` typé avec validation |
| CLI | Scripts bash | `bmad` CLI Python (Typer) |
| Outils | Scripts Python standalone | SDK Python importable |
| Mémoire | Fichiers plats | Backend pluggable (local, Qdrant, Ollama) |
| MCP | Non disponible | Serveur MCP intégré |
| Tests | Aucun | 640+ tests unitaires |

## Migration automatique

```bash
# Depuis le répertoire du projet v2
cd mon-projet-v2/

# Voir ce qui sera changé (dry-run)
bmad upgrade --dry-run

# Exécuter la migration
bmad upgrade
```

La commande `bmad upgrade` :

1. **Détecte** la version (présence de `project-context.yaml` sans marqueurs v3)
2. **Ajoute** la section `bmad:` avec `version: "3.0"` au YAML existant
3. **Crée** les répertoires v3 manquants (`_bmad/_config/agents/`, etc.)
4. **Préserve** tous les fichiers mémoire existants
5. **Ne supprime rien** — migration additive uniquement

## Migration manuelle

Si vous préférez migrer manuellement :

### 1. Mettre à jour `project-context.yaml`

Ajoutez la section `bmad` en haut du fichier :

```yaml
# Ajouter en haut du fichier existant
bmad:
  version: "3.0"

# Le reste du fichier v2 reste inchangé
project:
  name: "Mon Projet"
  # ...
```

### 2. Restructurer si nécessaire

```bash
# Créer les répertoires v3
mkdir -p _bmad/_config/agents
mkdir -p _bmad/_config/custom
mkdir -p _bmad/core/agents
mkdir -p _bmad/core/workflows
```

### 3. Vérifier

```bash
bmad doctor
```

## Ce qui est préservé

- `_bmad/_memory/` — toute la mémoire (shared-context, decisions-log, learnings, etc.)
- `project-context.yaml` — le contenu existant est conservé, seule la section `bmad` est ajoutée
- `.github/copilot-instructions.md` — inchangé
- Agents personnalisés dans `_bmad/_config/custom/`

## Ce qui change

- La CLI `bmad` remplace les scripts bash (`bmad-init.sh`, `cc-verify.sh`, etc.)
- Les outils Python sont maintenant importables via `from bmad.tools import ...`
- Le serveur MCP est disponible via `bmad-mcp`

## Vérification post-migration

```bash
# Santé du projet
bmad doctor

# Valider le YAML
bmad validate

# Vérifier le statut
bmad status
```

## Rollback

La migration est additive — pour revenir en v2, supprimez simplement la section `bmad:` du YAML et les répertoires v3 créés. Aucun fichier v2 n'est modifié ou supprimé.

## Voir aussi

- [Getting Started](getting-started.md) — guide v3 complet
- [Référence YAML](bmad-yaml-reference.md) — schéma v3
