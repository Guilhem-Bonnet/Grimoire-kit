# Inventaire d'usage — framework/tools/

> Généré le 2026-08-10 par `python scripts/framework-usage-inventory.py`. Instantané de décision pour le portage/suppression (cf. framework/FREEZE.md) — régénérer avant tout arbitrage.

**52 fichiers, 38561 lignes.** Classes par priorité de traitement : UNREFERENCED (suppression candidate), INTERNAL (référencé uniquement par d'autres outils de tools/), DOCS_ONLY (réécrire la doc ou porter), TEST_ONLY (test hérité sans usage runtime), REFERENCED (à porter vers src/ à la demande).

## TEST_ONLY — 11 fichiers, 7867 lignes

| Fichier | Lignes | runtime | tests | docs | interne |
| --- | ---: | ---: | ---: | ---: | ---: |
| framework/tools/rag-indexer.py | 1092 | 0 | 2 | 1 | 5 |
| framework/tools/rag-retriever.py | 872 | 0 | 3 | 0 | 3 |
| framework/tools/memory-sync.py | 837 | 0 | 3 | 1 | 2 |
| framework/tools/llm-router.py | 829 | 0 | 2 | 1 | 4 |
| framework/tools/token-budget.py | 783 | 0 | 1 | 1 | 2 |
| framework/tools/orchestrator.py | 744 | 0 | 2 | 3 | 0 |
| framework/tools/early-warning.py | 736 | 0 | 1 | 1 | 1 |
| framework/tools/self-healing.py | 678 | 0 | 2 | 1 | 1 |
| framework/tools/fitness-tracker.py | 536 | 0 | 1 | 1 | 1 |
| framework/tools/incubator.py | 394 | 0 | 2 | 1 | 1 |
| framework/tools/mcp-proxy.py | 366 | 0 | 1 | 2 | 1 |

## REFERENCED — 41 fichiers, 30694 lignes

| Fichier | Lignes | runtime | tests | docs | interne |
| --- | ---: | ---: | ---: | ---: | ---: |
| framework/tools/observatory.py | 1939 | 2 | 1 | 7 | 0 |
| framework/tools/dream.py | 1347 | 2 | 3 | 2 | 5 |
| framework/tools/tool-resolver.py | 1236 | 6 | 2 | 0 | 1 |
| framework/tools/context-guard.py | 1105 | 2 | 3 | 2 | 1 |
| framework/tools/web-browser.py | 1033 | 4 | 1 | 0 | 1 |
| framework/tools/stigmergy.py | 1011 | 2 | 4 | 2 | 9 |
| framework/tools/agent-darwinism.py | 968 | 1 | 1 | 1 | 2 |
| framework/tools/agent-forge.py | 925 | 1 | 2 | 2 | 1 |
| framework/tools/agent-debugger.py | 885 | 1 | 1 | 1 | 0 |
| framework/tools/grimoire-mcp-tools.py | 885 | 2 | 1 | 1 | 2 |
| framework/tools/context-summarizer.py | 880 | 1 | 1 | 1 | 1 |
| framework/tools/antifragile-score.py | 866 | 2 | 1 | 1 | 3 |
| framework/tools/cross-migrate.py | 816 | 1 | 1 | 1 | 1 |
| framework/tools/synapse-trace.py | 810 | 2 | 1 | 0 | 0 |
| framework/tools/nso.py | 794 | 1 | 2 | 2 | 1 |
| framework/tools/memory-lint.py | 793 | 2 | 1 | 1 | 2 |
| framework/tools/dna-evolve.py | 790 | 1 | 1 | 2 | 1 |
| framework/tools/agent-test.py | 782 | 1 | 1 | 0 | 2 |
| framework/tools/preflight-check.py | 747 | 1 | 1 | 1 | 0 |
| framework/tools/expert-tool-chain.py | 722 | 3 | 1 | 0 | 0 |
| framework/tools/vision-judge.py | 712 | 3 | 1 | 0 | 2 |
| framework/tools/tool-registry.py | 706 | 3 | 1 | 1 | 0 |
| framework/tools/adversarial-consensus.py | 695 | 1 | 1 | 1 | 1 |
| framework/tools/agent-caller.py | 692 | 2 | 1 | 0 | 0 |
| framework/tools/reasoning-stream.py | 679 | 1 | 1 | 1 | 1 |
| framework/tools/message-bus.py | 675 | 2 | 1 | 2 | 2 |
| framework/tools/agent-watch.py | 666 | 1 | 1 | 0 | 1 |
| framework/tools/agent-worker.py | 617 | 3 | 1 | 1 | 1 |
| framework/tools/schema-validator.py | 614 | 1 | 1 | 1 | 0 |
| framework/tools/auto-doc.py | 587 | 1 | 1 | 1 | 1 |
| framework/tools/agent-bench.py | 576 | 4 | 1 | 1 | 1 |
| framework/tools/session-lifecycle.py | 553 | 1 | 1 | 1 | 0 |
| framework/tools/distill.py | 508 | 1 | 1 | 0 | 0 |
| framework/tools/failure-museum.py | 483 | 2 | 1 | 1 | 0 |
| framework/tools/grimoire-setup.py | 470 | 1 | 1 | 0 | 0 |
| framework/tools/stigmergy_hooks/scripts/stigmergy_hook.py | 426 | 1 | 2 | 0 | 3 |
| framework/tools/image-prompt.py | 406 | 4 | 1 | 0 | 1 |
| framework/tools/gen-tests.py | 397 | 5 | 1 | 2 | 1 |
| framework/tools/swarm-consensus.py | 396 | 1 | 1 | 1 | 0 |
| framework/tools/dep-check.py | 296 | 2 | 1 | 1 | 0 |
| framework/tools/agent-integrity.py | 206 | 1 | 1 | 0 | 0 |

