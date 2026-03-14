# <img src="docs/assets/icons/chart.svg" width="32" height="32" alt=""> Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## [Unreleased]

### Ajouté

- **Tests: +8** — R31 review fixes : EnvCmdNarrowException (2) + VersionCmdGrimoireError (1) + InterruptedRemoved (2) + CtxObjGuard (1) + SetupGlobalJson (2) → 311 tests CLI (Round 31)

### Corrigé

- **CLI: C1 — env_cmd exception trop large** — `env` catchait `(typer.Exit, Exception)` masquant tout. Réduit à `(typer.Exit, GrimoireError)` — même correctif que R30 M4 sur `version_cmd` (Round 31)
- **CLI: C2 — version_cmd rate GrimoireProjectError** — `version` catchait `GrimoireConfigError` mais pas `GrimoireProjectError` (classes sœurs). Élargi à `GrimoireError` (base commune) (Round 31)
- **CLI: H1 — _interrupted dead variable** — Le flag `_interrupted` était set par `_handle_signal` mais jamais lu (`SystemExit` raised immédiatement). Supprimé (Round 31)
- **CLI: H2 — ctx.obj guard inconsistant** — 8 commandes utilisaient `ctx.obj.get()` sans guard None alors que d'autres utilisaient `(ctx.obj or {}).get()`. Standardisé vers le pattern sûr partout (Round 31)
- **CLI: H3 — setup ignore -o json global** — `setup` avait un flag `--json` dédié mais ignorait le flag global `-o json`. Ajout de `ctx: typer.Context` et unification : les deux méthodes fonctionnent (Round 31)

### Précédemment (Round 30)

#### Ajouté

- **Tests: +13** — R29 review fixes : EditorValidation (2) + SuggestIncludesAliases (1) + FlattenLists (3) + RequiredDirsConstant (2) + AuditLogAtomic (1) + HistorySkipCount (1) + RepairJsonOk (1) + VersionEnvFindConfig (2) → 294 tests CLI (Round 29)

#### Corrigé

- **CLI: C1 — Audit log race condition** — `_log_operation()` avait un TOCTOU entre `read_text()` et `write_text()` pour la troncation du log. Remplacé par un mode `r+` atomique (seek + truncate dans le même file handle) (Round 29)
- **CLI: C2 — Editor validation** — `config edit` appelait `os.execvp()` sans vérifier l'existence de l'éditeur. Ajout de `shutil.which()` avec suggestion `$VISUAL/$EDITOR` si absent (Round 29)
- **CLI: H3 — version/env hardcoded path** — `version` et `env` utilisaient `Path.cwd() / "project-context.yaml"` au lieu de `_find_config()`, ne fonctionnaient pas depuis un sous-répertoire (Round 29)
- **CLI: H4 — _flatten ignore lists-of-dicts** — `_flatten()` traitait les listes de dicts comme des valeurs opaques. Ajout de la récursion avec clés indexées : `repos.0.name`, `repos.1.path` (Round 29)
- **CLI: H8 — Aliases absent des suggestions** — `_suggest_command()` ne considérait que les commandes enregistrées, pas les alias. Ajout de `_KNOWN_COMMANDS.update(_ALIASES)` (Round 29)
- **CLI: H9 — DRY violation répertoires** — 8+ occurrences de tuples `("_grimoire", "_grimoire-output")` hardcodés. Extraction en constantes module `_REQUIRED_DIRS` + `_MEMORY_DIR` (Round 29)
- **CLI: M10 — config set acceptait des listes** — `config set` splittait les virgules pour créer des listes, comportement error-prone. Remplacé par un refus explicite avec guidance vers `config edit` (Round 29)
- **CLI: M13 — _log_operation muet sur erreur** — Le handler `OSError` ignorait silencieusement les erreurs. Ajout d'un avertissement console quand `GRIMOIRE_DEBUG` est défini (Round 29)
- **CLI: M14 — history ignore les entrées corrompues** — `history` sautait silencieusement les lignes JSONL invalides. Ajout d'un compteur `skipped` (affiché en texte et en JSON) (Round 29)
- **CLI: M15 — repair JSON manquait ok** — La sortie JSON de `repair` n'incluait pas le champ `"ok": true` contrairement aux autres commandes (Round 29)

### Précédemment (Round 28)

#### Ajouté

- **CLI: `grimoire config edit`** — Ouvre `project-context.yaml` dans `$VISUAL` / `$EDITOR` / `vi` (Round 28)
- **CLI: `grimoire config validate`** — Validation du schema config en place, JSON output `{valid, warnings}`, exit code 1 si invalide (Round 28)
- **CLI: `--profile` sur `check`** — 3 phases instrumentées : `check/lint`, `check/validate`, `check/structure` (Round 28)
- **Tests: +20** — R28 review fixes (13) + config edit (3) + config validate (4) → 281 tests CLI (Round 28)

### Corrigé

