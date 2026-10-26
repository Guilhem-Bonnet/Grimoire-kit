---
audience: référence
---

# L'intendant du projet

`grimoire steward` fait l'état des lieux d'un projet **en lecture seule** :
ce qui reste réellement à faire, ce qui est déjà livré, ce qui est en double,
quelles PR ont été abandonnées. La carte est calculée par du code ; le
jugement est celui d'un agent qui suit la skill `grimoire-steward` ; chaque
preuve citée par l'agent est revérifiée par du code.

Rien n'est écrit au Mission Ledger, au dépôt git ni sur GitHub. Les seules
écritures sont sous `_grimoire-output/steward/`, un dossier qui s'ignore
lui-même (son propre `.gitignore`) : `git status` ne bouge pas.

## `grimoire steward map`

```bash
grimoire steward map [--json] [--cible origin/main] [--inactivite-heures 48]
                     [--locales-seulement] [--sans-sonde] [--repo OWNER/REPO]
```

La carte, schéma `grimoire-steward-map/v1`, aussi écrite dans
`_grimoire-output/steward/map.json`. Déterministe, sans LLM.

| Section | Contenu |
|---|---|
| `taches` | tâches du Mission Ledger : état, critères, dernière activité, claim, session et vivacité (`vivant`, `inactif`, `inconnu`, `terminé`), issues liées |
| `branches` | branches locales et distantes du remote de la cible : dernier commit, avance/retard, fusion, conflit, orpheline |
| `worktrees` | `git worktree list` : chemin, branche, verrouillé, élagable, sale (fichiers modifiés ou non suivis, lu sans verrou optionnel) |
| `prs` | PR ouvertes (`gh`) : CI, `mergeable`, `mergeStateStatus`, dernière activité, auteur, tâches et sessions liées, conflit |
| `issues` | issues ouvertes : numéro, titre, labels, dernière activité |
| `signaux` | indices calculés, jamais des verdicts |

**Fusion.** Une branche sans commit d'avance est `vide` (rien à fusionner,
ce n'est pas une preuve de livraison). Elle est `fusionnee` si une PR
fusionnée **dans la cible** avait sa tête (`pr #N` : base = branche cible et
commit de fusion sur la cible, une PR empilée ne compte pas), ou si
`git merge-tree` dans la cible ne changerait rien (`fusion_sans_apport`). Si
son arbre est celui d'un commit **ancien** de la cible, elle est
`etat_anterieur` : la fusionner reviendrait en arrière. Sinon
`non_fusionnee`, avec le conflit que la fusion produirait : `aucun`,
`changelog_seul` ou `autre`. `git merge-tree` écrit ses arbres dans un
magasin d'objets jetable, propre à l'exécution, jamais dans `.git/objects`.
Le conflit d'une PR est calculé sur sa tête (`headRefOid`, rapportée dans
`conflit.sha`) ; absente du dépôt local, il est « non mesuré ».

**Signaux.** `pr_inactive_sans_session_vivante` (aucune activité depuis
`--inactivite-heures` et aucune session liée `vivant` ou `inactif`),
`tache_ouverte_issue_fermee`, `tache_ouverte_citee_par_commit` (un identifiant
homonyme peut tromper : à vérifier dans le code), `conflit_changelog_seul`,
`conflit_autre`, `branche_orpheline` (`task/<id>` sans tâche vivante),
`branche_fusionnee_sans_etre_ancetre`, `branche_etat_anterieur`,
`worktree_elagable`, `worktree_sale`.

**Non mesuré, partiel.** Sans `gh`, hors ligne ou sans cible, la section
concernée porte `"mesure": false` et sa raison, un champ vaut `"non mesuré"` ;
la carte n'échoue pas. Une liste lue jusqu'à sa limite (200 PR, 500 issues,
1 000 PR fusionnées, 5 000 commits de la cible) passe à `"mesure": "partielle"`
ou figure dans `troncatures` : ce qui n'y est pas n'est pas prouvé absent, et
aucun verdict ne s'établit sur cette absence (une fusion non vue laisse la
branche `non_fusionnee`, donc jamais `livree`). `--sans-sonde` n'interroge pas l'hôte (`claude agents`) : la
vivacité ne repose alors que sur les journaux de session.

## `grimoire steward verdicts`

```bash
grimoire steward verdicts --file verdicts.json [--json] [--cible origin/main]
```

Revérifie un fichier `grimoire-steward-verdicts/v1` et écrit
`_grimoire-output/steward/validation.json` (`grimoire-steward-validation/v1`).
Code 0 si tout est établi, 1 si un verdict est rejeté, 2 si le fichier est
illisible.

