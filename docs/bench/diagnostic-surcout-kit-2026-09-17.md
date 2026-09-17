<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/microscope.svg" width="32" height="32" alt=""> Diagnostic du surcoût kit — banc à trois bras 2026-09-17

> Rapport du banc : branche `bench-reports`, commit `c43483ac`,
> `_grimoire-output/bench-reports/three-arms/2026-09-17/{report.md,report.json}`.
> Données brutes locales (Grimoire-Forge, hors dépôt kit) :
> `_scratch/bench/workspace/state/{results.jsonl,selection.json,expected_cost.json}`.
> Harnais : `scripts/bench/three_arms.py` (`main`, ce dépôt).
> Verdict du banc (issue [#551](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/551),
> `docs/plan-2026-q4.md` §3) : succès égal ou inférieur, coût ×3,1, temps ×3,0
> — **critère d'arrêt déclenché**. Ce document attribue le surcoût à des
> mécanismes précis (code lu, pas supposé) et propose les lots de cœur qui le
> corrigent, chacun avec sa mesure de preuve sur ce même banc.

## 0. Ce qui a pu être mesuré, ce qui ne peut pas l'être

Les `.stream.jsonl` par run (transcript complet `--output-format stream-json`,
écrits par `run_claude_headless` à côté de chaque dépôt de tâche) n'ont pas
survécu au nettoyage disque de fin de campagne — seuls `results.jsonl`
(180 lignes, un par tâche×bras×rejeu) et les rapports agrégés restent. Deux
conséquences pour ce diagnostic :

- L'attribution en §1 s'appuie sur les agrégats de `results.jsonl`
  (`num_turns`, `wall_seconds`, `total_cost_usd`, `dispatch_stats`) et sur une
  **reproduction mécanique** du chemin `grimoire init --backend local
  --no-cockpit` + `grimoire host sync --host claude` + hooks, rejouée dans ce
  worktree (grimoire-kit `main` @ `f1289dd0`, 3.54.0 + `Unreleased`) — pas sur
  une relecture des transcripts de la campagne elle-même. Chaque mesure
  reproduite est marquée **[reproduit]** ; chaque mesure lue directement dans
  `results.jsonl` est marquée **[mesuré]**.
- Deux champs que le protocole demandait de regarder n'existent nulle part
  dans les données : `usage.cache_read_input_tokens` / `cache_creation_input_tokens`
  (le harnais ne les extrait jamais, voir §4) et `modelUsage` par tour
  (`RunOutcome.model_usage` est rempli mais **jamais écrit** dans
  `RunRecord`/`results.jsonl` — `three_arms.py:1128-1137` ne recopie que
  `input_tokens`/`output_tokens`/`num_turns`). Le cache et l'usage par modèle
  ne sont donc pas mesurables sur cette campagne ; traités comme lacune en §4,
  pas comme cause confirmée en §1.

## 1. Attribution du surcoût

### 1.1 Les ratios mesurés sont quasi identiques — signal le plus important

**[mesuré]**, sur les 60 runs par bras (`results.jsonl`, médianes) :

| Bras | Tours (médiane) | Temps (médiane, s) | Coût (médiane, $) | Coût/tour moyen |
|---|---|---|---|---|
| nu | 6,0 | 64,0 | 0,403 | 0,0741 |
| ecc | 7,0 | 61,0 | 0,629 | 0,0970 |
| kit | 19,0 | 189,0 | 1,247 | 0,0675 |

Ratio kit/nu apparié tâche par tâche (médiane sur les 20 tâches) :
**tours ×3,17**, **temps ×3,36**, **coût ×3,19**. Les trois ratios se
superposent quasiment. Or le coût moyen *par tour* du bras kit (0,0675) n'est
pas plus élevé que celui du bras nu (0,0741) — il est même légèrement
inférieur. Conclusion directe : **le surcoût n'est presque pas un effet de
contexte plus lourd par tour** (sinon coût/tour kit ≫ coût/tour nu) — **c'est
un effet de nombre de tours**. Le bras kit ne fait pas des tours plus chers,
il fait ~3,2× plus de tours pour la même tâche.

Cause écartée par construction du harnais : le temps de setup (`grimoire
init` + `host sync`, `setup_arm_kit`) n'entre pas dans `wall_seconds` —
`RunOutcome.wall_seconds` ne chronomètre que le `Popen` de `claude -p`
lui-même (`three_arms.py:653-689`). Le surcoût de temps est donc entièrement
à l'intérieur de la session Claude Code, pas dans l'échafaudage préalable.

### 1.2 Où partent les ~13 tours supplémentaires — mécanisme reproduit

**[reproduit]** — `grimoire init . --backend local --no-cockpit` puis
`grimoire host sync --host claude` sur un dépôt vierge (identique au
protocole `setup_arm_kit`) écrivent 100 fichiers / 731 Ko sous `.claude/`,
`.github/`, `_grimoire/`, dont un `.claude/settings.json` avec :

```json
"SessionStart": [{"hooks": [{"command": "grimoire-hook --host claude --event SessionStart"}]}],
"PreToolUse":   [{"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit",
                   "hooks": [{"command": "grimoire-hook --host claude --event PreToolUse"}]}]
```

`grimoire-hook --host claude --event SessionStart` (`decide_activation`,
`src/grimoire/hosts/decisions/activation.py`) construit le contexte injecté
en tête de session en concaténant, **sans aucune condition liée à la tâche
ni au projet** :

1. **La persona d'entrée** (`entry_persona_context`) : « Lis
   `_grimoire/kit/agents/concierge.md` **en entier** avant de répondre ».
   Mesuré [reproduit] : ce fichier fait 11 319 octets (~2 800 tokens) pour un
   agent dont le rôle documenté est « triage d'une demande humaine ambiguë »
   — sans objet dans une session `claude -p` batch, sans utilisateur à
   trier, la tâche étant déjà entièrement donnée par `TASK.md`.
2. **La directive du standard** (`activation_context_text`,
   `src/grimoire/core/claude_activation.py`, `_DIRECTIVE_TEMPLATE`) —
   **inconditionnelle**, émise même quand `_grimoire/standard/` n'existe pas
   du tout (vérifié [reproduit] : le dépôt de repro n'a jamais eu
   `_grimoire/standard/`, `grimoire init` ne le crée pas) :
   > « Ce projet est gouverné par le standard agentique Grimoire. […]
   > 1. AVANT toute modification de code : remplis
   > `_grimoire-output/evidence/bootstrap/task-envelope.md`. 2. PENDANT le
   > travail : consigne chaque preuve dans …/evidence-pack.md. 3. AVANT de
   > conclure : exécute `grimoire standard gate check --task-id bootstrap
   > --strict` puis `grimoire standard verify .` et corrige tout échec. Une
   > clôture sans gates verts est une tâche non terminée. »

   Le docstring du module l'assume : cette directive est validée par une
   campagne d'évals du 2026-07-09 (« 40/40 engagée avec le hook, 0/40 sans »)
   — mais rien dans le code ne distingue une tâche gouvernée en production
   d'un kata d'une fonction en exploration : **même directive, même exigence
   d'enveloppe + pack de preuve + gates, quelle que soit la taille de la
   tâche**. C'est exactement le chemin qui produit les tours supplémentaires :
   ouvrir/écrire `task-envelope.md`, tenir `evidence-pack.md` à jour, puis
   satisfaire `grimoire standard verify` qui — vérifié [reproduit] ci-dessous
   — réclame *sept* artefacts que rien n'a scaffoldés (`grimoire standard
   init` n'est jamais appelé par `setup_arm_kit`, ni suggéré comme
   nécessaire par le message lui-même).
3. La ligne fournisseurs et la ligne propositions (`_providers_status_line`,
   `_proposals_status_line`) — best-effort, courtes, non structurantes ici.

Total mesuré [reproduit] pour ce dépôt (archétype `minimal`, aucune stack
détectée) : **1 409 caractères ≈ 352 tokens**, injectés **une fois** par
session (`SessionStart`, pas par tour) — ce chiffre recoupe exactement celui
publié dans le `CHANGELOG.md` du kit pour `--lite` (« 1409 caractères ≈ 352
tokens », mesure indépendante, même archétype). Cette injection ponctuelle
n'est pas en elle-même coûteuse ; ce qui coûte, ce sont les *actions* qu'elle
prescrit.

`PreToolUse` (`grimoire-hook --host claude --event PreToolUse`), lui,
retourne dans le cas courant un objet minimal (`{"permissionDecision":
"allow"}`, 86 octets, aucun `additionalContext`) — **[reproduit]**, latence
mesurée **~50 ms/appel** (5 appels chronométrés), contre **~120 ms** pour
`SessionStart` (démarrage d'un interpréteur Python à chaque appel). Sur un
run kit à 19 tours (donc grossièrement 15-20 appels d'outils gated), cela
représente **~0,8-1,0 s** de latence hooks cumulée — négligeable face aux
~125 s de delta médian mesurés en §1.1. **Les hooks ne sont pas le poste qui
explique le temps** ; les tours de LLM supplémentaires (chacun plusieurs
secondes de aller-retour modèle+outil) le sont.

### 1.3 Le cascade de dispatch n'a jamais été sollicité — confirmé, pas supposé

**[mesuré]**, directement dans `results.jsonl` : sur les **60/60** runs du
bras kit, `dispatch_stats.overall.total == 0`. Aucun run n'a fait passer une
seule tâche par `grimoire dispatch`. Le chemin `claude -p` (utilisé par les
trois bras) n'invoque jamais la cascade de routage par coût du kit — elle
n'a donc rien économisé ici, et n'explique aucune part du surcoût (elle est
simplement absente du chemin mesuré). Le kit n'a payé, dans ce banc, que le
coût de sa couche de gouvernance (hooks, persona, standard), jamais compensé
par un routage vers un modèle moins cher.

### 1.4 Tableau d'attribution

| Cause | Preuve | Part du surcoût (tours/temps) | Part du coût |
|---|---|---|---|
| Directive standard inconditionnelle (enveloppe + pack de preuve + gate check + verify, sans classification de tâche) | §1.2.2, [reproduit] : sept artefacts requis par `verify`, aucun scaffoldé, sur un dépôt jamais `standard init` | **Dominant** — explique la quasi-totalité des ~13 tours supplémentaires (coût/tour kit ≤ coût/tour nu, §1.1) | **~90-95 %** (proportionnel aux tours) |
| Persona d'entrée imposée (lecture forcée de `concierge.md`, 2 800 tokens) sur une session batch sans utilisateur à trier | §1.2.1, [reproduit] | 1-2 tours forcés en tête de session | quelques % |
| Latence hooks (`SessionStart` + `PreToolUse` × N appels) | §1.2, [reproduit], chronométré | ~0,8-1,0 s cumulés sur ~125 s de delta | négligeable (<1 %) |
| Setup (`grimoire init` + `host sync`) | §1.1, lu dans le harnais (`wall_seconds` ne le compte pas) | **0 %** — écarté par construction | 0 % |
| Cascade de dispatch / routage coût | §1.3, [mesuré] `dispatch_stats` = 0 sur 60/60 | Absente du chemin — n'explique ni surcoût ni économie | 0 % (jamais invoquée) |
| Cache de prompt cassé | §0 — non mesurable (`cache_read_input_tokens` jamais extrait) ; indice indirect : `input_tokens` final quasi nul (6-52 sur les 180 lignes) est **cohérent avec un cache actif**, pas avec un cache cassé | Non mesurable ; indice contraire à l'hypothèse | Non mesurable |
| Contexte système plus lourd par tour (CLAUDE.md + copilot-instructions.md, ~5,2 Ko) | [reproduit], chargé une fois au démarrage de session (éligible au cache de prompt, pas réinjecté par tour) | Marginal — un seul chargement, pas un coût répété | Marginal |

**Lecture** : le surcoût n'est ni un problème de contexte système lourd, ni
un problème de hooks lents, ni un cache cassé, ni un temps de setup — c'est
un problème de **turnover d'actions imposées par un mandat de gouvernance
qui ne sait pas s'auto-doser selon la taille de la tâche**, combiné à une
cascade de coût qui n'intervient jamais sur ce chemin pour compenser quoi
que ce soit.

## 2. La preuve : « gates verts » sans que les tests réels aient tourné

Sur `go/palindrome-products` (kit 1/3) et `javascript/transpose` (kit 2/3),
les runs échoués ont `terminated_reason: "completed"` (**[mesuré]**,
`results.jsonl`) — la session Claude Code s'est terminée normalement, pas
par timeout ni boucle détectée : l'agent a cru avoir fini. Les
`.stream.jsonl` de ces runs précis ne sont plus disponibles (§0), donc ce
qui suit **reconstitue le mécanisme qui permet ce résultat**, vérifié en
relisant et en exécutant le code réel du kit (`main` @ `f1289dd0`) :

**A. `grimoire standard gate check --task-id bootstrap --strict` passe à vide
sur un projet sans aucune gouvernance initialisée** — [reproduit],
exécuté dans ce worktree :

```
$ grimoire standard gate check . --task-id bootstrap --strict
OK evidence gates for task bootstrap
$ echo $?
0
```

sur un dépôt qui n'a **jamais** eu `_grimoire/standard/` (vérifié : absent).
Lecture du code (`check_evidence_gates`,
`src/grimoire/core/agentic_standard.py:1512-1567`) : quand la tâche n'est
sur aucun board (`board_omits_task`), `state` retombe sur la chaîne vide, et
**aucun** des ensembles d'états qui déclenchent une vérification d'artefact
(`{"ready","in_progress","review","accepted","released"}`) ne contient la
chaîne vide — donc rien n'est vérifié, et le gate répond « OK » de façon
quasi tautologique. `--strict` ne change rien à ce chemin puisqu'il n'y a
aucun `missing` à faire échouer.

**B. `grimoire standard verify .` — la seule des deux commandes qui détecte
réellement l'absence de gouvernance — ne vérifie jamais le code, seulement
l'existence et le parsing de fichiers déclaratifs** — [reproduit] :

```
$ grimoire standard verify .
FAIL agentic standard profile starter (7 error(s))
   missing _grimoire/standard/mission-brief.md
   missing _grimoire-output/evidence/bootstrap/task-envelope.md
   missing _grimoire-output/evidence/bootstrap/evidence-pack.md
   missing _grimoire-output/evidence/bootstrap/claim-ledger.md
   missing _grimoire-output/evidence/bootstrap/acceptance-record.md
   missing _grimoire/standard/retention-registry.yaml
   missing _grimoire/standard/standard-profile.yaml
```

`run_verifiers` (`src/grimoire/core/standard_checks/verifiers.py:1423-1466`)
enchaîne **43 fonctions `_verify_*`** — grep exhaustif sur tout le fichier :
**zéro occurrence de `subprocess`, `pytest`, `cargo test`, `go test`, `npm
test`, ou de tout appel `.run([...])`**. Aucun verifier n'exécute la moindre
commande. En particulier, `_verify_acceptance_record`
(`src/grimoire/core/standard_checks/controls.py:923-968`) — le verifier
censé porter « AG-QUA-003 : le livrable est accepté ou refusé sur des
critères prouvés » — se contente de **parser un tableau Markdown** que
l'agent écrit lui-même : une ligne `| AC-001 | … | <preuve texte> | passé |`
satisfait le gate dès lors que la cellule « preuve » n'est pas vide — son
contenu n'est jamais confronté à l'exécution réelle d'un test. Un agent qui
tape « j'ai relancé les tests, ils passent » dans cette cellule, sans avoir
rien exécuté, produit un verify vert.

**Conclusion du mécanisme** : la chaîne complète que la directive
`SessionStart` prescrit (`gate check --strict` puis `verify`) peut se
terminer « verte » sans qu'aucune commande n'ait vérifié le comportement
réel du code produit — exactement le patron « garde qui échoue ouvert »
(mémoire projet `feedback_gardes_fail_open` : preuve en prose, jamais en
exécution). Cela explique mécaniquement comment une session peut se clore
`completed` (ni erreur, ni boucle, ni timeout) sur `go/palindrome-products`
et `javascript/transpose` alors que les tests cachés — exécutés
séparément et après coup par le harnais lui-même (`run_hidden_tests`, la
seule vérification du banc qui exécute réellement du code) — la contredisent.

Point notable : le kit **possède déjà** un mécanisme d'acceptance exécuté
réellement — `AcceptanceRun` / `AcceptanceEvidence(kind="test")`
(`src/grimoire/flows/schemas.py`, `src/grimoire/flows/dispatch_executor.py`,
issue #428) traduit une acceptance `{"test": "..."}` d'un flow en exécution
de commande, gate-checkée par `dispatch_executor`. Mais ce mécanisme vit
dans le moteur de flows (`grimoire flow run` / `grimoire dispatch`), jamais
sollicité sur le chemin `claude -p` (§1.3) — la directive `SessionStart`
pointe vers `standard gate check`/`verify`, la couche déclarative, pas vers
cette couche d'exécution qui existe déjà pour un autre usage.

## 3. Lots de cœur proposés

Classés par gain attendu / effort. S'inscrivent dans `docs/plan-2026-q4.md`
Phase 2 (« Nettoyage et surcoût », issue #552), dont l'objectif déclaré
(« mesurer et plafonner le surcoût par tour ») est exactement celui-ci.

### Lot A — Classer la tâche avant d'imposer le mandat de gouvernance (S)

**Mécanisme** : dans `decide_activation`
(`src/grimoire/hosts/decisions/activation.py`), ne construire la directive
complète (`activation_context_text` / `_DIRECTIVE_TEMPLATE`) que si le
projet a réellement adopté le standard — au minimum `(project_root /
STANDARD_DIR).is_dir()` (aujourd'hui la directive s'affiche même en son
absence totale, §1.2.2/§2.A) — et, au-delà, une classe V0 « exploration »
(pas de `_grimoire/standard/`, pas de board, ou heuristique déjà présente
dans le kit pour `--lite`/`_looks_like_a_playground`,
`src/grimoire/cli/cmd_init.py`) qui reçoit une directive courte ou aucune,
sans enveloppe ni pack de preuve exigés.

- **Gain attendu sur le banc** : le repro (§1.2, §2.A) montre que sur un
  projet jamais `standard init`, toute la chaîne enveloppe → pack de preuve
  → verify-en-boucle est déclenchée par la seule présence du hook, pour rien
  vérifier au final (gate check vide). Supprimer ce mandat sur les projets
  non gouvernés devrait ramener le bras kit vers l'ordre de grandeur du bras
  nu sur les tours (cible : tours kit/nu < 1,5×, contre 3,17× mesuré),
  donc coût et temps dans la même proportion (cible coût kit ≤ 70 % du bras
  nu, seuil déjà écrit dans `docs/plan-2026-q4.md` §3).
- **Effort** : S — un garde conditionnel dans `decide_activation` +
  tests rouge-avant/vert-après (`tests/unit/hosts/` ou équivalent).
- **Mesure de preuve** : rejouer `scripts/bench/three_arms.py --full` (même
  graine 551, mêmes 20 tâches) après le correctif ; comparer médiane
  tours/temps/coût du bras kit à ce rapport (19 tours/189 s/1,247 $) — succès
  du lot si le ratio kit/nu tombe sous 1,5× sans perte de succès (le
  mécanisme actuel n'aidait déjà pas sur les 2 tâches en échec, §2).

### Lot B — Relier les gates à une exécution réelle (M)

**Mécanisme** : remplacer (ou faire précéder) la vérification déclarative de
`_verify_acceptance_record` et de `check_evidence_gates` par le mécanisme
d'acceptance déjà existant et déjà exécuté ailleurs dans le kit —
`AcceptanceRun`/`AcceptanceEvidence(kind="test")`
(`src/grimoire/flows/schemas.py`, `dispatch_executor.py`, issue #428). Sur un
projet avec une commande de test connue (`project-context.yaml` ou
détection de `pytest.ini`/`package.json`/`Cargo.toml`/`go.mod`), une ligne
`acceptance-record.md` marquée « passé » doit correspondre à un run réel de
cette commande, dont le code de sortie et la sortie sont enregistrés — pas
une cellule de texte libre.

- **Gain attendu sur le banc** : ne change ni coût ni temps directement,
  change la **fiabilité du signal** — le pass^k mesuré du bras kit
  (85 %, contre 90 % nu / 90 % ecc, `report.md`) devrait rejoindre les deux
  autres bras si les gates cessent de laisser passer un code qui échoue
  réellement, en forçant l'agent à découvrir l'échec pendant la session
  plutôt qu'après coup par le harnais.
- **Effort** : M — touche `verifiers.py`, `controls.py`,
  `dispatch_executor.py` (réutilisation, pas duplication) et leurs tests ;
  respecter la discipline maison : écrire d'abord le test qui échoue
  ouvertement sur le comportement actuel (une ligne « passé » sans exécution
  réelle doit faire échouer `verify` après le correctif, pas avant).
- **Mesure de preuve** : sur le banc, viser `go/palindrome-products` et
  `javascript/transpose` en particulier — pass^k du bras kit sur ces deux
  tâches doit égaler ou dépasser nu/ecc (aujourd'hui 1/3 et 2/3 contre 2/3 et
  3/3) ; en isolé, un test unitaire qui injecte une ligne acceptance « passé »
  sans commande exécutée doit désormais faire échouer `verify` (rouge avant
  le lot, vert après).

### Lot C — Persona à la demande, pas systématique (S)

**Mécanisme** : dans `entry_persona_context`
(`src/grimoire/hosts/decisions/activation.py`), ne pas mandater la lecture
intégrale de la persona d'entrée (`concierge.md`, 2 800 tokens, §1.2.1) quand
la session est non interactive par construction (`claude -p`, pas de TTY) et
que la tâche arrive déjà cadrée (`TASK.md`/prompt initial non ambigu) — le
rôle documenté du concierge (« triage d'une demande humaine ambiguë ») ne
s'applique pas à ce cas. Alternative plus sûre si la détection
interactif/batch est fragile : rendre la persona d'entrée un résumé d'une
ligne plutôt qu'un mandat de lecture intégrale d'un fichier de 11 Ko.

- **Gain attendu sur le banc** : retire ~1-2 tours mandatés en tête de
  session et ~2 800 tokens de lecture forcée, sur les 60 runs kit — effet
  secondaire par rapport au Lot A mais cumulable, sans toucher à la
  gouvernance elle-même.
- **Effort** : S.
- **Mesure de preuve** : compter les tours avant la première action liée à
  `TASK.md` (prembattre lecture de `concierge.md`) avant/après, sur un
  sous-ensemble du banc (ou en pilote 2 tâches × 3 rejeux comme le fait déjà
  `--pilot`) ; cible : disparition du tour de lecture `concierge.md` dans les
  runs kit.

### Décision hors-lot — la cascade de dispatch dans `claude -p`

Le protocole demandait de trancher si la cascade doit intervenir dans ce
chemin. Constat (§1.3) : elle n'y intervient jamais aujourd'hui
(`dispatch_stats.overall.total == 0` sur 60/60), et rien ne l'y invite —
`claude -p` ne connaît que les hooks et le système de fichiers, pas
`grimoire dispatch`/`flow run`. Ce n'est pas un bug caché à corriger en
urgence : c'est une portée à documenter (le kit ne peut pas revendiquer
d'économie de routage par coût sur ce chemin) ou un chantier séparé,
volumineux et incertain (câbler `claude -p` pour qu'il route effectivement
via la cascade suppose de renoncer au modèle par défaut de Claude Code sur
certains tours, ce que le protocole du banc a délibérément choisi de ne pas
figer — `docs/bench-three-arms.md` §2). Recommandation : ne pas lancer ce
chantier avant d'avoir mesuré l'effet des lots A/B/C — le surcoût de
gouvernance à lui seul explique le ratio observé (§1.4), donc rien
n'indique aujourd'hui qu'un cascade actif changerait le verdict.

## 4. Ce que le banc doit mesurer en plus la prochaine fois

- **Le transcript brut par run doit survivre à la campagne.** Conserver
  (ou au moins échantillonner, ex. un run par tâche×bras) les `.stream.jsonl`
  au-delà du nettoyage disque — c'est la seule façon de montrer *dans la
  session elle-même* le moment où l'agent déclare les gates verts, plutôt
  que de reconstituer le mécanisme après coup (§0, §2).
- **Extraire l'usage complet, pas seulement le dernier événement
  `result`.** `parse_result_event` (`three_arms.py:350-364`) ne retient que
  le dernier événement `type: result` du flux, et `run_claude_headless` n'en
  tire que `total_cost_usd`, `usage.input_tokens`, `usage.output_tokens`,
  `num_turns`, `modelUsage` — jamais `cache_read_input_tokens` /
  `cache_creation_input_tokens`, jamais un cumul par tour. Sans ça,
  l'hypothèse « cache cassé » reste indécidable (§0) alors qu'elle est citée
  comme risque explicite du protocole.
- **Écrire `modelUsage` dans `RunRecord`.** `RunOutcome.model_usage` est
  déjà rempli (`three_arms.py:707`) mais jamais recopié dans `RunRecord` ni
  dans `results.jsonl` (`three_arms.py:1120-1137`) — un champ qui existe en
  mémoire et meurt avant d'être écrit. Coût quasi nul à corriger, et
  répondrait directement à « quel modèle par tour, la cascade a-t-elle été
  sollicitée » sans avoir à relire des transcripts.
- **Compter les tours par catégorie**, au moins approximativement, à la
  volée pendant le run (le harnais lit déjà chaque ligne `stream-json` pour
  `detect_loop` — `extract_bash_command` pourrait aussi classer les
  commandes vues : lecture de persona/`_grimoire`, écriture
  enveloppe/pack de preuve, `grimoire standard *`, reste). Cela aurait rendu
  le tableau de §1.4 directement mesuré plutôt que partiellement reconstitué.
- **Chronométrer les hooks depuis l'intérieur du run**, pas seulement les
  rejouer après coup en isolation (§1.2) — la latence réelle sous charge (I/O
  disque concurrente, plusieurs runs en parallèle) peut différer des ~50-120
  ms mesurés à froid ici.
- **Publier ces séries en continu**, pas en campagne ponctuelle — recoupe la
  Phase 2 lot 2.5 du plan produit (« Publier la mesure des tokens injectés
  par tour comme série suivie »), à étendre aux tours/hooks/cache ci-dessus
  plutôt que de rouvrir un nouveau chantier de mesure.