- **CLI: C1 — Audit filename in repair** — `repair` utilisait `"audit.jsonl"` au lieu de `_AUDIT_FILENAME` (`.grimoire-audit.jsonl`), le trimming du log ne fonctionnait jamais (Round 28)
- **CLI: C3 — Latence _is_online()** — Suppression du probe réseau 500ms dans le callback `main()` exécuté à chaque commande. Remplacé par `is_online()` lazy (cache une fois par process) (Round 28)
- **CLI: C4 — Config commands depuis subdirectory** — Les 5 commandes config (`show`, `get`, `path`, `set`, `list`) utilisaient un path hardcodé au lieu de `_find_config()` (Round 28)
- **CLI: C5 — Accumulation phase timings** — `_phase_timings` module-level jamais vidé entre invocations. Ajout de `.clear()` dans `cli()` (Round 28)
- **CLI: H6 — Newline échappé** — `\\n` dans l'affichage `--time` au lieu de `\n` (Round 28)
- **CLI: H7 — self version offline** — `self version` n'utilisait pas le flag offline, probe PyPI inutile quand hors-ligne (Round 28)
- **CLI: M5 — `_flatten` dupliqué** — Suppression de `_flatten_dict()` redondant, réutilisation de `_flatten()` dans `config list` (Round 28)

