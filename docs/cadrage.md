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

Les deux identifiants émis, `cadrage.gate_incomplete` et
`cadrage.phase_incomplete`, sont comptés dans la dimension de score
`artifacts` (voir `grimoire.core.standard_checks.registry`).

## Voir aussi

- [Référence CLI — Cadrage produit](cli-reference.md#cadrage-produit)
- [Standard agentique gouverné](standard/integration.md)
- [Installer par needs](standard/install-by-needs.md)
