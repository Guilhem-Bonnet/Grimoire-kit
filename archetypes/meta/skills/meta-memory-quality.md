<!-- ARCHETYPE: meta — Adaptez les {{placeholders}} à votre projet via project-context.yaml -->
---
description: "Auditer à la demande la qualité de la mémoire du projet : contradictions, doublons de learnings, fraîcheur, drift. À utiliser pour un audit ponctuel avec enjeu de qualité — la maintenance de routine (pruning, archivage) tourne déjà sans agent, via les hooks du cercle vertueux."
tools: ["read", "edit"]
---

# Qualité de la mémoire projet (ex-agent Mnemo, memory-keeper)

Ancien agent dédié (`memory-keeper`, faisceau `{read, edit}` distingué uniquement par le skill bundlé `grimoire-memory` — issue Grimoire-kit#375) : son propre fichier documentait que ses automatisations de routine (pruning, archivage) tournent déjà sans intervention utilisateur via les hooks existants du cercle vertueux. Le savoir-faire résiduel — l'audit qualité à la demande, non mécanisable — est repris tel quel, porté par `agent-optimizer`.

## Principes

- Une mémoire contradictoire est pire que pas de mémoire — détecter et résoudre.
- La fraîcheur prime — une info récente remplace une info ancienne (sauf décisions architecturales).
- Doublons = bruit — merger, jamais accumuler.
- Chaque agent mérite des learnings propres et non redondants.
- Mesurer la santé : hit rate, doublons, contradictions, couverture.

## Audit mémoire

Scanner `_grimoire/_memory/` (agent-learnings, decisions-log, shared-context, session-state, activity.jsonl) → mesurer fraîcheur, couverture et doublons → produire un rapport chiffré (nombre d'entrées, dates, scores), jamais une impression en prose.

## Détection de contradictions

Comparer les entrées récentes aux entrées existantes par sujet → signaler chaque conflit au format « conflit détecté — [ancien] vs [nouveau], résolution proposée : [action] » → appliquer la correction mémoire interne, proposer la correction cross-agent.

## Consolidation des learnings

Identifier les doublons sémantiques entre entrées d'un même agent ou d'agents proches → merger en une entrée unique conservant l'information la plus récente et la plus complète → journaliser la fusion.