- **CLI: Command suggestions** — `_suggest_command()` détecte les fautes de frappe et propose des commandes proches via `difflib.get_close_matches()` (Round 27)
- **CLI: Signal handling** — Gestion propre de SIGINT/SIGTERM avec message et code de sortie Unix standard (128+signal) (Round 27)
- **CLI: `--profile` flag** — Breakdown timing par phase avec arbre Rich (`_timed_phase` context manager), instrumenté sur `doctor` (Round 27)
- **CLI: `grimoire repair`** — Auto-réparation des problèmes courants : création répertoires manquants, trim du audit log >90j, `--dry-run`, JSON output (Round 27)
- **CLI: Offline mode detection** — `_is_online()` avec test de connectivité rapide, `GRIMOIRE_OFFLINE=1` env var, `ctx.obj["offline"]` flag (Round 27)
- **Tests: +21** — TestCommandSuggestions (4) + TestSignalHandling (3) + TestPerformanceProfiling (4) + TestRepairCommand (6) + TestOfflineMode (4) → 261 tests CLI (Round 27)
- **CLI: Config auto-discovery** — `_find_config()` remonte l'arborescence pour trouver `project-context.yaml` quand on est dans un sous-répertoire (Round 26)
- **CLI: Rich spinners** — `_status_spinner()` affiche un spinner animé sur `upgrade` et `merge` (respecte `--quiet` et `-o json`) (Round 26)
- **CLI: Exemples dans l'aide** — Rich markup examples ajoutés aux docstrings de 8 commandes : init, doctor, validate, add, remove, status, check, upgrade (Round 26)
- **CLI: `grimoire history`** — Audit trail des opérations CLI récentes avec `--limit`, `--filter`, JSON output (`_grimoire/_memory/.grimoire-audit.jsonl`) (Round 26)
- **CLI: Audit log** — `_log_operation()` trace automatiquement init, add, remove, config_set, upgrade, merge dans un fichier JSONL (Round 26)
- **CLI: Deprecation framework** — `_DEPRECATED_FLAGS` dict + `_warn_deprecated()` pour gérer proprement les flags obsolètes dans les futures versions (Round 26)
- **Tests: +28** — TestAutoDiscovery (4) + TestSpinnerHelper (2) + TestSubcommandExamples (8) + TestAuditLog (4) + TestHistoryCommand (5) + TestDeprecationWarnings (3) + TestAuditIntegration (2) → 995 tests CLI (Round 26)
- **CLI: `--yes/-y` global flag** — Skip les confirmations interactives sur `remove` et `merge --undo` ; implicite en mode JSON (Round 25)
- **CLI: Confirmations interactives** — `remove` et `merge --undo` demandent confirmation avant toute action destructive (Round 25)
- **CLI: JSON output `upgrade`** — Sortie JSON structurée `{ok, version, dry_run, warnings, actions}` (Round 25)
- **CLI: Rich help panels** — Commandes organisées par catégorie : Project, Agents, Validation, Configuration, Utilities, Info (Round 25)
- **CLI: Error handler amélioré** — Affichage du code d'erreur et suggestions de récupération (`_format_error`, `_RECOVERY_HINTS`) (Round 25)
- **Tests: conftest.py CLI** — Fixtures `cli_project` et helper `assert_json_output` pour réduire la duplication de tests (Round 25)
- **Tests: +23** — TestYesFlag (5) + TestUpgradeJson (4) + TestHelpPanels (6) + TestErrorHandler (4) + TestConftestFixtures (3) + TestEpilog (1) → 967 tests (Round 25)
- **CLI: JSON output `init`** — Sortie JSON structurée `{ok, project, path, archetype, backend, directories}` (Round 24)
- **CLI: JSON output `up`** — Sortie JSON structurée `{ok, project, actions, dry_run, agents_count}` (Round 24)
- **CLI: `doctor --fix`** — Auto-correction des répertoires manquants avec rapport `fixed` en JSON (Round 24)
- **CLI: `--time` flag** — Affiche le temps d'exécution en ms après chaque commande (Round 24)
- **Tests: +19** — TestInitJson (3) + TestUpJson (2) + TestDoctorFix (4) + TestTimeFlag (2) + TestJsonOutputParametrized (8) → 944 tests (Round 24)
- **CLI: `config set KEY VALUE`** — Modification de clé config par dot-notation avec coercion de type, `--dry-run`, JSON (Round 23)
- **CLI: JSON output `add`/`remove`** — Sortie JSON structurée `{ok, action, agent}` pour scripting CI/CD (Round 23)
- **CLI: "Did you mean?" sur init** — Suggestions fuzzy pour archétypes et backends mal typés (Round 23)
- **Tests: +16** — TestConfigSet (7) + TestAddRemoveJson (6) + TestDidYouMean (3) → 925 tests (Round 23)
- **CLI: Command aliases** — Raccourcis courts : `i`=init, `d`=doctor, `s`=status, `v`=validate, `l`=lint, `ck`=check, `u`=up, `c`=config, `r`=registry (Round 22)
- **CLI: Env var overrides** — `GRIMOIRE_OUTPUT=json`, `GRIMOIRE_QUIET=1`, `NO_COLOR=1` pour scripting/CI sans flags (Round 22)
- **CLI: `add --dry-run` / `remove --dry-run`** — Flag `-n/--dry-run` sur les commandes add/remove pour prévisualiser sans modifier (Round 22)
- **CLI: `check` phases structurées** — Helper `_phase_header()` avec support `--quiet` pour une sortie plus propre (Round 22)
- **Tests: +16** — TestCommandAliases (5) + TestAddRemoveDryRun (7) + TestEnvVarOverrides (4) → 909 tests (Round 22)
- **CLI: `grimoire version`** — Commande standalone avec version, Python, plateforme, projet actif (text/JSON) (Round 21)
- **CLI: `grimoire self version`** — Version installée + vérification de mise à jour PyPI (text/JSON) (Round 21)
- **CLI: `grimoire self diagnose`** — Auto-diagnostic : dépendances, Python, entry point, statut global (text/JSON) (Round 21)
- **CLI: `grimoire config get KEY`** — Lecture d'une clé config par dot-notation (text/JSON) (Round 21)
- **CLI: `grimoire config path`** — Affiche le chemin résolu vers project-context.yaml (Round 21)
- **CLI: `grimoire config list`** — Liste toutes les clés config avec valeurs actuelles en table Rich (text/JSON) (Round 21)
- **CLI: Epilog Rich** — Exemples et aide rapide dans `grimoire --help` (Round 21)
- **Validator: détection clés inconnues** — Avertissements pour clés non reconnues avec suggestions "Did you mean?" (Round 21)
- **Tests: +27** — TestVersionCommand (3) + TestConfigGet (4) + TestConfigPath (2) + TestConfigList (3) + TestSelfVersion (2) + TestSelfDiagnose (3) + TestEpilog (1) + TestUnknownKeys (9) → 893 tests (Round 21)
- **CLI: `completion export`** — Export script de complétion vers stdout pour piping/dotfiles (Round 20)
- **CLI: Rich traceback** — Stack traces Rich avec `show_locals=True` quand `GRIMOIRE_DEBUG=1` (Round 20)
- **Makefile: `release`** — Target `make release VERSION=x.y.z` : bump, build, instructions tag (Round 20)
- **Makefile: `bench`** — Target `make bench` pour benchmarks de performance (Round 20)
- **Makefile: `audit`** — Target `make audit` pour pip-audit de sécurité (Round 20)
- **Tests: fixture `init_project`** — Fixture partagée dans conftest.py pour projets pré-initialisés (Round 20)
- **Tests: +8** — TestCompletionExport (3) + TestDoctorFixture (5) → 866 tests (Round 20)
- **pyproject.toml: marker `bench`** — Nouveau marker pytest pour les tests de performance (Round 20)
- **CLI: `--quiet` / `--no-color`** — Flags globaux pour le scripting et l'intégration CI (Round 19)
- **CLI: JSON output `doctor`** — Sortie JSON structurée pour `grimoire doctor` avec checks détaillés (Round 19)
- **CLI: JSON output `validate`** — Sortie JSON pour `grimoire validate` : `{valid, errors, count}` (Round 19)
- **CLI: JSON output `check`** — Sortie JSON pour `grimoire check` avec phases détaillées (Round 19)
- **Docs: section "JSON scripting"** — Tableau récapitulatif de toutes les commandes avec support JSON (Round 19)
- **Tests: +24** — TestDoctorJson (3) + TestValidateJson (3) + TestQuietNoColor (4) + TestCheck JSON (2) + TestSchema core (12) → 858 tests (Round 19)
- **CLI `grimoire schema`** — Export JSON Schema Draft 2020-12 pour `project-context.yaml` (validation IDE et CI) (Round 18)
- **CLI `grimoire check`** — Commande compound : lint + validate + structure check en une passe (Round 18)
- **Core: `__all__` exports** — Ajout de `__all__` dans `config.py`, `validator.py`, `project.py`, `schema.py` (Round 18)
- **Core: `schema.py`** — Nouveau module `grimoire.core.schema` : générateur JSON Schema depuis la structure config (Round 18)
- **Tests: +10** — TestSchema (5 cas) + TestCheck (5 cas) → 834 tests unitaires (Round 18)
- **CLI JSON output** — Sortie JSON (`-o json`) pour `status`, `registry list`, `registry search` (Round 17)
- **Ruff: +4 catégories** — Ajout FLY (f-string), FURB (refurb), RSE (raise), ERA (dead code) → 20 catégories
- **GitHub: Issue templates** — Ajout `docs-improvement.yml` et `performance-regression.yml`
- **Docs: ADR-002 SemVer** — Architecture Decision Record sur la politique de versionnage et stabilité API
- **SECURITY.md enrichi** — Classification de sévérité, processus de divulgation, scope, timeline
- **CLI `grimoire lint`** — Commande de lint YAML avancée : validation structure, types, contraintes et références (sortie text/JSON)
- **CLI `grimoire diff`** — Affiche le drift de config entre le projet et les défauts de l'archétype (sortie text/JSON)
- **CLI `init --dry-run`** — Flag `--dry-run` sur `grimoire init` pour prévisualiser sans écrire
- **GitHub: Release Drafter** — Workflow `release-drafter.yml` + config : génère automatiquement les notes de release à partir des PRs
- **GitHub: Stale issue closer** — Workflow `stale.yml` : ferme automatiquement les issues/PRs inactives (60j stale + 14j close)
- **GitHub: PR auto-labeler** — Workflow `auto-label.yml` + `labeler.yml` : labellise automatiquement les PRs selon les fichiers modifiés
- **Docs: API reference mkdocstrings** — Autodoc Python intégrée dans mkdocs via `mkdocstrings[python]`
- **Docs: Référence config** — Page `docs/config-reference.md` : toutes les clés, types, défauts, valeurs valides, variables d'environnement
- **Docs: Plugin Development Guide** — Page `docs/plugin-development.md` : création d'outils, backends, archétypes, entry points
- **mypy étendu aux tests** — `[[tool.mypy.overrides]]` pour tests/ avec relaxation `disallow_untyped_defs`
- **Ruff PERF102** — Fix `.items()` → `.values()` dans 2 fichiers de tests
- **Ruff: 3 catégories de règles** — Ajout PIE (misc), PERF (performance), LOG (logging) au linter
- **Public API enrichie** — `GrimoireProject` exporté dans `grimoire.__init__` aux côtés de `GrimoireConfig` et `GrimoireError`
- **Docs: Référence CLI** — Page `docs/cli-reference.md` : toutes les commandes, flags, options, variables d'environnement
- **Docs: FAQ** — Page `docs/faq.md` : installation, backends, agents, plugins, migration, dépannage
- **Tests lint** — 7 tests (no config, valid, JSON valid, JSON invalid, direct YAML, invalid config, help)
- **Tests init --dry-run** — 6 tests (plan affiché, aucun fichier créé, validation archetype/backend)
- **Tests diff** — 5 tests (no config, fresh project, JSON output, archetype, help)
- **CI: Dependency audit** — Job `pip-audit --strict --desc` dans `ci-sdk.yml` pour détecter les CVE dans les dépendances
- **CI: Codecov upload** — Upload automatique de `coverage.xml` vers Codecov avec `codecov-action@v5`
- **CI: Cross-platform** — Matrice étendue à `ubuntu-latest`, `windows-latest`, `macos-latest` (Win/Mac sur Python 3.12)
- **CI: SBOM CycloneDX** — Génération SBOM JSON (`sbom.cdx.json`) dans le workflow publish, attaché aux releases GitHub
- **CLI `grimoire config show`** — Commande lecture de config (YAML complet ou clé dot-notation) avec sortie text/JSON
- **CLI `grimoire completion install`** — Installation automatique shell completion (bash/zsh/fish)
- **Tests integration** — Nouveau répertoire `tests/integration/` avec 12 tests end-to-end (init→doctor, config show, env+plugins flow)
- **Tests config + completion** — 10 tests unitaires pour `config show` (dot-key, JSON, missing, help) et `completion install`
- **pyproject.toml URLs** — Ajout Changelog + Issues dans `[project.urls]` pour PyPI
- **`GrimoireConfig.validate()`** — Méthode de validation sémantique : détecte les incohérences config (backend sans URL, nom vide)
- **CLI `grimoire plugins list`** — Commande listant les plugins installés (tools + backends) avec sortie text/JSON
- **Doctor amélioré** — 3 nouveaux checks : validation config, dépendances optionnelles (qdrant/ollama/mcp), version Python
- **Tests config validation** — 6 tests pour `GrimoireConfig.validate()` (warnings qdrant, ollama, blank name, cas valides)
- **Tests env + plugins** — 12 tests pour `grimoire env` (text/JSON) et `grimoire plugins list` (text/JSON/mocked)
- **`__all__` corrigés** — CLI exporte `["app", "cli"]`, MCP exporte `["main"]`
- **README badges** — Badges Ruff + Mypy strict ajoutés

