---
name: haystack-pipeline-design
description: Concevoir un pipeline RAG Haystack gouvernable — corpus déclaré, indexation avec métadonnées de provenance, rappel vérifiable. À utiliser avant d'indexer une base documentaire.
created: 2026-07-02
extension: haystack
---

# Conception de pipeline RAG Haystack gouvernable

Cette skill guide la mise en place d'un pipeline Haystack qui greffe une
base documentaire au système agentique (KNO-06) sans créer de RAG aveugle.

## Quand l'utiliser

- Avant d'indexer un nouveau corpus pour un projet Grimoire.
- Quand un rappel existant retourne des documents obsolètes, hors projet ou sans source.

## Règles de conception

1. **Corpus déclaré** : chaque source indexée a un propriétaire, une fréquence de rafraîchissement et un périmètre. Un document sans provenance n'entre pas dans l'index.
2. **Métadonnées avant embeddings** : source, date, version et périmètre sont indexés comme métadonnées filtrables — la similarité seule ne décide jamais (anti-pattern « RAG aveugle »).
3. **Rappel avec provenance** : chaque document retourné porte sa source et sa date ; le consommateur peut arbitrer via la constitution de contexte du projet (ORC-06, requis).
4. **Fraîcheur assumée** : un index a une date de dernière indexation visible ; au-delà du seuil déclaré, le rappel le signale.
5. **Pas tout indexer** : les brouillons, traces et handoffs temporaires restent hors index (anti-pattern « tout indexer »).

## Processus

1. Inventorier les sources : propriétaire, format, fraîcheur, périmètre.
2. Définir le schéma de métadonnées et les filtres obligatoires.
3. Construire le pipeline d'indexation Haystack (converters, embedders, writer) avec les métadonnées.
4. Construire le pipeline de rappel avec filtres + provenance en sortie.
5. Documenter l'index (sources, seuil de fraîcheur, propriétaire) dans la base de connaissances du projet.
