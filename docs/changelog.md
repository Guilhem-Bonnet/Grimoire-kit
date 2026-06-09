# Changelog

## Dernière release

### 3.5.0 — Installation par besoins et parité 15 patterns

La release `3.5.0` enrichit le runtime agentique standard avec une installation modulaire et la parité complète des patterns:

- **Installation par besoins** — `grimoire standard needs`, `standard plan --needs ...`, `standard init --needs/--pattern/--memory/--interactive` et `standard doctor`. Un besoin projet est résolu en profil + patterns + artefacts + extras technologiques, tracé dans un `install-manifest.yaml` auditable. Voir [Installation par besoins](agentic-standard-install-by-needs.md).
- **Catalogue étendu 9 → 15 patterns** — ajout de `code-graph-projection` (neo4j), `governed-agent-orchestration`, `governed-knowledge-indexing`, `mission-evidence-ledger`, `tool-mediation-gate` (mcp) et `provider-cost-slo`, plus la parité R8/R9/R10 (`redis-hot-memory-soft-gate`, `governed-hook-gateway`, `skill-classification-matrix`, `governed-observability-cockpit`).
- **Memory OS cible sur `main`** — socle Weaviate + Neo4j + SQLite sidecar, migration Qdrant → Weaviate/Neo4j, projections graph/vector et commandes `grimoire memory graph/vector/gate`, vérifiés par `grimoire standard init/verify/audit/score/gate`.
- package publié sur PyPI et validé par smoke install avec `grimoire-kit 3.5.0`.

Voir aussi la [release GitHub v3.5.0](https://github.com/Guilhem-Bonnet/Grimoire-kit/releases/tag/v3.5.0).

### 3.4.4 — Runtime standard prêt pour PyPI

La release `3.4.4` stabilise la publication du runtime agentique standard:

- CI SDK multi-OS stabilisée autour des commandes `grimoire standard`;
- tests du runtime standard rendus portables Linux, macOS et Windows;
- package publié sur PyPI et validé par smoke install avec `grimoire-kit 3.4.4`.

Voir aussi la [release GitHub v3.4.4](https://github.com/Guilhem-Bonnet/Grimoire-kit/releases/tag/v3.4.4).

## Historique complet

Consultez le [CHANGELOG complet](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/CHANGELOG.md) sur GitHub.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/)
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).
