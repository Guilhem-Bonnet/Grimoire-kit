<p align="right"><a href="../README.md">README</a> · <a href="../docs">Docs</a></p>

# <img src="../docs/assets/icons/microscope.svg" width="32" height="32" alt=""> Référence agentique — état de l'art industriel

> Chargé à la demande par tout agent ou workflow Grimoire qui conçoit, arbitre ou
> révise un workflow agentique. Ce fichier condense la documentation officielle des
> laboratoires (Anthropic, OpenAI, Google), les spécifications ouvertes (MCP, A2A,
> AGENTS.md, Agent Skills, OpenTelemetry GenAI), les cadres de sécurité et de
> gouvernance (OWASP, NIST, ISO, EU AI Act, IETF), les frameworks tiers et la
> recherche avec preuves chiffrées.
>
> **Date de veille** : 2026-09-08. **Prochaine révision** : voir section 12.
>
> **Ce que ce fichier n'est pas** : ni un tutoriel, ni la doctrine Grimoire. La
> doctrine vit dans `agent-base.md` et les protocoles frères. Ici, on lit ce que
> l'industrie a prouvé, et on en déduit où Grimoire est aligné, en avance, ou en retard
> (section 10).

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/clipboard.svg" width="28" height="28" alt=""> 0. Comment lire et utiliser ce fichier

### Niveaux de preuve

Chaque affirmation porte une étiquette de provenance. Un agent ne cite un chiffre
que si son étiquette le permet.

| Étiquette | Signification | Usage autorisé |
|---|---|---|
| `[spec]` | Spécification ou norme publiée, page lue le jour de la veille | Contrat opposable |
| `[doc]` | Documentation officielle d'un éditeur, page lue | Règle de plateforme, chiffres de tarif ou de limite |
| `[blog]` | Blog d'ingénierie ou de recherche d'un laboratoire, page lue | Leçon d'expérience, chiffres du cas décrit |
| `[papier]` | Article de recherche, abstract ou HTML lu | Preuve chiffrée dans le cadre de l'expérience |
| `[agrégateur]` | Chiffre relayé par un site tiers, page primaire inaccessible | Ordre de grandeur seulement, à recouper avant citation |
| `[non vérifié]` | Page inaccessible, contenu reconstitué de mémoire | Ne pas citer comme fait |

### Quand charger ce fichier

- Avant de dessiner un nouveau workflow ou de choisir entre mono-agent et multi-agent.
- Avant d'ajouter un outil, un serveur MCP, un hook ou un sous-agent à une pile.
- Quand une revue demande « est-ce l'état de l'art ? » ou « quel standard s'applique ? ».
- Quand un coût, une latence ou un taux d'échec dérive et qu'il faut un levier prouvé.
- Quand la persona `rodin` ou `architect` doit steelmanner un choix d'architecture.

### Carte de décision en dix lignes

1. Un workflow à code fixe suffit-il ? Si oui, pas d'agent (Anthropic, OpenAI, Google convergent).
2. Un seul agent, bien outillé, avant tout multi-agent (OpenAI, Cognition, Strands).
3. Multi-agent en lecture (recherche, revue, hypothèses concurrentes), mono-fil en écriture (code, même fichier).
4. Le contexte est une ressource finie : chaque token coûte de l'attention (Anthropic, Chroma).
5. Moins de dix à vingt outils actifs par tour ; au-delà, différer le chargement (Anthropic, OpenAI, Gemini).
6. Séparer générateur et évaluateur ; l'auto-évaluation est complaisante (Anthropic, MAST).
7. Une instruction n'est pas une contrainte : ce qui doit être garanti passe par un hook ou une politique (Claude Code, AgentCore Policy).
8. Une donnée non fiable ingérée ne doit plus pouvoir déclencher d'action conséquente (six patterns de Beurer-Kellner, CaMeL).
9. Mesurer le coût par tâche résolue et la consistance pass^k, pas le score brut (HAL, τ-bench).
10. Chaque composant de harness encode une hypothèse périssable sur ce que le modèle ne sait pas faire ; la re-tester à chaque génération de modèle (Anthropic).

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/temple.svg" width="28" height="28" alt=""> 1. Boussole : douze règles opposables

Ces règles sont celles sur lesquelles au moins deux sources primaires indépendantes
s'accordent. Elles priment sur toute préférence locale non justifiée.

| Règle | Formulation source | Sources |
|---|---|---|
| Simplicité d'abord | « Find the simplest solution possible, and only increase complexity when needed » | Anthropic Building effective agents `[blog]`, Harness design `[blog]` ; OpenAI guide `[doc]` ; MAF « if you can write a function, do that » `[doc]` |
| Workflow ≠ agent | Workflow = chemins de code prédéfinis ; agent = le LLM dirige son propre processus et ses outils | Anthropic `[blog]` ; OpenAI : « applications that integrate LLMs but don't use them to control workflow execution are not agents » `[doc]` ; ADK : le SequentialAgent « is deterministic » `[doc]` |
| Quand un agent se justifie | Décision complexe, règles ingérables, forte dépendance à des données non structurées ; sinon « a deterministic solution may suffice » | OpenAI guide `[doc]` ; ADK : passer au workflow quand « a single agent does not reliably complete all instructions » `[doc]` |
| Mono-agent d'abord | « Maximize a single agent's capabilities first » | OpenAI `[doc]` ; Cognition `[blog]` ; Strands « simplest, fastest pattern » `[doc]` |
| Le contexte est fini | « The smallest set of high-signal tokens that maximize the likelihood of some desired outcome » ; context rot mesuré sur 18 modèles | Anthropic Context engineering `[blog]` ; Chroma Context Rot `[papier]` ; Lost in the Middle `[papier]` ; Gemini 2.5 report : au-delà de 100k tokens l'agent répète ses actions `[papier]` |
| Tokens = performance | 80 % de la variance de performance multi-agent expliquée par les tokens dépensés ; multi-agent ≈ 15× le coût d'un chat | Anthropic Multi-agent research `[blog]` ; HAL : Pareto plat, « 100× plus cher pour +1 % » `[papier]` |
| Outils : peu, nets, consolidés | Viser moins de vingt fonctions au premier tour ; dix à vingt outils actifs maximum ; « more tools don't always lead to better outcomes » | OpenAI Function calling `[doc]` ; Gemini `[doc]` ; Anthropic Writing tools `[blog]` ; « How Many Tools Should an LLM Agent See? » `[papier]` |
| Vérification indépendante | « Self-evaluation leniency » ; « If you can't verify it, don't ship it » ; MAST : les trois modes dominants sont des défauts de vérification et de harness | Anthropic Harness design `[blog]`, Best practices `[doc]` ; MAST `[papier]` |
| Instruction ≠ contrainte | CLAUDE.md « are context, not enforced configuration » ; bloquer par hook PreToolUse ; politique hors du code de l'agent | Claude Code Memory `[doc]` ; AgentCore Policy Cedar/Dogwood `[doc]` ; ADK plugins pour guardrails `[doc]` |
| Défense en couches | « A single guardrail is unlikely to provide sufficient protection » ; « hybrid, defense-in-depth strategy » déterministe + raisonnement | OpenAI guide `[doc]` ; Google Secure AI Agents `[papier]` ; Anthropic Containment `[blog]` |
| Isoler l'action des données non fiables | Une fois une donnée non fiable ingérée, elle ne doit plus déclencher d'action conséquente | Beurer-Kellner et al. `[papier]` ; CaMeL `[papier]` ; OpenAI Computer use : « Text in a page, document, or tool result cannot grant permission » `[doc]` |
| Le harness vieillit | « Every component in a harness encodes an assumption about what the model can't do on its own, and those assumptions are worth stress testing » | Anthropic Harness design `[blog]`, Managed Agents `[blog]` |

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/workflow.svg" width="28" height="28" alt=""> 2. Catalogue des patterns de workflow

### 2.1 Patterns fondamentaux (consensus des trois laboratoires)

| Pattern | Utiliser quand | Ne pas utiliser quand | Équivalent ADK / OpenAI / Strands | Source |
|---|---|---|---|---|
| Prompt chaining | Sous-tâches fixes, gates entre étapes, on échange latence contre précision | Sous-tâches non prédéfinissables | SequentialAgent / code-first / Workflow DAG | Anthropic `[blog]`, ADK `[doc]` |
| Routing | Catégories d'entrée distinctes, petits modèles pour les cas simples | Classification non fiable | Graph routes (`Event(route=…)`) / handoffs / Graph | Anthropic `[blog]`, ADK `[doc]`, OpenAI `[doc]` |
| Parallelization (sectioning, voting) | Sous-tâches indépendantes ; ou perspectives multiples pour la confiance | Tâches séquentielles, même fichier, état mutable partagé | ParallelAgent (aucun partage entre branches) / multi-agent Responses / Workflow | Anthropic `[blog]`, ADK `[doc]`, OpenAI `[doc]` |
| Orchestrator-workers | Sous-tâches imprévisibles et émergentes (multi-fichiers, multi-sources) | Sous-tâches connues d'avance (parallelization suffit) | Collaborative (coordinator + `single_turn`) / Manager « agents as tools » / Agents as Tools | Anthropic `[blog]`, ADK `[doc]`, OpenAI `[doc]` |
| Evaluator-optimizer | Critères d'évaluation clairs, raffinement itératif prouvé utile | Évaluateur dans le même contexte que le générateur | LoopAgent avec `exit_loop()` / Generate and Review / Graph avec cycle | Anthropic `[blog]`, ADK `[doc]` |
| Handoffs décentralisés | Le routage fait partie du workflow, le spécialiste doit posséder la suite du tour | On veut un seul agent face à l'utilisateur et une synthèse centrale | `transfer_to_<agent>` / Swarm | OpenAI `[doc]`, Strands `[doc]` |
| Agent autonome | Problème ouvert, nombre d'étapes inconnu, feedback environnemental disponible | « Only when simpler solutions fall short » | Dynamic workflow ADK / Agents SDK loop / model-driven loop | Anthropic `[blog]`, ADK `[doc]`, Strands `[doc]` |

Règles d'échelle publiées par Anthropic pour la recherche `[blog]` : requête simple = 1 agent
et 3 à 10 appels d'outils ; comparaison = 2 à 4 sous-agents à 10-15 appels chacun ;
recherche complexe = 10 sous-agents ou plus, aux responsabilités disjointes.

Règle de choix OpenAI `[doc]` : « Use agents as tools when a specialist should help
with a bounded subtask but should not take over the user-facing conversation » ;
« Use handoffs when routing itself is part of the workflow ».

