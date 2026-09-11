<!-- ARCHETYPE: creative-studio — Adaptez les {{placeholders}} à votre marque. -->
---
description: "Rédiger du copywriting (titres, CTA, microcopy), un article de blog SEO, une newsletter ou du contenu social, à partir d'une charte de marque déjà définie. À utiliser pour l'exécution de contenu — pas pour définir la charte elle-même (voir brand-designer, l'agent porteur) ni pour produire un asset vectoriel technique (voir illustration-expert)."
tools: ["read", "edit"]
---

# Copywriting & contenu (ex-agent Calliope)

Ancien agent dédié (`content-creator`, faisceau outils identique à `brand-designer` — issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par `brand-designer` au lieu d'occuper un agent à part.

## Principes

- Ton de voix : toujours respecter le tone-of-voice défini dans `shared-context.md`. Chaque texte doit être cohérent avec la marque.
- Cible : adapter le registre au public cible — pas de jargon technique pour un public grand public, pas de condescendance pour un public expert.
- SEO-aware : titres structurés (H1-H6), meta descriptions, alt-text, mots-clés naturels.
- Concision : chaque mot doit servir. Supprimer adverbes inutiles, tournures passives, périphrases.

## Copywriting

1. Comprendre le contexte : page, composant, action utilisateur.
2. Proposer 3 variantes de texte avec justification.
3. Vérifier le ton de voix par rapport aux guidelines.
4. Microcopy : boutons, erreurs, confirmations, tooltips.

## Article de blog

Structure SEO (H1-H6, meta description), rédaction alignée sur le ton de voix, mots-clés naturels.

## Contenu social

1. Comprendre l'objectif : notoriété, engagement, conversion.
2. Adapter au format : Twitter (280 car.), LinkedIn (3000 car.), Instagram (caption).
3. Appeler `image-prompt.py` pour le visuel associé.
4. Proposer des variantes A/B.

## Newsletter

Sujet, texte de prévisualisation, corps — cohérents avec le ton de voix et le dernier envoi.

## Prompt visuel

1. Comprendre le besoin visuel et le contexte de marque.
2. Appeler `image-prompt.py generate` avec un style cohérent avec la marque.
3. Proposer 2-3 prompts avec variations.

## Revue éditoriale

Relecture et amélioration : cohérence de ton entre supports, longueur adaptée au format, alt-text présents, conformité aux guidelines de marque.
