# Le système nodal

Un blueprint est un flow d'agents dessiné plutôt qu'écrit : des **nodes**
reliés par des **edges**, chaque connexion portant un **contrat** vérifié avant
la moindre exécution. Le fichier produit est un `.blueprint.json` ; l'éditeur
qui le manipule est l'atelier local, ouvert par `grimoire serve`.

## Ce que le graphe ne fait pas

Le Studio **n'exécute rien**. Il fait trois choses, dans cet ordre :

| Étape | Ce qu'elle répond |
| --- | --- |
| **valider** | le fichier est-il un blueprint bien formé ? les contrats se correspondent-ils ? |
| **simuler** | dans quel ordre les nodes seraient parcourus, et qu'est-ce qui manque ? |
| **compiler** | quels artefacts gouvernés ce flow produit-il ? |

L'exécution appartient au runtime, et passe par ses portes. C'est une
séparation voulue : un graphe qu'on peut vérifier sans le lancer est un graphe
dont on connaît les défauts avant de payer des jetons pour les découvrir.

## Le vocabulaire minimal

- Un **node** a un `kind` — d'où il vient — et un `role` — ce qu'il fait.
  Les deux sont indépendants.
- Un **pin** est un point de connexion typé. Il porte un contrat et une
  direction.
- Une **edge** relie deux pins qui portent **exactement** le même contrat. Elle
  circule sur un canal : nominal, échec, ou escalade.
- Une **porte** affirme une précondition et gouverne le passage. Elle ne
  transforme rien.
- Un **pattern** est une pratique normée du catalogue, désignée par son id
  (`ORC-02`, `QUA-04`).

## Sept primitives, pas vingt types de node

La palette expose une vingtaine de cases, mais le format n'en connaît que
**sept rôles** : `Unit`, `Route`, `Scatter`, `Gather`, `Gate`, `Boundary`,
`Reference`. Chaque case de la palette est un paramétrage de l'un d'eux. Une
seule primitive produit quelque chose — `Unit` ; les six autres organisent,
contraignent ou désignent.

Comprendre les sept suffit à lire n'importe quel blueprint.

## Où aller

| Vous cherchez… | Allez à… |
| --- | --- |
| ce que fait un rôle précis | [les primitives](reference/index.md) |
| le type échangé sur une edge | [les contrats](reference/contrats/index.md) |
| ce qu'un pattern du catalogue impose | [les patterns](reference/patterns/index.md) |
| la structure exacte du fichier | [le format](reference/format-fichier.md) |
| à quoi correspond une case de la palette | [la palette](reference/palette.md) |
| ce vers quoi un flow dérive quand on le laisse faire | [les anti-patterns](reference/anti-patterns.md) |

Pour ouvrir l'atelier sur votre projet, voir
[Atelier local & blueprints](../serve-blueprints.md).
