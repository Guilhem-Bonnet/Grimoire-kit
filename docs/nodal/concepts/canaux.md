# Les trois canaux

Une edge ne dit pas seulement *quoi* circule — le contrat — mais aussi *sur
quel chemin*. Il y en a trois, et c'est un choix de conception : ce qui se
passe quand ça rate n'est pas une note en marge du flow, c'est une partie du
flow.

```json
{ "from": "verify.out", "to": "escalate.in", "channel": "escalation" }
```

## Les trois

`happy`
:   le chemin nominal. C'est la valeur par défaut : une edge sans `channel` est
    une edge `happy`. Les blueprints écrits avant l'introduction des canaux
    migrent donc sans rien changer.

`failure`
:   le chemin des échecs traités par la machine — reprise, repli, compensation,
    mise au rebut. On y route ce qui doit être rattrapé sans qu'un humain
    intervienne.

`escalation`
:   le chemin qui sort du système — vers une personne, ou vers une autorité
    supérieure. On y route ce qu'on refuse de laisser une machine décider.

## Pourquoi distinguer `failure` et `escalation`

Les deux sont des chemins d'erreur ; ils ne demandent pas la même chose.

Une edge `failure` dit : *ceci peut mal se passer, et voici le plan B, qui est
lui-même du travail automatisable.* Le flow continue, autrement.

Une edge `escalation` dit : *ceci peut mal se passer, et alors la machine
s'arrête de décider.* Elle marque la frontière de ce que le système
s'autorise. Confondre les deux, c'est soit escalader du bruit vers un humain —
qui apprendra vite à ignorer —, soit laisser une machine reprendre seule une
décision qui ne lui appartenait pas.

Les tenir séparés dans le graphe rend cette frontière **lisible** : on voit,
sans lire une ligne de configuration, ce qui sort du système.

## Ce qui circule sur un chemin d'erreur

Les canaux `failure` et `escalation` portent un contrat comme les autres — en
pratique un `error-envelope`. La règle d'identité des contrats s'applique
exactement pareil : un pin d'erreur porte son type, et l'edge relie deux pins
qui portent le même.

Un chemin d'erreur est donc typé, validé et compilé comme le chemin nominal. Ce
n'est pas une voie de garage.

## Ce qui n'a pas besoin d'edge

Toute la gestion d'erreur n'est pas un chemin. La **reprise bornée** et le
**délai d'expiration** sont locaux à un node : ils ne changent pas où l'on va,
seulement combien de fois on réessaie avant d'y aller. Ils vivent donc dans la
politique de résilience du node, pas dans une edge :

```json
{
  "retry": { "max": 3, "backoffMs": 500, "strategy": "exponential" },
  "timeoutMs": 30000,
  "onExhaustion": "escalate"
}
```

`onExhaustion` est la charnière : quand les tentatives sont épuisées, on
retombe sur un chemin — `escalate`, `deadletter` ou `compensate`. La politique
locale décide **quand** on renonce ; le canal décide **où** on va ensuite.

!!! note "Une reprise sans borne ne compile pas"
    `retry` exige `max`, entre 1 et 10. C'est délibéré : dans un flow d'agents,
    une reprise non bornée est une facture non bornée.

Voir [la politique de
résilience](../reference/format-fichier.md#resiliencepolicy).

## Le graphe reste acyclique

Ajouter des chemins d'erreur ne permet pas de faire des boucles. Le flow doit
rester acyclique pour compiler, canaux compris : une edge `failure` qui
remonterait vers un node déjà traversé formerait un cycle, et serait refusée.

Ce qui ressemble à une boucle — réessayer — est justement ce que la politique
de résilience exprime **sans** edge. La distinction n'est pas cosmétique : une
reprise bornée par `max` est vérifiable ; une boucle dans le graphe ne l'est
pas.

## À lire ensuite

- [Les portes](portes.md) — un rejet de porte emprunte ces mêmes canaux.
- [Format de fichier](../reference/format-fichier.md#edge) — la définition
  exacte d'une edge.
- [Anti-patterns](../reference/anti-patterns.md) — ce vers quoi un flow dérive
  quand les erreurs n'ont pas de chemin.
