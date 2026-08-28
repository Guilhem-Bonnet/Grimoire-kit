# Ce que le graphe fait, et ce qu'il ne fait pas

C'est la première chose à comprendre, et celle qui surprend : **le Studio
n'exécute rien.** Aucun agent ne tourne quand vous validez, simulez ou
compilez. Le graphe est un objet qu'on vérifie, pas un programme qu'on lance.

## Trois opérations, trois questions

| Opération | La question posée | Ce qui échoue |
| --- | --- | --- |
| **valider** | ce fichier est-il un blueprint bien formé ? | schéma, contrats, cycles, ids en double |
| **simuler** | dans quel ordre les nodes seraient parcourus, et que manque-t-il ? | prérequis absents, extension non installée |
| **compiler** | quels artefacts gouvernés ce flow produit-il ? | tout ce qui précède, plus les règles de compilation |

En ligne de commande, `grimoire blueprint` expose `validate` et `compile`. La
**simulation** est propre à l'atelier : c'est une lecture pas à pas du flow
dans l'éditeur, pas une commande. Les gestes et les écrans qui la déclenchent
sont décrits dans [Atelier local & blueprints](../../serve-blueprints.md).

Les deux surfaces ne posent d'ailleurs pas exactement les mêmes règles — la
ligne de commande est la plus stricte sur les cycles, l'atelier sur les
contrats. Voir [Les trois canaux](canaux.md) pour le cas où ça compte.

## Valider : deux couches

```bash
grimoire blueprint validate mon-flow.blueprint.json
```

La couche **schéma** répond « est-ce un blueprint v1 ? » — les clés
obligatoires, les motifs d'id, les énumérations. Elle a besoin du paquet
optionnel `jsonschema`.

!!! danger "Cette couche s'absente en silence"
    Sans `jsonschema` installé, la couche schéma s'annonce `skipped` et la
    commande sort quand même à zéro. Elle ne vous manque pas bruyamment : elle
    vous manque discrètement. `pip install jsonschema`, et lisez la première
    ligne de sortie.

La couche **structurelle** répond aux questions que le schéma ne peut pas
poser, parce qu'elles portent sur les relations et non sur la forme :

- les deux pins d'une edge portent-ils le même contrat ?
- deux nodes partagent-ils un id ?
- une extrémité d'edge pointe-t-elle un pin qui n'existe pas ?
- le graphe est-il acyclique ?
- (avec `--project-root`) les nodes `artifact` pointent-ils des fichiers réels ?

Sans `--project-root`, la validation vous dit que le flow est valide **en
soi**, pas qu'il est valide **pour votre projet**. La sortie le précise.

## Compiler : produire, pas lancer

```bash
grimoire blueprint compile mon-flow.blueprint.json --project-root .
```

La compilation écrit un **mission pack** dans
`.github/prompts/<id>.blueprint.prompt.md`, archive la source dans
`_grimoire/blueprints/`, et calcule une empreinte `sha256` du pack.

Le mission pack est un document : le plan d'exécution, node par node, dans
l'ordre topologique du graphe. Il est destiné au runtime, qui l'exécutera à
travers ses propres portes.

L'empreinte n'est pas décorative. Deux compilations du même fichier donnent le
même hash — c'est ce qui rend un flow rejouable au sens fort : non pas « on
peut le refaire », mais « on peut prouver que c'est le même ».

## Pourquoi cette séparation

On pourrait imaginer un éditeur qui lance le flow. Ce n'est pas un manque, c'est
un choix, et il tient en une phrase : **un graphe qu'on peut vérifier sans le
lancer est un graphe dont on connaît les défauts avant d'avoir payé pour les
découvrir.**

Un contrat qui ne correspond pas, un cycle, une extension absente, un pattern
dont la dépendance manque — tout cela se détecte sur le fichier, en quelques
millisecondes, sans jeton consommé et sans effet de bord. Si l'éditeur
exécutait, ces erreurs se manifesteraient au milieu d'un run, après plusieurs
appels de modèle, et il faudrait les reproduire pour les comprendre.

Corollaire pratique : **le Studio lit, valide et écrit des artefacts ; il
n'exécute rien.** L'exécution appartient au runtime existant et passe par ses
portes. Une porte du graphe déclare ce qui sera exigé ; c'est le runtime qui
l'applique.

## Ce qui se vérifie quand même sans exécuter

Beaucoup plus que ce à quoi on s'attend :

- **le typage complet du flow**, parce que chaque pin porte son contrat ;
- **l'ordre**, parce que les edges le définissent et que le cycle est interdit ;
- **les dépendances de patterns** : le catalogue déclare 141 relations, la
  compilation signale celles que votre flow ignore ;
- **la présence des extensions** dont les nodes dépendent ;
- **l'existence des fichiers** que les nodes `artifact` désignent ;
- **la cohérence des régions d'isolation** et de leurs sorties.

Ce qui ne se vérifie pas sans exécuter, c'est le **comportement** : est-ce que
l'agent fait bien ce qu'on attend ? C'est l'objet des
[suites d'évals](../reference/format-fichier.md#evalsuite), rejouées par
`grimoire blueprint evals` contre un enregistrement d'exécution produit par
l'hôte — là encore, le Studio ne lance rien lui-même.

## À lire ensuite

- [Contrats et pins](contrats-et-pins.md) — ce que la validation vérifie
  vraiment.
- [Les portes](portes.md) — ce qu'un flow déclare et que le runtime applique.
- [Format de fichier](../reference/format-fichier.md) — la structure complète.
