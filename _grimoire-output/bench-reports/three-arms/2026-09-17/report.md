# Banc à trois bras — Claude Code nu / + ecc / + grimoire-kit

Issue [Grimoire-kit#551](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/551) — plan produit
2026-Q4 (`docs/plan-2026-q4.md`), phase 1, lot 1.1. Protocole complet :
`docs/bench-three-arms.md` (branche `feat/bench-three-arms`, PR #567). Campagne réelle du
2026-09-16/17, seconde tentative — la première avait saturé un tmpfs `/tmp` de 32 Go faute de
`--workspace` explicite et a été perdue à l'expiration de la session.

Généré le 2026-09-17T05:25:51Z · graine `551` · 20/20 tâches rejouées · k=3 · 180/180 runs joués
(aucun arrêt anticipé — les IC bootstrap du bras `kit` n'ont jamais été disjoints de ceux de `nu`
et `ecc`, cf. §4).

Benchmark de tâches : [Aider polyglot-benchmark](https://github.com/Aider-AI/polyglot-benchmark)
(exercices Exercism, 5 par langue sur Python/JavaScript/Go/Rust, MIT par piste). Paquet
[ecc](https://github.com/affaan-m/ECC) (MIT), commit `8321021c54d670126ce3b2969d5deb880b4b0c2a`.

## 1. Coût attendu vs réel

Extrapolé du pilote (2 tâches × 3 bras × 1 rejeu, 6 appels réels) : **141,19 $**. Coût réel
constaté sur la campagne complète : **151,85 $** (×1,08 — bien en-deçà du seuil d'alerte ×3 ; aucune
alerte coût, disque ou sécurité n'a été émise sur toute la campagne).

| Bras | Coût attendu (pilote × runs planifiés) | Coût réel | Écart |
|---|---|---|---|
| nu | 17,10 $ | 27,04 $ | ×1,58 |
| ecc | 38,73 $ | 45,30 $ | ×1,17 |
| kit | 85,37 $ | 79,51 $ | ×0,93 |
| **Total** | **141,19 $** | **151,85 $** | **×1,08** |

Le pilote (n=2 par bras) sous-estimait `nu` et surestimait légèrement `kit` — bruit attendu sur un
échantillon de 2 tâches Python, pas un signal.

## 2. Par bras

| Bras | Runs | Tâches | Succès | IC95% succès | pass^k (k=3) | IC95% pass^k | Temps médian | Coût médian/tâche résolue | Coût total |
|---|---|---|---|---|---|---|---|---|---|
| nu | 60 | 20 | 91,7 % | [85,0 %, 98,3 %] | 90 % | [75 %, 100 %] | 64 s | 0,399 $ | 27,04 $ |
| ecc | 60 | 20 | 93,3 % | [86,7 %, 98,3 %] | 90 % | [75 %, 100 %] | 61 s | 0,629 $ | 45,30 $ |
| kit | 60 | 20 | 90,0 % | [81,7 %, 96,7 %] | 85 % | [70 %, 100 %] | 189 s | 1,247 $ | 79,51 $ |

Aucun run terminé par timeout ou détection de boucle sur les 180 — tous les échecs sont des
`terminated_reason: completed` avec tests cachés rouges (échecs de correction, pas de robustesse du
harnais).

## 3. Par tâche (succès / 3 rejeux)

| Tâche | Langue | nu | ecc | kit |
|---|---|---|---|---|
| go/bottle-song | go | 3/3 | 3/3 | 3/3 |
| go/error-handling | go | 3/3 | 3/3 | 3/3 |
| go/palindrome-products | go | 0/3 | 2/3 | 1/3 |
| go/pov | go | 3/3 | 3/3 | 3/3 |
| go/simple-linked-list | go | 3/3 | 3/3 | 3/3 |
| javascript/affine-cipher | javascript | 3/3 | 3/3 | 3/3 |
| javascript/go-counting | javascript | 3/3 | 3/3 | 3/3 |
| javascript/list-ops | javascript | 3/3 | 3/3 | 3/3 |
| javascript/queen-attack | javascript | 3/3 | 3/3 | 3/3 |
| javascript/transpose | javascript | 3/3 | 3/3 | 2/3 |
| python/bowling | python | 3/3 | 3/3 | 3/3 |
| python/list-ops | python | 3/3 | 3/3 | 3/3 |
| python/proverb | python | 3/3 | 3/3 | 3/3 |
| python/react | python | 3/3 | 3/3 | 3/3 |
| python/zipper | python | 3/3 | 3/3 | 3/3 |
| rust/forth | rust | 3/3 | 3/3 | 3/3 |
| rust/poker | rust | 3/3 | 3/3 | 3/3 |
| rust/react | rust | 3/3 | 3/3 | 3/3 |
| rust/scale-generator | rust | 1/3 | 0/3 | 0/3 |
| rust/two-bucket | rust | 3/3 | 3/3 | 3/3 |

`rust/scale-generator` échoue sur les **trois** bras (off-by-one partagé, voir §5) — un problème de
tâche, pas de gouvernance. `go/palindrome-products` et `javascript/transpose` sont les deux tâches
où `kit` décroche des deux autres bras.

## 4. Verdict selon le critère d'arrêt du plan (§3, `docs/plan-2026-q4.md`)

> Si le kit est moins bon que l'hôte nu **à la fois** en réussite **et** en coût : on arrête les
> extras du plan et on retravaille le cœur (dispatch, contexte injecté, mémoire) avant toute parité.
> Si réussite égale et coût ≤ 70 % de l'hôte nu : le plan continue.

- **Réussite** : `kit` 90,0 % contre `nu` 91,7 % (pass^k : 85 % contre 90 %) — `kit` est
  numériquement en-dessous sur les deux mesures, mais les IC95% se chevauchent largement
  ([81,7–96,7] vs [85–98,3] ; [70–100] vs [75–100]) : la différence de réussite n'est **pas**
  statistiquement significative sur cet échantillon.
- **Coût** : `kit` coûte 79,51 $ contre 27,04 $ pour `nu` — **294 %** du coût de `nu`, très loin du
  plafond de 70 % pour continuer. Le coût médian par tâche résolue confirme l'écart (1,247 $ contre
  0,399 $, ×3,1) et n'est pas un artefact d'un run extrême isolé (médiane, pas moyenne). Le temps
  médian suit la même pente : 189 s contre 64 s (×3,0).
- **Verdict : ARRÊT.** Le critère de coût déclenche seul l'arrêt (largement au-delà du seuil, sans
  ambiguïté statistique) ; le critère de réussite penche dans le même sens sans être significatif
  isolément. Conclusion honnête : sur ce banc, `grimoire-kit` gouverné par défaut ne livre **pas**
  au moins aussi bien que Claude Code nu — il coûte ~3× plus cher et ~3× plus de temps pour un taux
  de succès égal ou légèrement inférieur. Recommandation : suspendre les extras du plan 2026-Q4 et
  retravailler le cœur (dispatch, contexte injecté au démarrage, mémoire) avant de rejouer ce banc.

## 5. Trois causes principales d'échec du bras `kit`

Traces (`--output-format stream-json`) et tests cachés rejoués manuellement pour les 6 runs `kit`
en échec (2× `go/palindrome-products`, 1× `javascript/transpose`, 3× `rust/scale-generator`) :

1. **Off-by-one partagé sur `rust/scale-generator` (3/6 échecs `kit`, mais aussi 3/3 `ecc` et 2/3
   `nu`)** — la gamme chromatique générée s'arrête à la note précédant la tonique
   (`["C", ..., "B"]`, 12 notes) alors que le test attend la tonique répétée en fin de séquence
   (`["C", ..., "B", "C"]`, 13 notes). Une ambiguïté de l'énoncé Exercism plus qu'un défaut propre à
   `kit` : les trois bras s'y cassent, `kit` n'y échoue pas plus que les autres. Ne pas compter comme
   un signal spécifique à la gouvernance kit.
2. **Correction fonctionnelle mais texte d'erreur non conforme au test caché
   (`go/palindrome-products`, 2/3 runs `kit`)** — `Products(4, 10)` et `Products(10, 4)` retournent
   la bonne erreur au bon moment, mais avec un message reformulé et plus explicite (`"min must be
   <= max (fmin > fmax): 10 > 4"`) au lieu du préfixe exact attendu par le test
   (`"fmin > fmax"` / `"no palindromes"`). L'agent conclut au succès sur la base de ses propres
   gates (`gate check --strict`, `verify .` — outillage de preuve du kit, qui ne voit jamais les
   tests cachés) plutôt que sur l'exécution de la suite réelle, qu'il lui est explicitement interdit
   de lancer (elle n'existe pas encore dans son dépôt). Écart entre « le kit certifie que c'est
   prouvé » et « le test caché est vert ».
3. **Edge case non couvert malgré un agent qui se déclare confiant (`javascript/transpose`, 1/3
   runs `kit`)** — `input.split is not a function` sur le cas « chaîne vide » : l'implémentation
   suppose une entrée toujours découpable en lignes sans vérifier le type/la forme dégénérée. Le
   dernier message de l'agent annonce des « gates verts » et une implémentation « vérifiée » alors
   qu'aucun test (caché ou non) ne pouvait être exécuté pour vérifier ce cas précis — la confiance
   affichée dans les 189 s et 19 tours mobilisés en médiane sur ce bras porte sur la lecture de
   l'énoncé et le raisonnement, pas sur une preuve d'exécution qu'il n'a pas les moyens de produire.

Fil conducteur des causes 2 et 3 : le surcroît de temps/coût du bras `kit` (gates, doctrine de
preuve, contexte injecté) ne se traduit pas ici par une meilleure détection de ses propres angles
morts — il produit une conviction de correction (« gates verts », « vérifié ») déconnectée des tests
réellement cachés à l'agent, ce qui est cohérent avec le retravail du cœur recommandé au §4 (contexte
injecté, dispatch) plutôt qu'un ajustement cosmétique du prompt.

## 6. Espace disque

- Garde-fou (< 5 Go libres sur le volume de travail) : jamais déclenché — minimum observé
  489,7 Go libres sur `/mnt/Travail` (volume 1,8 To), largement au-dessus du seuil sur toute la
  campagne.
- Pic d'occupation propre au banc (`_scratch/bench`, échantillonné toutes les 30 s) : **≈ 4,0 Go**
  (4 020 285 440 octets), atteint en toute fin de campagne complète. Le nettoyage `target/`/
  `node_modules/` après chaque run (patch local, §7) a tenu l'empreinte à quelques Go sur 180 runs
  Rust/Node/Go/Python, contre la saturation d'un tmpfs de 32 Go lors de la première tentative.
- Rien écrit sous `/tmp` : `TMPDIR`/`TMP`/`TEMP` et les caches npm/cargo/Go
  (`NPM_CONFIG_CACHE`, `CARGO_HOME`, `GOPATH`/`GOCACHE`/`GOMODCACHE`) redirigés sous
  `_scratch/bench/caches/` pour toute la durée de la campagne.
- Aucun identifiant Claude Code oublié sous un `HOME` isolé en fin de campagne
  (`find_leftover_credentials` — vérifié après le pilote et après la campagne complète).

## 7. Modification du harnais à reporter en PR de suivi

`scripts/bench/three_arms.py` expose déjà `--workspace` (aucun changement nécessaire pour rediriger
clones/HOME isolés/dépôts de tâche/rapports hors de `/tmp`). Patch local ajouté sur cette campagne
(non poussé sur `feat/bench-three-arms`, PR #567 en fusion au moment de la campagne) :

- `free_space_gb()` + garde-fou avant chaque run (< 5 Go libres → arrêt propre, rapport partiel
  écrit avec les records déjà accumulés, jamais un plantage par disque plein en cours de run).
- `cleanup_run_dir()` appelé après chaque `_run_one()` : supprime `target/` et `node_modules/` du
  dépôt de tâche une fois succès/coût/tokens déjà extraits — n'a jamais été déclenché par le
  garde-fou disque sur cette campagne (marge confortable), mais tient l'empreinte disque à ~4 Go au
  lieu de plusieurs dizaines.

Ni l'un ni l'autre n'a été nécessaire pour éviter un incident sur cette campagne (marge de 489 Go),
mais les deux préviennent la récidive du symptôme de la première tentative perdue. À proposer en PR
séparée une fois #567 mergée.

## 8. Reproduire

```bash
cd <worktree grimoire-kit sur feat/bench-three-arms ou main>
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e . pytest pyyaml jsonschema mcp
python scripts/bench/three_arms.py --report-only --workspace <chemin> --report-dir <sortie>
```

`workspace/state/selection.json` (tâches tirées, commit ecc), `results.jsonl` (180 lignes, une par
run) et `expected_cost.json` de cette campagne ne sont pas publiés ici (working directory jetable,
supprimé en fin de campagne comme prévu par le protocole) — seuls `report.md`/`report.json` sont
conservés, conformément à `docs/bench-three-arms.md` §9.
