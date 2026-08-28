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

Les canaux `failure` et `escalation` portent un contrat comme les autres, et la
règle d'identité s'applique pareil. Mais elle est resserrée : un plan de
défaillance transporte **`error-envelope`**, et rien d'autre. L'atelier le
refuse explicitement si vous mettez autre chose.

Un chemin d'erreur est donc typé, validé et compilé comme le chemin nominal. Ce
n'est pas une voie de garage.

!!! danger "Aujourd'hui, l'atelier bloque les deux façons de l'écrire"
    `error-envelope` n'est pas déclaré parmi les 30 contrats du catalogue. Or
    l'atelier refuse tout pin dont le contrat lui est inconnu, **et** refuse
    une edge `failure` qui ne porte pas `error-envelope`. Les deux règles se
    ferment l'une sur l'autre : aucune edge d'erreur ne passe la simulation.

    En ligne de commande, `grimoire blueprint validate` n'applique pas ces deux
    règles et accepte le fichier. Les canaux sont donc déclarables et
    compilables, mais pas simulables dans la toile tant que le contrat n'est
    pas au catalogue.

    Vérifié le 2026-08-28 sur `web/data/catalogue-export.json` et
    `blueprint_resilience.py` (règle R-F2).

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

Ajouter des chemins d'erreur ne permet pas de faire des boucles. Le contrôle de
cycle de `grimoire blueprint validate` **ignore le canal** : une edge `failure`
qui remonte vers un node déjà traversé compte comme n'importe quelle autre, et
le fichier est refusé.

```text
$.edges: cycle detected between nodes: plan, verify
```

La simulation de l'atelier, elle, n'ordonne que sur le canal nominal — les
chemins d'erreur y sont des routes alternatives, hors du chemin. Retenez la
règle stricte : **c'est la ligne de commande qui décide**, et elle refuse.

Ce qui ressemble à une boucle — réessayer — est justement ce que la politique
de résilience exprime **sans** edge. La distinction n'est pas cosmétique : une
reprise bornée par `max` est vérifiable ; une boucle dans le graphe ne l'est
pas.

## Vérifier un chemin d'erreur sans rien exécuter

La question évidente : si rien ne s'exécute, comment savoir que mes edges
`failure` routent bien ?

La simulation de l'atelier accepte une **panne injectée** — un node et une
classe de défaillance — et rejoue le flow comme si elle survenait. Elle rend le
chemin réellement emprunté, le nombre de tentatives consommées, et signale
quand aucun chemin de défaillance n'existe pour le node visé. La simulation
nominale reste le plan `happy` ; la trace du what-if vit à côté.

C'est aussi ce que la suite d'évals sait affirmer, avec l'assertion
`path-taken` : *sous panne injectée, voici par où ça doit passer.*

## À lire ensuite

- [Les portes](portes.md) — un rejet de porte emprunte ces mêmes canaux.
- [Format de fichier](../reference/format-fichier.md#edge) — la définition
  exacte d'une edge.
- [Anti-patterns](../reference/anti-patterns.md) — ce vers quoi un flow dérive
  quand les erreurs n'ont pas de chemin.
