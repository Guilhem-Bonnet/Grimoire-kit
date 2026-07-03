---
name: godot-evidence-loop
description: "Boucle de preuve Godot via le serveur MCP Fennara : ancrer chaque claim sur des diagnostics éditeur, screenshots, contexte scène et logs runtime réels. Use when: développement Godot avec agent, validation de scène, debug runtime GDScript, migration Godot, preuve visuelle demandée par une gate evidence-gated, ou quand l'agent devine depuis les fichiers au lieu d'interroger l'éditeur. Produces evidence packs traçables (QUA-12, QUA-04) au lieu de déclarations."
---

# Godot Evidence Loop

Ancrer le travail agentique Godot sur le feedback réel de l'éditeur, via les
outils MCP de Fennara, au lieu de deviner depuis le système de fichiers.

## Quand l'utiliser

- Toute tâche Godot où une gate exige une preuve (`evidence-gated-milestones`).
- Validation d'une scène ou d'un nœud après édition.
- Debug d'une erreur runtime GDScript.
- Migration de version Godot (diagnostics avant/après).
- Review visuelle : screenshot de l'éditeur comme pièce du verdict.

## Pré-requis

- Addon Fennara installé dans le projet (`res://addons/fennara/`) et éditeur ouvert.
- Serveur MCP Fennara déclaré dans l'hôte agent (voir `scripts/setup-mcp.sh`).

## Boucle de preuve (à suivre dans l'ordre)

1. **Contexte avant action** — interroger l'état réel : scène courante,
   arborescence de nœuds, ressources. Jamais d'hypothèse issue des seuls
   fichiers `.tscn`/`.gd` si l'éditeur est disponible.
2. **Agir** — édition de fichiers/scènes avec les outils habituels de l'hôte.
3. **Valider par l'éditeur** — demander les diagnostics et la validation de
   scène à Fennara. Une absence d'erreur *rapportée par l'éditeur* est la
   preuve ; le silence du filesystem n'en est pas une.
4. **Prouver visuellement si l'enjeu est visuel** — capturer un screenshot de
   l'éditeur et le joindre au dossier de preuve (QUA-12 Visual Evidence Pack).
5. **Prouver au runtime si l'enjeu est comportemental** — lancer une session,
   lire les logs runtime, exécuter un script ciblé sur la scène live (RUN-08).
6. **Assembler l'evidence pack** — regrouper diagnostics + screenshot + logs
   avec la claim correspondante (QUA-04) ; le verdict de la gate référence ces
   pièces, jamais une déclaration.

## Règles

- **Aucune claim de complétion sans pièce d'éditeur ou de runtime.** « Le code
  compile probablement » n'existe pas : demander les diagnostics.
- **Dégradation honnête** : si l'éditeur n'est pas joignable (addon absent,
  éditeur fermé), le déclarer explicitement dans la sortie et marquer la
  validation comme non-groundée — ne pas simuler une preuve.
- **Économie** : ne capturer un screenshot que si l'enjeu est visuel ; les
  diagnostics textuels suffisent pour une gate de code.

## Anti-patterns

- Valider une scène en lisant le `.tscn` au lieu d'interroger l'éditeur.
- Joindre un screenshot décoratif sans lien avec la claim.
- Répéter la boucle complète à chaque micro-édition (batcher les validations).
