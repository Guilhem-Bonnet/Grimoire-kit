# Résorption de `grimoire-init.sh` — inventaire et plan

Chantier Phase 2 du plan retrieval/architecture (2026-07-09).
Objectif : converger sur le CLI Python `grimoire` comme surface unique,
réduire `grimoire-init.sh` (environ 3 800 lignes) à un bootstrap mince,
supprimer la double maintenance bash/Python.

## Constat

`grimoire-init.sh` porte 28 sous-commandes. Trois familles :

1. **Wrappers minces** vers `framework/tools/*.py` (parsing d'arguments
   bash puis exec python) — la logique est déjà en Python.
2. **Flux couverts** par le CLI Python (`grimoire init`, `status`,
   `doctor`, `upgrade`, `validate`…).
3. **Gaps réels** — logique implémentée uniquement en bash.

## Inventaire

| Sous-commande bash | Rôle | Équivalent Python | Verdict |
| --- | --- | --- | --- |
| init (flux principal `--name/--user/--archetype/--auto/--memory`) | Installer le framework | `grimoire init` (wizard, `-a`, `-b`, `--yes`, `--dry-run`) | Couvert |
| `install` (archétypes, `--list/--inspect/--force`) | Archétypes dans projet existant | `grimoire init -a`, `grimoire ext` | Couvert — vérifier parité `--inspect` |
| `status` | Dashboard projet | `grimoire status` | Couvert |
| `doctor` | Diagnostic | `grimoire doctor` | Couvert |
| `upgrade` / `quick-update` | Mise à jour framework | `grimoire upgrade`, `grimoire self update` | Couvert |
| `validate` | Validation DNA/config | `grimoire validate` (schéma project-context) | Partiel — vérifier le périmètre DNA archétypes |
| `bench`, `forge`, `guard`, `evolve`, `memorylint`, `nso`, `schemav`, `autodoc`, `dream`, `consensus`, `antifragile`, `reasoning`, `migrate`, `darwinism` | Wrappers `framework/tools/*.py` | Les tools python restent invocables directement ; `stigmergy` et `debugger` ont déjà une sous-commande Python | Wrapper mince — remplacer par exécution directe ou sous-commandes `grimoire tools <nom>` |
| `stigmergy` | Wrapper stigmergy | `grimoire stigmergy` | Couvert |
| `trace` | Lire/filtrer `GRIMOIRE_TRACE.md` | — | Gap mineur (lecture de fichier) |
| `changelog` | Générer CHANGELOG depuis la trace | — | Gap mineur |
| `session-branch` | Branches de session d'artefacts (`.runs`) | — | Gap — usage à confirmer avant port |
| `resume` | Reprise de checkpoint (`.runs`) | — | Gap — usage à confirmer avant port |
| `hooks --install/--list/--status` | Installer les git hooks (pre-commit CC, pre-push) | `grimoire standard hooks` (verify/simulate seulement) | **Gap principal** — l'installation des hooks n'existe pas en Python |
| `reset` / `uninstall` | Désinstallation propre | `grimoire repair` partiel | Gap |

## Plan de résorption (ordre)

1. **`grimoire hooks install`** — porter l'installation des git hooks en
   Python (gap principal, c'est lui qui rend `grimoire-init.sh`
   obligatoire aujourd'hui). Corriger au passage les deux bugs de layout
   constatés : le hook pre-push cherche `quick-check.sh` sous
   `$GIT_ROOT/grimoire-kit/` (introuvable depuis le repo kit lui-même) et
   le pre-commit CC résout `pytest` via le PATH système au lieu du venv
   projet.
2. **`grimoire tools <nom>`** — sous-commande générique exécutant les
   `framework/tools/*.py`, supprimant d'un coup les 14 wrappers bash.
3. **`trace` / `changelog`** — port trivial (lecture/agrégation de
   `GRIMOIRE_TRACE.md`).
4. **`reset` / `uninstall`** — port avec confirmation et `--dry-run`.
5. **`session-branch` / `resume`** — décision d'usage (conserver ou
   déprécier) avant port ; ne pas porter à l'aveugle.
6. **Bootstrap mince** — réduire `grimoire-init.sh` à : détection de
   Python/uv, installation de grimoire-kit (uv tool install ou pipx),
   exec `grimoire init "$@"`. Bandeau de dépréciation pendant un cycle de
   release, puis suppression.

## Contraintes

- Les campagnes d'evals pinnent des commits antérieurs : la résorption ne
  casse aucun pin (les runs archivent le témoin au commit pinné).
- `forge_server.py` référence `grimoire-init.sh` — à migrer avec l'étape 2.
- Le pre-push hook `validate --all` dépend du script — à migrer avec
  l'étape 1.
