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

## 2. Les trois bras

| Bras | Configuration du dépôt de tâche | Isolation |
|---|---|---|
| `nu` | Rien. `git init` seul. | `HOME` isolé, jetable, sans identifiants ECC/kit. |
| `ecc` | Plugin Claude Code `ecc@ecc` installé en **scope projet** (`.claude/settings.local.json`), depuis un clone local du dépôt ecc épinglé au commit utilisé. | `HOME` isolé partagé entre tous les runs `ecc` (cache du plugin, voir §4). |
| `kit` | `grimoire init . --backend local --no-cockpit` puis `grimoire host sync --host claude` (grimoire-kit ≥ 3.53.0, installé depuis PyPI). | `HOME` isolé partagé (évite d'écrire dans `~/.grimoire`) ; l'état projet (`_grimoire/`) vit dans le dépôt de tâche lui-même. |

Aucun bras ne force `--model` : les trois utilisent le modèle par défaut de
Claude Code pour l'environnement d'exécution. C'est volontaire — le bras
`kit` a le droit de cascader vers un modèle moins cher via ses propres hooks
et son propre routage ; c'est précisément ce que la mesure de coût doit
révéler, pas quelque chose à neutraliser en figeant un modèle unique.

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
`grimoire dispatch stats --json` est interrogé après chaque run `kit` et son
résultat est conservé dans `RunRecord.dispatch_stats`, y compris quand il est
vide (une tâche d'exercice résolue en une session headless ne passe pas
nécessairement par `grimoire task dispatch` — c'est un résultat honnête à
publier tel quel, pas une raison d'inventer un événement).

## 3. Authentification en environnement isolé

Isoler entièrement `HOME` casse l'authentification de Claude Code (« Not
logged in · Please run /login » — aucune clé API n'est configurée en variable
d'environnement sur ce poste, l'auth vit dans `~/.claude/.credentials.json`,
liée à OAuth). Le harnais copie ce seul fichier (`shutil.copyfile`, jamais lu
ni affiché) dans chaque `HOME` isolé avant le premier appel, avec
`chmod 600`. C'est la même clé physique que celle déjà configurée sur le
poste ; elle n'est ni régénérée ni journalisée. Voir
`provision_isolated_home()`.

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

## 5. Garde-fous par run

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

## 6. Critère d'arrêt statistique

Après chaque tâche complétée (les trois bras, k=3), `should_stop_early()`
recalcule un intervalle de confiance bootstrap (2000 ré-échantillonnages) sur
le taux de succès agrégé de chaque bras. La campagne s'arrête dès que les IC
de `kit` sont disjoints de **ceux de `nu` et de `ecc`**, avec un minimum de 4
tâches jouées pour éviter un verdict sur trop peu de données ; sinon elle va
au bout des 20 tâches.

## 7. Mesures

Par run : succès (tests cachés verts), coût réel (`total_cost_usd` renvoyé
par `claude -p --output-format json`/`stream-json`, pas une reconstruction
manuelle tokens × tarif — c'est la même source que la facturation réelle),
tokens d'entrée/sortie, nombre de tours (`num_turns`), temps mur, et pour le
bras `kit` : `grimoire dispatch stats --json` du dépôt de tâche.

Par tâche x bras : pass^k (k=3) au sens du kit
(`pass_k_fully_green / pass_k_observations`, `src/grimoire/traces/ledger.py`)
— une série de k rejeux compte 1 si les trois sont verts, 0 sinon
(`pass_hat_k()`).

Rapport agrégé (`report.md` + `report.json`) : par bras — nombre de runs, de
tâches, taux de succès avec IC95% bootstrap, taux de pass^k avec IC95%, temps
mur médian, coût médian par tâche résolue, coût total ; par tâche — succès
par bras et pass^k.

## 8. Reproduire le banc

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
```

Prérequis système pour `--pilot`/`--full` : `claude` (Claude Code CLI,
authentifié — voir §3), `node`/`npm`, `cargo`/`rustc`, `git`. `go` est
provisionné automatiquement sur Linux/amd64 s'il est absent du `PATH`.

`--dry-run` et les tests unitaires (`tests/unit/test_bench_three_arms.py`) ne
nécessitent ni réseau ni authentification : ils exercent le tirage, la
préparation de dépôt, la détection de succès (Python, via `pytest`
directement), le calcul de pass^k/IC et la détection de boucle sur des
fixtures synthétiques.

## 9. Sorties et emplacement du rapport

`workspace/state/selection.json` (tâches tirées, commit ecc), `results.jsonl`
(une ligne par run, append-only — permet `--resume` après interruption),
`expected_cost.json` (estimation post-pilote), `reports/<date>/report.md` et
`.json`.

Le rapport de la première campagne réelle est publié sur la branche
`bench-reports` (non protégée, poussée directement — comme
`weekly-bench.yml` — sous
`_grimoire-output/bench-reports/three-arms/<date>/report.md` et `.json`), pas
sur `main`.