- **Plugin discovery** — Module `grimoire.registry.discovery` : `discover_tools()` / `discover_backends()` via `importlib.metadata` entry points
- **Error codes en production** — Tous les `raise GrimoireConfigError` assignent maintenant un `error_code` (GR001–GR003)
- **Tests CLI global flags** — 9 tests pour `--verbose`, `--log-format`, `--output` (mock + intégration)
- **Tests plugin discovery** — 6 tests pour `discover_tools()` / `discover_backends()` (chargement, erreurs, multiples)
- **API Reference** — Page `docs/api-reference.md` : GrimoireConfig, exceptions, logging, retry, plugins, error codes
- **Docs nav** — Section « Référence » dans mkdocs.yml avec API reference et Changelog
- **CI hardening** — `permissions: contents: read` ajouté au workflow `ci-sdk.yml`
- **CLI `--output`/`-o`** — Flag global `--output text|json` pour sortie machine-readable (implémenté sur `grimoire env`)
- **Rich markup mode** — `rich_markup_mode="rich"` activé dans le Typer app pour panel/markup dans `--help`
- **Plugin entry points** — `[project.entry-points."grimoire.tools"]` et `"grimoire.backends"` dans pyproject.toml
- **Feature request template** — `.github/ISSUE_TEMPLATE/feature-request.yml` (formulaire structuré)
- **FUNDING.yml** — Sponsor GitHub activé via `.github/FUNDING.yml`
- **Tests `@deprecated()`** — 11 tests : warning emission, version/alternative dans message, functools.wraps
- **Tests `error_codes`** — 27 tests : ErrorCode class, CODES registry, catégories, `__slots__`
- **Tests `configure_logging`** — 16 tests : niveaux, env vars, handler setup, JSONFormatter
- **Tests `@with_retry()`** — 11 tests : success/failure, backoff, jitter, préservation nom/retour
- **JSON logging** — `configure_logging(fmt="json")` + `JSONFormatter` pour logs structurés machine-readable
- **CLI `--log-format`** — Flag global `--log-format text|json` pour choisir le format de sortie des logs
- **CLI `grimoire env`** — Commande de diagnostic (version, OS, dépendances, projet) pour les bug reports
- **CLI error handler** — Gestionnaire global d'erreurs avec messages rich ; `GRIMOIRE_DEBUG=1` pour traceback complet
- **`@with_retry()`** — Décorateur retry avec backoff exponentiel + jitter dans `grimoire.core.retry`
- **Error codes** — Codes stables `GR0xx`–`GR5xx` dans `grimoire.core.error_codes` + attribut `error_code` sur `GrimoireError`
- **Shell completion** — Documentation dans README (bash/zsh/fish via Typer natif)
- **CLI `--verbose`/`-v`** — Flag global de verbosité intégré à `configure_logging()` (`-v` = INFO, `-vv` = DEBUG)
- **CodeQL/SAST** — Workflow GitHub Actions `codeql-analysis.yml` (scans hebdomadaires + PR)
- **CITATION.cff** — Fichier de citation académique CFF 1.2.0
- **`@deprecated()`** — Décorateur de dépréciation dans `grimoire.core.deprecation`
- **Branch coverage** — `branch = true` ajouté à la config coverage
- **Classifiers PyPI** — Ajout `Environment :: Console` et `Topic :: Scientific/Engineering :: Artificial Intelligence`
- **Logging centralisé** — `grimoire.core.log.configure_logging()` + env var `GRIMOIRE_LOG_LEVEL`
- **Exceptions** — `GrimoireTimeoutError`, `GrimoireNetworkError` dans la hiérarchie
- **`__all__`** — Exports explicites pour `cli`, `mcp`, `registry`, `exceptions`
- **`python -m grimoire`** — Support PEP 302 via `__main__.py`
- **DevContainer** — `.devcontainer/devcontainer.json` pour onboarding en 1 clic
- **MkDocs** — Site de documentation Material + workflow GitHub Pages
- **Pre-commit** — Enrichi avec mypy strict, yamllint, check-toml, large file check
- **CI** — Coverage enforced (`--cov-fail-under=70`), pip caching, artifact XML
- **Makefile** — 16 targets (lint, test, check, pre-push, docs, clean…)
- **Tests** — +143 tests scaffolding pour 11 outils non couverts

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [3.1.0] — 2026-03-11

