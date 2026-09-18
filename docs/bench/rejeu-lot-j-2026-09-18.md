<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/flask.svg" width="32" height="32" alt=""> Rejeu du bras `kit-gov` après le lot I — la parité est en vue (lot J) — 2026-09-18

> Issue [#582](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/582) (phase 2bis « Cœur »), lot J.
> Commit rejoué : `main` @ [`b1523007`](https://github.com/Guilhem-Bonnet/Grimoire-kit/commit/b1523007657a8ac23d507aedbcb201e3c92c6d7a)
> — lots G1 ([#597](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/597)),
> G2 ([#598](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/598)/[#599](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/599)),
> G3 ([#602](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/602)), G4 ([#604](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/604)),
> harnais lot H ([#605](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/605)), doc lot H ([#607](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/607)),
> et lot I : friction de toolchain partagée agent/harnais ([#608](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/608)),
> gate dosé — I-1/I-2/I-3 ([#609](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/609)) — tous mergés avant le rejeu valide.
> Bras rejoué : `kit-gov` uniquement (`--arms kit-gov --resume`, `scripts/bench/three_arms.py`,
> graine 551, mêmes 20 tâches) ; bras `nu`/`ecc`/`kit` repris tels quels des campagnes précédentes
> (`docs/bench/rejeu-lot-f-2026-09-17.md`), aucun nouveau run, aucun coût.
> Données brutes locales (Grimoire-Forge, hors dépôt kit), non poussées :
> `_scratch/bench-j/workspace/state/{results.jsonl,selection.json}`,
> `_scratch/bench-j/workspace/reports/2026-09-18/{report.md,report.json}`.
> Coût de la campagne valide : **31,98 $** (60 runs `kit-gov`, seul bras rejoué). Un premier
> essai a coûté 0 $ pour des données invalides (§1.2, expiration OAuth immédiate sur 30 runs) —
> exclu de `results.jsonl`.
> **Verdict : cible du lot J largement atteinte (6,0 tours médians, cible ≤ 9 ; succès 96,7 %,
> supérieur à `kit` ET à `nu`).** Sur le critère de sortie de phase (`docs/plan-2026-q4.md` §3) :
> tours kit-gov/nu à **1,0×** (première fois sous 1,5×, cible atteinte), succès kit-gov (96,7 %)
> au-dessus de nu (91,7 %) en valeur ponctuelle (IC95 % chevauchants, non significatif), coût
> médian/tâche résolue à **≈112 % du nu** — au-dessus du seuil historique (≤ 70 %) mais à portée
> de la lecture de parité (≤ 110 %) évoquée en §5. Décision proposée en §6 et dans
> `docs/plan-2026-q4.md` §4 : soumise à Guilhem, pas appliquée unilatéralement.

<img src="../assets/divider.svg" width="100%" alt="">

## 1. Méthode

### 1.1 Code rejoué

- Worktree jetable `_scratch/kit-bench-j`, `origin/main` fraîchement fetché au moment du
  lancement, HEAD à `b1523007657a8ac23d507aedbcb201e3c92c6d7a` (lots G1-G4 + I complets, PR
  [#609](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/609) incluse). `grimoire-kit`
  installé en éditable dans ce worktree (`.venv/bin/python -c "import grimoire"` résout vers
  `_scratch/kit-bench-j/src/grimoire/__init__.py`, pas un paquet figé) — la version affichée par
  `grimoire --version` (3.56.0, tag de release antérieur aux lots G4/H/I) ne reflète donc pas le
  code réellement exécuté, plus récent.
- Lancement avec `--grimoire-bin` absolu (`.venv/bin/grimoire` du worktree, chemin complet) et
  `PATH` du venv en tête — la garde permanente `verify_grimoire_binary_matches_template` (lot H,
  PR [#605](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/605)) a vérifié le gabarit avant
  toute dépense, sans geste supplémentaire de ce lot : elle est désormais du harnais, pas une
  précaution à reproduire manuellement à chaque campagne.
- Toolchains dans la session : Go, Rust (`cargo`/`rustup`), Node/npm (`nvm`) et Python
  disponibles sur le `PATH` réel de la machine de lancement — mêmes conditions que les lots
  précédents. Nouveauté du lot I (PR [#608](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/608)) :
  `run_environment()` calcule un environnement **unique**, partagé entre la session de l'agent et
  la vérification finale du harnais (Go en tête de `PATH`, `GOROOT`/`GOPATH`/`GOCACHE` et
  `CARGO_HOME`/`RUSTUP_HOME`/`CARGO_TARGET_DIR` redirigés sous le workspace) —
  `verify_agent_toolchain_environment` a confirmé, avant le premier `claude -p`, que la commande
  de test résolue par `grimoire needs resolve` s'exécute réellement dans cet environnement pour
  les quatre langues (table dans le rapport du harnais, tous « toolchain disponible »), 0 $
  dépensé si ce n'était pas le cas.

### 1.2 Incident : deux relances avant la campagne valide, 0 $ perdu

Trois exécutions successives sur le même workspace (`_scratch/bench-j/workspace`,
`full.log`/`full-2.log`/`full-3.log`) :

1. **Premier lancement** : coupé par une expiration de session OAuth à 08:22 UTC. 30 des 60 runs
   `kit-gov` prévus se sont terminés en **1 tour, 0,000 $, `terminated_reason: "error"`**
   (`Failed to authenticate: OAuth session expired and could not be refreshed`) — même signature
   que l'incident déjà documenté au lot F (`docs/bench/rejeu-lot-f-2026-09-17.md` §3), mais ici
   en cours de campagne plutôt qu'à la toute fin. Coût réel de ces 30 runs : **0 $** (aucun appel
   API n'a jamais démarré). Les 30 lignes `error` ont été retirées de `results.jsonl` avant toute
   relance — jamais mélangées aux runs valides.
2. **Première relance (`--resume`)** : a immédiatement échoué sur un défaut distinct, trouvé en
   préparant ce lot, pas déjà connu des lots précédents — `verify_grimoire_binary_matches_template`
   (lot H) provisionne son dépôt jetable de vérification à un chemin **fixe**
   (`workspace/_grimoire_bin_check`) ; au second appel sur le même workspace, `grimoire init`
   retombait sur un dépôt déjà initialisé et refusait (« Use --force to overwrite ») — la garde
   elle-même n'était pas rejouable (`full-2.log`). Contournement immédiat : suppression manuelle
   du dépôt de vérification résiduel avant la relance suivante — **pas** un correctif de code à
   ce stade.
3. **Relance valide** (`full-3.log`) : les 60 runs `kit-gov` se terminent proprement
   (`terminated_reason: "completed"` sur 60/60, 0 erreur), rapport écrit à 13:33:37 UTC
   (`[report] écrit sous .../reports/2026-09-18`), aucun identifiant oublié sous un `HOME` isolé
   (vérifié en fin de campagne par le harnais lui-même).

**Cause racine et correctif permanent** (PR [#610](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/610),
mergée le jour même, **après** la fin de cette campagne — n'a donc pas servi à produire les
chiffres ci-dessous, mais ferme le défaut pour tout rejeu futur) :

- **Garde non rejouable** : `check_dir` désigne maintenant le répertoire *parent*, chaque appel
  provisionne un sous-répertoire jetable unique (`tempfile.mkdtemp`), supprimé dans un `finally`
  quel que soit le sort de la vérification.
- **Cause des déconnexions OAuth, nommée pour la première fois** : le harnais copiait les
  identifiants Claude Code de l'opérateur (`~/.claude/.credentials.json`) dans le `HOME` isolé de
  **chaque** run. Les jetons OAuth sont rafraîchis par rotation : quand une copie rafraîchit son
  jeton, la session interactive de l'opérateur ET les autres copies en cours deviennent
  invalides. Observé quatre fois en deux jours sur ce banc (lots E, F, H, J). Nouveau mode
  `--auth api-key` : aucune copie d'identifiant, `claude -p --bare` hérite `ANTHROPIC_API_KEY` de
  l'environnement — non utilisé pour produire ce rapport (la relance valide de ce lot a tourné en
  mode historique `oauth-copy`, sans nouvel incident sur sa durée plus courte), disponible pour
  les lots suivants.

Les 60 runs `kit-gov` valides de ce rapport viennent donc d'une seule exécution continue et
propre du code du point 1.1, avec un contournement manuel (pas un correctif) pour l'incident du
point 2.

## 2. Résultats agrégés

| Bras | Runs | Succès | IC95 % succès | pass^k | Temps médian | Tours médians | Coût médian/tâche résolue | Coût total | `test-run.json` (lot B) | Gate vert ou WARN `no_tests_collected` seul au dernier appel | Friction toolchain médiane (lot I) |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nu | 60 | 91,7 % | [85 %, 98 %] | 90 % | 64 s | 6,0 | 0,399 $ | 27,04 $ | s.o. | s.o. | s.o. |
| ecc | 60 | 93,3 % | [87 %, 98 %] | 90 % | 61 s | 7,0 | 0,629 $ | 45,30 $ | s.o. | s.o. | s.o. |
| kit | 60 | 90,0 % | [82 %, 97 %] | 85 % | 57 s | 6,0 | 0,416 $ | 30,26 $ | 0 % (jamais gouverné) | s.o. | s.o. |
| kit-gov (lot F, 2026-09-17) | 60 | 83,3 % | [73 %, 92 %] | 75 % | 261 s | 28,5 | 1,915 $ | 111,42 $ | 68,3 % | non mesurable (transcriptions non conservées) | champ introduit au lot I, non mesuré |
| kit-gov (lot H, 2026-09-18) | 60 | 90,0 % | [82 %, 97 %] | 85 % | 120 s | 12,0 | 0,769 $ | 53,38 $ | 75,0 % | **68,3 % (41/60)** — mesuré rétroactivement pour ce rapport (méthode §3) | champ introduit au lot I, non mesuré |
| **kit-gov (lot J, 2026-09-18)** | 60 | **96,7 %** | **[92 %, 100 %]** | **95 %** | **51 s** | **6,0** | **0,445 $** | **31,98 $** | **75,0 %** | **100 % (60/60)** | **1,0 (37 appels/60 runs)** |

Notes de méthode :

- Succès, IC95 %, pass^k, temps/tours médians, coût médian/tâche résolue, coût total et
  `test-run.json` (`kit_test_run_evidence`) sont recalculés directement depuis les 240 lignes de
  `results.jsonl` de ce lot et le `report.json` du harnais — identiques à trois décimales près
  entre les deux sources.
- **« Gate vert ou WARN `no_tests_collected` seul au dernier appel »** est une métrique nouvelle
  pour ce rapport, appliquée rétroactivement aux transcriptions encore disponibles de F (aucune,
  supprimées) et H (60/60 présentes, `_scratch/bench-h2/workspace/tasks/*/kit-gov/run*.stream.jsonl`) :
  pour chaque run, le dernier appel Bash correspondant à une invocation réelle de
  `grimoire standard gate check` (regex ancrée sur `grimoire ... standard gate check`, en
  excluant les occurrences internes à un heredoc — deux runs J avaient une mention du nom de la
  commande dans une note de mémoire écrite par l'agent, pas un appel réel) est classé `green` si
  sa sortie contient `OK evidence gates`, `fail` sinon. Sur le lot H (avant le lot I), 41/60 runs
  se terminent verts, 19/60 restent en échec au dernier appel (Go : toolchain absente de la
  session agent, exit 127 ; JS : suite de tests masquée, jamais collectée) — c'est exactement le
  défaut que corrige I-1. Sur le lot J (après le lot I), **60/60** runs se terminent verts,
  **aucun** avec le seul `acceptance.no_tests_collected` en cause (recherche exhaustive de la
  chaîne dans les 60 dernières sorties : 0 occurrence) — dans cet échantillon précis, les 15 runs
  JavaScript aboutissent tous à une preuve de test réelle collectée (dépendances installées par
  le lot H, cf. tableau détaillé du rapport du harnais), pas seulement à un avertissement toléré ;
  le mécanisme I-1 existe et est vérifié par test unitaire (PR #609) mais n'a pas été sollicité
  sur cet échantillon de 60 runs.
- **Friction toolchain médiane** (`toolchain_friction_bash_calls`, nouveau champ du lot I) :
  compte les appels Bash `which`/`command -v`/`find -name`/`rustup`/`npm install` par run. Non
  mesurable sur F/H (champ absent de leurs `results.jsonl`, introduit après leur exécution) — la
  seule comparaison possible est qualitative, via l'analyse tour par tour de H
  (`_scratch/bench-h2/analyse-go-js-kit-gov.md`, méthode différente par comptage de tours) qui
  chiffrait la recherche de toolchain Go à une **médiane de 4 tours/run**. Sur J, la médiane
  globale tombe à **1,0 appel/run**, et à **0** sur Go et Rust spécifiquement (détail §3) — la
  baisse est cohérente avec l'effet attendu de la PR #608, sans reproduire exactement la même
  unité de mesure.

## 3. Par langue

| Langue | Tours médians nu | Tours médians kit-gov F | Tours médians kit-gov H | **Tours médians kit-gov J** | Coût médian kit-gov J |
|---|---:|---:|---:|---:|---:|
| go | 6 | 29 | 16 | **6** | 0,417 $ |
| javascript | 5 | 29 | 16 | **9** | 0,615 $ |
| python | 4 | 32 | 7 | **5** | 0,385 $ |
| rust | 6 | 26 | 8 | **6** | 0,449 $ |

Friction toolchain médiane par langue (lot J, `toolchain_friction_bash_calls`) : go **0** (7
appels sur 15 runs), javascript **1** (10/15), python **1** (13/15), rust **0** (7/15). Le poste
qui pesait le plus lourd à H (Go : médiane 4 tours/run rien que pour chercher le binaire,
`_scratch/bench-h2/analyse-go-js-kit-gov.md` §3) tombe à une médiane de **0** appel de recherche
de toolchain et des **tours Go strictement identiques à `nu`** (6 = 6) — l'effet mesuré de la PR
#608 (`run_environment()` unique agent/harnais) correspond exactement à ce que son analyse
prédisait (« ramener la médiane Go vers 10-12 », dépassé : 6). Rust suit la même trajectoire (8 →
6, aligné sur `nu`). JavaScript reste le langage le plus coûteux en tours (9 contre 5 pour `nu`,
ratio 1,8×) — sans recherche de toolchain (`node`/`npm`/`jest` sont sur le `PATH` réel), l'écart
résiduel tient au travail réel (installation puis vérification des dépendances de test, cf. §2
« Dépendances de test installées », 15/15 tentées et réussies pour `kit-gov` contre 0/15 pour les
trois autres bras) et à un run isolé à 21 tours détaillé en §4.

## 4. Par tâche

| Tâche | Langue | nu | ecc | kit | kit-gov (J) |
|---|---|---|---|---|---|
| go/bottle-song | go | 3/3 | 3/3 | 3/3 | 3/3 |
| go/error-handling | go | 3/3 | 3/3 | 3/3 | 3/3 |
| go/palindrome-products | go | 0/3 | 2/3 | 1/3 | **3/3** |
| go/pov | go | 3/3 | 3/3 | 3/3 | 3/3 |
| go/simple-linked-list | go | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/affine-cipher | javascript | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/go-counting | javascript | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/list-ops | javascript | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/queen-attack | javascript | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/transpose | javascript | 3/3 | 3/3 | 2/3 | **1/3** |
| python/bowling | python | 3/3 | 3/3 | 3/3 | 3/3 |
| python/list-ops | python | 3/3 | 3/3 | 3/3 | 3/3 |
| python/proverb | python | 3/3 | 3/3 | 3/3 | 3/3 |
| python/react | python | 3/3 | 3/3 | 3/3 | 3/3 |
| python/zipper | python | 3/3 | 3/3 | 3/3 | 3/3 |
| rust/forth | rust | 3/3 | 3/3 | 3/3 | 3/3 |
| rust/poker | rust | 3/3 | 3/3 | 3/3 | 3/3 |
| rust/react | rust | 3/3 | 3/3 | 3/3 | 3/3 |
| rust/scale-generator | rust | 1/3 | 0/3 | 0/3 | **3/3** |
| rust/two-bucket | rust | 3/3 | 3/3 | 3/3 | 3/3 |

Deux mouvements notables par rapport à `kit` non gouverné :

- **`go/palindrome-products` (1/3 → 3/3) et `rust/scale-generator` (0/3 → 3/3)** : les deux
  tâches identifiées comme planchers de difficulté « structurels » aux lots F/H (Go : gate
  d'origine cassé par l'absence de toolchain avant le lot I, sans lien avec la qualité du code ;
  Rust : suite compilée à 0 test réellement exécutés) passent à 3/3 sous `kit-gov` J — la
  correction du lot I ne fait pas que retirer du bruit de mesure, elle change aussi le verdict
  réel du harnais sur ces deux tâches (`success` recalculé par les tests cachés, pas seulement le
  gate visible par l'agent).
- **`javascript/transpose` (2/3 → 1/3)**, seule dégradation face à `kit`. Cause identique à celle
  déjà documentée au lot F/H (`docs/bench/rejeu-lot-f-2026-09-17.md` §3) : le run en échec
  (`run2`, 12 tours, $0,846) écrit une solution plausible, se vérifie lui-même par un script
  ad hoc (invariant d'involution) plutôt que par la vraie suite Exercism — toujours masquée dans
  le dépôt de tâche, y compris après l'installation des dépendances npm du lot H — et rate un cas
  limite que cette suite aurait détecté (citation du run : « Aucun fichier de test n'a été écrit
  ni modifié. Vérification (aucune suite de te[st connue]… »). Rien dans ce lot ne touche à ce
  mécanisme ; le passage de 1 échec sur 3 (`kit`) à 2 échecs sur 3 (`kit-gov` J) sur une tâche déjà
  marginale (k = 3, deux issues possibles par run) est compatible avec du bruit d'échantillonnage
  sur la même cause connue, pas un nouveau défaut introduit par les lots G/H/I.

## 5. Décomposition des tours — 8 transcriptions lues en détail

Comptage par identifiant de message assistant (un « tour » = un `message.id` unique, ses blocs
`tool_use` classés par priorité gate_check > gate_other > toolchain > manual_test_run >
envelope_io > gouvernance > real_work > text_only), même méthode que les lots F et H, sur 8
transcriptions choisies pour couvrir les quatre langues et les régimes rapide/lent/anomalie :
`python/proverb` run0 (4 tours, rapide), `go/bottle-song` run0 (6 tours), `go/palindrome-products`
run1 (6 tours, tâche naguère difficile, désormais 3/3), `python/bowling` run0 (8 tours, le plus
lourd en Python), `javascript/transpose` run2 (12 tours, le seul échec analysé), `rust/react` run0
(15 tours, le plus lourd en Rust), `javascript/go-counting` run0 (21 tours, anomalie JS) et
`rust/poker` run2 (21 tours mesurés par ce comptage, 22 selon `num_turns` du harnais — écart de
mesure habituel de ±1, anomalie Rust). Échantillon volontairement biaisé vers les runs longs pour
observer où partent les tours ; ne reproduit pas la médiane réelle de la campagne (6,0).

| Catégorie | Médiane (/8) | Max | Présent dans (/8) |
|---|---:|---:|---:|
| Travail réel (lecture énoncé, écriture solution, débogage) | 5,0 | 14 | 8/8 |
| `gate check --strict` (appels) | 1,5 | 7 | 8/8 |
| Exécution manuelle des tests projet (`go test`/`npm test`/`cargo test`/`pytest`, lint) | 1,5 | 3 | 6/8 |
| `gate run-tests`/`verify` résiduels | 0,0 | 2 | 3/8 |
| Écriture envelope/pack/acceptance/claim-ledger (`Write`/`Edit`) | 0,0 | 1 | 3/8 |
| Orientation gouvernance (lister `_grimoire-output/evidence`, `git status`) | 0,0 | 3 | 2/8 |
| Recherche/installation toolchain | 0,0 | 1 | 1/8 |
| Tours texte seul (pas d'appel d'outil) | 1,0 | 1 | 8/8 |

Constats :

- **Spéléologie source Grimoire (`site-packages`/`src/grimoire`)** : **0 occurrence sur les 8
  transcriptions**, comme au lot H — l'effet du lot G1 (chemin et remède dans chaque message de
  gate) tient sur cet échantillon élargi.
- **Recherche de toolchain quasi disparue** : 1 seule occurrence sur les 8 (`rust/poker` run2, 1
  appel), contre une médiane de 4 tours/run sur les runs Go de H. Cohérent avec la friction
  toolchain médiane globale de 1,0 (§2) et la médiane nulle par langue sur Go/Rust (§3).
- **`gate check --strict` reste le poste fixe le plus régulier** (médiane 1,5, présent sur les
  8/8) — c'est le coût plancher, incompressible tant qu'un mandat de gouvernance existe, quelle
  que soit la taille de la tâche. `rust/poker` run2 l'appelle 7 fois sur son propre run (le
  maximum de l'échantillon) : le mécanisme I-2 (pas de ré-exécution sur arbre identique) empêche
  de relancer `cargo test` à chaque appel, mais n'empêche pas l'agent d'appeler `gate check`
  plusieurs fois par prudence — un coût de tours résiduel que I-2 ne visait pas et ne réduit pas
  (il réduit le coût/temps de chaque appel, pas leur nombre).
- **`javascript/go-counting` run0 (21 tours, le run le plus cher de toute la campagne kit-gov à
  $1,154)** : réussit malgré tout (3/3 pour cette tâche) — 14 tours de travail réel, cohérent avec
  un débogage itératif plus qu'avec un problème de gouvernance ; le poste gouvernance
  (`gate_check` 3, `gate_other` 1, `envelope_io` 1) ne dépasse pas les 5 tours combinés, loin
  d'expliquer la longueur du run à lui seul.
- **Aucune boucle détectée** (même commande Bash répétée ≥ 2 fois consécutives) sur les 8
  transcriptions, comme aux lots F et H.

## 6. Ce que coûte et rapporte la gouvernance maintenant

Comparé au bras `kit` non gouverné (même base, mêmes tâches, même graine) :

- **Tours** : identiques en médiane (6,0 = 6,0) — pour la première fois depuis l'ouverture de la
  phase 2bis, un mandat de gouvernance complet ne coûte plus aucun tour supplémentaire en
  médiane. La distribution garde une queue plus longue côté `kit-gov` (`javascript/go-counting`
  à 21, `rust/poker` à 22) que côté `kit` (max observé 8, §4 du rapport du harnais), mais le
  point central a rejoint `nu`/`kit`.
- **Coût médian/tâche résolue** : 0,416 $ (`kit`) → 0,445 $ (`kit-gov`), soit **+7 %** — la
  gouvernance a un coût marginal désormais mesuré en points de pourcentage, pas en multiples.
  Coût total de campagne : 30,26 $ (`kit`) → 31,98 $ (`kit-gov`), +5,7 %.
- **Succès** : 90,0 % (`kit`) → **96,7 %** (`kit-gov`) — `kit-gov` réussit mieux que `kit` non
  gouverné sur ce lot, porté par les deux tâches débloquées en §4 (`go/palindrome-products`,
  `rust/scale-generator`), avec une seule dégradation compensée (`javascript/transpose`).
- **pass^k** : 85 % (`kit`) → **95 %** (`kit-gov`), 19/20 tâches désormais parfaites (3/3) contre
  17/20 pour `kit`.
- **Ce qu'elle rapporte en traçabilité** : `test-run.json` présent sur 75,0 % des runs (stable
  depuis H, malgré une directive courte) ; **100 % des runs se terminent sur un `gate check`
  vert** (§2) — contre 68,3 % à H sur la même mesure appliquée rétroactivement — l'écart le plus
  direct et le plus lisible entre avant et après le lot I sur ce banc.

Face à `nu` (jamais gouverné, aucun artefact de preuve) : `kit-gov` coûte désormais **+12 % en
médiane par tâche résolue** (0,445 $ contre 0,399 $) et **+18 % en coût total de campagne**
(31,98 $ contre 27,04 $) pour un succès et un pass^k tous deux *supérieurs* en valeur ponctuelle
(96,7 % contre 91,7 % ; 95 % contre 90 %), une preuve de test exécutée sur 75 % des runs (0 % pour
`nu`, qui ne produit aucune preuve par construction) et un gate final vert sur 100 % des runs.
C'est la première campagne de la phase 2bis où la gouvernance n'a plus besoin d'un « oui mais » —
son surcoût se mesure en points de pourcentage sur un seul axe (coût), pas en multiples sur
quatre.

## 7. Verdict, sans arbitrage

### (a) Cible propre du lot J

Cible (donnée pour ce rejeu) : tours kit-gov ≤ 9 médians, succès kit-gov ≥ `kit`.

| Critère | Mesuré | Verdict |
|---|---|---|
| Tours kit-gov ≤ 9 médians | 6,0 | **Atteint**, avec marge |
| Succès kit-gov ≥ kit | 96,7 % ≥ 90,0 % | **Atteint** |

### (b) Critère de sortie de la phase 2bis (`docs/plan-2026-q4.md` §3, comparaison à `nu`)

Critère : tours kit-gov/nu < 1,5×, coût kit-gov ≤ 70 % du nu, succès kit-gov ≥ nu.

| Critère (phase) | Mesuré | Verdict |
|---|---|---|
| Tours kit-gov/nu < 1,5× | 6,0/6,0 = **1,0×** | **Atteint** — première fois depuis l'ouverture de la phase 2bis |
| Coût kit-gov ≤ 70 % du nu | 0,445 $/0,399 $ ≈ **111,7 %** du nu (coût total : 31,98 $/27,04 $ ≈ 118,3 %) | **Non atteint** |
| Succès kit-gov ≥ nu | 96,7 % vs 91,7 % (IC95 % [92 %, 100 %] vs [85 %, 98 %], chevauchants — non significatif) | **Atteint en valeur ponctuelle**, indécidable statistiquement à ce n |

Deux critères sur trois sont désormais atteints en valeur mesurée (tours sans réserve, succès en
valeur ponctuelle mais non significatif) ; le coût reste au-dessus du seuil historique de 70 % du
nu, quoique très rapproché par rapport aux lots précédents (F : ≈480 % du nu ; H : ≈193 % ; J :
≈112 %).

### (c) Lecture alternative « parité » du coût (kit-gov ≤ 110 % du nu)

Lecture proposée par Fable à soumettre à Guilhem : sur des tâches nues déjà résolues en quelques
tours, la parité de coût est le plancher physique — aucun mandat de gouvernance ne peut coûter
moins que zéro tour/appel supplémentaire, et seul un routage vers des modèles moins chers ferait
descendre `kit-gov` sous le coût de `nu` ; ce routage (`dispatch_stats`, cascade par
vérifiabilité) n'est jamais invoqué sous `claude -p` (mesuré à 0 sur 60/60 runs `kit`, lot F/H) et
ne l'est pas davantage ici (non ré-instrumenté pour ce lot, hypothèse non vérifiée à nouveau sur
J). Sous cette lecture, le seuil pertinent n'est pas 70 % du nu mais 110 % du nu.

| Critère (parité) | Mesuré | Verdict |
|---|---|---|
| Coût kit-gov ≤ 110 % du nu | **111,7 %** du nu (médian/tâche résolue) ; 118,3 % en coût total | **Non atteint**, mais de 1,7 point sur la médiane — dans l'ordre de grandeur du bruit d'échantillonnage d'une campagne à k = 3 par tâche (cf. dispersion des coûts par run, §2 du rapport du harnais : de 0,234 $ à 1,304 $ sur les runs `kit-gov` individuels) |

Ce document ne tranche pas lequel des deux seuils (70 % historique ou 110 % de parité) doit
gouverner la décision de dégel — c'est à Guilhem de re-baser le critère, pas à ce rapport de le
faire à sa place (§8, décision proposée dans `docs/plan-2026-q4.md`).

## 8. Ce qui reste

1. **Dosage par classe de tâche (V0/V1/V2)** : le lot I (I-3) n'a livré le dosage que pour la
   classe V0 en profil **non gouverné** (« rien à ouvrir ») — jamais pour `governed`, le profil
   que ce banc mesure exclusivement (`kit-gov`). Aucune des 20 tâches du banc actuel n'a été
   reclassée ni testée sous un mandat V1/V2 allégé en profil gouverné ; la piste ouverte depuis le
   lot F (doser le mandat de gouvernance selon la taille de la tâche, pas seulement selon sa
   vérifiabilité déclarée) reste entière pour fermer l'écart de coût résiduel (§7b).
2. **Second jeu de tâches, plus lourdes, pour mesurer le bénéfice de la gouvernance plutôt que
   son seul coût** : les 20 tâches Exercism de ce banc sont toutes courtes (3-15 tours en médiane
   selon le bras) — un régime où un mandat de gouvernance complet a structurellement peu à
   apporter (peu de dérive possible, peu d'occasion pour un gate de rattraper un vrai défaut). Le
   banc actuel a mesuré le coût de la gouvernance sur cinq lots consécutifs (F, H, J) sans jamais
   pouvoir mesurer un gain net sur une tâche où l'absence de gate aurait laissé passer un défaut
   réel — matière pour un futur lot, hors du périmètre outillage/produit déjà couvert par G/H/I.
3. **Mode `--auth api-key` du banc (PR #610)** : mergé après cette campagne, jamais exercé en
   conditions réelles sur un rejeu complet. Le prochain lot qui relance `kit-gov` (ou tout autre
   bras) devrait l'utiliser explicitement (`--auth api-key`, `ANTHROPIC_API_KEY` dans
   l'environnement du lanceur) pour vérifier qu'il élimine bien la classe entière d'incidents
   OAuth observée quatre fois en deux jours (lots E, F, H, J) — pas seulement qu'il compile et
   passe ses tests unitaires.
4. **`gate check --strict` appelé plusieurs fois par prudence malgré I-2** (§5, `rust/poker`
   run2, 7 appels sur un seul run) : I-2 supprime le coût de ré-exécution du test sur arbre
   inchangé, pas la tendance de l'agent à revérifier par appels répétés. Résiduel mesuré, pas
   nul, sur un échantillon de 8 transcriptions — à surveiller sur un échantillon plus large avant
   de conclure qu'il est négligeable pour le budget de tours.
5. **`test-run.json` toujours absent sur 100 % des tâches Python** (comme aux lots F et H) : les
   stubs Exercism Python ne portent toujours aucun marqueur de test-runner détectable par
   `resolve_need` — inchangé par ce lot, structurel au format de tâche, indépendant de tout futur
   lot de gouvernance.
