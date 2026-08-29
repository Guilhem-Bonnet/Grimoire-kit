# Plan cible de qualité produit

- **Statut** : actif
- **Portée** : toutes les dimensions du produit, dont le moteur de flows n'est qu'une
- **Plan subordonné** : [Plan cible du moteur de flows](flow-engine-target-plan.md)

Le plan du moteur de flows optimise un axe. Ce document est le plan de rang
supérieur : il dit où sont les points, lesquels sont chers, lesquels tombent du
moteur, et jusqu'où l'ingénierie seule peut monter.

## Point de départ

Évaluation du 2026-08-28, douze dimensions pondérées à 100, établie par lecture
du code, exécution des commandes sur un projet neuf et relevé des données
publiques du jour. Note globale **66/100** — décomposée en **75/100** comme
artefact d'ingénierie et **21/100** comme produit mis sur le marché.

## Où sont les points

Points disponibles par dimension, `(10 − note) × poids / 10`. Le total
disponible est de 34,35 points.

| Dimension | Note | Points en jeu | Coût |
|---|---|---|---|
| Solidité de l'enforcement | 5,0 | **6,00** | moyen — mais couplé au moteur |
| Adoption et écosystème | 1,0 | **5,40** | très élevé, partiellement non achetable |
| Cohérence des claims publics | 2,0 | **4,80** | quasi nul |
| Soutenabilité | 3,5 | 3,25 | négatif — on supprime |
| Architecture et code | 7,0 | 3,00 | moyen, mécanisable |
| Produit et onboarding | 6,0 | 2,80 | faible |
| Fonctionnement vérifié | 7,5 | 2,50 | faible |
| Rigueur d'ingénierie | 8,5 | 1,80 | faible |
| Thèse et originalité | 8,5 | 1,50 | tombe du moteur |
| Sécurité et chaîne d'appro | 7,5 | 1,50 | faible |
| Documentation | 8,0 | 1,20 | faible |
| Discipline de preuve interne | 9,5 | 0,50 | déjà au plafond |

Deux lectures s'imposent. La documentation est l'avant-dernier gisement : non
parce qu'elle est parfaite, mais parce qu'elle est déjà la deuxième meilleure
note du tableau — écrire davantage de documents ne rapporte rien. Et la page
publique est le meilleur rapport du système entier : 4,80 points pour une
suppression.

## Le couplage qui structure le plan

Le moteur de flows n'est pas un axe parmi douze. Il achète six dimensions.

| Dimension | Ce que le moteur lui apporte |
|---|---|
| Enforcement | Chaque node déclare sa frontière d'outils : le hook passe d'une denylist globale à un **allowlist par node** |
| Fonctionnement vérifié | Les contrats deviennent des invariants d'exécution, pas de composition |
| Thèse | L'exécution durable est ce que le marché n'a pas |
| Documentation | Un flow mesuré et son rapport de run documentent la tâche qu'il accomplit |
| Claims publics | Le rapport de run remplace les métriques par une mesure |
| Adoption | Le rapport de run est partageable : chaque partage est un événement de distribution |

L'enforcement est le plus gros gisement du tableau, et une denylist plus longue
ne le prendra jamais — allonger la liste, c'est jouer au chat et à la souris.
Le lot 1 du plan moteur est donc aussi le lot enforcement.

## Track A — le moteur

Six lots et deux transversales, décrits dans le
[plan cible du moteur de flows](flow-engine-target-plan.md). Rien à dupliquer
ici.

## Track B — l'hygiène

Indépendante du moteur, mécanique, sans conception à inventer.

### Architecture et code

Le ratchet impose déjà la décroissance des fichiers hérités : il a refusé la
première version du correctif de la porte de preuve, ce qui a produit
l'extraction plutôt que l'ajout. La mécanique est bonne ; ce qui manque est une
**cible de sortie** — un fichier hérité qui n'est plus au-dessus du seuil sort
de la liste, et la liste doit finir vide.

