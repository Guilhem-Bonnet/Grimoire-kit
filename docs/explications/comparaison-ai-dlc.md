---
audience: utilisateur
---

# <img src="../assets/icons/scale.svg" width="32" height="32" alt=""> grimoire-kit et AWS AI-DLC : ce qui se vérifie

Cette page compare grimoire-kit et AWS AI-DLC sur ce qu'on peut vérifier en lisant le code et en lançant des commandes, sans exécuter de modèle.
Elle s'adresse à quelqu'un qui choisit un outil.

## En bref

- **Résultat.** Aucune supériorité de résultat n'est démontrée, ni d'un côté ni de l'autre.
  Les tâches dont on dispose sont résolues par Claude seul : le banc ne sépare pas les outils sur le succès.
- **Ce qui est démontré** porte sur les garanties, le coût et l'outillage :
  - une garde du kit qui ne sait pas lire refuse, là où les hooks d'AI-DLC laissent passer ;
  - un coût inconnu n'y vaut jamais zéro ;
  - le kit apporte un cockpit, un kanban de tâches, un serveur MCP et une autonomie bornée par octroi.
- **Ce qu'AI-DLC couvre et que le kit ne couvre pas** est réel : un cycle de vie complet (33 stages, 14 rôles), le travail en équipe et la rétro-ingénierie d'un existant. Voir [Ce qu'AI-DLC fait et que le kit ne fait pas](#ce-quai-dlc-fait-et-que-le-kit-ne-fait-pas).

## Versions et méthode

| | Version | Où vérifier |
|---|---|---|
| grimoire-kit | 3.64.0, branche `main` | `src/grimoire/__version__.py:1` |
| AI-DLC | 2.11.0, tag `v2.11.0`, commit `6a378b53c0a4fe0641ed7d8de8dfff94264d5b6a` | dépôt `awslabs/aidlc-workflows`, `CHANGELOG.md:4` |

Règles de lecture :

- un chemin `src/…`, `rust/…`, `docs/…` ou `.github/…` cité côté kit désigne un fichier de ce dépôt, sur `main` ;
- un chemin cité côté AI-DLC désigne un fichier du dépôt `aidlc-workflows` au tag `v2.11.0` ;
- les numéros de ligne valent pour ces deux états ; ils bougeront avec les versions suivantes ;
- « non vérifié » signifie que la page ne l'a ni lu ni mesuré. Ce n'est pas une estimation.

## Gouvernance

| Point | grimoire-kit | AI-DLC 2.11.0 |
|---|---|---|
| Une garde qui bloque | Le hook rend `permissionDecision` avant un outil et `decision: block` sur Stop (`src/grimoire/hosts/runtime.py:202-217`), sur les hôtes capables de refuser. | Gardes de plan et de gel de revue en `PreToolUse` (`core/hooks/aidlc-plan-approval-guard.ts`, `core/hooks/aidlc-review-freeze.ts`). |
| Garde en panne | Un gate de preuve illisible bloque en profil `governed` et `production` (`src/grimoire/hosts/decisions/evidence_gate.py:151-154`, `:524-530`). Un drapeau de pause illisible vaut `drain` (`src/grimoire/missions/pause.py:14`). | Laisse passer : « Fail-open everywhere: malformed stdin, no audit ledger, unreadable state […] or any throw allows the call » (`core/hooks/aidlc-review-freeze.ts:39-41`). La garde de plan laisse passer hors Code Generation (`core/hooks/aidlc-plan-approval-guard.ts:36-40`). |
| Interrupteur | La variable `GRIMOIRE_DONE_GATE` peut armer le refus répété du Stop, jamais le désarmer (`src/grimoire/hosts/decisions/done_gate.py:40`, `:502-504`). Le plafond de blocages du Stop n'est pas réglable par l'agent (`:799-801`). | `AIDLC_DISABLE_PLAN_APPROVAL_GUARD=1` et `AIDLC_DISABLE_REVIEW_FREEZE_HOOK=1` coupent les gardes (`core/hooks/aidlc-plan-approval-guard.ts:2148-2149`, `core/hooks/aidlc-review-freeze.ts:237`). La coupure de la garde de plan écrit une ligne `GUARD_DISABLED` (`:41-44`). |
| Stop répété | Le premier Stop sans preuve bloque. Les suivants ne bloquent qu'en mode `enforce`, que le projet doit activer. Par défaut, ils sont journalisés `stop.would_block` et passent (`src/grimoire/hosts/decisions/evidence_gate.py:340-355`, `done_gate.py:146`). | Plafond de 2 blocages en interactif, 8 en autonome, avec signature de progrès (`core/hooks/aidlc-continue-workflow.ts:244-245`). |
| Règles apprises | Couches `organization`, `team`, `project`, `agent` (`src/grimoire/core/layered_rules.py:1-40`). Grammaire fermée `ALWAYS\|NEVER <verbe> <objet> [@portée]` : le code, pas un modèle, décide des conflits, et la couche extérieure l'emporte. Candidates tirées de faits observés, les refus de gate de tâche du TraceLedger, avec un seuil de 2 tâches distinctes ; acceptation par un humain, puis effet mesuré avant/après par un test exact de Fisher (`src/grimoire/core/learned_rules.py:1-48` ; `grimoire memory rules propose`, `accept`, `effect`). Limite écrite : la seule source de candidats est le refus de gate de tâche, et avant/après n'est pas une expérience contrôlée. | Couches organisation, équipe et projet (`docs/guide/09-rules-and-the-learning-loop.md:38`). Le candidat est un journal que le modèle rédige, trié par un humain à chaque porte (`:57-73`). `RULE_LEARNED` enregistre l'écriture de la règle, pas son effet (`core/knowledge/aidlc-shared/audit-format.md:403`). Le conflit avec une règle d'organisation est jugé par le modèle, « not an enforcement boundary » (`:90`). |
| Autorité humaine | Une décision est `attested` ou `unattested` (`src/grimoire/flows/attestation.py:101-102`). Écrire le journal des tours humains depuis un outil est refusé (`src/grimoire/hosts/decisions/tool_policy.py:310-313`). | `HUMAN_TURN` enregistré sur `UserPromptSubmit`, « it does not authenticate who launched the dispatcher » (`core/hooks/aidlc-record-human-turn.ts:6-7`). Les événements `HUMAN_TURN` ne s'écrivent pas par la CLI (`core/tools/aidlc-audit.ts:477-479`). |
| Limite écrite | « L'attestation rend la falsification plus coûteuse et visible, pas impossible » (`src/grimoire/hosts/decisions/human_turns.py:24-26`). | « Audit shards are operational evidence, not a tamper-proof human-authorship boundary » (`core/knowledge/aidlc-shared/audit-format.md:94-95`). |
| Approbation | Liée à l'empreinte de l'action : approuver une commande n'en approuve pas une autre (`src/grimoire/policies/temporal.py:532-539`). | Commande de vérification autorisée par empreinte SHA-256 (`core/tools/aidlc-swarm.ts:319-320`). |
| Contournement journalisé | `Override Gate <tâche>: <raison>` et `Lower Profile <profil>: <raison>`, tapés par l'humain, écrits avant tout effet, à usage unique et bornés dans le temps (`src/grimoire/hosts/decisions/gate_override.py:1-37`, `profile_floor.py:19-30`). Ces journaux ne s'écrivent pas par un outil (`gate_override.py:29-31`). | Ligne `GUARD_DISABLED` à la coupure de la garde de plan (`core/hooks/aidlc-plan-approval-guard.ts:41-44`). Pour le gel de revue : non vérifié. |

Lecture : les deux outils disent que leur preuve de présence humaine n'est pas une authentification.
La différence porte sur la panne : le kit refuse, AI-DLC laisse passer et le documente.

## Preuve

| Point | grimoire-kit | AI-DLC 2.11.0 |
|---|---|---|
| Journal chaîné | Le journal de preuve est chaîné par SHA-256, avec une ancre de fin (`src/grimoire/core/standard_checks/evidence_chain.py:187-191`). Limite écrite : qui réécrit le journal et l'ancre obtient une chaîne cohérente (`:27-32`). Le TraceLedger et le Mission Ledger ne sont pas chaînés. | Journal d'audit en ajout seul, 115 types d'événements (`core/knowledge/aidlc-shared/audit-format.md:45`). Aucun chaînage trouvé. |
| Preuve rattachée aux commits | `grimoire evidence resolve <base>..<head>` juge chaque preuve contre les objets git de `head` : seule une preuve `resolved` compte (`src/grimoire/core/standard_checks/evidence_resolve.py`). | `aidlc attest resolve` classe chaque chemin modifié en vérifié, dérivé ou non attesté (`docs/reference/20-commit-provenance.md:15-19`). |
| Reçu de revue | Lié à l'empreinte de l'arbre et du pack de preuve : un arbre modifié rend le reçu périmé (`src/grimoire/missions/review_receipt.py:308-314`). Limite écrite : un agent peut écrire lui-même un reçu frais (`:22-26`). | Lié au condensat de l'enregistrement de revue (`core/aidlc-common/protocols/stage-protocol-reviewer.md:188`). Même limite écrite (`docs/reference/20-commit-provenance.md:18`). |
| Tests du gate | Le gate exécute la commande de test et lie le résultat à l'empreinte de l'arbre (`src/grimoire/core/standard_checks/gate_test_run.py:181`). | Capteurs consultatifs : une erreur d'outil, un code 127 ou une sortie illisible donnent `PASSED` (`core/tools/aidlc-sensor.ts:24-30`). |
| Preuve déclarative | Marquée comme telle : verdict `skipped`, jamais `passed`, et elle ne ferme pas une tâche (`src/grimoire/missions/declarative_evidence.py:12-15`, `src/grimoire/missions/gates.py:247-248`). | Les champs `--user-input`, `--feedback` et `--details` sont de la prose fournie par l'appelant (`core/knowledge/aidlc-shared/audit-format.md:89-90`). |

## Coût

| Point | grimoire-kit | AI-DLC 2.11.0 |
|---|---|---|
| Coût inconnu | Trois états, `exact`, `lower_bound` et `unknown`. Le montant vaut `None` quand rien n'est chiffré, jamais `0.0`, en Python (`src/grimoire/costs.py:94-103`) comme en Rust (`rust/grimoire-traces-core/src/lib.rs:653-670`). | Coût `null` pour un modèle inconnu (`core/tools/aidlc-usage.ts:19-20`), mais le total additionne les seules lignes chiffrées, sans signaler que c'est une borne basse (`:983`). |
| Plafond et coût inconnu | Par défaut, un coût inconnu face à un plafond arrête (`src/grimoire/costs.py:36-37`, `:149-155`). | Non vérifié. |
| Mesure réelle | Tokens repliés depuis le journal de session de Claude Code, un appel par `message.id` (`src/grimoire/traces/transcript_usage.py`). Le coût d'un appel avec tokens de cache reste inconnu : la table de tarifs ne modélise pas le cache. Le repli est manuel (`grimoire task fold-usage`). Les autres hôtes sont déclarés `unavailable`. | Lecteur de transcription pour Claude Code seul. Sur les autres harnais, le journal n'est jamais écrit et l'outil « degrades silently to no-data » (`core/tools/aidlc-usage.ts:9-13`). |

## Orchestration

| Point | grimoire-kit | AI-DLC 2.11.0 |
|---|---|---|
| File de tâches | Tableau, vagues, file et tick : `grimoire task board`, `grimoire task waves`, `grimoire queue plan`, `grimoire orchestrate tick`. | Pas de kanban ni de file de tâches trouvés. `/aidlc team-board` est une vue en lecture seule des unités de Construction (`docs/guide/12-cli-commands.md:318-320`). |
| Limite de travail en cours | WIP global et par rôle, appliqués à la réservation. Une limite illisible refuse (`src/grimoire/missions/admission.py:619-629`). | Non trouvé. |
| Pause et reprise | `drain` et `hold`, par tâche, par projet ou pour toute la machine (`src/grimoire/missions/pause.py:1-23`) : `grimoire orchestrate pause`, `grimoire orchestrate resume`. Reprise de flow idempotente (`src/grimoire/flows/engine.py:966-986`). | `/aidlc park`, puis `/aidlc --resume` (`docs/guide/12-cli-commands.md:302-312`). `next` est idempotent à état inchangé (`core/tools/aidlc-orchestrate.ts:6683`). |
| Questions structurées | `grimoire task ask`, `grimoire task answer`. Une réponse qui ne vient pas du canal humain est refusée (`src/grimoire/missions/service.py:775-777`). | `DECISION_RECORDED` avant toute question structurée (`core/knowledge/aidlc-shared/audit-format.md:143`). |
| Cadrage vers tâches | Oui, à partir de la version qui inclura #851 : `grimoire cadrage plan` crée une tâche du ledger par exigence `REQ-NNN` et pose les ambiguïtés en questions (`src/grimoire/missions/cadrage_plan.py`). Avant, `grimoire cadrage` décrit et vérifie le cadrage sans créer de tâches. | Oui, par les stages d'Inception (exigences, user stories, planification de livraison) dans `core/aidlc-common/stages/inception/`. |
| Autonomie | Octroi borné par périmètre, coût et échéance, révoqué au premier coût inconnu ou au premier gate rouge (`src/grimoire/flows/autonomy.py:1-34`) : `grimoire autonomy grant`. | Mode `autonomous` ou `gated` (`AUTONOMY_MODE_SET`, `core/knowledge/aidlc-shared/audit-format.md:332`), limité aux checkpoints de Construction. |

## Hôtes

| Point | grimoire-kit | AI-DLC 2.11.0 |
|---|---|---|
| Hôtes | 6 : Claude Code, GitHub Copilot, Codex, Cursor, Gemini CLI, opencode (`src/grimoire/hosts/emitters/__init__.py:25-32`). Claude Code, Copilot et opencode peuvent refuser. Codex, Cursor et Gemini CLI ne le peuvent pas : ils reçoivent la raison sans blocage (`src/grimoire/hosts/capabilities.py:120-190`). | 7 harnais : Claude Code, Codex, Copilot, Cursor, Kiro CLI, Kiro IDE, opencode (`harness/`, `CHANGELOG.md:129`). Ce que chaque hook bloque sur chaque harnais : non vérifié. |
| Matrice règle × hôte | Calculée depuis ce que chaque émetteur écrit, sans cellule écrite à la main (`src/grimoire/hosts/matrix.py:1-19`) : `grimoire host matrix`. | Non trouvée. |
| Sonde sur disque | Confronte la matrice aux fichiers présents : `applied`, `missing`, `stale`, `conflict`, `disabled`… (`src/grimoire/hosts/probe.py:1-20`) : `grimoire host probe`. | `aidlc doctor` (`core/tools/aidlc.ts:181-188`). Son contenu n'a pas été comparé. |
| Synchronisation | Transactionnelle pour tous les hôtes à la fois : une panne défait ce qui a été écrit (`src/grimoire/hosts/emitters/base.py:457-471`). Limite écrite : un processus tué laisse les fichiers déjà remplacés (`:491-494`). | Non vérifié. |

## Distribution

| Point | grimoire-kit | AI-DLC 2.11.0 |
|---|---|---|
| Attestation de release | Provenance Sigstore sur chaque fichier, vérifiée par `gh attestation verify` avant toute publication (`.github/workflows/publish.yml:179-226`). | Attestation in-toto vérifiée à l'installation (`scripts/install.sh:389-396`). Sans `gh`, l'installation continue sur les seules sommes SHA-256 et ne le dit qu'en mode interactif (`:382-384`). |
| Vérifier une installation | `grimoire verify-install` : empreinte PyPI, `SHA256SUMS` de la release, puis `RECORD` (`src/grimoire/tools/install_trust.py:1-29`). | Sommes vérifiées à l'installation (`scripts/install.sh:447`). |
| Hors ligne | Installation d'un bundle avec `grimoire update --from <dossier>`, niveau de confiance `bundle`, jamais `verified` (`src/grimoire/tools/update_bundle.py:1-23`). `grimoire self bundle <dossier>` construit ce bundle en ligne : wheel vérifiée contre PyPI et le `SHA256SUMS` de la release, dépendances en wheels seulement, `SHA256SUMS` et `PROVENANCE.json`, relus par le lecteur de l'installation avant d'être rendus (`src/grimoire/tools/update_bundle_build.py`, `src/grimoire/cli/cmd_self_bundle.py`). Limite écrite : les dépendances viennent d'un index sans empreinte publiée par le kit. | `--from <dossier>` et `--offline` (`scripts/install.sh:110-111`). |
| Canaux | `stable` et `preview` (`grimoire update --channel`). | Stable et preview (`scripts/install.sh:17-20`). |
| Script d'installation | `install.sh` installe par `pipx`, sans somme de contrôle. | Sommes vérifiées. |

## Expérience

| Point | grimoire-kit | AI-DLC 2.11.0 |
|---|---|---|
| Tableau de bord | Cockpit web local : projets, activité, mises à jour (`grimoire cockpit serve`, `grimoire cockpit open`). | Aucune interface web trouvée. Ligne d'état du terminal (`core/hooks/aidlc-statusline.ts`) et `team-board` en lecture seule. |
| CLI | `grimoire --help` met cinq commandes en avant pour commencer, et `grimoire --help --all` les affiche toutes. | `aidlc` pour l'humain, `aidlc engine <nom> <verbe>` pour le moteur (`core/tools/aidlc.ts:181-212`), et les commandes `/aidlc` dans la session. |
| MCP | Serveur MCP livré, 26 outils : contexte, mémoire, standard, tâches, hôtes (`src/grimoire/mcp/server.py`). | Aucun serveur MCP livré. AI-DLC configure des serveurs externes. |

## Résultats mesurés

Les chiffres ci-dessous sont les seuls qui viennent d'une mesure. Ils portent sur un modèle (`claude-opus-5-5`), un hôte (Claude Code) et des exercices du banc polyglotte d'Aider.

### AI-DLC face à Claude seul, 20 tâches

Source : Banc à trois bras (`docs/_archive/bench-three-arms.md`, dépôt), §15.5 à §15.7.
Configuration : scope `express`, une personne simulée qui répond `Approved.` à chaque gate.

| | Claude seul (lot L, k=3) | AI-DLC (palier 1, k=1) |
|---|---:|---:|
| Tâches résolues | 20/20 | 20/20 |
| Coût médian par run | 0,159 $ | 4,592 $ |
| Rapport de coût par tâche (médiane) | 1 | environ 28 |
| Temps mur médian par run | 22 s | 494 s |
| Tours médians | 3 | 76 |

Limites, écrites dans la source :

- le succès ne départage rien : à 20 tâches, l'écart minimal détectable est de 44 points ;
- k=1 côté AI-DLC : la variance par tâche n'est pas mesurée ;
- dans 18 runs sur 20, l'agent principal écrit la solution avant le stage Code Generation : le succès mesure surtout le modèle, et le coût mesure la cérémonie d'AI-DLC (`docs/_archive/bench-three-arms.md`, §15.7) ;
- AI-DLC reçoit une personne qui approuve tout, ce qu'aucun autre bras ne reçoit ;
- les coûts sont des tarifs « list » de Claude Code sous abonnement, pas une facture.

### Tamis du lot M : Claude seul, 141 runs

Mesuré le 2026-10-20, bras `nu` seul, k=1, Claude Code 2.1.304.
Données brutes hors dépôt : `results.jsonl` du workspace du banc et journal de budget de la passe.

| | Valeur |
|---|---|
| Runs écrits | 141 sur 148 tâches tirées |
| Réussis | 140 sur 141. Seul échec : `python/tree-building` |
| Exercices polyglottes | 131 sur 132 |
| Bloc K (défauts réels du kit) | 9 sur 9 (`k01` à `k09`) |
| Coût | 21,68 $ connus au journal, dont 0,38 $ de sondes et de contrôle d'isolation |

Limites :

- la passe s'est arrêtée avant les tâches `k10` à `k17` ;
- l'appel de `k10` est ouvert au journal sans ligne de fin : son coût est inconnu, il n'est pas compté pour zéro ;
- 4 transcriptions sont signalées `invalid` par le contrôle de transcription (`go/two-bucket`, `k01`, `k06`, `k09`) et n'ont pas été relues ; les taux ci-dessus comptent tous les runs ;
- aucun autre bras n'a tourné sur ce lot.

### Ce qu'on en conclut

Aucune supériorité de résultat n'est démontrée.
Les tâches disponibles, y compris des défauts réels du kit, sont résolues par Claude seul.
La différence démontrée porte sur les garanties, le coût et l'outillage.

Les bancs payants sont suspendus depuis le 2026-10-20, par décision du mainteneur : ils coûtent en quota pour un rendement faible.
Cette page ne propose pas de nouvelle campagne.

## Ce qu'AI-DLC fait et que le kit ne fait pas

- **Un cycle de vie complet.** 5 phases et 33 stages (`docs/guide/04-phases-and-stages.md:3`) : étude de marché, exigences, user stories, conception, infrastructure, déploiement, réponse à incident (`core/aidlc-common/stages/`).
- **11 scopes**, du correctif à l'entreprise (`docs/guide/05-scopes-and-depth.md:13`), et un compositeur qui choisit les stages (`/aidlc compose`).
- **14 rôles d'agent** : produit, architecte, design, qualité, DevSecOps, conformité, exploitation… (`core/agents/`).
- **Le travail en équipe.** Construction partagée en unités, avec réservation, adoption et publication (`docs/guide/12-cli-commands.md:499-653`), et un mode atelier multi-équipes (`docs/guide/workshop-mode.md`).
- **La rétro-ingénierie d'un existant**, avec une base de connaissance du code (`core/aidlc-common/stages/inception/reverse-engineering.md:5`).
- **Kiro CLI et Kiro IDE** parmi les harnais.
- **Une installation hors ligne depuis un dossier de release publié** (`scripts/install.sh:110-111`).
- **Une ingénierie de test visible** : 779 fichiers `*.test.ts`, une couverture qui ne peut que croître (`.github/workflows/ci.yml:542`) et une suite complète exigée avant une release stable (`.github/workflows/release.yml:56-58`).

## Ce qui n'est pas vérifié ici

- La qualité du code produit par chacun des deux outils sur des tâches où Claude seul échoue : aucune tâche de ce genre n'est disponible.
- Le comportement des hooks d'AI-DLC sur chaque harnais autre que Claude Code.
- Le coût d'AI-DLC dans ses autres scopes que `express`.
- L'effort humain réel : la personne simulée approuve sans lire.
