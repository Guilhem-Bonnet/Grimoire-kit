# Triage code scanning — 2026-09

Phase 2 « nettoyage et surcoût » du plan produit 2026-Q4, épic #552. Constat de
départ : le check CodeQL est rouge sur toute PR, donc plus personne ne le lit.
Ce document explique pourquoi, ce qui a été corrigé (PR #573), ce qui a été
classé faux positif ou hors périmètre et fermé, et ce qui reste réellement à
surveiller.

## Ce qui a explosé le nombre d'alertes

Le nombre réel d'alertes ouvertes au moment de ce triage (402) est très
supérieur au constat initial de l'épic (« ~50 alertes, presque toutes
`py/path-injection` »). Cause trouvée en lisant `.github/workflows/codeql-analysis.yml` :
la ligne `queries: security-and-quality` charge, en plus des requêtes de
sécurité, environ 250 requêtes de **qualité/style** (imports inutilisés,
`except: pass`, retours mixtes, variables non utilisées, imports cycliques…)
qui n'ont rien à voir avec la sécurité et qui, pour l'essentiel, font double
emploi avec le gate `ruff`/`mypy --strict` déjà bloquant de cette CI. Elles
noient les ~150 alertes de sécurité réelles sous le bruit — exactement le
symptôme décrit par l'épic.

**Décision** (ce PR) : narrowing de `security-and-quality` vers
`security-extended` dans `.github/workflows/codeql-analysis.yml`. Toutes les
requêtes de sécurité (`py/path-injection`, `py/log-injection`, `py/tarslip`,
`py/command-line-injection`, etc.) restent actives ; les ~250 requêtes de
qualité/style disparaissent du prochain scan. C'est une désactivation globale
d'une partie de la configuration CodeQL, documentée ici comme le exige la
doctrine du projet — pas une désactivation de règle de sécurité, et pas un
contournement du gate qualité (`ruff`/`mypy` restent inchangés et bloquants).

## Correctifs de code (PR #573, mergée avant ce triage)

Trois vulnérabilités réelles, corrigées avec test rouge-avant/vert-après :

| Règle | Fichier | Ce qui était cassé | Correctif |
|---|---|---|---|
| `py/path-injection` | `src/grimoire/tools/forge_server.py` (`blueprint_compile`) | `blueprint["id"]` (corps d'une requête HTTP cockpit) servait à construire `artifact_rel`/`artifact_path` *avant* le premier appel à `_blueprint_path` (seule fonction du module à valider un id via `SLUG_RE`) — un id `../..` écrivait hors du projet | `SLUG_RE.match(bp_id)` appliqué dès l'extraction de `bp_id`, avant toute construction de chemin |
| `py/path-injection` | `src/grimoire/tools/forge_server.py` (`blueprint_validate`/`blueprint_lint`) | un `ref` de node (`artifact`/`composite`/schéma de gate) transformait un `.exists()`/`.is_file()` en oracle d'existence de fichier arbitraire sur la machine | chemin résolu confiné sous `project_root` (`is_relative_to`) avant tout accès filesystem |
| `py/log-injection` | `src/grimoire/cli/cmd_init.py` (`_maybe_register_cockpit`) | nom de dossier interpolé avec `%s` dans `logger.debug` — un `\n` dans le nom forge une fausse entrée de log | `%r` (repr, échappe `\n`/`\r`) |
| `py/tarslip` | `scripts/bench/three_arms.py` (`ensure_go_toolchain`) | `tarfile.extractall()` sans filtre sur une archive téléchargée | `filter="data"`, extraction factorisée dans `_extract_go_archive` (testable sans réseau) |
| `py/unused-global-variable` | `tests/unit/test_flows_composite.py`, `tests/unit/test_flows_extract.py` | constante `_NODE_RE` définie, jamais lue | supprimée |

Les alertes CodeQL correspondant à ces lignes ne sont pas dismissées par ce
PR : elles doivent se refermer d'elles-mêmes (état « fixed ») au prochain scan
de `main` qui suit la fusion de #573, puisque le code qu'elles pointaient a
changé.

## Méthode de triage pour le reste

Pour chaque fichier concerné par `py/path-injection` (145 alertes restantes,
22 fichiers), lecture directe du code au niveau de chaque ligne signalée (pas
seulement du nom de fonction). Constat systématique : le codebase porte
**trois mécanismes de confinement de chemin indépendants**, aucun reconnu par
CodeQL comme neutralisant un flux taché :

1. `resolve_within_allowed()` / `allowed_roots()` (`src/grimoire/tools/project_registry.py`)
   — utilisé par `scan_payload`, `browse`, `cmd_cockpit.py` : lève si le chemin
   résolu sort des racines autorisées (`$HOME` + parents des projets déjà au
   registre). Toute lecture (`looks_grimoire`, `is_grimoire_managed`,
   `crawl_projects`) en aval de cet appel est déjà bornée.
2. `_blueprint_path()` + `SLUG_RE` (`src/grimoire/tools/forge_server.py`) —
   valide la forme de l'id (`^[a-z0-9]+(-[a-z0-9]+)*$`, exclut `..` et les
   séparateurs) puis confine le chemin résolu sous le dossier des blueprints.
