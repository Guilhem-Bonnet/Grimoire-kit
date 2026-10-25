---
audience: décision
---

<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/search.svg" width="32" height="32" alt=""> Sous-agents émis : `omitClaudeMd`, `autoCompactWindow`, `effort` (2026-10)

> Tâche `GAO-veille-sous-001`. Sources ouvertes le 2026-10-25. `claude` n'a pas
> été lancé : le coût de contexte réel avec et sans `omitClaudeMd` **n'est pas
> mesuré** (voir « Mesure » pour ce qui l'est).

## Ce que dit la référence officielle

Page [Subagents](https://code.claude.com/docs/en/sub-agents) `[doc]`, lue le 2026-10-25 (page de 116 000 caractères environ, 100 000 lus).

| Champ | Ce que la page dit | Version |
|---|---|---|
| `omitClaudeMd` | « Set to `true` to start this subagent without the user, project, and local CLAUDE.md files in its initial context ; managed policy files still load, except for managed subagents. » Pensé pour les sous-agents qui tirent tout de l'invite de délégation. Ignoré quand l'agent tourne comme agent de session principal (`--agent` ou réglage `agent`). Défaut : non indiqué | `2.1.271` |
| `autoCompactWindow` | Abaisse la fenêtre d'auto-compactage du sous-agent à un nombre de jetons de 100 000 à 1 000 000. Défaut : non indiqué ; la section liée n'a pas été lue | `2.1.296` |
| `effort` | `low`, `medium`, `high`, `xhigh`, `max` (selon le modèle). Remplace l'effort de session, mais pas la variable `CLAUDE_CODE_EFFORT_LEVEL`, qui l'emporte aussi sur le paramètre d'invocation | champ : non indiquée ; paramètre par invocation : `2.1.292` |

Par défaut les sous-agents chargent toute la hiérarchie `CLAUDE.md` (`~/.claude/CLAUDE.md`, règles du projet, `CLAUDE.local.md`, politiques gérées, `AGENTS.md` chargés comme instructions de projet). Seuls **Explore** et **Plan** sont nommés comme démarrant sans `CLAUDE.md` ni instantané git ; même pour eux et pour `omitClaudeMd`, le `CLAUDE.md` d'un sous-dossier et les règles à chemin restent chargés quand l'agent y travaille.

## État dans le kit

- `git grep -n "omitClaudeMd\|autoCompactWindow" -- src docs framework` : aucun résultat.
- L'émetteur écrit pour chaque agent : `name`, `description`, `tools`, `model`, `effort`, `maxTurns`, et `background: true` hors point d'entrée (`src/grimoire/hosts/emitters/claude_code.py:227-242`, `_agent_file`).
- `effort` est binaire à dessein : `high` si `reasoning: high`, sinon `low` (`claude_code.py:126-131`, `_effort_for`). La référence admet cinq paliers ; `medium`, `xhigh` et `max` ne sont jamais émis. Ce n'est pas un défaut : le commentaire du code dit que l'hôte n'avait pas de palier intermédiaire ; la page actuelle en liste, ce qui rend ce commentaire à revérifier.
- Le défaut économique de délégation (#740, fusionné le 2026-10-13) agit sur `model=`, pas sur le contexte chargé.

## Mesure (statique, pas le coût réel)

Sans lancer l'hôte, on ne peut compter que ce qui serait chargé, pas les jetons. Dans le dépôt Forge (un projet qui consomme le kit), les fichiers que `omitClaudeMd` retirerait du contexte initial d'un sous-agent sont : `CLAUDE.md` (74 octets) qui importe `.github/copilot-instructions.md` (8 405) et `.github/agents/grimoire-master.agent.md` (8 360), plus `RTK.md` (964) et `~/.claude/CLAUDE.md` (8), soit **17 811 octets** (`wc -c`, relevé le 2026-10-25). `AGENTS.md` (2 303 octets) s'y ajoute si l'hôte le charge ; `non vérifié`. Nombre de jetons : **non mesuré**.

## Analyse

- Le gain potentiel existe mais dépend du projet : un `CLAUDE.md` lourd, copié à chaque délégation. Le coût est multiplié par le nombre de délégations (60 appels de personas en 12 jours dans la mesure de #740).
- Le risque est réel : ce `CLAUDE.md` porte les règles du projet et de l'utilisateur (doctrine, interdits). Un sous-agent qui ne le charge pas ne les voit plus ; seuls les hooks et la politique gérée le tiennent. Pour des personas émises par le kit, dont la consigne de dispatch vit dans la persona d'entrée et au `SessionStart` et non dans les fichiers des sous-agents, la perte porte sur les règles du projet de l'utilisateur, qu'on ne contrôle pas.
- Une généralisation sans mesure serait un changement de comportement non prouvé chez tous les utilisateurs.

## Ce que ça change pour le kit

**Rien maintenant.** Décision consignée : pas de généralisation d'`omitClaudeMd`, ni d'`autoCompactWindow` (aucun incident de compactage de sous-agent connu, `maxTurns` borne déjà la durée). Raison : le gain n'est mesurable qu'en faisant tourner l'hôte (quota d'abonnement, hors lot), et la perte des règles du projet n'est pas évaluée.

Condition de réouverture et tâche à créer **seulement sur accord de Guilhem** (elle consomme du quota) : « Mesurer le coût de contexte d'une persona émise avec et sans `omitClaudeMd` », module `src/grimoire/hosts/emitters/claude_code.py` (`_agent_file`). Critère : sur une persona du kit et un projet cobaye, relever les jetons d'entrée de la première requête du sous-agent dans le journal de session (`InstructionsLoaded` peut confirmer les fichiers chargés), 3 essais par bras, écart publié ; décision d'activer au point d'entrée exclu, par option explicite (`hosts.claude.omit_claude_md`), jamais par défaut. À part : revoir le commentaire de `_effort_for` à la lumière des cinq paliers documentés (tâche S, `--check` : le test d'`effort` suit la table décidée).

## Sources consultées le 2026-10-25

- [Subagents](https://code.claude.com/docs/en/sub-agents) — Claude Code Docs `[doc]`
- Issue [#740](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/740) (mesure de délégation) — dépôt du kit