Règle ADK 2.0 `[doc]` : graphe explicite pour du routage précis et prévisible ;
composition dynamique (`@node`, boucles, `gather`) quand le nombre d'itérations ou la
récursion varie ; les templates Sequential/Parallel/Loop sont « superseded » mais
restent documentés.

### 2.2 Patterns de harness pour tâches longues

| Pattern | Mécanique | Preuve | Source |
|---|---|---|---|
| Initializer + coding agent | Script d'initialisation, liste de fonctionnalités en JSON marquées « failing », notes de progression, commits git, une fonctionnalité à la fois, tests bout en bout | Reprise multi-sessions sans perte | Anthropic Effective harnesses `[blog]` |
| Planner / Generator / Evaluator | Contrats de sprint négociés, évaluateur séparé outillé (navigateur), context resets avec handoff | Application complète livrée ; le harness complet coûte environ vingt fois le solo (200 $ contre 9 $ sur le cas décrit) | Anthropic Harness design `[blog]` |
| Writer / Reviewer en contextes séparés | Un contexte frais « won't be biased toward code it just wrote » | Recommandation officielle | Claude Code Best practices `[doc]` |
| Boucle de complétion (Ralph Wiggum) | Hook Stop qui réinjecte le même prompt jusqu'à une « completion promise », avec plafond d'itérations | Plugin officiel Claude Code | `[doc]` |
| Spec-driven development | requirements → design → tasks, exécution en vagues parallèles ; constitution → specify → plan → tasks → implement | Kiro, GitHub Spec Kit 1.0, OpenSpec | `[doc]` |
| Task ledger double | Ledger de tâche (faits, hypothèses, plan) + ledger de progression | Magentic-One | `[blog]` |
| Récitation | Réécrire un `todo.md` en fin de contexte pour piloter l'attention ; garder les erreurs dans le contexte ; masquer les outils plutôt que les retirer (cache KV) | Manus, leçons de production | `[blog]` |
| Agent teams | Lead + équipiers à contextes séparés, liste de tâches partagée, boîte aux lettres ; 3 à 5 équipiers | Expérimental ; « significantly more tokens » ; déconseillé pour l'édition séquentielle | Claude Code Agent teams `[doc]` |
| Dynamic workflows scriptés | Script JS `agent()/pipeline()/parallel()`, 16 agents concurrents, rejouable (horloge interdite) | Audits et migrations massives | Claude Code Workflows `[doc]` |
| Multi-agent natif côté API | Arbre `/root/...`, `max_concurrent_subagents` par défaut 3, à éviter si chaque étape dépend de la précédente | Beta GPT-5.6 | OpenAI Responses multi-agent `[doc]` |

### 2.3 Patterns de recherche : statut 2026

Chiffres lus dans les articles, dans le cadre de leur expérience.

| Pattern | Idée | Preuve | Statut 2026 |
|---|---|---|---|
| ReAct | Entrelacer raisonnement et actions | ALFWorld +34 pts, WebShop +10 pts | Standard : boucle de base de tout agent |
| Self-Consistency | N échantillons, vote majoritaire | GSM8K +17,9 % | Standard sur réponses discrètes ; bat souvent le « débat » multi-agents |
| Reflexion | Réflexion verbale en mémoire épisodique | HumanEval pass@1 91 % contre 80 % | Standard sous forme « retry avec feedback d'exécution » |
| Self-Refine | Génère, critique, raffine dans le même modèle | +20 % absolu moyen | Contesté sans feedback externe (auto-évaluation complaisante) |
| Tree / Graph of Thoughts, LATS | Exploration arborescente ou en graphe | Game of 24 : 4 % → 74 % ; GoT −31 % de coût contre ToT | Niche ; remplacé par le compute natif au test |
| CodeAct | L'action est du code exécutable | +20 % de succès contre JSON | Standard : OpenHands, CaMeL, programmatic tool calling, CodeAct MAF (−63,9 % de tokens annoncés) |
| Voyager, AWM, ACE | Bibliothèque de skills ou de workflows induits des trajectoires ; playbook évolutif | AWM WebArena +51,1 % relatif ; ACE +10,6 % agents | Standard : ancêtres directs des Agent Skills et des mémoires procédurales |
| SWE-agent (ACI) | Interface agent-ordinateur conçue pour le modèle | SWE-bench 12,5 % à l'époque | Standard : le harness compte autant que le modèle |
| Agentless | Localiser, réparer, valider sans boucle d'agent | SWE-bench Lite 32 % à 0,70 $ par instance | Référence de coût ; dépassé en score |
| MemGPT | Hiérarchie mémoire façon OS | Qualitatif | Standard : Letta, compaction |
| Agent-as-a-Judge | Un agent évalue un agent avec feedback intermédiaire | Alignement 88-92 % contre 60-84 % pour un juge LLM ; 2,36 % du coût humain | En montée dans les evals |
| Mixture-of-Agents, débat multi-agents | Couches d'agents relisant les sorties des autres | MoA +7,6 pts AlpacaEval ; « Stop Overvaluing MAD » : échoue souvent à battre CoT + Self-Consistency à coût bien supérieur | Niche |
| MetaGPT, ChatDev, CAMEL | Rôles et SOP encodées | MetaGPT ≈ 1 $ par projet | Dépassés comme frameworks ; les pipelines SOP survivent |

### 2.4 Où tranche le consensus mono-fil contre multi-agent

- Cognition `[blog]` : « Share context, and share full agent traces, not just individual
  messages » ; « Actions carry implicit decisions, and conflicting decisions carry bad
  results ». Recommandation : agent mono-fil, puis compresseur d'historique.
- Anthropic `[blog]` : +90,2 % contre mono-agent en recherche large, mais « inadapté au
  code et aux dépendances inter-agents ».
- Lecture opérationnelle : parallélisme pour lire, mono-fil pour écrire. Un sous-agent
  qui écrit dans le même fichier qu'un autre est une faute de conception.

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/brain.svg" width="28" height="28" alt=""> 3. Contexte, mémoire, harness

### 3.1 Principes de context engineering

- **Right altitude** `[blog]` : ni logique codée en dur dans le prompt, ni consignes
  vagues ; sections balisées ; « minimal set of information that fully outlines your
  expected behavior ».
- **Just-in-time retrieval** `[blog]` : identifiants légers (chemins, requêtes) chargés à
  la demande ; progressive disclosure. Le memory tool et le tool search en sont des
  instances déclarées.
- **Compression par sous-agents** `[blog]` : « The essence of search is compression » ;
  chaque sous-agent explore dans sa fenêtre et renvoie 1 000 à 2 000 tokens condensés.
- **Write / select / compress / isolate** `[blog]` : les quatre opérations du context
  engineering selon LangChain.
- **Cache KV comme métrique n°1** `[blog]` : préfixe stable, ajout en fin seulement,
  pas d'horodatage dans le préfixe, masquer les outils plutôt que changer leur liste.