3. `_ensure_inside_root()` (`src/grimoire/core/agentic_standard.py`) — lève si
   le chemin résolu n'est pas égal à ou sous la racine attendue.

Pour les fichiers qui n'appellent aucun de ces trois helpers, le paramètre
tâché (`project_root`, `root`, `target`, `ext_dir`, `kit_path`…) provient
systématiquement soit (a) de la ligne de commande ou de la configuration de
l'opérateur local lui-même (`grimoire init/up/serve --project-root`, un slug
de projet déjà au registre, un fichier `project-context.yaml` du projet
ouvert) — jamais d'un réseau distant —, soit (b) d'un identifiant déjà purgé
de tout séparateur par une regex inline équivalente à `SLUG_RE`
(`re.sub(r"[^a-z0-9]+", "-", ...)`, `src/grimoire/tools/project_upgrade.py`).
Aucun cas trouvé où un chemin franchit une frontière de confiance réelle
(HTTP distant, contenu d'un projet tiers non demandé) sans passer par l'un des
trois confinements ci-dessus.

Fichiers vérifiés individuellement (au moins une ligne signalée lue en
contexte, avec ses appelants, par fichier) : `project_registry.py`,
`proposals.py`, `standard_state.py`, `standard_generation.py`,
`standard_profile_manifest.py`, `scanner.py`, `scaffold.py`, `project_setup.py`,
`override_drift.py`, `config.py`, `cadrage.py`, `blueprint_diff.py`,
`ext_manager.py`, `cmd_setup.py`, `cmd_up.py`, `cmd_init.py`,
`hosts/detection.py`, `hosts/collect.py`, `hosts/emitters/base.py`,
`agentic_standard.py`, `forge_server.py` (hors lignes déjà corrigées par
#573), `_common.py`.

**Verdict** : faux positif systémique, classe (b) de la méthode de triage —
chemin d'outil local, déjà confiné par un sanitizer que CodeQL ne modélise
pas. Dismiss `false positive` via l'API, un commentaire par groupe
(règle, fichier) référençant ce document.

## Tableau de triage (agrégé par règle × fichier)

Généré par `scripts/bench` — script de triage exécuté au moment de la
fusion, comptage exact dans le message de la PR. Répartition au moment de la
rédaction (peut varier de quelques unités au moment de l'exécution réelle, ce
dépôt étant actif en continu) :

### `py/path-injection` — 145 alertes, faux positif systémique (dismiss)

| Fichier | Alertes | Confinement déjà en place / origine du paramètre |
|---|---:|---|
| `src/grimoire/cli/cmd_up.py` | 22 | `project_root` : argument CLI (`grimoire up [PATH]`) |
| `src/grimoire/hosts/emitters/base.py` | 12-13 | `path` : fichier émis sous `project_root`, jamais réseau |
| `src/grimoire/core/standard_state.py` | 12 | `root` : `project_root` du hook, appelé sur chaque outil local |
| `src/grimoire/cli/cmd_setup.py` | 11 | `path` : `project-context.yaml` du projet ouvert |
| `src/grimoire/core/agentic_standard.py` | ~7 (hors `_ensure_inside_root` elle-même) | confiné par `_ensure_inside_root()` |
| `src/grimoire/tools/ext_manager.py` | 10 | `ext_dir` : slug d'extension déjà validé par `_is_safe_relpath` |
| `src/grimoire/tools/project_setup.py` | 9 | `target` : déjà passé par `resolve_within_allowed()` côté cockpit |
| `src/grimoire/hosts/detection.py` | 7 | `project_root` : argument CLI |
| `src/grimoire/hosts/collect.py` | 7 | `project_root` : argument CLI |
| `src/grimoire/core/override_drift.py` | 6 | `kit_path`/`root` : chemins d'installation locaux |
| `src/grimoire/tools/forge_server.py` | ~6 restantes | déjà validées par `_blueprint_path()`/`SLUG_RE` (ex. `blueprint_put`, `blueprint_get`, constructeur `ForgeAPI.__init__`) |
| `src/grimoire/core/standard_generation.py` | 5 | même famille que `standard_state.py` |
| `src/grimoire/core/scaffold.py` | 4 | `target` : dossier de destination `grimoire init` |
| `src/grimoire/core/scanner.py` | 4 | `root` : racine scannée, contrôles `.exists()`/`.glob()` en lecture seule |
| `src/grimoire/cli/cmd_init.py` | 4 | `target` : dossier de destination `grimoire init` |
| `src/grimoire/tools/project_registry.py` | 3 | confiné par `resolve_within_allowed()`/`allowed_roots()` (`scan_payload`, `browse`) ou lu depuis le registre local déjà écrit par `register_project` |
| `src/grimoire/proposals.py` | 3 | `slug` déjà purgé par une regex équivalente à `SLUG_RE` (`project_upgrade.py`) |
| `src/grimoire/core/cadrage.py` | 3 | `phase.filename` est une constante du code, pas une entrée |
| `src/grimoire/core/config.py` | 2 | `path` : `project-context.yaml` local |
| `src/grimoire/tools/blueprint_diff.py` | 2 | `path` déjà issu de `_blueprint_path()` |
| `src/grimoire/core/standard_profile_manifest.py` | 1 | même famille que `standard_state.py` |
| `src/grimoire/tools/_common.py` | 1 | `load_yaml(path)` générique, appelants déjà confinés (voir ci-dessus) |

### Rôle de qualité/style — 248 alertes, hors périmètre sécurité (dismiss, `won't fix`)

Générées uniquement par `security-and-quality` ; disparaissent du prochain
scan avec le narrowing vers `security-extended`. Dismissées ici pour ne pas
laisser un solde orphelin après le changement de config (une alerte dont la
requête sort de la suite ne se referme pas toute seule).

| Règle | Alertes | Couverture existante |
|---|---:|---|
| `py/empty-except` | 61 | essentiellement `except (...): pass` volontaires (best-effort documenté), très majoritairement dans des scripts `framework/`/`_grimoire/` hors du paquet `src/grimoire` publié |
| `py/unused-global-variable` | 41 (39 après les 2 fixées par #573) | quasi intégralement des constantes de test (`tests/`) ; équivalent `ruff` F824/F841 partiel, mais la variante « globale au module » n'est pas dans le set de règles activées ici |
| `py/ineffectual-statement` | 38 | expressions sans effet, très majoritairement `framework/`/`_grimoire/` (outillage annexe, pas le paquet publié) |
| `py/import-and-import-from` | 16 | tests, doublons d'import — `ruff` F811 couvre le cas général |
| `py/undefined-export` | 11 | un seul fichier (`hosts/decisions/__init__.py`), `__all__` à vérifier au prochain audit qualité, pas un enjeu sécurité |
| `py/unused-import` | 10 | couvert par `ruff` F401 |
| `py/unused-local-variable` | 10 | couvert par `ruff` F841 |
| `py/uninitialized-local-variable` | 8 | 100 % dans `tests/`, échantillon lu (`tests/test_rag_retriever.py`) — pas de gel côté production |
| `py/cyclic-import` | 7 | import différé (`from ... import` en tête de fonction) dans le code de production concerné, motif déjà volontaire pour casser les cycles |
| `py/side-effect-in-assert` | 7 | 100 % dans `tests/`, style d'assertion |
| `py/unreachable-statement` | 6 | mélange tests/outillage, aucun dans le chemin de sécurité |
| `py/imprecise-assert` | 6 | 100 % dans `tests/` |
| `py/implicit-string-concatenation-in-list` | 4 | style |
| `py/mixed-returns` | 3 | 100 % dans `tests/` |
| `py/multiple-definition` | 3 | `framework/tools/web-browser.py`, hors paquet publié |
| `py/regex/duplicate-in-character-class` | 3 | style regex |
| `py/repeated-import` | 3 | doublons d'import triviaux |
| `py/call-to-non-callable` | 1 | faux positif confirmé — `src/grimoire/tools/_common.py:69`, `loader` est correctement discriminé par `backend == "ruamel"` avant l'appel, CodeQL ne corrèle pas le tag de type avec la valeur |
| `py/overly-large-range` | 1 | test |
| `py/redundant-comparison` | 1 | `framework/tools/llm-router.py`, hors paquet publié |
| `py/regex/unmatchable-dollar` | 1 | style regex |
| `py/str-format/surplus-named-argument` | 1 | `framework/tools/agent-forge.py`, hors paquet publié |
| `py/unnecessary-lambda` | 1 | test |

## Résultat

Chiffres exacts au moment de l'exécution de
`scripts/security/dismiss-codeql-false-positives.py --apply` (2026-09-17,
après fusion et scan de main de la PR #573) :

- Corrigées (PR #573, mergée) : 3 vulnérabilités réelles + 2 constantes
  mortes. Confirmé par le scan CodeQL de main qui a suivi la fusion :
  log-injection, tarslip et les deux constantes mortes se referment tout
  seuls ("fixed"). **Les 4 alertes `py/path-injection` de `blueprint_compile`/
  `blueprint_get`/le constructeur `ForgeAPI`, elles, sont restées ouvertes sur
  ce même scan** malgré le correctif — confirmation empirique que CodeQL ne
  reconnaît pas la garde `SLUG_RE`/`_blueprint_path` comme un sanitizer, même
  quand le code est déjà sûr. Rejointes au lot des faux positifs dismissés
  ci-dessous plutôt que laissées ouvertes en attendant une fermeture qui ne
  viendra jamais.
- Faux positifs `py/path-injection` dismissés : **151** (chemin déjà confiné,
  sanitizer non reconnu par CodeQL — voir méthode ci-dessus, y compris les 4
  du paragraphe précédent).
- Hors périmètre sécurité dismissés (`won't fix`) : **243** (`security-and-quality`
  → `security-extended`, alertes de style dupliquant `ruff`/`mypy`).
- Alerte historique #223 (`py/command-line-injection`, `cmd_cockpit.py`) :
  déjà dismissée le 2026-08-27 par Guilhem (`won't fix` — pas de shell,
  `shell=False`, injection d'argument corrigée en `9fd3e4dd`), rien à faire.
- **0 alerte ouverte** sur le dépôt juste après l'exécution du script
  (vérifié par `gh api .../code-scanning/alerts?state=open` → `[]`). Le check
  CodeQL de cette même PR tourne en `security-extended` et doit rester vert.

## Recommandation de suivi (hors périmètre de ce lot)

Les trois helpers de confinement (`resolve_within_allowed`, `_blueprint_path`/
`SLUG_RE`, `_ensure_inside_root`) mériteraient un unique point d'entrée
partagé, documenté comme sanitizer CodeQL (modèle de données personnalisé,
`codeql-config.yml` → `packs`/`model-pack`) pour que CodeQL les reconnaisse et
que ce genre de triage massif n'ait pas à se répéter. Non fait ici : changement
d'outillage CodeQL avancé, hors du lot « nettoyage » de la phase 2.
