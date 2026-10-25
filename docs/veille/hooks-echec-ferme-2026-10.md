---
audience: décision
---

<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/search.svg" width="32" height="32" alt=""> Hooks Claude Code en échec fermé (`onFailure`) et nouveaux événements (2026-10)

> Tâche `GAO-veille-hook-001` du tri du kanban. Sources ouvertes le 2026-10-25
> (documentation officielle, liste en bas). Étiquettes : `[doc]` lu le jour même,
> `[non vérifié]` non établi. Le binaire `claude` n'a pas été lancé : ce que la
> version locale de Claude Code fait réellement n'est **pas observé** ici.

## Ce que dit la référence officielle

Page [Hooks reference](https://code.claude.com/docs/en/hooks) `[doc]`, lue le 2026-10-25 (33 événements listés ; la page fait environ 282 000 caractères, seuls les 100 000 premiers ont été lus, ce qui couvre le tableau des événements et la section `onFailure`, mais pas le détail par événement de `TaskCreated`, `TaskCompleted`, `InstructionsLoaded`, `PreModelSwitch`, `PostModelSwitch`).

### `onFailure` (« Requires Claude Code v2.1.295 or later »)

- Valeurs : `"continue"` ou `"block"`. Types de hook concernés : `command` et `http`. La page ne dit pas si le champ s'applique aux hooks `mcp_tool`, `prompt` ou `agent` (`non vérifié`).
- Défaut : l'action **a lieu** quand le hook échoue (HTTP : `continue` ; commande : champ non posé, même comportement).
- Ce qui compte comme échec : le hook ne démarre pas, code de sortie autre que 0 ou 2, connexion HTTP refusée ou statut non 2xx, délai dépassé, sortie invalide (JSON illisible ou hors schéma). Un code autre que 0 ou 2 est un échec **même si** le hook a imprimé un JSON qui autorisait l'action.
- Effet de `"block"` : l'échec fait ce que fait le code 2 sur cet événement, sauf sur `PermissionRequest` où il refuse la demande.
- **Sans effet** sur `Stop`, `SubagentStop`, `TaskCompleted`, `TeammateIdle`, ni sur les hooks de commande en arrière-plan (`async`, `asyncRewake`).
- Sur `PreToolUse`, un hook `command`, `http` ou `mcp_tool` qui dépasse son délai ne bloque pas l'appel sauf `onFailure: "block"`.

### Les autres événements demandés

| Événement | Ce que la page dit | Version |
|---|---|---|
| `PreModelSwitch` | Avant un changement de modèle demandé par l'utilisateur ou un client ; peut bloquer (code 2 ou `decision: "block"`) ; champs `from_model`, `to_model` ; délai par défaut 30 s | non indiquée sur la page |
| `PostModelSwitch` | Après le changement, y compris ceux faits par Claude Code (reprise de session) ; ne bloque pas ; contexte seulement via `hookSpecificOutput.additionalContext` | non indiquée |
| `TaskCreated` | À la création d'une tâche **par l'outil `TaskCreate`** ; le code 2 annule la création ; pas de `matcher` | non indiquée |
| `TaskCompleted` | Quand une tâche est marquée terminée ; le code 2 l'en empêche ; pas de `matcher` | non indiquée |
| `InstructionsLoaded` | Quand un `CLAUDE.md` ou `.claude/rules/*.md` entre dans le contexte (début de session, chargement paresseux, compaction) ; code de sortie ignoré ; `matcher` = raison du chargement (`session_start`, `nested_traversal`, `path_glob_match`, `include`, `compact`) | non indiquée |

Les versions `2.1.251` (modèle) citées par la tâche viennent du changelog, qui n'a pas été rouvert pour cette note : `non vérifié` côté page de référence. Les champs d'entrée de `TaskCreated`, `TaskCompleted`, `InstructionsLoaded` : `non vérifié` (hors des 100 000 caractères lus). Point d'attention : `TaskCreated`/`TaskCompleted` portent sur les tâches de l'hôte (`TaskCreate`, équipes d'agents), pas sur le registre de tâches du kit.

## État dans le kit

- Aucun `onFailure` n'est émis : `git grep -n onFailure -- src docs framework` ne trouve rien dans le code. Les entrées de hook sont `{"type": "command", "command": …, "timeout": …}` (`src/grimoire/hosts/emitters/claude_code.py:320-334`, `_hook_entry`).
- Les hooks déclarés `BLOCKING` sont deux, dans `governance_hooks` (`src/grimoire/hosts/collect.py:682` et `:727`) : `PreToolUse` (`grimoire.tool-policy`, délai 10 s) et `Stop` (`grimoire.evidence-gate`, délai 60 s). Tous les autres sont `ADVISORY`.
- Les événements émis (`_WIRE_NAMES`, `claude_code.py:417-427`) sont neuf : `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `SubagentStart`, `SubagentStop`, `PreCompact`, `Stop`. `TaskCreated`, `TaskCompleted`, `InstructionsLoaded`, `PreModelSwitch`, `PostModelSwitch` ne sont pas consommés (lot 5.4 de #555, toujours ouvert).
- Le programme lancé est le script console `grimoire-hook`, résolu par `resolve_hook_launcher` (#760, `src/grimoire/hosts/hook_launcher.py`). S'il n'est pas trouvé, le hook « ne démarre pas » : c'est un échec au sens de la page, donc aujourd'hui ouvert.
- Version d'hôte enregistrée dans le contrat du kit : `2.1.301` (`src/grimoire/hosts/capabilities.py:381`) ; c'est une valeur consignée, pas une observation faite pour cette note.

## Analyse

1. Seul `PreToolUse` / `grimoire.tool-policy` gagne quelque chose avec `onFailure: "block"` : aujourd'hui, un plantage, un délai de 10 s dépassé ou un `grimoire-hook` introuvable laisse passer la mutation que le frein devait refuser. C'est exactement le mode de panne « garde qui échoue ouvert ».
2. `Stop` / `grimoire.evidence-gate` ne peut **pas** être mis en échec fermé par ce champ (la page l'exclut). Son plantage reste ouvert ; le seul remède est dans le hook lui-même (il doit sortir en code 2 sur toute exception interne, ce qui est un autre chantier).
3. Risque à traiter avant d'émettre : avec `block`, un `grimoire-hook` absent du PATH **bloquerait tous les appels d'outil**. L'émission doit donc être conditionnée à un lanceur résolu et vérifié (le contrôle de #760 dans `doctor`), et à une version d'hôte `>= 2.1.295`. Comment une version plus ancienne traite le champ inconnu : `non vérifié`.

## Ce que ça change pour le kit

**À faire (petit)** : émettre `"onFailure": "block"` sur l'entrée `PreToolUse` seule, quand la version d'hôte lue est `>= 2.1.295` et que le lanceur est résolu ; ne rien émettre sinon.

Tâche proposée (prête, S) : « Hooks gate en échec fermé : `onFailure: block` sur PreToolUse », module `src/grimoire/hosts/emitters/claude_code.py` (`_hook_entry`), lecture de version par `src/grimoire/hosts/host_version.py`. Critères testables (`--check`) :

- test rouge avant / vert après : l'entrée `PreToolUse` émise porte `onFailure: "block"` pour une version d'hôte `2.1.295`, et ne le porte pas pour `2.1.294` ni sans version lisible ;
- test : aucune autre entrée (`Stop`, `SubagentStop`, `PostToolUse`…) ne porte le champ ;
- test : lanceur non résolu (`grimoire-hook` absent) → pas de `onFailure` et un avertissement `doctor`.

**Abandonnés, avec leur raison** : (a) `Stop` en échec fermé par `onFailure` : sans effet selon la référence ; (b) consommer `TaskCreated`/`TaskCompleted` : ils portent sur l'outil `TaskCreate` de l'hôte, le kit n'y a aucune décision à prendre, rouvrir sur besoin nommé ; (c) `PreModelSwitch` pour appliquer le routage de modèles : aucun incident ne le demande ; (d) `InstructionsLoaded` : utile comme sonde de mesure (quels `CLAUDE.md` entrent dans le contexte), pas comme gate ; à rouvrir avec la tâche de mesure d'`omitClaudeMd` (voir [la note sur les sous-agents](sous-agents-omitclaudemd-2026-10.md)).

## Sources consultées le 2026-10-25

- [Hooks reference](https://code.claude.com/docs/en/hooks) — Claude Code Docs `[doc]`
