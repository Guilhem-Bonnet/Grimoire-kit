---
audience: décision
---

# <img src="../assets/icons/search.svg" width="32" height="32" alt=""> Fiches d'agents : `maxTurns` borne-t-il une session headless ? (2026-11)

> Tâche `GAO-fiches-d-age-001`. Vérifié le 2026-11-12 sur Claude Code `2.1.323`
> (binaire installé), avec un faux fournisseur Messages local — aucun appel au
> service Anthropic, aucun quota consommé.

## Question

Un projet enrôlé rapporte que ses fiches d'agents générées portent
`maxTurns: 60`, mais que des sessions `claude -p --agent …` sont allées
jusqu'à 148 tours. Le kit ne transmet-il pas le champ, ou l'hôte l'ignore-t-il
dans ce mode ? Et que faire du champ : le retirer des fiches, ou le garder en
documentant sa portée ?

## Ce que le kit émet

L'émetteur Claude Code écrit `maxTurns` (camelCase, comme l'hôte l'attend)
dans le frontmatter de chaque fiche sous `.claude/agents/<nom>.md` :

- `src/grimoire/hosts/emitters/claude_code.py:222-237` — `_agent_file` place
  `"maxTurns": _max_turns_for(agent)` dans les champs du frontmatter, à côté de
  `name`, `description`, `tools`, `model` et `effort`.
- `src/grimoire/hosts/emitters/claude_code.py:134-137` — `_max_turns_for` rend
  `agent.max_turns` quand la fiche source déclare `max_turns:`, sinon
  `_DEFAULT_MAX_TURNS`.
- `src/grimoire/hosts/emitters/claude_code.py:74` — `_DEFAULT_MAX_TURNS = 30`.

Exemple dans ce dépôt, après `grimoire host sync` :
`.claude/agents/concierge.md:8` porte `maxTurns: 30`.

Le champ est donc bien transmis, sous le nom exact que l'hôte documente. Le
`60` du rapport **n'a pas été reproduit sur `main`** : le défaut y est 30, et
60 suppose une fiche source déclarant `max_turns: 60` (ou une version
différente du kit) — **non vérifié** pour le projet rapporteur.

## Ce que fait l'hôte

### Référence

