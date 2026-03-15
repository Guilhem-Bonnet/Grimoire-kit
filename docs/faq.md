# FAQ

Questions fréquemment posées sur Grimoire Kit.

---

## Installation & Configuration

### Comment installer Grimoire Kit ?

```bash
pip install grimoire-kit
```

Pour les fonctionnalités optionnelles :

```bash
pip install "grimoire-kit[all]"     # Tout inclus
pip install "grimoire-kit[mcp]"     # Serveur MCP seulement
pip install "grimoire-kit[qdrant]"  # Backend Qdrant seulement
```

### Quelles versions de Python sont supportées ?

Python **3.12+** est requis. Les versions 3.12 et 3.13 sont testées en CI sur Ubuntu, Windows et macOS.

### Comment changer de backend mémoire ?

Éditez `project-context.yaml` ou utilisez la CLI :

```bash
grimoire config show memory.backend   # Voir le backend actuel
```

Backends disponibles : `auto`, `local`, `qdrant-local`, `qdrant-server`, `ollama`.

Modifiez la section `memory:` dans `project-context.yaml` :

```yaml
memory:
  backend: qdrant-local
```

### Comment vérifier que mon projet est bien configuré ?

```bash
grimoire doctor    # 8 checks automatiques
grimoire validate  # Validation du schéma YAML
grimoire diff      # Drift vs archétype par défaut
```

---

## Agents & Archétypes

### Quels archétypes sont disponibles ?

| Archétype | Description |
|-----------|-------------|
| `minimal` | Base universelle — meta-agents + template vierge |
| `web-app` | Application web full-stack |
| `creative-studio` | Création de contenu et design |
| `fix-loop` | Boucle de correction de bugs |
| `infra-ops` | Infrastructure et DevOps |
| `meta` | Meta-gestion du framework |
| `stack` | Stack technique personnalisé |
| `features` | Développement de features |
| `platform-engineering` | Ingénierie de plateforme |

### Comment créer un agent personnalisé ?

Voir le guide [Créer un agent](creating-agents.md). En résumé :

1. Créez un fichier `.md` dans `_grimoire/agents/`
2. Définissez le persona, les outils et les instructions
3. Ajoutez-le dans `project-context.yaml` sous `agents.custom_agents`

### Puis-je utiliser Grimoire sans MCP ?

Oui. MCP est optionnel. La CLI fonctionne sans le package `mcp`. Installez-le uniquement si vous voulez exposer les outils Grimoire comme serveur MCP :

```bash
pip install "grimoire-kit[mcp]"
```

---

## Plugins & Extensions

### Comment fonctionne le système de plugins ?

Grimoire utilise les [entry points](https://packaging.python.org/en/latest/specifications/entry-points/) Python :

- `grimoire.tools` — plugins d'outils
- `grimoire.backends` — plugins de backends mémoire

Vérifiez les plugins installés : `grimoire plugins list`

### Comment créer un plugin ?

Ajoutez un entry point dans le `pyproject.toml` de votre package :

```toml
[project.entry-points."grimoire.tools"]
mon-outil = "mon_package.module:ma_fonction"
```

---

## Migration & Compatibilité

### Comment migrer de v2 à v3 ?

Voir le guide de [Migration v2 → v3](migration-v2-v3.md).

```bash
grimoire upgrade --dry-run  # Voir le plan
grimoire upgrade            # Exécuter la migration
```

### Où trouver le changelog complet ?

Consultez le [Changelog](changelog.md) ou sur GitHub : [CHANGELOG.md](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/CHANGELOG.md).

---

## Dépannage

### `grimoire doctor` signale des erreurs

Suivez les indications affichées. Les causes fréquentes :

- `project-context.yaml` manquant → `grimoire init`
- Répertoires manquants → `grimoire up`
- Backend configuré sans URL → vérifiez la section `memory:`

### L'auto-complétion ne fonctionne pas

Installez-la puis rechargez votre shell :

```bash
grimoire completion install --shell bash
source ~/.bashrc
```

Pour plus d'aide, consultez le guide [Troubleshooting](troubleshooting.md).
