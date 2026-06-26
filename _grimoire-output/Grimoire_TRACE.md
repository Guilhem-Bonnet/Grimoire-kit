# Grimoire_TRACE — Audit Trail

> Généré automatiquement — ne pas éditer manuellement


## 2026-05-29 21:35 | git-commit | system

[GIT-COMMIT] hash:efc9dc69 branch:work/reconcile-memory-os-r4-20260529
**Message :** feat(memory-os): R4-R6 Memory OS + standard bridge
**Fichiers :** CHANGELOG.md,docs/agentic-standard-final-target.md,docs/agentic-standard-integration.md,docs/agentic-standard-target-architecture.md,docs/archetype-guide.md,docs/cli-reference.md,docs/config-reference.md,docs/grimoire-yaml-reference.md,docs/memory-os-roadmap.md,docs/memory-system.md,docs/vscode-setup.md,framework/agentic-standard/templates/compliance-score.yaml,framework/agentic-standard/templates/context-contract.yaml,framework/agentic-standard/templates/memory-policy.yaml,framework/memory/docker-compose.memory-target.tpl.yml,mkdocs.yml,project-context.tpl.yaml,pyproject.toml,src/grimoire/bridges/__init__.py,src/grimoire/bridges/a2a_adapter.py


## 2026-05-30 00:46 | git-commit | system

[GIT-COMMIT] hash:be1c0971 branch:work/r8-redis-hot-memory-20260529
**Message :** feat(memory): add optional Redis hot memory layer
**Fichiers :** docs/memory-system.md,pyproject.toml,src/grimoire/memory/__init__.py,src/grimoire/memory/architecture.py,src/grimoire/memory/hot.py,src/grimoire/memory/manager.py,tests/unit/memory/test_architecture.py,tests/unit/memory/test_hot_memory.py


## 2026-06-08 19:07 | git-commit | system

[GIT-COMMIT] hash:9e9d67ec branch:work/r11-needs-based-install-20260610
**Message :** feat(standard): needs-based custom install + pattern parity (9→15)
**Fichiers :** CHANGELOG.md,docs/agentic-standard-install-by-needs.md,framework/agentic-standard/capability-map.yaml,framework/agentic-standard/needs-catalog.yaml,framework/agentic-standard/profile-map.yaml,framework/agentic-standard/templates/compliance-score.yaml,framework/agentic-standard/templates/hook-registry.yaml,framework/agentic-standard/templates/observability-policy.yaml,framework/agentic-standard/templates/pattern-catalog.yaml,framework/agentic-standard/templates/rule-packs.yaml,mkdocs.yml,src/grimoire/cli/cmd_standard.py,src/grimoire/core/agentic_standard.py,tests/test_agentic_standard.py


## 2026-06-08 19:49 | git-commit | system

[GIT-COMMIT] hash:aa1879b0 branch:work/r11-needs-based-install-20260610
**Message :** fix(ci): green typecheck + coverage gate for main landing
**Fichiers :** pyproject.toml,src/grimoire/codegraph/backends/neo4j.py,src/grimoire/memory/backends/mempalace.py,src/grimoire/memory/migration.py,src/grimoire/memory/neo4j_graph.py,tests/unit/test_gascity_converter.py,tests/unit/test_plans_registry.py,tests/unit/test_security_policies.py


## 2026-06-08 20:08 | git-commit | system

[GIT-COMMIT] hash:d53c69f5 branch:main
**Message :** chore(release): 3.5.0
**Fichiers :** CHANGELOG.md,src/grimoire/__version__.py


## 2026-06-09 12:12 | git-commit | system

[GIT-COMMIT] hash:7033b76d branch:main
**Message :** docs: record 3.5.0 release in public changelog
**Fichiers :** docs/changelog.md


## 2026-06-10 19:08 | git-commit | system

[GIT-COMMIT] hash:a0d28fbd branch:work/r13-start-small-ux-20260610
**Message :** feat(standard): start-small UX for needs-based install
**Fichiers :** CHANGELOG.md,docs/agentic-standard-install-by-needs.md,framework/agentic-standard/needs-catalog.yaml,src/grimoire/cli/cmd_standard.py,tests/test_agentic_standard.py


## 2026-06-10 22:03 | git-commit | system

[GIT-COMMIT] hash:1872bcb8 branch:work/r13-readme-needs-install-20260610
**Message :** docs(readme): surface needs-based install (start small) in Quick Start
**Fichiers :** README.md


## 2026-06-25 11:57 | git-commit | system

