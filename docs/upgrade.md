# Mettre à jour un projet (`grimoire upgrade-flow`)

Issue [#490](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/490). `grimoire up` régénère le tier
kit et synchronise les surfaces hôtes — une étape d'une vraie mise à niveau, pas toute la mise à niveau.
`grimoire upgrade-flow` conduit `registry/blueprints/project-upgrade.blueprint.json` à travers le moteur
de flows réel (`grimoire flow run`/`resume`) pour faire le reste : sauvegarde préalable, détection et
archivage des agents orphelins, application, puis deux nœuds de jugement qui **ne font jamais qu'une
proposition** — jamais une écriture — pour ce qui exige un arbitrage humain.

Nommée `upgrade-flow` et non `upgrade` : ce dernier nom est déjà pris par `grimoire upgrade`, la
migration de structure v2 → v3 (`grimoire.cli.cmd_upgrade`). Deux sens différents de « upgrade »
avaient besoin de deux commandes, pas d'une seule surchargée.

## Les nœuds, dans l'ordre

1. **backup** (V0, mécanique) — tarball de `_grimoire/`, `.claude/`, `.github/{agents,prompts,
   instructions}`, `GEMINI.md`, `AGENTS.md`, `CLAUDE.md`, `.mcp.json`, `project-context.yaml` sous
   `_archive/<date>-pre-<version>/`, plus un manifeste SHA-256 de `_grimoire/_memory/`. Idempotent : un
   second passage le même jour ne réécrit rien.
2. **preview** (V0) — `up --dry-run` + `host sync --dry-run`, diff écrit dans
   `_grimoire-output/upgrade/<date>/preview.md`. Enregistre aussi une **ligne de base** de *tous* les
   contrôles `grimoire doctor` (`_grimoire-output/upgrade/<date>/doctor-baseline.json`) : les références
   `_grimoire/...` déjà mortes (`dead_references`, granularité par ligne, issue #502) et, depuis l'issue
   #510 (point 1), toute autre FAIL déjà présente (`other_failures`, une signature `"<contrôle> :
   <détail>"` par check — ex. `agents_referenced`). C'est cette ligne de base qui distingue, au nœud
   `apply`, un défaut préexistant d'une vraie régression, quel que soit le contrôle en cause.
3. **orphans** (V0) — agents/wrappers portant le marqueur `grimoire:managed` que le kit installé ne
   livre plus (comparaison entre ce qui est sur disque et le plan du scaffolder pour l'archétype
   configuré — `grimoire.cli.cmd_up.fresh_kit_agent_roster`, qui ne write rien). Déplacés vers
   `_archive/<date>-pre-<version>/orphans/`, **jamais supprimés**. Doit tourner **avant** `apply` : la
   migration réelle du 2026-09-11 (Terraform-HouseServer, 3.38.0 → 3.44.2) a montré que `up`/`host sync`
   refusent (garde de distinction : deux agents au même faisceau) tant qu'un orphelin partage encore le
   faisceau d'un agent courant.

   Depuis l'issue #510 (point 2), ce nœud couvre aussi une seconde forme d'orphelin, indépendante de la
   première : une projection hôte managée (`.claude/agents/<nom>.md`, `.github/agents/<nom>.agent.md`,
   …) **sans aucune source installée dans aucune tier** — kit, overrides, ni la tier custom héritée. Ce
   cas échappe à la boucle principale (partie de `layout.installed_agents`, donc aveugle à un nom sans
   fichier installé) et à `host sync` (qui ne revisite que ce que son plan actuel résout — un nom sans
   source n'y apparaît jamais) : c'est exactement la forme trouvée sur la Forge, deux projections
   `grimoire:managed` survivantes d'un gabarit retiré du manifeste par une passe d'archivage antérieure,
   que `grimoire doctor` signalait (`agents_referenced`) sans que rien ne les retire. Choisi ici plutôt
   que dans `host sync` : ce nœud possède déjà le mécanisme d'archivage (jamais de suppression) et tourne
   déjà avant `apply`, donc avant que `doctor` ne juge le projet mis à niveau. Un fichier hôte sans le
   marqueur `grimoire:managed` n'est **jamais** un candidat, quel que soit son nom.
4. **apply** (V0) — `grimoire up` (refresh du tier kit + host sync). Acceptance : `doctor -o json` sans
   FAIL **et** le hook `SessionStart` rejoué sans échec structuré (clé `error` de premier niveau, ou le
   marqueur exact `[Grimoire] hook <id> en erreur` en tête d'un bloc rendu — jamais une recherche libre
   du mot « erreur », qui faisait échouer `apply` sur un simple rappel de mémoire le mentionnant, issue
   #502). Une référence `_grimoire/...` morte (check `paths_resolve`) ou tout autre contrôle doctor FAIL
   (issue #510, point 1) ne fait échouer ce nœud que s'il est **absent de la ligne de base** que
   `preview` a enregistrée — un défaut déjà présent avant la mise à niveau devient une **proposition
   `repair`** (`grimoire.proposals`, `artifact_type: "repair"`) au lieu de bloquer le flow :
   `paths_resolve` nomme fichier:ligne et propose une substitution vers la tier kit quand elle est
   évidente (`propose_repairs`) ; tout autre contrôle nomme le contrôle et son détail, toujours en
   revue humaine (`propose_doctor_repairs`, `category: "doctor-preexisting"`). `grimoire upgrade-flow
   apply`/`run` rapportent alors clairement « mis à niveau, N défaut(s) préexistant(s) en proposition »
   plutôt qu'un simple échec.

   Quand `apply` refuse malgré tout (une vraie régression, absente de la ligne de base), le flow a déjà
   modifié le projet — `up` a tourné — et le dit explicitement plutôt que de laisser croire que rien n'a
   bougé (issue #510, point 3) : `grimoire upgrade-flow run --json` rend `"ok": false`, `"done"` (les
   nœuds réellement exécutés, `backup`/`preview`/`orphans` compris), `"stopped_at": "apply"`,
   `"state": "upgraded-but-failed"`, `"failing_checks"` (le ou les contrôles en cause — `"up"`, `"hook"`
   ou un nom de contrôle doctor), et `"backup_path"` (le tarball du nœud `backup`, pour restaurer si
   besoin). Un `_grimoire-output/upgrade/<date>/report.md` est écrit avant le refus, sa première ligne
   étant toujours « Mis à niveau, flow en échec sur `<contrôle>`. » — jamais un fichier absent parce que
   `verify` (le seul autre nœud qui en écrit un) ne tourne jamais après un `apply` refusé. `grimoire.
   tools.project_update.update_project()` (l'atelier/le cockpit) porte les mêmes champs (`state`,
   `backupPath`) et relit ce rapport dès qu'il existe.
5. **overrides** (V1, proposition) — pour chaque override **plein** (`grimoire.core.override_drift.
   project_override_drift`, `override_kind != "partial"`), un essai `agent override convert --dry-run` :
   corps identique au kit → proposition « conversion sûre » ; corps divergent → proposition « revue
   nécessaire », diff joint. Un override **partiel** (`extends: kit`) est déjà la forme convertie —
   jamais proposé, quel que soit son statut de dérive. Écrit via `grimoire.proposals`
   (`artifact_type: "override-migration"`) — **jamais** d'override réécrit ici. Visible dans `grimoire
   proposals list` et le cockpit comme toute autre proposition.

   Issue #510 (point 6) : la convertibilité (le corps de l'override correspond-il encore à celui du
   kit ?) et le statut de dérive (`status`, le kit a-t-il bougé depuis la prise ?) répondent à deux
   questions différentes — les confondre sautait tout override `"fresh"`, y compris un override plein
   dont le corps est resté une copie exacte du kit (le cas le plus simple à convertir, puisque « fresh »
   veut justement dire que le kit n'a pas bougé depuis). Un homelab réel avait sept overrides pleins de
   cette forme, chacun ne portant qu'un `context:`, et n'a jamais reçu de proposition pour aucun d'eux.
   Chaque override plein reçoit désormais un essai de conversion quel que soit son statut de dérive ;
   seul le type (plein vs partiel) décide s'il y a quelque chose à proposer.
6. **memory** (V1, proposition) — fiches `_grimoire/_memory/agent-learnings/*.md`,
   `decisions-log.md`, `failure-museum.md`, `network-topology.md` qu'aucun `context:` d'agent ne
   référence : proposition de raccordement au porteur dont le `use_when` couvre le sujet (mots entiers,
   même principe que le porteur par catégorie du déclencheur de propositions) ; sans porteur plausible,
   proposition « à placer à la main ». `artifact_type: "memory-link"`. Jamais d'écriture de `context:`
   à cette étape.
7. **needs-hosts** (V0 + proposition) — `grimoire.core.execution_needs.resolve_execution_needs` ; tout
   besoin `unresolved` propose une déclaration `needs.commands`. `hosts.enabled` absent de
   `project-context.yaml` propose une déclaration égale à la détection sur disque
   (`grimoire.hosts.detection.detect_enabled_hosts`).
8. **verify** (V0) — recompare le manifeste pris par `backup`. Seul `_grimoire/_memory/config.yaml`
   (en-tête de version, réécrit par l'étape `identity` de `up`) peut différer sans que ce soit une
   anomalie. Rapport final : `_grimoire-output/upgrade/<date>/report.md`.
9. **destructive** (V2, `kind: "checkpoint"`) — point de passage humain forcé, jamais auto-décidé par
   la cascade (`--executor dispatch` s'arrête ici en `waiting_host`) : tout retrait réel de mémoire ou
   d'override — au-delà d'un déplacement vers `_archive/` — doit être décidé nommément par un humain.
   Ce nœud n'exécute rien lui-même ; sa seule présence dans le blueprint est le refus tracé que
   l'issue #490 demande.

## Ce qui est proposé, et jamais appliqué

Les nœuds `overrides`, `memory` et `needs-hosts` n'écrivent que des fichiers `_grimoire-output/
proposals/<slug>.yaml` (`status: pending`), via `grimoire.proposals.create_manual_proposal` — le même
mécanisme de stockage que le déclencheur de propositions sur non-choix répété (issue #395), étendu de
deux `artifact_type` (`override-migration`, `memory-link`) et d'un champ générique `artifact_ref`.
`grimoire proposals list` les affiche comme n'importe quelle autre proposition ; le cockpit (Piloter)
aussi.

`grimoire proposals accept <slug>` est la seule porte d'écriture, et seulement sur geste humain explicite :

- `override-migration` — accepter appelle `agent override convert` **seulement** si la proposition
  portait « conversion sûre » (le corps de l'override est un octet-pour-octet du kit). Une proposition
  « revue nécessaire » refuse toujours l'acceptation : fusionner du texte divergent n'est pas ce que
  cette commande fait, et faire semblant serait pire que de ne rien faire.
- `memory-link` — accepter ajoute le chemin de la fiche au `context:` de l'override du porteur proposé.
  Refuse si aucun porteur n'a été trouvé (« à placer à la main ») ; jamais faute d'override existant —
  si le porteur est un agent du kit sans override, accepter en crée un **partiel**
  (`extends: kit`, `kit_source_hash`, issue #427) qui ne porte que le champ `context:`.
- `needs-hosts` — `hosts-declare-enabled` a une valeur mécanique réelle (la détection sur disque déjà
  faite à la proposition) : accepter l'écrit dans `hosts.enabled` de `project-context.yaml`, par
  round-trip (`grimoire.tools._common.load_yaml_roundtrip`/`save_yaml`, grimoire-kit#430 : les
  commentaires du fichier survivent). `needs-declare-commands` reste, sciemment, un refus : un besoin
  *non résolu* l'est précisément parce qu'aucune commande n'a été déclarée ni détectée — en inventer
  une contredirait la doctrine de `grimoire.core.execution_needs` (« jamais une commande inventée à
  partir du seul id du besoin ») ; la déclarer reste un geste manuel dans `project-context.yaml`.

## Ce qui est refusé, et pourquoi

- **Suppression, jamais** : `orphans` déplace (`shutil.move`) vers `_archive/.../orphans/`, avec un
  `README.md` nommant le kit installé et les agents retirés. Restaurer un orphelin toujours voulu est
  un geste manuel, documenté sur place — jamais un second run qui le ferait disparaître pour de bon.
- **Le checkpoint `destructive`** : présent dans le blueprint précisément pour qu'aucun exécuteur —
  humain via `--executor interactive`, cascade via `--executor dispatch` — ne puisse faire disparaître
  quoi que ce soit sans une décision nommée. `grimoire upgrade-flow run` ne soumet jamais de
  `checkpoint_decision` à sa place.
- **Une conversion d'override qui fusionnerait du texte** : refusée nommément (voir ci-dessus), avec le
  diff exact dans le message de refus.
- **`--dry-run` combiné à `--executor dispatch`** : refusé — la cascade de dispatch n'a pas de notion
  générique de « n'exécuter que les deux premiers nœuds », et improviser une le rendrait moins
  prévisible, pas plus. `--dry-run` n'existe qu'avec `--executor interactive` (le défaut).

## Comment vérifier

```bash
# Aperçu seul (s'arrête après `preview`, rien d'autre ne tourne)
grimoire upgrade-flow run --project-root . --dry-run

# Le flow complet, mécanique (aucun appel à un fournisseur), s'arrête au checkpoint
grimoire upgrade-flow run --project-root . --json

# Les mêmes nœuds, mais délégués à la cascade de dispatch (comme `grimoire flow run
# --executor dispatch` pour n'importe quel blueprint) — un fournisseur réel fait le travail,
# le gate vérifie l'acceptance structurée de chaque nœud
grimoire upgrade-flow run --project-root . --executor dispatch

# État d'un run, propositions écrites, rapport final
grimoire flow status <run-id> --project-root .
grimoire proposals list
cat _grimoire-output/upgrade/<date>/report.md

# Le blueprint lui-même
grimoire blueprint validate registry/blueprints/project-upgrade.blueprint.json

# Ce que chaque nœud du blueprint invoque réellement comme acceptance
grimoire upgrade-flow check <backup|preview|orphans|apply|overrides|memory|needs-hosts|verify> \
  --project-root .
```

`grimoire flow extract <run-id>` reproduit un run — complet ou arrêté au checkpoint — en blueprint
brouillon, comme pour n'importe quel autre flow.

## Revoir les décisions

Un `run` qui s'arrête (checkpoint `destructive` ou propositions V1 en attente) laisse un projet dans
un état à décider, pas cassé. Trois façons de revoir ce contexte sans le reconstituer à la main
(issues #490/#506/#510) :

- **Le skill `upgrade-review`** (archétype `meta`, attaché au concierge) : lit, explique et
  recommande une ligne par proposition, pose les questions par lot, n'applique qu'une réponse
  explicite via `grimoire proposals accept|reject`/`grimoire flow resume`.
- **Le prompt `/grimoire-upgrade-review`** (projeté par `grimoire host sync` — commande Claude Code,
  prompt Copilot) : le même protocole, invocable depuis un hôte avec support des commandes/prompts.
- **`grimoire upgrade-flow review [--json]`** (lecture seule) : le contexte brut de l'étape 1 en un
  bloc prêt à coller, pour un hôte sans slash command. Refuse de tourner si l'outil `grimoire` est
  plus ancien que le kit aligné du projet (contrôle `doctor` `tool_version`, issue #515).

```bash
grimoire upgrade-flow review --project-root . --json
```

Rend `tool_version` (l'écart outil/kit, ou `null`), `last_run` (id, statut, nœud courant du dernier
run `project-upgrade` — `grimoire.tools.flow_runs.list_flow_runs`), `checkpoint_pending` (booléen) et
`pending_proposals` (le même contenu que `grimoire proposals list -o json`, filtré sur `status ==
"pending"`). Aucune de ces trois portes n'écrit ni ne décide à la place de l'humain — la décision
reste `grimoire proposals accept|reject <slug>` et `grimoire flow resume <run-id> --result
<fichier.json>` (`{"pins": {"out": {"contract": "upgrade-complete"}}, "checkpoint_decision":
"approve"|"reject"}`).

## Depuis le cockpit

`POST /api/projects/update` (`src/grimoire/tools/project_update.py`, câblé dans `cmd_cockpit.py` et
`forge_server.py` — deux hôtes, une route) appelle `grimoire upgrade-flow run` plutôt que `up` seul,
en sous-processus comme avant (jamais un import direct, pour qu'un refus n'y remonte pas comme une
exception non attrapée). Même contrat qu'en CLI :

- **aperçu par défaut** (`confirm` absent ou `false`) — `--dry-run`, s'arrête après `preview`. La
  réponse porte `report["preview"]`, le contenu exact de `_grimoire-output/upgrade/<date>/preview.md`,
  jamais un résumé reformulé ; l'espace Piloter l'affiche tel quel sous le bouton « Mettre à jour —
  aperçu » (spec §4), qui garde son nom malgré le changement de commande sous-jacente.
- **`confirm: true`** — le flow complet sous `--executor interactive` (mécanique, jamais un
  fournisseur), arrêté au checkpoint `destructive` comme en CLI. La réponse porte `report["report"]`
  (le `report.md` final) et `report["proposals"]` (les propositions encore `pending`, slug/type/
  spécialité — jamais leur contenu détaillé, que `GET /api/workspace/proposals` rend déjà).
- **projet non enregistré ou chemin hors registre** : refusé (`404`), comme toute autre cible du
  cockpit résolue par slug (`_resolve_project_path`) — jamais un repli silencieux sur le projet servi.

Les propositions qu'un run complet écrit apparaissent dans la section « Propositions » de Piloter dès
que la fiche projet se redessine (`options.refresh()`, déjà déclenché après confirmation) — le même
mécanisme de lecture/acceptation que le déclencheur de non-choix (#395), jamais une seconde vue. La
timeline par tâche (#443) suit le run comme n'importe quel autre flow : rien de spécifique à câbler,
`upgrade-flow run` passe par le même moteur que `grimoire flow run`.

À côté de « Mettre à jour », le bouton **« Revoir dans l'IDE »** (issue #520, lot 2) n'est présent
dans la page que s'il y a une proposition en attente ou un checkpoint destructif en attente — jamais
un bouton toujours là. Il ne fait rien lui-même : il prépare et met à disposition (presse-papiers,
repli affiché) le texte que `/grimoire-upgrade-review` attend (le prompt mission pack du lot 1), et
montre `grimoire upgrade-flow review` pour un hôte sans slash command. Aucun lien d'ouverture d'IDE :
ce dépôt ne déclare aucun mécanisme de ce type.

## Portée non couverte par cette livraison

- `grimoire flow list --require-measure project-upgrade` ne trouve une mesure qu'après au moins un run
  en `--executor dispatch` (les mesures viennent du `TraceLedger`, jamais d'un run purement
  interactif). La suite de tests de cette PR couvre le blueprint et le chemin mécanique
  (`--executor interactive`, celui que `grimoire upgrade-flow run` utilise par défaut) de bout en
  bout, avec de vrais nœuds sur un vrai projet ; elle ne rejoue pas `--executor dispatch` avec des
  fournisseurs simulés pour ce blueprint précis — ce chemin délègue au même moteur générique que
  `grimoire flow run --executor dispatch <n'importe quel blueprint>`, déjà couvert par
  `tests/unit/test_flows_dispatch_executor*.py`, mais l'acceptance de chaque nœud de *ce* blueprint
  n'a été vérifiée gate-exécutée que par lecture (`grimoire upgrade-flow check <node>` appelé
  directement, jamais par la cascade elle-même dans un test).
