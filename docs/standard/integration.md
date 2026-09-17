# Intégration du standard agentique

Grimoire Kit ne remplace pas le corpus normatif agentique. Il sert de **kit consommable** pour appliquer ce corpus dans un projet réel : profils, templates, registres, limites d'outils, preuves et déclarations de conformité.

## Positionnement

| Surface | Responsabilité | Ne doit pas faire |
|---|---|---|
| Corpus normatif externe | Définit les obligations, contrôles, patterns et profils | Dépendre de Grimoire Kit |
| Grimoire Forge | Assemble et génère un kit cible à partir du profil choisi | Modifier la norme pendant une génération |
| Grimoire Kit | Fournit les artefacts exécutables ou copiables dans les projets | Se déclarer source normative |

Le pont vit dans :

- `framework/agentic-standard/profile-map.yaml`
- `framework/agentic-standard/templates/`
- `archetypes/agentic-standard/`

## Version du standard tracée

Le bridge ne redéfinit pas la norme : il la trace. Pour que « aligné sur le
standard » soit vérifiable, `profile-map.yaml` épingle la révision exacte du
corpus normatif contre laquelle il a été réconcilié :

```yaml
metadata:
  upstream_standard:
    remote: "https://github.com/Guilhem-Bonnet/processus-developpement-agentique.git"
    commit: "53b2c34258580bd965631bcc186b21947b44c71e"
    pinned_on: "2026-06-12"
```

`grimoire standard upstream` compare cette révision à la tête distante :

| Sortie | Sens |
|---|---|
| 0 | la révision épinglée est la tête distante |
| 2 | le standard a avancé — réconcilier le profile-map, puis mettre à jour `commit` |
| 3 | distant injoignable — non vérifié, ce qui n'est pas « à jour » |
| 1 | aucune révision épinglée |

La CI du bridge exécute cette vérification en avertissement : une dérive du
corpus est un signal à traiter, pas une raison de refuser un commit qui ne
l'a pas causée.

## Traçabilité vers la norme

`framework/agentic-standard/traceability.yaml` relie chaque artefact du bridge
aux exigences `AG-*` et aux contrôles `CTRL-*` de la norme, avec la citation
qui justifie le lien, et chaque profil du kit à un niveau de conformité
(`starter` → N1 … `production` → N5). Un lien n'est posé que si la norme nomme
l'objet que l'artefact produit ; une entrée sans lien dit pourquoi.

```bash
grimoire standard traceability --profile governed
grimoire standard traceability .            # sur un projet enrôlé : + verdict par artefact
grimoire -o json standard traceability .
```

La commande rend, pour les artefacts requis par le profil, les exigences et
contrôles satisfaits, puis les exigences obligatoires jusqu'au niveau atteint
que le kit ne couvre par aucun artefact — les trous, cumulés de N1 au niveau
demandé. Sur un projet enrôlé, elle ajoute le verdict que `standard verify`
rend sur chaque artefact (`ok`, `warning`, `error`, `absent`) et le nombre
d'exigences effectivement vérifiées. C'est la matrice qu'AG-AUD-001 exige :
une conformité déclarée reliée à exigence, contrôle, preuve **et verdict**,
plutôt qu'affirmée. La déclaration de conformité la cite dans sa section
`Traceability`.

