# Intégration MCP — Grimoire Kit v3

> Exposer les outils Grimoire via le Model Context Protocol pour Copilot, Claude Desktop, et tout client MCP.

## Prérequis

```bash
pip install grimoire-kit[mcp]
```

## Démarrage rapide

```bash
# Lancer le serveur MCP
grimoire-mcp

# Ou directement via Python
python -m grimoire.mcp.server
```

## Configuration VS Code

Créez `.vscode/mcp.json` à la racine de votre projet :

```json
{
  "servers": {
    "grimoire": {
      "command": "grimoire-mcp"
    }
  }
}
```

Ou avec un chemin Python explicite :

```json
{
  "servers": {
    "grimoire": {
      "command": "python",
      "args": ["-m", "grimoire.mcp.server"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

## Configuration Claude Desktop

Dans `claude_desktop_config.json` :

```json
{
  "mcpServers": {
    "grimoire": {
      "command": "python",
      "args": ["-m", "grimoire.mcp.server"],
      "cwd": "/chemin/vers/projet"
    }
  }
}
```

## Outils exposés

| Outil | Description | Annotation |
|-------|-------------|------------|
| `grimoire_project_context` | Retourne le contexte projet complet (JSON) | lecture |
| `grimoire_status` | État du projet (agents, mémoire, santé) | lecture |
| `grimoire_agent_list` | Liste des agents installés | lecture |
| `grimoire_harmony_check` | Exécute un Harmony Check et retourne le rapport | lecture |
| `grimoire_config` | Configuration brute du projet | lecture |
| `grimoire_memory_store` | Stocker un texte en mémoire sémantique | écriture, destructif, monde ouvert |
| `grimoire_memory_search` | Recherche sémantique dans la mémoire | lecture, monde ouvert |
| `grimoire_add_agent` | Créer un agent dans `_grimoire/overrides/agents/` à partir du gabarit `custom-agent` | écriture, destructif |
| `grimoire_standard_verify` / `_audit` / `_score` / `_gate` | Le standard agentique : vérifier, auditer, scorer, opposer les gates | lecture ; `_score` persiste le score et `_gate` journalise le passage |
| `grimoire_host_status` / `grimoire_skill` / `grimoire_command` | Les surfaces hôtes, pour un client sans émetteur | lecture |
| `grimoire_providers_status` | Fournisseurs LLM activés, modèles par palier (cheap/mid/strong), refroidissement, dernier audit (`available`/`probed_at`/`models_seen`/`probe_note`, issue #330) et prochain choix — même donnée que `grimoire providers status` | lecture, monde ouvert |
| `task_list_ready` | Les tâches qu'un agent peut réclamer maintenant | lecture |
| `task_show` | Une tâche : état, acceptation, claim, et ce que chaque prochain pas exigera | lecture |
| `task_claim` | Réclamer une tâche prête (`ready → claimed`) | écriture, destructif |
| `task_update` | Déplacer (`move`), bloquer (`block`) ou fermer (`close`) une tâche | écriture, destructif |
| `task_context` | Sur quelle tâche la session est, et son context bundle | écriture : produit `context-bundle.yaml` et journalise |
| `task_recall` | Ce que la mémoire du projet sait de cette tâche et de ses voisines — borné en tokens | lecture |

### Les tâches : un outil, pas du texte dans un prompt

Les six outils `task_*` appellent le même service que `grimoire task`
(`grimoire.missions.service.TaskService`), donc le même gate de preuve
(`_grimoire/standard/evidence-gates.yaml`). Une transition que le CLI refuse,
MCP la refuse pour la même raison, et le refus est structuré :

```json
{
  "blocked": true,
  "task_id": "GAO-exposer-les-001",
  "transition": "ready_to_in_progress",
  "strictness": "hard_fail",
  "refusals": [
    {
      "evidence": "context_bundle",
      "reason": "context bundle absent",
      "remedy": "attendu : _grimoire-output/context/GAO-exposer-les-001/context-bundle.yaml"
    }
  ]
}
```

Rien n'est écrit au ledger sur un refus. Après une transition acceptée, le board
`_grimoire/standard/task-board.yaml` est reprojeté, et le hook `SessionStart`
de la session suivante nomme la tâche réclamée : `task_context` sans argument
rend `{"task_id": ..., "resolved_from": "ledger_claim"}`. Pour qu'un agent ne
se voie attribuer que ses propres claims, poser `GRIMOIRE_ACTOR` à la valeur
passée en `actor` à `task_claim` (règle complète dans la
[référence CLI](cli-reference.md#quelle-tâche-la-session-porte)).

Le parcours nominal d'un agent : `task_list_ready` → `task_context(task_id)`
(produit le bundle que le gate exige) → `task_claim` → `task_update(move,
running)` → travail et preuves → `task_update(move, needs_verification)` →
`task_update(close)`.

`task_recall` (#141) rend, pour la tâche active ou un identifiant explicite,
l'historique propre de la tâche, ses voisines et la cause de leur arrêt, et ce
que la mémoire du projet a consolidé sur des sujets voisins — borné en tokens.
C'est le même rappel que celui que le hook `SessionStart` injecte
automatiquement après un `task_claim` réussi, et que `grimoire task recall`
côté CLI ; voir [référence CLI](cli-reference.md#ce-que-le-claim-rappelle)
pour le détail. Une clôture ou un blocage (`task_update`, `close`/`block`)
consolide dans cette même mémoire ce qui a été appris — jamais un mouvement
ordinaire.

## Révision de protocole

Migré en 2026-09 (issue #436) : le kit épingle désormais `mcp>=2.0,<3`, la
première branche du SDK qui sait parler la révision courante.

| Révision | Rôle ici |
|---|---|
| **2026-07-28** | Révision négociée par défaut. Un client moderne qui sonde `server/discover` la reçoit directement — c'est le SDK (`mcp.server.lowlevel.Server`, monté par `MCPServer`) qui répond, aucune ligne du pont ne s'en occupe. |
| **2025-06-18** / **2025-11-25** | Toujours servies, sur la même connexion, pour un hôte qui n'a pas encore de client `server/discover` : le SDK sert les deux ères en parallèle (`serve_dual_era_loop`) et négocie exactement la révision que le handshake `initialize` propose. |
| **mcp 1.x** | N'est plus une cible : plafonne à 2025-11-25 côté SDK (pas de `server/discover`, pas de mode sans état) et sort de la plage `mcp>=2.0,<3`. |

Ce que la migration a changé, et ce qu'elle n'a pas eu besoin de changer :

- **Protocole sans état** : le pont ne gardait déjà aucun état entre deux
  appels d'outil en dehors des fichiers du projet — chaque outil relit
  `project-context.yaml` (ou l'équivalent) à chaque appel. Vérifié par un test
  qui appelle `grimoire_status` sur deux projets différents à travers deux
  connexions indépendantes et compare les réponses
  (`tests/unit/mcp/test_protocol_revision.py`).
- **`server/discover`** : géré par défaut dans le SDK à partir de `mcp` 2.0.0 —
  auto-dérivé des gestionnaires enregistrés (outils, prompts, ressources).
  Rien à écrire côté pont.
- **Compatibilité avec un hôte 2025-06-18** : testée explicitement (le SDK
  négocie exactement la version que le handshake propose, sans monter la mise
  de son côté).
- **MRTR** remplace toute requête initiée par le serveur : un outil qui a
  besoin d'un complément renvoie `resultType: "input_required"`, le client
  ré-émet avec `inputResponses`. Les outils du kit sont synchrones et sans
  élicitation : rien à porter.
- **`ttlMs` et `cacheScope`** sur les listes (`tools/list`, `prompts/list`,
  `resources/list`) : remplis par défaut par le SDK (`ttl_ms=0`,
  `scope="private"`) quand le pont ne les fixe pas explicitement — un client
  ne met donc jamais en cache une liste que le pont n'a pas explicitement
  déclarée réutilisable.
- **Dépréciés, retrait possible à partir du 2027-07-28** : Roots, Sampling,
  Logging, Dynamic Client Registration, HTTP+SSE. Le pont n'a jamais câblé
  aucun des cinq (transport stdio uniquement, pas de callback
  `sampling`/`roots`/`logging` sur la session) : rien à retirer.
- **Propagation OpenTelemetry dans `_meta`** : le SDK émet un span serveur par
  message par défaut (`OpenTelemetryMiddleware`), sans configuration côté
  pont ; le relier à un exporteur applicatif reste hors périmètre de cette
  migration.

Hors périmètre, explicitement : transport HTTP/SSE, authentification,
exposition réseau distante. Le pont reste stdio, en local, un seul projet.

## Annotations et erreurs

Chaque outil déclare ses quatre indices (`readOnlyHint`, `destructiveHint`,
`idempotentHint`, `openWorldHint`) : un hôte qui distingue lecture et écriture
peut auto-approuver la première. La spécification rappelle que ce sont des
*indices*, non fiables pour un serveur inconnu — ici le serveur est le kit
lui-même, et `tests/unit/mcp/test_server.py::TestToolContract` refuse tout outil
non annoté.

Un échec franc porte désormais `isError` **en plus** du corps JSON, jamais à la
place :

```json
{
  "error": "[…] Refusé : le texte porte un motif de consigne (override-en).",
  "refused": true,
  "code": "memory.instruction_like_content",
  "remedy": "Reformuler en énoncé factuel, ou citer le texte comme extrait attribué à sa source."
}
```

Un refus de *gate* de preuve fait exception et n'est pas marqué `isError` : la
porte a fonctionné, l'appel a répondu, et le corps nomme la preuve manquante.
Un hôte qui filtre sur `isError` doit donc lire `blocked` pour distinguer un
refus d'un succès. Le choix est figé par
`tests/unit/mcp/test_task_tools.py::TestGateRefusalIsNotAToolFailure` : l'inverser
fait échouer un test, il ne se fait pas en silence.

Réciproquement, `readOnlyHint` est vérifié plutôt que déclaré :
`TestReadOnlyToolsWriteNothing` appelle chaque outil annoté en lecture sur un
projet gouverné et compare l'arborescence avant et après. C'est ce contrôle qui
a montré que `task_context` écrivait son bundle et que `grimoire_standard_gate`
journalisait son passage — deux annotations qui mentaient, alors qu'un hôte
auto-approuve sur leur foi.

## Exemples d'utilisation

Dans Copilot Chat ou Claude, les outils sont appelés automatiquement quand le LLM détecte le besoin :

**"Quel est le stack de ce projet ?"**
→ L'agent appelle `grimoire_project_context` et extrait la liste du stack.

**"Ajoute l'agent architect au projet"**
→ L'agent appelle `grimoire_add_agent("architect")`.

**"Y a-t-il des problèmes dans le projet ?"**
→ L'agent appelle `grimoire_harmony_check` et résume le rapport.

**"Mémorise que nous avons choisi PostgreSQL"**
→ L'agent appelle `grimoire_memory_store("Décision: PostgreSQL comme base de données")`.

## Architecture

```
┌─────────────────────┐
│  LLM (Copilot/Claude) │
└──────────┬──────────┘
           │ MCP Protocol (stdio)
┌──────────▼──────────┐
│  grimoire-mcp server    │  ← FastMCP
│  (grimoire.mcp.server)  │
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│  Grimoire SDK           │
│  config / project   │
│  tools / memory     │
└─────────────────────┘
```

Le serveur MCP est un pont entre le protocole MCP (stdin/stdout JSON-RPC) et le SDK Python Grimoire.

## Voir aussi

- [Guide SDK](sdk-guide.md)
- [Référence YAML](grimoire-yaml-reference.md)
- [Getting Started](getting-started.md)
