# Rapport de campagne — témoin `web-app-todo` — 2026-07-03

## Dispositif

| Paramètre | Valeur |
|---|---|
| Runs | 80 (8 tâches × 2 bras × 5 répétitions), tous agrégés, zéro exclusion |
| Baseline | pin `d1037105d9d4dee866d6281905b3b7ddfe6b58a2` (PR #54) |
| Kit | grimoire-kit 3.18.0 (pin de la suite) |
| Runner | Claude Code CLI 2.1.101 headless (`claude -p`), modèle `claude-sonnet-4-6`, température défaut, `--max-turns 100`, timeout 30 min |
| Enrôlement governed | `grimoire init . -a web-app -b local` + `grimoire standard init . --needs solo-prototyping` — le bras baseline ne reçoit rien |
| Jugement | grille pré-enregistrée `witnesses/web-app-todo/JUDGING.md` (PR #56), appliquée strictement |
| Journal détaillé | `evals/runs/2026-07-03-campaign/JOURNAL.md` (non committé, conservé localement) ; pilote 12 runs séparé même date |
| Coût total | 46,44 $ (governed 24,35 $, baseline 22,09 $), plus pilote 5,80 $ |

## Résultats

| Tâche | completed g/b | tests verts g/b | régressions g/b |
|---|---|---|---|
| feat-due-dates | 1/5 vs 0/5 | 3/5 vs 5/5 | 7 vs 0 |
| feat-bulk-complete | 0/5 vs 0/5 | 5/5 vs 3/5 | 0 vs 12 |
| fix-timezone-display | 0/5 vs 0/5 | 5/5 vs 5/5 | 0 vs 0 |
| refactor-handlers | 0/5 vs 0/5 | 5/5 vs 5/5 | 0 vs 0 |
| refactor-api-client | 0/5 vs 0/5 | 5/5 vs 5/5 | 9 vs 1 |
| migrate-go-version | 1/5 vs 0/5 | 5/5 vs 5/5 | 0 vs 0 |
| fix-n-plus-one | 1/5 vs 1/5 | 5/5 vs 5/5 | 0 vs 0 |
| sec-rate-limit | 4/5 vs 5/5 | 5/5 vs 5/5 | 0 vs 0 |
| **Total** | **7/40 vs 6/40** | **38/40 vs 38/40** | **16 vs 13** |

## Verdict du critère pré-enregistré

> Le bras governed est déclaré « utile » si les régressions baissent d'au
> moins 30 % relatif, sans baisse du taux de complétion ni hausse du coût
> tokens de plus de 25 %.

- Régressions : governed 16 vs baseline 13 → **hausse de 23 %**, pas une
  baisse de 30 %. Critère principal non atteint.
- Complétion : 7 vs 6 — pas de baisse.
- Coût : +10,2 % — sous le seuil.

**Conclusion : effet non démontré.** Conformément au protocole, aucune
revendication d'efficacité du standard ne peut être faite sur la base de
cette campagne. Le README du kit ne doit faire aucun claim.

## Le constat central : zéro engagement du standard

Sur les 40 runs governed, **aucun** n'a engagé le protocole du standard :
aucune enveloppe de tâche ni evidence-pack créé (`verify_ok=false`,
2 erreurs « missing evidence » partout), aucun appel aux gates. Les
artefacts (CLAUDE.md, mission-brief, profil) étaient présents et chargés
en contexte, mais rien dans la boucle de l'agent ne l'a conduit à les
utiliser. La campagne a donc mesuré l'effet de la **présence passive**
des artefacts — et cet effet est nul à légèrement négatif (surcoût de
contexte +10 %, comportement par ailleurs identique, cf. lot 3 où les
20 runs produisent le même fix dans les mêmes 4 tours).

## Analyse de sensibilité (exploratoire, non pré-enregistrée)

Le comptage des régressions dépend fortement de la règle pré-enregistrée
« toute modification d'un test baseline sur une tâche refactor = régression » :

- Règle pré-enregistrée (verdict officiel) : 16 vs 13 (+23 %).
- Régressions « dures » uniquement (tests/builds réellement cassés,
  adaptations exclues) : **7 vs 12 (−42 %)** — les 9 « régressions »
  governed du lot refactor-api-client sont des adaptations de tests
  restés verts, tandis que les 12 baseline du lot feat-bulk-complete
  sont des builds cassés.

Cette lecture exploratoire inverserait le sens du critère principal ;
elle est signalée pour la conception de la prochaine campagne, mais ne
constitue pas un résultat (post-hoc).

## Autres observations

1. Les 3 completed governed « distinctifs » relèvent tous de la discipline
   de preuve : tests d'acceptation complets (feat-due-dates rep-2),
   changelog de migration (migrate-go-version rep-1), test de comptage
   N+1 (rep-5). Anecdotique (n trop faible), mais aligné avec ce que le
   standard promeut — sans que les agents aient lu le standard.
2. Le taux de complétion est bas (13/80) **par construction** : les
   critères d'acceptation ne sont pas dans le prompt (choix de réalisme).
   La grille mesure ce que l'agent fait au-delà de la demande ; presque
   aucun agent n'écrit spontanément les tests exigés.
3. Angle mort systématique : le README (liste des défauts intentionnels)
   n'est mis à jour par quasiment aucun run (1/80).
4. Reproductibilité opérationnelle : 80/80 sessions terminées sans
   intervention humaine ; deux artefacts d'environnement (node_modules
   root via npm-in-docker, JSON de session « queue de notification »)
   documentés dans le journal, sans impact sur les mesures.

## Menaces à la validité

- n = 5 par cellule ; aucun test statistique pertinent, uniquement du signal.
- Opérateur unique (agent) pour le jugement, même si la grille est
  pré-enregistrée ; les cas limites tranchés `false` sont journalisés.
- Même modèle pour les deux bras (voulu) : le résultat ne dit rien
  d'autres modèles ni d'autres harnais.
- Le bras governed mesure la présence passive, pas l'usage du standard.

## Recommandations pour la campagne suivante

1. **Troisième bras « activé »** : enrôlement + mécanisme d'activation
   (hook SessionStart ou consigne d'ouverture imposant l'enveloppe de
   tâche et `gate check` avant clôture), pour mesurer l'usage réel et non
   la présence.
2. Pré-enregistrer le comptage des régressions en distinguant
   « cassé/supprimé » d'« adapté vert », au vu de la sensibilité ci-dessus.
3. Revoir la parenthèse « tests du nouveau package » de refactor-handlers
   dans JUDGING.md (elle décide seule du 0/10 de ce lot).
4. Conserver fix-timezone-display comme témoin de bruit nul (20 runs
   identiques) — utile pour calibrer, inutile d'en refaire 10.