### User Config Sync + HPE Parallel Execution + Architecture Doc

#### Ajouté

- **`grimoire setup`** — Nouvelle commande CLI pour synchroniser la configuration utilisateur
  (nom, langue, niveau) depuis `project-context.yaml` vers tous les fichiers BMAD.
  Modes : `--sync`, `--check` (CI-friendly), `--json`, overrides CLI (`--user`, `--lang`, `--skill-level`)
- **`grimoire-setup.py`** — Outil standalone (stdlib-only) pour la même synchronisation,
  utilisable sans pip via `grimoire.sh setup`
- **HPE — High-Performance Execution** — Moteur d'exécution parallèle pour les outils :
  `hpe-runner.py` (orchestrateur), `hpe-executors.py` (ThreadPool/ProcessPool/Async),
  `hpe-monitor.py` (métriques temps réel), `agent-task-system.py` (dispatch intelligent)
- **ARCHITECTURE.md** — Documentation détaillée de l'architecture du projet
- **Tests** — +3200 lignes de tests : `test_grimoire_setup.py` (50),
  `test_hpe_runner.py`, `test_hpe_executors.py`, `test_hpe_monitor.py`,
  `test_agent_task_system.py`
- **Archetypes bundled** — Les archetypes sont désormais inclus dans le wheel Python

