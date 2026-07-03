---
applyTo: "**"
description: Gouvernance du rappel documentaire RAG (Haystack)
created: 2026-07-02
extension: haystack
---

# Gouvernance du rappel documentaire

Ces règles s'appliquent à tout usage du rappel Haystack dans le projet
(patterns KNO-06 et KNO-02 du catalogue).

## Règles

1. **La similarité n'est pas la vérité** : un document rappelé est un candidat, pas une réponse. Sa provenance et sa date participent à l'arbitrage.
2. **Provenance obligatoire** : citer la source (chemin, version, date) de tout contenu issu du rappel utilisé dans une décision ou une production.
3. **Conflit = arbitrage** : quand deux documents rappelés se contredisent, appliquer l'ordre d'autorité des sources du projet (ORC-06) au lieu de choisir le plus similaire.
4. **Périmètre projet** : un rappel qui retourne des documents d'un autre projet ou périmètre est un défaut d'index à corriger, pas un bonus de contexte.
5. **Signaler la fraîcheur** : au-delà du seuil de fraîcheur déclaré de l'index, le consommateur du rappel doit être averti.
