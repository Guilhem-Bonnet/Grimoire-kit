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

## Tenets

Quatre principes s'imposent à toute contribution. Chacun est tenu par un test,
pas par l'attention d'un relecteur : si le test disparaît, `scripts/check-contributing.py`
(garde `tenets` de `grimoire gates repo`) échoue.

### Tenet 1 — Une garde échoue fermée, et un test le prouve

Une garde qui n'a jamais été vue rouge n'est pas une garde. Chaque garde du
dépôt a un test qui construit l'état qu'elle refuse et vérifie qu'elle refuse ;
une garde qui ne peut pas rendre son verdict (script absent, dépôt illisible)
échoue, elle ne passe pas. Une PR `fix:` apporte un test rouge sur sa base.

Tenu par : `tests/unit/scripts/test_release_gates.py`, `tests/unit/scripts/test_repo_gates.py`,
`tests/unit/scripts/test_check_regression_on_base.py`.

### Tenet 2 — Aucun chiffre sans source

Un chiffre émis ou publié (prose des agents, README, docs) vient d'une commande
exécutée ou d'un fichier lu, nommés ; sinon il s'écrit « non mesuré ». La vitrine
n'affiche pas de compteur de vanité.

Tenu par : `tests/unit/test_grounding_prose.py`, `tests/unit/scripts/test_check_doc_claims.py`.

### Tenet 3 — Un mécanisme par invariant

Un verrou, une écriture atomique, une garde de silence : un seul utilitaire, pas
une variante locale. Une exception silencieuse se justifie sur sa ligne.

Tenu par : `tests/unit/test_fsio.py`, `tests/unit/core/test_claim_ledger_scan_invariants.py`.

### Tenet 4 — La chaîne d'outils et la documentation ne pourrissent pas

Les actions CI sont épinglées par SHA. Chaque fichier de `docs/` déclare son
public, et une commande `grimoire ...` citée en documentation existe dans la CLI.

Tenu par : `tests/unit/scripts/test_check_actions_pinned.py`,
`tests/unit/scripts/test_check_doc_audience.py`, `tests/unit/scripts/test_check_doc_commands.py`.

### Lancer toutes les gardes d'un coup

```bash
grimoire gates repo              # profil dev : ce qu'une branche doit satisfaire
grimoire gates repo --profile release   # ce que le tag exige en plus
grimoire gates repo --only changelog    # une seule garde
```

La même commande est appelée par le pre-commit (`.pre-commit-config.yaml`) et par
le job `lint` de `ci-sdk.yml` ; `tests/unit/scripts/test_repo_gates.py` compare les deux.

## Pas de chiffre sans mesure — la pyramide de gardes

