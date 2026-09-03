# Surfaces hôtes

Un projet Grimoire décrit des personas, des compétences, des commandes et une
gouvernance. Chaque hôte agentique — Claude Code, GitHub Copilot, Codex, Cursor,
Gemini CLI — expose une part de cela sous forme **exécutable** : fichiers de
sous-agents chargés dans leur propre fenêtre de contexte, compétences chargées à
la demande, commandes utilisateur, hooks de cycle de vie capables de refuser une
action ou une clôture, table de permissions déclarative.

Décrire un projet en prose et laisser chaque hôte la lire plafonne tout le monde
au plus petit dénominateur commun. La couche « surfaces hôtes » fait l'inverse :
**une description host-neutre du projet, un émetteur par hôte**.

```text
projet (agents, standard, mémoire)
        │
        ▼
  ProjectSurface  ← description host-neutre, construite une fois
        │
        ├──▶ émetteur Claude Code  → .claude/{agents,skills,commands}, settings.json
        ├──▶ émetteur Copilot      → .github/{agents,skills,prompts,hooks}
        └──▶ émetteur prose        → AGENTS.md · GEMINI.md · .cursor/rules
```

La règle de gouvernance, elle, n'est écrite qu'une fois. Le module de décisions
est du Python host-neutre ; le module de wire format le traduit dans le JSON de
chaque hôte. Un refus formulé sous Claude Code est **le même texte** que sous
Copilot, parce que c'est la même décision.

## Commandes

| Commande | Effet |
|---|---|
| `grimoire host list` | Hôtes connus, ce que chacun sait exécuter, hôte détecté |
| `grimoire host surface` | Description host-neutre du projet |
| `grimoire host sync --host all` | Génère les surfaces (ajouter `--dry-run` pour voir sans écrire) |
| `grimoire host status` | Écart entre ce que le projet déclare et ce que l'hôte exécute |
| `grimoire host run <slug>` | Corps d'une commande, pour un hôte sans commandes natives |

`grimoire init`, `grimoire up --fix` et `grimoire standard init` appellent la
synchronisation automatiquement. Un appel manuel n'est nécessaire qu'après avoir
ajouté ou modifié une persona.

## Ce que chaque hôte exécute

| Hôte | Sous-agents | Compétences | Commandes | Hooks bloquants | Permissions |
|---|---|---|---|---|---|
| Claude Code | oui | oui | oui | oui | oui |
| GitHub Copilot | oui | oui | oui | oui | non |
| Codex | non | non | non | non | non |
| Cursor | non | non | non | non | non |
| Gemini CLI | non | non | non | non | non |

Rien n'est abandonné en silence. Ce qu'un hôte ne sait pas exécuter est déclaré
comme **dégradation**, avec son repli, et remonté par `grimoire host status` :

- Copilot n'a pas de table de permissions déclarative : les mêmes règles sont
  appliquées par le hook `PreToolUse`, avec la même formulation de refus.
- Les hôtes en prose n'ont pas de hooks : la gouvernance y est énoncée comme
  règle dans le fichier d'entrée, et n'est opposable qu'en CI. Le catalogue le
  dit explicitement plutôt que de laisser croire à une protection.

## Persona d'entrée

Chaque projet désigne une persona d'entrée — `concierge` par défaut — celle qui
tranche quand une demande ne désigne pas clairement un rôle. Aucun hôte ne sait
ouvrir une session *à l'intérieur* d'un agent : Claude Code n'instancie un
sous-agent que par son outil Agent, Copilot que par le sélecteur de chat. Ce
n'est pas un réglage manquant, c'est une propriété des hôtes, et le catalogue
la déclare : `agent_autostart` est faux sur les cinq profils, et
`grimoire host list` le remonte comme une dégradation avec son substitut.

Le substitut est fourni là où un hook `session_start` s'exécute : le hook remet
la persona à la boucle principale, avant la directive du standard. La session
n'est pas ouverte dans l'agent — elle en adopte la persona en gardant toute la
surface d'outils de l'hôte, et dispatcher un sous-agent reste un choix.