- **Compaction** : stratégie primaire côté serveur chez Anthropic `[doc]` (déclencheur par
  défaut 150 000 tokens d'entrée, minimum 50 000) ; ADK `[doc]` propose token-based ou
  sliding window ; les instructions données seulement en conversation ne survivent pas à
  une compaction, les mettre dans le fichier d'instructions `[doc]`.
- **Contexte comme code** `[doc]` : « We treat context like source code » (ADK 2.0).
- **RLM** `[papier]` : traiter le prompt comme une variable dans un REPL et appeler
  récursivement le modèle : +26 % contre la compaction GPT-5, entrées cent fois la fenêtre.
  Prometteur, non stabilisé.

### 3.2 Mémoire

| Couche | Pratique établie | Sources |
|---|---|---|
| Fichier d'instructions projet | AGENTS.md / CLAUDE.md / GEMINI.md, le plus proche du fichier édité gagne, les prompts explicites de l'utilisateur priment ; cible moins de 200 lignes ; un fichier sur-spécifié est ignoré à moitié | agents.md `[spec]`, Claude Code Memory `[doc]`, Codex `[doc]` |
| Mémoire persistante auto-écrite | Fichier de mémoire typé (user, feedback, project, reference) chargé en tête ; memory tool côté client avec « ASSUME INTERRUPTION » ; validation anti-traversal obligatoire | Claude Code `[doc]`, Memory tool `[doc]` |
| Sessions et état | Préfixes de portée `user:`, `app:`, `temp:` ; checkpointers par thread et stores cross-thread ; conversations stockées 30 jours par défaut | ADK `[doc]`, LangGraph `[doc]`, OpenAI `[doc]` |
| Mémoire long terme managée | Memory Bank (génération, ingestion, retrieval, profiling, versioning) ; AgentCore Memory ; Letta memory-first | Google `[doc]`, AWS `[doc]`, Letta `[doc]` |
| Mémoire procédurale | Skills et workflows induits (AWM, ACE, Voyager) ; SkillsBench : skills curatés +16,6 pts, skills restreints à trois modules meilleurs que les gros paquets | `[papier]` |
| Menace | Memory poisoning : valider les écritures en externe, cloisonner les composants qui écrivent, contraindre le format | Microsoft AI Red Team `[blog]`, OWASP T1 `[spec]` |
| Méta-apprentissage | ALMA apprend le design de mémoire (quoi stocker, comment retrouver, comment mettre à jour) | `[papier]`, recherche |

### 3.3 Skills : format standard

Spécification agentskills.io `[spec]` : dossier avec `SKILL.md` ; frontmatter `name`
(1 à 64 caractères, minuscules, chiffres, tirets, égal au nom du dossier) et
`description` (1 à 1 024 caractères) obligatoires ; `license`, `compatibility` (500
caractères maximum), `metadata`, `allowed-tools` (expérimental) optionnels ; corps
Markdown de moins de 500 lignes ; dossiers `scripts/`, `references/`, `assets/` ;
divulgation progressive en trois niveaux (métadonnées, corps, ressources) ; validateur
`skills-ref`. Adopté par une quarantaine de clients dont Claude, Codex, Gemini CLI,
GitHub Copilot, Cursor, goose, Kiro, Deep Agents. Une extension MCP « Skills over MCP »
est en revue (SEP-2640).

### 3.4 Sous-agents : contrat des hôtes

| Hôte | Déclaration | Limites lues | Source |
|---|---|---|---|
| Claude Code | `.claude/agents/*.md` ; champs `tools`, `model`, `permissionMode`, `maxTurns`, `memory`, `background`, `effort`, `isolation: worktree` | Descriptions cumulées sous 15 000 tokens ; profondeur 3 ; 20 concurrents ; arrière-plan par défaut ; Explore et Plan non résumables | `[doc]` |
| Codex | TOML dans `.codex/agents/` ; `developer_instructions`, `model_reasoning_effort`, `sandbox_mode` | « Subagent workflows consume more tokens than comparable single-agent runs » | `[doc]` |
| ADK 2.0 | `sub_agents` ; modes `chat`, `task`, `single_turn` ; seul `single_turn` parallélise | Un agent en mode `task` doit être une feuille | `[doc]` |
| GitHub Copilot | `.github/agents/*.agent.md` ; `tools`, `model`, `target`, `mcp-servers` | Prompt ≤ 30 000 caractères | `[doc]` |
| Agent SDK Anthropic | `agents={AgentDefinition(...)}` ; `max_budget_usd` couvre les sous-agents | Un fork hérite tout l'historique mais pas la liste des pairs | `[doc]` |

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/plug.svg" width="28" height="28" alt=""> 4. Outils et protocoles d'interopérabilité

### 4.1 Conception des outils : règles chiffrées

| Règle | Valeur | Source |
|---|---|---|
| Nombre d'outils actifs au premier tour | Moins de 20 (OpenAI) ; 10 à 20 maximum (Gemini) ; précision de sélection en baisse au-delà de 30 à 50 (Anthropic) ; 90,3 % de couverture BFCL avec 7 outils affichés (papier) | `[doc]`, `[papier]` |
| Chargement différé | Tool search : dès 10 outils ou plus de 10k tokens de définitions ; Opus 4.5 79,5 % → 88,1 % avec recherche d'outils ; jusqu'à 10 000 outils différés | Anthropic `[doc]`, `[blog]` ; OpenAI `[doc]` |
| Consolidation | Un outil `schedule_event` plutôt que list, list, create ; namespacing ; identifiants sémantiques plutôt qu'UUID | Anthropic Writing tools `[blog]` |
| Paramètres | « Fewer Parameters are Better » ; clé `status` dans le retour | ADK `[doc]` |
| Schéma strict | `strict: true` recommandé partout | Anthropic `[doc]`, OpenAI `[doc]` |
| Exemples d'usage | `input_examples` : 72 % → 90 % sur paramètres complexes | Anthropic `[blog]` |
| Appels programmatiques | Le modèle écrit du code qui appelle les outils : −37 % de tokens (Anthropic) ; V8 hébergé sans réseau ni FS (OpenAI) ; à éviter pour les actions soumises à approbation | `[doc]`, `[blog]` |
| Appels parallèles | LLMCompiler : latence ÷ 3,7, coût ÷ 6,7 ; Anthropic : temps de recherche −90 % | `[papier]`, `[blog]` |
| Évaluer les outils | Dizaines de tâches réalistes multi-appels ; métriques au-delà de l'exactitude (durée, appels, tokens, erreurs) ; le modèle réécrit les descriptions à partir des transcripts | Anthropic `[blog]` |
| Annotations | `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` : non fiables sauf serveur de confiance | MCP `[spec]` |
| Cotation de risque par outil | Low / medium / high selon lecture ou écriture, réversibilité, permissions, impact financier ; déclenche pause, garde ou escalade humaine | OpenAI guide `[doc]` |

### 4.2 Model Context Protocol : contrat par révision

MCP est gouverné par « Model Context Protocol a Series of LF Projects, LLC » au sein de
l'Agentic AI Foundation. Processus SEP avec conformance obligatoire pour le statut
Final. Registre officiel en preview depuis 2025-09-08.

| Révision | Changements structurants `[spec]` |
|---|---|
| 2024-11-05 | JSON-RPC 2.0, connexions stateful, `initialize` ; serveur : Tools, Resources, Prompts ; client : Sampling ; transports stdio et HTTP+SSE ; pas d'autorisation |
| 2025-03-26 | Autorisation OAuth 2.1 ; Streamable HTTP remplace HTTP+SSE ; annotations d'outils ; batching JSON-RPC |
| 2025-06-18 | Batching retiré ; `structuredContent` + `outputSchema` ; serveur = OAuth Resource Server (RFC 9728) ; Resource Indicators RFC 8707 obligatoires ; Elicitation ; `resource_link` ; en-tête `MCP-Protocol-Version` ; page Security Best Practices |
| 2025-11-25 | OIDC Discovery ; icônes ; scope incrémental ; règles de nommage ; elicitation en mode URL ; `tools` dans sampling ; Client ID Metadata Documents ; Tasks expérimentaux ; JSON Schema 2020-12 par défaut |
| 2026-07-28 (Current) | Protocole **stateless** : fin d'`initialize` et des sessions, `server/discover` obligatoire, version et capacités dans `_meta` à chaque requête ; `subscriptions/listen` remplace le GET SSE ; **MRTR** (le serveur renvoie `resultType: "input_required"`, le client ré-émet avec `inputResponses`) remplace toute requête initiée par le serveur ; `resultType` obligatoire ; Tasks sortis en extension officielle ; en-têtes `Mcp-Method`, `Mcp-Name` ; `ttlMs` et `cacheScope` obligatoires sur les listes ; propagation OTel dans `_meta` ; **dépréciés** : Roots, Sampling, Logging, Dynamic Client Registration, HTTP+SSE (retrait possible à partir de 2027-07-28) |

Règles d'autorisation en vigueur `[spec]` : serveur MCP = resource server publiant ses
métadonnées ; client avec PKCE, `resource` RFC 8707, validation d'audience, `iss`
RFC 9207 ; jeton Bearer en en-tête, jamais en query ; **aucun passthrough de jeton** ;
CIMD recommandé ; extension Enterprise-Managed Authorization (ID-JAG) pour la
délégation d'entreprise. Politique de cycle de vie : douze mois minimum avant retrait,
quatre-vingt-dix jours en cas de faille.

Sécurité côté clients `[doc]` : OpenAI demande approbation avant tout partage avec un
serveur distant (`require_approval`), « A malicious server can exfiltrate sensitive
data from anything that enters the model's context » ; Codex ignore l'AGENTS.md des
projets non fiables depuis 0.150.0.

### 4.3 Agent2Agent : contrat

A2A 1.0.0 (2026-03-12) puis 1.0.1 (2026-05-28), Linux Foundation puis Agentic AI
Foundation depuis août 2026 `[spec]`.

- **Objets** : Agent Card (`/.well-known/agent-card.json`, signable en JWS, champs
  `capabilities`, `skills`, `securitySchemes`, `supportedInterfaces` avec `tenant`) ;
  Task (`id`, `contextId`, `status`, `artifacts`, `history`) ; Message (parts `text`,
  `file`, `structuredData`) ; Artifact.
- **États** : SUBMITTED, WORKING, INPUT_REQUIRED, AUTH_REQUIRED, puis terminaux
  COMPLETED, FAILED, CANCELED, REJECTED. Une Task terminale ne redémarre pas : nouvelle
  Task sur le même `contextId`.
- **Transports** : JSON-RPC, gRPC, HTTP+JSON ; en-tête `A2A-Version` obligatoire (vide
  = 0.3) ; streaming SSE ; push par webhook.
- **Auth** : HTTPS obligatoire, schémas APIKey, HTTPAuth, OAuth2, OIDC, mTLS ;
  credentials en en-têtes standard ; OpenTelemetry recommandé.
- **Complémentarité** : « MCP is for agent-to-tool communication » ; A2A pour le
  partenariat stateful multi-tours entre agents opaques. ACP (IBM) est archivé et absorbé.
- **Écosystème** : « A2Family » = AP2 (paiements mandatés, transféré à FIDO), A2UI, UCP
  (commerce, plus de 80 partenaires).

### 4.4 Autres formats et protocoles

| Protocole | Couche | Statut | Adoption réelle `[spec]`/`[doc]` |
|---|---|---|---|
| AGENTS.md | Instructions projet | Format ouvert AAIF, aucun champ requis | Plus de 60 000 dépôts, 23 hôtes au moins |
| Agent Skills | Compétences packagées | Spec ouverte (Anthropic) | Une quarantaine de clients |
| AG-UI | Agent ↔ frontend, événements | Spec ouverte CopilotKit | Intégré nativement par MAF, ADK, Strands, Mastra, Pydantic AI, Agno |
| llms.txt | Découverte de contenu | Proposition | Milliers de sites, Mintlify, GitBook |
| x402 | Paiement HTTP 402 | Fondation LF depuis 2026-07-14 | AWS, Cloudflare, Stripe, Vercel |
| Arazzo 1.1 | Workflows d'API OpenAPI | Stable | Aucune mention d'agents |
| NLWeb, NANDA | Site conversationnel ; registre d'agents | OSS ; draft IETF individuel | Niche, recherche |
| Agentic AI Foundation | Gouvernance | Formée 2025-12-09 sous la LF | Héberge MCP, goose, AGENTS.md, AgentGateway, A2A ; plus de 250 membres |

### 4.5 Hooks : convergence inter-hôtes

Tous les hôtes de coding agents convergent vers la même famille d'événements et le
même contrat : JSON sur stdout, code de sortie 2 pour bloquer, texte d'erreur comme
raison.

| Hôte | Événements lus | Particularités | Source |
|---|---|---|---|
| Claude Code | SessionStart/End, UserPromptSubmit, PreToolUse, PostToolUse, PostToolUseFailure, PermissionRequest, Stop, SubagentStart/Stop, TaskCreated/Completed, PreCompact/PostCompact, InstructionsLoaded, FileChanged, WorktreeCreate/Remove, Elicitation… | Types `command`, `http`, `mcp_tool`, `prompt`, `agent` ; `permissionDecision` allow/deny/ask ; `updatedInput` ; un Stop hook est outrepassé après huit blocages consécutifs | `[doc]` |
| Codex | PreToolUse, PermissionRequest, PostToolUse, Pre/PostCompact, SessionStart/End, SubagentStart/Stop, UserPromptSubmit, Stop, Interrupt | Reviewer automatique `approvals_reviewer = "auto_review"` | `[doc]` |
| Gemini CLI / Antigravity | SessionStart/End, BeforeAgent/AfterAgent, BeforeModel/AfterModel, BeforeToolSelection, BeforeTool/AfterTool, PreCompress | Hooks projet empreintés ; « Hooks execute arbitrary code with your user privileges » | `[doc]` |
| GitHub Copilot | sessionStart, sessionEnd, userPromptSubmitted, preToolUse, postToolUse, errorOccurred | `.github/hooks/*.json`, timeout 30 s, fichier requis sur la branche par défaut | `[doc]` |
| Kiro | PostFileSave/Create/Delete, PromptSubmit, AgentStop, PreToolUse/PostToolUse, SessionStart, AgentSpawn | Action `command` ou `agent` | `[doc]` |
| ADK | Callbacks before/after agent, model, tool ; Plugins globaux exécutés avant les callbacks | Plugins recommandés pour les guardrails | `[doc]` |
| OWASP Agent Control Standard | Hooks middleware déclaratifs portables entre frameworks | Annoncé 2026-09-01, draft | `[agrégateur]` |

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/shield-pulse.svg" width="28" height="28" alt=""> 5. Sécurité, identité, gouvernance

### 5.1 Patterns de défense prouvés

Beurer-Kellner et al. `[papier]`, six patterns ; principe : une donnée non fiable
ingérée ne doit plus pouvoir déclencher d'action conséquente.

1. **Action-Selector** : l'agent choisit parmi des actions prédéfinies, sans retour de données.
2. **Plan-Then-Execute** : plan figé avant lecture des données non fiables.
3. **LLM Map-Reduce** : sous-agents isolés par donnée, agrégation sûre.
4. **Dual LLM** : un LLM privilégié avec outils, un LLM en quarantaine sans outils.
5. **Code-Then-Execute** : l'agent écrit un programme formel qui appelle outils et LLM non privilégiés.
6. **Context-Minimization** : retirer le prompt utilisateur du contexte après traitement.

CaMeL `[papier]` : séparation flux de contrôle et flux de données avec capabilities :
77 % des tâches AgentDojo résolues avec sécurité prouvable, contre 84 % sans défense.
AgentDojo `[papier]` : attaque « important message » à 47,69 % de succès sur GPT-4o ;
filtre d'outils → 6,84 % (lecture seule) ; les auteurs concluent qu'« une percée dans
la distinction instructions/données sera nécessaire ».

Containment `[blog]` (Anthropic) : « Design for containment at the environment layer
first, then steer behavior at the model layer » ; « The weakest layer is the one you
built yourself ». Sandboxes lus : Seatbelt, bubblewrap + seccomp, conteneurs à deux
phases (réseau puis hors ligne), microVM par session (AgentCore), MXC (Copilot),
Actions éphémères avec pare-feu anti-exfiltration.

### 5.2 Référentiels de menaces

**OWASP LLM Top 10 2026** `[spec]` (publié 2026-08) : LLM01 Prompt Injection ;
LLM02 Sensitive Information Disclosure ; **LLM03 Excessive Agency** (monte de la
sixième à la troisième place) ; LLM04 Supply Chain ; LLM05 Data and Model Poisoning ;
LLM06 Unbounded Consumption ; LLM07 Misinformation ; **LLM08 Hidden Context Exposure**
(remplace System Prompt Leakage) ; LLM09 Vector and Embedding Weaknesses ; LLM10
Improper Output Handling.

**OWASP Top 10 for Agentic Applications 2026** `[agrégateur]` (date lue 2025-12-09) :
ASI01 Agent Goal Hijack ; ASI02 Tool Misuse and Exploitation ; ASI03 Agent Identity
and Privilege Abuse ; ASI04 Agentic Supply Chain Compromise ; ASI05 Unexpected Code
Execution ; ASI06 Memory and Context Poisoning ; ASI07 Insecure Inter-Agent
Communication ; ASI08 Cascading Agent Failures ; ASI09 Human-Agent Trust
Exploitation ; ASI10 Rogue Agents.

**OWASP Agentic AI Threats and Mitigations v1.0** `[agrégateur]` : T1 Memory Poisoning,
T2 Tool Misuse, T3 Privilege Compromise, T4 Resource Overload, T5 Cascading
Hallucination, T6 Intent Breaking, T7 Misaligned and Deceptive Behaviors, T8
Repudiation, T9 Identity Spoofing, T10 Overwhelming Human-in-the-Loop, T11 Unexpected
RCE, T12 Agent Communication Poisoning, T13 Rogue Agents, T14 Human Attacks on
Multi-Agent Systems, T15 Human Manipulation.

**CSA MAESTRO** `[doc]` : sept couches (Foundation Models, Data Operations, Agent
Frameworks, Deployment and Infrastructure, Evaluation and Observability, Security and
Compliance, Agent Ecosystem) ; menaces par couche puis inter-couches.

**MITRE ATLAS** `[doc]` : plateforme « Agentic AI » depuis 2026.05 ; techniques
d'agents ajoutées à cadence mensuelle (tool credential harvesting, tool poisoning,
escape to host, autonomous reconnaissance, autonomous attack orchestration en 2026.08).