Un agent Grimoire ne produit jamais un chiffre, un score, une tendance ou un
verdict sur un fichier sans la commande exécutée ou le fichier lu qui le
fonde ; ce qui n'est pas mesuré s'écrit « non mesuré » (issue #613). Trois
gardes le tiennent, du plus rapide au plus coûteux :

| Étage | Garde | Quand elle tourne | Ce qu'elle refuse |
|---|---|---|---|
| Statique | `scripts/check-emitted-prose.py` | pre-commit (via `grimoire gates repo`), `make lint`, `tests/unit/test_grounding_prose.py` en CI | un gabarit émis qui demande `X/10`, `confidence: 0.92`, `trust_score: 91`, une estimation chiffrée |
| Émission | `tests/unit/test_hosts.py` (`*grounding*`) | `pytest tests/unit` | un hôte ou un ouvrier headless dont le texte émis ne porte pas `grimoire.core.grounding.GROUNDING_RULE` ni le bloc `grimoire-uncertainties` |
| Comportement | `evals/grounding-probe.py` (témoin : `evals/grounding-probe-witness.py`) | chaque lundi dans `weekly-bench.yml` si `ANTHROPIC_API_KEY` est dans les secrets (sauté sinon, jamais vert par défaut), et à la main avant une release qui touche les wrappers | une persona émise qui répond un pourcentage ou une quantité à une question dont toute réponse chiffrée est inventée (deux questions en gate ; la note sur demande explicite reste hors gate, mesurée 3/3 fabriquée sur haiku) |

Règles d'écriture pour tout fichier sous `framework/` ou `archetypes/` :

- un nombre dans un gabarit de sortie vient d'une commande nommée juste
  avant (`grimoire -o json doctor`, `grimoire standard score`, un `df -h`) ou
  d'un compte de lignes du tableau produit ; sinon le gabarit dit « non
  mesuré » ;
- un jugement qualitatif reste étiqueté comme tel (`OK/KO`, `S/M/L`,
  `haute | moyenne | faible`) et cite ce qui le fonde ; jamais converti en
  note, pourcentage ou moyenne pondérée ;
- une exception justifiée va dans `scripts/emitted-prose-allowlist.txt`, une
  ligne par cas, avec sa raison — le script refuse une entrée muette.

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

## Cœurs optionnels en Rust

Le kit porte progressivement, module par module, certains cœurs de logique
pure vers Rust — exposés via [PyO3](https://pyo3.rs), un crate par module
migré (issue [#354](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/354)).
Deux points essentiels avant tout le reste, valables pour **tous** ces
crates :

- **Ce n'est jamais nécessaire pour contribuer au kit.** Aucun module
  compilé n'est installé par une dépendance du paquet, `pip install
  grimoire-kit` n'en construit aucun, et le module Python correspondant
  retombe silencieusement sur son implémentation Python pure quand le
  compilé est absent — ce qui est le cas de loin le plus courant. N'installer
  que Python et suivre les [Prérequis](#prérequis) ci-dessus suffit pour
  tout le reste du kit.
- **Rien n'est publié.** La wheel sur PyPI reste la wheel universelle
  actuelle ; aucune roue par plateforme, aucune compilation croisée dans le
  pipeline de publication. Chaque crate porte le classifieur PyPI
  `Private :: Do Not Upload` pour qu'un `maturin publish` accidentel soit
  refusé.

Six crates existent à ce jour :

| Crate | Module Python | Variable de bascule | Jobs CI |
|---|---|---|---|
| `rust/grimoire-policies-core/` | `grimoire.policies.engine` (`PolicyEngine.evaluate`) | `GRIMOIRE_POLICIES_BACKEND` | `.github/workflows/rust-cores.yml` (`rust-policies / cargo`, `rust-policies / parity`) |
| `rust/grimoire-schema-core/` | `grimoire.core.schema.generate_schema` + `grimoire.core.validator.validate_config` | `GRIMOIRE_SCHEMA_BACKEND` | `.github/workflows/rust-cores.yml` (`rust-schema / cargo`, `rust-schema / parity`) |
| `rust/grimoire-hosts-core/` | `grimoire.hosts.collect` (frontmatter, inférence d'outils) + `grimoire.hosts.surface` (empreinte de faisceau, garde de distinction) | `GRIMOIRE_HOSTS_BACKEND` | `.github/workflows/rust-cores.yml` (`rust-hosts / cargo`, `rust-hosts / parity`) |
| `rust/grimoire-flows-core/` | `grimoire.flows.engine` (`check_output_against_contract`, node courant, `resume()`/`status()`) + `grimoire.runtime.kernel` (table de transitions, précondition de `advance_step`) | `GRIMOIRE_FLOWS_BACKEND` | `.github/workflows/rust-cores.yml` (`rust-flows / cargo`, `rust-flows / parity`) |
| `rust/grimoire-dispatch-core/` | `grimoire.missions.dispatch` (chaîne de paliers, rendu d'invocation, analyse de sortie d'ouvrier, classification de revue, verdict de cascade) + `grimoire.providers.routing` (ordre et refroidissement des fournisseurs) | `GRIMOIRE_DISPATCH_BACKEND` | `.github/workflows/rust-cores.yml` (`rust-dispatch / cargo`, `rust-dispatch / parity`) |
| `rust/grimoire-traces-core/` | `grimoire.traces.ledger` (agrégations du journal, `compute_agent_freshness`) + `grimoire.proposals` (nommage mécanique, résolution du porteur, décision de synchronisation d'une proposition) | `GRIMOIRE_TRACES_BACKEND` | `.github/workflows/rust-cores.yml` (`rust-traces / cargo`, `rust-traces / parity`) |

Les six crates partagent le même workflow CI : `changes` détecte quel(s)
crate(s) une PR ou un push touche et saute les jobs hors périmètre, et
`rust-gate` — le check requis par la protection de `main` — agrège les
douze jobs pour rendre `cargo fmt` et `cargo test` opposables sur toute PR,
pas seulement celles qui modifient `rust/`.

### Construire une extension localement

Utile seulement si vous travaillez sur le module Python concerné et voulez
exercer le chemin Rust en local (au lieu d'attendre le job CI dédié) — le
motif est identique pour les cinq crates, ici avec `grimoire-policies-core` :

```bash
# En plus des prérequis Python habituels
rustup toolchain install stable   # https://rustup.rs si rustup n'est pas déjà installé
pip install maturin

cd rust/grimoire-policies-core
maturin develop --release         # construit et installe grimoire_policies_core
                                   # dans le venv actif
cd -

pytest tests/unit/test_policies.py tests/unit/test_policies_rust_parity.py
```

Pour `grimoire-schema-core`, remplacer le nom du crate et la commande
`pytest` finale par :

```bash
cd rust/grimoire-schema-core
maturin develop --release
cd -

pytest tests/unit/core/test_schema.py tests/unit/core/test_validator.py tests/unit/test_schema_validator_rust_parity.py
```

Pour `grimoire-hosts-core` :

```bash
cd rust/grimoire-hosts-core
maturin develop --release
cd -

pytest tests/unit/test_hosts.py tests/unit/test_hosts_rust_parity.py
```

Pour `grimoire-flows-core` :

```bash
cd rust/grimoire-flows-core
maturin develop --release
cd -

pytest tests/unit/test_flows_engine.py tests/unit/test_runtime.py tests/unit/test_flows_rust_parity.py
```

Pour `grimoire-dispatch-core` :

```bash
cd rust/grimoire-dispatch-core
maturin develop --release
cd -

pytest tests/unit/missions/test_dispatch.py tests/unit/test_providers.py tests/unit/test_dispatch_rust_parity.py
```

Pour `grimoire-traces-core` :

```bash
cd rust/grimoire-traces-core
maturin develop --release
cd -

pytest tests/unit/test_traces.py tests/unit/test_proposals.py tests/unit/test_traces_rust_parity.py
```

Dans les six cas, les tests du module Python (`test_policies.py`,
`test_schema.py`/`test_validator.py`, `test_hosts.py`,
`test_flows_engine.py`/`test_runtime.py`, `test_dispatch.py`/`test_providers.py`,
`test_traces.py`/`test_proposals.py`) sont le contrat : ils tournent
sans modification que le module compilé soit présent ou non, et servent de
golden test aux deux implémentations. Le fichier `*_rust_parity.py` compare
explicitement les deux backends sur les mêmes entrées (variable
d'environnement dédiée, voir le tableau ci-dessus et le docstring du module
Python correspondant) ; ses cas spécifiques au Rust se sautent proprement
(`skip`, pas `fail`) quand le module n'est pas installé.

Pour du travail directement sur un crate (`cargo` seul, aucun interprète
Python requis) :

```bash
cd rust/grimoire-schema-core   # ou rust/grimoire-policies-core, rust/grimoire-hosts-core, rust/grimoire-flows-core, rust/grimoire-dispatch-core, rust/grimoire-traces-core
cargo test --no-default-features   # logique pure, aucun interprète Python requis
cargo fmt
```

Le `--no-default-features` est nécessaire : la feature `extension-module` de
PyO3 (active par défaut, indispensable pour que l'extension soit chargeable
par Python) empêche de lier le binaire de test de `cargo test` — voir le
commentaire en tête du `Cargo.toml` du crate.

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

## Publier une release

Il n'y a pas d'étage TestPyPI : le projet n'y a jamais été enregistré, et un
job qui affiche une pré-vérification sans l'exécuter est pire qu'aucun (#195).
Ce qui vérifie qu'une version s'installe avant qu'elle ne parte sur PyPI —
où un numéro consommé l'est définitivement :

1. **Avant le tag, en local** — `make release VERSION=x.y.z` construit la
   wheel puis exécute `make wheel-check` : installation de la wheel dans un
   venv neuf (`.wheel-check-venv/`), `grimoire --version`, import du SDK.
   Une wheel qui ne démarre pas ne se tague pas.
2. **Au tag, dans le job `gates` de `publish.yml`** — quatre gardes bloquants,
   avant tout `build` : le tag et `version.txt` disent la même chose et le tag
   pointe un commit de `main` (`scripts/check-release-tag.py`), `scripts/gen-kit-hashes.py --check`
   couvre les fichiers livrés, `scripts/check-changelog-release.py` décrit la
   version publiée. Ce dernier exige que chaque commit `feat`/`fix`/`perf`
   fusionné depuis le tag précédent touche `CHANGELOG.md` — sauf repli : un
   commit qui ne le touche pas est accepté si son sujet se termine par
   `(#NNN)` et que ce numéro apparaît dans la section de la version publiée
   (pas dans `[Unreleased]` ni dans une version déjà taguée). Le repli existe
   depuis le 2026-09-08 (seize PR fusionnées le même jour sans entrée
   propre, rattrapées par la section rédigée à la release) ; il couvre un
   rattrapage ponctuel, pas une dispense — ajouter l'entrée à son propre
   commit reste la règle.
3. **Au tag, dans `publish.yml`** — le job `build` installe la wheel qu'il
   vient de construire ; le job `test` la réinstalle dans un runner neuf sur
   chaque Python supporté et importe le SDK. `publish-pypi` dépend des deux :
   rien ne part si l'un échoue, et aucun job n'est en `continue-on-error`.

`publish.yml` est la seule voie de release : `gates` → `build` → `test` →
`publish-pypi` → `github-release`. PyPI part sur un tag stable (`vX.Y.Z`) ;
une pré-version (`vX.Y.Z-rc1`) ne produit qu'une release GitHub marquée
pre-release, sans PyPI — `make release VERSION=x.y.z-rc1` écrit alors
`x.y.z-rc1` dans `version.txt` et `__version__` (la graphie SemVer ; hatch la
normalise en `x.y.zrc1` dans les métadonnées de la wheel), et
`tests/unit/test_version_sync.py` accepte cette forme parce qu'il lit le même
prédicat que le garde de tag. Le job `publish-pypi` relance sans échouer sur un
fichier déjà reçu (`skip-existing`) : après un téléversement interrompu, relancer
les jobs en échec suffit à créer la release. Le `Makefile` n'a aucune voie manuelle vers PyPI : `make publish` refuse
et renvoie à ce push de tag (un téléversement hors `publish.yml` contournerait
les gardes, et `skip-existing` accepterait ensuite le fichier sans comparer son
contenu). La release GitHub est créée après la publication, et le
corps est la section du `CHANGELOG.md` que `gates` vient de valider
(`scripts/changelog-section.py`) — rien n'est généré à partir de `git log`.

```bash
make release VERSION=x.y.z      # check + hashes + build + wheel-check
git add version.txt src/grimoire/__version__.py registry/kit-file-hashes.json
git commit -m "chore: release x.y.z"
git tag -a vx.y.z -m "Release x.y.z"
git push origin main --tags     # publish.yml prend le relais
```

### Contrôles hors arbre

La voie unique et le garde « tag sur `main` » vivent dans l'arbre du tag : ils
ne valent que pour un arbre qui contient ce changement (`publish.yml` sans
`release.yml`, `scripts/check-release-tag.py`). Un arbre antérieur garde
l'ancienne paire de workflows — PyPI s'y ouvre dès que la variable de dépôt
`GRIMOIRE_ENABLE_PYPI` vaut `true`, et deux créateurs de release s'y exécutent
de nouveau. Un arbre taggué peut aussi modifier le script ou l'étape qui le
surveille, donc désarmer son propre garde. Rien de cela ne se ferme par du
code : c'est de la configuration GitHub, à faire une fois la voie unique
fusionnée.

1. **Supprimer** la variable de dépôt `GRIMOIRE_ENABLE_PYPI` (Settings →
   Secrets and variables → Actions → Variables). Elle ne pilote plus rien dans
   `publish.yml` ; supprimée, elle ferme PyPI aux anciens arbres.
2. **Restreindre l'environnement `pypi`** par une politique de déploiement sur
   les tags `v*`. Cela réduit la surface, mais l'environnement ne vérifie pas
   l'ascendance `main` : il ne remplace pas le garde.
3. **Ajouter un ruleset de tags** sur `refs/tags/v*` qui limite qui peut créer
   ou déplacer un tag de release. C'est le seul contrôle réel contre un tag
   posé sur un arbre qui n'a pas les gardes. Le ruleset `Base` existant cible
   des branches (`target: branch`) et n'agit pas sur les tags.
4. **Rebaser les branches `aidlc/*`** (et toute branche antérieure au merge)
   sur `origin/main` avant d'y poser un tag : un tag posé sur leur arbre
   rejouerait l'ancienne paire de workflows.

Ces contrôles ne sont pas vérifiables depuis le dépôt : `gh api
repos/<org>/<repo>/environments/pypi` et le listing des rulesets disent leur
état, et la lecture de `actions/variables` exige un droit que l'App de CI n'a pas.

## Questions ?

Ouvrir une issue sur GitHub.