[GIT-COMMIT] hash:068665e5 branch:main
**Message :** feat(agentic-standard): tool-blast-radius-limiter control + corpus benchmark
**Fichiers :** docs/agentic-standard-benchmark-corpus-2026Q2.md,framework/agentic-standard/capability-map.yaml,framework/agentic-standard/profile-map.yaml,framework/agentic-standard/templates/blast-radius-policy.yaml,framework/agentic-standard/templates/pattern-catalog.yaml,src/grimoire/core/agentic_standard.py,tests/test_agentic_standard.py


## 2026-06-25 21:17 | git-commit | system

[GIT-COMMIT] hash:c5a739bc branch:main
**Message :** feat(agentic-standard): 10 governed controls (slice v3.6.0)
**Fichiers :** framework/agentic-standard/capability-map.yaml,framework/agentic-standard/profile-map.yaml,framework/agentic-standard/templates/compression-gate.yaml,framework/agentic-standard/templates/cost-registry.yaml,framework/agentic-standard/templates/decision-council.yaml,framework/agentic-standard/templates/guardrail-contract.yaml,framework/agentic-standard/templates/memory-integrity.yaml,framework/agentic-standard/templates/merge-lane.yaml,framework/agentic-standard/templates/pattern-catalog.yaml,framework/agentic-standard/templates/privilege-boundary.yaml,framework/agentic-standard/templates/prompt-firewall.yaml,framework/agentic-standard/templates/remote-hygiene.yaml,framework/agentic-standard/templates/visual-evidence.yaml,src/grimoire/core/agentic_standard.py,tests/test_agentic_standard.py


## 2026-06-25 21:20 | git-commit | system

[GIT-COMMIT] hash:430a3d4c branch:main
**Message :** chore(release): v3.6.0 — governed controls + benchmark
**Fichiers :** CHANGELOG.md,docs/travaux-inacheves-2026Q2.md,mkdocs.yml,src/grimoire/__version__.py


## 2026-06-25 21:21 | git-commit | system

[GIT-COMMIT] hash:4cffa7d0 branch:detached
**Message :** feat(agentic-standard): tool-blast-radius-limiter control + corpus benchmark
**Fichiers :** docs/agentic-standard-benchmark-corpus-2026Q2.md,framework/agentic-standard/capability-map.yaml,framework/agentic-standard/profile-map.yaml,framework/agentic-standard/templates/blast-radius-policy.yaml,framework/agentic-standard/templates/pattern-catalog.yaml,src/grimoire/core/agentic_standard.py,tests/test_agentic_standard.py


## 2026-06-25 21:21 | git-commit | system

[GIT-COMMIT] hash:05583aef branch:detached
**Message :** feat(agentic-standard): 10 governed controls (slice v3.6.0)
**Fichiers :** framework/agentic-standard/capability-map.yaml,framework/agentic-standard/profile-map.yaml,framework/agentic-standard/templates/compression-gate.yaml,framework/agentic-standard/templates/cost-registry.yaml,framework/agentic-standard/templates/decision-council.yaml,framework/agentic-standard/templates/guardrail-contract.yaml,framework/agentic-standard/templates/memory-integrity.yaml,framework/agentic-standard/templates/merge-lane.yaml,framework/agentic-standard/templates/pattern-catalog.yaml,framework/agentic-standard/templates/privilege-boundary.yaml,framework/agentic-standard/templates/prompt-firewall.yaml,framework/agentic-standard/templates/remote-hygiene.yaml,framework/agentic-standard/templates/visual-evidence.yaml,src/grimoire/core/agentic_standard.py,tests/test_agentic_standard.py


## 2026-06-25 21:21 | git-commit | system

[GIT-COMMIT] hash:2dd528db branch:detached
**Message :** chore(release): v3.6.0 — governed controls + benchmark
**Fichiers :** CHANGELOG.md,docs/travaux-inacheves-2026Q2.md,mkdocs.yml,src/grimoire/__version__.py


## 2026-06-25 21:26 | git-commit | system

[GIT-COMMIT] hash:d6b7117d branch:main
**Message :** ci(publish): gate PyPI upload behind opt-in repo variable
**Fichiers :** .github/workflows/publish.yml


## 2026-06-25 21:52 | git-commit | system

[GIT-COMMIT] hash:8dfea509 branch:main
**Message :** fix(agentic-standard): reconcile profile capabilities + cc-verify venv (v3.6.1)
**Fichiers :** CHANGELOG.md,framework/agentic-standard/profile-map.yaml,framework/cc-verify.sh,src/grimoire/__version__.py,tests/test_agentic_standard.py


