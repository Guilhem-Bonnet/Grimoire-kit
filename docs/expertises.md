<p align="right"><a href="../README.md">README</a></p>

# <img src="assets/icons/puzzle.svg" width="32" height="32" alt=""> Catalogue d'expertises — langages, ingénierie, cloud

Issue [#616](https://github.com/Guilhem-Bonnet/Grimoire-kit/issues/616). Ce document est la
référence API du mécanisme : ce qu'un appelant (le wizard d'onboarding de la PR 3, le cockpit, un
script) peut attendre de `grimoire.core.expertises` et de `grimoire expertise`, et comment le
mécanisme s'articule avec les tiers `kit`/`overrides` existants.

<img src="assets/divider.svg" width="100%" alt="">

## <img src="assets/icons/lightbulb.svg" width="28" height="28" alt=""> Le principe : rien d'attaché par défaut, sauf choix ou détection

Le kit livre par archétype un agent généraliste (`stack-engineer`, `ops-engineer`) plus les skills
qu'il attache d'emblée. Le catalogue d'expertises ajoute une troisième couche, optionnelle : un
langage bas niveau, un patron d'architecture, un fournisseur cloud — que l'utilisateur choisit
explicitement ou que la détection recommande, jamais attaché d'office. La raison est chiffrée dans
[la doctrine des artefacts](artifact-doctrine.md#-décisions-déjà-prises) : un skill non référencé
par un agent devient **transversal** (chargé à chaque tour de la session, par tous les agents),
alors qu'un skill attaché ne coûte que sur les tours où son agent porteur travaille.

Ce même principe a corrigé un défaut du mécanisme historique (issue #375) : les sept skills
`stack-python`/`stack-go`/`stack-typescript`/`stack-docker`/`stack-terraform`/`stack-ansible`/
`stack-k8s` s'attachaient en bloc dès que l'archétype `stack` était installé, qu'ils servent ou
non — un projet Python nu payait six skills transversaux pour rien. Depuis cette issue, seule la
détection réelle du projet (`grimoire.core.scanner.StackScanner`) décide du sous-ensemble attaché
par défaut ; le reste s'ajoute à la demande via ce même catalogue (voir
`ProjectScaffolder._detected_stack_skill_slugs` dans `src/grimoire/core/scaffold.py`).

## <img src="assets/icons/hexagon.svg" width="28" height="28" alt=""> Le registre — `registry/expertises.yaml`

Trois familles, chacune une liste d'entrées :

| Famille | Contenu | Détection |
|---|---|---|
| `languages` | Python, TypeScript, Go (skills `stack-*` réutilisés tels quels), Rust, C, C++, C#, Java, Kotlin, Swift, PHP, Ruby, Scala, Elixir | Marqueurs de fichiers, ou `StackScanner` pour les langages qu'il connaît déjà |
| `engineering` | Design patterns, architecture haut niveau, systèmes bas niveau, testing avancé, sécurité applicative, performance | Aucune — transversal, jamais recommandé automatiquement |
| `cloud` | AWS, Azure, GCP, OVHcloud, Hetzner, Cloudflare, Scaleway, Kubernetes managé (EKS/AKS/GKE) | Marqueurs de fichiers et/ou bloc `provider "<nom>" {}` dans les `*.tf` |

Chaque entrée :

```yaml
- id: rust                                            # identifiant stable, utilisé par le CLI et l'API
  name: "Rust"                                        # nom affiché
  summary: "Une phrase."                              # résumé affiché par `expertise list`
  skill: "framework/expertises/skills/expertise-rust.md"  # chemin relatif à la racine kit
  porteur: stack-engineer                             # agent visé par défaut
  porteur_fallback: stack-engineer                    # optionnel — utilisé si `porteur` est absent du projet
  detect:                                             # optionnel — absent = jamais recommandé automatiquement
    stack_scanner: rust                               # réutilise StackScanner (mêmes marqueurs qu'à l'init)
    files: ["Cargo.toml"]                              # marqueurs vérifiés directement (fichiers, globs, dossiers en `/`)
    terraform_providers: ["aws"]                       # bloc `provider "aws" {}` dans un `*.tf` du projet
```

`skill` pointe soit un skill `archetypes/stack/skills/stack-*.md` déjà existant (les sept ne sont
jamais dupliqués), soit un nouveau skill sous `framework/expertises/skills/` — cette seconde
racine n'est **jamais** copiée en bloc par le scaffolder (contrairement à `archetypes/*/skills/`) :
un skill n'y devient effectif pour un projet que via `grimoire expertise add`.

`porteur_fallback` couvre le cas cloud sur un projet sans `infra-ops` : `expertise add aws` sur un
projet `stack`-seul attache le skill à `stack-engineer` plutôt que d'échouer.

## <img src="assets/icons/microscope.svg" width="28" height="28" alt=""> API Python — `grimoire.core.expertises`

```python
from pathlib import Path
from grimoire.core.expertises import load_catalog, detect_expertises, attach_expertise, detach_expertise

catalog = load_catalog()                              # tuple[Expertise, ...], ordre du registre
recommendations = detect_expertises(Path("."), catalog=catalog)  # list[Recommendation], jamais de mutation

by_id = {e.id: e for e in catalog}
result = attach_expertise(Path("."), by_id["rust"])   # AttachResult — écrit l'override, valide, ou lève
detach_expertise(Path("."), by_id["rust"])            # DetachResult — retire le slug ET le fichier de skill
```

- `Expertise.resolve_porteur(project_root)` — l'agent porteur effectif pour ce projet
  (`porteur`, sinon `porteur_fallback`, sinon `GrimoireAgentError` nommée : jamais un attachement
  silencieux à un agent absent).
- `Recommendation` porte `id`, `name`, `family`, `reason` (texte humain, jamais vide) et
  `confidence` (0.0–1.0). Une expertise sans signal présent n'apparaît pas dans la liste — il n'y a
  pas de recommandation à confiance nulle.
- `attach_expertise`/`detach_expertise` valident toujours via
  `grimoire.hosts.collect.collect_agents`/`collect_skills` avant de considérer l'écriture définitive
  ; un échec de résolution restaure l'état précédent (même discipline que
  `grimoire.core.override_drift.convert_override`). Idempotents : attacher deux fois ne duplique
  rien (`AttachResult.already_attached`), détacher une expertise jamais attachée est un no-op nommé
  (`DetachResult.was_attached is False`).

