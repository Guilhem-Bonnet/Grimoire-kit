# Travaux inachevés — backlog (2026-Q2)

> Statut : annexe d'ingénierie. Inventaire du travail restant après la slice v3.6.0
> (11 contrôles gouvernés). Voir aussi [Benchmark corpus & écarts](agentic-standard-benchmark-corpus-2026Q2.md).

## 1. Suite directe de la slice v3.6.0 (→ v3.6.1)

Les 11 nouveaux contrôles sont **disponibles** (patterns sélectionnables via `--pattern`/needs,
vérifiés fail-closed quand l'artefact est présent) mais **pas encore enrôlés par défaut** :

- **Enrôlement par profil** — aucun n'est dans `required_artifacts` d'un profil, donc non généré
  automatiquement par `grimoire standard init`. À ajouter (ex. `agent-privilege-boundary`,
  `guardrail-contract` au profil `governed`) en complétant en parallèle les templates pour que
  les scaffolds restent verts (`test_verify_passes_after_setup`).
- **Câblage rules/hooks** — les `rule_refs`/`hook_refs` des nouveaux patterns ne sont pas encore
  matérialisés dans `rule-packs.yaml` / `hook-registry.yaml` (références documentaires).
- **Scoring** — les nouveaux contrôles n'alimentent pas encore `compliance-score.yaml`
  (dimensions de score) ni `remediation-plan.yaml`.
- **Mapping normatif** — relier les check codes aux IDs de la norme amont (GOV-12/13/14, QUA-12).

## 2. Sous-systèmes runtime lourds (→ v3.7.0+, profil production)

Identifiés par le benchmark, non self-contained (au-delà du moteur capability-map + checks) :

- **Workflow State Engine durable** (checkpoint/interrupt, type LangGraph/Conductor)
- **Local Agent Worker Pool** (slots, retries, cancellation)
- **Agent Backend Boundary** (séparation node → backend → events → runtime)
- **Kubernetes Agent Control Plane** (CRD, admission policies, provider client-go, OTel)
- **Orders Exec/Formula Dispatcher** (shell-only vs workflow agentique)
- **Agent Telemetry Plane OTel complet** (au-delà du cockpit actuel)

## 3. Memory OS — couches à construire

D'après `docs/memory-os-roadmap.md` (plan en 8 étapes), statuts `ready`/`partial`/`planned` à
aligner avec `grimoire memory status` :

1. Contrat Memory OS (marquage explicite des couches) — en cours
2. Mémoire courte (Redis TTL/leases) — partiel
3. Promotion / consolidation — planifié
4. Knowledge Graph sémantique — partiel
5. Code Graph sémantique (neo4j) — partiel
6. Kanban Task Memory — planifié
7. Visualisation cible — planifié
8. Évaluation — planifié

## 4. R&D à trier et porter depuis le clone nested

Le clone `Grimoire-Forge/grimoire-kit` (branche backup, +37 / **−78** vs main) porte une couche
observabilité « intelligence d'essaim » absente de main, **à porter après tri (pas en merge
wholesale, base trop ancienne)** :

- stigmergie, pheromone board (BM-20), détecteur de contradictions intra-fichier (BM-31)
- détecteur d'anomalies par fenêtre glissante (V4.6), ledger d'événements canonique `GrimoireEvent`
- dashboard office live

Ces briques alimentent le **Agent Telemetry Plane** et le **Session Reliability SLO Reporter**.

## 5. Dette repo / hygiène

- **Pre-commit local cassé** — « Grimoire Completion Contract » invoque `pytest` hors-venv
  (ModuleNotFoundError) et lance `ruff check .` sur tout le repo. À réparer (pointer le venv ;
  restreindre le scope au périmètre CI).
- **Dette ruff `framework/memory/`** — casse `Grimoire_*` env vars (devraient être majuscules),
  imports inutilisés / implicit `Optional` dans `mem0-bridge.py`. **Hors scope CI**
  (`ruff check src/ tests/unit/`) mais à nettoyer.
- **Écart de scope lint** — CI lint = `src/ tests/unit/` ; le pre-commit local = repo entier.
  Aligner les deux pour éviter les faux blocages.
- **ADR-001 (no-multi-llm) vs routage multi-provider** — tension à arbitrer/documenter au regard
  du `llm-provider-registry` et du `llm-cost-registry`.

## 6. Branches / PR en attente

**Sur `Guilhem-Bonnet/Grimoire-kit` (GitHub) :**

- dépendances/CI (dependabot) : #22 chromadb, #23 setup-python 6, #24 upload-pages 5,
  #25 codecov 7, #27 checkout 7 — à merger.
- #16 (draft) docs presentation polish.
- branche `feat/agentic-standard-bridge` — à rapprocher de l'état actuel de main.

**Sur le projet consommateur `Grimoire-Forge` (lignes parallèles à réconcilier) :**

- `work/r8-memory-runtime`, `work/r10-governed-observability`, `work/r12-forge-kit-3.5.0-adoption`
- `feat/agentic-standard-baseline`, `agentic-standard-runtime-adoption`
- `wip/snapshot-*`, `preserve/*`, `backup/main-before-origin-sync-*`
