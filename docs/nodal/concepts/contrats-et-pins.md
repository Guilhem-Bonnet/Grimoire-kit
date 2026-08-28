# Contrats et pins — le système de types

Un blueprint est typé. Pas au sens décoratif : une connexion mal typée ne
compile pas. Le type s'appelle un **contrat**, il est porté par un **pin**, et
il est vérifié à la validation.

## Un pin, trois champs

```json
{ "id": "out", "direction": "out", "contract": "task-envelope" }
```

`id`
:   unique au sein de son node. C'est la deuxième moitié d'une extrémité
    d'edge : `plan.out` désigne le pin `out` du node `plan`.

`direction`
:   `in` reçoit, `out` émet. Une edge va d'un `out` vers un `in`.

`contract`
:   le type. Un nom du [catalogue des contrats](../reference/contrats/index.md)
    — `task-envelope`, `handoff-packet`, `evidence-pack`…

Un node non connecté garde quand même sa clé `pins`, avec une liste vide.
L'absence de la clé sur **tous** les nodes rétrograde le fichier en brouillon
d'atelier, qui sera re-projeté — et le typage que vous aviez écrit sera ignoré.

## La règle : identiques, pas compatibles

Une edge ne relie que deux pins qui portent **exactement** le même contrat.

```text
$.edges[1]: pin contracts differ ('task-envelope' != 'evidence-pack')
  | expected: the same contract on both connected pins
  | fix: align the two pin contracts, or route through an adapter node
```

Il n'y a **aucune conversion implicite**. Pas de sous-typage, pas de coercition,
pas de « c'est presque pareil ». C'est volontairement rigide, et pour une
raison précise : dans un flow d'agents, la conversion d'un contrat en un autre
n'est jamais gratuite — quelqu'un doit décider ce qu'on garde, ce qu'on jette,
ce qu'on résume. Autoriser la conversion tacite reviendrait à laisser cette
décision se prendre nulle part.

Si vous devez passer d'un contrat à un autre, ce n'est pas une conversion :
c'est **un node qui fait ce travail**, et il apparaît dans le graphe. Le message
d'erreur l'appelle un *adapter node*. Sa présence est le point : on voit qui
transforme quoi.

## Le contrat déclaré sur l'edge

Une edge peut porter un `contract` en plus de ses deux extrémités :

```json
{ "from": "plan.out", "to": "govern.in", "contract": "task-envelope" }
```

Il est facultatif. Quand il est là, il doit être **égal** au contrat des deux
pins — une divergence est bloquante. C'est une redondance délibérée : elle rend
le fichier lisible sans avoir à remonter aux deux nodes, et elle attrape le cas
où quelqu'un change un pin en oubliant l'autre bout.

## Ce qu'un contrat contient

Un contrat n'est pas qu'un nom : c'est une liste de champs, chacun obligatoire
ou facultatif, chacun avec un rôle. `task-envelope`, par exemple, exige
`mission_id`, `task_id`, `stage`, `role` — de quoi relier une tâche à sa
mission, situer l'étape et désigner qui la traite.

Les 30 contrats sont détaillés dans la
[référence](../reference/contrats/index.md), un par page.

Le format ne vérifie pas le contenu des champs — ce n'est pas un validateur de
charge utile. Il vérifie que les deux bouts d'une connexion se sont mis
d'accord sur **lequel** contrat circule. C'est le niveau de garantie qu'on peut
donner sans exécuter, et il suffit à attraper la grande majorité des erreurs de
composition.

## Les contrats les plus courants

| Contrat | Ce qui circule |
| --- | --- |
| [`task-envelope`](../reference/contrats/task-envelope.md) | une tâche déléguée, avec son cadrage |
| [`context-pack`](../reference/contrats/context-pack.md) | le contexte sélectionné pour une étape |
| [`handoff-packet`](../reference/contrats/handoff-packet.md) | le passage de relais entre deux agents |
| [`evidence-pack`](../reference/contrats/evidence-pack.md) | la preuve jointe à un travail terminé |
| [`verification-verdict`](../reference/contrats/verification-verdict.md) | le verdict d'une vérification |

Une lecture rapide de ces cinq-là suffit à comprendre la plupart des flows :
une mission descend en `task-envelope`, circule en `handoff-packet`, remonte en
`evidence-pack`, et se conclut en `verification-verdict`.

## Erreurs fréquentes

**Un pin `in` relié à un pin `in`.** La direction est vérifiée : `from` doit
viser un `out`, `to` un `in`.

**Une extrémité qui pointe un pin inexistant.** `plan.result` alors que le node
`plan` n'a qu'un pin `out`. L'erreur nomme l'extrémité fautive.

**Un point dans un id.** Interdit, et pour une raison mécanique : une extrémité
est lue en coupant `<nodeId>.<pinId>` au premier point. Un id contenant un point
rendrait la coupe ambiguë. Les espaces sont interdits pour la même famille de
raisons.

**Deux nodes avec le même id.** Bloquant : les extrémités d'edge deviendraient
ambiguës.

## À lire ensuite

- [Les trois canaux](canaux.md) — une edge ne dit pas que le type, elle dit
  aussi sur quel chemin.
- [Référence des contrats](../reference/contrats/index.md) — les 30, avec leurs
  champs.
- [Format de fichier](../reference/format-fichier.md#pin) — la définition
  exacte d'un pin et d'une edge.