Depuis le 2026-09-04 (#246), aucun niveau N1 à N5 ne laisse d'exigence obligatoire sans
artefact : les dix-sept trous que la matrice listait le 2026-09-03 sont
couverts par les artefacts ci-dessous. Ce qui reste partiel est écrit dans
la `note` de l'artefact concerné dans `traceability.yaml`.

## Artefacts obligatoires de la norme

Les artefacts que la norme exige et que les profils livrent, par premier
niveau où la norme les attend :

| Artefact | Exigences | Profils | Fichier |
|---|---|---|---|
| Claim ledger | AG-QUA-002 — une affirmation sans preuve reste une hypothèse | tous | `_grimoire-output/evidence/<task>/claim-ledger.md` |
| Dossier d'acceptation | AG-QUA-003 — le livrable est accepté ou refusé par celui qui le reçoit | tous | `_grimoire-output/evidence/<task>/acceptance-record.md` |
| Registre de rétention | AG-RET-001, -003, -004, -005 — destination de chaque artefact, remplaçante, nettoyages, purges suivies | tous | `_grimoire/standard/retention-registry.yaml` |
| Registre d'outils | AG-TOL-001, -003, -005 — risque et permissions, contrats MCP, capture des erreurs | dès `controlled` | `_grimoire/standard/tool-registry.yaml` |
| Registre d'incidents | AG-INC-001, -002, -003 — containment, correction, purge, prévention, récurrences | dès `controlled` | `_grimoire/standard/incident-registry.yaml` |
| Matrice risques / contrôles / preuves | AG-AUD-003, AG-QUA-005 — défauts IA plausibles anticipés | dès `controlled` | `_grimoire/standard/risk-control-matrix.yaml` |
| Registre des surfaces runtime | AG-TOL-007, AG-RET-006 — owner, mode, rétention, statut par surface | dès `governed` | `_grimoire/standard/runtime-surface-registry.yaml` |
| Registre des capacités | AG-DYN-001 à -005 — gap, durabilité, expiration, promotion justifiée | dès `governed` | `_grimoire/standard/capability-registry.yaml` |

Deux exigences sont portées par un champ d'artefact existant plutôt que par un
fichier : AG-ORC-004 par le bloc `wip:` d'`orchestration-policy.yaml` (WIP
borné, `open_delegation: forbidden`), AG-AUD-001 par la section `Traceability`
de `compliance-declaration.md`.

`grimoire standard verify` les lit tous. Un registre vierge est un
avertissement : il attend d'être rempli. Ce qui est une erreur, c'est une
déclaration fausse — un critère `passé` sans preuve, un livrable `accepté`
sans validateur, une source `superseded` sans remplaçante, un `on_critical`
qui n'est ni `stop` ni `reduce_autonomy`, une délégation ouverte — et, en
profil gouverné, une purge non suivie comme incident, un serveur MCP sans
scopes ni timeout, un incident fermé sans prévention, une capacité éphémère
sans expiration, un risque sans contrôle. Un projet déjà enrôlé reçoit les
fichiers manquants par `grimoire standard fix --apply` ; le bloc `wip:` et la
section `Traceability` arrivent par `grimoire up` si les deux fichiers n'ont
pas été édités, sinon ils se recopient depuis les gabarits.

## Profils de conformité opérationnelle

| Profil | Usage | Artefacts minimaux |
|---|---|---|
| `starter` | Individu ou petit projet qui veut un flow standard-aware léger | Mission Brief, Task Envelope, Evidence Pack, Claim Ledger, Acceptance Record, Retention Registry |
| `controlled` | Équipe qui veut gouvernance répétable et routage LLM explicite | Starter + LLM Provider Registry + Compliance Declaration + Tool, Incident et Risk registries |
| `orchestrated` | Multi-agents avec contexte avancé et documentation externe indexée | Controlled + board, mémoire, contexte, décisions, rules/hooks, orchestration, evidence gates, patterns |
| `governed` | Organisation avec politiques par environnement et audit | Orchestrated + score, remediation, risques acceptés et waivers |
| `production` | Flow critique avec dry-run, rollback, SLO et coûts | Governed + preuves de release gates et métriques critiques |

## Knowledge Base Indexer

La base de connaissance indexée est volontairement séparée de la mémoire :

- **Mémoire** : apprentissage persistant sur le projet, décisions, erreurs et signaux d'usage.
- **Contexte de session** : informations bornées injectées pour une tâche précise.
- **Base de connaissance** : documentation externe indexée depuis dossier, dépôt, URL, API, MCP, base de données ou stockage.

Un projet déclare ses sources dans `knowledge-source-registry.yaml`. Une source indexée n'est source de vérité que si elle est explicitement marquée comme telle pour le périmètre concerné.

## Compatibilité multi-provider LLM

Le flow ne doit pas dépendre implicitement d'un fournisseur unique. Le registre `llm-provider-registry.yaml` déclare :

- providers activés : GitHub Copilot, OpenAI/Codex, Anthropic Claude, Gemini, local, etc. ;
- capabilities autorisées : chat, code, reasoning, embeddings, multimodal ;
- politiques de données ;
- fallback chain ;
- métadonnées d'audit.

La règle est simple : pas d'appel récurrent à un provider ou modèle non déclaré.

