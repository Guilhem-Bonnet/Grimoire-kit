# Benchmark corpus agentique → écarts grimoire-kit (2026-Q2)

> Statut : annexe d'ingénierie. Source de comparaison : audit finalisé du corpus
> `Référence-Agentique` (37 projets / 51 repos) consolidé dans
> `Concepts/reference-agentique-audit/aggregate/` et la cible normative
> `Concepts/processus-developpement-agentique/docs/`. Cible interne : patterns
> déclarés dans `framework/agentic-standard/capability-map.yaml` + modules
> `src/grimoire/`.

## Méthode

Comparaison de l'**architecture cible** dérivée du corpus (22 patterns + 15 contrôles,
mappés sur leurs sources d'inspiration et IDs normatifs) contre la **couverture
actuelle** de grimoire-kit sur `main` (15 patterns gouvernés + modules SDK), vérifiée
sur le code réel (grep src/framework, pas seulement les manifestes). Statut :
`✓ couvert` · `◐ partiel` · `✗ absent`.

## Matrice d'écarts — patterns

| # | Pattern cible | Source corpus | Statut kit | Preuve / module | Priorité |
|---|---|---|---|---|---|
| 1 | Workflow State Engine (graphe durable, checkpoint, interrupt) | LangGraph, Conductor, Dify | ◐ | `evidence-gated-fsm` + `governed-agent-orchestration`, pas de checkpoint/interrupt durable | P2 |
| 2 | Capability Marketplace (lifecycle, promotion, expiration) | gas town, claude-skills, superpowers | ◐ | `src/grimoire/registry`, `skill-classification-matrix` ; manque promotion gate complet | P2 |
| 3 | Advanced Context Orchestrator | LangGraph, Haystack, mempalace | ✓ | `advanced-context-orchestrator` + `context_contract` | — |
| 4 | Context Compression Gate | LLMLingua, Haystack | ✗ | absent (grep) | **P1** |
| 5 | Source Graph Resolver | CodeGraphContext, graphify | ✓ | `code-graph-projection` + `src/grimoire/codegraph` + neo4j | — |
| 6 | Agent Telemetry Plane (OTel, costs, tool calls) | Langfuse, OpenAI Agents SDK | ◐ | `governed-observability-cockpit` + `src/grimoire/traces` ; pas de normalisation OTel complète | **P1** |
| 7 | Tool Blast-Radius Limiter (fichiers/réseau/coût/prod par tâche) | agent-sandbox, OpenHands, kagent | ✗ | amorce dans nested, absent main | **P1** |
| 8 | Visual Evidence Pack (DOM/screenshot/parcours) | browser-use, pixel-agents, Design | ✗ | absent ; Playwright MCP dispo | P2 |
| 9 | Remote Hygiene Guard (refs obsolètes, fraîcheur) | openclaw | ✗ | absent | **P1** |
| 10 | Decision Council Gate (quorum, veto, budget) | Claude Octopus, AutoGen, OpenAI Agents SDK | ✗ | CVTL existe côté orchestrateur Forge, pas comme contrôle kit | **P1** |
| 11 | Local Agent Worker Pool (slots, retries, cancel) | Octogent, Conductor, Shannon | ✗ | absent | P3 |
| 12 | Evidence-Gated Workflow FSM | Switchboard, BMAD, Shannon | ✓ | `evidence-gated-fsm` + `evidence_gates` | — |
| 13 | Agent Backend Boundary (node→backend→events→runtime) | Dify, OpenHands, Agent Framework | ✗ | core relativement monolithique | P3 |
| 14 | Secure Local Coordinator (inbox/terminal signés, no token proxy) | Switchboard, VS Code Copilot Chat | ◐ | `governed-hook-gateway` couvre une partie | P2 |
| 15 | Kubernetes Agent Control Plane (CRD, policies, OTel) | kagent, agent-sandbox, gas-town | ✗ | absent (profil production) | P3 |
| 16 | Agent Privilege Boundary (ScrubTokenEnv controller/agent) | gas-town/gascity | ✗ | absent — invariant sécurité non négociable | **P1** |
| 17 | Merge Lane Fault Classifier (transient vs hard) | gas-town/gascity | ✗ | absent | **P1** |
| 18 | LLM Cost Registry (pricing par model/provider/rig) | gas-town/gascity | ◐ | `provider-cost-slo` + `llm_provider_registry` ; pas de registre coût multicouche | **P1** |
| 19 | Session Reliability SLO Reporter (CrashRate/UnhealthyRate) | gas-town/gascity | ◐ | `provider-cost-slo` ; pas de métriques santé session | **P1** |
| 20 | Orders Exec/Formula Dispatcher (shell-only vs agentique) | gas-town/gascity | ✗ | absent | P2 |
| 21 | Primitive-First Capability Model (rôles = configs) | gas-town/gascity | ✓ | architecture config-driven | — |
| 22 | Guardrail Contract (input/output/tool/model versionnés) | OpenAI Agents SDK, LLMSecurityGuide | ◐ | `rule_packs` ; pas de contrat guardrail versionné 4-faces | **P1** |