```json
{
  "$schema": "grimoire-steward-verdicts/v1",
  "verdicts": [
    {"element": "tache:GAO-x-001", "verdict": "livree", "theme": "missions",
     "preuves": [{"type": "commit", "sha": "564e4add9"}]}
  ]
}
```

`element` : `tache:<id>`, `branche:<nom>`, `pr:<n>`, `issue:<n>` ou
`worktree:<chemin>`. Verdicts : `a_faire`, `livree`, `obsolete`, `doublon`,
`abandonnee`, `conflit_simple`, `conflit_semantique`, `humain`.

| Preuve | Champs | Vérification |
|---|---|---|
| `commit` | `sha`, `message_contient` | le SHA désigne un commit (jamais une référence homonyme) ancêtre de la cible ; le message contient le texte |
| `fichier` | `chemin`, `motif`, `absent` | un fichier (blob, pas un dossier) et son motif (`git grep -F`) sur la cible ; avec `absent: true`, absent **après avoir existé** dans l'historique de la cible |
| `issue` | `numero`, `etat` (`fermee`) | état et raison de fermeture lus par `gh` ; un numéro de PR n'est « livré » que fusionné dans la cible |
| `pr` | `numero`, `etat` (`fusionnee`) | état lu par `gh pr view` ; fusionnée : base = branche cible et commit de fusion sur la cible |
| `inactivite` | `heures` | dernière activité recalculée et règle d'abandon ci-dessous |
| `doublon_de` | `element` | l'autre élément existe, reste ouvert, n'est pas jugé `doublon`, `abandonnee` ou `obsolete` dans le fichier, sans cycle |
| `note` | `texte` | aucune ; ne suffit jamais seule |

Une preuve doit porter **sur l'élément jugé** :

- `livree` d'une branche exige qu'elle soit `fusionnee` (recalculé) ;
- `livree` d'une tâche, d'une issue ou d'une PR exige une preuve qui la
  nomme : commit dont `message_contient` cite l'élément (`#N` ou
  l'identifiant de tâche), PR fusionnée dans la cible dont le titre, le
  corps ou la branche le nomme, ou l'issue elle-même (ou l'issue liée à la
  tâche) fermée comme terminée — `not_planned` et `duplicate` ne valent pas
  livraison ;
- `obsolete` exige `commit`, `fichier`, `pr` ou `issue` ; `doublon` exige
  `doublon_de` ; `abandonnee` exige `inactivite` ;
- `livree`, `obsolete` et `abandonnee` sont refusés sur une branche (ou la
  PR, ou la tâche `task/<id>`) dont le worktree a du travail non commité ;
- `conflit_simple` et `conflit_semantique` sont recalculés (CHANGELOG seul,
  ou autre fichier).

**Règle d'abandon.** `abandonnee` exige une vivacité mesurée « morte » :
toutes les sessions des tâches liées sont `terminé`. `inconnu`, `inactif`,
`vivant`, ou une tâche liée sans session ne suffisent pas. Seule exception :
sans aucune tâche liée, sans travail non commité dans un worktree de la
branche, et avec un dernier commit au-delà du seuil.

Les noms de branche sont confinés à `refs/heads/<nom>` et
`refs/remotes/<remote de la cible>/<nom>` : `main~1`, `a..b`, `HEAD@{1}`,
`x:chemin` sont refusés. Toute preuve fausse rejette le verdict, avec la
raison ; un élément introuvable ou non mesurable aussi (échec fermé).

## `grimoire steward report`

```bash
grimoire steward report [--verdicts verdicts.json] [--json]
```

Carte et verdicts validés, regroupés par action proposée : fermer avec
preuve, reprendre une PR abandonnée, réaligner, à faire par thème, à
l'humain, verdicts rejetés avec leur raison, **contradictions** (un conflit,
une branche orpheline, un worktree sale ou un état antérieur signalé sur un
élément qui a pourtant un verdict établi : montré, jamais masqué) et signaux
sans verdict établi.
Écrit `report.json` (`grimoire-steward-report/v1`) et `report.txt` sous
`_grimoire-output/steward/`. **Aucune action n'est faite.**

## Limites

- Le jugement reste celui d'un LLM : seule la preuve citée est vérifiée. Une
  preuve vraie peut appuyer un verdict faux (un fichier présent ne dit pas
  que le critère est rempli) ; la skill demande de lire le code.
- Une fusion squash d'une branche en retard, dont la cible a depuis retouché
  les mêmes lignes, n'est vue que si une PR fusionnée dans la cible avait sa
  tête : sinon la branche reste `non_fusionnee` ou `etat_anterieur` (échec
  fermé, jamais `livree`).
- Étape 1 : une proposition. Fermer, reprendre ou réaligner reste à faire.