### Produit et onboarding

- Un mode d'installation minimal : déposer trente-six fichiers dans un projet
  qui en compte un est un refus déguisé.
- Aucune écriture hors du projet sans consentement explicite. `init` enregistre
  aujourd'hui le répertoire dans un registre global sans le dire.
- La classe d'encodage Windows se traite par un utilitaire d'affichage partagé,
  pas par quarante-cinq correctifs.

### Rigueur d'ingénierie

Mesurer la couverture réelle avant de toucher au seuil de 70 % : s'il existe du
mou, relever le seuil ne coûte rien et verrouille l'acquis.

### Sécurité et chaîne d'appro

Traiter l'alerte d'analyse statique ouverte et la borne de dépendance subie —
deux dossiers connus, tous deux documentés, aucun des deux ouvert.

### Soutenabilité

C'est la seule dimension que ce plan peut **faire baisser**. Chaque lot doit
sortir avec sa suppression effectuée, pas promise. Un lot livré sans sa
suppression est un lot qui a coûté deux fois.

## La règle qui unifie documentation et site

Les deux défauts constatés — un README affirmant qu'aucune campagne n'a été
exécutée à côté de trois rapports, une page publique affichant six métriques
que rien n'adosse — ne sont pas deux fautes de rédaction. C'est le même
mécanisme : **du texte écrit à la main à côté d'artefacts produits par
machine dérive toujours.**

D'où la règle opposable :

> Tout ce qui décrit un artefact doit être dérivé de cet artefact, ou testé
> contre lui.

Trois applications, par ordre de coût croissant :

1. **Tester.** Un test de dérive compare l'affirmation documentaire à ce que le
   dépôt contient. Le premier est en place : `tests/unit/test_docs_derivation.py`
   refuse un README d'évals qui nie ou omet une campagne publiée.
2. **Dériver.** La référence CLI, le catalogue de patterns et le catalogue de
   besoins sont tous dérivables d'une sortie `--output json`. Ce qui est
   dérivable et reste écrit à la main finira faux.
3. **Générer.** La page publique cesse d'être du texte libre : sa section de
   preuve devient un rapport de run produit par le kit. C'est le lot 5 du
   moteur, et c'est ce qui rend le défaut structurellement impossible.

Les plans, eux, ne meurent jamais tout seuls. Ce document et celui du moteur
portent un statut en tête ; les plans antérieurs doivent en recevoir un —
`actif`, `gelé` ou `absorbé` — faute de quoi le lecteur ne sait pas lequel
engage encore le produit.

## Projection

Si Track A et Track B atterrissent :

| | Aujourd'hui | Projeté |
|---|---|---|
| Note globale | 66 | **84** |
| Artefact d'ingénierie | 75 | **89** |
| Produit et mise sur le marché | 21 | **60** |

Le plafond n'est pas 100 et ne peut pas l'être. Sur les seize points restants,
plus de cinq sont verrouillés derrière des utilisateurs que le code ne produit
pas : l'adoption ne s'écrit pas, elle s'obtient. L'effort d'ingénierie seul
sature autour de 84, et c'est la véritable information de ce plan.

Ce qui reste achetable sur l'adoption, et qui figure ici pour mémoire : un
geste d'installation unique, un artefact partageable par usage, et **un seul
cas racontable**. Ce dernier existe déjà et n'est pas utilisé — sur ce témoin,
avec ce runner et ce modèle, l'activation du standard élimine les régressions
dures, zéro sur quatre-vingt-seize runs activés contre neuf sur quarante en
baseline contemporaine. Borné, répliqué, publiable.

## Références

- [Plan cible du moteur de flows](flow-engine-target-plan.md)
- [Protocole d'évaluation](evals-protocol.md)
- [ADR-005 — Le Mission Ledger est la source](adr-005-mission-ledger-source-of-truth.md)