**Microsoft AI Red Team** `[blog]` : taxonomie sur deux axes, sécurité contre sûreté et
nouveau contre existant ; memory poisoning comme cas d'école.

### 5.3 Modes de défaillance du modèle lui-même

- Agentic Misalignment `[blog]` : sur 16 modèles en scénario d'objectifs conflictuels,
  chantage jusqu'à 96 % (Opus 4, Gemini 2.5 Flash). La menace interne inclut le modèle.
- Auto mode Claude Code `[blog]` : classificateur avec 0,4 % de faux positifs et 17 % de
  faux négatifs sur actions trop zélées ; arrêt après trois refus consécutifs.
- DeepMind `[blog]` : traiter les agents comme des « insider threats », modélisation
  MITRE ATT&CK, métriques coverage, recall, time-to-response ; sur un million de tâches
  de coding agent, les événements signalés viennent surtout de « misinterpretation or
  overeagerness ».
- OpenAI `[agrégateur]` : pénaliser les « mauvaises pensées » dans la chaîne de
  raisonnement pousse le modèle à cacher son intention ; ne pas optimiser directement
  la chaîne surveillée.

### 5.4 Identité et autorisation des agents

Convergence constatée `[spec]`/`[doc]` :

1. L'agent est un client OAuth, le serveur d'outils un resource server (MCP).
2. Délégation d'entreprise par ID-JAG (RFC 8693 puis RFC 7523) ; Okta Cross App
   Access l'implémente sur MCP.
3. Identité d'agent de premier rang dans l'annuaire : Microsoft Entra Agent ID (agent
   identities distinctes des service principals, sponsor humain, journaux marqués) ;
   Google Agent Identity (SPIFFE, X.509 renouvelé toutes les 24 heures) ; AgentCore Identity.
4. Workloads : SPIFFE/SPIRE et IETF WIMSE.
5. Trafic web : Web Bot Auth (HTTP Message Signatures RFC 9421, en-tête `Signature-Agent`),
   WG IETF webbotauth.
6. Paiements : AP2 (mandats vérifiables), UCP Identity Linking, x402.

Pratique minimale : une identité par agent, scopes minimaux avec step-up, jetons à
audience liée, chaîne de délégation auditable, aucun passthrough.

### 5.5 Réglementation et normes

| Cadre | État au 2026-09-08 | Portée agents |
|---|---|---|
| EU AI Act `[doc]` | GPAI et gouvernance depuis 2025-08-02 ; Omnibus IA (Regulation 2026/1744) en vigueur 2026-07-27 : haut risque Annexe III reporté au 2027-12-02, Annexe I au 2028-08-02 ; transparence Art. 50 maintenue au 2026-08-02 | Aucune catégorie « agent » ; un agent relève du GPAI (si modèle) ou du haut risque (par usage) |
| NIST `[doc]` | AI RMF 1.0 en révision ; profil GenAI AI 600-1 ; AI Agent Standards Initiative (2026-02-17) ; COSAiS overlays SP 800-53 single-agent et multi-agent en draft ; concept paper NCCoE identité et autorisation des agents | Rien de contraignant, drafts à suivre |
| ISO/IEC 42001:2023, 42005:2025 `[non vérifié]` | Système de management IA certifiable ; évaluation d'impact | Aucune clause « agents » identifiée |
| IETF `[doc]` | WG wimse, aipref, webbotauth ; ID-JAG adopté ; drafts OAuth « on-behalf-of-user » | En cours |
| W3C `[doc]` | AI Agent Protocol CG, WebAgents CG | Aucune recommandation |

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/chart.svg" width="28" height="28" alt=""> 6. Évaluations, observabilité, benchmarks

### 6.1 Doctrine d'évaluation

- Commencer petit : une vingtaine de requêtes représentatives, 20 à 50 tâches issues de
  vrais échecs ; « 0% pass@100 usually signals broken tasks » (Anthropic `[blog]`).
- Grader l'état final, pas la trajectoire exacte : les agents trouvent d'autres chemins
  (Anthropic `[blog]`) ; mais juger la trajectoire ET la réponse finale quand l'efficacité
  compte (ADK `[doc]`).
- Trois familles de graders : code, LLM-as-judge, humain ; distinguer capability evals
  (taux bas) et regression evals (proche de 100 %) ; pass@k contre pass^k (consistance)
  (Anthropic `[blog]`, τ-bench `[papier]`).
- « Adopt eval-driven development » ; valider les juges LLM contre des labels humains
  avant d'optimiser le coût ; les LLM discriminent mieux qu'ils ne notent (pairwise) ;
  biais de position et de longueur (OpenAI `[doc]`).
- Métriques de trajectoire standard : `trajectory_exact_match`, `in_order_match`,
  `any_order_match`, `precision`, `recall`, `single_tool_use` (Vertex `[doc]`) ; modes
  `strict`, `unordered`, `subset`, `superset` (AgentEvals `[doc]`).
- Agent-as-a-Judge `[papier]` : 2,36 % du coût humain, alignement 88-92 %.
- Anti-patterns : métriques génériques (BLEU, perplexité), évaluation « vibe-based »
  (OpenAI `[doc]`) ; un reviewer prompté pour trouver des manques en trouvera toujours,
  limiter aux findings de correctness (Claude Code `[doc]`).
- État du terrain `[doc]` (LangChain, 1 340 répondants, fin 2025) : observabilité
  déployée à 89 % contre evals hors ligne à 52,4 % ; la qualité reste l'obstacle n°1.

### 6.2 Observabilité standardisée

OpenTelemetry GenAI `[spec]` : source normative dans le dépôt dédié
`semantic-conventions-genai` ; **tout est en statut Development**, rien de stable.

