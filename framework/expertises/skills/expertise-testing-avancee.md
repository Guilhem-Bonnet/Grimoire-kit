<!-- EXPERTISE: testing-avancee — Adaptez à votre projet. -->
---
description: "Durcir une suite de tests au-delà des exemples unitaires — tests par propriétés, mutation testing, contrats de service, discipline de pyramide. À utiliser une fois explicitement attachée, sur demande de renforcement de suite ou d'investigation de test instable — pas liée à une extension de fichier, s'applique à tout langage."
tools: ["read", "edit", "execute"]
---

# Testing avancé

Expertise transversale sur la qualité réelle d'une suite de tests, au-delà du compte de cas ou du pourcentage de couverture. Chargée sur demande explicite de renforcement, d'audit de suite ou de diagnostic d'instabilité.

## Principes

- Les invariants du domaine se testent par génération d'entrées (property-based testing — Hypothesis en Python, QuickCheck en Haskell, fast-check en JS/TS, proptest en Rust), pas seulement par des exemples choisis à la main qui ne couvrent que ce à quoi on a pensé.
- Une suite qui survit à un mutant qu'elle aurait dû tuer donne un faux sentiment de couverture — le mutation testing (mutmut en Python, PIT en Java, Stryker en JS/TS) mesure la qualité des assertions, pas seulement leur présence.
- Aux frontières de services qui évoluent indépendamment, un contrat consommateur-fournisseur (style Pact) remplace avantageusement des tests d'intégration bout-en-bout fragiles et lents.
- La pyramide de tests se respecte en volume : unitaire très majoritaire, intégration modérée, end-to-end minoritaire — l'inversion (cornet de glace) est un signal d'alerte, pas une variante acceptable.
- Un test instable (flaky) est mis en quarantaine et ticketé immédiatement — jamais laissé « généralement vert », car il finit par masquer une vraie régression.
- La couverture de code est un signal, pas un objectif — 100 % de couverture de branches sans assertion sur le résultat produit est du théâtre, pas une garantie.

## Garde-fou

Supprimer ou ignorer (`skip`) un test qui échoue pour faire repasser la CI au vert, sans ticket lié expliquant pourquoi, est refusé — cela transforme un signal réel en absence de signal, silencieusement.

## Renforcer une suite de tests

Identifier le chemin le plus risqué et le moins testé → si ses entrées sont combinatoires, écrire des tests par propriétés sur ses invariants plutôt que des exemples isolés → lancer le mutation testing sur le module critique concerné → corriger chaque mutant qui survit, car chacun révèle une assertion manquante ou trop faible.

## Diagnostiquer un test instable

Rejouer le test isolément N fois pour confirmer l'instabilité et sa fréquence → chercher un état partagé ou global, une hypothèse de timing, ou une concurrence non ordonnée → corriger la cause racine, jamais ajouter un simple retry qui masque le symptôme sans le résoudre.

## Checklist de revue

- Vérifier que les invariants combinatoires sont testés par propriétés, pas seulement par exemples.
- Confirmer qu'un module critique modifié a été passé au mutation testing et que les mutants survivants sont traités.
- Rejeter tout contrat de service remplacé par un test end-to-end fragile sans justification.
- Refuser tout test skip/supprimé sans ticket lié expliquant la raison.
- Ne jamais accepter un chiffre de couverture comme preuve suffisante sans lire les assertions réelles.