## 2026-06-25 22:08 | git-commit | system

[GIT-COMMIT] hash:4f8a2cab branch:main
**Message :** feat(agentic-standard): 8 declarative contracts (v3.7.0)
**Fichiers :** CHANGELOG.md,docs/travaux-inacheves-2026Q2.md,framework/agentic-standard/capability-map.yaml,framework/agentic-standard/profile-map.yaml,framework/agentic-standard/templates/browser-tool-contract.yaml,framework/agentic-standard/templates/cluster-action-policy.yaml,framework/agentic-standard/templates/doc-graph-pipeline.yaml,framework/agentic-standard/templates/environment-policy.yaml,framework/agentic-standard/templates/flow-dsl-manifest.yaml,framework/agentic-standard/templates/pattern-catalog.yaml,framework/agentic-standard/templates/prompt-version-log.yaml,framework/agentic-standard/templates/runtime-provider-contract.yaml,framework/agentic-standard/templates/workspace-isolation.yaml,src/grimoire/__version__.py,src/grimoire/core/agentic_standard.py,tests/test_agentic_standard.py


## 2026-06-25 22:08 | git-commit | system

[GIT-COMMIT] hash:f11433e4 branch:detached
**Message :** feat(agentic-standard): 8 declarative contracts (v3.7.0)
**Fichiers :** CHANGELOG.md,docs/travaux-inacheves-2026Q2.md,framework/agentic-standard/capability-map.yaml,framework/agentic-standard/profile-map.yaml,framework/agentic-standard/templates/browser-tool-contract.yaml,framework/agentic-standard/templates/cluster-action-policy.yaml,framework/agentic-standard/templates/doc-graph-pipeline.yaml,framework/agentic-standard/templates/environment-policy.yaml,framework/agentic-standard/templates/flow-dsl-manifest.yaml,framework/agentic-standard/templates/pattern-catalog.yaml,framework/agentic-standard/templates/prompt-version-log.yaml,framework/agentic-standard/templates/runtime-provider-contract.yaml,framework/agentic-standard/templates/workspace-isolation.yaml,src/grimoire/__version__.py,src/grimoire/core/agentic_standard.py,tests/test_agentic_standard.py


## 2026-06-25 22:29 | git-commit | system

[GIT-COMMIT] hash:86dab067 branch:main
**Message :** feat(agentic-standard): finish declarative roadmap + README refresh (v3.8.0)
**Fichiers :** CHANGELOG.md,CITATION.cff,README.md,framework/agentic-standard/capability-map.yaml,framework/agentic-standard/profile-map.yaml,framework/agentic-standard/templates/k8s-agent-manifest.yaml,framework/agentic-standard/templates/pattern-catalog.yaml,framework/agentic-standard/templates/workflow-state-manifest.yaml,src/grimoire/__version__.py,src/grimoire/core/agentic_standard.py,tests/test_agentic_standard.py


## 2026-06-25 22:50 | git-commit | system

[GIT-COMMIT] hash:29edeecb branch:main
**Message :** feat(scaffold): portable multi-assistant entrypoints (v3.9.0)
**Fichiers :** CHANGELOG.md,README.md,src/grimoire/__version__.py,src/grimoire/core/scaffold.py,tests/test_scaffold_copilot.py


## 2026-06-25 23:04 | git-commit | system

[GIT-COMMIT] hash:70439c41 branch:main
**Message :** feat(scaffold): generate portable .mcp.json (v3.10.0)
**Fichiers :** CHANGELOG.md,README.md,src/grimoire/__version__.py,src/grimoire/core/scaffold.py,tests/test_scaffold_copilot.py


## 2026-06-25 23:24 | git-commit | system

[GIT-COMMIT] hash:062e6804 branch:main
**Message :** fix(scoring): attribute governed-control checks to score dimensions (v3.10.1)
**Fichiers :** CHANGELOG.md,src/grimoire/__version__.py,src/grimoire/core/agentic_standard.py,tests/test_agentic_standard.py


## 2026-06-25 23:33 | git-commit | system

[GIT-COMMIT] hash:3f82dcd9 branch:main
**Message :** chore(framework/memory): safe ruff cleanup + flag env-var casing (v3.10.2)
**Fichiers :** CHANGELOG.md,framework/memory/backends/__init__.py,framework/memory/backends/backend_qdrant_server.py,framework/memory/maintenance.py,framework/memory/mem0-bridge.py,framework/memory/session-save.py,src/grimoire/__version__.py