```text
[Grimoire — persona d'entrée]
Cette session adopte la persona d'entrée du projet dans sa boucle principale,
sans sous-agent : **concierge** — Concierge — Triage, clarification, routage…

1. Lis `_grimoire/kit/agents/concierge.md` en entier avant de répondre…
2. Tiens sa frontière d'outils pour ce que tu fais toi-même : read, search, edit, execute.
3. Tu restes la boucle principale : dispatcher un sous-agent reste un choix.
```

Sur un hôte sans hook `session_start`, la persona d'entrée est nommée dans le
fichier d'instructions ; c'est le seul endroit qui reste.

Un projet qui porte déjà son propre point d'entrée — un orchestrateur chargé
par `CLAUDE.md`, par exemple — en reçoit un second : la persona d'entrée n'est
pas encore configurable par projet. C'est l'objet de
[#247](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/247).

## Gouvernance

Les hooks générés dépendent de l'enrôlement du projet dans le standard agentique.

| Événement | Décision | Portée |
|---|---|---|
| `session_start` | directive de session | toujours |
| `pre_tool_use` | politique d'outils (destructif, secrets) | toujours, **bloquant** |
| `user_prompt_submit` | nomme la tâche courante | projet enrôlé |
| `post_tool_use` | rappel de preuve après écriture | projet enrôlé |
| `pre_compact` | capsule de gouvernance avant compaction | projet enrôlé |
| `subagent_stop` | état des gates, sans bloquer | projet enrôlé |
| `stop` | gates de preuve | projet enrôlé, **bloquant** |

Un projet non enrôlé ne reçoit aucun hook de gate : un gate inexistant ne peut
pas être rouge, et bloquer sur son absence ferait du hook un piège.

### Le hook `stop`

C'est le seul endroit où la règle du kit — « une clôture sans gates verts est une
tâche non terminée » — devient une contrainte plutôt qu'une consigne. Sur les
profils `governed` et `production`, une tâche dont les gates sont rouges voit sa
clôture refusée, avec la liste de ce qui manque.

Trois garde-fous encadrent ce refus :

1. **Pas de boucle** — si l'hôte relance déjà l'agent à cause d'un blocage
   précédent, le hook laisse passer.
2. **Pas de blocage à vide** — un projet non enrôlé, un profil `starter`, ou une
   tâche encore à l'état `proposed` ne bloquent jamais. Ce dernier cas est
   signalé : un gate vert parce que rien n'est encore exigé ne protège rien, et
   le hook le dit au lieu de laisser croire le contraire.
3. **Pas de panne fatale** — un `task-board.yaml` cassé ou une exception dans une
   décision sortent en « autorisé », avec l'erreur en contexte. Un hook qui
   plante ne doit pas rendre une session inutilisable.

### Politique d'outils

La décision `pre_tool_use` fait passer chaque appel mutant par le moteur de
politique du kit. Le nom de l'outil est lu dans le vocabulaire de n'importe quel
hôte (`Bash` comme `run_in_terminal`, `Edit` comme `replace_string_in_file`),
puis classé en famille neutre.

| Constat | Verdict |
|---|---|
| Suppression récursive, force push, `terraform destroy`, `kubectl delete`… | refus sous les profils non stricts, confirmation demandée en strict |
| Lecture d'un fichier de secrets (`.env`, clés privées, `credentials.json`…) | refus à tous les profils |
| Appel en lecture seule | autorisé sans traitement |

## Coût des hooks

Un hook s'exécute une fois par appel d'outil : son coût est une propriété de
conception, pas un détail d'implémentation.

- Les configurations générées invoquent `grimoire-hook`, un script console
  dédié. Passer par `grimoire host hook` construit tout l'arbre de commandes
  avant d'en résoudre une seule — 391 ms par appel contre 102 ms. La
  sous-commande reste disponible pour un usage humain.
- Le chemin de décision n'importe pas le moteur du standard au chargement :
  évaluer des gates en a besoin, décider d'un appel d'outil non. Un test échoue
  si cette frontière est franchie.
- Sur un hôte doté d'une table de permissions, `Read` ne figure pas dans le
  matcher : les fichiers de credentials y sont déjà refusés déclarativement, à
  coût nul. L'accès par commande shell reste couvert par `Bash`.

## Ce que la gouvernance enregistre

Chaque appel d'outil réellement évalué et chaque décision de clôture sont
consignés dans `_grimoire-output/traces/traces.jsonl`. Un refus qui ne laisse
pas de trace ne peut pas être mesuré, et un garde-fou non mesuré reste une
affirmation.

```bash
grimoire -o json standard verify .   # l'état déclaré
```

Le ledger répond à une autre question : ce qui s'est réellement passé.
`TraceLedger.policy_block_rate()` donne la fraction des appels évalués qui ont
été refusés.

Trois limites assumées :

- **les appels en lecture seule n'écrivent rien** — ils sortent avant toute
  évaluation, et le chemin le plus chaud reste libre ;
- **les arguments sont hachés**, jamais stockés tels quels : le fichier part sur
  disque et s'exporte en OTel, une trace qui cite une commande devient une fuite ;
- **un ledger illisible n'interrompt rien** — l'observabilité ne vaut jamais une
  session cassée.

## Frontière d'outils des personas

Chaque persona est projetée avec une frontière d'outils. Elle vient du champ
`tools:` de son frontmatter quand il existe :

```yaml
---
name: "scribe"
description: "Scribe — documentation"
tools: ["read", "search", "edit"]
---
```

Sans ce champ, la frontière est **déduite** du texte de la persona : lecture et
recherche toujours, écriture et exécution seulement sur un signal explicite.
`grimoire host status` liste les personas dont la frontière est déduite —
l'ajout d'un `tools:` explicite est la façon de la figer.

Verbes disponibles : `read`, `search`, `edit`, `execute`, `web`. Chaque émetteur
les traduit dans le vocabulaire de son hôte.

## Fichiers générés et fichiers à vous

Les fichiers générés portent un marqueur `grimoire:managed`. La synchronisation
ne réécrit qu'eux :

- un fichier écrit à la main à un chemin géré est **préservé** et signalé comme
  conflit (`--force` pour l'écraser sciemment) ;
- `.claude/settings.json` n'est jamais réécrit en bloc : seules les entrées de
  hooks appartenant au kit sont remplacées, le reste de la configuration est
  conservé tel quel ;
- une synchronisation répétée ne produit aucune écriture si rien n'a changé.

Pour personnaliser durablement, modifier la source — la persona dans
`_grimoire/`, la compétence ou la commande du kit — puis resynchroniser.

Chaque chemin a un seul propriétaire. `.github/agents/` appartient à l'émetteur
Copilot, `.github/prompts/` au scaffolder pour les workflows du kit, et
`.claude/**` à l'émetteur Claude Code. Deux générateurs sur un même fichier
produisent un conflit permanent, jamais un contenu stable.

## Hôtes sans émetteur

Un hôte sans émetteur n'est pas pour autant sans surface : **MCP est le seul
canal que tous partagent**, et le serveur Grimoire y expose les trois primitives.

| Primitive | Contenu | Ce que le client en fait |
|---|---|---|
| Prompts | les commandes du projet | des slash commands, sans émetteur |
| Resources | les compétences, sous `grimoire://skill/<slug>` | un corps chargeable à la demande |
| Tools | `grimoire_host_status`, `grimoire_skill`, `grimoire_command`, … | l'inventaire et l'état |

C'est la réponse à « peu importe l'hôte » — pour le **contenu**. La
**contrainte** (hooks, permissions) reste l'affaire des émetteurs : MCP
n'intercepte rien.

Ajouter un hôte se fait en écrivant un émetteur et un profil de capacités ; ni la
description du projet, ni les règles de gouvernance ne changent.
