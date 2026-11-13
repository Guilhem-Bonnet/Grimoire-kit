---
audience: décision
---

# Gain mesuré des crates Rust, crate par crate — 2026-11

Mesuré le 2026-11-13 (UTC) sur `main` à `faa33529`, grimoire-kit 3.84.0.
Script : `scripts/bench/rust_cores_gain.py`. Ce rapport mesure ; il ne
tranche pas. Aucun crate ni aucun module du produit n'a été modifié.

## Question

`rust/` porte sept crates, 11 535 lignes de Rust dans leurs `src/`. Chacune
double un module Python qui reste l'implémentation livrée. Que rapporte chaque
doublure, que coûte-t-elle, et lesquelles garder ?

## Réponse courte

1. **Un seul crate va plus vite que le Python qu'il double à l'échelle d'une
   commande : `hosts`.** Lire le frontmatter des 15 fiches d'agent livrées
   prend 32,4 ms en Python et 0,94 ms en Rust (34×) ; `build_surface` passe de
   37,7 ms à 13,3 ms (2,8×). De bout en bout, `grimoire host sync` gagne
   27 ms en médiane sur 413 ms, à la limite de ce que la dispersion de la
   machine laisse lire.
2. **`dispatch` va plus vite, en dixièmes de milliseconde.** Analyser dix
   sorties d'ouvrier : 0,25 ms contre 0,09 ms. Le routage des fournisseurs,
   lui, est indiscernable.
3. **Quatre crates sont plus lents que Python là où ils ont été mesurés.**
   `policies` (0,53× à 0,59× sur le moteur du hook), `schema`
   (`generate_schema` : 0,13×), `flows` (contrôle de contrat : 0,53×), `acp`
   (0,27×). La frontière PyO3 coûte plus que la boucle qu'elle remplace.
4. **`traces` est indiscernable** (0,97×) : les deux tiers du temps sont la
   lecture du journal, faite en Python sur les deux chemins.
5. **Le hook `PreToolUse` ne voit rien.** Il charge deux extensions et y entre
   une fois (`evaluate`) ; avec ou sans extension, l'écart est indiscernable
   (−2,7 ms en médiane sur 171 ms, intervalle interquartile de −7,6 à
   +8,0 ms). La fonction portée pèse 12 µs dans un appel qui en consomme
   169 000.
6. **Aucun de ces chemins n'atteint un utilisateur.** Aucune roue n'est
   publiée (`CONTRIBUTING.md`, « Cœurs optionnels en Rust » ;
   `tests/unit/test_acp.py:350`) : `pip install grimoire-kit` donne le chemin
   Python. Les gains comme les surcoûts ci-dessous ne valent que pour la CI et
   pour un contributeur qui construit l'extension.
7. **Le coût se concentre sur deux crates.** Depuis leur port, 7 des 9 commits
   qui ont touché `schema.py`/`validator.py` ont dû toucher aussi le crate
   `schema` ; 4 sur 7 pour `policies`. Pour les quatre autres crates anciens,
   c'est 1 ou 2 commits sur 12 à 20.

Recommandation, détaillée plus bas : **garder** `hosts` ; **geler** `dispatch`,
`flows`, `traces`, `acp` ; **archiver** `schema` et `policies`.

## Ce qui était déjà mesuré, et ce qui ne l'était pas

Le constat de départ, « le bénéfice n'a jamais été mesuré », est vrai pour cinq
crates sur sept, pas pour les deux premiers.

- **`policies` et `schema` ont été mesurés le 2026-09-11** par
  `scripts/bench-rust-cores.py` (PR #404). Le résultat est en commentaire de
  l'issue #354 : Rust plus lent dans les cinq cas par fonction (0,12× à 0,89×),
  écarts de bout en bout dans le bruit. `docs/_archive/rust-cores-benchmark.md`
  décrit le script et, par choix, ne recopie aucun chiffre.
- **La décision qui a suivi ne portait pas sur la vitesse.** Clôture de #354,
  2026-09-12 : « garder Rust pour le retour du compilateur (défauts trouvés à
  chaque port), pas pour la vitesse ». `docs/audits/positionnement-2026-09-12.md`
  (lignes 27 et 81) consigne le même verdict.
- **`hosts`, `flows`, `dispatch`, `traces` et `acp` n'avaient aucune mesure.**
  Les descriptions des PR #412, #413, #415 et #429 n'en portent pas (recherche
  par mots-clés dans leur texte).

Ce rapport refait la mesure des deux premiers, l'étend aux cinq autres, et
ajoute ce que le banc de septembre ne regardait pas : le coût d'import, les
appels qui entrent réellement dans chaque extension pendant une commande, et
la fréquence à laquelle un changement Python oblige à réécrire le Rust.

Deux écarts avec la description habituelle des crates, relevés en lisant le
code :

- **Le contrôle d'ABI n'existe que pour `traces`, et à un seul de ses deux
  points de chargement.** `src/grimoire/traces/ledger.py:85-99` compare
  `RUST_ABI_VERSION` à la constante du crate ; `src/grimoire/proposals.py:83`
  charge la même extension sans ce contrôle. `acp` exporte `ABI_VERSION`
  (`rust/grimoire-acp-core/src/lib.rs:32`) que rien ne lit côté Python. Les
  cinq autres n'en ont pas.
- **`rust-cores.yml` compte 16 jobs**, pas 14 : `changes`, quatorze jobs par
  crate, et `rust-gate`. `CONTRIBUTING.md` annonce encore « six crates », un
  motif « pour les cinq crates » et « douze jobs » au-dessus d'un tableau de
  sept lignes.

## Méthode

- **Construction**, comme le job `parity` de `rust-cores.yml` :
  `maturin build --release --manifest-path rust/<crate>/Cargo.toml --out dist-rust`
  puis `pip install --force-reinstall dist-rust/*.whl`, pour les sept crates.
  maturin 1.16.0, PyO3 0.29 (`abi3-py312`), rustc 1.94.1. Les sept ont
  construit ; une construction à froid a pris de 15 à 19 s par crate (une
  seule mesure chacune).
- **Deux environnements** Python 3.12.13 (la version de la CI), le kit installé
  en mode éditable dans chacun : l'un avec les sept extensions, l'autre sans
  aucune.
- **Parité vérifiée avant de chronométrer.** Les six fichiers `*_rust_parity.py`
  et `tests/unit/test_acp.py`, lancés avec les sept extensions : 510 réussis,
  4 sautés (les quatre cas « extension absente », sans objet ici). Et le script
  compare lui-même les deux backends sur chaque entrée mesurée : 20 lignes
  sur 20 rendent le même résultat.
- **Par fonction** : la fonction publique Python, backend forcé par la variable
  d'environnement du crate, conversions PyO3 comprises. 30 échantillons par
  backend, alternés ; chaque échantillon est un lot d'appels d'au moins 5 ms.
  Médiane et quartiles.
- **De bout en bout** : un processus par appel, 50 répétitions par bras, bras
  alternés, écart lu répétition par répétition. Projet de banc : un
  `grimoire init -y` dans un HOME jetable. Le bras « sans extension » masque
  l'import ; une seconde paire lance les scripts console des deux
  environnements.
- **Entrées** : les fixtures du dépôt (`tests/fixtures/`), les 15 fiches
  d'agent livrées, le blueprint `web-pipeline` livré, les cas des tests de
  parité. Trois lignes utilisent une entrée fabriquée pour voir l'effet de la
  taille ; elles sont marquées et n'entrent dans aucune recommandation.
- **Lecture** : « Rust plus rapide » ou « Rust plus lent » quand les intervalles
  [Q1 ; Q3] des deux chemins ne se recouvrent pas, « indiscernable » sinon.
  Descriptif, pas un test statistique.

