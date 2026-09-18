<p align="right"><a href="../README.md">README</a> · <a href="../CHANGELOG.md">Changelog</a> · <a href="index.md">Docs</a></p>

# <img src="assets/icons/rocket.svg" width="32" height="32" alt=""> Banc à trois bras

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

Aucun bras ne force `--model` : les quatre utilisent le modèle par défaut de
Claude Code pour l'environnement d'exécution. C'est volontaire — les bras
`kit`/`kit-gov` ont le droit de cascader vers un modèle moins cher via leurs
propres hooks et leur propre routage ; c'est précisément ce que la mesure de
coût doit révéler, pas quelque chose à neutraliser en figeant un modèle
unique.

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
| `api-key` (préféré) | `--bare` + `ANTHROPIC_API_KEY` (héritée de l'environnement du lanceur par `run_environment()`) | Aucune | Aucun |
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
python scripts/bench/three_arms.py --pilot --workspace /chemin/scratch/bench-workspace

# 3. Campagne complète : jusqu'à 20 tâches x 3 bras x k=3 (180 runs), avec
#    arrêt anticipé si le critère statistique est atteint plus tôt.
python scripts/bench/three_arms.py --full --workspace /chemin/scratch/bench-workspace \
    --resume  # relance sans rejouer ce qui est déjà dans results.jsonl

# 4. Régénérer report.md/report.json depuis results.jsonl sans rien rejouer :
python scripts/bench/three_arms.py --report-only --workspace /chemin/scratch/bench-workspace

# Authentification (lot J, #582, voir §3) : --auth api-key explicite (recommandé,
# aucune copie d'identifiant) ou --auth oauth-copy explicite (repli historique) ;
# sans l'option, le mode est déduit de la présence d'ANTHROPIC_API_KEY.
ANTHROPIC_API_KEY=sk-ant-... python scripts/bench/three_arms.py --pilot \
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
