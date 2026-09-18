<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/flask.svg" width="32" height="32" alt=""> Rejeu du bras `kit-gov` — la gouvernance non dosée coûte, elle ne rapporte pas (lot F) — 2026-09-17

> Issue [#582](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/582) (phase 2bis « Cœur »), lot F.
> Commit rejoué : `main` @ `6d9db6c6` + PR [#592](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/592)
> (lots A #584, B #585, C #586 déjà sur `main` ; #592 ajoute le bras `kit-gov`
> à `scripts/bench/three_arms.py`) — **avant** les lots G1
> ([#597](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/597)), G2
> ([#598](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/598)/[#599](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/599))
> et G3 (en cours), qui sont la réponse aux chiffres de ce rejeu, pas leur
> contexte.
> Bras rejoués : les quatre (`nu`, `ecc`, `kit` repris de la campagne du
> 2026-09-17, `kit-gov` nouvellement joué), mêmes 20 tâches, graine 551,
> `--full`.
> Données brutes locales (Grimoire-Forge, hors dépôt kit), non poussées :
> `_scratch/bench-f/workspace/state/results.jsonl` (240 lignes),
> `_scratch/bench-f/workspace/reports/2026-09-17/{report.md,report.json}`,
> analyse tour par tour de 21 runs `_scratch/bench-f/analyse-tours-kit-gov.md`.
> Coût réellement dépensé pour cette campagne : **111,42 $** (60 runs
> `kit-gov`, aucun nouvel appel `nu`/`ecc`/`kit`, repris tels quels).
> **Verdict : le bras gouverné par défaut est pire que le bras nu sur les
> quatre axes mesurés** (succès, pass^k, temps, coût), et pire que le bras
> `kit` non gouverné sur les mêmes quatre axes. Les phases 3 et 4 restent
> gelées (`docs/plan-2026-q4.md` §3). **Nuance importante établie en §3** :
> une partie non négligeable de l'écart de réussite brut vient d'un incident
> d'infrastructure (expiration de session en toute fin de campagne), pas
> d'un défaut de gouvernance — l'écart de tours et de coût, lui, est entier
> et sans cette explication.

## 1. Méthode

- Worktree jetable `_scratch/kit-bench-f` sur `origin/main` + PR #592
  (`feat/bench-kit-gov`) fraîchement fetchée, venv dédié (`uv venv .venv
  --python 3.12`, dépendances installées en isolation).
- Même précaution que le lot E : `PATH="<worktree>/.venv/bin:$PATH"` préfixé
  explicitement avant lancement, vérifié (`grimoire standard gate run-tests
  --help` répond dans cet environnement).
- `setup_arm_kit_gov()` (nouveau dans #592) provisionne, en plus du
  provisionnement du bras `kit` (`grimoire init . --backend local
  --no-cockpit` puis `grimoire host sync --host claude`) :
  1. `grimoire standard init .` avec le profil par défaut **`starter`** —
     celui qu'un développeur seul obtient sans passer `--profile`/`--needs`,
     donc le scénario le plus représentatif d'une adoption réelle, pas un
     profil renforcé choisi pour l'occasion.
  2. Une tâche de board posée directement en `in_progress`, à l'id exact de
     la tâche du banc (`_governed_task_id()`, barres obliques remplacées par
     des doubles underscores — Grimoire refuse `/` dans un `task_id`),
     importée dans le Mission Ledger par `grimoire task migrate-standard`
     (ADR-007) — seule voie CLI qui préserve un id exact.
  3. Résultat vérifié en isolation avant tout rejeu réel : `grimoire standard
     activation-context` rend la directive complète avec `gate run-tests
     --task-id <id>`, `_is_governed()` vaut `True`, `active_task_id()`
     résout cet id — jamais `bootstrap`. Le défaut de méthode du lot E
     (`docs/bench/rejeu-lot-e-2026-09-17.md` §3, bras `kit` jamais gouverné)
     est donc fermé pour ce rejeu.
- `ARMS` passe de trois à quatre bras (`nu`, `ecc`, `kit`, `kit-gov`), chacun
  avec son propre `HOME` isolé. Les bras `nu`/`ecc`/`kit` sont **repris tels
  quels** de la campagne du 2026-09-17 (`--resume`, aucun nouvel appel) ;
  seul `kit-gov` est rejoué en réel, 60 runs (20 tâches × k = 3).
- Campagne lancée en arrière-plan (`nohup`, PID consigné), suivie par
  intervalles espacés (10-15 minutes). Durée réelle des 60 runs `kit-gov` :
  ~4h25 (10:14–14:39 UTC) — nettement plus long que `kit` (189 s médian
  avant lots A/B/C, 57 s après), cohérent avec le coût de gouvernance mesuré
  en §4.
- Rapport généré directement par le harnais (`--label "lot F kit-gov après
  A+B+C"`) : `_scratch/bench-f/workspace/reports/2026-09-17/report.md`,
  toutes les valeurs de ce document sont recalculées directement depuis
  `results.jsonl` (240 lignes), pas recopiées du rapport sans vérification.
- Analyse tour par tour complémentaire (`_scratch/bench-f/analyse-tours-kit-gov.md`) :
  21 des 60 runs `kit-gov` lus en détail (transcriptions `stream.jsonl`,
  dépôts de tâche, notes mémoire écrites par l'agent lui-même) pendant que la
  campagne tournait encore — comptage par appel d'outil classé sur le contenu
  réel de la commande, pas une estimation.

## 2. Résultats agrégés

| Bras | n | Succès | IC95 % succès | pass^k (k=3) | Temps médian | Tours médians | Coût médian/tâche résolue | Coût total | Runs avec preuve de test (`kit_test_run_evidence`) |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| nu | 60 | 91,7 % (55/60) | [85 %, 98 %] | 90 % | 64 s | 6,0 | 0,399 $ | 27,04 $ | — (bras non gouverné) |
| ecc | 60 | 93,3 % (56/60) | [87 %, 98 %] | 90 % | 61 s | 7,0 | 0,629 $ | 45,30 $ | — (bras non gouverné) |
| kit | 60 | 90,0 % (54/60) | [82 %, 97 %] | 85 % | 57 s | 6,0 | 0,416 $ | 30,26 $ | 0 % (0/60 — défaut de méthode du lot E, fermé par ce rejeu) |
| **kit-gov** | 60 | **83,3 % (50/60)** | **[73 %, 92 %]** | **75 %** | **261 s** | **28,5** | **1,915 $** | **111,42 $** | **68,3 % (41/60)** |

Tours médians, coût médian/tâche résolue et part de preuve recalculés
directement depuis les 240 lignes de `results.jsonl` (`num_turns`,
`total_cost_usd` des runs `success=true`, champ `kit_test_run_evidence`) —
identiques à trois décimales près à ceux du rapport généré par le harnais.

`kit-gov` vs `kit` (le seul comparatif qui isole l'effet de la gouvernance,
tout le reste étant identique — même provisionnement de base, même modèle,
même graine, mêmes 20 tâches) :

- **Tours** : 6,0 → 28,5 médians, soit **×4,75**.
- **Coût médian/tâche résolue** : 0,416 $ → 1,915 $, soit **×4,60**.
- **Coût total de campagne** : 30,26 $ → 111,42 $, soit **×3,68**.
- **Temps médian** : 57 s → 261 s, soit **×4,58**.
- **Succès** : 90,0 % → 83,3 %, soit **-6,7 points** (IC95 % chevauchants :
  [82 %, 97 %] vs [73 %, 92 %] — pas de signal statistique isolé, voir §3
  pour la lecture causale).
- **pass^k** : 85 % → 75 %, soit **-10 points** (17/20 tâches à 3/3 pour
  `kit`, 15/20 pour `kit-gov` — voir §3, l'écart tient à deux tâches sur
  cinq non-parfaites).
- **Preuve de test réellement exécutée** : 0 % → 68,3 % — c'est la seule
  ligne où `kit-gov` **rapporte** quelque chose que `kit` ne mesure jamais
  sur ce banc : le lot B (`gate run-tests`) est désormais mesurable, pour la
  première fois depuis son merge. Détail par langage :
  Go 15/15, JavaScript 15/15, Rust 11/15, Python 0/15 — le plancher Python
  n'est pas un raté de l'agent, c'est structurel (§3).

## 3. Les cinq tâches dégradées — tableau et causes lues sur disque

| Tâche | nu | ecc | kit | kit-gov | Cause du delta kit → kit-gov |
|---|---|---|---|---|---|
| `rust/poker` | 3/3 | 3/3 | 3/3 | **0/3** | **Incident d'infrastructure, pas de gouvernance** — voir ci-dessous |
| `rust/two-bucket` | 3/3 | 3/3 | 3/3 | 2/3 | 1 des 2 échecs de plus = même incident d'infrastructure ; l'autre run est vert |
| `go/palindrome-products` | 0/3 | 2/3 | 1/3 | 1/3 | **Aucun delta kit → kit-gov** : tâche déjà difficile pour tous les bras (nu pire que kit-gov) |
| `javascript/transpose` | 3/3 | 3/3 | 2/3 | 2/3 | **Aucun delta kit → kit-gov** : même taux qu'en non gouverné, cause détaillée ci-dessous |
| `rust/scale-generator` | 1/3 | 0/3 | 0/3 | 0/3 | **Aucun delta kit → kit-gov** : plancher déjà à 0-1/3 sur tous les bras avant toute gouvernance |

### `rust/poker` 0/3 — lecture attentive : ce n'est pas la gouvernance

Les trois runs `kit-gov` de `rust/poker` se terminent identiquement, en
**1 tour, 0,000 $, 4 secondes**, avec `terminated_reason: "error"`. Le
résultat brut de chaque run (`run*.stream.jsonl`) est :

```
"result": "Failed to authenticate: OAuth session expired and could not be refreshed"
```

Aucun fichier de tâche n'a été écrit (`tasks/rust__poker/kit-gov/run{0,1,2}/`
sont vides à l'exception des transcriptions). Ce n'est ni un timeout, ni des
tests jamais lancés faute de temps, ni du code non écrit par épuisement de
tours en gouvernance : la session n'a jamais commencé à travailler, le
premier appel API a été refusé avant le premier tour utile.

Recoupement avec `rust/two-bucket` run 2 (le seul échec de cette tâche, même
signature exacte : 1 tour, 0,000 $, 4 s, même message d'erreur) : les
horodatages (`recorded_at`) des quatre runs en échec sont
**consécutifs et regroupés dans les 16 dernières secondes de toute la
campagne** (14:39:01 à 14:39:17 UTC, sur une campagne qui a commencé à
10:14:36 — soit 4h25 plus tôt) :

| Run | Horodatage |
|---|---|
| `rust/two-bucket` run 2 | 14:39:01 |
| `rust/poker` run 0 | 14:39:06 |
| `rust/poker` run 1 | 14:39:11 |
| `rust/poker` run 2 | 14:39:17 |

Ce sont, à l'exception près, les quatre derniers runs programmés de toute la
campagne. Aucun autre run (sur 240, tous bras confondus) n'a cette signature
(1 tour, coût nul, `terminated_reason: "error"`) — recherche exhaustive sur
`results.jsonl`. Lecture la plus probable : la session Claude Code longue
durée utilisée pour lancer les runs en série a vu son jeton OAuth expirer en
toute fin de campagne (après ~4h25 d'exécution quasi continue), un artefact
de la durée totale du bras `kit-gov` — elle-même conséquence du surcoût de
tours/temps mesuré en §4, mais pas une défaillance du mandat de gouvernance
lui-même, du code produit, ni des gates.

**Conséquence chiffrée sur le verdict brut** : sur les 60 runs `kit-gov`, 4
(6,7 %) sont des échecs à coût et durée nul, non représentatifs d'un travail
de gouvernance raté. En les retirant du dénominateur (56 runs restants, tous
avec un travail réel effectué) : succès 50/56 = **89,3 %** (contre 90,0 %
pour `kit` — écart résiduel de 0,7 point, largement dans le bruit) et pass^k
recalculé sur les mêmes 20 tâches, en traitant les 3 runs `rust/poker`
manquants comme non observés plutôt que comme des échecs : la tâche redevient
non tranchable côté `kit-gov` faute de données, ce qui ramène le nombre de
tâches non-parfaites à 3 sur 20 comme pour `kit` (`go/palindrome-products`,
`javascript/transpose`, `rust/scale-generator`) — pass^k reviendrait à 85 %,
identique à `kit`, **si** on accepte de traiter l'incident comme hors
périmètre de mesure. Ce document ne fait pas ce choix à la place du plan : le
tableau du §2 rapporte le chiffre brut (83,3 %/75 %, incident inclus, c'est
ce qui s'est réellement passé sur ce banc) et cette note documente
explicitement l’ampleur exacte de l'artefact pour qu'un futur rejeu (lot H)
puisse trancher en connaissance de cause. Ce que l'incident **ne change
pas** : le surcoût de tours et de coût (§4) est mesuré sur 56-60 runs qui ont
réellement tourné, il ne doit rien à ces 4 runs à coût nul — si quoi que ce
soit, leur coût nul *sous-estime légèrement* le coût total réel de la
campagne (un run complet aurait coûté plus que 0 $).

### `go/palindrome-products` 1/3 — tâche déjà difficile, pas un effet de gouvernance

Le taux kit-gov (1/3) est **identique** au taux kit non gouverné (1/3) — nu
fait pire (0/3), ecc fait mieux (2/3). Cause technique de l'échec du gate,
sans lien avec la réussite de la tâche : les trois runs (y compris le run 1
qui réussit) reçoivent `go: commande introuvable` (exit 127) sur `go test
./...` — le binaire Go est absent de **tous** les runs Go de ce banc,
succès ou échec confondus (`go/bottle-song`, `go/error-handling`, `go/pov`,
`go/simple-linked-list` réussissent 3/3 avec la même erreur de gate). Ce
n'est donc pas la cause de l'échec des runs 0 et 2 : cette tâche est connue
comme difficile pour tous les bras (matrice §2 du rapport complet), et rien
dans ce rejeu ne montre qu'elle le devient davantage sous gouvernance.

### `javascript/transpose` 2/3 — même taux qu'en non gouverné, mais un défaut de gate mis en évidence

Le taux kit-gov (2/3) est identique au taux kit (2/3). Le run en échec
(run 0) a produit du code plausible, validé par l'agent lui-même via un
script de vérification ad hoc (invariant d'involution
`transpose(transpose(x)) === x`, 0 échec relevé) — mais **jamais** par la
vraie suite de tests Exercism livrée avec l'exercice
(`transpose.spec.js`, présent sur disque). Cause : `npm test` échoue avec
`jest: commande introuvable` (exit 127) sur les 15/15 runs JavaScript de
`kit-gov`, gouvernés ou non — `node_modules` n'est jamais installé dans
l'environnement du banc. L'agent obéit à la directive (« corrige tout
échec » devient, en pratique, « déclare la non-applicabilité et justifie »
puisque relancer `npm install` sans réseau est impossible) et referme la
tâche sur la foi de sa propre vérification, qui a raté un cas limite que la
vraie suite aurait probablement détecté. C'est un angle mort du lot B déjà
identifié par l'analyse tour par tour (`_scratch/bench-f/analyse-tours-kit-gov.md`
§2) : le gate mandate `gate run-tests` sans jamais vérifier au préalable si
la commande de test peut seulement s'exécuter dans cet environnement — ni
un progrès ni un recul par rapport à `kit`, qui n'a jamais eu de gate du
tout sur ce point.

### `rust/scale-generator` 0/3 — plancher préexistant sur les quatre bras

`cargo test` s'exécute avec succès (`ok=true`, exit 0) sur les trois runs
`kit-gov` — mais rapporte **0 tests exécutés** (« running 0 tests… test
result: ok. 0 passed »), la vraie suite de tests de l'exercice n'étant
jamais compilée/liée dans cette configuration. Le gate passe au vert sur une
suite vide, pas sur un vrai résultat — mais cette tâche est déjà à 0-1/3 sur
les quatre bras (nu 1/3, ecc 0/3, kit 0/3, kit-gov 0/3) : c'est un plancher
de difficulté de la tâche elle-même, préexistant à toute gouvernance,
inchangé par ce rejeu.

## 4. Décomposition des tours par activité (reprise de l'analyse de 21 runs)

Comptage par appel d'outil (Bash/Read/Write/Edit), sur les 21 des 60 runs
`kit-gov` lus en détail (transcriptions complètes, `_scratch/bench-f/analyse-tours-kit-gov.md`) :

| Catégorie | Médiane (tours) | Part du total (≈31 tours médians observés sur l'échantillon de 21) |
|---|---:|---:|
| Orientation gouvernance (lister `_grimoire-output`, lire les gabarits, `git status`) | 8,0 | ≈26 % |
| Spéléologie dans le source Grimoire installé (décoder un message de gate) | 8,0 | ≈26 % |
| `gate run-tests` | 4,0 | ≈13 % |
| `gate check --strict` | 3,0 | ≈10 % |
| `context build` + découverte CLI (`--help`) | 3,0 | ≈10 % |
| **Travail réel sur la tâche** (édition du fichier solution, vérification locale) | **2,0** | **≈6-7 %** |
| `standard verify` | 1,0 | ≈3 % |
| Écriture `task-envelope.md` | 1,0 | ≈3 % |
| Écriture `evidence-pack.md`/`acceptance-record.md`/`claim-ledger.md` | 1,0 | ≈3 % |
| Écriture mémoire Grimoire | 0,0 | — |

Constats vérifiés sur les 21 transcriptions, pas supposés : **zéro boucle**
(même commande répétée) sur 21 runs ; **zéro occurrence** du hook `Stop`
bloquant une clôture (le hook est bien câblé mais n'intervient jamais dans
le flux observé). Le poste « travail réel » — le seul qui produit
directement le livrable — ne représente que 2 tours sur 31 en médiane
(6-7 %) ; gouvernance générale + spéléologie source, à eux seuls, en
représentent 16 sur 31 (environ la moitié).

## 5. Ce que coûte la gouvernance et ce qu'elle rapporte

**Ce qu'elle coûte** (comparé au bras `kit` non gouverné, même base, mêmes
tâches, même graine) :

- **×4,75 tours** médians (6,0 → 28,5).
- **×4,60 coût** médian par tâche résolue (0,416 $ → 1,915 $), **×3,68 coût
  total de campagne** (30,26 $ → 111,42 $).
- **×4,58 temps** médian (57 s → 261 s).
- **-6,7 points de succès brut** (90,0 % → 83,3 %) et **-10 points de
  pass^k** (85 % → 75 %) — dont, d'après la lecture causale du §3, la quasi
  totalité tient à un seul incident d'infrastructure (expiration de session
  en fin de campagne, 4/60 runs à coût nul) plutôt qu'à une dégradation de la
  qualité du travail produit sous gouvernance : sur les trois tâches où
  `kit-gov` et `kit` sont directement comparables sans cet incident
  (`go/palindrome-products`, `javascript/transpose`, `rust/scale-generator`),
  le taux de réussite est **rigoureusement identique**, tâche par tâche,
  entre `kit` et `kit-gov`.
- **Aucune tâche mieux réussie sous gouvernance** : sur les 20 tâches, pas
  une seule ne passe d'un échec (sous `kit`) à un succès (sous `kit-gov`) —
  le sens du delta, quand il existe, est toujours défavorable ou nul.

**Ce qu'elle rapporte** — la seule ligne positive de ce rejeu :

- **La preuve de test devient mesurable pour la première fois** : 68,3 %
  des runs `kit-gov` (41/60) écrivent effectivement `test-run.json` (lot B),
  contre 0 % pour `kit` (défaut de méthode du lot E, fermé par ce rejeu).
  C'est un gain de méthode de banc, pas encore un gain de qualité démontré :
  aucune des deux tâches que le lot B ciblait explicitement
  (`go/palindrome-products`, `javascript/transpose`) ne voit son pass^k
  s'améliorer sous gouvernance — dans les deux cas le gate s'exécute
  (positivement pour la mesure) mais échoue pour une raison d'environnement
  (binaire absent), pas parce qu'il aurait détecté un vrai défaut que
  l'absence de gate aurait laissé passer.
- Les artefacts de preuve (`task-envelope.md`, `evidence-pack.md`,
  `acceptance-record.md`) sont produits à 100 % des runs, avec un contenu
  substantiel (pas des gabarits vides) — une matière que le bras `kit` ne
  produit jamais, utile si l'objectif est la traçabilité plutôt que la
  vitesse, mais hors du critère de sortie de phase (§6).

**Bilan net** : sur ce banc, avec un mandat de gouvernance non dosé selon la
taille de la tâche, la gouvernance multiplie le coût par 4 à 5 pour un gain
de correction non démontré (nul sur les tâches directement comparables) et
un gain de traçabilité réel mais non chiffré dans le critère de sortie de
phase.

## 6. Verdict par rapport au critère de sortie de phase

Critère (`docs/plan-2026-q4.md` §4, phase 2bis) : tours kit/nu < 1,5×, coût
kit ≤ 70 % du nu, succès kit ≥ nu — mesuré ici sur `kit-gov` (le bras
réellement enrôlé, celui que le critère visait implicitement) plutôt que sur
`kit` (mesuré par le lot E, jamais gouverné avant ce rejeu).

| Critère | Mesuré (`kit-gov` vs `nu`) | Verdict |
|---|---|---|
| Tours kit/nu < 1,5× | 28,5 / 6,0 ≈ **4,75×** | **Non atteint**, largement |
| Coût kit ≤ 70 % du nu | 1,915 $ / 0,399 $ ≈ **480 %** du nu (coût total : 111,42 $ / 27,04 $ ≈ **412 %**) | **Non atteint** |
| Succès kit ≥ nu | 83,3 % vs 91,7 % (IC95 % [73 %, 92 %] vs [85 %, 98 %], chevauchants) | **Non atteint**, et le seul des trois critères où l'écart brut inclut un artefact de mesure (§3) — même en le retirant (89,3 % estimé), toujours < 91,7 % |

Les deux lectures du coût sont rapportées sans arbitrage, comme pour le lot
E : lue par rapport au coût `kit` non gouverné (×4,60/tâche résolue, ×3,68 en
coût total de campagne) ou par rapport au coût `nu` (×4,8/tâche résolue,
×4,1 en coût total) — dans les deux lectures, la direction est la même et
sans ambiguïté : **le bras gouverné par défaut est plus cher et moins bon
que le bras nu ET que le bras kit non gouverné**, sur les quatre axes
mesurés (succès, pass^k, temps, coût). Aucune tension entre les deux
lectures, contrairement au lot E où le sens du critère de coût dépendait du
dénominateur choisi : ici, `kit-gov` est au-dessus de 70 % du nu et
au-dessus de 100 % de `kit` dans tous les cas.

**Verdict global : critère de sortie de phase non atteint, plus nettement
que sur le bras `kit` du lot E.** Les phases 3 et 4 restent gelées
(`docs/plan-2026-q4.md` §3). **Décision : les extras restent gelés, le
travail sur le cœur reste prioritaire** — ce rejeu confirme et chiffre
précisément ce que le diagnostic du 2026-09-17 anticipait déjà en
conclusion (« mandat de gouvernance qui ne se dose jamais selon la taille de
la tâche ») : un mandat de gouvernance complet appliqué à des tâches courtes
et déjà bien réussies sans lui ne peut pas, par construction, produire un
gain net tant qu'il n'est pas dosé.

## 7. Ce qui reste

1. **Lots G1 ([#597](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/597)),
   G2 ([#598](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/598)/[#599](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/599))
   déjà mergés en réponse directe à l'analyse tour par tour de ce rejeu**
   (§4 ci-dessus) : G1 rend le gate auto-suffisant (artefacts scaffoldés,
   messages de gate avec chemin et remède, `gate check --strict` exécute
   lui-même `gate run-tests`) pour retirer la spéléologie source (médiane 8
   tours/run, jusqu'à 20) et l'orientation gouvernance qui lui est liée ; G2
   fait remonter le pack de preuve depuis les actions déjà observées par les
   hooks plutôt que de les faire retaper par l'agent (médiane 3-4 tours/run
   retirés). Ni l'un ni l'autre n'a encore été mesuré sur ce banc — ce
   rejeu (lot F) leur est **antérieur**, il documente l'état qu'ils
   corrigent, pas leur effet.
2. **Lot G3 en cours** : conditionner le mandat `gate run-tests` à ce que
   `resolve_need("test-runner")` résout effectivement une commande pour le
   projet (angle mort documenté en §3 pour `go/palindrome-products` et
   `javascript/transpose` — le gate s'exécute et échoue pour une raison
   d'environnement plutôt que de ne jamais être mandaté quand il ne peut
   structurellement rien vérifier).
3. **Lot H — à lancer après G3** : rejouer le bras `kit-gov` (même harnais,
   même graine 551, mêmes 20 tâches) une fois G1/G2/G3 mergés, pour mesurer
   si le ratio tours/coût kit-gov/nu descend sous les seuils du critère de
   sortie de phase (§6) et si l'incident d'infrastructure du §3 ne se
   reproduit pas sur une campagne mécaniquement plus courte (moins de tours
   par run ⇒ moins de temps total ⇒ moins de risque d'expiration de session
   en cours de campagne).
4. **Au-delà du lot H, si le ratio reste défavorable** : la piste ouverte
   par le diagnostic initial (`docs/plan-2026-q4.md` §4, phase 2bis, lot A)
   reste le dosage de la gouvernance par classe de tâche — un mandat complet
   pour une tâche longue/à risque, une directive courte ou nulle pour une
   tâche courte déjà bien réussie sans lui, plutôt qu'un mandat uniforme
   quelle que soit la taille de la tâche. Ce rejeu ne l'instruit pas : les
   20 tâches du banc sont toutes courtes (exercices Exercism), aucune ne
   permet de mesurer si un mandat complet rapporte davantage sur une tâche
   longue ou à risque réel.
5. **L'incident d'infrastructure du §3 n'a pas de parade de méthode de banc
   à ce jour** : une campagne de 4h25 sur une session Claude Code continue a
   suffi à faire expirer un jeton OAuth avant la fin. Le lot D (harnais,
   déjà livré) n'inclut pas de rafraîchissement de session ni de reprise
   automatique sur `api_error` ; ni la relance automatique des runs en
   erreur ni un découpage de la campagne en sessions plus courtes n'a été
   instruit — matière possible pour un futur lot de harnais si la durée de
   campagne ne diminue pas suffisamment après G1-G3.
