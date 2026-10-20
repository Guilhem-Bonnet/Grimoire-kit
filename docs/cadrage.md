---
audience: utilisateur
---

# Cadrage produit

Comprendre avant de construire. Le cadrage (brique B4) pose un chemin guidé en
cinq phases — brief, brainstorm, compréhension, exigences, cahier des charges
— matérialisées en artefacts gouvernés sous `_grimoire/cadrage/`. Le kit ne
« pense » pas à votre place : il structure le chemin, mesure la progression et
**gate** la complétude ; la réflexion se fait dans vos sessions d'agents,
guidées par les gabarits.

## Les cinq phases

| # | Fichier | Phase | Discipline | Gate |
|---|---|---|---|---|
| 1 | `01-brief.md` | Brief — l'intention | Une page : le problème, l'utilisateur, pourquoi maintenant | non |
| 2 | `02-brainstorm.md` | Brainstorm — diverger | Diverger sans censure ; noter ce qui est écarté et pourquoi | non |
| 3 | `03-comprehension.md` | Compréhension — l'utilisateur réel | Séparer strictement les *faits* (sourcés) des *hypothèses* (à valider) | non |
| 4 | `04-exigences.md` | Exigences — quoi, pas comment | Priorisées (MoSCoW), chaque exigence porte ses critères d'acceptation | **oui** |
| 5 | `05-cahier-des-charges.md` | Cahier des charges | Consolidation opposable : périmètre, hors-périmètre, risques, jalons | **oui** |

Seules les deux dernières phases sont un **gate** : un cahier des charges
incomplet est une erreur — on ne construit pas sur un engagement flou. Les
phases amont incomplètes restent des avertissements : le chemin est
recommandé, pas imposé.

## Trois façons de démarrer le cadrage

### 1. Le need `project-discovery`, au moment du setup

Le need et la commande se parlent par code, pas par une suggestion textuelle :
choisir `project-discovery` — au wizard web (espace **Piloter**), ou en ligne
de commande — scaffolde réellement `_grimoire/cadrage/` en même temps que le
reste du standard (`cmd_up.py::_step_cadrage`, appelé par
`grimoire up . --needs project-discovery`).

```bash
grimoire up . --needs project-discovery
```

C'est aussi la suggestion que `grimoire up` (sans `--needs`) affiche pour un
projet vierge sans signal particulier — `needs_suggest.py` la recommande
comme point d'entrée, cadrer avant de foncer.

### 2. `grimoire cadrage init`, à la main

Pose les cinq gabarits sans passer par le need — utile pour cadrer un projet
déjà initialisé, ou reprendre un cadrage abandonné (`--force` pour réécrire).

```bash
grimoire cadrage init            # pose les cinq phases
grimoire cadrage status          # progression, phase par phase
grimoire cadrage check           # gate de complétude (exigences + CDC)
```

### 3. Rien du tout

Un projet qui ne cadre jamais reste un projet Grimoire valide — le cadrage
n'est ni un prérequis à `grimoire init`, ni une case du standard obligatoire
par défaut. Il ne devient exigeant qu'au moment où on le demande (voir plus
bas).

## Du cadrage aux tâches : `grimoire cadrage plan`

Identifiez chaque exigence par `REQ-` suivi de chiffres, en tête de ligne ou
dans la première colonne d'un tableau. Ses critères d'acceptation vont en
sous-liste :

```markdown
## Fonctionnelles

- REQ-001 [Must] : l'utilisateur se connecte par lien magique
  - `pytest tests/test_login.py`
- REQ-002 (Should) : export CSV des commandes
  - un test vérifie que l'export contient toutes les commandes

| ID | Exigence | Priorité | Critère d'acceptation |
|----|----------|----------|-----------------------|
| REQ-003 | Temps de réponse < 200 ms | Could | la revue de performance valide le rapport |
```

Ce qui est lu :

- dans une section « Critères d'acceptation », `- REQ-001 : …` ajoute un
  critère à REQ-001 ;
