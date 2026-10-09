# Errata : contamination du lot J par les tests cachés

**Date** : 2026-10-09  
**Lot affecté** : Lot J (2026-09-18, `docs/bench/rejeu-lot-j-2026-09-18.md`, issue #582)  
**Cause établie par** : le rejeu du lot L (#693, `docs/bench/rejeu-lot-l-2026-10-02.md` § 5)  
**Issue** : #694  
**Statut** : garde livrée par la PR W1-08a (refus du dossier sale, contrôle du dépôt préparé avant tout appel au modèle). La lecture de tests cachés pendant le run n'est pas couverte : W1-08b.

## Constat

Le harnais du banc à trois bras réutilisait les dossiers de préparation des tâches sans les nettoyer. Sur le lot J, 30 des 60 runs `kit-gov` (les 15 de go et les 15 de rust, soit 10 tâches) tournaient dans des dossiers déjà présents, issus des campagnes F à H :

- deux commits « état initial » au lieu d'un dans l'historique git ;
- les fichiers de test, copiés là pour la vérification finale, embarqués par le second commit : les tests cachés devenaient visibles ;
- sur `rust/scale-generator`, la transcription du lot J s'ouvre sur `cat tests/scale-generator.rs`.

## Impact sur les résultats

- **Succès surévalué** : le 96,7 % du lot J est surévalué, au moins 30 runs sur 60 ayant vu les tests.
- **Fausse régression** : `rust/scale-generator` passe de 3/3 (lot J) à 0/3 (lot K). Le 0/3 vient d'une ambiguïté de l'énoncé (gamme de 12 notes produite, 13 attendues par la suite masquée), pas d'une régression du kit. Au lot L, les 6 runs (`nu` et `kit-gov`) passent sans test visible.
- **Portée non chiffrée** : seule `scale-generator` a été vérifiée en détail. L'effet sur les 9 autres tâches contaminées n'est pas chiffré.
- **Bras** : seul `kit-gov` est concerné au lot J (dossiers frais pour `nu`, `ecc`, `kit`).

## Remède

PR W1-08a (issue #694) :

1. `prepare_task_repo` refuse un dossier non vide (`FileExistsError`).
2. `assert_task_repo_clean` est appelée dans `_run_one` après la préparation et avant `run_environment` et `claude` : elle lève `ContaminatedTaskRepoError` si un fichier de `test_files` est suivi par git ou si l'historique ne compte pas exactement un commit. Le run est alors enregistré à 0 $ avec `terminated_reason="contaminated"`.
3. `_run_one` et `_do_dry_run` effacent le résidu d'un run interrompu avant de le rejouer (la garde 2 reste le filet).

## Limites

- Les lots K et L sont sains parce que leurs workspaces étaient neufs (lot L § 5), pas grâce à ce remède, qui leur est postérieur.
- Le remède ne détecte pas la lecture de tests cachés pendant le run (glob, outil, accès indirect) : W1-08b.
- Le rejeu du lot J n'est pas fait ; le vrai score de `kit-gov` au lot J est inférieur à 96,7 %, sans valeur mesurée.

## Références

- Issue #694 : https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/694
- Rejeu lot L : `docs/bench/rejeu-lot-l-2026-10-02.md` (§ 1.3, § 5)