## 2026-06-25 23:40 | git-commit | system

[GIT-COMMIT] hash:71997ff4 branch:main
**Message :** docs(governed-controls): generated reference page for the 36 patterns (v3.11.0)
**Fichiers :** CHANGELOG.md,docs/gen-governed-controls.py,docs/governed-controls.md,mkdocs.yml,src/grimoire/__version__.py,tests/test_agentic_standard.py


## 2026-06-25 23:45 | git-commit | system

[GIT-COMMIT] hash:d48dbf90 branch:main
**Message :** docs(getting-started): add agentic standard + multi-assistant sections (v3.11.1)
**Fichiers :** CHANGELOG.md,docs/getting-started.md,src/grimoire/__version__.py


## 2026-06-26 00:05 | git-commit | system

[GIT-COMMIT] hash:783208a0 branch:main
**Message :** fix(framework/memory): canonical GRIMOIRE_* env + lint hardening (v3.11.2)
**Fichiers :** CHANGELOG.md,framework/memory/backends/__init__.py,framework/memory/backends/backend_ollama.py,framework/memory/backends/backend_qdrant_local.py,framework/memory/backends/backend_qdrant_server.py,framework/memory/maintenance.py,src/grimoire/__version__.py,tests/unit/test_framework_memory_backends.py


## 2026-06-26 00:29 | git-commit | system

[GIT-COMMIT] hash:97007354 branch:main
**Message :** docs(readme): honesty + maturity pass on features (v3.11.3)
**Fichiers :** CHANGELOG.md,README.md,src/grimoire/__version__.py


## 2026-06-26 00:37 | git-commit | system

[GIT-COMMIT] hash:8ef69b58 branch:main
**Message :** docs(readme): home-made icons only, zero Unicode emoji (v3.11.4)
**Fichiers :** CHANGELOG.md,README.md,src/grimoire/__version__.py


## 2026-06-26 00:44 | git-commit | system

[GIT-COMMIT] hash:37cf177c branch:main
**Message :** docs(web): cover agentic standard in core docs + fixes (v3.11.5)
**Fichiers :** CHANGELOG.md,docs/cli-reference.md,docs/concepts.md,docs/index.md,src/grimoire/__version__.py


## 2026-06-26 01:17 | git-commit | system

[GIT-COMMIT] hash:a071c287 branch:main
**Message :** refactor(icons): home-made icon names in icon: fields, zero emoji (v3.12.0)
**Fichiers :** CHANGELOG.md,archetypes/agentic-standard/archetype.dna.yaml,archetypes/creative-studio/archetype.dna.yaml,archetypes/fix-loop/archetype.dna.yaml,archetypes/infra-ops/archetype.dna.yaml,archetypes/minimal/archetype.dna.yaml,archetypes/platform-engineering/archetype.dna.yaml,archetypes/stack/agents/ansible-expert.dna.yaml,archetypes/stack/agents/docker-expert.dna.yaml,archetypes/stack/agents/go-expert.dna.yaml,archetypes/stack/agents/python-expert.dna.yaml,archetypes/stack/agents/terraform-expert.dna.yaml,archetypes/stack/agents/typescript-expert.dna.yaml,archetypes/web-app/archetype.dna.yaml,framework/teams/team-build.yaml,framework/teams/team-ops.yaml,framework/teams/team-vision.yaml,framework/tools/agent-forge.py,src/grimoire/__version__.py,src/grimoire/tools/agent_forge.py


## 2026-06-26 01:36 | git-commit | system

[GIT-COMMIT] hash:62178e41 branch:main
**Message :** refactor(setup): remove BMAD _bmad sync from grimoire setup (SDK) (v3.13.0)
**Fichiers :** CHANGELOG.md,docs/getting-started.md,docs/grimoire-yaml-reference.md,docs/onboarding.md,src/grimoire/__version__.py,src/grimoire/cli/app.py,src/grimoire/cli/cmd_setup.py,src/grimoire/core/project.py


## 2026-06-26 01:45 | git-commit | system

[GIT-COMMIT] hash:bae237cc branch:main
**Message :** refactor: emoji terminal markers + BMAD removed from legacy setup tool (v3.14.0)
**Fichiers :** CHANGELOG.md,docs/archetype-guide.md,docs/creating-agents.md,framework/tools/context-guard.py,framework/tools/grimoire-setup.py,src/grimoire/__version__.py,tests/test_grimoire_setup.py,tests/test_python_tools.py