**Contrat de stabilité (pour la PR 3 — étape « Expertises » du wizard)** : les noms de fonctions,
les champs de `Expertise`/`Recommendation`/`AttachResult`/`DetachResult` et le format du registre
sont la surface publique de ce module. Un appelant peut lister le catalogue, appeler
`detect_expertises` pour pré-cocher les recommandations, laisser l'utilisateur ajuster la sélection,
puis appeler `attach_expertise` pour chaque id retenu — sans jamais lire ou écrire directement sous
`_grimoire/`.

## <img src="assets/icons/wrench.svg" width="28" height="28" alt=""> CLI — `grimoire expertise`

```bash
grimoire expertise list                              # tout le catalogue, groupé par famille
grimoire expertise list --detected                   # seulement ce que la détection recommande, avec raison
grimoire expertise add rust aws                       # attache (override projet, jamais _grimoire/kit/)
grimoire expertise remove rust                        # détache (retire aussi le fichier de skill)
```

`add`/`remove` n'invoquent pas `grimoire host sync` elles-mêmes (même séparation des responsabilités
que `grimoire agent override convert`) — lancez-le ensuite pour projeter le changement sur les
hôtes configurés. `grimoire up` conserve la sélection : elle vit sous `_grimoire/overrides/`, un
tier que `up` ne régénère jamais.

## <img src="assets/icons/branch.svg" width="28" height="28" alt=""> Mécanique d'attachement — ce que `add`/`remove` écrivent

1. Le corps du skill est copié depuis le paquet kit (`archetypes/stack/skills/` ou
   `framework/expertises/skills/`) vers `_grimoire/overrides/skills/<slug>.md`.
2. Le porteur reçoit un override partiel (`extends: kit`) sous
   `_grimoire/overrides/agents/<porteur>.md`, avec le slug ajouté (ou retiré) de son champ
   `skills:` — le même mécanisme que `grimoire agent override convert`
   (`grimoire.core.override_drift`), une simple redéfinition de champ de frontmatter, jamais une
   copie intégrale de l'agent.
3. `detach_expertise` supprime aussi le fichier de skill de l'étape 1 — le garder sans qu'aucun
   agent le référence le rendrait transversal, un état pire que l'attachement qu'on voulait défaire.

## <img src="assets/icons/clipboard.svg" width="28" height="28" alt=""> État de la couverture

24 des 28 entrées ont un skill livré (première et seconde vagues, issue #616). Une seule entrée
n'a pas encore de détection fiable : `kubernetes-managed` (distinguer une ressource de cluster
managé d'un simple provider `aws`/`azurerm`/`google` utilisé pour autre chose demande un détecteur
par nom de ressource Terraform, pas encore écrit) — le skill existe, l'attachement manuel
(`grimoire expertise add kubernetes-managed`) fonctionne, seule la recommandation automatique reste
une issue de suivi.

<img src="assets/divider.svg" width="100%" alt="">

Voir aussi : [Doctrine — quand créer un artefact](artifact-doctrine.md) (le coût d'un skill non
attaché), [Créer un agent](creating-agents.md), `tests/unit/core/test_expertises_catalog.py` (le
contrat que chaque entrée doit tenir).