- [Subagents](https://code.claude.com/docs/en/sub-agents.md), lue le
  2026-11-12 : `maxTurns` y est décrit comme « Maximum number of agentic turns
  before the subagent stops », dans le tableau des champs de frontmatter des
  **sous-agents**. La même table précise pour `omitClaudeMd` qu'il est ignoré
  quand l'agent tourne comme agent de session principal (`--agent`) ; aucune
  note équivalente ne dit que `maxTurns` s'applique au fil principal.
- [CLI reference](https://code.claude.com/docs/en/cli-reference.md), lue le
  2026-11-12 : `--max-turns` — « Limit the number of agentic turns (**print
  mode only**). **No limit by default.** »
- [Environment variables](https://code.claude.com/docs/en/env-vars.md), lue le
  2026-11-12 : `CLAUDE_CODE_MAX_TURNS` — « Cap the number of agentic turns when
  no explicit limit is passed. Equivalent to passing `--max-turns`, which takes
  precedence when both are set. »

### Code du binaire 2.1.323

Lecture des chaînes du binaire installé
(`/home/guilhem/.local/share/claude/versions/2.1.323`) :

- Le plafond du fil principal est résolu par `lOa(o.maxTurns)` : la valeur
  vient de l'option CLI `--max-turns` ou, à défaut, de
  `CLAUDE_CODE_MAX_TURNS`. Aucune lecture du `maxTurns` de la définition
  d'agent du fil principal n'apparaît (aucun `?.maxTurns` sur
  `mainThreadAgentDefinition`).
- Le lancement d'un sous-agent passe en revanche le `maxTurns` de sa fiche à sa
  boucle de requête (`maxTurns: e.maxTurns` dans les options de `query`), et le
  client traite l'événement `max_turns_reached` (`Reached maximum number of
  turns (N)`, sous-type `error_max_turns`).
- Les fiches d'agent voient leur `maxTurns` validé comme entier positif au
  chargement (`Agent file … has invalid maxTurns '…'. Must be a positive
  integer.`), ce qui confirme que le champ est reconnu — mais sa consommation
  est celle des sous-agents.

## Mesure

Protocole : un faux serveur HTTP local implémente `/v1/messages` et renvoie,
en SSE, un `tool_use` `Bash("true")` à chaque tour (puis `end_turn` après un
plafond de sécurité de 8 tours) ; `ANTHROPIC_BASE_URL` pointe dessus,
`HOME` est vide et `--setting-sources project` isole le projet. Le nombre de
requêtes `/v1/messages` compte donc les tours agentiques réellement demandés
par le client. Fiche de test : `maxTurns: 2`, corps « Tu es une sonde ».
Modèle demandé : alias `haiku` (le faux serveur l'ignore).

| Cas | Requêtes Messages | Fiche chargée dans le `system` | Sortie |
|---|---|---|---|
| `--agent probe` (fiche `maxTurns: 2`), **sans** `--max-turns` | **9** (= 8 tours + fin) | oui, 9/9 | exit 0, `num_turns: 9`, `terminal_reason: completed` |
| aucun agent, `--max-turns 3` | **3** | — | exit 1, `error_max_turns`, « Reached maximum number of turns (3) » |
| `--agent probe` + `--max-turns 3` | **3** | oui, 3/3 | exit 1, `error_max_turns` — l'option CLI l'emporte |
| fil principal → `Agent(subagent_type: probe)` (fiche `maxTurns: 2`) | **2** pour le sous-agent | oui, 2/2 | sous-agent `completed`, `spawned: 1`, `num_turns: 1` côté parent |

Lecture : la fiche est bien chargée comme agent de session principale (son
corps est dans le `system` de chaque requête), mais son `maxTurns: 2` n'est
pas appliqué : la session a continué jusqu'au plafond du faux serveur. En
revanche, la même fiche **borne bien un sous-agent** : deux tours, puis arrêt
propre et reprise de la main.

## Décision

**Garder `maxTurns` dans les fiches émises, et documenter sa portée.**

- Le champ est utile et réellement appliqué là où il est le plus fréquent :
  tout agent non point d'entrée est émis avec `background: true` et tourne
  comme sous-agent, où `maxTurns` borne effectivement la boucle (mesure
  ci-dessus). Le retirer supprimerait une protection qui fonctionne.
- Il ne borne **pas** une session dont la fiche est l'agent du fil principal,
  en particulier `claude -p --agent <nom>`. Dans ce mode, le plafond de tours
  se règle par `--max-turns N` (mode `--print` uniquement) ou par
  `CLAUDE_CODE_MAX_TURNS`, `--max-turns` l'emportant quand les deux sont posés.
  Sans l'un des deux, il n'y a **aucun plafond** par défaut.
- Aucun changement de l'émetteur : les fiches gardent `maxTurns` (défaut 30,
  ou `max_turns:` de la fiche source). Les tests existants restent valides :
  `tests/unit/test_hosts.py::test_sub_agents_carry_effort_max_turns_and_background`
  et `::test_max_turns_override_from_the_agent_file_wins` couvrent l'émission.
- Pour un orchestrateur qui lance `claude -p --agent …`, la borne doit être
  passée à l'invocation. Le kit le fait déjà pour ses propres sondes headless
  (`src/grimoire/hosts/contract_probe.py:254` passe `--max-turns 4`).

Le constat rapporté (une session à 148 tours malgré `maxTurns: 60`) est donc
cohérent avec le comportement de l'hôte : ce n'est pas le kit qui perd le
champ, c'est le mode headless en fil principal qui ne le lit pas.

## Non vérifié

- Le `maxTurns: 60` et les 148 tours du projet rapporteur : non reproduits ici,
  faute d'accès à sa configuration et à son journal de session.
- Le comportement d'un abonnement ou d'une passerelle réelle : les mesures
  n'ont utilisé que le faux fournisseur local, qui prouve la politique de tours
  du client, pas une éventuelle limite côté service.
- La borne d'un sous-agent en mode interactif (hors `-p`) n'a pas été mesurée ;
  seul le chemin de dispatch en mode `--print` l'a été.

## Sources consultées le 2026-11-12

- [Subagents](https://code.claude.com/docs/en/sub-agents) — Claude Code Docs
- [CLI reference](https://code.claude.com/docs/en/cli-reference) — `--max-turns`
- [Environment variables](https://code.claude.com/docs/en/env-vars) — `CLAUDE_CODE_MAX_TURNS`
- Binaire `claude` 2.1.323 installé localement (lecture des chaînes et mesure
  contre un faux serveur Messages local)
- `src/grimoire/hosts/emitters/claude_code.py`, `src/grimoire/hosts/contract_probe.py`,
  `tests/unit/test_hosts.py` — dépôt du kit
