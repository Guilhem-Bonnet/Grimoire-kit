# Le standard agentique

Le standard agentique est la **norme que le kit applique** : un ensemble de
patterns gouvernés, chacun matérialisé par un artefact déclaratif dans
`_grimoire/standard/` et vérifié *fail-closed*. Un projet qui l'adopte ne
gagne pas des recommandations, il gagne des contrôles qui échouent.

C'est un corpus distinct du manuel. Le manuel décrit ce que le produit fait ;
cette section décrit ce à quoi il vous engage quand vous activez le standard.

## Ce que ça change concrètement

| Sans le standard | Avec le standard |
| --- | --- |
| une convention écrite dans un README | un artefact YAML déclaré, versionné |
| une revue qui « vérifie que » | `grimoire standard verify` qui sort non-zéro |
| une preuve rédigée après coup | un `evidence-pack` exigé par la porte |
| une maturité affirmée | un score calculé et persisté |

## Les trois pages

<div class="grid cards" markdown>

- **[Intégration](integration.md)**

    Comment le kit se branche sur le corpus normatif, qui fait autorité sur
    quoi, et où vit le pont. À lire en premier pour comprendre le
    positionnement.

- **[Installation par besoins](install-by-needs.md)**

    Vous déclarez ce que le projet doit faire ; le runtime résout le profil,
    les patterns, les artefacts à générer et les extras `pip` à installer.
    C'est la porte d'entrée pratique.

- **[Contrôles gouvernés](controles-gouvernes.md)**

    La référence : 36 patterns répartis sur 11 catégories, avec leur profil
    minimal, leur artefact et leurs checks. Page générée depuis le catalogue,
    jamais éditée à la main.

</div>

## Les profils

Un projet ne démarre pas gouverné. Les patterns s'activent par paliers de
maturité — chaque pattern déclare le profil à partir duquel il devient
pertinent :

```text
starter → controlled → orchestrated → governed → production
```

`grimoire standard profiles` liste les paliers, `grimoire standard plan
--needs <id>` montre ce qu'un besoin déclenche avant d'écrire quoi que ce
soit.

## Les commandes

| Commande | Rôle |
| --- | --- |
| `grimoire standard needs` | lister les besoins projet |
| `grimoire standard plan --needs <id>` | prévisualiser le plan sans rien écrire |
| `grimoire standard init . --needs <id>` | générer les artefacts |
| `grimoire standard verify` | vérifier, fail-closed |
| `grimoire standard audit` | rapport de conformité et écarts restants |
| `grimoire standard score` | calculer et persister le score |
| `grimoire standard gate check` | porte CI : échoue si une preuve obligatoire manque |
| `grimoire standard fix [--apply]` | planifier ou appliquer les correctifs sûrs |

La référence complète des options est dans le manuel, page
[CLI](../cli-reference.md).

## Ce qui n'est pas ici

Les plans cibles, les trajectoires et les backlogs du standard ne sont pas
publiés : ce sont des documents de travail, ils vivent dans
[`planning/`](https://github.com/Guilhem-Bonnet/Grimoire-kit/tree/main/planning)
dans le dépôt. Une décision arrêtée, elle, devient une ADR et se lit dans le
manuel.
