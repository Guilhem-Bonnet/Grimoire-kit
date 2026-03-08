# Migration v2 → v3

> Guide pour migrer un projet Grimoire existant (v2) vers la structure v3.

## Quoi de neuf en v3 ?

| Aspect | v2 | v3 |
|--------|----|----|
| Installation | `git clone` + `grimoire-init.sh` | `pip install grimoire-kit` |
| Configuration | `project-context.yaml` brut | `project-context.yaml` typé avec validation |
| CLI | Scripts bash | `grimoire` CLI Python (Typer) |
| Outils | Scripts Python standalone | SDK Python importable |
| Mémoire | Fichiers plats | Backend pluggable (local, Qdrant, Ollama) |
| MCP | Non disponible | Serveur MCP intégré |
| Tests | Aucun | 640+ tests unitaires |

## Migration automatique

```bash
# Depuis le répertoire du projet v2
cd mon-projet-v2/

# Voir ce qui sera changé (dry-run)
grimoire upgrade --dry-run

# Exécuter la migration
grimoire upgrade
```

La commande `grimoire upgrade` :

1. **Détecte** la version (présence de `project-context.yaml` sans marqueurs v3)
2. **Ajoute** la section `grimoire:` avec `version: "3.0"` au YAML existant
3. **Crée** les répertoires v3 manquants (`_grimoire/_config/agents/`, etc.)
4. **Préserve** tous les fichiers mémoire existants
5. **Ne supprime rien** — migration additive uniquement

## Migration manuelle

Si vous préférez migrer manuellement :

### 1. Mettre à jour `project-context.yaml`

Ajoutez la section `grimoire` en haut du fichier :

```yaml
# Ajouter en haut du fichier existant
grimoire:
  version: "3.0"

# Le reste du fichier v2 reste inchangé
project:
  name: "Mon Projet"
  # ...
```

### 2. Restructurer si nécessaire

```bash
# Créer les répertoires v3
mkdir -p _grimoire/_config/agents
mkdir -p _grimoire/_config/custom
mkdir -p _grimoire/core/agents
mkdir -p _grimoire/core/workflows
```

### 3. Vérifier

```bash
grimoire doctor
```

## Ce qui est préservé

- `_grimoire/_memory/` — toute la mémoire (shared-context, decisions-log, learnings, etc.)
- `project-context.yaml` — le contenu existant est conservé, seule la section `grimoire` est ajoutée
- `.github/copilot-instructions.md` — inchangé
- Agents personnalisés dans `_grimoire/_config/custom/`

## Ce qui change

- La CLI `grimoire` remplace les scripts bash (`grimoire-init.sh`, `cc-verify.sh`, etc.)
- Les outils Python sont maintenant importables via `from grimoire.tools import ...`
- Le serveur MCP est disponible via `grimoire-mcp`

## Vérification post-migration

```bash
# Santé du projet
grimoire doctor

# Valider le YAML
grimoire validate

# Vérifier le statut
grimoire status
```

## Rollback

La migration est additive — pour revenir en v2, supprimez simplement la section `grimoire:` du YAML et les répertoires v3 créés. Aucun fichier v2 n'est modifié ou supprimé.

## Voir aussi

- [Getting Started](getting-started.md) — guide v3 complet
- [Référence YAML](grimoire-yaml-reference.md) — schéma v3