**Machine** : Intel Core i9-9900K (16 CPU logiques), Linux 7.3.3 (Fedora 44),
CPython 3.12.13, rustc 1.94.1. **Elle était chargée** : d'autres sessions y
tournaient, la charge moyenne valait 17,9 au départ de la passe publiée. Les
durées absolues sont donc gonflées ; `python -c pass` y coûtait 10,5 ms. Trois
passes complètes ont été faites, à des charges de 41, 18 et 18 : les lectures
sont les mêmes sur 19 lignes sur 20 (la ligne fabriquée de `validate_config` a
lu « Rust plus lent » une fois sur trois), et les rapports calculés sur le
temps CPU ne bougent presque pas d'une passe à l'autre (`parse_frontmatter` :
30,9×, 33,2×, 32,4×). Les chiffres cités sont ceux de la troisième passe.

## Synthèse par crate

| Crate | Ce qu'elle double | Parité testée | Gain mesuré | Rust (`src/`) | CI (lignes ; durée) | Recommandation |
|---|---|---|---|---:|---|---|
| `policies` | `grimoire.policies.engine`, `grimoire.policies.temporal` | oui, `tests/unit/test_policies_rust_parity.py` | Aucun : 0,53× à 0,59× (plus lent) ; hook `PreToolUse` indiscernable | 1 505 | 76 ; 1 794 s | **archiver** |
| `schema` | `grimoire.core.schema`, `grimoire.core.validator` | oui, `tests/unit/test_schema_validator_rust_parity.py` | Aucun : `generate_schema` 0,13× ; `validate_config` indiscernable ; `grimoire check` indiscernable | 2 304 | 80 ; 1 554 s | **archiver** |
| `hosts` | `grimoire.hosts.collect`, `grimoire.hosts.surface` | oui, `tests/unit/test_hosts_rust_parity.py` | Oui : frontmatter 34× ; `build_surface` 2,8× (−24 ms) ; `host sync` +27 ms en médiane | 1 381 | 80 ; 5 044 s | **garder** |
| `flows` | `grimoire.flows.engine`, `grimoire.runtime.kernel` | oui, `tests/unit/test_flows_rust_parity.py` | Aucun : contrôle de contrat 0,53× ; run complet indiscernable | 1 417 | 83 ; 3 347 s | **geler** |
| `dispatch` | `grimoire.missions.dispatch`, `grimoire.providers.routing` | oui, `tests/unit/test_dispatch_rust_parity.py` | En dixièmes de milliseconde : 2,8× et 4,1× sur les fonctions pures ; routage indiscernable | 1 630 | 90 ; 6 988 s | **geler** |
| `traces` | `grimoire.traces.ledger`, `grimoire.proposals` | oui, `tests/unit/test_traces_rust_parity.py` | Aucun : indiscernable (0,97×) | 2 467 | 91 ; 2 421 s | **geler** |
| `acp` | `grimoire.acp.machine` | oui, dans `tests/unit/test_acp.py` (pas de fichier `*_rust_parity*`) | Aucun : 0,27× ; jamais chargé par défaut | 831 | 83 ; 1 824 s | **geler** |

La colonne CI donne les lignes de `rust-cores.yml` propres au crate (ses deux
jobs) puis la durée cumulée de ces deux jobs sur les 299 exécutions du
régime actuel du workflow (voir « Ce que coûtent les sept crates »).

## Recommandations

La décision de septembre dit que la vitesse n'est pas la raison d'être de ces
crates. Une absence de gain ne suffit donc pas à recommander un retrait. La
règle appliquée ici croise deux mesures, le gain et le coût d'entretien :

- **garder** : un gain mesuré sur une entrée réelle, retrouvé dans une
  commande ;
- **archiver** : aucun gain (plus lent ou indiscernable) *et* plus de la moitié
  des commits qui ont touché le module Python depuis le port ont dû toucher
  aussi le crate ;
- **geler** : aucun gain utile, mais le crate ne coûte presque rien à porter.
  Ne plus l'étendre, garder le repli Python et le test de parité.

| Crate | Gain | Commits Python après le port, dont joints au crate | Recommandation |
|---|---|---|---|
| `hosts` | oui | 12, dont 1 | garder |
| `dispatch` | réel, en dixièmes de milliseconde | 17, dont 1 | geler |
| `flows` | aucun | 20, dont 2 | geler |
| `traces` | aucun | 19, dont 2 | geler |
| `acp` | aucun | 0 | geler |
| `schema` | aucun | 9, dont 7 | archiver |
| `policies` | aucun | 7, dont 4 | archiver |

**Ce qui renverserait ces recommandations.** La règle ne mesure pas ce qu'un
oracle évite. Sur les fenêtres de CI lues, les jobs `cargo` et `parity` n'ont
presque jamais fini en échec (voir plus bas) : c'est une absence de signal, pas
la preuve que l'oracle ne sert pas. Si le rôle d'oracle sur le moteur de
politiques, la garde que traverse chaque appel d'outil, vaut à lui seul la
double écriture, `policies` passe de « archiver » à « geler ». Le seuil de la
moitié repose aussi sur de petits effectifs (7 et 9 commits).

## Détail par crate

Les durées sont en microsecondes par appel, médiane de 30 échantillons ; le
rapport est Python divisé par Rust (au-dessus de 1, Rust est plus rapide).

### `grimoire-policies-core`

- **Ce qu'elle fait** : la boucle de correspondance du moteur de règles
  (`evaluate`), les règles temporelles de session (`evaluate_temporal` : budget,
  approbation, refroidissement) et `matching_approval_rule_ids`. 3 fonctions
  exportées.
- **Chargée** : `src/grimoire/policies/engine.py:51` ; appelée en
  `engine.py:218`, `temporal.py:473` et `temporal.py:722`. Backend `auto` : Rust
  dès qu'il est importable (`GRIMOIRE_POLICIES_BACKEND`).
- **Parité** : `tests/unit/test_policies_rust_parity.py` (578 lignes), job
  `rust-policies / parity`.
- **Mesure** :

| Fonction | Entrée | Python | Rust | Rapport | Lecture |
|---|---|---:|---:|---:|---|
| `PolicyEngine.evaluate`, lecture seule | moteur du hook, 5 règles | 12,28 | 23,24 | 0,53× | Rust plus lent |
| `PolicyEngine.evaluate`, destructif refusé | moteur du hook, 5 règles | 13,51 | 25,13 | 0,54× | Rust plus lent |
| `evaluate_temporal` | 3 règles temporelles | 27,57 | 46,96 | 0,59× | Rust plus lent |
| `PolicyEngine.evaluate`, destructif refusé | 105 règles, **fabriquée** | 31,60 | 183,28 | 0,17× | Rust plus lent |

- **De bout en bout** : c'est le crate du point chaud. Le hook `PreToolUse` y
  entre une fois par appel d'outil ; l'écart est indiscernable (voir « Le hook,
  de bout en bout »). Le rapport de septembre (0,54×) est retrouvé à
  l'identique.
