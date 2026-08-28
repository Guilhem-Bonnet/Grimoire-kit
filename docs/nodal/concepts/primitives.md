# Sept primitives, pas vingt types de node

La palette de l'atelier expose une vingtaine de cases. Le format de fichier,
lui, n'en connaît que **sept**. Chaque case est un paramétrage de l'un des sept
rôles — pas un type de plus.

Cette page explique pourquoi, et ce que ça change pour vous.

## Les sept

| Rôle | Ce qu'il fait | Produit ? |
| --- | --- | --- |
| [`Unit`](../reference/unit.md) | consomme des contrats, en produit | **oui** |
| [`Route`](../reference/route.md) | branche sur un verdict, un seuil, une étiquette | non |
| [`Scatter`](../reference/scatter.md) | éclate en parallèle, borné | non |
| [`Gather`](../reference/gather.md) | rejoint : fan-in, quorum, consensus | non |
| [`Gate`](../reference/gate.md) | affirme une précondition, gouverne le passage | non |
| [`Boundary`](../reference/boundary.md) | annote une région ou une edge | non |
| [`Reference`](../reference/reference.md) | désigne quelque chose hors du graphe | non |

**Une seule primitive fait le travail.** `Unit` est le seul rôle qui
transforme ; les six autres organisent, contraignent ou désignent. C'est le
test le plus rapide sur un flow : s'il n'a pas de `Unit`, il ne fait rien.

## `kind` et `role` sont indépendants

Un node porte les deux, et ils répondent à deux questions différentes :

`kind`
:   **d'où vient** le node — du catalogue (`pattern`), d'un fichier du projet
    (`artifact`), d'une extension (`extension-node`), d'un sous-flow
    (`composite`)…

`role`
:   **ce qu'il fait** structurellement — l'une des sept primitives.

Un node `kind: "pattern"` peut être un `Unit` ou une `Gate` selon le pattern
qu'il désigne. Un node `kind: "extension-node"` aussi. L'orthogonalité est le
point : la provenance n'impose pas la fonction.

`role` est facultatif et additif. Un blueprint qui ne le déclare pas reste
valide ; les blueprints antérieurs à son introduction n'ont rien eu à changer.
Certaines configurations le dérivent — poser un `config.gate` sur un node en
fait une `Gate` sans que vous l'écriviez.

## Pourquoi sept et pas vingt

On aurait pu donner un type à chaque case de la palette : `human-gate`,
`budget-guard`, `bounded-loop`, `join-fanin`, `mcp-toolbox`… C'est ce que fait
la plupart des éditeurs nodaux, et ça se paie de trois façons.

**Le vocabulaire enfle sans fin.** Chaque besoin nouveau réclame une case, donc
un type, donc une page de documentation, donc une règle de validation. Vingt
devient quarante.

**Les règles se dupliquent.** `human-gate` et `budget-guard` rejettent tous les
deux — faut-il écrire deux fois comment un rejet se propage ? Avec une porte
paramétrée, `onReject` s'écrit une fois.

**Le lecteur doit tout apprendre.** Un flow inconnu devient illisible dès qu'il
utilise trois cases qu'on ne connaît pas. Avec sept rôles, on lit n'importe
quel flow : on reconnaît la structure, et on va chercher le paramètre.

D'où le choix inverse : **une algèbre courte, des paramètres nombreux.** La
palette reste riche — c'est une affaire d'ergonomie, on veut cliquer sur
« porte humaine », pas configurer une porte générique. Mais le fichier, lui,
ne connaît que le rôle et ses paramètres.

## Lire une case de la palette

`human-gate`, dans le fichier, c'est :

```json
{ "role": "Gate", "config": { "gate": { "mode": "human" } } }
```

`budget-guard`, c'est la même primitive, un autre mode. `bounded-loop`, c'est
une `Boundary` avec un mode `loop` et un budget maximum. `mcp-toolbox`, c'est
une `Reference` qui exige une `Gate(mcp-trust)`.

La table complète — chaque case, sa primitive, ses paramètres — est dans
[la palette](../reference/palette.md).

## Ce que ça change quand vous composez

**Vous n'avez que sept formes à connaître.** Le reste se lit dans les
paramètres, qui sont dans la référence.

**Vous savez tout de suite où est le travail.** Repérez les `Unit` : c'est là
que quelque chose est produit. Tout le reste est de l'organisation.

**Vous savez ce qui ne peut pas arriver.** Une `Gate` ne transforme jamais.
Une `Reference` ne produit rien. Un `Gather` n'invente pas de données. Ces
interdits sont structurels, pas des conventions.

**Vous savez vers quoi ça compile.** Chaque rôle a une cible : `Unit` vers un
artefact gouverné, `Route` vers une règle de policy, `Gate` vers un contrôle
`GOV-xx` ou `QUA-xx`, `Boundary` vers une métadonnée de workflow. La page de
chaque rôle le précise.

## À lire ensuite

- [Référence des primitives](../reference/index.md) — une page par rôle.
- [La palette](../reference/palette.md) — les cases et leur primitive.
- [Les portes](portes.md) — la primitive la plus paramétrée des sept.
