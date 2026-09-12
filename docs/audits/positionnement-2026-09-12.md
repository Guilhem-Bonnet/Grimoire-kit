<p align="right"><a href="../../README.md">README</a></p>

# <img src="../assets/icons/microscope.svg" width="32" height="32" alt=""> Audit de positionnement — 2026-09-12

Demande de Guilhem, 2026-09-12 : « est-ce qu'on est bien situé, est-ce qu'on exploite bien le
potentiel, qu'est-ce qui pourrait être mieux ». Portée : `framework/agentic-industry-reference.md`
(sections 1, 9, 10, 11, 12), le CHANGELOG 3.41.0 → 3.45.0, le code là où la prose ne suffisait pas,
et les issues #354, #307, #405/#419, #427, #430, #139, #280. Méthode : chaque affirmation ci-dessous
est sourcée par un fichier, une commande ou une issue — pas une opinion.

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/clipboard.svg" width="28" height="28" alt=""> Verdict en dix lignes

1. Le kit applique correctement ses propres douze règles opposables ; aucune violation trouvée en
   relisant le code au 2026-09-12.
2. La section 10 datait déjà le jour de sa rédaction : deux PR (#321 hooks, #322 export OTel) ont
   fusionné le 2026-09-08 dans l'après-midi, après que la référence (#315, matin) a été écrite —
   deux lignes étaient fausses avant même la première lecture.
3. Le potentiel réel de la période 3.41.0 → 3.45.0 n'est pas dans le tableau de correspondance : le
   système émergent (non-choix → proposition → fraîcheur) et le port Rust comme oracle de
   correction sont des mécanismes sans équivalent publié trouvé dans le corpus de sources.
4. Deux mesures réelles (pas des projections) ont été faites cette période : les cœurs Rust ne
   gagnent rien (#354, 2026-09-11), et la cascade de dispatch coûte 56,4 % du coût opus par nœud
   réel contre 21,5 % projeté au lot 0 (#307) — l'écart venait d'un gate qui fermait vert sur la
   forme, pas sur l'acceptance, corrigé par #428 dans la même fenêtre.
5. L'export OTel GenAI existe et est conforme au schéma (#322) ; rien ne le consomme côté cockpit —
   c'est un cas net de mécanisme livré mais non branché, pas un manque de mécanisme.
6. Le plus grave des sept points n'est pas un écart face à l'industrie mais une promesse du kit non
   tenue par le code : un override d'agent en copie intégrale ne reçoit plus jamais les mises à
   niveau du kit, sans le moindre signal (#427, ouverte).
7. Aucune source du registre n'est stale : la veille date du 2026-09-08, quatre jours avant cet
   audit — rien ne dépasse le seuil de six mois sur MCP, A2A ou les modèles.
8. La frontière 2026 (section 9) reste correcte telle quelle : le kit n'y a rien changé cette
   période, à l'exception du dispatch en cascade qui a maintenant une mesure réelle au lieu d'une
   mesure isolée.
9. Le triage produit/atelier est resté propre : les six ports Rust, le CLI paresseux et la
   restructuration de `hosts/decisions.py` sont tous documentés comme des changements du produit
   (Grimoire-kit), la Forge s'est contentée de consommer 3.45.0.
10. Le tableau de la section 10 est refait ci-dessous avec vingt et une lignes (dix-huit reprises,
    trois nouvelles) et six priorités au lieu de cinq.

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/handshake.svg" width="28" height="28" alt=""> Tableau section 10 refait (2026-09-12)

Reproduit tel qu'appliqué dans `framework/agentic-industry-reference.md` — voir ce fichier pour la
version canonique et sa légende de statuts.

| Protocole ou mécanisme Grimoire | Équivalent industriel | Écart au 2026-09-12 | Statut |
|---|---|---|---|
| SOG (`orchestrator-gateway.md`) | Manager pattern OpenAI, Coordinator ADK | Inchangé | aligné |
| HUP (`honest-uncertainty-protocol.md`) | Abstention à l'entraînement, MAST FM-2.2 | Inchangé | en avance |
| QEC (`question-escalation-chain.md`) | Elicitation MCP, HITL OpenAI | Inchangé | en avance |
| CVTL (`cross-validation-trust.md`) | Evaluator-optimizer, Agent-as-a-Judge | pass^k livré en campagne (`pass_hat_k`, A3, 2026-09-08), pas encore consommé ni branché sur CVTL ; coût par tâche mesuré une fois à la main (#307) | aligné |
| PCE (`productive-conflict-engine.md`) | Multi-agent debate | Inchangé | à cadrer |
| AMN/SHP | Agent teams, Swarm Strands | Inchangé | aligné |
| ARG (`agent-relationship-graph.md`) | Graphe d'orchestration OpenAI/ADK | Inchangé | aligné |
| ELSS (`event-log-shared-state.md`) | Checkpointers LangGraph, task ledger Magentic | Export OTel livré (#322) côté ledger ; non consommé côté cockpit (#139 ouverte) | à combler |
| CC (`cc-reference.md`) | « Give Claude a check it can run » | Renforcé par #428 : le gate exécute l'acceptance structurée, pas l'enveloppe | aligné |
| Standard agentique (`evidence-gated-fsm`, gates de preuve) | Trace grading OpenAI, HAL | #428 en est la preuve la plus récente | en avance |
| `tool-mediation-gate`, hooks shadow/canary/enforced | Policy Cedar/Dogwood | Aucun compteur ni budget par session trouvé dans `grimoire/policies/` | à combler |
| `provider-routing-contract`, `model-routing.yaml` | FrugalGPT, RouteLLM | Première mesure réelle (#307) : 56,4 % du coût opus par nœud, contre 21,5 % projeté (#308) ; pas une instrumentation continue | à combler |
| `governed-memory-policy` | Memory Bank, memory tool | Aucun mécanisme de validation externe trouvé | à combler |
| `advanced-context-orchestrator`, capsule PreCompact | Context engineering Anthropic | Inchangé | en avance |
| Agent Skills (`skill-forge`) | agentskills.io, SkillsBench | Skills attachés par défaut depuis #377/#372 (moins de skills transversaux) ; validation `skills-ref` et eval par skill toujours absents | à combler |
| Hooks Forge via gateway | Familles d'événements des hôtes | `PostToolUseFailure`/`SubagentStart` désormais exploités (#321, même jour, après la référence) ; `TaskCreated`/`TaskCompleted`/`InstructionsLoaded` absents | à combler |
| Persona d'entrée SessionStart | Aucun hôte équivalent | Inchangé | en avance |
| Pont MCP (`grimoire` serveur) | MCP 2026-07-28 | **Comblé pendant la revue de cette PR** (#436/#437, 2026-09-12) : plancher `mcp>=2.0,<3`, `server/discover` négocie 2026-07-28 par défaut | aligné |
| Observabilité cockpit | OTel GenAI, Langfuse, Phoenix | Émission conforme livrée (`otel_conventions.py`, #322) ; `/api/otel` sans appelant, `observability.html` sur l'ancien chemin (#139) | à combler |
| Sécurité, patterns destructifs | Beurer-Kellner, CaMeL | Aucun pattern Plan-Then-Execute trouvé | à combler |
| Identité des agents | Entra Agent ID, SPIFFE | Hors périmètre assumé | à suivre |
| **Nouveau** — système émergent non-choix→proposition (`grimoire.proposals`) | Skills auto-écrits, ACE | Seuil mécanique + acceptation humaine obligatoire, sans LLM ; pattern distinct, pas retrouvé publié | en avance |
| **Nouveau** — fraîcheur des agents (`compute_agent_freshness`) | — | Aucun équivalent trouvé dans le corpus | en avance |
| **Nouveau** — cœurs Rust comme oracle de correction | Differential testing (pratique générale, non nommée section 11) | Mesuré plus lent que Python (#354) ; conservé pour les défauts trouvés, pas la vitesse | aligné (hors corpus) |

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/temple.svg" width="28" height="28" alt=""> Douze règles opposables : le kit se les applique-t-il ?

| Règle | Le kit se l'applique-t-il ? | Preuve ou contre-exemple |
|---|---|---|
| Simplicité d'abord | Oui | `docs/artifact-doctrine.md` : trois échecs (agents fantômes, artefacts éphémères morts, outil d'ajout muet) tous dus à un artefact créé sans nécessité écrite ; la doctrine qui en résulte est le contre-exemple corrigé, pas un contre-exemple actuel |
| Workflow ≠ agent | Oui | `docs/artifact-doctrine.md` : « la décision peut-elle être écrite à l'avance, et qui invoque ? » distingue skill/prompt (workflow) d'agent (jugement), et sert de gate à la création |
| Quand un agent se justifie | Oui | Archétypes refaits en 3.42.0 (#375, #380) : sept experts stack à faisceau identique redevenus sept skills sous un agent unique quand ils ne justifiaient pas une frontière d'outils propre |
| Mono-agent d'abord | Oui | Même série de PR : `stack-engineer` généraliste plus skills, au lieu de sept agents |
| Le contexte est fini | Oui | #379 (3.42.0) : un agent sans `context:` déclaré ne charge plus le contexte partagé (~193 tokens) par défaut ; mesuré, pas supposé |
| Tokens = performance | Oui | #377 mesure 1480 tokens/tour pour dix skills transversales contre 0 pour les mêmes attachées ; c'est la mesure qui a motivé le changement de défaut, pas une estimation |
| Outils : peu, nets, consolidés | Partiel | `tool_boundary` obligatoire par agent depuis #370, mais aucune limite chiffrée du nombre d'outils actifs par agent n'a été vérifiée cette période |
| Vérification indépendante | Oui, renforcé | #428 (3.45.0) corrige exactement une auto-évaluation clémente : un gate qui se contentait de vérifier la forme de la réponse d'un ouvrier plutôt que sa vraie acceptance |
| Instruction ≠ contrainte | Oui | Hooks en `mode: shadow` par défaut au registre de sûreté (`hook-safety-registry.json`), promotion explicite requise — la contrainte est dans le gateway, pas dans la prose d'un agent |
| Défense en couches | Oui | `tool-mediation-gate` + `governed-hook-gateway` + registre shadow/canary/enforced : trois couches distinctes avant qu'un hook devienne bloquant |
| Isoler l'action des données non fiables | Contre-exemple partiel | Aucun pattern Plan-Then-Execute trouvé pour les tâches qui lisent du contenu externe (dispatch d'un ouvrier délégué, sortie non fiable par construction) ; c'est un écart déjà listé section 10, pas une violation active mais un point aveugle |
| Le harness vieillit | Oui | Le bench #354 est exactement ce test de stress : mesurer si l'hypothèse « Rust est plus rapide » tient encore, la trouver fausse, et documenter la décision malgré l'hypothèse infirmée |

Verdict : dix règles sur douze appliquées avec preuve directe, une appliquée partiellement
(consolidation d'outils, jamais vérifiée), une en écart actif documenté (isolation de l'action face
à une sortie d'ouvrier non fiable — cf. section 10, ligne sécurité).

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/sparkle.svg" width="28" height="28" alt=""> Frontière 2026 : où se situe le kit

| Sujet | Position du kit | Preuve |
|---|---|---|
| Auto-optimisation des workflows | Absent, assumé | Aucune fonction d'évaluation automatique fiable dans le kit ; hors périmètre non documenté comme décision, simplement non entrepris |
| Skills auto-écrits et apprentissage continu | En avance sur un axe, absent sur l'autre | Le déclencheur de proposition (mécanique, seuil, acceptation humaine) n'a pas d'équivalent trouvé ; en revanche aucune consolidation de playbook façon ACE |
| Harness engineering | Aligné | Six cœurs Rust + CLI paresseux + découpage de `hosts/decisions.py` sont exactement des stress-tests d'hypothèses de harness (règle 12), avec mesure et décision documentées à chaque fois |
| Horizon long | Non instrumenté | Aucune mesure d'horizon long (type METR) trouvée sur les missions Grimoire |
| Computer use | Hors périmètre, non documenté comme tel | Le kit ne pilote pas de navigateur/OS ; absence cohérente avec son domaine (dev tooling), mais pas actée en décision explicite comme le sont A2A ou la roue Rust |
| Agent teams et multi-agent natif API | Aligné, en veille documentée | AMN/SHP répliquent le plafond d'échanges P2P des agent teams ; pas de client API multi-agent natif dans le kit — cohérent avec la décision « pas de client API dans le kit » |
| MCP stateless et MRTR | Comblé pendant la revue de cette PR | Migration vers 2026-07-28 livrée le 2026-09-12 (#436/#437) ; était en retard au moment de la rédaction initiale de cet audit |
| Recursive Language Models | Absent, non entrepris | Aucune trace dans le kit |
| Politique temporelle hors du code | En retard | Aucun compteur ni budget par session (tool-mediation-gate) ; écart connu, priorité 5 |
| Identité d'agent de premier rang | Absent, décision explicite | « Hors périmètre tant que les agents restent locaux » — décision assumée, pas un oubli |
| Cloud/A2A | Volontairement en veille | Décision documentée (#376, mémoire Forge) ; aucun mouvement cette période, cohérent avec la veille actée |
| Roue Rust publiée | Volontairement absente | Décision du 2026-09-09 (commentaire #354) : jamais de roue publiée, repli Python conservé ; tenue sur les six ports livrés à ce jour |

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/wrench.svg" width="28" height="28" alt=""> Sept points, classés

Coût : petit (une PR ciblée), moyen (plusieurs PR ou un mécanisme nouveau), lot (plusieurs
chantiers liés). Les points 1 et 2 sont des promesses du kit non tenues par le code — comptent
double par construction (leçon de l'audit du 2026-09-08).

1. **[Promesse non tenue] Un override d'agent en copie intégrale ne reçoit plus jamais les mises à
   niveau du kit, sans signal.** Fait : quatre agents sur quatre migrés 3.38.0 → 3.44.2 sont restés
   figés sur le texte de 3.38.0 après une refonte complète de leur archétype, `doctor` restant
   22/22. La doctrine des artefacts (#370) promet des overrides personnalisables ; le code ne
   signale pas leur péremption. Issue #427 (ouverte). Coût : moyen (signal `doctor`/cockpit par
   empreinte, puis override partiel `extends: kit`).

2. **[Promesse non tenue] L'export OTel GenAI livré (#322) n'est consommé par aucune page du
   cockpit.** Fait : `otel_conventions.py` et `TraceLedger.export_otel_jsonl` produisent des spans
   `invoke_agent`/`execute_tool` conformes au semconv ; `/api/otel` existe dans `forge_routes.py`
   et n'a aucun appelant dans `web/`, qui reste sur `framework/tools/observatory.py`. Issue #139
   (ouverte, portée exacte : timeline unifiée par tâche, `task_id` sur `GrimoireEvent`). Coût : lot
   (câblage `task_id` sur les événements de hooks, puis la page).

3. **[Écart industrie] Aucune politique temporelle par session sur le tool-mediation-gate.**
   Fait : recherche de `budget`/`counter`/`session_limit` dans `grimoire/policies/` sans résultat au
   2026-09-12. Pas d'issue dédiée trouvée. Coût : moyen.

4. **[Comblé pendant la revue de cette PR] Pont MCP migré vers la révision 2026-07-28.**
   Fait constaté à la rédaction initiale (2026-09-12, matin) : commentaires de
   `src/grimoire/mcp/server.py` citant 2025-03-26 et 2025-06-18 comme bornes basses du SDK. Fermé
   dans la même journée par #436/#437 (`mcp>=2.0,<3`, `server/discover` négocie 2026-07-28 par
   défaut) pendant que cette PR d'audit était en attente de fusion — conservé ici comme preuve que
   la méthode (fait daté, jamais figé) tient même sur un cycle de quelques heures.

5. **[Écart industrie] Coût par tâche résolue et pass^k non instrumentés en continu.**
   Fait : `pass_hat_k` existe (`grimoire.evals.schemas`, A3) mais seulement pour les campagnes
   d'évals ; le seul chiffre de coût par tâche résolue en cascade réelle vient d'un rejeu manuel
   (#307). Pas d'issue dédiée trouvée pour le brancher sur les gates. Coût : moyen.

6. **[Écart industrie, petit] `grimoire upgrade` (v2→v3) perd les commentaires YAML de
   `project-context.yaml`.** Fait : `tools/_common.py` charge en `typ="safe"` puis sauve en
   round-trip sur des données qui ne sont déjà plus une `CommentedMap` — trouvé en creusant #426.
   Issue #430 (ouverte). Coût : petit (aligner `load_yaml()` sur `typ="rt"`).

7. **[Optionnel, frontière] IntelliSense assistée par modèle local pour l'espace Source.**
   Fait : la voie déterministe (colorisation, correcteur, complétion) est livrée (#303, PR mergée,
   commentaire sur #280) ; la seconde piste (petit modèle Ollama, déjà détecté par `doctor`) reste
   non instruite. Issue #280, piste 2 (ouverte). Coût : lot, et explicitement de moindre priorité
   que la voie 1 déjà livrée.

<img src="../assets/divider.svg" width="100%" alt="">

## <img src="../assets/icons/folder-tree.svg" width="28" height="28" alt=""> Sources à rafraîchir

Aucune source du registre (section 11) ne dépasse six mois : la veille est datée du 2026-09-08,
quatre jours avant cet audit. Rien à rafraîchir dans l'immédiat sur MCP, A2A ou les modèles.

Point de vigilance pour le prochain cycle (section 12, déclencheurs de révision) : la révision MCP
citée comme cible (2026-07-28) et le blog A2A v1.0 (2026-03-12, soit six mois pile au 2026-09-12)
sont les deux sujets les plus susceptibles d'avoir bougé avant le déclencheur trimestriel
(2026-12-08 au plus tard). Aucun changement de génération de modèle chez les trois laboratoires
n'a été constaté dans cette veille ; rien ne déclenche de révision anticipée des règles de la
section 1 à ce jour.

<img src="../assets/divider.svg" width="100%" alt="">

Voir aussi : [`framework/agentic-industry-reference.md`](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/framework/agentic-industry-reference.md)
(la référence mise à jour par cet audit), [`docs/artifact-doctrine.md`](../artifact-doctrine.md),
[`docs/evals-protocol.md`](../evals-protocol.md).
