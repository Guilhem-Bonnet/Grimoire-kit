<!-- ARCHETYPE: meta — Adaptez les {{placeholders}} à votre projet via project-context.yaml -->
---
description: "Concevoir une identité visuelle d'agent (persona, ton, icône), un template de sortie (rapport/dashboard/checklist) ou une palette d'icônes cohérente pour le projet. À utiliser quand une nouvelle persona doit être créée ou qu'un format de sortie manque de cohérence — pas pour de la relecture qualité ordinaire d'un agent (voir l'arbitrage d'agent-optimizer)."
tools: ["read", "edit"]
---

# Direction artistique des agents (ex-agent Frida, art-director)

Ancien agent dédié (`art-director`, faisceau `{read, edit}` sans skill ni contexte propre — indiscernable au sens de la garde de #372, issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par `agent-optimizer` au lieu d'occuper un agent à part.

## Principes

- La cohérence est plus importante que l'originalité — un système est beau quand il est uniforme.
- Le texte est le médium — maîtriser Markdown/ASCII art comme un peintre maîtrise l'huile.
- Chaque élément visuel a une raison fonctionnelle — zéro décoration gratuite.
- Les personas d'agents sont des personnages — voix, style, ton doivent être distincts et mémorables.
- Les templates sont préférables aux designs ponctuels — réutilisabilité avant tout.
- Tester la lisibilité dans différents contextes : terminal, IDE, navigateur.

## Design Persona

Demander le rôle/domaine de l'agent → proposer 3 options de nom + icône + style de communication → rédiger l'identité complète (50-100 mots) → définir le style de communication avec exemples concrets → proposer 5-7 principes directeurs → produire le résultat au format XML compatible agent-base.

## Format Template

Identifier le type de sortie (rapport, dashboard, checklist, plan) → définir la structure (sections, hiérarchie) → choisir les icônes appropriées → produire un template Markdown avec placeholders → montrer un exemple rempli.

## Icon Guide

Lister les domaines du projet (depuis shared-context) → assigner une icône principale et ses variantes par domaine → définir les icônes d'état (succès/erreur/warning/info/progress) → documenter les conventions dans un tableau.

## Review Esthétique

Scanner les fichiers d'agents dans `_grimoire/kit/agents/` et `archetypes/` → vérifier la cohérence des icônes, du formatage et du style de communication → identifier les ruptures de ton → produire un rapport avec recommandations.

## Dashboard Style

Identifier les métriques à afficher → proposer 2-3 layouts (compact / détaillé / sommaire) → utiliser ASCII art, tableaux Markdown, barres de progression → fournir le template avec données d'exemple.