- **Coût** : 1 505 lignes de Rust ; 4 des 7 commits qui ont touché `engine.py`
  ou `temporal.py` après le port ont touché le crate (règles temporelles,
  clé d'outil, budget de session, exemption).
- **Recommandation : archiver.** Plus lent sur le chemin le plus fréquenté du
  produit, et plus de la moitié des changements du module se paient deux fois.
  Le docstring du test de parité ne documente aucun défaut Python trouvé par ce
  port. Réserve : c'est la garde de sécurité, voir « Ce qui renverserait ces
  recommandations ».

### `grimoire-schema-core`

- **Ce qu'elle fait** : le schéma JSON de `project-context.yaml`
  (`generate_schema`) et la validation d'une configuration (`validate_config`).
  2 fonctions exportées.
- **Chargée** : `src/grimoire/core/schema.py:39` (appel en `schema.py:94`) et
  `src/grimoire/core/validator.py:63` (appel en `validator.py:292`). Backend
  `auto` (`GRIMOIRE_SCHEMA_BACKEND`).
- **Parité** : `tests/unit/test_schema_validator_rust_parity.py` (333 lignes),
  job `rust-schema / parity`.
- **Mesure** :

| Fonction | Entrée | Python | Rust | Rapport | Lecture |
|---|---|---:|---:|---:|---|
| `generate_schema` | aucune (schéma fixe) | 13,97 | 110,51 | 0,13× | Rust plus lent |
| `validate_config` | `project-context.yaml` d'un `grimoire init`, 6 sections, 788 octets | 11,86 | 10,86 | 1,09× | indiscernable |
| `validate_config` | 192 sections, **fabriquée** | 3 866,76 | 4 215,39 | 0,92× | indiscernable |

- **De bout en bout** : `grimoire check` entre une fois dans `validate_config` ;
  écart indiscernable (−8,6 ms en médiane sur 326 ms, intervalle de −29,0 à
  +18,4 ms).
- **Coût** : 2 304 lignes, le deuxième plus gros crate, et le plus retouché
  (9 commits). 7 des 9 commits qui ont touché `schema.py` ou `validator.py`
  après le port ont touché le crate : chaque clé ajoutée à
  `project-context.yaml` (`proposals`, `source.assist`, `hosts.enabled`…)
  s'écrit deux fois.
- **Recommandation : archiver.** Aucun gain, et c'est le crate où la double
  écriture est la règle plutôt que l'exception. Le port a trouvé de vrais
  défauts (plantage sur un champ énuméré recevant une liste, champs jamais
  typés, corrigés par #410) ; ces cas restent couverts par les tests du backend
  Python.

### `grimoire-hosts-core`

- **Ce qu'elle fait** : lecture du frontmatter YAML des fiches d'agent, verbes
  d'outils, inférence d'outils, `max_turns`, affinité de modèle, empreinte de
  faisceau, garde de distinction. 10 fonctions exportées.
- **Chargée** : `src/grimoire/hosts/collect.py:46` et
  `src/grimoire/hosts/surface.py:70`. Backend `auto` (`GRIMOIRE_HOSTS_BACKEND`).
- **Parité** : `tests/unit/test_hosts_rust_parity.py` (538 lignes), dont le
  rejeu des fiches livrées ; job `rust-hosts / parity`.
- **Mesure** :

| Fonction | Entrée | Python | Rust | Rapport | Lecture |
|---|---|---:|---:|---:|---|
| `parse_frontmatter` sur chaque fiche | 15 fiches livrées, 185 308 octets | 32 401,12 | 943,14 | 34,35× | Rust plus rapide |
| Dérivation complète d'un `AgentSpec` | les mêmes 15 fiches | 25 217,36 | 1 227,97 | 20,54× | Rust plus rapide |
| `build_surface`, lecture disque comprise | projet `grimoire init` : 4 agents, 8 skills | 37 688,23 | 13 298,33 | 2,83× | Rust plus rapide |

- **D'où vient le gain** : de l'analyse YAML. Le chemin Python instancie un
  `YAML(typ="safe")` de ruamel à chaque appel (`collect.py:121-124`, `:143`) ;
  ruamel.yaml 0.19.1, la version du `uv.lock`, tourne ici sans chargeur C. Le
  crate analyse avec `yaml-rust2`.
- **De bout en bout** : `grimoire host sync --host claude --dry-run` entre
  61 fois dans l'extension (22 `parse_frontmatter`) et gagne 26,9 ms en médiane
  sur 413 ms (intervalle de −2,5 à +50,6 ms : indiscernable à l'horloge
  murale ; +28,2 ms au temps CPU, intervalle de +1,8 à +51,7 ms : « plus rapide
  avec »). La paire des scripts console donne +19,9 ms, intervalle chevauchant
  zéro. Le hook `SessionStart` y entre 40 fois : +31,5 ms en médiane sur
  471 ms, intervalle de −1,6 à +67,9 ms. L'ordre de grandeur recoupe le gain
  mesuré en processus sur `build_surface` (24 ms).
- **Coût** : 1 381 lignes ; 1 seul des 12 commits qui ont touché `collect.py`
  ou `surface.py` après le port a touché le crate.
- **Recommandation : garder.** C'est le seul crate dont le retrait ferait
  perdre quelque chose de mesuré, et l'un des moins chers à porter. Deux
  limites : le gain n'est livré à personne tant qu'aucune roue n'est publiée,
  et il vaut 6 à 7 % d'une commande de 0,4 s sur un projet de 4 agents. Ce
  qu'une correction du chemin YAML côté Python rapporterait n'a pas été mesuré.

### `grimoire-flows-core`

- **Ce qu'elle fait** : la table de transitions des statuts de workflow, le
  contrôle d'une sortie contre le contrat d'un node, le node courant, la
  décision de `resume`, le découpage de `status`. 10 fonctions exportées.
- **Chargée** : `src/grimoire/flows/engine.py:96` et
  `src/grimoire/runtime/kernel.py:30`. Backend `auto` (`GRIMOIRE_FLOWS_BACKEND`).
- **Parité** : `tests/unit/test_flows_rust_parity.py` (522 lignes), job
  `rust-flows / parity`.
- **Mesure** :

| Fonction | Entrée | Python | Rust | Rapport | Lecture |
|---|---|---:|---:|---:|---|
| `check_output_against_contract` sur chaque node | `web-pipeline.blueprint.json` livré, 7 nodes | 7,57 | 14,38 | 0,53× | Rust plus lent |
| `run`, `resume` de chaque node, `status`, écritures disque comprises | le même blueprint | 143 968,06 | 150 997,71 | 0,95× | indiscernable |

- **Coût** : 1 417 lignes ; 2 des 20 commits Python après le port ont touché le
  crate.
- **Recommandation : geler.** Plus lent sur la seule fonction isolable,
  invisible sur un run complet que les écritures disque dominent (144 ms). Le
  port a trouvé deux défauts réels (`REFUSED` absent des statuts terminaux ;
  #414). Le crate ne freine presque jamais un changement Python : rien de
  mesuré ne presse de le retirer, rien ne justifie de l'étendre.

### `grimoire-dispatch-core`

- **Ce qu'elle fait** : chaîne de paliers, rendu de la ligne d'invocation,
  détection de limite de débit, extraction du coût et des incertitudes d'une
  sortie d'ouvrier, classification de revue, ordre et refroidissement des
  fournisseurs, verdict de cascade. 15 fonctions exportées.
- **Chargée** : `src/grimoire/missions/dispatch.py:110` et
  `src/grimoire/providers/routing.py:32`. Backend `auto`
  (`GRIMOIRE_DISPATCH_BACKEND`).
- **Parité** : `tests/unit/test_dispatch_rust_parity.py` (701 lignes), job
  `rust-dispatch / parity`.
- **Mesure** :

| Fonction | Entrée | Python | Rust | Rapport | Lecture |
|---|---|---:|---:|---:|---|
| Analyse d'une sortie d'ouvrier (coût, incertitudes, limite de débit) | les 10 sorties de `dispatch_worker_outputs`, 1 610 octets | 250,88 | 88,68 | 2,83× | Rust plus rapide |
| `render_invocation`, `_tier_chain`, `start_tier_for` | 5 gabarits, 12 chaînes, 3 classes | 143,88 | 35,19 | 4,09× | Rust plus rapide |
| `_matches_review_surface` | 7 chemins, 5 motifs | 35,00 | 33,72 | 1,04× | indiscernable |
| `providers.routing.candidates`, lecture du registre comprise | registre de 3 fournisseurs | 10 307,39 | 9 789,12 | 1,05× | indiscernable |

- **Lecture** : le gain existe et il est net, mais il vaut 0,16 ms pour dix
  sorties analysées et 0,11 ms pour le lot de petites fonctions. Le routage est
  indiscernable parce que 57 % du temps est la lecture du registre YAML,
  commune aux deux chemins. La durée d'un dispatch réel (l'appel au
  fournisseur) n'a pas été mesurée ici.
- **Coût** : 1 630 lignes ; 1 des 17 commits Python après le port a touché le
  crate.
- **Recommandation : geler.** Un gain d'un dixième de milliseconde par lot, sur
  un crate qui ne coûte presque rien. Le port a trouvé deux défauts (coût
  booléen converti en nombre, `NaN`/`Infinity` acceptés).

### `grimoire-traces-core`

- **Ce qu'elle fait** : les agrégations du journal de traces (choix d'agent,
  non-choix, ancienneté, coût par tâche résolue), la fraîcheur des agents, et
  pour `grimoire.proposals` le nommage, la résolution du porteur et la décision
  de synchronisation. 15 fonctions exportées.
- **Chargée** : `src/grimoire/traces/ledger.py:76` (avec contrôle d'ABI) et
  `src/grimoire/proposals.py:83` (sans). Backend `auto`
  (`GRIMOIRE_TRACES_BACKEND`).
- **Parité** : `tests/unit/test_traces_rust_parity.py` (595 lignes), job
  `rust-traces / parity`.
- **Mesure** :

| Fonction | Entrée | Python | Rust | Rapport | Lecture |
|---|---|---:|---:|---:|---|
| Cinq agrégations du journal | `10_large_multi_agent.jsonl`, 26 enregistrements, 18 635 octets | 5 339,14 | 5 501,97 | 0,97× | indiscernable |
| Les mêmes | la fixture répétée 100 fois, 2 600 enregistrements, **fabriquée** | 404 377,58 | 453 673,32 | 0,89× | indiscernable |
| Propositions : nommage, outils, clause d'emploi, décision | chaînes du test de parité, grille de 30 décisions | 47,79 | 45,77 | 1,04× | indiscernable |

- **Lecture** : chaque agrégation relit et analyse le journal en Python avant de
  choisir un backend ; cette part commune fait 67 % du chemin Python sur la
  fixture et 82 % sur le journal de 2 600 enregistrements. Tant que la lecture
  reste en Python, cette frontière ne peut pas rapporter.
- **De bout en bout** : `PreToolUse` et `SessionStart` chargent l'extension
  sans jamais y entrer.
- **Coût** : 2 467 lignes, le plus gros crate ; 2 des 19 commits Python après
  le port ont touché le crate. C'est le seul dont le côté Python vérifie un
  numéro d'ABI, ajouté parce que la forme des tuples échangés a changé
  (`ledger.py:81-85`).
- **Recommandation : geler.** Aucun gain et aucun en vue à cette frontière ;
  peu de double écriture. C'est le premier candidat à l'archivage si le coût
  d'ensemble doit baisser : le plus de lignes pour un écart indiscernable. Le
  port a trouvé un défaut (horodatage ISO sans décalage).

### `grimoire-acp-core`

- **Ce qu'elle fait** : la machine d'états d'une session ACP (validation des
  messages, états, permissions). Une classe `Machine` et une fonction `replay`.
- **Chargée** : `src/grimoire/acp/machine.py:22`. **Jamais par défaut** :
  `new_machine` choisit Python sauf `GRIMOIRE_ACP_BACKEND=rust`
  (`machine.py:388`). Ici c'est le module Python qui se déclare port du crate.
- **Parité** : pas de fichier `*_rust_parity*` ; les cas sont dans
  `tests/unit/test_acp.py` (lignes 56 à 70), et `cargo test` relit les mêmes
  transcriptions de `tests/fixtures/acp/`. Job `rust-acp / parity`.
- **Mesure** :

| Fonction | Entrée | Python | Rust | Rapport | Lecture |
|---|---|---:|---:|---:|---|
| `replay` de chaque transcription | 7 transcriptions enregistrées, 80 messages, 16 025 octets | 338,19 | 1 251,86 | 0,27× | Rust plus lent |

- **Lecture** : chaque message traverse la frontière en JSON, à l'aller et au
  retour (`machine.py:366` et `:370`).
- **Coût** : 831 lignes, 1 commit, aucun changement depuis le port.
- **Recommandation : geler.** Aucun effet à l'exécution puisqu'il n'est jamais
  chargé par défaut, aucun entretien depuis sa création. Il ne sert que
  d'oracle ; l'étendre n'apporterait pas de vitesse.

## Le hook, de bout en bout

Le hook `PreToolUse` tourne une fois par appel d'outil : c'est là qu'un gain
compterait. Les écarts ci-dessous comparent, à l'horloge murale, le bras où
l'import des extensions est masqué au bras où elles sont installées avec leur
backend par défaut ; le détail des cinq bras est en annexe.

| Commande | Extensions chargées | Appels entrés dans une extension | Écart apparié sans − avec (ms, médiane [Q1 ; Q3]) | Lecture |
|---|---|---|---:|---|
| `grimoire-hook PreToolUse` | `policies`, `traces` | 1 (`evaluate`) | −2,7 [−7,6 ; 8,0] sur 171 | indiscernable |
| `grimoire-hook SessionStart` | `dispatch`, `hosts`, `policies`, `traces` | 40, tous dans `hosts` | 31,5 [−1,6 ; 67,9] sur 471 | indiscernable |
| `grimoire host sync --host claude --dry-run` | `hosts` | 61, tous dans `hosts` | 26,9 [−2,5 ; 50,6] sur 413 | indiscernable (CPU : plus rapide avec) |
| `grimoire check` | `schema` | 1 (`validate_config`) | −8,6 [−29,0 ; 18,4] sur 326 | indiscernable |

Pour `PreToolUse`, les composantes mesurées séparément expliquent pourquoi rien
ne se voit :

- l'appel à `evaluate` coûte 11 µs de plus en Rust (12,28 contre 23,24) ;
- importer une extension présente coûte 0,54 à 0,68 ms ; l'import manqué
  d'une extension absente, 0,13 à 0,16 ms. Deux extensions chargées : environ
  0,9 ms de plus quand elles sont installées ;
- le tout fait moins de 1 ms sur un appel de 171 ms dont l'intervalle
  interquartile dépasse 30 ms.

Un utilisateur, qui n'a aucune extension, paie aujourd'hui les deux imports
manqués : environ 0,3 ms par appel de hook.

Le temps du hook n'est pas dans les fonctions portées : `evaluate` représente
0,007 % du temps CPU de l'appel (12,28 µs sur 168,6 ms). Ce `PreToolUse` coûte
ici 171 ms contre un plancher d'interprète de 10,5 ms ; le CHANGELOG de la
3.44.2 (2026-09-11) le donnait à 55,1 ms. L'écart entre ces deux chiffres n'a
pas été instruit (version du kit, charge de la machine et construction de
l'interprète diffèrent).

## Ce que coûtent les sept crates

| Poste | Mesure |
|---|---|
| Rust, `src/*.rs` | 11 535 lignes |
| Manifestes (`Cargo.toml`, `pyproject.toml`) | 396 lignes |
| `rust-cores.yml` | 779 lignes, 16 jobs ; 583 lignes propres aux crates, 196 communes |
| Tests de parité | 3 652 lignes (les six `*_rust_parity.py` et `tests/unit/test_acp.py`) |
| Binaires construits | 5,3 Mo pour les sept (0,65 à 0,98 Mo chacun) |
| Construction locale à froid | 15 à 19 s par crate, une mesure chacune |

**Durée réelle de la CI.** Le workflow a changé de régime le 2026-11-06 (#1103,
`654bd81c`) : depuis, l'étape « Full SDK suite » de chaque job `parity`, qui
relance toute la suite unitaire avec l'extension chargée, ne tourne plus sur
les PR, seulement sur les pushes vers `main`
(`if: github.event_name != 'pull_request'`). Les deux régimes se lisent à part.

*Régime actuel* — les 299 exécutions terminées de `rust-cores.yml` créées depuis
ce commit (du 2026-11-06T16:29:16Z au 2026-11-13T01:12:11Z) : 25 737 s de jobs,
soit 7,1 h, dont 22 972 s pour les quatorze jobs par crate. `changes`
et `rust-gate` tournent à chaque exécution ; un job par crate ne tourne que si
son périmètre est touché.

| Crate | `cargo` : exécutions | médiane (s) | `parity` : exécutions | médiane (s) | maximum (s) | Total des deux jobs (s) | Jobs en échec |
|---|---:|---:|---:|---:|---:|---:|---:|
| `policies` | 7 | 15,0 | 7 | 69,0 | 806 | 1 794 | 0 |
| `schema` | 5 | 13,0 | 5 | 94,0 | 708 | 1 554 | 0 |
| `hosts` | 23 | 18,0 | 23 | 99,0 | 835 | 5 044 | 0 |
| `flows` | 14 | 16,0 | 14 | 70,5 | 794 | 3 347 | 0 |
| `dispatch` | 26 | 13,0 | 25 | 91,0 | 888 | 6 988 | 0 |
| `traces` | 9 | 11,0 | 9 | 67,0 | 778 | 2 421 | 0 |
| `acp` | 5 | 11,0 | 5 | 71,0 | 815 | 1 824 | 0 |

*Les 1 000 dernières exécutions* (du 2026-10-25 au 2026-11-13), qui
couvrent surtout l'ancien régime : 247 270 s de jobs, soit
68,7 h, dont 237 535 s pour les jobs par crate. Un job `parity` y
durait de 111 à 1 564 s en médiane selon le crate (tableau en annexe).

**L'oracle n'a presque jamais viré au rouge sur ces fenêtres.** Jobs `cargo` ou
`parity` finis en échec : 0 sur 177 exécutions de job depuis #1103, 1 sur
476 sur les 1 000 dernières exécutions (`rust-traces / parity`). `rust-gate`, lui, a
échoué 12 et 42 fois : il échoue aussi quand un job a été annulé (en-tête de
`rust-cores.yml`), ce que `concurrency: cancel-in-progress` provoque à chaque
nouvelle poussée ; la cause de chacun de ces échecs n'a pas été lue. L'API ne
rend que la dernière tentative de chaque exécution.

**Qui a bougé depuis le port** (historique git de `main`, commit de port exclu) :

| Crate | Porté le | Commits sur le crate, port compris | Commits sur les modules Python doublés, après le port | dont commits qui touchent aussi le crate |
|---|---|---:|---:|---:|
| `policies` | 2026-09-09 | 5 | 7 | 4 |
| `schema` | 2026-09-11 | 9 | 9 | 7 |
| `hosts` | 2026-09-11 | 2 | 12 | 1 |
| `flows` | 2026-09-11 | 3 | 20 | 2 |
| `dispatch` | 2026-09-11 | 2 | 17 | 1 |
| `traces` | 2026-09-12 | 3 | 19 | 2 |
| `acp` | 2026-10-27 | 1 | 0 | 0 |

Deux lectures possibles de la dernière colonne, que ce rapport ne départage
pas : pour `hosts`, `flows`, `dispatch` et `traces`, le module Python évolue
presque toujours hors des fonctions portées, donc le crate coûte peu ; mais il
ne vérifie alors qu'une partie stable du module, pas ce qui change.

## Ce qui n'a pas été mesuré

- **Une machine au repos.** La charge moyenne valait de 18 à 41 pendant les
  trois passes. Les rapports sont stables d'une passe à l'autre ; les durées
  absolues sont gonflées d'un facteur non mesuré.
- **Windows, macOS, Python 3.13 et 3.14.** Linux x86_64 et CPython 3.12.13
  seulement.
- **Un projet enrôlé au standard** (`governed`). Le hook y traverse des gates
  que le projet de banc n'a pas.
- **Un journal de traces de taille réelle.** Seules la fixture de 26
  enregistrements et sa répétition ont été lues.
- **La durée d'un dispatch réel**, appel au fournisseur compris : le gain de
  `dispatch` n'est rapporté à aucun total.
- **Ce qu'une correction Python du chemin YAML de `hosts` rapporterait**
  (instance `YAML` réutilisée, chargeur C).
- **Les fonctions non chronométrées une à une** : `matching_approval_rule_ids`,
  `_classify_review` (exige un dépôt git), les transitions du noyau prises
  isolément, `category_carrier` et `resolve_carrier`. Elles ne sont couvertes
  que par les chemins complets.
- **La valeur d'oracle** : les défauts que la double implémentation a évités
  depuis les ports. Seuls sont connus les défauts trouvés au moment de chaque
  port (docstrings des tests de parité) et le très petit nombre d'échecs sur
  les fenêtres de CI lues.
- **Le temps d'écriture de la double implémentation.** Seul le nombre de
  commits joints est compté.
- **La mémoire.**
- **L'écart entre 171 ms et les 55 ms de septembre** sur le hook `PreToolUse`.

## Rejouer la mesure

```bash
python3.12 -m venv venv-rust && python3.12 -m venv venv-pur
venv-rust/bin/pip install -e . "maturin>=1.7,<2"
venv-pur/bin/pip install -e .

for c in policies schema hosts flows dispatch traces acp; do
  venv-rust/bin/maturin build --release \
    --manifest-path rust/grimoire-$c-core/Cargo.toml --out dist-rust
done
venv-rust/bin/pip install --force-reinstall --no-deps dist-rust/*.whl

venv-rust/bin/python scripts/bench/rust_cores_gain.py \
  --reps 30 --process-reps 50 \
  --pure-python "$PWD/venv-pur/bin/python" \
  --json-out gain.json --markdown-out gain.md

# la CI seule, régime actuel puis fenêtre large
venv-rust/bin/python scripts/bench/rust_cores_gain.py \
  --skip-functions --skip-import --skip-process \
  --ci-runs 1000 --ci-since 2026-11-06T16:29:12Z
venv-rust/bin/python scripts/bench/rust_cores_gain.py \
  --skip-functions --skip-import --skip-process --ci-runs 1000
```

Le script n'a pas de dépendance hors de celles du kit ; `--ci-runs` appelle
`gh`. Sans les extensions, il tourne et écrit « non mesuré » dans chaque
colonne Rust. `scripts/bench-rust-cores.py`, le banc de septembre limité à deux
crates, est laissé en place.

## Annexe — sortie du script, passe publiée

Sortie de `scripts/bench/rust_cores_gain.py` pour la passe citée dans ce rapport, sans retouche. Durées par fonction en microsecondes, durées de processus en millisecondes, médiane [Q1 ; Q3].

Machine : Intel(R) Core(TM) i9-9900K CPU @ 3.60GHz (16 CPU logiques), Linux-7.3.3-200.fc44.x86_64-x86_64-with-glibc2.43, CPython 3.12.13, rustc 1.94.1 (e408947bf 2026-03-25). Charge au départ : [17.89, 18.36, 22.47]. Répétitions : 30.

### Par fonction (µs par appel, horloge murale : médiane [Q1 ; Q3])

Python / Rust au-dessus de 1 : Rust plus rapide ; en dessous : Rust plus lent. La colonne CPU refait le rapport sur le temps CPU du processus, moins sensible à la charge de la machine. « Lecture » est descriptive, pas un test statistique : « indiscernable » quand les intervalles [Q1 ; Q3] des deux chemins se recouvrent.

| Crate | Fonction | Entrée | Python | Rust | Python / Rust | Python / Rust (CPU) | Lecture | Mêmes résultats |
|---|---|---|---:|---:|---:|---:|---|---|
| policies | PolicyEngine.evaluate — lecture seule, aucune règle ne s'applique | moteur du hook, 5 règles | 12,28 [12,13 ; 12,87] | 23,24 [22,92 ; 24,50] | 0,53× | 0,53× | Rust plus lent | oui |
| policies | PolicyEngine.evaluate — destructif, refusé | moteur du hook, 5 règles | 13,51 [13,44 ; 14,54] | 25,13 [24,84 ; 29,20] | 0,54× | 0,54× | Rust plus lent | oui |
| policies | evaluate_temporal — budget, approbation, refroidissement | 3 règles temporelles, état de session neuf à chaque appel | 27,57 [27,29 ; 27,75] | 46,96 [45,61 ; 47,29] | 0,59× | 0,59× | Rust plus lent | oui |
| policies | PolicyEngine.evaluate — destructif, refusé | 105 règles (100 fabriquées) — **fabriquée** | 31,60 [24,33 ; 38,64] | 183,28 [166,41 ; 212,67] | 0,17× | 0,17× | Rust plus lent | oui |
| schema | generate_schema | aucune entrée (schéma fixe) | 13,97 [13,56 ; 14,71] | 110,51 [108,66 ; 115,87] | 0,13× | 0,13× | Rust plus lent | oui |
| schema | validate_config — project-context.yaml d'un `grimoire init` | 6 sections, 788 octets | 11,86 [10,76 ; 14,53] | 10,86 [10,00 ; 13,75] | 1,09× | 1,12× | indiscernable | oui |
| schema | validate_config — 190 sections inconnues | 192 sections fabriquées — **fabriquée** | 3866,76 [3596,77 ; 4646,84] | 4215,39 [3862,52 ; 4837,39] | 0,92× | 0,90× | indiscernable | oui |
| hosts | parse_frontmatter sur chaque fiche | 15 fiches d'agent livrées (archetypes/*/agents/*.md), 185308 octets | 32401,12 [29554,64 ; 36188,45] | 943,14 [888,06 ; 1004,61] | 34,35× | 32,42× | Rust plus rapide | oui |
| hosts | dérivation complète d'un AgentSpec (frontmatter, outils, affinité, empreinte) | 15 fiches d'agent livrées (archetypes/*/agents/*.md), 185308 octets | 25217,36 [23945,47 ; 28438,01] | 1227,97 [1104,70 ; 1503,84] | 20,54× | 20,52× | Rust plus rapide | oui |
| hosts | build_surface du projet (lecture disque comprise) | projet `grimoire init` : 4 agents, 8 skills | 37688,23 [31438,87 ; 38437,47] | 13298,33 [12503,22 ; 13961,19] | 2,83× | 2,84× | Rust plus rapide | oui |
| flows | check_output_against_contract sur chaque node | web-pipeline.blueprint.json livré, 7 nodes, 8610 octets | 7,57 [7,28 ; 7,65] | 14,38 [14,20 ; 14,49] | 0,53× | 0,53× | Rust plus lent | oui |
| flows | run, resume de chaque node, status (écritures disque comprises) | web-pipeline.blueprint.json livré, 7 nodes, 8610 octets | 143968,06 [132157,37 ; 153931,01] | 150997,71 [134362,27 ; 180055,56] | 0,95× | 1,01× | indiscernable | oui |
| dispatch | analyse d'une sortie d'ouvrier (coût, incertitudes, limite de débit) | 10 sorties du corpus dispatch_worker_outputs, 1610 octets | 250,88 [231,36 ; 257,34] | 88,68 [85,06 ; 94,99] | 2,83× | 2,59× | Rust plus rapide | oui |
| dispatch | render_invocation, _tier_chain, start_tier_for | 5 gabarits, 12 chaînes de paliers, 3 classes | 143,88 [132,91 ; 147,22] | 35,19 [34,64 ; 36,01] | 4,09× | 3,70× | Rust plus rapide | oui |
| dispatch | _matches_review_surface | 7 chemins contre 5 motifs | 35,00 [32,42 ; 36,16] | 33,72 [31,02 ; 34,88] | 1,04× | 1,05× | indiscernable | oui |
| dispatch | providers.routing.candidates (lecture du registre comprise) | registre de 3 fournisseurs, comme le test de parité | 10307,39 [8872,26 ; 11861,66] | 9789,12 [8752,63 ; 11982,92] | 1,05× | 1,05× | indiscernable | oui |
| traces | cinq agrégations du journal (choix, non-choix, ancienneté, fraîcheur, coût) | 10_large_multi_agent.jsonl, 26 enregistrements, 18635 octets | 5339,14 [5287,56 ; 5387,42] | 5501,97 [5354,75 ; 5554,34] | 0,97× | 0,97× | indiscernable | oui |
| traces | cinq agrégations du journal (choix, non-choix, ancienneté, fraîcheur, coût) | la même fixture répétée 100 fois, 2600 enregistrements — **fabriquée** | 404377,58 [378125,22 ; 454407,89] | 453673,32 [375185,90 ; 492149,25] | 0,89× | 0,89× | indiscernable | oui |
| traces | propositions : nommage, outils, clause d'emploi, décision de synchronisation | chaînes de référence du test de parité, grille de 30 décisions | 47,79 [41,56 ; 58,31] | 45,77 [38,71 ; 56,21] | 1,04× | 1,04× | indiscernable | oui |
| acp | replay de chaque transcription | 7 transcriptions enregistrées, 80 messages, 16025 octets | 338,19 [272,82 ; 423,25] | 1251,86 [1120,96 ; 1527,55] | 0,27× | 0,27× | Rust plus lent | oui |

Part commune aux deux chemins (lecture et analyse faites en Python avant tout choix de backend) :

| Crate | Fonction | Entrée | Part commune (µs) | Chemin Python complet (µs) |
|---|---|---|---:|---:|
| dispatch | providers.routing.candidates (lecture du registre comprise) | registre de 3 fournisseurs, comme le test de parité | 5861,59 [5336,43 ; 6495,55] | 10307,39 [8872,26 ; 11861,66] |
| traces | cinq agrégations du journal (choix, non-choix, ancienneté, fraîcheur, coût) | 10_large_multi_agent.jsonl, 26 enregistrements, 18635 octets | 3566,64 [3491,56 ; 3650,81] | 5339,14 [5287,56 ; 5387,42] |
| traces | cinq agrégations du journal (choix, non-choix, ancienneté, fraîcheur, coût) | la même fixture répétée 100 fois, 2600 enregistrements | 330143,98 [281704,10 ; 358519,37] | 404377,58 [378125,22 ; 454407,89] |

### Import dans un interprète neuf (µs : médiane [Q1 ; Q3])

| Extension | Import réussi (extension installée) | Import manqué (extension absente) |
|---|---:|---:|
| `grimoire_policies_core` | 596,7 [567,4 ; 621,0] | 158,1 [155,1 ; 163,3] |
| `grimoire_schema_core` | 608,3 [488,6 ; 648,2] | 160,7 [150,9 ; 164,8] |
| `grimoire_hosts_core` | 543,1 [493,0 ; 625,4] | 142,3 [109,7 ; 158,6] |
| `grimoire_flows_core` | 675,6 [645,9 ; 769,7] | 158,9 [154,0 ; 167,1] |
| `grimoire_dispatch_core` | 651,3 [614,0 ; 722,7] | 160,4 [149,5 ; 170,9] |
| `grimoire_traces_core` | 615,5 [519,5 ; 638,6] | 148,6 [115,1 ; 163,7] |
| `grimoire_acp_core` | 555,6 [485,7 ; 646,0] | 125,4 [114,8 ; 155,1] |

### Bout en bout : un processus par appel (ms : médiane [Q1 ; Q3])

Plancher de la machine au moment de la mesure — `python -c pass`, 50 répétitions : 10,5 [9,8 ; 12,0] ms d'horloge murale, 9,9 [9,2 ; 11,6] ms de CPU.

**`grimoire-hook PreToolUse`** — tests/fixtures/hosts/claude-code/01-pretooluse-control-allow.json (charge utile capturée), 50 répétitions par bras. Codes de sortie : [0]. Sortie standard identique dans tous les bras : oui.

| Bras | Durée murale (ms) | CPU du processus (ms) |
|---|---:|---:|
| sans extension (import masqué) | 171,0 [156,4 ; 187,8] | 168,6 [153,8 ; 183,7] |
| extensions installées, backend par défaut | 167,6 [158,8 ; 186,5] | 164,2 [154,0 ; 181,7] |
| extensions installées, backend forcé à python | 164,5 [153,2 ; 190,7] | 162,4 [151,3 ; 183,8] |
| script console, venv sans extension | 160,7 [147,5 ; 179,1] | 158,0 [145,5 ; 172,3] |
| script console, venv avec extensions | 165,8 [152,7 ; 181,1] | 161,3 [150,5 ; 178,0] |

Écart apparié, répétition par répétition (sans − avec, en ms ; positif : plus rapide avec) :

| Sans | Avec | Écart mural (ms) | Lecture | Écart CPU (ms) | Lecture |
|---|---|---:|---|---:|---|
| sans extension (import masqué) | extensions installées, backend par défaut | -2,7 [-7,6 ; 8,0] | indiscernable | -0,3 [-7,7 ; 7,7] | indiscernable |
| sans extension (import masqué) | extensions installées, backend forcé à python | 7,8 [-7,0 ; 16,1] | indiscernable | 5,5 [-6,4 ; 13,1] | indiscernable |
| script console, venv sans extension | script console, venv avec extensions | -1,9 [-16,2 ; 5,6] | indiscernable | -2,4 [-13,9 ; 4,3] | indiscernable |

Extensions chargées : `grimoire_policies_core`, `grimoire_traces_core`. Appels entrés dans une extension : `grimoire_policies_core.evaluate` × 1.

**`grimoire-hook SessionStart`** — tests/fixtures/hosts/claude-code/10-session-start-agent.json (forme documentée, non capturée), 50 répétitions par bras. Codes de sortie : [0]. Sortie standard identique dans tous les bras : oui.

| Bras | Durée murale (ms) | CPU du processus (ms) |
|---|---:|---:|
| sans extension (import masqué) | 470,7 [430,0 ; 516,6] | 461,1 [426,3 ; 511,0] |
| extensions installées, backend par défaut | 424,2 [400,9 ; 472,9] | 419,9 [395,2 ; 460,2] |
| extensions installées, backend forcé à python | 453,1 [420,2 ; 492,4] | 446,9 [416,7 ; 482,7] |
| script console, venv sans extension | 446,6 [420,6 ; 491,5] | 440,2 [415,8 ; 486,0] |
| script console, venv avec extensions | 442,5 [404,5 ; 493,4] | 438,1 [401,5 ; 480,8] |

Écart apparié, répétition par répétition (sans − avec, en ms ; positif : plus rapide avec) :

| Sans | Avec | Écart mural (ms) | Lecture | Écart CPU (ms) | Lecture |
|---|---|---:|---|---:|---|
| sans extension (import masqué) | extensions installées, backend par défaut | 31,5 [-1,6 ; 67,9] | indiscernable | 31,4 [-4,6 ; 64,6] | indiscernable |
| sans extension (import masqué) | extensions installées, backend forcé à python | 17,4 [-27,2 ; 40,5] | indiscernable | 15,4 [-26,8 ; 40,0] | indiscernable |
| script console, venv sans extension | script console, venv avec extensions | 7,3 [-23,4 ; 42,8] | indiscernable | 4,5 [-19,1 ; 40,5] | indiscernable |

Extensions chargées : `grimoire_dispatch_core`, `grimoire_hosts_core`, `grimoire_policies_core`, `grimoire_traces_core`. Appels entrés dans une extension : `grimoire_hosts_core.max_turns` × 4, `grimoire_hosts_core.model_affinity_from_frontmatter` × 4, `grimoire_hosts_core.parse_frontmatter` × 12, `grimoire_hosts_core.str_tuple` × 8, `grimoire_hosts_core.tool_verbs_with_rejects` × 12.

**`grimoire host sync --host claude --dry-run`** — projet `grimoire init`, 50 répétitions par bras. Codes de sortie : [0]. Sortie standard identique dans tous les bras : oui.

| Bras | Durée murale (ms) | CPU du processus (ms) |
|---|---:|---:|
| sans extension (import masqué) | 413,4 [384,0 ; 437,5] | 402,8 [380,0 ; 429,1] |
| extensions installées, backend par défaut | 379,9 [357,9 ; 411,8] | 373,0 [354,2 ; 401,6] |
| extensions installées, backend forcé à python | 412,1 [367,3 ; 439,6] | 404,7 [363,5 ; 429,5] |
| script console, venv sans extension | 395,2 [375,2 ; 437,7] | 391,5 [370,0 ; 427,0] |
| script console, venv avec extensions | 389,6 [360,7 ; 411,7] | 381,6 [355,2 ; 405,7] |

Écart apparié, répétition par répétition (sans − avec, en ms ; positif : plus rapide avec) :

| Sans | Avec | Écart mural (ms) | Lecture | Écart CPU (ms) | Lecture |
|---|---|---:|---|---:|---|
| sans extension (import masqué) | extensions installées, backend par défaut | 26,9 [-2,5 ; 50,6] | indiscernable | 28,2 [1,8 ; 51,7] | plus rapide avec |
| sans extension (import masqué) | extensions installées, backend forcé à python | 6,8 [-29,1 ; 29,9] | indiscernable | 5,9 [-30,1 ; 27,2] | indiscernable |
| script console, venv sans extension | script console, venv avec extensions | 19,9 [-4,9 ; 36,4] | indiscernable | 17,4 [-1,7 ; 34,0] | indiscernable |

Extensions chargées : `grimoire_hosts_core`. Appels entrés dans une extension : `grimoire_hosts_core.max_turns` × 4, `grimoire_hosts_core.model_affinity_from_frontmatter` × 4, `grimoire_hosts_core.parse_frontmatter` × 22, `grimoire_hosts_core.partition_duplicate_pairs` × 1, `grimoire_hosts_core.str_tuple` × 8, `grimoire_hosts_core.tool_verbs_with_rejects` × 22.

**`grimoire check`** — projet `grimoire init`, 50 répétitions par bras. Codes de sortie : [0]. Sortie standard identique dans tous les bras : oui.

| Bras | Durée murale (ms) | CPU du processus (ms) |
|---|---:|---:|
| sans extension (import masqué) | 325,9 [293,9 ; 345,1] | 311,8 [288,6 ; 322,9] |
| extensions installées, backend par défaut | 330,7 [289,5 ; 358,6] | 314,6 [284,9 ; 326,5] |
| extensions installées, backend forcé à python | 326,0 [291,5 ; 358,6] | 316,6 [284,5 ; 330,7] |
| script console, venv sans extension | 327,4 [282,5 ; 347,5] | 311,2 [278,8 ; 326,9] |
| script console, venv avec extensions | 329,5 [285,0 ; 353,4] | 314,7 [277,8 ; 329,7] |

Écart apparié, répétition par répétition (sans − avec, en ms ; positif : plus rapide avec) :

| Sans | Avec | Écart mural (ms) | Lecture | Écart CPU (ms) | Lecture |
|---|---|---:|---|---:|---|
| sans extension (import masqué) | extensions installées, backend par défaut | -8,6 [-29,0 ; 18,4] | indiscernable | -1,9 [-16,0 ; 15,3] | indiscernable |
| sans extension (import masqué) | extensions installées, backend forcé à python | -4,7 [-26,8 ; 31,0] | indiscernable | -2,7 [-17,3 ; 19,8] | indiscernable |
| script console, venv sans extension | script console, venv avec extensions | -4,8 [-26,6 ; 15,9] | indiscernable | -3,9 [-16,3 ; 11,2] | indiscernable |

Extensions chargées : `grimoire_schema_core`. Appels entrés dans une extension : `grimoire_schema_core.validate_config` × 1.

### Coût par crate

| Crate | Lignes Rust (`src/`) | Manifestes | Lignes de CI (jobs du crate) | Test de parité | Backend par défaut | Binaire (octets) |
|---|---:|---:|---:|---|---|---:|
| policies | 1505 | 60 | 76 | `tests/unit/test_policies_rust_parity.py` (578 lignes) | auto | 671072 |
| schema | 2304 | 51 | 80 | `tests/unit/test_schema_validator_rust_parity.py` (333 lignes) | auto | 697896 |
| hosts | 1381 | 63 | 80 | `tests/unit/test_hosts_rust_parity.py` (538 lignes) | auto | 975976 |
| flows | 1417 | 57 | 83 | `tests/unit/test_flows_rust_parity.py` (522 lignes) | auto | 650712 |
| dispatch | 1630 | 63 | 90 | `tests/unit/test_dispatch_rust_parity.py` (701 lignes) | auto | 767360 |
| traces | 2467 | 58 | 91 | `tests/unit/test_traces_rust_parity.py` (595 lignes) | auto | 753304 |
| acp | 831 | 44 | 83 | `tests/unit/test_acp.py` (385 lignes) | python | 789952 |

Qui a bougé depuis le port (historique git, commit de port exclu des deux dernières colonnes) :

| Crate | Porté le | Commits sur le crate (port compris) | Commits sur les modules Python doublés, après le port | dont commits qui touchent aussi le crate |
|---|---|---:|---:|---:|
| policies | 2026-09-09 | 5 | 7 | 4 |
| schema | 2026-09-11 | 9 | 9 | 7 |
| hosts | 2026-09-11 | 2 | 12 | 1 |
| flows | 2026-09-11 | 3 | 20 | 2 |
| dispatch | 2026-09-11 | 2 | 17 | 1 |
| traces | 2026-09-12 | 3 | 19 | 2 |
| acp | 2026-10-27 | 1 | 0 | 0 |

`rust-cores.yml` : 779 lignes, 16 jobs ; 196 lignes communes aux sept crates (en-tête, `changes`, `rust-gate`).

### CI, régime actuel (`--ci-runs 1000 --ci-since 2026-11-06T16:29:12Z`)

Durée réelle des jobs de CI — 299 dernières exécutions terminées de `rust-cores.yml` sur `Guilhem-Bonnet/Grimoire-kit`, du 2026-11-06T16:29:16Z au 2026-11-13T01:12:11Z ; 25737 s de jobs au total :

| Job | Exécutions où il a tourné | En échec | Médiane (s) | Maximum (s) | Total (s) |
|---|---:|---:|---:|---:|---:|
| `changes` | 286 | 0 | 6,5 | 15 | 1890 |
| `rust-acp / cargo` | 5 | 0 | 11,0 | 28 | 69 |
| `rust-acp / parity` | 5 | 0 | 71,0 | 815 | 1755 |
| `rust-dispatch / cargo` | 26 | 0 | 13,0 | 23 | 371 |
| `rust-dispatch / parity` | 25 | 0 | 91,0 | 888 | 6617 |
| `rust-flows / cargo` | 14 | 0 | 16,0 | 22 | 204 |
| `rust-flows / parity` | 14 | 0 | 70,5 | 794 | 3143 |
| `rust-gate` | 296 | 12 | 3,0 | 8 | 875 |
| `rust-hosts / cargo` | 23 | 0 | 18,0 | 23 | 357 |
| `rust-hosts / parity` | 23 | 0 | 99,0 | 835 | 4687 |
| `rust-policies / cargo` | 7 | 0 | 15,0 | 24 | 108 |
| `rust-policies / parity` | 7 | 0 | 69,0 | 806 | 1686 |
| `rust-schema / cargo` | 5 | 0 | 13,0 | 20 | 69 |
| `rust-schema / parity` | 5 | 0 | 94,0 | 708 | 1485 |
| `rust-traces / cargo` | 9 | 0 | 11,0 | 24 | 124 |
| `rust-traces / parity` | 9 | 0 | 67,0 | 778 | 2297 |

### CI, 1 000 dernières exécutions (`--ci-runs 1000`)

Durée réelle des jobs de CI — 1000 dernières exécutions terminées de `rust-cores.yml` sur `Guilhem-Bonnet/Grimoire-kit`, du 2026-10-25T06:55:00Z au 2026-11-13T00:53:11Z ; 247270 s de jobs au total :

| Job | Exécutions où il a tourné | En échec | Médiane (s) | Maximum (s) | Total (s) |
|---|---:|---:|---:|---:|---:|
| `changes` | 974 | 0 | 7,0 | 21 | 6709 |
| `rust-acp / cargo` | 37 | 0 | 16,0 | 54 | 682 |
| `rust-acp / parity` | 32 | 0 | 1564,5 | 1873 | 42820 |
| `rust-dispatch / cargo` | 51 | 0 | 16,0 | 44 | 843 |
| `rust-dispatch / parity` | 48 | 0 | 836,0 | 2022 | 42019 |
| `rust-flows / cargo` | 42 | 0 | 17,0 | 42 | 708 |
| `rust-flows / parity` | 37 | 0 | 1276,0 | 1970 | 39171 |
| `rust-gate` | 995 | 42 | 3,0 | 8 | 3026 |
| `rust-hosts / cargo` | 36 | 0 | 17,5 | 28 | 583 |
| `rust-hosts / parity` | 35 | 0 | 111,0 | 1876 | 22463 |
| `rust-policies / cargo` | 27 | 0 | 18,0 | 25 | 477 |
| `rust-policies / parity` | 25 | 0 | 1197,0 | 1873 | 26769 |
| `rust-schema / cargo` | 18 | 0 | 15,5 | 21 | 273 |
| `rust-schema / parity` | 17 | 0 | 1486,0 | 1870 | 19459 |
| `rust-traces / cargo` | 37 | 0 | 18,0 | 46 | 654 |
| `rust-traces / parity` | 34 | 1 | 1464,5 | 1850 | 40614 |