#### Documentation

- `getting-started.md` — Ajout de `grimoire setup` + section "Configurer votre identité"
- `onboarding.md` — `grimoire setup` intégré dans le parcours J1
- `grimoire-yaml-reference.md` — Section "Synchronisation avec grimoire setup"
- Installation : `pipx` et `venv` documentés comme alternatives à `pip install` système

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [3.0.0] — 2026-03-08

### Réécriture complète — SDK Python pur + Indépendance totale

Le projet prend son indépendance sous le nom **Grimoire Kit** avec un **package Python installable**
(`pip install grimoire-kit`), architecture modulaire, API typée, et couverture de tests extensive.

#### Ajouté

- **SDK Core** (`grimoire.core`) — Modèles immutables (`@dataclass(frozen=True, slots=True)`),
  `GrimoireConfig` pour le chargement de `project-context.yaml`, résolution de chemins,
  système d'exceptions typées (`GrimoireConfigError`, `GrimoireProjectError`, `GrimoireRegistryError`)
- **CLI complète** (`grimoire.cli`) — 12 commandes Typer : `init`, `doctor`, `status`,
  `add`, `remove`, `validate`, `up`, `upgrade`, `merge`, `registry list`, `registry search`
- **MCP Server** (`grimoire.mcp`) — Intégration Model Context Protocol avec 6 tools
  et 4 resources pour les IDE compatibles MCP
- **Outils portés** (`grimoire.tools`) — `harmony-check`, `preflight-check`, `memory-lint`
  réécrits en modules Python avec API programmatique (`run()` / `RunResult`)
- **Système de registre** (`grimoire.registry`) — Résolution d'agents, workflows, tasks
  depuis les manifests CSV avec support multi-modules
- **Système de mémoire** (`grimoire.memory`) — Architecture à backends : fichier JSON,
  Ollama (embeddings), Qdrant (vector store) avec interface `MemoryBackend` abstraite
- **Archétypes** — 8 templates de projet : `web-app`, `creative-studio`, `fix-loop`,
  `infra-ops`, `meta`, `minimal`, `stack`, `features`
- **Merge engine** (`grimoire merge`) — Fusion intelligente de fichiers YAML/Markdown
  avec détection de conflits et dry-run
- **Upgrade engine** (`grimoire upgrade`) — Migration entre versions avec diff et backup
- **Documentation** — `getting-started.md`, `concepts.md`, `onboarding.md`,
  `memory-system.md`, `workflow-design-patterns.md`, `workflow-taxonomy.md`,
  `creating-agents.md`, `archetype-guide.md`, `vscode-setup.md`, `troubleshooting.md`
- **CI / Qualité** — 694 tests unitaires, 96% couverture, ruff lint, mypy strict,
  `py.typed` marker

#### Modifié

- **Rebranding complet** — Toutes les références `bmad` renommées en `grimoire` dans le code source,
  tests, documentation, CI, shell scripts, et noms de répertoires (`_bmad/` → `_grimoire/`)
- **Entry points** — `grimoire` (CLI) et `grimoire-mcp` (serveur MCP) enregistrés
  dans `pyproject.toml`
- **Build** — Migration vers `hatchling` comme build backend
- **URLs** — Repo renommé en `Grimoire-kit`
- **MemoryManager** — Paramètre `project_root` explicite (déterministe, plus de `os.getcwd()`)
- **Atomic writes** — `LocalMemoryBackend._save()` utilise `tempfile` + `os.replace`

#### Supprimé

- Scripts shell standalone (remplacés par le SDK Python)
- Dépendance à `bash` pour l'exécution des outils
- Toute dépendance au package npm `bmad-method`

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.4.1] — 2026-03-03

### Corrigé — Bug hunt (3 fichiers, 10 corrections)

- **cognitive-flywheel.py** — `cmd_analyze()` n'écrivait pas dans l'historique →
 la tendance (trend) restait toujours "stable" car `compute_score` n'avait
 jamais de cycle précédent. Ajout d'un `append_history()` à chaque analyse.
- **cognitive-flywheel.py** — Variable morte `high` dans `apply_gates()` :
 construite mais jamais utilisée dans le return (dead code supprimé).
- **tests/test_maintenance_advanced.py** — 6 appels `open()` sans
 `encoding="utf-8"` : crash potentiel sur Windows/locales non-UTF8.

### Vérifié — Aucun problème trouvé

- Division par zéro : 12 sites vérifiés, tous protégés (max, or, if guards)
- Pyflakes (F) : 0 erreur sur 48 outils + 53 tests
- Bare except : 0 (tous les except ont un type)
- eval/exec : 0 appel dangereux
- assert en production : 0
- Fonctions dupliquées : 0
- Mutable default args : 0
- Shadowing builtins : 0
- 82 swallowed-exception (`except ... pass`) : tous intentionnels (graceful degradation)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.4.0] — 2026-03-02