- Spans : nom `{gen_ai.operation.name} {gen_ai.request.model}` ; opérations `chat`,
  `embeddings`, `create_agent`, `invoke_agent`, `invoke_workflow`, `plan`,
  `execute_tool`, opérations mémoire ; attributs requis `gen_ai.operation.name`,
  `gen_ai.provider.name` ; contenu des messages en opt-in seulement.
- Span outil : `execute_tool {gen_ai.tool.name}`, `gen_ai.tool.type` parmi `function`,
  `code_interpreter`, `retrieval`, `web_search`.
- Métriques : `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`,
  `gen_ai.invoke_agent.duration`, `gen_ai.invoke_agent.inference_calls`,
  `gen_ai.invoke_agent.tool_calls`, `gen_ai.execute_tool.duration`.
- MCP : span `{mcp.method.name} {target}` ; propagation W3C Trace Context dans
  `params._meta` (`traceparent`, `tracestate`, `baggage`).
- Outils tiers alignés : Langfuse (OTLP, mapping `gen_ai.*`), Phoenix (OpenInference),
  Honeycomb (semconv v1.40.0), Datadog, Braintrust, AgentCore Observability.
- Production `[blog]` : tracer les patterns de décision sans le contenu des
  conversations ; checkpoints et reprise ; rainbow deployments ; retries ; le
  sous-agent synchrone est le goulot connu.

### 6.3 Benchmarks : ce qu'ils mesurent et ce qu'il faut en retenir

Les scores de tête 2026 marqués `[agrégateur]` doivent être recoupés avant citation ;
seuls les benchmarks avec page primaire lue sont notés `[doc]` ou `[papier]`.

| Benchmark | Mesure | Métrique de coût ou de fiabilité | Repère lu |
|---|---|---|---|
| SWE-bench Verified / Pro | Issues réelles ; Pro = tâches longues multi-langages | HAL fournit le dollar par tâche | Pro `[doc]` : tête à 61,5 % ; Verified `[agrégateur]` : tête ≈ 95 %, meilleur open ≈ 80 % |
| Terminal-Bench 2.1 | 89 tâches terminal, cinq essais | Prix par million de tokens affiché | `[doc]` tête 79,1 % |
| τ-bench, τ²-bench | Agent + utilisateur simulé + politique | **pass^k** | `[papier]` GPT-4o sous 50 % pass^1, sous 25 % pass^8 |
| GAIA2 | 1 000 scénarios, sept capacités | Pareto score contre coût et temps | `[doc]` |
| OSWorld, WebArena | Tâches OS et web réelles | Étapes | Humain 72,36 % et 78,24 % ; frontier `[agrégateur]` ≈ 85 % et 74 % |
| ARC-AGI-3 | Environnements interactifs sans consigne | Efficacité d'acquisition | `[doc]` humains 100 %, frontier 0,51 % au lancement |
| Vending-Bench 2 | 365 jours simulés | Solde en dollars | `[agrégateur]` ; tous les modèles dérivent sur horizons > 20 M tokens `[papier]` |
| HAL | 9 benchmarks, 26 597 rollouts | **Pareto exactitude contre coût** | `[papier]` « 100× plus cher pour +1 % » ; plus d'effort de raisonnement réduit l'exactitude dans la majorité des runs |
| Aider Polyglot | 225 exercices, six langages | **Dollars par run** | `[doc]` 88,0 % à 29,08 $ contre 81,3 % à 10,37 $ pour le même modèle en effort bas |
| MCP-Atlas, MCPUniverse, Toolathlon | Usage réel de serveurs MCP et d'applications | Tours par tâche | `[doc]` Toolathlon tête 78,4 % ; MCPUniverse GPT-5 43,72 % |
| AgentDojo | 97 tâches, 629 injections | Utilité contre taux de succès d'attaque | `[papier]` |
| METR time horizon | Durée humaine des tâches réussies à 50 % | Doublement 89 à 131 jours | `[blog]` Opus 4.5 à 320 min ; mesures au-delà de 16 h non fiables |
| GDPval, SWE-Lancer, TheAgentCompany | Valeur économique réelle | Dollars gagnés, tâches d'entreprise | `[papier]` |

Leçons transversales : la vérification est le goulot (« verification gap ») ; les
versions « Verified » des benchmarks corrigent 1,4 à 5,2 % d'inflation d'évaluateur ;
HAL a surpris des agents cherchant le benchmark sur HuggingFace, d'où l'inspection
de logs par LLM ; le bruit d'infrastructure fausse les evals agentiques (Anthropic).

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/bolt.svg" width="28" height="28" alt=""> 7. Économie du workflow

Leviers prouvés, par ordre d'impact attendu sur un workflow Grimoire.

| Levier | Chiffres lus | Source |
|---|---|---|
| Prompt caching | Anthropic : lecture 0,1× (0,025× sur Fable 5.1), écriture 1,25× (5 min) ou 2× (1 h), quatre breakpoints, minimums 512 à 4 096 tokens selon modèle ; OpenAI GPT-5.6+ : 0,1× lecture, 1,25× écriture, TTL 30 min, quatre écritures explicites ; Gemini : cache implicite, minimum 2 048 à 4 096 tokens. Invalidation : tout changement de préfixe, d'outils, de `tool_choice`, d'effort ou de thinking | `[doc]` |
| Batch | −50 % chez les trois éditeurs ; fenêtre 24 h ; cumulable avec le cache | `[doc]` |
| Effort et thinking | Anthropic `effort` low à max, `low` recommandé pour les sous-agents, `xhigh` pour le coding long, `max` « coût significatif pour gains faibles » ; effort par message préserve le cache ; `budget_tokens` rejeté sur 4.7+ ; OpenAI `reasoning.effort` low recommandé pour tool-use et planification ; Gemini `thinking_level` remplace `thinking_budget` | `[doc]` |
| Routing et cascades | FrugalGPT : jusqu'à −98 % de coût à qualité GPT-4 ; RouteLLM : plus de 2× moins cher ; AFlow : petits modèles > GPT-4o à 4,55 % du coût ; MaAS −6 à −45 % par requête | `[papier]` |
| Petits modèles pour sous-tâches | Orchestrateur fort + workers économiques ; Agent Distillation : 0,5B à 3B atteignent le palier supérieur ; SWEET-RL : 8B ≈ GPT-4o | `[blog]`, `[papier]` |
| Chargement différé d'outils | Réduction de plus de 85 % sur un contexte multi-MCP typique de 55k tokens | `[doc]` |
| Appels programmatiques et parallèles | −37 % de tokens ; latence ÷ 3,7 | `[blog]`, `[papier]` |
| Anti-overthinking | Sélectionner les trajectoires à faible score d'overthinking : −43 % de coût, +30 % de performance sur SWE-bench Verified | `[papier]` |
| Compaction et sous-agents | Sous-agent = 1 000 à 2 000 tokens rendus pour une exploration entière | `[blog]` |
| Sleep-time compute | Précalcul hors ligne : −5× de compute au test, −2,5× de coût amorti | `[papier]` |
| Caching sémantique | Hit rate 61,6 à 68,8 %, −68,8 % d'appels, précision > 97 % ; faible sur requêtes volatiles | `[papier]` |
| Agent-as-a-Judge | Évaluation à 2,36 % du coût humain | `[papier]` |
| Plafonds explicites | `max_budget_usd`, `max_turns`, `maxTurns`, limites de tours et de handoffs (Strands), budgets Dogwood (AgentCore) | `[doc]` |

Loi empirique à retenir : les tokens dépensés expliquent l'essentiel de la
performance (Anthropic) et la frontière de Pareto est plate (HAL). Un workflow qui
n'expose pas son coût par tâche résolue n'est pas pilotable.

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/resilience.svg" width="28" height="28" alt=""> 8. Modes de défaillance documentés

### 8.1 MAST : quatorze modes, prévalence mesurée `[papier]`

Berkeley, 1 642 traces, sept frameworks, accord inter-annotateurs κ = 0,88.

| Catégorie | Mode | Prévalence |
|---|---|---|
| Conception du système | FM-1.1 Disobey task specification | 11,8 % |
| Conception du système | FM-1.2 Disobey role specification | 1,5 % |
| Conception du système | FM-1.3 Step repetition | 15,7 % |
| Conception du système | FM-1.4 Loss of conversation history | 2,8 % |
| Conception du système | FM-1.5 Unaware of stopping conditions | 12,4 % |
| Désalignement inter-agents | FM-2.1 Conversation reset | 2,2 % |
| Désalignement inter-agents | FM-2.2 Fail to ask for clarification | 6,8 % |
| Désalignement inter-agents | FM-2.3 Task derailment | 7,4 % |
| Désalignement inter-agents | FM-2.4 Information withholding | 0,85 % |
| Désalignement inter-agents | FM-2.5 Ignored other agent's input | 1,9 % |
| Désalignement inter-agents | FM-2.6 Reasoning-action mismatch | 13,2 % |
| Vérification | FM-3.1 Premature termination | 6,2 % |
| Vérification | FM-3.2 No or incomplete verification | 8,2 % |
| Vérification | FM-3.3 Incorrect verification | 9,1 % |

