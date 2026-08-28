# Les portes

Une porte affirme une précondition et gouverne le passage. **Elle ne
transforme rien.** C'est la seule chose qu'elle a en propre, et c'est ce qui la
distingue de tout le reste du graphe.

```json
{
  "gate": {
    "mode": "evidence",
    "onReject": "block",
    "params": { "require": ["tests", "diff"], "format": "evidence-pack" }
  }
}
```

La politique se pose sur un node, dans `config.gate`. Sa présence dérive
`role: "Gate"` — vous n'avez pas à le déclarer deux fois.

## Une primitive, six modes

Il n'y a pas six types de porte : il y a **une** porte, paramétrée six façons.

| Mode | Ce qu'il exige |
| --- | --- |
| `human` | qu'une personne valide — action, approbateurs, seuil de confiance |
| `budget` | que le coût reste sous un plafond de jetons ou de dollars |
| `evidence` | qu'une preuve soit présente, dans un format donné |
| `output-contract` | que la sortie soit conforme à un schéma |
| `guardrail` | que des contrôles de contenu passent, en entrée ou en sortie |
| `mcp-trust` | qu'un serveur MCP reste dans son périmètre déclaré |

Cette unité n'est pas une économie de code, c'est une garantie de lecture : où
qu'elle apparaisse, une porte se lit pareil — *elle vérifie ceci, et voici ce
qui se passe si ça ne tient pas.*

## `onReject` : ce qui se passe quand ça ne passe pas

Trois réponses, et le choix est la vraie décision de conception :

`block`
:   arrêt net. La valeur par défaut. Rien ne continue.

`failure`
:   le rejet part sur une edge `failure` — il existe un plan B automatisable.

`escalation`
:   le rejet part sur une edge `escalation` — la décision remonte à un humain.

Une porte dont le rejet ne mène nulle part est une porte qui bloque. C'est
volontaire : **le défaut est le refus**, pas le laisser-passer.

Voir [Les trois canaux](canaux.md).

## Fail-closed, et ce que ça implique

Le principe tient en une phrase : **tant que la preuve ne tient pas, rien ne
passe.**

L'inverse — le fail-open — est le mode de panne qui compte vraiment. Une porte
qui laisse passer quand elle n'arrive pas à vérifier n'est pas une porte
imparfaite : c'est une porte absente, avec l'apparence rassurante d'une porte
présente. Elle est pire que rien, parce qu'elle empêche de remarquer le trou.

Conséquence pratique quand vous écrivez une porte : **écrivez d'abord le cas
qui doit la faire refuser**, et vérifiez qu'elle refuse. Une porte qu'on n'a
jamais vue dire non n'a pas été testée, elle a été observée en train de ne rien
faire.

## Ce qu'une porte n'est pas

**Ce n'est pas un branchement.** Une porte affirme une condition et laisse
passer ou non. Choisir entre deux suites selon un verdict, un seuil ou une
étiquette, c'est le rôle
[`Route`](../reference/route.md).

**Ce n'est pas un contrôle qualité qui produit un rapport.** Une porte ne rend
rien : elle conditionne. Ce qui produit un verdict est un node de travail — un
[`Unit`](../reference/unit.md) — dont la sortie peut ensuite alimenter une
porte.

**Ce n'est pas le runtime.** Le graphe **déclare** ce qui sera exigé ; c'est le
runtime qui l'applique au moment d'exécuter. Le Studio n'exécute rien, donc il
ne franchit ni ne refuse aucune porte — il vérifie que la porte est bien formée.

## Les portes dans la palette

Quatre cases de la palette sont des portes déguisées, et ce sont juste des
modes :

| Case de palette | Mode |
| --- | --- |
| `human-gate` | `human` |
| `budget-guard` | `budget` |
| `evidence-checkpoint` | `evidence` |
| `output-contract` | `output-contract` |

Voir [la palette](../reference/palette.md) et
[`Gate`](../reference/gate.md).

## Où les poser

Une porte a un coût — elle interrompt, elle exige, parfois elle réveille
quelqu'un. Trois endroits la méritent presque toujours :

- **avant un effet de bord irréversible** — écrire, publier, dépenser ;
- **avant une remontée de résultat** que quelqu'un traitera comme acquise ;
- **au franchissement d'une frontière de confiance** — un serveur MCP, une
  extension, un service externe.

Partout ailleurs, demandez-vous ce que la porte empêcherait concrètement. Si la
réponse n'est pas immédiate, la porte est décorative — et une porte décorative
apprend à l'équipe que les portes se contournent.

## À lire ensuite

- [Référence `Gate`](../reference/gate.md) — le rôle et ses cases.
- [Format de fichier](../reference/format-fichier.md#gatepolicy) — la structure
  exacte de la politique.
- [Sept primitives](primitives.md) — où la porte se situe parmi les rôles.
