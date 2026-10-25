---
audience: décision
---

<p align="right"><a href="../../README.md">README</a> · <a href="../../CHANGELOG.md">Changelog</a> · <a href="../index.md">Docs</a></p>

# <img src="../assets/icons/search.svg" width="32" height="32" alt=""> Canal plugin Claude Code et oracles `plugin validate` / `plugin eval` (2026-10)

> Tâche `GAO-veille-cana-001`. Sources ouvertes le 2026-10-25. `claude` n'a pas
> été lancé : aucune sortie de `claude plugin validate` n'a été observée, tout
> ce qui suit sur son comportement vient de la documentation.

## Pourquoi #177 est fermée

L'issue [#177](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/177) proposait un canal plugin (`grimoire host plugin build`, `.claude-plugin/`, `marketplace.json`). Elle a été fermée le 2026-09-13 par la PR [#466](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/466) (`d95e38798`, « déclarer les hôtes activés »), dont le corps dit : « Version "petite" décidée par Guilhem le 2026-09-12 : le canal plugin […] décrit dans l'issue est **écarté par décision** — pas de second mécanisme d'installation à documenter/tester/maintenir. » La solution retenue est `hosts.enabled` dans `project-context.yaml` : `grimoire host sync` n'écrit que les hôtes déclarés. L'issue n'a donc pas été réalisée : elle a été tranchée par un contournement. Vérifié sur ce worktree : `git ls-tree -r --name-only HEAD | grep -c "claude-plugin\|marketplace.json"` donne 0.

## Ce que dit la référence officielle

- [Plugin commands reference](https://code.claude.com/docs/en/plugins/cli-reference) `[doc]` (lue le 2026-10-25) : `claude plugin validate <path> [--strict] [--json]`. `--json` : « Requires Claude Code v2.1.259 or later ». Codes de sortie : `0` validé (avec ou sans avertissements ; avec `--strict`, sans avertissement), `1` échec, `2` erreur inattendue du validateur (rien sur stdout dans ce cas). Rapport JSON : `success`, `strict`, `target`, `manifest`, `contents` (un résultat par fichier avec `errors`, `warnings`, `notes`).
- Portée : un manifeste de plugin, un manifeste de marketplace, ou, sans manifeste, les fichiers de composants d'un dossier (« Validating component files without a manifest requires Claude Code v2.1.233 or later ») : un dossier `skills`, `agents` ou `commands`, un dossier `.claude`, ou tout autre dossier dont on prend le `.claude` interne. Les liens symboliques ne sont pas suivis. Un `SKILL.md` à la racine d'un plugin n'est pas contrôlé.
- [Plugin manifest reference](https://code.claude.com/docs/en/plugins-reference) `[doc]` : « `claude plugin validate` is the authoritative check for a manifest » ; contrôle aussi les serveurs MCP déclarés (à partir de `2.1.281`). Les règles précises appliquées à un `SKILL.md` (longueur de `name`, `description`) : `non vérifié`, la page de dépannage n'a pas été lue.
- [Test plugins with evals](https://code.claude.com/docs/en/plugin-evals) `[doc]` : `claude plugin eval` exige `2.1.269`, lance chaque cas plusieurs fois (3 par défaut) avec et sans le plugin, et « every eval run and every judge grader is a real model call on your account, counted against your plan's usage or your API bill ». `--max-cost-usd` borne la dépense (sortie 2 au plafond), `--threshold` fixe le seuil (sortie 1 en dessous). Les cas sont du format propre à cet outil, que l'outil `skill-creator` ne lit pas.
- Le champ `experimental.evals` du manifeste indique que le format des évals est lui-même noté expérimental sur la page de référence.

## État dans le kit

- Aucun `claude plugin validate` ni `plugin eval` n'est appelé : `git grep -n "plugin validate\|plugin eval" -- src docs framework` ne trouve rien.
- Le contrôle de #874 (fermée le 2026-10-24, `cb586343c`, PR #884) est un **oracle maison** : `src/grimoire/hosts/skill_spec.py` (`validate_skill_text`, `validate_skill_file`) applique les règles de la spec Agent Skills (`name`, `description` 1 à 1024, `compatibility`, corps de moins de 500 lignes, références à un niveau) ; `src/grimoire/cli/doctor_skills.py:15` en fait un avertissement `doctor`, jamais un échec.
- Les compagnons de `plan 2026-Q4` 5.5 (« `skills-ref validate` + eval par skill ») restent ouverts pour la partie évals.

## Analyse

- `validate --json` est un **second oracle**, d'un autre type : il dit ce que *Claude Code* accepte ou tolère, là où `skill_spec.py` dit ce que *la spec* demande. Un désaccord entre les deux est une information (l'hôte refuse ce que la spec admet, ou l'inverse), c'est son intérêt.
- Limites : il ne couvre que Claude Code (les skills Copilot restent seulement sous `skill_spec.py`) ; il suppose le binaire `claude` installé, donc pas dans la CI du kit par défaut (`non vérifié` : l'image CI n'a pas été inspectée) ; il valide un dossier `.claude` ou un plugin, alors que le kit n'émet pas de plugin ; s'il appelle le modèle : la page ne le dit pas pour `validate`, elle le dit explicitement pour `eval` seul.
- `plugin eval` coûte du quota à chaque exécution (cas × runs × 2 bras) : même arbitrage que les bancs que Guilhem demande d'éviter ; pas pour le kit hors demande.

## Ce que ça change pour le kit

**Canal plugin** : rien. La décision du 2026-09-12 tient et rien dans la documentation lue ne la contredit ; rouvrir seulement si le coût de `host sync` devient un incident utilisateur nommé.

**`plugin validate --json` comme oracle de #874** : oui, en test optionnel. Tâche proposée (prête, S) : « Confronter les skills émis à `claude plugin validate --json` », modules `src/grimoire/hosts/skill_spec.py`, test sous `tests/unit/hosts/`. Critères (`--check`) :

- le test est marqué et ignoré (`skip`) si le binaire `claude` est absent du PATH ; il n'appelle jamais le modèle ;
- il émet les skills d'un projet cobaye et lance `claude plugin validate <dossier>/.claude --json` ; il exige `success: true` sur l'arbre réel du kit ;
- pour un skill volontairement hors spec (`description` de 1025 caractères), il consigne l'écart éventuel entre le verdict de `validate` et celui de `skill_spec.py` (le test échoue sur désaccord non listé).

`plugin eval` : abandonné pour le kit, raison : coût de quota par exécution et format non lu par les autres outils.

## Sources consultées le 2026-10-25

- [Plugin commands reference](https://code.claude.com/docs/en/plugins/cli-reference) — Claude Code Docs `[doc]`
- [Plugin manifest reference](https://code.claude.com/docs/en/plugins-reference) — Claude Code Docs `[doc]`
- [Test plugins with evals](https://code.claude.com/docs/en/plugin-evals) — Claude Code Docs `[doc]`
- Issues [#177](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/177), [#874](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/874) et PR [#466](https://github.com/Guilhem-Bonnet/Grimoire-kit/pull/466) — dépôt du kit