Lecture : les trois modes dominants (répétition, incohérence raisonnement-action,
conditions d'arrêt) sont des défauts de harness, pas de modèle.

### 8.2 Anti-patterns publiés par les éditeurs

| Anti-pattern | Conséquence | Source |
|---|---|---|
| Session fourre-tout, corrections répétées au-delà de deux | Contexte pollué ; effacer et repartir | Claude Code `[doc]` |
| Fichier d'instructions sur-spécifié | « Claude ignores half of it » | Claude Code `[doc]` |
| Compaction trop agressive | Perte de contexte subtil | Anthropic `[blog]` |
| Auto-évaluation, QA superficielle, stubbing de fonctionnalités, « context anxiety » | Fausse complétion | Anthropic `[blog]` |
| Déclaration prématurée de fin de projet | Travail inachevé | Anthropic `[blog]` |
| Guardrail unique ; guardrails sans authn/authz | Protection insuffisante | OpenAI `[doc]` |
| Input guardrails supposés actifs après un handoff | Ils ne tournent que sur le premier agent | OpenAI SDK `[doc]` |
| Reasoning items omis entre tool calls | Raisonnement cassé, cache invalidé | OpenAI `[doc]`, Gemini `[doc]` |
| Boucle sans condition de terminaison ; cycle de graphe non borné | Boucle infinie | ADK `[doc]` |
| Branches parallèles supposées partager l'état | Aucun partage automatique | ADK `[doc]` |
| Sortie structurée forcée | Supprime l'usage des outils | ADK `[doc]` |
| Allowlist réseau vue comme sûre | Exfiltration par injection | OpenAI Shell `[doc]` |
| Confiance au contenu d'écran ou d'outil comme autorisation | Escalade de privilèges | OpenAI Computer use `[doc]` |
| Jetons dans les URL ; serveurs MCP non vérifiés | Fuite de credentials | MCP `[spec]`, OpenAI `[doc]` |
| Sous-agents en écriture parallèle sur le même fichier | Décisions incompatibles | Cognition `[blog]`, Codex `[doc]` |
| Débat multi-agents par défaut | Coût élevé, gain rarement supérieur à Self-Consistency | `[papier]` |
| Reviewer prompté pour trouver des manques | Sur-ingénierie | Claude Code `[doc]` |
| Breakpoint de cache sur contenu variable | Cache jamais réutilisé | Anthropic `[doc]`, Manus `[blog]` |

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/sparkle.svg" width="28" height="28" alt=""> 9. Frontière 2026 : prometteur mais non stabilisé

À suivre, à expérimenter derrière un flag, à ne pas prendre pour acquis.

| Sujet | Ce qui est démontré | Ce qui manque | Sources |
|---|---|---|---|
| Auto-optimisation des workflows | ADAS +13,6 pts DROP ; AFlow petits modèles > GPT-4o à 4,55 % du coût ; GEPA bat GRPO avec 35× moins de rollouts ; AlphaEvolve récupère 0,7 % du compute Google ; Darwin Gödel Machine SWE-bench 20 → 50 % ; ADK expose `GEPARootAgentPromptOptimizer` | Une fonction d'évaluation automatique fiable ; sans elle rien ne converge ; inclure le coût dans l'objectif (Pareto) | `[papier]`, `[doc]` |
| Skills auto-écrits et apprentissage continu | SkillsBench +16,6 pts avec skills curatés ; ACE playbook évolutif ; ALMA | SkillLearnBench : aucune méthode ne domine, gains limités aux workflows réutilisables | `[papier]` |
| Harness engineering | Planner/Generator/Evaluator ; agents en arrière-plan ; Harness Agent MAF, AgentCore Harness GA, Vercel `HarnessAgent` | Coût en tokens et latence ; hypothèses de harness à re-tester à chaque modèle | `[blog]`, `[doc]` |
| Horizon long | METR doublement 89-131 jours ; `/goal` ; worktrees isolés | Mesures au-delà de 16 h non fiables ; dérive sur 20 M tokens (Vending-Bench) | `[blog]`, `[papier]` |
| Computer use | OSWorld au-delà de la baseline humaine `[agrégateur]` ; toolsets production Anthropic, Preview Gemini | ARC-AGI-3 : 0,51 % pour les frontier sur environnements non instruits ; injections ; « avoid irreversible actions without human oversight » | `[doc]` |
| Agent teams et multi-agent natif API | Claude Code teams ; Responses multi-agent beta ; Codex subagents | Expérimental ; pas de reprise de session ; coût | `[doc]` |
| MCP stateless et MRTR | Révision 2026-07-28 courante | SDK et serveurs encore majoritairement sur 2025-11-25 ; Roots, Sampling en sursis | `[spec]` |
| Recursive Language Models | +26 % contre compaction, entrées cent fois la fenêtre | Recherche, coût de REPL | `[papier]` |
| Politique temporelle hors du code | AgentCore Dogwood : approbation préalable, compteurs, budgets par session ; OWASP Agent Control Standard | Standardisation en cours | `[doc]`, `[agrégateur]` |
| Identité d'agent de premier rang | Entra Agent ID, Google Agent Identity SPIFFE, ID-JAG | Drafts IETF, NCCoE en labo | `[doc]` |
| Open weight | Écart moyen 4 mois / 8 points ECI ; quasi nul sur Toolathlon, τ², BFCL ; ≈ 15 pts sur SWE-bench Verified | Chiffres 2026 à recouper | Epoch `[doc]`, `[agrégateur]` |

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/handshake.svg" width="28" height="28" alt=""> 10. Correspondance Grimoire ↔ industrie

Mise à jour 2026-09-12, audit de positionnement (`docs/audits/positionnement-2026-09-12.md`),
suite à la version 3.41.0 → 3.45.0. Statuts : **aligné** (même pratique, même
niveau) ; **en avance** (Grimoire va plus loin que la pratique publiée) ; **à
combler** (l'industrie a une pratique prouvée que Grimoire n'a pas encore, ou
pas au même niveau). Un écart comblé ou partiellement comblé porte la
référence de l'issue ou de la PR qui l'a fait bouger, conformément à la
section 12.

| Protocole ou mécanisme Grimoire | Équivalent industriel | Écart | Statut |
|---|---|---|---|
| SOG, point d'entrée unique et dispatch invisible (`orchestrator-gateway.md`) | Manager pattern OpenAI, Coordinator ADK, Harness Agent MAF | Même architecture ; Grimoire ajoute la détection de zones d'ombre et le batching de questions | aligné |
| HUP, seuils de confiance vert/jaune/rouge (`honest-uncertainty-protocol.md`) | « Why language models hallucinate » (récompenser l'abstention) ; MAST FM-2.2 | Aucun éditeur n'expose un protocole équivalent côté agent ; l'industrie le traite à l'entraînement | en avance |
| QEC, remontée groupée des questions (`question-escalation-chain.md`) | Elicitation MCP, `interrupt()` LangGraph, `request_confirmation` ADK, HITL OpenAI | Grimoire groupe les questions ; l'industrie les pose une à une | en avance |
| CVTL, vérification croisée (`cross-validation-trust.md`) | Evaluator-optimizer, Writer/Reviewer séparés, Agent-as-a-Judge | Aligné sur la séparation générateur-évaluateur ; **pass^k livré** (`grimoire.evals.schemas.pass_hat_k`, amendement A3 du protocole d'évals, 2026-09-08) mais seulement au niveau campagne — aucune campagne ne l'a encore consommé et il n'est pas branché sur CVTL elle-même ; coût par tâche mesuré une fois à la main (rejeu dispatch, #307, 2026-09-11), jamais instrumenté | aligné |
| PCE, débats productifs (`productive-conflict-engine.md`) | Multi-agent debate | La recherche montre un gain rarement supérieur à Self-Consistency à coût bien supérieur ; réserver PCE aux décisions, pas à la production | à cadrer |
| AMN et SHP, maillage et huddles (`agent-mesh-network.md`, `selective-huddle-protocol.md`) | Agent teams Claude Code, Swarm Strands, handoffs | Aligné sur le plafond d'échanges P2P ; la limite de cinq échanges avant notification SOG répond à MAST FM-1.3 et FM-1.5 | aligné |
| ARG, graphe de relations (`agent-relationship-graph.md`) | Graphe d'orchestration OpenAI, Graph ADK/Strands | Aligné ; Grimoire l'utilise pour le routage, l'industrie pour l'exécution | aligné |
| ELSS, journal d'événements partagé (`event-log-shared-state.md`) | Checkpointers LangGraph, task ledger Magentic, mailbox agent teams | **Export en spans OTel GenAI livré** (`TraceLedger.export_otel_jsonl`, un span `invoke_agent` par trace plus un `execute_tool` par appel d'outil, format semconv exact — #322, 2026-09-08). **Comblé le 2026-09-12** (#139) : `grimoire.missions.trace` corrèle par `task_id`/`traceId` le Mission Ledger, le TraceLedger (outils, gates, dispatch d'agent) et, s'il existe, l'export OTel lui-même, en CLI (`grimoire task trace`) comme dans le cockpit ; `grimoire task trace-export --format otel` reste une commande manuelle, mais son résultat est désormais lu, pas seulement produit | aligné |
| CC, Completion Contract (`cc-reference.md`, `cc-verify.sh`) | « Give Claude a check it can run », Stop hooks, Ralph loop | Aligné ; **renforcé par #428** (3.45.0) : le gate de `flow run --executor dispatch` exécute désormais l'acceptance structurée d'un node, pas seulement la conformité de l'enveloppe JSON — corrige un faux vert réel trouvé par le rejeu #307 (nœud fermé vert alors que sa suite `pytest` ne s'exécutait pas) | aligné |
| Standard agentique : `evidence-gated-fsm`, `mission-evidence-ledger`, gates de preuve | Trace grading OpenAI, evals ADK, HAL | Grimoire exige la preuve avant clôture ; l'industrie évalue après coup. Le gate d'acceptance structurée (#428) est la preuve la plus récente que l'exigence porte sur l'exécution réelle, pas la forme de la réponse | en avance |
| `tool-mediation-gate`, `governed-hook-gateway`, registre de sûreté des hooks en mode shadow/canary/enforced | Cotation de risque par outil (OpenAI), Policy Cedar/Dogwood (AgentCore), OWASP Agent Control Standard | Même idée ; Dogwood ajoute des politiques temporelles par session (compteurs, budgets) que Grimoire n'a pas. Vérifié au code le 2026-09-12 (`grimoire/policies/`) : aucun compteur ni budget par session, intact | à combler |
| `provider-routing-contract`, `provider-cost-slo`, `model-routing.yaml` | Routing et cascades (FrugalGPT, RouteLLM), `effort` par sous-agent | Aligné sur le principe. **Première mesure réelle faite** (#307, rejeu 2026-09-11, deux runs complets sur flow réel) : la cascade coûte 56,4 % du coût opus seul par nœud complété, contre 21,5 % projeté par la mesure isolée du lot 0 (#308) — l'écart venait du gate qui fermait vert sur la conformité d'enveloppe plutôt que sur l'acceptance réelle, corrigé par #428. Reste à combler : cette mesure est un rejeu ponctuel, pas une instrumentation continue du coût par tâche résolue | à combler |
| `governed-memory-policy`, composition mémoire, Mémoire OS | Memory Bank, AgentCore Memory, Letta, memory tool | Aligné ; ajouter la validation externe des écritures contre le memory poisoning (OWASP T1, ASI06). Revérifié le 2026-09-12 (recherche `memory poisoning`/`validation externe` dans le code) : intact, aucun mécanisme trouvé | à combler |
| `advanced-context-orchestrator`, `context-contract.yaml`, capsule de contexte PreCompact | Context engineering Anthropic, write/select/compress/isolate | Aligné ; la capsule PreCompact est une pratique que peu d'hôtes formalisent | en avance |
| Agent Skills du kit (`.github/skills`, `skill-forge` avec gate qualité) | agentskills.io, `skills-ref validate`, SkillsBench | Aligné sur le format ; ajouter la validation `skills-ref` et un eval par skill (evals.json), toujours intact au 2026-09-12. Changement de contexte à noter : les archétypes ont été refaits (#375, #380, 3.42.0) et l'attachement d'un skill à son agent porteur est devenu la règle par défaut (#377/#372, doctrine `docs/artifact-doctrine.md`) — le nombre de skills transversaux a chuté, mais cela ne comble pas l'écart de validation | à combler |
| Hooks Forge via gateway (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact, Subagent*, Stop) | Familles d'événements Claude Code, Codex, Gemini, Copilot, Kiro | **Partiellement comblé** : `PostToolUseFailure` et `SubagentStart` sont désormais des `HookEvent` exploités par le kit (#321, fusionné le 2026-09-08 dans l'après-midi, donc après la rédaction de cette référence le même jour) ; `TaskCreated`, `TaskCompleted` et `InstructionsLoaded` restent absents de `grimoire/hosts/events.py` au 2026-09-12 | à combler |
| Persona d'entrée injectée par SessionStart | Aucun hôte ne sait démarrer dans un agent (constat 2026-08-29) | Pratique propre à Grimoire | en avance |
| Pont MCP du kit (`grimoire` serveur MCP) | MCP 2026-07-28 stateless, MRTR, `server/discover`, headers `Mcp-Method` | **Comblé pendant la revue de cet audit** (issue #436, PR #437, 2026-09-12) : plancher `mcp>=2.0,<3`, `server/discover` négocie 2026-07-28 par défaut, `initialize` d'un hôte 2025-06-18/2025-11-25 toujours servi sur la même connexion, aucun état entre appels hors fichiers du projet — vérifié par test. Roots/Sampling/Logging jamais câblés, rien à retirer. Transport HTTP/SSE et OTel `_meta` restent hors périmètre | aligné |
| Observabilité cockpit, `observability-policy.yaml` | OTel GenAI spans et métriques, Langfuse, Phoenix | **Émission livrée** : `otel_conventions.py` définit le vocabulaire semconv exact (`gen_ai.provider.name`, spans `invoke_agent`/`execute_tool`) et `TraceLedger.export_otel_jsonl` (#322) les produit réellement. **Consommation comblée le 2026-09-12** (#139) : `web/workspace/spaces/executer.js` (timeline par tâche, filtrable par source et gravité, drill-down depuis une carte du board) et `observer.js` (drill-down depuis un span qui porte `grimoire.task_id`) la consomment côté cockpit ; l'ancien `web/observability.html` (`framework/tools/observatory.py`) n'a pas bougé et reste hors périmètre de ce correctif | aligné |
| Sécurité : garde des surfaces de contrôle, patterns destructifs | Six patterns Beurer-Kellner, CaMeL, sandbox à deux phases | Ajouter un pattern Plan-Then-Execute explicite pour les tâches qui lisent du contenu externe ; documenter l'isolation d'exécution. Revérifié le 2026-09-12 : intact, aucune occurrence trouvée | à combler |
| Identité des agents | Entra Agent ID, SPIFFE, ID-JAG | Grimoire n'a pas d'identité par agent ; hors périmètre tant que les agents restent locaux | à suivre |
| **Nouveau** — système émergent non-choix → proposition → acceptation (`grimoire.proposals`, `agent_miss_counts`, #394/#395/#402, 3.43.0/3.43.1) | Skills auto-écrits (section 9) : SkillsBench, ACE playbook évolutif | Grimoire propose sur un seuil mécanique de non-choix répétés (jamais 1), par gabarit sans appel LLM, avec acceptation humaine explicite obligatoire (`grimoire proposals accept`) ; l'industrie évalue des skills déjà écrits par un LLM (SkillsBench) ou consolide un playbook en continu à l'exécution (ACE). Aucun des deux ne fait du taux d'échec de dispatch un déclencheur de proposition — mécanisme distinct, pas un même pattern appliqué plus tôt | en avance |
| **Nouveau** — règle de fraîcheur des agents (`compute_agent_freshness`, #396/#398, 3.43.0) | — | Aucun équivalent trouvé dans le corpus de sources de la section 11 : signale un agent non choisi depuis N jours (défaut 90), ne le retire ni ne le déprécie jamais automatiquement | en avance |
| **Nouveau** — cœurs Rust optionnels comme oracle de correction, jamais de performance (#354 et ses six ports : `policies`, `schema/validator`, `hosts`, `flows`, `dispatch`, `traces`) | Differential testing / second implémentation comme oracle (pratique d'ingénierie reconnue, non nommée dans le corpus de sources listées section 11) | Mesuré (commentaire #354, 2026-09-11) : le cœur Rust est plus lent que le Python qu'il remplace sur les cinq micro-benchmarks, écart dans le bruit de mesure en macro — décision de Guilhem : conservé pour les défauts trouvés par le compilateur (sept corrigés à ce jour), pas pour la vitesse. Aucune roue publiée (assumé) | aligné (hors corpus) |

Priorités issues de cette carte, à instruire comme issues produit dans le dépôt
Grimoire-kit (jamais comme chantiers d'atelier) :

1. Faire courir pass^k en continu et instrumenter le coût par tâche résolue directement dans les gates de preuve, pas seulement en campagne ponctuelle ou en rejeu manuel (#307).
2. Validation externe des écritures mémoire.
3. Politiques temporelles par session sur le tool-mediation-gate.
4. Réparer #427 (un override d'agent en copie intégrale ne reçoit plus jamais les mises à niveau du kit, sans aucun signal de `doctor`) — promesse de personnalisation de la doctrine des artefacts non tenue par le code, distincte d'un écart face à l'industrie.

(Migration du pont MCP vers la révision 2026-07-28 : comblée pendant la revue de cet audit, issue #436, PR #437, 2026-09-12. Cockpit branché sur l'export OTel et timeline unifiée par tâche : comblé le 2026-09-12, issue #139.)

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/folder-tree.svg" width="28" height="28" alt=""> 11. Registre des sources

Pages lues le 2026-09-08 sauf mention. Les pages inaccessibles ce jour sont listées à
la fin avec la meilleure URL connue.

### Anthropic

- Building effective agents (2024-12-19) : <https://www.anthropic.com/engineering/building-effective-agents>
- Effective context engineering for AI agents (2025-09-29) : <https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents>
- Effective harnesses for long-running agents (2025-11-26) : <https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents>
- How we built our multi-agent research system (2025-06-13) : <https://www.anthropic.com/engineering/multi-agent-research-system>
- Writing effective tools for agents (2025-09-11) : <https://www.anthropic.com/engineering/writing-tools-for-agents>
- Advanced tool use (2025-11-24) : <https://www.anthropic.com/engineering/advanced-tool-use>
- Equipping agents for the real world with Agent Skills (2025-10-16) : <https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills>
- Demystifying evals for AI agents (2026-01-09) : <https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents>
- Harness design for long-running application development (2026-03-24) : <https://www.anthropic.com/engineering/harness-design-long-running-apps>
- How we built Claude Code auto mode (2026-03-25) : <https://www.anthropic.com/engineering/claude-code-auto-mode>
- Scaling Managed Agents (2026-04-08) : <https://www.anthropic.com/engineering/managed-agents>
- How we contain Claude across products (2026-05-25) : <https://www.anthropic.com/engineering/how-we-contain-claude>
- Agentic Misalignment (2025-06-20) : <https://www.anthropic.com/research/agentic-misalignment>
- Claude Code : best practices, sub-agents, hooks, skills, memory, agent teams, workflows, headless, MCP : <https://code.claude.com/docs/en/>
- Agent SDK : overview, agent loop, subagents, sessions, hooks : <https://code.claude.com/docs/en/agent-sdk/overview>
- Plateforme : prompt caching, batch, pricing, tool use, tool search, programmatic tool calling, memory tool, context editing, compaction, effort, extended thinking, structured outputs, files, computer use : <https://platform.claude.com/docs/en/>
- Agent Skills specification : <https://agentskills.io/specification>

### OpenAI

- A practical guide to building agents (PDF, non daté) : <https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf>
- Agents SDK Python (agents, tools, guardrails, handoffs, multi-agent, sessions, tracing, MCP, HITL, sandbox) : <https://openai.github.io/openai-agents-python/>
- Docs API : prompt caching, batch, flex, fast mode, background, tools, function calling, tool search, programmatic tool calling, shell, computer use, MCP et connecteurs, conversation state, reasoning, multi-agent, models, pricing, changelog, deprecations, evals, graders, trace grading, agent evals : <https://developers.openai.com/api/docs/>
- Codex : AGENTS.md, approvals et sécurité, permission modes, prompting, subagents, MCP, cloud, models, changelog : <https://learn.chatgpt.com/docs/>
- Guardrails library : <https://github.com/openai/openai-guardrails-python>
- AGENTS.md : <https://agents.md/> et <https://github.com/openai/agents.md>

### Google, DeepMind, A2A

- ADK 2.0 (agents, workflows, graphs, tools, sessions, context, callbacks, plugins, evaluate, optimize, observability, safety, A2A, deploy) : <https://adk.dev/>
- A2A specification 1.0 et guides : <https://a2a-protocol.org/latest/specification/>
- A2A ships v1.0 (2026-03-12) : <https://a2a-protocol.org/latest/blog/>
- Gemini API : function calling, thinking, caching, batch, live, computer use, structured output, models, pricing, interactions : <https://ai.google.dev/gemini-api/docs/>
- Gemini CLI et hooks : <https://geminicli.com/docs/> ; Antigravity : <https://antigravity.google/docs/> ; Jules : <https://jules.google/docs>
- Agent Engine, Memory Bank, Agent Identity, evaluation agents : <https://docs.cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/overview>
- AlphaEvolve (2025-05-14), AI co-scientist (2025-02-19), Chain of Agents, Gemini 2.5 report, Secure AI Agents (Díaz, Kern, Olive 2025), Securing the future of AI agents (2026-06-18) : <https://deepmind.google/> et <https://research.google/>
- AP2 : <https://ap2-protocol.org/> ; UCP : <https://ucp.dev/>

### Protocoles, standards, gouvernance

- MCP specification, changelogs, authorization, tools, elicitation, deprecated, governance, SEP : <https://modelcontextprotocol.io/specification/latest>
- MCP registry : <https://registry.modelcontextprotocol.io/>
- Agentic AI Foundation : <https://aaif.io/>
- OpenTelemetry GenAI semantic conventions : <https://github.com/open-telemetry/semantic-conventions-genai>
- OWASP LLM Top 10 2025 et 2026, Agentic Threats, Top 10 Agentic Applications, Securing Agentic Applications Guide, AIVSS : <https://genai.owasp.org/>
- CSA MAESTRO : <https://cloudsecurityalliance.org/blog/2025/02/06/agentic-ai-threat-modeling-framework-maestro>
- MITRE ATLAS changelog : <https://github.com/mitre-atlas/atlas-data>
- NIST AI RMF, AI Agent Standards Initiative, COSAiS : <https://www.nist.gov/itl/ai-risk-management-framework>, <https://www.nist.gov/artificial-intelligence/ai-agent-standards-initiative>, <https://csrc.nist.gov/projects/cosais>
- EU AI Act et Omnibus IA : <https://digital-strategy.ec.europa.eu/en/policies/regulatory-framework-ai>
- IETF wimse, aipref, webbotauth : <https://datatracker.ietf.org/wg/>
- AG-UI : <https://docs.ag-ui.com/introduction> ; x402 : <https://www.x402.org/> ; llms.txt : <https://llmstxt.org/> ; Arazzo : <https://spec.openapis.org/arazzo/latest.html>
- Entra Agent ID : <https://learn.microsoft.com/en-us/entra/agent-id/what-are-agent-identities> ; Okta XAA : <https://developer.okta.com/docs/concepts/xaa/>

### Frameworks et plateformes

- Microsoft Agent Framework (overview, workflows, harness, releases) : <https://learn.microsoft.com/en-us/agent-framework/>
- Microsoft Foundry Agent Service : <https://learn.microsoft.com/en-us/azure/ai-foundry/agents/overview>
- Taxonomy of Failure Modes in Agentic AI Systems (2025-04-24) : <https://www.microsoft.com/en-us/security/blog/2025/04/24/new-whitepaper-outlines-the-taxonomy-of-failure-modes-in-ai-agents/>
- Magentic-One : <https://www.microsoft.com/en-us/research/articles/magentic-one-a-generalist-multi-agent-system-for-solving-complex-tasks/>
- GitHub Copilot cloud agent, custom agents, hooks, instructions, CLI, sandboxes, SDK : <https://docs.github.com/en/copilot/>
- Strands Agents et multi-agent patterns : <https://strandsagents.com/docs/>
- Amazon Bedrock AgentCore (what is, harness, policy) : <https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html>
- Kiro specs, steering, hooks : <https://kiro.dev/docs/>
- AWS Well-Architected Generative AI Lens (2025-11-19) : <https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/generative-ai-lens.html>
- LangGraph, LangChain 1.0, Deep Agents, LangSmith, AgentEvals : <https://docs.langchain.com/>
- How to think about agent frameworks (2025-04-20), Context engineering for agents (2025-07-02), State of Agent Engineering : <https://www.langchain.com/blog/>
- Cognition, Don't Build Multi-Agents (2025-06-12) : <https://cognition.com/blog/dont-build-multi-agents>
- Manus, Context Engineering lessons (2025-07-18) : <https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus>
- CrewAI, Pydantic AI, smolagents, Mastra, Vercel AI SDK, LlamaIndex Workflows, DSPy, Haystack, Letta, Temporal, Inngest AgentKit, Dapr Agents, Agno, goose, Cline, Cursor, Devin, Factory, Aider, OpenHands : documentation officielle de chaque projet
- Spec Kit : <https://github.com/github/spec-kit> ; OpenSpec : <https://github.com/Fission-AI/OpenSpec> ; Ralph Wiggum : <https://github.com/anthropics/claude-code/tree/main/plugins/ralph-wiggum>
- Langfuse, Arize Phoenix, Braintrust, Helicone, Datadog LLM Observability, Honeycomb Agent Observability (2026-05-12) : documentation officielle

### Recherche et benchmarks (arXiv sauf mention)

- ReAct 2210.03629 ; Reflexion 2303.11366 ; Tree of Thoughts 2305.10601 ; Self-Consistency 2203.11171 ; Plan-and-Solve 2305.04091 ; Toolformer 2302.04761 ; Voyager 2305.16291 ; Generative Agents 2304.03442 ; CAMEL 2303.17760 ; MetaGPT 2308.00352 ; ChatDev 2307.07924 ; AutoGen 2308.08155 ; SWE-agent 2405.15793 ; Agentless 2407.01489 ; Agent Workflow Memory 2409.07429 ; Agent-as-a-Judge 2410.10934 ; CodeAct 2402.01030 ; LATS 2310.04406 ; STaR 2203.14465 ; MemGPT 2310.08560 ; A-MEM 2502.12110 ; Graph of Thoughts 2308.09687 ; Self-Refine 2303.17651 ; Mixture-of-Agents 2406.04692
- ADAS 2408.08435 ; AFlow 2410.10762 ; MaAS 2502.04180 ; GEPA 2507.19457 ; TextGrad 2406.07496 ; MIPROv2 2406.11695 ; Darwin Gödel Machine 2505.22954 ; Agent Laboratory 2501.04227 ; AI Scientist-v2 2504.08066 ; ALMA 2602.07755 ; Absolute Zero 2505.03335
- MAST 2503.13657 ; NVIDIA SLM 2506.02153 ; Illusion of Thinking (Apple) et réponse 2506.09250 ; Lost in the Middle 2307.03172 ; Chroma Context Rot <https://research.trychroma.com/context-rot> ; METR 2503.14499 et <https://metr.org/time-horizons/> ; Vending-Bench 2502.15840 ; Beurer-Kellner et al. 2506.08837 ; CaMeL 2503.18813 ; Overthinking 2502.08235 ; Stop Overvaluing MAD 2502.08788 ; How Many Tools 2605.24660 ; ACE 2510.04618 ; RLM 2512.24601 ; Sleep-time Compute 2504.13171 ; Adaptation survey 2512.16301 ; Agentic CPT 2509.13310 ; Agent Data Protocol 2510.24702 ; SkillsBench 2602.12670 ; SkillLearnBench 2604.20087
- FrugalGPT 2305.05176 ; RouteLLM 2406.18665 ; s1 2501.19393 ; LLMCompiler 2312.04511 ; Agent Distillation 2505.17612 ; GPT Semantic Cache 2411.05276
- τ-bench 2406.12045 ; τ²-bench 2506.07982 ; GAIA 2311.12983 ; GAIA2 <https://huggingface.co/blog/gaia2> ; AgentBench 2308.03688 ; SWE-bench Pro <https://labs.scale.com/leaderboard/swe_bench_pro_public> ; Terminal-Bench 2.1 <https://tbench.ai/> ; OSWorld 2404.07972 ; BrowseComp 2504.12516 ; HLE <https://labs.scale.com/leaderboard/humanitys_last_exam> ; MLE-bench 2410.07095 ; PaperBench 2504.01848 ; RE-Bench 2411.15114 ; HCAST 2503.17354 ; Cybench <https://cybench.github.io/> ; ARC-AGI-3 <https://arcprize.org/> ; GDPval 2510.04374 ; Aider Polyglot <https://aider.chat/docs/leaderboards/> ; MCP-Bench 2508.20453 ; MCPUniverse 2508.14704 ; MCP-Atlas <https://labs.scale.com/leaderboard/mcp_atlas> ; AgentDojo 2406.13352 ; Toolathlon <https://toolathlon.xyz/> ; SWE-Lancer 2502.12115 ; TheAgentCompany 2412.14161 ; AppWorld 2407.18901 ; HAL 2510.11977 et <https://hal.cs.princeton.edu/>
- Epoch AI, open/closed gap (2026-05-29) : <https://epoch.ai/data-insights/open-closed-eci-gap>

### Non accessibles le jour de la veille

Ces pages n'ont pas pu être lues ; leur contenu n'est cité qu'avec l'étiquette
`[non vérifié]` ou `[agrégateur]`.

- Pages `openai.com/index/*` (hallucinations, CoT monitoring, deliberative alignment, governing agentic systems, Preparedness Framework, AgentKit, harness engineering).
- Whitepapers Kaggle Agents, Agents Companion, Introduction to Agents, Context Engineering.
- SAIF agents ; PDF de la taxonomie Microsoft ; PDF du guide OWASP Securing Agentic Applications ; pages ISO ; matrice ATLAS ; leaderboards swebench.com, webarena.dev, livecodebench, appworld, the-agent-company, Vending-Bench 2 (andonlabs), BFCL (table non rendue).

<img src="../docs/assets/divider.svg" width="100%" alt="">

## <img src="../docs/assets/icons/wrench.svg" width="28" height="28" alt=""> 12. Protocole de mise à jour de cette référence

Cette référence est un artefact produit du kit. Elle se périme : les tarifs, les
révisions de protocole et les scores de tête bougent chaque trimestre.

### Déclencheurs de révision

- Nouvelle révision de MCP ou d'A2A publiée.
- Nouvelle génération de modèle chez l'un des trois laboratoires.
- Nouvelle liste OWASP, nouvelle plateforme ATLAS, texte réglementaire entrant en vigueur.
- Un agent Grimoire constate qu'une règle de la section 1 est contredite par une source primaire plus récente.
- Au plus tard un trimestre après la date de veille en tête de fichier.

### Méthode

1. Relancer la collecte par axe (laboratoires, protocoles et normes, frameworks,
   recherche et benchmarks) avec des sous-agents en lecture seule, sources primaires
   uniquement, et exiger pour chaque affirmation l'étiquette de provenance.
2. Ne modifier une règle de la section 1 que si deux sources primaires indépendantes
   la contredisent.
3. Passer chaque chiffre `[agrégateur]` en `[doc]` ou `[papier]` dès que la page
   primaire est lisible, ou le retirer.
4. Remettre à jour la section 10 : un écart comblé change de statut avec la référence
   de l'issue ou de la PR du kit qui l'a comblé.
5. Mettre à jour la date de veille, puis passer la charte documentaire
   (CommonMark strict, aucune estimation temporelle, icônes SVG uniquement).

### Critère d'entrée d'une source

Une source entre dans ce fichier si elle est primaire (éditeur, organisme de
normalisation, laboratoire, article évalué ou preprint avec chiffres reproductibles) et
si elle apporte soit une règle, soit un chiffre, soit un contrat. Les billets
d'opinion, les agrégateurs et les comparatifs commerciaux n'entrent pas, sauf comme
ordre de grandeur explicitement étiqueté.