## Matrice d'écarts — contrôles (catalogue gates)

| Contrôle cible | ID normatif | Statut | Priorité |
|---|---|---|---|
| Prompt Injection Firewall | GOV-12 | ✗ (amorce nested) | **P1** |
| Remote Hygiene Guard | GOV-13 | ✗ | **P1** |
| Decision Council Gate | GOV-14 | ✗ | **P1** |
| Visual Evidence Gate | QUA-12 | ✗ | P2 |
| Eval lifecycle | QUA-13 | ◐ (`src/grimoire/evals`) | P2 |
| Source Graph Resolver | KNO-10 | ✓ | — |
| Secure Local Coordinator | RUN-12 | ◐ | P2 |
| Context Compression Gate | — | ✗ | **P1** |
| Tool Blast-Radius Limiter | — | ✗ | **P1** |
| MCP Trust Gate | — | ◐ (`tool-mediation-gate`) | P2 |
| Memory Integrity Validator | — | ✗ (amorce nested) | **P1** |
| Agent Privilege Boundary | — | ✗ | **P1** |
| Merge Lane Fault Classifier | — | ✗ | **P1** |
| Capability Promotion Gate | — | ◐ | P2 |

## R&D portable depuis le clone nested (37 commits hors main)

Le clone `Grimoire-Forge/grimoire-kit` (branche backup, +37/−78 vs main) porte une couche
observabilité « intelligence d'essaim » non présente sur main, à évaluer/adapter :
stigmergie, pheromone board (BM-20), détecteur de contradictions intra-fichier (BM-31),
détecteur d'anomalies par fenêtre glissante (V4.6), ledger d'événements canonique
`GrimoireEvent`, dashboard office live. Ces briques alimentent le **Agent Telemetry Plane**
(#6) et le **Session Reliability SLO Reporter** (#19). À porter sur main après tri, pas en
merge wholesale (base 78 commits trop ancienne).

## Plan de release — découpage

**v3.6.0 — « governed controls » (slice 1, livrable immédiat).** Patterns/contrôles
self-contained qui s'intègrent au moteur natif du kit (capability-map + template yaml +
codes de check + tests), faible rayon d'impact :

1. Agent Privilege Boundary (#16) — sécurité non négociable
2. Prompt Injection Firewall (GOV-12)
3. Remote Hygiene Guard (GOV-13 / #9)
4. Decision Council Gate (GOV-14 / #10) — formalise CVTL
5. Context Compression Gate (#4)
6. Tool Blast-Radius Limiter (#7)
7. Memory Integrity Validator
8. Merge Lane Fault Classifier (#17)
9. LLM Cost Registry + Session Reliability SLO (compléter #18/#19)
10. Guardrail Contract (formaliser #22)
11. Visual Evidence Gate (QUA-12, via Playwright MCP)

**v3.7.0+ — runtime subsystems (lourd, profil production).** Workflow State Engine durable
(#1), Local Worker Pool (#11), Agent Backend Boundary (#13), Kubernetes Agent Control Plane
(#15), Orders Exec/Formula Dispatcher (#20), Agent Telemetry Plane OTel complet (#6) +
portage R&D nested.

## Règle directrice (issue de l'audit)

Le schéma cible ne doit pas transformer le kit en dépendance à un framework : extraire les
invariants (graphe d'exécution, contexte gouverné, capacités versionnées, outils limités,
preuves vérifiables, télémétrie corrélée, amélioration continue), pas copier les
implémentations.
