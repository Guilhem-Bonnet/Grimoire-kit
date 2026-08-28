# Votre premier blueprint

Un quart d'heure, en ligne de commande, sans ouvrir l'atelier. À la fin vous
aurez un flow validé, compilé en mission pack, et vous aurez vu le typage
refuser une connexion — c'est la partie qui apprend le plus vite.

## Installer

```bash
pip install grimoire-kit
pip install jsonschema
```

!!! warning "Le deuxième `pip install` n'est pas décoratif"
    La validation a deux couches : la couche **schéma** (le fichier est-il un
    blueprint bien formé ?) et la couche **structurelle** (les contrats se
    correspondent-ils, le graphe est-il acyclique ?). La première a besoin de
    `jsonschema`. Sans lui elle ne proteste pas : elle s'annonce `skipped` et
    la commande sort quand même à zéro. Installez-le, ou lisez la première
    ligne de sortie à chaque fois.

## Créer

```bash
grimoire blueprint new mon-premier --template pipeline
```

```text
Created mon-premier.blueprint.json (template: pipeline)
Next steps:
  1. Edit the blueprint: nodes, pins, edges, extensions
  2. Validate it       : grimoire blueprint validate mon-premier.blueprint.json
  3. Compile it        : grimoire blueprint compile mon-premier.blueprint.json --project-root .
  4. Publish it        : grimoire ext publish mon-premier.blueprint.json --registry <registry-dir>
```

Deux gabarits existent : `minimal` (un node, aucune edge) et `pipeline`, celui
qu'on prend ici.

## Lire ce qu'on vient de créer

Ouvrez `mon-premier.blueprint.json`. Trois nodes, deux edges :

```json
{
  "id": "plan",
  "kind": "pattern",
  "ref": "ORC-01",
  "label": "Plan",
  "pins": [
    { "id": "out", "direction": "out", "contract": "task-envelope" }
  ]
}
```

Quatre choses à voir, et elles suffisent à lire n'importe quel blueprint :

`kind: "pattern"`
:   dit **d'où vient** le node. Ici, du catalogue.

`ref: "ORC-01"`
:   dit **lequel**. C'est un id de pattern — trois majuscules, un tiret, deux
    chiffres. Sa fiche est dans la
    [référence](reference/patterns/orchestration-et-contexte.md#orc-01).

`pins`
:   les points de connexion. `plan` n'a qu'une sortie. Un node sans connexion
    garde quand même la clé, avec une liste vide : sans elle, le fichier serait
    pris pour un brouillon d'atelier et re-projeté.

`contract: "task-envelope"`
:   le **type** de ce qui circule. C'est ce que la validation vérifie.

Les edges relient des pins, pas des nodes :

```json
{ "from": "plan.out", "to": "govern.in", "contract": "task-envelope" }
```

## Valider

```bash
grimoire blueprint validate mon-premier.blueprint.json
```

```text
Schema layer: checked against schemas/blueprint-v1.schema.json
Structural layer: validate_blueprint_file + compile-level checks
  note: artifact refs not checked (pass --project-root to check them against a project)
Valid: mon-premier.blueprint.json passes both validation layers
```

La note compte : sans `--project-root`, les nodes de `kind: artifact` — ceux
qui pointent un fichier du projet — ne sont pas vérifiés. Le flow est valide
« en soi », pas « pour votre projet ».

## Casser quelque chose, exprès

C'est le moment utile. Dans `verify`, changez le contrat du pin d'entrée :

```json
{ "id": "in", "direction": "in", "contract": "evidence-pack" }
```

L'edge qui arrive sur ce pin vient de `govern.out`, qui émet un
`task-envelope`. Revalidez :

```text
Structural layer: validate_blueprint_file + compile-level checks
  $.edges[1]: pin contracts differ ('task-envelope' != 'evidence-pack')
    | expected: the same contract on both connected pins
    | fix: align the two pin contracts, or route through an adapter node
Invalid: 1 error(s) found in mon-premier.blueprint.json
```

Sortie non nulle, message qui dit où (`$.edges[1]`), quoi, et quoi faire.

Retenez le principe : **les contrats doivent être identiques, pas
compatibles.** Il n'y a pas de conversion implicite. Si vous avez besoin de
passer d'un `task-envelope` à un `evidence-pack`, ce n'est pas une conversion
tacite, c'est un node qui fait le travail — et il apparaît dans le graphe.

Voir [Contrats et pins](concepts/contrats-et-pins.md).

Remettez `task-envelope` et revalidez avant de continuer.

## Compiler

```bash
grimoire blueprint compile mon-premier.blueprint.json --project-root .
```

```text
Compiled: mon-premier
  mission pack : .github/prompts/mon-premier.blueprint.prompt.md
  hash         : sha256:d8306690594cbbf07e80753ba092fae29049cc4a42d8ee84abf60726227c1553
  source saved : _grimoire/blueprints/mon-premier.blueprint.json
  warning: ORC-01 dépend de ORG-01 (Entreprise-agent), absent du flow
```

Trois choses viennent de se produire.

**Un mission pack a été écrit.** C'est l'artefact que le runtime consomme : le
plan d'exécution, node par node. Ouvrez-le, il est lisible.

**Une empreinte a été calculée.** Recompilez : même fichier, même hash. C'est
la reproductibilité, sous une forme vérifiable par une machine.

**Un avertissement a été émis.** `ORC-01` déclare une dépendance vers `ORG-01`,
qui n'est pas dans votre flow. Le catalogue connaît 141 relations de ce genre ;
la compilation vous les rappelle sans vous bloquer. À vous de décider si la
dépendance compte dans votre cas.

## Ce que vous n'avez pas fait

Vous n'avez **rien exécuté**. Aucun agent n'a tourné, aucun jeton n'a été
consommé. Tout ce qui précède — le typage, le cycle, les dépendances, la
compilation — a été vérifié sur le fichier.

C'est le principe : [ce que le graphe fait, et ce qu'il ne fait
pas](concepts/execution.md).

## La suite

- Ouvrir le même fichier dans l'atelier : `grimoire serve`, puis la page
  blueprints. Voir [Atelier local & blueprints](../serve-blueprints.md).
- Ajouter une porte qui exige une preuve : [Les portes](concepts/portes.md).
- Router les échecs ailleurs que le chemin nominal :
  [Les trois canaux](concepts/canaux.md).
- Voir ce que les autres nodes peuvent être :
  [les sept primitives](concepts/primitives.md).
