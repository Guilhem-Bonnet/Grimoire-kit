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
   `_grimoire-output/upgrade/<date>/preview.md`.
3. **orphans** (V0) — agents/wrappers portant le marqueur `grimoire:managed` que le kit installé ne
   livre plus (comparaison entre ce qui est sur disque et le plan du scaffolder pour l'archétype
   configuré — `grimoire.cli.cmd_up.fresh_kit_agent_roster`, qui ne write rien). Déplacés vers
   `_archive/<date>-pre-<version>/orphans/`, **jamais supprimés**. Doit tourner **avant** `apply` : la
   migration réelle du 2026-09-11 (Terraform-HouseServer, 3.38.0 → 3.44.2) a montré que `up`/`host sync`
   refusent (garde de distinction : deux agents au même faisceau) tant qu'un orphelin partage encore le
   faisceau d'un agent courant.
4. **apply** (V0) — `grimoire up` (refresh du tier kit + host sync). Acceptance : `doctor -o json` sans
   FAIL **et** le hook `SessionStart` rejoué sans mention d'erreur (régression #423 documentée dans le
   même rapport de migration).
5. **overrides** (V1, proposition) — pour chaque override en dérive (`grimoire.core.override_drift.
   project_override_drift`), un essai `agent override convert --dry-run` : corps identique au kit →
   proposition « conversion sûre » ; corps divergent → proposition « revue nécessaire », diff joint.
   Écrit via `grimoire.proposals` (`artifact_type: "override-migration"`) — **jamais** d'override
   réécrit ici. Visible dans `grimoire proposals list` et le cockpit comme toute autre proposition.
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
  Refuse si aucun porteur n'a été trouvé (« à placer à la main »), ou si le porteur n'a pas encore
  d'override à éditer.
- `needs-hosts` — ces propositions décrivent une déclaration à faire dans `project-context.yaml`
  (`needs.commands`, `hosts.enabled`) ; les accepter aujourd'hui ne fait qu'acter la décision (aucune
  écriture de configuration automatique n'est câblée pour ce type — la déclarer reste, sciemment, un
  geste manuel dans `project-context.yaml`).

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

## Portée non couverte par cette livraison

- Le cockpit (`POST /api/projects/update`) appelle encore `up` seul — le brancher sur ce flow est la
  PR suivante (issue #490, deuxième volet) : aperçu par défaut, confirmation pour appliquer, timeline
  par tâche, propositions visibles dans Piloter.
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