Chaque fournisseur peut en plus déclarer, en option (issue #310, lot 2) :

- `currency` : `quota` (consommé sur un abonnement/CLI), `api` (facturé à l'appel) ou `local` (calcul local) ;
- `invocation` : commande headless (`claude -p {prompt} --model {model}`, `ollama run {model}`...) ;
- `models` : liste de `{id, tier}`, `tier` ∈ `cheap` | `mid` | `strong`.

Ces trois champs sont absents par défaut — un registre qui ne les déclare pas
continue de passer `verify`/`gates` sans rien changer. Déclarés, ils
alimentent `grimoire providers status` (tableau de disponibilité par palier
de coût, refroidissement après échec) et `grimoire providers cooldown <id>
--reason rate_limit|timeout` (enregistrer un échec depuis un hook ou un
script). Un `tier` ou une `currency` hors de ce vocabulaire est une erreur de
vérification, pas un avertissement.

Le choix provider est maintenant explicite au moment de l'initialisation :

```bash
grimoire standard detect-providers
grimoire standard init . --profile orchestrated --provider github-copilot
grimoire standard init . --profile orchestrated --providers github-copilot,anthropic,openai --provider-policy mixed
```

La détection ne lit pas les secrets. Elle ne remonte que des signaux non sensibles comme la présence d'un exécutable (`gh`, `codex`, `claude`, `gemini`, `ollama`) ou le fait qu'une variable d'environnement connue soit définie.

### Coût par tâche résolue et pass^k (`dispatch.cost_slo`)

Le pattern `provider-cost-slo` (production) gagne, depuis l'issue #442, un
contrôle qui lit en continu ce que le kit journalise déjà à chaque cascade
(`dispatch.outcome`, voir `grimoire dispatch stats` dans la
[référence CLI](../cli-reference.md#comptabilité-du-dispatch--coût-par-tâche-résolue-et-passk)) —
plus besoin d'une campagne d'évals manuelle pour savoir si le coût dérive ou
si un node rejoué réussit systématiquement. `llm-provider-registry.yaml`
déclare, en option, une section `dispatch_cost_slo` :

```yaml
dispatch_cost_slo:
  max_cost_per_resolved_task_usd: 2.0   # défaut si absent
  min_pass_k_rate: 0.8                  # défaut si absent
  min_resolved_observations: 5          # défaut si absent — sous ce seuil, INFO plutôt qu'un jugement
  min_pass_k_observations: 3            # défaut si absent — dénominateur indépendant du précédent
  enforce: false                        # défaut si absent — true transforme le WARN en FAIL
```

Toute la section, et chacune de ses clés, est optionnelle : absente, le
contrôle reste actif avec les valeurs par défaut ci-dessus plutôt que de se
taire faute de réglage. Le check `dispatch.cost_slo` rend alors, par
métrique (coût et pass^k jugés indépendamment, chacun avec son propre
plancher de données) :

- `INFO` s'il y a trop peu d'observations pour juger ;
- `WARN` si le coût par tâche résolue dépasse `max_cost_per_resolved_task_usd`,
  ou si le pass^k mesuré est sous `min_pass_k_rate` ;
- `FAIL` à la place du `WARN` ci-dessus, uniquement si `enforce: true` — un
  dépassement reste silencieux (`WARN`) par défaut, jamais bloquant sans
  décision explicite du projet.

Le cockpit n'affiche pas encore ces chiffres — seuls `grimoire dispatch
stats` et `grimoire standard verify`/`gate` les exposent pour l'instant.

## Installation dans un projet cible

```bash
grimoire init . -a minimal,agentic-standard
```

Ensuite, générer les artefacts selon le profil :

```bash
grimoire standard init . --profile orchestrated --provider github-copilot
grimoire standard verify . --profile orchestrated
grimoire standard audit . --profile orchestrated --markdown
```

Le profil choisi détermine les artefacts requis :

```text
_grimoire/standard/mission-brief.md
_grimoire/standard/compliance-declaration.md
_grimoire/standard/knowledge-source-registry.yaml
_grimoire/standard/llm-provider-registry.yaml
_grimoire/standard/task-board.yaml
_grimoire/standard/memory-policy.yaml
_grimoire/standard/context-contract.yaml
_grimoire/standard/decision-graph.yaml
_grimoire/standard/rule-packs.yaml
_grimoire/standard/hook-registry.yaml
_grimoire/standard/orchestration-policy.yaml
_grimoire/standard/evidence-gates.yaml
_grimoire/standard/pattern-catalog.yaml
_grimoire/standard/retention-registry.yaml
_grimoire/standard/tool-registry.yaml
_grimoire/standard/incident-registry.yaml
_grimoire/standard/risk-control-matrix.yaml
_grimoire-output/evidence/{task-id}/task-envelope.md
_grimoire-output/evidence/{task-id}/evidence-pack.md
_grimoire-output/evidence/{task-id}/claim-ledger.md
_grimoire-output/evidence/{task-id}/acceptance-record.md
```

### Activation de session (Claude Code)

`grimoire standard init` installe par défaut un hook `SessionStart`
Claude Code (`.claude/settings.json`) qui injecte la directive
d'activation au démarrage de chaque session — le mécanisme validé
40/40 par la campagne d'évals du 2026-07-09 (`evals/reports/`), là où
les artefacts seuls produisaient 0/40 d'engagement :

- la directive vit dans `.claude/activation-context.md` (éditable par
  projet, jamais écrasée si présente) ; c'est un gabarit : `{task_id}` y
  est remplacé, à chaque session, par la tâche courante — le claim actif
  du Mission Ledger, ou `GRIMOIRE_TASK_ID`, ou `bootstrap` (ordre complet
  dans la [référence CLI](../cli-reference.md#quelle-tâche-la-session-porte)) ;
- le hook exécute `grimoire-hook --host claude --event SessionStart`, qui
  rend la même directive que `grimoire standard activation-context`,
  portable et versionné avec le kit ;
- fusion non destructive dans un `settings.json` existant ; un fichier
  malformé est laissé intact (le hook est alors sauté avec un
  avertissement) ;
- opt-out : `grimoire standard init . --no-claude-hook`.

## Contenu externe : donnée, pas instruction

Une page web récupérée, un message inter-agents, une réponse d'API : rien ne
les distinguait d'une consigne une fois dans le contexte d'un agent. C'est
OWASP LLM01 et ASI01, et le principe commun aux six patterns de défense de
Beurer-Kellner — une donnée non fiable ingérée ne doit plus pouvoir déclencher
d'action conséquente.

**Récupérer une page.** Une seule entrée, côté agent comme côté code :

```bash
grimoire web fetch https://example.com/doc            # sortie enveloppée
grimoire web fetch https://example.com/doc --json     # + provenance externe
```

```python
from pathlib import Path
from grimoire.tools.untrusted import fetch_untrusted

page = fetch_untrusted("https://example.com/doc", project_root=Path("."))
contexte = page.render()      # bannière + marqueurs encadrant le corps
journal = page.to_dict()      # source, nonce, tampering, code de sortie, taille
```

`fetch_untrusted` exécute `framework/tools/web-browser.py` en **sous-processus**
— le script est en zone gelée et n'est jamais importé — puis enveloppe sa sortie.

**Le câblage est vérifié à deux endroits, pas déclaré.** Le manifeste dit
*quels outils existent* ; le catalogue de résolution dit *lequel appeler pour
une intention*. C'est le second que consulte un agent qui doit lire une page —
câbler le premier seul laissait le trou entier.

| Artefact livré | Ce qui doit y figurer | Check |
|---|---|---|
| `_grimoire/kit/tool-manifest.csv` | colonne `entrypoint` = `grimoire web fetch` sur la ligne du navigateur | `firewall.untrusted_output_unwrapped` |
| `_grimoire/kit/tools/tool-resolver.py` | la capacité `web-browsing` résout vers `grimoire web fetch` | `firewall.capability_resolves_unwrapped` |

Les deux sont des erreurs en `production` et des avertissements en `governed`.
Un projet qui ne livre ni l'un ni l'autre ne déclenche rien : il n'a pas de
navigateur à câbler. Sans ces contrôles, le pare-feu ne serait qu'un YAML
cochant des cases, et l'enveloppe du code que personne n'appelle.

`web-browser.py` reste exécutable à la main — il est en zone gelée, on ne peut
pas l'en empêcher. Ce qui change, c'est que plus rien dans ce que le kit livre
n'y envoie : le catalogue, le manifeste et la documentation nomment tous
`grimoire web fetch`, et un projet qui reviendrait en arrière échoue à la
vérification.

**Pourquoi le marqueur est aléatoire.** Une balise fixe (`BEGIN UNTRUSTED`) se
recopie dans la page : il suffirait à un attaquant d'écrire la balise de fin
pour sortir de l'enveloppe. Chaque enveloppe porte donc un identifiant tiré au
hasard à l'emballage, que la source ne peut pas connaître. Une page qui tente
malgré tout de recopier le sentinelle le voit neutralisé, et le fait est
signalé (`tampering: true`) — c'est un événement à journaliser, pas seulement
une chaîne à nettoyer.

**Messages inter-agents.** Le log partagé (`framework/event-log-shared-state.md`)
exige désormais un champ `origin` valant `user`, `agent` ou `external`. Un
message qui n'est pas d'origine `user` ne peut ni relayer une approbation ni
porter une instruction : `tag_event_payload()` refuse à l'écriture un `payload`
qui porte une clé d'autorité (`approved`, `authorized`, `instruction`,
`command`, `override`…). Un événement sans `origin` est traité comme `external`.

**Profil.** L'artefact `prompt-firewall.yaml` est requis à partir du profil
`production`. En `governed`, son absence produit l'avertissement
`firewall.artifact_missing` : le rendre obligatoire à ce palier aurait rendu
tout projet consommateur non conforme du jour au lendemain.

## Médiation des outils : le registre est comparé au réel

`tool-mediation-gate` est exigé par le profil `governed` depuis le premier
jour, avec `checks: []` : rien ne le vérifiait. `grimoire standard verify`
oppose désormais la règle `tools.mediated-before-use` en comparant le registre
aux serveurs MCP **réellement résolus à l'exécution** :

| Source lue | Portée |
|---|---|
| `.mcp.json` du projet | projet |
| `~/.claude.json` (y compris son entrée `projects.<racine du projet>`) | utilisateur |
| `~/.claude/settings.json` | utilisateur |

Un serveur de portée utilisateur est tout aussi appelable par l'agent que celui
du projet : l'ignorer ferait naître le vérificateur fail-open (décision 5 du
plan d'exécution du 2026-09-08).

Quatre façons d'échouer, erreur dès `governed`, avertissement en dessous :

| Check | Cause |
|---|---|
| `mediation.server_undeclared` | un serveur résolu n'est pas au registre |
| `mediation.server_risk_missing` | il y est, sans risque |
| `mediation.out_of_scope_without_reason` | il est mis hors périmètre sans motif |
| `mediation.registry_stale` | le registre inscrit un serveur que plus aucune source ne résout |
| `mediation.source_unreadable` | une source existe mais n'est pas du JSON valide — avertissement dès `governed`, erreur en `production` |

Le dernier cas est ce qui empêche le registre d'être rempli une fois pour
toutes : un registre périmé affirme une médiation qui n'a plus d'objet.

Déclarer un serveur volontairement hors périmètre, plutôt que l'omettre :

```yaml
mcp_servers:
  - id: MCP-001
    server: playwright
    owner: guilhem
    scopes: [read]
    timeout_s: 30
    logging: {requests: true, errors: true, secrets_masked: true}
    out_of_scope: true
    out_of_scope_reason: "navigateur de test local, jamais appelé par un agent en production"
```

La lecture des sources est sans secret : seules les **clés** de `mcpServers`
sont extraites. Les commandes, arguments et `env`, où vivent les jetons, ne sont
ni lus ni journalisés.

Une source **absente** est muette : un projet sans `.mcp.json` n'a rien à
déclarer. Une source **présente et illisible** ne l'est pas : le vérificateur ne
peut alors rien affirmer, et se taire confondrait « rien à déclarer » avec « je
n'ai pas pu regarder ». Elle produit donc `mediation.source_unreadable` —
avertissement dès `governed`, erreur en `production`, jamais un plantage : la
configuration cassée d'un autre outil ne doit pas emporter la vérification.

## Commandes runtime normatives

Les profils `orchestrated`, `governed` et `production` ne se limitent plus aux templates de gouvernance : ils exposent une première tranche exécutable du runtime standard.

```bash
grimoire standard board verify .
grimoire standard memory verify .
grimoire standard context verify .
grimoire standard context build . --task-id bootstrap
grimoire standard decision trace . --task-id bootstrap
grimoire standard decision explain . --task-id bootstrap
grimoire standard rules verify .
grimoire standard hooks verify .
grimoire standard hooks simulate . --phase pre_context_build --task-id bootstrap
grimoire standard gate check . --task-id bootstrap --target-state review
grimoire standard gate check . --task-id bootstrap --target-state released --profile governed --strict
grimoire standard gate check . --task-id bootstrap --strict --no-run
grimoire standard gate run-tests . --task-id bootstrap
grimoire standard task scaffold . --task-id bootstrap
grimoire standard task scaffold . --task-id bootstrap --dry-run
grimoire standard knowledge index . --task-id bootstrap
grimoire standard knowledge graph . --task-id bootstrap
grimoire standard knowledge verify . --task-id bootstrap
grimoire standard pattern list .
grimoire standard pattern show advanced-context-orchestrator .
grimoire standard events audit .
grimoire standard score . --task-id bootstrap
grimoire standard fix . --dry-run
grimoire standard fix . --apply
```

`grimoire standard memory verify` vérifie aussi le contrat Memory OS cible généré dans
`_grimoire/standard/memory-policy.yaml` : Redis reste la mémoire chaude TTL/streams/locks,
Weaviate devient la mémoire sémantique durable, Neo4j la projection graphe typée,
SQLite le sidecar/fallback local et Qdrant une source legacy de migration/rollback.
Les profils `governed` et `production` traitent une dérive de ce contrat comme une
erreur bloquante dans `standard gate check --strict`.

Les sorties opérationnelles restent dans `_grimoire-output/` :

- `context/{task-id}/context-bundle.yaml` : sources sélectionnées, mémoire injectée, contrat Memory OS, contraintes providers, routage provider évalué, redactions et preuves attendues ;
- `decisions/{task-id}/decision-trace.yaml` : traces task/context/memory/provider/agent/tool/state/release ;
- `knowledge/{task-id}/index-manifest.yaml` : sources, artefacts normatifs, patterns et checks ;
- `knowledge/{task-id}/knowledge-graph.yaml` : graphe local doc-to-graph des artefacts, sources folder autorisées et patterns ;
- `events/runtime-journal.jsonl` : journal des événements context, decision, knowledge, hooks, gates et score ;
- `events/applied-fixes.jsonl` : audit trail des remédiations sûres appliquées ;
- `standard/{task-id}/compliance-score.yaml` : score profil-aware avec dimensions pondérées.

Les commandes sont volontairement sûres : la simulation de hooks n'exécute aucune action externe, la remediation reste en dry-run par défaut et les chemins générés sont contraints au project root. Seule exception, délibérée : `grimoire standard gate run-tests` exécute réellement la commande de test connue du projet (voir « Acceptance reliée à une exécution réelle » ci-dessous) — c'est tout son objet, remplacer une déclaration de texte libre par un run vérifiable.

## Acceptance reliée à une exécution réelle

Une ligne `acceptance-record.md` marquée `passé` n'est plus, à elle seule, une
déclaration suffisante quand le projet a une commande de test connue (issue
#582 lot B, `docs/bench/diagnostic-surcout-kit-2026-09-17.md` §2). La commande
vient de `grimoire.core.execution_needs.resolve_need("test-runner", …)` :
déclarée explicitement (`needs.commands.test-runner` dans
`project-context.yaml`) ou détectée par marqueur (`pyproject.toml`,
`package.json`, `Cargo.toml`, `go.mod`).

```bash
grimoire standard gate run-tests . --task-id bootstrap
```

exécute cette commande et enregistre le verdict (code de sortie, sortie
tronquée à 2 Ko) dans `_grimoire-output/evidence/<task-id>/test-run.json` — ne
duplique pas d'exécuteur, réutilise `_run_checks`
(`grimoire.missions.dispatch`), le même primitif que
`flows.dispatch_executor` utilise déjà pour une `AcceptanceEvidence(kind="test")`
(issue #428). `standard verify` et `gate check` (pour les états `review`,
`accepted`, `released`) lisent ce fichier :

- une ligne `passé` sans run enregistré et vert, sur un projet à commande de
  test connue → `acceptance.passed_without_test_run` ;
- un projet sans commande de test détectable → comportement inchangé
  (déclaratif), signalé par `acceptance.no_test_command_detected` ;
- un run enregistré et vert, mais dont l'empreinte de l'arbre de travail
  diverge de celle recalculée à la vérification → `acceptance.test_run_stale`.

Un run vert n'est pas une preuve permanente : `record_acceptance_test_run`
calcule une empreinte de l'arbre de travail juste après avoir exécuté la
commande (`grimoire.core.standard_checks.tree_fingerprint.
compute_tree_fingerprint`) — après, pas avant : l'état de référence d'un run
est celui que les tests laissent derrière eux — et l'enregistre dans
`test-run.json` (`tree_fingerprint`). Dans un dépôt git : sha256 de
`git rev-parse HEAD` + `git status --porcelain=v1 -z` + `git diff HEAD` +
`(chemin, taille, mtime_ns)` de chaque fichier derrière une entrée non suivie
(`??`) du status (un fichier non suivi n'est représenté par git que par son
chemin ; sans ce complément, le retoucher après le run ne changerait jamais
l'empreinte). Hors dépôt git (ou si git échoue) : sha256 de la liste triée
`(chemin, taille, mtime_ns)` de tout fichier hors les mêmes exclusions.
Exclus des deux modes : `_grimoire-output/` (ce dossier est écrit par le
mécanisme lui-même — l'inclure invaliderait chaque run dès son écriture),
`.venv/`, `node_modules/`, `target/`, `.git/`, et les caches d'outillage non
déterministes qu'une commande de test régénère à chaque run sans que le code
change (`.pytest_cache/`, `__pycache__/`, `.ruff_cache/`, `.mypy_cache/`,
`.hypothesis/`, `.coverage`) — sans quoi, sur un projet sans `.gitignore`
adapté, un run se périmerait dès son propre enregistrement. Une empreinte
absente (ancien format, fichier altéré à la main) compte comme périmée —
garde fermée, jamais l'inverse.

**Transition WARN → FAIL.** Cette release (celle qui introduit ce mécanisme)
répond aux trois cas ci-dessus par un avertissement, quel que soit le profil —
aucun projet gouverné existant ne se retrouve bloqué du jour au lendemain par
un mécanisme qu'il ne connaissait pas encore. Une prochaine release
promouvra `acceptance.passed_without_test_run` (et `acceptance.test_run_stale`)
en erreur bloquante pour les profils `governed`/`production`, le temps que
les projets déjà gouvernés adoptent `gate run-tests` (ou une intégration CI
équivalente) dans leur boucle de clôture de tâche.

## Gate auto-suffisant : chemin, remède, scaffold, tests intégrés

Le banc à trois bras du 2026-09-17 (`docs/bench/diagnostic-surcout-kit-2026-09-17.md`)
a mesuré 31 tours médians pour le bras gouverné contre 6 pour Claude nu, à
succès égal. L'analyse tour par tour de 21 runs a isolé le plus gros poste :
une médiane de 11 tours par run (jusqu'à 22) passés à lire le source installé
du kit après un `FAIL missing context_bundle` qui ne disait ni où créer le
fichier ni quoi faire — 21 runs sur 21 ont ouvert `site-packages/grimoire/`.
Le lot G1 de l'issue #582 ferme ce trou en quatre pièces.

**Chaque manque nomme son chemin et son remède.** Un check
`gate.<clé>_missing` de `check_evidence_gates` (donc de `gate check`, du hook
`Stop` et de l'outil MCP) porte désormais le chemin attendu et une commande
shell copiable, la racine citée en absolu :

```text
gate.context_bundle_missing (_grimoire-output/context/T-1/context-bundle.yaml):
  Artefact de gate manquant : context_bundle — attendu à
  _grimoire-output/context/T-1/context-bundle.yaml ; remède :
  grimoire standard task scaffold /chemin/du/projet --task-id T-1
```

La table clé → chemin → remède vit en un seul endroit,
`grimoire.core.standard_checks.gate_remedy` : les artefacts par tâche
(`task_envelope`, `evidence_pack`, `claim_ledger`, `acceptance_record`,
`context_bundle`, `decision_trace`) se scaffoldent ; `compliance_score` se
calcule (`standard score`) ; `task_board` et `memory_policy` appartiennent au
profil (`standard init`, ou `task board export` quand un ledger existe). Le
rendu texte de `gate check` affiche tous les checks (plus seulement la clé nue
des manques), et `verify` fait suivre chaque chemin manquant de son remède.

**`grimoire standard task scaffold`** crée, s'ils manquent seulement, tous les
artefacts par tâche que le profil actif exige, et ne réécrit jamais un fichier
présent. Chaque squelette est pré-rempli avec ce que le kit sait déjà :
identifiant et titre, critères d'acceptation (une ligne `AC-00n … à vérifier`
par critère, lus dans le Mission Ledger — ADR-007 — ou à défaut sur le
board), profil, état courant, niveau de risque, `HEAD` git, commande de test
résolue par `resolve_need("test-runner", …)`, date du jour. Le résumé
placeholder du pack de preuve (`- Outcome:` vide) est remplacé par un résumé
généré (titre + critères). Un squelette frais passe `gate check` — y compris
vers `review` — sans autre motif de refus qu'un motif de fond (tests rouges,
critère déclaré passé sans preuve). Une tâche inconnue du ledger et du board
est refusée (`grimoire task add` d'abord) : pas de dossier orphelin. `--dry-run`
rend le plan sans rien écrire, ni fichier ni événement de journal.

**Le hook `SessionStart` scaffolde la tâche active** d'un projet reconnu
gouverné (`_is_governed`, lot A), silencieusement et de façon idempotente :
les artefacts existent avant la première commande de l'agent. Quand tout
existe déjà — chaque session après la première — le coût se limite à six
`stat` (mesuré 0,18 ms en médiane, borné à 100 ms par
`test_session_start_scaffold_noop_stays_under_budget`) ; aucun YAML n'est lu,
le ledger n'est pas ouvert.

**`gate check --strict` exécute lui-même `gate run-tests`** quand la tâche
doit une preuve d'exécution (`in_progress`, `review`, `accepted`, `released`),
qu'une commande de test est connue et qu'aucun run vert et frais n'est
enregistré (même empreinte d'arbre que `test-run.json`), puis évalue comme
avant. Un run rouge frais est une erreur `acceptance.test_run_failed` ; un run
rouge périmé ne l'est plus (le code a pu changer). `--no-run` restaure
l'ancien comportement : aucune exécution, évaluation de ce qui est enregistré.
Le hook `Stop` n'exécute jamais de tests : il évalue via
`check_evidence_gates`, qui lit le run enregistré sans le relancer.

## Ce qui est maintenant prêt

Le kit possède une première structure pour transformer le standard en flow actionnable sans polluer le corpus normatif :

1. cartographie profils -> artefacts ;
2. archétype installable `agentic-standard` ;
3. templates de mission, tâche, preuve, conformité, knowledge sources et providers ;
4. templates runtime pour board, mémoire, contexte, décisions, rules/hooks, orchestration, evidence gates et patterns ;
5. distinction explicite mémoire / contexte / base de connaissance ;
6. compatibilité provider-first pour Copilot, Codex/OpenAI, Claude, Gemini et modèles locaux ;
7. génération de context bundle, decision trace, knowledge index/graph, hook simulation, gate check strict, event audit, score dimensionnel et remediation sûre.

## Limites actuelles

- Le registry provider est audité, vérifié et évalué dans le context bundle ; le branchement aux appels providers réels reste l'étape suivante.
- Les gates d'évidence peuvent bloquer explicitement les profils `governed`/`production` avec `standard gate check --strict`; l'intégration CI de chaque projet doit appeler cette commande.
- Le knowledge graph indexe les artefacts locaux et sources `folder` autorisées ; les connecteurs MCP, URL, base de données et vector store restent à brancher.

## Étendre les profils

Les profils livrés par défaut vivent dans `framework/agentic-standard/profile-map.yaml`. Pour créer un profil projet ou organisation :

1. ajouter une entrée dans `profiles` avec un `id`, des `required_artifacts`, des `mapped_capabilities` et du `minimum_evidence` ;
2. déclarer tout nouveau type d'artefact dans `artifact_types` avec un template associé ;
3. ajouter sa destination dans `generation_targets` si l'artefact doit être généré ;
4. versionner les templates custom avec les autres artefacts de gouvernance.

La commande `standard init` ne remplace pas les artefacts existants sauf avec `--force`, ce qui permet de faire évoluer la carte de profils sans écraser une baseline projet déjà remplie.

## Cible d’évolution

Le standard agentique a maintenant une cible de runtime normatif plus large :

- [Plan cible du runtime normatif agentique](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/planning/agentic-standard-target-plan.md) (document de travail, non publié)
- [Schéma et documentation cible du standard agentique](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/planning/agentic-standard-target-architecture.md) (document de travail, non publié)

Le contrat machine-readable associé est versionné dans `framework/agentic-standard/target-schema.yaml`.
