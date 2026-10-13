# Errata : contamination du lot J par les tests cachés

**Date** : 2026-10-09  
**Lot affecté** : Lot J (2026-09-18, `docs/bench/rejeu-lot-j-2026-09-18.md`, issue #582)  
**Cause établie par** : le rejeu du lot L (#693, `docs/bench/rejeu-lot-l-2026-10-02.md` § 5)  
**Issue** : #694  
**Statut** : garde livrée par la PR W1-08a (refus du dossier sale, contrôle du dépôt préparé avant tout appel au modèle, arrêt de la campagne à 0 $ si le dépôt est contaminé). La lecture de tests cachés pendant le run n'est pas couverte : W1-08b.

## Constat

Le harnais du banc à trois bras réutilisait les dossiers de préparation des tâches sans les nettoyer. Sur le lot J, 30 des 60 runs `kit-gov` (les 15 de go et les 15 de rust, soit 10 tâches) tournaient dans des dossiers déjà présents, issus des campagnes F à H :

- deux commits « état initial » au lieu d'un dans l'historique git ;
- les fichiers de test, copiés là pour la vérification finale, embarqués par le second commit : les tests cachés devenaient visibles ;
- sur `rust/scale-generator`, la transcription du lot J s'ouvre sur `cat tests/scale-generator.rs`.

## Impact sur les résultats

- **Succès surévalué** : le 96,7 % du lot J est surévalué, au moins 30 runs sur 60 ayant vu les tests.
- **Fausse régression** : `rust/scale-generator` passe de 3/3 (lot J) à 0/3 (lot K). Le 0/3 vient d'une ambiguïté de l'énoncé (gamme de 12 notes produite, 13 attendues par la suite masquée), pas d'une régression du kit. Au lot L, les 6 runs (`nu` et `kit-gov`) passent sans test visible.
- **Portée non chiffrée** : seule `scale-generator` a été vérifiée en détail. L'effet sur les 9 autres tâches contaminées n'est pas chiffré.
- **Bras** : seul `kit-gov` a été rejoué au lot J (`rejeu-lot-j-2026-09-18.md`) ; `nu`, `ecc` et `kit` sont repris du lot F (dossiers frais à l'époque, lot L § 5), donc non contaminés mais mesurés à une autre date.

## Remède

PR W1-08a (issue #694) :

1. `prepare_task_repo` refuse un dossier non vide (`FileExistsError`).
2. `assert_task_repo_clean` est appelée dans `_run_one` après la préparation et avant `claude` : elle lève `ContaminatedTaskRepoError` si un fichier de `test_files` est présent dans le dépôt (suivi, non suivi ou ignoré par git), si l'un d'eux est suivi par git (même absent du disque) ou si l'historique ne compte pas exactement un commit. L'erreur n'est pas enregistrée comme un échec : `main` arrête la campagne (code 1, 0 $ dépensé pour ce run, gardes de sécurité de fin de campagne exécutées) sans rien écrire dans `results.jsonl`, de sorte que la clé reste rejouable avec `--resume` et ne pèse pas dans les taux de succès. `--dry-run` s'arrête de la même façon.
3. `_run_one` et `_do_dry_run` effacent sans condition le dossier de run existant avant de le préparer (la garde 2 reste le filet). Sans `--resume`, `main` signale sur stderr l'effacement du dossier d'un run déjà enregistré dans `results.jsonl` ; l'effacement lève les protections en lecture seule des objets git (Windows) et échoue bruyamment si le dossier survit.

## Limites

- Les lots K et L ne sont pas prouvés sains. Leurs dossiers de préparation étaient neufs (lot L § 5), ce qui écarte la contamination du lot J (deux commits, tests suivis), pas la lecture de la suite masquée pendant le run. Le lot K contient au moins un succès obtenu après lecture de cette suite : `python/bowling`, bras `kit-gov`, run 2 (transcription `bench-k/workspace/tasks/python__bowling/kit-gov/run2.stream.jsonl`, ligne 44 : `cat` de `hidden-tests/bowling_test.py` ; ligne 45 : le contenu de la suite est rendu), enregistré `success=True` dans `state/results.jsonl` le 2026-09-29T22:26:27. Le lot L n'a pas été relu pour cette lecture.
- La disposition du banc expose deux choses à l'agent, et le remède n'en traite aucune : `workspace/tasks/<tâche>/hidden-tests/` (frère du dossier `<bras>/run<i>`, atteignable par `../../hidden-tests/`) et les `test_files` que `run_hidden_tests` copie dans le dossier du run pour la vérification finale et ne retire jamais, donc présents dans les `run<i>` précédents du même bras (`../run0/`, `../run1/`). W1-08b doit couvrir ces deux expositions (sortir `hidden-tests/` de l'arbre `tasks/`, retirer les `test_files` du `run<i>` après la vérification, ou interdire l'accès).
- Le remède ne détecte pas la lecture de tests cachés pendant le run (glob, outil, accès indirect) : W1-08b.
- Le rejeu du lot J n'est pas fait ; le vrai score de `kit-gov` au lot J est inférieur à 96,7 %, sans valeur mesurée.

## Références

- Issue #694 : https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/694
- Rejeu lot L : `docs/bench/rejeu-lot-l-2026-10-02.md` (§ 1.3, § 5)
