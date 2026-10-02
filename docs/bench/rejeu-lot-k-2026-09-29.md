<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/flask.svg" width="32" height="32" alt=""> Rejeu du bras `kit-gov` après le lot K — les excursions reculent, aucun critère de sortie n'est atteint — 2026-09-29

> Issue [#642](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/642) (plan produit 2026-Q4, phase 2bis « Cœur », lot K, suite directe du lot J — [#582](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/582)).
> Commit rejoué : `main` @ [`d22807d0`](https://github.com/Guilhem-Bonnet/Grimoire-kit/commit/d22807d02095685a57f555fc6dff58a0bef6fb77) (release 3.61.0), qui inclut le correctif du lot K
> ([#651](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/651) : message `acceptance.no_tests_collected` sans injonction, repli `--task-id`
> nommant le board plutôt que d'évaluer une tâche `bootstrap` fantôme) mergé le 2026-09-28, avant ce rejeu.
> Bras rejoué : `kit-gov` uniquement (`--arms kit-gov --resume`, `scripts/bench/three_arms.py`, graine 551, mêmes 20 tâches) ;
> bras `nu`/`ecc`/`kit` repris tels quels du lot J (`docs/bench/rejeu-lot-j-2026-09-18.md`), aucun nouveau run, aucun coût.
> Binaire mesuré : `grimoire` 3.61.0 installé (roue publiée), invoqué par chemin absolu (`--grimoire-bin`), pas le worktree éditable —
> contrairement aux lots F-J qui rejouaient un worktree jetable, ce lot mesure directement le code publié.
> Données brutes locales (Grimoire-Forge, hors dépôt kit), non poussées :
> `_scratch/bench-k/workspace/state/{results.jsonl,selection.json}`,
> `_scratch/bench-k/workspace/reports/2026-09-29/{report.md,report.json}`.
> Coût de la campagne : **33,72 $** (60 runs `kit-gov`, seul bras rejoué), aucun incident (0 run à erreur ou coût nul,
> disque toujours > 400 Go libres, 60/60 `terminated_reason: "completed"`).
> **Verdict : aucun des quatre critères chiffrés de l'issue #642 n'est atteint.** Part des runs « avec excursion » 26,7 %
> (16/60, contre 30 % au lot J — recul réel mais loin du seuil < 10 %) ; coût médian/tâche résolue ≈122 % du nu (contre
> ≈112 % au lot J — dégradation) ; succès 90,0 % (contre 96,7 % au lot J et 91,7 % pour `nu` — dégradation) ; `gate check`
> vert au dernier appel sur 60/60 (seul critère atteint, déjà acquis au lot J). Décision proposée en §7, pas appliquée
> unilatéralement.

<img src="../assets/divider.svg" width="100%" alt="">

## 1. Méthode

### 1.1 Code rejoué

- Worktree jetable `_scratch/wt-bench-k` (`git worktree add … origin/main -b docs/bench-lot-k`), `origin/main` fraîchement
  fetché, HEAD à `d22807d0` (3.61.0) — inclut le correctif du lot K ([#651](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/651)),
  mergé la veille de ce rejeu. Contrairement aux lots F à J, le harnais n'a pas tourné contre un worktree éditable : le
  binaire mesuré (`--grimoire-bin /mnt/…/rel-check-3610/venv/bin/grimoire`, `grimoire --version` = 3.61.0) est la roue
  publiée, exactement ce qu'un projet consommateur installe.
- Bras `nu`/`ecc`/`kit` non rejoués : les 180 lignes du lot J (`_scratch/bench-j/workspace/state/results.jsonl`) ont été
  copiées dans `_scratch/bench-k/workspace/state/results.jsonl` avant tout lancement, lignes `kit-gov` retirées de la
  copie pour que `--resume` ne rejoue que ce bras (même mécanisme que le lot J, qui reprenait lui-même les bras
  `nu`/`ecc`/`kit` du lot F).
- Authentification `--auth oauth-copy` (pas de clé API dans l'environnement de lancement) — mode historique, seul mode
  disponible ici ; surveillé en continu (jalons 20/40/60 runs) pour la rotation de jeton qui avait coûté 30 runs à 0 $
  au lot J : **aucune occurrence cette fois**, campagne continue de bout en bout.
- Toolchains (Go, Rust, Node/npm, Python) et environnement agent/harnais partagé (`run_environment()`, lot I) inchangés
  par rapport au lot J ; `verify_agent_toolchain_environment` a confirmé les quatre langues disponibles avant le premier
  appel `claude -p` (table §1.2 du rapport du harnais, toutes « toolchain disponible »).

### 1.2 Aucun incident

Les 60 runs `kit-gov` se terminent en `terminated_reason: "completed"` (60/60), 0 ligne à coût nul, jamais moins de
400 Go libres sous `_scratch`. Une seule exécution continue, jalonnée à 20, 43 puis 60 runs pour vérifier l'absence
d'expiration OAuth en cours de campagne (défaut du lot J, §1.2 de son rapport) — jamais observée ici.

## 2. Résultats agrégés

| Bras | Runs | Succès | IC95 % succès | pass^k | Temps médian | Tours médians | Coût médian/tâche résolue | Coût total | `test-run.json` (lot B) | Gate vert au dernier appel | Friction toolchain médiane |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| nu | 60 | 91,7 % | [85 %, 98 %] | 90 % | 64 s | 6,0 | 0,399 $ | 27,04 $ | s.o. | s.o. | s.o. |
| ecc | 60 | 93,3 % | [87 %, 98 %] | 90 % | 61 s | 7,0 | 0,629 $ | 45,30 $ | s.o. | s.o. | s.o. |
| kit | 60 | 90,0 % | [82 %, 97 %] | 85 % | 57 s | 6,0 | 0,416 $ | 30,26 $ | 0 % (jamais gouverné) | s.o. | s.o. |
| kit-gov (lot J, 2026-09-18) | 60 | 96,7 % | [92 %, 100 %] | 95 % | 51 s | 6,0 | 0,445 $ | 31,98 $ | 75,0 % | 100 % (60/60) | 1,0 (37 appels/60 runs) |
| **kit-gov (lot K, 2026-09-29)** | 60 | **90,0 %** | **[82 %, 97 %]** | **85 %** | **78 s** | **7,0** | **0,488 $** | **33,72 $** | **75,0 %** | **100 % (60/60)** | **1,0 (54 appels/60 runs)** |

Nouvelle métrique de ce lot — **part des runs « avec excursion »** (définition de l'issue #642, reprise et vérifiée sur
le lot J avant application ici, script `scripts/bench/excursions.py` : un run est « mandat pur » si son seul appel
`grimoire … standard gate check` réel est le dernier appel outil de la session, « avec excursion » sinon — plusieurs
appels gate, ou tout appel outil après le premier) :

| Lot | Runs « avec excursion » | Part | Surcoût total lot−nu porté par les excursions |
|---|---:|---:|---:|
| J (2026-09-18) | 18/60 | 30,0 % | 103 % (+4,95 $ sur +4,94 $ total) |
| **K (2026-09-29)** | **16/60** | **26,7 %** | **37 % (+2,45 $ sur +6,68 $ total)** |

Rejeu : `python scripts/bench/excursions.py --workspace <dossier du banc> --arm kit-gov --reference-arm nu` (option `--json` pour le détail par run ; le dossier du banc contient `state/results.jsonl` et `tasks/`).

Le nombre d'excursions recule (18 → 16) et leur poids relatif dans le surcoût recule fortement (103 % → 37 %), mais le
surcoût total J−nu → K−nu augmente (+4,94 $ → +6,68 $) : les runs « mandat pur » eux-mêmes coûtent désormais plus cher
qu'au lot J (surcoût médian mandat pur : +0,039 $ au lot J, **+0,098 $** au lot K — plus du double) alors que la
définition du mandat n'a pas changé entre les deux lots. Le fichier `report.json` ne distingue pas la cause ; hypothèse
non vérifiée par ce lot : dérive d'environnement entre le 2026-09-18 et le 2026-09-29 (versions de `cargo`/`npm`/`go`
sur la machine de lancement), pas nécessairement un effet du correctif #651 — voir §6.

## 3. Par langue

| Langue | Tours médians nu | Tours médians kit-gov J | **Tours médians kit-gov K** | Coût médian kit-gov K |
|---|---:|---:|---:|---:|
| go | 6 | 6 | **7** | 0,475 $ |
| javascript | 5 | 9 | **6** | 0,503 $ |
| python | 4 | 5 | **6** | 0,466 $ |
| rust | 6 | 6 | **8** | 0,683 $ |

Seul mouvement net favorable : **javascript** (9 → 6 tours médians, se rapproche de `nu` à 5) — cohérent avec l'effet
attendu du correctif #651 sur le WARN `acceptance.no_tests_collected`, le déclencheur documenté par l'issue pour 15/15
runs JavaScript du lot J. **go** (6 → 7), **python** (5 → 6) et **rust** (6 → 8) régressent tous, dans une fourchette
que ce lot n'attribue à aucune cause précise (aucun de ces trois langages n'était visé par le correctif #651).

## 4. Par tâche

| Tâche | Langue | nu | ecc | kit | kit-gov (J) | kit-gov (K) |
|---|---|---|---|---|---|---|
| go/bottle-song | go | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| go/error-handling | go | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| go/palindrome-products | go | 0/3 | 2/3 | 1/3 | 3/3 | **2/3** |
| go/pov | go | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| go/simple-linked-list | go | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/affine-cipher | javascript | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/go-counting | javascript | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/list-ops | javascript | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/queen-attack | javascript | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| javascript/transpose | javascript | 3/3 | 3/3 | 2/3 | 1/3 | 1/3 |
| python/bowling | python | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| python/list-ops | python | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| python/proverb | python | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| python/react | python | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| python/zipper | python | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| rust/forth | rust | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| rust/poker | rust | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| rust/react | rust | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| rust/scale-generator | rust | 1/3 | 0/3 | 0/3 | 3/3 | **0/3** |
| rust/two-bucket | rust | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |

Deux mouvements notables par rapport au lot J, tous deux sur des tâches déjà marginales aux lots précédents (`nu`/`ecc`/`kit`
échouaient déjà dessus avant le lot I) :

- **`rust/scale-generator` (3/3 → 0/3)**, la régression la plus nette de ce lot. Le gate `kit-gov` reste vert à chaque
  run (`cargo test` exécuté, exit 0, enregistré dans `test-run.json`) — la solution passe la suite visible par l'agent
  et par le gate. Le verdict `success` du harnais, qui rejoue la vraie suite Exercism masquée du dépôt de tâche, la
  classe pourtant en échec sur les 3 runs. Signature identique à celle documentée aux lots F/H avant le lot I (« suite
  compilée à 0 test réellement exécutés ») : la disparition d'un fichier de test réel entre l'exécution du lot J et
  celle-ci n'a pas été vérifiée par ce lot (pas de diff de `polyglot-benchmark` inspecté) — cause non tranchée, à
  reprendre avant tout nouveau rejeu si elle se reproduit.
- **`go/palindrome-products` (3/3 → 2/3)** : un des deux runs KO (`run1`, `run2`) ouvre sur un premier `gate check`
  en échec (`first_gate_ok: False`), même défaut de repli sur le board que celui déjà nommé au lot J pour
  `rust/poker run2` — un appel de plus après ce premier échec (1 appel, pas une spéléologie de 22 tours), donc classé
  « avec excursion » mais d'un ordre de grandeur très inférieur à l'incident du lot J.
- `javascript/transpose` reste à 1/3, cause inchangée depuis les lots F/H/J (auto-vérification par un script ad hoc au
  lieu de la vraie suite Exercism masquée, cf. rapport du lot J §4) — rien dans ce lot ne touche à ce mécanisme.

## 5. Ce que change (et ne change pas) le correctif #651

- **Ce qu'il corrige, visible** : plus aucune occurrence du texte « écris un test » ni « justifie » dans les sorties de
  gate JavaScript (recherche exhaustive sur les 60 dernières sorties de gate, comme au lot J) — le déclencheur nommé
  par l'issue pour 15/15 runs JavaScript du lot J a disparu, et les tours médians JavaScript reculent en conséquence
  (9,0 → 6,0, §3).
- **Ce qu'il ne corrige pas** : la définition « mandat pur » de l'issue (un seul appel gate, rien après) exclut aussi
  un run qui enchaîne un appel bénin après un gate déjà vert — par exemple `javascript/affine-cipher run0` lance
  `npx eslint` après un `gate check` vert (§2, script `scripts/bench/excursions.py`). Sur les 16 runs « avec excursion » du lot K,
  10 ont un gate vert dès le premier appel et un seul appel supplémentaire (souvent un lint ou une relecture finale) —
  un ordre de grandeur sans rapport avec les boucles de 3 à 22 tours du lot J. Cette catégorie n'est pas nommée par
  l'issue #642 et n'est donc pas résolue par son critère binaire actuel.
- Le repli `--task-id bootstrap` (deuxième volet du correctif #651) n'a été sollicité par aucun run de cet échantillon
  (aucune carte `bootstrap` manquante rencontrée sur les 60 runs) — non mesurable sur ce banc, comme au lot J pour
  d'autres mécanismes ponctuels.

## 6. Verdict, sans arbitrage

Critères chiffrés de l'issue [#642](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/642) :

| Critère | Cible | Lot J (référence) | **Lot K (ce rejeu)** | Atteint |
|---|---|---:|---:|:---:|
| Part des runs « avec excursion » | < 10 % | 30,0 % (18/60) | **26,7 % (16/60)** | Non |
| Coût médian/tâche résolue | ≤ 105 % du nu | ≈112 % | **≈122 %** | Non |
| Succès | ≥ 96,7 % | 96,7 % | **90,0 %** | Non |
| `gate check` verts au dernier appel | 60/60 | 60/60 | **60/60** | Oui |

Un seul des quatre critères est atteint (déjà acquis au lot J, non affecté par ce lot). Les trois autres reculent ou
stagnent loin de la cible : les excursions diminuent en nombre et en poids relatif dans le surcoût (§2), mais le
surcoût total et le taux de succès se dégradent par rapport au lot J, sur des causes que ce rapport documente (§4, §5)
sans toutes les trancher — en particulier la régression `rust/scale-generator` (§4) et le surcoût accru des runs
« mandat pur » eux-mêmes (§2), qui ne sont pas des effets attendus du correctif #651 tel que décrit par l'issue.

Ce rapport ne propose pas de dégeler la phase 2bis sur ce résultat : à la lettre des quatre critères, ce lot ne clôt
pas l'issue #642. Décision (rouvrir l'analyse causale sur `rust/scale-generator` et le surcoût élargi, redéfinir la
catégorie « excursion bénigne » à un seuil de coût plutôt qu'à une présence binaire d'appel, ou classer le lot comme
non concluant sans nouveau rejeu immédiat) : à Guilhem.

## 7. Ce qui reste

- Cause de la régression `rust/scale-generator` (§4) non tranchée : comparer l'état de `polyglot-benchmark` entre le
  2026-09-18 et le 2026-09-29 (le clone est fait à chaque campagne, `--depth 1` sur `HEAD` du dépôt tiers), ou l'état
  des toolchains (`cargo`/`rustc`) de la machine de lancement.
- Cause du surcoût accru des runs « mandat pur » (+0,039 $ → +0,098 $ médian, §2) non tranchée — vérifier si elle est
  généralisée (dérive de tarification ou de contexte de session Claude Code) ou localisée à quelques tâches.
- La distinction entre « excursion coûteuse » (boucle de plusieurs tours, cas du lot J) et « appel bénin après un gate
  vert » (cas majoritaire des excursions du lot K, §5) n'est pas représentée par le critère binaire actuel de l'issue
  #642 — matière à une définition affinée si un nouveau lot est ouvert sur ce sujet.
