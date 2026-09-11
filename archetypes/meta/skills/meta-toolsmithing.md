<!-- ARCHETYPE: meta — Adaptez les {{placeholders}} à votre projet via project-context.yaml -->
---
description: "Créer, auditer ou refactorer un outil framework Grimoire (CLI, module, pattern d'automatisation sous framework/tools/). À utiliser dès qu'un outil doit être forgé, testé triple-interface (CLI/MCP/module) ou découpé — pas pour créer un agent (arbitrage d'agent-optimizer) ni un workflow métier."
tools: ["read", "edit", "execute"]
---

# Forge d'outils framework (ex-agent Vulcan, creative-toolsmith)

Ancien agent dédié (`creative-toolsmith`, faisceau `{read, edit, execute}` sans skill ni contexte propre — indiscernable au sens de la garde de #372, issue Grimoire-kit#375) : le savoir-faire est repris tel quel, porté par `agent-optimizer` au lieu d'occuper un agent à part.

## Principes

- Un outil = un fichier, une responsabilité, un test, un docstring.
- Stdlib Python uniquement — les dépendances externes sont des dettes.
- CLI + MCP + Module : triple interface pour chaque outil.
- Les tests ne sont pas optionnels — écrire le test en même temps que l'outil.
- Backward compatibility sacrée — ne jamais casser un import existant.
- Convention over configuration — suivre les patterns établis du kit.
- La documentation est le premier test — si le docstring est faux, l'outil est faux.

## Garde-fou

CC obligatoire avant tout « terminé » : exécuter `bash {project-root}/_grimoire/kit/framework/cc-verify.sh --stack py` et afficher le résultat ; corriger si FAIL.

## Nouvel outil

Demander le nom, le but et les commandes → générer le squelette (docstring, imports, constantes, fonctions MCP, CLI, tests) → implémenter la logique métier avec gestion d'erreurs → créer le fichier test correspondant → vérifier avec `dep-check.py` que les dépendances sont propres → CC PASS obligatoire.

## Audit du catalogue d'outils

Lister les outils sous `_grimoire/kit/tools/` → vérifier la présence de docstring, `--project-root`, `--json`, tests, `mcp_*` → identifier les outils sans tests, sans interface MCP ou avec du code dupliqué → produire un rapport tabulaire avec scores par dimension → proposer les améliorations prioritaires.

## Refactoring d'un outil existant

Charger l'outil et ses tests → analyser taille, complexité, responsabilités multiples → proposer un plan de découpage si > 500 lignes ou > 3 responsabilités → implémenter en maintenant la backward compat (re-exports) → vérifier que tous les tests passent après refactoring.

## Exposition MCP

Identifier les fonctions principales de l'outil → créer les wrappers `mcp_*` à signature compatible auto-discovery → documenter les paramètres pour le JSON Schema automatique → tester via `grimoire-mcp-tools.py --discover`.

## Squelette standard

Docstring multilingue (FR) avec exemples d'usage, imports stdlib uniquement, constante VERSION, interface MCP (`mcp_*`), CLI argparse avec `--project-root`/`--json`/sous-commandes, `main() -> int`, fichier test unittest avec pattern `_load()`.
