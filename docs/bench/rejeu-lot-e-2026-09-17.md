<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/flask.svg" width="32" height="32" alt=""> Rejeu du banc à trois bras après les lots A/B/C — 2026-09-17

> Issue [#582](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/582) (phase 2bis « Cœur »), lot E.
> Commit rejoué : `main` @ `6d9db6c6` (lot A #584, lot B #585, lot C #586, harnais
> lot E #589, tous mergés le 2026-09-17 avant le rejeu).
> Bras rejoué : `kit` uniquement (`--arms kit --resume`, `scripts/bench/three_arms.py`,
> graine 551, mêmes 20 tâches) ; bras `nu`/`ecc` repris tels quels de la
> campagne du 2026-09-17 (`docs/bench/diagnostic-surcout-kit-2026-09-17.md`).
> Données brutes locales (Grimoire-Forge, hors dépôt kit), non poussées :
> `_scratch/bench-e/workspace/state/{results.jsonl,selection.json}`,
> `_scratch/bench-e/workspace/reports/2026-09-17/{report.md,report.json}`.
> Coût réellement dépensé pour ce rejeu : **30,26 $** (60 runs `kit`, aucun
> nouvel appel `nu`/`ecc`).
> **Verdict : critère de sortie de phase partiellement atteint.** Le surcoût
> de tours/temps est éliminé (lot A confirmé en isolation), mais le lot B
> n'a été exercé sur aucun des 60 runs de ce rejeu — constat détaillé en §3 —
> et le coût kit reste au-dessus, pas en-dessous, de 70 % du coût `nu`. Les
> phases 3 et 4 restent gelées (`docs/plan-2026-q4.md` §3).

## 1. Méthode

- Worktree jetable `_scratch/kit-bench-e` sur `origin/main` fraîchement
  fetché (`git log --oneline -8` vérifié avant lancement : lots A, B, C et le
  harnais lot E présents), venv dédié (`uv venv .venv --python 3.12 && uv pip
  install --python .venv/bin/python -e '.[dev]'`).
- **Vérification préalable critique** : sans précaution, `grimoire` sur `PATH`
  résout vers le venv global de la Forge (`grimoire-kit 3.54.0` publié sur
  PyPI, **sans** le lot B — `grimoire standard gate run-tests --help` y
  échoue avec « No such command »). Le harnais lance ses sous-processus
  `grimoire` via `env = {**os.environ, "HOME": ...}` : il hérite du `PATH` du
  processus appelant, pas d'un chemin figé. Lancement de la campagne avec
  `PATH="<worktree>/.venv/bin:$PATH"` en préfixe explicite, vérifié avant
  lancement (`grimoire standard gate run-tests --help` répond correctement
  dans cet environnement, avec `--task-id`).
- Workspace `_scratch/bench-e/workspace` : `state/selection.json` et
  `state/results.jsonl` de la campagne du 2026-09-17 copiés à l'identique,
  **lignes du bras `kit` retirées** (120 lignes `nu`/`ecc` conservées sur
  180 d'origine).
- Campagne : `python scripts/bench/three_arms.py --full --resume --arms kit
  --seed 551 --label "lot E après A+B+C" --workspace
  .../bench-e/workspace`, lancée en arrière-plan (`nohup`, PID consigné),
  suivie par intervalles espacés (15 puis 5 minutes en fin de campagne).
  Durée réelle : ~81 minutes (08:25–09:46 UTC) pour 60 runs `kit`, plus
  rapide que l'estimation de 2-3 h — aucun `run-timeout-s` atteint, aucune
  boucle détectée, disque jamais sous le seuil de 5 Go, aucun identifiant
  oublié en fin de campagne.
- Rapport généré directement par le harnais du lot E (`--label`, notes de
  reprise pour `nu`/`ecc`, détail par run du bras `kit`) :
  `_scratch/bench-e/workspace/reports/2026-09-17/report.md`.

## 2. Résultats agrégés — avant (2026-09-17, pré-lots) / après (ce rejeu)

| Bras | Succès avant | Succès après | pass^k avant | pass^k après | Temps médian avant | Temps médian après | Tours médians avant | Tours médians après | Coût médian/tâche résolue avant | Coût médian/tâche résolue après |
|---|---|---|---|---|---|---|---|---|---|---|
| nu | 91,7 % | 91,7 % (repris) | 90 % | 90 % (repris) | 64 s | 64 s (repris) | — | 6,0 | 0,399 $ | 0,399 $ (repris) |
| ecc | 93,3 % | 93,3 % (repris) | 90 % | 90 % (repris) | 61 s | 61 s (repris) | — | 7,0 | 0,629 $ | 0,629 $ (repris) |
| **kit** | **90,0 %** | **90,0 %** | **85 %** | **85 %** | **189 s** | **57 s** | **19** | **6,0** | **1,247 $** | **0,416 $** |

Les lignes `nu`/`ecc` « après » sont les mêmes lignes `results.jsonl` que
« avant » (`--resume` sans les rejouer, cf. §1) : identiques par
construction, listées ici uniquement comme référence de comparaison pour le
bras `kit`.

Le bras `kit` change radicalement sur l'axe gouvernance (tours, temps, coût)
et reste identique à la lettre sur l'axe correction :

- **Tours** : 19 → 6,0 médians, ratio kit/nu 19/6 ≈ 3,17× → **1,0×**
  (6,0/6,0 — nu est aussi à 6,0 tours médians sur ce rejeu). Cible du lot A
  (< 1,5×) très largement atteinte : le mandat de gouvernance était bien
  l'intégralité du surcoût de tours, confirmé en isolation.
- **Temps médian** : 189 s → 57 s, désormais **sous** le temps médian `nu`
  (64 s) et `ecc` (61 s).
- **Coût médian/tâche résolue** : 1,247 $ → 0,416 $, soit **33 % de l'ancien
  coût kit** (sous le seuil de 70 % lu comme « par rapport à l'ancien coût
  kit »). Lu par rapport au bras `nu` — la formulation du critère de sortie
  de phase dans `docs/plan-2026-q4.md` §4 (« coût kit ≤ 70 % du nu ») — le
  résultat est différent : 0,416 $ / 0,399 $ ≈ **104 %** du coût `nu` (coût
  total campagne : 30,26 $ kit contre 27,04 $ nu, ratio ≈ **112 %**) — **au-dessus**,
  pas en-dessous, de 70 % du nu. Les deux lectures du critère sont rapportées
  ici sans arbitrage : `docs/plan-2026-q4.md` (§4, phase 2bis) est mis à jour
  en conséquence.
- **Succès et pass^k** : strictement identiques à la campagne du 2026-09-17,
  tâche par tâche (`kit`, les 20/20 tâches ont le même nombre de succès et le
  même pass^k avant/après — vérifié par comparaison programmatique des deux
  `report.json`). 90,0 % de succès (contre 91,7 % `nu`, 93,3 % `ecc`) et
  85 % de pass^k (contre 90 %/90 %) — écart inchangé, IC95 % très
  chevauchants (kit [81,7 %, 96,7 %] vs nu [85 %, 98,3 %] vs ecc
  [86,7 %, 98,3 %] sur pass^k [70 %, 100 %] vs [75 %, 100 %] vs [75 %, 100 %]) :
  la différence n'était déjà pas significative avant, elle ne l'est pas plus
  après.

## 3. Constat majeur : le lot B n'a été exercé sur aucun des 60 runs

`_grimoire-output/evidence/*/test-run.json` — la preuve que `grimoire
standard gate run-tests` a tourné (lot B) — est **absent des 60 runs `kit`
de ce rejeu, sans exception** (colonne « `test-run.json` (lot B) » du
rapport : `non` sur les 60 lignes).

Diagnostic (code lu, pas supposé) :

1. `_is_governed(project_root)` (lot A, `src/grimoire/hosts/decisions/activation.py`)
   exige `_grimoire/standard/` sur le disque avant même de regarder
   `task-board.yaml` ou un profil enregistré. Sans ce dossier, le projet
   reçoit la notice courte (deux lignes) au lieu de la directive complète —
   c'est exactement le comportement voulu par le lot A.
2. `setup_arm_kit()` (`scripts/bench/three_arms.py`) provisionne le bras
   `kit` avec `grimoire init . --backend local --no-cockpit` puis `grimoire
   host sync --host claude` — **jamais** `grimoire standard init`.
3. Vérifié en isolation sur ce worktree (`grimoire init . --backend local
   --no-cockpit` dans un dépôt jetable) : l'arborescence produite contient
   `_grimoire/_memory/`, `_grimoire/overrides/`, `_grimoire/kit/` — **aucun
   `_grimoire/standard/`**, et aucun `task-board.yaml` nulle part dans les 60
   dépôts de tâche `kit` de la campagne.

Conséquence : `_is_governed()` renvoie `False` pour les 60 runs `kit` de ce
banc, quelle que soit la tâche — le bras `kit` du banc à trois bras ne passe
**jamais** par le chemin gouverné depuis le lot A, donc ne reçoit jamais la
ligne `gate run-tests --task-id {task_id}` que le lot B a ajoutée à la
directive gouvernée (`_DIRECTIVE_TEMPLATE`, `claude_activation.py`). Le lot B
est mergé, testé unitairement (rouge-avant/vert-après) et fonctionnellement
correct en isolation — mais **structurellement invisible sur ce banc tel
qu'il provisionne le bras `kit` aujourd'hui**. C'est la cause directe de
l'identité parfaite, tâche par tâche, des résultats `go/palindrome-products`
(kit 1/3, pass^k 0, inchangé) et `javascript/transpose` (kit 2/3, pass^k 0,
inchangé) entre l'ancien et le nouveau rapport — les deux tâches que le lot B
visait explicitement (`docs/plan-2026-q4.md`, ligne du lot B).

Ce n'est pas un défaut du lot B : c'est un défaut de méthode du banc, révélé
par le lot A. Avant le lot A, la directive complète partait sur *toute*
session avec les hooks installés, gouvernée ou non — c'est le bug que le lot
A corrige. Le lot A corrigeant précisément cela, le bras `kit` du banc — qui
n'a jamais simulé un projet réellement enrôlé (`grimoire standard init`) —
bascule du côté « non gouverné » et n'exerce plus jamais la directive, lot B
compris. Le gain de tours/coût du bras kit (§2) est donc probablement optimiste
par rapport à un projet qui aurait réellement adopté le standard.

## 4. Verdict par rapport au critère de sortie de phase

Critère (`docs/plan-2026-q4.md` §4, phase 2bis) : tours kit/nu < 1,5×, coût
kit ≤ 70 % du nu, succès kit ≥ nu.

| Critère | Mesuré | Verdict |
|---|---|---|
| Tours kit/nu < 1,5× | 1,0× (6,0/6,0) | **Atteint**, largement |
| Coût kit ≤ 70 % du nu | ≈ 104-112 % du nu | **Non atteint** |
| Succès kit ≥ nu | 90,0 % vs 91,7 % (non significatif, IC très chevauchants) | **Non atteint** au sens strict, indécidable statistiquement |

**Verdict global : critère de sortie de phase non entièrement atteint.** Les
phases 3 et 4 restent gelées (`docs/plan-2026-q4.md` §3). Le lot A est validé
sans réserve sur son objectif propre (éliminer le surcoût de tours d'un
mandat de gouvernance non dosé). Le lot B reste à valider — il n'a
simplement pas encore été mis à l'épreuve par ce banc.

## 5. Ce qui reste

1. **Fermer la lacune de méthode du banc** avant tout nouveau rejeu utile du
   lot B : ajouter `grimoire standard init` (ou équivalent) à
   `setup_arm_kit()` pour que le bras `kit` représente un projet réellement
   enrôlé — sinon `_is_governed()` restera toujours `False` et le lot B
   restera toujours invisible sur ce banc, quel que soit le nombre de
   rejeux. C'est un chantier de harnais (comme le lot E), pas un chantier
   produit.
2. **Rejouer `go/palindrome-products` et `javascript/transpose`** une fois
   cette lacune fermée — ce sont les deux tâches que le lot B visait
   explicitement ; tant qu'elles n'ont pas été rejouées avec `gate
   run-tests` effectivement invoqué, l'effet du lot B sur le pass^k reste une
   hypothèse non testée, pas un résultat.
3. **Le coût kit est proche de la parité avec `nu`** (≈104-112 %), pas
   spectaculairement supérieur comme avant (×3,1), mais pas non plus sous le
   seuil de 70 % exigé par la phase. Rien dans les lots A/B/C n'agissait
   directement sur le coût par tour restant (le rapport §1.4 du diagnostic
   notait déjà un coût/tour kit *inférieur* à celui de nu — l'écart de coût
   total vient du nombre de tours, désormais aligné ; l'écart résiduel est
   marginal et pourrait se résorber ou s'inverser sur un échantillon plus
   large, n=60 par bras étant faible pour trancher à 10 points de pourcentage
   près).
4. **La décision hors-lot sur la cascade de dispatch reste inchangée** :
   aucun élément de ce rejeu ne la réexamine (hors périmètre du lot E).
