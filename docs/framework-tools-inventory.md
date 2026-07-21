# Inventaire d'usage — framework/tools/

> Généré le 2026-07-12 par `python scripts/framework-usage-inventory.py`. Instantané de décision pour le portage/suppression (cf. framework/FREEZE.md) — régénérer avant tout arbitrage.

**109 fichiers, 71604 lignes.** Classes par priorité de traitement : UNREFERENCED (suppression candidate), INTERNAL (référencé uniquement par d'autres outils de tools/), DOCS_ONLY (réécrire la doc ou porter), TEST_ONLY (test hérité sans usage runtime), REFERENCED (à porter vers src/ à la demande).

## TEST_ONLY — 68 fichiers, 40910 lignes

| Fichier | Lignes | runtime | tests | docs | interne |
| --- | ---: | ---: | ---: | ---: | ---: |
| framework/tools/hpe-runner.py | 1102 | 0 | 3 | 0 | 1 |
| framework/tools/rag-indexer.py | 1092 | 0 | 2 | 1 | 9 |
| framework/tools/hpe-monitor.py | 1030 | 0 | 1 | 0 | 0 |
| framework/tools/agent-task-system.py | 946 | 0 | 1 | 0 | 1 |
| framework/tools/rag-retriever.py | 872 | 0 | 3 | 0 | 4 |
| framework/tools/r-and-d.py | 842 | 0 | 1 | 2 | 2 |
| framework/tools/memory-sync.py | 837 | 0 | 3 | 1 | 2 |
| framework/tools/llm-router.py | 829 | 0 | 2 | 1 | 6 |
| framework/tools/token-budget.py | 818 | 0 | 3 | 1 | 4 |
| framework/tools/delivery-contracts.py | 800 | 0 | 2 | 0 | 0 |
| framework/tools/cognitive-flywheel.py | 777 | 0 | 1 | 1 | 0 |
| framework/tools/digital-twin.py | 771 | 0 | 2 | 1 | 1 |
| framework/tools/docs-fetcher.py | 759 | 0 | 1 | 0 | 0 |
| framework/tools/hpe-executors.py | 754 | 0 | 2 | 0 | 0 |
| framework/tools/orchestrator.py | 744 | 0 | 4 | 3 | 2 |
| framework/tools/rnd_engine.py | 743 | 0 | 1 | 0 | 1 |
| framework/tools/bug-finder.py | 740 | 0 | 1 | 1 | 0 |
| framework/tools/early-warning.py | 736 | 0 | 1 | 1 | 2 |
| framework/tools/doc-fetcher.py | 731 | 0 | 1 | 0 | 0 |
| framework/tools/synapse-dashboard.py | 709 | 0 | 2 | 0 | 1 |
| framework/tools/background-tasks.py | 683 | 0 | 1 | 1 | 0 |
| framework/tools/self-healing.py | 678 | 0 | 3 | 1 | 3 |
| framework/tools/crispr.py | 677 | 0 | 1 | 1 | 0 |
| framework/tools/semantic-cache.py | 634 | 0 | 1 | 1 | 1 |
| framework/tools/rnd_harvest.py | 632 | 0 | 2 | 0 | 1 |
| framework/tools/context-merge.py | 628 | 0 | 2 | 1 | 0 |
| framework/tools/conversation-branch.py | 627 | 0 | 2 | 0 | 1 |
| framework/tools/synapse-config.py | 622 | 0 | 2 | 0 | 0 |
| framework/tools/code-review.py | 616 | 0 | 1 | 0 | 0 |
| framework/tools/oracle.py | 610 | 0 | 1 | 1 | 1 |
| framework/tools/context-router.py | 608 | 0 | 1 | 1 | 2 |
| framework/tools/conversation-history.py | 606 | 0 | 1 | 0 | 0 |
| framework/tools/auto-index.py | 568 | 0 | 1 | 0 | 0 |
| framework/tools/harmony-check.py | 567 | 0 | 1 | 1 | 0 |
| framework/tools/immune-system.py | 558 | 0 | 1 | 1 | 0 |
| framework/tools/skill-validator.py | 556 | 0 | 1 | 0 | 0 |
| framework/tools/dark-matter.py | 554 | 0 | 1 | 1 | 0 |
| framework/tools/desire-paths.py | 548 | 0 | 1 | 1 | 1 |
| framework/tools/semantic-chain.py | 547 | 0 | 1 | 1 | 0 |
| framework/tools/agent-lint.py | 546 | 0 | 1 | 0 | 0 |
| framework/tools/nudge-engine.py | 543 | 0 | 1 | 1 | 0 |
| framework/tools/mirror-agent.py | 537 | 0 | 1 | 1 | 0 |
| framework/tools/fitness-tracker.py | 536 | 0 | 3 | 1 | 3 |
| framework/tools/time-travel.py | 536 | 0 | 1 | 1 | 0 |
| framework/tools/sensory-buffer.py | 535 | 0 | 1 | 1 | 0 |
| framework/tools/decision-log.py | 532 | 0 | 1 | 2 | 0 |
| framework/tools/grimoire-daemon.py | 526 | 0 | 1 | 0 | 0 |
| framework/tools/tool-advisor.py | 516 | 0 | 1 | 1 | 0 |
| framework/tools/rosetta.py | 502 | 0 | 1 | 1 | 0 |
| framework/tools/workflow-adapt.py | 499 | 0 | 1 | 1 | 0 |
| framework/tools/quantum-branch.py | 475 | 0 | 2 | 1 | 1 |
| framework/tools/agent-build.py | 470 | 0 | 1 | 0 | 0 |
| framework/tools/bias-toolkit.py | 458 | 0 | 1 | 1 | 0 |
| framework/tools/concierge.py | 440 | 0 | 1 | 0 | 0 |
| framework/tools/cc-feedback.py | 430 | 0 | 1 | 0 | 0 |
| framework/tools/dashboard.py | 427 | 0 | 3 | 1 | 2 |
| framework/tools/project-graph.py | 403 | 0 | 2 | 1 | 1 |
| framework/tools/mycelium.py | 402 | 0 | 1 | 1 | 0 |
| framework/tools/crescendo.py | 399 | 0 | 1 | 0 | 0 |
| framework/tools/incubator.py | 394 | 0 | 3 | 1 | 2 |
| framework/tools/new-game-plus.py | 379 | 0 | 1 | 1 | 0 |
| framework/tools/quality-score.py | 368 | 0 | 1 | 2 | 0 |
| framework/tools/mcp-proxy.py | 366 | 0 | 1 | 2 | 2 |
| framework/tools/grimoire-log.py | 365 | 0 | 1 | 0 | 0 |
| framework/tools/rnd_core.py | 338 | 0 | 4 | 0 | 1 |
| framework/tools/rag-auto-inject.py | 324 | 0 | 1 | 0 | 0 |
| framework/tools/procedural-memory.py | 292 | 0 | 1 | 2 | 0 |
| framework/tools/mcp-web-search.py | 221 | 0 | 1 | 1 | 0 |

## REFERENCED — 41 fichiers, 30694 lignes

| Fichier | Lignes | runtime | tests | docs | interne |
| --- | ---: | ---: | ---: | ---: | ---: |
| framework/tools/observatory.py | 1939 | 2 | 1 | 8 | 0 |
| framework/tools/dream.py | 1347 | 2 | 4 | 2 | 8 |
| framework/tools/tool-resolver.py | 1236 | 6 | 2 | 0 | 2 |
| framework/tools/context-guard.py | 1105 | 2 | 3 | 2 | 1 |
| framework/tools/web-browser.py | 1033 | 4 | 1 | 0 | 1 |
| framework/tools/stigmergy.py | 1011 | 2 | 4 | 2 | 11 |
| framework/tools/agent-darwinism.py | 968 | 1 | 1 | 1 | 2 |
| framework/tools/agent-forge.py | 925 | 1 | 2 | 2 | 1 |
| framework/tools/agent-debugger.py | 885 | 1 | 2 | 1 | 1 |
| framework/tools/grimoire-mcp-tools.py | 885 | 2 | 2 | 1 | 5 |
| framework/tools/context-summarizer.py | 880 | 1 | 1 | 1 | 2 |
| framework/tools/antifragile-score.py | 866 | 2 | 1 | 1 | 4 |
| framework/tools/cross-migrate.py | 816 | 1 | 1 | 1 | 1 |
| framework/tools/synapse-trace.py | 810 | 2 | 1 | 0 | 1 |
| framework/tools/nso.py | 794 | 1 | 2 | 2 | 1 |
| framework/tools/memory-lint.py | 793 | 2 | 2 | 1 | 3 |
| framework/tools/dna-evolve.py | 790 | 1 | 1 | 2 | 1 |
| framework/tools/agent-test.py | 782 | 1 | 1 | 0 | 2 |
| framework/tools/preflight-check.py | 747 | 1 | 2 | 1 | 1 |
| framework/tools/expert-tool-chain.py | 722 | 3 | 1 | 0 | 0 |
| framework/tools/vision-judge.py | 712 | 3 | 1 | 0 | 3 |
| framework/tools/tool-registry.py | 706 | 3 | 1 | 1 | 2 |
| framework/tools/adversarial-consensus.py | 695 | 1 | 1 | 1 | 1 |
| framework/tools/agent-caller.py | 692 | 2 | 1 | 0 | 1 |
| framework/tools/reasoning-stream.py | 679 | 1 | 1 | 1 | 1 |
| framework/tools/message-bus.py | 675 | 2 | 2 | 2 | 4 |
| framework/tools/agent-watch.py | 666 | 1 | 1 | 0 | 1 |
| framework/tools/agent-worker.py | 617 | 3 | 1 | 1 | 2 |
| framework/tools/schema-validator.py | 614 | 1 | 1 | 1 | 1 |
| framework/tools/auto-doc.py | 587 | 1 | 1 | 1 | 1 |
| framework/tools/agent-bench.py | 576 | 4 | 1 | 1 | 1 |
| framework/tools/session-lifecycle.py | 553 | 1 | 1 | 1 | 0 |
| framework/tools/distill.py | 508 | 1 | 1 | 0 | 0 |
| framework/tools/failure-museum.py | 483 | 2 | 1 | 1 | 3 |
| framework/tools/grimoire-setup.py | 470 | 1 | 1 | 0 | 0 |
| framework/tools/stigmergy_hooks/scripts/stigmergy_hook.py | 426 | 1 | 2 | 0 | 3 |
| framework/tools/image-prompt.py | 406 | 4 | 1 | 0 | 1 |
| framework/tools/gen-tests.py | 397 | 5 | 1 | 2 | 1 |
| framework/tools/swarm-consensus.py | 396 | 1 | 1 | 1 | 0 |
| framework/tools/dep-check.py | 296 | 2 | 1 | 1 | 0 |
| framework/tools/agent-integrity.py | 206 | 1 | 1 | 0 | 0 |

