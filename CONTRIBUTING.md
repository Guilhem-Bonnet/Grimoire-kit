<p align="right"><a href="README.md">README</a></p>

# <img src="docs/assets/icons/handshake.svg" width="32" height="32" alt=""> Grimoire Kit — Contributing Guide

## <img src="docs/assets/icons/handshake.svg" width="28" height="28" alt=""> Bienvenue

Tu veux améliorer le kit ? Excellent. Voici comment fonctionne le processus.

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/folder-tree.svg" width="28" height="28" alt=""> Structure du projet

```
grimoire-kit/
├── bmad-init.sh              # Script d'installation — teste après tout changement
├── framework/
│   ├── agent-base.md         # Protocole universel — impacte TOUS les agents
│   ├── cc-verify.sh          # Completion Contract verifier
│   ├── sil-collect.sh        # Self-Improvement Loop
│   ├── hooks/
│   │   └── pre-commit-cc.sh  # Hook git CC
│   ├── memory/               # Scripts Python mémoire
│   ├── tools/                # Outils CLI Python (stdlib only)
│   │   ├── agent-bench.py    # Bench performance agents
│   │   ├── agent-forge.py    # Génération squelettes agents
│   │   ├── context-guard.py  # Budget contexte LLM
│   │   ├── dna-evolve.py     # Évolution DNA depuis usage réel
│   │   ├── gen-tests.py      # Génération templates tests
│   │   └── bmad-completion.zsh  # Autocomplétion zsh
│   └── workflows/
│       ├── github-cc-check.yml.tpl  # Template CI déployé dans les projets
│       └── incident-response.md
├── archetypes/
│   ├── meta/agents/          # Agents universels — inclus dans TOUS les archétypes
│   ├── stack/agents/         # Agents par technologie (Modal Team Engine)
│   ├── infra-ops/            # Archétype infrastructure
│   ├── web-app/              # Archétype application web
│   ├── fix-loop/             # Archétype boucle de correction certifiée
│   └── minimal/              # Archétype point de départ
└── docs/
```

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Règles fondamentales

### 1. Tout agent doit respecter le format BMAD

Chaque fichier `.md` d'agent doit suivre le pattern :

```xml
<agent id="..." name="..." title="..." icon="...">
  <activation critical="MANDATORY">
    <step n="1">Load persona...</step>
    <step n="2">⚙️ BASE PROTOCOL — Load agent-base.md with: AGENT_TAG=... ...</step>
    <!-- 6-8 steps max -->
    <rules>
      <r>🔒 CC OBLIGATOIRE...</r>
      <r>RAISONNEMENT...</r>
      <!-- 5-8 règles -->
    </rules>
  </activation>
  <persona>...</persona>
  <menu>...</menu>
  <prompts>...</prompts>
</agent>
```

Voir [docs/creating-agents.md](docs/creating-agents.md) pour le guide complet.

### 2. Pas de "terminé" sans CC PASS

Avant tout commit qui touche des fichiers vérifiables (.go, .ts, .py, .sh, .tf...) :

```bash
bash framework/cc-verify.sh
```

Le hook pre-commit s'en charge automatiquement si installé.

### 3. Tests pour les scripts bash

Tout changement à `bmad-init.sh`, `cc-verify.sh` ou `sil-collect.sh` doit passer :

```bash
bash -n bmad-init.sh && echo "✅ syntaxe OK"
shellcheck bmad-init.sh  # si shellcheck disponible
bash bmad-init.sh --help  # smoke test
```

### 4. Tests pour les outils Python (framework/tools/*.py)

Tout changement à un outil Python doit passer :

```bash
# Vérification syntaxe
python3 -m py_compile framework/tools/[outil].py && echo "✅ syntaxe OK"

# Smoke test (les tools supportent --help ou s'exécutent sans erreur)
python3 framework/tools/context-guard.py --list-models
python3 framework/tools/agent-forge.py --list
python3 framework/tools/agent-bench.py --summary
python3 framework/tools/dna-evolve.py --report
```

Règles obligatoires pour tout outil dans `framework/tools/` :
- Stdlib Python uniquement (pas de dépendances externes)
- Type hints sur toutes les fonctions
- `if __name__ == "__main__"` clause
- `argparse` pour la CLI
- Exit codes normalisés : 0=OK, 1=warning, 2=critical
- Un wrapper `cmd_<nom>()` dans `bmad-init.sh`
- Les options ajoutées dans `bmad-completion.zsh`

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/wrench.svg" width="28" height="28" alt=""> Ajouter un outil CLI (framework/tools/*.py)

