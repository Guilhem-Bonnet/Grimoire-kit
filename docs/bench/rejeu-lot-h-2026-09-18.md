<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/flask.svg" width="32" height="32" alt=""> Rejeu du bras `kit-gov` après les lots G1-G4 — 2026-09-18

> Issue [#582](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/582) (phase 2bis « Cœur »), lot H.
> Commit rejoué : `main` @ `b62df8dd` (lots G1 [#597](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/597),
> G2 [#598](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/598)/[#599](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/599),
> G3 [#602](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/602), G4 [#604](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/604),
> harnais lot H [#603](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/603)/[#605](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/605), tous mergés avant le rejeu valide).
> Bras rejoué : `kit-gov` uniquement (`--arms kit-gov --resume`, `scripts/bench/three_arms.py`,
> graine 551, mêmes 20 tâches) ; bras `nu`/`ecc`/`kit` repris tels quels de la
> campagne du lot F (`docs/bench/rejeu-lot-f-2026-09-17.md`).
> Données brutes locales (Grimoire-Forge, hors dépôt kit), non poussées :
> `_scratch/bench-h2/workspace/state/{results.jsonl,selection.json}`,
> `_scratch/bench-h2/workspace/reports/2026-09-18/{report.md,report.json}`.
> Coût de la campagne valide : **53,38 $** (60 runs `kit-gov`). Un premier
> essai a coûté 44 $ pour des données invalides (§1.1) — non compté dans les
> résultats ci-dessous, exclu de `results.jsonl`.
> **Verdict : cible du lot non atteinte sur les tours (12,0 médians, cible
> ≤ 9), atteinte sur le succès (= `kit`).** Amélioration nette par rapport au
> lot F (tours ÷2,4, coût/tâche résolue ÷2,5) mais insuffisante pour
> satisfaire le critère de sortie de phase 2bis. Les phases 3 et 4 restent
> gelées (`docs/plan-2026-q4.md` §3).

## 1. Méthode

### 1.1 Incident : campagne invalidée, 44 $, cause et correctif

Premier lancement (2026-09-18, 01:xx UTC) : worktree `_scratch/kit-bench-h`
sur `origin/main` fraîchement fetché (G1, G2, G2-fix, G3, harnais lot H
[#603](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/603) vérifiés
présents), venv dédié, campagne lancée avec
`PATH=".venv/bin:$PATH" python scripts/bench/three_arms.py --full --resume
--arms kit-gov ...` — la même recette que les lots E/F.

Tuée par Guilhem après **27 runs et 44 $** : preuve, dans chaque dépôt de
tâche `kit-gov` (`.claude/activation-context.md`), la directive installée
faisait **847 caractères** (ancien gabarit « remplis l'enveloppe… » en trois
étapes), alors que le code du worktree sert **396 caractères** (gabarit du
lot G3). **Cause** : `PATH=".venv/bin:$PATH"` est une entrée **relative**.
Le harnais exécute chaque `grimoire` avec `cwd` = dépôt de tâche jetable —
une entrée `PATH` relative se résout **par rapport à ce `cwd`**, jamais au
répertoire de lancement du harnais. `.venv/bin` n'existe pas sous un dépôt
de tâche : la résolution retombait sur le `grimoire` suivant du `PATH`,
celui de la Forge (`grimoire-kit` 3.55.0, publié **avant** les lots G). Les
27 runs ont donc rejoué le lot F, pas le lot H — invalidés dans leur
intégralité, jamais mélangés au rejeu valide.

**Correctif** (PR [#605](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/605), mergée) :

- `resolve_grimoire_bin()` résout TOUJOURS un chemin absolu (`--grimoire-bin
  <chemin>` explicite ou `shutil.which("grimoire")` résolu en absolu) —
  jamais un repli silencieux sur la chaîne littérale `"grimoire"`. Toutes
  les invocations `grimoire` du harnais utilisent ce chemin en `argv[0]`,
  PATH absolu en tête en ceinture-bretelles.
- `verify_grimoire_binary_matches_template()` : avant le tout premier appel
  `claude -p` d'une campagne, provisionne un dépôt jetable et compare son
  `.claude/activation-context.md`, caractère par caractère, au gabarit lu
  directement dans le **code source de ce worktree**
  (`src/grimoire/core/claude_activation.py`, jamais un `import grimoire`).
  Écart → `RuntimeError` avant toute dépense, 0 $.
- **Piège trouvé en testant le garde-fou en conditions réelles, pas
  seulement mocké** : une première version comparait le gabarit servi au
  rendu de `activation_directive_template()` importé depuis l'interpréteur
  Python *sibling* du binaire vérifié lui-même. Contre le grimoire 3.55.0 de
  la Forge — le binaire même de l'incident — cette version **ne détectait
  rien** : un binaire installé ailleurs sert un gabarit cohérent avec son
  propre code, la comparaison ne pouvait donc jamais échouer. Corrigée avant
  merge ; testé de nouveau en réel contre ce même binaire, qui déclenche
  bien l'erreur (824 caractères servis contre 396 attendus).

### 1.2 Relance valide

- `_scratch/kit-bench-h` mis à jour (`git merge origin/main` pour prendre
  G4 [#604](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/604) et le
  rapport du lot F publié [#600](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/600),
  puis fusion du commit du correctif lot H directement depuis la branche
  locale — sans attendre la fin de la CI de la PR [#605](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/605)).
- Nouveau workspace `_scratch/bench-h2/workspace` : `state/selection.json`
  et `state/results.jsonl` de la campagne du lot F copiés à l'identique,
  lignes `kit-gov` retirées (180 lignes `nu`/`ecc`/`kit` conservées sur 240
  d'origine) — jamais réutilisé le workspace de la campagne invalidée
  (`bench-h`), pour ne prendre aucun risque de contamination.
- Lancement : `DISK_GUARD_MIN_FREE_GB=5 PATH="<chemin absolu du venv du
  worktree>/bin:$PATH" python scripts/bench/three_arms.py --full --resume
  --arms kit-gov --seed 551 --label "lot H kit-gov après G1-G4 (relance,
  garde-fou binaire)" --grimoire-bin "<chemin absolu>/.venv/bin/grimoire"
  --workspace .../bench-h2/workspace`, arrière-plan (PID consigné), suivi
  par intervalles de 15 minutes.
- **Vérifié avant de laisser tourner** : premier dépôt provisionné
  (`python/zipper` run0) — `.claude/activation-context.md` fait exactement
  396 caractères, `grimoire standard gate check --strict` a bien tourné, le
  garde-fou n'a levé aucune erreur, le premier run a réussi en 6 tours/66 s
  — un ordre de grandeur cohérent avec `kit`, pas avec le lot F.
- Durée réelle : ~2 h 15 (04:14-06:29 UTC) pour 60 runs `kit-gov`. Aucun
  `run-timeout-s` atteint, aucune boucle détectée (`terminated_reason:
  "completed"` sur 60/60), disque jamais sous le seuil de 5 Go, coût
  cumulé jamais au-dessus du plafond de garde (60 $ ; arrêt à 53,38 $),
  aucun identifiant oublié en fin de campagne.

## 2. Résultats agrégés

| Bras | Runs | Succès | IC95% succès | pass^k | Temps médian | Tours médians | Coût médian/tâche résolue | Coût total | `test-run.json` présent et vert | `gate check` appelé |
|---|---|---|---|---|---|---|---|---|---|---|
| nu | 60 | 91,7 % | [85 %, 98 %] | 90 % | 64 s | 6,0 | 0,399 $ | 27,04 $ | s.o. | s.o. |
| ecc | 60 | 93,3 % | [87 %, 98 %] | 90 % | 61 s | 7,0 | 0,629 $ | 45,30 $ | s.o. | s.o. |
| kit | 60 | 90,0 % | [82 %, 97 %] | 85 % | 57 s | 6,0 | 0,416 $ | 30,26 $ | 0 % (0/60) | 0 % (0/60, jamais gouverné) |
| kit-gov (lot F, 2026-09-17) | 60 | 83,3 % | [73 %, 92 %] | 75 % | 261 s | 28,5 | 1,915 $ | 111,42 $ | 68,3 % (41/60) | — (non journalisé à ce lot) |
| **kit-gov (lot H, 2026-09-18)** | 60 | **90,0 %** | **[82 %, 97 %]** | **85 %** | **120 s** | **12,0** | **0,769 $** | **53,38 $** | **75,0 % (45/60)** | **100 % (60/60)** |

Lecture des variations lot F → lot H (tous deux bras `kit-gov`, mêmes 20
tâches, même graine) :

- **Tours médians** : 28,5 → 12,0, soit **÷2,4**. Ratio kit-gov/nu :
  28,5/6,0 ≈ 4,75× → **12,0/6,0 = 2,0×**. Cible du lot (< 1,5×, soit
  ≤ 9 tours) **non atteinte**, mais l'essentiel du surcoût mesuré au lot F
  a bien été retiré par les lots G1-G3 (détail §3).
- **Temps médian** : 261 s → 120 s (÷2,2), toujours au-dessus de `nu` (64 s)
  et `kit` (57 s), mais très loin du ×4,08 du lot F.
- **Coût médian/tâche résolue** : 1,915 $ → 0,769 $ (÷2,5). Ratio vs `kit` :
  1,915/0,416 ≈ 4,60× → **0,769/0,416 ≈ 1,85×** ; ratio vs `nu` :
  0,769/0,399 ≈ **1,93×** (critère de sortie de phase : ≤ 70 % du nu — loin
  d'être atteint, mais nettement rapproché depuis le ×4,8 du lot F).
- **Succès** : 83,3 % → **90,0 %**, désormais **identique** à `kit` (54/60
  run par run, IC95 % identiques [82 %, 97 %]) — cible du lot (succès ≥
  `kit`) **atteinte**, à égalité stricte. Face à `nu` (91,7 %) : légèrement
  en dessous, écart non significatif (IC très chevauchants).
- **pass^k** : 75 % → **85 %**, désormais identique à `kit` (85 %), toujours
  sous `nu`/`ecc` (90 %/90 %) — écart non significatif (IC [70 %, 100 %]
  pour les trois).
- **`test-run.json` (lot B, preuve de gate)** : 68,3 % → 75,0 % — comparable,
  malgré une directive beaucoup plus courte : la couverture du lot B n'a pas
  reculé quand le rituel a été raccourci.
- **`gate check` invoqué** : 100 % des runs (60/60, mesuré sur les
  transcriptions — champ non journalisé au lot F) — le mandat unique du lot
  G3 est systématiquement suivi.
- **Incident OAuth du lot F** (session expirée en fin de campagne,
  `rust/poker` 0/3 et un tiers de `rust/two-bucket`) : absent de cette
  campagne — tous les runs `rust/poker` (3/3) et `rust/two-bucket` (3/3)
  ont réellement tourné, aucun run à 1 tour/0 $/4 s.

### Par tâche (kit-gov, lot H)

| Tâche | Langue | nu | ecc | kit | kit-gov (H) |
|---|---|---|---|---|---|
| go/bottle-song | go | 3/3 | 3/3 | 3/3 | 3/3 |
| go/error-handling | go | 3/3 | 3/3 | 3/3 | 3/3 |
| go/palindrome-products | go | 0/3 | 2/3 | 1/3 | 1/3 |
| go/pov | go | 3/3 | 3/3 | 3/3 | 3/3 |
| go/simple-linked-list | go | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/affine-cipher | javascript | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/go-counting | javascript | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/list-ops | javascript | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/queen-attack | javascript | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/transpose | javascript | 3/3 | 3/3 | 2/3 | 2/3 |
| python/bowling | python | 3/3 | 3/3 | 3/3 | 3/3 |
| python/list-ops | python | 3/3 | 3/3 | 3/3 | 3/3 |
| python/proverb | python | 3/3 | 3/3 | 3/3 | 3/3 |
| python/react | python | 3/3 | 3/3 | 3/3 | 3/3 |
| python/zipper | python | 3/3 | 3/3 | 3/3 | 3/3 |
| rust/forth | rust | 3/3 | 3/3 | 3/3 | 3/3 |
| rust/poker | rust | 3/3 | 3/3 | 3/3 | 3/3 |
| rust/react | rust | 3/3 | 3/3 | 3/3 | 3/3 |
| rust/scale-generator | rust | 1/3 | 0/3 | 0/3 | 0/3 |
| rust/two-bucket | rust | 3/3 | 3/3 | 3/3 | 3/3 |

`kit-gov` (lot H) est identique, tâche par tâche, à `kit` : les trois tâches
dégradées (`go/palindrome-products` 1/3, `rust/scale-generator` 0/3,
`javascript/transpose` 2/3) le sont **déjà pour `nu`** au moins autant
(`go/palindrome-products` 0/3 pour `nu`, `rust/scale-generator` 1/3 pour
`nu` seulement) : ce sont des exercices intrinsèquement difficiles pour
Claude Code sur ce jeu de tâches, pas un effet de la gouvernance — aucune
tâche n'est mieux réussie sous gouvernance, mais aucune régression propre à
`kit-gov` non plus par rapport à `kit`.

## 3. Décomposition des tours — 8 transcriptions lues en détail

Comptage par appel d'outil (Bash/Read/Write/Edit) sur le contenu réel de la
commande ou du chemin, dans 8 transcriptions `run*.stream.jsonl` choisies
pour couvrir les quatre langues et les deux régimes (rapide/lent) :
`python/zipper` run0 (6 tours), `python/bowling` run2 (19 tours),
`go/pov` run1 (28 tours, pire cas go), `go/simple-linked-list` run0
(28 tours, pire cas go), `javascript/affine-cipher` run2 (25 tours),
`rust/react` run0 (20 tours), `javascript/queen-attack` run2 (8 tours,
rapide), `rust/two-bucket` run1 (6 tours, rapide).

| Catégorie | Médiane | Max | Présent dans (/8) |
|---|---:|---:|---:|
| Travail réel + outillage (lecture énoncé/solution, écriture solution, build/lint : `cargo build`/`clippy`, `npx eslint`, exécution locale) — mélangé avec la friction outillage ci-dessous, voir texte | 5,0 | 13 | 8/8 |
| Orientation gouvernance (lister/lire `_grimoire-output/evidence`, gabarits) | 4,0 | 9 | 7/8 |
| `gate check --strict` | 1,0 | 2 | 8/8 |
| Travail réel (exécution de tests locaux : `pytest`/`npm test`/`cargo test`/`go test`) | 1,0 | 4 | 4/8 |
| `gate run-tests`/`standard verify` résiduels (appelés à la main malgré une directive qui ne les mandate plus) | 0,0 | 1 chacun | 3/8 |
| Spéléologie source grimoire (`site-packages`/`src/grimoire`) | 0,0 | 0 | 0/8 |
| Orientation git (`git status`/`diff`/`log`) | 0,0 | 2 | 3/8 |

**Où sont passés les ~19 tours retirés depuis le lot F (médiane 31 → 12)** :

- **Spéléologie source (lot F : médiane 8, jusqu'à 20) → 0/8 dans cet
  échantillon.** Le remède ajouté par G1 (chemin et commande copiable dans
  chaque message de gate manquant) semble avoir supprimé ce poste presque
  entièrement — confirmé qualitativement sur les 8 transcriptions, aucune
  ouverture de `site-packages/grimoire` ni du worktree source constatée.
- **Écriture `task-envelope.md`/`evidence-pack.md` via l'outil `Write`/`Edit`
  (lot F : médiane 1+1) → 0/8 dans cet échantillon**, mais le travail n'a
  pas disparu : l'agent édite désormais ces fichiers par des scripts Python
  ou des substitutions `sed`/heredoc lancés en `Bash` (visible dans la
  catégorie « travail réel + outillage » ci-dessus, notamment sur les runs
  à 19-28 tours) — un déplacement de méthode, pas une suppression du geste.
- **`gate run-tests`/`standard verify` mandatés séparément (lot F : médiane
  4+1) → résiduels, appelés à la main sur 3/8 runs** malgré une directive
  qui ne les mandate plus (G3) : l'agent les invoque par prudence ou par
  habitude sur les tâches où le premier `gate check --strict` ne suffit
  visiblement pas à le rassurer — coût résiduel mesuré, pas nul, mais très
  inférieur au mandat systématique d'avant G3.
- **Poste nouveau, non présent au lot F : friction de découverte de
  toolchain sur les tâches Go/Rust.** `gate check --strict` exécute
  lui-même la commande de test (lot G1) — mais dans l'environnement de
  l'agent, pas dans celui, séparé, que le harnais provisionne pour sa
  propre vérification finale (`ensure_go_toolchain`). Sur `go/pov` run1 et
  `go/simple-linked-list` run0 (les deux pires cas de l'échantillon, 28
  tours chacun), une part mesurable des tours « autre bash » est réellement
  la recherche du binaire `go` (`command -v go`, `which -a go`, `find / -name
  gofmt`, jusqu'à un `find /` sur 6 niveaux) et, sur `rust/react`, de la
  toolchain Rust (`rustup toolchain list`, `rustup default stable`) — un
  coût qui n'existe QUE sur les runs gouvernés, puisque `kit` ne mandate
  jamais l'exécution réelle des tests. Non chiffré précisément sur les 8
  transcriptions (mélangé dans « travail réel + outillage »), mais
  visiblement concentré sur les deux pires runs Go de l'échantillon.
- **Boucles (même commande Bash répétée ≥2 fois consécutives) : zéro
  occurrence sur les 8 transcriptions lues**, comme au lot F.

## 4. Verdict par rapport à la cible du lot

Cible du lot H (donnée pour ce rejeu) : tours kit-gov ≤ 9 médians
(< 1,5× `nu`), succès kit-gov ≥ `kit`, `test-run.json` présent et vert sur
la majorité des runs.

| Critère | Mesuré | Verdict |
|---|---|---|
| Tours kit-gov ≤ 9 médians (< 1,5× nu) | 12,0 médians (2,0× nu) | **Non atteint** |
| Succès kit-gov ≥ kit | 90,0 % = 90,0 % (identique run par run) | **Atteint**, à égalité stricte |
| `test-run.json` présent et vert sur la majorité des runs | 75,0 % (45/60) | **Atteint** |

Critère de sortie de la phase 2bis (`docs/plan-2026-q4.md` §3, plus strict —
comparaison à `nu`, pas à `kit`) : tours kit-gov/nu < 1,5×, coût kit-gov
≤ 70 % du nu, succès kit-gov ≥ nu.

| Critère (phase) | Mesuré | Verdict |
|---|---|---|
| Tours kit-gov/nu < 1,5× | 2,0× (12,0/6,0) | **Non atteint** |
| Coût kit-gov ≤ 70 % du nu | ≈193 % du nu (0,769 $/0,399 $) | **Non atteint** |
| Succès kit-gov ≥ nu | 90,0 % vs 91,7 % (non significatif, IC très chevauchants) | **Non atteint** au sens strict, indécidable statistiquement |

**Verdict global, sans arbitrage entre les deux lectures : la cible propre
du lot H est partiellement atteinte (succès et preuve de gate oui, tours
non) ; le critère de sortie de la phase 2bis, plus strict, n'est atteint sur
aucun des trois axes.** Les lots G1-G4 ont réduit le surcoût de tours/coût
de gouvernance d'un facteur 2,4-2,5 par rapport au lot F, sans le faire
franchir les seuils fixés en début de phase. **Décision : les phases 3 et 4
restent gelées** (`docs/plan-2026-q4.md` §3) ; `docs/plan-2026-q4.md` est mis
à jour en conséquence (ligne H, §4 phase 2bis, bannières de gel phases 3/4).

## 5. Ce qui reste

1. **Le dosage du mandat de gouvernance selon la taille de la tâche** (idée
   déjà nommée au lot F comme piste non exercée par aucun lot G) reste la
   piste la plus probable pour franchir le seuil des 1,5× : les tâches déjà
   résolues en 3-5 tours par `nu` (ex. `python/proverb`, `python/list-ops`)
   sont précisément celles où le rapport tours kit-gov/nu est le plus
   défavorable en proportion, alors que `gate check --strict` coûte le même
   tour fixe quelle que soit la taille de la tâche.
2. **Friction de découverte de toolchain sur les runs gouvernés Go/Rust**
   (§3) : `ensure_go_toolchain`/le provisionnement Rust du harnais rendent
   un compilateur disponible pour la vérification finale du harnais, jamais
   pour la session de l'agent qui doit, elle, faire réussir `gate check
   --strict` en conditions réelles. Chantier de harnais (comme les lots
   E/H), pas un chantier produit : exposer le même binaire aux deux.
3. **`gate run-tests`/`standard verify` résiduels** (3/8 de l'échantillon) :
   pas un défaut de la directive (G3 ne les mandate plus), mais un
   comportement de prudence de l'agent à surveiller sur un échantillon plus
   large avant de conclure qu'il est négligeable.
4. **`test-run.json` toujours absent sur 100 % des tâches Python** (15/15,
   comme au lot F) : les stubs Exercism ne portent aucun des marqueurs
   `test-runner`, historiques ou du repli du lot G3 (`pytest.ini`,
   `setup.cfg`, `tox.ini`, dossier `tests/`, `test_*.py` à la racine) —
   `resolve_need` ne peut structurellement rien détecter sur ce format de
   tâche précis, indépendamment de tout futur lot.
5. **Dosage par classe V0/V1/V2** (lot A, jamais réellement exploité par ce
   banc — chaque tâche du jeu Exercism est de taille comparable) : aucun
   résidu mesuré ici, à instruire sur un jeu de tâches plus hétérogène si un
   futur lot veut trancher la piste du point 1.
6. **Incident du 2026-09-18** (§1.1) : le garde-fou ajouté
   (PR [#605](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/605)) est
   désormais permanent dans le harnais — tout rejeu futur du banc en
   bénéficie automatiquement, sans geste supplémentaire à répéter.
