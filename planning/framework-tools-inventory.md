# Inventaire d'usage — framework/tools/

> Généré le 2026-10-25 par `python scripts/framework-usage-inventory.py`. Instantané de décision pour le portage/suppression (cf. framework/FREEZE.md) — régénérer avant tout arbitrage.

**47 fichiers, 35475 lignes.** Classes par priorité de traitement : UNREFERENCED (suppression candidate), INTERNAL (référencé uniquement par d'autres outils de tools/), DOCS_ONLY (réécrire la doc ou porter), TEST_ONLY (test hérité sans usage runtime), TRANSITIVE (chargé au runtime par un outil référencé — supprimer l'appelant d'abord), REFERENCED (à porter vers src/ à la demande).

## TRANSITIVE — 6 fichiers, 4807 lignes

| Fichier | Lignes | runtime | tests | docs | interne | chargé par | décision |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| framework/tools/rag-indexer.py | 1092 | 0 | 3 | 2 | 5 | memory-sync.py | geler (drainer l'appelant d'abord) |
| framework/tools/rag-retriever.py | 872 | 0 | 3 | 1 | 3 | grimoire-mcp-tools.py | geler (drainer l'appelant d'abord) |
| framework/tools/memory-sync.py | 837 | 0 | 4 | 1 | 2 | grimoire-mcp-tools.py | geler (drainer l'appelant d'abord) |
| framework/tools/llm-router.py | 829 | 0 | 2 | 1 | 3 | agent-caller.py | geler (drainer l'appelant d'abord) |
| framework/tools/token-budget.py | 783 | 0 | 1 | 0 | 1 | context-summarizer.py | geler (drainer l'appelant d'abord) |
| framework/tools/incubator.py | 394 | 0 | 2 | 0 | 1 | dream.py | geler (drainer l'appelant d'abord) |

## REFERENCED — 41 fichiers, 30668 lignes

| Fichier | Lignes | runtime | tests | docs | interne | chargé par | décision |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| framework/tools/observatory.py | 1939 | 4 | 2 | 6 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/dream.py | 1347 | 2 | 3 | 1 | 5 | — | geler (porter vers src/ à la demande) |
| framework/tools/tool-resolver.py | 1211 | 9 | 4 | 3 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/context-guard.py | 1105 | 2 | 3 | 1 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/web-browser.py | 1033 | 9 | 4 | 4 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/stigmergy.py | 1010 | 2 | 6 | 3 | 8 | — | geler (porter vers src/ à la demande) |
| framework/tools/agent-darwinism.py | 968 | 1 | 1 | 0 | 2 | — | geler (porter vers src/ à la demande) |
| framework/tools/agent-forge.py | 925 | 1 | 3 | 2 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/agent-debugger.py | 885 | 1 | 1 | 0 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/grimoire-mcp-tools.py | 885 | 3 | 1 | 1 | 2 | — | geler (porter vers src/ à la demande) |
| framework/tools/context-summarizer.py | 880 | 1 | 1 | 0 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/antifragile-score.py | 866 | 2 | 1 | 0 | 2 | — | geler (porter vers src/ à la demande) |
| framework/tools/cross-migrate.py | 816 | 1 | 1 | 0 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/synapse-trace.py | 810 | 2 | 1 | 0 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/nso.py | 794 | 1 | 2 | 0 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/memory-lint.py | 793 | 2 | 1 | 0 | 2 | — | geler (porter vers src/ à la demande) |
| framework/tools/dna-evolve.py | 790 | 1 | 1 | 1 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/agent-test.py | 782 | 2 | 1 | 0 | 2 | — | geler (porter vers src/ à la demande) |
| framework/tools/preflight-check.py | 747 | 1 | 1 | 0 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/expert-tool-chain.py | 722 | 4 | 1 | 0 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/vision-judge.py | 712 | 4 | 1 | 0 | 2 | — | geler (porter vers src/ à la demande) |
| framework/tools/tool-registry.py | 706 | 2 | 1 | 0 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/adversarial-consensus.py | 695 | 1 | 1 | 0 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/agent-caller.py | 692 | 2 | 2 | 0 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/reasoning-stream.py | 679 | 1 | 1 | 0 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/message-bus.py | 675 | 4 | 2 | 1 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/agent-watch.py | 666 | 2 | 1 | 0 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/agent-worker.py | 617 | 3 | 1 | 1 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/schema-validator.py | 614 | 1 | 1 | 0 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/auto-doc.py | 587 | 1 | 1 | 0 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/agent-bench.py | 576 | 4 | 2 | 0 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/session-lifecycle.py | 553 | 1 | 1 | 1 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/distill.py | 508 | 1 | 1 | 0 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/failure-museum.py | 483 | 3 | 2 | 1 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/grimoire-setup.py | 470 | 1 | 1 | 0 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/stigmergy_hooks/scripts/stigmergy_hook.py | 426 | 2 | 2 | 0 | 3 | — | geler (porter vers src/ à la demande) |
| framework/tools/image-prompt.py | 406 | 5 | 1 | 0 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/gen-tests.py | 397 | 5 | 1 | 1 | 1 | — | geler (porter vers src/ à la demande) |
| framework/tools/swarm-consensus.py | 396 | 1 | 1 | 0 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/dep-check.py | 296 | 3 | 1 | 2 | 0 | — | geler (porter vers src/ à la demande) |
| framework/tools/agent-integrity.py | 206 | 1 | 1 | 0 | 0 | — | geler (porter vers src/ à la demande) |