- dans « Priorisation (MoSCoW) », `- REQ-001 : Must` fixe la priorité ;
- dans « Hors périmètre », l'exigence sort du plan, comme une priorité `Won't` ;
- une exigence reprise dans le cahier des charges est la même exigence :
  sa formulation y fait foi. Redéfinie autrement dans un même fichier, c'est
  un conflit à trancher.

Un cadrage sans identifiant reste valide. `cadrage plan` dit alors qu'il ne voit
rien à planifier.

```bash
grimoire cadrage plan            # aperçu ; dans un terminal, propose d'écrire
grimoire cadrage plan --yes      # écrit les tâches et pose les questions
grimoire cadrage plan --check    # couverture : rend 1 s'il reste un trou
grimoire -o json cadrage plan    # le plan en JSON, sans écrire
```

Pour chaque exigence, une tâche du ledger : critères repris tels quels,
`covers: [REQ-…]`, priorité tirée du MoSCoW, classe de vérifiabilité calculée.
Un critère qui n'est qu'une commande entre accents graves devient une commande
de vérification de la tâche. Relancer ne recrée rien : une exigence déjà
couverte par une tâche non annulée est sautée.

Les ambiguïtés deviennent des questions, posées en un seul lot sur une tâche
« Cadrage : trancher les questions des exigences ». Elles arrivent dans la vue
**Décisions** du cockpit :

| Ambiguïté | Question | Bloquante |
|---|---|---|
| Exigence sans critère | « À quoi verra-t-on qu'elle est satisfaite ? » (la réponse humaine devient le critère) | oui |
| Même identifiant défini deux fois dans un fichier | « Quelle formulation fait foi ? » | oui |
| Critère sans verdict reconnu (V2) | « Comment vérifier … ? » | non |
| Deux identifiants pour la même exigence | « Les fusionner ? » | non |

Code de sortie 1 quand une exigence reste sans critère vérifiable ou en conflit.
Corriger le fichier suffit à rendre une question caduque, sans réponse
fabriquée.

## Le gate, dans `standard verify`

`cadrage check` (la commande) et le gate du standard (`grimoire standard
verify` / `gate` / `score` / `audit`) partagent désormais le même verdict —
un contrôle dédié (`grimoire.core.standard_checks.verifiers._verify_cadrage`)
lit `_grimoire/cadrage/` et pousse ses constats dans le même rapport que tout
le reste du standard agentique.

Trois règles gouvernent sa sévérité :

1. **Rien à vérifier** si `_grimoire/cadrage/` n'existe pas — un projet qui
   n'a jamais cadré n'est pas pénalisé pour ça.
2. **Avis seulement** (`info` pour les phases amont, `warning` pour les
   phases gate) quand le cadrage existe mais que le need
   `project-discovery` n'a jamais été demandé — par exemple un `grimoire
   cadrage init` manuel sur un projet déjà en profil `starter`. Un avis ne
   fait jamais échouer `standard verify`.
3. **Échec dur** (`error` sur les phases gate) quand `project-discovery` a
   été explicitement choisi (`_grimoire/standard/install-manifest.yaml`
   porte le need dans sa sélection). C'est cette demande explicite qui rend
   le cadrage exigeant, pas le palier du profil (`starter`, `governed`…) :
   un projet qui a lui-même demandé à cadrer avant de construire ne doit pas
   pouvoir ignorer silencieusement son propre gate.

```bash
grimoire up . --needs project-discovery   # scaffolde _grimoire/cadrage/
grimoire standard verify                  # FAIL tant que exigences/CDC sont incomplets
# ... remplir 04-exigences.md et 05-cahier-des-charges.md ...
grimoire standard verify                  # verdict propre
```

Avec des exigences identifiées, le gate lit aussi le ledger :

- `cadrage.question_open` (**erreur**, tout profil) : une question bloquante
  posée par `cadrage plan` reste sans réponse humaine alors que son ambiguïté
  est toujours dans le cadrage. Le message nomme la question et comment y
  répondre. Une question non bloquante ne fait jamais échouer le gate ;
- `cadrage.requirement_uncovered` (exigence sans tâche) et
  `cadrage.task_orphan` (tâche qui couvre une exigence absente) :
  avertissements. Ils deviennent des erreurs en profil `governed` ou
  `production` quand le cahier des charges porte `status: accepted` dans son
  front matter (même règle que `acceptance.uncovered`).

Les identifiants émis, `cadrage.gate_incomplete` et
`cadrage.phase_incomplete`, puis les trois ci-dessus, sont comptés dans la dimension de score
`artifacts` (voir `grimoire.core.standard_checks.registry`).

## Voir aussi

- [Référence CLI — Cadrage produit](cli-reference.md#cadrage-produit)
- [Standard agentique gouverné](standard/integration.md)
- [Installer par needs](standard/install-by-needs.md)