### Ajouté — Cross-pollination depuis zav-sandbox (GSANE)

- **cognitive-flywheel.py** (outil #47) — Boucle d'auto-amélioration continue :
 analyse Grimoire_TRACE.md pour détecter les patterns récurrents (failures,
 AC-FAIL), calcule un score de santé (A+ à D), génère des corrections
 automatiques avec système de gates (max 5 corrections, collision → escalade).
 6 commandes CLI : `analyze`, `report`, `apply`, `history`, `score`, `dashboard`
- **failure-museum.py** (outil #48) — Catalogue structuré des échecs :
 enregistre chaque failure avec root-cause, règle ajoutée, sévérité et tags.
 Persistance JSONL + sync markdown automatique.
 7 commandes CLI : `add`, `list`, `search`, `stats`, `export`, `lessons`, `check`
- **cleanup-branches.yml** — Workflow CI GitHub Actions pour supprimer
 automatiquement les branches mergées (protège main/develop/release/*)
- **tests/test_cognitive_flywheel.py** — 45 tests couvrant dataclasses,
 parsing trace, extraction de patterns, scoring, corrections, gates,
 persistence report/history, scoreboard, commandes, CLI, constantes
- **tests/test_failure_museum.py** — 43 tests couvrant dataclasses,
 persistence JSONL, markdown sync, commandes, CLI, intégration, constantes
- Total : **1 875 tests**, 0 échecs

### Inspiré par

- [zav-sandbox](https://github.com/zavrocKk/zav-sandbox) (framework GSANE) :
 Cognitive Flywheel, Failure Museum, branch cleanup CI

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.3.0] — 2026-03-02

### Ajouté — Couverture de tests complète (31 fichiers, +787 tests)

- **31 fichiers de tests** générés pour couvrir les 46 outils du framework :
 `bias-toolkit`, `context-guard`, `context-router`, `crescendo`, `crispr`,
 `dark-matter`, `dashboard`, `decision-log`, `desire-paths`, `digital-twin`,
 `distill`, `early-warning`, `harmony-check`, `immune-system`, `incubator`,
 `mirror-agent`, `mycelium`, `new-game-plus`, `nudge-engine`, `oracle`,
 `preflight-check`, `project-graph`, `quantum-branch`, `r-and-d`, `rosetta`,
 `self-healing`, `semantic-chain`, `sensory-buffer`, `swarm-consensus`,
 `time-travel`, `workflow-adapt`
- Chaque fichier teste : dataclasses, fonctions pures, fonctions projet,
 formats de sortie, constantes, parser CLI, intégration CLI
- **_gen_tests.py** — générateur automatique de tests par analyse AST
- Total : **1 787 tests**, 0 échecs, ~130 s d'exécution

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.2.1] — 2026-03-02

### Corrigé — Audit multi-cycles (15 cycles, 3 fichiers)

- **nso.py:323** — condition morte `status = "ok" if ... else "ok"` corrigée
 en `"warn"` — le NSO signale désormais correctement les erreurs memory-lint
- **gen-tests.py** — ajout `encoding="utf-8"` sur 2 appels `open()` (L167, L260)
 — évite les erreurs d'encodage sur Windows avec des fichiers contenant des
 accents/emojis
- **r-and-d.py:849** — ajout `encoding="utf-8"` sur `tool_file.open()` dans
 l'analyse de gap (même correctif portabilité Windows)

### Vérifié — Aucun problème trouvé

- Division par zéro : 15+ sites vérifiés, tous protégés par des gardes
- Regex : toutes les regex compilées valides
- Références croisées `_load_tool()` : 8 appels, tous vers des fichiers existants
- Aucun `open()` sans `with`, aucun chemin absolu hardcodé
- Aucune variable non-initialisée dans `finally`, aucun dict muté pendant itération
- Aucun import inutilisé, aucune variable morte (ruff F401/F841 clean)
- Chemins mémoire/output cohérents entre tous les outils

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.2.0] — 2026-03-02

### Corrigé — Fuites mémoire Python

- **r-and-d.py** — 5 correctifs mémoire :
 - `save_memory()` : ajout cap `MAX_MEMORY_SIZE = 500` — tronque aux N entrées
 les plus récentes au lieu de grossir indéfiniment
 - `_load_tool()` : cache via `sys.modules` — évite de recréer le module à chaque
 appel (14 exec_module/cycle → 1 par outil)
 - `load_cycle_reports()` : paramètre `last_n` — ne charge que les N derniers
 rapports au lieu de tout l'historique
 - `next_cycle_id()` : extraction directe depuis le nom du dernier fichier
 au lieu de charger et parser tous les rapports JSON
 - `tool_file.open()` L817 : ajout `with` context manager (file descriptor leak)
- **nso.py** — `_load_tool()` : même cache `sys.modules`
- **dream.py** — `emit_to_stigmergy()` : cache `sys.modules` pour stigmergy
- **memory-lint.py** — `emit_to_stigmergy()` : cache `sys.modules` pour stigmergy

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.1.1] — 2026-03-01

### Supprimé

- **workflow-snippets.py** (389 lignes) — aucune intégration CLI, aucun test, aucune
 cross-référence. Overlap avec `workflow-design-patterns.md`
- **quorum.py** (400 lignes) — aucune intégration CLI, aucun test. Overlap fonctionnel
 avec `antifragile-score.py` (signaux SIL) et `stigmergy.py` (seuils phéromoniques)
- **confidence-scores.py** (572 lignes) — aucune intégration CLI, aucun test. Heuristiques
 simplistes, overlap avec `reasoning-stream.py` (niveaux de confiance)

### Corrigé

- Nettoyage des références aux 3 outils supprimés dans `docs/concepts.md`
- Total : **−1361 lignes** de dead code, 49 → 46 outils

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.1.0] — 2026-03-01

### Ajouté

- **CHANGELOG.md** — suivi formel des changements (issue R&D oracle-swot)
- **Multi-projet** pour `antifragile-score.py` — comparer la santé entre projets
 via `--multi-project dir1 dir2 ...`
- **Multi-projet** pour `dream.py` — croiser les insights entre projets
 via `--multi-project dir1 dir2 ...`
- **Moteur R&D v2.1** — filtre anti-chaîne de mutations + pénalité actionnabilité

### Corrigé

- Nettoyage du TODO orphelin dans le template prototype de `r-and-d.py`
- Moteur R&D : les mutations de mutations (profondeur > 1) sont progressivement
 pénalisées dans le challenge, réduisant le bruit combinatoire

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.0.0] — 2026-02-28

### Ajouté

- **r-and-d.py v2.0** — Moteur d'Innovation R&D avec apprentissage par renforcement
 - Closed-loop reward via health snapshots du projet réel
 - Challenge durci : GO threshold 0.60, CONDITIONAL 0.40, quota 20% rejet
 - Générateur de mutations des gagnants passés (transposition, escalade,
 inverse, fusion)
 - Générateur gap-driven (gaps réels : tests manquants, docs absentes,
 domaines sous-représentés, dépendances fragiles)
 - Commande `seed` pour initialiser la mémoire
 - Commande `health` — snapshot de santé du projet
 - Commande `prototype` — génération de squelettes Python
 - 13 sources de récolte (dream, oracle-swot, oracle-attract, early-warning,
 dna-drift, workflow-adapt, antifragile, harmony, stigmergy, incubator,
 synthetic, mutation, gap-analysis)

### Corrigé

- Déduplication inter-cycles dans le moteur R&D (idées recyclées filtrées)
- Générateur synthétique enrichi (21 templates de concept blending)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.6.0] — 2026-02-27

### Ajouté

- **Vague 6** — 7 outils d'exploration avancée :
 - `digital-twin.py` — simulation de l'écosystème projet
 - `quantum-branch.py` — exploration parallèle de décisions
 - `time-travel.py` — machine à remonter le temps projet
 - `crispr-rules.py` — mutation ciblée de règles agents
 - `decision-log.py` — journal structuré des décisions
 - `mirror-agent.py` — audit croisé inter-agents
 - `sensory-buffer.py` — tampon sensoriel entre sessions

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.5.0] — 2026-02-26

### Ajouté

- **Vague 5** — Dream Nervous System :
 - `dream.py` v2 — mémoire cross-session, décroissance temporelle, bigram keywords
 - Boucle fermée nervous system avec feedback loop et trigger intelligent
 - `memory-lint.py` — vérificateur d'hygiène mémoire
 - `nso.py` — orchestrateur du système nerveux

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.4.0] — 2026-02-25

### Ajouté

- **Vague 4** — Stigmergy :
 - `stigmergy.py` — coordination indirecte par phéromones numériques

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.3.0] — 2026-02-24

### Ajouté

- **Vague 3** — Cross-Project Migration + Agent Darwinism :
 - `cross-migrate.py` — migration d'artefacts entre projets
 - `agent-darwinism.py` — sélection naturelle des agents

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.2.0] — 2026-02-23

### Ajouté

- **Vague 2** — Anti-Fragile Score + Reasoning Stream :
 - `antifragile-score.py` — scoring de résilience adaptative
 - `reasoning-stream.py` — flux de raisonnement structuré

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.1.0] — 2026-02-22

### Ajouté

- **Vague 1** — Dream Mode + Adversarial Consensus :
 - `dream.py` — consolidation hors-session et insights émergents
 - `adversarial-consensus.py` — protocole de consensus adversarial

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.0.0] — 2026-02-20

### Ajouté

- Vagues précédentes : 25 outils de base, protocole cognitif Completion Contract,
 Modal Team Engine, Self-Improvement Loop, Vector DB, web-app archetype
- Architecture framework : agent-base, agent-rules, hooks, mémoire, sessions,
 outils, registre, équipes, workflows
- Archetypes : web-app, infra-ops, minimal, stack, meta, features, fix-loop
- Documentation : getting-started, archetype-guide, memory-system, troubleshooting,
 workflow-design-patterns, creating-agents
- Tests : smoke-test.sh + suite de tests Python (122 tests)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [0.1.0] — 2026-02-15

### Ajouté

- Initial commit — Grimoire Custom Kit structure de base
