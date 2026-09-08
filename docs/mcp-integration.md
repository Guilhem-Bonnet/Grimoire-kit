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
| `grimoire_add_agent` | Ajouter un agent au projet | écriture, destructif, idempotent |
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

| Révision | Rôle ici |
|---|---|
| **2025-11-25** | Révision servie par le SDK Python `mcp` 1.x que le kit épingle (`mcp>=1.10,<3`). C'est ce que voit un client aujourd'hui. |
| **2026-07-28** | Révision courante de la spécification. Le kit ne la sert pas encore. |

Ce que la révision 2026-07-28 change, et ce que la migration coûtera :

- **Protocole sans état** : plus d'`initialize`, plus de session. `server/discover`
  devient obligatoire, la version et les capacités voyagent dans `_meta` à chaque
  requête. Le serveur du kit est aujourd'hui monté par la façade du SDK
  (`FastMCP` / `MCPServer`) : la migration se fera par montée du SDK, pas par
  réécriture — à condition que la façade suive.
- **MRTR** remplace toute requête initiée par le serveur : un outil qui a besoin
  d'un complément renvoie `resultType: "input_required"`, le client ré-émet avec
  `inputResponses`. Les vingt-deux outils du kit sont synchrones et sans
  élicitation : rien à porter.
- **`resultType` obligatoire** sur les résultats, `ttlMs` et `cacheScope`
  obligatoires sur les listes. À produire par la façade.
- **Dépréciés, retrait possible dès le 2027-07-28** : Roots, Sampling, Logging,
  Dynamic Client Registration, HTTP+SSE. Le kit n'utilise aucun des cinq — ni
  Sampling (contrairement à ce qu'annonçait encore une feuille de route interne),
  ni Roots. Il expose des outils, des prompts et des ressources, tous conservés.
- **Propagation OpenTelemetry dans `_meta`** : point d'accroche pour relier les
  traces du kit à celles du client, pas encore câblé.

Coût estimé de la migration : montée de borne SDK et vérification des vingt-deux
annotations plus des sorties `isError`, sans changement de la surface d'outils.

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
