---
audience: contributeur
---

<p align="right"><a href="../README.md">README</a> · <a href="../CHANGELOG.md">Changelog</a> · <a href="index.md">Docs</a></p>

# <img src="assets/icons/play.svg" width="32" height="32" alt=""> Banc à trois bras

> Issue [#551](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/551) —
> plan produit 2026-Q4 (`docs/plan-2026-q4.md`), phase 1, lot 1.1. Protocole
> complet, rejouable par un tiers sans accès à cette conversation.

## 1. Objectif

Prouver ou infirmer, sur des tâches publiques, que Claude Code gouverné par
grimoire-kit livre au moins aussi bien qu'un Claude Code nu ou qu'un Claude
Code avec le paquet [ecc](https://github.com/affaan-m/ECC) (MIT) — en qualité
d'abord, puis temps, puis coût. Le critère d'arrêt du plan (§3 du plan
2026-Q4) se lit directement sur ce banc :

- Si le kit est moins bon que l'hôte nu **à la fois** en réussite **et** en
  coût : on arrête les extras du plan et on retravaille le cœur (dispatch,
  contexte injecté, mémoire) avant toute parité.
- Si réussite égale et coût ≤ 70 % de l'hôte nu : le plan continue.

## 2. Les quatre bras

| Bras | Configuration du dépôt de tâche | Isolation |
|---|---|---|
| `nu` | Rien. `git init` seul. | `HOME` isolé, jetable, sans identifiants ECC/kit. |
| `ecc` | Plugin Claude Code `ecc@ecc` installé en **scope projet** (`.claude/settings.local.json`), depuis un clone local du dépôt ecc épinglé au commit utilisé. | `HOME` isolé partagé entre tous les runs `ecc` (cache du plugin, voir §4). |
| `kit` | `grimoire init . --backend local --no-cockpit` puis `grimoire host sync --host claude` (grimoire-kit ≥ 3.53.0, installé depuis PyPI). | `HOME` isolé partagé (évite d'écrire dans `~/.grimoire`) ; l'état projet (`_grimoire/`) vit dans le dépôt de tâche lui-même. |
| `kit-gov` | Le bras `kit`, PLUS `grimoire standard init` (profil `starter`, celui qu'un développeur seul obtient sans option) et une tâche de board — à l'id de la tâche du banc — posée en `in_progress` via `grimoire task migrate-standard` (ADR-007). | `HOME` isolé propre à ce bras (`workspace/homes/kit-gov`), jamais partagé avec `kit`. |

Depuis B-01, `--model` est obligatoire pour `--pilot` et `--full` : les quatre
bras partent sur le même modèle demandé (voir §12). Avant B-01 ils utilisaient
le modèle par défaut de Claude Code, qui a changé entre le lot K et le lot L.
Le modèle demandé fixe la session principale ; les bras `kit`/`kit-gov` gardent
le droit de cascader vers un modèle moins cher via leurs propres hooks et leur
propre routage, et `model_usage` (modèle effectif) le révèle : c'est ce que la
mesure de coût doit montrer, pas quelque chose à neutraliser.

### Ce que le bras `ecc` ajoute exactement

Commandes (README d'ecc, §« Install ECC », mode automatisable) :

```bash
claude plugin marketplace add <chemin-local-du-clone-ecc> --scope local
claude plugin install ecc@ecc --scope local
```

Le `--scope local` écrit `.claude/settings.local.json` **dans le dépôt de
tâche**, jamais dans le `HOME` réel de l'opérateur. La preuve d'isolation (mtimes
inchangées sur `~/.claude/settings.local.json` et `~/.claude.json` avant/après)
est documentée dans le pack de preuve de la PR harnais. Le contenu réel du
plugin (68 agents, 292 skills, commandes, hooks, règles — chiffres de
`.claude-plugin/plugin.json`) vit dans le cache de l'installation, sous
`<ecc_home>/.claude/plugins/cache/ecc/ecc/<version>/`, et est donc partagé
entre tous les runs `ecc` d'une même campagne (téléchargé une fois).

Version utilisée : celle du commit HEAD du clone `--depth 1` fait au lancement
du banc (`workspace/ecc/`), enregistrée dans `state/selection.json` sous
`ecc_commit`. Au 2026-09-16, `.claude-plugin/plugin.json` déclarait la version
`2.2.1`. Licence : MIT (voir `LICENSE` du dépôt ecc).

### Ce que le bras `kit` ajoute exactement

- `grimoire init . --backend local --no-cockpit` : crée `_grimoire/` dans le
  dépôt de tâche (registre, mémoire backend local, standard agentique), sans
  jamais démarrer le cockpit (`GRIMOIRE_NO_COCKPIT=1` en plus du flag, par
  ceinture-bretelles).
- `grimoire host sync --host claude` : matérialise les fichiers Claude Code
  (CLAUDE.md, commandes, hooks, persona d'entrée) sous `.claude/` et à la
  racine du dépôt de tâche.

Le harnais journalise la liste exacte des fichiers ajoutés (diff de
l'arborescence avant/après ces deux commandes) dans le résultat de setup de
chaque run `kit` — voir `setup_arm_kit()` dans `scripts/bench/three_arms.py`.
`grimoire dispatch stats --json` est interrogé après chaque run `kit`/`kit-gov`
et son résultat est conservé dans `RunRecord.dispatch_stats`, y compris quand
il est vide (une tâche d'exercice résolue en une session headless ne passe
pas nécessairement par `grimoire task dispatch` — c'est un résultat honnête à
publier tel quel, pas une raison d'inventer un événement).

### Ce que le bras `kit-gov` ajoute exactement (lot F, issue #582)

Le rejeu du lot E (`docs/bench/rejeu-lot-e-2026-09-17.md` §3) a mesuré que le
bras `kit` ne fait jamais passer `_is_governed()` (lot A,
`src/grimoire/hosts/decisions/activation.py`) à `True` : il n'installe
jamais `_grimoire/standard/`. Le lot B (`gate run-tests`, la directive
gouvernée) reste donc structurellement invisible sur le bras `kit`, quel que
soit le nombre de rejeux — pas un défaut du lot B, un défaut de méthode du
banc. `kit-gov` le corrige en simulant un projet réellement enrôlé, en plus
de tout ce que fait `kit` :

- `grimoire standard init .` — profil par défaut `starter` (celui qu'un
  développeur seul obtient sans `--profile`/`--needs`).
- Une tâche de board à l'id de la tâche du banc (barres obliques remplacées
  par des doubles underscores : Grimoire refuse `/` dans un `task_id`),
  posée directement en `in_progress`, puis importée dans le Mission Ledger
  par `grimoire task migrate-standard` (ADR-007,
  `docs/adr-007-unification-des-taches.md`, Grimoire-kit#587/#588) — seule
  voie CLI qui préserve un id exact plutôt que d'en dériver un du titre
  (`grimoire task add` n'expose aucune option `--task-id`).

Vérifié en isolation (dépôt jetable, non versionné) avant le premier rejeu :
`grimoire standard activation-context` rend alors la directive complète avec
`gate run-tests --task-id <id>`, `_is_governed()` vaut `True` et
`active_task_id()` résout bien cet id — jamais `bootstrap`. Voir
`setup_arm_kit_gov()` dans `scripts/bench/three_arms.py`.

## 3. Authentification en environnement isolé

Isoler entièrement `HOME` casse l'authentification de Claude Code par défaut
(« Not logged in · Please run /login » — l'auth interactive vit dans
`~/.claude/.credentials.json`, liée à OAuth). Deux modes, choisis par
`--auth {oauth-copy,api-key}` (défaut : `api-key` si `ANTHROPIC_API_KEY` est
présente dans l'environnement du lanceur, sinon `oauth-copy` avec un
avertissement — voir `resolve_auth_mode()`) :

| Mode | Ce qui est transmis à `claude -p` | Copie d'identifiant | Risque |
|---|---|---|---|
| `api-key` (préféré, bras `nu` seul : voir le piège `--bare` ci-dessous) | `--bare` + `ANTHROPIC_API_KEY` (héritée de l'environnement du lanceur par `run_environment()`) | Aucune | Aucun |
| `oauth-copy` (repli) | Rien de spécial ; `~/.claude/.credentials.json` copié dans le `HOME` isolé du run | Temporaire, un fichier par run | Rotation de jeton partagée (voir §3.2) |

### 3.1 Mode `api-key` (lot J, #582)

Aucun identifiant n'est jamais copié. `run_claude_headless` ajoute `--bare` à
la commande quand `auth_mode="api-key"` ; `_run_one` n'appelle alors JAMAIS
`credentials_provisioned`. D'après la documentation Claude Code consultée le
2026-09-18 :

- `code.claude.com/docs/en/headless.md` (« Start faster with bare mode ») :
  « In bare mode, Claude Code never reads OAuth credentials or the system
  keychain. » — le mode conçu précisément pour ce cas d'usage.
- `code.claude.com/docs/en/authentication.md` (ordre de précédence) :
  `ANTHROPIC_API_KEY` prime sur les identifiants OAuth de toute façon — même
  hors `--bare`, la variable l'emporterait.

`ANTHROPIC_API_KEY` n'a besoin d'aucune transmission spéciale : `env`
(construit une fois par `run_environment()`, voir §5) part de `os.environ`
et la contient donc déjà si elle est exportée dans l'environnement du
lanceur. Aucun fichier de config supplémentaire (`.claude.json` ou autre)
n'est nécessaire — `--bare` est justement documenté comme la voie CI
recommandée pour éviter tout onboarding interactif.

**Piège `--bare` : refusé pour tout bras gouverné (B-06).** `claude --help`
(2.1.302) : `--bare` saute les hooks, les skills et la découverte de `CLAUDE.md`.
Un bras dont la mesure dépend de ceux du projet (`ecc`, `kit`, `kit-gov`, `aidlc`)
lancé en `--auth api-key` ne serait donc pas gouverné, sans aucun signal :
résultat faux. `scripts/bench/bare_guard.py` (`refuse_bare_for_governed_arms`)
fait échouer le banc (`SystemExit`, message explicite) dès `main`, avant tout
appel payant, et `run_claude_headless(arm=...)` refuse aussi un `--bare` forcé
pour ces bras. Seul `nu` (ni hook, ni skill, ni instruction) garde `--bare` :
`--auth api-key` ne vaut donc que pour `--arms nu`. Les autres bras se lancent en
`--auth oauth-copy` (rotation de jeton : §3.2). Non implémenté : lancer
`api-key` sans `--bare` (la doc d'authentification donne la priorité à
`ANTHROPIC_API_KEY` sur OAuth même hors `--bare`) ; non vérifié sur un appel réel.

**Garde de fin de campagne (mode `api-key`).** `find_leaked_api_keys(workspace,
api_key=...)` balaie les `HOME` isolés (`root/homes/**`) et les journaux de
run (`root/tasks/**/*.stream.jsonl`) à la recherche du littéral de la clé —
jamais affichée, seuls les chemins concernés sont remontés. `--pilot` et
`--full` l'appellent après le dernier run, uniquement en mode `api-key` : une
trouvaille est signalée bruyamment sur stderr (`[ALERTE SÉCURITÉ]`), sans
suppression automatique (la structure du fichier trouvé n'est pas connue).
Ne doit jamais rien trouver dans une campagne saine. Preuve par test :
`test_find_leaked_api_keys_detects_key_in_isolated_home_and_in_run_log`.

### 3.2 Mode `oauth-copy` (repli, comportement historique)

C'est la même clé physique que celle déjà configurée sur le poste ; elle
n'est ni régénérée ni journalisée (`shutil.copyfile`, jamais lue ni affichée
par ce script).

**Le fichier n'est jamais laissé à demeure dans un `HOME` isolé.** Les `HOME`
créés par `ensure_isolated_home()` (un par bras, partagés entre les runs de
ce bras pour réutiliser le cache du plugin ecc et le registre du kit) ne
portent aucun identifiant au repos — avant le premier run, entre deux runs,
après la campagne. Les identifiants ne sont copiés que pour la durée d'un
seul appel `claude -p`, via le gestionnaire de contexte
`credentials_provisioned(home)` :

```python
with credentials_provisioned(home) as creds:
    outcome = run_claude_headless(run_dir, prompt, home=home, ...)
# à la sortie du bloc — succès, exception, ou timeout intercepté par
# run_claude_headless — le fichier a déjà été supprimé.
```

La suppression vit dans le `finally` du gestionnaire de contexte : elle
s'exécute que le run se termine normalement, lève une exception, ou soit tué
pour timeout/boucle (`run_claude_headless` ne lève pas dans ces deux derniers
cas, mais le `finally` ne dépend pas de ce choix — une exception plus tard
dans `_run_one` déclencherait le même nettoyage). Preuve par test :
`test_credentials_provisioned_copies_then_removes_on_success` et
`test_credentials_provisioned_removes_even_on_exception`
(`tests/unit/test_bench_three_arms.py`).

Les commandes de setup qui n'appellent pas `claude -p` (`claude plugin
marketplace add`/`install` pour le bras `ecc`, `grimoire init`/`host sync`
pour le bras `kit`) ne reçoivent jamais d'identifiant — elles n'en ont pas
besoin (vérifié en réel : ces commandes réussissent sans aucun fichier de
credentials dans le `HOME` isolé).

**Cause des déconnexions en rafale (lots E, F, H, J).** Les jetons OAuth de
Claude Code sont rafraîchis par rotation. Copier le même jeton dans le `HOME`
isolé de plusieurs runs (même séquentiels : le fichier réel du poste change
dès qu'un run le rafraîchit) fait que le rafraîchissement déclenché par UNE
copie invalide à la fois la session interactive de l'opérateur ET toute autre
copie en cours d'utilisation — observé quatre fois en deux jours,
systématiquement pendant une campagne (« OAuth session expired and could not
be refreshed »), jusqu'à 30 runs perdus en une fois (lot J). Le mode
`api-key` (§3.1) élimine ce risque par construction : préférez-le dès que
`ANTHROPIC_API_KEY` est disponible.

**Garde de fin de campagne.** `find_leftover_credentials(workspace)` balaie
tous les `HOME` isolés à la recherche d'un `.credentials.json` oublié (bug de
nettoyage, process tué avant que le `finally` n'ait pu s'exécuter — signal
possible si l'orchestrateur du système d'exploitation envoie un `SIGKILL`
plutôt qu'un `SIGTERM`). `--pilot` et `--full` l'appellent après le dernier
run, quel que soit le mode d'authentification : la campagne n'échoue pas
là-dessus, mais toute trouvaille est effacée immédiatement et signalée
bruyamment sur stderr (`[ALERTE SÉCURITÉ]`) — elle ne doit jamais apparaître
dans une campagne saine. Preuve par test :
`test_find_leftover_credentials_detects_and_is_clean_after_normal_use`.

Le rapport (`report.md`, voir §10) nomme le ou les modes effectivement
utilisés par la campagne (`report["auth_modes"]`, `RunRecord.auth_mode`).

## 4. Jeu de tâches

[Aider polyglot-benchmark](https://github.com/Aider-AI/polyglot-benchmark)
(licence : exercices sourcés des pistes Exercism officielles — Python, Go,
JavaScript, Rust, Java, C++ — chacune MIT côté Exercism ; voir le
`README.md` du dépôt, section « Source Attribution »).

- **4 langues retenues** : Python, JavaScript, Go, Rust (parmi les 6
  disponibles ; Java et C++ écartés faute de toolchain de build complète —
  `javac`/Maven/Gradle et `g++` absents de l'environnement de référence).
- **5 exercices par langue, tirage reproductible** : `sample_tasks()` trie le
  catalogue par identifiant puis tire `per_language=5` exercices avec
  `random.Random(f"{seed}:{langue}")`, graine par défaut **551** (numéro de
  l'issue). Le tirage est indépendant de l'ordre de retour du système de
  fichiers.
- **Tests cachés** : `prepare_task_repo()` copie l'énoncé (`.docs/` →
  `TASK.md`) et les fichiers de solution/support de l'exercice, mais exclut
  les fichiers listés comme `test` dans `.meta/config.json`. Ces fichiers de
  test sont conservés à part (`hidden_tests_dir()`) et ré-injectés
  uniquement au moment de la vérification, jamais donnés à l'agent.
- **Ordre des bras tiré au sort par tâche, reproductible** :
  `select_run_order()`, graine dérivée de `(seed, task_id)`.

Commande d'exécution des tests cachés par langue :

| Langue | Commande | Remarque |
|---|---|---|
| Python | `pytest -q` | aucune dépendance externe |
| JavaScript | `npm install --no-audit --no-fund` puis `npx jest --silent` | dépendances identiques sur tous les exercices (jest + babel), cache npm réutilisé après le premier run |
| Rust | `cargo test --quiet` | aucune dépendance externe (vérifié sur l'échantillon) |
| Go | `go test ./...` | toolchain Go **provisionnée en local** si absente du `PATH` (`ensure_go_toolchain()` télécharge go1.23.4 linux/amd64 dans le workspace du banc — jamais dans le système ; sur une autre plateforme, installer Go manuellement et l'exposer via `PATH`) |

## 5. Environnement partagé agent/harnais (lot I, #582)

Le rejeu du lot H (`docs/bench/rejeu-lot-h-2026-09-18.md` §3) a mesuré que
`grimoire standard gate check --strict` exécute la commande de test du
projet DANS la session de l'agent (`claude -p`, `HOME` isolé, lot G1) —
mais que le harnais ne provisionnait la toolchain Go (`ensure_go_toolchain`)
que pour SA PROPRE vérification finale (`run_hidden_tests`) : deux
environnements différents pour la même commande de test, jamais transmis à
l'agent. Résultat : `go` absent du `PATH` de la session, jusqu'à 28 tours
perdus à chercher le binaire (`command -v go`, `find / -name gofmt`) sur les
pires runs Go, un risque analogue (non chiffré séparément) pour Rust
(`rustup toolchain list`/`rustup default stable` observés sur `rust/react`).

**Une seule fonction, `run_environment(task_dir, arm, *, home, grimoire_bin,
workspace, go_bin=None, npm_cache_dir=None, real_home=None, base_env=None)`**
calcule désormais cet environnement — appelée UNE fois par run dans
`_run_one`, le même `dict` est transmis tel quel à `run_claude_headless`
(paramètre `env`, la session de l'agent, ses hooks, et donc `gate check
--strict`) et à `run_hidden_tests` (paramètre `env`, la vérification finale
du harnais) : jamais deux calculs qui pourraient diverger.

- **Go** : le binaire résolu par `ensure_go_toolchain()` est mis EN TÊTE du
  `PATH`, pour LES QUATRE BRAS — un agent `nu`/`ecc` sur une tâche Go
  affronte la même absence de toolchain qu'un agent gouverné, ce n'est pas
  spécifique à `kit-gov`. `GOROOT` n'est fixé que si le binaire est celui
  provisionné par ce harnais sous le workspace (jamais pour un `go` système,
  dont la disposition des répertoires n'est pas garantie) ; `GOPATH`
  (`tools/go-path`) et `GOCACHE` (`tools/go-build-cache`) sont TOUJOURS
  redirigés sous le workspace, partagés entre bras et tâches — jamais écrits
  sous le `HOME` réel de l'opérateur ni sous un `HOME` isolé éphémère.
- **Rust/Node** : jamais provisionnés par ce harnais (contrairement à Go) —
  `CARGO_HOME`/`RUSTUP_HOME` sont explicitement repointés sur ceux du `HOME`
  réel de l'opérateur (`real_home`, `Path.home()` par défaut) SI
  l'environnement de base ne les redirige pas déjà lui-même : sans ça, le
  `HOME` isolé utilisé pour l'authentification Claude Code ferait chercher à
  `cargo`/`rustup` une toolchain sous un `$HOME/.cargo` qui n'existe pas pour
  cet utilisateur isolé, quand bien même Rust est installé sur le poste —
  cause probable de la friction Rust observée au lot H. `CARGO_TARGET_DIR`
  (le cache de compilation, pas le registre) est lui TOUJOURS redirigé sous
  le workspace (`tools/cargo-target`).
- **npm** : `npm_config_cache` sous `npm_cache_dir` si fourni — même
  convention que `install_test_dependencies` (lot H).
- **grimoire** : son répertoire n'est ajouté en tête de `PATH` (et
  `GRIMOIRE_NO_COCKPIT=1` posé) que pour les bras `kit`/`kit-gov` —
  `nu`/`ecc` ne doivent jamais découvrir `grimoire` par un effet de bord de
  ce harnais.

**Garde-fous avant toute dépense**, dans cet ordre, avant le tout premier
appel `claude -p` d'une campagne `--pilot`/`--full` (jamais pour
`--dry-run`/`--report-only`) — même emplacement que
`verify_grimoire_binary_matches_template` (lot H) :

1. `ensure_system_toolchains_present(languages)` : Rust (`cargo`) et Node
   (`npm`) doivent déjà être sur le `PATH` système pour les langues
   réellement présentes dans la sélection de tâches — `SystemExit` avec la
   liste complète des manques sinon, jamais un `cargo test` qui échoue en
   pleine session `claude -p` payante.
2. `verify_agent_toolchain_environment(tasks, ...)` : pour CHAQUE langue de
   la sélection, provisionne un dépôt de vérification jetable (tests cachés
   compris — jamais donnés à un agent réel, uniquement à cette vérification
   interne) sous `workspace/_agent_toolchain_check/<langue>`, résout la
   commande de test via `grimoire needs resolve --project-root . --json`
   (la même source que `gate check --strict`), et l'exécute dans
   `run_environment()`. Le code de sortie n'est PAS vérifié — un test peut
   légitimement échouer faute de solution écrite — seule l'ABSENCE d'un
   échec de toolchain est exigée (commande introuvable/`FileNotFoundError`,
   exit 127, motif `... not found` dans la sortie). Un écart lève
   `RuntimeError` avant tout appel modèle, 0 $ dépensé ; le résultat est
   persisté sous `state/toolchain_check.json` et publié dans le rapport
   (section « Environnement de l'agent »).

**Indicateur de friction de toolchain** : `count_toolchain_friction_bash_calls`
compte, parmi les commandes Bash déjà extraites par `extract_bash_command()`
(utilisées pour la détection de boucle), celles qui contiennent
`which`/`command -v`/`find / -name`/`rustup`/`npm install` —
`is_toolchain_friction_command()` pour le motif exact. Calculé pour chaque
run (`RunOutcome.toolchain_friction_bash_calls`, recopié dans
`RunRecord`), agrégé par bras dans le rapport (total et médiane) et détaillé
par run dans les tableaux « Détail par run — bras kit/kit-gov » : l'indicateur
qui doit baisser une fois `run_environment()` en place, en particulier sur le
bras `kit-gov` (seul bras qui mandate l'exécution réelle des tests).

## 6. Garde-fous par run

- **Timeout** : `--run-timeout-s` (défaut 900 s = 15 min). Un run qui dépasse
  est terminé (`SIGTERM` puis `SIGKILL` après 10 s) et compte comme échec,
  jamais retiré du rapport (`terminated_reason: "timeout"`).
- **Boucle** : le flux `--output-format stream-json` est lu en continu ;
  chaque commande Bash exécutée par l'agent est extraite
  (`extract_bash_command()`). Si les 4 dernières commandes consécutives sont
  identiques (`detect_loop()`, seuil configurable), le run est terminé
  (`terminated_reason: "loop"`) et compte comme échec.
- **Permissions** : `--permission-mode bypassPermissions`, restreint de fait
  au dépôt de tâche par le `cwd` (chaque dépôt est jetable et isolé sous le
  workspace du banc). `--setting-sources project,local` exclut explicitement
  les settings « user », même si le `HOME` isolé ne devrait de toute façon en
  porter aucun.
- **Coût** : pas de plafond en dollars (décision de Guilhem). Le coût réel
  cumulé est comparé à 3x l'estimation issue du pilote après chaque tâche du
  mode `--full` ; un dépassement déclenche une ligne d'alerte dans le rapport
  et sur stderr, sans jamais bloquer la campagne.

## 7. Critère d'arrêt statistique

Après chaque tâche complétée (les trois bras, k=3), `should_stop_early()`
recalcule un intervalle de confiance bootstrap (2000 ré-échantillonnages) sur
le taux de succès agrégé de chaque bras. La campagne s'arrête dès que les IC
de `kit` sont disjoints de **ceux de `nu` et de `ecc`**, avec un minimum de 4
tâches jouées pour éviter un verdict sur trop peu de données ; sinon elle va
au bout des 20 tâches.

## 8. Mesures

Par run : succès (tests cachés verts), coût réel (`total_cost_usd` renvoyé
par `claude -p --output-format json`/`stream-json`, pas une reconstruction
manuelle tokens × tarif — c'est la même source que la facturation réelle),
tokens d'entrée/sortie, nombre de tours (`num_turns`), temps mur, friction de
toolchain (`toolchain_friction_bash_calls`, lot I — voir §5), et pour le
bras `kit` : `grimoire dispatch stats --json` du dépôt de tâche.

Une fois par campagne, avant le premier run : l'environnement de l'agent par
langue (`state/toolchain_check.json`, lot I — voir §5), publié dans le
rapport sous « Environnement de l'agent ».

Par tâche x bras : pass^k (k=3) au sens du kit
(`pass_k_fully_green / pass_k_observations`, `src/grimoire/traces/ledger.py`)
— une série de k rejeux compte 1 si les trois sont verts, 0 sinon
(`pass_hat_k()`).

Rapport agrégé (`report.md` + `report.json`) : par bras — nombre de runs, de
tâches, taux de succès avec IC95% bootstrap, taux de pass^k avec IC95%, temps
mur médian, coût médian par tâche résolue, coût total ; par tâche — succès
par bras et pass^k.

## 9. Reproduire le banc

```bash
cd /chemin/vers/le/worktree/grimoire-kit
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e . pytest ruff mypy pyyaml jsonschema mcp

# 1. Dry-run : clone les dépôts, tire les 20 tâches, prépare les 60 dépôts
#    (20 tâches x 3 bras), sans appeler aucun modèle.
python scripts/bench/three_arms.py --dry-run --workspace /chemin/scratch/bench-workspace

# 2. Pilote réel : 2 tâches x 3 bras x 1 rejeu (6 appels claude -p réels).
#    Écrit workspace/state/expected_cost.json (coût attendu extrapolé).
python scripts/bench/three_arms.py --pilot --model <identifiant-de-modele> --workspace /chemin/scratch/bench-workspace

# 3. Campagne complète : jusqu'à 20 tâches x 3 bras x k=3 (180 runs), avec
#    arrêt anticipé si le critère statistique est atteint plus tôt.
python scripts/bench/three_arms.py --full --model <identifiant-de-modele> --workspace /chemin/scratch/bench-workspace \
    --resume  # relance sans rejouer ce qui est déjà dans results.jsonl

# 4. Régénérer report.md/report.json depuis results.jsonl sans rien rejouer :
python scripts/bench/three_arms.py --report-only --workspace /chemin/scratch/bench-workspace

# Authentification (lot J, #582, voir §3) : --auth api-key explicite (recommandé,
# aucune copie d'identifiant) ou --auth oauth-copy explicite (repli historique) ;
# sans l'option, le mode est déduit de la présence d'ANTHROPIC_API_KEY.
ANTHROPIC_API_KEY=sk-ant-... python scripts/bench/three_arms.py --pilot --model <identifiant-de-modele> \
    --workspace /chemin/scratch/bench-workspace --auth api-key
```

Prérequis système pour `--pilot`/`--full` : `claude` (Claude Code CLI,
authentifié en mode `oauth-copy`, ou `ANTHROPIC_API_KEY` exportée pour le mode
`api-key` — voir §3), `node`/`npm`, `cargo`/`rustc`, `git`. `go` est
provisionné automatiquement sur Linux/amd64 s'il est absent du `PATH`.
Depuis le lot I (#582), Rust/Node sont vérifiés par le code AVANT toute
dépense (`ensure_system_toolchains_present`, §5) — un manquant arrête la
campagne avec un message actionnable plutôt que de laisser échouer un run
payant en pleine session.

`--dry-run` et les tests unitaires (`tests/unit/test_bench_three_arms.py`) ne
nécessitent ni réseau ni authentification : ils exercent le tirage, la
préparation de dépôt, la détection de succès (Python, via `pytest`
directement), le calcul de pass^k/IC et la détection de boucle sur des
fixtures synthétiques.

## 10. Sorties et emplacement du rapport

`workspace/state/selection.json` (tâches tirées, commit ecc), `results.jsonl`
(une ligne par run, append-only — permet `--resume` après interruption),
`expected_cost.json` (estimation post-pilote), `toolchain_check.json`
(vérification de l'environnement de l'agent par langue, lot I, §5),
`reports/<date>/report.md` et `.json`.

Le rapport de la première campagne réelle est publié sur la branche
`bench-reports` (non protégée, poussée directement — comme
`weekly-bench.yml` — sous
`_grimoire-output/bench-reports/three-arms/<date>/report.md` et `.json`), pas
sur `main`.

## 11. Garde de coût face au nu à chaque barrière (B-00)

Chaque chantier qui ajoute de l'appel modèle (panel, passes de vérification, juge) ajoute du coût ; le critère de
coût du lot L (≈124 % du nu pour 105 % visés) ne doit pas se dégrader sans que quelqu'un le voie. À chaque barrière,
on rejoue un **sous-lot fixe** par `nu` et `kit-gov` et on compare le ratio à celui de la barrière précédente.

- **Sous-lot** : `docs/bench/cost-gate-subset.json`, 4 tâches (une par langue, première dans l'ordre alphabétique du lot L),
  k=3, soit 24 runs. Il est saturé : il mesure le coût, pas le succès, et le rapport le dit.
- **Base** : `docs/bench/cost-gate-baseline.json`, le ratio du même sous-lot relu dans les 120 lignes du lot L : **118,4 %**
  (le 124 % du lot L porte sur les 20 tâches). Aucun nouveau run n'a servi à l'établir.
- **Tolérance** : 5 points par barrière (décision 13 du plan : arbitraire, non mesurée ; `--tolerance-points`).
- **Un coût inconnu n'est pas zéro** : `results.jsonl` porte `cost_known` (faux si `claude -p` n'a pas rendu `total_cost_usd`) et le rapport
  `cost_known_runs` / `cost_known_ratio` par bras. Sous 1, la garde rend `unevaluated`, qui bloque comme un dépassement.
- **Le coût des dispatchs compte** : les dispatchs du kit (panel k=3 de verify-panel, passes de vérification, juge) tournent dans des
  sous-processus distincts du `claude -p` parent, leur coût n'est pas dans le `total_cost_usd` de la session. La garde ajoute
  `dispatch_stats.overall.total_cost_usd` au coût de chaque ligne. `dispatch_stats` absent (bras `nu`, ligne d'avant B-00) ou `overall.total == 0`
  vaut zéro dispatch connu ; `dispatch_stats` en `{"error": ...}`, un coût `lower_bound` / `unknown`, ou un `0.0` d'un ledger d'avant W1-01 avec
  des dispatchs comptés valent coût inconnu, donc `unevaluated`. La base de 118,4 % reste comparable : les 60 lignes `kit-gov` du lot L ont
  `overall.total == 0` (relu dans `results.jsonl`, voir `diagnostic-surcout-kit-2026-09-17.md`), elle ne contient donc aucun coût de dispatch.

Lancer le sous-lot (dépense réelle, environ 4 $ au tarif du lot L ; non lancé par le chantier B-00) puis évaluer :

```bash
python scripts/bench/three_arms.py --full --model <identifiant-de-modele> --arms nu,kit-gov --seed 551 --auth oauth-copy \
    --workspace <workspace neuf> --grimoire-bin <chemin absolu> --label barriere-N \
    --subset docs/bench/cost-gate-subset.json
python scripts/bench/cost_gate.py --results <workspace>/state/results.jsonl \
    --previous docs/bench/cost-gate-baseline.json --out-dir <dossier du rapport de barrière>
```

`--previous` reçoit le `cost-gate.json` de la barrière précédente (la base pour la première), et seulement un rapport au verdict `pass`,
`waived` ou `baseline` : un `exceeded`, un `unevaluated` ou un rapport sans verdict est refusé comme référence (verdict `unevaluated`), sinon
rejouer le même sous-lot contre le rapport d'un dépassement le ferait passer à l'écart zéro. Une dérogation entérine le ratio dérogé : la
barrière suivante s'y compare (avertissement dans le rapport). Sortie : `cost-gate.md` (ratio, écart,
verdict à inclure dans le rapport de barrière) et `cost-gate.json`. Code de sortie : 0 `pass` ou `waived`, 1 `exceeded`, 2 `unevaluated`.
Une dérogation passe par `--waiver <fichier>` : `{"by": "Guilhem", "reason": "...", "date": "AAAA-MM-JJ"}`, tracée dans le rapport avec le verdict
d'avant dérogation.

`--subset` restreint le tirage aux 4 tâches déclarées et s'arrête avant tout run si l'une manque. Limites : un changement de modèle entre deux
barrières est signalé en avertissement, pas refusé (voir B-01).

## 12. Modèle épinglé, appariement et saturation (B-01)

Trois défauts constatés sur le lot L : `claude -p` partait sans `--model` (le modèle effectif n'était que relu dans `model_usage`, et il avait
changé entre K et L, `docs/bench/rejeu-lot-l-2026-10-02.md` §1.3) ; rien n'empêchait de reprendre un lot sur un autre modèle ; le `nu` réussissait
60 runs sur 60, donc le banc ne pouvait montrer aucun gain de succès. Code : `scripts/bench/model_pin.py`, tests : `tests/unit/test_bench_model_pin.py`.

- **`--model <id>`** : obligatoire pour `--pilot` et `--full` (refus avant tout travail, code de sortie 2), transmis à `claude -p --model`. Préférer un identifiant
  complet : un alias peut pointer plus tard vers un autre modèle, et le banc ne le détecte pas.
- **Où il est noté** : `state/selection.json` (`model_id`, `claude_code_version`, lue par `claude --version`, `null` si illisible), chaque ligne de `results.jsonl`
  (`model_id`, `claude_code_version`) et `report.json`/`report.md` (`model_id`, `claude_code_versions`). Un `--dry-run` ne touche pas au modèle déjà noté.
- **Appariement refusé** : un rapport (`build_report`, donc `--report-only` et la fin d'une campagne) dont les lignes viennent de deux modèles est refusé
  (`ModelMismatchError`, aucun fichier écrit). Une campagne `--pilot`/`--full` refuse aussi, avant d'écraser `selection.json` et avant tout run, de s'ajouter à un
  `results.jsonl` d'un autre modèle. Une ligne sans `model_id` (lot antérieur à B-01) est INCONNUE : elle ne s'apparie avec aucun modèle noté, et un lot entièrement
  antérieur reste lisible avec la mention « non enregistré ». Pour changer de modèle : un workspace neuf.
- **`nu_baseline_pass_rate`** : part de runs `nu` réussis, par tâche, dans `report.json` (`by_task`) ; `null` sans run `nu` valide (jamais 0.0). Les runs invalidés (W1-08b) sont exclus.
- **SATURÉ** : strictement plus de 50 % des tâches à 1.0 pour le `nu` donne `suite_saturation.verdict = "SATURÉ"` et une mention en tête de `report.md`. Sans run `nu` : `NON ÉVALUÉ`.
  Sur les données figées du lot L (`tests/fixtures/bench/lot-l-nu-baseline.json`) : 20 tâches sur 20 à 1.0, SATURÉ.
- **`--fail-on-saturated`** (opt-in, `--report-only` et `--full`) : le rapport est écrit, puis la sortie est 1 si le verdict est `SATURÉ`
  (message sur stderr). `NON SATURÉ` et `NON ÉVALUÉ` sortent en 0 : une donnée absente ne fait pas rougir. Le drapeau n'est pas câblé en CI : sur la suite actuelle
  (lot L) il rougirait en permanence.

Limites. La version de Claude Code qui diffère dans un lot est signalée dans le rapport, pas refusée. Le modèle effectif des sous-agents n'est pas comparé : seul le modèle demandé l'est.
`cost_gate.py` ne compare pas les modèles de sa référence (`cost-gate-baseline.json`, lot L). Aucune suite non saturée n'existe encore : choisir ou construire des tâches où le `nu`
échoue ou varie reste à faire, et ne se fait pas sans run payant.

## 13. Recalcul, hash des données, MDE et n requis (B-02)

Trois manques constatés : aucun moyen de refaire un rapport depuis ses données brutes (le mot `recompute` n'existait nulle part) ; aucun calcul de l'effet que
la suite peut détecter, donc « pas de différence » pouvait se lire « équivalents » ; `evals/aggregate.py` sommait les coûts `null` comme s'ils étaient absents
(`cost_total` à 0.0 sans aucun coût connu) sans publier `cost_known_ratio`. Code : `scripts/bench/recompute.py`, `scripts/bench/power_calc.py`, tests :
`tests/unit/test_bench_recompute.py`, `tests/unit/test_evals_aggregate.py`.

```bash
python scripts/bench/recompute.py --results <workspace>/state/results.jsonl --report <rapport>/report.json [--mde 0.10] [--arms nu,kit-gov] [--require-seal] [--report-md <report.md>]
python scripts/bench/power_calc.py --mde 0.10 --tasks 20
```

- **Recalcul** : `report.json` est reconstruit par le même `build_report` à partir de `results.jsonl` seul. Chaque champ dérivé (`per_arm`, `by_task`, `kit_runs`,
  `kit_gov_runs`, `invalid_runs`, `unevaluated_runs`, `auth_modes`, `model_id`, `claude_code_versions`, `suite_saturation`, `tasks_run`, `k`) qui ne se
  recalcule pas à 1e-9 près est listé (chemin, valeur publiée, valeur recalculée) et la sortie est 1. Graine, étiquette, coût attendu, contrôle de toolchain et bras repris sont
  des entrées : non recalculés, et listés comme tels.
- **Hash des données** : `write_report` (campagne et `--report-only`) écrit dans `report.json` un bloc `data` (`results_file`, `results_rows`, `results_sha256`, sha256
  des octets de `results.jsonl`) et le cite dans `report.md`. `recompute` compare : fichier modifié depuis = `mismatch`, sortie 1. Un rapport antérieur à B-02 est « NON SCELLÉ » :
  ses chiffres se recalculent, mais rien ne prouve que le fichier est celui d'origine ; `--require-seal` en fait une erreur (à utiliser à la barrière 3).
- **Ce que le sceau couvre** : `write_report` ne cite un hash que si `results.jsonl` contient exactement les lignes que le rapport agrège (mêmes clés tâche/bras/rejeu). Un pilote
  suivi de `--full` sans `--resume`, ou `--full --arms kit` sur un fichier qui contient déjà `nu`/`ecc`, laisse des lignes que le rapport n'a pas comptées : le bloc `data` porte alors
  `sealed: false`, `report_rows` et la raison, sans hash, et `report.md` écrit « NON SCELLÉ ». `recompute` rend « NON SCELLÉ (rien n'a été recalculé) » (sortie 0, 1 avec `--require-seal`) : il ne
  peut pas refaire ce rapport depuis ce fichier, et le dit au lieu d'annoncer un hash « ok » suivi d'écarts.
- **Schéma antérieur** : un champ absent du rapport publié, ou publié à `null` quand le recalcul en donne une valeur (`cost_known_*`, `n_invalid`, `suite_saturation`,
  `nu_baseline_pass_rate`...), n'est pas un écart : il est listé sous « NON PUBLIÉS ». Une valeur publiée non nulle qui diffère reste REFUSÉE. Limite : un champ mis à `null` à la main n'est pas non plus un écart.
- **`report.md`** : régénéré par `render_report_markdown` depuis le rapport recalculé (entrées et `data` repris du rapport publié) puis comparé ligne à ligne ; tout écart donne REFUSÉ, les lignes
  fautives sont listées. Par défaut `report.md` à côté de `report.json` (s'il existe) ; `--report-md` en désigne un autre. Un rapport d'un schéma antérieur n'est pas comparé (le rendu actuel a des sections que
  son Markdown n'avait pas) : « NON VÉRIFIÉ », dit.
- **Clés dupliquées** : deux lignes pour la même (tâche, bras, rejeu) sont refusées. Le rapport les compterait deux fois et le recalcul aussi, donc aucun écart ne le trahirait.
- **`cost_known_ratio`** : repris par bras dans le verdict (déjà dans `report.json` depuis B-00). Dans `evals/aggregate.py`, `summarize` publie maintenant
  `cost_known_runs`, `cost_known_ratio` et `cost_is_lower_bound`, `cost_total` vaut `null` (et non 0.0) sans aucun coût connu, et le critère de coût du verdict A1 rend
  « NON CALCULABLE » quand l'un des deux bras n'a qu'un coût partiel (deux bornes basses ne se comparent pas).
- **MDE et n requis** : approximation normale, deux bras de `n` tâches, une tâche = une unité binaire (`pass^k`), alpha 0,05 bilatéral, puissance 0,80.
  `MDE(n) = (z_{1-α/2} + z_{puissance}) · sqrt((Var_a + Var_b) / n)`, `Var = p(1-p)`. Deux bases toujours côte à côte : **pire cas** (p = 0,5) et **variance observée**
  sur les deux bras comparés (`--arms`, défaut `nu,kit-gov`). Un taux observé de 0 ou 1 (suite saturée, lot L) donne « NON ESTIMABLE » et non un MDE nul.

  | Base pire cas | Valeur |
  |---|---|
  | n requis pour un MDE de 10 points | 393 tâches par bras (310 en unilatéral) |
  | n requis pour un MDE de 20 points | 99 |
  | MDE à n = 3 | 114 points : aucun effet détectable |
  | MDE à n = 20 (taille du banc) | 44 points |
  | MDE à n = 30 | 36 points |

Limites. L'approximation normale est grossière pour `n` petit (3, 30) ; les tâches sont traitées comme indépendantes et de même difficulté ; la variance observée sur peu de tâches est
elle-même incertaine ; le seuil de non-infériorité de 5 points (V1) n'a pas son calcul dédié : seul l'écart détectable bilatéral est chiffré. Le hash cité dans le rapport est aussi
réécrivable que le rapport : il rend la falsification visible à qui compare avec une copie versionnée ou un hash publié ailleurs, il ne la rend pas impossible. Le n requis n'est atteint
par aucun lot existant (20 tâches) : atteindre 393 tâches par bras n'est pas réaliste sur le polyglotte, et la barrière 3 doit donc re-fixer le MDE visé avant tout run.

## 14. Interventions humaines par tâche vérifiée (B-05)

Le critère V3 du protocole comparatif (charge humaine de `kit-gov` face à `aidlc`) ne se lit jamais dans un champ déclaré par un
bras : `scripts/bench/human_gates.py` recompte les interventions sur les traces de chaque run, avec la même règle (`human-gates/1`)
pour tous les bras. L'étiquette du bras ne sert qu'à regrouper.

Une unité est comptée pour chaque événement suivant, une seule fois même si deux traces le portent :

| Nature | Trace du kit | Trace d'AI-DLC |
|---|---|---|
| `gate` : un gate ou checkpoint présenté qui attend une réponse humaine, levé par un octroi ou par la configuration autonome compris | un *challenge* de `flows/authority/<run>.authority.jsonl` (ou une décision sans challenge) | un `STAGE_AWAITING_APPROVAL` de `audit/*.md` (sauf `Revalidated`) |
| `refusal` : un `ask` du hook, ou un refus qui demande une action humaine pour être levé | la trace typée `_grimoire-output/traces/traces.jsonl` (un appel `PreToolUse` au verdict `ask`) **et** un résultat d'outil en erreur dont le texte est une raison d'`ask` : « Confirme ... », panne du hook, du frein du bail ou de la garde d'autorité (« à toi de décider »), avertissement générique `[Grimoire policy] ... (profil X).` (règle maison, mutation destructive au profil strict) ; les deux voient un même appel, qui ne compte qu'une fois (outil, condensé des arguments, rang) | un résultat d'outil en erreur portant « attended session » ou « withholds human authority » |
| `question` : une question posée à l'humain | `task.question_asked` du Mission Ledger, hors `origin: flow` (c'est le checkpoint, déjà un `gate`) | un fichier `[Answer]:` écrit par `Write`/`Edit`, ou un `DECISION_RECORDED` (le même fichier ne compte qu'une fois) |
| `grant` : l'octroi W5-06 lui-même, 1 seul (son challenge n'est pas compté en plus) | une ligne `grant` de `@autonomy-grants.authority.jsonl` | sans objet |
| `autonomy` : l'activation du mode autonome | sans objet (c'est l'octroi) | `AUTONOMY_MODE_SET` avec `Mode: autonomous` |

Ne comptent pas : un refus (`deny`) que l'agent lève seul (le refus de la politique du kit qui renvoie à une commande réversible) ou qu'aucun humain ne lève (garde d'autorité, frein du bail), un message
d'information, un tour humain (c'est la réponse, pas la sollicitation), une demande d'octroi jamais attestée.

Un run dont le dépôt ou la transcription manque, ou dont la transcription n'a aucune ligne JSON, vaut `null` (inconnu), jamais 0 :
`human_gates_known_ratio` le dit par bras, et la médiane par tâche résolue est inconnue dès qu'un run résolu l'est. Comme pour le coût,
la métrique se lit par tâche résolue (`success` vrai, transcription non invalidée).

```bash
python scripts/bench/human_gates.py --workspace <workspace du banc> [--arm kit-gov --arm aidlc] [--json]
```

Limites : les marques de refus sont des sous-chaînes des textes actuels du kit et d'AI-DLC 2.11.0 lus dans leurs sources, pas observées
dans une transcription d'AI-DLC (B-06) ; une question posée par un widget (`AskUserQuestion`) sans ligne de journal n'est pas vue ; la
déclaration `AIDLC_UNATTENDED=1` faite au lancement n'est pas dans les traces du bras, le protocole d'équité de B-06 doit la compter à part.

## 15. Bras `aidlc` : AI-DLC 2.11.0, scope express, HOME isolé (B-06)

Le troisième bras du protocole comparatif (plan « Dépasser AI-DLC », section 6) : AI-DLC 2.11.0 sur les mêmes tâches que `nu` et `kit-gov`.
Code : `scripts/bench/aidlc_arm.py`. Tests : `tests/unit/test_bench_aidlc_arm.py`. Un pilote payant d'une tâche par pilote (k=1, 6,54 $) est décrit au §15.5.

### 15.1 Configuration épinglée

| Élément | Valeur | Source (clone `awslabs/aidlc-workflows`, tag `v2.11.0`) |
|---|---|---|
| Version | `2.11.0`, commit `6a378b53c0a4fe0641ed7d8de8dfff94264d5b6a`, vérifié par `git rev-parse` avant tout travail | `CHANGELOG.md:4` |
| Installation | installeur du tag, `install.sh --version 2.11.0 --yes --quiet`, sous `homes/aidlc` : `AIDLC_BIN_DIR`, `AIDLC_INSTALL_ROOT` et `XDG_DATA_HOME` pointent dans ce `HOME` | `docs/guide/18-install-and-lifecycle.md:191-198`, `:224` |
| Projet | `aidlc config --harness claude --mcp none --yes`, dans chaque dépôt de tâche ; un `.claude/settings.json` sans hooks arrête le run avant tout appel | `docs/guide/18-install-and-lifecycle.md:418-433` |
| Modèle | aucune politique de modèle : les agents héritent du modèle de session, celui de `--model` (B-01). `express` n'a pas de relecteur, seul agent à tier propre | `docs/guide/18-install-and-lifecycle.md:462` |
| Scope | `express` (10 stages sur 33) : relecture `none`, Guard Policy, capteurs, apprentissages, confirmation de résumé et approbation du plan `off`. `bugfix` (9 stages) reste disponible | `core/scopes/aidlc-express.md`, `core/scopes/aidlc-bugfix.md`, `docs/guide/workflow-profiles.md:25`, `:30` |
| Premier message | `/aidlc express ` suivi de la consigne des autres bras (`BUILD_PROMPT_TEMPLATE`) | `harness/claude/skills/aidlc/SKILL.md` (boucle de transfert) |
| Jamais `--bare` | le mode `--bare` ignore hooks et skills du projet, donc AI-DLC entier ; le banc le refuse pour tout bras gouverné en `--auth api-key` (`bare_guard.py`, §3.1) | §3.1 |
| Timeout, boucle | 3600 s par run (toutes reprises comprises), seuil de boucle à 8 commandes identiques (le conducteur relance `orchestrate next` par construction) | constantes `DEFAULT_RUN_TIMEOUT_S`, `LOOP_REPEAT_THRESHOLD` |

`express` plutôt que `bugfix` : un exercice polyglotte part d'un fichier de départ à compléter, pas d'un défaut connu. `bugfix` déclare `existing_code: true`
et garde l'approbation du plan et les capteurs ; `express` est le scope qu'AI-DLC recommande pour « a small change or a quick end-to-end try »
(`docs/guide/facilitator-guide.md:130`).

Effet assumé : `express` est aussi le scope le moins coûteux d'AI-DLC, donc le plus favorable à AI-DLC sur le critère V2 (coût). Le choix est pré-enregistré.

### 15.2 Équité : deux pilotes, un seul pré-enregistré

AI-DLC ne documente pas de mode sans humain qui lève ses gates. `AIDLC_UNATTENDED=1` est le mode prévu pour un pilote sans personne présente,
et il **retient** l'autorité humaine : aucun `HUMAN_TURN` n'est émis, donc aucun gate d'approbation ne se lève (`CHANGELOG.md:129`, `:281-284`,
`core/knowledge/aidlc-shared/audit-format.md:86`). Le mode autonome (`AUTONOMY_MODE_SET`) ne couvre que les checkpoints de Construction après le
squelette, absent d'`express` (`docs/guide/glossary.md:11`). Les tests de bout en bout d'AI-DLC jouent la personne par un script séparé de l'agent
(`tests/harness/scope-run.ts:1-14`, `PersonScript`). D'où deux pilotes, au choix de `--driver` :

| Pilote | Ce qu'il fait | Interventions comptées |
|---|---|---|
| `unattended` (mode documenté) | un seul `claude -p` avec `AIDLC_UNATTENDED=1` ; personne ne répond | les gates du journal d'audit (B-05), plus 1 déclarée par run (`declared_interventions`, l'activation du mode sans humain, que les traces ne portent pas : limite de B-05) |
| `scripted-person` (configuration spéciale, déclarée) | `AIDLC_UNATTENDED` retiré ; à chaque fin de tour sans `WORKFLOW_COMPLETED` dans `audit/*.md`, la même session est reprise (`claude -p --resume <session_id>`) avec la réponse fixe `Approved.`, au plus 12 fois | chaque `STAGE_AWAITING_APPROVAL` et chaque question du journal d'audit, par `human_gates.py` (B-05) ; `person_replies` noté à part |

Attendu, non vérifié : en `unattended`, le run s'arrête au premier gate (Requirements Analysis) sans code, donc échoue. C'est un résultat honnête du mode
documenté, mais il ne dit rien de la qualité d'AI-DLC. Le pilote `scripted-person` est ce qui rend la comparaison informative ; il donne à AI-DLC une
personne qui approuve tout, ce qu'aucun bras du kit ne reçoit. Le rapport doit le dire.

### 15.3 Garde-fous avant toute dépense

- **Pré-enregistrement** : `--dry-run` écrit `state/aidlc-preregistration.json` (configuration complète et son empreinte sha256) ;
  `--run` refuse de partir (code 2) sans ce fichier ou si la configuration diffère (pilote, scope, modèle, tâches, k, timeout).
- **Plafond explicite** : `--run` sans `--max-total-usd` (ou avec un montant nul, négatif, non fini, non numérique) est refusé par l'analyseur
  d'arguments avant tout travail, réseau ou disque.
- **Cumul fermé par défaut** : `state/aidlc-budget.jsonl` (ou le journal partagé de `--budget-ledger`, un seul plafond pour plusieurs workspaces) reçoit une ligne `begin` avant chaque appel `claude -p` (reprises comprises) et une ligne
  `end` après. Avant chaque appel, le cumul connu est comparé au plafond : atteint, l'appel n'est pas lancé et la campagne s'arrête (code 3).
  Un `end` sans coût, un `begin` sans `end` (processus tué), une ligne illisible valent *inconnu* : jamais 0, et tout appel suivant est refusé.
  Une campagne relancée relit ce journal : elle repart du cumul, pas de zéro.
- **Plafond pendant l'appel** : chaque appel reçoit le reste du plafond en `--max-budget-usd` ; Claude Code arrête lui-même l'appel qui l'atteint.
  Ce que ce drapeau compte sous `--resume` (l'appel ou la session) n'est pas mesuré : dans le second cas il s'arrête plus tôt, jamais plus tard.
- **Coût d'une reprise** : sous `--resume`, `total_cost_usd` et `modelUsage` cumulent la session (mesuré, §15.5). Le coût d'un appel est la
  différence avec le total précédent de la même session ; un total en baisse compte en entier (surcompte, jamais sous-compte). `modelUsage` d'un run
  est la dernière valeur de chaque session, pas une somme.
- **Sorties** : `results.jsonl` (une ligne `RunRecord`, `arm: "aidlc"`, coût = somme des appels connus, `cost_known` faux dès qu'un appel est inconnu),
  `aidlc-runs.jsonl` (pilote, réponses jouées, interventions déclarées, détail par appel, état du budget). Utiliser un workspace propre au bras :
  `build_report` de `three_arms.py` n'agrège pas encore `aidlc`, et des lignes mêlées casseraient le sceau de B-02.

```bash
# 0 $ : pré-enregistrement, dépôts, installation d'AI-DLC sous HOME isolé, commandes prévues, couverture du plafond
python scripts/bench/aidlc_arm.py --dry-run --driver scripted-person --aidlc-clone <clone aidlc-workflows> \
    --workspace <workspace neuf> --model <identifiant> --max-total-usd <plafond>
# Estimation seule, sans réseau
python scripts/bench/aidlc_arm.py --estimate --n 20,99,393
# Payant, après décision du plafond par Guilhem (décision 5 du plan)
python scripts/bench/aidlc_arm.py --run --driver scripted-person --aidlc-clone <clone> --workspace <même workspace> \
    --model <identifiant> --max-total-usd <plafond> --auth oauth-copy [--budget-ledger <journal partagé>]
```

### 15.4 Estimation chiffrée (aucune mesure AI-DLC)

**Mesuré** (lot L, Opus 5.5, 20 tâches, k=3) : `nu` 10,15 $ pour 60 runs, `kit-gov` 12,69 $ (`docs/bench/rejeu-lot-l-2026-10-02.md:17`), soit 0,169 $
et 0,212 $ par run en moyenne, 0,159 $ et 0,198 $ en médiane par tâche résolue (`:55-56`), 3 tours médians (`:55-56`). Tarif implicite de `claude -p`,
ajusté sur le `modelUsage` des 120 runs (données brutes hors dépôt, résidu < 1e-14) : 4 $ par million de jetons en entrée, 20 $ en sortie,
0,20 $ en lecture de cache, 8 $ en écriture de cache. Un run `nu` médian : 86 k jetons lus en cache, 12,8 k écrits, 1,8 k en sortie.

**Structure d'AI-DLC express** (tag `v2.11.0`) : 10 stages, dont 3 d'initialisation sans gate et 4 CONDITIONAL (Reverse Engineering, queue Operation) ;
Code Generation en mode `subagent` (`core/aidlc-common/stages/construction/code-generation.md`, front-matter). Volume d'instructions : la skill du
conducteur fait 87 669 octets (`harness/claude/skills/aidlc/SKILL.md`), le protocole de stage 103 313 (`core/aidlc-common/protocols/stage-protocol.md`),
le protocole de Construction 90 325, les 10 fichiers de stage 117 842 au total (Reverse Engineering 27 590, Code Generation 32 139). Quelle part
entre réellement dans le contexte n'est pas mesuré : depuis 2.11.0, une étape « nomme les règles que la conversation porte déjà au lieu de les renvoyer »
(`CHANGELOG.md:4`, puce « Long runs carry less »).

Modèle de coût (`RunScenario.cost_usd`) : lectures de cache = appels × contexte moyen, plus écritures de cache, plus sortie, au tarif ci-dessus.

| Hypothèse | Appels (principal + sous-agent) | Contexte de départ → final | Écrit en cache | Sortie | Coût d'un run |
|---|---|---|---|---|---:|
| `scripted-person`, bas | 35 + 10 | 55 k → 100 k | 130 k | 23 k | **2,13 $** |
| `scripted-person`, haut | 90 + 30 | 60 k → 200 k | 380 k | 95 k | **7,70 $** |
| `unattended`, bas (arrêt au premier gate) | 15 | 55 k → 85 k | 60 k | 6 k | 0,81 $ |
| `unattended`, haut | 25 | 55 k → 110 k | 90 k | 10 k | 1,33 $ |

Un run `aidlc` coûterait donc de 13 à 48 fois un run `nu`. Protocole complet, trois bras (`nu`, `kit-gov`, `aidlc`), k=3, pilote `scripted-person`,
`nu` et `kit-gov` au coût moyen mesuré :

| Tâches par bras | Ce que la taille permet (§13, pire cas) | Runs `aidlc` | `aidlc` | `nu` + `kit-gov` | Total |
|---:|---|---:|---:|---:|---:|
| 20 (banc actuel) | MDE de 44 points | 60 | 128 – 462 $ | 22,84 $ | **151 – 485 $** |
| 99 | MDE de 20 points | 297 | 633 – 2 287 $ | 113 $ | **746 – 2 400 $** |
| 393 (n requis par B-02) | MDE de 10 points | 1 179 | 2 514 – 9 078 $ | 449 $ | **2 963 – 9 527 $** |

Hypothèses : modèle Opus 5.5 au tarif du lot L ; pas de compaction (fenêtre de 1 M) ; cache d'une heure tenu entre deux reprises ; tâches de la difficulté
du lot L. Le protocole demande des tâches plus dures (où le `nu` échoue ou varie, multi-fichiers) : les trois bras coûteraient plus, d'un facteur non mesuré.
393 tâches par bras n'existent pas dans le polyglotte (§13).

**Non mesurable sans dépense** (état avant le pilote, levé en partie au §15.5) : le nombre réel d'appels et de reprises d'un run AI-DLC ; la part des 400 ko d'instructions qui entre dans le contexte ;
si un gate se lève sous `claude -p` (hooks de projet chargés, `HUMAN_TURN` émis par une reprise) ; si `--resume` rend un `total_cost_usd` propre à l'appel
ou cumulé sur la session (le bras somme les appels, ce qui surcompterait dans le second cas) ; le temps mur ; le coût de `kit-gov` sous octroi W5-06.

**Ce qui lève ces inconnues au moindre coût** : un `--dry-run` complet (0 $, installe et configure réellement AI-DLC), puis un pilote d'une tâche par
pilote, k=1, sous un plafond qui couvre deux runs à l'hypothèse haute (environ 16 $). Le plafond reste la décision de Guilhem (décision 5).

### 15.5 Pilote B-06 (2026-10-19) : une tâche par pilote, k=1, mesures réelles

Autorisé par Guilhem : plafond dur de 16 $ pour toute la passe (contrôle compris), un seul journal de budget partagé (`--budget-ledger`).
**n = 1 par pilote : aucune conclusion de supériorité, ni de fiabilité, ne se tire de ces chiffres.** Ils servent au chiffrage et aux garde-fous.

**Conditions.**

- AI-DLC 2.11.0 installé par l'installeur du tag sous le `HOME` isolé de chaque workspace : `aidlc version` rend `aidlc 2.11.0 (runtime 2.11.0)`.
- Épinglage vérifié :
  - `v2.11.0^{commit}` vaut `6a378b53c0a4fe0641ed7d8de8dfff94264d5b6a` ;
  - le `version.json` de la release porte `sourceRef: refs/tags/v2.11.0` et `sourceDigest: 6a378b53…` ;
  - sa somme sha256 est celle de `checksums.txt` ;
  - l'`install.sh` publié ne diffère de celui du tag que par `PACKAGED_VERSION='2.11.0'` ;
  - l'installeur vérifie lui-même les sha256 du binaire `aidlc-linux-x64` et du runtime.
- La vérification de provenance par `gh attestation` est désactivée (`AIDLC_GH_BIN` invalide) pour ne présenter aucun jeton GitHub.
- Rien n'a été écrit dans le `HOME` réel.
- Tâche `python/zipper` : la première du tirage (graine 551). Au lot L, le `nu` a résolu les 20 tâches 3 fois sur 3 : aucune tâche où il échoue ou varie.
- Modèle `claude-opus-5-5`, Claude Code 2.1.302, `--auth oauth-copy` (aucune `ANTHROPIC_API_KEY` sur le poste), jamais `--bare`.
- Environnement du lanceur vidé (`env -i`) : aucune variable de la session Claude Code parente, `TMPDIR` sous le workspace.
- Les hooks d'AI-DLC tournent sous `claude -p` : `SessionStart` répond dans le flux, et le journal d'audit est écrit par les outils d'AI-DLC.

**Doute 1, `--bare`.** `claude --help` (2.1.302) : `--bare` « skip hooks […] and CLAUDE.md auto-discovery ». `run_claude_headless` l'ajoute en
`--auth api-key` (§3.1). Un bras `kit-gov` lancé en `api-key` ne serait donc pas gouverné. Aucun résultat publié n'est concerné :

- les lots E, F, H et J ont tourné avant le mode `api-key` (PR #610), et le lot J a été relancé en `oauth-copy` (`rejeu-lot-j-2026-09-18.md:100`) ;
- les lots K et L ont tourné en `oauth-copy` (`rejeu-lot-k-2026-09-29.md:43`, `rejeu-lot-l-2026-10-02.md:14`) ;
- aucune ligne `auth_mode: "api-key"` dans les `results.jsonl` des lots, et `evals/runner.py`, `evals/grounding-probe.py` lancent `claude -p` sans jamais `--bare`.

Ce piège est désormais refusé par le banc pour tout bras gouverné (§3.1, `bare_guard.py`). Le bras `aidlc` force aussi `bare=False`.

**Doute 2, coût d'une reprise.** Un run de contrôle (deux appels d'une même session, le second en `--resume`) a été compté dans le plafond. Résultat :

- `usage` est propre à l'appel ;
- `total_cost_usd` et `modelUsage` cumulent la session : 0,0921 $, puis 0,1618 $ = 0,0921 + 0,0697, et 0,0697 $ est le coût des jetons du seul second appel ;
- les jetons de `modelUsage` du second appel sont la somme des deux appels (10 718 + 14 345 = 25 063 lus en cache).

Le garde-fou sommait les totaux. Il compte désormais la différence avec le total précédent de la session (tests rouges puis verts). Avant correction, le
run `scripted-person` ci-dessous aurait été compté 19,37 $ (somme des totaux cumulés) au lieu de 4,90 $ : le garde l'aurait arrêté au plafond sans motif. Le journal partagé garde
le contrôle surcompté (0,2540 $ inscrits pour 0,1618 $ dépensés), écrit avant la correction.

**Mesures.**

| | `scripted-person` | `unattended` |
|---|---:|---:|
| Coût (`total_cost_usd`, final de session) | **4,8963 $** | **1,4835 $** |
| Appels `claude -p` (dont reprises) | 6 (5) | 1 (0) |
| Tours (`num_turns`, boucle principale) | 85 | 11 |
| Jetons lus / écrits en cache, sortie (`modelUsage`, sous-agents compris) | 8 830 938 / 326 827 / 44 602 | 1 069 923 / 130 991 / 18 847 |
| Temps mur des appels | 618 s | 168 s |
| Stages (sur 10) | 7 terminés, 3 sautés (queue Operation) | 3 terminés, arrêt en Reverse Engineering (`ERROR_LOGGED`) |
| Gates rencontrés / levés | 4 `STAGE_AWAITING_APPROVAL` / 4 `GATE_APPROVED`, 1 question, 5 `HUMAN_TURN` | 0 / 0 |
| Interventions (`human_gates.py`, B-05) | 6 (4 gates, 2 questions) | non compté (run invalide), 1 déclarée |
| `WORKFLOW_COMPLETED` | oui | non |
| Tests cachés | **réussis** | réussis |
| Contrôle de transcription (W1-08b) | `clean` | **`invalid`** à l'époque, `clean` après correction du détecteur (voir ci-dessous) |
| Cumul du journal partagé après le run | 5,1503 $ | 6,6337 $ |

Dépense exacte de la passe : 0,1618 $ (contrôle) + 4,8963 $ + 1,4835 $ = **6,5416 $** sur 16 $. Le journal en compte 6,6337 $ (surcompte du contrôle).

Lecture, sans conclusion de supériorité :

- En `scripted-person`, le code est écrit dans le stage Code Generation, par le sous-agent prévu (appel 4).
- En `unattended`, l'agent principal écrit le fichier de solution de la tâche `zipper` **avant** d'entrer dans le workflow. Le workflow s'arrête ensuite en Reverse Engineering :
  `aidlc-log engine log link` refuse un lien sans `--artifact`. Les tests cachés passent donc sans qu'AI-DLC ait produit le code. Ce run ne mesure pas
  AI-DLC.
- Le `invalid` d'`unattended` vient du détecteur W1-08b, qui lit le gras Markdown `**zipper**` d'un heredoc comme un glob. Les tests cachés n'étaient
  pas dans le dépôt : c'était un faux positif. Corrigé ensuite (B-06) : le corps d'un heredoc que `cat`/`tee` écrit vers un fichier de prose
  (`.md`, `.markdown`, `.txt`, `.rst`, `.adoc`), sans tube en fin de ligne, n'est plus lu comme une référence de chemin ; tout le reste l'est encore (corps
  envoyé à un shell ou à `python`, écrit vers un script, cible inconnue, redirection d'entrée). Rejouée sur la transcription réelle
  (`ws-unattended/.../run0.stream.jsonl`), la vérification rend `clean`. Aucun résultat publié n'est reclassé (aucune ligne publiée ne porte ce
  contrôle) ; le tableau ci-dessus garde les valeurs de l'époque, et le compte d'interventions du run `unattended` (« non compté ») n'a pas été recalculé.
- Le même `.claude/settings.json` porte 45 entrées `permissions.allow` ignorées (« workspace has not been trusted ») : sans effet sous
  `bypassPermissions`.

**Comparaison à l'estimation (§15.4).**

| | Estimation basse | Mesuré | Estimation haute |
|---|---:|---:|---:|
| `scripted-person`, coût | 2,13 $ | **4,90 $** | 7,70 $ |
| `scripted-person`, lus en cache | 3,16 M | 8,83 M | 13,8 M |
| `scripted-person`, écrits en cache | 130 k | 327 k | 380 k |
| `scripted-person`, sortie | 23 k | 44,6 k | 95 k |
| `unattended`, coût | 0,81 $ | **1,48 $** | 1,33 $ (dépassée de 11 %) |

Sur la même tâche au lot L, le `nu` a coûté 0,141 à 0,165 $ (moyenne 0,155 $), et `kit-gov` 0,195 à 0,333 $. Le run `scripted-person` coûte
31,6 fois la moyenne du `nu`, et `zipper` est l'une des tâches les moins chères du lot.

**Campagne recalculée** (k=3, pilote `scripted-person` au coût mesuré, `nu` et `kit-gov` au coût moyen du lot L) :

| Tâches par bras | Runs `aidlc` | `aidlc` à 4,90 $ | `nu` + `kit-gov` | Total | Ancienne fourchette |
|---:|---:|---:|---:|---:|---:|
| 20 | 60 | 293,78 $ | 22,84 $ | **316,62 $** | 151 – 485 $ |
| 99 | 297 | 1 454,21 $ | 113,06 $ | **1 567,27 $** | 746 – 2 400 $ |
| 393 | 1 179 | 5 772,77 $ | 448,81 $ | **6 221,57 $** | 2 963 – 9 527 $ |

En `unattended` à 1,48 $ : 111,85 $, 553,64 $ et 2 197,80 $. Ce pilote ne mesure pas AI-DLC (voir plus haut).

**Plafond proposé** (décision 5, à Guilhem) : une campagne `scripted-person` en deux paliers, chacun sous `--max-total-usd` et un journal partagé.

1. **Palier 1 : 20 tâches, k=1, `aidlc` seul, 160 $.** C'est 20 × 5,35 $ × 1,5. Le 5,35 $ vient du rapport mesuré de 31,6 fois le `nu`, appliqué
   au coût moyen du `nu` sur le lot L. La marge de 1,5 couvre la variance inconnue, sur n = 1 et sur les tâches Rust et Go, plus chères au lot L.
2. **Palier 2 : k=3 complet à 20 tâches, 500 $ au total, palier 1 compris.** Le palier 2 se recalcule sur la médiane mesurée au palier 1 avant
   d'être lancé.

99 tâches demanderaient environ 2 400 $. 393 tâches n'existent pas dans le polyglotte (§13).

**Limites.**

- n = 1 par pilote.
- La tâche est la plus simple des deux critères du protocole : le `nu` n'y échoue jamais, et elle tient en un seul fichier.
- Tarif « list » de Claude Code sous abonnement OAuth : un coût rapporté, pas une facture.
- Ce que `--max-budget-usd` compte sous `--resume` n'est pas mesuré : il n'a jamais été atteint.
- Les champs de jetons de `RunRecord` somment `usage`, qui couvre la boucle principale sans les sous-agents. `modelUsage` couvre les sous-agents.
- `build_report` n'agrège toujours pas `aidlc`.

Données brutes hors dépôt : `_scratch/bench-aidlc-b06-pilot/` de la Forge (`budget.jsonl`, `ws-*/state/`, journaux `*.stream.jsonl`).
