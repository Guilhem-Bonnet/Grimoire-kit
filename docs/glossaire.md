# Glossaire

> Page générée depuis `framework/glossary.yaml` (source unique). Régénérer
> via `python scripts/gen-glossaire-doc.py`. C'est aussi le fichier que lisent
> les infobulles épinglables de la vue de travail
> (`web/workspace/glossary.js`, spécification §3.2) : éditer une définition ici
> n'aurait aucun effet, éditer le YAML met à jour les deux.

**63 concepts.**

## Espace de travail {: #espace-de-travail }

Un des six regroupements de l'interface par intention — Piloter, Concevoir, Exécuter, Observer, Mémoire, Source — chacun avec sa toile, sa barre de document et son inspecteur.

Raccourci : `⌘1 … ⌘6`

Termes liés : [Toile](#toile), [Inspecteur](#inspecteur), [Dock](#dock)

## Toile {: #toile }

La surface centrale d'un espace, où le document se manipule directement à quatre niveaux de zoom, de la flotte au nœud.

Raccourci : `Z puis F/P/W/N`

Termes liés : [Niveau de zoom](#niveau-de-zoom), [Vue](#vue), [Flotte](#flotte)

## Rail {: #rail }

La colonne d'icônes de 44 px qui ouvre les panneaux latéraux sans jamais les épingler d'elle-même.

Raccourci : `1 … 5`

Termes liés : [Panneau](#panneau), [Explorateur](#explorateur), [Inspecteur](#inspecteur)

## Panneau {: #panneau }

Une surface latérale à trois états — repliée, entrouverte en surimpression, épinglée dans la grille — dont l'état est mémorisé par espace de travail et par projet.

Raccourci : `1 … 5`

Termes liés : [Rail](#rail), [Mode concentration](#mode-concentration), [Densité](#densite)

## Explorateur {: #explorateur }

Le panneau gauche : l'arbre du projet servi — agents, workflows, blueprints, tâches, preuves, mémoire.

Raccourci : `1`

Termes liés : [Panneau](#panneau), [Projet](#projet)

## Inspecteur {: #inspecteur }

Le panneau droit : les propriétés, la validation, le coût et les preuves de ce qui est sélectionné sur la toile.

Raccourci : `4`

Termes liés : [Panneau](#panneau), [Toile](#toile), [Porte de preuve](#porte-de-preuve)

## Dock {: #dock }

La bande basse commune aux six espaces : Console, Traces, Timeline, Problèmes.

Raccourci : `5`

Termes liés : [Console](#console), [Trace](#trace), [Timeline](#timeline), [Problème](#probleme)

## Console {: #console }

Le terminal du dock, qui exécute les sous-commandes `grimoire` du projet servi et refuse tout le reste.

Raccourci : ```

Termes liés : [Dock](#dock), [Commande équivalente](#commande-equivalente)

Documentation : [cli-reference.md](/cli-reference.md)

## Commande équivalente {: #commande-equivalente }

La commande `grimoire …` qu'une action à la souris aurait produite, affichée dans le dock pour que le clavier s'apprenne sans être imposé.

Termes liés : [Console](#console), [Palette de commandes](#palette-de-commandes)

Documentation : [cli-reference.md](/cli-reference.md)

## Palette de commandes {: #palette-de-commandes }

La recherche unique qui atteint projets, espaces, workflows, agents, tâches, fichiers et commandes du kit, chaque entrée montrant sa commande équivalente.

Raccourci : `⌘K`

Termes liés : [Commande équivalente](#commande-equivalente), [Espace de travail](#espace-de-travail)

## Infobulle épinglable {: #infobulle }

Une bulle de définition qu'Alt fige, dans laquelle le pointeur peut entrer, et dont les termes soulignés ouvrent une bulle enfant — trois niveaux au plus, Échap ferme la pile.

Raccourci : `Alt`

Termes liés : [Glossaire](#glossaire), [Mode concentration](#mode-concentration)

## Glossaire {: #glossaire }

Le fichier `framework/glossary.yaml` : une entrée par concept, source unique des infobulles et de la documentation.

Raccourci : `F1`

Termes liés : [Infobulle épinglable](#infobulle)

## Mode concentration {: #mode-concentration }

Le réglage qui replie tous les panneaux, réduit les infobulles au nom et au raccourci et donne la toile à l'écran entier.

Raccourci : `⇧⌘F`

Termes liés : [Densité](#densite), [Panneau](#panneau), [Infobulle épinglable](#infobulle)

## Densité {: #densite }

Un des deux réglages d'affichage — Découverte, aérée et bavarde, ou Concentration, dense et muette — choisi par l'utilisateur, jamais deviné.

Termes liés : [Mode concentration](#mode-concentration)

## Niveau de zoom {: #niveau-de-zoom }

Un des quatre grains de la toile — Flotte, Projet, Workflow, Nœud — qui changent ce qui est montré, pas seulement sa taille.

Raccourci : `Z puis F/P/W/N`

Termes liés : [Toile](#toile), [Flotte](#flotte), [Workflow](#workflow)

## Vue {: #vue }

La forme sous laquelle une collection se lit — Carte, Board ou Liste — la Liste étant celle qui sert à gérer flows et groupes.

Raccourci : `V`

Termes liés : [Toile](#toile), [Board gouverné](#board-gouverne)

## Atelier {: #atelier }

L'hôte mono-projet servi par `grimoire serve` : la vue de travail appliquée au projet courant.

Termes liés : [Cockpit](#cockpit), [Projet](#projet), [Kit](#kit)

Documentation : [cli-reference.md](/cli-reference.md)

## Cockpit {: #cockpit }

L'hôte multi-projets servi par `grimoire cockpit serve` : la même coque que l'atelier, plus le niveau de zoom Flotte et le sélecteur de projet.

Termes liés : [Atelier](#atelier), [Flotte](#flotte), [Projet](#projet)

Documentation : [cli-reference.md](/cli-reference.md)

## Flotte {: #flotte }

L'ensemble des projets enregistrés au registre local, et le niveau de zoom que seul le cockpit ajoute.

Raccourci : `Z puis F`

Termes liés : [Cockpit](#cockpit), [Projet](#projet), [Niveau de zoom](#niveau-de-zoom)

## Projet {: #projet }

Un dépôt initialisé par le kit, reconnaissable à son dossier `_grimoire/`, et l'unité que l'atelier sert et que le cockpit gouverne.

Termes liés : [Kit](#kit), [Étage](#etage), [Standard agentique](#standard-agentique)

## Kit {: #kit }

Le paquet `grimoire-kit` lui-même, dont la version décide de ce que `grimoire up` régénère dans un projet.

Termes liés : [Étage](#etage), [Override](#override), [Empreinte](#empreinte)

## Hôte {: #hote }

Un runtime d'agents que le kit sait équiper — Claude Code, Copilot, Cursor — et vers lequel il projette agents, workflows et hooks.

Termes liés : [Projection](#projection), [Agent](#agent)

## Étage {: #etage }

Une des trois couches auxquelles appartient tout fichier d'un projet — kit régénéré, overrides possédés, projections d'hôtes — et qui décide qui a le droit de l'écrire.

Termes liés : [Override](#override), [Projection](#projection), [Kit](#kit)

## Override {: #override }

Une copie d'un fichier du kit placée sous `_grimoire/overrides/`, qui prime sur son homologue et survit à toutes les mises à jour.

Termes liés : [Étage](#etage), [Kit](#kit), [Empreinte](#empreinte)

## Projection {: #projection }

Un fichier écrit par le kit dans le format d'un hôte — `.claude/`, `.github/` — régénéré à chaque synchronisation et jamais édité à la main.

Termes liés : [Hôte](#hote), [Étage](#etage)

## Empreinte {: #empreinte }

Le SHA-256 d'un fichier, comparé au catalogue des digests livrés par le kit pour décider si le projet l'a écrit ou si une version du kit l'a livré.

Termes liés : [Kit](#kit), [Override](#override), [Étage](#etage), [Digest](#digest)

## Digest {: #digest }

L'entrée du catalogue `registry/kit-file-hashes.json` qui associe l'empreinte d'un fichier livré à son chemin et à la version du kit qui l'a écrit.

Termes liés : [Empreinte](#empreinte), [Kit](#kit)

## Tâche {: #tache }

L'unité de travail du Mission Ledger, avec ses critères d'acceptation, son propriétaire et les portes de preuve qu'elle doit franchir.

Termes liés : [Mission Ledger](#mission-ledger), [Porte de preuve](#porte-de-preuve), [Board gouverné](#board-gouverne), [Rappel de tâche](#rappel-de-tache)

Documentation : [cli-reference.md](/cli-reference.md)

## Mission Ledger {: #mission-ledger }

Le journal en ajout seul qui est la source de vérité des missions, des tâches et de leurs transitions ; le board n'en est qu'une projection.

Termes liés : [Tâche](#tache), [Board gouverné](#board-gouverne), [TraceLedger](#trace-ledger)

Documentation : [adr-005-mission-ledger-source-of-truth.md](/adr-005-mission-ledger-source-of-truth.md)

## Board gouverné {: #board-gouverne }

La projection du Mission Ledger en huit colonnes — proposed, ready, in_progress, blocked, review, accepted, released, archived — repliables en quatre.

Termes liés : [Mission Ledger](#mission-ledger), [Tâche](#tache), [Porte de preuve](#porte-de-preuve)

Documentation : [cli-reference.md](/cli-reference.md)

## Porte de preuve {: #porte-de-preuve }

La règle qui refuse une transition de tâche tant que les preuves déclarées ne sont pas là, et dont le refus nomme la preuve manquante et son remède.

Termes liés : [Tâche](#tache), [Evidence pack](#evidence-pack), [Decision trace](#decision-trace), [Task envelope](#task-envelope), [Claim ledger](#claim-ledger)

## Claim ledger {: #claim-ledger }

Le relevé qui associe chaque affirmation d'une tâche à sa preuve — une affirmation sans preuve reste une hypothèse (AG-QUA-002) — sous `_grimoire-output/evidence/<tâche>/claim-ledger.md`.

Termes liés : [Porte de preuve](#porte-de-preuve), [Evidence pack](#evidence-pack), [Standard agentique](#standard-agentique)

Documentation : [standard/integration.md](/standard/integration.md)

## Evidence pack {: #evidence-pack }

L'inventaire des preuves d'une tâche — commande, test, diff — écrit sous `_grimoire-runtime-output/evidence/`.

Termes liés : [Porte de preuve](#porte-de-preuve), [Tâche](#tache)

## Decision trace {: #decision-trace }

Le relevé des décisions prises pendant une tâche : ce qui a été choisi, contre quoi, et sur quelle base.

Termes liés : [Porte de preuve](#porte-de-preuve), [Evidence pack](#evidence-pack)

## Task envelope {: #task-envelope }

Le contrat d'entrée d'une tâche : ce qu'elle vise, ses critères d'acceptation et ce qu'elle a le droit de toucher.

Termes liés : [Tâche](#tache), [Porte de preuve](#porte-de-preuve)

## Context bundle {: #context-bundle }

Le paquet de contexte qu'un agent reçoit pour une tâche donnée, calculé depuis le projet et non recopié à la main.

Termes liés : [Tâche](#tache), [Agent](#agent)

## Rappel de tâche {: #rappel-de-tache }

Ce que le Mission Ledger et la mémoire du projet savent d'une tâche et de ses voisines au moment du claim — historique propre, cause d'arrêt d'une jumelle, mémoire consolidée à une clôture ou un blocage passés. Borné en tokens, injecté par le hook SessionStart pour une tâche réclamée seulement.

Termes liés : [Tâche](#tache), [Mission Ledger](#mission-ledger), [Evidence pack](#evidence-pack)

Documentation : [cli-reference.md](/cli-reference.md)

## Standard agentique {: #standard-agentique }

Le corps de règles que `_grimoire/standard/` déclare pour un projet — profil, gates, politique mémoire, registre de hooks.

Termes liés : [Profil](#profil), [Porte de preuve](#porte-de-preuve), [Projet](#projet)

## Profil {: #profil }

Le niveau d'exigence du standard — `observed`, `activated`, `governed`, `enforced` — qui décide si une porte avertit ou bloque.

Termes liés : [Standard agentique](#standard-agentique), [Porte de preuve](#porte-de-preuve)

## Trace {: #trace }

Un enregistrement d'exécution du runtime — appels d'outils, verdicts de policy, durées — écrit au TraceLedger.

Termes liés : [TraceLedger](#trace-ledger), [Span](#span), [Timeline](#timeline)

## TraceLedger {: #trace-ledger }

Le journal d'observabilité du projet, sous `_grimoire/standard/traces/` : ce que les hooks ont autorisé ou refusé, et les portes rouges.

Termes liés : [Trace](#trace), [Porte de preuve](#porte-de-preuve), [Mission Ledger](#mission-ledger)

## Span {: #span }

Un intervalle mesuré à l'intérieur d'une trace : une étape, un appel d'outil, avec sa durée et son issue.

Termes liés : [Trace](#trace), [Timeline](#timeline)

## Timeline {: #timeline }

La chronologie unifiée d'une tâche, qui recoud Mission Ledger, TraceLedger, runtime et preuves en une seule suite d'événements datés.

Termes liés : [Tâche](#tache), [Trace](#trace), [Mission Ledger](#mission-ledger)

Documentation : [cli-reference.md](/cli-reference.md)

## Problème {: #probleme }

Un constat de `grimoire doctor` ou de la validation d'un workflow, montré dans le dock avec ce qu'il faut faire pour le lever.

Termes liés : [Doctor](#doctor), [Dock](#dock), [Validation](#validation)

## Doctor {: #doctor }

Le diagnostic du kit sur un projet : chemins qui ne se résolvent pas, hôtes désynchronisés, standard incomplet.

Termes liés : [Problème](#probleme), [Projet](#projet), [Hôte](#hote)

Documentation : [cli-reference.md](/cli-reference.md)

## Validation {: #validation }

Le verdict statique sur un workflow ou un blueprint : agents absents du manifeste, nœuds orphelins, portes sans preuve déclarée.

Termes liés : [Workflow](#workflow), [Blueprint](#blueprint), [Problème](#probleme)

## Antifragilité {: #antifragilite }

Le score qui mesure ce qu'un projet apprend de ses incidents ; `null` signifie « pas encore mesurée », jamais zéro.

Termes liés : [Projet](#projet), [Trace](#trace)

## Coût {: #cout }

La dépense calculée par le modèle de coût unique du kit (`/api/cost-model`), par modèle et par tâche, plutôt qu'estimée dans chaque écran séparément.

Termes liés : [Trace](#trace), [Série](#serie)

## Série {: #serie }

Une des trois couleurs `--s1`, `--s2`, `--s3` réservées aux graphes — coût par modèle, latence — jamais réutilisées pour un état ; une seule série reste neutre.

Termes liés : [Coût](#cout)

## Agent {: #agent }

Une persona exécutable — rôle, outils autorisés, protocole d'activation — déployée dans les hôtes du projet.

Termes liés : [Hôte](#hote), [Équipe](#equipe), [Workflow](#workflow)

## Workflow {: #workflow }

Un enchaînement de nœuds — agents, portes, décisions — que le projet peut valider, simuler puis compiler.

Termes liés : [Nœud](#noeud), [Blueprint](#blueprint), [Validation](#validation)

Documentation : [workflow-taxonomy.md](/workflow-taxonomy.md)

## Nœud {: #noeud }

Une étape d'un workflow, avec son genre, son équipe, son déclencheur et la preuve qu'elle exige à la porte suivante.

Raccourci : `Z puis N`

Termes liés : [Workflow](#workflow), [Porte de preuve](#porte-de-preuve), [Équipe](#equipe)

## Blueprint {: #blueprint }

La description éditable d'un système d'agents, d'où sont compilés agents, workflows et équipes.

Termes liés : [Workflow](#workflow), [Agent](#agent), [Pattern](#pattern)

## Pattern {: #pattern }

Une forme d'orchestration éprouvée et nommée, référencée par un nœud plutôt que recopiée.

Termes liés : [Nœud](#noeud), [Blueprint](#blueprint), [Workflow](#workflow)

## Équipe {: #equipe }

Un groupe d'agents désigné par un nom, qu'un nœud invoque d'un bloc.

Termes liés : [Agent](#agent), [Nœud](#noeud)

## Extension {: #extension }

Un paquet installable qui ajoute des agents, des workflows ou des outils à un projet sans modifier le kit.

Termes liés : [Projet](#projet), [Kit](#kit), [Archétype](#archetype)

## Archétype {: #archetype }

Le jeu d'agents et de workflows que `grimoire init` déploie selon la nature du projet détectée.

Termes liés : [Projet](#projet), [Agent](#agent), [Extension](#extension)

## Mémoire {: #memoire }

Ce que le projet retient entre les sessions : un store d'entrées et un graphe de relations, servis par un ou plusieurs backends.

Termes liés : [Store](#store), [Graphe](#graphe), [Backend](#backend)

## Store {: #store }

La collection d'entrées de mémoire du projet, chacune datée, typée et attribuée.

Termes liés : [Mémoire](#memoire), [Graphe](#graphe), [Backend](#backend)

## Graphe {: #graphe }

Les relations entre entrées de mémoire, entités et décisions, lues comme un réseau plutôt que comme une liste.

Termes liés : [Mémoire](#memoire), [Store](#store)

## Backend {: #backend }

Le moteur qui stocke et interroge une couche de mémoire — local, lexical, vectoriel ou graphe.

Termes liés : [Mémoire](#memoire), [Store](#store), [Graphe](#graphe)

## Démo {: #demo }

Le jeu de données de démonstration, toujours explicitement demandé et toujours marqué comme tel : une vue sans donnée reste vide.

Termes liés : [Projet](#projet)

## Coût {: #cout }

La dépense en tokens d'un span ou d'une trace, convertie en devise par le modèle de coût du projet — jamais estimée quand aucune trace n'existe.

Termes liés : [Span](#span), [Trace](#trace), [Agent](#agent)