Les outils CLI s'intègrent dans le pipeline `bmad-init.sh` et VS Code.

1. Créer `framework/tools/[nom].py` avec :
 - `argparse` pour la CLI
 - Type hints partout
 - Stdlib uniquement
 - Exit codes 0/1/2 (OK/warning/critical)

2. Ajouter `cmd_[nom]()` dans `bmad-init.sh` :
```bash
cmd_monoutil() {
    shift  # retirer "monoutil"
    check_python3
    python3 "$SCRIPT_DIR/framework/tools/mon-outil.py" \
        --project-root "$PROJECT_ROOT" \
        "$@"
    exit $?
}
```

3. Ajouter le dispatch dans `bmad-init.sh` (section dispatch) :
```bash
if [[ "${1:-}" == "monoutil" ]]; then
    cmd_monoutil "$@"
fi
```

4. Ajouter le subcommand + options dans `framework/tools/bmad-completion.zsh`

5. Ajouter les tasks VS Code dans `.vscode/tasks.json`

6. Documenter dans `framework/tools/README.md`

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/puzzle.svg" width="28" height="28" alt=""> Ajouter un archétype

1. Créer `archetypes/[nom]/` avec :
 - `agents/` — au moins 1 agent `.md`
 - `shared-context.tpl.md` — template contexte projet (optionnel mais recommandé)
 - `README.md` — description, cas d'usage, agents inclus

2. Ajouter la détection dans `auto_select_archetype()` de `bmad-init.sh` si pertinent

3. Documenter dans [docs/archetype-guide.md](docs/archetype-guide.md) avec :
 - Cas d'usage
 - Stack typiquement détecté
 - Liste des agents et leur rôle

4. Mettre à jour le tableau dans [README.md](README.md)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/team.svg" width="28" height="28" alt=""> Ajouter un agent stack (Modal Team Engine)

Les agents stack sont dans `archetypes/stack/agents/` et sont déployés automatiquement par `detect_stack()`.

Nommage : `[technologie]-expert.md` (ex: `rust-expert.md`)

Dans `bmad-init.sh`, ajouter dans le `STACK_MAP` :
```bash
["rust"]="rust-expert.md"
```

Et dans `detect_stack()`, ajouter la détection :
```bash
# Rust
[[ -f "$dir/Cargo.toml" ]] && detected+=("rust")
```

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/team.svg" width="28" height="28" alt=""> Modifier `framework/agent-base.md`

**Attention** **Attention** : ce fichier est chargé par TOUS les agents. Tout changement a un impact global.

Avant de modifier :
1. Identifier quel(s) agent(s) sont impactés
2. Tester sur au moins 2 agents différents
3. Documenter la modification dans le commit message

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> Format des commits

```
type: description courte (max 72 chars)

- détail 1
- détail 2
```

Types : `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

Exemples :
```
feat: Rust archetype + detect_stack Cargo.toml

- archetypes/stack/agents/rust-expert.md: agent Ferris avec CC --stack rust
- bmad-init.sh: detect_stack() Cargo.toml → rust, STACK_MAP["rust"] ajouté
- docs/archetype-guide.md: section Rust ajoutée
```

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/flask.svg" width="28" height="28" alt=""> Tester localement

### Tests automatisés

```bash
# Lancer tous les tests Python (244+ tests)
cd grimoire-kit
python3 -m unittest discover -s tests -v

# Lancer un fichier spécifique
python3 -m unittest tests.test_context_guard_advanced -v

# Smoke tests Bash (78 assertions)
bash tests/smoke-test.sh
```

**Convention** : tout nouveau tool Python dans `framework/tools/` ou `framework/memory/` doit avoir un fichier de test correspondant dans `tests/`. Les tests utilisent uniquement `unittest` (stdlib, pas de pytest).

### Test d'intégration manuel

```bash
# Smoke test complet
cd /tmp && mkdir test-project && cd test-project && git init
bash /chemin/vers/grimoire-kit/bmad-init.sh \
  --name "Test" --user "Test" --auto

# Vérifier la structure générée
ls -la _bmad/_config/custom/agents/
cat _bmad/_memory/shared-context.md

# Vérifier le hook
cat .git/hooks/pre-commit
```

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/lightbulb.svg" width="28" height="28" alt=""> Questions ?

Ouvrir une issue sur GitHub avec le label approprié :
- `bug` — quelque chose ne fonctionne pas
- `enhancement` — proposition d'amélioration
- `new-archetype` — proposition d'un nouvel archétype
- `new-stack-agent` — proposition d'un agent technologie
