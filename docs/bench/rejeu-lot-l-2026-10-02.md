<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/flask.svg" width="32" height="32" alt=""> Rejeu `nu` et `kit-gov` le même jour (lot L) — le coût reste à ≈124 % du nu, la régression `scale-generator` était une contamination du lot J — 2026-10-02

> Issue [#642](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/642) (plan produit 2026-Q4, phase 2bis « Cœur », lot L, suite des lots J et K).
> Commit rejoué : `main` @ `6ce89df0` (release 3.62.0). Harnais `scripts/bench/three_arms.py` du même commit, graine 551, mêmes 20 tâches, k=3.
> Bras rejoués : `nu` **et** `kit-gov`, dans un workspace neuf, le même jour, pour supprimer le biais de date du lot K
> (bras `nu` du 2026-09-16, bras gouverné du 2026-09-29). Aucune ligne reprise des lots J ou K.
> Binaire mesuré : `grimoire` 3.62.0 installé (roue publiée), invoqué par chemin absolu (`--grimoire-bin`), `--version` vérifié avant le lancement.
> Authentification `--auth oauth-copy` (pas de clé API).
> Données brutes locales (Grimoire-Forge, hors dépôt kit), non poussées :
> `_scratch/bench-l/workspace/state/{results.jsonl,selection.json}`, `_scratch/bench-l/workspace/reports/2026-10-02/{report.md,report.json}`.
> Coût de la campagne : **22,84 $** (60 runs `nu` 10,15 $ + 60 runs `kit-gov` 12,69 $), aucun incident (0 run à coût nul, 120/120 `terminated_reason: "completed"`, disque > 400 Go libres).
> **Verdict : trois des quatre critères chiffrés de #642 sont atteints, pas celui du coût.** Coût médian par tâche résolue **≈124 % du nu**
> (cible ≤ 105 %) : le surcoût est structurel (≈ +0,04 $ par run, y compris sur les runs « mandat pur »), pas un effet d'excursions.
> Excursions : 16,7 % au sens binaire de l'issue (10/60, cible < 10 % non atteinte), mais **8,3 % d'excursions coûteuses** (5/60, atteinte) une fois les appels bénins distingués.
> Succès 100 % (cible ≥ 96,7 %), `gate check` vert au dernier appel 60/60. **Deux faits changent la lecture des lots précédents** : le modèle
> par défaut de `claude -p` a changé entre K et L (§1.3), et le 3/3 de `rust/scale-generator` au lot J venait de dépôts de tâche contaminés (§5).
> Décision proposée en §7, pas appliquée unilatéralement.

<img src="../assets/divider.svg" width="100%" alt="">

## 1. Méthode

### 1.1 Code rejoué

- Worktree jetable `_scratch/wt-bench-l` (`git worktree add … -b docs/bench-lot-l origin/main`), HEAD `6ce89df0` (3.62.0).
- Commande : `three_arms.py --full --arms nu,kit-gov --seed 551 --auth oauth-copy --workspace <workspace neuf> --grimoire-bin <chemin absolu>`,
  dans un processus détaché (`nohup`), suivi par jalons.
- Clone `polyglot-benchmark` : même commit `7e0611e7` (2024-12-22) aux lots J, K et L.
- Toolchains de la machine de lancement : `cargo`/`rustc` 1.94.1 (mesurés ce jour ; non tracés aux lots J et K).

### 1.2 Aucun incident

0 run à coût nul, 0 erreur d'authentification, 120/120 runs terminés. La rotation OAuth du lot J ne s'est pas reproduite. Un seul run lent
(`go/palindrome-products` run 2, 566 s, 11 appels outils), sans effet sur le succès.

### 1.3 Changement de modèle entre K et L

Les transcriptions enregistrent le modèle de la session : `claude-opus-5[1m]` pour les 60 runs `kit-gov` des lots J et K, `claude-opus-5-5`
pour les 120 runs du lot L (Claude Code 2.1.287, modèle par défaut non épinglé par le harnais). Conséquences :

- les coûts et tours absolus du lot L ne sont **pas comparables** à ceux de J et K (nu : 6,0 → 3,0 tours médians, 0,399 $ → 0,159 $ par tâche résolue) ;
- seuls les **rapports** `kit-gov` / `nu` du même lot sont comparables d'un lot à l'autre, et encore sous réserve d'un modèle différent ;
- le banc est **saturé** : les deux bras résolvent 60/60, le critère de succès ne discrimine plus rien.

## 2. Résultats agrégés

| Bras | Runs | Succès | IC95 % | pass^k | Temps médian | Tours médians | Coût médian/tâche résolue | Coût total |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| nu | 60 | 100 % | [100 %, 100 %] | 100 % | 22 s | 3,0 | 0,159 $ | 10,15 $ |
| **kit-gov** | 60 | **100 %** | [100 %, 100 %] | **100 %** | 24 s | 3,0 | **0,198 $** | 12,69 $ |

Rapport `kit-gov` / `nu` : **124 %** sur le coût médian par tâche résolue, 125 % sur le coût total, 127 % sur la médiane des rapports appariés
(même tâche, même run). Par langue (tours médians / coût médian, `nu` → `kit-gov`) : python 3 / 0,145 $ → 3 / 0,191 $ ; javascript 3 / 0,150 $ → 4 / 0,207 $ ;
go 4 / 0,170 $ → 3 / 0,185 $ ; rust 5 / 0,203 $ → 3 / 0,229 $.

Les 3 appels Bash de « friction toolchain » du bras `kit-gov` (5 pour `nu`) confirment que l'environnement de l'agent n'est plus un facteur (lot I).

### 2.1 Excursions

`scripts/bench/excursions.py` a été corrigé par ce lot (§4) puis rejoué sur les trois lots :

| Lot | Runs « avec excursion » | Dont coûteuses | Dont bénignes | Surcoût total lot−nu | Part portée par les excursions | Surcoût médian « mandat pur » |
|---|---:|---:|---:|---:|---:|---:|
| J (2026-09-18) | 18/60 (30,0 %) | 17 (28,3 %) | 1 | +4,95 $ | 103 % | +0,039 $ |
| K (2026-09-29) | 16/60 (26,7 %) | 6 (10,0 %) | 10 | +6,68 $ | 37 % | +0,098 $ |
| **L (2026-10-02, nu le même jour)** | **10/60 (16,7 %)** | **5 (8,3 %)** | **5** | **+2,54 $** | **30 %** | **+0,038 $** |

Définitions (script versionné) : « avec excursion » = pas exactement un appel `standard gate check`, ou un appel outil après lui ; « bénigne » = premier
gate vert et au plus un appel outil ensuite (lint, relecture) ; « coûteuse » = tout le reste (gate rouge, second gate, boucle).
Les 5 excursions coûteuses du lot L : `go/palindrome-products` run 2 (11 appels, 4 gates, 0,386 $ contre 0,203 $), `go/pov` run 0, `rust/poker` run 1,
`rust/react` run 2 (2 gates chacun), `rust/two-bucket` run 0 (10 appels, 2 gates, 0,326 $ contre 0,185 $). Toutes finissent sur un gate vert.

Le surcoût médian des runs « mandat pur » revient à +0,038 $ (lot J : +0,039 $), alors qu'il était de +0,098 $ au lot K : l'hypothèse du lot K (« dérive
d'environnement ») n'est pas démontrée, mais le biais de date (nu du 2026-09-16 contre gouverné du 2026-09-29) suffit à expliquer l'écart sans cause nouvelle.

## 3. Par tâche

Toutes les tâches sont à 3/3 pour les deux bras (20 tâches, dont `javascript/transpose`, `go/palindrome-products` et `rust/scale-generator`
qui échouaient dans les lots précédents). Voir `report.md` du workspace pour la table complète.

## 4. Défaut du classifieur trouvé par ce lot

La première exécution de `excursions.py` sur le lot L donnait 46/60 excursions (76,7 %),, dont de nombreux runs « sans gate » à 2 appels outils. Faux : l'agent
(Opus 5.5) enchaîne désormais l'écriture de la solution et `grimoire standard gate check` dans le **même** appel Bash (`cat > f <<'EOF' … EOF` puis la commande gate).
Le classifieur coupait la commande à la première occurrence de `<<` pour ignorer les mentions dans un heredoc, et perdait donc l'appel enchaîné après lui.
Correctif : retirer les **corps** de heredoc et chercher la commande dans le reste ; ajout de `gate_trace` (verdict vert/rouge par appel gate, par `tool_use_id`),
des champs `benign`, `last_gate_green` et des compteurs `excursions_benign` / `excursions_costly`. Les lots J et K donnent les mêmes 18/60 et 16/60 qu'avant
(vérifié), donc leurs chiffres publiés sont inchangés. Tests : `tests/unit/test_bench_excursions.py` (gate enchaîné après heredoc, excursion bénigne contre coûteuse).

## 5. Cause de la régression `rust/scale-generator` (lot K) : contamination du lot J

Le 3/3 du lot J n'était pas une capacité du bras gouverné, et le 0/3 du lot K n'était pas une régression du kit.

- **Écartées** : `polyglot-benchmark` est au même commit `7e0611e7` aux lots J, K et L ; la suite masquée est identique (`hidden-tests/tests/scale-generator.rs`, même taille).
  Les versions de `cargo`/`rustc` aux lots J et K ne sont pas tracées : non testable, mais inutile ci-dessous.
- **Cause établie** : `prepare_task_repo` n'efface pas le dossier de destination. Au lot J, 30 des 60 dossiers `kit-gov/run*` existaient déjà (campagnes F à H),
  avec les fichiers de test que le harnais y copie pour la vérification finale, puis un second `git init` + commit « task: état initial » qui les a embarqués. Preuve :
  `git log` de `bench-j/.../rust__scale-generator/kit-gov/run0` montre deux commits « état initial », le second ajoute `tests/scale-generator.rs`, et la transcription J
  ouvre sur `cat tests/scale-generator.rs`, puis « Les 17 tests passent ». Les 30 runs concernés sont les 15 de go et les 15 de rust (10 tâches), 15 runs
  javascript et python étant sains. Les lots K et L (workspaces neufs) : 1 seul commit partout, `ls tests` → `no tests dir`.
- **Mécanisme du 0/3 au lot K** : sans test visible, l'agent a produit une gamme chromatique de 12 notes ; la suite masquée en attend 13 (octave comprise), `chromatic_scale_with_sharps`
  échoue (`left` 12 notes, `right` 13), reproduit en rejouant la solution du run K contre la suite masquée. L'énoncé ne précise pas ce point. Au lot L, les 6 runs (nu et kit-gov)
  passent sans test visible : le modèle plus récent retient la bonne interprétation (non analysé plus avant).
- Cohérence : `nu`, `ecc` et `kit` (dossiers frais du lot F) étaient à 1/3, 0/3, 0/3 sur cette tâche.
- **Portée** : le 96,7 % de succès du lot J (et le « seul critère atteint » du lot K sur ce point) est **surévalué** : au moins 30 runs sur 60 ont vu les tests.
  Seule la tâche `scale-generator` a été vérifiée en détail ; l'effet sur les 9 autres tâches contaminées n'est pas chiffré. Le défaut du harnais n'est pas corrigé par cette PR
  (hors périmètre d'un lot de mesure) : voir §7.

## 6. Verdict, sans arbitrage

| Critère de #642 | Cible | Lot J | Lot K | **Lot L** | Atteint |
|---|---|---:|---:|---:|:---:|
| Part des runs « avec excursion » (définition binaire) | < 10 % | 30,0 % | 26,7 % | **16,7 % (10/60)** | Non |
| Part des excursions **coûteuses** (distinction demandée) | < 10 % | 28,3 % | 10,0 % | **8,3 % (5/60)** | Oui |
| Coût médian/tâche résolue | ≤ 105 % du nu | ≈112 % | ≈122 % | **≈124 %** | **Non** |
| Succès | ≥ 96,7 % | 96,7 % (surévalué, §5) | 90,0 % | **100 %** (nu 100 %) | Oui |
| `gate check` vert au dernier appel | 60/60 | 60/60 | 60/60 | **60/60** | Oui |

Synthèse J → K → L (`kit-gov`, nu du même lot sauf J et K dont le nu date du 2026-09-16) :

| | Tours médians | Coût médian en % du nu | Succès | pass^k | Excursions (coûteuses) |
|---|---:|---:|---:|---:|---:|
| J | 6,0 | ≈112 % | 96,7 % | 95 % | 30,0 % (28,3 %) |
| K | 7,0 | ≈122 % | 90,0 % | 85 % | 26,7 % (10,0 %) |
| L | 3,0 | ≈124 % | 100 % | 100 % | 16,7 % (8,3 %) |

À la lettre, le critère de coût n'est pas atteint, donc l'issue n'est pas close par ce lot. Le biais de date est levé : l'écart de coût n'était pas un artefact du nu
ancien, il persiste à ≈ +0,04 $ par run, sur des runs « mandat pur » comme sur les autres. Les excursions, elles, ne sont plus le problème : les coûteuses passent sous 10 % et
portent 30 % d'un surcoût qui est surtout le prix fixe du mandat (contexte du projet gouverné, appel gate).

## 7. Décision proposée (à Guilhem)

1. **Critère de coût** : à 124 % avec un surcoût fixe, ≤ 105 % revient à retirer du contexte ou de l'appel gate ; deux voies : (a) ouvrir un lot sur le coût fixe du bras gouverné
   (taille de `CLAUDE.md`, skills attachés, sortie du gate), mesuré par le surcoût médian « mandat pur » ; (b) re-baser le critère sur ce surcoût (par exemple en valeur absolue par run) plutôt que
   sur un ratio qui dépend du modèle et de la difficulté. Décision à prendre avant tout nouveau rejeu.
2. **Excursions** : retenir la distinction coûteuse/bénigne du script comme définition du critère de l'issue (8,3 % atteint) ou garder la définition binaire (16,7 % non atteint).
3. **Contamination du harnais** : ouvrir une issue pour que `prepare_task_repo` vide le dossier de destination (ou le refuse s'il existe), puis considérer le 96,7 % du lot J comme non fiable.
   Le chantier est dans le banc, donc produit.
4. **Banc saturé** : à 100 % pour les deux bras avec Opus 5.5, le critère de succès ne mesure plus rien ; épingler le modèle dans le harnais (`--model`) et durcir la sélection de tâches si un
   critère de succès doit rester discriminant.
5. Dégel de la phase 2bis : non proposé sur ce seul lot (critère de coût non atteint).

## 8. Limites

- Modèle non épinglé et différent de J et K (§1.3) : comparaisons absolues invalides, rapports seulement indicatifs.
- k=3 sur 20 tâches, une seule journée ; banc saturé, aucun intervalle de confiance utile sur le succès.
- Authentification `oauth-copy` : coûts rapportés par Claude Code, pas une facture.
- Un run isolé à 566 s ; non investigué (cause inconnue, sans effet sur le résultat).
- Contamination du lot J chiffrée en nombre de dossiers (30/60) mais vérifiée en détail sur une seule tâche.
- Versions de `cargo`/`rustc` aux lots J et K non tracées.
