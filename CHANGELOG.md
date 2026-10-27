# <img src="docs/assets/icons/history.svg" width="32" height="32" alt=""> Changelog

Toutes les modifications notables de ce projet sont documentées dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## [Unreleased]

- feat(missions): sous mandat, une session orchestrateur lancée par le tick répond aux questions des workers (Refs #962)
  - **Rôle `orchestrator`** dans `orchestration-policy.yaml` (`enabled` faux par défaut, `model` sonnet, `wip` 1, `sessions_per_day` 12, `permission_mode` obligatoire une fois activé) : après le merger, quand une tâche lancée par le tick attend en `waiting(human)` une réponse qu'un mandat actif couvre, le tick lance une session `claude --bg --name grimoire-orchestrator` dans la racine du projet, avec la liste des questions (texte, options, critères, notes, consignes). Elle tranche dans le périmètre des critères par `GRIMOIRE_PRINCIPAL=orchestrator GRIMOIRE_PROVENANCE=rule:… grimoire task answer`, ou ne répond pas et la question reste à l'humain.
  - **Gestes** : `answer` pour une question `choice` du worker ; `resume` pour une reprise de la récolte (`resume_unfenced`, `retry_exhausted`) ou un désaccord de relecture. Jamais pour un checkpoint de flow, une question `merge`, `permission` ou `resume_branch`, une question du merger, ni la réponse `abandonner`. `task answer` refuse ces cas pour toute origine `orchestrator`, avant le mandat : le journal d'autorité ne dit plus « pris » d'un geste refusé ensuite.
  - **Identité** : le vrai `sessionId` est lu dans `claude agents --json` après le lancement (#966), puis inscrit en rôle `orchestrator`, **borné aux gestes `answer` et `resume`** : la session ne peut ni accepter, ni fusionner, ni approuver un check, même si le mandat les couvre. Identité introuvable ou inscription refusée : session arrêtée, tentative notée et comptée au disjoncteur. WIP, plafond du jour, arrêt après 30 minutes, et une question présentée une fois n'est pas relistée.
  - **Limites** : la session est un LLM et peut trancher faux dans le périmètre ; chaque réponse est journalisée avec sa provenance, et l'humain garde la révocation du mandat.
- feat(cockpit): cartes du kanban lisibles par un humain (P74, `GAO-p74-cartes-k-001`)
  - **Fin des sessions vivantes en colonne « Fait »** : le serveur pose `task.session` (claim, ou journal récent qui nomme la tâche) sans condition d'état, et la carte l'affichait tel quel. Une tâche close (acceptée, publiée, annulée) n'affiche plus de vivacité — si la session existe encore, c'est l'inspecteur qui le dit.
  - **Fin du jargon** : « consigne non lue » devient « message en attente de la session » (badge de carte, portefeuille, inspecteur), avec l'explication au survol (consigne posée au ledger, pas encore livrée à la session qui tient la tâche).
  - **Plus d'UUID sur les cartes** : hôte + persona du claim + palier de modèle quand ils sont connus (« hôte inconnu » sinon, rien n'est inventé) ; l'identifiant complet et la commande de reprise restent dans l'inspecteur.
  - **Titres sans préfixe interne** : « R52 — », « [P47] », « P68 — » sortent des cartes, de la liste et du titre de l'inspecteur ; l'identifiant reste en métadonnée discrète à côté de l'identifiant de tâche, et le titre entier au survol.
  - **Owner situé** : « ouverte par guilhem » / « ouverte par kit-dev » (au survol : qui a ouvert la tâche, pas qui travaille dessus), icône personne ou agent déduite du nom, et un liseré gauche stable par porteur (hash → tokens `--owner-1…6`, dérivés des teintes d'identité) pour balayer le tableau.
  - **Deux temps nommés** : « dans `<colonne>` depuis … » (dernière transition, nouveau champ `in_state_since` servi par le board) sur la carte, l'âge total (depuis l'ouverture) au survol et dans l'inspecteur.
  - **Type lisible** : badge couleur stable code / design / doc / infra / autre (tokens `--type-*`), du `type` déclaré au ledger quand il tranche seul, sinon d'une heuristique honnête sur titre + critères, dite au survol (« type déduit du contenu ») ; le mot est toujours écrit, jamais la couleur seule.
## [3.68.0] - 2026-10-26

- fix(hosts): citer la zone de contrôle dans un texte ne révoque plus le mandat, et une révocation ciblée ne touche que les mandats de la session (#982)
  - **Mention prouvée inoffensive** : la garde d'autorité refuse toujours, par prudence, une commande qui cite `~/.grimoire/control`. Elle révoque désormais **sauf** si une liste blanche stricte prouve la mention inoffensive : valeur d'une option de prose (`git commit|tag -m/--message`, `gh issue|pr create|comment|edit --body/--title`), corps de heredoc consommé par `cat > <hors zone>`, `git commit -F -` ou `gh … --body-file -`, opérande d'un lecteur pur (`grep`, `rg`, `ls`, `head`, `tail`, `wc`, `cat` sans redirection). Tout ce qu'elle ne sait pas lire révoque : `$`, `$(…)`, backtick, `eval`, `| bash`, `<(…)`, `source`, commentaire, guillemet non fermé, programme ou préfixe inconnu (`sudo -u`, `env -i`…). Une exception de cette lecture révoque aussi ; le refus reste un refus.
  - **Qui tombe** : viser le journal du mandat ou la clé du cockpit (qui forgent un mandat pour n'importe quel projet), ou une cible illisible, révoque tous les mandats du poste. Sinon tombent le mandat du projet de la session (et celui du worktree principal) et chaque mandat où la session est inscrite. Une session hors projet reconnu ou sans mandat révoque tout, comme une révocation ciblée en erreur.
  - **La cause au statut** : l'entrée de révocation inscrit la session, l'outil, le programme lancé (premier mot) et la zone visée, sans aucun argument de la commande. `grimoire autonomy mandate status` les affiche, et sa sortie JSON les porte sous `revocation`, avec l'heure.
- fix(missions): le minuteur du tick met en tête de son `PATH` le dossier de l'interpréteur qui l'a posé, s'il porte `grimoire` (#983). Les sessions lancées par le tick appellent `grimoire task …` par le `PATH` et exécutaient le premier `grimoire` du poste : un pipx resté en 3.65.0 sous un tick en 3.67.0 annulait le correctif d'identité (#966), et les reçus des relecteurs étaient refusés. L'environnement des sessions de fond étant celui du démon `claude`, le changement vaut après la réinstallation du minuteur (`orchestrate install-timer`) et le redémarrage du démon.

- feat(cockpit): premier lancement en lecture seule — la page servie dit l'état du projet (S0 à S3) avec sa source, et le cockpit guide un projet non configuré sans rien écrire (P36)
  - **État lu, jamais deviné** : `web/workspace/setup-state.js` classe le projet d'après `/api/status`, `/api/health`, `/api/setup/run` et `/api/memory/status` (sans sonde) — S0 pas initialisé, S1 initialisation en échec, S2 mémoire non liée (lexical seul), S3 prêt — et cite toujours la lecture qui tranche (« lu dans /api/health · kit.scaffolded = false ») ; une lecture ratée ou une mémoire jamais sondée donne « inconnu » avec sa raison, jamais un faux S0 ni un faux S3.
  - **Guide « Démarrer »** : sur un projet que `/api/health` dit non initialisé, À traiter cède la place au guide (`#a-traiter?view=demarrer`, joignable à tout moment et depuis la palette) : détection de la machine, quatre profils mémoire dont les impossibles restent visibles, grisés, avec leur raison, plan (ce qui serait écrit, démarré, partagé, les extras à installer) et commandes à copier, « Copier » confirmant sur place. Aucun POST, aucun PUT, aucun bouton qui installe (ADR-008, P40).
  - **Route** : `GET /api/workspace/setup/guide` (`tools/workspace_setup_guide.py`) — Python, outil d'installation, démon Docker, embeddings locaux, services mémoire en ligne, hôtes, registre ; réseau sortant jamais sondé ; une sonde ratée rend « inconnu », jamais « absent ».
  - **Vitrine** : servie par le cockpit (marqueur `grimoire-session` qu'il injecte), `index.html` affiche l'état en tête avec sa source et le lien vers le guide ; publiée, ouverte depuis le disque ou servie par un autre serveur, elle ne fait aucune requête, ni vers localhost ni vers une API.
- feat(cockpit): Savoir et Flotte › Données conformes aux maquettes « Savoir — santé et carte » et « Flotte — données » (P73, `GAO-p73-savoir-e-001`)
  - **Carte par hall** : Savoir › Santé et carte dessine enfin la carte (surface = nombre d'entrées classées, forme = hall), nourrie par les comptes par type ajoutés à `memory/quality` (`measures.types`) — jamais simulée : un store vide ou non lu dit sa place vide, un scan incomplet annonce « comptes sur les N premières entrées ». Un clic sur un hall ouvre la Recherche filtrée sur ses types (nouveau paramètre `types` de `#savoir/memoire`). La légende « Lire la carte » occupe le panneau de droite et ne documente que ce qui est rendu (la forme ; fraîcheur et liens sont nommés à venir).
  - **Recherche** : la colonne du type d'un résultat s'ajuste au libellé (« Apprentissage » entier, plus d'ellipse) ; le badge de verdict de la Santé passe à la ligne au lieu d'être coupé.
  - **Flotte › Données** : la matrice « Qui partage quoi » répartit ses colonnes comme la maquette (projet plus large, une colonne égale par service) au lieu d'hériter des largeurs du tableau Projets — l'en-tête « Embarqué » n'est plus écrasé ; un service en ligne ne répète plus « le service répond » sous son badge (le motif reste dans son `title`), tout autre état garde sa raison écrite.
- feat(cockpit): panneau « Capacité » dans la fiche projet de Piloter (P71, `GAO-p71-capacit-001`) — les goulots d'étranglement de l'admission (K10) deviennent visibles.
  - **Route** : `GET /api/workspace/capacity` (`src/grimoire/tools/workspace_capacity.py`), lecture pure servie par les deux hôtes — le même plan à blanc que `grimoire queue plan` (routes du routeur lues, jamais écrites) : WIP `{actifs, limite, par_role}`, réservoirs (`ci.runners`, `host` charge/mémoire, `llm.<fournisseur>` — noms stables, le préflight « Assigner une session » de P47 lira la même source), quota de tokens du jour, file ordonnée avec le verdict `admit|defer`, la raison en clair et `retry_at`, exclues (dépendances, backoff), famine, pause. Une sonde muette, une section de `capacity.yaml` illisible ou une consommation inconnue sortent `inconnu`, jamais une valeur devinée.
  - **Panneau** : composant réutilisable `spaces/capacite.js` (pensé pour une future vue « Plan » de Travail), hébergé par la fiche projet. L'info reine est la file des tâches prêtes groupée par raison de report (« Lançables maintenant », puis « WIP atteint », « CI pleine », « hôte chargé », « quota »… — logique des groupes de la maquette Plan) ; au-dessus, « N workers actifs sur M », le quota, et un bandeau compact de réservoirs : une ligne par réservoir saturé ou inconnu (« inconnue » en ton neutre — une sonde muette n'est pas une panne), les sains regroupés sur une ligne. Jauges `role="meter"` ; actualisation par le pouls de la coque (UI-3), jamais une boucle dédiée ; aucune session listée — la seule liste de sessions reste Travail › Sessions.
- feat(cockpit): décisions lisibles — une carte par type, reprises en lot, frise sans chevauchement (P68 ; `GAO-p68-d-cision-002`)
  - **Une carte par type** : la vue Travail › Décisions range ses cartes en sections (gestes humains, choix, questions libres, reprises), la plus ancienne d'abord, avec des compteurs par type qui filtrent. Chaque carte n'a plus qu'une action primaire : « Fait » pour un geste humain, « Valider ce choix » après une liste de choix alignés à gauche (`aria-pressed`, au clavier), « Répondre » pour une question libre, « Reprendre » pour une reprise (« Abandonner » reste secondaire). La zone libre se replie derrière « Autre réponse… » dès qu'il y a des réponses proposées.
  - **Reprises** : une phrase écrite par la vue remplace le texte serveur (sonde, variable d'environnement), qui reste entier dans « Détails » avec le pourquoi, l'identifiant interne (« R52 », retiré du titre), la question, la session et « Copier la commande ». « Tout reprendre (N) » et « Tout abandonner (N) » demandent confirmation dans la page, envoient les réponses l'une après l'autre ; un refus n'arrête pas le lot et le bilan le nomme.
  - **Pastilles et boutons** : `.chip` et `.btn` passent d'une hauteur fixe à une hauteur minimale, si bien qu'un libellé long (preuve attendue, option) s'enroule dans sa pastille au lieu d'en sortir. Une pastille ou un bouton d'une ligne garde sa hauteur au pixel près, mesurée sur cinq espaces à 1024, 1440 et 390 px. Dans l'inspecteur, « déclarée » n'est plus coupé à côté du select Priorité, et la carte de passage d'une colonne terminale ne réserve plus de place à ses blocs vides.
  - **Barre d'état sous 900 px** : `#center` est borné (`min-height: 0`) — en colonne, le min-height `auto` d'un item flex le faisait grossir avec une liste longue et son contenu peignait par-dessus la barre d'état, qui ne recevait plus les clics (sélecteur de thème inatteignable à 390 px, tiroir ouvert). Désormais c'est `#canvas` qui défile. Défaut préexistant, révélé par les nouvelles cartes de test.
  - **Frise** : la date se lit « 26 oct. 07:47:43 » dans une colonne à sa largeur ; l'ISO complet est dans `<time datetime>` et au survol, et le type ne se répète plus quand l'événement n'a pas de résumé.
## [3.67.0] - 2026-10-26

- fix(missions): l'identité d'une session de fond se lit après son lancement (#966). Constat : `claude --bg` ignore `--session-id`, et l'environnement passé au client n'atteint que le démon des sessions de fond, dont toutes héritent ; workers et relecteur portaient donc le `GRIMOIRE_SESSION_ID` du premier worker lancé, et le lanceur inscrivait au journal d'autorité, au ledger (`task.session_launched`) et au sceau du relecteur un uuid qui n'était la session de personne — le reçu du relecteur était refusé (« autre session ») et pouvait même être classé forgé. Correctif : le lanceur et le relecteur lisent l'id court imprimé par `--bg`, résolvent le vrai `sessionId` dans `claude agents --json` (recoupé avec le nom et le dossier de lancement quand l'hôte les donne), puis seulement l'inscrivent, le notent et le scellent. Le mandat est relu avant de lancer : plafond du jour atteint ou journal illisible, rien n'est lancé. Identité introuvable ou inscription refusée : la session est arrêtée (`claude stop`), la tentative notée au ledger et comptée au plafond du jour et au disjoncteur (sauf refus de plafond, qui révoque déjà le mandat) ; un relecteur refusé n'est pas relancé, la tâche va à l'humain. Un arrêt non confirmé ne rend pas la tâche : elle reste tenue, la session est nommée au ledger, et aucun relecteur n'est relancé avant que l'arrêt soit confirmé. `session_env` ne porte plus que `GRIMOIRE_UNATTENDED=1`, et sous ce drapeau `current_session` préfère la variable de l'hôte (`CLAUDE_CODE_SESSION_ID`) à un `GRIMOIRE_SESSION_ID` hérité du démon. Limites documentées : l'identité de session n'est pas un secret (un worker peut lire celle du relecteur dans `claude agents`, le verrou 7 ne tient que contre un worker honnête), une reprise `--resume` n'est ni résolue ni réinscrite, et un tick qui meurt entre `--bg` et sa note laisse une session orpheline.

- feat(cockpit): la Console du dock devient un vrai champ de saisie, les entrées mortes disparaissent et plus aucun état ne fuit d'un espace à l'autre (P06, `GAO-p06-coque-c-001`)
  - **Console** : la saisie passe par un `<input>` libellé que la coque possède, sous l'onglet Console (« ` » y place le curseur de partout) ; Tab sort du dock au lieu de lancer une complétion, Échap vide le champ puis rend le focus d'où l'on venait, ↑ / ↓ rappellent les commandes lancées, les sous-commandes ouvertes sont proposées sous le champ, et les raccourcis d'une lettre se taisent pendant la saisie. Le tampon simulé de Source (écouteur sur toute la page, sourd tant que Source n'avait pas été ouvert) est retiré ; la palette et la Console partagent la même exécution, code de sortie compris.
  - **Entrées mortes** : `workspace_exec.ALLOWED` perd `blueprint list` (inexistante) et `task board` (un groupe), et `config` (un groupe dont le positionnel ouvrait aussi `config edit`) devient `config show` ; `doctor` accepte `-o/--output` au lieu de `--check-paths`/`--json`, qui n'ont jamais existé. Les commandes équivalentes citées par Blueprints (`blueprint simulate`, `blueprint list`, `validate`/`compile` sur un identifiant au lieu du fichier), Travail (`task board`) et Fichiers (`doctor --check-paths`) sont corrigées ; un test parcourt l'arbre de la CLI pour chaque entrée et chaque commande citée par les modules.
  - **Aucun état qui fuit** : après le démontage d'un espace, `ctx.dock`, `ctx.docbar`, `ctx.setItem`, `ctx.openPanel` et `ctx.rail` n'ont plus d'effet, et Source teste `ctx.signal.aborted` après chaque lecture : une réponse en retard ne pose plus la pastille, l'URL ou l'inspecteur de Fichiers sur Travail. Les onglets Problèmes, Traces et Timeline du dock repartent vides à chaque changement d'espace ; la Console garde son historique.
  - **Rail et bandeau** : le bouton Bibliothèque n'est visible que dans Blueprints, désactivé avec sa raison hors du niveau Workflow ; « Créer un override » n'apparaît que sur un fichier du kit dont le serveur a calculé l'override (pack de preuve, projection et fichier du standard ont leur phrase, sans bouton), et le gabarit « (à calculer par le serveur) » n'existe plus. Le dock est un conteneur : à 1024 px, ses onglets tiennent sur une ligne.
- fix(missions): l'unité systemd du minuteur du tick (`orchestrate install-timer`) porte `KillMode=process`. En `oneshot` avec le `control-group` par défaut, systemd tuait à la fin du tick tout ce qu'il avait lancé, dont le démon de `claude --bg` : la session worker mourait à sa naissance, la tâche restait réclamée sans session jusqu'à la récolte de son bail. Vu au premier tick réel sur le dépôt du kit (R14, aucun transcript). Un minuteur déjà posé se répare en relançant `grimoire orchestrate install-timer`.
- feat(cockpit): menus, infobulles et palette sur Popover API et `<dialog>`, « Annuler » plutôt que confirmer pour les gestes réversibles, mise en page des panneaux par requêtes de conteneur (P55, P57, P59 ; `GAO-p55-menus-i-001`, `GAO-p57-annuler-001`, `GAO-p59-mise-en-001`)
  - **Surcouches natives (P55)** : la palette ⌘K (et le sélecteur de portée) et l'aide « ? » sont des `<dialog>` modaux (`closedby="any"`) ; le menu « … » partagé (`moreMenu`) est un `popover="auto"` commandé par son bouton et ancré dessous en CSS (`anchor-name`), les infobulles du glossaire des `popover="manual"`. Échap, le clic sur le fond ou à côté, le piège du focus et le retour du focus au déclencheur sont ceux du navigateur : `setBackgroundInert`, le piège de Tab de l'aide, `closeMenuOutside`, l'Échap du menu et de la palette et le focus rendu à la main sont retirés (garde : `tests/unit/test_workspace_overlays.py`). Sans Popover API, le menu s'ouvre et se ferme au bouton (repli `hidden`), sans fermeture par Échap ni clic à côté.
  - **« Annuler » (P57)** : un toast partagé (`undoToast`, région `role="status"`, 10 s, en pause au survol et au focus, horloge injectable, fin du délai déclenchable par `GrimoireWorkspace.toast.expire()`) suit chaque geste réversible, qui part sans confirmation et s'annule par la route inverse existante : bloquer une tâche prête (`move` vers `ready`) ou remettre en revue une tâche en cours (`move` vers `running`), retirer une entrée mémoire ou la rendre au service (geste inverse, aperçu puis application), retirer un projet du registre depuis Configuration › Projets ou la Flotte (nouvelle action de ligne « Retirer de la flotte ») quand « Ajouter » le rend tel quel (`POST /api/projects/add`). Un geste irréversible n'a jamais de toast : accepter une tâche demande désormais une confirmation sur place, supprimer une entrée mémoire, purger le registre et retirer une entrée au nom choisi à la main ou au dossier disparu gardent la leur.
  - **Conteneurs (P59)** : `#middle` et `#canvas` sont des conteneurs de requête ; les règles de mise en page des espaces passent de `@media (max-width…)` à `@container canvas (…)`, et le détail (l'ancien inspecteur) se pose sous la liste quand `#middle` mesure entre 600 et 1180 px (hors atelier), au lieu d'un tiroir ou d'une colonne écrasée. Le seuil de 900 px de la coque (tiroirs, barre basse) ne décide plus que de la coque.
  - **Barres de la coque (P59)** : la barre d'application et la barre d'état sont aussi des conteneurs ; sous 1280 px de barre, les onglets non choisis gardent leur icône (nom accessible et `title` inchangés), le pouls et la palette la leur, le pied masque ses éléments secondaires ; rien n'y passe plus à la ligne, le badge d'« À traiter » n'est plus coupé, le nom du projet s'élide avec son `title` (défaut vu à 1024 px). Sur les cartes du tableau de Travail, les libellés d'action (« → En cours », « Bloquer… ») ne se cassent plus : faute de place, ce sont les boutons qui passent à la ligne.
  - **Changements de comportement** : le menu de l'inspecteur de tâche se referme sur un clic à côté même avec une raison saisie (la saisie reste dans le champ) ; « Retirer » une entrée réversible du registre ne passe plus par l'aperçu ; « Réaliser : → Acceptée » demande une confirmation.
- feat(cockpit): les lignes de liste — actions au survol et menu « … », clavier d'abord, squelettes à la forme de chaque liste (P56, P58, P60 ; `GAO-p56-actions-001`, `GAO-p58-navigat-001`, `GAO-p60-squelet-001`)
  - **Clavier d'abord (P58)** : un seul contrôleur de liste dans la coque (`listNav` de `web/workspace/shell.js`), branché dans À traiter (son code maison retiré), Travail (cartes du tableau, lignes de la liste, Sessions), la Flotte, Savoir › Recherche et Configuration › Projets. j et k passent à la ligne suivante ou précédente, Entrée lance l'action principale de la ligne tenue ; aucun raccourci pendant une saisie (champ, zone de texte, liste déroulante, `contenteditable`), la palette, l'aide ou un menu « … » ouvert. Dans la Flotte et les Projets, la ligne active porte `aria-current` ; dans Travail, j et k ne font que déplacer le focus, Entrée ouvre la carte. L'aide « ? » annonce J / K pour toutes les listes.
  - **Actions au survol (P56)** : les actions secondaires d'une ligne (copier l'identifiant, la commande ou le lien, voir la chronologie, voir le Travail d'un projet) apparaissent au survol, au focus clavier et sur la ligne active, restent dans l'ordre de tabulation, et sont toutes dans le menu « … » de la ligne ; sur écran tactile (`hover: none`, `pointer: coarse`) elles sont visibles sans survol. Le menu « … » de l'inspecteur de tâche (P11) devient le composant de la coque (`moreMenu`) que les lignes réutilisent : un seul écouteur de clic à côté, un seul menu ouvert à la fois.
  - **Squelettes (P60)** : le squelette de chargement prend la forme de la liste qui vient — colonnes de cartes pour le tableau de Travail, lignes de table pour la Flotte, Sessions, Projets et le Journal, résultats pour Savoir, tuiles de chiffres pour la fiche d'un projet et la Santé, tuiles et lignes à pastille pour À traiter (`ctx.loading(libellé, lignes, forme)`). Les chiffres des tuiles ne s'animent toujours que sur un changement réel, jamais en boucle ni sous `prefers-reduced-motion`.
  - **Changement de comportement** : la liste de Travail gagne une colonne d'actions, la dernière colonne de la Flotte s'élargit pour porter les actions avant le chevron d'ouverture.
- fix(missions): le tick renouvelle le bail des workers dont la session vit encore (#961). Le bail n'était écrit qu'au claim, et rien ne le renouvelait pour une session lancée par le tick : au-delà de 20 min de travail, la récolte garait la tâche en `waiting(human)` (`resume_unfenced`), alors que son worker tournait. Avant la récolte, `renew_live_workers` lit `claude agents --json` et renouvelle chaque tâche tenue dont la session de fond lancée pour **ce** claim (`lease_epoch` noté au lancement, identifiant et nom `grimoire-<id>`) est `busy`. Une session `idle` (tour fini), absente, interactive ou d'un claim antérieur, ou une lecture en échec, ne renouvelle rien : la récolte juge comme avant, et le plafond dur du claim tient. Un battement en erreur ne casse pas le tick, il le signale en attention. La consigne du worker lui demande aussi de passer la tâche en revue après sa PR (le relecteur ne prend que `needs_verification` : le premier worker autonome, R49, avait ouvert #964 et laissé sa tâche `claimed`) et de trancher lui-même, en le notant, un choix réversible qui reste dans le périmètre de ses critères.

- feat(cockpit): résumé « depuis ta dernière visite » en tête d'À traiter (P53, `GAO-p53-r-sum-d-001`)
  - **Résumé** : une carte refermable liste ce qui a été livré (transition vers `closed`), refusé (refus de gate `task.gate.refused`, regroupés par tâche) ou laissé en attente (tâche aujourd'hui `waiting` ou `blocked`, entrée depuis la visite, avec la question ou la raison) depuis la dernière visite, chaque ligne liée à sa tâche dans Travail ; en portée « Tous les projets », chaque ligne nomme son projet. Sans visite précédente, aucun résumé n'est affiché ni demandé.
  - **Lecture seule** : `GET /api/workspace/attention/since?since=<ISO>` (`src/grimoire/tools/workspace_since.py`) ne retient rien ; un `since` illisible est un 400, les instants sont comparés en UTC conscient. Le résumé se charge après le premier rendu d'À traiter, jamais devant lui. Les PR (P48) ne sont pas encore lues.
  - **Visite** : gardée par le navigateur avec les préférences de la coque (`grimoire.workspace.<projet>`, champ `visit:<portée>`). Elle prend l'instant d'ouverture et n'avance que quand le résumé a été vu : « Refermer », ou sortie d'À traiter vers un autre écran ; un rechargement ne l'efface pas.
- feat(steward): l'intendant du projet, étape 1 — état des lieux en lecture seule dont chaque verdict porte une preuve vérifiée par du code (#876, lot 5 « intake et planner », recentré). Le tri manuel du kanban (171 tâches ouvertes, dont 64 déjà livrées, 5 obsolètes ou en doublon, 4 PR abandonnées) a montré qu'un LLM juge avec aplomb et se trompe (identifiants homonymes dans les commits, notes périmées) : la carte est calculée, le jugement est proposé, la preuve est vérifiée.
  - **`grimoire steward map [--json]`** : carte déterministe, sans LLM, schéma `grimoire-steward-map/v1` (aussi dans `_grimoire-output/steward/map.json`). Tâches du Mission Ledger (état, critères, dernière activité, claim, session et sa vivacité calculée par `missions.liveness`, issues liées) ; branches locales et du remote de la cible avec avance/retard, dernier commit, orpheline (`task/<id>` sans tâche vivante) et **fusion par comparaison d'arbres** : ancêtre, arbre identique à un commit de la cible (fusion squash ou rebase), tête d'une PR fusionnée, ou `git merge-tree` qui ne changerait rien ; conflit calculé (`changelog_seul` ou `autre`) ; worktrees ; PR ouvertes (CI, `mergeable`, `mergeStateStatus`, auteur, dernière activité, tâches et sessions liées) et issues ouvertes. Signaux : PR sans activité depuis `--inactivite-heures` (48 par défaut) dont aucune session liée n'est vivante, tâche ouverte dont l'issue liée est fermée, tâche ouverte citée par un commit de la cible, conflit limité au CHANGELOG ou non, branche orpheline, branche fusionnée sans être ancêtre, worktree élagable. Sans `gh`, hors ligne ou sans cible : `"mesure": false` et la raison, champs « non mesuré », jamais d'échec.
  - **`grimoire steward verdicts --file <verdicts.json>`** : valide un fichier `grimoire-steward-verdicts/v1` (verdicts `a_faire`, `livree`, `obsolete`, `doublon`, `abandonnee`, `conflit_simple`, `conflit_semantique`, `humain`) en revérifiant chaque preuve typée : `commit` (sur la cible), `fichier` (chemin et motif sur la cible, ou absents), `issue`/`pr` (état lu par `gh`, commit de fusion sur la cible), `inactivite` (dernière activité et vivacité recalculées), `doublon_de` (l'autre élément existe), `note` (jamais suffisante seule pour `livree`, `obsolete`, `doublon` ou `abandonnee`). Une preuve fausse, un élément introuvable ou non mesurable rejette le verdict avec sa raison (code 1) ; `conflit_simple`/`conflit_semantique` sont recalculés. Écrit `validation.json`.
  - **`grimoire steward report [--verdicts …] [--json]`** : carte + verdicts établis et rejetés, regroupés par action proposée (fermer avec preuve, reprendre une PR abandonnée, réaligner, à faire par thème, à l'humain) et signaux sans verdict ; écrit `report.json` et `report.txt`. Aucune action.
  - **Lecture seule stricte** : seules des écritures sous `_grimoire-output/steward/`, dossier qui s'ignore lui-même (`.gitignore` `*`, `git status` inchangé) ; `git merge-tree` écrit ses arbres dans un magasin d'objets jetable sous ce dossier (`GIT_OBJECT_DIRECTORY`, le vrai magasin en alternate), retiré à la sortie ; ledger lu sous `suspended_state_writes` (aucun instantané écrit). Tout appel `git`/`gh` passe par un exécuteur injectable ; un test relève chaque fichier du projet, du dépôt git et de la remote avant et après les trois commandes, et les doubles refusent toute sous-commande qui écrit.
  - **Skill `grimoire-steward`** (livrée avec les skills d'hôte, `evals/skills/grimoire-steward/evals.json`) : comment produire `verdicts.json` depuis la carte — lire le code de la cible plutôt que les titres, se méfier des identifiants homonymes et des notes périmées, « en cas de doute entre livrée et à faire, à faire », toujours une preuve typée.
  - Enregistrement hors de `cli/app.py` (plafond de taille) : `cli/_lazy_extra.py`, fusionné par `LazyTyperGroup.configure`. Référence : `docs/reference/cli/intendant.md`. Tests : `tests/unit/steward/`.
  - **Durcissement après relecture adversariale (un faux « livré » fait perdre du travail : dans le doute, on rejette)** : une PR fusionnée ne fusionne sa branche que si sa base est la cible et son commit de fusion sur la cible (une PR empilée ne compte plus) ; une branche sans commit d'avance est `vide`, et une branche dont l'arbre est celui d'un commit ancien de la cible est `etat_anterieur`, ni l'une ni l'autre `fusionnee` ; **une preuve doit porter sur l'élément jugé** : `livree` d'une branche exige sa fusion recalculée, `livree` d'une tâche ou d'une issue exige un commit dont `message_contient` la cite, une PR fusionnée dans la cible qui la nomme ou l'issue elle-même fermée comme terminée (`stateReason` lu : `not_planned`/`duplicate` ne livrent rien, un numéro de PR doit être fusionné) ; `fichier absent` n'établit rien si le chemin n'a jamais existé dans l'historique de la cible, un dossier n'est pas un fichier ; `doublon_de` refuse un cycle, une cible fermée ou annulée, ou jugée doublon, abandonnée ou obsolète dans le même fichier ; `abandonnee` exige des sessions liées toutes terminées (« inconnu » n'est pas « morte »), ou, sans tâche liée, aucun travail non commité et un dernier commit au-delà du seuil ; `livree`, `obsolete` et `abandonnee` sont refusés sur une branche dont le worktree est sale (signal `worktree_sale`, `git status` lu sans verrou optionnel) ; noms de branche confinés à `refs/heads/` et `refs/remotes/<remote de la cible>/` (`main~1`, `a..b`, `HEAD@{1}` refusés), un SHA ne se résout jamais par une référence homonyme ; listes `gh` et journal de la cible lus jusqu'à leur limite marqués `partielle` ou dans `troncatures` ; magasin d'objets propre à chaque exécution (`mkdtemp`) ; conflit d'une PR calculé sur sa seule tête (`conflit.sha`), « non mesuré » si elle n'est pas locale ; le rapport montre les contradictions (signal qui contredit un verdict établi) au lieu de les masquer.
  - **Limites** : le jugement reste celui d'un LLM, seule la preuve citée est vérifiée (une preuve vraie peut appuyer un verdict faux) ; une fusion squash d'une branche en retard que la cible a retouchée n'est vue fusionnée qu'avec une PR fusionnée de même tête (échec fermé) ; étape 1 = proposition, aucune tâche fermée, aucune PR touchée, rien d'écrit au ledger ni sur GitHub.

## [3.66.1] - 2026-10-26

- fix(missions): `missions/finition.py` lit `project-context.yaml` avec ruamel.yaml, la dépendance du kit, au lieu de PyYAML qui n'est pas installé chez l'utilisateur. La 3.66.0 n'est jamais partie sur PyPI : son test de fumée (`up --prove` sur une installation vierge) s'arrêtait sur « No module named 'yaml' ». La CI ne le voyait pas, PyYAML y étant installé par les dépendances de développement. Garde `tests/unit/test_no_unguarded_pyyaml.py` : tout `import yaml` de `src/` doit avoir un repli sur `ImportError`.
- feat(cockpit): PR à revoir dans À traiter, et l'écran de revue, sans fusion depuis le cockpit (P48)
  - **Lecture** : `GET /api/workspace/reviews` et une section « PR à revoir » dans `GET /api/workspace/attention` rendent les PR ouvertes du dépôt (`ci.repository` de la politique d'orchestration, sinon `origin`), leurs checks comptés (verts, échecs nommés, ignorés, en cours) et la tâche du ledger liée (branche `task/<id>`, sinon un identifiant cité dans la branche, le titre ou le corps).
  - **Jamais bloquante** : une seule lecture `gh pr list --state open` par dépôt, bornée à 15 s, partagée en vol et gardée 60 s ; la route d'attention ne l'attend que 0,3 s puis dit « lecture en cours » et l'écran relit. Un `gh` absent, non authentifié ou lent est un « état des PR inconnu » avec sa raison, jamais une liste vide ; un projet sans remote n'affiche rien.
  - **Écran de revue** (`web/workspace/spaces/revue.js`, `#a-traiter?view=revue&pr=…`) : la liste des PR les plus prêtes d'abord, la PR choisie (origine, quatre tuiles de CI, checks en échec avec leur journal, fichiers touchés, CHANGELOG, autres PR sur les mêmes fichiers, issues fermées) et les trois questions avant de fusionner.
  - **Aucune fusion** : le seul geste est un lien qui ouvre la PR sur GitHub dans un nouvel onglet ; aucune route d'écriture ajoutée.
  - **Correctif** : `ci_repository` reconnaît un remote `origin` absent au code de sortie de git, plus seulement au message anglais (un poste en français prenait « aucun remote » pour « état inconnu »).
- feat(cockpit): créer une tâche depuis Travail et entretenir le registre des projets depuis Configuration (P35, `GAO-p35-cr-er-u-001`)
  - **Nouvelle tâche** : le bouton primaire de l'en-tête de Travail (maquette Travail) ouvre un formulaire sur place (titre, critères d'acceptation un par ligne, description) envoyé à `POST /api/workspace/tasks/add`, qui appelle `TaskService.add`, la même entrée que `grimoire task add` et l'outil MCP `task_add`. La tâche créée s'annonce sur place avec un lien qui l'ouvre ; un formulaire incomplet est refusé sur place. En portée « Tous les projets » ou sur un projet qu'on ne fait que regarder, le bouton est désactivé et la raison est écrite (garde P02 : la route n'est câblée que pour le projet servi, sans dérogation).
  - **Configuration › Projets** (`web/workspace/spaces/registre.js`) : le registre du cockpit en tableau (projet, chemin, état), « Scanner un dossier… » (le scan propose, chaque candidat s'ajoute d'un clic), « Ajouter un projet existant… », « Retirer… » sur chaque ligne et « Purger les entrées mortes… », chacun par un aperçu puis une confirmation dans la page. Retirer ou purger ne touche jamais au dossier d'un projet, seulement à son entrée du registre, et l'écran le dit ; le projet servi ne se retire pas d'ici. **Changement de comportement** : Configuration gagne la rubrique « Projets », avant « Préférences ».
  - **Routes** : `POST /api/projects/remove` et `POST /api/projects/prune` rejoignent `add` et `scan` dans `src/grimoire/tools/cockpit_registry_routes.py`, derrière les gardes communes des écritures (jeton de session, `Origin`, `Sec-Fetch-Site`, JSON). Sans `confirm: true` elles rendent un aperçu et n'écrivent rien ; la purge confirmée ne retire que les entrées que l'aperçu a montrées (`slugs`) ; un slug sert de clé de recherche, jamais de chemin. Retirer le projet servi est refusé (409). `forget_selection_if_gone` passe dans `project_registry` (partagé avec `grimoire cockpit prune`).

## [3.66.0] - 2026-10-25

- fix(missions): une ligne illisible du journal que couvre l'instantané du rejeu reste signalée (K12 x K13). L'instantané, écrit quand un rejeu dépasse 50 ms, ne portait pas les lignes illisibles : le rejeu suivant ne relisait que la suite et `unreadable_lines()` revenait vide, l'alerte « ligne(s) illisible(s) » du tick disparaissait. Vu en CI (runner lent) sur `test_crash_bench` scénario H ; test déterministe dans `tests/unit/test_ledger_snapshot.py`.
- fix(cockpit): correctifs de fusion sans entrée propre, repris ici — copie de l'état d'un service par `copy.copy`, hors du garde d'écriture atomique (P22, d48b0a3d) ; la ligne « Bloquée » du board passe par `stateIcon` (P07, ead06ec9) ; `list_flow_runs` annote son retour pour `mypy --strict` (P07, ebbb28dc).
- feat(cockpit): Flotte › Données, la carte des services mémoire de la machine et de qui les partage, sans jamais rien démarrer (P26, `GAO-p26-carte-q-001`)
  - **Lecture** : `GET /api/workspace/data/machine` rend, pour Weaviate, Qdrant, Neo4j, Redis et Ollama, l'état, la version annoncée par le service (sinon l'étiquette de l'image), le conteneur, le Compose propriétaire et la politique de redémarrage lus par `docker inspect`, l'extra Python importable et les projets du registre qui l'utilisent ; puis les partages de collection, de base Neo4j et de Redis entre projets.
  - **Huit états distincts** : en ligne, démarrage, incomplet (extra absent, `GRIMOIRE_NEO4J_PASSWORD` absente), emprunté (le conteneur appartient au Compose d'un projet qui ne l'utilise pas), en échec, arrêté, absent, inconnu ; Docker absent ou injoignable rend « inconnu » avec son motif, jamais « arrêté ».
  - **Sans démarrage ni secret** : `docker ps` et `docker inspect` seulement, sondes bornées et injectables ; du mot de passe Neo4j seule la présence sort, et `Config.Env` des conteneurs n'est jamais lu.
  - **Interface** : segmenté Projets / Données à côté du titre de la Flotte (`#flotte?vue=donnees`), lu au clic seulement ; chaque service porte un lien vers sa console native et « Copier la commande », puis la matrice « Qui partage quoi » et ce que voit le cockpit.
- feat(cockpit): reçu de rappel visible et rejouable dans l'inspecteur de tâche et dans Savoir › Rappel (P27, `GAO-p27-re-u-de-001`)
  - **Mémoire servie** : l'inspecteur d'une tâche (espace Travail, après la session) montre chaque lecture mémoire servie à la tâche : requête, heure, mode, puis les souvenirs avec rang, score, portée (projet, non attribuée, autre projet au contenu masqué), état d'aujourd'hui (`live`, `superseded`, `retracted`, `orphan`, introuvable) et fraîcheur. Sans lecture, la phrase le dit (« aucune lecture mémoire enregistrée pour cette tâche ») ; une lecture ratée porte son motif.
  - **Rejouer maintenant** : relance la même lecture (même mode, mêmes filtres, rappel de tâche compris) sur la mémoire actuelle et rend le différentiel : apparue, remplacée ou retirée, déclassée, disparue, montée. Une requête libre se rejoue de la même façon dans Savoir › Rappel (premier passage, puis comparaison). Aucun bouton quand la requête n'a pas été gardée : la raison est écrite à sa place.
  - **Savoir › Rappel** (`web/workspace/spaces/rappel.js`) : le reçu en grand (tableau rang, souvenir, lexical et sémantique en lecture fusionnée, score, état), « Rejouer et comparer », et dans l'inspecteur ce que le reçu révèle (servis, remplacés, retirés, hypothèses). **Changement de comportement** : le segmenté de Savoir devient « Santé et carte | Recherche | Rappel | Journal ».
  - **Lecture seule** : `GET /api/workspace/tasks/<id>/receipts`, `GET /api/workspace/memory/recall` et `GET /api/workspace/memory/recall/replay` (`src/grimoire/tools/workspace_recall.py`) relisent le journal P13 et rejouent par un manager qui n'écrit aucun reçu ; aucune route d'écriture. **Correctif** : ouvrir l'inspecteur d'une tâche écrivait un reçu de rappel (surface `api`) à chaque clic ; il n'en écrit plus.
  - **Le reçu garde la requête d'une tâche** : une lecture rattachée à une tâche (`task_ref`) garde désormais le texte de sa requête dans `memory-reads.jsonl`, secrets caviardés et borné à 300 caractères ; hors tâche, seule sa longueur reste. Le rappel de tâche expose `task_recall_entries` et `task_recall_query` pour être rejoué à l'identique.

- feat(cockpit): inspecteur de tâche de Travail, les gates et leur remède avant toute transition (P11, `GAO-p11-inspect-001`)
  - **Gates d'abord** : l'inspecteur liste chaque passage déclaré depuis la colonne (`next_moves_require`), chaque condition verte ou rouge avec sa raison et son remède, puis les boutons de transition ; un refus se lit sur place, preuve par preuve, remède compris.
  - **Dernier refus** : le dernier `task.gate.refused` du TraceLedger s'affiche (date, acteur, gate, preuves manquantes) et date les conditions qu'il a nommées (« déjà refusée le … »).
  - **Reçu de revue** : la clôture d'une tâche relue montre aussi le reçu de revue que `TaskService.gate` exige ; la carte comptait 1/2 un passage que la clôture refusait ensuite.
  - **Routage** : la carte et l'inspecteur portent la classe V0/V1/V2, le modèle recommandé (« modèle de session » en V2) et le coût, sur chaque carte backlog compris ; un coût non mesuré s'écrit « inconnu », jamais 0.
  - **Ordre** : gates, transition, critères, preuves, session ; « Annuler la tâche » ne vit plus que dans le menu « ⋯ » de l'en-tête (raison exigée, Échap pour fermer).
  - **Lecture** : `GET /api/workspace/tasks/<id>` rend `gate_checks` et `last_gate_refusal`, les cartes `recommended_model` et `model_tier` (`tools/workspace_task_gates.py`) ; aucune route d'écriture nouvelle.
- feat(cockpit): corriger une entrée mémoire depuis son panneau de détail, avec aperçu avant/après et annulation depuis son historique (P34, `GAO-p34-correct-001`)
  - **Gestes** : « Corriger » (étiquettes, source, fusion, retrait), « Marquer remplacée… » et « Rendre au service… » s'ouvrent dans le panneau de détail de Savoir › Recherche, jamais dans une fenêtre native. « Rattacher une source » accepte une tâche, une session, un fichier et sa ligne (relatif au projet, haché pour la fraîcheur) ou un commit. Fusionner met à jour la survivante (texte, union des étiquettes) puis marque les autres remplacées par elle. Une raison est exigée pour remplacer, retirer et fusionner.
  - **Même route, même entrée** : `POST /api/memory` reçoit l'action `gesture` (aucune nouvelle route), donc les mêmes gardes que la suppression (Origin, Sec-Fetch-Site, JSON, projet servi seulement, 403 ailleurs, même pour un aperçu). La logique vit dans `src/grimoire/tools/workspace_memory_gestures.py` ; chaque geste passe par `MemoryManager` (`run_gesture` pour étiquettes, retrait et rétablissement, `supersede`, `update`) et écrit le journal des écritures (`_grimoire-output/memory-changes.jsonl`). `MemoryManager.update` et `supersede` acceptent `journal_extra` (geste, renvoi vers l'état d'avant).
  - **Aperçu, puis application** : `mode: preview` rend l'avant/après de chaque champ et la version des entrées en jeu (empreinte du texte, des étiquettes et des métadonnées) ; `mode: apply` exige `confirm` et cette version. Une entrée changée depuis l'aperçu est refusée (409) et l'aperçu recalculé s'affiche avec le refus.
  - **Historique et annulation** : le panneau liste les écritures de l'entrée (journal P14) ; étiquettes, retrait, rétablissement, remplacement, fusion et source portent « Annuler… », qui passe par le même aperçu. L'état d'avant d'une écriture qui touche texte ou métadonnées est déposé dans `_grimoire-output/memory-undo.jsonl` (le journal, lui, ne porte jamais de texte).
  - **Projections et autres projets** : une projection montre « Corriger la source » (fichier ou tâche d'origine, commande de régénération) au lieu des gestes d'édition ; hors du projet servi, les gestes sont absents et la raison est écrite. **Changement de comportement** : la ligne « Journal » de la fiche devient la section « Historique », et « Supprimer… » devient un bouton discret, comme la maquette.
- feat(cockpit): santé de la mémoire et file d'hygiène dans Savoir › Santé et carte, alertes poussées dans À traiter (P25, `GAO-p25-sant-m-001`)
  - **Route `GET /api/workspace/memory/quality`** (`src/grimoire/tools/workspace_hygiene.py`) : par projet, textes distincts, utilisateurs (identités de test repérées), taux de provenance des souvenirs, part de projections, dérive store / index lexical / graphe comptée sur la collection du projet (`Neo4jMemoryGraph.count_memories_in`, plus le compte global d'une base partagée), résultats de `memory lint`, supersessions orphelines, motifs transverses à reconfirmer, fraîcheur du graphe de code. Chaque mesure devient un critère en échec, réussi ou inconnu ; un échec met le projet en alerte. Lecture seule : un store jamais créé n'est pas ouvert, une mesure impossible est « inconnue » avec son motif, le lint au-delà de 1 000 entrées se lance à la main.
  - **Hygiène typée dans À traiter** : `memory_quality` (store contaminé : un même texte au moins trois fois, identités de test), `memory_contradiction`, `memory_orphan_supersession`, `memory_parity_drift`, `codegraph_stale` et `shared_aging` rejoignent la section « À entretenir » de `GET /api/workspace/attention`, chacun avec son geste (`ref.gesture` : libellé, commande, aperçu). La route d'attention reprend un rapport de moins de dix minutes et ne touche jamais le réseau : un backend serveur jamais sondé n'est pas ouvert. Le store de la Forge (25 « python is the best language » d'alice) sort en alerte.
  - **Écran Santé et carte** (`web/workspace/spaces/sante.js`) : carte « Santé de la mémoire » (chaque critère avec sa mesure) et « File d'hygiène » ; chaque geste ouvre un aperçu (ce qui sera touché, pourquoi) puis donne sa commande avec Copier et une confirmation sur place, l'export toujours avant la purge. La carte par hall (P26) est dite non servie, rien n'est dessiné à sa place. **Changement de comportement** : le segmenté de Savoir devient « Santé et carte | Recherche | Journal » (la rubrique Mémoire s'appelle Recherche) ; l'entrée de Savoir reste la Recherche. L'action d'une alerte mémoire dans À traiter ouvre la Santé sur le constat.
- feat(orchestrate): lot 4 de #876 — merger (verrou 12) : une file série fusionne les tâches relues READY dont la PR est ouverte, puis les accepte, sous mandat seulement ; sinon une question non bloquante est posée et l'humain fait le geste lui-même. Désactivé par défaut (`orchestrator.roles.merger.enabled`). Un projet qui n'active aucun rôle autonome garde le kit de main, à l'identique.
  - **File** (`src/grimoire/missions/merger.py`) : étape du tick après le relecteur. Entrent les tâches lancées, en revue, dont la dernière relecture est jugée `READY`, dans l'ordre des jugements ; le merger n'ouvre pas de PR. Une fusion à la fois ; seules les `realign_max` premières tâches (défaut 1) sont réalignées ; une tête refusée ou en attente de l'humain ne bloque pas les suivantes.
  - **GitHub fait foi** : au lancement de la tâche, avant le worker, le lanceur fige au journal d'autorité le dépôt GitHub (`owner/repo`, résolu comme pour les créneaux CI) et l'URL de `origin`. Chaque `gh` du merger vise ce dépôt (`-R`) ; sans dépôt figé, rien n'est fusionné sous mandat. La tête et la base de la PR viennent de `gh pr view`, et ses fichiers de l'API REST (`gh api …/pulls/<n>/files --paginate`, `filename` **et** `previous_filename` : `gh pr view --json files` tait l'ancien nom d'un renommage, cli/cli#14575), jamais de refs locales. Les objets sont récupérés à refspec explicite vers `refs/grimoire/tick/…`, sans sous-module, depuis l'URL de `origin` égale à celle figée, et doivent valoir ce que GitHub annonce. Sinon : attente ou refus. Une PR d'un fork est refusée.
  - **READY scellé** (`missions/review_seal.py`) : au lancement du relecteur, la tête de `task/<id>` est notée au journal d'autorité. Au jugement, sont scellés la même tête (une branche qui a bougé pendant la relecture n'est pas scellée), son arbre git, un worktree propre et sur cette tête, et les empreintes du reçu et du pack, avec une copie au ledger. La tête GitHub de la PR doit être la tête scellée, ou un commit de réalignement noté au même journal par le merger. `accept_task` sous mandat (lots 1 et 3 compris) compare au même sceau.
  - **Gardes, dans l'ordre** : worktree `task/<id>` du même dépôt ; PR ouverte vers la cible ; tête couverte par le sceau (tête relue pas encore poussée : attente ; sinon nouvelle relecture, `task.merge_requeued`) ; CI verte et stable — les checks requis (`--required`), sinon, seulement si gh dit qu'aucun n'est requis, tous les checks hors `ignored_checks` (défaut `codecov/*`), au moins un `pass`, le même vert au tick précédent. CI rouge : retour au worker comme un `NOT-READY`. Branche en retard : réalignement **en plomberie** (`merge-tree`, `commit-tree -p <tête GitHub> -p <base GitHub>`), sans toucher au worktree, poussé sans forcer. Conflit limité à `CHANGELOG.md` : union des deux côtés, en octets (les fins de ligne du fichier, CRLF compris, sont gardées, même sous Windows), refusée si une ligne de la cible manque ; tout autre conflit rend la tâche au worker. Au cycle `max_cycles` (2), `waiting(human)`.
  - **Fusion** : `gh pr merge <n> --squash --match-head-commit <tête>` (ni `--admin` ni `--auto`), seulement si `require_authority` l'autorise sous `acting_as_orchestrator()`, si aucune catégorie de la tâche n'est gardée à l'humain, si aucun fichier de la PR n'est dans la courte liste des chemins qui escaladent, comparée en `casefold()`, et si le reçu est attesté au journal d'autorité. La liste couvre le code des gardes (`src/grimoire/missions/*`, `hosts/decisions/*`, `core/*` — qui porte `safe_git`, `fsio` et `standard_checks` —…), la politique (`_grimoire/standard/*`), les workflows et hooks (`.github/*`, `.husky/*`…), le packaging (`pyproject.toml`…), `.gitattributes`, `conftest.py`, la configuration d'outils qui exécutent du code chez l'utilisateur (`.claude/*`, `.mcp.json`, `.vscode/*`, `.pre-commit-config.yaml`) et les fichiers compilés ; la clé `protected_paths` de la politique s'y ajoute. Sinon, et quand le dépôt refuse la fusion, une question non bloquante « fusionner ? » est posée une fois (ce qu'elle cite de la PR est échappé). Seul un échec technique de gh compte au disjoncteur. `task.merged` n'est noté que constaté ; le geste est inscrit au journal d'autorité avant, une fois.
  - **Acceptation** : fusion revérifiée, tête fusionnée couverte par le sceau, fichiers de la PR hors des chemins qui escaladent, puis `accept_task` (paramètres `proof_root` et `head`) et clôture sur les gates déclarées par le projet. Une coupure ne refait ni geste, ni pack, ni verdict. Sinon « accepter ? » donne la commande `grimoire task accept <id> --project-root <worktree>`. Le janitor supprime aussi une branche locale restée en amont de la tête fusionnée.
  - **Git du tick neutralisé, seulement quand un rôle autonome est activé** (`src/grimoire/core/safe_git.py`) : le temps du tick, l'environnement (`GIT_CONFIG_COUNT`, l'équivalent de `git -c`) neutralise pour tous les git du processus ce qui exécute du code — hooks, `core.fsmonitor`, signatures, transports `ext::` et `fd::`, et, quand le dépôt ou un worktree les déclare localement, les filtres, les pilotes de fusion, `core.sshCommand` et `credential.helper` (remis aux valeurs globales de l'utilisateur). Rien n'est refusé pour une configuration ordinaire. L'identité, les identifiants et la configuration globale de l'utilisateur ne sont pas touchés, et les sessions `claude` lancées par le tick ne reçoivent pas ces variables. Hors d'un tel tick (CLI, empreinte des reçus, Stop, worktrees lancés par un humain, projets sans rôle), rien ne change.
  - **Aucune réponse du ledger ne vaut un geste** : le merger ne lit pas `task.human_answered`.
  - **Limites** :
    - les sessions tournent sous le même utilisateur du système : un processus qui écrit ce que le tick lit avant lui (le journal d'autorité, le ledger, la configuration du dépôt) contourne une garde de fichier ; seule une isolation par utilisateur du système la fermerait ;
    - la neutralisation git est calculée à l'entrée du tick : un filtre ou un pilote posé par le worker pendant le tick tourne le temps de ce tick (hooks, fsmonitor et signatures restent toujours coupés) ;
    - les pilotes de diff (`diff.<nom>.command`, `textconv`, `diff.external`) ne sont pas neutralisés, car un pilote vide casse `git diff`, et le tick n'en lance pas ;
    - les git que `gh` lance lui-même héritent de la neutralisation par l'environnement, sans plus ;
    - un sceau ne se pose pas sans tête dans un dépôt git, et une tête demandée n'est jamais couverte par un sceau sans tête ;
    - la liste des chemins qui escaladent est courte : le reste de `src/` se fusionne sous mandat ;
    - GitHub peut tronquer la liste des fichiers d'une grosse PR : un écart avec `changedFiles` fait escalader ;
    - la branche locale du worker n'est pas avancée par le réalignement ;
    - une PR sans aucun check attend indéfiniment ;
    - le classement « refus du dépôt » contre « panne » lit le texte d'erreur de gh ;
    - une ligne `task.human_answered` forgée remet à zéro le compte des retours au worker (un coût, pas une autorité) ;
    - la stabilité de la CI tient sur deux ticks.
  - Fichiers : `core/safe_git.py`, `missions/{merger,review_seal,reviewer,orchestrate,orchestrator_policy,human_acceptance,janitor,launcher}.py`, `cli/cmd_orchestrate.py`, `docs/reference/cli/taches.md`. Tests : `tests/unit/missions/test_merger.py`, `test_merger_securite.py` (les preuves de concept des trois revues de sécurité de #927, et les régressions de l'usage ordinaire), `test_mandate.py`.
- feat(cockpit): vue « Sessions » de Travail et vivacité réelle (P33, `GAO-p33-vue-ses-001`) — le board disait ce que chaque tâche attend, rien ne disait ce que fait chaque session
  - **Vue Sessions** : troisième vue du segment de Travail (« Sessions N »), conforme à l'artboard : quatre tuiles (actives, attendent ta réponse, silencieuses avec claim, terminées sur 24 h), un tableau (état, hôte, session, projet et tâche tenue, persona et modèle, activité des 15 dernières minutes, dernier signe) et un panneau de détail (claims, dernier signe et sa source, consignes en attente, commande `claude --resume` à copier, confirmée sur place). « Envoyer une consigne » pose une consigne sur la tâche tenue (route existante `comment`) ; « Ouvrir la décision » et « Voir la tâche » mènent aux vues de Travail. « Relancer la session » et « Libérer les claims… » de la maquette n'ont pas de route : ils ne sont pas dessinés.
  - **Lecture** : `GET /api/workspace/sessions[?scope=portfolio]` (`tools/workspace_sessions.py`) croise journaux de session, claims du Mission Ledger, queue du TraceLedger (activité, agent, modèle, dernier outil) et sonde `claude agents --json`. Un projet illisible est nommé, une activité que rien ne trace vaut « non lue », jamais zéro.
  - **Sonde d'hôte** : `probe_entries` lit aussi les sessions d'arrière-plan (`state` `running`/`waiting`/`done`/`stopped`, `waitingFor`) ; une session arrêtée est « terminée », une session qui attend reste vivante. Sonde muette : le journal de session tranche seul.
  - **Une seule définition de « vivante »** : la Flotte disait 15 min, Travail 30. `missions.liveness.DEFAULT_ACTIVE_MINUTES` (30) est désormais la seule fenêtre, lue par la Flotte (`project_health`), le portefeuille, le pouls, la vue Sessions et `grimoire task list --live-minutes`.
- feat(autonomy): `--attest-from <projet>` sur `autonomy mandate grant` et `autonomy grant` (Refs #876) — accorder un mandat sur un projet depuis la session d'un autre projet échouait toujours en `human_authority_unprovable`, le journal des tours humains n'étant écrit que là où tourne la session
  - **Source désignée, jamais devinée** : l'option seule désigne le projet dont le journal des tours atteste ; ni le dossier courant ni l'environnement. Sans elle, rien ne change ; le refus `human_authority_unprovable` propose désormais `--attest-from`.
  - **Mêmes règles** : session de l'appelant, tour `user` postérieur au challenge, un tour pour un seul octroi. Le challenge et le mandat (ou l'octroi) restent ceux de la cible et inscrivent `attest_from`.
  - **Registre des tours consommés tenu dans la source** : le tour est cherché hors du registre de la source et de celui de la cible, puis consommé dans celui de la source (`for_project` = la cible). Le registre suit le journal qu'il protège : un même tour ne sert ni à deux cibles ni à la source elle-même ; un lien symbolique vers la source est résolu.
  - **Registre des tours de l'utilisateur** : toute consommation de tour, avec ou sans l'option (octroi, mandat, checkpoint de flow, `approve-check`), s'inscrit aussi dans `~/.grimoire/control/consumed-turns.authority.jsonl` (clé `(session_id, ts, prompt_sha256)`, indépendante du chemin du journal), que toute attestation consulte : une copie du journal des tours ne réatteste plus un tour consommé (un tour pour N octrois via des copies de la source, trouvé en relecture). Sans l'option, le résultat est inchangé : une ligne de plus dans ce registre.
  - **Course fermée** : `consume_turn` revérifie le tour sous le verrou de ce registre ; de deux décisions concurrentes qui ont retenu le même tour, la seconde est refusée (« déjà consommé ») sans rien inscrire.
  - **Échec fermé** : source absente, sans journal ou illisible, refus nommé (code 1) avant tout challenge. Le tour est consommé avant l'inscription du mandat (comme l'était déjà l'octroi) : un registre impossible à écrire n'inscrit rien. Limite : le registre de l'utilisateur ne couvre que son `HOME`, et un agent qui exécute un script arbitraire peut toujours écrire la zone de contrôle.
- feat(cockpit): espace Savoir › Mémoire conforme à sa maquette, la recherche en entrée (P24, `GAO-p24-espace-001`)
  - **La recherche d'abord** : la rubrique Mémoire s'ouvre sur un champ de recherche (et la commande équivalente à copier), des filtres à gauche (Type avec comptes, Source souvenirs/projections, Projet multiple en portée « Tous les projets », Date, « Inclure les décisions remplacées ») et le détail de l'entrée choisie à droite. Chaque résultat montre son type, son titre, son extrait, sa provenance, sa puce projet, sa chaîne de supersession (« en vigueur », « remplacée par … », « remplace … ») et sa date. Sans terme, la colonne liste une page d'entrées par projet. **Changement de comportement** : les onglets Store, Graphe, Couches, Architecture et le tableau d'aperçu de la flotte quittent cette rubrique (`memory/overview` reste servi).
  - **Routes `GET /api/workspace/memory/entries` et `memory/entry`** (`src/grimoire/tools/workspace_knowledge.py`) : une page lue par tranches de 200 avec un curseur `nextOffset` et un budget de 2 000 entrées parcourues par appel, jamais un `get_all` complet ; filtres `kind` (souvenir contre projection : `projection_group` ou `source_kind == "projection"`), `types`, `since`, `include_superseded` ; comptes par nature et par type avant filtres. La fiche d'une entrée rend sa provenance, la chaîne de ses remplaçants, ce qu'elle remplace (lu dans le journal des écritures P14) et ses nœuds liés, dérivés de l'entrée (tâche, source, remplaçants, tags), sans requête au graphe. Lecture seule, chemins du registre ou du projet servi, une lecture ratée porte son motif.
  - **Recherche de flotte par rang fusionné** : `memory/search` fusionne les listes classées de chaque projet par rang réciproque (RRF, k = 60, égalité départagée par slug), plus par score brut (cosinus, BM25 et RRF local ne se comparent pas). Le score brut reste porté et nommé (`scoreKind`), avec `projectRank` et `fleetScore`. Le projet servi absent du registre (atelier `grimoire serve`) est désormais interrogé.
  - **Emplacement partagé** : dans un store lu par plusieurs projets (même collection, même fichier), les entrées d'un autre projet (`project_name`) ne sont jamais rendues, ni en liste, ni en recherche, ni en fiche : elles sont comptées (`hiddenOther`). Une entrée sans projet n'est montrée que si aucun autre projet du registre ne lit le même store.
  - **Supprimer…** : seul geste du détail, proposé uniquement sur le cockpit lancé dans le projet de l'entrée (`POST /api/memory`, garde 403 inchangée), avec confirmation dans la page et le résultat dit sur place. Corriger et Marquer remplacée attendent leurs routes (P34, P23) ; un journal des écritures n'a pas encore de lecture serveur : rien n'est affiché à sa place.
  - **Graphe par tags partagés retiré** : `_memory_graph` quitte `scripts/gen-site-data.py` (et la clé `graph` des instantanés `web/data/**/memory.json`) ; `web/memory.html` ne dessine plus de graphe ni de « liens » par étiquette commune.
  - **Backends distants** : `get_all(offset=, limit=)` de Weaviate et de Qdrant cesse de parcourir la collection dès que la page demandée est pleine.
- fix(cockpit): les actions de Concevoir et de Source disent leur résultat là où on clique — depuis la refonte, le dock n'est visible qu'en mode atelier, et un refus du serveur y restait invisible
  - **Retour sur place partagé** : `actionStatus` (shell.js, `role="status"`) affiche succès et refus à côté du bouton ; le bouton se désactive avec un libellé d'attente pendant la requête. Flotte l'emploie aussi (son libellé « Échec » ne se déclenchait jamais : il lisait un champ que l'API ne pose pas). Un refus en lecture seule dit quoi lancer pour écrire.
  - **Concevoir** : Valider, Simuler, Compiler, Enregistrer et l'ajout de nœud rendent leur verdict sous la barre d'outils, sans recouvrir de nœud. Valider contrôle désormais le brouillon à l'écran au lieu de relire le disque, qui effaçait sans prévenir les nœuds non enregistrés. Un blueprint illisible n'affiche plus un chargement sans fin. La Bibliothèque ouverte ne masque plus Valider, Simuler et Compiler. Le double-clic sur une carte ouvre bien son workflow : le premier clic faisait apparaître le panneau de détail, la grille rétrécissait sous le curseur et le second clic tombait à côté.
  - **Source** : Enregistrer et Créer un override confirment ou affichent le refus sous la barre du fichier ; un refus garde le brouillon et l'éditeur intacts. Chaque ligne de l'arborescence tient sur une ligne (les noms longs chevauchaient la suivante) et les dossiers se nomment depuis la racine de leur étage.
  - **Travail** : « Copier la commande » de reprise de session copie réellement et le confirme (il n'écrivait que dans le dock).
- fix(missions): une tâche qui dépend d'une tâche annulée redevient réclamable (#906) — `cancelled` est terminal et aucune commande ne retirait un lien : le dépendant restait bloqué à vie
  - **`grimoire task unlink <id> --depends-on <id> --reason "<motif>"`** : retire le lien sous le verrou du journal (`task.unlinked`, rejoué, motif et acteur journalisés, visible dans la frise de `task trace`). Un retrait sans motif, ou d'un lien qui n'existe pas, est refusé sans rien écrire ; le refus liste les vraies dépendances.
  - **Une dépendance annulée reste non satisfaite** : la traiter comme close aurait fait échouer la garde ouvert. Le refus de claim le dit et donne la commande : retirer le lien, puis relier la remplaçante avec `task link`.
- fix(site): lot L du tri du kanban, version lue sans import et vitrine sans nombre saisi (`GAO-r17-lire-la-001`, `GAO-p04-vitrine-001`)
  - **R17** : `scripts/gen-site-data.py` lit la version du kit dans `src/grimoire/__version__.py`, `version.txt`, `_grimoire/kit/.up-version` (kit installé) puis `project-context.yaml` (`kit_version`/`grimoire_version`) ; « unknown » seulement sans aucune source. Les comptes d'outils et d'agents regardent aussi `_grimoire/kit/`. Nouveaux drapeaux `--project-root` (alias de `--root`), `--dry-run` et `--meta-only`.
  - **P04** : `web/index.html` ne contient plus de nombre saisi (le « 48 secondes » et les deux « 3.62.1 » périmés sont partis) ; la version vient d'un `data-source` rendu par `scripts/render-vitrine.py` à partir de `meta.json`, que le workflow Pages régénère à chaque déploiement. Sans valeur calculable, la page n'affiche aucun chiffre. `meta.json` gagne `tests_method` (`static` = plancher, `pytest` = collecte exacte) ; les instantanés versionnés ne figent plus de compteur de tests (le 5948 contradictoire a disparu). `tests/unit/test_vitrine_numbers.py` échoue si un nombre littéral réapparaît dans le texte de la page (hors dates, `#issue` et rejeu verbatim `data-verbatim`).
- feat(memory): lot I du tri du kanban, mémoire sans interface (`GAO-p13-re-u-de-001`, `GAO-p23-gestes-001`). On peut corriger la mémoire sans rien perdre, et chaque lecture servie laisse un reçu sans contenu.
  - **Reçu de rappel (P13)** : chaque lecture mémoire qui sert des souvenirs (`search`, `hybrid_search`, taxonomie, `recall_typed`, `build_task_recall`, outil MCP `grimoire_memory_search`) ajoute une ligne à `_grimoire-output/memory-reads.jsonl` : horodatage, surface, acteur/session/tâche si connus, mode, filtres, et pour chaque souvenir servi identifiant, rang, score et, en hybride, rang et score par classement avant fusion. **Aucun contenu** : la requête n'y figure que par sa longueur, ni texte ni empreinte (celle d'une requête courte se retrouve par dictionnaire). Borné à 1 Mio (au-delà, ramené à ses lignes les plus récentes), fichier ignoré par git, aucun appel réseau, un échec d'écriture est un avertissement. Le cockpit (lecture seule) et le banc n'écrivent rien. `grimoire memory reads` calcule `recall_count_7d` depuis ce journal (le champ de `missions/projections.py` avait été retiré avec le code mort du lot C). **`readOnlyHint` maintenu** : `grimoire_memory_search` et `task_recall` restent en lecture seule, le reçu est une télémétrie interne locale, ignorée par git et par l'empreinte d'arbre du gate `Stop` (`done_gate._tree_fingerprint`), et `TestReadOnlyToolsWriteNothing` les contrôle en n'autorisant que `_grimoire-output/memory-reads.jsonl`. La ligne `.gitignore` est aussi dans `GITIGNORE_SECTION` (projets générés ; `ensure_grimoire_gitignore` l'ajoute à une section déjà posée).
  - **Retirer sans effacer (P23)** : prédicat `metadata.retracted` (`at`, `by`, `session_id`, `reason`), filtré comme `superseded_by` par `search`, `hybrid_search` (la copie du compagnon lexical est revérifiée sur le store principal), `search_taxonomy`, `recall_typed`, `get_all`, `get_all_filtered` et `taxonomy` (filtre avant pagination) ; `include_retracted` les relit. CLI `memory retract|unretract|tags|show`, outils MCP `grimoire_memory_retract`, `grimoire_memory_unretract`, `grimoire_memory_update_tags`, tous par `run_gesture` donc mêmes gardes et même journal (P14, qui trace aussi `supersede`, maintenant avec `--reason`). **Échec fermé** : une raison est obligatoire pour retirer ; un backend sans `update` est refusé avec un message (rien n'est supprimé) ; une écriture que la relecture ne confirme pas est défaite et signalée. **Cloisonnement** : `retract`, `unretract`, `tags`, `supersede` et `delete` refusent le souvenir d'un autre projet (`project_name`) ; un souvenir sans `project_name` n'est pas attribuable et reste accessible. **Changement de comportement** : `MemoryManager.get_all` et `get_all_filtered` masquent désormais remplacés et retirés par défaut ; les chemins de maintenance (export, `mempalace-export`, `migrate`, `split`, `graph sync-memories`, `graph purge-orphans`, `memory doctor`, vérification de projection) passent `INCLUDE_ALL` et ne perdent rien.
  - **Neo4j : `delete` efface le contenu** : `delete_memory` vide `text`, `tags`, `metadata_json`, `provenance_json` et `source` du nœud et retire toutes ses arêtes (`TAGGED_WITH`, `MEMORY_FOR`, `VECTORIZED_AS`, `VECTOR_FOR`) ; jusqu'ici `deleted = true` laissait tout lisible. La pierre tombale garde identifiant, projet et collection. `find_orphan_memory_nodes` reconnaît désormais les nœuds `deleted = true` du projet courant **sans condition de collection** (un souvenir qui n'a jamais été dans Weaviate n'en a pas : sa pierre tombale n'était jamais purgée), donc `memory graph purge-orphans --apply` retire la pierre tombale de tout souvenir supprimé, Weaviate ou non. Prouvé sur une vraie base par `tests/integration/test_neo4j_project_key_service.py` (job CI `neo4j-service`), pas exécutable localement faute de conteneur.
  - **Corrections de la relecture adversariale (perte et remise en service à tort)** : (1) Weaviate `update` calcule l'embedding avant toute écriture puis remplace l'objet par un `PUT` en place (il faisait `DELETE` puis `POST` : un embedding qui levait détruisait le souvenir) ; (2) `consolidate()` de `local`, `lexical` et `tantivy-local` (`memory gc`) ne regroupe jamais un souvenir retiré ou remplacé avec un autre (il supprimait le retiré avec sa raison, ou la copie vivante) ; (3) `remember` et `upsert` conservent `retracted` et `superseded_by` d'un souvenir existant (réenregistrer le même texte effaçait le retrait) ; (4) `unretract` supprime la clé `retracted` (neutralisée par `None` seulement sur un backend à fusion, MemPalace) et l'annulation d'un retrait non confirmé fait de même, en chaînant l'erreur d'origine ; (5) le journal ne garde plus d'empreinte de requête (SHA-256 tronqué, devinable par dictionnaire pour une requête courte) : la longueur seule.
- feat(trace,mcp): lot J du tri du kanban — spans de run dans l'export OTel, outil MCP `task_intake` (`GAO-r26-renseign-001`, `GAO-r07-brancher-001`)
  - **Spans du kernel dans l'export OTel (R26)** : `TraceLedger.export_otel_jsonl` et `otel_spans` (et `--format otlp`) acceptent `runtime_root` et lisent le journal `run_events.jsonl` du RuntimeKernel (`traces/runtime_spans.py`), un span par `RunEvent`, avec `parentSpanId` (racine du workflow, puis un span par node). Voie retenue : lire le journal là où il est plutôt que le recopier dans le TraceLedger, pour garder une seule source par donnée (pas de doublon à tenir d'accord, un run existant reste relisible tel quel). `grimoire task trace-export` passe la racine du projet ; sans journal, l'export est identique à avant et rien n'est créé.
  - **`task_intake` (R07)** : l'outil MCP équivalent à `grimoire task add --intake` ; même fonction (`missions.intake.intake_proposal`, désormais partagée par le CLI), même JSON, aucune écriture au ledger. Test de parité CLI/MCP et test d'absence d'écriture.
- fix(orchestrate): suites de #880 (#886) — `ci_finished` n'est émis que sur le sha de tête courant de la PR (un nouveau sha émet un nouveau `ci_pushed` et garde le créneau), UNE seule lecture `gh pr list` par tick pour tous les occupants (échec de `gh` : aucune émission), dépôt des PR lu dans `ci.repository` de `orchestration-policy.yaml` puis le remote `origin` (`gh -R`), aucune preuve de contrat d'hôte retenue quand la configuration effective de Claude Code désactive les hooks (`disableAllHooks`, `allowManagedHooksOnly`), `git diff|log|show --output` classé en écriture, le frein d'hôte jugé sur les réglages du projet (et non du dossier courant) partout, la lecture des réglages qui échoue fermé (`host status` la suit ; sous `allowManagedHooksOnly`, il faut une commande managée `grimoire-hook`), et une PR sans check encore rapporté lue « en cours » (jamais « sans PR »)
- feat(cockpit): lot H du tri du kanban — garde de sécurité, thème système, clavier, transitions, chiffres, contraste (`GAO-p02-cockpit-001`, `GAO-p67-th-me-p-001`, `GAO-p58-navigat-001`, `GAO-p54-transit-001`, `GAO-p60-squelet-001`, `GAO-p61-mode-de-001`)
  - **Garde 403 sur `POST /api/memory` (P02)** : `delete`, `gc` et `sync` n'agissent que sur le projet servi (`_HOME_SLUG`) ; un autre projet du registre rend 403 `hôte en lecture seule`, et sans projet servi identifié la mutation est refusée (échec fermé). La garde compare des chemins (`samefile` sur `_HOME_PATH`, figé au démarrage), pas un slug re-résolu : un `cockpit remove` puis `add` qui reprend le slug ne la déplace pas, et `..`, lien symbolique ou slug vide ne la contournent pas. Les lectures (`status`, `search`, `list`…) restent permises sur tout projet du registre. Le jeton de session sur toute écriture était déjà livré (#794).
  - **Thème par défaut = système (P67)** : sans choix mémorisé, la coque suit `prefers-color-scheme` ; un choix explicite du sélecteur reste prioritaire et mémorisé. Le choix est recopié sous une clé non scopée lue par un petit script inline de `<head>` avant le premier paint (et par le premier rendu de `shell.js`, avant `boot()`) : un « sombre » mémorisé ne clignote plus en clair. Le dernier projet ouvert fixe cette copie. Les captures en clair et en sombre contre l'artboard StyleDouce restent à valider par Guilhem.
  - **Aide des raccourcis (P58)** : « ? » ouvre la liste des raccourcis (Échap la ferme), jamais pendant une saisie ; la boîte piège le focus (le reste de la coque est `inert`), se ferme à Échap, au clic sur le voile ou par le bouton « Fermer », et rend le focus à l'élément d'origine ; `SELECT` rejoint les champs de saisie qui gardent leurs frappes. j / k / Entrée (À traiter) et ⌘K existaient déjà.
  - **Transitions entre espaces (P54)** : changer d'espace passe par `document.startViewTransition` quand l'API existe et que le mouvement n'est pas réduit (fondu de 150 ms) ; repli sans transition dans les autres cas, y compris si `startViewTransition` lève ; les promesses `ready` et `finished` d'une transition écartée par un `goto` rapproché sont rattrapées (aucune erreur non gérée).
  - **Chiffres animés sur changement réel (P60)** : une tuile KPI ne s'anime (une passe de 480 ms) que si sa valeur a changé depuis son dernier rendu, jamais au premier rendu, jamais sous `prefers-reduced-motion`. La dernière valeur est mémorisée par rubrique et par identifiant de tuile (`id`, sinon le libellé, avec le rang pour deux tuiles de même libellé) et n'est pas vidée par `goto()` : revenir sur une rubrique compare à la dernière valeur vue. Les squelettes de chargement existaient.
  - **Contraste élevé et hauteurs de ligne (P61)** : `data-contrast="high"` (Préférences, ou `prefers-contrast: more` par défaut) porte tout texte à 7:1 au moins dans les deux thèmes (calcul des rapports sur `tokens.css` et mesure sur le DOM rendu de cinq espaces) ; le token `--h-row` fixe les lignes de tableau à 44 px (confortable) et 32 px (compacte). Avec un pointeur grossier (`pointer: coarse`), `--h-ctl` et `--h-row` valent 44 px aussi en densité compacte.
  - Fichiers : `src/grimoire/cli/cmd_cockpit.py`, `web/workspace/{shell.js,shell.css,tokens.css,index.html,spaces/a-traiter.js}`, tests `tests/unit/cli/test_cmd_cockpit_routes_restantes.py`, `tests/unit/test_workspace_tri_h.py`, `tests/e2e/test_workspace_tri_h.py`.
- feat(memory): lot F du tri du kanban, back-ends mémoire (P15, P16, R10, P46, P28 ; P15b partiel). Une même base Neo4j ne mélange plus les nœuds que le kit y écrit désormais ; une migration garde les identifiants et ne défait rien dans la cible.
  - **Clé de projet dans Neo4j (P15)** : chaque nœud écrit par `Neo4jMemoryGraph` porte `project` (le `project.name`) et les identifiants qu'une base partagée ferait collisionner sont préfixés `<projet>::` : nœuds de code, souvenirs, objets vectoriels, missions, tâches, événements, incidents, preuves, verdicts, décisions, faits, journaux, étiquettes, entités, agents (clé à une propriété, toutes éditions). Deux projets qui réutilisent un identifiant ne s'écrasent plus. **Limite** : les nœuds écrits avant cette version n'ont pas de clé et restent partagés ; les compteurs et le repérage des orphelins les comptent encore (aucune régression de lecture) mais ils ne sont pas attribués. Le constructeur ne se connecte ni n'écrit ; le schéma est posé une fois par la même fonction avant la première écriture de toute voie ; pilote borné (~3 s au lieu de ~30 s) et disjoncteur d'une minute après une panne de connectivité seulement (une erreur de requête ou de contrainte ne suspend rien), qui nomme la cause et renvoie à `memory graph sync-memories`, sans bloquer l'écriture du souvenir. `find_orphan_memory_nodes` rend l'identifiant du souvenir dans le store (préfixe retiré, double forme pour les nœuds antérieurs) et `node_id` pour la suppression. Job CI `Neo4j 5.26 (service)` (`services:` de GitHub Actions) qui échoue si aucun test n'a tourné.
  - **Rattrapage (P15b) : non livré.** Attribuer les nœuds antérieurs exige une racine de code propre à chaque projet, un refus des conflits et un chemin pour les faits, journaux et objets vectoriels ; une première version a été retirée avant fusion plutôt que livrée fragile. Les identifiants préfixés, eux, sont livrés.
  - **Setup reproductible (P16)** : images épinglées (Qdrant `v1.19.1` dans le gabarit Compose, le gabarit Kubernetes et les `docker run`, Weaviate, Redis) ; Redis avec `--restart unless-stopped` ; une pile Redis existante est cherchée par image et port avant toute création ; gabarit Compose sans `container_name` ni nom de volume fixe (deux projets ne partagent plus un stockage), ports lus dans `GRIMOIRE_QDRANT_PORT` (l'ancienne graphie reste acceptée) ; les remèdes d'extras viennent d'une seule fonction (`updater.extras_install_cmd` : `uv tool`, `pipx` ou `pip`, en reportant les extras déjà installés). **Migration** : un `docker-compose.memory.yml` déjà généré n'est pas réécrit et garde son volume `grimoire-qdrant-storage`.
  - **Sélecteur de backend du graphe de code (R10)** : `grimoire memory graph sync-code --backend neo4j|codegraph-neo4j` ; le second instancie `Neo4jCodeGraphBackend`. Défaut inchangé.
  - **`memory migrate run` (P46)** : copie par upsert sous l'identifiant d'origine, `created_at` conservé, clés techniques du backend d'origine (`weaviate_collection`...) non recopiées. Ne crée que les identifiants absents de la cible (une relance ne défait pas une décision revue) ; un registre `_grimoire/_memory/migration/copied-*.json` empêche de recréer un souvenir copié puis supprimé dans la cible ; l'aperçu annonce les identifiants refusés, une source inexistante est refusée sans rien créer, code de sortie 1 si tout est refusé ; `--overwrite` remplace, l'aperçu chiffre créations, existants et écrasements. Un souvenir refusé par la cible est listé, la copie continue. `--project` filtre sur `project_name` ; `--source-collection` sans `--project` refuse tant que `--all-projects` n'est pas passé. **Changement de comportement** : l'ancienne commande régénérait les identifiants.
  - **Upsert à identifiant fourni et `memory split` (P28)** : `QdrantStorageMixin.upsert` (Qdrant et Ollama) ; un identifiant non UUID est refusé. `grimoire memory split` découpe un segment partagé par `project_name` : aperçu par défaut ; `--apply` refuse une cible qui désigne le même store que la source (résolution de `weaviate_collection`) et les souvenirs sans `project_name` (sauf `--allow-unassigned`) ; export JSON, copie, vérification, configs du registre repointées en tout ou rien et seulement si tout est vérifié, deux projets de même collection cible refusés, source inexistante refusée, source jamais supprimée.

- feat: lot G du tri du kanban, hôtes, traces, skills et équipes (R27, R31, R26, R30, R12). R15 (export observatoire) et P29 (installeurs sur uv) restent au kanban, sans changement.
  - **Drapeaux de profil d'hôte (R27)** : `plugin_packaging` est retiré de `HostProfile` (aucun lecteur, aucun émetteur de paquet ne s'y fiait) ; `mcp_native` est lu par `gaps_for`, un hôte sans client MCP déclare désormais l'écart `mcp` dans `host status` et dans l'outil MCP de statut. `agent_autostart`, `subagents_native`, `skills_native`, `commands_native`, `permissions_native` et `blocking_hooks` étaient déjà lus par `gaps_for` et l'émission ; les tests changent de comportement selon leur valeur (`tests/unit/test_hosts_event_trace.py`, `tests/unit/test_hosts.py`).
  - **Trois `HookEvent` de plus (R31)** : `task_created`, `task_completed` et `instructions_loaded`, avec une décision par défaut `grimoire.event-trace` qui n'autorise ni ne refuse rien et écrit une ligne dans `traces.jsonl` : le nom de l'événement et l'identifiant de tâche de l'hôte (tronqué), sans sujet, raison de chargement, chemin ni prompt, textes libres de l'hôte. Alignés sur la veille `hooks-echec-ferme-2026-10` : aucun hôte ne les déclare (`NOT_EMITTED_EVENTS`), donc aucun émetteur ne les écrit dans une configuration ; seul `grimoire host hook --event task_completed` (appel manuel) les consomme. `tests/unit/test_hosts_event_trace.py`.
  - **Arbre de spans du kernel (R26)** : les `RunEvent` portent `parent_span_id` (racine `span-workflow-<instance>`, un span `span-step-<node>` par node, checkpoints, échecs et outils sous le span de leur node) ; `WorkflowInstance.inputs_ref` reçoit le blueprint du flow, `outputs_ref` le dernier checkpoint à la fin du run (posé dans la transition `COMPLETED`, sans écriture de plus). Limite : l'export OTel du `TraceLedger` ne lit pas ces événements (un run de flow n'y écrit aucune trace), ce lien n'est pas fait. `tests/unit/test_runtime.py`.
  - **Garde des skills (R30)** : `scripts/check-skills.py`, branchée dans `grimoire gates repo` (garde `skills`), contrôle le format Agent Skills (`hosts/skill_spec.py`) des 30 skills livrés et exige `evals/skills/<slug>/evals.json` avec au moins un cas (`prompt`, `expected_output`). Les 30 fichiers sont ajoutés ; ils fixent une attente écrite, ils ne mesurent pas le skill. `tests/unit/scripts/test_check_skills.py`.
  - **Équipes retirées (R12)** : le kit n'embarque aucun manifeste d'équipe ; `grimoire workflows teams`, `workflows/teams.py`, le champ `team:` des workflows, la copie `_grimoire/kit/teams/` du scaffold et le bloc d'équipe de `workflows show` sont retirés. `grimoire upgrade-flow orphans` (donc le point de contrôle destructif de `up`) propose d'archiver les `_grimoire/kit/teams/*` des projets déjà installés, seulement ceux que le kit a livrés inchangés (empreinte de `registry/kit-file-hashes.json`) ; un manifeste modifié ou ajouté reste en place et est signalé (`kept`), le tier du projet n'est pas touché. Le chemin mort `validate_team` de `framework/tools/schema-validator.py` est retiré (le fichier gelé rétrécit). Un hôte inconnu n'affirme plus « sans client MCP » : l'écart `mcp` dit « non déterminé ». `tests/unit/test_project_upgrade.py`, `tests/unit/cli/test_cmd_workflows.py`.
- feat(cli): lot D du tri du kanban — missions et CLI (`GAO-r07-brancher-001`, `GAO-r08-exposer-001`, `GAO-r24-crire-fi-001`, `GAO-r25-exposer-001`, `GAO-p49-command-001`, `GAO-p50-command-001`, `GAO-r55-fusion-d-001`)
- `grimoire mission open "<titre>" [--scope-repo|--scope-surface|--scope-pack]` ouvre une mission et rend son identifiant ; une mission homonyme est refusée avec l'identifiant existant, rien n'est écrit ; `task_context` (MCP) rend le périmètre (`mission_scope`) — P49, R25.
- `grimoire task add --intake "<texte>"` propose type, risque, périmètre et tâches (JSON, sans écrire) ; `grimoire task add --finition maquette|peaufine` pose la finition, héritée sinon de la phase de `project-context.yaml` (`exploration` → `maquette`, `construction`/`exploitation` → `peaufine`) ; l'outil MCP `task_add` porte `finition` — R07, R24. `task add` quitte `cmd_task.py` (plafond de taille) pour `cmd_task_add.py`.
- `grimoire task import <plan.json> [--mission] [--dry-run]` : plan validé en entier avant d'écrire, tâches et dépendances créées, table clé du plan → clé du ledger, idempotent (une entrée déjà importée n'est ni recréée ni modifiée, une divergence est signalée), entrées `existing` sautées — P50.
- `grimoire plans list [--status] [--check]` lit le registre des plans — R08.
- Garanties de `task import` et `mission open` : relecture, détection de doublon, création et liens sous un verrou inter-processus (`TaskService.structure_lock`, fichier distinct du journal) — 4 imports simultanés créent un seul jeu de tâches, 4 `mission open` du même titre une seule mission ; ne protège pas une création par `task add`. Le champ `"plan"` est obligatoire, une entrée dont le titre ou les critères diffèrent de la tâche déjà importée refuse tout le plan (code 1), clés sans blanc ni caractère de contrôle, plafonds 200 tâches / 1 Mo. `project-context.yaml` non UTF-8 ou à `phase:` nulle ne fait plus échouer `task add`.
- `grimoire init` sans terminal écrit « mode non interactif, équivalent à -y » sur stderr (R55).
- Fichiers : `missions/{finition,plan_import,service,ledger}.py`, `mcp/server.py`, `cli/{cmd_task_add,cmd_task_import,cmd_plans,cmd_mission,cmd_task,cmd_init,app}.py`, `docs/reference/cli/{taches,projet}.md`, `tests/unit/cli/test_lot_d_missions_cli.py`.
- fix(gardes): lot B du tri du kanban — des gardes qui échouaient ouvert échouent fermé (`GAO-r37-lever-un-001`, `GAO-r38-r39-v-ri-001`, `GAO-r40-message-001`, `GAO-r41-utiliser-001`, `GAO-r44-mettre-l-001`, `GAO-r32-pattern-001`, `GAO-r34-check-ha-001`, `GAO-r35-instrume-001`).
  - R37 : un `_grimoire/standard/policies.yaml` illisible (`rules: [`, racine non mapping, `rules:` qui n'est pas une liste, entrée qui n'est pas un mapping) lève `GR-POL-004` au lieu d'être lu comme « aucune règle » ; `run_decision` rend `ASK` en `PreToolUse` avec la cause. Fichier absent, vide ou sans clé `rules:` : toujours « aucune règle ».
  - R38/R39 : un `task-board.yaml` ou un `events.jsonl` corrompu n'est plus « aucune tâche ». `resolve_active_task` rend la source `unreadable` (champ `unreadable` nommant le fichier) ; SessionStart et UserPromptSubmit disent « illisible » quel que soit le profil, Stop le dit aussi et, après une écriture, refuse en profil bloquant au lieu de juger la tâche fantôme `bootstrap`. Un ledger ou un board absent reste `bootstrap`.
  - R40 : `pre-commit-cc.sh` écrit « CC non installé » sur stderr quand `cc-verify.sh` manque (le commit reste permis), se replie sur `grimoire standard gate check` (indicatif) pour un projet enrôlé, et n'a plus de branche morte.
  - R41 : `pre-push.sh` fait échouer le push sur le code retour de `grimoire-init.sh validate --all` (et non plus seulement sur le mot « error ») ; un `grimoire-init.sh` absent est dit au lieu d'afficher « DNA validate OK ».
  - R44 : le docstring de `grimoire.hosts.decisions` dit ce que le code rend (`ASK` en `PreToolUse`, `ALLOW` avec contexte ailleurs).
  - R32 : pattern `plan-then-execute` (carte des capacités, catalogue, `controles-gouvernes.md`, `kit-coverage.json`) avec ses checks `firewall.plan_not_frozen` et `firewall.plan_deviation_allowed` sur le bloc optionnel `plan_then_execute:` de `prompt-firewall.yaml` ; nouveau besoin `web`.
  - R34 : checks `orchestration.handoff_role_unknown` et `orchestration.handoff_invalid` (chaque `from`/`to` de `handoffs:` est un `role_id` déclaré) ; la note « trou de vérification » disparaît de `traceability.yaml`.
  - R35 : PIP, décrit mais jamais instrumenté (aucun hook ne compte de correction proactive), est retiré de `agent-base.md` et `agent-base-compact.md` comme AORA et DCF ; aucun compteur inventé.
  - Relecture adversariale (même lot) : `pre-push.sh` ne meurt plus en silence sans `archetypes/` (`pipefail`) ; `policies.yaml` avec une clé racine inconnue (`rule:`, `Rules:`), sans `rules:`, ou `rules:` nul, ou qui est un dossier ou un lien cassé, lève `GR-POL-004` ; un outil en lecture seule passe avec avertissement quand `policies.yaml` est illisible (l'agent peut lire le fichier fautif), les autres rendent `ASK` ; un board (`tasks:` non liste, entrée non mapping) ou un ledger/board dossier ou lien cassé est « illisible » ; `unreadable` est propagé par `stop_scope` et par les retours `session_claim`/`ledger_claim`/`board` ; un ledger corrompu ne bloque plus le Stop d'une session qui n'a rien écrit (il le dit), le remède ne cite plus `git checkout` mais de sortir la ligne fautive du journal en la conservant ; `plan-then-execute` est installé dès `controlled` et son bloc absent donne l'avertissement `firewall.plan_block_missing` ; `handoffs[].from/to` en liste est refusé explicitement ; PIP retiré aussi de `_grimoire/_config/custom/agent-base.md`.
  - Tests : `tests/unit/test_policies_unreadable.py`, `tests/unit/test_task_state_unreadable.py`, `tests/unit/test_hook_scripts_fail_closed.py`, `tests/unit/test_pip_retired.py`, `tests/unit/core/test_handoff_targets.py`, `tests/unit/core/test_plan_then_execute.py`.

- docs(veille): lot E du tri du kanban — six notes sous `docs/veille/`, chaque fait sur Claude Code ou MCP sourcé par une page officielle ouverte le 2026-10-25 (le reste « non vérifié », `claude` jamais lancé) et close par « Ce que ça change pour le kit ». Hooks en échec fermé : `onFailure: block` (v2.1.295) n'a d'intérêt que sur `PreToolUse`, la référence l'exclut de `Stop`, tâche S proposée (`GAO-veille-hook-001`). Sous-agents : `omitClaudeMd`, `autoCompactWindow`, `effort`, pas de généralisation sans mesure de l'hôte (`GAO-veille-sous-001`). Canal plugin : #177 fermée par décision (#466), `claude plugin validate --json` retenu comme second oracle optionnel de #874, `plugin eval` écarté pour son coût de quota (`GAO-veille-cana-001`). Skills par MCP (SEP-2640, Final) : aucun des quatre hôtes du kit n'est listé comme client, rien à servir (`GAO-veille-serv-001`). Statusline : `prompt_cache` et `rate_limits.spend_limit` documentés, aucun appelant de `statusline_segment` dans le kit (`GAO-veille-stat-001`). R72 : la note de #376 (la tâche, pas l'agent, est l'unité de parallélisation) reprise telle quelle (`GAO-r72-converti-001`).
- chore: lot C du tri du kanban, code mort (R01, R02, R03, R04, R05, R09, R11, R16, R19, R20). Un module qu'aucun utilisateur ne peut atteindre est retiré ; deux seulement sont branchés, parce que le modèle de menaces ou une extension livrée s'appuient déjà dessus.
  - Retiré, API SDK : `AgentForge`, `ContextGuard`, `ContextRouter`, `PreflightCheck` (R01 et R02, sans appelant hors tests ; `grimoire.tools` n'exporte plus que `HarmonyCheck`, `MemoryLint`, `Stigmergy`), `grimoire.core.retry.with_retry` (R11, aucun appel réseau ne l'utilisait) ; guide SDK, référence API, ARCHITECTURE et ADR-002 mis à jour. Les outils autonomes de `framework/tools/` ne changent pas.
  - Retiré : `grimoire.runtime.p2p_guard` (R05, aucun chemin d'échange ne le comptait) et la règle `max_p2p_without_sog` de `framework/agent-mesh-network.md` ; `grimoire.missions.projections` (R09, aucune route `/api/workspace/projections`) ; onglets RTK et Bench du cockpit, Observer (R20, aucune route serveur) ; colonnes Trust et HUP de `framework/tools/observatory.py` (R16, le fichier ne fait que rétrécir).
  - Branché : `grimoire task import-a2a <fichier>` (R03) crée une tâche depuis une tâche A2A, un `completed` externe atterrissant en `needs_verification` (menace THR-004) ; `grimoire blueprint import <fichier> --from crewai|langgraph|gascity` (R04) écrit un blueprint `status=experimental`, relisible par `blueprint validate`, et rend atteignables les adaptateurs sur lesquels reposent les extensions `crewai` et `langgraph`.
  - Durcissement des deux commandes branchées (relecture adversariale) : l'id Grimoire d'une tâche A2A est un slug tronqué suivi d'une empreinte de l'id complet (deux ids distincts ne partagent plus une tâche, la casse comprise) ; un import fait sous l'ancien id est repris s'il appartient bien au même id A2A, et un homonyme est refusé. `task import-a2a` valide la forme et l'état avant d'écrire (rc=2, rien d'écrit, message de schéma lisible) et résout `evidence/` et `traces/` comme le ledger (`--project-root`). `blueprint import` déduplique les ids de nœuds (`-2`, `-3`) et passe le blueprint au validateur avant d'écrire (rc=1 sans fichier s'il est invalide). Docs : matrice de couverture du cockpit (résumé régénéré), spec du workspace, fonctions MCP d'ARCHITECTURE.
  - Dit vrai : la promesse de promotion des features beta « sur métriques d'usage » (R19) est retirée de l'aide, des docs et de la page Labs, aucun compteur n'existant ; la promotion est une décision des mainteneurs.

- feat(orchestrate): lot 2 de #876 — lanceur, worker et janitor (verrous 1, 12 et 13) : le tick lance enfin des sessions, sans rien changer tant que le rôle est désactivé (défaut).
- **Lanceur** (`src/grimoire/missions/launcher.py`) : avec `orchestrator.roles.worker.enabled: true` dans `orchestration-policy.yaml`, `orchestrate tick --apply` (donc le minuteur) pose pour chaque tâche admise le worktree `task/<id>`, le contexte et le claim, puis lance `claude --bg --name grimoire-<id>` dans ce worktree et note l'identifiant de session au ledger (`task.session_launched`). Une session distincte par tâche, jamais un sous-agent. Base et cible : la clé `orchestrator.base` de la politique, à défaut la branche par défaut du remote (`origin/HEAD`), jamais la branche extraite du poste qui lance le tick ; sans l'une ni l'autre, rien n'est lancé. Bornes : WIP du worker (1) et sessions par jour (5), modèle ou palier. Politique illisible : rien n'est lancé. `permission_mode` est obligatoire quand le rôle est activé (sans mode, une session de fond se bloque à la première demande d'autorisation, et le kit ne choisit pas à la place du projet) ; `model` et `permission_mode` ne peuvent pas être une option. Le WIP compte aussi les sessions en attente (blocked, waiting, needs_verification) et le plafond du jour compte les tentatives échouées.
- **Disjoncteur** (`missions/breaker.py`, `orchestrate breaker status|trip|reset`) : ouvert après 3 échecs consécutifs de lancement ou de session (réglable) ou par `trip(reason)` ; ouvert, aucun lancement ; seul un humain le referme (`breaker reset`). Son état figure au rapport du tick quand il est ouvert ou qu'il compte des échecs ; fermé et intact, la sortie du tick (texte et JSON) est celle d'avant, clé pour clé.
- **Janitor** (`missions/janitor.py`) : à l'état terminal d'une tâche lancée, rend le créneau CI, arrête la session (`claude stop`), retire le worktree, supprime la branche `task/<id>` en local et en distant seulement si `gh pr view` la dit fusionnée, archive la preuve ; une branche n'est supprimée que si la PR fusionnée porte exactement la tête de la branche (un commit poussé après la fusion la garde), et seulement si le lanceur l'a posée ; idempotent, journalisé (`janitor.action`), un échec est rapporté sans bloquer le tick. L'unité du minuteur reçoit un `PATH` réduit aux dossiers du poste qui portent `claude`, `gh`, `git` ou `node` (hors dossiers temporaires, relatifs ou absents), plus les dossiers système : jamais le `PATH` complet d'une session.
- docs(framework): lot A du tri du kanban (#919) — la documentation ne cite plus ce que le code ne fait pas. `grimoire up --fix` (inexistant) devient `grimoire up`, `grimoire serve` devient `grimoire cockpit serve` (port 8420) dans le glossaire, l'ADR-006 et les guides ; la référence agentique passe « aligné » les lignes que le code comble (`policies/temporal.py`, #440 ; `memory/validation.py`, `e28d68e9`) et tranche ses « à cadrer » ; `hybrid-parallelism-engine.md` est marqué « décrit, non instrumenté » ; l'inventaire de `framework/tools/` porte une colonne `décision` par outil et `FREEZE.md` les décisions et suppressions, avec un test qui génère le rapport sur un faux arbre (R13, R14, R29, R33, R43, R45, R47, R51).
- feat(orchestrate): lot 3 de #876 — relecteur (verrous 7 et 8) : une session distincte relit chaque tâche lancée qui arrive en revue, son verdict par critère alimente la couverture, un reçu du worker est un verdict forgé. Désactivé par défaut (`orchestrator.roles.reviewer.enabled`).
  - **Relecteur** (`src/grimoire/missions/reviewer.py`) : étape du tick après le lanceur. Une fois par entrée en `needs_verification`, `claude --bg --name grimoire-review-<id> --session-id <uuid>` dans le worktree de la tâche, avec une consigne qui ne donne que les critères, le diff de la branche et le pack de preuve : la session ne voit pas le raisonnement du worker. Modèle : celui de la politique, sinon un autre que celui du worker (sonnet ↔ opus, haiku → sonnet). WIP, disjoncteur ouvert et lancement raté se comportent comme pour le worker ; une erreur de l'étape est rapportée sans casser le tick.
  - **Identité de session (verrou 7)** : le lanceur fixe l'identifiant (`--session-id` et `GRIMOIRE_SESSION_ID`) du worker comme du relecteur ; sous mandat actif, il l'inscrit au journal d'autorité (`worker` ou `reviewer`, pour la tâche) avant le lancement et après la préparation du worktree (une préparation ratée ne consomme pas le plafond du mandat) ; une inscription refusée ou un journal en erreur ne lance rien. Le ledger ne garde que l'empreinte de l'identifiant du relecteur.
  - **Qui peut rendre un verdict** : `task review` refuse, avant d'écrire quoi que ce soit, un verdict venu de la session du worker et, sous mandat, d'une session qui n'est pas inscrite relectrice de la tâche (`review.self_review`) ; le canal humain (CLI interactive) n'est pas concerné. Au tick, un reçu de la session du worker est un **verdict forgé** — disjoncteur ouvert et, sous mandat, `revoke_on_forged_verdict` ; une autre session est ignorée ; une relecture lancée sous mandat exige encore, au jugement, un mandat actif et une provenance tenue au journal d'autorité, sinon refus définitif (une révocation entre-temps ne lève pas la garde).
  - **Verdict par critère (verrou 8)** : `grimoire task review <id> --criteria <fichier>` (schéma `grimoire-review-verdict/v1`) — un statut par critère (`met`, `unmet`, `unverifiable`) avec sa commande ou sa note, validé par du code : chaque critère une fois et dans son texte, `READY` si et seulement si tous sont `met`, un `unmet` montré par une commande, rien d'écrit sur un refus. Il ajoute un pack de preuve où les critères `met` sont couverts **par le verdict**, plus par une sous-chaîne trouvée dans un résumé (`EvidenceService.create_pack(reviewed=…)`) ; son verdict reprend les contrôles de commandes du dernier verdict (une relecture ne rend pas vert un check rouge) et celui de la relecture (un `NOT-READY` le rend rouge). Le reçu porte ce pack ; `task.review_recorded` est noté. Le gate refuse un reçu dont les critères ne sont plus ceux de la tâche (`review.criteria_changed`).
  - **Après le reçu** : `READY` frais est noté (l'acceptation et la fusion sont le lot 4) ; `READY` périmé est relu. `NOT-READY` renvoie la tâche au worker (`needs_verification → running`, session du worker reprise par `claude --bg --resume <son uuid>`, constats en note) ; une reprise ratée remet la tâche en revue et sera retentée ; disjoncteur ouvert, rien n'est repris. Au cycle `max_cycles` (2, compté depuis la dernière réponse humaine), ou sans session à reprendre, la tâche passe `waiting(human)` avec la question « reprendre ou abandonner ». Un relecteur muet plus de 2 h est arrêté et relancé ; après 2 relances (muet ou périmé), l'escalade aussi. Chaque étape est notée (`task.review_launched`, `task.review_judged`, `task.review_forged`) et n'est faite qu'une fois.
  - **Limites** : un `met` sur un critère non mécanique reste un jugement — le code vérifie qu'il est complet, cohérent et adossé à une preuve citée, pas que la preuve est juste ; chaque `NOT-READY` compte un cycle, checks verts ou rouges (le planner du lot 5 replanifiera) ; les sessions tournent sous le même utilisateur du système : un processus qui lit l'environnement ou la ligne de commande du relecteur (`/proc`), ou son reçu une fois écrit, connaît son identifiant et peut le reprendre — l'attestation rend la falsification visible et coûteuse, seule une isolation par utilisateur la fermerait.
  - Fichiers : `missions/{reviewer,review_verdict,review_receipt,launcher,orchestrator_policy,orchestrate}.py`, `evidence/service.py`, `cli/{cmd_task_review,cmd_orchestrate}.py`. Tests : `tests/unit/missions/test_reviewer.py`.
- feat(missions): lot 1 de #876 — mandat et principal `orchestrator`, journal d'autorité, zone protégée (verrous 6, 7, 9, 15 ; #886 d)
- **Mandat** (`src/grimoire/missions/mandate.py`) : un principal distinct, `orchestrator`, agit sous un mandat accordé par l'humain une fois — `grimoire autonomy mandate grant|status|revoke` (attesté par un tour humain, même mécanisme que `autonomy grant`) ou cockpit. Borné : gestes (`accept`, `answer`, `approve-check`, `resume`, `queue-release`, `merge`), types de tâche, plafond de sessions de worker par jour, durée. Sans mandat rien ne change. Modifier le mandat et modifier la politique d'escalade ne sont jamais couverts : la section `escalation` est scellée par empreinte à l'octroi, une politique modifiée depuis révoque le mandat.
- **Journal d'autorité** : `~/.grimoire/control/mandates.authority.jsonl`, chaîné par hash (`seq`, `prev`, `hash`). Illisible, rompu ou absent, il ne porte aucun mandat (échec fermé) et n'est jamais prolongé. Chaque geste pris sous mandat y est inscrit avec `origin: orchestrator`, l'identifiant du mandat et sa provenance (`rule`, `verdict` ou `receipt`) ; `GRIMOIRE_PRINCIPAL=orchestrator` et `GRIMOIRE_PROVENANCE=<kind>:<réf>` *revendiquent* le principal côté CLI, sans le prouver : la revendication n'est acceptée que si la session appelante est inscrite au journal d'autorité en rôle `orchestrator` (`register_orchestrator_session`, que le lot 2 appelle) et n'est celle d'aucun worker ni relecteur de la tâche ; sans session inscrite, refus. Le tick, qui est du code, passe par l'API en processus `with acting_as_orchestrator():`, jamais par l'environnement. Une session tient un seul rôle, et l'inscription d'orchestrateur vaut pour le mandat sous lequel elle a été faite.
- **Gestes** : `task accept`, `task answer`, `orchestrate resume`, `queue release` et `task approve-check` acceptent l'origine `orchestrator` si et seulement si un mandat valide couvre le geste et le type de tâche (`require_authority`) ; la réponse sous mandat porte `mandate_id` au ledger et tranche comme une réponse humaine.
- **Politique d'escalade** : section `escalation` de `orchestration-policy.yaml` (catégories `release`, `secrets`, `paid`, `security`, `merge`, `accept`, `human` ou `orchestrator`) ; le gabarit livré garde tout à l'humain. Le dépôt du kit ne versionne pas de `_grimoire/standard/orchestration-policy.yaml` : la configuration du pilote (`merge` et `accept` à `orchestrator`, `release`, `secrets`, `paid` à `human`) vit dans ce fichier du dépôt qui porte le pilote. Une tâche de type `security`, de profil `release`/`security_critical` ou labellisée garde ses catégories à l'humain.
- **Reçu de revue (verrou 7)** : champ `reviewer_session` ; sous mandat, `accept` exige un reçu READY dont le relecteur diffère de l'auteur et dont la session figure au journal d'autorité en rôle `reviewer` pour la tâche (`MandateStore.register_session`, que le lot 3 appellera). L'acceptation sous mandat est signée `orchestrator:<mandat>`.
- **Checks scellés (verrou 9)** : `TaskService.add --check` scelle l'empreinte au journal ; `approve_checks_under_mandate` n'approuve que des checks inchangés, sans `--cmd` libre ; un check modifié ou non scellé n'est pas approuvable et ouvre un événement « disjoncteur » (`MandateStore.breaker_events`, que le lot 2 consomme).
- **Révocation** : `MandateStore.revoke(reason)` (humain), échéance, plafond de sessions dépassé, écriture d'agent refusée dans la zone protégée, politique modifiée ; points d'entrée `revoke_on_forged_verdict` (lot 3) et `revoke_on_write_outside_worktree` (lot 2).
- **Zone protégée (#886 d, verrou 15)** : le frein `PreToolUse` refuse toute écriture d'agent sous `~/.grimoire/control/` (preuve de contrat d'hôte, journal d'autorité, clé du cockpit) par Write, Edit, NotebookEdit, redirection, `cp`/`mv`/`tee`/`sed -i`/`install`/`rsync`/`dd of=`/`ln`/`truncate`/`chmod`, `python -c`, et révoque alors les mandats ; les chemins sont résolus avant comparaison (`$HOME`, `${HOME}`, `~`, `..`, `//`, chemins relatifs joints au répertoire courant et à chaque `cd`/`-C`, globs, liens symboliques existants, composants cités), et `mv`/`cp`/`ln`/`rm`… sur `~/.grimoire` lui-même est refusé (déplacer le dossier pour l'éditer ailleurs) ; la règle de secrets `cockpit-key` refuse sa lecture, et celle du journal du mandat (`mandate-journal`, il porte les identifiants de session à prouver) ; les deux se lisent aussi résolus (jokers `cockpit-k*`, `control/*`, `cd` puis chemin relatif, `..`, lien symbolique). Cockpit : `GET /api/workspace/autonomy/mandate`, `POST …/mandate/grant` (jeton de session **et** clé de contrôle) et `…/mandate/revoke`.
- **Limites** : la garde lit des chemins et des commandes, pas du code — un agent qui lance un script Python arbitraire écrit sous `~/.grimoire/control/` ; la chaîne de hash ne voit pas la troncature de la queue du journal : deux témoins la voient, une **tête** (`<journal>.head`, `seq` et hash de la dernière entrée, vérifiée à chaque lecture : journal plus court, sans tête, ou tête sans journal = rompu) et un **marqueur de révocation** (`revoked/<mandat>`, écrit avant l'entrée `revoke`) ; ils vivent dans la même zone protégée, donc un écrivain qui forge la chaîne, la tête et supprime les marqueurs réécrit tout (la garde de zone est ce qui l'en empêche) ; l'identifiant de session de l'appelant vient lui-même de l'environnement (`GRIMOIRE_SESSION_ID` ou la variable de l'hôte) : un processus qui connaît l'identifiant de la session d'orchestrateur peut le reprendre ; le jeton de session du cockpit est injecté dans toute page HTML servie sur la loopback, donc lisible par un processus local (le mandat exige pour cela la clé de contrôle ; commentaires et réponses humains du cockpit restent derrière le seul jeton).
- Fichiers : `missions/mandate.py`, `missions/{service,human_acceptance,check_gate,review_receipt,questions,ledger,schemas}.py`, `cli/{cmd_autonomy,cmd_orchestrate,cmd_queue,cmd_task_approve,cmd_task_review,cmd_task_waiting}.py`, `tools/workspace_routes.py`, `hosts/decisions/{authority_guard,tool_policy}.py`, `hosts/secrets.py`, gabarit `orchestration-policy.yaml`. Tests : `tests/unit/missions/test_mandate.py`, `tests/unit/test_tool_facts.py`.
- fix(cockpit): les cartes du tableau Travail ne cassent plus leurs lignes. La méta (type, owner, risque, priorité) tient sur une ligne, l'owner trop long s'élide et le mot « standard » cède la place à son seul glyphe (nom au survol). Le badge « consigne non lue » passe sur sa propre ligne. La session se lit sur une ligne (identifiant court sans cadre, hôte en clair) : l'identifiant encadré se coupait en deux et « Claude Code » passait à la ligne. Vivacité, dernier signe, durée et coût partagent une ligne de faits, chaque fait d'un seul bloc, et la raison d'une inactivité passe au survol. Les lignes de blocage et d'attente s'élident, avec le texte entier au survol. Test : `test_chaque_fait_de_carte_tient_sur_une_ligne_sans_couper_un_mot` (`tests/e2e/test_workspace_executer_p64.py`).
- fix(cockpit): les modules JS et feuilles CSS du cockpit sont servis avec `Cache-Control: no-cache`, le reste des fichiers statiques (icons.svg, polices) avec `max-age=60` (un `no-cache` y coûtait une requête par rendu d'icône). Sans cet en-tête, le navigateur gardait d'anciens modules après une mise à jour du kit, ce qui donnait une page neuve avec des scripts périmés et l'impression que rien n'avait changé ; une revalidation sans changement rend un 304. Tests : `tests/unit/test_cockpit_static_cache.py`.
- fix(cockpit): fiche projet, section « Propositions » lisible, et « Accepter » ne finit plus dans le vide. Chaque proposition est une ligne d'une carte (style Douce) : pastille de type et nom, puis les faits en texte secondaire, puis les actions ; plus de faits coincés en colonne étroite à droite du nom. Cause de « Accepter ne fait rien » : une proposition `memory-link` sans porteur est toujours refusée par `accept_proposal` (« aucun porteur plausible »), et ce refus partait dans `ctx.dock.echo`, la Console que la coque ne montre plus depuis P62. Le serveur dit désormais, sur la proposition seule, quand l'acceptation échouera toujours : `grimoire.proposals.accept_blocker` (porteur absent, substitution non évidente, revue humaine requise, commande impossible à déduire), appelé par `accept_proposal` avant tout `_accept_*` (même texte que le refus réel) et joint par `proposals_view` (`accept_blocker`, lecture seule, aucune route d'écriture touchée). La fiche ne montre alors pas « Accepter » mais « À faire à la main » et la marche à suivre (fiche à ajouter au `context:` d'un agent, commande `grimoire agent override convert … --dry-run`, `needs.commands` à déclarer) ; la règle client `repairHasEvidentSubstitution` disparaît. Un refus que seul le disque révèle (porteur disparu) s'affiche sur la ligne même, dans une zone `role="status"` : bouton désactivé avec « Acceptation… » / « Refus… » pendant la requête, motif du serveur en clair, bouton rendu ensuite. Les enregistrements de l'inspecteur d'agent (clause, outils, contexte, skills) affichent aussi leur refus sur place au lieu de l'onglet `doctor` du dock. Tests : `tests/unit/test_proposals_accept_blocker.py`, `tests/e2e/test_workspace_proposals_types.py` (une acceptation refusée affiche son motif sur la ligne ; une proposition sans porteur ou en revue n'offre pas « Accepter »).
- fix(cockpit): sélecteur de portée et palette ⌘K au style Douce (revue du 2026-10-25). Le fond n'est plus l'accent doux opaque, qui faisait disparaître la coque : il assombrit sans masquer (`--scrim-soft`). Le sélecteur devient un menu ancré sous la puce de portée (440 px au plus, 16 px de marge à 400 px) ; la palette ⌘K reste une carte flottante, du même composant. Une ligne par projet : pastille d'état (suivi par Grimoire, sans Grimoire, dossier introuvable), nom en gras, dossier en texte secondaire sur une seule ligne dont le début s'élide (chemin entier au survol). « Tous les projets » vient en tête et la portée actuelle porte une coche, à la place des colonnes « Projet » / « Portée actuelle » et de la commande. Les lignes avaient une hauteur fixe de 34 px : un chemin ou un nom long passait sur deux ou trois lignes et chevauchait la ligne suivante ; chaque texte tient désormais sur une ligne (ellipse) et la ligne suit son contenu. En-têtes de section en casse normale avec leur compte, champ de recherche à loupe, aide clavier en pied, message quand rien ne correspond. Recherche : `renderPalette` comparait le texte tapé au libellé et à l'étiquette (« Projet », « Portée actuelle ») sans replier les accents, si bien que « ref » ou « reference » ne trouvaient pas « Référence agentique », qu'un morceau du dossier affiché ne trouvait rien et que « projet » ou « actuelle » faisaient correspondre tout le registre ou la portée en cours ; elle ignore maintenant casse et accents, cherche chaque mot tapé, et porte sur le dossier. Survol et clavier tiennent la même ligne (Entrée choisit celle qu'on voit en surbrillance, `aria-activedescendant` la suit), et une palette ouverte pendant l'amorçage se remplit dès que ses sources répondent. Tests : `tests/e2e/test_workspace_selecteur_portee.py` (trente projets aux dossiers profonds : aucun chevauchement ni débordement à 1440 et 400 px, menu ancré sur fond non opaque, coche unique, recherche sans accents et par dossier, survol, Entrée, Échap ; rouge sur main) ; `test_workspace_controls.py` suit la coche.

- feat(cockpit): espace « Travail » conforme à l'artboard Travail (P64, `GAO-p64-travail-001`). En-tête : titre de 32 px, vues (Tableau, Huit colonnes, Liste, Chronologie, Décisions) posées dans l'en-tête, filtre « Demande une action de moi » (question, revue, blocage) et formule `grimoire task board`. Cartes : identifiant, classe de route V0/V1/V2, anneau de preuve (un segment par condition du prochain passage, plein quand le gate l'accepte), titre sur deux lignes, session vivante, badge de blocage ou de condition manquante. La carte sélectionnée ouvre dans le panneau de détail : état, titre, carte « Pour passer en … » (conditions vertes ou rouges et leur gate, puis les gestes existants), preuves, consigne, derniers événements. Aucune écriture nouvelle ; `workspace_api` expose en lecture `verifiability_class` et `next_gate` (verdict de `gates.check_transition`).
- fix(cockpit): un re-rendu du panneau de détail de Travail n'écrase plus une saisie en cours (raison d'annulation, de blocage, consigne) : reporté pendant la saisie, et valeur, focus et curseur rétablis quand il a lieu (course de `test_executer_priorite_consigne_et_annulation_depuis_l_inspecteur`). Tests : `tests/e2e/test_workspace_executer_p64.py`.

- fix(cockpit): la Flotte ne retient plus sa table pour la lecture d'attention. Depuis P65, `drawOnce` attendait `/api/fleet` ET `/api/workspace/attention` ensemble (`Promise.all`) ; l'attention parcourt tout le registre, donc la table attendait la lecture la plus lente (933 ms mesurés en CI contre un budget de 900 ms). La table se peint désormais dès la flotte, la sous-ligne dit « attention en cours de lecture », la colonne Attention montre « … » et le filtre « Demandent une action » reste désactivé jusqu'à la réponse ; la Flotte se repeint alors avec les badges, sans écraser un autre rendu entre-temps et en gardant la saisie du filtre (focus et curseur). La lecture d'attention parcourt tout le registre côté serveur et disputait le processeur à `/api/fleet` (2 lectures concurrentes au chargement : la Flotte et le compteur de la barre) : la Flotte ne la lance plus qu'après la flotte, `api.attention` partage une lecture en vol entre ses lecteurs simultanés, et la première lecture du compteur de la barre attend la fin du premier montage d'espace, jamais onglet masqué, en reprenant la réponse que l'espace vient de lire s'il en a lu une de moins de 5 s (les pouls suivants relisent toujours le serveur). Tests : `test_la_flotte_ne_retient_jamais_sa_table_pour_la_lecture_d_attention` dans `tests/e2e/test_workspace_piloter_perf.py` tient les lectures d'attention en suspens (rouge avant le correctif : la table n'apparaissait jamais) ; `test_aucune_lecture_d_attention_ne_dispute_le_serveur_a_la_flotte` vérifie qu'aucune lecture d'attention ne part avant la réponse de `/api/fleet` (rouge avant : 2).
- feat(cockpit): Configuration conforme à la maquette Douce (P66, `GAO-p66-configu-001`). Six rubriques verticales avec leurs comptes (Agents, Fichiers, Blueprints, Hôtes et hooks, Fournisseurs et budgets, Préférences) ; l'espace atterrit sur « Hôtes et hooks » : titre de 32 px, sous-ligne de comptes réels (appliqué / essai / observation), segments par hôte, table hook / événement / mode, formule `grimoire hooks status` à copier, et dans le panneau de détail les fournisseurs (état, palier, budgets de session, formule `grimoire providers status`). « Fournisseurs et budgets » et « Agents » sont des rubriques en lecture (agents lus sur la route qui alimente la fiche projet).
  - Lecture seule : aucun « Passer en observation », « Promouvoir » ni « Remettre à zéro » (écritures, P01/P02) ; deux routes GET `/api/workspace/hooks` (registre de sûreté des hooks, hôtes attribués seulement sur un nom explicite) et `/api/workspace/providers` (même charge que `grimoire providers status --json` plus un état lisible), dans `grimoire.tools.workspace_config`.
  - L'arbre de Fichiers vit dans le contenu de sa rubrique (colonne à gauche du fichier ouvert) ; l'explorateur de la coque n'est plus affiché hors atelier (⇧⌘L le rouvre).
  - Tests : `tests/unit/test_workspace_config.py` (modes, hôtes, registre absent, illisible, sans table, fournisseurs refroidis ou désactivés, routes) et `tests/e2e/test_workspace_configuration_p66.py` (rubriques et comptes, atterrissage, segments, panneau, Fichiers, 390 px, captures `p66-*` via `GRIMOIRE_E2E_CAPTURES`).
- feat(cockpit): Flotte conforme à la maquette Douce (P65, `GAO-p65-flotte-001`). En portée « Tous les projets », titre de 32 px, sous-ligne de comptes réels (« N projets suivis · N alignés sur le kit X · N demandent une action »), formule `› grimoire cockpit list`, champ « Filtrer par nom ou chemin », segments Tous / Demandent une action / Kit en retard (avec leur compte) et table : projet + chemin, badges d'attention lus sur `/api/workspace/attention` (un par type, avec son compte ; le retard du kit a sa colonne), kit et écart en fichiers, sessions (exécutions encore vivantes de `health.activity`), dernier signe, ouverture de la fiche ; cartes équivalentes sous 760 px. « Rafraîchir la flotte » et « Nouveau projet » passent dans l'en-tête. Retirés de la vue : les tuiles KPI, la section « À traiter » (c'est l'espace À traiter) et la colonne CI (rien ne la mesure : elle ne disait qu'« inconnue »). Pas de panneau de détail vide (pas de colonne à droite). Aucune lecture ni écriture nouvelle ; pas de sélection multiple, d'aperçu groupé, de « Masquer », de « Retirer du registre », de « Scanner » ni d'« Ajouter » : ces gestes n'existent pas encore dans le cockpit. La fiche projet et l'écran Mise à jour (P44) ne changent pas. Tests : `tests/e2e/test_workspace_flotte_p65.py` (captures `p65-flotte-*` sous `GRIMOIRE_E2E_CAPTURES`) ; le test « tableau avec inconnue » de `test_workspace_lot4_spaces.py` suit la table.
- feat(cockpit): « À traiter » conforme à la maquette Douce (P63, `GAO-p63-traiter-001`). Titre de 32 px, sous-ligne « Ce qui attend une décision de toi, sur N projets » et formule `› grimoire status` ; tuiles de comptes réels par section (À décider, À débloquer, À réparer, À entretenir) avec l'ancienneté du plus vieil élément ; lignes à pastille d'icône teintée par gravité (sprite maison, jamais d'emoji), projet · raison, ancienneté, action en pilule, primaire sur la ligne choisie ; panneau de détail (contexte, titre, explication, paires clé/valeur, l'action existante seulement, équivalent CLI quand le serveur en connaît un). Aucune écriture nouvelle. Le compteur passe de la pastille séparée de la barre au badge de l'onglet « À traiter » (`#decisions-count`) : c'est le `count` de `/api/workspace/attention` (hors « résolu automatiquement ») dans la portée courante, relu au premier pouls et à chaque changement d'empreinte, donc égal à la somme des tuiles. `kpiRow()` accepte une sous-ligne facultative (`sub`).
- Tests P63 : 4 e2e dans `tests/e2e/test_workspace_a_traiter_p32.py` (tuiles, pastilles et action primaire, détail et CLI, compteur sur l'onglet) et captures `p63-a-traiter-*` sous `GRIMOIRE_E2E_CAPTURES` ; le test du compteur de `test_workspace_live_pilot.py` suit le badge.
- feat(cockpit): coque conforme aux maquettes, style Douce appliqué (P62, `GAO-p62-coque-c-001`). Par défaut, à 1440 px, chaque espace n'affiche plus que la barre du haut (« › Grimoire », portée avec le nombre de projets, les cinq espaces en onglets pilule à icône, « en direct », « Rechercher ou agir ⌘K »), le contenu et, quand l'écran y met quelque chose, un panneau de détail à droite. Rail d'icônes, explorateur, preuves, inspecteur permanent et dock Console/Traces ne sont pas supprimés : ils deviennent les « panneaux d'atelier », fermés par défaut, rouverts par ⇧⌘L, par l'entrée « Afficher les panneaux d'atelier » de la palette ⌘K ou par le bouton « Atelier » du pied de page ; le choix est mémorisé par projet (localStorage, en try/catch). Lancer une commande (« Lancer dans la Console ») rouvre l'atelier pour en montrer la sortie. Sous 900 px la coque garde ses tiroirs, le rail en reste la porte.
- Panneau de détail : `ctx.inspector` rend dans une carte arrondie à ombre douce (380 px), affichée seulement quand l'écran y a posé du contenu (une phrase « Sélectionnez une tâche », un squelette ou l'indice vide n'en sont pas) ; vide, il n'y a pas de colonne. L'arbre de Configuration › Fichiers reste visible hors atelier : c'est la navigation de la rubrique, pas un outil de diagnostic. Les rubriques d'un espace (Savoir, Configuration) passent en sous-menu vertical à gauche.
- Style Douce porté par les tokens : Instrument Sans vendorisée (`web/workspace/fonts/InstrumentSans-Variable.woff2`, variable 400-600 réduite au latin, 35 Ko, licence OFL jointe, aucune requête tierce) à la place de Geist (retirée), rayons 10 / 16 / 20 / pilule (`--r`, `--r-l`, `--r-xl`, `--r-pill`), titre d'espace de 32 px (`--t-title`), barre du haut de 52 px, boutons en pilule, cartes à ombre douce.
- Tests : `tests/e2e/test_workspace_coque_p62.py` (cinq espaces sans panneaux d'atelier, bascule clavier/palette/pied et mémoire, contrat du panneau de détail, style rendu, aucune requête tierce, captures sous `GRIMOIRE_E2E_CAPTURES`) ; `tests/unit/test_workspace_tokens.py` étendu (fonte vendorisée et licence, rayons, absence de fonte distante) ; les e2e qui mesurent le rail, l'explorateur ou le dock ouvrent la bascule via `open_atelier()` (`tests/e2e/conftest.py`), aucun n'est désactivé.

- feat(cockpit): écran « Mise à jour » plein canevas (P44, `GAO-p44-cran-mi-001`), en lecture. « Mettre à jour — aperçu » ouvre l'aperçu dans le canevas (« Retour à la fiche » le referme), plus dans l'inspecteur ; les étapes que l'aperçu ne joue pas portent « non exécutée en aperçu », « jamais atteint » restant réservé à un vrai run arrêté avant elles. La sortie de `grimoire up --dry-run` et celle de `host sync --dry-run` sont rendues en vrais tableaux HTML en français (`POST /api/projects/update` joint `previewTable`, lu par `grimoire.tools.upgrade_preview` ; un détail inconnu reste tel quel) — plus aucun caractère de boîte Rich dans le DOM — et le nom du projet affiché est celui du cockpit, pas « Project: x ». Deux lectures nouvelles, `GET /api/workspace/upgrade/changes` (changements par catégorie : agents, kit, Copilot, hôtes, autres) et `GET /api/workspace/upgrade/diff?path=` (diff d'un fichier que la mise à jour toucherait ; 400 pour tout autre chemin), calculées en comparant le plan de `up` (`cmd_up.kit_refresh_plan`) au disque, sans rien écrire ; la liste et le diff s'affichent au clic. Le bouton « Confirmer la mise à jour » et les méthodes POST/PUT de `cmd_cockpit.py` ne changent pas. Tests : `tests/unit/test_upgrade_preview_p44.py`, `tests/e2e/test_workspace_update_screen_p44.py` (captures par `GRIMOIRE_E2E_CAPTURES`).
- feat(memory): provenance et journal des écritures mémoire (P14, `GAO-p14-provena-002`) ; remplace #889. `MemoryManager.store`, `update`, `supersede`, `delete` et `invalidate_fact` écrivent chacun une ligne dans `_grimoire-output/memory-changes.jsonl` (opération, acteur, session, tâche, ids avant/après, empreinte du contenu, raison) ; l'écriture part du manager, jamais d'une route HTTP, et ne porte pas le texte. `actor`, `session_id`, `task_ref` (et `reason`) sont des arguments nommés, sinon pris de la session (`GRIMOIRE_SESSION_ID`, `CLAUDE_CODE_SESSION_ID`, `GRIMOIRE_ACTOR`) et du claim actif ; ils sont posés dans `metadata` et omis quand ils sont inconnus, jamais vides ni inventés. Les champs `MemoryEntry.source`, `provenance`, `freshness` et `task_ref` restent (EKP-E1 les remplit au service) : `task_ref` est désormais dérivé de `metadata["task_ref"]` au lieu de rester mort. Tests : `tests/unit/memory/test_write_journal.py`.
- feat(cockpit): écran « À traiter » (P32, `GAO-p32-cran-tr-001`). La liste lue sur `/api/workspace/attention` (P22) est groupée par urgence (À décider, À débloquer, À réparer, À entretenir, Résolu automatiquement), une ligne par élément : icône du type, projet, titre et raison, ancienneté (« il y a … », tenue à jour par la coque), action en ligne. L'action ouvre l'écran où la décision se prend, la sélection posée : la tâche dans Travail (`#travail?task=…`, et `of=<projet>` en portée « Tous les projets », sans changer de portée), la question dans la vue Décisions, la proposition, le checkpoint, le kit ou la mémoire sur la fiche du projet (`#flotte?focus=…`, le bloc visé est marqué et amené à l'écran). Le détail de la ligne choisie va dans l'inspecteur (référence, refus du gate pour une revue bloquée).
  - États : vide (« Rien n'attend de décision », heure de la dernière vérification, « Ouvrir le Travail ») ; erreur partielle (« N projets n'ont pas répondu » et leurs noms, Réessayer relit avec `probe=1`) ; lecture seule (actions désactivées, la raison écrite au-dessus de la liste et reliée à chaque bouton).
  - Clavier : j / k passent d'une ligne à l'autre, Entrée ouvre l'action de la ligne ; la sélection entre dans l'URL (`#a-traiter?item=…`). Le pouls (P10) relit la liste en gardant la sélection.
  - `api.attention(portfolio, { probe })` ; `piloter.js` lit `ctx.params.focus` pour poser la sélection sur la fiche.
  - Tests : `tests/e2e/test_workspace_a_traiter_p32.py` (cockpit réel, quatre projets au registre : groupes et lignes, action vers Travail dans les deux portées, question vers Décisions, proposition sur la fiche, vide, erreur partielle et Réessayer, lecture seule, j / k / Entrée, lien profond ; captures par `GRIMOIRE_E2E_CAPTURES`).

- feat(cockpit): coque à cinq espaces, portée globale unique et liens profonds (P31, `GAO-p31-coque-c-001`). Les six espaces (Piloter, Concevoir, Exécuter, Observer, Mémoire, Source) deviennent cinq espaces par intention — À traiter (accueil, ⌘1), Travail, Flotte, le 4e espace et Configuration — dont les rubriques montent les modules d'avant ; le nom du 4e espace (« Savoir », décision D2 encore ouverte) n'est écrit que dans `web/workspace/glossary.js` (`FOURTH_SPACE`).
- Portée : une seule sélection, « Tous les projets » ou un projet, choisie par la puce du haut (palette réduite aux projets, sans plafond à 12) et écrite dans l'URL (`?project=<slug>` / `?scope=all`, puis `#<espace>[/<rubrique>][?<élément>]`). Plus de zoom Flotte/Projet dans Piloter ni Concevoir, plus de « Ce projet / Tous les projets » dans Mémoire ni de sélecteur « Portée » dans Exécuter. La puce et le fil d'Ariane disent le nom du projet (accents compris), jamais le slug ; choisir un projet ouvre sa fiche. Les anciens liens `#piloter`, `#source`… mènent à la rubrique qui les remplace.
- Lecture seule : un bandeau en tête de la toile le dit, avec « Revenir à <projet ouvert> » et la commande qui ouvre le cockpit en écriture sur ce projet ; `GET /api/status` du cockpit expose `home` (lecture seule, aucune route d'écriture touchée).
- Préférences : rubrique de Configuration rendue par la coque ; « Densité » (confortable / compacte) et « Plein écran » (⇧⌘F) sont deux réglages, deux mots — « Concentration » n'est plus employé ; les préférences mémorisées se relisent sous leur nouveau nom. Glossaire : `plein-ecran` remplace `mode-concentration`, entrées `portee`, `lecture-seule`, `a-traiter`, `configuration`.
- À traiter : liste minimale par urgence lue sur `/api/workspace/attention` (P22), chaque élément avec son lien profond ; l'écran complet reste P32.
- Tests : `tests/e2e/test_workspace_coque_p31.py` (cinq espaces, lien profond et historique, bandeau de lecture seule, sélecteur de 15 entrées, nom et non slug, fiche plutôt que Flotte, aucun zoom Flotte/Projet, Densité et Plein écran), `tests/unit/test_workspace_coque.py` ; les tests e2e existants suivent les nouveaux identifiants.

- fix(ci): le garde de budget du hook PostToolUse (`test_repetition_guard`) mesure le minimum sur 5 exécutions au lieu de la médiane. Sous charge d'un runner partagé, la médiane devenait rouge par intermittence sans régression du code (issue #877) ; le minimum reste sensible à une vraie régression.
- perf(ci): la jambe Windows de la suite est réarmée sur les PR (#857). Constat (run 40042732352, `--durations=0`) : 193 min de travail cumulé, dont 5 250 s pour `tests/unit/cli/test_app.py` seul sur un worker (`--dist loadfile`) ; 189 tests entre 20 et 40 s, tous des `grimoire init -y` ; profil d'un `init -y` in-process sur windows-latest : 43,2 s dont 41,2 s dans `socket.connect` (40 appels à ~1 s), un port fermé de la boucle locale n'y refusant pas le SYN. Le même `init -y --backend local` : 1,0 s.
  - Produit : `cmd_init._port_answers` (connect TCP borné à 0,5 s) garde les sondes Weaviate/Qdrant/Ollama avant toute requête HTTP, et la sonde Redis passe de 1 s à 0,5 s ; un `init -y` sans service local cesse de payer 2 s par point d'entrée sous Windows.
  - Tests : `tests/conftest.py` éteint par défaut les trois sondes pour tout `init` en process (même forme que `_default_no_docker_daemon`), fixture `real_memory_service_probes` pour les tests qui les exercent ; `TestLoopbackProbeBudget` (`tests/unit/cli/test_init_memory_profile.py`) ; les trois classes à faux binaire de `tests/unit/test_host_contracts.py` (shebang, PATH `:`, HOME) sont marquées harnais POSIX, rouges sur Windows depuis #876.
  - CI : `cache: pip` sur le job de tests ; la garde « Windows écarté sur les PR » et `test_windows_leg_is_skipped_on_pull_requests_only` sont retirées.
- fix(cockpit): Concevoir — le segment « Workflow » du zoom charge enfin le blueprint sélectionné (`web/workspace/spaces/concevoir.js::pickZoom`). Un simple clic sur une carte sélectionnait le blueprint sans le charger (seul le double-clic le faisait) : le segment laissait l'écran sur « Chargement du blueprint… » indéfiniment (#536). Rouge avant / vert après : `tests/unit/test_concevoir_zoom_source.py` (garde de structure, rejouée en CI).
- feat(workspace): fin du point polysémique dans le cockpit, direction Douce (P30). Un point ne porte plus qu'une présence ou une quantité : sur une carte d'Exécuter, le type de tâche prend son icône (libellé français au survol et pour les lecteurs d'écran, le mot brut « implementation » disparaît), la priorité trois barres (critique : trois barres teintées), le profil de risque un bouclier à 0, 1 ou 2 traits (octogone pour `security_critical`), la vivacité de session une forme par valeur (anneau à moitié plein, vide, coché, en tirets), l'hôte un pictogramme générique sans logo, le projet un monogramme de deux lettres, la preuve attendue un sceau et les consignes non lues une bulle. Les appels à `dot()` dans `executer.js` passent de 17 à 3, réservés aux vrais états (colonne d'une tâche, échec d'une ligne de timeline, ledger absent). Dans Source, le statut d'un fichier s'écrit en lettre façon git (`A` ajouté par le projet, `M` override modifié, `U` override à fusionner car le kit a changé depuis la copie, `O` override identique ou fichier du kit masqué, `D` chemin que le kit ne livre plus), déduite des champs du serveur et dite en clair au survol.
  - Sprite (`docs/assets/cockpit-icons`, régénéré par `scripts/build-icon-sprite.py`) : 9 types de tâche (`i-task-*`, un par valeur de `TaskType`), 8 rôles de base (`i-store-*`), 5 hôtes génériques (`i-host-*`), 6 icônes mémoire (`i-mem-*`), 4 vivacités (`i-session-*`), 4 profils de risque (`i-risk-*`) et 8 compositions d'état vide (`i-empty-*`, 96 × 64, encre en `--ink3` par le CSS, un seul trait d'accent). Les dessins repris de la famille Douce (code, fiole, document, bouclier, terminal, éditeur, curseur, bases…) sont copiés tels quels.
  - États vides : `ctx.empty(…, { art })` pose une composition au-dessus du titre ; branchée sur Exécuter sans ledger, les décisions en attente, Concevoir sans blueprint et sans projet, la flotte vide de Mémoire, les couches mémoire absentes, la recherche mémoire sans résultat et l'activité d'Observer sans hook. `i-empty-sessions` est au sprite, sans état vide où se poser pour l'instant.
  - Composants partagés dans `shell.js` : `glyph()`, `monogram()`, `monogramText()`, `levelBars()`.
  - Tests : `tests/unit/test_workspace_polysemy.py` (au plus 3 `dot()` dans `executer.js`, tables type/risque/vivacité/hôte alignées sur `TaskType`, `RiskProfile`, K5 et `HostId`, lettres A/M/D/U/O de Source, huit compositions), `tests/e2e/test_workspace_polysemy.py` (cartes, monogramme, lettres de Source, états vides rendus, captures par `GRIMOIRE_E2E_CAPTURES`) ; la garde du sprite de P17 (`tests/unit/test_workspace_icons.py`) couvre les nouvelles familles et leur effectif.
- fix(cockpit): les lectures `/api/workspace/*` du cockpit traduisent enfin leurs refus métier (`src/grimoire/cli/cmd_cockpit.py::do_GET`) : tâche inconnue ou fichier absent → 404, chemin refusé → 403, paramètre ou identifiant invalide → 400, comme l'atelier et `do_POST`. Tout tombait avant dans un 500 générique, indiscernable d'une vraie panne (#536). Tests : `tests/unit/cli/test_cmd_cockpit_workspace_errors.py`.
- feat(init): wizard en trois questions (pile, qui travaille, domaine), archétype déduit (#888, décisions #630). Constat : le wizard demandait un archétype — vocabulaire interne du kit — et un niveau, et laissait la détection de côté dès qu'on tapait un numéro.
  - Changement : `src/grimoire/cli/init_questions.py` (nouveau, fonctions pures) porte les trois questions, le pré-remplissage depuis `StackScanner`/`ArchetypeResolver` (une supposition `is_best_guess` n'est jamais pré-remplie), la déduction (archétype(s), `skill_level`, besoins et profil du standard via `resolve_install_plan`, skills `stack-*`, expertises recommandées jamais attachées d'office), l'ajustement en une ligne (`domain=web who=team stack=python,go`) et la charge utile JSON partagée. `cmd_init._run_wizard` les pose à la place du choix d'archétype et du choix de niveau ; la pile confirmée devient la détection que le scaffold croit (`answers_to_scan`) ; le résumé montre pile, archétype, niveau, profil, skills, expertises, hôtes, cockpit. `-y` garde la détection seule, sans question. `grimoire up --interactive` reprend les besoins déduits quand `--needs` est absent.
  - Cockpit : `GET /api/setup/questions` (`project_setup.setup_questions_view`, même module que la CLI ; `?stack=&who=&domain=` pour rejouer, `?prefill=0` pour le formulaire « nouveau projet ») ; l'assistant de Piloter remplace le sélecteur d'archétype par les trois questions et affiche la déduction, le sélecteur reste le repli d'un hôte ancien.
  - Vérification : `tests/unit/cli/test_cmd_init.py` (trois questions en interactif, aucune en `-y`, même archétype CLI et cockpit, session simulée : Python nu → `stack` + `stack-python` seuls, `up` lit les besoins déduits), `tests/test_cmd_init.py` adapté, `tests/unit/tools/test_forge_routes.py`. Documentation : `docs/guides/expertises.md` (section wizard), `docs/tutoriels/onboarding.md`, `docs/tutoriels/premier-projet.md`.
  - Non fait : les expertises recommandées ne sont pas attachées par `init` (commande `grimoire expertise add` affichée) ; l'e2e Playwright du wizard cockpit n'a pas été rejoué localement (Playwright absent du venv).

- feat(hosts): contrôle de conformité des skills à la spec Agent Skills (#874) — `src/grimoire/hosts/skill_spec.py` vérifie `name` (charset, tirets, longueur, identique au dossier), `description` (1 à 1024), `compatibility` (1 à 500), corps (moins de 500 lignes) et références (relatives, sans chaîne de références au-delà d'un niveau depuis `SKILL.md`, liens des blocs de code ignorés) ; un fichier illisible est un écart, pas une exception. `grimoire doctor` signale un skill de projet non conforme en WARN, jamais en FAIL (`src/grimoire/cli/doctor_skills.py`). Les champs optionnels `license` et `compatibility` sont transmis à l'émission Claude Code et Copilot quand ils sont présents. Tests : `tests/unit/test_skill_spec_conformance.py` (dont tous les skills livrés) et `tests/unit/cli/test_doctor_skill_spec.py`.

- fix(hosts): une attente porte sur sa tâche, elle ne gèle plus la session (WAIT-SCOPE). Constat sur la Forge (3.64.0, 2026-10-23) : (1) le coordinateur faisait `task ask` sur la tâche de sa session ; passé le TTL du bail, le frein du bail refusait TOUT Bash (« la tâche T est garée… seules la reprise et la lecture de cette tâche passent ») alors que `task resume` est refusé à juste titre sur un `waiting(human)` : impossible de rattacher une autre tâche, de lancer `grimoire cockpit serve` pour que l'humain réponde, ou de travailler ; (2) `task wait --reason ci` pendant que des sous-agents (même session) travaillaient les arrêtait par le même refus ; (3) la commande de réponse proposée n'était pas exécutable telle quelle ; (4) MCP `task_update(action="resume")` refusait faute d'epoch alors que la CLI le prend.
  - Changement : `hosts/decisions/lease_brake.py` — `_parked` ne refuse plus ; une tâche garée (ci, quota, paused, human) n'est plus « la tâche courante » (`held_task_id` et `resolve_active_task` ne la nomment déjà pas) et la session est jugée comme une session sans tâche, avec les consignes du repli normal ; `grimoire task …` sur n'importe quelle tâche et `cockpit serve` passent. Choix le plus sobre pour les sous-agents : le même, sans cas particulier (ils partagent le journal de session, donc ils ne sont plus gelés). `TaskService.parking_warnings` : `task wait` / `task ask` (CLI et MCP, champ `warnings`) préviennent quand des sous-agents de la session tiennent des claims. `missions.waiting.answer_command` / `answer_hint` : la commande `grimoire task answer <T> "<réponse>" --question <q-id>`, avec le rappel du binaire du projet, dans les refus de reprise `waiting(human)`, de réservation et de `task resolve`. `TaskService.resume` : sans `--epoch`, l'epoch du claim quand la session de l'environnement est celle qui le porte (CLI et MCP) ; sinon l'erreur nommée est inchangée.
  - Inchangé : un agent n'écrit pas au nom d'une tâche garée qu'il ne tient plus, ne lève jamais lui-même une question humaine (`ledger_rules`), gates et journal de preuve intacts ; le bail d'une tâche garée dans son TTL passe comme avant ; epoch périmé, annulation et `--no-verify` refusent toujours.
  - Rouge-avant / vert-après : `tests/unit/missions/test_wait_scope.py` (24 cas ; 23 rouges sur origin/main). `test_kanban_resume_frictions.py` : « la session garée reste freinée » devient « n'est plus gelée ».
  - Non fait : `task ask --from` (lot) n'émet pas l'avertissement sous-agents ; une session garée peut écrire comme une session sans tâche (le profil governed décide au Stop) ; la commande de réponse n'est pas imprimée par `task ask` lui-même.
- feat(memory): chaque résultat de mémoire dit d'où il vient, de quand il date et s'il est encore vrai (EKP-E1, cadrage portail de connaissance §5, D2/D5/D11/D13). Constat : `provenance` et `freshness` étaient déclarés sur `MemoryEntry` sans être alimentés, `freshness` figé à `"current"` ; `supersede` n'avait aucun appelant hors tests ; le profil `standard` annonçait une fusion qui n'a pas lieu sans moteur vectoriel.
  - Fiche `grimoire.knowledge.fact/v1` (`memory/knowledge.py`) posée dans `provenance` (l'export JSON garde ses clés) : `source.kind/uri/empreinte` (sha256 du fichier source à l'indexation), dates source/indexation/confirmation, cycle. `source` n'est jamais vide (`saisie libre` ou `inconnue` explicites).
  - Fraîcheur recalculée à chaque lecture (`a_jour`, `perime`, `source_disparue`, `non_verifie`, `hypothese` ; paliers 90/270 jours du transverse, aucune durée de vie) : relecture locale bornée (4 Mio, confinée au projet) ; un fait sans source à relire ou une source non relisible n'est jamais à jour. `source_disparue` n'est pas servi par défaut (`--include-stale`, nombre écarté annoncé).
  - `grimoire memory search` (texte et JSON, `--include-stale`), outil MCP `grimoire_memory_search` (`include_stale`, `retrieval_in_service`), `memory remember --source` / `grimoire_memory_store(source=)`, nouveau `grimoire memory supersede` et outil `grimoire_memory_supersede`, espace Mémoire du cockpit (badge, date, lien Source, cases « décisions remplacées » / « sources disparues »), `memory status` et cockpit affichent la recherche réellement en service ; libellé du profil `standard` corrigé. Les synchronisations docs/code comparent aussi l'empreinte de la source (un changement au-delà de la troncature à 8 000 caractères n'était pas resynchronisé).
  - Rouge-avant / vert-après : `tests/unit/memory/test_knowledge_freshness.py` (29 cas, 28 rouges sur origin/main), `tests/unit/tools/test_workspace_memory.py`.
  - Score : le cockpit affichait « score 0.000 » pour un BM25 réel de l'ordre de 1e-6 (terme présent dans presque toutes les entrées) ; le score n'était pas perdu mais arrondi. `web/workspace/score.js` le nomme (`BM25`, `rang RRF`), passe les très petites valeurs en notation scientifique et dit « score inconnu » pour un score absent ou nul, jamais 0.000 ; l'API rend `scoreKind`. Tests : `tests/unit/test_workspace_score.py` (node), `tests/unit/tools/test_workspace_memory.py`.
  - Non fait : découpage par section et manifeste d'ingestion (E2) ; capture cockpit à valider par Guilhem.
- refactor(memory): retrait de `MemoryManager.progressive_search`, que rien n'appelait (EKP E0). Constat : `progressive_search()` (niveaux L1/L2/L3 par budget de tokens) n'avait aucun appelant dans `src/`, `framework/`, `tests/`, `web/`, les emitters d'hôtes ni les prompts et skills livrés ; seule sa docstring et la section « Progressive search » de `docs/explications/memoire.md` la décrivaient, donc le manuel annonçait une fonction absente de la surface réelle.
  - Changement : suppression de la méthode, de `_TOKEN_LIMITS`, de `_trim_to_budget` (propre au seul appelant) et de la section du manuel ; `ClassVar` retiré des imports de `memory/manager.py`. Le `_trim_to_budget` de `missions/recall.py` n'est pas touché. Redis (`RedisHotMemory`) et la pile RAG de `framework/tools/` restent en place, hors de ce lot.
  - Rouge-avant / vert-après : `grep -rn progressive_search` renvoie `src/grimoire/memory/manager.py:1253` et `docs/explications/memoire.md:583` (`_scratch/aidlc-prs/EKP-E0.red.txt`), puis rien (`EKP-E0.green.txt`). Aucun test n'a porté sur la méthode, donc la preuve est la disparition de la référence et une suite verte, pas un test rouge.
  - Cliquet : `scripts/code-ratchet-baseline.json` rebasé en mode shrink-only (`--rebaseline`) : total des modules figés 44394 -> 44392 lignes (`framework/tools/stigmergy.py` et `tool-resolver.py`, -1 ligne chacun), et `src/grimoire/core/agentic_standard.py` 1916 -> 1808 lignes (cause non examinée : baisse antérieure à ce lot, non enregistrée au baseline). Ces baisses ne viennent pas de ce lot ; `memory/manager.py` (1308 -> 1239 lignes) n'est pas mesuré par le cliquet (sous le seuil de 1500 lignes, hors `framework/`).
  - Non fait / limites : `ruff format` signale du drift de formatage déjà présent sur `main` dans `memory/manager.py` (non corrigé ici, hors périmètre) ; les échecs de `tests/unit/` (34 bancs `test_bench_*`, sous un worktree placé dans la Forge, et 6 connus de `test_protocol_revision` et `TestRedisNamespace`) sont identiques sur `origin/main`.
- docs: cadrage du portail de connaissance local (EKP, RAG, CAG, KAG), `docs/cadrage/portail-connaissance.md` (audience `décision`, EKP-00). Constat, relu sur `main` 3.64.0 : le socle lexical FTS5 et la fusion RRF sont branchés, mais le contexte préchargé se réduit à l'injection du SessionStart (`context-pack` est un manifeste que rien ne relit), aucun graphe n'est interrogeable depuis un agent (`CodeGraph.impact_query` et `Neo4jCodeGraphBackend.query_impact` n'ont aucun appelant), et `MemoryEntry.freshness` vaut toujours `"current"` sans calcul.
  - Changement : vision (questions sourcées et datées, un ou plusieurs projets), modèle conceptuel (briques EKP, trois modes CAG, RAG et KAG, routeur à règles sans modèle), tableau existant/cible brique par brique avec fichiers, architecture à trois niveaux au même contrat, gouvernance (fiche de fait, fraîcheur calculée à la lecture, « inconnu ≠ vrai », oubli, frontières entre projets après #172), évaluation déterministe sans banc payant, lots E0 à E9, treize décisions avec recommandation, sources classées publié / indépendant / éditeur. Lien depuis `docs/index.md` et la nav (Explications › Cadrages).
  - Vérification : `check-doc-audience`, `check-doc-claims`, `check-doc-commands`, `test_no_dangling_tool_refs`, `mkdocs build --strict`, `grimoire gates repo`.
  - Non fait : aucun code ; aucune mesure (les gains de qualité entre niveaux restent à mesurer par le lot E3) ; les décisions D1 à D13 attendent Guilhem.
- fix(doctor): un serveur MCP distant en https sans port explicite n'est plus un FAIL « has no port ». Constat sur la Forge après passage en 3.65.0 : `context7` (`https://mcp.context7.com/mcp`) et `github` (`https://api.githubcopilot.com/mcp/`) sortaient en échec, avec une remédiation `grimoire mcp register` hors sujet pour un serveur tiers.
  - Changement : `cli/doctor_mcp.py` — sans port dans l'URL, le port est celui du schéma (80 pour http, 443 pour https) ; seule une URL inutilisable (port illisible, schéma autre que http(s), hôte absent) reste un FAIL, nommé « invalid url » ; un serveur injoignable reste un WARN, avec la remédiation `grimoire mcp` seulement pour un hôte local.
  - Rouge-avant / vert-après : `tests/unit/tools/test_mcp_processes.py` (2 cas rouges sur origin/main, 43 verts avec `test_cmd_mcp.py` et `test_hosts_opencode.py`).
- feat(orchestrate): lot 0 de #876 — débloquer le tick sans rien lancer (créneaux CI, critères V0, contrat d'hôte, Stop sur les lectures)
- **Créneaux CI rendus** (`src/grimoire/missions/ci_slots.py`) : le tick lit `gh pr checks task/<id>` et émet `task.ci_pushed` puis `task.ci_finished` ; une migration unique (marqueur `capacity.ci_migrated`) libère les créneaux des tâches sans bail vivant, sans PR ni check en cours (un `gh` en échec est `unknown`, jamais « aucune PR » : rien n'est libéré ni marqué) ; une tâche clôturée ou annulée dont le push est vu rend son créneau sans `gh`. Sur le ledger du kit, `orchestrate tick --plan` passe de 50/3 à 4/3 runners occupés (les 4 restants sont des baux vivants).
- **Critère couvert par un `--check`** (`missions/verifiability.py::_couvert_par_commande`) : un critère ambigu qui cite la commande déclarée, un de ses fichiers ou son numéro (`#763`) ET énonce un verdict (passe, vert, fusionnée, sortie 0…) devient V0 ; citer sans verdict (« est bien conçu ») ne suffit pas ; un critère de revue reste V1 ; `task add` affiche la classe et nomme chaque critère non reconnu.
- **Contrat d'hôte resondé par version** (`src/grimoire/hosts/contract_probe.py`) : une version de Claude Code sans preuve déclenche, à la récolte appliquée et **sur opt-in seulement** (`GRIMOIRE_HOST_PROBE=1` : la sonde fait deux appels payants), une sonde témoin + refus ; sans opt-in la reprise reste en `waiting(human)` et la question dit comment l'activer ; la preuve est consignée par version exacte (`~/.grimoire/control/host-contracts.json`), un échec laisse `pretooluse_deny` à faux et se rejoue après 24 h, jamais de plage présumée ; jamais en aperçu ni en plan du tick. `grimoire host status` affiche la version sondée et la date (`--probe` pour sonder). Sondé réellement sur 2.1.309 le 2026-10-23 : frein prouvé.
- **Stop sans faux « hors tâche »** (#875, `hosts/decisions/tool_facts.py`, `evidence_trace.py`) : `cd x && grep`, `sed -n`, `awk | sort | uniq -c`, `git grep`, `command -v` sont en lecture ; `sed`, `awk`, `sort`, `git remote` sont en liste blanche stricte (tout `w`/`e` de sed, `-f`/`getline`/`system`/`>` d'awk, `--compress-program`, `git remote -v add`, toute substitution `$(…)` ou backtick sont des mutations) ; un Write hors de `project_root` ne compte plus.
- Tests : `tests/unit/missions/test_ci_slots.py`, `test_verifiability.py`, `test_task_verify.py`, `test_tick_recovery.py`, `tests/unit/test_host_contracts.py`, `test_tool_facts.py`, `test_session_enrolment.py` (faux `gh`, faux `claude`, aucun appel réseau).
- feat(cockpit): route d'attention `GET /api/workspace/attention` et `attention_order` (P22, #719). Constat (revue UX, B1) : la liste « À traiter » ne connaissait que le kit en retard, le projet non initialisé et la CI rouge, calculés dans le navigateur ; un checkpoint destructif en attente ou des tâches en revue ne faisaient pas apparaître le projet, et une sonde expirée le faisait disparaître de la liste.
  - Changement : `grimoire.tools.workspace_attention` recense, pour le projet servi, tout le registre (`scope=portfolio`) ou un projet du registre (`slug=`) : checkpoints de flow non décidés (tous les runs), questions `task ask`, revues dont le gate refuse la clôture (avec les refus), tâches bloquées (avec la raison), consignes non accusées, claims que plus aucune session ne tient (session terminée, vivacité inconnue en attention, claim expiré sans session), propositions en attente (lues sans resynchronisation), kits en retard, sondes en échec, projets illisibles. Lecture seule ; `probe=1` relance la sonde mémoire au lieu de lire son cache.
  - Ordre : `attention_order(cartes)`, fonction pure, range chaque élément dans une seule section (À décider, À débloquer, À réparer, À entretenir, Résolu automatiquement ; un type inconnu va dans « Autre ») : rang du type, plus ancien d'abord, puis ordre d'arrivée.
  - Lecture ratée ≠ zéro : un dossier disparu, un chemin absent du registre ou un ledger illisible donne un élément « projet illisible » avec sa raison ; une source qui casse (kit, checkpoints, propositions, mémoire) devient une sonde en échec ; une sonde mémoire expirée dit « état inconnu, sonde expirée ».
  - Services : un service mémoire déclaré (`memory_setup.configured_services`, dont dérive désormais `unreached_configured_services`) et requis par un projet qui a une session vivante ou une tâche en cours devient un élément après deux sondes négatives consécutives (`memory_setup.service_state` : arrêté, en échec, inconnu) ; deux lectures à moins de 5 s ne comptent que pour une sonde ; revenu, il passe en « résolu automatiquement à HH:MM UTC » pendant une heure.
  - Vérification : `tests/unit/tools/test_workspace_attention.py` (24 cas) avec la fixture `tests/fixtures/attention/cards.json` ; `tests/unit/test_workspace_routes.py` sert la route par l'atelier et par le cockpit.
  - Non fait : aucun écran ne lit encore la route (P32) ; « projet non initialisé » et « CI rouge » restent dans `watchReasons` de Piloter.
- feat(docs): thème Douce du manuel (identité D). Constat : le manuel gardait le thème sombre noir et orange, la police Geist et deux feuilles de style qui chargeaient Google Fonts, alors que le site et le cockpit sont en Douce. Les icônes (famille du site, `docs/assets/icons/`) et le sprite du cockpit (`scripts/build-icon-sprite.py`) lisent déjà la nouvelle famille depuis les lots A et cockpit.
  - Changement : `docs/stylesheets/douce.css` remplace `forge-theme.css` et `presentation-onepager.css` (plus aucune page ne l'utilisait, `presentation-onepager.js` sort avec) : tokens du site en clair et en sombre, Instrument Sans et JetBrains Mono auto-hébergées (`docs/assets/fonts/`, licences OFL), rayons 10 et 16, boutons en pilule, chevron rouille devant le nom, aucun `@import`.
  - Changement : `mkdocs.yml` : `theme.font: false`, deux palettes `default` et `slate` avec bascule, logo et favicon de `docs/assets/brand/`.
  - Vérification : `tests/unit/test_manuel_theme.py`, `tests/unit/test_workspace_icons.py`, `mkdocs build --strict` vert, aucune occurrence de `googleapis` ni `gstatic` dans le site construit.
- feat(cockpit): l'espace ouvert suit un flux en direct (Server-Sent Events) et dit sa fraîcheur (P10, #719). Constat : la page ne voyait un changement qu'au sondage suivant (`GRIMOIRE_PULSE_SECONDS`, 5 s par défaut), et une proposition écrite ne bougeait pas l'empreinte du pouls.
  - Changement : `GET /api/workspace/stream` (`workspace_routes.workspace_stream`, générateur `workspace_pulse.pulse_stream`) pousse la charge utile du pouls en `text/event-stream` : un événement `pulse` tout de suite, puis à chaque changement d'empreinte recalculée toutes les 0,5 s, et un battement `changed: false` par intervalle déclaré. L'empreinte surveille aussi `_grimoire-output/proposals/*.yaml` (contenu haché, comme les petits JSON). `cmd_cockpit.py` ne gagne que le branchement GET qui recopie le flux. `live.js` ouvre un `EventSource` (fermé onglet masqué, rouvert au retour) et se replie sur le sondage sans `EventSource` ou si l'hôte ne sert pas le flux ; l'info-bulle de la barre d'état dit « Flux en direct, confirmé au moins toutes les N s ».
  - Vérification : `tests/unit/tools/test_workspace_stream.py` (écriture dans le ledger, un journal de session ou une proposition → événement reçu en moins de 2 s par le vrai serveur du cockpit ; battement et reprise sur horloge factice) ; `tests/e2e/test_workspace_live_stream.py` (serveur à 30 s d'intervalle : la carte paraît sans rechargement en quelques secondes, « mis à jour il y a N s ») ; les tests de sondage de `test_workspace_live_pilot.py` tournent désormais sans `EventSource`, pour garder le repli prouvé.
- feat(cockpit): pilotage vrai — checkpoint pendant lu sur tous les runs non décidés, aperçu honnête, transitions réelles du board (P07, revue UX B2/M1/M4). Constat : un aperçu de mise à jour créait un run ordinaire (WFI-022) qui masquait le checkpoint destructif toujours pendant du run 021 ; le KPI « 0 flows » contredisait les runs listés par Observer ; chaque carte en cours proposait « → Bloquée » comme suite et « Fait » comptait les tâches annulées.
  - Aperçu : `FlowRunMeta.preview` (écrit seulement quand il est vrai), posé par `upgrade-flow run --dry-run` via `FlowEngine.run(preview=)` ; `list_flow_runs` ne rend jamais un aperçu. Message de la fiche : « Aperçu : sauvegarde créée, aucun fichier du projet modifié. »
  - Fiche Piloter : le checkpoint `destructive` non décidé se cherche sur tous les runs `project-upgrade` rendus, pas sur le seul dernier ; le KPI devient « runs de flow », lu sur `sheet.flowRuns` — `flow_runs_view`, la même fonction que `GET /api/workspace/flows/runs` (qui rend désormais aussi `total`).
  - Observer : le journal du Mission Ledger devient un flux de `read_events` (`EVENT_SOURCES`, jamais un span OTel) ; sans span mais avec un flux ou un run, l'espace montre l'activité enregistrée et la liste des runs (statut compris) au lieu de « Aucune trace ».
  - Exécuter : suite de carte lue sur le graphe du ledger (en cours → Revue, bloquée → Prête), « Bloquer… » comme geste à part (carte et inspecteur, champ de raison focalisé) ; colonne « Annulées » séparée et repliée ; carte bloquée avec le mot et la raison écrite au blocage (`blocked_reason`, posé par `annotate_cards` depuis `task.transitioned`, repli sur `board.blockers_of`).
  - Preuves : `tests/unit/test_flow_runs.py`, `tests/unit/cli/test_cmd_upgrade_flow_run.py` (un vrai `--dry-run` marqué `preview`, total inchangé), `tests/unit/test_workspace_api.py` (fiche et route identiques, raison de blocage), `tests/unit/tools/test_blueprint_telemetry.py` ; e2e `test_workspace_cockpit_upgrade_flow.py` (checkpoint pendant puis aperçu réel, rechargement : pastille et « Revoir dans l'IDE » toujours là, KPI = total d'Observer), `test_workspace_executer_board_moves.py`, `test_workspace_lot4_spaces.py` (Observer).
  - Non fait : `grimoire upgrade-flow review` lit encore le seul dernier run `project-upgrade` ; la Flotte affiche toujours le nombre de blueprints (`health.flows`) en « flow(s) ».
- fix(memoire): l'aperçu mémoire de flotte (`GET /api/workspace/memory/overview`) ne prend plus une lecture ratée pour un zéro, ne compte plus deux fois un store partagé et sonde les projets en parallèle (P09).
  - Lecture ratée : une sonde en échec ou expirée rend `entries: null` et sa raison (`error` de `memory_link_status`, `reason` de la ligne d'aperçu), plus `entries: 0`. Sur sonde expirée, `MemoryManager.health_check` nommait le backend d'après le projet (`backend=self._project_name`) : il porte désormais l'identifiant résolu du backend (`qdrant-server`…), repli sur le nom de classe.
  - Dédoublonnage : `store_identity(config, project_root)` (`grimoire.memory.manager`) rend l'emplacement (URL du serveur ou chemin résolu) et la collection que le backend ouvrirait ; `summary.totalEntries` ne compte chaque store qu'une fois.
  - Parallèle : un seul `MemoryManager` par projet, réutilisé par la sonde (`memory_link_status(..., manager=)`), l'index lexical et la dernière écriture (avant : trois ouvertures) ; projets sondés dans un pool borné à 8.
  - Mesure locale, 4 backends factices à 0,5 s, `memory_overview(projects=all)` : avant 2,03 s et 12 ouvertures de `MemoryManager` ; après 0,51 s et 4. Tests rouge-avant / vert-après dans `tests/unit/tools/test_workspace_memory.py` (5 cas, dont une barrière à 4 qui prouve l'exécution simultanée sans dépendre de la machine).

## [3.65.0] - 2026-10-23

- feat(workspace): composants partagés des espaces du cockpit, direction Douce (P21). Le badge, la tuile KPI, le tableau, l'état vide et l'en-tête d'espace n'ont plus qu'une implémentation chacun, dans `web/workspace/shell.css` (`.badge`, `.kpi`, `.tbl`, `.empty`, `.space-head`) et `shell.js` (`badge()`, `kpiRow()`, `spaceHead()`) ; les copies préfixées de Piloter, Exécuter, Observer, Mémoire et Concevoir sont retirées.
- Changement : le badge tient sur une ligne (`nowrap`, ellipse, texte complet en `title`) ; la tuile KPI devient une rangée de tuiles séparées, libellé au-dessus, et un statut sans chiffre (« à jour », « inconnue ») s'affiche en mot d'état sur le fond doux de son état ; l'état vide s'aligne à gauche, sans cadre, la commande dans un bloc copiable.
- Correction : un segment de la barre de document n'est plus jamais pressé et désactivé à la fois (`segment()` refuse `aria-pressed` à un bouton désactivé, et la feuille fait gagner le désactivé) ; Concevoir sans blueprint rendait sa vue active ainsi.
- Tests : `tests/unit/test_workspace_components.py` (aucune copie préfixée dans `web/`, `tests/`, `.design-sync/` ; badge `nowrap` + ellipse ; segment) et `tests/e2e/test_workspace_components.py` (DOM des cinq espaces, badge long rendu sur 22 px, segments désactivés jamais pressés) ; les sélecteurs des tests e2e existants suivent `.tbl`.
- feat(init): onboarding en soixante secondes — détection des piles nues, discovery par défaut, next steps dynamiques (issue onboarding audit 2026-09-18, PR 1/3 — voir aussi #onboarding). Audit vécu sur trois projets jetables (Python nu, Node nu, dépôt vide, kit 3.57.0) : `grimoire init -y`/`up` sans `-a` retombait systématiquement sur `minimal` sans jamais le dire, le wizard interactif ne choisissait jamais de pattern par défaut, et le panneau « Next Steps » était identique sur les 6 runs testés. Décision produit de Guilhem, en cours de revue : `resolve()` ne rend plus jamais `minimal` automatiquement — `minimal` reste accessible uniquement via `-a minimal` explicite (démo/test).
  - **Détection des piles nues** (`src/grimoire/core/archetype_resolver.py`) : nouvelle `recommend_naked_stack()` — le repli honnête pour « une pile sans domaine affirmé » (Python/Node/Rust/Go nu, bibliothèque, CLI, dépôt vide) est `stack` (Atlas, `archetypes/stack/archetype.dna.yaml` : le généraliste prévu exactement pour ce cas, plus un skill par technologie détectée — `stack-python`, `stack-typescript`, `stack-go`…), jamais `platform-engineering` (correction de relecture 2026-09-18 sur cette même PR). `platform-engineering`/`web-app`/`infra-ops` restent réservés à un domaine confirmé (frontend, IaC Terraform/K8s/Ansible) ou à un signal plus spécifique (`weak_signal_suggestion()` : bundler dans `package.json`, CI+tests). Nouveau champ `ResolvedArchetype.is_best_guess` : `False` pour `stack` sur une pile détectée non vide (c'est le choix correct, pas une supposition) — `True` uniquement pour un dépôt vide (aucun signal du tout) ou un signal faible (bundler/CI+tests).
  - **Discovery par défaut dans le wizard** (`src/grimoire/cli/cmd_init.py::_run_wizard`) : l'étape Archétypes défaut désormais sur `0` (discovery guidée, 3 questions oui/non) quand aucune règle n'a matché avec confiance, au lieu de `none` (→ minimal). Un pick automatique (`is_best_guess`) n'est plus jamais pré-rempli/affiché comme « détecté » dans la liste numérique — sinon le wizard confirmerait silencieusement une supposition (`stack` n'y figure de toute façon jamais : ce n'est pas une spécialisation du menu numéroté, seulement le repli). `_guided_discovery()` ne retombe plus jamais sur `minimal` en bout de chaîne (`stack`, pas `platform-engineering`).
  - **Mode express non-TTY signalé** : nouveau flag `-i/--interactive` sur `grimoire init` (déjà présent sur `up`) ; `grimoire init` bare sous stdin non-TTY affiche désormais « Express mode (no interactive terminal): `grimoire init --interactive` to rerun with guidance. » au lieu de basculer en silence sur le chemin express.
  - **Next Steps dynamique** (`src/grimoire/core/onboarding_panel.py`, nouveau module) : le panneau nomme l'archétype installé et pourquoi, puis au plus trois actions choisies selon l'état réel du projet (changer un pick automatique quand c'en est un, ouvrir le cockpit, démarrer la gouvernance, monter en gamme mémoire, première tâche gouvernée), plus une ligne nommant les capacités non exploitées (`grimoire --all --help`).
  - **Capacités partagées** (`src/grimoire/core/project_capabilities.py`, nouveau module) : source unique pour « qu'est-ce que ce projet n'exploite pas encore » (archétype, gouvernance, mémoire, cockpit), réutilisée par le panneau Next Steps et par le nouveau pied de page.
  - **Pied de page « Découvrir »** sur `grimoire doctor`/`status` (`src/grimoire/cli/app.py`) : une ligne nommant les capacités non exploitées avec leur commande d'accès, silencieuse une fois tout exploité.
  - **Ratchet de taille** (`scripts/check-code-ratchet.py`, R2, `cli/app.py` plafonné à 2801 lignes) : le check `policy_budget_guard` (issue #463) extrait de `app.py` vers `src/grimoire/core/policy_budget_guard_check.py` (même contrat, mêmes tests) pour libérer la marge nécessaire au pied de page et au nouveau flag `--interactive`. `app.py` : 2801 → 2743 lignes après tout ce lot.
  - **Lacune connue, vérifiée et rapportée plutôt que masquée** : `grimoire init -a stack` sur une pile nue attache aujourd'hui les SEPT skills de pile d'un coup (`archetypes/stack/skills/*.md`, copiés sans filtre par `ProjectScaffolder._plan_archetype_agents`/`_plan_stack_agents`), pas seulement celui de la techno détectée — un projet Python nu reçoit `stack-python` **et** `stack-go`/`stack-docker`/`stack-terraform`/`stack-ansible`/`stack-k8s`/`stack-typescript`. La raison affichée nomme le skill pertinent (`stack-python`) sans prétendre qu'il est seul attaché. Résolu dans ce même lot par l'entrée `feat(expertises)` ci-dessous (#618) : le scaffold ne copie plus que les skills de la pile détectée.
  - Tests rouge-avant sur tous les points ci-dessus : `tests/test_archetype_resolver.py` (dont `TestNeverMinimalAutomatically`, paramétré sur Python/Node/Rust/Go/vide/Java/Ruby, et `TestRecommendNakedStack` vérifiant `stack`+skill nommé, jamais `platform-engineering`), `tests/test_project_capabilities.py`, `tests/test_onboarding_panel.py` (nouveaux fichiers), `tests/test_cmd_init.py` (wizard, notice non-TTY, suggestion express, panneau dynamique), `tests/unit/cli/test_app.py` (pied de page doctor/status, archétype par défaut `stack`). `ruff check` et `mypy --strict` verts sur tous les fichiers touchés.
  - Rejeu réel : trois projets jetables (Python nu, Node nu, dépôt vide) en `-y` et en interactif (`pexpect`, pty réel) — jamais `minimal` en sortie sauf `-a minimal` explicite, jamais `platform-engineering` en repli automatique, panneaux distincts par projet, parcours `init` → panneau sous 1 seconde. Rejeu en lecture seule sur la Forge (`grimoire doctor`/`status` depuis le venv du worktree) : 31/31 checks OK, pied de page pertinent (archétype `minimal` explicite de la Forge), aucune écriture (`git status` identique avant/après).
  - **Reste hors périmètre, prochaines PR sur le même sujet** (décisions produit de Guilhem 2026-09-18, en cours de revue) : PR 2 — vraie étape Mémoire dans le wizard (détection docker/services/embedding, profil recommandé = le plus complet que la machine peut servir, jamais câblé d'office) et fin du profil `--lite` (dépréciation avec avertissement, comportement = défaut) ; PR 3 — wizard complet (profil standard, hôtes, besoins d'exécution, langue, cockpit, MCP, un défaut recommandé par étape, chemin express appliquant les mêmes défauts complets). Filtrage des skills de pile par technologie détectée (lacune ci-dessus) à planifier séparément.
  - **Corrections de relecture Copilot (PR #617)** : (1) le pied de page « Découvrir » de `grimoire doctor`/`status` (`src/grimoire/cli/app.py`) ne respectait pas `GRIMOIRE_NO_COCKPIT` — il suggérait `grimoire cockpit` même l'opt-out actif ; honore désormais `unexploited_hints(..., no_cockpit=...)` sur les deux commandes. Même défaut trouvé (hors périmètre de la remarque, même classe) dans `run_init()` (`src/grimoire/cli/cmd_init.py`, partagé par `init` et `up`) : le rapport « Next Steps » gardait la suggestion cockpit malgré la variable d'environnement, seule `_maybe_register_cockpit` la lisait — `no_cockpit` est désormais résolu une fois, tôt dans la fonction, pour tous les consommateurs. (2)(3) `recommend_naked_stack()` citait une liste de marqueurs fixe par langage (« pyproject.toml/setup.py/requirements.txt » pour tout projet Python, « Dockerfile » pour tout projet Docker) alors que le scanner reconnaît chaque pile via plusieurs marqueurs alternatifs (`poetry.lock`, `Pipfile`, `.python-version`, `docker-compose.yml`, `.dockerignore`…) — nouvelle `_markers_for()` dérive le texte du `StackDetection.evidence` réellement matché ; nouvelle branche `docker` dédiée (retirée de `weak_signal_suggestion` par la correction de relecture 2026-09-18, un Dockerfile seul n'étant plus un signal infra). (4) `build_next_steps()` (`src/grimoire/core/onboarding_panel.py`) sondait `cockpit_registered()` une seconde fois après que `unexploited_hints()` l'avait déjà fait en interne — I/O dupliquée et risque d'incohérence si le registre change entre les deux appels ; dérivé désormais de `hint_by_command` déjà calculé. Tests rouge-avant sur les quatre points (`tests/unit/cli/test_app.py::TestDiscoverFooter`, `tests/test_cmd_init.py::TestInitNoCockpit`, `tests/test_archetype_resolver.py::TestNakedStackReasonCitesRealMarkers`, `tests/test_onboarding_panel.py::TestCockpitProbeIsNotDuplicated`). Rejeu réel sur quatre projets jetables (`_scratch/onboarding-audit/` : Python nu, Node nu, dépôt vide, Docker nu sans Dockerfile) : plus aucune suggestion cockpit sous `GRIMOIRE_NO_COCKPIT=1`, raisons citant les marqueurs réels (`package.json (jest)`, `docker-compose.yml, .dockerignore`…). `ruff`, `mypy --strict` et le ratchet restent verts.
  - **CI E2E rouge, régression réelle, pas un flake** : `E2E (chromium, ubuntu-latest)` (`tests/e2e/test_workspace_source_language.py`) — d'abord classé flake à tort (verdict revu sur demande de Guilhem), confirmé régression par bissection locale sur cette même branche : passe à `83a3f629`, échoue à `f1b992d7` et `e38b4ba0`, échoue encore identiquement à la relance CI. Cause exacte, reproduite en local (Playwright + Chromium dans le venv du worktree) : le repli des piles nues vers `stack` (Atlas) installe sept skills de pile (`stack-ansible.md`…`stack-typescript.md`) et un agent `stack-engineer.md` au lieu des deux fichiers `platform-engineering` d'avant cette PR (`backend-engineer.md`, `platform-architect.md`, `archetype.dna.platform-engineering.yaml` → `archetype.dna.stack.yaml`) — diff exact de `_grimoire/kit/` entre les deux archétypes sur un projet jetable identique : `-agents/backend-engineer.md -agents/platform-architect.md -archetype.dna.platform-engineering.yaml +agents/stack-engineer.md +archetype.dna.stack.yaml`, et sous `skills/` deux fichiers `platform-*` remplacés par sept `stack-*`. L'arbre plus long/différent déplace `_pick_untouched_kit_file()` (`tests/e2e/test_workspace_source_language.py`, `test_workspace_source.py`, `test_workspace_controls.py`, `test_workspace_source_assist.py` — même helper dupliqué dans les quatre) sur des lignes plus profondes de l'arbre Source (`_grimoire/kit/agents/stack-engineer.md`, dernière ligne du dossier `agents/`, juste avant le prochain dossier). À cette profondeur, cliquer la ligne via `Locator.click()` — coordonnées écran calculées sur `getBoundingClientRect()` — atterrit sur la ligne suivante de l'arbre : reproduit à l'identique via `document.elementFromPoint()` sur le centre exact du rectangle de la ligne visée, et via un vrai clic souris émis au niveau CDP (`page.mouse.click`) — jamais un défaut de contenu ou de l'API (`/api/workspace/files`, `/api/workspace/file?path=…` répondent correctement, 200, contenu correct pour `stack-engineer.md`) : dérive sous-pixel de rendu Chromium sur une longue liste flex, exposée par cette PR (arbre plus profond) mais pas causée par un défaut applicatif du gabarit `stack`. Corrigé à la cause réelle du flake — pas en changeant le fichier ciblé, ce qui n'aurait fait que déplacer le risque vers un futur changement d'archétype — mais en rendant `_open()` indépendant du hit-testing de coordonnées écran : `Locator.dispatch_event("click")` envoie l'événement directement à l'élément déjà résolu par le locator, dans les quatre fichiers ci-dessus. Vérifié : `pytest tests/e2e` complet, 149 passed/1 skipped (contre 5 échecs déterministes avant correctif, sur trois rejeux consécutifs).
- feat(expertises): catalogue d'expertises langages, ingénierie et cloud, détection et attache à la demande (issue #616). Guilhem : « il me manque des expertises en dev, design patterns, code haut et bas niveau (Rust, Go, C, C++, C#, Java…) à choisir par l'utilisateur, et des compétences cloud (AWS, Azure, GCP, OVH…) ». Réutilise le mécanisme existant (agent porteur + skill attaché, issue #375) plutôt que d'en inventer un second — aucun skill ne s'attache jamais par défaut sur ce chantier, seul un choix explicite ou une détection nommée le fait, conformément à la doctrine des artefacts (`docs/artifact-doctrine.md`).
  - **Registre** `registry/expertises.yaml` (28 entrées, trois familles) : `languages` (python/typescript/go réutilisent tels quels `archetypes/stack/skills/stack-*.md` ; rust, c, cpp, csharp, java, kotlin, swift, php, ruby, scala, elixir sont nouveaux), `engineering` (design-patterns, architecture-haut-niveau, systemes-bas-niveau, testing-avancee, securite-applicative, performance — jamais recommandées automatiquement, `detect: null`), `cloud` (aws, azure, gcp, ovh, hetzner, cloudflare, scaleway, kubernetes-managed — ce dernier sans détection fiable pour l'instant, faute de détecteur par nom de ressource Terraform : catalogué et attachable à la main, issue de suivi pour la recommandation automatique). Chaque entrée déclare `porteur` (agent visé, `stack-engineer` pour langages/ingénierie) et `porteur_fallback` (`stack-engineer` pour le cloud sur un projet sans `infra-ops`).
  - **25 nouveaux skills** sous `framework/expertises/skills/` (jamais copiés en bloc par le scaffolder, contrairement à `archetypes/*/skills/` — un skill n'y devient effectif pour un projet que via `grimoire expertise add`), au format des sept `stack-*` existants (frontmatter `description`/`tools`, principes vérifiables, garde-fou, workflows, checklist de revue) : rust, c, cpp, csharp, java, design-patterns, architecture-haut-niveau, systemes-bas-niveau, aws, azure, gcp (première vague), kotlin, swift, php, ruby, scala, elixir, testing-avancee, securite-applicative, performance, ovh, hetzner, cloudflare, scaleway, kubernetes-managed (seconde vague, livrée dans le même chantier).
  - **Nouveau module** `src/grimoire/core/expertises.py` : `load_catalog()`, `detect_expertises(project_root)` (réutilise `grimoire.core.scanner.StackScanner` pour les marqueurs de langage déjà connus, ajoute une détection de bloc `provider "<nom>" {}` Terraform pour le cloud), `attach_expertise`/`detach_expertise` (écrivent/nettoient un override partiel `extends: kit` sur l'agent porteur, même mécanisme que `grimoire.core.override_drift.convert_override` — validation par `collect_agents`/`collect_skills` avec restauration de l'état précédent en cas d'échec ; `detach_expertise` supprime aussi le fichier de skill, pas seulement la référence, pour ne jamais le laisser transversal). API documentée pour la PR 3 (wizard, étape « Expertises ») dans `docs/expertises.md`.
  - **Nouvelle commande** `grimoire expertise list [--detected] / add <id>… / remove <id>…` (`src/grimoire/cli/cmd_expertise.py`, enregistrée en lazy-loading dans `app.py`). `add`/`remove` écrivent uniquement sous `_grimoire/overrides/` — jamais sous `_grimoire/kit/`, régénéré en entier par `grimoire up`, qui conserve donc la sélection sans rien y écrire. La projection sur les hôtes reste `grimoire host sync`, déjà capable de lire ces overrides.
  - **Correctif du mécanisme historique (#375), trouvé en construisant ce catalogue** : les sept skills `stack-*` s'attachaient en bloc dès que l'archétype `stack` était installé (`ProjectScaffolder._plan_archetype_agents`/`_plan_stack_agents` copiaient tout `archetypes/stack/skills/` sans filtre, et `stack-engineer.md` déclarait les sept en dur) — un projet Python nu recevait stack-go/typescript/docker/terraform/ansible/k8s attachés à `stack-engineer` (déclarés en dur dans son frontmatter, donc chargés à chaque tour de cet agent) sans jamais les utiliser. `stack-engineer.md` déclare désormais `skills: {{stack_skills_yaml}}`, résolu au scaffold par la nouvelle `ProjectScaffolder._detected_stack_skill_slugs()` (mapping direct sur `StackScanner`, zéro second mécanisme de détection) : seule la pile réellement détectée s'attache par défaut, le reste reste disponible via `grimoire expertise add <id>`. `grimoire up` construit son scaffolder sans re-détection (`scan=None`, `cmd_up.py`, non touché par ce chantier) : `_detected_stack_skill_slugs()` préserve alors ce que le fichier kit-tier `stack-engineer.md` déclare déjà plutôt que de le recalculer à vide — un `up` sans changement de pile ne régresse jamais la sélection installée. Rouge-avant vérifié (`tests/test_scaffold.py`, trois nouveaux tests + un existant corrigé pour refléter le vrai contrat) et reproduit en conditions réelles sur un projet jetable (`grimoire init . -a stack -y` sur un Python nu → `skills: ["stack-python"]` au lieu des sept ; `grimoire up` → sélection inchangée ; `doctor` 22-28/28).
  - **Preuve de bout en bout** sur un projet jetable Rust + Terraform AWS : `grimoire expertise list --detected` recommande `rust` (marqueur `Cargo.toml`) et `aws` (bloc `provider "aws" {}`), chacun avec une raison nommée ; `grimoire expertise add rust aws` attache les deux à `stack-engineer` (pas d'`ops-engineer` installé → repli `porteur_fallback` exercé) ; `grimoire host sync` projette ; `grimoire up` conserve l'override sans y toucher (`override_review: 1 override(s) to review — keep, convert, or remove`).
  - Tests : `tests/unit/core/test_expertises_catalog.py` (chaque entrée pointe un skill existant à frontmatter valide, porteur/repli résolvable vers un agent réellement livré par `stack`/`infra-ops`, ids uniques, familles reconnues), `tests/unit/core/test_expertises_detection.py` (scénario Rust+AWS, famille `engineering` jamais recommandée, faux positif de provider Terraform exclu), `tests/unit/core/test_expertise_attach.py` (attache/détache idempotentes, validation fail-closed, non-fuite transversale au détachement, cohabitation de plusieurs expertises), `tests/unit/cli/test_cmd_expertise.py`, `tests/test_scaffold.py` (régression #375 et non-régression de `grimoire up`). `ruff check`, `ruff format` et `mypy --strict` verts sur tous les fichiers touchés ; `scripts/gen-kit-hashes.py --check` vert après régénération du catalogue (963 empreintes).
  - Reste pour une issue de suivi : détecteur par nom de ressource Terraform pour recommander `kubernetes-managed` automatiquement (le skill et l'attachement manuel fonctionnent déjà) ; extension de la garde `tests/unit/test_artifact_employment_clause.py` (`use_when`/`dont_use_when`) aux skills livrés, actuellement limitée aux agents (discussion issue #368, non traitée par ce chantier).
  - **Revue de PR** (#618) : `console = Console(stderr=True)` dans `cmd_expertise.py`, convention du dépôt pour ne jamais polluer `-o json` sur stdout ; `expertise add`/`remove` interceptent désormais `GrimoireRegistryError` sur `load_catalog()` comme le fait déjà `expertise list` (refus nommé + `typer.Exit(1)`, au lieu d'une trace Python) ; `detach_expertise` (`core/expertises.py`) supprime aussi un fichier de skill d'override orphelin (`_grimoire/overrides/skills/<slug>.md`) quand l'override d'agent porteur a disparu autrement — sinon il redevient transversal, chargé à chaque tour, sans qu'aucun agent ne le référence (même défaut que #375) ; typo « pas tiée » → « pas liée » dans les descriptions de `expertise-performance`, `expertise-securite-applicative`, `expertise-testing-avancee`. Durcissement CodeQL `py/path-injection` (alertes 599/600, `docs/security/code-scanning-triage-2026-09.md`) : `_detected_stack_skill_slugs()` (`core/scaffold.py`) validait déjà les slugs relus depuis le frontmatter d'un agent installé par appartenance à l'ensemble fermé des slugs `stack-*`, mais pas par leur forme — nouveaux `_SKILL_SLUG_RE` et `_safe_skill_source()` (garde de forme + confinement `_ensure_inside_root`, #573) avant toute construction de chemin ; les deux alertes elles-mêmes pointent en réalité sur `self._target` (projet déjà enregistré, sélectionné depuis le cockpit), une classe de faux positif déjà documentée, dismissée en conséquence. `rust-gate` (requis) rouge sur `rust-hosts / parity` depuis le tout premier commit de cette PR, cause distincte trouvée en investiguant : `archetypes/stack/agents/stack-engineer.md` porte `skills: {{stack_skills_yaml}}` non guillemeté — invalide en YAML nu (`{{...}}` ouvre un mapping flow) tant que le scaffolder n'a pas substitué la valeur ; le parseur Python de référence avalait l'exception et rendait un frontmatter vide (`tools_origin` bascule de `declared` à `inferred`), le cœur Rust non. Corrigé à la source : `skills: "{{stack_skills_yaml}}"` (guillemeté, valide en YAML brut) ; `_render_placeholders` (`core/scaffold.py`) fait sauter les guillemets à la substitution pour cette seule clé (elle porte une liste, pas une chaîne) sans toucher aux autres placeholders guillemetés du dépôt (`name: "{{agent_tag}}"` etc., gardés par `tests/unit/mcp/test_server.py::TestAddAgent`, rouge le temps d'une variante plus générale de ce correctif). Vérifié avec le cœur Rust compilé localement (`maturin build --release` + `tests/unit/test_hosts.py tests/unit/test_hosts_rust_parity.py`, vert) et suite `tests/unit/` complète (verte, cœur chargé).
- feat(init): étape Mémoire — profil le plus complet que la machine peut servir, santé vérifiée, fin du profil `--lite` (issue #616, PR 2/3 — suite de la PR 1 ci-dessus). L'audit avait montré que `grimoire init -y`/`up` sans `-a` retombait toujours sur `lexical`, même quand un Weaviate détecté sur `localhost:8080` aurait permis mieux, et que le seul mécanisme d'activation (`grimoire memory up --profile standard --apply`) retombait lui aussi en `lexical` faute de service serveur — aucun chemin ne donnait de vecteurs locaux sans Docker.
  - **Détection élargie** (`src/grimoire/tools/memory_setup.py`) : `docker_daemon_reachable()` sonde le démon (`docker info`), pas seulement le binaire sur `PATH` (`shutil.which` seul déclarait « disponible » un Docker Desktop éteint) ; `local_embedding_available()` teste fastembed/sentence-transformers installés ou un Ollama joignable ; `recommend_profile()` en déduit le profil le plus complet servable : `complet` si Docker répond, `standard` (Qdrant embarqué, aucun service) avec une capacité d'embedding locale mais sans Docker, `lexical` en dernier recours (« no local embedding capability »).
  - **`standard` sait enfin donner des vecteurs locaux sans service** : `build_memory_plan()` retombe sur le backend embarqué `qdrant-local` (fichier local, zéro dépendance réseau) quand aucun serveur Weaviate/Qdrant ne répond mais qu'un moteur d'embedding local est installé — au lieu de `lexical` par construction, quel que soit l'état de la machine.
  - **`grimoire memory up --start`** (`src/grimoire/cli/cmd_memory_ops.py`) : démarre, via les gabarits Docker Compose déjà livrés par le kit, les services que `graphe`/`complet` réclament et que la machine ne sert pas encore (Weaviate + Neo4j, puis Redis pour `complet`) — jamais implicite, seulement sur ce drapeau explicite ; `standard`/`lexical` n'ont rien à démarrer (Qdrant embarqué). Nouvelle fonction partagée `start_memory_stack()`.
  - **Étape Mémoire de `grimoire init`, express et interactive** (`src/grimoire/cli/cmd_init.py`) : dès qu'un moteur d'embedding local est installé et que Docker répond, le profil appliqué est `complet` — jamais un service simplement détecté (issue #496 intacte : le backend qu'écrit `complet` est toujours la connexion par défaut du kit et une collection propre au projet, jamais une URL lue sur ce que le probe a trouvé). Sans Docker : `standard` (Qdrant embarqué, aucun service) avec un moteur d'embedding local, `lexical` sinon. L'étape se termine par une vérification de santé structurelle (jamais un chargement de modèle d'embedding — ce coût reste celui du premier usage réel, `grimoire memory status`) reprise dans le rapport (`Memory:`, raison, commande pour monter d'un profil) et en JSON (`memory_profile`).
  - **Arbitrage produit 2026-09-18 (Guilhem, sur #619) : le potentiel `complet` s'exploite tout de suite, pas seulement configuré.** (1) Interactif : choisir `complet` déclenche « Démarrer la pile mémoire complète (Weaviate + Neo4j + Redis) maintenant ? », défaut **oui** — Entrée démarre via les gabarits Docker Compose du kit, attend la santé, écrit `complet`. (2) `-y` vaut consentement explicite : quand Docker répond, la pile démarre et `complet` est appliqué, sans aucune question. (3) Hors terminal et sans `-y` (script, CI) : aucun conteneur démarré, `complet` tout de même configuré (backend et connexions écrits), la commande de démarrage affichée, et `grimoire doctor` signale « pile mémoire non démarrée ». (4) Sans Docker : `standard` embarqué, le rapport dit ce qui manque pour monter. `--memory-stack up` reste un consentement explicite équivalent pour un run non-interactif sans `-y` pour d'autres raisons. Nouvelle `grimoire.tools.memory_setup.unreached_configured_services()` : ce que le projet déclare mais qui ne répond pas encore — utilisée par `grimoire init`, `grimoire doctor` (nouveau check `memory_stack_check`) et `grimoire memory doctor`.
  - **`grimoire memory doctor`** (`src/grimoire/cli/cmd_memory_ops.py`, nouvelle commande) : entretien minimal de la pile — santé des couches, pile démarrée ou non (même sonde que ci-dessus), dérive du graphe (nœuds `GrimoireMemory` sans fiche source dans le store durable, même sonde que `grimoire memory graph purge-orphans`, réutilisée telle quelle), purge sur confirmation (`--apply`, dry-run sinon) avec rappel de sauvegarde (`grimoire memory migrate export-bundle`) avant toute purge — jamais lancée automatiquement. Ne `FAIL` jamais : un diagnostic, pas une passerelle.
  - **Correction de collision de nommage découverte en cours de route** : `detect_memory_backend()` renvoyait `"qdrant-local"` pour un *serveur Qdrant joignable*, la même chaîne que le vrai backend embarqué (fichier local, sans URL) — confirmer « utiliser le service détecté » attachait donc silencieusement à un store embarqué sans rapport avec le serveur trouvé. Renommé en `"qdrant-server"` (`_DETECTED_SERVICE_LABELS` à l'avenant) ; l'attachement au service détecté pointe désormais réellement dessus (`qdrant_url` inclus via `BACKEND_CONNECTION`).
  - **Panneau Next Steps et pied de page « Découvrir »** (`src/grimoire/core/project_capabilities.py`, `onboarding_panel.py`) : nouvelle `memory_upgrade_target()`, remplace le test « encore lexical ? » par une comparaison au profil réellement servable par la machine (`layer_profile` vs. `recommend_profile()`) — un projet déjà en `standard` se voit maintenant proposer `complet` une fois Docker disponible, pas seulement un projet resté en `lexical`.
  - **Fin du profil `--lite`** (décision produit 2026-09-18) : plus de notion de profil « léger » — l'expérience installée est toujours complète, le cœur s'adapte à chaque tâche par classe de travail. `--lite`/`--profile lite` sont dépréciés : acceptés pour compatibilité, ils affichent désormais l'avertissement « the light profile no longer exists: the installed experience is complete, the core adapts to each task » et se comportent exactement comme le défaut (plus de `backend: lexical`/`--no-cockpit`/`archetype: minimal` forcés, plus de panneau ni de clés JSON dédiés). Retrait définitif de l'option prévu plus tard. Mentions retirées de `docs/getting-started.md`, `docs/cli-reference.md`.
  - **Fixture de test suite-wide** (`tests/conftest.py::_default_no_docker_daemon`) : cette machine — comme, potentiellement, tout runner CI avec Docker installé — sert réellement Weaviate/Qdrant/Neo4j/Redis/Ollama sur leurs ports par défaut (dogfooding partagé). Sans ce garde-fou, chaque test exerçant le défaut de `grimoire init` aurait hérité du profil que *cette machine* peut servir plutôt que celui, déterministe, qu'un projet neuf devrait voir — même classe de fuite qu'#493 (`detect_memory_backend`), même remède (un défaut autouse, à côté d'`_isolate_user_state`).
  - Tests rouge-avant : `tests/unit/tools/test_memory_setup.py` (repli `qdrant-local` sans serveur, `docker_daemon_reachable`, `local_embedding_available`, `recommend_profile`, `start_memory_stack`, `unreached_configured_services` — Docker toujours simulé, jamais invoqué réellement), `tests/unit/cli/test_cmd_memory_ops.py` (`--start`, `memory doctor`), `tests/unit/cli/test_init_memory_profile.py` (question « démarrer maintenant » par défaut oui), `tests/test_cmd_init.py` (`-y` + Docker simulé disponible → **avant `standard`, après `complet` avec pile démarrée simulée** ; bare non-TTY + Docker disponible → `complet` configuré, jamais démarré, `grimoire doctor` le signale), `tests/unit/cli/test_app.py` (défaut express, dépréciation `--lite`, nouveau check doctor), `tests/unit/cli/test_init_lite_profile.py` (réécrit : `--lite` inerte + avertissement, `--memory-stack`), `tests/test_project_capabilities.py`/`tests/test_onboarding_panel.py` (comparaison au profil servable). `ruff check`/`mypy --strict` verts sur `src/grimoire/` en entier ; ratchet R2 respecté (`cli/cmd_init.py` sous le plafond générique de 1500 lignes, docstrings condensées pour tenir).
  - Rejeu réel (`_scratch/onboarding-audit/pr2-verify/`, Grimoire-Forge, jamais committé) — **avant** (branche `feat/onboarding-60s`) : `grimoire init -y` sur Python nu/Node nu/vide → `Memory: lexical`, aucune explication, quel que soit l'état de la machine. **Après** (cette PR, arbitrage inclus) : sans Docker → `Memory: qdrant-local` (`standard`) avec raison nommée ; avec Docker simulé disponible et `-y` → `Memory: weaviate-server` (`complet`), pile démarrée (Weaviate/Neo4j/Redis « démarré »), sans aucune question ; sans `-y` (script) et Docker disponible → `complet` configuré, aucun conteneur démarré, rapport et `grimoire doctor` signalent « pile mémoire non démarrée » avec la commande exacte. Interactif (`pexpect`, pty réel) : choisir `complet` propose « Démarrer la pile mémoire complète… ? » par défaut oui. Rejeu en lecture seule sur la Forge (`grimoire memory status`/`memory doctor`) : profil `complet` déjà servi, `git status` identique avant/après (aucune écriture).
  - **Découvert en cours de route, hors périmètre de cette PR** : `tests/unit/test_agent_override_partial.py::test_doctor_warn_nomme_l_agent_et_ne_fail_jamais` choisit son agent de test via un `glob("*.md")` non trié — l'ordre d'entrées du système de fichiers diffère entre deux checkouts (worktrees) du même commit, et peut faire retomber le test sur un agent classé `info` plutôt que `warn`. Reproduit indépendamment de cette PR (identique sur le worktree de la PR 1) ; non corrigé ici (fichier sans rapport avec l'onboarding/la mémoire).
  - **Relecture Copilot sur cette PR, trois défauts corrigés** : (1) `--memory-stack up` avec un `--backend` explicite pinnant sa propre composition (`weaviate-server`) et sans `--memory-profile` laissait `memory_profile` vide — le consentement explicite à démarrer les services était donc silencieusement ignoré (la branche de consentement ne matchait que `"complet"`, jamais une composition dérivée), et même atteinte, `start_memory_stack("")` résout au profil par défaut `standard` (aucun besoin de Docker) et ne fait rien. Corrigé : le profil est dérivé du backend explicite (`memory_profiles.infer`, upgradé en `complet` si Redis répond déjà) avant que la question de consentement ne soit posée, et cette question porte désormais sur `requires` (tout profil qui a besoin de Docker) plutôt que sur la chaîne `"complet"` en dur. (2) `local_embedding_available()` sondait les cinq services mémoire (`probe_services()`) quand elle n'a besoin de savoir que si un Ollama local répond — coûteux et inutile sur le chemin `doctor`/`status` (`memory_upgrade_target()`), qui l'appelle sans sondes déjà calculées à chaque exécution. Séparé en une sonde Ollama seule (`_ollama_usable()`), et `memory_upgrade_target()`/`unexploited_hints()` acceptent désormais un jeu de sondes déjà calculé à réutiliser plutôt que d'en relancer un. (3) `start_memory_stack()` levait une `OSError` brute si le gabarit Docker Compose embarqué était illisible (fichier manquant, permissions, souci d'empaquetage) — accessible depuis `init --memory-stack up`/`memory up --start`, ça plantait la commande au lieu de rapporter un refus nommé. `_ensure_compose_file()` lève désormais `GrimoireRuntimeError`, capturée par les deux commandes pour un message clair et un exit code 1. Tests : `tests/unit/cli/test_init_lite_profile.py` (dérivation de profil honorée par `--memory-stack up`), `tests/unit/tools/test_memory_setup.py` (`local_embedding_available()` sans sondes ne déclenche jamais `probe_services()`, refus nommé sur gabarit illisible).
- fix(policies): politique `pre_tool_use` (Bash) — faux positifs « secret » et code interprété non inspecté (#724). §1 : `jq '.env.CLAUDE_CODE_TMPDIR = …'`, `grep -E '\.env\b|secret'`, `grep 'secret\|recursive'` et un heredoc décrivant le bug étaient refusés comme accès à un secret. Correctif dans `hosts/secrets.py` et `tool_facts.classify_tool` : les corps de heredoc écrits vers un fichier ou un `--body-file -` sont de la donnée et ne sont plus scannés (ceux qu'un interpréteur exécute le restent) ; une barre oblique inverse n'ancre un chemin que collée à un segment (`app\.env`, `C:\secrets\x`), pas après une quote ni devant `|` ou `b` d'un motif ; le suffixe d'un fichier `.env.*` reste en minuscules sous une comparaison désormais insensible à la casse (`.env.CLAUDE_CODE_TMPDIR` est un champ jq) ; `secret(s)` ne compte que suivi d'un vrai séparateur de chemin. `cat .env`, `cat ".env"`, `cat config/.env.local`, `cat secrets/token.txt`, `~/.ssh/id_rsa` restent bloqués. §2 : `python3 - <<'EOF' … shutil.rmtree(…)` passait sans un mot alors que `rm -rf` est refusé. `tool_facts.interpreted_bodies` relève les corps qu'un interpréteur exécute (heredoc sur stdin de python/node/perl/ruby/php/sh, `cat <<EOF | node`, `-c`/`-e`) ; `interpreted_destructive_reason` les lit contre un jeu de motifs par langage (`shutil.rmtree`, `os.remove`, `Path.unlink`, `fs.rm*`, `rimraf`, `FileUtils.rm_rf`, `unlink`…) et contre les motifs shell dans un appel `os.system`/`subprocess`/`exec` — classé `destructive`, donc refusé par `no-destructive-without-strict` comme `rm -rf`. Un corps sans appel connu est au moins nommé : `ToolFacts.interpreted_code`, `detail.interpreted_code`, et sur un `allow` un avertissement « code python interprété : … le reste n'est pas lu » porté par `additionalContext` et `systemMessage` (PreToolUse, hôte bloquant). Une chaîne python qui ne fait que citer `rm -rf` (un CHANGELOG écrit par heredoc) reste de la donnée. Rouge-avant / vert-après : `tests/unit/test_policy_secret_false_positives_and_interpreted_code.py` (36 cas : les commandes du §1 autorisées, les lectures réelles bloquées, l'exemple du §2 refusé et nommé). Non fait : `secret\b` dans un motif grep reste un faux positif (la lettre après `\` est aussi un caractère de chemin) ; seuls les appels destructifs listés sont reconnus dans le code interprété.
- fix(hosts): profil Claude Code — « aucun hôte ne sait démarrer dans un agent » était faux, et les champs `agent_type` / `agent_id` des hooks étaient ignorés (#716, prérequis du lot #721). `CLAUDE_CODE_PROFILE.agent_autostart` passe à vrai (motif documenté : `claude --agent <nom>`, clé de réglage `agent`, `agent_type` au `SessionStart` — cité dans `notes` et `docs/hosts.md`), les quatre autres profils restent faux avec leur substitut. `HookInput` porte `agent_type` et `agent_id` (`normalize_input` lit `agent_type`/`agentType`, `agent_id`/`agentId` ; `agent_name` reprend `agent_type` quand rien de plus explicite n'est envoyé, pour que la trace nomme l'agent en jeu). `decide_activation` n'injecte ni la persona d'entrée ni la politique de dispatch quand `agent_type` est renseigné (`detail.session_agent`) ; sans `--agent`, comportement inchangé. `test_no_host_can_open_a_session_inside_an_agent` est réécrit hôte par hôte avec la source de chaque valeur. Fixtures : le `SubagentStop` réellement capturé (`05-subagent-stop.json`, `agent_id` + `agent_type`) et un `SessionStart` à la forme documentée sous `--agent` (`10-session-start-agent.json`, non capturé). Rouge-avant / vert-après : `tests/unit/test_hosts_agent_identity.py` (5 cas).
- feat(missions): une tâche porte une description de bout en bout (#714, socle du brief à deux faces du lot #719). Constat : `MissionTask.description` existait, `create_task` l'acceptait, le board et l'import la relayaient, mais aucune entrée de création ne la recevait. Changement : `TaskService.add(description=…)` (texte brut, borné à `MAX_DESCRIPTION_CHARS` = 4000, erreur nommée au-delà), `grimoire task add --description/-d` ou `--description-file` (exclusifs), outil MCP `task_add(description=…)` ; `task show` l'affiche (JSON déjà via `to_dict`), `task_show` / `task_context` MCP la rendent, le `context-bundle` la porte dans `task_summary.description`, la carte du cockpit (inspecteur de l'espace Exécuter) la montre ; sans description, comportement inchangé. `docs/cli-reference.md` complétée. Rouge-avant / vert-après : `tests/unit/cli/test_cmd_task_write.py` (6 cas), `tests/unit/mcp/test_task_tools.py::test_task_add_mcp_conserve_la_description`.
- fix(hosts): sur l'hôte Claude Code, la feature `stigmergy-hooks` activée n'injectait aucun signal au `SessionStart` : `grimoire stigmergy install-hooks` n'écrit que des hooks au format Copilot sous `.github/hooks/`, et `grimoire-hook --event SessionStart` (décision `decide_activation`) ne lisait pas le board (#703). La décision injecte désormais les signaux actifs (même texte que `format_sense` du hook Copilot embarqué, via `stigmergy.sense_context`), sous le rappel de tâche, quand la feature est activée ; feature coupée ou board vide : aucune ligne ; `detail.stigmergy_injected` le dit. Rouge-avant / vert-après : `tests/unit/test_hosts_stigmergy_context.py` (4 cas).
- fix(standard): gabarit `llm-provider-registry.yaml` — l'exemple local devient `ollama run {model} {prompt}` (sans `{prompt}`, `render_invocation` ne transmettait jamais le prompt : session interactive ou échec), et les exemples Anthropic passent aux alias `haiku` / `sonnet` / `opus` de la CLI au lieu d'ids versionnés (#700). Nouveau contrôle `providers.invocation_prompt_missing` (erreur, catégorie `provider_policy`) dans `grimoire standard verify` : une `invocation` déclarée sans `{prompt}` est nommée. Rouge-avant / vert-après : `tests/unit/test_providers.py` (`test_verify_rejects_an_invocation_without_prompt_placeholder`, `test_template_invocation_examples_carry_the_prompt_placeholder`).
- fix(context-pack): `grimoire context-pack <dépôt>` écrivait sa sortie par défaut dans le dépôt ciblé (`<dépôt>/_grimoire-runtime-output/repo-contexts/`) sans l'ignorer : `git add .` embarquait un résumé généré du dépôt (#701). Après écriture, si la cible est un dépôt git et que git n'ignore pas déjà le fichier (`git check-ignore`), le dossier de sortie est ajouté à `<dépôt>/.gitignore` sous un marqueur, une seule fois ; une sortie `--out` hors du dépôt ou un dossier sans `.git` ne touchent rien. Rouge-avant / vert-après : `tests/unit/tools/test_context_pack.py::TestOutputIgnored` (`git status` ne liste plus `repo-contexts`).
- fix(policies): une valeur d'énumération invalide dans `_grimoire/standard/policies.yaml` (`verdict_on_match: nope`, `action_kinds`, `mutation_classes`) est une `GrimoirePolicyError` (`GR-POL-002`) qui nomme la règle, le champ, la valeur et les valeurs admises, au lieu d'un `ValueError` nu qui faisait planter `grimoire doctor` et `policies status` (#844). Rouge-avant / vert-après : `tests/unit/cli/test_cmd_policies.py` (`test_invalid_verdict_on_match_is_a_named_policy_error`, `test_doctor_fails_instead_of_crashing_on_an_invalid_verdict`) ; doctor rend `policy_budget_guard` en FAIL.
- feat(missions): `grimoire task accept <id> --reason "…"` ferme une tâche à preuve déclarative (#865). Constat sur la Forge (3.64.0) : `task verify --evidence` rangeait un verdict `skipped`, `review_gate` refusait la clôture (« la décision revient à un humain ») et aucun geste ne laissait cet humain trancher ; une tâche ouverte sans `--check` ne pouvait être fermée par personne (5 tâches en revue, critères remplis).
  - `missions/human_acceptance.py` : canal humain seulement (même contrôle d'origine que `task answer`), tâche en `needs_verification` avec un pack, raison obligatoire. Pack d'attestation (éléments du dernier pack + l'acceptation, qui nomme chaque critère), verdict `passed` signé `created_by: human:<acteur>`, reçu de revue `READY` signé `reviewer_id: human:<acteur>`, puis clôture (`--no-close` pour s'arrêter avant). Un commentaire humain au ledger garde la raison.
  - Preuve : `tests/unit/missions/test_task_accept.py` (8 tests : sans acceptation la clôture reste refusée ; acceptation humaine → verdict `passed`, couverture complète, reçu, clôture ; origine agent ou non vérifiée refusée sans verdict écrit ; tâche hors revue et raison vide refusées ; CLI depuis le canal humain ferme, depuis une session d'agent refuse).
  - Limite : la couverture des critères repose sur la parole de l'humain, pas sur une commande ; le verdict et le reçu le disent (`human:`), un vert mécanique reste distinct.
- feat(mcp): un serveur MCP partagé en HTTP, le projet résolu par requête (#723). Constat : chaque session d'hôte lance son `grimoire-mcp` stdio (5 processus pour 114 Mo de RSS sur ce poste au 2026-10-20, 11 pour ≈ 540 Mo le 2026-10-09).
  - `grimoire mcp serve --http` / `start` / `stop` / `status` : un démon streamable-http sur la loopback (port 7331), détaché sur le modèle de `cockpit start`, état sous `registry_home()/mcp-server.json` ; `grimoire-mcp --http` est le même serveur au premier plan. `grimoire mcp register <projet>` ajoute à `.mcp.json` une entrée séparée `grimoire-http` (`type: http`) sans toucher à l'entrée stdio `grimoire`, qui reste le défaut.
  - Projet par requête (`grimoire.mcp.project`) : chaque outil est enregistré derrière `project_scoped` ; en HTTP, `project_path` absolu obligatoire, sinon repli sur la première racine `file://` que le client déclare (`roots/list`) ; sans projet résolu, refus `isError` (`code: mcp.project_unresolved`, racines du client dans le corps) — jamais le `cwd` du démon, qui n'enregistre d'ailleurs aucune surface (prompts, ressources). En stdio, rien ne change : l'enveloppe est transparente et les fonctions nues restent celles du module. La résolution est une fonction pure de l'argument et des racines, recalculée à chaque appel.
  - `grimoire doctor` : `mcp_processes` compte les serveurs stdio vivants par `/proc` (WARN au-delà de 3, « non mesuré » sans `/proc`) ; une entrée `type: http` de `.mcp.json` est sondée (WARN si injoignable, jamais FAIL). `McpServerSpec.url` : OpenCode reçoit une entrée `remote` au lieu d'une commande vide.
  - Preuves : `tests/unit/mcp/test_shared_project.py` (un vrai `ClientSession` en mémoire sous `GRIMOIRE_MCP_TRANSPORT=http` alterne deux projets — config, tâches prêtes, mémoire écrite dans l'un et introuvable dans l'autre ; refus de `.`, d'un chemin relatif, d'un absent et d'un argument omis avec un `cwd` pourtant valide ; la racine déclarée par le client l'emporte sur le `cwd` ; le schéma des outils ne montre pas le paramètre de contexte), `tests/unit/cli/test_cmd_mcp.py`, `tests/unit/tools/test_mcp_processes.py`. Vérifié contre mcp 1.28.1 et 2.3.0 (démon réel interrogé en streamable-http).
  - Non fait : la bascule des hôtes vers l'entrée http reste manuelle (une session ne devrait charger qu'une des deux entrées) ; `task_claim` ne rattache pas la session (`CLAUDE_CODE_SESSION_ID`, `GRIMOIRE_ACTOR` lus dans l'environnement du serveur, pas de la session) quand il passe par le démon ; Roots est déprécié par la révision 2026-07-28 — le repli est une commodité de transition, le chemin durable est `project_path` absolu.
- feat(cadrage): `grimoire cadrage plan` tire du cadrage une tâche du ledger par exigence, et `standard verify` échoue sur une question bloquante du cadrage sans réponse (campagne « Dépasser AI-DLC », chantier W6-07b, ADP-9 et ADP-8 critère 2). Constat : les exigences du cadrage (`04-exigences.md`, `05-cahier-des-charges.md`) étaient de la prose libre, rien ne les reliait aux tâches (`cadrage plan` inconnue, exit 2), et le gate du standard ignorait les questions posées sur le cadrage.
  - Changement : convention d'identifiants rétrocompatible `REQ-NNN` en tête de ligne (liste, titre) ou dans un tableau, critères en sous-liste, sections « critères », « priorisation » et « hors périmètre » reconnues (`core/cadrage_requirements.py`) ; un cadrage sans identifiant reste valide et `cadrage plan` dit qu'il ne voit rien. `missions/cadrage_plan.py` propose une tâche par exigence (critères repris, `extra.covers`, priorité MoSCoW, classe de vérifiabilité calculée, commande seule entre accents graves promue en `checks`), idempotente ; les ambiguïtés (sans critère, critère V2, identifiant redéfini, doublon) deviennent des questions à clé stable posées en un lot sur une tâche d'ancrage garée en `waiting(human)` (vue Décisions du cockpit). Une réponse humaine à « à quoi verra-t-on… » devient le critère ; corriger le cadrage rend la question caduque et annule l'ancre sans réponse fabriquée. Aperçu par défaut, confirmation dans un terminal interactif, `--yes` pour écrire, `--check` pour la couverture. `standard verify` : `cadrage.question_open` (erreur), `cadrage.requirement_uncovered` et `cadrage.task_orphan` (avertissement, erreur en governed/production quand le cahier des charges est `status: accepted`). `TaskService.add` accepte `priority` et `extra`.
  - Rouge-avant / vert-après : `tests/unit/test_cadrage_plan.py` (12 cas : convention, prose valide, fusion entre fichiers et conflit, aperçu sans écriture, application idempotente, gate sur bloquante ouverte, non bloquante jamais en échec, réponse humaine qui lève le gate et devient critère, agent qui ne lève pas le gate, question caduque, sévérités de couverture, CLI `--yes`/`--check`). Sur `origin/main` : échec à la collecte (`grimoire.core.cadrage_requirements`) et `cadrage plan` inconnue.
  - Non fait : pas de surface MCP ni cockpit dédiée à `cadrage plan` (les questions passent par la vue Décisions existante) ; pas de `task add --covers` pour relier à la main une tâche existante ; un critère modifié après création de la tâche n'est pas resynchronisé ; une revue nommée (V1) compte comme critère vérifiable, seule la V2 est « manquante » ; les commandes promues en `checks` viennent du fichier de cadrage, avec la confiance de son auteur.
- docs(bench): lot M, tâches discriminantes pour le banc comparatif (proposition, B-07). Le lot L est saturé (le `nu` résout 60/60) : `docs/bench-three-arms.md` §16 évalue les sources (polyglotte non joué, historique du kit, SWE-bench Verified/Lite, SWE-bench Pro, SWE-rebench, LiveCodeBench, Terminal-Bench), propose un lot de 30 tâches (bloc P tamisé sur 132 exercices, bloc K de 16 bugs réels du kit) et un protocole par paliers chiffré (tamis 41,51 $ estimés, plafond 90 $ ; campagne 3 bras k = 3 851,17 $ estimés, plafonds 260 / 340 $ puis recalcul ; écart minimal détectable 36,2 points à 30 tâches).
  - Changement : `--task-lot <fichier>` sur `three_arms.py` et `aidlc_arm.py` (`scripts/bench/task_lot.py`, schéma `grimoire.bench.task-lot.v1`) nomme les tâches au lieu du tirage, refuse avant tout run une tâche absente, un doublon et un clone polyglotte à un autre commit que `polyglot_commit` ; `selection.json` note `task_lot` et `polyglot_commit`. `docs/bench/lot-m-polyglot-sieve.json` liste les 132 tâches du tamis, épinglées au commit du lot L.
  - Rouge-avant / vert-après : `tests/unit/test_bench_task_lot.py` (13 cas, aucun appel modèle). Les 16 tests de régression du bloc K ont été rejoués sans modèle : verts au correctif, rouges au parent.
  - Non fait : aucun run payant ; `--k` et le plafond total de `three_arms.py`, le matérialiseur du bloc K et la fermeture du réseau de l'agent restent à coder (§16.4).
- feat(bench): lot M exécutable — tâches du kit, répétitions et plafond global (B-08). Constat : le lot M de B-07 n'était pas jouable. `three_arms.py` n'avait ni `--k` ni plafond de campagne (une campagne `--full` partait sans limite de dépense), le bloc K (16 bugs réels du kit) n'avait ni chargeur ni énoncés, et rien ne fermait l'accès de l'agent au correctif publié sur GitHub et PyPI.
  - Changement : `scripts/bench/budget_guard.py` extrait d'`aidlc_arm.py` le garde-fou fermé par défaut (`BudgetGuard`, `parse_cap`, `session_call_cost`), désormais partagé. `three_arms.py` gagne `--k`, `--max-total-usd` (obligatoire pour `--pilot` et `--full`), `--budget-ledger` : refus sans plafond, arrêt au plafond (code 3), coût inconnu = arrêt, reste du plafond passé en `--max-budget-usd`, journal append-only rejoué à la reprise et partageable avec `aidlc_arm.py`. `scripts/bench/kit_lot.py` charge le bloc K par `--kit-repo` : arbre du parent au format polyglotte, tests du correctif cachés hors du dépôt de l'agent, venv de test sans grimoire-kit (refusé s'il importe `grimoire`), contrôle rouge au parent / vert au correctif avant toute dépense, succès = au moins un test passé et aucun sauté. Les 16 énoncés (`docs/bench/lot-m-kit-tasks.json`) décrivent le symptôme et nomment les interfaces nouvelles que les tests importent (K03 à K06, K08 à K11, K14), sans le correctif ; `docs/bench/lot-m.json` est le lot complet du tamis (148 tâches). Réseau de l'agent sur les tâches K : `--disallowedTools WebFetch WebSearch`, pip et uv hors ligne, `PYTHONNOUSERSITE`, proxys des commandes `Bash` sur un port fermé (`CLAUDE_ENV_FILE`), et un appel d'outil qui vise la solution (clone source, `site-packages/grimoire`, GitHub ou PyPI du kit, `git clone`) rend le run `invalid`. Les bras `kit` et `kit-gov` sont refusés sur le bloc K : le kit installé contient les correctifs, un kit installé au parent porterait le bug dans ses propres gardes (`docs/bench-three-arms.md` §17.7).
  - Rouge-avant / vert-après : `tests/unit/test_bench_lot_m.py` (34 cas, fakes, aucun appel modèle) : collecte en échec sur la base ; avec les modules neufs mais les lanceurs de la base, 11 échecs (`--max-total-usd` et `--k` inconnus, campagne lancée sans plafond, pas d'arrêt au plafond ni sur coût inconnu, `aidlc_arm` avec son propre garde-fou, données absentes). `--dry-run` du tamis (bras `nu`, k = 1, lot M) : code 0, 148 dépôts préparés, 16 tâches K rouges au parent et vertes au correctif, coût estimé 41,53 $ (haut 83,77 $), plafond de 90 $ couvert.
  - Relecture sceptique des 16 énoncés (2026-10-20) : K08 réécrit (l'énoncé dictait l'algorithme de la revue ; il décrit maintenant le défaut d'origine, 14 tests dont 11 rouges au parent) ; K09 à sélection élargie (un seul des deux tests discriminait ; 9 tests, 6 rouges au parent avec un bouchon du comportement d'origine) ; les 14 autres gardés. Garde : `test_reviewed_statements_keep_k08_narrow_and_k09_discriminating`.
  - Tamis payant (2026-10-20) arrêté avant le pilote, après une seule sonde à 0,1383 $. `--disallowedTools` et `CLAUDE_ENV_FILE` sont effectifs. Mais le bras `nu` hérite des serveurs MCP du `.mcp.json` de la Forge (`grimoire`, `playwright`, `context7`), déjà présents dans 57 des 60 runs `nu` du lot L. C'est une fuite de solution non détectée pour le bloc K (kit installé et mémoire de la Forge, navigateur hors proxy), et un agent en `bypassPermissions` peut écrire dans le ledger de l'opérateur. Correctif proposé, non appliqué : `--strict-mcp-config` et un workspace hors de l'arbre de la Forge (`docs/bench-three-arms.md` §17.8). Aucune tâche tamisée, aucune décision de campagne.
  - Non fait : tamis du lot M ; `--disallowedTools`, `CLAUDE_ENV_FILE` et `--max-budget-usd` non vérifiés par un run réel ; le système de fichiers de l'opérateur reste lisible par l'agent (détection seulement) ; `aidlc_arm.py` n'est pas branché sur le bloc K ; le champ `k` est dans `selection.json`, pas dans `report.json`. `--pilot` et `--full` sans `--max-total-usd` sont désormais refusés.


- feat(hosts): scan déterministe des fichiers d'instructions au `SessionStart` (#667, phase 5 lot 5.4, lot E). `grimoire.hosts.decisions.instruction_scan` énumère `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, `.github/copilot-instructions.md`, `.github/{instructions,agents,prompts,skills,rules,commands}/` et tout `.md`/`.mdc` sous `.claude/`, `.cursor/`, `.codex/`, `.gemini/`, `.windsurf/`, classe chaque fichier par provenance (`kit-owned` par digest du catalogue livré, `project-owned` si suivi par git et propre, `unrecognised` sinon) et ne passe par les motifs — annulation des instructions précédentes, action cachée à l'utilisateur, exfiltration, exécution distante, texte invisible, directive en commentaire HTML — que les non reconnus. Extension de `decide_activation` (pas de nouveau hook) : silencieux sauf paire (non reconnu, motif), ligne de contexte nommant fichier et codes ; cache par session dans `SessionState.instruction_scan` (empreinte chemin/taille/mtime, jamais d'extrait) ; contrôle `instruction_files` de `grimoire doctor` (`grimoire.cli.doctor_instructions`). Empreintes CRLF/LF identiques. Preuve : `tests/unit/test_instruction_scan.py` (fixtures bénignes et malveillantes, dix critères de l'issue). Doc : `docs/hosts.md`, « Scan des fichiers d'instructions ».
- feat(policies): règles d'armement (#666, phase 5, lot E) — `arms: {pattern, then, minutes}` sur une règle de `_grimoire/standard/policies.yaml` : une action anodine qui correspond à `pattern` (`Write(*flux/*)`, `Bash(chmod +x:*)`, `Bash(git remote set-url:*)`…) arme, pendant `minutes` (10 par défaut), l'action `then` (`Bash(kubectl apply:*)`, `Bash(./*)`, `Bash(git push:*)`…), qui reçoit alors `verdict_on_match` (`warn` → `ask`, `block` → refus) au lieu de passer inaperçue. État de session borné (`SessionState.armed`, identifiant de règle et échéance seulement), segments shell comparés en plus de la ligne entière (`chmod +x s.sh && ./s.sh` est jugé armé), chemins Windows normalisés, échéance illisible traitée comme armée. **Aucune règle livrée par le kit** : l'utilisateur déclare les siennes (décision de Guilhem). Preuve : `tests/unit/test_arming_rules.py` (cinq séquences, fenêtre, Windows/POSIX, aucun faux positif sur `jq`/`grep`, aucune règle par défaut, latence, bout en bout par `policies.yaml`). Doc : `docs/hosts.md`, « Règles d'armement ».
- feat(workspace): matrice contrôle × état du cockpit couverte systématiquement (#536, lot E). Quatorze primitives de `shell.css` (bouton, primaire, basculé, chip, sept pastilles, select, input, `.input`, lien, épingle, onglet, segment, rail, arbre, ligne de preuve, kbd, libellé, état vide, barre d'état en erreur/pause, infobulle, squelette) × leurs états (normal, survol, focus visible, actif, désactivé, chargement, erreur, vide) dans les deux thèmes : 80 paires nommées dans `docs/cockpit-coverage-matrix.md` (section « Contrôles × états (coque) », résumé régénéré). Preuves : `tests/e2e/test_workspace_control_states.py` force chaque état par CDP (`CSS.forcePseudoState`) et mesure contraste ≥ 4,5:1 (3:1 pour une icône), cible ≥ 24 px, anneau de focus ≥ 3:1 ; `tests/unit/test_workspace_control_states.py` mesure les mêmes paires sur les valeurs déclarées dans les trois blocs de thème. Deux paires défaillantes corrigées dans `shell.css` : le `select` natif rendait 21 px (cible minimale 24 px, `min-height`), le placeholder gardait l'encre du navigateur (3,55:1 sur `--card` en sombre ; `--ink3`, opacité 1). Non fait : la couleur du lien en ligne reste celle du navigateur (≥ 4,5:1 mesuré, mais hors tokens) ; les lots B restants de #536 (états vides/erreur par espace, raccourcis, panneaux) ne sont pas dans ce lot.
- fix(tests): les tests HTTP du cockpit survivent à une connexion coupée — `ConnectionAbortedError: [WinError 10053]` sur `windows-latest`, une PR sur deux (#857). Cause (`src/grimoire/cli/cmd_cockpit.py`, `_send_json`) : `http.client` envoie l'en-tête et le corps en deux `send` ; sous charge, `_CockpitHandler` parsait l'en-tête, refusait (403 « hôte en lecture seule », 404 « route inconnue »), répondait et fermait avant l'arrivée du corps ; le corps atteignait un socket fermé, RST, et Windows jetait la réponse déjà reçue. Seuls les refus étaient touchés : les routes qui lisent le corps attendaient, et `_guard_mutation` drainait déjà le sien.
  - Serveur : `_CockpitHandler` compte les octets du corps lus (`_CountingReader`) et `_send_json` comme `send_error` consomment le reste déclaré par `Content-Length` avant la première ligne de réponse ; `Connection: close` est posé et `wfile` vidé avant la fermeture.
  - Tests : un client unique `tests/unit/_cockpit_http.py` (`get`/`post`/`put`/`request`) retente au plus 3 fois, uniquement sur `ConnectionAbortedError`/`ConnectionResetError`/`BrokenPipeError` (visage macOS du même RST, sur le `send` du corps)/`RemoteDisconnected`, avec 0,2 s de délai, et relit la réponse en entier ; un 4xx/5xx ou un refus de connexion est rendu tel quel. `test_workspace_routes.py`, `test_cmd_cockpit_blueprint_writes.py` et `test_cmd_cockpit.py` l'utilisent à la place de leurs copies d'`urlopen`.
  - Rouge-avant / vert-après : `tests/unit/test_cockpit_http_connection_cut.py` — en-tête seul envoyé, la base répond avant le corps (2 cas, 403 et 404) ; faux serveur qui coupe une fois (`SO_LINGER` 0) puis répond, refus 403 jamais retenté, coupure rendue après 3 essais, refus de connexion immédiat.
  - Non fait / non mesuré : aucun run Windows reproduit en local (mécanisme rejoué sous Linux) ; `forge_http.py` (`grimoire serve`) garde ses refus sans drainage hors `_guard_mutation` ; les autres tests du cockpit (`test_cmd_cockpit_multiproject.py`, `_proposals.py`, `_create_project.py`, `_memory_overview.py`, `_blueprint_put.py`, `test_project_update.py`, `test_workspace_task_steering.py`) gardent leur propre `urlopen`, désormais protégés par le seul correctif serveur. Critère de l'issue : 0 occurrence sur 10 runs Windows consécutifs de `main`.
- fix(hosts): le plugin de garde opencode et le serveur MCP `grimoire-mcp` se lancent aussi hors du venv activé (suite de #760). Constat sur la branche de #763 : le plugin `.opencode/plugin/grimoire-guard.js` appelait `spawnSync("grimoire-hook")`, et `.mcp.json` (seed de `grimoire init`) comme le repli de `hosts/collect.py` écrivaient `grimoire-mcp` par son nom nu. Avec un kit installé par `uv pip install` dans un venv, l'hôte ne trouvait aucun des deux ; en profil governed, le plugin opencode refusait alors chaque outil gardé.
  - `hosts/hook_launcher.py` : `resolve_hook_launcher` prend le nom du programme ; `mcp_command` rend `grimoire-mcp`, `${CLAUDE_PROJECT_DIR:-.}/.venv/bin/grimoire-mcp` (forme documentée par Claude Code pour un `.mcp.json` de projet) ou un chemin absolu ; `GRIMOIRE_MCP_COMMAND` force la commande.
  - Plugin opencode : le programme résolu est inscrit dans le fichier, relatif à la racine du projet (`directory`), variante `Scripts\grimoire-hook.exe` sous Windows. `opencode.json` reçoit un chemin absolu, opencode ne documentant pas la résolution d'un chemin relatif.
  - `grimoire doctor` (`mcp_*`) cherche la commande dans le PATH sans le venv actif, comme l'hôte, et comprend `${CLAUDE_PROJECT_DIR:-.}`. Effet : un `.mcp.json` existant en `grimoire-mcp` nu passe en échec sur un kit installé en venv. Il n'est jamais réécrit (seed) : le remède le dit.
  - `doctor` (`hook_commands`) ne retient plus que les hooks qui lancent `grimoire-hook` lui-même. Constat sur la Forge en 3.64.0 : un hook maison `grimoire-hook-gateway.sh --hook-id …` contenait la chaîne, la ligne entière était prise pour le programme et le contrôle tombait en FAIL. Rejeu sur la Forge : 14 commandes résolues, contrôle vert.
  - Preuve : 8 tests dans `tests/unit/test_hook_launcher.py` (résolution MCP, seed `.mcp.json`, plugin opencode, `opencode.json`, contrôle `doctor` sous venv activé). Rejeu : le plugin généré, importé sous Node avec un PATH sans le programme, lance `.venv/bin/grimoire-hook` du projet et relaie son refus. Non vérifié : aucun opencode ni Claude Code réel lancé avec ces fichiers ; cwd du serveur MCP non documenté.
- fix(evidence): une rupture du journal de preuve peut être expliquée par un humain, sans réécrire le journal (CHAIN-ACK). Constat (Forge, kit 3.64.0) : `evidence.chain_broken` rendait une erreur « tant que la rupture n'est pas expliquée » alors qu'aucun mécanisme n'existait pour l'expliquer ; une ligne sans `prev` écrite par le hook d'une version antérieure pendant `pip install -U` (ligne 1822 du journal `bootstrap`, que la ligne 1823 prolonge, pas la 1821) laissait `standard verify` et le gate rouges pour toujours, et une session sans tâche bloquée à chaque fin de tour.
- Changement : `analyze_chain` nomme le cas (`kind` : `unchained_inserted` quand la chaîne continue autour, `truncated`, `chain_broken` pour toute autre rupture ou ambiguïté, y compris une ligne non chaînée en queue) et liste les lignes avec l'empreinte de leurs voisines. Un humain l'explique en tapant `Explain Chain <tâche> line <n>: <raison>` (`hosts/decisions/chain_ack.py`, même voie que `Override Gate` : tour humain d'un hôte nommé, sans claim exigé) ; l'acquittement, nominatif et lié aux empreintes, va dans un fichier au suffixe d'autorité protégé par la garde. `evidence.chain_broken` (erreur) devient `evidence.chain_break_explained` (avertissement qui cite raison, auteur, date) dans `standard verify`, `gate check` et le gate Stop ; la rupture reste listée, et l'ancre figée à la rupture est dite (`evidence.chain_anchor_frozen`). `grimoire evidence chain explain --task-id --line` diagnostique et imprime la phrase, sans rien écrire : un CLI lancé par un agent ne prouve pas l'origine humaine. Le journal n'est jamais réécrit ni rechaîné.
- Rouge-avant / vert-après : `tests/unit/core/test_evidence_chain_ack.py` (diagnostic nommé, acquittement humain accepté, refusé pour hôte inconnu, `GRIMOIRE_UNATTENDED`, absence de session ou événement d'outil, rupture différente et voisine modifiée non couvertes, rupture vraie ou non chaînée en queue non explicable, fichier non écrivable par Write/Edit/Bash, journal inchangé octet pour octet).
- Non fait : l'ancre n'est pas ré-ancrée après une rupture expliquée (elle reste figée, dite en avertissement) ; l'acquittement ne prouve pas que la ligne est innocente, un écrivain sans chaînage et une ligne ajoutée à la main sont indiscernables.
- feat(hosts): la boucle principale déclenche d'elle-même un brainstorm en sous-agents parallèles, sans qu'on le demande (#687, lot A). La politique de dispatch Claude Code (`_dispatch_policy_section`, injectée à chaque `SessionStart` et émise dans `.claude/agents/<entrée>.md`) gagne la sous-section « Quand déléguer en parallèle (brainstorm) » : 2 à 4 sous-agents indépendants dans un seul message sur une demande ouverte, un arbitrage d'architecture ou plusieurs axes indépendants ; anti-signaux (correction ciblée, tâche V0, question factuelle, fichier déjà désigné) ; 4 angles maximum, un seul tour, synthèse faite par la boucle principale. Le texte est factorisé dans `parallel_brainstorm_section` (`src/grimoire/hosts/emitters/base.py`), partagé avec l'émetteur Copilot (`.github/agents/<entrée>.agent.md`), dont la variante ne nomme aucun modèle. `framework/workflows/party-mode.md` renvoie vers ce déclencheur. Tests : `tests/unit/test_hosts.py` (SessionStart, non-dérive Claude, Copilot sans nom de modèle).
- feat(onboarding): `grimoire onboard brownfield` — connaissance d'un projet existant, dérivée du code et vérifiable (W7-01). Constat : le kit avait un graphe de code (`codegraph/`, fraîcheur W6-02), une détection de stack et `project_kind`, mais aucune étape qui documente un projet existant ; AI-DLC en a une (rétro-ingénierie, base « codekb »), écrite par un modèle.
  - Changement : nouveau paquet `src/grimoire/onboarding/` (`facts`, `scan_python` sur le graphe de code et l'AST, `scan_js` par motifs, `scan_manifests`, `brownfield`) et commande `grimoire onboard brownfield [--check] [--json] [--no-memory]`. Sortie `_grimoire-output/knowledge/project-knowledge.{md,json}`, déterministe, sans modèle ni réseau : composants et responsabilité déclarée, points d'entrée, commandes (déclarée / observée / supposée / inconnue), dépendances déclarées et importées, conventions, zones à risque, carte des tests ; chaque fait cite `fichier:ligne` et son empreinte. `--check` réutilise `codegraph/freshness.py` (`collect_sources` accepte des extensions, `combine_hashes`, `diff_hashes`) : `current` / `stale` (faits touchés nommés) / `unverified`, code 1 hors `current`. Ce qui demande du jugement devient des questions non bloquantes au format W6-07 (`task ask --from` lit la base). Renvoi balisé dans `shared-context.md` s'il existe. `grimoire init` le propose sur un projet qui a du code (panneau, clé JSON `onboard`, confirmation interactive non par défaut).
  - Rouge-avant / vert-après : `tests/unit/test_onboard_brownfield.py` (27 cas, projet jetable Python + JS) — 24 échecs sur les sources d'`origin/main` 459fd8e40, 27 verts après ; les 3 qui passent sur la base sont des gardes de non-écriture.
  - Non fait / non mesuré : pas de graphe JS/TS (motifs seulement), pas de mode multi-dépôt ni de scan ciblé, pas d'exécution de la couverture, pas d'outil MCP dédié ; coût sur un gros dépôt non mesuré.
- feat(missions): travail en équipe — unités partagées entre humains par git (W7-02, première tranche). Constat : le Mission Ledger (`_grimoire-runtime-output/ledger/events.jsonl`) est local à un clone et pensé pour un opérateur et ses agents ; deux humains sur deux postes n'avaient aucun moyen de se répartir le travail, ni de voir qu'ils prenaient la même chose. Conception dans `docs/guides/travail-en-equipe.md` : on partage des unités (titre, critères, titulaire, bail grossier, livraison prouvée, revue), pas le ledger ; tout le reste reste local.
  - Changement : `missions/team_units.py` — un fichier JSON par événement sous `_grimoire/team/units/<unité>/`, nommé par l'empreinte de son contenu (nonce compris), jamais réécrit : deux clones n'écrivent jamais le même fichier, `git pull` ne fait pas de conflit textuel. L'ordre vient des têtes citées (`prev`), jamais des horodatages. Deux gestes écrits sur la même tête dans deux clones donnent deux têtes après fusion : l'unité est `contested`, toute écriture est refusée sauf `resolve --keep <tête>`, sans départage automatique (fail-closed, branche écartée conservée). Empreinte fausse, fichier illisible ou transition impossible : `invalid` ; parent absent : `incomplete` ; rien ne s'écrit. Identité déclarée (`git config user.email` ou `--as`), refusée si inconnue, nommée comme déclarée. `deliver` exige des preuves résolues contre git (SHA complet d'un commit ou empreinte d'un fichier du dépôt) ; on ne revoit pas sa propre livraison ; `adopt` seulement sur un bail expiré, en citant le bail repris.
  - Changement : `grimoire team unit publish|claim|renew|release|adopt|deliver|review|resolve|list|check` (`cli/cmd_team.py`, groupe paresseux) ; `--commit` committe le seul fichier écrit, rien n'est poussé ; `check` sort en 1 sur une unité contestée, invalide ou incomplète (après `git pull`).
  - Rouge-avant / vert-après : `tests/unit/missions/test_team_units_two_clones.py` (7 cas) et `tests/unit/cli/test_cmd_team.py` (parcours CLI complet) simulent un `origin` nu et deux clones, Alice et Bob : réservations concurrentes fusionnées sans conflit puis contestées des deux côtés, résolution explicite, publication concurrente, adoption d'un bail expiré puis renouvellement tardif de l'ancien titulaire détecté à la fusion, livraison prouvée et revue croisée, fichier altéré et parent manquant, fichier extrait en CRLF. Sur `origin/main` : `cannot import name 'team_units'` et `No such command 'team'`.
  - Non fait : pas de vue d'équipe dans le cockpit ni le kanban (la sortie `-o json` est prête) ; identité non prouvée (pas de vérification des commits signés) ; aucun lien entre une unité et les tâches du ledger local ; pas de dépendances entre unités ; l'expiration d'un bail est jugée par l'horloge de celui qui adopte ; aucun hook `post-merge` installé. Liste des tranches suivantes dans `docs/guides/travail-en-equipe.md`.
- feat(update): `grimoire self bundle <dossier>` construit le bundle hors ligne que `grimoire self update --from` installe (D-15, plan « Dépasser AI-DLC »). Constat : le kit lisait un bundle (`update_bundle.py`, D-04) mais ne livrait aucun outil pour le fabriquer ; l'opérateur devait enchaîner `pip download` et calculer les sommes à la main (AI-DLC installe depuis un dossier de release publié).
- Changement : `src/grimoire/tools/update_bundle_build.py` + `cli/cmd_self_bundle.py`. Sources : `--version` (PyPI, empreinte PyPI et `SHA256SUMS` de la release), `--from-release vX.Y.Z[-rc1]` (asset GitHub vs `SHA256SUMS`), `--wheel` (locale). `--with-deps` télécharge les dépendances en wheels seulement (`--platform`, `--python-version`, `--implementation`, `--abi`, `--index-url`, `--find-links`, `--python`). Sortie : wheel, dépendances, `SHA256SUMS` (LF, tout le dossier) et `PROVENANCE.json`. `gh attestation verify` est rejoué pour une wheel de PyPI/GitHub : `gh` absent = `unverified` dit, échec = construction refusée (`--skip-attestation` consigné). Le bundle est relu par le lecteur d'installation avant d'être rendu, monté dans un dossier voisin puis déplacé : un échec ne laisse rien ; une destination non vide est refusée. `update_channels.release_wheel` et `update_bundle.is_kit_wheel` factorisent les règles déjà utilisées par le lecteur.
- Rouge-avant / vert-après : `tests/unit/test_update_bundle_build.py` (25 tests, sans réseau : wheels factices, index local, réseau PyPI/GitHub simulé) échoue sur origin/main (module absent) et passe ; aller-retour construire -> `stage_and_verify` -> vrai `pip install --no-index` dans un venv jetable ; refus d'une wheel altérée, d'une dépendance altérée, d'une somme absente, d'un fichier ajouté, d'un `SHA256SUMS` absent.
- Non fait / limites : `--with-deps` pour une autre plateforme évalue les marqueurs d'environnement sur la machine qui construit (limite de pip, écrite dans la doc) ; la provenance est consignée, pas rejouée à l'installation (niveau `bundle`, jamais `verified`) ; les dépendances n'ont pas d'empreinte publiée par le kit ; pas de test contre le vrai PyPI ni un vrai `gh` (réseau simulé).
- docs: comparaison vérifiable avec AWS AI-DLC 2.11.0 (`docs/comparaison-ai-dlc.md`, liée depuis `docs/index.md` et la navigation). Constat : aucune page ne disait à un utilisateur ce qui distingue le kit d'AI-DLC, ni ce que le banc a réellement mesuré.
  - Changement : comparaison par axe (gouvernance, preuve, coût, orchestration, hôtes, distribution, expérience) où chaque affirmation cite un `fichier:ligne` de `main` ou une commande `grimoire` côté kit, un `fichier:ligne` du tag `v2.11.0` côté AI-DLC ; liste de ce qu'AI-DLC fait et que le kit ne fait pas ; ce qui n'a pas été lu ni mesuré est dit « non vérifié ». Résultats mesurés repris de `docs/bench-three-arms.md` §15.5-15.7 (20/20 des deux côtés, coût d'AI-DLC environ 28 fois Claude seul, 494 s contre 22 s, 18/20 solutions écrites hors workflow) et du tamis du lot M (141 runs du bras `nu`, 140 réussis, 21,68 $ connus au journal, un appel ouvert de coût inconnu). Conclusion : aucune supériorité de résultat démontrée ; la différence démontrée porte sur les garanties, le coût et l'outillage. Les bancs payants sont suspendus (décision du 2026-10-20).
  - Vérification : `scripts/check-doc-audience.py`, `scripts/check-doc-claims.py`, `scripts/check-doc-commands.py`, `tests/unit/test_no_dangling_tool_refs.py`, `python -m grimoire gates repo`. Aucun appel de modèle.
  - Non fait / non vérifié : les données brutes du lot M restent hors dépôt (pas de `report.json` scellé) et 4 transcriptions `invalid` n'ont pas été relues ; le comportement des hooks d'AI-DLC sur les harnais autres que Claude Code n'est pas lu ; les numéros de ligne valent pour `main` au 2026-10-20 et pour le tag `v2.11.0`.
- fix(workspace): le test e2e `test_workspace_cockpit_upgrade_flow_states.py` ne rougit plus par intermittence (#850). Cause reproduite : sur l'hôte servi, le niveau « Projet » est déjà actif ; le test cliquait ce zoom dès que `#zoom-seg` existait, et quand le clic tombait après le premier rendu de la fiche, `draw()` repartait — l'ancien bouton « Mettre à jour » restait cliquable le temps du rechargement, puis `renderSheet` vidait l'Inspecteur avec l'aperçu dedans (timeout sur « Confirmer la mise à jour »). Correctif : `piloter.js` ignore un clic sur le niveau de zoom déjà actif ; le test attend `.pl-sheet` avant de cliquer le zoom. Mesure locale (Playwright 1.63, Chromium 1243, fichier seul, sans instrumentation, `GRIMOIRE_E2E_STRICT=1`) : avant, sur `origin/main` fca1ba9fd, 1/100 rouge (le timeout `wait_for_function` du mécanisme décrit) puis 0/100 sur une seconde série ; après, 0/100. Le taux local est bien plus bas que les ~10 % de l'issue (poste au repos) ; le test durci seul, sur l'ancien `piloter.js`, fait 0/10 — la fenêtre est celle entre le clic sur l'ancien bouton et l'arrivée de la fiche, pas le simple redessin.
- fix(standard): les dix patterns de `capability-map.yaml` sans vérificateur ont chacun au moins un check présent au registre (#636). Constat : `checks: []` sur `runtime-journal`, `redis-hot-memory-soft-gate`, `governed-hook-gateway`, `skill-classification-matrix`, `governed-observability-cockpit`, `code-graph-projection`, `governed-agent-orchestration`, `governed-knowledge-indexing`, `mission-evidence-ledger`, `provider-cost-slo` — un profil qui les exigeait n'avait aucun contrôle derrière.
  - Six patterns se règlent par déclaration seule : la carte (et `check_refs` du gabarit `pattern-catalog.yaml`, désormais identiques) cite des checks que le registre émettait déjà (`hooks.*`, `rules.pack_violation`, `gate.codegraph_fresh`, `orchestration.*`, `knowledge.*`, `gates.*`/`evidence.*`/`claims.*`, `dispatch.cost_slo`/`cost.*`).
  - Quatre vérificateurs neufs, un module chacun sous `core/standard_checks/` : `journal.missing` (avertissement ; erreur en governed/production dès que le Mission Ledger porte un claim), `journal.line_invalid` (erreur), `journal.stale` (claim plus récent que le dernier événement journalisé au-delà d'une marge de 300 s, non mesurée) — le score ne compte plus le journal à part, un seul compte ; `observability.policy_missing`, `observability.inputs_undeclared`, `observability.secret_export_allowed` lisent enfin `observability-policy.yaml` (le gabarit cite ces trois codes, à la place de trois codes que rien n'émettait) ; `skills.unclassified` (avertissement) : un skill du palier `_grimoire/overrides/skills/` sans classe sous `managed_sources.skills` ; `memory.hot_degradation_undeclared` (avertissement, comme la règle) : `memory_os.hot_memory_degradation` doit dire `on_unavailable: degrade` avec un `fallback` — bloc ajouté au gabarit `memory-policy.yaml`. `observability_cockpit` rejoint `DEFAULT_SCORE_DIMENSIONS` (poids 6, celui que `compliance-score.yaml` déclarait déjà).
  - Rouge-avant / vert-après : `tests/unit/core/test_standard_pattern_checks.py` (28 cas) ; garde-fou `tests/unit/core/test_capability_map_checks.py` (aucun `checks: []`, chaque code au registre, carte = catalogue) — 3 échecs sur `origin/main`. Projet `governed` jetable : `standard verify` gagne le seul avertissement `journal.missing`, `score` inchangé (46/90), `gate check` inchangé.
  - Non fait / non mesuré : `observability.cockpit-read-only` reste une règle de code, sans check sur l'artefact ; la dégradation effective sans Redis n'est pas vérifiée à l'exécution ; les `SKILL.md` déployés sous `.github/skills/`/`.claude/skills/` (sorties de la synchro d'hôte) ne sont pas lus par `skills.unclassified`.
- feat(brand): marque « › Grimoire », famille d'icônes du site, README FR/EN réécrits autour des cinq outils (identité A). Constat : trois chartes cohabitaient (violet du logo et des bannières, Douce du site, orange du manuel), le README disait trois phrases produit différentes de celle de PyPI, et les gabarits GitHub portaient des emoji. Le sprite du cockpit est régénéré depuis la nouvelle famille, avec des alias pour les anciens noms (93836ab2).
  - Changement : `scripts/build-brand.py` génère `docs/assets/brand/` depuis `web/fonts/` (OFL) : mot-symbole clair/sombre/mono en tracés, tuile, favicon adaptatif, chevron nu, PNG 16/32/180/512, mot-symbole @2x et `social-preview.png` 1280×640 (texte seul) ; `--check` échoue si un SVG est périmé. `logo.svg`, `logo-light.svg`, `banner-*.svg` et leurs générateurs (`gen-design-system.py`, `transform-readme.py`, `transform-docs.py`, `fix-hexagon-icons.py`, `preview.html`) sont retirés.
  - Changement : `docs/assets/icons/` contient les icônes du site (grille 24, trait 1,75, `currentColor`, gris clair/sombre quand chargées en `<img>`) à la place des anciennes ; les références du manuel, de `framework/` et des archétypes suivent la table de remplacement, les icônes sans équivalent sortent des titres. `scripts/build-icon-sprite.py` est remplacé par le sprite `docs/assets/brand/icons.svg` ; le sprite du cockpit (`web/workspace/icons.svg`) reste figé sur l'ancienne famille jusqu'à sa bascule.
  - Changement : `README.md` et `README.fr.md` : mot-symbole, phrase produit, trois badges plats, trois commandes, les cinq outils (le tableau de bord, les tâches, la mémoire du projet, le contrôle des preuves, l'installation) avec une commande chacun, liens vers le manuel. `pyproject.toml` : `description` = phrase produit courte. Gabarits d'issue et de PR sans emoji, `grimoire doctor` à la place du chemin shell. `release-drafter` retiré (la note de version vient de `publish.yml`).
  - Vérification : `uv run scripts/build-brand.py --check`, `tests/unit/test_workspace_icons.py` (5 cas), `scripts/check-doc-claims.py`, `scripts/check-doc-commands.py`, `scripts/ci/audit-workflow-permissions.py`.
  - Changement (fusion avec #847) : `scripts/build-icon-sprite.py` génère le sprite du cockpit (`web/workspace/icons.svg`) depuis la nouvelle famille et `docs/assets/cockpit-icons/` ; les anciens noms encore cités par le cockpit (`rocket`, `brain`, `chart`, `team`…) reçoivent un symbole dupliqué depuis leur remplaçant (table `ALIASES`), et la famille gagne `check`, `cross`, `info`, `alert` (glyphes d'état) et `chevron` (forme C du logo).
  - Non fait : réglages GitHub (description, sujets, aperçu social) à poser à la main le jour de la fusion ; sprite du cockpit, thème et structure du manuel dans les lots suivants.
- docs(manuel): structure du manuel en quatre quadrants Diátaxis (tutoriels, guides, référence, explications), tâche GAO-identit-c-st-001, PR C de l'identité. Constat : le manuel mêlait trois accueils, des documents de travail (plan, bancs, veilles) et des pages sans place claire ; aucune page n'expliquait le tableau de bord, les tâches ni les limites.
  - Changement : `mkdocs.yml` passe à une nav à quatre onglets ; les pages déplacées gardent leur ancienne adresse par `mkdocs-redirects` (ajouté à l'extra `docs`) ; `cli-reference.md` est scindé par outil sous `docs/reference/cli/` ; quatre pages neuves (`le-tableau-de-bord`, `les-taches-et-sessions`, `les-limites`, `le-refus-de-preuve` avec le rejeu réel et le tri V0/V1/V2) et un `index.md` par quadrant ; l'accueil reprend la phrase produit et les cinq outils ; `docs/changelog.md` (périmé) devient un renvoi vers `CHANGELOG.md` ; plan Q4, banc à trois bras, détail 3.63.0, présentation, copie anglaise du démarrage et banc Rust vont dans `docs/_archive/`, et `audits/`, `bench/`, `security/`, `veille/` sont exclus du site (`exclude_docs`) ; le hook des signaux, son registre et `signaux.md` sont retirés. Les chemins lus par des tests, scripts et workflows suivent (`glossary.yaml` régénéré, catalogue de digests).
  - Rouge-avant / vert-après : `mkdocs build --strict` vert avant et après ; `test_cli_reference_drift`, `test_cost_unknown_docs`, `test_w1_11_wire_or_delete`, `test_examples_replay`, `test_glossaire_doc`, `test_workspace_glossary` pointent les nouveaux chemins et passent.
  - Non fait / non mesuré : aucun thème ni CSS touché (PR D) ; les README et `framework/mcp/grimoire-mcp-server.md` citent encore d'anciens chemins `docs/` (PR A) ; `standard/integration.md` n'est pas scindé « comment / pourquoi » ; `onboarding`/`getting-started`, `troubleshooting`/`faq`, `config`/`grimoire-yaml` ne sont pas fusionnés ; les liens `Documentation :` du glossaire généré restent absolus, comme avant.
- feat(site): partie basse de la page d'accueil en langage simple, `<head>` complet et liste blanche des pages publiées (identité B, `GAO-identit-b-pa-001`). Constat : sous le teaser, la page montrait un rejeu de terminal avec identifiants générés, le tri `V0/V1/V2`, un tableau des hôtes qui divergeait du manuel et un lien « Ce qui n'est pas prouvé » qui menait au CHANGELOG ; treize autres pages de l'ancien atelier étaient publiées sans lien depuis l'accueil.
  - Changement : `web/index.html` enchaîne la phrase produit, cinq blocs (le tableau de bord, les tâches, la mémoire du projet, le contrôle des preuves, l'installation) avec leur pictogramme, une ligne « Claude Code · Copilot », « Ce qui n'est pas prouvé » (limites vérifiées), « Sous le capot » et le pied. Le rejeu du terminal reste verbatim, replié par défaut dans le bloc du contrôle des preuves (il ne joue qu'à l'ouverture), sa légende ne porte aucun identifiant ; une seule commande est visible dans l'installation, les deux autres sont repliées. `<head>` : `<title>` explicite, `description`, `og:*`, `twitter:card`, favicon tuile chevron rouille en data URI, aucune ressource tierce. `web/documentation.html` redirige vers `docs/`. `docs.yml` ne copie plus `web/` en entier : `index.html`, `workspace/`, `media/`, `fonts/` (et `teaser/` s'il existe).
  - Non fait / non mesuré : aucune image dans les blocs (les captures du storyboard sont des plans de film, pas des écrans du produit), pictogrammes seuls ; `og:image` reprend l'affiche du teaser en attendant l'aperçu social de la marque ; les pages de l'atelier ne sont plus publiées sur Pages (`documentation.html` non plus, sa redirection ne sert que le site servi localement) ; les liens vers `docs/getting-started/`, `docs/standard/`, `docs/hosts/` n'ont pas été suivis en ligne ; aucune lecture sur navigateur réel.
- fix(vitrine): retrait des affirmations non tenues et des pictogrammes Unicode de `web/`, garde en CI (P-03). Constat : la vitrine promettait ce que le produit ne fait pas — artefacts et contrats « signés », preuves et traces « scellées », « Multi-LLM », « EARLY ACCESS » — dans `demo.html`, `kanban.html`, `observability.html`, `bp2-docs.js`, `forge-observatory.js`, `data/catalogue-export.json` et `DESIGN-BRIEF-observability.md` (19 occurrences sur `origin/main`), et la page teaser `index.html` portait 9 flèches Unicode là où les icônes sont des SVG maison.
  - Changement : chaque formulation est retirée ou ramenée à ce que le code fait (`signé` retiré ; `scellé` devient `produit`, `conservée`, `archivé` ou disparaît ; `Multi-LLM` devient `Multi-modèle`, ce que l'Observatory affiche réellement : coût par modèle et par fournisseur ; `horodatage: signé` devient `ISO 8601`, format de `evidence/service.py` et `missions/ledger.py` ; `ACCÈS EARLY ACCESS` devient `DÉCOUVRIR`). Dans `index.html`, les 9 `→` deviennent une icône masque `.i-arrow` déclarée comme les autres icônes de la page, sans toucher à la mise en page. `tests/unit/test_vitrine_garde.py` relit mécaniquement `web/` (hors `workspace/` et hors revues datées `DESIGN-REVIEW-*.md`, qui citent les anciennes pages pour les critiquer) : aucune de ces formulations, aucun reste d'outil de design et aucun pictogramme Unicode dans `index.html`.
  - Rouge-avant / vert-après : la garde trouve 19 affirmations et 9 pictogrammes sur `origin/main` (31cb2901b), 0 après ; les tests de `web/` existants (`test_doc_reference`, `test_site_chrome_local`, `test_site_data_layers`, `test_workspace_icons`, `test_serve_hardening`, `test_workspace_legacy_redirect`, `tools/test_forge_server`) restent verts.
  - Non fait / non mesuré : la garde ne couvre que les formulations listées, pas la véracité du reste des pages (`demo.html` reste un pitch) ; le `✗` du rejeu verbatim de `index.html` est conservé, c'est ce que `cmd_task.py` imprime ; les flèches du rejeu sont elles aussi imprimées par la CLI (`cmd_task.py`), remplacées ici par l'icône pour la règle « zéro pictogramme » ; rendu vérifié en local sur un seul navigateur, pas sur téléphone.
- fix(doctor): un programme de hook relatif au projet n'est plus déclaré introuvable. Constat (Forge, grimoire-kit 3.64.0) : le contrôle `hook_commands` (#760/#763) rendait FAIL pour un `grimoire-hook` relatif au projet suivi d'arguments sans `--host` (`tools/grimoire-hook --event … --control-file …`), alors que le script existe, est exécutable et que l'hôte lance les hooks depuis la racine du workspace. Cause : seule la coupe sur ` --host ` retirait les arguments ; une commande sans `--host` était résolue en entier comme un chemin.
- Changement : `command_program()` isole le programme (guillemets et `"$CLAUDE_PROJECT_DIR"/…` respectés) ; `hook_program_problem()` résout un chemin non absolu contre la racine du projet (confiné à celle-ci), accepte `$CLAUDE_PROJECT_DIR`, `${CLAUDE_PROJECT_DIR}`, `${workspaceFolder}`, exige existence et bit exécutable (Windows : `.sh` avec `bash` dans le PATH) et dit pourquoi (`not found`, `not executable`, `outside the project`). Un nom nu reste résolu par le PATH hors venv : un `grimoire-hook` du seul venv actif reste FAIL.
- Rouge-avant / vert-après : `tests/unit/test_hook_launcher.py` (rebasé sur #852 qui ne retient que les hooks lançant `grimoire-hook`) : script relatif avec arguments exécutable OK, absent FAIL, non exécutable FAIL (POSIX), variables de racine, nom nu FAIL, évasion `..`.
- Non fait : pas de test sous Windows réel (le chemin `.sh` + bash y est couvert par lecture seulement).
- docs(adr): ADR-008 proposé — actions d'infrastructure locale typées depuis le cockpit (catalogue plan/effets/vérification/inverse, classes A à D, journal, routes plan/apply/rollback) ; complète ADR-006 D7 sans élargir la Console (GAO-p18-adr-008-001)
- feat(workspace): icônes du chrome « Douce » dans le sprite du cockpit (P17, GAO-p17-sprite-001). Quatorze symboles maison (`i-space-*` × 5, `i-ui-*` × 9 ; grille 24, trait 1,75, `currentColor`, un `<title>` chacun) vivent dans `docs/assets/cockpit-icons/` et sont copiés tels quels dans `web/workspace/icons.svg` par `scripts/build-icon-sprite.py`, à côté des icônes maison normalisées de `docs/assets/icons/`. Le rail de `shell.js` appelle `i-ui-panel-left`, `i-space-savoir`, `i-ui-files`, `i-ui-panel-right`, `i-ui-dock` et les trois boutons d'épinglage de `index.html` passent du cadenas à `i-ui-pin`.
  - Garde : `tests/unit/test_workspace_icons.py` (sprite à jour, toute icône citée existe, aucune couleur littérale ni `<style>`, grammaire de chaque symbole Douce, aucun dessin en ligne dans la coque, encre désactivée lisible à travers les alias de tokens).
  - Non fait : captures du rail et des en-têtes à relire et valider par Guilhem ; les symboles `i-ui-panel-left`, `i-ui-panel-right`, `i-ui-dock` et `i-ui-focus` n'existent pas parmi les icônes maison et sont dessinés dans la même grammaire ; les cinq `i-space-*` (espaces de la refonte) ne sont pas encore appelés par la barre des six espaces actuels.
- feat(workspace): tokens Douce (P08) — `tokens.css` porte quatre surfaces (`--ground`, `--panel`, `--card`, `--overlay`), trois encres, `--acc`/`--on-acc`/`--acc-soft`, `--rub` et, par état, `--ok|warn|bad`, `-mark`, `-soft`, dans les deux thèmes, avec `color-scheme` déclaré ; les noms historiques (`--bg`, `--e1`…) y renvoient par alias, `--pri` suit `--acc`. `shell.css` : select/option/input posés par token, état désactivé des champs et des boutons (tirets, encre `--ink3`), cadenas d'épinglage ≥ 24×24 px. Preuves : `tests/unit/test_workspace_tokens.py` (contrastes ≥ 4,5:1, `--pri`/`--bad` distincts, `--ink2`/`--ink3` distincts, contrôles natifs, désactivé) et `tests/e2e/test_workspace_target_size.py` (cibles ≥ 24 px rendues). Le test e2e d'écart entre surfaces passe de cinq niveaux à 1,05 sur page/panneau. Non fait : fonte Instrument Sans et rayons de Douce, captures non encore validées.
- fix(web): la vitrine et le cockpit ne chargent plus rien hors du site. Geist et Geist Mono (sous-ensemble latin, woff2 variable, 63 Ko au total) sont vendorisées dans `web/fonts/` avec leur licence OFL ; les pages de design et les générateurs qui importaient Google Fonts pointent vers elles. IBM Plex et Source Sans, importées par deux planches de design, ne sont pas vendorisées : leur lien est retiré et la pile système prend le relais. Garde : `tests/unit/test_cockpit_airgap.py` sert `web/` par le handler du cockpit et refuse toute ressource externe chargée par une page (les liens de navigation ne sont pas visés).
- fix(missions): le claim d'un sous-agent est rattaché à (`session_id`, `agent_id`), pas à la session seule (#698, suite de #695/#696). Constat : un sous-agent (outil `Agent` de Claude Code) hérite du `CLAUDE_CODE_SESSION_ID` du parent et ne peut pas poser `GRIMOIRE_ACTOR` pour ses propres hooks ; ses hooks résolvaient donc la tâche rattachée par l'orchestrateur, ou l'ambiguïté de #695. La doc des hooks de Claude Code (code.claude.com/docs/en/hooks) dit que les événements d'outil tirés depuis un sous-agent portent `agent_id` et `agent_type`.
  - Changement : `HookInput.agent_id` (lu dans `agent_id`/`agentId` du payload) ; `TaskClaim.agent_id`, posé par le même événement `task.session_attached` (champ `agent_id`, jamais une réécriture, jamais un vol : un claim déjà tenu par un autre sous-agent garde le sien). Le `PostToolUse` qui observe un `grimoire task claim <id>` (Bash) ou l'outil MCP `task_claim` depuis un sous-agent (`hosts/decisions/enrolment.link_agent_claim`) rattache ce claim à son `agent_id`. `resolve_active_task(..., agent_id=)` : parmi les claims de la session, d'abord celui que ce sous-agent tient, puis l'ordre de #696 (dernier `task attach` explicite, `GRIMOIRE_ACTOR`), enfin, pour la boucle principale seulement, le seul claim qu'aucun sous-agent ne tient. Tous les hooks passent `agent_id=hook.agent_id`.
  - Rouge-avant / vert-après : `tests/unit/test_active_task_agent_claims.py` (15 cas : deux sous-agents d'une même session résolvent chacun la leur par le vrai chemin PostToolUse, la boucle principale résout l'attach explicite comme #696 et le seul claim sans sous-agent, pas de vol entre sous-agents ni entre sessions, sérialisation, formes Bash/MCP reconnues).
  - Non fait / non mesuré : aucun payload réel de PostToolUse émis depuis un sous-agent n'a été capturé (les hooks ne journalisent pas leur entrée brute) — la présence d'`agent_id` repose sur la doc ; sans ce champ (autre hôte, version ancienne), tout reste comme en #696. Les claims MCP ne sont observés que si le matcher PostToolUse de l'hôte couvre les outils `mcp__*`.
- refactor(missions): l'adaptateur Beads est retiré (#715). Constat sur 7c172595 : `missions/beads_adapter.py` promettait un import/export « compatible Beads » mais attendait des lignes typées `kind: issue|dependency|comment` que `bd export` n'écrit pas (une issue complète par ligne, sans `kind`) ; un vrai export rendait `issues_found: 0` sans erreur. Le module n'était atteint par aucune commande, aucun outil MCP, aucun test, aucune doc (`grep -rln beads src tests docs scripts framework` : le module seul et le détail historique du changelog 3.63.0). Code mort à revendication fausse : retiré plutôt que réaligné, faute d'un usage ou d'une fixture réelle. Le rejeu des événements `task.dependency.added` déjà écrits dans un ledger reste en place (`MissionLedger`).
- fix(upgrade): `verify` ne refuse plus une mise à jour réussie parce qu'une session vivante a écrit la mémoire pendant `apply` (#691). Constat (homelab, 3.61.1 → 3.62.0, six sessions ouvertes) : le flow s'arrêtait sur `verify refusé : [sqlite3, shared-context.md] manquants=[]` — le manifeste de `backup` comparait tout `_grimoire/_memory/` sans distinguer ce que le kit possède de l'état vivant du projet.
  - Changement : `tools/project_upgrade.py` nomme l'état vivant (`is_live_memory_path` : bases `.sqlite3`/`.db` et leurs `-wal`/`-shm`, journaux `.jsonl`, `shared-context.md`, `session-state.md`, `handoff-log.md`, `memories.json`, `qdrant_data/`, `telemetry/`) ; un écart sur ces fichiers est rangé dans `live_diffs` (rapport, JSON, ligne « toléré » en texte) et ne refuse pas. Un fichier vivant *vidé* alors que le manifeste portait du contenu reste un écart inattendu : une session réécrit, elle ne tronque pas. Les fiches et fichiers du kit (`agent-learnings/`, notes) restent jugés comme avant.
  - Le message de refus (flow, `upgrade-flow verify`, `upgrade-flow check verify`) nomme chaque seau : `écarts=[…] manquants=[…] vivants=[…]`.
  - Rouge-avant / vert-après : `tests/unit/test_project_upgrade.py` (5 cas : mémoire vivante tolérée, fichier vivant tronqué refusé, fiche du kit altérée refusée, message du nœud, nœud OK sur la seule mémoire vivante) ; 2 échouent sur la base.
  - Non fait : pas de vérification « aucune session ne tourne » avant `backup` (l'autre voie de l'issue) ; un fichier vivant hors de ces motifs (ex. `decisions-log.md` réécrit par un agent mémoire) reste un écart.
## [3.64.0] - 2026-10-20

- feat(registry): `grimoire registry dispatches` mesure les rafales de délégation par session (#687, lot B). Le rapport agrégeait les délégations (#657) par agent et par modèle, sans les regrouper ni par session ni dans le temps : impossible de savoir si la règle « plusieurs sous-agents dans un seul message » est suivie. Nouveau `TraceLedger.delegation_bursts()` (`src/grimoire/traces/ledger.py`) : par `run_id` (= `session_id` de l'hôte), les délégations triées par `started_at` sont chaînées, un écart <= `DELEGATION_BURST_WINDOW_S` (10 s, valeur de départ non mesurée) prolonge la rafale, une rafale compte >= 2 délégations. Rendu : sessions avec délégation, sessions avec rafale, nombre de rafales, taille moyenne, en texte et dans le JSON (clé `bursts`). Datation corrigée (revue) : `_record_delegation` (`src/grimoire/hosts/decisions/evidence_trace.py`) datait la délégation à `now` au `PostToolUse`, soit la FIN d'un sous-agent au premier plan, ce qui biaisait la mesure vers zéro. `started_at` vaut désormais `now - totalDurationMs` (champ `tool_response`, observé dans les transcripts locaux) ; un lancement en arrière-plan (`tool_input.run_in_background`) reçoit le tag `background` et reste daté à maintenant ; sans durée, tag `timing:completion`, et `delegation_bursts` compte ces sessions à part (`sessions_timing_approx`) au lieu de taire le biais. Aucun autre champ ajouté côté écriture : `run_id` et `started_at` existaient déjà. Les événements sans horodatage lisible, ou sans session fournie par l'hôte (`run_id` de repli `delegation-<uuid>`), sont ignorés. Test : `tests/unit/cli/test_app.py` (session avec rafale de 3, isolée, sans rafale, horodatage absent ou illisible, journal vide).
- fix(hosts): la commande de hook émise se lance dans l'environnement de l'hôte, et `grimoire doctor` le vérifie (#760). Constat : sous Windows, kit installé par `uv pip install` dans un venv, tous les outils du concierge étaient bloqués sous Copilot. Les émetteurs écrivaient le nom nu `grimoire-hook` (`hosts/emitters/base.py`), introuvable hors d'un shell où le venv est activé ; ni l'émission ni `doctor` ne le détectaient.
  - `hosts/hook_launcher.py` résout le programme à l'émission : `GRIMOIRE_HOOK_COMMAND` s'il est posé ; nom nu si `grimoire-hook` se résout dans le PATH sans le venv actif (pipx, `uv tool` : fichiers inchangés) ; chemin relatif au projet si le venv est sous la racine (Copilot : `./.venv/bin/grimoire-hook`, variante `windows` `.\.venv\Scripts\grimoire-hook.exe`, `cwd: "."` ; Claude Code : `"$CLAUDE_PROJECT_DIR"/.venv/bin/grimoire-hook`) ; sinon chemin absolu, signalé en dégradation « propre à cette machine ».
  - `grimoire doctor` : contrôle `hook_commands` (fail, module `cli/doctor_hooks.py`) quand le programme d'un hook généré ne se lance pas hors du venv actif, avec le fichier fautif et le remède (`uv tool install` ou `grimoire host sync`).
  - `docs/troubleshooting.md` §18. La suite fige `GRIMOIRE_HOOK_COMMAND=grimoire-hook` (`tests/conftest.py`) pour que les fichiers générés ne dépendent pas du poste.
  - Preuve : `tests/unit/test_hook_launcher.py` (13 tests : résolution, émission Copilot et Claude Code, guillemets, `doctor`). Rejeu de bout en bout sous Linux : projet avec `.venv` local (`uv pip install -e`), PATH sans installation globale → les commandes générées rendent `allow`, le nom nu sort en 127 ; `doctor` vert sur les nouveaux fichiers, rouge sur des fichiers 3.62.1.
  - Non vérifié : aucun hôte Windows réel ; le shell que VS Code utilise sous Windows et son traitement d'une commande de hook introuvable ne sont pas documentés ; le lien entre ce défaut et le blocage observé reste une hypothèse. Une équipe multi-OS sur Claude Code reçoit la forme de l'OS qui a lancé la synchro. `.mcp.json` (`grimoire-mcp`) a le même défaut, non traité ici.
- fix(workspace): le pouls du cockpit (UI-3) ratait une session réécrite en place sous Windows : un journal de session ou un bail de même taille, réécrit dans le même tic d'horloge du système de fichiers, gardait la même empreinte, et l'interface ne se rafraîchissait pas. Les petits JSON surveillés (≤ 64 Kio) entrent dans l'empreinte par leur contenu ; le journal d'événements, qui ne fait que croître, reste jugé par mtime et taille. Rouge-avant / vert-après : `test_un_battement_de_session_change_l_empreinte` fige la taille et le mtime (`os.utime`) et échoue sur l'ancien code.
- fix(policies): les suppressions destructives PowerShell et cmd sont reconnues comme `rm -rf` (#761). Constat sur 3.62.1 : en profil governed, sous Copilot (`runInTerminal`), `rm -rf build` était refusé mais `Remove-Item -Recurse -Force`, `Remove-Item build -Recurse`, `rd /s`, `rmdir /s` et `del /s` sortaient en `allow` : `_DESTRUCTIVE_PATTERNS` (`hosts/decisions/tool_facts.py`) ne connaissait que des formes POSIX, le garde était inopérant sous Windows.
  - Nouveaux motifs, bornés à un segment de commande : `Remove-Item` et ses alias (`ri`, `rm`, `rd`, `rmdir`, `del`, `erase`) avec `-Recurse` ou `-Force` sous n'importe quel préfixe PowerShell (`-r`, `-Rec`, `-Recurse:$true`), un pipeline vers `Remove-Item`, `rd|rmdir /s`, `del|erase /s|/q`, `Format-Volume`, `Clear-Disk`, `Initialize-Disk`.
  - `pwsh -Command`, `powershell -c` et `cmd /c` rejoignent `bash -c` parmi les introducteurs dont l'argument entre guillemets reste inspecté.
  - Rouge-avant / vert-après : `tests/test_policy_windows_destructive.py` (22 formes reconnues, 10 témoins non destructifs dont `Remove-Item fichier.txt`, `Get-ChildItem -Recurse`, `dir /s`, `rd build`, un message de commit qui les cite).
  - Non fait : pas de test sur un hôte Windows réel ; les alias PowerShell redéfinis par l'utilisateur et `Remove-Item` lancé via un script `.ps1` ne sont pas lus.
- fix(missions): la réservation de fichiers exclusifs est jugée sous le verrou d'écriture du ledger, et un claim hérité sans `expires_at` ne dure plus éternellement (campagne « Dépasser AI-DLC », chantier W1-07). Constat : `TaskService._check_exclusive_files` lisait les claims des autres tâches puis `transition_task` écrivait, le second hors du verrou (reproduit : un claim s'intercale entre les deux, deux tâches réservent le même fichier ; deux threads et deux processus de même) ; `TaskClaim.is_expired` tient un `expires_at` vide pour « jamais », donc un claim d'avant les baux gardait ses fichiers à vie.
  - Changement : la règle vit dans `missions/reservation.py` et `TaskService` s'en sert toujours en amont (verdict `exclusive_files`, trace du refus) ; le ledger la rappelle sous son verrou par `claim_guard` (K10 WIP, K11 pause), qui reçoit désormais le claim demandé en troisième argument optionnel. Un perdant de la course reçoit un `GrimoireMissionError` qui nomme le fichier et la tâche détentrice, rien n'est écrit. Au rejeu, un claim sans `expires_at` reçoit l'horodatage de son événement plus le TTL par défaut (4 h) : déterministe, sans écriture à la lecture ; un claim hérité plus vieux que le TTL est expiré et libère ses fichiers. Aucun verrou nouveau : celui de `fsio.locked` (avec ou sans `fcntl`) déjà tenu par `transition_task`.
  - Rouge-avant / vert-après : `tests/unit/missions/test_locked_reservation.py` (7 cas) ; sur `origin/main` 5 échecs (fenêtre de course, deux threads, deux processus x10, deux claims hérités), 0 après.
  - Non fait : pas d'événement d'audit à la dérivation du TTL (le rejeu ne peut pas écrire) ; `ledger.claim_task` appelé directement sans `claim_guard` (ledger hors `TaskService`) n'est pas gardé.

- feat(evidence): `grimoire evidence resolve <base>..<head>` juge chaque preuve écrite dans une plage git contre les objets git de sa tête (campagne « Dépasser AI-DLC », chantier D-13, ENG-6/ENG-9). Constat : la fiche était différée faute d'ancrage ; W3-02 ne résout la cellule Preuve que contre l'arbre de travail courant, et le journal chaîné de W4-03 (`evidence-log.jsonl`) vit sous `.runs/`, ignoré par git, avec une ancre qui se réécrit avec lui. Aucune commande ne disait si les preuves d'un ensemble de commits tiennent encore.
  - Décision d'ancrage : `<range>` est une plage de révisions git. Ses preuves sont les jetons de la cellule Preuve (forme W3-02) des lignes de `claim-ledger.md` qu'un commit de la plage a écrites ou modifiées (`git blame` à la tête) ; chaque jeton est jugé contre les blobs de la tête et du commit qui a écrit l'affirmation, jamais contre l'arbre de travail.
  - Changement : `core/standard_checks/evidence_resolve.py` (plage `A..B` ou commit seul, plage symétrique refusée, projet dans un sous-dossier du dépôt, variables `GIT_DIR` & co. retirées de l'environnement de git) et `cli/cmd_evidence.py` (groupe `evidence`, chargé à la demande). Statuts : `resolved` (lignes citées identiques depuis le commit de l'affirmation, empreinte `@sha256:` égale, run `test-run.json` commité, vert, même `run_id`, avec `tree_fingerprint`), `stale`, `missing`, `unknown` (mémoire, URL, texte libre, fichier absent au commit de l'affirmation, run illisible ou sans empreinte, erreur du résolveur). Une affirmation « prouvé » sans preuve est `missing`. Sortie texte et `-o json` (`anchor` avec base, tête, nombre de commits et `head_published`, `summary`, `fingerprint` des lignes citées) ; code 1 dès qu'une preuve n'est pas `resolved`, 2 sur une plage illisible ou hors d'un dépôt git ; une plage sans preuve rend `empty` (code 0) et le dit.
  - Rouge-avant / vert-après : `tests/unit/cli/test_cmd_evidence_resolve.py` (33 cas, dépôts git jetables) ; contre les sources de `origin/main` 33 échecs (commande `evidence` inconnue), 33 verts après.
  - Non fait / non mesuré : pas de provenance chemin -> tâche -> verdict (ENG-6) ni d'échelle de confiance : le seul lien entre un chemin commité et une tâche est le journal local, et l'attribuer depuis lui paraîtrait plus fort que son ancrage ; pas de check requis en CI (ENG-9). Une ligne citée déplacée sans être modifiée est `stale` (comparaison à numéro de ligne égal) ; une référence au fichier entier est `stale` dès que le fichier change ; un dossier cité est `missing` ; `@sha256:` est comparé aux octets du blob git (fins de ligne du dépôt, pas celles d'un checkout Windows avec `autocrlf`) ; une URL d'archive est `unknown` (aucun accès réseau), alors que W3-02 l'accepte ; seul `claim-ledger.md` est lu, pas `evidence-pack.md`.
- feat(cadrage): questions structurées dans le ledger, posées par lot (campagne « Dépasser AI-DLC », chantier W6-07, issue #564, ADP-8). Constat : K6 (#771) garait une tâche sur une seule question à la fois (`Waiting.question`) ; deux clarifications faisaient deux interruptions et deux notifications (la seconde `task ask` est refusée tant que la première attend : `waiting → waiting`), une question n'avait ni « bloquante ou non », ni « pourquoi », ni identité stable (une reprise la reposait), aucune réponse ne pouvait être déduite par l'agent avec sa source, et rien ne disait au bout de la tâche combien de questions avaient été posées, à qui et pourquoi.
  - Changement : le ledger existant est étendu, sans second registre. `Question` gagne `blocking`, `why` et `key` (écrits seulement hors défaut : une ligne K6 se relit à l'identique) ; `Waiting.batch` porte les autres questions du lot. `missions/questions.py` rejoue `task.question_asked`, `task.human_answered` (origine humaine seulement) et le nouveau `task.question_resolved` dans `MissionTask.questions`, et lit le statut (`open`, `answered`, `deduced`, `not_awaited`). `TaskService.ask_batch` pose un lot en une transition vers `waiting(human)` (une carte, une notification), ne repose pas une question dont la clé est déjà répondue ou déduite, inscrit une non bloquante sans garer la tâche et trace une déduite (non bloquante, `source` obligatoire) sans la poser. `answer` accepte toute question ouverte du lot et ne rend la tâche que quand toutes les bloquantes sont tranchées (le tick aussi) ; `resolve_question` refuse une bloquante. Surfaces : `task ask --from lot.yaml` / `--non-blocking` / `--why` / `--key`, `task questions [--open]`, `task resolve --source`, `task decisions` liste les questions ouvertes du lot ; MCP `task_update` (`ask` avec `questions`, `resolve`) et `task_show.questions_summary` ; cockpit : la carte de la vue Décisions montre une réponse par question ouverte du lot, avec son « pourquoi ».
  - Rouge-avant / vert-après : `tests/unit/test_question_batches.py` (13 cas : test de fait de l'issue — deux clarifications, une interruption, une notification —, libération seulement quand toutes les bloquantes sont tranchées, tick, agent qui ne répond pas, reprise sans re-création, registre relu et questions K6 incluses, non bloquante, déduction sourcée, forme K6 inchangée, CLI, MCP, route du cockpit) et `tests/e2e/test_workspace_executer_question_batch.py` (lot tranché question par question depuis la vue Décisions). Sur `origin/main` le module échoue à la collecte (`grimoire.missions.questions` absent) ; sonde comportementale sur la base : 2 interruptions pour 2 clarifications, question déjà répondue reposée, `task ask --from` et `task questions` inconnus (exit 2), `task_update(questions=…)` refusé. Après : 13/13 et e2e 194/194.
  - Non fait / non mesuré : `grimoire cadrage plan` (exigences acceptées vers tâches à critère mécanique, ADP-9) et l'échec de `standard verify` quand une question bloquante du cadrage reste ouverte (ADP-8, critère 2) ne sont pas livrés ; le reçu portable (lot 4.7) n'existe pas, le registre est dans `task show`/`task questions` ; « jamais poser ce qui se déduit du dépôt » reste une discipline de l'agent (la déduction est tracée, pas vérifiée) ; `task ask` sans les options du lot et les checkpoints de flow gardent le comportement K6 (une question, sans déduplication) ; la notification reste une par lot, pas une par question restante.
- feat(web): sources du teaser de la vitrine sous `web/teaser/` (tâche GAO-sources-du-t-001) : scène HTML, scripts de rendu et de son, listes de rushes avec leurs licences, polices woff2 avec licences OFL, README en français sur l'ordre de rendu. Les chemins sont relatifs au dossier ; ni rushes, ni images extraites, ni `.mp4`/`.wav` ne sont commités (`.gitignore`). Non fait / non mesuré : le teaser n'a pas été re-rendu depuis ce dossier.
- fix(missions): reprise d'une tâche garée, claim expiré re-réclamable, revue sans checks (KAN-FRICTIONS). Constat du 2026-10-19 sur la Forge (kit 3.63.0, profil governed, WIP global 5) : une session qui gare sa tâche (`task wait --reason paused`) ne pouvait plus la reprendre une fois le bail plus vieux que son TTL (le frein du bail demandait au ledger de le renouveler, le ledger refusait « tâche waiting, aucun bail », et toute commande Bash de la session était refusée, `grimoire task resume` compris) ; un claim dont `expires_at` était passé répondait « déjà claimed … jusqu'à <date passée> » ; le WIP global comptait ce claim mort ; `task move --to needs_verification` exigeait un pack que `task verify` refusait de produire sans commande déclarée.
  - Changement : le frein du bail (`hosts/decisions/lease_brake.py`) ne renouvelle plus le bail d'une tâche garée ; au-delà du TTL il refuse avec un motif qui nomme la reprise, et laisse passer `grimoire task resume|show|attach|renew <sa tâche>` (options `--epoch`, `--actor`, `--session-id` seulement ; guillemets, `$`, enchaînement, autre tâche, `--project-root`/`--ledger-root` refusés). La sortie d'attente renouvelle le battement de cœur (`lease.mark_waiting`) : une reprise après une longue pause n'est plus aussitôt jugée perdue. `lease.lost_reason` porte la règle de perte (`fence_pending`, `expires_at`, `heartbeat`) que `lease_view` utilise ; un claim perdu se réclame directement (`missions/reclaim.py`, sous le verrou : barrière du bail, `task.transitioned` vers `ready` marqué `reclaimed`, nouveau claim au nouvel epoch, non compté comme tentative) par son titulaire, ou par un autre acteur si l'hôte du claim a un frein vérifié ; horloge incohérente ou bail vivant : refus inchangé. Le WIP (garde du claim et vue d'admission du tick) ne compte que les claims vivants. `task verify --evidence <fichier>` (tâche sans commande seulement) écrit un pack déclaratif : fichiers du projet hachés (SHA-256, taille, `file:<chemin>`), verdict `skipped` (`created_by: declarative`), code de sortie 4 ; la revue s'ouvre, la clôture reste refusée par `review_gate`, qui le dit. MCP : `task_update move` refuse le sentinelle `unknown` comme un état inconnu. L'exemption « `grimoire task` simple » du frein refuse aussi `"$(…)"` et l'accent grave entre guillemets doubles.
  - Déjà corrigé sur `main` (prouvé par test, inchangé) : le MCP connaît `waiting` depuis K6 (#801, 3.63.0) et lit un état d'une version plus récente comme `unknown` sans planter ; l'erreur « 'waiting' is not a valid TaskState » venait d'un serveur MCP lancé depuis un clone en 3.57.0. Les appels MCP `task_update(resume)` et `task_show` d'une session garée passaient déjà le frein.
  - Rouge-avant / vert-après : `tests/unit/missions/test_kanban_resume_frictions.py` (33 cas, dont un de bout en bout par le point d'entrée réel du hook dans un processus séparé) ; 25 échecs sur `origin/main`, 33 verts après.
  - Non fait / non mesuré : la reprise directe d'un claim perdu ne rejoue pas les autres prudences de la récolte (travail non poussé du worktree, tentatives épuisées) ; un claim repris puis refusé par une autre garde (pause, WIP par rôle) reste `ready` ; la clôture d'une tâche à preuve déclarative reste sans chemin gouverné (#707).

- fix(missions): le classifieur de vérifiabilité ne fait plus monter en V2 sur une preuve attendue en prose, et reconnaît « fichier attendu X existe » (#835). Constat du 2026-10-19 : une tâche aux critères « fichier _scratch/x.json existe » et « fichier _scratch/x.md existe » restait V2 parce que la preuve attendue « x.json et x.md » était comptée comme critère non reconnu, donc `recommended_model` nul et délégation sur le modèle de la session.
  - Changement : `_criteres` garde toute l'`acceptance` mais n'ajoute une `expected_evidence` que si elle est reconnue (mécanique ou revue), car la preuve décrit ce qu'on produit, pas un verdict (le champ avait été inclus pour qu'un critère sérieux logé là ne passe pas en V0 faute d'être lu, cela reste vrai). Le motif `fichier_attendu` accepte un qualificatif fermé (attendu, généré, produit, créé, livré, de sortie) et « est présent / créé / généré / produit », « existe bien » ; aucun mot libre entre « fichier » et le verbe.
  - Rouge-avant / vert-après : `tests/unit/missions/test_verifiability.py` et le corpus étiqueté, 3 échecs sur `origin/main`, 0 après ; « un fichier décrit pourquoi X existe » reste V2. Non fait : formulations anglaises voisines (« expected file X exists »).
- test(e2e): scénario live Claude Code, le hook de preuve bloque une clôture sans preuve puis le gate se ferme, rapport JSON (campagne « Dépasser AI-DLC », chantier D-12). Constat : aucun e2e ne faisait tourner un vrai hôte sur un projet enrôlé ; le dossier e2e ne couvre que le cockpit (Playwright), et rien ne montrait qu'un `Stop` refusé par le kit l'est aussi sous `claude -p`.
  - Changement : `scripts/bench/live_claude_e2e.py` prépare un projet jetable `governed` (hooks émis pour Claude Code, relais `grimoire`/`grimoire-hook` vers `src/`), fait attester l'octroi W5-06 et l'approbation de la commande de vérification par deux tours d'une session d'hôte, puis joue deux appels sans humain (`GRIMOIRE_UNATTENDED=1`) : U1 écrit le code sans preuve, U2 (`--resume`) remet la preuve. Le rapport lit le TraceLedger, le Mission Ledger, les journaux d'autorité et les packs, jamais ce que la session déclare ; il compte le coût réel (delta de session sous `--resume`, inconnu = arrêt, reste du plafond en `--max-budget-usd`, garde de B-06), refuse `--bare`, construit l'environnement de zéro, charge seul le MCP du projet (`--strict-mcp-config`), écarte les `CLAUDE.md` parents (`claudeMdExcludes`), purge et cherche les identifiants en fin de run. `tests/live/test_claude_code_live.py` (marqueur `live_host`, désélectionné par `addopts`, ignoré sans `GRIMOIRE_LIVE_CLAUDE_MAX_USD`, en échec avec `GRIMOIRE_REQUIRE_LIVE=1`). Procédure : `docs/bench-three-arms.md` §16.
  - Rouge-avant / vert-après : `tests/unit/test_live_claude_e2e.py` (28 cas, sans modèle) : erreur de collecte sur `origin/main` (module absent), 28 verts après.
  - Passe réelle (2026-10-19, autorisée par Guilhem, plafond 5 $) : 1,0118 $ au total, dont 0,4875 $ pour le passage retenu (4 appels `claude-opus-5-5`, Claude Code 2.1.302) ; deux premiers passages à 0 $ (« Not logged in » : MCP d'un dépôt parent, `HOME` isolé par la suite de tests), un diagnostic haiku à 0,0652 $. Premier `Stop` de U1 en `block` (`refusal:task_unparked`, aucun pack au ledger), premier `Stop` de U2 en `allow`, `gate check --strict` vert, tâche en `needs_verification`.
  - Non fait / non mesuré : n = 1 sur une tâche d'un fichier ; Copilot non couvert ; aucun job CI ni garde de release n'exige ce test (décision 11 du plan) ; l'octroi est obtenu et relu mais ne décide aucun checkpoint (aucun flow ne tourne) ; les tours humains sont des messages pré-enregistrés rejoués avec l'autorisation de l'opérateur.
- fix(hooks): une session qui a rendu sa tâche (`needs_verification`) ou l'a bloquée, échouée, annulée ou fermée n'est plus refusée au `Stop` comme « clôture hors tâche » ; le motif de chaque refus est écrit au TraceLedger (chantier D-12). Constat du scénario live : `stop_scope` ne résout une tâche que `claimed`/`running` ; rendue, elle retombait sur `bootstrap`, et la session qui avait écrit était refusée — après avoir fait le geste même que le refus de `Stop` lui demandait. Et cette consigne, pour une tâche `claimed`, était une transition que le ledger refuse (`claimed → needs_verification`).
  - Changement : `enrolment.parked_task_of_session` (états `waiting`, `needs_verification`, `blocked`, `failed`, `cancelled`, `closed`, claim de cette session) remplace la seule attente K6 dans `evidence_gate._parked_task` ; `needs_verification` reste jugée sur ses gates de preuve (refus nommé si rouges). La consigne d'une tâche `claimed` passe par `--to running`. `runtime._record_decision` ajoute `refusal:<motif>` (`task_unparked`, `no_active_task`, `gates_red`, `guard_error`) aux traces de refus.
  - Rouge-avant / vert-après : `tests/unit/test_stop_handover.py` (7 cas) : 5 échecs sur `origin/main` (« Clôture hors tâche » au lieu de la sortie ou du refus nommé, consigne `claimed` invalide, aucun motif de refus dans la trace), 7 verts après ; `test_waiting_surfaces.py` inchangé et vert.
  - Non fait : une session qui a rendu sa tâche puis réécrit hors tâche n'est pas distinguée (la dernière tâche du journal de session la couvre) ; la sortie d'une tâche `blocked`/`failed`/`cancelled` ne revérifie rien.
- docs(bench): campagne B-06 palier 1, bras `aidlc` seul (AI-DLC 2.11.0, scope `express`, pilote `scripted-person`), les 20 tâches du lot L, k=1, sous plafond dur de 160 $ (`docs/bench-three-arms.md` §15.6 pré-enregistrement, §15.7 résultats). Dépense exacte 87,2406 $ (125 appels, aucun coût inconnu ; la somme des `costUSD` du `modelUsage` final de chaque session donne le même total). Succès aux tests cachés 17/17 runs valides (IC95 Wilson [81,6 %, 100 %]), 20/20 tous runs ; coût par run médian 4,592 $ (0,886 à 5,930 $), environ 28 fois le `nu` et 22 fois `kit-gov` du lot L sur les mêmes tâches ; 494 s médians ; 4 interventions humaines simulées par tâche résolue (B-05). Comparaison descriptive seulement : le banc est saturé et l'écart minimal détectable à 20 tâches est de 44 points.
  - Constat : dans 18 runs sur 20, l'agent principal écrit la solution avant le stage Code Generation ; le succès mesure surtout le modèle, le coût mesure la cérémonie AI-DLC. Deux runs parquent le workflow en Reverse Engineering sur la consigne commune et reçoivent 12 `Approved.` inutiles (0,51 $ comptés). Trois signalements W1-08b relus : faux positifs (`./` et `/`).
  - Palier 2 recalculé à la médiane mesurée : 40 runs de plus, 183,68 $, soit 270,92 $ pour le bras `aidlc` paliers 1 et 2 (362,76 $ avec la marge de 1,5) ; non lancé, non autorisé.
  - Non fait : aucun code modifié ; `build_report` n'agrège toujours pas `aidlc` ; données brutes hors dépôt, comme celles du lot L.
- feat(hosts): matrice règle × hôte calculée depuis les émetteurs, `grimoire host matrix` / `grimoire host probe`, et `host_sync.ok` exigé (campagne « Dépasser AI-DLC », chantier D-07). Constat : la dégradation par hôte était déclarée (`Degradation`) mais jamais confrontée à ce que l'émetteur écrit, et `host sync` / `up` annonçaient « à jour » alors qu'un fichier hôte modifié à la main avait été préservé, donc non régénéré.
  - Changement : `hosts/matrix.py` rend le plan réel de chaque hôte et cherche chaque hook (commande `--event`) et chaque règle de couche (ligne et `globs:`/`applyTo:`) dans ce que le plan écrit, sinon la `Degradation` qui le dit ; sont des échecs `undeclared` (ni écrit ni déclaré), `inert` (écrit pour un événement que l'hôte ne déclenche pas) et `misscoped` (règle scopée écrite sans sa portée). `hosts/probe.py` confronte la matrice au disque sans rien écrire (`applied`, `declared`, `missing`, `stale`, `conflict`) ; `host probe` sort en 1 si un fichier émis manque, est périmé ou édité à la main, et aussi si aucun hôte n'a été sondé. `HostSyncOutcome.ok` est faux quand un fichier est préservé (champ `conflicts`) ; l'étape `host_sync` de `up` échoue sur un conflit d'un hôte activé ; `upgrade-flow apply` échoue (`host_sync`, champ `host_drift`) si des surfaces hôtes manquent ou sont périmées après `up`. Les hôtes sans fichier de règles scopées déclarent aussi les règles non livrées (couche extérieure, à l'ombre, en conflit).
  - Rouge-avant / vert-après : `tests/unit/test_hosts_matrix_probe.py`, `test_hosts_sync_ok.py` et `tests/unit/cli/test_cmd_host_probe.py` (27 cas) ; sur `origin/main` 10 échecs et 1 erreur de collecte (module `grimoire.hosts.matrix` absent), 0 après.
  - Non fait / non mesuré : la sonde vérifie que le hook est câblé dans le fichier que l'hôte lit, pas que l'hôte l'exécute (le rejeu reste `upgrade-flow probe-hook`, Claude seul) ; un fichier modifié à la main reste préservé (`conflict`) et fait désormais échouer `up` au lieu de passer pour synchronisé, `host sync --force` l'écrase ; seuls les hooks de gouvernance et les règles de couche sont des lignes de la matrice, pas les agents, skills ni commandes.
  - Correction de revue (D-07-S1-01) : `up` rendait `host_sync failed` à chaque passage sur un fichier hôte légitime de l'utilisateur (un `.claude/agents/concierge.md` à lui, un `settings.json` avec commentaires), `doctor` répondait 28/28 et la seule issue offerte, `host sync --force`, l'écrasait. Nouvelle clé `hosts.preserve` (liste de chemins, schéma et validation) : le fichier n'est jamais écrit, `up` passe en le listant « user-owned », `host probe` le rend `user-owned` (jamais `applied`), `host matrix` ajoute `file:<chemin>` au statut `preserved` et marque `preserved` les hooks et règles qu'il aurait portés (non livrés). Sans déclaration, `up` reste en échec mais nomme les deux issues (déclarer, ou `--force`), et `doctor` échoue aussi (`host_sync_<hôte>`) : un fichier préservé n'est jamais présenté comme synchronisé. La sonde rend `disabled` (non verte) un hook câblé quand `disableAllHooks: true` figure dans `.claude/settings.json` ou `settings.local.json`.
  - Correction de CI (parité Rust) : `hosts.preserve` est aussi déclaré dans le cœur Rust `grimoire-schema-core` (schéma identique, clé connue, validation relatif / sans `..`) et dans `validator.py`, qui signalait la clé comme inconnue ; 6 cas de parité ajoutés.
  - Rouge-avant / vert-après (correction) : `tests/unit/test_hosts_preserve.py` (18 cas) ; 15 rouges sur le commit précédent, 18 verts après ; scénario `init` + `up` avec un `concierge.md` à l'utilisateur rejoué en CLI (échec nommant les deux issues avant déclaration, `up` rc 0 deux fois de suite après, fichier intact, `host sync --force` ne l'écrase pas).
  - Non fait / non mesuré (correction) : `disableAllHooks` n'est lu que dans les deux fichiers du projet (pas `~/.claude/settings.json` ni les réglages d'organisation) et seulement pour Claude Code ; `hosts.preserve` ne s'applique qu'à un chemin émis par le kit (une entrée sans correspondance est ignorée sans avertissement) ; l'échec de `doctor` sans déclaration est un changement de comportement (28/28 devient 27/28 sur un tel projet).
- test(cli): les 11 commandes de file, d'orchestration et de vague arrivées sans preuve (`queue plan`, `queue release`, `orchestrate pause|resume|status|tick|install-timer|uninstall-timer`, `task wave-checkpoint|approve-check|review`) portent le marqueur `covers` (D-09). Les tests existants qui les exerçaient par un sous-groupe Typer passent par l'application racine (`["queue", "plan", ...]`), les cas manquants sont écrits : `task review` (nominal, NOT-READY fondé, refus), `task approve-check` (JSON, terminal nu refusé, sans commande), `task wave-checkpoint` (action inconnue, empreinte périmée), `orchestrate tick` sans `--apply` (rien d'écrit), minuteur avec `systemctl` injecté et sans systemd. Aucun défaut trouvé dans les commandes. Non mesuré : le marqueur reste une déclaration que le script ne rejoue pas.
- feat(bench): bras `aidlc` du banc comparatif, AI-DLC 2.11.0 épinglé, scope `express`, HOME isolé, dry-run sans modèle et plafond en dollars fermé par défaut (campagne « Dépasser AI-DLC », chantier B-06). Constat : le protocole comparatif (section 6 du plan) n'avait aucun bras AI-DLC, son coût n'était « pas mesuré » et `three_arms.py` lance ses campagnes sans plafond en dollars. AI-DLC ne documente aucun mode sans humain qui lève ses gates : `AIDLC_UNATTENDED=1` retient l'autorité humaine (`CHANGELOG.md` d'AI-DLC 2.11.0, l. 129 et 281-284).
  - Changement : `scripts/bench/aidlc_arm.py` vérifie le tag `v2.11.0` d'un clone de référence (commit `6a378b53`, lecture seule), installe AI-DLC par l'installeur du tag sous `homes/aidlc` (`AIDLC_BIN_DIR`, `AIDLC_INSTALL_ROOT`, `XDG_DATA_HOME`), lance `aidlc config --harness claude --mcp none --yes` dans chaque dépôt de tâche et joue les mêmes tâches que `nu`/`kit-gov` (préparation W1-08a, contrôle de transcription W1-08b, tests cachés, `RunRecord` sous `arm: "aidlc"`). Deux pilotes au choix du pré-enregistrement : `unattended` (mode documenté, une intervention déclarée par run) et `scripted-person` (configuration spéciale déclarée : `Approved.` rejoué par `claude -p --resume`, au plus 12 fois). `--dry-run` n'appelle aucun modèle et écrit le pré-enregistrement (empreinte sha256) que `--run` exige à l'identique. `--run` refuse de partir sans `--max-total-usd` ; `state/aidlc-budget.jsonl` encadre chaque appel (`begin`/`end`), le cumul connu est comparé au plafond avant chaque appel, et un coût inconnu, un appel jamais terminé ou une ligne illisible bloquent tout appel suivant. `--estimate` chiffre un run (2,13 à 7,70 $ en `scripted-person`) et le protocole (151 à 485 $ à 20 tâches par bras, 2 963 à 9 527 $ au n requis par B-02) depuis le tarif implicite et les coûts mesurés du lot L, hypothèses dans `docs/bench-three-arms.md` §15. `run_claude_headless` accepte `extra_args`, `append_log` (seules les lignes de l'appel comptent) et `bare` ; le bras `aidlc` n'utilise jamais `--bare`, qui ignorerait les hooks et la skill d'AI-DLC.
  - Rouge-avant / vert-après : `tests/unit/test_bench_aidlc_arm.py` (31 cas : plafond absent, nul, négatif, non fini ; arrêt au plafond ; coût inconnu bloquant ; journal rejoué, appel commencé jamais terminé, ligne illisible ; pilotes ; dry-run de bout en bout sans appel modèle, `subprocess.Popen` refusant `claude` ; pré-enregistrement exigé ; tarif reproduit sur une ligne du lot L). Sur la base (`origin/main`), le module échoue à la collecte (`aidlc_arm.py` absent) ; avec `aidlc_arm.py` sur le `three_arms.py` de la base, les 2 cas de `run_claude_headless` échouent (`TypeError`, paramètres absents) ; tous verts avec le correctif, `tests/unit/test_bench_*.py` complet vert. Dry-run réel sur le clone AI-DLC et le polyglotte du lot L (`--no-install`) : 20 tâches préparées, pré-enregistrement écrit, aucun modèle lancé.
  - Pilote payant (2026-10-19, autorisé par Guilhem, plafond 16 $ pour la passe) : 6,5416 $ dépensés, `python/zipper`, k=1, Opus 5.5, `oauth-copy`, jamais `--bare`. Un run de contrôle montre que, sous `claude -p --resume`, `total_cost_usd` et `modelUsage` cumulent la session (`usage` reste propre à l'appel) : le garde-fou compte désormais la différence avec le total précédent de la session (sans quoi le run `scripted-person` aurait été compté 19,37 $ au lieu de 4,90 $), passe le reste du plafond en `--max-budget-usd` à chaque appel, et `--budget-ledger` partage un plafond entre workspaces. `scripted-person` : 4,8963 $, 6 appels (5 reprises), 85 tours, 618 s, 4 gates levés et 2 questions, `WORKFLOW_COMPLETED`, tests cachés réussis, dans la fourchette estimée (2,13 à 7,70 $). `unattended` : 1,4835 $ (au-dessus de l'hypothèse haute de 1,33 $), 1 appel, 168 s, arrêt en Reverse Engineering ; l'agent principal avait écrit `zipper.py` avant le workflow, donc les tests cachés passent sans qu'AI-DLC ait produit le code, et W1-08b classe le run `invalid` (faux positif probable : gras Markdown lu comme glob). Campagne recalculée à k=3 : 316,62 $ à 20 tâches, 1 567,27 $ à 99, 6 221,57 $ à 393 ; plafond proposé en deux paliers (160 $ puis 500 $ à 20 tâches). n = 1 : aucune conclusion de supériorité. Détail dans `docs/bench-three-arms.md` §15.5. Tests : 7 cas ajoutés (delta de session, total en baisse compté en entier, `modelUsage` non sommé, reste du plafond par appel, `--max-budget-usd` dans la commande, journal partagé), 6 rouges sur le code d'avant, 38 verts.
  - Non fait / non mesuré : n = 1 par pilote, sur une tâche où le `nu` n'échoue jamais ; ce que `--max-budget-usd` compte sous `--resume` (jamais atteint) ; les champs de jetons de `RunRecord` somment `usage`, sans les sous-agents ; `--auth api-key` ajoute `--bare`, qui coupe les hooks : le banc le refuse désormais pour tout bras gouverné (voir ci-dessous ; aucun résultat publié concerné, lots J à L en `oauth-copy`) ; `build_report` n'agrège pas encore `aidlc` ; le plafond de campagne reste la décision de Guilhem (décision 5).
  - Correctif `--bare` (fail-closed). `scripts/bench/bare_guard.py` : `refuse_bare_for_governed_arms` fait échouer `main` (`SystemExit`, avant tout appel payant) quand `--auth api-key` est demandé avec un bras autre que `nu`, et `run_claude_headless(arm=...)` refuse aussi un `--bare` forcé pour ces bras ; seul `nu` garde `--bare`. Rouge-avant / vert-après : 8 cas dans `test_bench_three_arms.py` (rouges sur la base : `arm` inconnu de `run_claude_headless`, `main` démarre le run). `test_bench_model_pin.py` jouait tous les bras sous un `api-key` simulé : il simule désormais `oauth-copy`. Non fait : un `api-key` sans `--bare` pour les bras gouvernés.
  - Correctif W1-08b (faux positif du pilote). `hidden_reads._writes_prose_only` : le corps d'un heredoc écrit par `cat`/`tee` vers un fichier de prose n'est plus lu comme des références (le gras `**zipper**` valait un glob qui visait `hidden-tests/`). Les corps envoyés à un shell ou à un interpréteur, écrits vers un script ou vers une cible inconnue restent analysés ; vraies lectures inchangées. Rouge-avant / vert-après : 16 cas (6 `clean`, dont le cas réel du pilote en extrait minimal ; 10 contre-épreuves `invalid`). La transcription réelle du pilote `unattended` passe de `invalid` à `clean`. Aucun résultat publié n'est reclassé.

## [3.63.0] - 2026-10-19

Le texte intégral des entrées (constat, changement, rouge-avant / vert-après, non fait / non mesuré) est dans [`docs/changelog-3.63.0-detail.md`](docs/changelog-3.63.0-detail.md) : la note de release GitHub est tronquée en silence au-delà de 125 000 unités UTF-16, et ce détail en pèse plus de 500 000.

- **Missions et ledger.** Planification en DAG (claim refusé si une dépendance n'est pas close), vivacité à quatre valeurs, bail renouvelable avec epoch de fencing et récolte, worktree éphémère par tâche et merge série vérifié, état `waiting` à raison nommée, lecteur du ledger tolérant aux états inconnus, V0 réservé aux commandes déclarées, `task verify`, classeur de vérifiabilité, route de cérémonie déterministe et profondeur de test opposable (#771, #772, #780, #781, #783, #798, #799, #801, #803, #747, #732, #825, #826).
- **Flows.** Décision humaine attestée sur les checkpoints, `directive_id` stable et reprise liée à la directive émise, plafond d'amendements et `accept_as_is` tracé, `flow doctor` et reprise sûre après un crash, octroi d'autonomie à périmètre révocable, parallélisme borné du fanout, reprise rejouée sans casse (#753, #754, #759, #751, #800, #805, #744).
- **Hôtes et hooks.** opencode comme hôte natif, frein PreToolUse, capacités d'hôte et fence git pre-push, contrats d'hôtes capturés, garde Stop à signature de progrès, réinjection du run de flow actif après compaction, panne du gate « fini » tracée en fail-closed, autorité du canal et hygiène d'hôte (#796, #797, #765, #779, #755, #785, #729, #706, #711, #717, #782, #735, #740).
- **Preuve et dispatch.** Verdict `tampered` si un test protégé a changé, vert vide détecté pour tout exécuteur de tests, empreinte de run par contenu et toolchain, cellule Preuve d'un claim qui doit se résoudre, couverture critère vers preuve du dossier d'acceptance, verdict `current`/`stale`/`unverified` du graphe de code (#804, #752, #746, #756, #814, #748, #734).
- **Coûts, routage et fournisseurs.** Routeur déterministe par politique déclarative, suspension datée par fournisseur et par modèle, table de tarifs versionnée et sourcée, coût inconnu = `unknown` et jamais 0, garde de coût du banc (#802, #786, #757, #733, #758, #743, #730, #824).
- **Mémoire, politiques et runtime.** Supersession branchée sur la recherche, `memory lint` et intake branchés, approbation liée à l'empreinte de l'action, écritures atomiques et verrouillées du kernel (#749, #732, #731, #742, #736, #827).
- **Cockpit et web.** Jeton de session et en-tête obligatoire sur toute mutation, icônes SVG maison et hiérarchie visuelle de la vue de travail, page teaser `web/index.html` (`c564e700`, #791) dont la commande se lit en entier sur téléphone (`122c5ce2`, poussé sur `main` sans PR ni entrée de CHANGELOG, rattrapé ici et cité par son hash) (#793, #794, #791).
- **Livraison et CI.** Une seule voie de release (notes tirées du CHANGELOG), actions GitHub épinglées par SHA avec garde, scan de secrets sur l'historique, suite complète sur trois systèmes, import du paquet `tests` interdit, catalogue de hachages régénéré (#741, #737, #738, #739, #750, #795, #745).

## [3.62.1] - 2026-10-03

- fix(hosts): `Stop` nomme les claims concurrents au lieu de juger la tâche fantôme `bootstrap` (issue #692, suite de #680/#686 ; empilée sur la PR #696). Rejeu du 2026-10-02 : deux claims sans session, aucune écriture — `UserPromptSubmit` nommait les candidates et `grimoire task attach <id>`, mais `decide_evidence_gate` ne traitait l'ambiguïté qu'après une écriture et retombait sinon sur « Tâche bootstrap encore en état proposed ».
  - `hosts/decisions/evidence_gate.py` : résolution `ambiguous` sans écriture → contexte (rendu en `systemMessage` au Stop) qui nomme les candidates et la commande de rattachement, sans bloquer ; avec écriture, comportement de #680 inchangé (blocage en profil bloquant).
  - Rouge-avant / vert-après : `tests/unit/test_stop_names_candidates.py` (3 tests, 1 rouge avant).
- fix(missions): les claims de sous-agents ne détrônent plus la tâche que l'orchestrateur a rattachée (issue #695, suite de #680/#686). Les sous-agents de Claude Code héritent de `CLAUDE_CODE_SESSION_ID` : leurs `task claim` se rattachaient à la session du parent, `resolve_active_task` voyait plusieurs claims sur la même session et retombait sur `ambiguous` → « Clôture hors tâche », même juste après un `grimoire task attach`.
  - `grimoire task attach` marque désormais son événement `task.session_attached` (`explicit: true`) et s'écrit même quand la session est déjà celle du claim ; le claim porte `attached_explicit_at`. Le rattachement automatique (claim CLI/MCP, hook) ne le marque jamais. `core/standard_state.py` : parmi les claims d'une session, le dernier attach explicite gagne, puis le claim unique de `GRIMOIRE_ACTOR` dans la session ; sinon, comportement de #686 inchangé (`ambiguous`, candidates nommées).
  - Rouge-avant / vert-après : `tests/unit/test_active_task_subagent_claims.py` (7 tests, 5 rouges avant).
  - Non fait : résoudre sur l'identité du sous-agent lue dans le payload de hook (`agent_id`/`agent_type`) — le claim ne porte que son `--actor`, aucune clé ne relie les deux ; seul `GRIMOIRE_ACTOR` départage. Critère « réel » (orchestrateur + 3 sous-agents) non rejoué.
- docs(bench): rejeu `nu` et `kit-gov` le même jour (lot L, issue #642) — `docs/bench/rejeu-lot-l-2026-10-02.md`. Trois critères sur quatre atteints, pas le coût (≈124 % du nu, surcoût fixe ≈ +0,04 $ par run) ; excursions 16,7 % au sens binaire, 8,3 % coûteuses ; la régression `rust/scale-generator` du lot K vient d'une contamination du lot J (dossiers de tâche réutilisés, tests visibles), pas du kit. `scripts/bench/excursions.py` : un gate enchaîné après un heredoc compte comme un appel, distinction excursions bénignes et coûteuses (J et K inchangés : 18/60, 16/60).

## [3.62.0] - 2026-10-02

- docs(bench): rejeu du bras `kit-gov` après le correctif du lot K (#651, issue #642) — `docs/bench/rejeu-lot-k-2026-09-29.md`. Aucun des quatre critères chiffrés de l'issue n'est atteint : part des runs « avec excursion » 26,7 % (16/60, contre 30,0 % au lot J, cible < 10 %), coût médian/tâche résolue ≈122 % du nu (contre ≈112 %, cible ≤ 105 %), succès 90,0 % (contre 96,7 %, cible ≥ 96,7 %) ; `gate check` vert au dernier appel 60/60 (seul critère atteint, inchangé depuis le lot J). Régression notable non tranchée par ce lot : `rust/scale-generator` passe de 3/3 à 0/3 (gate agent vert, verdict caché du harnais négatif — même signature qu'avant le lot I). Campagne propre (60/60 `completed`, aucun incident d'authentification, contre 30 runs perdus au lot J).
  - Classifieur d'excursions versionné : `scripts/bench/excursions.py` (`--workspace`, `--arm`, `--reference-arm`, `--json`), qui retrouve 18/60 (lot J) et 16/60 (lot K) ; test `tests/unit/test_bench_excursions.py`.
- fix(missions): plusieurs sessions sur un projet — la tâche active suit le `session_id`, et un claim lancé depuis une session la rattache (issue #680). Constat du 2026-10-02 (homelab, kit 3.61.1) : cinq sessions Claude Code, deux claims, et toutes retombaient sur `bootstrap` (« Clôture hors tâche » au Stop), parce que `resolve_active_task` sautait le niveau « claim unique » dès que deux claims coexistaient sans jamais lire le rattachement `task.session_attached` du lot A (#638), et qu'un `grimoire task claim` lancé depuis la session ne produisait pas cet événement.
  - `core/standard_state.py` : `resolve_active_task(..., session_id=)` (et `active_task_id`) — ordre `GRIMOIRE_TASK_ID` → claim rattaché à ce `session_id` (source `session_claim`) → claim unique → board → `bootstrap`. Plusieurs claims dont aucun n'est rattaché à la session rendent `bootstrap` avec la source `ambiguous` et `ActiveTask.candidates` : le repli n'est plus silencieux. Sans `session_id` explicite, la session se lit dans `GRIMOIRE_SESSION_ID` puis `CLAUDE_CODE_SESSION_ID` (`current_session`) — jamais par « journal le plus récent », qui rattacherait au hasard.
  - Hooks : `UserPromptSubmit`, `SessionStart`, `Stop` (`decide_evidence_gate`) et les décisions qui appellent `active_task_id` passent `hook.session_id`. En cas d'ambiguïté, le contexte et le motif du Stop nomment les claims concurrents et la commande de rattachement au lieu de « aucune tâche en cours ».
  - Rattachement : `grimoire task claim` et l'outil MCP `task_claim` rattachent la session désignée par l'environnement (`TaskService.attach_current_session`, un événement `task.session_attached`, silencieux sans session identifiable ou si le claim appartient déjà à une autre) ; nouvelle commande `grimoire task attach <id> [--session-id]` pour rattacher après coup.
  - Rouge-avant / vert-après : `tests/unit/test_active_task_multi_session.py` (16 tests — deux sessions deux claims, claim CLI depuis une session, deux claims sans rattachement, un seul claim inchangé, hooks Prompt et Stop) ; `tests/conftest.py` retire `GRIMOIRE_SESSION_ID`/`CLAUDE_CODE_SESSION_ID` de l'environnement des tests.
  - Non mesuré : le critère « réel » de l'issue (deux vraies sessions Claude Code sur un même projet) ; seuls des payloads de hook et l'environnement simulés ont été rejoués. Et le claim par un serveur MCP dépend de l'export de `CLAUDE_CODE_SESSION_ID` à ce processus par l'hôte, non vérifié.
- fix(hosts): abaisser le profil du standard (`_grimoire/standard/standard-profile.yaml`, champ `profile`) demande désormais une confirmation au lieu de passer inaperçu (issue #670, party mode `_scratch/party-oss/gouvernance.md` idée A). Ce champ sélectionne le jeu de règles appliqué (`_risk_profile`, `tool_policy.py:38-42`) mais n'était protégé par aucune règle : un `Edit`/`Write` de l'agent, ou une commande shell (`sed -i`, redirection), pouvait faire passer un projet de `production`/`governed` à `starter` en une seule écriture, relâchant tous les seuils de gouvernance sans toucher une seule règle — le principe « ne peut que se durcir », déjà de fait dans le moteur de règles (`policies/engine.py:171-181,247-251`) et déjà la doctrine des verdicts probabilistes (`docs/veille/decision-models-2026-09.md:86-89`), ne couvrait pas le levier qui choisit *quel* jeu de règles s'applique.
  - `tool_facts.classify_tool` (`src/grimoire/hosts/decisions/tool_facts.py`) : nouveau champ `ToolFacts.standard_profile_write` — `None` si l'appel ne mute pas ce fichier, `""` si une commande shell le mute (contenu proposé non lisible depuis la ligne de commande), sinon la valeur du champ `profile:` lue dans le `content`/`new_string` d'un `Write`/`Edit` (regex, pas de parse YAML — pas d'import lourd sur le chemin `PreToolUse`). Portée volontairement étroite : seul ce fichier et son champ `profile` sont concernés, jamais le reste de `_grimoire/standard/` (`policies.yaml`, `task-board.yaml`…).
  - `tool_policy._profile_downgrade_check` (`src/grimoire/hosts/decisions/tool_policy.py`) : compare la valeur proposée à `active_profile_id` via le classement existant `agentic_standard.profile_rank` (déjà utilisé par le garde anti-downgrade de `grimoire up`, importé paresseusement — seulement sur ce chemin rare). Appelé au même point que `_untrusted_escalation` (uniquement depuis la branche où le moteur a déjà conclu `allow` : ne rétrograde jamais un `deny`/`ask` existant). Une valeur plus faible, ou un contenu shell illisible → `Outcome.ASK` avec un motif explicite (« abaisse le profil de X à Y »). Jamais `deny` : un downgrade légitime existe (archivage de fin de vie) — décision humaine, pas un refus.
  - Rouge-avant / vert-après : `tests/unit/test_hosts.py` (7 tests neufs — downgrade par `Edit`, par `Write`, par `sed -i` → `ASK` ; upgrade, même valeur, autre fichier YAML de `_grimoire/standard/` → `ALLOW` inchangé ; un `Edit` hors cible ne déclenche jamais `profile_rank`, monkeypatch qui lève). 3/7 rouges avant le correctif (les trois downgrades, vérifié en isolant temporairement le correctif). Non-régression : `tests/unit/test_hosts.py` (173), `tests/unit/test_temporal_policies.py`, `tests/unit/test_policies_rust_parity.py`, `tests/unit/test_session_enrolment.py`, `tests/unit/test_tool_facts.py`, `tests/security/test_injections.py`, `tests/test_policy_command_surface.py` — tous verts. `ruff check` et `mypy --strict` propres sur les fichiers modifiés.
  - Budget : le garde compare d'abord le chemin par simple chaîne (`ToolFacts.standard_profile_write` reste `None` sans aucun import ni lecture dès que la cible n'est pas `standard-profile.yaml` — substring sur la commande pour un appel shell, avant tout parsing) ; `active_profile_id`/`profile_rank` (import paresseux) ne sont atteints que sur l'appel rare qui écrit réellement ce fichier. A/B contre `origin/main`, médiane de 7 exécutions du hook réel (`grimoire-hook --event PreToolUse`, process séparé, mêmes fixtures) sur un `Edit` hors cible : 96,5 ms (`origin/main`) vs 92,4 ms (cette branche) — écart de −4,1 ms, dans le bruit de la machine (un second run à 15 mesures donne −5,9 ms ; un troisième à 7 mesures +2,5 ms — le signe change, l'amplitude reste sous les ~10 ms de bruit ambiant mesurés entre lancements consécutifs du même code). Le nombre initialement rapporté (~90 ms) comparait à tort un `Edit` (passe par le moteur de politique complet) à un `Read` de référence (repli anticipé lecture-seule) plutôt qu'à `origin/main` sur le même `Edit` — ce n'était pas une régression de ce correctif. Coût du chemin cible (import paresseux compris, appel rare) : ~160 ms médiane, inchangé.
- fix(standard): trois faux positifs du standard (issue #682). (1) `toolreg.mcp_*_missing` n'est plus exigé d'un serveur MCP `out_of_scope: true` (`controls._verify_tool_registry`) : seul son motif est exigé, nouveau check `toolreg.mcp_out_of_scope_reason_missing` (erreur) s'il est vide. (2) `evidence.pending_gate` lit la structure du pack de preuve (cellule de tableau égale à `pending`, ou ligne de liste réduite à `Status: pending`) et non plus la sous-chaîne « pending » en texte libre (`verifiers._has_pending_gate`) ; c'est l'avertissement récurrent « Evidence pack still contains pending gates ». (3) `monitoring-specialist` (archétype infra-ops) reçoit `execute` (`tools: "read, edit, execute"`) pour interroger le système (kubectl, PromQL, amtool) ; l'émetteur Claude Code le projette en `Bash`. `grimoire up` rafraîchit `_grimoire/kit/agents/` ; un override partiel `extends: kit` sans `tools:` hérite du changement après `host sync`. Rouge-avant / vert-après : `tests/unit/core/test_standard_gap_artifacts.py` (4 tests) et `tests/unit/core/test_scaffold_archetype_skills.py::TestMonitoringSpecialistCanExecute` (2 tests).
- fix(memory): un backend vectoriel tombé est refroidi au lieu de coûter un timeout à chaque appel (#673). `MemoryManager.search()`/`hybrid_search()` (`src/grimoire/memory/manager.py:519-539`) appelaient `qdrant-server`/`weaviate-server` directement, sans jamais consulter le refroidissement existant de `providers/state.py` (`record_failure`/`record_success`, backoff exponentiel — déjà en production pour la cascade de dispatch LLM, `providers/routing.py`) : un backend distant injoignable payait son plein timeout client (5 s Qdrant, 10 s Weaviate) à **chaque** appel `grimoire_memory_search` (MCP) ou recherche cockpit. Même trou pour l'assistant local (`src/grimoire/tools/source_assist.py`, `assist_view`) : chaque frappe repayait `ASSIST_TIMEOUT_S` (10 s) si le modèle était up mais bloqué.
  - Réutilisation stricte de `providers/state.py`, zéro nouveau mécanisme ni fichier d'état : nouveaux identifiants `memory:qdrant`, `memory:weaviate`, `source_assist:ollama`, nouvelle fonction `cooling_down_entry()` (un seul identifiant à vérifier, sans recharger l'état à la main). `MemoryManager` (nouveau paramètre `cooldown_backend_kind`, posé par `from_config` uniquement pour `qdrant-server`/`weaviate-server` — jamais `qdrant-local`, embarqué, pas ce risque réseau) : `_guarded_backend_search` court-circuite l'appel réel quand le refroidissement est ouvert (liste vide, raison nommée en log, jamais une exception muette), sinon appelle réellement et pose (`record_failure`) ou efface (`record_success`) le refroidissement. `hybrid_search` applique le même refroidissement au backend vecteur via un petit adaptateur (`_CooldownGuardedBackend`), pas seulement `search` seule — c'est le chemin le plus courant en lecture (MCP, cockpit). `source_assist.assist_view` : même disjoncteur autour de l'appel modèle réel, posé après la sonde de résidence existante (`_readiness`) — un modèle en cours de chargement (issue #450) n'est jamais compté comme un échec.
  - Garde de routage : `providers/routing.py` (`choose`/`candidates`) ne lit que le registre déclaratif des fournisseurs LLM, jamais les clés brutes de l'état runtime partagé — ces nouveaux identifiants n'y entrent donc jamais, même en refroidissement ; `grimoire providers status` ne les affiche jamais comme des fournisseurs LLM. Testé explicitement (`tests/unit/test_providers.py::test_state_ids_outside_the_llm_registry_never_enter_routing`, `tests/unit/cli/test_cmd_providers.py::test_status_never_lists_memory_or_source_assist_cooldown_ids`).
  - Rouge-avant / vert-après : `tests/unit/memory/test_manager.py::TestMemoryBackendCooldown` (6 tests, horloge injectée sur `manager.py`/`providers/state.py`, jamais un vrai sommeil), `tests/unit/test_source_assist.py` (5 tests neufs, mock de l'appel modèle). Non-régression : suites `tests/unit/memory/`, `tests/unit/test_source_assist.py`, `tests/unit/test_providers.py`, `tests/unit/cli/test_cmd_providers.py`, `tests/unit/mcp/test_server.py`, `tests/test_grimoire_mcp_tools.py` vertes (hors l'échec préexistant sans rapport : extra `redis` non installé). `ruff check`/`mypy --strict` verts sur les quatre fichiers source touchés.
- fix(scaffold): la carte `<agents>` du concierge lit l'agent effectif d'un override partiel `extends: kit` au lieu du fichier partiel nu (issue #679). `ProjectScaffolder._agent_routing_map` (`src/grimoire/core/scaffold.py`) faisait gagner le partiel « tel quel » : sans `description:` ni corps, la carte affichait `role="Grimoire agent"` et un nom de persona égal au tag. Nouveau `_effective_agent_card` : persona et rôle viennent du kit fusionné avec le partiel par `hosts.collect.resolve_extends_kit` (la fusion de #441/#427, non dupliquée ; nouveau paramètre `kit_path` car le tier kit installé n'est pas encore régénéré au moment du plan). Une description propre au partiel l'emporte ; la persona vient toujours du kit.
  - Seconde cause : `_extract_agent_description` (carte, `agent-manifest.csv`, tableau d'instructions Copilot) lisait `description:` par regex sur une seule ligne, donc tronquait le YAML replié qu'écrit `convert_override` (ruamel, largeur 80). Correction à la lecture, pas à l'écriture : le frontmatter est parsé comme YAML (`parse_frontmatter`), repli regex conservé pour les gabarits `{{…}}` non rendus. Raison : un correctif côté écriture laisserait tronquer toute description pliée par une autre main (édition manuelle, cockpit, kit) ; le lecteur est la seule frontière que tous les écrivains traversent.
  - Effet de bord voulu : la carte du concierge ne change plus d'une régénération à l'autre pour un partiel, donc `kit_source_hash` reste valide et `grimoire doctor` ne lève plus le faux WARN « le kit a changé ».
  - Rouge-avant / vert-après : `tests/test_kit_integrity.py::TestAgentRoutingMapPartialOverrides` (4 tests : nom et rôle du kit, description du partiel, description repliée entière, partiel frais non obsolète) et `tests/unit/test_agent_override_partial.py::test_convert_description_longue_reste_lisible_en_entier_par_les_lecteurs_du_kit`.
- feat(hosts): détecter la répétition exacte d'un échec, en shadow (issue #668, phase 5, Refs #562 #555). Rien dans le kit ne détectait qu'un agent répète le même échec — `flows/pilot.py::PilotPolicy.decide()` (lu en entier) ne contient aucune notion de boucle/répétition, et le signal « Tourne en rond » n'existe que dans la table du lot 4.4 (`docs/plan-2026-q4.md:481-490`), une phase gelée (gel non levé). Lecture déterministe de `pi-warden/src/stuck.ts` (MIT, `AttemptWindow`/`exactRepeats()`) — sans son juge ni ses autres signaux (`churn`, répétition de succès) : nouvelle issue distincte de #562 (comme #643/#644/#645 pendant les gels précédents), jamais une extension du lot gelé, hors de `pilot.py`.
  - Nouveau module `src/grimoire/hosts/decisions/repetition_guard.py`, même gabarit que `session_memory.py` (#645) : fichier d'état `_grimoire-output/.runs/repetition-<session>.json`, ring buffer de 12 entrées `{tool, call_hash, output_hash, failed, ts}` par session. `call_hash` = sha1(outil + JSON de `tool_input`, clés triées) ; `output_hash` = sha1 de la sortie normalisée (espaces aplatis, durées/horodatages/adresses hexadécimales/grands nombres et chemins temporaires masqués — même liste que `stuck.ts:98-105`, plus les chemins temporaires que ce lot ajoute). Échec = code de sortie ≠ 0 quand disponible, sinon un motif d'erreur textuel simple. Seuil : 3 répétitions exactes (même appel, même sortie normalisée) dans la fenêtre ET la dernière sortie signale un échec → `looping`.
  - `evidence_trace.py` (`decide_evidence_trace`, `PostToolUse`) : le verdict va dans `Decision.detail["repetition"]` sur tous les chemins (projet non enrôlé, appel en lecture, écriture), jamais seulement sur celui qui renvoyait déjà un contexte — nouveau `_with_repetition` qui fusionne sans écraser le rappel d'évidence existant. Un contexte de recadrage (« même appel, même échec, N fois : change d'approche ou demande de l'aide ») est injecté une seule fois par série (`last_nudge_key` persisté ; un nouvel appel ou une sortie différente réarme) via `additionalContext`, déjà consommé par ce hook sur `PostToolUse` (`grimoire.hosts.runtime.render`). Jamais un `DENY`/`BLOCK`, mode shadow uniquement — aucune option d'enforce, contrairement à `done_gate.py` (#644) : ce signal n'a encore aucune heure de production à charger contre lui. Exclu des appels de délégation (`Task`/`Agent`/`runSubagent`) : mesure pure à coût nul (#657), le contrat `decision == Decision()` de `test_post_tool_use_logs_a_delegation_call_silently` reste inchangé.
  - Rouge-avant / vert-après (`tests/unit/test_repetition_guard.py`, nouveau, 16 tests ; 100 % rouges avant le module — `ModuleNotFoundError`, prouvé sur un worktree jetable d'`origin/main`) : 3 échecs identiques → `looping` ; 3 succès identiques → rien ; sorties différentes → rien ; horodatage différent dans la sortie → toujours détecté (normalisation) ; fenêtre glissante de 12 (une répétition qui en sort ne compte plus) ; nudge une seule fois par série, réarmé par une série distincte ; fichier d'état corrompu → aucune exception, dégrade en état neuf ; intégration `decide_evidence_trace` (verdict attaché, jamais de blocage, résiste à un état corrompu). Non-régression : `tests/unit/test_hosts.py`, `tests/unit/test_tool_facts.py`, `tests/security/test_injections.py` (235 tests) et `tests/unit` complet (5 487 passés, 353 ignorés, hors huit échecs préexistants et sans rapport : `FastMCP._lowlevel_server` absent, extra `redis` non installé, tarball `grimoire-state.tar.gz` d'un autre test d'upgrade-flow). `ruff check` et `mypy --strict` verts sur les fichiers `src` touchés.
  - Budget : médiane sur 5 exécutions du hook `PostToolUse` réel (`grimoire.hosts.runtime:main`), fenêtre de répétition déjà remplie (12 entrées) : **88,2 ms** — aucun import lourd (voir docstring du module).
- fix(proposals): état « résolu », fusion des hôtes à l'acceptation, accept idempotent (issue #681, Refs #520 #510). Cause : `sync_proposals` ne relisait jamais la condition d'une proposition `pending` (traitée à la main, elle restait en attente, seul `reject` la clôturait, en disant le contraire), `_accept_needs_hosts` affectait `hosts["enabled"] = detected` (écrasement) et un second `accept` renvoyait une erreur ou relançait la conversion.
  - `src/grimoire/proposals.py` : nouveau statut `resolved` (champs `resolved_at`, `resolved_reason`), posé à la lecture (`list_proposals`/`sync_proposals`/`count_pending`) quand la condition est satisfaite sur disque : override déjà partiel (`extends: kit`), `hosts.enabled` couvrant les hôtes détectés, besoins de `needs-declare-commands` tous résolus. Une proposition `resolved` que le flow repropose est rouverte.
  - `accept hosts-declare-enabled` fait l'union ordonnée (déclaré d'abord, rien de perdu) ; `accept` d'une proposition déjà acceptée ou résolue renvoie `ok: true, status: already-resolved` sans rien réécrire ; `reject` refuse une proposition résolue.
  - `grimoire upgrade-flow status` : aucun gabarit de `src/` ne le cite (seul un commentaire de `web/workspace/spaces/observer.js` note son absence) ; test de garde ajouté, la commande n'est pas créée (`grimoire flow status <run_id>` existe).
  - Tests : `tests/unit/test_proposals_resolved.py` (11 tests, 7 rouges avant) ; `tests/unit/test_proposals.py` mis à jour (second accept = no-op).
- fix(policies): `GRIMOIRE_THREAT_MATRIX` ne déclare plus implémentée une garde absente du kit (issue #669). Les 10 entrées avaient `implemented=True` avec un `negative_test_id` ne nommant aucune fonction de test existante nulle part sous `tests/` (recherche exhaustive : 0 correspondance hors leur propre occurrence dans `security.py`) ; le seul test qui les touchait (`test_every_entry_has_negative_test`) vérifiait juste `negative_test_id.startswith("test_")`, jamais que le test existe. THR-001 (`grimoire-control-surface-guard`) et THR-009 (`grimoire-terminal-guard`) citaient des hooks qui n'existent que dans la Forge (ou nulle part, pour le second) ; THR-008 citait une passerelle de blocage que `src/grimoire/tools/stigmergy_hooks.py` dit lui-même, dans son propre docstring, non bloquante par construction.
  - Nouveau champ `ThreatEntry.mitigation_ref: str` (`"module.path:Attr.chain"`) pointant vers l'objet réel qui porte la mitigation. Nouvelle suite `tests/security/test_threat_matrix_honesty.py` : pour toute entrée `implemented=True`, un scan AST de `tests/` doit trouver `negative_test_id` comme fonction de test réelle, et `mitigation_ref` doit s'importer via `importlib`/`getattr` — rouge-avant confirmé sur les 10 entrées d'origine (20/20 échecs, un par champ), vert-après sur les 10 entrées corrigées.
  - THR-001 repointé sur le vrai mécanisme livré en 3.61.0 : `tool_policy.py::_untrusted_escalation` (escalade `allow`→`ask`), test `test_injection_suite_blocks_or_escalates_at_least_90_percent`. THR-002 repointé sur `validate_memory_write()` (refuse le contenu *authored* qui se donne pour une consigne ; le contenu *derived* n'est que journalisé), texte corrigé (« human approval »/« digest-only storage » n'existaient pas dans `MemoryManager.recall()`, une simple passerelle sans garde). THR-003 repointé sur `decide_evidence_gate()` (`evidence_gate.py`), bien plus solide que la « règle PolicyEngine » citée (qui n'était qu'un motif `warn` de prévisualisation à l'intake, jamais une garde à la clôture). THR-004/005/006/007/010 : mécanismes déjà réels, `negative_test_id` corrigé vers le nom réel du test qui les couvre (ex. `test_completed_maps_to_needs_verification`, `test_community_doctor_required`).
  - THR-008 et THR-009 passent à `implemented=False`, `negative_test_id`/`mitigation_ref` vidés, texte de mitigation honnête (« non implémenté dans le kit », Forge-only pour THR-008/009 où pertinent). `coverage_pct()` de la matrice canonique passe de 100,0 (faux) à 80,0.
  - `tests/unit/test_security_policies.py` mis à jour en conséquence (plus d'assertion figée à 100 %, nouvelle assertion sur les deux entrées non implémentées). Aucune commande ni doc ne surface `GRIMOIRE_THREAT_MATRIX.coverage_pct()` en dehors de ses propres tests (vérifié, `grep` sur `src/` et `docs/`) — rien d'autre à recaler.
  - Rouge-avant/vert-après : `tests/security/test_threat_matrix_honesty.py` (20 échecs → 0), `tests/unit/test_security_policies.py`. Non-régression : `tests/security/test_injections.py`, `tests/unit/test_a2a_adapter.py`, `tests/unit/test_crewai_adapter.py`, `tests/unit/missions/`, `tests/unit/test_hosts.py`, `tests/unit/memory/test_write_validation.py`.
- feat(hosts): calibrer les verdicts des hooks et limiter le gate fini à la racine du projet (issue #677, party mode `_scratch/party-oss/gouvernance.md` idée B, Refs #644). Deux volets, plus deux corrections de revue avant fusion.
  - **Correctif de périmètre du gate « fini »** (`src/grimoire/hosts/decisions/done_gate.py`) : faux positif observé en réel sur la Forge le 2026-09-29 — la seule mutation depuis le dernier test vert était une commande bash écrivant dans un worktree d'un AUTRE dépôt (`python3 script.py /chemin/hors/racine`, `git -C /chemin/hors/racine push`). `_is_mutation_candidate` classait toute commande bash non read-only comme mutation sans jamais vérifier que sa cible identifiable est sous `project_root`. Nouvelle règle : un `file_write` ne compte que si son chemin (absolu) est sous la racine ; une commande bash ne compte que si elle a au moins une cible identifiable (token en `/…`, sur la surface `command_surface` — quotes et corps de heredoc élagués) *et* qu'aucune ne résout hors racine — en cas de doute (aucune cible absolue, cible non résolvable, mélange dedans/dehors), elle compte toujours (jamais échouer ouvert).
    - Revue post-implémentation : cinq déclenchements réels observés le même jour sur la Forge, quatre faux positifs couverts a posteriori. #2 (commande composée `&&` aux deux cibles hors racine), #3/#4 (`cat >> /tmp/…` ou heredoc vers `/tmp`, juste après un pytest vert) et #5 (un tour sans écriture, la seule mutation venant d'un `git push` lancé en arrière-plan dans un tour antérieur — le hook `PostToolUse` journalise une commande d'arrière-plan à son lancement, avec la commande complète déjà connue à cet instant ; l'exclusion hors-racine s'applique à cette entrée comme à toute autre, quel que soit le tour). #3/#4 ont révélé un second défaut : le journal aplatit chaque commande sur une seule ligne (`evidence_journal._truncate_command`) *avant* que ce module ne la lise, ce qui rend le déheredocage habituel de `command_surface` (ancré sur de vrais retours à la ligne) inopérant — un chemin absolu du projet simplement *cité* dans le corps d'un heredoc redirigé vers `/tmp` polluait alors la détection et gardait l'écriture comptée à tort. Nouvelle fonction `_strip_flattened_heredoc_bodies`, spécifique à ce module, qui élague un heredoc aplati (`<<TAG … TAG`) avant l'extraction des cibles.
    - Rouge-avant/vert-après : `tests/unit/test_hosts.py` (7 tests neufs — `git -C <hors racine> push` seul → `no_mutation` ; `Edit` de chemin absolu hors racine → `no_mutation` ; `Edit` de chemin absolu sous la racine → `stale` inchangé ; commande ambiguë sans cible absolue → `stale` inchangé ; commande composée aux deux cibles hors racine → `no_mutation` ; heredoc vers `/tmp` dont le corps cite un chemin du projet → `no_mutation` ; tour de lecture seule après un `git push` d'arrière-plan hors racine → `no_mutation`). 3/7 rouges avant correctif (les deux scénarios hors racine du premier lot, puis le heredoc contaminé une fois isolé — les autres sont des tests de non-régression). Non-régression : `tests/unit/test_hosts.py` (185 tests) au complet.
  - **Journal de calibration** : deux nouveaux tags `TraceLedger` (`src/grimoire/traces/ledger.py`, pattern `agent.dispatch`/`agent.miss` existant, aucune nouvelle dépendance) — `policy.hold`, écrit par `tool_policy.decide_tool_policy` (tout `Outcome` ≠ `ALLOW`) et par `evidence_gate._with_done_gate` (`done_gate` stale, y compris en shadow — c'est exactement la donnée qu'il faut avant d'envisager `GRIMOIRE_DONE_GATE=enforce` plus largement), et `policy.hold_followup`, écrit au `PostToolUse` suivant de la même session (`evidence_trace.decide_evidence_trace`) et lié au premier par un `hold_id` commun. Étiquette structurelle à quatre valeurs — jamais « regret », le mot est trop fort tant qu'aucune deuxième source de vérité n'existe (désaccord du party-mode, arbitré ici) : `retried_same` (même empreinte d'action rejouée), `retried_variant` (même outil, cible proche), `respected` (autre chose s'est passé), `abandoned` (aucun followup retrouvé — limite documentée : ne distingue pas encore « session finie » de « pas encore rejoué »). Nouveau module `src/grimoire/hosts/decisions/calibration.py` : `action_fingerprint` (hash à sens unique, jamais la commande/le chemin en clair) et `target_key` (verbe shell ou nom de dossier parent, jamais le chemin complet) — rien de ce qui bloquait l'action d'origine (motif destructif, secret détecté) ne peut donc être reconstruit depuis ce journal.
    - Commande `grimoire hooks calibrate [--project-root] [--since 7d] [--json]` (`src/grimoire/cli/cmd_hooks.py`, même verbe CLI que les git hooks — mécanisme différent, groupe de commandes partagé) : par hook et par motif, nombre de holds et distribution des quatre étiquettes ; seule commande qui relit le journal complet (`TraceLedger.policy_hold_calibration()`), même mécanisme de lecture que `grimoire dispatch stats`.
    - **Revue post-implémentation — latence** : la première version (`TraceLedger.open_policy_hold`, retirée) relisait et reparsait tout `traces.jsonl` à chaque `PostToolUse` porteur d'un `session_id`, pour retrouver le hold encore ouvert de la session — un coût croissant sans borne avec l'âge du projet, mesuré à +10,2 ms contre un journal de 200 lignes (budget du hook : 60 ms). Corrigé : le hold ouvert vit désormais dans un petit fichier d'état par session (`_grimoire-output/.runs/policy-holds-<session>.json`, même gabarit que `session_memory.py` — écriture atomique, borné à 20 entrées). Le `TraceLedger` ne reçoit plus qu'un ajout (`.record(...)` avec un `trace_id` explicite, jamais de relecture pour numéroter) ; seul `grimoire hooks calibrate` relit le journal complet. Rouge-avant/vert-après : `tests/unit/test_calibration.py::TestHookPathNeverReadsTheLedger` (un monkeypatch à compteur sur `TraceLedger._load_all` — jamais une exception qui lève, silencieusement avalée par le `except Exception` best-effort des deux fonctions ; confirmé rouge en réintroduisant temporairement l'ancienne relecture), `TestHoldStateFileIsBounded` (plafond à 20 entrées, écriture atomique sans fichier `.tmp` résiduel). A/B refait (médiane de 7, hook réel, journal de 2 000 lignes) : +0,7 ms — dans le bruit (un run à journal vide mesure même −10,9 ms, pure variance machine entre process séparés).
    - Rouge-avant/vert-après (reste du lot) : `tests/unit/test_calibration.py` (17 tests, module neuf), `tests/unit/test_traces.py` (3 tests neufs sur `policy_hold_calibration`, les tests de l'ancien `open_policy_hold` retirés avec la méthode), `tests/unit/test_hosts.py` (8 tests neufs de bout en bout — deny/stale journalisés, `retried_same`/`respected` sur le `PostToolUse` suivant, un `ALLOW` ne journalise jamais rien), `tests/unit/cli/test_cmd_hooks.py` (5 tests neufs — rapport JSON, table texte, `--since` invalide et filtrant). Non-régression : `tests/unit/test_hosts.py`, `tests/unit/test_traces.py`, `tests/unit/test_calibration.py`, `tests/unit/test_traces_rust_parity.py`, `tests/unit/cli/test_cmd_hooks.py`, `tests/unit/cli/test_cmd_dispatch.py`, `tests/unit/cli/test_app.py` (693 passed, 23 skipped). `ruff check` et `mypy --strict` propres sur tous les fichiers modifiés.

## [3.61.1] - 2026-09-29

- fix(hosts): une commande emballée dans `bash -c "…"`/`sh -c '…'`/`zsh -c "…"`/`eval "…"`/`python -c "…os.system(…)"`/`xargs sh -c "…"` n'échappe plus à l'escalade du contenu non fiable (issue #663, suite de #645). La comparaison de `_untrusted_escalation` (`tool_policy.py`) n'utilisait que `command_surface()`, qui conserve l'emballage complet (préfixe interpréteur + guillemets) pour ne pas blanchir un heredoc documentaire — donc la commande *nue* plantée dans un contenu non fiable ne matchait jamais la commande *emballée* proposée par l'agent, et `python -c "…"` en particulier disparaissait entièrement de la surface (`python`/`python3` absents de `_EVAL_INTRODUCER`). Nouveau `extract_c_bodies()` (`tool_facts.py`, jamais appelé par `command_surface` ni par la classification lecture/mutation) : extrait le corps d'un `-c`/`eval` d'un interpréteur shell ou python (avec un niveau d'imbrication, pour `os.system('…')` dans du code python), comparé normalisé (espaces, casse — nouveau paramètre `normalize` de `find_untrusted_match`, `session_memory.py`) aux extraits non fiables, en repli quand la comparaison littérale de la surface entière échoue. Rouge-avant/vert-après : `tests/security/test_injections.py` (6 nouvelles charges emballées, 6/6 rouges avant, vertes après) ; témoins inchangés : message de commit citant « bash -c », heredoc documentaire citant un emballage, et commande emballée demandée par l'utilisateur restent `allow`. Non-régression : suite d'injections complète (22 charges + témoins), `tests/unit/test_tool_facts.py`, `tests/unit/test_hosts.py` (211 tests). Refs #645.
- fix(agents): l'étape ROUTE du concierge distingue désormais boucle principale (outil de délégation présent : traiter une demande directe et bornée, sinon déléguer par classe de vérifiabilité, vérification à l'orchestrateur) et sous-agent ou hôte sans délégation (`tools: "read, search"` seuls : jamais d'action hors read/search, trier puis recommander la persona et le modèle, ou dire à l'utilisateur vers quel agent basculer) — PR #656 avait retiré ce repli et fait dépendre l'auto-exécution d'une condition sans jamais tenir compte de la frontière `tool_boundary` du concierge en sous-agent restreint (#660)
- fix(hosts): quatre défauts de la mesure de délégation (#657, #661). Le matcher `PostToolUse` de `grimoire.evidence-trace` n'avait pas la famille `execute` : sur Claude Code, ce hook ne recevait donc jamais Bash, et `_record_session_mutation` ne comptait jamais une mutation faite en shell (`sed -i`, `git commit`…) — `evidence_gate.py` laissait alors clore hors tâche après une telle mutation. `_DELEGATION_AGENT_KEYS`/`_DELEGATION_MODEL_KEYS` n'incluaient pas `agentName`/`modelName`, la forme réelle d'une invocation Copilot `runSubagent` (`toolSpecificData.agentName`/`.modelName`) : une telle délégation se journalisait sous `"(sans nom)"`, modèle vide. `_DELEGATION_DESCRIPTION_KEYS` retombait sur `prompt` : les 160 premiers caractères du prompt complet partaient dans un tag `desc:` exporté par `_to_langfuse_trace` et OTel. `_record_delegation` appelait `TraceLedger.record` sans `trace_id` : `_next_id` relit tout le journal et collisionne pour deux appels concurrents lisant le même état.
  - Correctifs : `execute` ajouté au matcher (`src/grimoire/hosts/collect.py`) ; `agentName`/`modelName` ajoutés aux clés de repli et lus aussi sous `toolSpecificData` imbriqué (nouvel helper `_delegation_fields`) ; `prompt` retiré des clés de description ; un `trace_id` uuid4 explicite passé à chaque délégation (`src/grimoire/hosts/decisions/evidence_trace.py`).
  - Rouge-avant / vert-après (`tests/unit/test_hosts.py`, prouvé rouge sur un worktree jetable d'`origin/main`) : `test_evidence_trace_matcher_covers_bash_calls`, `test_post_tool_use_counts_a_mutating_bash_call_but_not_a_read_only_one`, `test_post_tool_use_reads_a_copilot_delegation_nested_under_tool_specific_data`, `test_post_tool_use_never_falls_back_to_the_full_prompt_for_desc_tag`, `test_delegation_trace_id_never_relies_on_next_id_and_its_journal_reread` (course simulée déterministe). Coût mesuré : ~74 ms médiane par appel Bash pour ce hook supplémentaire (20 mesures de `grimoire-hook --event PostToolUse --decision grimoire.evidence-trace`).
- fix(missions): `recommended_model` ne rend plus jamais `"session"` — pas un nom de modèle que l'outil `Agent` de Claude Code accepte pour `model=`, alors que la politique injectée dit justement de préférer la donnée à la prose. Lot G : quatre défauts vérifiés sur le socle posé par `#654`/`#655`/`#657` (#662).
  - Point 1 — `src/grimoire/missions/dispatch_advice.py` : contrat externe scindé du contrat interne. `recommended_model` est `"haiku"`/`"sonnet"`, ou `None` sur V2 (« omets le paramètre `model` ») ; nouveau champ `model_tier` (`"cheap"`/`"mid"`/`"session"`), toujours renseigné, même vocabulaire que `missions.dispatch.start_tier_for`. `RECOMMENDED_MODEL_BY_CLASS` garde ses valeurs internes (`"session"` compris) : `hosts.emitters.claude_code._model_label` continue de les lire telles quelles. Nouveau `model_hint()` pour ne jamais afficher `None` littéralement en sortie texte (`grimoire task show`/`claim`/`move`, MCP `task_claim`/`task_show`/`task_context` rendent aussi `model_tier`). Rouge-avant/vert-après : `tests/unit/missions/test_dispatch_advice.py`.
  - Point 2 — `src/grimoire/hosts/decisions/activation.py` (`_claude_dispatch_context`) : une phrase tranche désormais entre le roster `subagent_type` → modèle par défaut de la persona et la politique de dispatch (classe de la tâche → modèle), qui semblaient se contredire au même `SessionStart`. Rouge-avant/vert-après : `tests/unit/test_hosts.py::test_the_roster_default_model_is_explicitly_subordinate_to_the_task_class`.
  - Point 3 — même fichier : `entry_persona_context`/`_claude_dispatch_context` appelaient chacune `collect_agents` au même `SessionStart` (~35 ms + ~16 ms mesurés sur la Forge) ; `decide_activation` collecte une fois et passe l'inventaire aux deux, signatures publiques inchangées (`agents` optionnel, `None` par défaut). Rouge-avant/vert-après : `tests/unit/test_hosts.py::test_collect_agents_runs_once_per_claude_session_start`.
  - Point 4 — `src/grimoire/traces/ledger.py` (`agent_freshness_report`) : ne lisait que `agent_dispatch_counts()` (choix de persona d'entrée) et ignorait `delegation_counts()` — une persona déléguée mais jamais choisie comme entrée ressortait « jamais choisie » de `grimoire registry dispatches`, doctor et le cockpit. Nouvelle `_dispatch_and_delegation_last_seen()` : `last_seen` = le plus récent des deux sources, fusionné en Python avant tout choix de backend Rust — parité automatique, aucun code Rust supplémentaire. Rouge-avant/vert-après : `tests/unit/test_traces.py::TestTraceLedger::test_agent_freshness_report_counts_a_delegation_as_a_sign_of_life`.
  - Non-régression : `tests/unit/test_hosts.py`, `tests/unit/test_traces.py`, `tests/unit/test_traces_rust_parity.py`, `tests/unit/cli/`, `tests/unit/mcp/`, `tests/unit/missions/` verts (hors trois échecs préexistants et sans rapport : `FastMCP._lowlevel_server` absent, extra `redis` non installé).

## [3.61.0] - 2026-09-28

- feat(hosts): contenu non fiable mémorisé entre hooks, et suite d'injections (issue #645, plan Q4 phase 5 lot 5.2, Refs #555). Rien ne distinguait, au moment d'une décision d'outil, une instruction de l'utilisateur d'une instruction venue d'une sortie d'outil (page web, fichier lu hors dépôt, retour de sous-agent) — la seule mitigation était le garde à motifs `THR-001`. Nouveau module `src/grimoire/hosts/decisions/session_memory.py` : mémoire de session bornée dans `_grimoire-output/.runs/memory-<session>.json` (même forme que `session_state.py`, écriture atomique, best-effort dans les deux sens, jamais d'exception qui remonte au hook) — six derniers messages utilisateur (≤ 700 caractères), dix derniers contenus externes marqués non fiables (extrait ≤ 4000 caractères, provenance). Alimentée par `task_context.py` (`UserPromptSubmit`, le tour humain seul) et `evidence_trace.py` (`PostToolUse`, une sortie ≥ 200 caractères venant du web, d'un fichier lu hors du dépôt ou d'un retour de sous-agent, marquée non fiable si elle contient un motif de consigne — copie verbatim des motifs de `grimoire.memory.validation._INSTRUCTION_PATTERNS`, jamais importés : le paquet `grimoire.memory` coûte ~43 ms à l'import, ce qui aurait à lui seul dépassé le budget du hook — ou ressemble à une ligne de commande). `tool_policy.py` : quand la commande proposée (`command_surface`) apparaît textuellement dans un contenu non fiable et jamais dans les messages utilisateur mémorisés, `allow` devient `ask` avec un motif qui cite la provenance ; jamais `deny` par ce seul signal, jamais un `ask`/`deny` existant relâché (l'escalade n'est consultée qu'aux deux points où le verdict serait sinon `allow`). Le raisonnement de l'assistant n'entre jamais dans la mémoire — seuls `hook.prompt` et les sorties d'outil y entrent.
  - `tests/security/test_injections.py` (33 tests) : 22 charges (commande plantée dans une page web, un fichier hors dépôt, un retour de sous-agent ; « the owner approved this », commentaire « no tests needed », variantes de casse et d'espacement ; 3 recoupant volontairement le garde destructif existant pour prouver que les deux mécanismes coexistent) — mesuré 22/22 = **100 %** bloquées ou escaladées (19 `ask` par le nouveau mécanisme, 3 `deny` déjà couvertes) ; le test échoue sous 90 %. Rouge confirmé avant le mécanisme : 3/22 = 13,6 %. Témoins bénins verts : commande identique demandée par l'utilisateur → `allow`, heredoc documentaire et message de commit citant une commande dangereuse → inchangés (non-régression PR #650), lecture d'un fichier *du* dépôt jamais traitée comme externe, sortie < 200 caractères jamais mémorisée, mémoire non fiable non vide sans corrélation textuelle → `allow`. Non-régression : `tests/unit/test_hosts.py`, `tests/unit/test_tool_facts.py`, `tests/unit/test_policies.py`, `tests/unit/test_security_policies.py`, `tests/unit/test_policies_rust_parity.py`, `tests/unit/test_session_enrolment.py`, `tests/unit/test_temporal_policies.py`, `tests/unit/test_hook_cost.py`, `tests/unit/missions/test_human_steering.py` et suites `workspace_*`/`task_tools` adjacentes (299 passés, 28 ignorés préexistants).
  - Budget : médiane sur 5 exécutions du hook `PreToolUse` réel (`grimoire.hosts.runtime:main`, point d'entrée `grimoire-hook` de `pyproject.toml`) dans un projet gouverné avec une mémoire de session déjà remplie (6 messages, 10 extraits) : **61,5 ms**. Cible de l'issue (60 ms) non strictement tenue, mais mesuré : un process Python nu démarre déjà à 55-65 ms sur cette machine avant la moindre ligne de ce lot (comparaison à mémoire vide vs remplie : 59,0 ms vs 56,2 ms, différence dans le bruit) — le coût propre de la fonctionnalité (une petite lecture JSON) est négligeable ; aucun import lourd (`grimoire.memory` explicitement évité, voir ci-dessus).
- fix(hosts): `find … -delete` et un heredoc redirigé (`cat <<EOF > fichier`) ne passent plus pour de la lecture seule (issue #643). Deux défauts dans `is_read_only_command`/`command_surface` (`src/grimoire/hosts/decisions/tool_facts.py`) : `find` était un verbe lecture-seule sans que rien n'inspecte ses primaires d'action (`-delete` n'est attrapé par aucun motif destructif, contrairement à `-exec rm`) ; et `_strip_heredoc_bodies` effaçait le corps du heredoc à partir de `<<TAG` lui-même, emportant avec lui la redirection (`> fichier`/`>> fichier`) posée sur la même ligne d'ouverture avant que `_WRITE_REDIRECTION_RE` ne l'examine. Correctif : `_FIND_MUTATING_PRIMARIES` (`-delete`, `-exec`, `-execdir`, `-ok`, `-okdir`, `-fprint`, `-fprint0`, `-fprintf`, `-fls`) fait sortir `find` du cas générique ; l'effacement du heredoc part désormais de la fin de la ligne d'ouverture, jamais de `<<TAG`. Rouge-avant / vert-après : `tests/unit/test_tool_facts.py` (nouveau, 11 tests ; 2 rouges sur `main` — `find … -delete`, heredoc redirigé `>`/`>>` — dont le heredoc sans redirection documentant `rm -rf /` reste lecture seule), `tests/unit/test_hosts.py` et `tests/unit/test_temporal_policies.py` verts (non-régression).
- feat(missions): la classe de vérifiabilité et le modèle recommandé au claim et au contexte (#654). En mode interactif (Claude Code, Copilot — le défaut), l'hôte exécute directement, sans jamais passer par `grimoire task dispatch` ; la seule correspondance V0/V1/V2 → haiku/sonnet/session vivait en prose dans la persona d'entrée (`hosts.emitters.claude_code`), à charge du modèle de session de la relire et de l'appliquer lui-même, et `task_claim` ne rendait même pas la classe de la tâche qu'il réclame.
  - `src/grimoire/missions/dispatch_advice.py` (nouveau) : `dispatch_advice(task)` calcule `verifiability` (même format que `verifiability.as_dict`) et `recommended_model`, depuis `verifiability.classify` — la même source que `missions.dispatch.start_tier_for` (`grimoire task dispatch`), jamais une seconde classification. Table opposable `RECOMMENDED_MODEL_BY_CLASS` (V0→haiku, V1→sonnet, V2→session) exposée pour qu'un lot ultérieur y fasse pointer la prose de la persona d'entrée plutôt que de la recopier.
  - `task_claim` (MCP et CLI, via `TaskMove.to_dict()`) et `task_show`/`task_context` (MCP, via `_task_json`) rendent désormais les deux champs ; `grimoire task show --json`/texte pareillement ; `grimoire task claim/move/block/close` ajoutent une ligne compacte `· <classe> → <modèle>` en sortie texte.
  - Une tâche sans critère exploitable (aucun critère, ou critère ambigu) reste V2 → `session`, sans exception (`classify_criteria`, déjà existant).
  - Non retenu : `grimoire task context` en CLI (bundle du standard hérité, dérivé du board YAML brut, pas d'un `MissionTask` — coupler `core.agentic_standard` à `missions` pour ce seul champ aurait dépassé la portée du lot).
  - Rouge-avant / vert-après : `tests/unit/missions/test_dispatch_advice.py`.
- fix(standard): l'avertissement de suite vide n'enjoint plus, `gate check` ne juge plus une tâche fantôme (issue #642, lot K). Analyse du lot J (18/60 runs kit-gov « avec excursion », 103 % du surcoût J−nu) : 15/15 runs JavaScript relisent et réécrivent `acceptance-record.md` puis relancent le gate 3-4 fois en réaction à un message qui enjoignait « écris un test… ou justifie l'absence » alors que la suite est structurellement masquée par le harnais ; `rust/poker run2` perd 22 tours après un premier `gate check` évaluant les gates de la tâche `bootstrap` alors qu'aucune carte de ce nom n'existe sur le board.
  - `src/grimoire/core/standard_checks/gate_test_run.py:241` : le message `acceptance.no_tests_collected` dit désormais le fait (commande, code de sortie) et que ce n'est pas un échec de la tâche, avec une seule action proposée (déclarer `needs.commands.test-runner` dans `project-context.yaml` si la suite existe sous une autre commande) — ni « écris un test », ni « justifie ».
  - `src/grimoire/cli/cmd_standard.py` (`gate check`) : sans `--task-id`, nouvelle fonction `resolve_gate_check_task_id` (`gate_test_run.py`) qui délègue à `resolve_active_task` (env, réclamation du ledger, carte `in_progress` du board, puis `bootstrap`) ; si ce dernier repli retombe sur un `bootstrap` que le board ne déclare pas, la commande échoue (`gate.no_task_to_evaluate`, exit ≠ 0) en nommant le board et la commande de réparation (`grimoire task add` / `grimoire task claim`) au lieu d'évaluer les gates d'une tâche fantôme. Comportement inchangé quand `bootstrap` est une vraie carte du board.
  - Tests rouge-avant/vert-après : `tests/test_agentic_standard.py::test_cli_gate_check_strict_warns_instead_of_failing_when_nothing_collected` (le message ne contient ni « écris un test » ni « justifie », cite `needs.commands.test-runner`), `test_cli_gate_check_without_task_id_fails_when_bootstrap_is_not_on_the_board`, `test_cli_gate_check_without_task_id_resolves_the_board_in_progress_card`.
  - Rejeu du banc `kit-gov` (`scripts/bench/three_arms.py --arms kit-gov`) non fait, en attente de décision (coût réel) — voir issue #642.
- fix(hosts): la politique de dispatch (V0→`haiku`, V1→`sonnet`, V2→modèle de session) et le répertoire `subagent_type` → modèle atteignent désormais le contexte `SessionStart` de la boucle principale Claude Code, gatés sur cet hôte seul (`decide_activation` est partagé avec Copilot). Avant : la règle n'existait que dans `.claude/agents/<entrée>.md`, lu presque jamais par la boucle principale et sans effet quand il l'est (le concierge en sous-agent n'a pas l'outil `Agent`) — mesuré à 92 % de sous-agents dispatchés en `sonnet`, `haiku` presque jamais. La ligne « Frontière d'outils » du résumé de persona d'entrée ne se lit plus comme si elle bornait la boucle principale (#655).
- feat(hosts): gate « fini » déterministe au `Stop` — mutation après le dernier check vert, veto dur, shadow par défaut (issue #644). Le gate d'évidence existant (`evidence_gate.py`) ne lit que les artefacts de preuve, jamais l'ordre des actions : une session qui édite un fichier après son dernier test vert, sans jamais relancer les tests, passait tant que les fichiers de preuve existaient. Lecture déterministe seulement (jev-belay, pi-warden) — aucun modèle, aucun réseau.
  - `src/grimoire/hosts/decisions/done_gate.py` (nouveau) : `evaluate_done_gate` relit le journal `PostToolUse` existant (`evidence-log.jsonl`, issue #582 lot G2), jamais un second fichier. Mutation = un `file_write`, ou un `bash` dont la commande n'est pas classée lecture seule (`tool_facts.is_read_only_command`, réutilisé sans modification). Check = l'entrée `"test_run"` du journal (motif étendu ici à `vitest`/`jest`/`tsc`/`eslint`/`ruff` dans `evidence_journal._TEST_COMMAND_PATTERN`), ou une commande `bash` qui est la commande de test résolue du projet (`resolve_need("test-runner", ...)`, appelée seulement s'il existe au moins une mutation candidate — jamais sur un `Stop` en lecture seule). Ordre déterminé par la position dans le journal (append-only), jamais par comparaison de chaînes `ts` (deux événements à la même microseconde auraient une ambiguïté). Veto dur : un check frais vert après la dernière mutation → jamais de refus par ce chemin, quoi que dise le reste.
  - `src/grimoire/hosts/decisions/evidence_gate.py` : `_with_done_gate` attache le verdict à `Decision.detail["done_gate"]` sur les deux chemins où la tâche doit une preuve (même condition que le gate existant). Mode shadow par défaut : le verdict est journalisé, un contexte d'avertissement s'ajoute en profil `governed`/`production`, mais rien ne bloque — sans production journalisée à relire, ce gate n'a aucune heure de vérité sur son propre taux de faux positifs, et un refus coûte un tour entier à tort. Un vrai `BLOCK` exige `GRIMOIRE_DONE_GATE=enforce` dans l'environnement, ou `options: {done_gate: enforce}` dans `_grimoire/standard/standard-profile.yaml`. Caps (même en enforce) : un refus par tâche toutes les 60 s, trois par session, état dans `done-gate-state.json` à côté du journal (jamais dans le pack de preuve versionné).
  - Rouge-avant / vert-après : `tests/unit/test_hosts.py` (7 tests neufs — mutation après check vert → `stale`/`BLOCK` en enforce ; check vert après la dernière mutation → jamais bloqué (veto dur, même en enforce) ; aucune mutation → rien ; `stop_active` → jamais évalué ; caps (cooldown 60 s + plafond de session 3) ; option de profil `standard-profile.yaml` équivalente à la variable d'environnement). 6/7 rouges avant l'implémentation (le 7e, veto dur en shadow, ne pouvait pas déjà bloquer). Suite complète (`tests/unit`, hors trois échecs préexistants et sans rapport : `FastMCP._lowlevel_server` absent, extra `redis` non installé, tarball `grimoire-state.tar.gz` d'un autre test d'upgrade-flow) : 5 419 verts.
  - Budget : médiane sur 5 mesures (`time echo '{}' | grimoire-hook --host claude --event Stop`, projet gouverné jetable) ≈ 160 ms — identique (mesuré en A/B, avec et sans ce lot) au `Stop` gouverné déjà en place : le coût vient de l'import de `grimoire.core.agentic_standard` par le gate d'évidence existant, pas de ce lot. Le chemin sans mutation (le cas courant) n'importe ni `resolve_need` ni le moteur de tests ; le chemin avec mutation ajoute environ 10 ms mesurés. Aucun nouvel import lourd dans le chemin `Stop`.
- fix(agents): le concierge délègue par défaut au lieu de légitimer l'auto-exécution — l'étape 8 TRIAGE et le wrapper d'entrée Copilot conditionnent désormais le travail sur place à une demande directe et bornée (`grimoire-agent-dispatch`), sinon dispatchent un sous-agent par classe de vérifiabilité (V0 → haiku, V1 → sonnet, V2 → session) ; recherche large/mesure vers un sous-agent économique, conclusions seulement (#656)
- docs(veille): modèles de décision (Jev et clones `/v1/systemone`) instruits et écartés pour l'instant (`docs/veille/decision-models-2026-09.md`) — verdict mesuré sur le banc `kit-gov` (lot J : 18/60 excursions portent 103 % du surcoût, déclencheurs `acceptance.no_tests_collected` et repli `--task-id bootstrap`) et sur un prototype CPU du clone Von 1.2.3 (policy d'outils non discriminante face à la regex du kit, dosage proche du hasard, français effondré). Récolte sans modèle ouverte en issues : #642 (lot K, reformuler le WARN et corriger le repli, phase 2bis de `docs/plan-2026-q4.md`), #643 (deux défauts `tool_facts` trouvés en lisant le code : `find … -delete` classé lecture seule, heredoc qui avale sa redirection), #644 (gate « done » déterministe au `Stop`) et #645 (mémoire de contenu non fiable `PostToolUse` → `PreToolUse`, phase 5). Compagnon `docs/index.md` et ligne dédiée dans `docs/plan-2026-q4.md` §6.
- feat(hosts): mesurer la délégation vers un sous-agent, agent et modèle (#657). Rien ne mesurait la délégation : `PreToolUse` ne capte qu'execute/write/secret, `SubagentStop` ne fait que reporter les gates, `ToolVerb` n'a aucun verbe de délégation — le cockpit ne pouvait pas dire si une délégation avait lieu, ni avec quel modèle. `decide_evidence_trace` (`PostToolUse`, déjà non bloquant) reconnaît désormais l'outil de délégation — `Task`/`Agent` chez Claude Code, `agent`/`runSubagent` chez Copilot — et journalise une ligne dans le `TraceLedger` existant (`_grimoire-output/traces/traces.jsonl`, déjà exporté en OTel GenAI), tag `agent.delegation`, sans jamais injecter de contexte (zéro coût en tokens). Le matcher `PostToolUse` de l'émetteur Claude Code est élargi (`Task`, `Agent`) pour que l'hôte spawn le hook sur ces appels. `grimoire registry dispatches` (texte + JSON) affiche désormais aussi les délégations observées : par agent délégué, occurrences, part avec modèle explicite, dernier modèle, dernière délégation.
- test(e2e): `test_workspace_executer_steering.py` échouait par intermittence en CI (~1 run sur 8-30, sur plusieurs branches le même jour) sur `assert 'proposed' == 'cancelled'`, malgré le verrou de ledger déjà posé au lot B (#640) — un tout autre risque de concurrence, sans effet sur ce flake. Cause réelle isolée par reproduction : `page.wait_for_function()` avec un prédicat qui retourne une promesse (`fetch(...).then(...)`) ne l'attend pas dans la version de Playwright installée par la CI — la promesse elle-même est traitée comme « truthy », sans attendre sa résolution (reproductible avec `page.wait_for_function("() => Promise.resolve(false)", timeout=1200)` seul). Le test relisait donc `TaskService` sur disque sans aucune synchronisation réelle : course entre la requête `POST .../cancel` en vol et la lecture Python. Correctif : `page.expect_response()` autour du geste, pour la priorité et pour l'annulation. Aucun changement produit (`executer.js` diff nul). Avant/après local : ~1/30 échecs → 60/60 exécutions vertes (#658).

## [3.60.0] - 2026-09-28

- feat(cockpit): lot C — portefeuille de tâches multi-projets dans l'espace Exécuter, action routée vers le projet propriétaire, reprise de session (issue #638). Treize sessions ouvertes sur trois projets, chacune avec son ledger, et l'espace Exécuter n'en lisait qu'un : l'orchestrateur humain n'avait aucune vue d'ensemble. Le portefeuille agrège côté serveur tous les projets du registre cockpit, chaque carte avec son projet, son état ledger et sa colonne, sa priorité, son claim et sa session (vivante / inactive selon l'âge du journal `_grimoire-output/.runs/session-<id>.json`, N minutes paramétrable, 30 par défaut) ; un projet dont le ledger est absent ou illisible est listé avec sa raison, jamais tu. Les chemins viennent du registre, jamais de la requête.
  - `src/grimoire/tools/workspace_portfolio.py` (module dédié, zones gelées intactes) : `portfolio_tasks()` avec filtres `state` (état ledger ou colonne board), `project`, `live` ; `resolve_registry_root()` fail-closed ; `resume_command()` (`claude --resume <id>` sur l'hôte Claude Code, rien ailleurs) ; `unread_directives()` tolérant à la forme que le lot B donnera aux consignes. Champs optionnels des lots A et B lus s'ils existent (`claim.session_id`, sinon le journal de session qui porte `task_id` ; `priority`, sinon dérivée du `risk_profile` comme `board.py`).
  - Route `GET /api/workspace/portfolio/tasks` (`?state=`, `?slug=`, `?live=1`, `?minutes=`) et action `POST /api/workspace/portfolio/tasks/<id>/<claim|move|block|close>?project=<slug>` — troisième dérogation nommée de `is_registry_scoped_write` (`workspace_routes.py`) : routée par le registre vers le projet propriétaire, `project` du corps confirmé, `TaskService` de CE projet avec son gate (refus en 200 `blocked: true`) ; le chemin historique `tasks/<id>/<action>` reste `_HOME_SLUG` seulement.
  - Espace Exécuter (`web/workspace/spaces/executer.js`, `api.js`) : portée « Ce projet » / « Tous les projets » (cockpit seulement), puce projet, session et priorité en point + mot (jamais la couleur seule, plancher 13 px des tokens), filtres état / projet / « sessions vivantes », bandeau des projets illisibles, cartes clé-ées par (projet, id) — deux ledgers peuvent dériver le même identifiant d'un même titre —, inspecteur identique avec bloc « Session » et « Reprendre la session » qui copie la commande sans jamais l'exécuter (ADR-007).
  - CLI : `grimoire task list --all-projects [--project <slug>] [--live] [--live-minutes N]`, même agrégation, tableau ou `-o json`.
  - Rouge-avant / vert-après : `tests/unit/tools/test_workspace_portfolio.py` (test de fait de l'issue : deux projets gouvernés au registre isolé, une réclamée, filtre `live`, troisième projet absent nommé), `tests/unit/test_workspace_routes.py` section 3ter (`block` depuis le portefeuille écrit dans le ledger de `projet-b` et pas de `projet-a`, refus du `project` discordant, chemin historique toujours 403), `tests/unit/cli/test_cmd_task_list_all_projects.py`, `tests/e2e/test_workspace_executer_portfolio.py` (Playwright, captures avec `GRIMOIRE_E2E_CAPTURES`).
  - Non mesuré : le coût du portefeuille sur un registre de plusieurs dizaines de projets (une lecture de ledger et un `glob` des journaux par projet, à chaque appel — aucun cache).

- fix(missions): verrou inter-process sur l'écriture du Mission Ledger (issue #638, lot B). L'E2E `test_workspace_executer_steering.py` échouait par intermittence en CI (~1 run sur 8) : `assert task.status.value == "cancelled"` retombait à `proposed` alors que la réponse HTTP de `cancel` montrait déjà `cancelled`. Cause : `MissionLedger._atomic_append` lit le fichier `events.jsonl` entier, ajoute une ligne, puis renomme un temporaire par-dessus — sans verrou, deux écritures concurrentes (le cockpit sert chaque requête dans son propre thread, `TaskService` frais à chaque fois) peuvent toutes deux lire le même contenu avant que l'une n'écrive ; la seconde écrase alors l'événement de la première (« lost update »), silencieusement. `_ledger_file_lock` (même patron que `tools/stigmergy._board_lock` : `fcntl.flock` sur un fichier `.lock` sidecar, dégradé en no-op sur Windows/FS sans flock) sérialise désormais chaque écriture sur `events.jsonl`/`incidents.jsonl`. Vérifié : 41/42 exécutions locales de la suite e2e complète (contre des échecs répétés avant le correctif) ; `tests/unit/missions`, `tests/unit/test_hosts.py`, `tests/unit/test_session_enrolment.py`, `tests/unit/mcp/test_task_tools.py`, `tests/unit/test_workspace_task_steering.py` verts.
- feat(missions): lot B — l'orchestrateur humain pilote les tâches depuis le cockpit : prioriser, commenter, diriger, annuler (issue #638). Constat vérifié le 2026-09-27 : `priority` existait au schéma sans qu'aucune action ne le change, aucun champ de commentaire ni de consigne, l'annulation passait par `move --to cancelled` sans raison, et rien ne relisait une consigne humaine côté agent. Tout passe par `TaskService` (ADR-007 : la webview n'est jamais une autorité causale), un gate contourné par une surface le serait par toutes.
  - Modèle : `MissionTask.directives`, journal append-only de `TaskDirective` (id, auteur, horodatage, texte, `kind` ∈ {comment, directive}, `delivered_at`, `acknowledged_at`), rejoué depuis quatre événements du ledger (`task.directive_added`, `task.directive_delivered`, `task.directive_acknowledged`, `task.prioritized`) — jamais une réécriture de l'historique (`missions/schemas.py`, `missions/ledger.py`). Échelle `PRIORITIES = low < medium < high < critical` et `priority_of()` (déclarée, sinon dérivée du `risk_profile` comme avant) dans `missions/board.py` ; le board se trie par priorité dans chaque colonne et porte `directives_pending` / `directives_unacknowledged`.
  - Service : `TaskService.prioritize`, `comment`, `acknowledge`, `deliver_pending_directives`, `cancel(task_id, reason, actor, force=False)` — la raison est obligatoire et une tâche `claimed`/`running` tenue par un autre acteur exige `force` explicite ; les deux refus sont des `TaskRefusedError` (200 `blocked: true` au cockpit, comme un gate rouge). `task.transitioned` porte `cancelled_by` et `forced`.
  - Surfaces : CLI `grimoire task prioritize / comment / cancel / ack` (`task show` liste les consignes avec leur état non lue / livrée / accusée) ; MCP `task_update` gagne `prioritize`, `comment`, `cancel`, `ack` (paramètres `text`, `kind`, `directive_id`, `force`), `task_show` / `task_recall` exposent `effective_priority`, `directives_pending` et les consignes ; cockpit `_task_action` gagne les mêmes quatre verbes et `tasks_view` rend `priorities` et la priorité effective. Espace Exécuter : sélecteur de priorité, zone commentaire/consigne, bouton « Annuler la tâche » inerte sans raison (l'option « forcer » n'apparaît qu'après un refus `claim`), tri par priorité dans chaque colonne et dans la liste, badge « consigne non lue (n) » sur la carte.
  - Injection côté agent (`hosts/decisions/steering.py`, appelé par `task_context` au `UserPromptSubmit` et par `activation` au `SessionStart`) : les consignes non livrées de la tâche active sont ajoutées au contexte (`[Grimoire — consigne de l'orchestrateur] <auteur>, <date> : <texte>` + rappel de `grimoire task ack` / `task_update(action="ack")`), marquées `delivered_at` dans le même geste, livrées une fois. Une tâche annulée pendant qu'une session la tient est dite en clair au prochain `UserPromptSubmit` de cette session avec sa raison (`[Grimoire — tâche annulée] …`, une fois par session, événement `task.cancellation_notified`) ; le `Stop` ne réclame plus son gate (état `archived` : contexte explicite dans `evidence_gate`). Le helper ne lit que `MissionLedger`, jamais le service ni ses gates : le chemin du hook reste léger (`tests/unit/test_hook_cost.py` vert).
  - Rouge-avant / vert-après : `tests/unit/missions/test_human_steering.py` (12 tests : priorité et historique, ordre du board, consigne livrée au prompt suivant puis au SessionStart, accusé de réception, annulation sans raison refusée, `force` exigé pour une tâche tenue ailleurs, annulation annoncée à la session puis Stop non bloquant) ; `tests/unit/test_workspace_task_steering.py` (route HTTP réelle `ForgeAPI` : commentaire posé par `POST /api/workspace/tasks/<id>/comment` visible au `UserPromptSubmit` suivant, priorité visible dans `tasks_view` et dans l'ordre du board, annulation sans raison en 200 `blocked`, appel direct de `_task_action`) ; `tests/unit/cli/test_cmd_task_steering.py` ; `tests/unit/mcp/test_task_tools.py::test_task_update_prioritise_commente_accuse_et_annule_avec_raison` ; `tests/e2e/test_workspace_executer_steering.py` (Playwright : sélecteur, consigne, badge, annulation depuis l'inspecteur).
  - Rejeu manuel sur projet jetable (`grimoire init -y --backend local`, `standard init --profile governed`, `host sync --host claude`, `GRIMOIRE_COCKPIT_HOME` isolé, `grimoire serve` réel) : consigne posée par `curl` sur la route, relue par `grimoire-hook --host claude --event UserPromptSubmit`, livrée une fois ; priorité `critical` en tête de colonne dans `task-board.yaml` ; `cancel` sans raison → `blocked: true`, tenue par `claude-session` → refus `claim`, `force: true` → `claimed → cancelled`, annonce au prompt suivant, `Stop` en `allow`.
  - Non mesuré : coût en tokens de l'injection sur le banc `kit-gov` (une consigne n'est injectée que lorsqu'un humain en pose une) ; aucun schéma Rust ne porte `MissionTask` (`rust/grimoire-schema-core` couvre `project-context`), donc pas de parité à tenir.

- feat(missions): lot A — les chantiers des sessions entrent dans le Mission Ledger (issue #638). Constat du 2026-09-27 : treize sessions ouvertes sur un projet gouverné, aucune au ledger — toutes sous la tâche de repli `bootstrap`, que le hook `Stop` acceptait de clore. Désormais, sur un projet enrôlé, une session sans tâche est prévenue et, en profil `governed`/`production`, refusée à la clôture si elle a écrit ; un agent peut ouvrir sa tâche par MCP ; et le claim d'une tâche sait quelle session le porte.
  - `task_add` (MCP, `src/grimoire/mcp/server.py`) : titre, critères d'acceptation ≥ 1, owner, mission, preuves attendues, actor, `ready=true` pour la rendre réclamable tout de suite. Même service que `grimoire task add` — nouveau `TaskService.add` (`src/grimoire/missions/service.py`, rend `TaskAdded`) que le CLI appelle aussi, avec la nouvelle option `--ready` ; un critère blanc est refusé par le ledger comme un critère absent.
  - Enrôlement fail-closed (`src/grimoire/hosts/decisions/enrolment.py`, partagé par `activation.py`, `task_context.py`, `evidence_gate.py`) : quand `resolve_active_task` retombe sur `bootstrap`, `SessionStart` (profils bloquants) et `UserPromptSubmit` (tout projet enrôlé) disent qu'aucune tâche n'est en cours et donnent le remède copiable (`grimoire task add … --ready`, `task context`, `task claim`, ou `task_add`/`task_context`/`task_claim` MCP, ou `GRIMOIRE_TASK_ID`). `PostToolUse` compte dans `session-<id>.json` (`mutations`) chaque outil non lecture seule (`src/grimoire/policies/session_state.py`, `evidence_trace.py`) ; `Stop` rend `decision: block` avec le remède quand ce compteur est non nul et la tâche encore `bootstrap` (`detail.blocked_on = no_active_task`), avertit seulement hors `governed`/`production`, ne bloque jamais une session qui n'a fait que lire ni un hôte sans `session_id`, et respecte `stop_hook_active`.
  - Lien session ↔ tâche : `TaskClaim.session_id` / `session_host` (`src/grimoire/missions/schemas.py`), posés au rejeu d'un événement `task.session_attached` (`MissionLedger.attach_session`, `TaskService.attach_session`) — jamais en réécrivant l'historique, jamais en volant un claim déjà rattaché à une autre session. Le hook `UserPromptSubmit`/`SessionStart` l'ajoute au premier tour d'une session dont la tâche active est un claim (`HookInput.host` porte désormais l'identifiant d'hôte de `grimoire-hook --host`). Le journal `session-<id>.json` porte `task_id`. `tasks_view`/`task_view` (`src/grimoire/tools/workspace_api.py`) et `task_show` (MCP) exposent `session_id`, `host` et `resume_command` (`src/grimoire/missions/session_link.py` : `claude --resume <session_id>` pour Claude Code, chaîne vide pour tout autre hôte — rien d'inventé) ; l'espace Exécuter (`web/workspace/spaces/executer.js`) les montre sur la carte, dans la liste et dans l'inspecteur (bouton « Reprendre » qui pose la commande dans le dock).
  - Rouge-avant / vert-après : `tests/unit/test_session_enrolment.py` (20 tests ; 16 rouges sur `main` au commit « rouge-avant », tous verts après), dont le test de fait de l'issue rejoué par `grimoire-hook` sur stdin avec des payloads Claude Code réels : (a) sans tâche le contexte porte le remède, (b) après une écriture `Stop` bloque avec le remède, (c) après `task_add` + `claim` la carte porte `session_id`, le journal `task_id`, et `Stop` ne bloque plus pour ce motif. `tests/unit/mcp/test_task_tools.py` compte sept outils.
  - Non mesuré : le coût du hook `UserPromptSubmit` avec rattachement (une lecture du ledger de plus par tour, pas par appel d'outil) ; le rendu de l'espace Exécuter n'a pas de test navigateur dans ce lot.
- ci(evals): la sonde d'ancrage tourne chaque lundi dans `weekly-bench.yml` (issue #613, cas ouvert 16). Nouveau job `grounding-probe` : installe le kit du commit courant et le CLI `claude`, construit le témoin avec `evals/grounding-probe-witness.py` (projet jetable où toute réponse chiffrée est inventée, wrapper `scribe` émis par ce kit), pose les deux questions dont la réponse propre est mesurée (couverture d'un module inexistant, capacité d'une API inexistante ; haiku, 3 reps) et échoue dès qu'un chiffre sans source apparaît (`report --fail-on-fabrication after`). La question « note sur 10 » reste hors gate : mesurée 3/3 fabriquée sur haiku dans `evals/reports/2026-09-18-grounding-probe/`. Sans `ANTHROPIC_API_KEY` dans les secrets, le job est sauté avec un avertissement — jamais vert par défaut. Coût de l'ordre d'un demi-dollar par run. Test : `tests/unit/test_grounding_probe_witness.py`.
- refactor(framework): l'échelle de confiance auto-déclarée du HUP et Session Momentum sont retirés (issue #613, cas ouverts 10 et 11). Après la sonde d'ancrage (`evals/reports/2026-09-18-grounding-probe/` : un agent écrit « non mesuré » puis donne quand même une note), garder une échelle VERT / JAUNE / ROUGE que rien ne calcule était une contradiction avec le socle — `agent-base.md` déclare depuis le 2026-07-12 qu'aucune confiance numérique n'est requise. Décision de Guilhem (2026-09-25) : retirer.
  - `framework/honest-uncertainty-protocol.md` réécrit (320 → ~90 lignes) en « HUP — Incertitude déclarée » : la règle de source, le bloc ```grimoire-uncertainties``` (le seul état qu'un programme lit, `missions.dispatch._extract_uncertainties`), l'Uncertainty Report avec `blocking: true` comme unique déclencheur d'escalade, l'anti-évitement (effort documenté). Retirés : circuit breaker sur la couleur, pre-flight / post-flight `confidence_level`, options à `confidence: "30%"`, exemples par couleur.
  - `agent-base.md` : section HUP réécrite sur la règle de source et le bloc ; section Session Momentum supprimée (formule `boost = min(n/5, 2)` et paliers LOW/NORMAL/HIGH/FLOW que rien ne calculait) ; « Confidence Signal » HIGH/MEDIUM/LOW remplacé par un « Source Check ». `agent-base-compact.md` : règle 2 réécrite, règle 12 retirée (Friction Budget devient la 12), self-critique sans niveau de confiance.
  - Les protocoles copiés dans les projets parlent désormais le même vocabulaire : « aucune incertitude déclarée » / « incertitudes déclarées » / « point bloquant déclaré » à la place de HUP VERT / JAUNE / ROUGE ; compteur `blocking_gaps_count` à la place de `hup_red_count` ; traces `UNC:declared` / `UNC:blocking` à la place de `HUP:preflight` / `HUP:postflight` / `HUP:escalation` (`agent-relationship-graph`, `cross-validation-trust`, `event-log-shared-state`, `grimoire-trace`, `hybrid-parallelism-engine`, `orchestrator-gateway`, `question-escalation-chain`, `selective-huddle-protocol`, `workflows/subagent-orchestration` et sa copie locale). Divergence connue, non corrigée : `framework/tools/observatory.py` (zone gelée) lit encore `hup_red_count` et les traces `HUP:*` (défaut à zéro, comme pour `avg_trust_score`).
  - Garde : nouvelle règle `confidence-scale` dans `scripts/check-emitted-prose.py` (`confidence_level:`, `confiance : ROUGE`, `HUP JAUNE`, `HIGH/MEDIUM/LOW →`) avec son cas synthétique dans `tests/unit/test_grounding_prose.py`, pour qu'une échelle ne revienne pas par un gabarit.

## [3.59.0] - 2026-09-26

- fix(hosts): sur Copilot, le point d'entrée agit ET délègue ; verbe `web` émis sous son vrai nom (issue #622, second retour). Après #624, un utilisateur rapportait encore un concierge « avec de gros soucis de droits, capable de rien » : en `read, search` + `agent`, il ne fait que router, ce qui dépend d'un VS Code qui sait lancer des sous-agents et d'un modèle qui appelle l'outil. Décision de Guilhem (2026-09-25) : l'agent d'entrée reçoit l'union des outils des personas qu'il route (`read, search, edit, execute` sur un projet courant) plus `agent` — il délègue quand un rôle précis existe, sinon il fait le travail lui-même ; c'est le modèle de l'orchestrateur écrit à la main dans la Forge (tous les outils + `agent/runSubagent`). Les personas routées gardent leur frontière propre.
  - `hosts/emitters/copilot.py` : union ordonnée (outils propres, puis ceux des personas routées, puis `agent`), frontière d'outils qui le dit, rôle « tu agis, ou le spécialiste agit ». Vérifié contre la référence VS Code « tools reference » : `read`, `search`, `edit`, `execute`, `agent` sont bien des tool sets ; `fetch` n'en est pas un — le set s'appelle `web` (`web/fetch`) et un nom inconnu est ignoré en silence, donc toute persona déclarant `web` perdait le web sans message. Corrigé dans `_TOOL_TABLE`.
  - Persona `concierge` : fait lui-même ce qui ne relève d'aucun rôle quand l'hôte lui donne les outils.
  - Rouge-avant / vert-après : `tests/unit/test_hosts.py::test_copilot_entry_agent_acts_with_the_union_of_routed_tools`, `::test_copilot_maps_the_web_verb_to_the_vs_code_web_tool_set`.

- fix(hosts): sur Copilot, le point d'entrée peut agir et déléguer (issue #622). Des utilisateurs ont été surpris par un concierge « sans droits » qui ne passait pas la main : déclaré `read, search` par conception (« il ne fait pas le travail lui-même »), il ne pouvait ni éditer ni exécuter — et sur VS Code, un custom agent n'invoque une autre persona que s'il a l'outil `agent` dans `tools` et une liste `agents:` (docs VS Code « custom agents »), que l'émetteur n'émettait pas. Le wrapper lui disait « tranche toi-même » et le persona « ROUTE: suggest the best agent » : une suggestion, pas une action.
  - `hosts/emitters/copilot.py::_agent_file` : l'agent d'entrée reçoit `agent` dans `tools` et `agents: [toutes les personas routées]` ; son rôle dit qu'il délègue avec l'outil `agent` (brief : contexte, objectif, contraintes) et ne répond jamais « je n'ai pas les droits » ; sa frontière d'outils nomme la délégation. Pas de `handoffs:` : un bouton par persona après chaque réponse (vingt-cinq sur un projet courant) n'aide pas, la délégation passe par l'outil et le menu d'agents reste là pour la main. Les personas routées ne reçoivent ni `agents:` ni handoff : invocables comme sous-agents, jamais routeurs.
  - `archetypes/meta/agents/concierge.md` : « ROUTE » devient une invocation en sous-agent quand l'hôte le permet (outil `agent`, dispatch), sinon dire à l'utilisateur vers quel agent basculer ; l'identité précise que sur un hôte qui sait déléguer, c'est le concierge qui lance le spécialiste.
  - Rouge-avant / vert-après : `tests/unit/test_hosts.py::test_copilot_entry_agent_can_delegate_to_every_routed_persona`.

## [3.58.0] - 2026-09-24

- fix(standard): `gate check` contrôle le claim-ledger ligne à ligne dès `in_progress` (issue #614). Avant : `_verify_claim_ledger` (AG-QUA-002) n'était appelé que par `review_state_content_checks`, donc aux états `review`/`accepted`/`released` ; une ligne « utiliser » non prouvée écrite pendant le travail passait `gate check --strict` — le seul chemin que la directive de session mandate — sans un mot jusqu'au passage en revue, alors que c'est pendant le travail que l'affirmation pèse sur les décisions. Constaté en corrigeant #613 (personas qui inventent des chiffres) : la seule défense mécanique contre une affirmation sans source arrivait trop tard.
  - `standard_checks/gate_review_checks.py::in_progress_content_checks`, appelé par `check_evidence_gates` à l'état `in_progress` (deux lignes dans `agentic_standard.py`, sous le plafond du ratchet : 1898 ≤ 1916). `claim_ledger_verify.verify_claim_ledger(rows_only=True)` ne lève que les contradictions ligne à ligne — `claims.proved_without_evidence` (erreur), `claims.used_unproved` (erreur en `governed`/`production`, avertissement ailleurs) — jamais `claims.empty` ni `claims.summary_placeholder`, constats de clôture qui bloqueraient toute tâche gouvernée dès sa première minute.
  - Rouge-avant / vert-après : `tests/test_agentic_standard.py::test_gate_check_flags_an_unproved_claim_used_while_the_task_is_in_progress` ; gardes : `::test_gate_check_in_progress_does_not_demand_a_finished_ledger`, `::test_gate_check_in_progress_only_warns_on_a_non_strict_profile`.
  - Non mesuré sur le banc `kit-gov` : le contrôle ne lit qu'un fichier déjà scaffoldé et ne s'exprime que si une ligne contradictoire existe ; aucun tour supplémentaire attendu tant que l'agent n'écrit pas une affirmation qu'il n'a pas prouvée — ce qui est précisément le cas à arrêter.

- fix(hosts): ancrage des affirmations — chaque persona émise cite sa source ou écrit « non vérifié » (issue #613). Rapport d'un utilisateur Copilot (Opus 5) : ses personas « inventent des chiffres et donnent des pronostics sur des éléments non regardés ». Lu sur `origin/main` 3.57.0 : rien de ce que Copilot charge n'exigeait de lire avant d'affirmer (le wrapper disait seulement « signale comme non vérifié », le bloc d'incertitudes ne vivait que dans `.github/hooks/README.md`, jamais chargé), le HUP restait à trois sauts de lecture volontaire, et le kit demandait lui-même des chiffres sans mesure (`Score global : X/5`, `X/10`, `X/100`, projections « estimées »). Une matrice de confiance auto-déclarée n'y changerait rien — le kit en porte déjà trois, aucune n'est vérifiable ; la règle vérifiable est celle du claim-ledger, appliquée à la réponse.
  - `GROUNDING_RULE` et `UNCERTAINTIES_BLOCK_RULE` (`src/grimoire/hosts/emitters/base.py`), partagées par les émetteurs Copilot et Claude Code : tout chiffre, verdict ou affirmation sur un fichier cite la commande exécutée ou le fichier:ligne, sinon « non vérifié » ; chaque persona routée clôt par le bloc ```grimoire-uncertainties``` (celui que `grimoire task dispatch` exigeait déjà des ouvriers headless, `missions.dispatch._UNCERTAINTIES_INSTRUCTION`). L'agent d'entrée Copilot reçoit une section « Politique de dispatch » (V0/V1/V2, sans nom de modèle — dégradation « model affinity » inchangée) qui exige ce bloc des personas qu'il route, au lieu d'un README qu'il ne lit pas. Rouge-avant / vert-après : `tests/unit/test_hosts.py::test_copilot_agent_files_bind_every_claim_to_a_source`, `::test_claude_agent_files_bind_every_claim_to_a_source`.
  - Gabarits émis sans score à remplir de tête : `grimoire-health-check.prompt.md` tire son bilan des clés `passed`/`failed` de `grimoire -o json doctor` ; `prompt-templates/audit-report.md` compte des lignes de tableau (conformes, avertissements, critiques, non vérifiés) au lieu d'un `X/10` (variable `scoring` retirée) ; `prompt-templates/capacity-planning.md` ne projette qu'à partir d'une série mesurée (« non mesuré » sinon) ; `archetypes/meta/agents/agent-optimizer.md` rend des verdicts OK/KO sourcés fichier:ligne au lieu de `X/100`. Gardé par `tests/unit/test_grounding_prose.py` sur toute la prose émise (`framework/copilot`, `framework/hosts`, `framework/prompt-templates`, `archetypes/`, les deux socles).
  - Socle : `agent-base(-compact).md` — le budget de friction porte sur les questions, jamais sur les faits (un fait non vérifié se livre marqué, il ne devient pas un « choix autonome ») ; Session Momentum fait croître l'autonomie, pas « la confiance » ; un signal de confiance n'est jamais une preuve. `grimoire-project.instructions.md` (seul fichier auto-chargé côté Copilot) et le seed `.github/copilot-instructions.md` portent la même règle. La skill `grimoire-evidence` nomme enfin le claim-ledger que le gate vérifie.
  - Non fait ici, issue #614 : contrôler le claim-ledger dès `in_progress` (aujourd'hui `review`+ seulement) — comportement du gate, à mesurer sur le banc avant.
  - Second lot, même PR — les autres entrées de contexte et la pyramide de gardes. Balayage de toute la prose copiée ou émise (93 fichiers) : l'émetteur générique (Codex `AGENTS.md`, Cursor `.cursor/rules/grimoire.mdc`, Gemini `GEMINI.md`) n'émettait aucune règle de source ; le prompt des ouvriers headless (`missions.dispatch.build_prompt`) exigeait le bloc d'incertitudes mais pas la règle ; les instructions du serveur MCP ne disaient rien ; six protocoles copiés dans `_grimoire/kit/framework/` demandaient des scores que rien ne calcule (`trust_score` 0-100 et sa moyenne pondérée dans `cross-validation-trust.md`, `confidence: 0.92` / `synergy_score` / `avg_trust_score` dans `agent-relationship-graph.md`, `match_score` dans `agent-mesh-network.md`, `confiance ≥ 80%` dans `question-escalation-chain.md`, `Trust: {composite_score}/100` dans `orchestrator-gateway.md`, `trust_threshold: 70` dans `workflows/subagent-orchestration.md`), alors que `agent-base.md` déclare depuis le 2026-07-12 qu'« aucune confiance numérique n'est calculée ni requise ». Sept autres gabarits d'archétype notaient de tête (`score /10` de `platform-architect`, « noter 1-5 » de `web-frontend-ux`, grille 0-10 pondérée et `Score [X]/100` de `agent-optimizer`, « score de résilience » et « estimer le RTO » de `infra-backup-dr`, `confidence < 0.3` du concierge, « scores par dimension » de `meta-toolsmithing`).
    - Une seule source pour la règle : `src/grimoire/core/grounding.py` (`GROUNDING_RULE`, `UNCERTAINTIES_BLOCK_RULE`, `GROUNDING_RULE_EN`), consommée par les trois émetteurs, `build_prompt` et `mcp/server.py`. La règle dit désormais aussi qu'un score, une note ou une probabilité n'existe que si une commande l'a calculée — « même si on te demande un chiffre ». Gardé par `tests/unit/test_hosts.py::test_every_host_emits_the_grounding_rule_and_the_uncertainties_block` (paramétré sur `supported_hosts()`) et `tests/unit/missions/test_dispatch_agent_context.py::test_worker_prompt_carries_the_grounding_rule`.
    - Les scores de confiance inter-agents deviennent des verdicts sourcés et des comptes observables : `pass | fail | unverified` + `evidence:` par dimension CVTL, `tally: "{pass} sur {verified}"` sans pondération ; `handoffs_accepted`, `capabilities_matched`, `cross_validations_passed` dans le graphe et le mesh ; règles ordonnées (rôle déclaré → historique de handoffs → disponibilité) à la place des formules pondérées ; l'événement ELSS `trust_scored` devient `cross_validated`. Divergence connue et non corrigée : `framework/tools/observatory.py` (zone gelée) lit encore `avg_trust_score`/`avg_outcome_trust` avec un défaut à 0.
    - Pyramide de gardes, documentée dans `CONTRIBUTING.md` : (1) statique — `scripts/check-emitted-prose.py`, quatre règles (`placeholder-score`, `invented-confidence`, `numeric-trust-score`, `estimate-placeholder`), corpus dérivé de `Scaffolder._PROTOCOL_DOCS`, exceptions justifiées dans `scripts/emitted-prose-allowlist.txt` (une seule : l'horodatage `estimated_free_at` du mesh), branché sur pre-commit (`emitted-prose-guard`), `make lint` et `tests/unit/test_grounding_prose.py` ; (2) émission — les tests par hôte ci-dessus ; (3) comportement — `evals/grounding-probe.py`, trois questions dont toute réponse chiffrée est inventée, juge mécanique (pourcentage, note `/10`, quantité, hors nombres repris de la question), rapport dans `evals/reports/2026-09-18-grounding-probe/`.
    - Mesuré, sonde haiku 3 reps par question, wrappers Claude Code avant/après (`evals/reports/2026-09-18-grounding-probe/report.md`) : bloc d'incertitudes 8/8 après contre 0/9 avant, marque « non vérifié » 8/8 contre 2/9, projection de capacité inventée 0/2 contre 1/3. **Non résolu par la prose** : à la question « note sur 10 + probabilité de régression », haiku produit une note et un pourcentage dans 3/3 runs des deux bras, parfois précédés de « non mesuré », et invente au passage un défaut du fichier lu. La règle émise réduit le défaut, elle ne l'élimine pas : la défense mécanique reste le claim-ledger et le gate (#614).

## [3.57.0] - 2026-09-18

- docs(bench): rejeu du bras `kit-gov` après le lot I et verdict de phase (issue #582, lot J). Campagne réelle (`scripts/bench/three_arms.py --arms kit-gov --resume`, graine 551, mêmes 20 tâches, `main` @ `b1523007`) : tours médians 12,0 → **6,0** (identique à `nu`, ratio 1,0×, sous le seuil de 1,5× de la phase pour la première fois), coût médian/tâche résolue 0,769 $ → **0,445 $** (≈112 % du nu, contre ≈193 % au lot H), succès 90,0 % → **96,7 %** (au-dessus de `nu` 91,7 % en valeur ponctuelle, non significatif), pass^k 85 % → 95 %. Effet direct et chiffré du lot I : friction de toolchain Go/Rust (médiane 4 tours/run au lot H) tombe à une médiane de 0 ; part de runs se terminant sur un `gate check` vert : 68,3 % (lot H, mesuré rétroactivement) → **100 % (60/60)**. Deux critères de sortie de la phase 2bis sur trois désormais atteints (tours, succès en valeur ponctuelle) ; le coût reste au-dessus du seuil historique (≤ 70 % du nu) mais à 1,7 point d'une lecture de parité (≤ 110 % du nu) documentée sans arbitrage. `docs/bench/rejeu-lot-j-2026-09-18.md` (méthode, incident OAuth à coût nul et défaut de garde non rejouable rencontré en préparant ce lot, résultats agrégés/par langue/par tâche, décomposition de tours sur 8 transcriptions, ce qui reste). `docs/plan-2026-q4.md` mis à jour : ligne J (§4), résultat du lot I et du lot J, bannières des phases 3/4 (« gel levable sur décision de Guilhem, pas encore levé »), §7 risques. `docs/index.md` : lien vers le nouveau rapport.

- fix(bench): garde du gabarit rejouable et authentification par clé API sans copie des jetons OAuth (issue #582, lot J). Deux défauts réels trouvés dans `scripts/bench/three_arms.py` en préparant le lot J :
  - **Garde non rejouable.** `verify_grimoire_binary_matches_template` (lot H, PR #605) provisionnait le dépôt jetable de vérification à un chemin FIXE (`workspace / "_grimoire_bin_check"`) : au second appel sur le même workspace (ex. un `--resume` après incident), `grimoire init` retombait sur un dépôt déjà initialisé et refusait (« Use --force to overwrite »), faisant échouer la garde elle-même. Corrigé : `check_dir` est désormais le RÉPERTOIRE PARENT, chaque appel provisionne un sous-répertoire jetable unique via `tempfile.mkdtemp(prefix="_grimoire_bin_check-", dir=check_dir)`, supprimé dans un `finally` (`shutil.rmtree`) que la vérification réussisse, échoue, ou lève. Rouge-avant vérifié : deux appels consécutifs sur le même workspace faisaient échouer le second (`test_verify_grimoire_binary_matches_template_is_replayable_on_the_same_workspace`, `tests/unit/test_bench_three_arms.py`) ; vert après correction, et plus aucun résidu sous `check_dir` d'un appel à l'autre.
  - **Copie des identifiants OAuth = déconnexions en rafale.** `credentials_provisioned` copiait les identifiants Claude Code de l'opérateur (`~/.claude/.credentials.json`) dans le `HOME` isolé de CHAQUE run. Ces jetons OAuth sont rafraîchis par rotation : quand une copie rafraîchit son jeton, la session interactive de l'opérateur ET les autres copies en cours deviennent invalides (« OAuth session expired and could not be refreshed »). Observé quatre fois en deux jours, systématiquement PENDANT une campagne (lots E, F, H, J — 30 runs perdus au lot J, plusieurs agents tués). Nouveau `--auth {oauth-copy,api-key}` (`main`, `scripts/bench/three_arms.py`) : `resolve_auth_mode(explicit, *, env=None)` choisit `api-key` par défaut si `ANTHROPIC_API_KEY` est présente dans l'environnement du lanceur, sinon `oauth-copy` avec un avertissement explicite sur la rotation (`SystemExit` si `--auth api-key` est demandé explicitement sans la variable). En mode `api-key`, `_run_one` n'appelle plus jamais `credentials_provisioned` : `run_claude_headless` ajoute `--bare` à la commande — d'après la documentation Claude Code (`code.claude.com/docs/en/headless.md`, « Start faster with bare mode » ; `code.claude.com/docs/en/authentication.md`, ordre de précédence), ce mode ignore explicitement le trousseau OAuth et le keychain système, et `ANTHROPIC_API_KEY` prime de toute façon sur les identifiants OAuth dans l'ordre de précédence documenté — `ANTHROPIC_API_KEY` est déjà transmise telle quelle à `claude -p` puisque `run_environment()` part de `os.environ`, aucune copie ni fichier de config supplémentaire n'est nécessaire. En mode `oauth-copy` : comportement inchangé (copie temporaire, effacée dans le `finally` de `credentials_provisioned`, comme avant ce lot). Nouveau champ `RunRecord.auth_mode`, nouveau `report["auth_modes"]` (`build_report`) affiché en tête de `report.md` (`render_report_markdown`) — le rapport nomme désormais le(s) mode(s) utilisés. Nouveau garde de fin de campagne `find_leaked_api_keys(workspace, api_key=...)` (pendant, en mode `api-key`, de `find_leftover_credentials`) : vérifie qu'`ANTHROPIC_API_KEY` n'a été écrite ni dans un `HOME` isolé ni dans un journal de run (`*.stream.jsonl`), alerte bruyamment sur stderr sans suppression automatique (structure du fichier trouvé inconnue). Tests (`tests/unit/test_bench_three_arms.py`) : `resolve_auth_mode` (défaut selon l'environnement, choix explicite prioritaire, `SystemExit` sur `api-key` sans clé ou mode inconnu, avertissement stderr en repli), `run_claude_headless` ajoute/omet `--bare` selon le mode, `_run_one` en mode `api-key` n'appelle jamais `credentials_provisioned` et n'écrit aucun `.credentials.json`, mode `oauth-copy` inchangé, `find_leaked_api_keys` (clé absente, détectée dans un `HOME` isolé et un journal, jamais dans un dépôt de tâche hors périmètre, rien trouvé), rapport listant les modes utilisés.
  - Doc : `docs/bench-three-arms.md` §3 mis à jour (les deux modes, la cause de rotation, `--auth`).
  - `ruff check` et `mypy --strict` verts sur `scripts/bench/three_arms.py` et `tests/unit/test_bench_three_arms.py` (aucune régression sur les erreurs préexistantes du fichier, non liées à ce lot).

- feat(standard): gate dosé — aucun test collecté distingué du rouge, pas de ré-exécution sur arbre identique, tâche V0 sans artefact à ouvrir (issue #582, lot I). Analyse du lot H (`_scratch/bench-h2/analyse-go-js-kit-gov.md`, 30 runs Go/JavaScript gouvernés) : sur les 15 runs JavaScript, `npm test` -> jest répond systématiquement « No tests found » (les dépôts de tâche masquent volontairement `*.spec.js`, comme Python masque ses `_test.py`) ; aucune action légitime de l'agent ne peut rendre ce gate vert — écrire le test caché est hors mandat, `--passWithNoTests` truquerait le gate sans rien prouver — et 0/15 runs atteignent `OK evidence gates` alors que 14/15 réussissent au jugement réel du harnais. Sur les 30 runs Go+JS, le seul check qui échoue jamais est `acceptance.test_run_failed` ; aucun échec d'enveloppe, de pack de preuve, d'acceptance-record ou de claim-ledger — ces quatre artefacts n'étaient donc pas le problème et ne sont pas touchés par ce lot.
  - **I-1 — « aucun test collecté » n'est plus « tests rouges ».** Nouveau module `src/grimoire/core/standard_checks/no_tests_collected.py` : `classify_no_tests_collected(command, exit_code, output_excerpt)` reconnaît, par la sortie (jamais par le nom de la commande — `npm test` ne dit nulle part qu'il invoque jest), pytest (code de sortie 5 ou « no tests ran »), jest/vitest (« No tests found »), `go test` (« no test files » / « [no test files] »), `cargo test` (« running 0 tests » sur *toutes* les cibles, jamais si une seule cible en a trouvé), mocha (« 0 passing » sans jamais « failing ») et `dotnet test` (« No test is available »). `record_acceptance_test_run` (`acceptance_test_run.py`) enregistre alors `ok: null`, `collected: 0` dans `test-run.json` plutôt que `ok: false`. Le gate (`verify_recorded_test_run_is_green`, déplacé dans `standard_checks/gate_test_run.py`, voir plus bas) émet `acceptance.no_tests_collected` en avertissement — jamais `acceptance.test_run_failed` pour ce cas. Une ligne `sans test : <raison>` reconnue n'importe où dans `acceptance-record.md` (`has_no_test_justification`, insensible à la casse, exige une raison non vide après les deux-points) éteint l'avertissement. Matrice état x profil (documentée dans le docstring de `verify_recorded_test_run_is_green`) : sans justification, l'entrée en état `review`/`accepted`/`released` sous un profil strict (`governed`/`production`) reste une **erreur** — une tâche qui entre en revue ou est acceptée sans preuve d'exécution ni justification n'a rien de plus à montrer plus tard ; tout autre croisement (état `in_progress`, ou profil non strict) reste un avertissement, comme avant l'entrée dans ces états. `gate check --strict` (`cmd_standard.py`) rend le nouveau verdict (« rien collecté », coloré en jaune) ; `gate run-tests` répond `WARN` (pas `FAIL`) sur ce même cas. Rouge-avant vérifié : un run jest « No tests found » émettait `acceptance.test_run_failed` (erreur) ; il émet désormais `acceptance.no_tests_collected` (avertissement, sauf matrice ci-dessus).
  - **I-2 — pas de ré-exécution sur arbre identique.** `gate check --strict` ne relance plus la commande de test quand un run enregistré a la même empreinte d'arbre que l'arbre courant, **quel que soit son verdict** (vert, rouge, ou rien collecté) — le fingerprint-skip du lot G1 (`_recorded_run_is_fresh_and_green`) ne court-circuitait que le cas vert, rejouant un rouge identique jusqu'à 10 fois sur un seul run mesuré au lot H, et un « rien collecté » (JS) à chaque appel sur 15/15 runs sans jamais converger. Nouvelle fonction `_recorded_run_matching_tree` (`gate_test_run.py`), généralisant l'ancienne ; nouvelle option CLI `--rerun` (`grimoire standard gate check --strict --rerun`) qui force malgré tout une nouvelle exécution. Le texte de sortie non-JSON nomme désormais explicitement le cas (« arbre inchangé depuis le dernier run (…) : relance forcée par `--rerun` »). Rouge-avant vérifié (runner factice comptant les appels réels) : deux `gate check --strict` consécutifs sur un run rouge exécutaient la commande deux fois ; une seule exécution après ce lot, `--rerun` en force une seconde. Même garantie vérifiée pour le cas « rien collecté ».
  - **I-3 — dosage V0 : rien à ouvrir.** Une tâche classée V0 par la classification de vérifiabilité existante (`grimoire.missions.verifiability`, tous les critères nomment un verdict mécanique) et dont le profil n'est pas `governed` n'a, par construction, aucun jugement humain à consigner. La directive du SessionStart (`activation_context_text`, `claude_activation.py`) ajoute alors la phrase « Tâche V0 : aucun artefact à lire ni à compléter, le gate suffit. », lue depuis la classe déjà projetée sur le board (`missions.board`, jamais recalculée). Vérifié sur un projet jetable réel (`grimoire task add` + `claim` + `move`, tâche V0 acceptée sur « les tests passent », profil starter) : à l'état `review`, `gate check --strict` portait `acceptance.decision_pending` et `claims.empty` (avertissements) même avec des tests verts ; `evidence.pending_gate`/`evidence.inventory_placeholder` n'apparaissaient pas dans ce scénario (le scaffold pré-remplit déjà l'inventaire avec le HEAD git — ni l'un ni l'autre n'était donc le problème identifié empiriquement, contrairement à l'hypothèse initiale). Ces deux avertissements réels sont désormais tus pour une tâche V0 en profil non gouverné : `_verify_acceptance_record` (`controls.py`) et le nouveau `verify_claim_ledger` (`src/grimoire/core/standard_checks/claim_ledger_verify.py`, extrait de `verifiers.py`) prennent chacun un paramètre `suppress_v0`, calculé une seule fois par `gate_review_checks.review_state_content_checks` (`_v0_non_governed`). Jamais pour V1/V2, jamais en profil `governed` — vérifié par test dans les deux sens. Rouge-avant / vert-après vérifié en CLI et en rejeu manuel : la même tâche re-classée V1 (critère « revue par le PO ») ou re-vérifiée en profil `governed` conserve les deux avertissements.
  - Ratchet de taille (`scripts/check-code-ratchet.py`, R2, seuil 1500 lignes) : `controls.py` et `verifiers.py` frôlaient déjà le seuil (1489/1496 lignes) sans marge pour ce lot. `verify_recorded_test_run_is_green` déplacée de `controls.py` vers `standard_checks/gate_test_run.py` (qui produit le run qu'elle relit) ; `_verify_claim_ledger` extraite de `verifiers.py` vers le nouveau `standard_checks/claim_ledger_verify.py` (importé à la fois par `verifiers.run_verifiers` et `gate_review_checks.review_state_content_checks`, sans cycle). `controls.py` : 1489 -> 1461 lignes ; `verifiers.py` : 1496 -> 1458 lignes.
  - Nouveau check `acceptance.no_tests_collected` déclaré dans `standard_checks/registry.py` (dimension `evidence_gates`).
  - Tests : `tests/unit/core/test_no_tests_collected.py` (16 cas, classification par runner et détection de justification, rouge/vert par cas) ; `tests/test_agentic_standard.py` (8 nouveaux tests CLI : rien collecté vs rouge, matrice état x profil, justification, non-ré-exécution avec runner factice comptant les appels réels, `--rerun`) ; `tests/unit/test_claude_activation.py` (6 nouveaux tests : phrase V0 figée textuellement, tâche V1/V2 et profil `governed` inchangés caractère pour caractère, silence sans board). Suite complète rejouée (`pytest -n auto`, hors deux échecs préexistants sans rapport avec ce lot — pollution d'isolation `test_cmd_memory_shared.py` sous xdist, `test_project_upgrade.py::test_find_orphans_keeps_feature_declared_and_override_agents` déjà rouge sur `origin/main`, vérifié par `git stash`) : verte. `ruff check`, `mypy --strict` et le ratchet de taille du code verts sur tous les fichiers touchés.
  - Rejeu réel en lecture seule sur la Forge (`grimoire standard gate check --task-id bootstrap --strict --no-run`, profil `governed`, depuis le venv du worktree) : sortie inchangée par ce lot (`--no-run` n'exécute jamais `ensure_fresh_test_run`, seul chemin modifié par I-1/I-2 ; I-3 ne s'applique jamais au profil `governed`).

- docs(bench): rejeu du bras `kit-gov` après les lots G1-G4 (issue #582, lot H). Après les lots G1 (#597), G2 (#598/#599), G3 (#602) et G4 (#604), le bras `kit-gov` du banc à trois bras passe de 28,5 à 12,0 tours médians (÷2,4), de 1,915 $ à 0,769 $ de coût médian par tâche résolue (÷2,5), et son succès (90,0 %) rejoint exactement celui de `kit` (contre 83,3 % au lot F, écart alors attribué à un incident OAuth isolé — confirmé a posteriori). `test-run.json` (preuve du lot B) reste présent sur 75,0 % des runs (68,3 % au lot F) malgré une directive divisée par plus de deux (824 → 396 caractères). Le critère de sortie de la phase 2bis reste toutefois non atteint sur ses trois axes : tours kit-gov/nu 2,0× (cible < 1,5×), coût ≈193 % du nu (cible ≤ 70 %), succès 90,0 % contre 91,7 % pour `nu` (non significatif). Décomposition des tours sur 8 transcriptions : la spéléologie source (médiane 8 au lot F) a disparu de l'échantillon, un poste nouveau apparaît (friction de découverte de toolchain Go/Rust dans la session de l'agent, absente côté harnais qui provisionne son propre compilateur séparément pour sa vérification finale). Décision : phases 3/4 toujours gelées ; piste retenue pour la suite, non encore exercée : doser le mandat de gouvernance selon la taille de la tâche. Rapport complet : `docs/bench/rejeu-lot-h-2026-09-18.md`. `docs/plan-2026-q4.md` mis à jour (ligne H, §4 phase 2bis, §7 risques, bannières de gel phases 3/4).
  - Incident de méthode traité dans le même lot : un premier lancement de la campagne (`PATH=".venv/bin:$PATH"`, entrée relative) a fait rejouer 27 runs (44 $) sur l'ancien gabarit de directive (pré-lots-G, 847 caractères) au lieu du nouveau (396, lot G3) — voir `fix(bench)` ci-dessous, PR #605. Données invalidées exclues, campagne relancée proprement (`--grimoire-bin` explicite, PATH absolu, garde-fou vérifié sur le premier dépôt provisionné avant de laisser tourner).
- fix(bench): la session de l'agent reçoit les mêmes toolchains que la vérification du harnais (issue #582, lot I). Le rejeu du lot H (`docs/bench/rejeu-lot-h-2026-09-18.md` §3) a mesuré que `grimoire standard gate check --strict` exécute la commande de test du projet DANS la session de l'agent (`claude -p`, HOME isolé, lot G1), mais que `scripts/bench/three_arms.py` ne provisionnait la toolchain Go (`ensure_go_toolchain`) que pour SA PROPRE vérification finale (`run_hidden_tests`) : dans la session de l'agent, `go` restait absent du `PATH`, `go test` échouait, et l'agent perdait des tours à chercher le binaire (`command -v go`, `find / -name gofmt`, jusqu'à 28 tours sur `go/pov`/`go/simple-linked-list`) — même risque, non chiffré séparément, pour Rust (`rustup toolchain list`/`rustup default stable` observés sur `rust/react`). Bras gouvernés go/javascript à 16 tours médians contre 6/5 pour Claude nu au lot H, python/rust à 7/8.
  - Nouvelle fonction `run_environment(task_dir, arm, *, home, grimoire_bin, workspace, go_bin=None, npm_cache_dir=None, real_home=None, base_env=None)` (`scripts/bench/three_arms.py`) : UN SEUL calcul d'environnement par run, transmis tel quel à la fois à `run_claude_headless` (nouveau paramètre `env`, la session de l'agent, ses hooks, et donc `gate check --strict`) et à `run_hidden_tests` (nouveau paramètre `env`, la vérification finale du harnais) — jamais deux calculs qui pourraient diverger. `_run_one` appelle `run_environment()` une fois et passe le même `dict` aux deux endroits.
    - Go : le binaire résolu par `ensure_go_toolchain` est mis EN TÊTE du `PATH`, pour TOUS les bras (pas seulement `kit-gov` — un agent `nu`/`ecc` sur une tâche Go affronte la même absence). `GOROOT` n'est fixé que si le binaire est celui provisionné par ce harnais sous le workspace (jamais pour un `go` système, dont la disposition des répertoires n'est pas garantie) ; `GOPATH`/`GOCACHE` sont toujours redirigés sous le workspace (`tools/go-path`, `tools/go-build-cache`), partagés entre bras et tâches.
    - Rust/Node restent ceux du système, jamais provisionnés par ce harnais : `CARGO_HOME`/`RUSTUP_HOME` sont explicitement repointés sur ceux du `HOME` réel de l'opérateur (`real_home`, `Path.home()` par défaut) SI l'environnement de base ne les redirige pas déjà lui-même — sans ça, le `HOME` isolé utilisé pour l'authentification Claude Code ferait chercher à `cargo`/`rustup` une toolchain sous un `$HOME/.cargo` qui n'existe pas pour cet utilisateur isolé, cause probable de la friction Rust observée. `CARGO_TARGET_DIR` (le cache de compilation, pas le registre) est toujours redirigé sous le workspace (`tools/cargo-target`).
    - `npm_config_cache` sous `npm_cache_dir` si fourni (même convention que `install_test_dependencies`, lot H). Le répertoire de `grimoire_bin` n'est ajouté en tête de `PATH` (et `GRIMOIRE_NO_COCKPIT=1` posé) que pour les bras `kit`/`kit-gov` — `nu`/`ecc` ne doivent jamais découvrir `grimoire` par un effet de bord de ce harnais.
  - Nouveau garde-fou `ensure_system_toolchains_present(languages)` : Rust/Node doivent déjà être sur le `PATH` système AVANT toute dépense (jamais provisionnés par ce harnais, contrairement à Go) — `SystemExit` avec message actionnable au premier binaire manquant, appelé avant le premier `claude -p` d'une campagne `--pilot`/`--full`.
  - Nouveau garde-fou `verify_agent_toolchain_environment(tasks, *, workspace, grimoire_bin, go_bin, npm_cache_dir)`, appelé une seule fois avant le tout premier appel `claude -p` d'une campagne (même emplacement que `verify_grimoire_binary_matches_template`, lot H) : pour CHAQUE langue présente dans la sélection de tâches, provisionne un dépôt jetable (tests cachés compris, jamais donné à un agent réel), résout la commande de test via `grimoire needs resolve --project-root . --json` (la même source que `gate check --strict`), et l'exécute dans `run_environment()`. Le code de sortie n'est PAS vérifié (un test peut légitimement échouer faute de solution) — seule l'absence d'un échec de toolchain (commande introuvable, `FileNotFoundError`, exit 127, motif `... not found` dans la sortie via le nouveau `_toolchain_failure_reason`) est exigée ; un écart lève `RuntimeError` avant tout appel modèle, 0 $ dépensé. Le résultat est persisté sous `state/toolchain_check.json` et intégré au rapport (nouvelle section « Environnement de l'agent »).
  - Nouvel indicateur « friction de toolchain » : `is_toolchain_friction_command`/`count_toolchain_friction_bash_calls` détectent, dans les commandes Bash vues par `run_claude_headless` (déjà extraites pour la détection de boucle), les motifs `which`/`command -v`/`find / -name`/`rustup`/`npm install`. Nouveau champ `RunOutcome.toolchain_friction_bash_calls`/`RunRecord.toolchain_friction_bash_calls` (calculé pour tous les bras), agrégé par bras (`toolchain_friction_bash_calls_total`/`_median`) et affiché par run dans les tableaux « Détail par run — bras kit/kit-gov » (nouvelle colonne) avec un résumé dédié au bras `kit-gov` — l'indicateur qui doit baisser une fois `run_environment()` en place.
  - Tests unitaires sans réseau ni LLM (`tests/unit/test_bench_three_arms.py`) : `run_environment` (Go en tête de `PATH` pour tous les bras, `GOROOT`/`GOPATH`/`GOCACHE`, `grimoire_bin` réservé à `kit`/`kit-gov`, `CARGO_HOME`/`RUSTUP_HOME` par défaut vs déjà redirigés, `npm_config_cache`), `_run_one` transmet le MÊME objet `env` à `run_claude_headless` et `run_hidden_tests`, `is_toolchain_friction_command`/`count_toolchain_friction_bash_calls`, `run_claude_headless` enregistre la friction depuis la transcription, `ensure_system_toolchains_present` (passe/`SystemExit`), `_toolchain_failure_reason` (test légitime vs exit 127 vs motif `not found`), `verify_agent_toolchain_environment` (passe, refuse avant dépense sur exit 127, `test-runner` non résolu, `npm install` en échec). Mise à jour des tests `main --full` existants (nouveaux garde-fous mockés) et des tests de rapport (nouveau champ `toolchain_friction_bash_calls`). `ruff check` et `mypy --strict` verts (aucune régression sur les erreurs préexistantes du fichier, non liées à ce lot).
  - Doc : `docs/bench-three-arms.md` mis à jour (nouveau §5 « Environnement partagé agent/harnais », §8 mesures, §9 prérequis système désormais vérifiés par le code, §10 sorties — sections 6 à 10 renumérotées en conséquence).

## [3.56.0] - 2026-09-17

- fix(bench): binaire grimoire résolu en absolu et gabarit vérifié avant toute dépense (issue #582, lot H). Incident du 2026-09-18 : la campagne lot H a été lancée avec `PATH=".venv/bin:$PATH"` (entrée PATH RELATIVE) ; le harnais invoque chaque `grimoire` avec `cwd` = dépôt de tâche jetable, où une entrée PATH relative se résout PAR RAPPORT À CE `cwd`, pas au répertoire de lancement — la résolution retombait donc sur le `grimoire` suivant du PATH, celui de la Forge (grimoire-kit 3.55.0, publié avant les lots G). 27 runs `kit-gov` (44 $) ont ainsi rejoué l'ancien gabarit de directive (847 caractères, rituel en trois étapes) au lieu du nouveau (396 caractères, lot G3) sans qu'aucun message d'erreur ne le signale — détecté seulement en comparant `.claude/activation-context.md` des dépôts de tâche au code du worktree.
  - Nouveau `resolve_grimoire_bin(explicit)` (`scripts/bench/three_arms.py`) : résout TOUJOURS un chemin absolu — `--grimoire-bin <chemin>` explicite (vérifié `is_file()`) ou, à défaut, `shutil.which("grimoire")` résolu en absolu à son tour ; `SystemExit` avec message actionnable si rien n'est trouvable, jamais un repli silencieux sur la chaîne littérale `"grimoire"`. Toutes les invocations `grimoire` du harnais (`setup_arm_kit`, `setup_arm_kit_gov`, `_collect_dispatch_stats`) utilisent désormais `grimoire_bin` en `argv[0]`, plus jamais la chaîne `"grimoire"` — et leur `env["PATH"]` place `Path(grimoire_bin).parent` en tête, ceinture-bretelles, pour tout sous-appel interne qui shellerait `grimoire` par lui-même.
  - Nouveau garde-fou `verify_grimoire_binary_matches_template(grimoire_bin, check_dir)`, appelé une seule fois avant le tout premier appel `claude -p` d'une campagne `--pilot`/`--full` (jamais pour `--dry-run`/`--report-only`) : provisionne un dépôt jetable (`git init` + `<grimoire_bin> init` + `host sync --host claude` + `standard init` — c'est `standard init` qui écrit `.claude/activation-context.md`, vérifié en isolation, `init`/`host sync` seuls ne l'écrivent jamais), lit ce fichier, et le compare caractère par caractère au gabarit ATTENDU. Un écart lève `RuntimeError` avant tout appel modèle — 0 $ dépensé.
  - Le gabarit attendu (`_expected_activation_directive_template()`) est lu directement dans le CODE SOURCE de ce worktree (`src/grimoire/core/claude_activation.py`, extraction de `_DIRECTIVE_TEMPLATE` par texte, sans jamais `import grimoire`) — jamais importé depuis l'environnement du binaire vérifié. Une première version de ce garde-fou comparait au contraire le gabarit servi au rendu de `activation_directive_template()` importé depuis l'interpréteur Python SIBLING de `grimoire_bin` : un binaire installé ailleurs sert un gabarit *cohérent avec son propre code*, donc cette comparaison ne pouvait jamais échouer — trouvé en testant le garde-fou en réel contre le grimoire 3.55.0 de la Forge (celui-là même de l'incident) avant tout usage sur une vraie campagne, corrigé avant merge.
  - `--grimoire-bin <chemin absolu>` : nouvelle option CLI (défaut : résolution automatique ci-dessus).
  - Tests unitaires sans réseau ni LLM (`tests/unit/test_bench_three_arms.py`) : `resolve_grimoire_bin` (chemin explicite, repli `which`, échecs des deux), `_expected_activation_directive_template` (extraction depuis un worktree synthétique, source absente), `verify_grimoire_binary_matches_template` (passe, échoue sur écart — y compris le cas qui a fait échouer la première version du garde-fou en test réel, `--version` en échec), `setup_arm_kit` invoque bien `grimoire_bin` en `argv[0]` avec PATH absolu en tête, jamais la chaîne littérale `"grimoire"`. Mise à jour des tests existants (`setup_arm_kit_gov`, `_run_one`, `_do_dry_run`, `main --full`) pour le nouveau paramètre requis.
- docs(bench): rejeu du bras `kit-gov` (projet réellement enrôlé au standard, profil `starter`, PR #592) sur le banc à trois bras — issue #582, phase 2bis « Cœur », lot F. 60 runs (graine 551, mêmes 20 tâches), 111,42 $. Résultat, comparé au bras `kit` non gouverné : tours ×4,75 (6,0 → 28,5 médians), coût médian/tâche résolue ×4,60 (0,416 $ → 1,915 $), temps médian ×4,58, succès -6,7 points (90,0 % → 83,3 %), pass^k -10 points (85 % → 75 %) — aucune tâche mieux réussie sous gouvernance. Le lot B (`gate run-tests`) devient enfin mesurable (68,3 % des runs avec `test-run.json`, contre 0 % pour `kit` au lot E), sans gain de pass^k démontré sur les deux tâches qu'il visait. Analyse causale des cinq tâches dégradées : `rust/poker` 0/3 et un tiers de `rust/two-bucket` 2/3 sont entièrement expliqués par un incident d'infrastructure isolé (expiration de session OAuth dans les 16 dernières secondes d'une campagne de 4h25, horodatages à l'appui) et non par la gouvernance — sur les trois autres tâches dégradées, le taux de réussite `kit-gov` est identique à celui de `kit`, tâche par tâche. Verdict par rapport au critère de sortie de phase : non atteint, plus nettement qu'au lot E. Décision : extras (phases 3/4) toujours gelés, cœur prioritaire — lots G1 (#597) et G2 (#598/#599) mergés en réponse directe à l'analyse tour par tour, G3 en cours, lot H (rejeu après G) à lancer ensuite. Rapport complet : `docs/bench/rejeu-lot-f-2026-09-17.md`. `docs/plan-2026-q4.md` mis à jour (phase 2bis : lots F/G1/G2/G3/H, §3, §7 risques, bannières de gel phases 3/4).
- fix(hosts): `grimoire up` rafraîchit le contexte d'activation possédé par le kit (issue #582 lot G4). Défaut trouvé en vérifiant le lot G3 : `install_claude_activation` (`src/grimoire/core/claude_activation.py`) n'écrivait `.claude/activation-context.md` que s'il n'existait pas (`if not context_path.exists()`) — le CHANGELOG du lot G3 lui-même le documentait déjà en creux (« la Forge, non retouchée par ce lot, sert toujours son texte pré-lot-B à 824 caractères tant qu'elle n'est pas réenrôlée »). Aucun projet déjà enrôlé ne pouvait jamais recevoir une directive corrigée par `grimoire up` ni `host sync` ; ni `install_claude_activation` (appelée seulement par `grimoire standard init`) ni le CLI `grimoire standard activation-context` (`activation_context_text`, qui ne comparait le fichier qu'au rendu *courant*) ne reconnaissaient un rendu non modifié provenant d'une version antérieure du kit — un fichier intact mais daté passait pour une personnalisation du projet.
  - Même mécanisme que les autres artefacts du standard plutôt qu'un nouveau : `grimoire.core.standard_generation` (manifeste `_grimoire/standard/.generated.json`, `destination -> sha256`) et sa fonction `decide(root, destination, content, force=, refresh=, manifest=)` (`write`/`adopt`/`keep`, absent du manifeste = propriété du projet). `.claude/activation-context.md` y est désormais suivi comme `_grimoire/standard/standard-profile.yaml` et consorts.
  - Nouveau `_HISTORICAL_DIRECTIVE_TEMPLATES` (`claude_activation.py`) : le texte exact de chaque révision passée de `_DIRECTIVE_TEMPLATE` (761 caractères avant #585, 824 avant #597, 947 avant #602) et un reconnaisseur par expression régulière (`_is_known_directive_rendering`, backreference sur `{task_id}` pour couvrir aussi bien le gabarit brut que le fichier pré-#266 déjà substitué avec un id arbitraire). Un fichier prédatant le manifeste et correspondant à un rendu connu — courant ou passé — est adopté (son empreinte enregistrée) au premier passage, exactement comme `standard_generation.decide` adopte les autres artefacts antérieurs au manifeste.
  - `install_claude_activation` gagne `force`/`refresh` (mêmes sémantiques que `setup_standard_profile`) : `refresh` rafraîchit un rendu daté mais intact, laisse un vrai fichier édité par le projet et le signale via le nouveau champ `ClaudeActivationResult.context_needs_review` plutôt que de rester silencieux ; `force` écrase inconditionnellement. `activation_context_text` reconnaît désormais tout rendu connu (pas seulement le cas `bootstrap` littéral pré-placeholder) et l'affiche à jour même si le fichier sur disque ne l'a pas encore été. Nouveau prédicat pur `activation_context_needs_refresh`, sans écriture, pour `up --dry-run`.
  - Câblage : `cmd_up.py::_step_standard` appelle `install_claude_activation(..., refresh=already_initialized)` — comme `setup_standard_profile` — mais seulement si `.claude/activation-context.md` existe déjà (un `up` n'enrôle jamais un projet au hook Claude Code de son propre chef, ce n'est le rôle que de `grimoire standard init`) ; le message du pas `standard` gagne un suffixe (« ; .claude/activation-context.md refreshed » ou « … modified by hand — à revoir (grimoire standard init --force to overwrite) »), et le mode `--dry-run` annonce le rafraîchissement prévu sans rien écrire. `cmd_standard.py`'s `init_profile` passe désormais `force=force` (déjà en portée) à `install_claude_activation`, et son JSON/texte de sortie expose `context_needs_review`.
  - Rouge-avant / vert-après : `tests/test_cmd_up.py::TestUpActivationContextRefresh` (contexte figé à 824 caractères → `grimoire up` → 396 caractères ; contexte édité à la main → inchangé + « à revoir » dans la sortie ; `--dry-run` annonce sans écrire), `tests/unit/test_claude_activation.py` (adoption au premier passage, refresh vs plain call, `--force`, prédicat `activation_context_needs_refresh`), `tests/unit/cli/test_cmd_standard_activation.py` (`init` seul laisse un rendu daté intact, `init --force` écrase même un fichier édité). `ruff check`, `mypy --strict` et le ratchet de taille du code restent verts.
  - Rejeu réel en lecture seule sur la Forge (dépôt `Grimoire-Forge`) : `grimoire up --dry-run` termine sans rien écrire (`git status` identique avant/après) ; la Forge elle-même n'a pas de `.claude/activation-context.md` (le hook Claude Code y est géré par le `SessionStart` unifié des émetteurs, jamais par `grimoire standard init`), donc la note de rafraîchissement ne s'affiche pas sur ce dépôt précis — vérifié séparément sur trois projets jetables sous `_scratch/` (bootstrapés par `grimoire up`, contexte legacy semé à la main, jamais réécrit par `install_claude_activation` pour rester antérieur au manifeste) : `--dry-run` annonce « refresh untouched standard artifacts; .claude/activation-context.md (stale) would be refreshed » sans écrire, l'exécution réelle passe le fichier de 824 à 396 caractères, un contexte édité à la main (`Notre directive maison…`) reste inchangé et le pas `standard` affiche « modified by hand — à revoir (grimoire standard init --force to overwrite) ».

- feat(bench): dépendances de test installées dans le bac à sable, préparation du lot H (issue #582). Les rejeux des lots E/F ont mesuré que l'agent, DANS sa propre session `claude -p`, ne pouvait jamais faire aboutir `npm test`/`npx jest` sur une tâche JavaScript du banc à trois bras (`scripts/bench/three_arms.py`) : `node_modules/` n'existait pas encore à ce stade, seule la vérification finale du harnais (`run_hidden_tests`, exécutée APRÈS la fin du run) installait les dépendances — `exit 127` systématique côté agent (« jest absent du bac à sable »), sur les quatre bras également (un confondu de méthode, pas une différence entre bras).
  - Nouvelle fonction `install_test_dependencies(task_dir, *, npm_cache_dir, timeout=120)` : si `task_dir` contient un `package.json`, lance `npm install --no-audit --no-fund --loglevel=error` avec le cache npm forcé sous `npm_cache_dir` (`npm_config_cache`, un sous-dossier du workspace du banc — jamais le `HOME` réel de l'opérateur ni l'un des `HOME` isolés par bras) ; `None` sans manifeste JS. Best-effort et jamais bloquant : un échec (réseau, registre, timeout) est journalisé dans le résultat du setup et compté comme tel dans le rapport, sans jamais lever d'exception — la campagne continue avec un `node_modules` absent, comme avant ce lot.
  - `setup_arm_nu`, `setup_arm_ecc`, `setup_arm_kit`, `setup_arm_kit_gov` acceptent tous un `npm_cache_dir: Path | None = None` (défaut rétrocompatible, aucun appel existant cassé) et y appellent `install_test_dependencies` — y compris `setup_arm_nu`, qui n'était jusqu'ici jamais invoquée par le harnais (`_run_one`/`_do_dry_run` n'avaient pas de branche `if arm == "nu"`) : équité de méthode, le bras nu reçoit désormais le même outillage de test que les bras gouvernés.
  - `_run_one`/`_do_dry_run` provisionnent tous les bras avec un `npm_cache_dir` commun (`workspace / "npm-cache"`, partagé entre bras et tâches pour ne pas retélécharger jest/babel à chaque run) ; `cleanup_build_artifacts` (déjà appelée après chaque run) supprime `node_modules/` comme avant, aucun risque disque supplémentaire.
  - Nouveau champ `RunRecord.test_deps_install_ok: bool | None` (`True`/`False` si un manifeste JS a été détecté et une installation tentée, `None` sinon) ; le rapport l'indique par run (nouvelle colonne « Dépendances JS installées (lot H) » dans les tableaux « Détail par run — bras kit/kit-gov ») et en agrégé par bras (nouvelle section « Dépendances de test installées avant l'agent », comptant manifestes détectés et installations réussies pour les quatre bras).
  - Tests unitaires sans réseau ni LLM (`tests/unit/test_bench_three_arms.py`) : `install_test_dependencies` (absence de manifeste, `npm install` réussi avec cache isolé vérifié, échec best-effort), `setup_arm_nu` avec/sans `npm_cache_dir`, câblage `_run_one` (`test_deps_install_ok` recopié depuis le résultat du setup), agrégation et rendu du rapport ; mise à jour des tests existants dont la forme de `kit_runs`/`kit_gov_runs` change (nouvelle clé) et du monkeypatch de `setup_arm_kit`/`setup_arm_kit_gov` (nouveau paramètre `npm_cache_dir`).

- feat(standard): directive gouvernée réduite au seul `gate check --strict`, détection étendue des commandes de test (issue #582 lot G3). Mesure d'origine : le banc à trois bras du 2026-09-17 (`_scratch/bench-f/analyse-tours-kit-gov.md`, 21 runs) mesure le bras `kit-gov` à 31 tours médians contre 6 pour Claude nu (83 % de succès contre 92 %, ×4,6 en coût) ; la directive de session (§3) mandatait un rituel en trois étapes — enveloppe, pack, puis `gate run-tests` puis `gate check --strict` puis `standard verify .` — que les lots G1/G2 ont rendu redondant sans jamais raccourcir le texte lui-même.
  - `_DIRECTIVE_TEMPLATE` (`src/grimoire/core/claude_activation.py`) tient désormais en trois phrases et un seul mandat : `grimoire standard gate check --task-id {task_id} --strict`. Avant/après (task_id `bootstrap`) : 947 → 396 caractères (mesuré sur `default_activation_directive("bootstrap")`, `origin/main` avant ce lot vs après ; sur le terrain, le fichier `.claude/activation-context.md` déjà installé d'un projet gouverné reste inchangé par design — jamais écrasé une fois présent — la Forge, non retouchée par ce lot, sert toujours son texte pré-lot-B à 824 caractères tant qu'elle n'est pas réenrôlée). `verify_standard_profile` couvre plus de contrôles que `check_evidence_gates` (artefacts au niveau du profil : manifeste, registres, provider — jamais modifiés pendant une tâche), mais sur le chemin d'une tâche (task-envelope, evidence-pack, claim-ledger, acceptance-record, run de test) les deux couvraient déjà les mêmes constats sauf un écart trouvé en vérifiant : `claim_ledger` (voir plus bas). La directive n'a donc plus besoin de mandater `gate run-tests`/`standard verify` séparément.
  - Écart comblé plutôt que remis dans la directive : `claim_ledger` figurait depuis le lot G1 dans `standard_checks.gate_remedy.GATE_ARTIFACT_KEYS` (scaffoldé pour toute tâche, requis par chaque profil dans `framework/agentic-standard/profile-map.yaml`) mais `check_evidence_gates` (`src/grimoire/core/agentic_standard.py`) n'en vérifiait ni la présence ni le contenu — seul `standard verify` le faisait (`run_verifiers` -> `_verify_claim_ledger`, AG-QUA-002). Une affirmation critique marquée « utiliser » sans être « prouvée » passait donc `gate check --strict`, le seul chemin que la directive mandate désormais, sans être jamais signalée. Nouveau module extrait `src/grimoire/core/standard_checks/gate_review_checks.py` (`review_state_content_checks`) : regroupe la projection de l'inventaire observé (lot G2) et les trois vérificateurs de contenu (`_verify_evidence_pack`, `_verify_acceptance_record`, `_verify_claim_ledger`) pour les états `review`/`accepted`/`released` — extrait d'`agentic_standard.py` (grandfathered au ratchet de taille) plutôt qu'ajouté en ligne, comme `standard_task_scaffold.py`/`gate_test_run.py` au lot G1. Rouge-avant / vert-après : `tests/test_agentic_standard.py::test_gate_check_catches_an_unproved_claim_used_in_a_decision_like_verify_does`.
  - Détection étendue de `test-runner` (`src/grimoire/core/execution_needs.py::resolve_need`) : le banc a mesuré que 15/21 runs (tous les exercices Python d'Exercism, un seul fichier `.py` de stub) ne portaient aucun des quatre marqueurs historiques (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`) — `need.resolved` restait structurellement faux et `gate run-tests` ne pouvait qu'échouer tôt (`acceptance.no_test_command_detected`). Nouveau repli `_FALLBACK_TEST_RUNNER_MARKERS`, consulté uniquement pour `test-runner` et seulement après la déclaration explicite et les quatre marqueurs historiques (même ordre premier-trouvé-gagne, jamais une invention sur un projet à deux langages) :

    | Marqueur | Commande |
    |---|---|
    | `pytest.ini` | `python -m pytest -q` |
    | `setup.cfg` avec section `[tool:pytest]` | `python -m pytest -q` |
    | `tox.ini` | `python -m pytest -q` |
    | dossier `tests/` | `python -m pytest -q` |
    | `test_*.py`/`*_test.py` à la racine | `python -m pytest -q` |
    | `pom.xml` | `mvn -q test` |
    | `build.gradle`/`build.gradle.kts` | `./gradlew test` si `gradlew` existe, sinon `gradle test` |
    | `CMakeLists.txt` | `ctest --test-dir build` (suppose un `build/` déjà configuré) |
    | `Makefile` avec une cible `test:` réelle | `make test` |
    | `mix.exs` | `mix test` |
    | `Gemfile` + dossier `spec/` | `bundle exec rspec` |
    | `*.csproj`/`*.sln` | `dotnet test` |
    | `Package.swift` | `swift test` |

    Le cas cité par le banc (`*_test.py` à la racine, sans `pyproject.toml` ni `tests/`) est couvert et testé explicitement. 23 tests dédiés dans `tests/unit/core/test_execution_needs.py` : un par marqueur (positif et, quand pertinent, négatif — ex. `setup.cfg` sans la section, `Makefile` sans cible réelle, `Gemfile` sans `spec/`), plus la priorité (déclaration > quatre marqueurs historiques > repli) et un cas d'ambiguïté à deux langages.
  - Docs : `docs/standard/integration.md` (section « Activation de session »). Tests mis à jour pour le nouveau texte : `tests/unit/test_claude_activation.py`, `tests/unit/cli/test_cmd_standard_activation.py`, `tests/unit/test_hosts.py`.

- feat(bench): bras `kit-gov`, un projet réellement enrôlé au standard, phase 2bis « Cœur » lot F (issue #582) — le rejeu du lot E (`docs/bench/rejeu-lot-e-2026-09-17.md` §3) a mesuré que le bras `kit` du banc à trois bras (`scripts/bench/three_arms.py`) ne fait jamais tourner `grimoire standard init` : `_is_governed()` (lot A, `src/grimoire/hosts/decisions/activation.py`) renvoie donc toujours `False`, et le lot B (`gate run-tests`, `_DIRECTIVE_TEMPLATE`) reste structurellement invisible sur ce banc, quel que soit le nombre de rejeux — un défaut de méthode du banc, pas du lot B. Nouveau `setup_arm_kit_gov()` : le provisionnement du bras `kit` (init + host sync), PLUS `grimoire standard init .` (profil par défaut `starter` — celui qu'un développeur seul obtient sans `--profile`/`--needs`), PLUS une tâche de board à l'id de la tâche du banc (barres obliques remplacées par des doubles underscores, `_governed_task_id()` — Grimoire refuse `/` dans un `task_id`, `core/standard_generation.py::TASK_ID_PATTERN`) posée directement en `in_progress` et importée dans le Mission Ledger par `grimoire task migrate-standard` (ADR-007, `docs/adr-007-unification-des-taches.md`, Grimoire-kit#587/#588) — seule voie CLI qui préserve un id exact plutôt que d'en dériver un du titre (`grimoire task add` n'expose aucune option `--task-id`). `ARMS` passe de trois à quatre bras (`nu`, `ecc`, `kit`, `kit-gov`), chacun avec son propre `HOME` isolé ; `_run_one`/`_do_dry_run` provisionnent `kit-gov` comme `kit`, et collectent `dispatch_stats`/`kit_test_run_evidence` pour les deux bras gouvernés. Rapport : nouvelle clé `kit_gov_runs` (même détail par run que `kit_runs`, non fusionnée pour ne rien changer à la forme déjà relue par les tests existants), section markdown « Détail par run — bras kit-gov », en-tête « Par tâche » désormais généré depuis `ARMS` (4 colonnes au lieu de 3 codées en dur). Vérifié en isolation (dépôt jetable, non versionné) avant tout rejeu réel : `grimoire standard activation-context` rend la directive complète avec `gate run-tests --task-id <id>`, `_is_governed()` vaut `True`, `active_task_id()` résout cet id — jamais `bootstrap`. Nouveaux tests unitaires sans réseau ni LLM (`tests/unit/test_bench_three_arms.py`) : `_governed_task_id`, ordre des commandes et contenu du board synthétique écrit par `setup_arm_kit_gov` (provisionnement simulé, `_run`/`setup_arm_kit` monkeypatchés), câblage `_run_one` (setup, `dispatch_stats`, `kit_test_run_evidence`), rendu du rapport (`kit_gov_runs`, en-tête à 4 colonnes). Doc `docs/bench-three-arms.md` mise à jour (tableau des bras, section dédiée `kit-gov`).

- feat(standard): gate auto-suffisant — artefacts scaffoldés, messages avec chemin, run-tests intégré (issue #582 lot G1). Mesure d'origine : le banc à trois bras du 2026-09-17 (`docs/bench/diagnostic-surcout-kit-2026-09-17.md`, analyse tour par tour de 21 runs kit-gov) attribue une médiane de 11 tours par run (jusqu'à 22) à la « spéléologie » dans le source installé du kit après un `FAIL missing context_bundle` sans chemin ni remède — 21 runs sur 21 ont ouvert `site-packages/grimoire/`. Cible du lot G : bras gouverné sous 1,5× les tours de Claude nu.
  - Messages de gate avec chemin et remède : `check_evidence_gates` (`src/grimoire/core/agentic_standard.py`) rend, pour chaque `gate.<clé>_missing`, le chemin attendu et une commande shell copiable (racine en absolu), via la nouvelle table unique `src/grimoire/core/standard_checks/gate_remedy.py` (`gate_artifact_relpath`, `remedy_command`, `remedy_for_relpath`, `missing_artifact_message`). `grimoire standard gate check` en mode texte (`src/grimoire/cli/cmd_standard.py`) rend désormais tous les checks (identifiant, chemin, message) et plus seulement `missing <clé>` ; `grimoire standard verify` fait suivre chaque chemin manquant de son remède ; le résumé du hook `Stop` (`src/grimoire/hosts/decisions/gate_summary.py`) relaie ces messages au lieu de la clé nue. Rouge-avant : `FAIL evidence gates for task X` / `missing context_bundle` ; vert-après : `gate.context_bundle_missing (_grimoire-output/context/X/context-bundle.yaml): Artefact de gate manquant : context_bundle — attendu à … ; remède : grimoire standard task scaffold /racine --task-id X`.
  - Nouvelle commande `grimoire standard task scaffold [racine] [--task-id X] [--dry-run]` (`src/grimoire/core/standard_task_scaffold.py`, sous-groupe `task` de `src/grimoire/cli/cmd_standard.py`) : crée, s'ils manquent seulement, les six artefacts par tâche que les gates réclament (`task-envelope.md`, `evidence-pack.md`, `claim-ledger.md`, `acceptance-record.md` selon le profil actif ; `context-bundle.yaml` et `decision-trace.yaml` via `build_context_bundle`/`build_decision_trace`), jamais un fichier présent. Pré-remplissage sans question : identifiant, titre, critères d'acceptation (une ligne `AC-00n … à vérifier` par critère, lus dans le Mission Ledger — ADR-007 — ou à défaut sur le board), profil, état courant, niveau de risque, `HEAD` git (`subprocess.run(..., encoding="utf-8")`), commande de test résolue par `resolve_need("test-runner", …)`, date ; le résumé placeholder du pack de preuve (`- Outcome:` vide) devient un résumé généré (titre + critères). Un squelette frais passe `gate check` jusqu'à `review` sans motif de forme. Tâche inconnue du ledger et du board → refus (`grimoire task add` d'abord). `--dry-run` n'écrit ni fichier ni événement ; `task.scaffolded` est journalisé sinon.
  - Le hook `SessionStart` (`src/grimoire/hosts/decisions/activation.py::_scaffold_active_task`, appelé par `decide_activation` quand `_is_governed` — lot A — reconnaît le projet) scaffolde la tâche active, silencieusement (seul `detail["scaffolded"]` le dit) et de façon idempotente ; best-effort, jamais une session cassée. Chemin « tout existe déjà » : six `stat`, sans YAML ni ledger — mesuré 0,18 ms en médiane (30 itérations, `scaffold_task_artifacts` no-op), `decide_activation` complet 2,6 ms ; borné à 100 ms par `tests/unit/test_hosts.py::test_session_start_scaffold_noop_stays_under_budget` et `tests/unit/core/test_standard_task_scaffold.py::test_scaffold_noop_stays_under_the_session_start_budget`. La directive d'activation (`src/grimoire/core/claude_activation.py`, étape 1) nomme le scaffold.
  - `gate check --strict` exécute lui-même `gate run-tests` (`src/grimoire/core/standard_checks/gate_test_run.py::ensure_fresh_test_run`) quand l'état de la tâche doit une preuve d'exécution (`in_progress`, `review`, `accepted`, `released`), qu'une commande de test est résolue et qu'aucun run vert et frais (`tree_fingerprint`, lot B) n'est enregistré, puis évalue comme avant ; le JSON porte `test_run` (`ran`, `reason`, `command`, `ok`, `exit_code`, `path`). `--no-run` restaure l'ancien comportement. Nouveau check `acceptance.test_run_failed` (`src/grimoire/core/standard_checks/controls.py::_verify_recorded_test_run_is_green`, déclaré dans `registry.py`) : un run enregistré, frais et rouge est une erreur — dans `gate check` comme dans le hook `Stop` (qui ne relance jamais de tests) ; un run rouge périmé ne l'est pas. Les identifiants `gate.task_board_missing`, `gate.task_envelope_missing`, `gate.context_bundle_missing`, `gate.memory_policy_missing`, émis depuis toujours mais jamais déclarés, sont ajoutés au registre.
  - Docs : `docs/cli-reference.md` (sous-groupe `task`, `gate check --no-run`, `gate run-tests`), `docs/standard/integration.md` (section « Gate auto-suffisant : chemin, remède, scaffold, tests intégrés »).

- feat(hosts): les hooks alimentent le pack de preuves depuis les actions observées (issue #582 lot G2). Le rapport du banc à trois bras (`_scratch/bench-f/analyse-tours-kit-gov.md`, 21 runs) montre l'agent recopiant systématiquement, à la main, dans `evidence-pack.md`, ce que le hook `PostToolUse` venait déjà de lui rappeler (« Écriture enregistrée… ») — un coût de tours pour une information que le hook connaît.
  - Nouveau module `src/grimoire/core/standard_checks/evidence_journal.py` : `append_evidence_event` (append-only, `encoding="utf-8"`) écrit dans `_grimoire-output/.runs/evidence/<task_id>/evidence-log.jsonl` (corrigé en relecture de PR — voir l'entrée `fix(hosts)` ci-dessous, initialement sous `EVIDENCE_DIR`) ; `read_evidence_log` le relit ligne à ligne, garde fermée — un fichier absent ou dont aucune ligne ne parse en JSON valide se lit comme `[]` (« rien observé »), sans jamais fabriquer de preuve ni faire remonter d'erreur ; une ligne corrompue au milieu n'invalide pas ses voisines. `build_bash_event`/`build_file_write_event` classent une commande Bash en `test_run` (motif `pytest|npm test|cargo test|go test|ctest|mvn … test|gradlew? test`) ou `bash` — un motif de commande, pas un appel à `resolve_need` (trop coûteux à chaque outil de chaque tour, voir le lot G3 pour l'extension de `resolve_need` lui-même). `render_observed_inventory`/`regenerate_observed_inventory_section` projettent le journal en section `## Inventaire observé` dans `evidence-pack.md`, délimitée par des marqueurs HTML (`<!-- grimoire:observed-inventory:start/end -->`), insérée sous la section manuelle « Evidence inventory » qui reste éditable, régénérée en place (jamais dupliquée) à chaque appel.
  - Le hook `PostToolUse` (`src/grimoire/hosts/decisions/evidence_trace.py::_record_observed_actions`) consigne, pour un projet enrôlé au standard : chaque commande Bash exécutée (tronquée à 240 caractères, code de sortie s'il est connu dans `tool_response`), chaque cible d'un outil d'écriture/édition, chaque run de test reconnu. Best-effort (`except Exception: return`), jamais au prix du hook. Mesuré (`tests/unit/test_hosts.py::test_post_tool_use_journal_write_stays_under_the_30ms_budget`, 30 itérations) : 0,31 ms en médiane, 0,40 ms au 95e percentile — budget de garde 30 ms.
  - `check_evidence_gates` (`src/grimoire/core/agentic_standard.py`) projette le journal (écrit la section observée) avant d'évaluer `_verify_evidence_pack`, pour les états `review`/`accepted`/`released` — seul appelant à le faire : `_verify_evidence_pack` (`src/grimoire/core/standard_checks/verifiers.py`, partagée avec `grimoire standard verify`) reste strictement lecture seule et se contente de lire `has_observed_inventory`, parce qu'elle sert aussi `grimoire_standard_verify`/`grimoire_standard_audit` côté MCP (`readOnlyHint`, `tests/unit/mcp/test_server.py::TestReadOnlyToolsWriteNothing`) — un premier essai qui projetait depuis `_verify_evidence_pack` elle-même faisait échouer ce test en écrivant `evidence-pack.md` sous un outil annoncé lecture seule. Le check `evidence.inventory_placeholder` n'est plus émis dès qu'au moins une action a été observée, même si la section manuelle « Evidence inventory » reste à l'état de gabarit ; reste un avertissement, jamais une erreur (transition douce, comme le reste des constats de contenu de cette fonction — voir `tests/test_agentic_standard.py::test_gate_check_surfaces_an_unproven_passed_criterion_without_failing`, qui fixe cette règle et qu'une première version de ce lot faisait régresser en promouvant le check en erreur pour les profils stricts).
  - Rouge-avant / vert-après (`tests/unit/core/test_evidence_journal.py::test_governed_gate_flags_empty_inventory_then_accepts_the_observed_log`) : avant ce lot, le module `evidence_journal` n'existe pas (`ModuleNotFoundError` à la collecte des tests) ; après, `check_evidence_gates(..., target_state="review")` sur une tâche scaffoldée sans dépôt git (donc sans ligne d'inventaire manuelle) rapporte `evidence.inventory_placeholder` tant qu'aucune action n'a été observée, puis cesse de le rapporter dès un seul `append_evidence_event`.
  - Tests : `tests/unit/core/test_evidence_journal.py` (garde fermée, classification, idempotence de la projection, rouge-avant/vert-après), `tests/unit/test_hosts.py` (écriture par le hook, budget de latence, silence sur un projet non enrôlé).

- fix(hosts): déplace le journal de preuve observé (lot G2 ci-dessus) sous `_grimoire-output/.runs/`. Relecture de PR #598 : `evidence-log.jsonl` vivait sous `EVIDENCE_DIR`, un dossier suivi par git dans tout projet gouverné (la Forge y commite `evidence-pack.md`) — un fichier append-only qui grossit à chaque appel d'outil aurait pollué `git status` et les commits de chaque projet. Déplacé sous `_grimoire-output/.runs/evidence/<task_id>/evidence-log.jsonl` (`evidence_log_relpath`, `src/grimoire/core/standard_checks/evidence_journal.py`) ; la projection dans `evidence-pack.md` (la preuve versionnée) ne bouge pas.
  - Nouvelle constante `RUNS_DIR` et nouvelle fonction `ensure_grimoire_gitignore` (`src/grimoire/core/standard_generation.py`), même marqueur/contenu (`# --- Grimoire Kit ---`, `_grimoire-output/.runs/`) que `ProjectScaffolder._plan_gitignore` (`src/grimoire/core/scaffold.py`), qui délègue désormais à cette fonction partagée plutôt que de dupliquer le texte.
  - `grimoire standard init` seul (`setup_standard_profile`, sans passer par le scaffold complet de `grimoire init`) n'écrivait jamais la ligne `.gitignore` pour `_grimoire-output/.runs/` — un vrai trou, comblé : `setup_standard_profile` appelle désormais `ensure_grimoire_gitignore` (best-effort, jamais en `--dry-run`).
  - Tests : `tests/unit/core/test_evidence_journal.py` (chemin sous `RUNS_DIR`, création/idempotence/préservation du contenu existant de `ensure_grimoire_gitignore`, `setup_standard_profile` seul pose la ligne, aucune écriture en `--dry-run`).

- fix(missions): la migration `task-board.yaml` -> Mission Ledger (ADR-007) ne remplace plus priorité, rôles et référence de remédiation d'une tâche par des défauts, et ne jette plus les champs de board qu'elle ne connaît pas encore. Défaut réel constaté le 2026-09-17 sur la Forge (kit 3.55.0) après `grimoire up` → `migrate-standard` : `git show 5de804a^:_grimoire/standard/task-board.yaml` (avant) contre `git show 5de804a:_grimoire/standard/task-board.yaml` (après) montre, pour les 10 tâches migrées, `priority: "high"` écrasé en `medium`, `agent_roles: [orchestrator, ...]` réduit à `[implementation]`, et `remediation_ref` disparu dès que le statut d'origine n'était pas `blocked`. Cause : `_import_one_task` (`src/grimoire/missions/task_unification.py`) ne transmettait ni `priority`, ni `agent_roles`, ni `remediation_ref` à `MissionLedger.create_task` — et `MissionTask` (`src/grimoire/missions/schemas.py`) n'avait nulle part où les ranger, ce qui forçait `_task_entry` (`src/grimoire/missions/board.py`) à toujours (re)dériver `priority` de `risk_profile` (défaut `medium`) et `agent_roles` de `type` (défaut `[implementation]`) — et à ne poser `remediation_ref` que pour une carte `blocked`. Tout champ de board que ce schéma ne modélise pas (ex. `labels`) était silencieusement jeté par le même chemin, faute de tout endroit où le ranger.
  - `MissionTask` gagne trois champs optionnels (`priority: str = ""`, `agent_roles: tuple[str, ...] = ()`, `remediation_ref: str = ""`) et un fourre-tout `extra: dict[str, Any]` pour toute clé de board non encore modélisée — compatibilité ascendante garantie : un événement `task.created` du ledger sans ces clés (tout l'historique existant) retombe sur les défauts actuels via `MissionTask.from_dict`.
  - `MissionLedger.create_task` (`src/grimoire/missions/ledger.py`) accepte ces quatre nouveaux paramètres, tous par défaut à leur valeur neutre.
  - `_import_one_task` (`task_unification.py`) transmet désormais `priority`/`agent_roles`/`remediation_ref` lus tels quels sur l'entrée de board, et collecte dans `extra` toute clé absente de `_KNOWN_BOARD_KEYS` (les clés déjà portées par un champ dédié, plus celles que `board.py` recalcule à chaque projection — `context_bundle_ref`, `decision_trace_ref`, `evidence_pack_ref`, `verifiability`, `blockers` — pour ne jamais les désynchroniser du ledger qui les régénère).
  - `_task_entry` (`board.py`) préfère désormais `task.priority`/`task.agent_roles`/`task.remediation_ref` quand ils sont posés, ne retombant sur la dérivation historique (`risk_profile`/`type`/défaut `blocked` seulement) que pour une tâche créée directement dans le ledger sans ces champs — les deux tests déjà existants sur cette dérivation (`tests/unit/test_missions_board.py`) restent verts sans modification. Toute clé de `task.extra` est reportée par `entry.setdefault(...)`, donc jamais prioritaire sur un champ déjà posé par la projection.
  - Tests rouge-avant/vert-après (`tests/unit/missions/test_task_unification.py`) : `test_priority_roles_remediation_ref_and_unknown_fields_survive_migration` reproduit exactement le board de la Forge (priorité `high`, deux `agent_roles`, `remediation_ref`, un champ `labels` inconnu, statut `accepted`) et compare le board reprojeté champ à champ à l'original (seul `verifiability` est un écart admis) — rouge avant le correctif (`priority == 'medium'` au lieu de `'high'`, vérifié en stashant les quatre fichiers source touchés puis en rejouant le test), vert après. `test_task_list_shows_the_original_status_after_migration` couvre le critère « `grimoire task list` montre l'état d'origine » (ledger `closed`, colonne de board `accepted`). `test_repair_sequence_restore_then_remigrate_matches_original_board` prouve la séquence de réparation d'un projet déjà migré à perte : migration simulée avec l'ancien comportement (`_old_lossy_import_one_task`, copie figée du code pré-correctif) → `restore_task_unification` sur l'instantané de cette migration lossy (board et ledger reviennent bit à bit à l'état d'avant migration, `events.jsonl` redevient absent) → remigration avec le correctif → board identique à l'original.
  - **Réparation d'un projet déjà migré à perte (ex. la Forge)** : chaque migration prend un instantané avant d'écrire (`_snapshot`, `SNAPSHOT_ROOT = "_grimoire-output/.migrations"`) et le rapport de migration porte son horodatage (`stamp`). Séquence : mettre à niveau vers la version du kit qui porte ce correctif, puis `grimoire task migrate-standard <racine> --restore <stamp>` (l'horodatage du run lossy, visible dans le rapport JSON d'origine ou dans `_grimoire-output/.migrations/<stamp>-tasks-unification/manifest.json`) pour ramener `task-board.yaml` et le Mission Ledger à leur état d'avant cette migration, puis rejouer `grimoire task migrate-standard <racine>` (sans `--restore`) pour remigrer avec le correctif. `restore_task_unification` existait déjà (`--restore`, lot 4.1) ; ce correctif ne change que ce que la remigration produit ensuite.
- fix(upgrade): le nœud `verify` lit désormais le manifeste réellement écrit par `backup`, jamais un chemin recalculé. Repro réelle, 2026-09-17 (homelab, kit 3.55.0), via `POST /api/projects/update` (cockpit) : `_archive/<date>-pre-<version>/` contenait déjà un `grimoire-state.tar.gz` fait à la main avant que le flow ne tourne. `backup_project` (`src/grimoire/tools/project_upgrade.py::backup_project`) a, comme documenté, poussé sa propre sauvegarde sur le suffixe `-2` (`grimoire-state-2.tar.gz`, `memory-manifest-sha256-2.txt`), mais `src/grimoire/cli/cmd_upgrade_flow.py` — dans `upgrade_flow_verify` (commande `verify`), `_check_verify` (acceptance `check verify`) et `_node_handlers()._verify` (nœud `verify` de `run`) — recalculait le chemin du manifeste depuis `archive_root(root) / "memory-manifest-sha256.txt"` (nom canonique fixe), un fichier que `backup` n'avait jamais écrit ce run-là : `verify` refusait avec « manifeste introuvable [...] le nœud backup a-t-il tourné ? » alors qu'`apply` avait réussi et que le projet était réellement mis à niveau. Le rapport JSON d'un run en échec (`_fail_run`) ne portait en plus jamais `backup_path` — `null` côté cockpit même quand `backup` avait réussi juste avant le nœud fautif ; seul le chemin spécial de refus d'`apply` le portait.
  - Nouvelle fonction `latest_backup_manifest` (`src/grimoire/tools/project_upgrade.py`) : résout le manifeste le plus récemment écrit dans l'archive du jour (glob + mtime) au lieu de deviner un nom fixe — utilisée par `upgrade_flow_verify` et `_check_verify`.
  - `_node_handlers()` (`cmd_upgrade_flow.py`) prend désormais `node_outputs` en paramètre : `_verify` lit le chemin littéral que le nœud `backup` a rapporté ce run (`node_outputs["backup"]["manifest"]`), avec `latest_backup_manifest` en repli seulement si ce détail est absent.
  - `_fail_run` porte désormais `backup_path` (même clé/forme que le chemin de refus d'`apply`, issue #510) : les deux appels de la boucle de `run` transmettent `(node_outputs.get("backup") or {}).get("tarball")`.
  - Tests rouge-avant/vert-après : `tests/unit/test_project_upgrade.py::test_upgrade_flow_run_verifies_against_a_suffixed_manifest_when_backup_is_pushed_to_dash_2` (archive préexistante → backup suffixé → verify doit réussir jusqu'au checkpoint `destructive`, `backup_path` doit pointer sur le tarball suffixé) et `tests/unit/cli/test_cmd_upgrade_flow_run.py::test_a_node_failure_after_backup_still_reports_the_backup_path` (un nœud après `backup` qui échoue doit quand même rapporter `backup_path`).
  - Pas de `--repair`/reprise dédiée dans le code existant pour rejouer `verify` seul sur un run `upgraded-but-failed` : le rejeu complet (`grimoire upgrade-flow run` relancé depuis le début) reste la voie testée et prouvée idempotente (`test_upgrade_flow_run_end_to_end`, second appel en fin de test).
- fix(hosts): la garde « force push » ne confond plus `-f` dans un nom de branche avec l'option `-f`. Défaut réel constaté le 2026-09-17 : `git push -u origin docs/rejeu-lot-f-2026-09-17` a été refusé par le hook `PreToolUse` comme une poussée forcée, forçant un renommage de branche. Cause : dans `_DESTRUCTIVE_PATTERNS` (`src/grimoire/hosts/decisions/tool_facts.py::classify_tool`), le motif `\bgit\s+push\b.*(--force|-f)\b` finit sur un `\b` — qui n'atteste qu'une transition mot/non-mot, et `-` compte comme non-mot au même titre qu'un espace — donc `-f-` au milieu d'un nom de branche se lit comme l'option `-f`. Même défaut sur `--hard` (`git reset --hard`) et sur le point final de `git checkout -- .` (`git checkout -- .gitignore` se lisait comme un abandon complet de l'arbre de travail).
  - Les trois motifs ancrent désormais sur `(?=\s|$)` (fin de jeton shell réelle — espace ou fin de chaîne) plutôt que sur `\b`. Le motif de poussée forcée reconnaît en plus `--force-with-lease[=réf]`, `--force-if-includes`, et les options courtes combinées contenant `f` (`-uf`, `-fu`) via `-[a-z]*f[a-z]*`, chacune bornée en jeton entier.
  - Tests rouge-avant/vert-après (`tests/unit/test_hosts.py`) : `test_force_push_detection_matches_the_flag_not_a_branch_name` (`git push -u origin docs/rejeu-lot-f-2026-09-17` et `git push origin feature-force` ne sont pas une poussée forcée ; `--force`, `-f`, `--force-with-lease[=réf]`, `--force-if-includes`, `-uf`, `-fu` en sont une), `test_hard_reset_detection_requires_the_flag_to_stand_alone`, `test_checkout_discard_all_requires_the_dot_to_stand_alone`.

- fix(ci): le job « Framework Tools Tests (pytest, windows-latest) » (`.github/workflows/ci-validate.yml`) stagnait ~9 minutes sans progression pytest à chaque commit de `main` depuis au moins le 14 septembre 2026 (`timeout-minutes: 15`, annulé aléatoirement — PR #590, #591), toujours au même point de la collecte (juste après `tests/test_cmd_init.py`), absent sur `ubuntu-latest`/`macos-latest`. Cause confirmée par les journaux d'un run réel (`gh api .../actions/jobs/<id>/logs`, trou de 09:13:17 à 09:22:31 entre 23 % et 25 % de la collecte) recoupés avec l'ordre de collecte local : `TestInitCLI`/`TestInitNoCockpit` (`tests/test_cmd_init.py`) invoquent `-y init` sans `--backend` explicite sur une vingtaine de cas qui n'ont rien à voir avec la détection mémoire (dry-run, archétypes, sortie JSON, `.gitignore`…) — ce qui déclenche pour de vrai `detect_memory_backend()` (issue #496), jusqu'à six sondes HTTP/TCP non mockées vers `localhost` (Weaviate :8080, Qdrant :6333, Ollama :11434, jusqu'à 2 s de timeout chacune). Un port loopback fermé refuse instantanément sous Linux ; pas sous le runner Windows hébergé, où chaque sonde paie son timeout en entier — exactement le risque que `tests/conftest.py::_init_real_project` documentait déjà pour la fixture `real_project` (issue #493) sans que `TestInitCLI` en hérite. Correctif : nouvelle fixture `_stub_unreachable_memory_services` (autouse, portée aux deux classes concernées) qui neutralise `_is_weaviate_reachable`/`_is_qdrant_reachable`/`_is_ollama_reachable` par défaut — les tests qui testent réellement la détection (`TestDetectMemoryBackend` et consorts) gardent la main via leur propre `patch(...)`, plus spécifique, qui l'emporte. Extraction de `_is_ollama_reachable()` (`src/grimoire/cli/cmd_init.py`), jusqu'ici un `_http_ok(...)` en ligne dans `detect_memory_backend()` sans rien à patcher isolément, sur le même patron que les deux fonctions sœurs. `--durations=25` ajouté au job pytest des deux OS pour garder cette visibilité à l'avenir. Preuve : job Windows 13m58s-14m24s avant sur `main` (runs 2026-09-15/17), confirmé à 13m48s sur cette branche avant correctif (`--durations=25 -v` : chaque test `TestInitCLI`/`TestInitNoCockpit` mesuré à 24,13-25,23s) → **3m43s** après correctif (même branche, même runner) ; `ubuntu-latest` inchangé (1m21s, le trou n'existait déjà pas côté Linux).

## [3.55.0] - 2026-09-17

- fix(cockpit): la migration des tâches d'un board du standard vers le Mission Ledger s'applique désormais à n'importe quel projet du registre, pas seulement au projet de lancement du cockpit — issue #559 suite, #560. `POST /api/workspace/tasks/migrate-standard` (`src/grimoire/tools/workspace_routes.py::_task_migrate_standard`) et `POST /api/projects/update`/les décisions de proposition (#507, #490) ouvrent déjà cette porte pour n'importe quel projet du registre : migrer les tâches d'un projet qu'on pilote depuis la flotte (« jongler entre mes projets ») est le même genre de geste explicite et ponctuel, pas une écriture continue comme réclamer une tâche ou écrire un fichier — celles-là restent `_HOME_SLUG` seulement. Nouvelle fonction `is_registry_scoped_write` (`workspace_routes.py`), même patron que `is_proposal_decision` (#490) : le point d'entrée que `cmd_cockpit.py::_CockpitHandler.do_POST` interroge, en plus de `is_proposal_decision`, pour ouvrir la garde générale `_HOME_SLUG` sur cette seule route nommée. La résolution du projet cible reste celle déjà partagée par toutes les écritures de la vue de travail (`_resolve_project_path(self._query_slug())`, le registre `~/.grimoire`, jamais un chemin libre fourni par le client) : un slug inconnu ou hors registre reste un 404 explicite, un slug malformé ne correspond à aucune entrée du registre et reste donc refusé de la même façon. Côté client, `web/workspace/api.js::migrateStandardTasks` appelle désormais `postOpen` (comme `updateProject`/`proposalAction`) au lieu de `post`, qui aurait refusé l'appel sur le verrou général `readOnly` avant même d'atteindre le serveur ; le projet ciblé reste celui affiché, porté par la mécanique `withProject` existante, sans paramètre `project` dupliqué (piège #507). `web/workspace/spaces/executer.js` : le bouton « Migrer les tâches » n'est plus désactivé par `ctx.host.readOnly`, même précédent que les boutons Accepter/Refuser d'une proposition dans Piloter. Tests rouge-avant/vert-après (`tests/unit/test_workspace_routes.py`) : `ImportError` sur `is_registry_scoped_write` avant le correctif (la fonction n'existait pas) ; nouveaux cas verts après — projet du registre non home (`test_post_migrate_standard_fonctionne_sur_un_projet_du_registre_hors_lancement`, migration réellement effective, pas un 200 muet), slug inconnu (`test_post_migrate_standard_sur_un_slug_inconnu_est_un_404`), slug malformé (`test_post_migrate_standard_sur_un_slug_malforme_reste_refuse`), reconnaissance étroite de la dérogation (`test_is_registry_scoped_write_ne_reconnait_que_migrate_standard`, six cas). Les deux tests déjà paramétrés sur `POST_ROUTES` (`test_le_cockpit_refuse_les_ecritures_sur_un_projet_qu_il_ne_lance_pas`, `test_le_cockpit_refuse_toujours_l_autre_projet_meme_avec_un_lancement_direct`) excluent désormais explicitement cette route (`_REGISTRY_SCOPED_POST_ROUTES`), sans affaiblir la garde pour le reste de la table — même patron que #507 pour les décisions de proposition. `docs/cockpit-coverage-matrix.md` (ligne `POST /api/workspace/tasks/migrate-standard`) et `scripts/cockpit-coverage.py --check` synchronisés.
- feat(standard): une ligne `acceptance-record.md` marquée « passé » ne peut plus rester une déclaration de texte libre sur un projet qui a une commande de test connue — phase 2bis « Cœur » lot B (issue #582), diagnostic du surcoût kit du banc à trois bras (`docs/bench/diagnostic-surcout-kit-2026-09-17.md` §2) : `_verify_acceptance_record` (`src/grimoire/core/standard_checks/controls.py`) et `check_evidence_gates` (`src/grimoire/core/agentic_standard.py`) acceptaient une cellule « preuve » écrite par l'agent lui-même, jamais confrontée à une exécution réelle — constaté sur le banc (pass^k du bras kit 85 % contre 90 % nu/ecc, deux runs `go/palindrome-products`/`javascript/transpose` clos `completed` alors que les tests cachés du harnais les contredisaient). Réutilise le mécanisme d'acceptance déjà exécuté ailleurs dans le kit (`AcceptanceEvidence(kind="test")`, `src/grimoire/flows/schemas.py`/`dispatch_executor.py`, issue #428) plutôt que d'en dupliquer un : `_run_checks` (`src/grimoire/missions/dispatch.py`, importé localement pour ne pas alourdir l'import de `agentic_standard` du coût de `providers.audit`, ~60 ms mesurés) exécute la commande de test résolue par `grimoire.core.execution_needs.resolve_need("test-runner", …)` (déclaration `needs.commands.test-runner` dans `project-context.yaml`, ou détection par marqueur `pyproject.toml`/`package.json`/`Cargo.toml`/`go.mod`, déjà existante — issue #205). Nouvelle commande `grimoire standard gate run-tests [--task-id]` (`record_acceptance_test_run`, extrait dans son propre module `src/grimoire/core/standard_checks/acceptance_test_run.py` pour respecter le ratchet de taille — `agentic_standard.py` est grandfathered à 1916 lignes, `scripts/check-code-ratchet.py` R2) : exécute cette commande et enregistre le verdict (code de sortie, sortie tronquée) sous `_grimoire-output/evidence/<task-id>/test-run.json`. `_verify_acceptance_record` et `check_evidence_gates` (qui la réutilise désormais pour les états `review`/`accepted`/`released`, pas seulement `standard verify`) lisent ce fichier : une ligne « passé » sans run enregistré et vert lève **`acceptance.passed_without_test_run`** (avertissement — transition douce documentée ci-dessous, jamais un blocage sur cette release) ; un projet sans commande de test détectable reste déclaratif comme avant, signalé par **`acceptance.no_test_command_detected`**. **Transition WARN → FAIL** : cette release répond en avertissement quel que soit le profil ; une prochaine release promouvra `acceptance.passed_without_test_run` en erreur bloquante pour les profils `governed`/`production` — laisser le temps aux projets déjà gouvernés d'adopter `gate run-tests` avant que le gate ne les bloque. Tests rouge-avant/vert-après (`tests/unit/core/test_standard_gap_artifacts.py` : import de `record_acceptance_test_run` en échec avant le correctif faute d'exister, quatre nouveaux cas verts après — commande connue/inconnue, run réel vert/rouge ; `tests/test_agentic_standard.py` : `check_evidence_gates` surface le même avertissement sans faire échouer le gate, `gate run-tests` bout-en-bout via CLI). Rejeu sur la Forge (`grimoire standard gate check --task-id bootstrap --strict`, projet réel gouverné) : le gate reste vert, aucune ligne acceptance-record marquée « passé » sans run n'y étant déclarée aujourd'hui — le nouveau signal n'apparaît que lorsqu'une telle ligne existe. **Suite (revue de la PR) : un run enregistré n'était lié à aucun état du code** — un agent pouvait lancer `gate run-tests` tôt puis modifier le code sans jamais relancer les tests, et le gate restait vert sur du code jamais exercé. Nouveau module `src/grimoire/core/standard_checks/tree_fingerprint.py` (`compute_tree_fingerprint`) : dans un dépôt git, sha256 de `git rev-parse HEAD` + `git status --porcelain=v1 -z` + `git diff HEAD` (`_grimoire-output/` exclu par pathspec `:(exclude)`, sous peine d'auto-invalidation à chaque écriture de `test-run.json`) ; hors git (ou si git échoue), sha256 de la liste triée `(chemin, taille, mtime_ns)` de tout fichier hors `_grimoire-output/`, `.venv/`, `node_modules/`, `target/`, `.git/`. `record_acceptance_test_run` calcule l'empreinte juste avant d'exécuter la commande (pas après : la commande elle-même écrit des artefacts non suivis — `.pytest_cache`, `.coverage` — qui périmeraient le run dès son écriture si l'empreinte était prise après coup) et l'enregistre dans `test-run.json` (`tree_fingerprint`). `_verify_acceptance_test_execution` (`controls.py`) recalcule et compare à la vérification : un run vert dont l'empreinte diverge (ou est absente — garde fermée, un ancien format ou un fichier altéré compte comme périmé) lève **`acceptance.test_run_stale`** (avertissement, même transition douce WARN → FAIL que les deux autres constats de ce lot), message « run antérieur aux dernières modifications, relancez `gate run-tests` ». Subprocess git en `encoding="utf-8"`. Tests rouge-avant/vert-après (`tests/unit/core/test_standard_gap_artifacts.py::test_modifying_a_source_file_after_a_green_run_makes_it_stale` : le module n'existait pas avant, faisait échouer l'assertion par absence du constat ; vert après) et de non-régression (`test_modifying_only_grimoire_output_does_not_make_a_run_stale` : le mécanisme n'invalide jamais son propre run) ; nouvelle suite unitaire dédiée `tests/unit/core/test_tree_fingerprint.py` (9 cas : stabilité, détection d'un fichier suivi modifié, d'un fichier non suivi ajouté, exclusion de `_grimoire-output`/`.venv`/`node_modules`/`target`, repli filesystem sans commit git) — dépôts git jetables construits en `tmp_path`, jamais le dépôt ambiant. **Suite (deuxième revue de `tree_fingerprint.py`) — deux défauts corrigés avant fusion.** (1) L'empreinte était calculée AVANT le run : sur un projet git sans `.gitignore` adapté, un cache non suivi que la commande de test crée elle-même (`.pytest_cache/`, `__pycache__/`, `.ruff_cache/`, `.mypy_cache/`, `.hypothesis/`, `.coverage`) apparaissait dans `git status --porcelain` dès la vérification suivante, périmant le run à peine enregistré. Calculée désormais APRÈS le run (l'état de référence est celui que les tests laissent derrière eux) et ces caches ajoutés à `_EXCLUDED_DIRS` des deux modes, avec le pathspec git correspondant — vérifié empiriquement que `:(exclude)**/<nom>/**` seul, sans la magie `glob`, ne filtre qu'une occurrence à la racine, jamais en profondeur ; la forme retenue combine `:(exclude,glob)**/<nom>` (fichier plat, ex. `.coverage`) et `:(exclude,glob)**/<nom>/**` (dossier, à toute profondeur). (2) En mode git, une entrée `??` (fichier non suivi) n'est représentée que par son chemin : retoucher son contenu après le run ne changeait jamais l'empreinte — le cas courant d'un agent qui crée un fichier puis le retouche. Chaque entrée `??` du status ajoute désormais au condensé `(chemin, taille, mtime_ns)` du fichier, ou de chaque fichier sous le répertoire si l'entrée est un dossier (mêmes exclusions de cache appliquées à la récursion). Tests rouge-avant/vert-après : `test_a_cache_created_by_the_test_run_itself_does_not_make_it_stale` (dépôt git jetable, commande de test créant `.pytest_cache/x` non ignoré — rouge avant : `acceptance.test_run_stale` apparaissait à tort) et `test_editing_an_untracked_file_created_before_the_run_makes_it_stale` (fichier non suivi créé avant le run, retouché après — rouge avant : aucun `stale` détecté), tous deux dans `tests/unit/core/test_standard_gap_artifacts.py` ; six nouveaux cas unitaires dans `tests/unit/core/test_tree_fingerprint.py` (caches ignorés à la racine et sous un dossier déjà suivi, en mode git et filesystem ; fichier non suivi retouché, à la racine et sous un dossier lui-même non suivi). **Suite (revue de la PR, point 2) : la directive gouvernée mandate désormais `gate run-tests` avant `gate check`** — `_DIRECTIVE_TEMPLATE` (`src/grimoire/core/claude_activation.py`, étape 3) ajoute une ligne `grimoire standard gate run-tests --task-id {task_id}` juste avant `gate check --strict`, sans toucher au reste du texte ; le projet non gouverné (contexte court du lot A) est inchangé. Directive gouvernée 761 → 824 caractères (+63, un seul ajout de ligne) ; contexte `SessionStart` complet sur la Forge 1248 → 1311 caractères (même delta, lot C déjà appliqué). Tests : `test_default_directive_matches_preregistered_mechanism` (nouvel ancrage `gate run-tests --task-id bootstrap`) et nouveau `test_gate_run_tests_precedes_gate_check_in_the_directive` (`tests/unit/test_claude_activation.py`) vérifient l'ordre ; suite `test_hosts.py`/`test_claude_activation.py` complète revérifiée verte (l'assertion relative `short_len < governed_len / 2` de `test_an_ungoverned_project_gets_the_short_notice_instead` absorbe la croissance sans modification).
- perf(hosts): la persona d'entrée du `SessionStart` n'impose plus la lecture intégrale du fichier de l'agent — phase 2bis « Cœur » lot C (issue #582), diagnostic du surcoût kit du banc à trois bras (`docs/bench/diagnostic-surcout-kit-2026-09-17.md` §1.2.1) : `entry_persona_context` (`src/grimoire/hosts/decisions/activation.py`) mandatait « lis `{definition_ref}` en entier avant de répondre » sur *toute* session, mesuré à ~2 800 tokens pour la persona `concierge` livrée (11 Ko) — sans objet pour une session batch (`claude -p`) qui a déjà reçu sa tâche entière et n'a personne à trier. `HookInput` ne porte aujourd'hui aucun signal interactif/batch fiable (pas de TTY, rien qui survit dans le sous-processus du hook) pour réserver le mandat complet à l'interactif gouverné ; option retenue documentée dans le code : un résumé d'une ligne (nom, rôle, frontière d'outils) s'applique désormais uniformément, avec renvoi conditionnel — « lis le fichier complet si la demande est ambiguë ou si tu dois trier » — au lieu du mandat inconditionnel. Mesuré sur la Forge (projet gouverné, `grimoire.hosts.runtime` avec `--project-root`) : contexte `SessionStart` 1558 → 1248 caractères (-310, même delta mesuré sur un projet non gouverné jetable créé avec `grimoire init --backend local --lite`, 903 → 593). Test rouge-avant/vert-après (`tests/unit/test_hosts.py::test_the_entry_persona_is_a_one_line_summary_not_a_full_read_mandate`) : la chaîne `"en entier avant de répondre"` était présente avant ce correctif, absente après, remplacée par un renvoi conditionnel ; suite `test_hosts.py` complète revérifiée verte (aucun test existant ne dépendait du libellé du mandat complet).
- fix(hosts): la directive complète du `SessionStart` (enveloppe de tâche, pack de preuve, mandat `gate check --strict` + `verify .`) n'est plus injectée sur un projet qui n'a pas réellement adopté le standard agentique — phase 2bis « Cœur » lot A (issue #582), issues #551 #552, diagnostic du surcoût kit du banc à trois bras (`docs/bench/diagnostic-surcout-kit-2026-09-17.md` §1.2/§3, PR #581) : `decide_activation` (`src/grimoire/hosts/decisions/activation.py`) envoyait cette directive à *toute* session, y compris sur un dépôt jamais passé par `grimoire standard init` — reproduit : `grimoire standard verify .` y échoue alors sur 7 artefacts qu'aucune commande n'a jamais demandé de créer, et la session part faire des tours de « gouvernance » sur du vide (mesuré : ~90-95 % du surcoût de coût du bras kit, ratio de tours ×3,17 face au bras nu). Nouvelle fonction `_is_governed(project_root)` : un projet est gouverné quand `_grimoire/standard/` existe ET porte un signal fort (`task-board.yaml`) ou, à défaut, un profil enregistré (`_read_manifest_profile`, `agentic_standard.py`) qui ne ressemble pas à un simple répertoire minimal abandonné dans un terrain de jeu (`_looks_like_a_playground`, déjà utilisé par `grimoire init --lite`, issue #552 lot 2.6) — un `task-board.yaml` réel reste un signal suffisant à lui seul, jamais annulé par l'heuristique de terrain de jeu. Un projet non gouverné reçoit deux lignes (`[Grimoire — projet non gouverné]`) au lieu du mandat complet ; le comportement d'un projet gouverné reste bit-à-bit identique (mesuré en test : ~760 caractères inchangés contre ~250 pour l'avis court). `Decision.detail` gagne une clé `governed: bool`, terrain d'observation pour la métrique 3b (lot 2.5) sans rien y brancher ici. Tests (`tests/unit/test_hosts.py`) : cas rouge-avant sur un `tmp_path` vierge (aucune ligne `[Grimoire Standard — activation]`), non-régression sur un projet réellement gouverné (directive inchangée, ordre persona/rappel/directive), quatre tests existants qui présumaient — à tort, c'est exactement le défaut corrigé — qu'un projet avec seulement des agents personnalisés recevait la directive complète, migrés vers la fixture `governed` ou un board minimal écrit à la main.
- test(bench): garde-fou disque et tokens de cache dans le harnais du banc à trois bras — phase 2bis lot D (issue #582), issues #551 #552. `scripts/bench/three_arms.py` : `disk_guard_ok()` vérifie l'espace libre du workspace (`shutil.disk_usage`, seuil `DISK_GUARD_MIN_FREE_GB = 5.0`) avant chaque run de la boucle principale de `main()` — sous le seuil, la campagne s'arrête proprement (aucun nouveau run lancé) et écrit son rapport partiel avec ce qui a déjà tourné, plutôt que de crasher au milieu d'un run ; `cleanup_build_artifacts()` supprime `target/` (Rust) et `node_modules/` (JS) sous chaque dépôt de tâche après CHAQUE run (pas seulement en fin de campagne), appelée dans `_run_one` juste après `run_hidden_tests`. `RunOutcome` et `RunRecord` gagnent `cache_read_input_tokens`/`cache_creation_input_tokens` (lus depuis `usage` de l'événement `result` du flux `stream-json`, défaut 0 si absents) et `RunRecord` gagne `model_usage` — `RunOutcome.model_usage` était déjà rempli mais jamais recopié dans `results.jsonl`, un oubli documenté par le diagnostic du surcoût. Tests (`tests/unit/test_bench_three_arms.py`, 8 nouveaux cas, aucun appel réseau ni `claude -p`) : extraction des tokens de cache et de `modelUsage` depuis un `subprocess.Popen` simulé, valeurs par défaut à 0 sans ces clés, trajet complet jusqu'au `RunRecord` via `_run_one` avec collaborateurs simulés, nettoyage `target`/`node_modules` (présence et absence), garde disque sous/au-dessus du seuil, arrêt propre de `main()` avec rapport partiel quand le disque est déjà sous le seuil avant le premier run.
- feat(bench): rejouer un sous-ensemble de bras (`--arms`) et tracer les runs de tests du bras kit — phase 2bis lot E, harnais (issue #582). `scripts/bench/three_arms.py` : nouvelle option `--arms nu,ecc,kit` (`parse_arms`, défaut : les trois, repli sur les trois si la valeur est vide/absente, erreur explicite sur un bras inconnu) qui restreint quels bras la boucle principale de `main()` rejoue réellement — `_run_one` n'est plus jamais appelé pour un bras absent de la sélection. Compatible `--resume` : les lignes déjà présentes dans `results.jsonl` pour les bras non sélectionnés restent chargées et comptées dans `build_report`/`render_report_markdown`, avec une mention « bras repris de la campagne du `<date>` » dès qu'un bras a des runs mais n'a pas été rejoué par cette exécution (`carried_over_label`, basé sur le nouveau champ `RunRecord.recorded_at` — absent sur les lignes antérieures à ce lot, repli explicite « date inconnue » plutôt qu'une date inventée). Correctif au passage dans `main()` : le critère d'arrêt statistique (`should_stop_early`) comptait les tâches de TOUS les bras présents dans `records`, y compris ceux simplement repris via `--resume` — avec `--arms kit` et des lignes nu/ecc déjà complètes pour les 20 tâches, il se déclenchait à tort dès la première tâche kit (« les 20 tâches ont été rejouées ») ; désormais réservé aux exécutions qui rejouent bien les trois bras (`selected_arms == ARMS`). Nouvelle option `--label` (texte libre, reprise telle quelle dans `report.md`/`report.json`, y compris via `--report-only --arms --label` pour annoter un rapport régénéré sans rien rejouer). Bras `kit` : `RunRecord.kit_test_run_evidence` (`has_test_run_evidence()`, glob `_grimoire-output/evidence/*/test-run.json` dans le dépôt de tâche en fin de run — peu importe le `--task-id` choisi par l'agent) atteste que `grimoire standard gate run-tests` a tourné (lot B, #585) ; nouvelle section « Détail par run — bras kit » de `report.md` (tours, coût, temps, preuve `test-run.json`) construite par `build_report`. Tests (`tests/unit/test_bench_three_arms.py`, 17 nouveaux cas, aucun appel réseau ni `claude -p`) : `parse_arms` (défaut, valeur unique, ordre canonique indépendant de l'ordre saisi, espaces/doublons tolérés, bras inconnu rejeté, repli argparse via `SystemExit`), `has_test_run_evidence` (présence/absence, `task-id` arbitraire), `carried_over_label` (date la plus ancienne, repli explicite sans `recorded_at`), `build_report` (notes de reprise uniquement pour les bras non rejoués avec des données, absence de note quand `rerun_arms` est `None`, détail par run kit), et un test de bout en bout de `main(["--full", "--resume", "--arms", "kit", ...])` vérifiant qu'aucun run nu/ecc n'est lancé et que le rapport final comptabilise bien les lignes reprises et les nouvelles lignes kit.
- docs(bench): rejeu du banc à trois bras après les lots A/B/C — phase 2bis « Cœur » lot E (issue #582). `docs/bench/rejeu-lot-e-2026-09-17.md` : `scripts/bench/three_arms.py --full --resume --arms kit --seed 551` rejoué sur `main` @ `6d9db6c6` (lots A #584, B #585, C #586, harnais lot E #589), bras `nu`/`ecc` repris tels quels de la campagne du 2026-09-17. Résultat : tours kit/nu ×3,17 → ×1,0 (cible du lot A largement atteinte, temps médian kit désormais sous nu/ecc), coût médian/tâche résolue kit 1,247 $ → 0,416 $ (33 % de son propre niveau d'avant les lots, mais ≈104-112 % du coût `nu` — au-dessus, pas en-dessous, du seuil de 70 % du nu fixé par `docs/plan-2026-q4.md`), succès et pass^k kit strictement identiques à la lettre, tâche par tâche, à la campagne d'avant les lots (90 %/85 %, IC95 % très chevauchants avec nu/ecc). **Constat qui rouvre la mesure du lot B** : les 60 runs `kit` de ce rejeu n'ont jamais produit de `_grimoire-output/evidence/*/test-run.json` (0/60) — `setup_arm_kit()` (`scripts/bench/three_arms.py`) provisionne le bras `kit` avec `grimoire init`/`host sync` mais jamais `grimoire standard init`, donc `_grimoire/standard/` n'existe jamais dans les dépôts de tâche du banc et `_is_governed()` (lot A, `src/grimoire/hosts/decisions/activation.py`) classe le bras `kit` comme non gouverné à chaque run — la directive du lot B (`gate run-tests` avant `gate check`) ne part donc jamais, vérifié en isolation (`grimoire init . --backend local --no-cockpit` dans un dépôt jetable) et confirmé par l'identité bit à bit de `go/palindrome-products` (kit 1/3) et `javascript/transpose` (kit 2/3) — les deux tâches visées par le lot B — entre l'ancien et le nouveau rapport. Nouveau lot F consigné dans `docs/plan-2026-q4.md` (phase 2bis) : fermer cette lacune de méthode (`setup_arm_kit()` doit provisionner un projet réellement enrôlé) avant tout nouveau verdict sur le lot B. Verdict global : critère de sortie de phase (`docs/plan-2026-q4.md` §4) partiellement atteint seulement — tours atteint, coût et succès non atteints au sens strict — phases 3 et 4 restent gelées. `docs/plan-2026-q4.md` (lignes de la table des lots, critère de fait, notices de gel des phases 3/4, registre des risques) et `docs/index.md` mis à jour en conséquence. Données brutes de la campagne (`results.jsonl`, `selection.json`, `report.json`) non poussées, conservées localement (Grimoire-Forge, hors dépôt kit).
- docs(plan): verdict du banc à trois bras (2026-09-17, graine 551, [rapport](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/bench-reports/_grimoire-output/bench-reports/three-arms/2026-09-17/report.md)) consigné dans `docs/plan-2026-q4.md` — issue #551. Critère d'arrêt §3 déclenché : **ARRÊT**, le kit gouverné par défaut coûte ×3,1 le coût médian par tâche résolue de l'hôte nu (1,247 $ vs 0,399 $) et ×3,0 le temps (189 s vs 64 s médian), pour une réussite égale ou légèrement inférieure (90,0 % vs 91,7 %, IC95% chevauchants). §2 mis à jour avec les valeurs mesurées (succès, pass^k, tours, coût/tour par bras). Décision : extras du plan (reçu portable, rejeu, skills, parité première heure — phases 3 et 4) gelés jusqu'à un rejeu du banc où le kit atteint réussite ≥ nu et coût ≤ 70 % du nu ; la plomberie déjà en vol (unification des tâches, lot 4.1/#559) continue, hors du chemin d'exécution mesuré. Nouvelle phase 2bis « Cœur » (issue #582) insérée entre les phases 2 et 3, avec les lots A (classer la tâche avant le mandat de gouvernance), B (gates reliées à une exécution réelle), C (persona d'entrée à la demande), D (harnais : garde-fous et mesures manquantes) et E (rejeu du banc comme critère de fait) — d'après `docs/bench/diagnostic-surcout-kit-2026-09-17.md` (PR #581). Décision hors-lot consignée : la cascade de dispatch n'est jamais sollicitée sur le chemin `claude -p` (`dispatch_stats.overall.total == 0` sur 60/60 runs kit) — mesure à étendre à un bras « flow du kit » séparé. Correction du lot 5.5 (§6) : le gate `grimoire-skill-analyzer` n'existe que côté Forge, le kit doit créer son propre `skills-ref validate`, pas le réutiliser. §7 : deux risques ajoutés (gouvernance non conditionnelle, preuve déclarative = garde qui échoue ouvert).
- docs(bench): diagnostic du surcoût du bras kit sur le banc à trois bras du 2026-09-17 — issues #551 #552, `docs/bench/diagnostic-surcout-kit-2026-09-17.md`. Le rapport (`bench-reports@c43483ac`) déclenche le critère d'arrêt du plan produit 2026-Q4 (kit ×3,1 le coût et ×3,0 le temps de Claude Code nu pour un succès égal ou inférieur) : ce document attribue le surcoût, sur les agrégats de `results.jsonl` et une reproduction mécanique du chemin `grimoire init --backend local --no-cockpit` + `host sync --host claude` (pas les transcripts bruts de la campagne, disparus au nettoyage disque — limite documentée en tête du rapport). Constat central : les ratios tours/temps/coût du bras kit sont quasi identiques (×3,17/×3,36/×3,19) alors que son coût moyen par tour n'est pas plus élevé que celui du bras nu — le surcoût est un effet de nombre de tours, pas de contexte plus lourd par tour, pas du temps de setup (hors `wall_seconds` par construction du harnais), pas de la cascade de dispatch (`dispatch_stats.overall.total == 0` sur 60/60 runs kit, jamais invoquée sur le chemin `claude -p`). Cause reproduite : `decide_activation` (`src/grimoire/hosts/decisions/activation.py`) injecte au `SessionStart`, sans aucune condition liée au projet ou à la tâche, une persona d'entrée (lecture forcée de `concierge.md`, 2800 tokens) et la directive du standard (`_DIRECTIVE_TEMPLATE`, `src/grimoire/core/claude_activation.py`) même sur un projet qui n'a jamais initialisé `_grimoire/standard/` — vérifié en conditions réelles sur un dépôt vierge. Preuve du patron « gates verts sans tests » (échecs `go/palindrome-products`, `javascript/transpose`, `terminated_reason: completed`) : `grimoire standard gate check --strict` répond `OK` (exit 0) sur un projet sans aucune gouvernance initialisée (l'état de tâche retombe sur une chaîne vide qui ne déclenche aucune vérification d'artefact) ; `grimoire standard verify` ne vérifie que l'existence et le parsing de fichiers déclaratifs — grep exhaustif de `src/grimoire/core/standard_checks/verifiers.py` (43 fonctions `_verify_*`) : zéro appel `subprocess`/`pytest`/`cargo test`/`go test`/`npm test` ; `_verify_acceptance_record` (`controls.py`) parse un tableau Markdown écrit par l'agent lui-même, jamais confronté à une exécution réelle. Trois lots de cœur proposés (S/M/S) : classer la tâche avant d'imposer le mandat de gouvernance, relier les gates au mécanisme d'exécution déjà existant `AcceptanceRun`/`AcceptanceEvidence(kind="test")` du moteur de flows (`dispatch_executor.py`, issue #428) au lieu du tableau déclaratif, alléger la persona d'entrée sur une session batch — chacun avec sa mesure de preuve sur le même banc (`three_arms.py --full`, graine 551). Aucun code de `src/grimoire` modifié par cette PR (diagnostic en lecture seule).
- feat(missions): moteur de migration `task-board.yaml` -> Mission Ledger — issue #559, lot 4.1 du plan 2026-Q4 (1/3), ADR-007 (`docs/adr-007-unification-des-taches.md`). ADR-005 (2026-08-27) avait déjà décidé que le `MissionLedger` est la source de vérité et `_grimoire/standard/task-board.yaml` sa projection, mais `grimoire standard init` scaffold ce board en copiant le template statique sans jamais ouvrir de ledger (constat #521) : tout projet gouverné a un board sans source, et `workspace_api.tasks_view` (Exécuter, Preuves du cockpit) ne lit que le ledger. Nouveau module `src/grimoire/missions/task_unification.py` : `migrate_standard_tasks()` reconstruit dans le ledger toute tâche du board absente de lui (appariée par `task_id`, rattachée à une mission `MIS-standard-import-001`), en marchant sur `_TASK_TRANSITIONS` (`ledger.py`) pour atteindre l'état cible de chacune des 8 colonnes du board — idempotent (rejouer sur un projet déjà migré n'importe rien de plus) et réversible (`restore_task_unification`, patron snapshot/apply/restore de `grimoire migrate`, manifeste JSON sous `_grimoire-output/.migrations/`). `tasks_unification_status()` expose déjà la divergence board/ledger, terrain du futur check `doctor` (PR 2/3). Nouvelle sous-commande additive `grimoire task migrate-standard [--dry-run] [--restore STAMP]` (`src/grimoire/cli/cmd_task.py`) — aucune commande existante renommée. `MissionTask` gagne un champ `finition: str = ""` (`""`/`maquette`/`peaufine`, validé dans `__post_init__`), propagé dans la projection board (`missions/board.py::_task_entry`) : terrain du lot 4.3 (issue #561), non exploité par aucune interface ici. Cette PR ne touche ni `grimoire standard init` ni `grimoire up` (PR 2/3) ni le cockpit (PR 3/3) — purement additive. Tests (`tests/unit/missions/test_task_unification.py`, 21 cas) : le bug documenté avant le correctif (board scaffoldé sans ledger), les 8 états de `BOARD_LIFECYCLE` couverts (avec la classe d'équivalence `released`→`accepted` déjà documentée par ADR-005), idempotence, réversibilité bit-à-bit, aucune perte des références de preuve (`context_bundle_ref`/`decision_trace_ref`/`evidence_pack_ref`), statut de divergence, champ `finition`, CLI bout-en-bout.
- feat(standard): `grimoire standard init` ouvre le Mission Ledger, `up` migre en meilleur effort, `doctor` signale la divergence — issue #559, lot 4.1 du plan 2026-Q4 (2/3), ADR-007 points 1/3/4 (`docs/adr-007-unification-des-taches.md`). Point 1 : `setup_standard_profile()` (`src/grimoire/core/agentic_standard.py`) n'écrit plus l'artefact `task_board` en copiant le template statique `framework/agentic-standard/templates/task-board.yaml` — nouvelle fonction `ensure_task_board_via_ledger()` (`core/task_board_ledger.py`) ouvre (`ledger.create_mission`/`ledger.create_task`, mission `MIS-standard-bootstrap-001`) puis projette (`build_board`/`write_board`) la tâche `bootstrap` dans le Mission Ledger, exactement comme `TaskService.project_board()` le fait déjà pour chaque transition ; les règles `force`/`refresh`/`keep` de `standard_generation.decide()` restent calculées sur le rendu du template inchangé, seul ce qui est écrit sur disque à l'action `write` a changé — un projet déjà édité à la main garde son board (invariant I4). Idempotent : rejouer l'init (`refresh=True`, comme `up`) ne recrée ni mission ni tâche. Point 3 : nouveau pas `step_task_unification()` (`cli/cmd_up_task_unification.py`), exécuté indépendamment de `--no-standard` (un projet peut être enrôlé d'un `init` précédent que ce passage ne répète pas) — sur un projet enrôlé (`is_standard_enrolled`) dont `tasks_unification_status()` (`task_unification.py`, PR 1/3) rapporte une divergence, appelle `migrate_standard_tasks()` en meilleur effort (jamais bloquant : une erreur devient un pas `failed` nommé, jamais un `up` interrompu) et le dit dans son rapport (texte et JSON, via `StepResult`). Point 4 : nouveau check `task_unification` dans `grimoire doctor` (`apply_task_unification_check()`, `core/task_unification_doctor.py`, appelé depuis `src/grimoire/cli/app.py`) — `WARN` nommé (jamais `FAIL`, ADR-007 : « jamais une correction automatique ») avec le remède exact (`grimoire task migrate-standard .`) quand un projet enrôlé diverge (board sans ledger, ou tâches manquantes), silencieux sinon. Quatre tests préexistants ajustés pour la même raison (une fixture qui présumait — à tort, c'est le défaut que ce lot corrige — qu'un `standard init` gouverné ne laissait aucun ledger) : `tests/unit/cli/test_cmd_task_board.py` (le board « écrit à la main » et le cas « sans ledger » sont désormais simulés explicitement plutôt que d'être l'état par défaut d'un init frais ; le compte de tâches JSON inclut la tâche `bootstrap`), `tests/unit/missions/test_trace.py` (le cas « sans ledger » retire explicitement le ledger que l'init ouvre maintenant), `tests/unit/test_hosts.py` (`_set_task_in_progress` édite la structure YAML parsée au lieu d'un remplacement de texte littéral `status: "proposed"`, qui ne correspond plus au formatage `ruamel.yaml` non cité du board projeté depuis le ledger). Tests neufs : `tests/unit/missions/test_task_board_init.py` (ledger ouvert à l'init, idempotence, équivalence du board produit face à `grimoire standard gate check` comparé à l'ancien template statique), `tests/unit/cli/test_doctor_task_unification.py` (WARN nommé rouge-avant, silence après migration, silence sur un projet non enrôlé), `tests/test_cmd_up.py::TestUpTaskUnification` (migration d'un board legacy sans ledger, idempotence, rien à migrer sur un projet fraîchement `up`). Les trois ajouts extraits dans leur propre module (`task_board_ledger.py`, `cmd_up_task_unification.py`, `task_unification_doctor.py`) plutôt que d'agrandir `agentic_standard.py`/`cmd_up.py`/`app.py` — trois fichiers déjà au-dessus du seuil de `scripts/check-code-ratchet.py` (1500 lignes, grandfathered pour les deux premiers) ; `app.py` reste 3 lignes au-dessus de son ancien plafond malgré l'extraction (un appel minimal à trois lignes, cohérent avec le reste de `doctor()`), plafond relevé à la main dans `scripts/code-ratchet-baseline.json` (2798 -> 2801, chemin prévu par le script lui-même pour une exception délibérée).
- feat(cockpit): l'espace Exécuter et le panneau Preuves lisent le Mission Ledger, migration proposée dans l'interface — issue #559, lot 4.1 du plan 2026-Q4 (3/3), ADR-007 point 5 (`docs/adr-007-unification-des-taches.md`). Point 5 (« aucun changement requis côté cockpit ») vérifié explicitement plutôt que supposé : nouveaux tests prouvant que `workspace_api.tasks_view`/`task_view` (Exécuter, Preuves) et `grimoire task list` (`TaskService.list_tasks`) lisent déjà la même source, pour un projet fraîchement `standard init` (point 1) et pour un projet migré (point 3) — `tests/unit/test_workspace_api.py::test_tasks_view_et_task_list_lisent_le_meme_ledger_apres_un_init_frais`/`_apres_migration`. `tasks_view()` (`src/grimoire/tools/workspace_api.py`) distingue désormais, dans son `ledger: false`, un projet enrôlé au standard dont le board n'a jamais été migré (`migration_available: true`, message dédié) d'un projet qui n'a même pas de board (message générique inchangé) — `tasks_unification_status()` (`missions/task_unification.py`, lot 1/3) porte déjà ce diagnostic. Nouvelle route `POST /api/workspace/tasks/migrate-standard` (`src/grimoire/tools/workspace_routes.py`) : même moteur que `grimoire task migrate-standard` (`migrate_standard_tasks()`), exposée en écriture uniquement sur le projet de lancement direct du cockpit — même garde `_HOME_SLUG` que le reste de `POST_ROUTES`, prouvée automatiquement par les tests déjà paramétrés sur cette table. Espace Exécuter (`web/workspace/spaces/executer.js`) : un projet enrôlé mais non migré affiche désormais un bouton « Migrer les tâches » (au lieu du seul rappel `grimoire task add`, qui ouvrirait une tâche neuve plutôt que d'importer celles déjà déclarées) — le clic appelle la nouvelle route et recharge le board. Champ `finition` (ADR-007 point 6, lot 4.3 à venir, issue #561) affiché en lecture seule dans la fiche tâche de l'inspecteur, absent tant qu'il n'est pas posé (aucune interface ne l'écrit encore). `docs/cockpit-coverage-matrix.md` + `scripts/cockpit-coverage.py` : deux lignes ajoutées (bouton de migration, affichage `finition`) et une route, résumé régénéré (169 lignes, 133 couvertes, 79 %). Quatre tests préexistants ajustés pour la même raison que la PR précédente (un `standard init --profile governed` scaffoldé sans ledger était la précondition de plusieurs fixtures e2e, plus vraie depuis le lot 2/3) : `tests/e2e/conftest.py::served_empty` retire désormais explicitement le ledger fraîchement ouvert pour rester le projet « sans Mission Ledger » qu'il documente — ce qui en fait aussi, sans rien y ajouter, le projet « enrôlé mais jamais migré » que le nouveau test du bouton utilise. Tests neufs : `tests/unit/test_workspace_api.py` (`migration_available`, disparition du message après migration, même source Exécuter/`task list` × 2 scénarios, `finition` en lecture seule), `tests/unit/test_workspace_routes.py::test_post_migrate_standard_importe_le_board_non_migre_sur_le_projet_de_lancement` (rouge-avant : 404 sans la route), `tests/e2e/test_workspace_controls.py::test_executer_etat_non_migre_propose_le_bouton_migrer_les_taches` (clic réel, board recharge, tâche `bootstrap` visible). Closes #559, Closes #521.
- fix(init): `grimoire init` en mode `auto` (défaut, y compris `-y`) ne câble plus silencieusement un projet neuf sur un backend mémoire réseau trouvé sur l'hôte — issue #496, reconfirmée par #578 sur ce même poste (un Weaviate de dogfooding sur `:8080` avait écrit `backend: weaviate-server` sur un projet jetable, cas réel #493). `detect_memory_backend()` (`src/grimoire/cli/cmd_init.py`) ne décide plus le backend : sans `--backend` explicite, `run_init` retombe toujours sur `lexical`, et le service détecté (Weaviate, Qdrant, Ollama) n'est plus que *suggéré* dans le rapport texte et JSON, avec la commande pour l'activer (`grimoire memory up --profile standard --apply`). Le wizard interactif peut toujours proposer le service détecté, mais uniquement via une question explicite (`_choose_memory_profile`) — jamais comme sélection par défaut ; un `--backend` déjà explicite ne déclenche aucune question. Sur un backend partagé choisi explicitement (`weaviate-server`, `qdrant-local`, `qdrant-server`), la collection est désormais nommée d'après le projet (`collection_prefix`, dérivé de son slug via `grimoire.memory.taxonomy.slugify`) plutôt que le nom fixe `GrimoireMemory` que tous les projets partageaient jusqu'ici (retiré de `BACKEND_CONNECTION` dans `src/grimoire/memory/profiles.py`) ; `ProjectScaffolder` (`src/grimoire/core/scaffold.py`) accepte ce `collection_prefix` et l'émet dans `project-context.yaml`. S'attacher à une collection déjà peuplée exige désormais `--memory-collection <nom>` explicite — sans lui, `init` sonde la collection visée (`collection_has_content`, best-effort, une sonde en échec ne bloque jamais) et refuse avec un message clair si elle contient déjà des données. Reproduit et vérifié en conditions réelles sur ce poste (Weaviate de dogfooding réel sur `:8080`, projets jetables, aucune écriture dans le service) : avant le correctif, `init -y` écrivait `backend: "weaviate-server"` + `weaviate_collection: "GrimoireMemory"` ; après, `backend: "lexical"` avec la suggestion, et `--backend weaviate-server` explicite refuse de s'attacher à la collection `GrimoireKitMemory` existante (peuplée) tant que `--memory-collection` n'est pas fourni. Tests (`tests/test_cmd_init.py`, `tests/unit/cli/test_app.py`) : détection purement informationnelle, sonde de contenu de collection tolérante aux pannes, question explicite du wizard (acceptée/refusée/jamais posée si déjà explicite), CLI bout-en-bout (repli lexical malgré un service détecté, suggestion dans le rapport texte/JSON, `--backend` explicite toujours honoré, nommage par slug, `--memory-collection` en override, refus sur collection non vide, backends locaux non affectés). Docs : `docs/getting-started.md` (nouvelle section « Mémoire par défaut »), `docs/memory-system.md` (§ Mise en place et diagnostic).
- feat(init): `grimoire init --lite` (alias `--profile lite`) — issue #552, phase 2 du plan produit 2026-Q4, lot 2.6. Un preset nommé, pas un nouveau mécanisme : chaque réglage qu'il pose est déjà un flag existant (`--backend lexical`, `--memory-profile lexical`, `--no-cockpit`, `--archetype minimal` par défaut si aucun n'est fourni) — appliqué avant la résolution habituelle dans `run_init` (`src/grimoire/cli/cmd_init.py`), donc un assistant interactif peut toujours les changer (pas de `--yes` implicite, cohérent avec le comportement existant de tous les autres flags d'`init`). Le rapport texte et JSON (`_display_report`/`_display_json`) nomme les trois choses laissées de côté (mémoire lexicale seule, pas de cockpit, standard non activé) et la commande exacte pour activer chacune plus tard (`grimoire memory up --profile standard --apply`, `grimoire cockpit add .`, `grimoire standard init .`). Le wizard interactif recommande désormais `lexical` en premier (au lieu du profil par défaut) quand `--lite` est passé ou quand le dépôt ressemble à un terrain de jeu (`_looks_like_a_playground` : aucun marqueur de CI top-level, aucun répertoire de tests non vide) — heuristique légère, jamais un scan récursif, qui ne change jamais ce qui est écrit, seulement la suggestion. Mesuré sur un projet jetable (`GRIMOIRE_NO_COCKPIT=1`, 3 exécutions chacun) : temps d'`init` 0,31-0,35 s en lite contre 0,37-0,45 s par défaut sur cette machine (qui a détecté un Weaviate + Neo4j réels sur localhost et écrit `backend: weaviate-server` — exactement le risque de non-déterminisme que `--lite` évite en pinnant `lexical` sans probe réseau) ; 102 fichiers écrits en lite contre 103 par défaut (`docker-compose.memory-target.yml` en moins) ; tokens injectés au premier hook `SessionStart` identiques dans les deux cas (1409 caractères ≈ 352 tokens — l'archétype `minimal` est déjà le même des deux côtés sur ce dépôt sans stack détectée, donc `--lite` ne réduit pas ce total ici, contrairement aux fichiers/services). Tests (`tests/unit/cli/test_init_lite_profile.py`, nouveau, 16 cas) : heuristique de détection, recommandation du wizard, backend lexical écrit, aucune écriture cockpit (HOME isolé de la suite, `conftest._isolate_user_state`), aucun répertoire `_grimoire/standard`, contenu du rapport texte et JSON, alias `--profile lite`, rejet d'un nom de profil inconnu, `doctor` vert sur le projet généré, hook `SessionStart` réel qui répond sainement, aucune référence à qdrant/weaviate/ollama dans la config écrite.
- feat(cli): `grimoire --help` ne liste plus 49+ commandes de premier niveau par défaut (issue #552, phase 2 du plan produit 2026-Q4, métrique 2, cible ≤ 5). Cinq commandes couvrent la boucle réelle de la première heure — scaffolder (`init`), tout enchaîner (`up`), diagnostiquer (`doctor`), l'unité de travail gouvernée avec son reçu de preuve (`flow`), le tableau de bord (`cockpit`) — rendues par `_render_condensed_help` (`src/grimoire/cli/_condensed_help.py`, extrait d'`app.py` — R2 du ratchet de taille, `scripts/code-ratchet-baseline.json`, interdit toute croissance d'un fichier déjà au-dessus du seuil) ; `grimoire --help --all` (n'importe quel ordre des deux flags) restitue l'affichage complet à 100 % inchangé (mêmes panneaux, mêmes 52 commandes mesurées ce jour, aucune renommée ni retirée). Le rendu condensé n'importe plus aucun module `cmd_*` lazy (`LazyTyperGroup`, #405) — c'était le vrai coût : la vue complète walke les 24 groupes + 5 commandes paresseux pour lire leur description courte, ce qui les importe tous. Mesuré (5 exécutions, `/usr/bin/time`) : `grimoire --help` 530-540 ms → 120-130 ms (plancher mesuré = `grimoire --version`, ~130 ms, coût d'interpréteur incompressible hors périmètre de ce lot) ; `grimoire --help --all` reste à 500-560 ms, inchangé. Détection de `--all` indépendante de l'ordre des flags : lue depuis les arguments bruts dans `_RootHelpGroup.parse_args`, pas depuis `ctx.params` (l'eager processing de click suit l'ordre de saisie, pas l'ordre de déclaration — `--help --all` et `--all --help` donnaient un résultat différent avec la première implémentation). `docs/getting-started.md` réécrit autour des cinq commandes (nouvelle section en tête, table `grimoire --help --all`/`docs/cli-reference.md` en aval remplaçant l'ancien tableau de 27 lignes qui contredisait le message). Tests : `tests/unit/cli/test_help_first_hour.py` (garde de dérive — `_FIRST_HOUR_COMMANDS` ne peut pas dépasser cinq entrées ; rendu par défaut vs `--all` ; sous-commande non affectée) et deux gardes anti-régression d'import dans `tests/unit/cli/test_lazy_startup.py` (`--help` ne fuite aucun module lourd, `--help --all` continue de tous les importer — preuve que le gain n'est pas un registre cassé).

## [3.54.0] - 2026-09-17

- docs(security): nettoyage code scanning, lot 2 — triage (phase 2 du plan produit 2026-Q4, épic #552, suite de la PR #573). Le constat de départ (« ~50 alertes, presque toutes py/path-injection ») était largement dépassé : 402 alertes ouvertes au moment du triage, cause identifiée dans `.github/workflows/codeql-analysis.yml` — `queries: security-and-quality` charge ~250 requêtes de qualité/style (imports inutilisés, `except: pass`, retours mixtes…) déjà couvertes par le gate `ruff`/`mypy --strict` bloquant de cette CI, en plus des ~150 alertes de sécurité réelles ; le bruit est ce qui rend le check illisible. Narrowing vers `security-extended` (garde toutes les requêtes de sécurité, retire les requêtes de qualité). `docs/security/code-scanning-triage-2026-09.md` : méthode et preuve du triage des `py/path-injection` restants — lecture directe de chaque fichier signalé, constat de trois mécanismes de confinement de chemin déjà en place et non reconnus par CodeQL comme sanitizers (`resolve_within_allowed`/`allowed_roots` dans `project_registry.py`, `_blueprint_path`/`SLUG_RE` dans `forge_server.py`, `_ensure_inside_root` dans `agentic_standard.py`), et pour les fichiers sans ces helpers, un paramètre racine systématiquement dérivé de la CLI/config locale de l'opérateur, jamais d'un réseau distant. Alertes `py/path-injection` restantes dismissées `false positive` avec justification précise ; alertes de qualité/style dismissées `won't fix` (hors périmètre sécurité, doublon du gate existant). Alerte historique #223 (`py/command-line-injection`, `cmd_cockpit.py`) déjà dismissée le 2026-08-27 par Guilhem, rien à faire. Objectif de fait : 0 alerte ouverte non justifiée après ce lot, check CodeQL vert sur cette même PR.
- perf(cockpit): la fiche Piloter faisait encore HUIT appels réseau à son premier rendu, dominée par `doctor` (issue #548, phase 2 du plan produit 2026-Q4, épic #552, lot 2 — suite de #542/#547). Cause du cache `proposals` inopérant CONFIRMÉE en lisant `sync_proposals()` (`src/grimoire/proposals.py`) : les branches `keep_rejected` et `refresh_pending` appelaient `_save_proposal()` INCONDITIONNELLEMENT à chaque appel, même quand rien n'avait changé — cette écriture bouge la mtime du dossier `_grimoire-output/proposals/`, or c'est exactement ce que surveille la signature de `proposals_view()` (`grimoire.tools.view_cache`) : auto-invalidation à chaque lecture, le cache ne pouvait jamais faire hit (mesuré 0,46s→0,52s→0,49s, jamais 0,00s). Corrigé : les deux branches comparent désormais le `Proposal` recalculé à l'existant (`frozen=True` → égalité de valeur native) avant d'écrire ; nouveaux tests rouges avant/verts après dans `tests/unit/test_proposals.py` (mtimes des fichiers de propositions identiques avant/après une resynchronisation sans rien de nouveau) et au niveau vue dans `tests/unit/test_workspace_api.py`. Mesuré ensuite (micro-benchmark `time.perf_counter`, projet réel) : `kit_alignment()` (`project_health.py`) domine `/api/health` à elle seule (~120ms, sha256 par fichier shipé du kit à CHAQUE appel) ; `doctor_view()` (`workspace_exec.py`, relance un `grimoire doctor` complet en sous-processus) est en réalité la route la PLUS chère des sept, ~350ms — jamais mesurée jusqu'ici. Les deux gagnent le même patron de cache que `agents_view` (`view_cache`, signature de mtimes des dossiers pertinents — `_grimoire/kit` pour `health`, config/agents/skills/kit/overrides/wrappers VS Code/policies pour `doctor`) avec `?probe=1` pour forcer un recalcul frais (même câblage que `memory_link_view`). Nouvelle route agrégée `GET /api/workspace/sheet?project=` (`workspace_api.sheet_view`, câblée dans `workspace_routes.py::GET_ROUTES`) : santé, mémoire (mode rapide), agents, propositions, dernier run du wizard, dernier run de flow `project-upgrade` et nom du projet (registre) calculés en parallèle (`ThreadPoolExecutor`, même principe que `fleet_status`) en une seule réponse — `doctor` volontairement absent (mesuré comme le vrai coût des sept, l'onglet Problèmes le rend séparément, à la demande). `web/workspace/spaces/piloter.js::loadSheet()` remplacé par un unique `ctx.api.sheet(slug)` ; le nom du projet (cockpit) vient désormais de `sheet.name`, plus de second appel `projects()`. Mesures avant/après (harnais e2e `tests/e2e/test_workspace_piloter_perf.py`, projets jetables) : fiche Piloter 8 appels réseau → 1 (`/api/workspace/sheet`), interactive en ~811-849ms → ~110ms (seuil du test : < 400ms, critère d'arrêt de l'issue).
- fix(security): nettoyage code scanning, lot 1 — correctifs (phase 2 du plan produit 2026-Q4, épic #552). **(a)** `ForgeAPI.blueprint_compile` (`src/grimoire/tools/forge_server.py`) construisait `artifact_rel`/`artifact_path` à partir de `blueprint["id"]` (issu du corps d'une requête HTTP cockpit, `POST /api/blueprints/<id>/compile`) *avant* le premier appel à `_blueprint_path`, seule fonction du module à valider un id de blueprint (`SLUG_RE` + confinement sous `.github/prompts/`) — un id contenant `../` écrivait le mission pack hors du projet (CodeQL py/path-injection, alertes réelles, pas un faux positif). Même garde `SLUG_RE` appliquée dès l'extraction de `bp_id`, réutilisée telle quelle plutôt que dupliquée. `blueprint_validate`/`blueprint_lint` (mêmes routes cockpit, ouvertes en lecture sur toute la flotte sans garde `_HOME_SLUG`) laissaient aussi un `ref` de node (`artifact`/`composite`/schéma de gate) avec `../` transformer une vérification d'existence en oracle de fichier arbitraire sur la machine — `_schema_resolves`, la vérification d'artefact et celle de sous-blueprint confinent désormais le chemin résolu sous `project_root` avant tout `.exists()`/`.is_file()` (même message « absent » pour un chemin manquant et pour un chemin qui s'échappe, aucune fuite de bit). **(b)** `_maybe_register_cockpit` (`src/grimoire/cli/cmd_init.py`) journalisait `target` (nom de dossier fourni par l'appelant) avec `%s` — un nom de dossier contenant un retour à la ligne pouvait forger une fausse entrée de log (py/log-injection) ; `%r` (repr) échappe désormais `\n`/`\r`. **(c)** `ensure_go_toolchain` (`scripts/bench/three_arms.py`) appelait `tarfile.extractall()` sans filtre sur l'archive Go téléchargée (py/tarslip) — même une archive officielle pinnée reste vulnérable en cas de compromission de la source ou de MITM ; extraction factorisée dans `_extract_go_archive` avec `filter="data"` (refuse tout membre `../`/absolu), désormais testable sans réseau. Deux constantes `_NODE_RE` mortes retirées (`tests/unit/test_flows_composite.py`, `tests/unit/test_flows_extract.py`, py/unused-global-variable). Tests rouges avant/verts après pour les trois correctifs de sécurité (`tests/unit/tools/test_forge_server.py::test_compile_rejects_path_traversal_id`, `tests/test_cmd_init.py::TestMaybeRegisterCockpitLogging`, `tests/unit/test_bench_three_arms.py::test_extract_go_archive_rejects_a_path_traversal_member`) ; suite complète revérifiée verte, aucun changement de comportement pour un chemin/id légitime. Triage documenté des alertes restantes (lot 2, PR séparée) : `docs/security/code-scanning-triage-2026-09.md`.
- feat(bench): harnais du banc à trois bras (issue #551, plan produit 2026-Q4 phase 1 lot 1.1) — `scripts/bench/three_arms.py` compare Claude Code nu, Claude Code + ecc (github.com/affaan-m/ECC, MIT, plugin scope projet) et Claude Code + grimoire-kit (`grimoire init --backend local --no-cockpit` + `host sync`) sur un sous-ensemble reproductible du benchmark polyglot d'Aider (20 exercices Exercism, 5 par langue sur Python/JavaScript/Go/Rust, graine fixée `551`, k=3 rejeux, ordre des bras tiré au sort par tâche). Garde-fous par run (timeout 15 min, détection « tourne en rond » sur commandes Bash répétées via le flux `stream-json`), critère d'arrêt statistique (IC bootstrap disjoints entre bras sur le succès), rapport `report.md`/`report.json` (succès, pass^k, temps médian, coût médian par tâche résolue, IC95%). `--dry-run` prépare les 60 dépôts de tâche sans appel modèle ; `--pilot` (2 tâches x 3 bras x 1 run) écrit le coût attendu avant `--full` (20 x 3 x k=3) ; alerte (sans bloquer) si le coût réel dépasse 3x l'attendu. Isolation : `HOME` jetable par bras avec les identifiants Claude Code du poste recopiés le temps d'un run puis effacés dans un `finally` (jamais lus/affichés, jamais laissés à demeure — balayage de fin de campagne), jamais d'écriture dans `~/.grimoire` ni le `HOME` réel. Tests unitaires sans LLM ni réseau (`tests/unit/test_bench_three_arms.py`) : tirage reproductible, préparation de dépôt de tâche (tests cachés), détection de succès sur tests verts/rouges, calcul de pass^k et de son IC, détection de boucle, nettoyage des identifiants sur succès/exception. Protocole complet dans `docs/bench-three-arms.md`.
- fix(status): `kit_alignment()` (`src/grimoire/tools/project_health.py`) affichait « aligné sur 3.46.0 » pour un projet à jour en 3.51.1 (issue #519) — `aligned` date un contenu encore présent à sa *première* introduction au catalogue de digests, pas au dernier passage réel de l'outil ; un projet dont l'essentiel n'a pas changé depuis une vieille release se lisait donc comme en retard. `grimoire init`/`up` (`ProjectScaffolder`) écrit désormais un marqueur `_grimoire/kit/.up-version` (tier kit, régénéré à chaque `up` comme le reste de cette arborescence — jamais figé comme `project-context.yaml`) avec la version de l'outil qui vient de tourner ; `kit_alignment()` le lit dans un nouveau champ `upVersion`, seule source ajoutée (jamais une seconde estimation). `tool_version_gap()` (doctor `tool_version`, #515) compare désormais l'outil courant à `upVersion` en priorité — correctif de fond, pas seulement d'affichage : un outil dont la version tombe *entre* la première introduction d'un contenu et le vrai dernier `up` se disait à tort « à jour » en comparant à l'ancienne référence. La fiche Piloter (`web/workspace/spaces/piloter.js`) affiche « à jour (kit X) » quand `upToDate` et `upVersion` sont connus, avec « contenu inchangé depuis Y » en détail secondaire si `aligned` diffère ; repli inchangé (« aligné sur X, installé Y », #288) sur un projet sans marqueur connu. Tests rouges avant (`tests/unit/test_project_health.py`, `tests/test_scaffold.py`) : `KeyError: 'upVersion'` sans le marqueur, faux « pas en retard » avec un outil intermédiaire entre les deux références ; `tests/unit/cli/test_doctor_tool_version.py` et l'e2e `test_piloter_kit_reel_a_jour_dit_a_jour_avec_le_kit_du_dernier_up` (`tests/e2e/test_workspace_lot4_spaces.py`) mis à jour pour la nouvelle référence.
- fix(cockpit): `POST /api/blueprints/<id>/compile` manquait de `_CockpitHandler` (`src/grimoire/cli/cmd_cockpit.py`), issue #546, même défaut que `PUT` avant #543 : `do_POST` n'avait aucune branche pour `/compile`, réponse 404 « route inconnue » avant même d'atteindre une garde — sur le seul serveur qui tourne réellement pour `grimoire serve` et `grimoire cockpit serve` depuis #351. Câblée avec la même garde que `/api/setup` et `PUT` : `blueprint_compile` écrit un artefact `.prompt.md` sur disque et persiste la section `compiled` dans le blueprint d'origine, ce n'est pas un calcul comme `/validate`/`/simulate` — seul le projet de lancement direct (`_HOME_SLUG`) peut donc compiler, 403 sinon. `/validate` et `/simulate` étaient déjà câblés et volontairement sans cette garde (calcul pur) : confirmé avec un vrai handler plutôt que supposé. `grimoire blueprint evals` vérifié comme commande CLI, jamais une route HTTP ni sur le cockpit ni sur l'atelier — rien à câbler. Nouveau `tests/unit/cli/test_cmd_cockpit_blueprint_writes.py`, paramétré sur les trois routes (200 home, 404 blueprint inconnu, 403 hors home pour `/compile` seulement, 400 blueprint bloqué) contre un vrai `_CockpitHandler` — rouge avant, vert après. `docs/cockpit-coverage-matrix.md` mis à jour (`/compile` et `PUT` passent « oui », étiquette « atelier only » de `PUT` corrigée, périmée depuis #543) et résumé régénéré par `scripts/cockpit-coverage.py` (Routes API 17→18, Concevoir 15→16, total 77→78 %).
- fix(archetypes): `minimal` et `agentic-standard` (`archetype.dna.yaml`) référençaient `project-navigator` et `memory-keeper` (issue #550), retirés du paquet par la refonte meta #387 (commit `f636633c`) sans mise à jour des DNA en aval — `grimoire init --archetype minimal` déclarait deux agents qu'il ne pouvait pas installer. Vérifié sur `git log -S project-navigator -- archetypes` : retrait volontaire (savoir-faire porté depuis par les skills `meta-project-navigation`/`meta-memory-quality` attachés à `agent-optimizer`), donc retrait des références plutôt que rétablissement. `checked_by: memory-keeper` réaligné sur `agent-optimizer` dans les deux DNA. Nouveau test `tests/unit/registry/test_archetype_dna_agents.py`, paramétré sur tous les `archetype.dna.yaml` livrés : échoue si un agent référencé n'a pas de fichier `.md` livré (rouge avant ce correctif, vert après) — attrape aussi tout futur cas.
- docs(veille): lots 1.2 et 1.3 de la phase 1 du plan produit 2026-Q4 (issue #551). `docs/veille/anthropic-direction-2026-09.md` : verdict chevaucher/envelopper/ignorer par capacité Anthropic récente (Agent Teams, Managed Agents, subagents, skills, plugins/marketplace, hooks, mémoire, sessions/reprise, tâches en arrière-plan, sandbox, computer use, prompt caching, MCP Apps), sources officielles lues le 2026-09-16 (`code.claude.com/docs`, `platform.claude.com/docs`, `github.com/anthropics/skills`, changelog Claude Code) — chevauche déjà Agent Teams via le genre de flow `fanout` (`src/grimoire/flows/genres.py:246`, preuve et coût que l'expérimental Anthropic n'a pas). `docs/veille/idees-a-recolter-2026-09.md` : instruction des huit idées du plan (§6) avec fichier source et licence vérifiés dans chaque dépôt concurrent (affaan-m/ECC 292 skills/68 agents, SuperClaude, cline/cline, langchain-ai/langgraph, mastra-ai/mastra, bmad-code-org/BMAD-METHOD), deux corrections (licence Mastra Apache-2.0 et non Elastic-2.0 ; le gate `grimoire-skill-analyzer` cité par le plan est un outil de la Forge, absent de `src/grimoire/` — écart réel de la phase 5.5, pas une capacité déjà livrée), une neuvième idée ajoutée (gate de confiance pré-implémentation, skill `confidence-check` de SuperClaude). Section « Révision 2026-09-16 » de `framework/agentic-industry-reference.md` complétée avec un lien vers les deux documents.
- fix(memory): `memory up --profile` et le schéma parlaient deux vocabulaires (issue #527, phase 2 du plan produit 2026-Q4, épic #552). **(a)** La CLI acceptait `lexical|vector|full` (`grimoire.tools.memory_setup`) pendant que le schéma et `grimoire.memory.profiles` parlent `lexical|standard|graphe|complet` (`layer_profile`) — `memory up` n'écrivait jamais `layer_profile` et laissait `retrieval_mode` sur son défaut (`vector`) même en profil `full`, faisant croire au cockpit et à `memory status` qu'un projet passé en composition `complet` tournait encore en `standard`. `vector`/`full` sont maintenant des alias résolus par `grimoire.memory.profiles.ALIASES` vers `standard`/`complet` ; `build_memory_plan` écrit toujours `layer_profile`/`retrieval_mode`/`vector_database` d'après ce que la machine sert réellement (`_finalize_layers`, jamais d'après le profil demandé — un `complet` sans Neo4j ni Redis s'écrit `standard`, sans aucun service vectoriel s'écrit `lexical`). **(b)** `memory migrate plan` retombait sur `cfg.memory.backend` (le backend COURANT) faute de `migration_source_backend` — bug nommé par l'issue : un plan ne peut pas migrer un backend dans lui-même. `memory up --apply` laisse maintenant une trace du backend qu'il quitte (`_grimoire/_memory/migration/previous-backend.json`, `write_previous_backend_breadcrumb`) ; `plan` la lit et l'expose (`source_known`). Nouvelle commande `memory migrate run [--source] [--apply]` : lit n'importe quel backend Memory OS via l'API publique du manager (`get_all`) et réécrit dans le backend courant (`store_many`, qui recalcule ses propres embeddings) — remplace le couple manuel `memory export` + `memory import`, seul chemin qui fonctionnait jusqu'ici pour un lexical → vecteur. **(c)** `memory graph sync-memories` est additif seulement : un souvenir supprimé du store, ou un essai antérieur écrit sous une autre collection Weaviate, laisse des nœuds `GrimoireMemory` orphelins pour toujours (cas réel observé sur la Forge : store=75, graph=3782). Nouvelle commande `memory graph purge-orphans` (dry-run par défaut, `--apply` pour supprimer), bornée à la collection du projet et à l'ancienne collection générique `GrimoireMemory` d'avant les profils (`Neo4jMemoryGraph.find_orphan_memory_nodes`/`purge_memory_nodes`) — jamais un scan non borné qui risquerait un autre projet partageant la même instance Neo4j. **(d)** `RedisHotMemory` namespace déjà ses clés par `collection_prefix`, mais un projet resté sur le défaut partagé (`grimoire`) collisionnait avec tout autre projet dans le même cas sur une instance Redis mutualisée (constat réel : Forge sur `redis://localhost:6379/0` partagé avec un autre projet du poste) — `_redis_namespace` (`grimoire.memory.manager`) retombe désormais sur le slug du nom de projet quand le préfixe est encore générique, et `memory up` le rapporte (`plan.notes`, nouveau champ). Tests rouges avant (backends mockés — driver Neo4j factice, `store_many` factice, aucun service réel) : `tests/unit/tools/test_memory_setup.py`, `tests/unit/memory/test_migration.py`, `tests/unit/memory/test_neo4j_graph.py`, `tests/unit/memory/test_projections.py`, `tests/unit/memory/test_manager.py`, `tests/unit/cli/test_cmd_memory.py`, `tests/unit/cli/test_cmd_memory_ops.py` (nouveau).
- fix(tests): `test_status_non_resident_declenche_un_chargement_sans_generer_de_texte` (`tests/unit/test_source_assist.py`) était instable en CI (`assert 2 == 1`, issue #529). Cause confirmée par une reproduction isolée (frontière exacte entre la fin d'un test et le début du suivant) : `_trigger_warm_up` (`src/grimoire/tools/source_assist.py`) démarre un thread démon fire-and-forget que ni `assist_status` ni les tests ne joignent ; un test précédent qui déclenche un chargement (`running = ()`) puis rend la main avant la fin de ce thread laisse ce dernier écrire dans `_FakeOllama.generate_calls` (classvar partagée entre tous les tests) pendant le test SUIVANT, une fois la liste réinitialisée par la fixture `fake_ollama` — d'où tantôt 1 tantôt 2 entrées vues par l'assertion suivante, selon l'ordonnancement. Code de production non modifié : `_trigger_warm_up` ne fait déjà qu'un seul appel réel par déclenchement (garde `_WARM_UP_INFLIGHT`), vérifié en lisant le code. Correctif : nouvelle fonction `_drain_warm_up()` dans le test, qui attend que `_WARM_UP_INFLIGHT` se vide avant que la fixture ne réinitialise l'état partagé et n'arrête le serveur — plus aucun thread en vol ne peut polluer le test suivant. Preuve : harnais de reproduction isolé montrant la contamination à 10/10 sans le correctif et 0/10 avec ; suite complète relancée 15 fois sans échec après correctif.
- docs(plan): axes phase/finition et délégation optimale dans le plan produit 2026-Q4 (décisions Guilhem du 2026-09-16 matin, suite à #558). `docs/plan-2026-q4.md` §2 : métriques 4 et 5 deviennent des métriques de délégation (part des tâches résolues au palier de départ, escalades justifiées vs subies, temps avant demande d'aide, boucles détectées), le coût par tâche et pass^k passent en métrique de conséquence (4bis) — le banc compare qualité puis temps puis coût. §3 : le banc à trois bras s'arrête sur clarté statistique (IC disjoints ou n suffisant), alerte (jamais un blocage) à 3× le coût attendu, jamais un plafond en dollars. §4 phase 4 réordonnée (préalable #521/#559 unification des tâches, puis phase et finition déclarées #560, propagation aux tâches et au kanban #561, pilote adaptatif #562, boucle de finition + juge #563, politique de question #564, reçu portable, rejeu diff, skills, cascade coût visible) ; deux nouvelles sections "Phase et finition" (deux axes, tableau phase × finition par défaut, cinq moments où l'utilisateur choisit) et "Délégation optimale" (signaux/actions du pilote, formes d'équipe). Issue #551 mise à jour avec la définition complète du banc (bras Claude Code nu/+ecc/+grimoire-kit, jeu de tâches Aider polyglot/Exercism, 20 tâches extensible, arrêt statistique). Six nouvelles issues #559-#564 (une par lot 4.1-4.6).
- docs(plan): plan produit 2026-Q4 (issue #557, epics #551-#556). Cible développeur seul/entreprise/équipe, promesse « preuve, coût, portabilité », dix métriques de succès mesurées sur un projet jetable le 2026-09-16 (`grimoire --help` : 49 commandes de premier niveau ; `grimoire-hook` PreToolUse : ~50 ms ; `grimoire init` : 0,24 s ; SessionStart `additionalContext` : 1409 caractères ≈ 352 tokens ; PreToolUse `additionalContext` pour Claude Code : 0 par construction, `src/grimoire/hosts/runtime.py:166-174` ; 2 émetteurs d'hôtes riches sur 5 hôtes cibles ; 20 skills et 15 agents livrés dans les 10 archétypes), sept phases (0 à 6) et un transversal, matrice de parité reprise de la veille avec colonne phase, registre des risques (waivers chromadb corrigés à `2027-02-28`, `.github/security/dependency-waivers.yaml`), section « Révision 2026-09-16 » ajoutée à `framework/agentic-industry-reference.md` recentrant la référence sur le segment développeurs-sur-un-hôte.

## [3.53.0] - 2026-09-15

- perf(cockpit): moitié front de la perf Piloter (issue #541, suite du backend #542 déjà mergé). (1) `loadFleet()` (`web/workspace/spaces/piloter.js`) appelait encore `projects()` PUIS `health()`/`memoryStatus()` par projet du registre — remplacé par un unique appel `ctx.api.fleet()` (`GET /api/fleet`). (2) `mount()`/`draw()` appelait `ctx.api.projects()` (nom du projet, cockpit) en SÉRIE avant `loadSheet()` — les deux partent désormais en même temps (`Promise.all`). (3) `draw()` peignait un écran vide jusqu'à la résolution complète des appels — une coquille « Chargement… » s'affiche maintenant tout de suite, retirée avant le rendu réel (jamais empilée dessous). (4) Un `draw()` déclenché pendant qu'un tour précédent tourne encore rejoint désormais la même promesse (`inFlight`) plutôt que d'en relancer un second en concurrence — prouvé par un test qui chevauche deux rafraîchissements forcés au niveau DOM (rouge sans la garde : 2 appels `/api/fleet` ; vert avec : 1) ; corrigé en cours de route pour ne jamais avaler un changement de cible légitime survenu pendant ce tour (clic de zoom Flotte/Projet), attrapé par le harnais générique de bascule de segments : un rendu frais s'enchaîne désormais après coup UNIQUEMENT si la cible a réellement changé, jamais pour un second déclenchement identique. (5) `ctx.api.memoryStatus()` accepte désormais `{ probe: true }` (`GET /api/memory/status?probe=1`) ; nouveau bouton « Sonder la mémoire » dans la fiche Piloter, seul déclencheur d'une sonde réseau fraîche (jamais de `setInterval`), mise à jour ciblée (pas de redessin de toute la fiche). Complément mineur côté backend (pas une réouverture de #542) : `_fleet_entry()` (`project_health.py`) manquait `name` et `managed` — la Flotte (`renderFleet`/`watchReasons`) les lit sur chaque entrée et n'a plus d'autre appel pour les obtenir depuis que `loadFleet` ne fait plus qu'UN appel réseau. Le cache 60s de la Flotte n'était pas non plus invalidé après création d'un projet depuis ce même écran — corrigé.

  | Scénario (harnais e2e, 3 projets jetables) | Avant | Après |
  |---|---|---|
  | Flotte — appels réseau (registre à froid) | 7 (`projects` + 3×`health`+`memoryStatus`) | 1 (`fleet`) |
  | Flotte — temps interactif | ~269 ms | ~262 ms |
  | Fiche Piloter — appels réseau | 8 | 8 (inchangé, hors périmètre — voir ci-dessous) |
  | Fiche Piloter — étalement des départs de requête (parallélisme réel) | ~12,7 ms | ~1,3 ms |
  | Fiche Piloter — temps interactif | ~811-849 ms | ~813-850 ms (dominé par le backend, voir ci-dessous) |
  | Rafraîchissement Flotte chevauché (forcé) — appels `/api/fleet` | 2 | 1 |

  Mesures par `tests/e2e/test_workspace_piloter_perf.py` (rouge sur le code d'avant, vert après), pas inventées. Deux écarts documentés par rapport à la cible initiale de l'issue, les deux hors périmètre d'une PR front-only : le budget d'appels de la fiche Piloter reste à HUIT (`loadSheet` en porte SEPT depuis #513, un chantier antérieur — le ramener à SIX demanderait d'y retirer un appel, pas d'en changer l'ordonnancement) ; le seuil d'interactivité de la fiche vise 700 ms dans l'issue mais plafonne à ~810 ms sur ce harnais quel que soit l'ordonnancement front, la réponse la plus lente des huit endpoints (`doctor`/`health`, IO disque réelle) dominant le total — le gain mesuré ici est la disparition du petit aller-retour série (`projects()` avant `loadSheet`), pas le temps total.
- test(cockpit): lot B de couverture systématique — raccourcis et palette (issue #536). Dix tests e2e nouveaux (`tests/e2e/test_workspace_shell_palette_actions.py`) : les six raccourcis `⌘1`–`⌘6` (changement d'espace, jamais pressés réellement — seul `GrimoireWorkspace.goto` direct l'était), le raccourci `` ` `` (ouvre la Console du dock), et l'exécution réelle (pas seulement l'ouverture) des trois sections de palette restées non exercées : « Tâches » (`grimoire task show`), « Workflows » (navigation Concevoir), « Projets » (rechargement `?project=`). Constat en cours de route, pas un bug : `buildPalette()` (`shell.js`) ne se reconstruit qu'au chargement de la page, jamais à l'ouverture — une tâche/un blueprint créé après coup n'apparaît dans la palette qu'après rechargement (documenté dans la matrice). Résumé chiffré régénéré : 163 lignes, 124 couvertes (76 %, coque à 97 %).
- fix(cockpit): Piloter n'affichait ni ne rafraîchissait jamais les propositions en attente après une mise à jour confirmée qui échoue en `upgraded-but-failed` (issue #538, `xfail(strict=True)` posé par le lot A de couverture ci-dessous). `project_update.py::_run_upgrade_flow` joint déjà `report`/`proposals` sur cet état précis, mais le handler du bouton « Confirmer la mise à jour » (`piloter.js`) gardait l'affichage du nombre de propositions ET son rafraîchissement (`refreshProposals()`) derrière `result.ok` — or `state: "upgraded-but-failed"` implique toujours `ok: false` (`_derive_state`), donc les deux blocs restaient systématiquement morts sur cet état, alors qu'ils fonctionnent pour `completed`/`upgraded-checkpoint-pending`. Les deux gardes portent désormais sur la présence réelle du champ (`Array.isArray(result.proposals)`), indépendamment de `result.ok` ; `markKitCheckpointPending`/`checkpointPendingRunId` restent réservés au succès (le nœud `destructive` n'est jamais atteint sur un run interrompu à `apply`). Retire le `xfail` de `test_confirmer_upgraded_but_failed_devrait_rafraichir_les_propositions` — rouge avant, vert après.
- test(cockpit): lot A de couverture systématique — écritures et décisions (issue #536). Six tests e2e nouveaux pour les six `artifact_type` de proposition (`tests/e2e/test_workspace_proposals_types.py`, issue #490) : jusqu'ici seul le type `agent` du déclencheur passait par un vrai clic Piloter + la route `POST /api/workspace/proposals/<slug>/accept`, les cinq autres (`skill`, `repair` avec/sans substitution, `override-migration`, `memory-link`, `needs-hosts`) n'étaient prouvés qu'au niveau moteur. Un test pour le repli « presse-papiers indisponible » du bouton « Revoir dans l'IDE » (`tests/e2e/test_workspace_piloter_review_button.py`) et un pour l'aperçu en échec de la mise à jour (`tests/e2e/test_workspace_cockpit_upgrade_flow_states.py`). Un bug confirmé et marqué `xfail(strict=True)` en attendant correction : Piloter n'affiche ni ne rafraîchit jamais les propositions en attente après une mise à jour confirmée qui échoue en `upgraded-but-failed`, alors que la route les renvoie déjà (#538). Deux corrections de la matrice (deux lignes marquées à tort « non » en PR précédente : la garde 404 sur projet hors registre et les erreurs de création de projet sont déjà couvertes au niveau route). Résumé chiffré régénéré : 162 lignes, 116 couvertes (72 %, +8 points).
- perf(cockpit): l'espace Piloter était lent à charger (mesuré sur le cockpit réel : `/api/memory/status` 0,88s, `/api/workspace/agents` 1,58s, `/api/workspace/proposals` 0,46s ; aucun cache n'existait nulle part, côté serveur comme côté client). (1) `memory/status` : `MemoryManager.health_check()` sondait Weaviate/backend, Redis et Neo4j SÉQUENTIELLEMENT, sans timeout — désormais en parallèle (`ThreadPoolExecutor`), chaque sonde bornée à 300ms (`DEFAULT_PROBE_TIMEOUT_SECONDS`) : un service injoignable ne fait plus jamais attendre la route plus que ce délai. `memory_link_status()` gagne un mode rapide par défaut (`probe=False`) qui ne sonde JAMAIS le réseau : il rend le dernier statut sondé (nouveau cache process `grimoire.memory.health_cache`, TTL 25s, invalidé sur toute écriture mémoire — `store`/`upsert`/`update`/`delete`/`store_many`/facts/diary) avec `probedAt`/`probed`/`stale` explicites ; `probe=True` (`GET /api/memory/status?probe=1`) force une sonde fraîche. Trouvé en profilant sur un projet jetable avec Neo4j réellement injoignable : la CONSTRUCTION du client Neo4j (`Neo4jMemoryGraph.__init__` appelle `ensure_schema()`, un vrai aller-retour Cypher) peut à elle seule bloquer ~30s via le propre backoff du driver, avant même que `health_check()` ne s'exécute — `MemoryManager.from_config()` la borne désormais aussi (`_create_memory_graph_bounded`, 3s), un no-op pour tout appel en mode rapide puisque celui-ci ne construit jamais de `MemoryManager`. Corrige au passage un bug latent trouvé par ce même profilage : `RedisHotMemory.health_check()` ne rattrapait que les exceptions builtin (`ConnectionError`/`TimeoutError`/`OSError`) — `redis.exceptions.ConnectionError` n'en dérive pas, et un Redis réellement injoignable crashait toute la route au lieu de rendre `healthy: False`. (2) `agents_view()`/`proposals_view()` (`grimoire.tools.workspace_api`) relisaient et re-résolvaient toute leur surface (agents/skills/overrides, propositions) à chaque appel — nouveau cache générique par signature de mtimes (`grimoire.tools.view_cache`), invalidation naturelle dès qu'un fichier surveillé change. (3) Nouvelle route agrégée `GET /api/fleet` (`grimoire.tools.project_health.fleet_status`, câblée dans `forge_routes.py`) : santé + mémoire (mode rapide) de tous les projets du registre en une seule réponse, calculés en parallèle — remplace les `2×N` appels HTTP que `web/workspace/spaces/piloter.js::loadFleet()` faisait un par un et par projet. Mesuré sur un projet jetable (backend local, Redis/Neo4j pointés vers une IP non routable) : `memory/status` 3108ms → 2,6ms (mode rapide, après une première sonde) ; `agents_view` 58ms → 0,3ms ; `proposals_view` 4,4ms → 0,0ms.
- docs(cockpit): matrice de couverture contrôles × états (issue #536). Inventaire exhaustif construit depuis le code (`web/workspace/*.js`, `workspace_routes.py`, `forge_routes.py`, `project_update.py`, `cmd_cockpit.py`, `proposals.py`), confronté aux tests réels (`tests/e2e/*.py`, `tests/unit/test_workspace_*.py`, `tests/unit/cli/test_cmd_cockpit*.py`, `tests/unit/test_project_upgrade.py`) — `docs/cockpit-coverage-matrix.md`, résumé chiffré régénéré (jamais estimé à la main) par `scripts/cockpit-coverage.py`, gardé synchronisé par `tests/unit/test_cockpit_coverage_matrix.py`. Deux régressions confirmées sur `origin/main` en construisant l'inventaire, chacune avec son issue : le bouton de rail « Preuves » (touche `3`) n'est enregistré par aucun espace (#534), et l'ajout d'un nœud depuis la Bibliothèque de Concevoir ne persiste jamais sur disque malgré le message de succès affiché (#535).
- fix(cockpit): le bouton « Preuves » du rail (touche 3) n'avait aucun effet, sur aucun des six espaces (issue #534, trouvé par la matrice de couverture contrôles × états ci-dessus). `triggerRailAction('evidence', …)` (`web/workspace/shell.js`) cherchait un handler que seul un espace pouvait enregistrer via `ctx.rail.on(…)` — comme « 2 »/bibliothèque, propre à Concevoir — mais aucun des six espaces n'enregistrait `'evidence'` : bouton mort et raccourci mort partout, contraire à la doctrine du cockpit (« aucun contrôle mort visible »). Contrairement à « 2 », « Preuves » n'a pas de sens propre à un espace : `evidence` rejoint désormais `PANELS` aux côtés d'Explorateur/Inspecteur, un panneau tenu par la coque elle-même — pas par ce qui est monté au centre — donc utilisable identiquement depuis les six écrans, avec la mécanique générique déjà existante des trois états (replié/entrouvert/épinglé, largeur mémorisée par espace et par projet). Nouveau panneau `#panel-evidence` (`index.html`) listant les tâches du standard gouverné, l'état de leurs gates et le chemin de leur pack — via `GET /api/workspace/evidence` (nouveau, `workspace_routes.py`/`workspace_api.py::evidence_view`), qui rejoue `grimoire.core.agentic_standard.check_evidence_gates` (la même lecture que `grimoire standard verify`/`grimoire_standard_gate`), jamais une relecture parallèle des fichiers ; `enrolled: false` nomme `grimoire standard init` quand le projet n'en a pas. Cliquer une tâche ouvre son pack dans Source (`goto('source', { file })`) — ce qui a exigé un quatrième étage Source, `evidence` (`_grimoire-output/evidence/`, lecture seule) : sans lui, `file_view` refusait (403) tout chemin sous `_grimoire-output`, le même échec que le bouton qu'il corrige. Nouveaux tests unitaires (`tests/unit/test_workspace_api.py`, nouvelle fixture `governed_project` dans `conftest.py` — `real_project` est mutée par `project_with_task` dans d'autres tests du même fichier, ce qui aurait rendu ceux-ci dépendants de l'ordre de collecte) et e2e (`tests/e2e/test_workspace_shell.py` : la touche 3 ouvre le panneau depuis Piloter, le clic sur une tâche ouvre Source — rouge avant le correctif, vert après).

- fix(cockpit): dans Concevoir, ajouter un nœud depuis la Bibliothèque ne persistait jamais malgré le message de succès affiché (issue #535, trouvé par la matrice de couverture ci-dessus). `addNode()` (`concevoir.js`) ne mutait que `state.blueprint` en mémoire ; corrigé en deux moitiés distinctes, trouvées l'une après l'autre. **Client** : `state.dirty`, posé par `addNode` et remis à `false` au chargement/à l'enregistrement — indicateur « modifié — non enregistré » dans la barre d'outils du graphe, bouton « Enregistrer » (`saveBlueprint()`, désactivé sans modification) qui appelle `ctx.api.blueprintPut` (jamais automatique), et confirmation avant de rouvrir un blueprint modifié (`zoomToWorkflow`, même mécanisme que `source.js::openFile`). **Serveur, trouvé en écrivant le test e2e** : même le client corrigé échouait encore — `_CockpitHandler` (`cmd_cockpit.py`), le seul serveur qui tourne réellement pour `grimoire serve` **et** `grimoire cockpit serve` depuis la fusion #351, ne définissait aucun `do_PUT` : `http.server` répondait 501 avant d'atteindre la moindre garde, y compris sur l'atelier (`readOnly: false`). `forge_http.py::do_PUT` (couvert par `tests/unit/tools/test_forge_server.py`) est un chemin mort en pratique — jamais celui qu'emprunte un navigateur réel. Nouveau `_CockpitHandler.do_PUT` : `PUT /api/blueprints/<id>` routé vers `blueprint_put`, même garde `_HOME_SLUG` que `/api/setup` (un projet du registre qu'on ne fait que regarder reste en lecture seule, #356). `blueprint_put` (`forge_server.py`) gagne aussi une garde structurelle — un corps dont `nodes`/`edges` n'est pas une liste d'objets levait une `AttributeError` non rattrapée (500) au lieu d'un refus explicite (400) — sans devenir une validation de schéma stricte : un nœud tout juste ajouté a un `ref` vide, « à compléter dans Propriétés », un brouillon légitime que le lint (informatif, jamais bloquant) laisse déjà passer. Nouveaux tests : `tests/unit/tools/test_forge_server.py` (structure), `tests/unit/cli/test_cmd_cockpit_blueprint_put.py` (200 sur le projet de lancement, 403 sur un autre projet du registre, 400 sur un corps invalide, 404 hors `/api/blueprints/`), `tests/e2e/test_workspace_concevoir.py` (nouvelle fixture `concevoir_scratch_blueprint` — jamais `workspace-demo`, partagé et jamais réinitialisé pour toute la session e2e : ajouter-et-enregistrer un nœud réel l'aurait pollué pour tous les autres tests du module) — indicateur affiché, persistance après rechargement, confirmation avant d'écraser un brouillon ; rouge avant chaque moitié du correctif, vert après.

## [3.52.1] - 2026-09-15

- feat(cockpit): thème plus coloré « de façon intelligente » (retour direct de Guilhem — « ça manque de couleur, ça paraît terne/sombre » en sombre, « trop sobre, sans vie, teintes trop proches » en clair). Les cinq niveaux de surface (`--bg`/`--e1`/`--bar`/`--e2`/`--e3`, `tokens.css`) avaient un rapport de luminance de 1,06 à 1,17 entre voisins — sous le seuil de lisibilité d'une superposition — dans les deux thèmes ; rouverts à ~1,30, encre secondaire (`--ink2`/`--ink3`) retenue en retour pour garder l'AA. `--acc` (interaction : survol, focus, onglet actif, ligne sélectionnée) valait `--ink` — aucune teinte propre, cause principale du rendu terne — reçoit désormais la teinte de la série 1 (bleu froid), distincte de `--pri` (toujours l'unique orange par écran). Nouveaux vocabulaires de couleur, toujours informatifs — jamais décoratifs, toujours point + mot ou filet fin : six teintes d'identité d'espace (`--id-piloter…--id-source`, filet sous l'onglet actif et liseré de panneau) et des badges sémantiques pour le genre de blueprint (Concevoir), le `risk_profile` des tâches (Exécuter) et le type de proposition — six valeurs distinctes, `agent`/`skill`/`repair`/`memory-link`/`override-migration`/`needs-hosts` — jusqu'ici confondues sous un unique point orange (Piloter). Corrige au passage un contraste limite préexistant : `.cv-node .kind`/`.cv-prim .d` (survol, Concevoir) passent de `--ink3` à `--ink2`. Nouveau test `tests/e2e/test_workspace_shell.py::test_les_surfaces_voisines_s_ecartent_assez_pour_se_distinguer` (rouge sur les tokens d'avant, vert après) mesure l'écart de surface sur le DOM rendu, dans les deux thèmes.
- feat(cockpit): thème, seconde marche plus franche (suite directe de la précédente entrée — accord de Guilhem : « accepté tel quel, puis une seconde marche plus franche »). Quatre changements, chacun mesuré : (1) les écarts de surface repassent de ~1,30 à 1,26-1,42 selon la paire, avec un vrai palier entre panneaux (`--e1`) et cartes (`--e2`) pour que ces dernières se détachent nettement (sombre : `--bar`/`--e2` 1,30→1,41 ; clair : `--bar`/`--e2` 1,42→2,06) ; `--bg` sombre reçoit une teinte froide (bascule R/B, luminance inchangée) et `--bg` clair remonte franchement près du blanc chaud. Coût documenté : l'écart `--ink`/`--ink2` (sombre) se resserre de 1,32 à 1,21. (2) `--acc` se voit désormais sur chaque élément actif, pas seulement une bordure fine : onglet (fond `--accsoft` + filet 3 px), vue (`.seg > button[aria-pressed]`, fond plein + nouveau `--onfill`), ligne sélectionnée (`.tree > .sel`, liseré 3 px ajouté), bouton par défaut (nouveau `.btn[aria-pressed="true"]`, fond plein), champ focus (`.input:focus-within`, nouveau). (3) Les statuts kit/CI/doctor/mémoire (Inspecteur et Flotte), le type de proposition et le nœud du déroulé passent du point + mot à la pastille pleine (`.chip.pill.<état>`, nouvelle primitive shell.css, fond saturé + `--onfill` — mesuré ≥4,5:1 sur les six couleurs, marge la plus juste 5,72:1 sur `--bad`) ; genre de blueprint et `risk_profile` restent en point + mot, hors du périmètre demandé. (4) Le filet d'identité d'espace devient un bandeau plein (`panel-head`, 2 px → 4 px) et le panneau qui a le focus clavier gagne un liseré élargi (`:focus-within`, 3 px, pas d'état posé côté JS). `web/DESIGN-SPEC-workspace-2026-09.md` et `tests/e2e/test_workspace_lot4_spaces.py` (sélecteur `.dot` → `.chip.pill`, même assertion) mis à jour ; harnais de contraste inchangé (texte AA 4,5:1, surfaces 1,25:1 minimum).
- feat(cockpit): thème clair, fond franchement blanc (troisième retouche, clair seulement — malgré des rapports de surface déjà conformes, `--bg` clair mesurait L* 94,7, encore perçu comme gris sur les captures des deux passes précédentes). `--bg` clair remonte à L* 96,5 (luminance 0,912, teinte chaude inchangée) ; `--e1`/`--bar`/`--e3` recomposés par le pas le plus court qui tienne 1,25:1 à chaque paire adjacente — pas un simple report proportionnel, pour rester clair plutôt que retomber dans le gris (`--bg`/`--e1` 1,34→1,27, `--e1`/`--bar` 1,34→1,27, `--bar`/`--e2` 2,06→1,77, `--e2`/`--e3` 1,43→1,27, toutes ≥1,25). `--e2` reste l'unique blanc pur. `--ink2`/`--ink3` inchangées : la surface la plus sombre qu'elles touchent (`--bar`) est désormais plus claire qu'avant, donc plus de marge — 6,67:1 et 5,44:1 mesurés sur `--bar`, contre 5,74:1/4,68:1 avant cette retouche. Thème sombre et pastilles (issues des deux passes précédentes) non touchés.
- feat(cockpit): thème clair, pile inversée (quatrième retouche, clair seulement). Remonter `--bg` trois fois n'avait rien changé à l'écran : `--bg` ne se voit qu'aux marges (`#canvas`), le gris perçu venait de `--e1` (`.pl-kpi`/`.pl-watch-row`/`.pl-table` — les cartes de contenu, L* 87) et `--bar` (`.pl-table th` — les en-têtes, L* 79). La pile est inversée comme un thème clair sobre : `--e1`/`--e2` (cartes) blancs ou quasi (L* 99/100), `--bg`/`--bar`/`--e3` (fond de page et barres) un gris clair proche (L* 90), chacun ≥1,25:1 de son voisin déclaré (`--bg`/`--e1` 1,27, `--e1`/`--bar` 1,26, `--bar`/`--e2` 1,30, `--e2`/`--e3` 1,27). `--bar` ne peut pas atteindre la bande visée à l'origine (L* 93-95) sans redescendre sous 1,25:1 face à `--e1` quasi blanc — contrainte algébrique documentée dans `tokens.css` plutôt que forcée. `--line` clair monte de .13 à .16 (bordures de carte plus marquées, sans le secours d'un fond gris derrière). `--ink2`/`--ink3` inchangées, marge élargie (surface la plus sombre qu'elles touchent, `--bar`, plus claire qu'avant : 9,08:1 et 7,40:1 contre 6,67:1/5,44:1). Sombre et pastilles non touchés.

## [3.52.0] - 2026-09-15

- fix(cockpit): cinq contrôles inertes ou muets de la vue de travail, trouvés par un audit factuel (issue #523). (1) Observer (`spaces/observer.js`) appelait `ctx.docbar.setViews([...], 'runtime')` sans le callback `onPick` (3e argument) — Runtime/Activité/RTK/Bench ne réagissaient à aucun clic ; les trois onglets secondaires n'avaient de toute façon aucun contenu prévu (dead code), ils rendent désormais un flux d'événements réel (Activité) ou l'admettent explicitement (RTK/Bench, aucune route serveur). (2) Mémoire (`spaces/memoire.js`) appelait `setViews` une seule fois, hors de `draw()` : cliquer « Couches » changeait le contenu mais laissait « Store » visuellement actif — `setViews` est désormais rappelé à chaque rendu, comme `executer.js`/`concevoir.js`. (3) `#project-chip` n'avait aucun gestionnaire de clic ; il ouvre désormais la palette sur une section « Projets » mise en tête, et la palette (⌘K) est restructurée en sections titrées (Projets, Espaces, Commandes, Tâches, Workflows, Fichiers) au lieu d'une liste plate de 30+ entrées — le chip gagne aussi curseur/survol/chevron. (4) Le bouton « Suggérer » (`spaces/source-editor.js`) n'était expliqué qu'au survol (`title`) ; il porte désormais une aide permanente, et l'état « modèle configuré mais indisponible » (Ollama injoignable, modèle absent…) — qui faisait disparaître bouton ET statut en entier, sans le moindre indice — affiche maintenant la vraie raison rendue par le serveur. (5) Les états vides de Concevoir et Exécuter (pas de blueprint / pas de Mission Ledger) faisaient disparaître les boutons de vue par early-return ; ils restent désormais visibles (désactivés) et l'état vide cite la commande CLI exacte qui produit la première donnée. Nouveau test générique (`tests/e2e/test_workspace_controls.py`) : clique chaque bouton de chaque `view-seg`/`zoom-seg`, deux fois, dans deux ordres différents, et vérifie `aria-pressed` ET le style calculé — rouge avant le correctif sur Observer/Mémoire, vert après ; couvre aussi le chip projet, l'aide de Suggérer et les états vides.
- feat(upgrade-review): revue de mise à jour — un mission pack pour décider ce qu'un `grimoire upgrade-flow run` a laissé en attente (issues #490/#506/#510, #520). Nouvelle commande `grimoire upgrade-flow review [--json]` (lecture seule) : rend l'écart outil/kit (`tool_version`, issue #515 — refuse de tourner s'il est en retard), le dernier run `project-upgrade` (`grimoire.tools.flow_runs.list_flow_runs`) et les propositions en attente (`grimoire.proposals.list_proposals`, lecture pure disque), sans jamais rien appliquer. Nouveau skill `upgrade-review` (archétype `meta`, attaché au concierge) et prompt mission pack `grimoire-upgrade-review` (projeté par `host sync` : commande Claude Code, prompt Copilot) qui portent le même protocole — une ligne par proposition, questions posées par lot, décision appliquée uniquement via `grimoire proposals accept|reject` et `grimoire flow resume --result` (jamais une écriture directe). Doc : `docs/upgrade.md#revoir-les-décisions`.
- feat(cockpit): bouton « Revoir dans l'IDE » dans Piloter (issue #520, lot 2). À côté de « Mettre à jour », présent dans la page seulement s'il y a une proposition en attente ou un checkpoint destructif en attente (`GET /api/workspace/proposals`/`flows/runs`, déjà chargés par la fiche — aucun nouvel appel réseau). Au clic : copie dans le presse-papiers (repli affiché si indisponible) un texte prérempli `/grimoire-upgrade-review` + l'id du dernier run `project-upgrade` + les slugs des propositions en attente, et montre `grimoire upgrade-flow review` à lancer dans le projet — aucun mécanisme d'ouverture d'IDE n'existe dans ce dépôt (aucun schéma `vscode://`, aucun champ de config d'éditeur), donc aucun lien de ce type n'est inventé. `.pl-preview` renommé `.pl-review-preview` pour ce nouveau bloc — le sélecteur `.pl-preview` des tests e2e existants doit continuer à ne trouver que celui du bouton « Mettre à jour ».

## [3.51.1] - 2026-09-15

- fix(cockpit): trois restes de la validation finale de la boucle de mise à jour (issues #506/#510, PR #511/#513). (1) `POST /api/projects/update` rendait `state: null` sur tout run qui n'était pas le refus d'`apply` — y compris un run complet 9/9 arrêté, non décidé, au checkpoint `destructive`. Nouvelle fonction `_derive_state()` (`tools/project_update.py`), dérivée des mêmes champs structurés que `nodes`/`stoppedAt` (`done`, `stopped_at`, `ok`, `dry_run`), jamais du texte libre : `preview-only`, `upgraded-checkpoint-pending`, `upgraded-but-failed` (relayé tel quel), `failed`, `completed`. (2) Le badge Kit « mis à niveau, checkpoint destructif en attente » (Piloter) était un état purement client, posé une fois après le clic « Confirmer » — perdu à la première navigation Flotte → Projet. `grimoire.tools.flow_runs.list_flow_runs()` pose désormais `status`/`currentNode` (depuis le kernel réel, `FlowEngine.status()`) sur chaque run déjà retenu ; la fiche Piloter dérive le badge, à chaque rendu, du dernier run `project-upgrade` du projet dont le nœud courant est `destructive` en `checkpointed`, avec son `run_id`. `api.flowRuns()` accepte désormais un `project` explicite, nécessaire à la fiche d'un projet consulté depuis la Flotte. (3) Le Dock de l'espace Observer citait `grimoire upgrade-flow status`, une commande qui n'existe pas (`grimoire upgrade-flow --help` n'a pas de sous-commande `status`) — remplacée par la vraie commande, `grimoire flow status <run_id>`, avec l'id du run le plus récent.
- perf(cockpit): la Flotte de Piloter ne rebalaye plus tout le registre à chaque changement de projet (issue #510 point 5). Cas réel : chaque changement de projet dans le cockpit lançait `GET /api/health` + `GET /api/memory/status` pour CHAQUE projet du registre (34 requêtes pour 17 projets, 20 pour 10) — le fan-out vivait dans `loadFleet()` (`web/workspace/spaces/piloter.js`), seul point d'entrée à balayer tout le registre ; sélectionner un projet ne l'a jamais touché, une seule lecture ciblée (`loadSheet`) suffisait déjà. `loadFleet()` gagne un cache mémoire par slug (60 s) : un retour sur la vue Flotte dans la fenêtre sert les données déjà lues plutôt que de tout relire. « Rafraîchir la flotte » (nouveau bouton, `renderFleet`) et une mise à jour de projet confirmée (`invalidateFleetCache`, dans `renderSheet`) restent les deux seules façons de le contourner — la seconde pour que la ligne du projet qu'on vient de mettre à niveau ne reste jamais périmée jusqu'à 60 s.
- feat(doctor,cockpit): écart entre l'outil CLI de l'utilisateur et le kit du projet, désormais nommé plutôt que tu (issue #510 point 4). Cas réel : un homelab piloté par un `grimoire` pipx en 3.50.1 alors que le cockpit (un process séparé, 3.50.2) avait mis le projet à niveau — ni `doctor` ni la fiche projet du cockpit ne le disaient, le badge Kit ne comparait la version alignée du projet qu'au serveur cockpit. Nouveau contrôle `doctor` `tool_version` (`grimoire.cli.app`) : WARN — jamais FAIL — quand l'outil qui exécute la commande est plus vieux que le kit aligné du projet (`grimoire.tools.project_health.tool_version_gap`), nommant `pipx upgrade grimoire-kit` / `pip install -U grimoire-kit`. `project_health.kit_alignment()` gagne `projectTool` : la version que CE projet déclare comme la sienne (`.venv/bin/grimoire` à la racine, ou `tool:` dans `project-context.yaml`), distincte du process qui répond — jamais devinée via `$PATH`. La fiche projet du cockpit (Piloter, Inspecteur « Kit ») affiche cette troisième version quand elle diverge des deux autres, ou « inconnu, vérifiez `grimoire --version` dans le projet » quand rien n'est déclaré.

## [3.51.0] - 2026-09-15

- fix(upgrade-flow): projections hôte fantômes retirées par `orphans`, overrides pleins « fresh » proposés à la conversion (issue #510, points 2 et 6 — troisième rejeu réel, neuf projets). (1) `host sync` n'écrit que ce que son plan actuel résout, et `find_orphans()` partait de `layout.installed_agents` : une projection hôte managée (`.claude/agents/x.md`, `.github/agents/x.agent.md`) dont la source avait disparu de toutes les tiers (kit/overrides/custom, manifeste) n'était donc jamais vue par personne, alors que `grimoire doctor` la signalait déjà (`agents_referenced`) — c'est exactement la forme trouvée sur la Forge. Nouvelle fonction `_ghost_managed_projections()` (`tools/project_upgrade.py`) : toute projection managée dont le nom n'a aucun installé correspondant devient un orphelin comme un autre, archivée sous `_archive/.../orphans/` — jamais un fichier hôte sans le marqueur `grimoire:managed`, jamais une suppression. Choisi dans le nœud `orphans` plutôt que `host sync` : le mécanisme d'archivage y vit déjà, et ce nœud tourne avant `apply`. (2) `propose_override_migrations()` sautait tout override en statut de dérive `"fresh"` (le kit n'a pas bougé depuis la prise), y compris un override **plein** dont le corps est resté une copie exacte du kit — la forme la plus simple à convertir, puisque « fresh » signifie justement que rien n'a changé côté kit. Un homelab réel avait sept overrides pleins de cette forme, chacun ne portant qu'un `context:`, sans qu'aucun ne reçoive de proposition. Chaque override plein reçoit désormais un essai de conversion (`convert_override --dry-run`) quel que soit son statut de dérive ; seul un override déjà partiel (`extends: kit`) est sauté, sans condition — c'est la forme déjà convertie.
- fix(upgrade-flow): ligne de base de `preview` étendue à tous les contrôles `grimoire doctor`, rapport explicite quand `apply` refuse après `up` (issue #510, points 1 et 3 — troisième rejeu réel, neuf projets). (1) La ligne de base que `preview` enregistre (`doctor-baseline.json`) ne couvrait que `paths_resolve` (#502) : sur la Forge, `apply` a refusé sur un FAIL `agents_referenced` préexistant (une projection hôte managée sans source dans aucune tier), sans proposition. Elle couvre désormais tout contrôle doctor — une signature `"<contrôle> : <détail>"` par check (`other_failures`, à côté de `dead_references`, déjà par ligne). Un contrôle déjà en échec au moment de `preview` ne fait plus refuser `apply` : il devient une proposition `repair` nommant le contrôle (`propose_doctor_repairs`, `category: "doctor-preexisting"`) ; seule une régression apparue depuis `preview` échoue encore. (2) Quand `apply` refuse malgré tout, `grimoire upgrade-flow run --json` rendait `done: []`, `stoppedAt: null`, sans proposition ni piste — alors que `up` avait déjà tourné. Il rend désormais `done` (les nœuds réellement exécutés), `stopped_at: "apply"`, `state: "upgraded-but-failed"`, `failing_checks` (le ou les contrôles en cause) et `backup_path` ; un `_grimoire-output/upgrade/<date>/report.md` est écrit avant le refus, commençant par « Mis à niveau, flow en échec sur `<contrôle>`. ». `grimoire.tools.project_update.update_project()` (atelier/cockpit) porte les mêmes `state`/`backupPath` et relit ce rapport. `ApplyResult` gagne `failing_checks` ; nouvelles fonctions `propose_doctor_repairs`/`write_apply_failure_report` (`tools/project_upgrade.py`).
- fix(tests): le registre cockpit réel de la machine (`~/.grimoire/cockpit/registry.json`) contenait sept entrées mortes `x`/`x-2`…`x-7` pointant vers des répertoires `tempfile.mkdtemp()` disparus (`/tmp/tmpXXXXXXXX/...`), en plus de l'entrée `probe` déjà trouvée (#492). Reproduction exhaustive (suite complète `tests/unit`, `tests/test_*.py`, `tests/integration` rejouée sous un `$HOME` simulé instrumenté pour refuser toute écriture hors de l'isolation) : aucune fuite dans les suites pytest telles qu'elles s'exécutent aujourd'hui — l'isolation `HOME`/`GRIMOIRE_COCKPIT_HOME` de `tests/conftest.py` (#153) tient. La forme des chemins retrouvés (`tempfile.mkdtemp()` direct, pas le `tmp_path` imbriqué de pytest) désigne un `grimoire init` manuel sur un dossier jetable, jamais nettoyé — un smoke test ou une session d'agent, sans `--no-cockpit`. Durcissement à la source plutôt qu'un correctif de test isolé : `_maybe_register_cockpit` (`cli/cmd_init.py`, chemin partagé par `init`/`up`) refuse désormais silencieusement (trace `logging.DEBUG`) d'enrôler un projet dont le chemin résolu vit à un ou deux niveaux sous la racine temporaire du système (`tools/project_registry.py::is_scratch_path`, nouveau) — assez large pour couvrir `/tmp/tmpXXXXXXXX/<nom>` et `/tmp/<nom>`, assez étroit pour ne jamais toucher le `tmp_path` isolé de pytest (toujours ≥3 niveaux sous cette racine). Les actions explicites (`grimoire cockpit add`/`create`, `grimoire serve --project-root`) restent inchangées. Le garde de session existant (`tests/conftest.py::pytest_sessionfinish`, #339) couvre désormais aussi l'état cockpit (`cockpit.json`), pas seulement le registre.
- feat(cockpit): décider une proposition (Accepter/Refuser, espace Piloter) fonctionne désormais pour n'importe quel projet du registre depuis le cockpit, pas seulement le projet de lancement direct (issue #506) — première moitié de « fermer la boucle de mise à jour » après le premier test réel du flow `upgrade-flow`. `POST /api/workspace/proposals/<slug>/accept|reject` ouvrait la même garde `_HOME_SLUG` que le reste des écritures de la vue de travail, alors que `POST /api/projects/update` (le bouton « Mettre à jour » juste au-dessus, dans le même écran) écrit déjà dans n'importe quel projet du registre — décider une proposition (jamais la créer automatiquement) est le geste que le cockpit existe pour rendre possible sur toute la flotte. Nouvelle fonction `workspace_routes.is_proposal_decision()` nomme la dérogation ; le reste des écritures (fichier, tâche, override) reste strictement réservé au projet de lancement. Distingue aussi côté UI le type `repair` (issue #502) : une substitution évidente s'accepte comme les autres propositions, une réparation sans substitution évidente n'offre plus que « Refuser » et « Ouvrir le fichier » (lien vers l'espace Source à la ligne citée, avec saut de curseur). Corrige au passage un bug trouvé pendant le test réel : `withProject()` (web/workspace/api.js) ajoutait `project` à la query string même quand l'appelant en avait déjà posé un explicite, produisant un paramètre dupliqué (`?project=terraform&project=grimoire-forge`) qui pouvait faire lire la garde d'écriture sur le mauvais projet.

- feat(cockpit): montrer ce que le flow de mise à jour a fait — deuxième moitié de « fermer la boucle de mise à jour » (issue #506). `/api/projects/update` rend désormais un statut par nœud (`nodes` — `fait`/`proposition`/`sauté`/`erreur`/`checkpoint en attente`, `project_update.py::_node_statuses`) et le chemin de la sauvegarde (`backupPath`, jusqu'ici jeté après avoir servi de `detail` au contrat de flow — `cmd_upgrade_flow.py`, `_fail_run` porte aussi `done`/`failed_node` sur un refus, perdus jusqu'ici par le `_fail` générique). Piloter affiche ce déroulé nœud par nœud, un état « en cours » pendant l'appel (jusqu'à une minute), et rend `preview.md` en Markdown structuré (nouveau module partagé `web/workspace/markdown.js`, extrait de l'éditeur Source) plutôt qu'en texte brut tronqué à 2000 caractères sans indicateur ; le badge Kit passe à « mis à niveau, checkpoint destructif en attente » après un run confirmé, et nomme les fichiers en retard. Corrige un bug trouvé en testant : `options.refresh()`, appelé juste après avoir montré le résultat de la confirmation, redessinait toute la fiche et l'effaçait aussitôt (« l'Inspecteur revient silencieusement à l'état initial ») — retiré du chemin de confirmation, remplacé par un rafraîchissement ciblé des propositions. Corrige aussi « Ouvrir ce projet » (rechargeait toute la page et retombait sur la vue Flotte au lieu d'ouvrir l'écran Projet demandé) en navigation interne (`ctx.goto`), et ajoute `GET /api/workspace/flows/runs` (nouveau module `grimoire.tools.flow_runs`) pour qu'Observer cesse de dire « TraceLedger vide » quand un run de flow existe bel et bien, juste ailleurs que dans le TraceLedger.

## [3.50.2] - 2026-09-14

- fix(upgrade-flow): second rejeu réel du flow `upgrade-flow` sur dix projets (issue #502) — le nœud `orphans` archivait encore des agents vivants, 1/3/13 par projet, quand ils vivaient dans une tier possédée par le projet (`_grimoire/_config/custom/agents/`, legacy) sans être déclarés sous `agents.custom_agents` : `project_upgrade.find_orphans()` ne posait pas la question dans le bon ordre, un agent absent du roster frais ET non déclaré ET hors overrides passait pour orphelin même hors tier kit. Un candidat orphelin doit désormais vivre dans la tier kit elle-même (`_grimoire/kit/agents/`) — la seule que ce nœud archive — en plus des critères existants (roster frais, déclaration). `grimoire doctor` gagne un nouveau check `agents_referenced` (`core/integrity.py`) : un agent que `agent-manifest.csv` ou une projection hôte gérée (`.claude/agents/`, `.github/agents/`, …) nomme mais dont le fichier source a disparu est désormais un FAIL nommé — un projet réel était passé à 25/25 malgré 13 agents manifestés disparus. La génération du manifeste (`core/scaffold.py`) exclut au passage le gabarit vierge `custom-agent.md` (`name: "{{agent_tag}}"` non rendu) qu'elle listait par erreur comme un agent installé.
- fix(upgrade-flow): suite du second rejeu réel (issue #502) — deux défauts supplémentaires. (1) `probe_hook` (`project_upgrade.py`) jugeait le hook SessionStart en erreur dès que la sous-chaîne « erreur »/« error » apparaissait n'importe où dans le JSON rendu ; un projet dont le rappel de tâche mémoire mentionnait une erreur passée (cas réel : TTS-Voice) faisait échouer `apply` sur un hook parfaitement sain. La détection ne porte plus que sur des signaux structurés — clé `error` de premier niveau, ou le marqueur exact `[Grimoire] hook <id> en erreur` (`hosts/decisions/__init__.py::_failed_decision`) en tête d'un des blocs de texte réellement rendus (`additionalContext`, `systemMessage`, `reason`) — jamais une recherche libre dans le texte. (2) `apply` refusait sur des références `_grimoire/...` périmées déjà présentes AVANT la mise à niveau (`_memory/decisions-log.md`, `.github/copilot-instructions.md`, `.claude/skills/*/SKILL.md`), laissant trois projets réels « mis à niveau mais flow en échec » sans proposition. Le nœud `preview` enregistre désormais une ligne de base des références mortes (`_grimoire-output/upgrade/<date>/doctor-baseline.json`) ; `apply` n'échoue plus que sur une régression (référence absente de cette ligne de base) — chaque référence déjà connue devient une proposition `repair` (`propose_repairs`/`_accept_repair`, nouveau `artifact_type`), nommant une substitution vers la tier kit quand elle est évidente (`_suggest_repair_substitution`, jamais une automatique) et une revue humaine sinon. Le rapport de fin du flow (`grimoire upgrade-flow apply`/`run`) dit désormais clairement « mis à niveau, N défaut(s) préexistant(s) en proposition » quand c'est le cas.

## [3.50.1] - 2026-09-14

- fix(upgrade-flow): premier rejeu réel du flow `upgrade-flow` (#490/#491/#492) sur un projet réel (archétype `infra-ops`+`fix-loop`, feature `vector-memory`) — trois défauts, issue #499. (1) Le nœud `orphans` archivait des agents encore vivants (`fix-loop-orchestrator`, `vectus`), cassant le projet jusqu'à réparation manuelle : `cmd_up._infer_resolved()` codait `feature_agents=()` en dur malgré son propre docstring, gelant tout agent de feature (`vectus`) sur `up` et le faisant passer pour orphelin — corrigé par `_available_features()`, qui l'infère depuis le disque comme `stack_agents` l'était déjà. `project_upgrade.find_orphans()` ignorait en plus `agents.custom_agents` (`project-context.yaml`) et la tier des overrides (un agent fully-custom sans base kit) — les deux sont désormais des motifs d'exclusion explicites, en plus du roster frais ; en cas de doute, jamais d'archivage. (2) `backup_project()` écrasait silencieusement le tarball/manifeste d'un run précédent le même jour dont le contenu avait changé depuis (dry-run puis run réel) : un digest SHA-256 du contenu réel décide désormais réutilisation (identique) vs nouveau fichier suffixé `-2`/`-3`/… (différent) — le nom canonique, une fois écrit, n'est plus jamais réécrit. (3) `grimoire upgrade-flow --help`/`run --help` décrivent désormais l'ordre des 9 nœuds, la distinction V0 (mécanique)/V1 (proposition)/V2 (checkpoint) et l'emplacement de la sauvegarde.

## [3.50.0] - 2026-09-14

- feat(cockpit): mettre à jour un projet depuis le cockpit lance le flow, pas `up` seul (#490). `POST /api/projects/update` (`src/grimoire/tools/project_update.py`, deux hôtes — `cmd_cockpit.py` et `forge_server.py`, câblage inchangé) appelle désormais `grimoire upgrade-flow run` en sous-processus : aperçu par défaut (`--dry-run`, la réponse porte le contenu exact de `preview.md`, jamais un résumé reformulé), `confirm: true` pour le flow complet sous `--executor interactive` (mécanique, arrêté au checkpoint `destructive` comme en CLI — la réponse porte alors `report.md` et les propositions encore `pending`). Projet non enregistré ou chemin hors registre : refusé (`404`), jamais un repli sur le projet servi. Le bouton « Mettre à jour — aperçu » de l'espace Piloter garde son nom ; les propositions qu'un run complet écrit apparaissent dans sa section Propositions dès le redessin qui suit la confirmation, sans mécanisme neuf. `docs/upgrade.md` gagne la section « Depuis le cockpit ».
- feat(upgrade): mettre à jour un projet est un flow, pas un `up` nu (issue #490). `registry/blueprints/project-upgrade.blueprint.json` : sauvegarde (tarball + manifeste SHA-256 de `_grimoire/_memory/`) -> aperçu (`up`/`host sync --dry-run`) -> orphelins (agents/wrappers `grimoire:managed` que le kit installé ne livre plus, archivés sous `_archive/<date>-pre-<version>/orphans/`, jamais supprimés — avant `apply`, sinon la garde de distinction de `up`/`host sync` refuse) -> application (`up`, acceptance : doctor vert + hook SessionStart sans erreur) -> deux nœuds de jugement V1 qui ne font jamais qu'écrire une proposition via le mécanisme `proposals` existant (nouveaux `artifact_type` `override-migration` et `memory-link`, jamais d'écriture directe) : overrides en dérive (convertir en partiel si le corps est identique au kit, sinon revue humaine avec diff) et fiches mémoire non raccordées à un `context:` -> besoins/hôtes non déclarés (proposition `needs-hosts`) -> vérification (rapport final, manifeste recomparé) -> checkpoint forcé (`kind: "checkpoint"`) pour tout retrait au-delà d'un archivage. Nouvelle commande `grimoire upgrade-flow` (nom distinct de `grimoire upgrade`, déjà pris par la migration de structure v2→v3) : `run [--dry-run] [--executor interactive|dispatch]`, plus les utilitaires `backup`/`preview`/`apply`/`orphans`/`propose`/`verify`/`check`/`probe-hook` que les acceptances du blueprint invoquent elles-mêmes. `grimoire.cli.cmd_up.fresh_kit_agent_roster` (nouveau) diffuse le plan du scaffolder sans rien écrire, pour que l'orphelinage se détecte sans dépendre d'un `up` déjà passé. `grimoire.proposals` gagne `create_manual_proposal` (propositions hors déclencheur de non-choix) et le traitement `accept_proposal` des trois nouveaux `artifact_type` (`override-migration`, `memory-link`, `needs-hosts`) : un `memory-link` accepté sur un agent kit sans override lui en crée un **partiel** (`extends: kit`, `kit_source_hash`, issue #427) ne portant que `context:` — jamais un refus pour absence d'override, seul un porteur non trouvé refuse encore ; un agent déjà overridé voit sa fiche ajoutée au `context:` existant. `needs-hosts` écrit désormais `hosts.enabled` dans `project-context.yaml` par round-trip (`grimoire.tools._common.load_yaml_roundtrip`/`save_yaml`, #430 : commentaires intacts) quand la proposition porte une valeur mécanique réelle (détection sur disque) ; `needs.commands` reste, sciemment, un refus nommé — un besoin non résolu n'a par définition aucune commande à écrire, en inventer une contredirait `grimoire.core.execution_needs`. Corrige au passage `grimoire blueprint validate` : sa liste de `kind` connus n'avait pas suivi les sept genres de node (#207/#488), refusant tout blueprint qui en déclare un comme « unknown kind » malgré un schéma qui l'acceptait déjà.
- fix(tests): `real_project` (fixture) était sondable par un vrai backend mémoire réseau sur l'hôte qui lance la suite — `_init_real_project` (`tests/conftest.py`) appelait `grimoire init` sans `--backend`, donc `auto` : `detect_memory_backend()` sonde de vrais ports localhost (Weaviate `:8080`, Qdrant `:6333`, Ollama `:11434`), hors du périmètre que `_isolate_user_state` protège. Sur un poste où l'un de ces backends répond réellement, `TaskService.recall()` remontait des `decisions`/`failures` du poste sans rapport avec le projet jetable du test : `test_le_rappel_d_une_tache_neuve_est_honnetement_vide` et son jumeau e2e échouaient en local, jamais en CI (ports injoignables). Épingle `--backend local` (mémoire fichier) dans `_init_real_project` ; garde de régression `test_le_projet_reel_n_a_pas_de_backend_memoire_sondable_sur_l_hote`. Aucun changement côté `src/` (#494, closes #493).

## [3.49.0] - 2026-09-14

- feat(flows): les sept genres de node que le statique ne sait pas exprimer — lot 4 de l'épic moteur de flows (#207). `kind` gagne `fanout` (un node produit N éléments via `fanout_items`, N sous-flows instanciés, résultats recollés, N borné par `pilot.max_fanout_n` — absent, refus), `verify-panel` (k vérificateurs indépendants sous angles imposés, `config.verifyPanel.k`/`.angles`, majorité requise, coût = k tentatives toujours, jamais un vert sur 1/k), `loop-until-dry` (relance jusqu'à `config.loopUntilDry.maxRounds` tours sans `novelty_key` neuve, dédupliquée contre tout le vu), `judge` (N tentatives sous angles imposés, `config.judge.n`/`.angles`, le node lui-même redispatché comme juge V1 qui désigne `judge_winner`, la trace des N survit dans leurs propres runs), `checkpoint` (`approve`/`reject`/`amend`, toujours une décision d'hôte forcée — jamais auto-décidée par la cascade, `reject` bloque en nommant le motif dans l'événement de refus du kernel), `budget` (`config.budget.maxCostUsd`/`.passes` — première passe obligatoire, les suivantes abandonnées avant dépassement, jamais tentées au-delà) et `replay-diff` (même flow rejoué deux fois sur la même entrée, `replay_diverged` expose une divergence sans faire échouer le node). Six des sept genres généralisent le lancement de sous-flow déjà posé par `composite` (#206, `_launch_child`, factorisé) ; `checkpoint` seul n'a pas de `ref`. Paramétrage refusé nommément au chargement (`blueprint_loader._validate_genre_config`) ; `flow extract` ne les aplatit jamais. Nouveau module `flows/genres.py`, compagnon de `flows/dispatch_executor.py` : le cliquet de code (`scripts/check-code-ratchet.py`, R2, seuil 1500 lignes) a refusé de laisser `dispatch_executor.py` grossir au-delà (1695 lignes) — même geste que la scission historique de `forge_server.py`. Aucun changement Rust : orchestration Python (E/S, runs enfants), hors périmètre des fonctions pures déjà portées par `grimoire-flows-core`.
- feat(flows): un flow est un node — un node `kind: "composite"` (déjà réservé par le schéma pour un sous-flow, jusqu'ici jamais exécuté par le moteur) lance son `ref` (`.blueprint.json`, id de registre local, ou refus nommé pour `use-case:` — réservé au Studio) comme un run enfant à part entière, lié au parent (`FlowRunMeta.parent_run_id`/`parent_node_id`), dont le coût est remonté et plafonné par le pilote sur le **total** du sous-flow ; profondeur de composition bornée à 3, cycle ou référence introuvable refusés au chargement, jamais au node. `flow extract` ne l'aplatit jamais (`kind`/`ref` préservés, `extraction.child_run_id` tracé). `grimoire.flows.registry.describe_flow` + `flow list` affichent, par flow, sa version, sa plage de compatibilité kit (`kitMin`/`kitMax`), l'empreinte SHA-256 du fichier et l'union des besoins d'exécution (`run_need`) du flow et de ses sous-flows ; `flow list --require-measure` couvre un sous-flow sans code neuf (même clé `blueprint_id` que #473). Aucun changement Rust (#206).
- fix(policies): rechute du 2026-09-12 (#463), constatée le 2026-09-14 sur Grimoire-Forge : une session dont `per_session.max_duration_min` (`block`, sans `tool_pattern`) avait dépassé sa fenêtre refusait `git status` (lecture) et — surtout — `grimoire policies reset-session`, la commande que son propre message de refus recommandait. L'exemption de réparation (`_is_repair_exempt`, miroir Rust `is_repair_exempt`) couvrait déjà tout appel non mutant et l'édition de `_grimoire/standard/policies.yaml`/`_grimoire-output/.runs/session-*.json`, mais pas l'invocation de `grimoire policies` elle-même : son premier mot (`grimoire`) n'est reconnu ni lecture seule (`is_read_only_command`) ni comme ciblant l'un des deux fichiers exemptés. Reconnaît désormais l'invocation de `grimoire policies` (`status`, `reset-session`) dans ses trois formes documentées (`grimoire policies …`, `<venv>/bin/grimoire policies …`, `python -m grimoire policies …`), en n'inspectant que le premier mot de chaque segment shell — jamais une commande qui se contente de la *nommer* (`echo grimoire policies …` reste refusé). `grimoire doctor` signale désormais (`WARN`, `policy_budget_duration_guard`) toute règle `per_session` sans `tool_pattern` en `block` dont `max_duration_min` est inférieur à 2880 min (48h), une session Claude Code pouvant rester ouverte plusieurs jours. (#481)
- feat(cockpit): agrégation mémoire multi-projets — dernier volet de l'issue #172 (« Cockpit — du générateur statique au portefeuille actif », Refs #468). `GET /api/workspace/memory/overview?projects=all|<slugs>` (`src/grimoire/tools/workspace_memory.py`) rend, par projet du registre de la machine, backend configuré/résolu, nombre d'entrées, dernière écriture et état de l'index lexical (primaire/compagnon/absent) — réutilise `memory_link_status()` et `MemoryManager`, aucun nouveau lecteur ; un projet dont la mémoire est absente ou illisible est listé avec sa raison, jamais compté comme un store vide en silence (#264). `GET /api/workspace/memory/search?q=...&projects=...` interroge la même chaîne que `grimoire memory search` (`run_memory_search`, hybride si le projet a un compagnon lexical) projet par projet et fusionne seulement la LISTE de résultats, étiquetés par `projectSlug`, ordre stable (score puis slug) — jamais de fusion des stores. L'espace Mémoire (`web/workspace/spaces/memoire.js`) gagne un onglet Flotte : zoom docbar « Ce projet / Tous les projets », tableau agrégé, recherche croisée dont les résultats s'ouvrent dans le panneau d'inspection avec leur projet d'origine. Lecture seule ; `cmd_cockpit.py` non modifié (les deux routes passent par la table `workspace_routes.GET_ROUTES` déjà partagée par l'atelier et le cockpit).

## [3.48.0] - 2026-09-13

- feat(flows): un node de flow déclare un besoin d'exécution (`{"run_need": "test-runner"}`) plutôt qu'une commande en dur — lot 2 de l'épic moteur de flows (#205). Catalogue fixe (`test-runner`, `lint`, `typecheck`, `build`, `migration-tool`, `format`, `grimoire.core.execution_needs`), résolu en deux temps au chargement du blueprint : `needs.commands` déclaré dans `project-context.yaml` (schéma/validateur/Rust en parité, `rust/grimoire-schema-core/`) l'emporte toujours sur la détection par marqueur de projet (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`). Un besoin non résolvable **refuse le chargement du blueprint entier** en le nommant, avant que le premier node soit présenté à l'hôte — jamais une installation qui échoue au troisième node. `grimoire needs resolve` affiche le verdict par besoin et sa source. Rétrocompatible : les blueprints à commandes en dur restent valides ; `grimoire flow run` avertit (jamais un refus) quand une commande en dur correspond à un besoin résolu pour ce projet. Aucun changement côté `rust/grimoire-flows-core/` : la résolution d'un besoin est une lecture disque (config + marqueurs), donc hors périmètre des fonctions pures déjà portées (`check_output_against_contract` ne voit que les pins, jamais l'acceptance).
- feat(flows): `grimoire flow extract <run-id> [--out <fichier>]` — un flow s'extrait d'un run, il ne se dessine pas (transversale T2, issue #210). Joint les métadonnées du run (`FlowRunMeta`) au Mission Ledger (`node_dispatch_history`) pour produire un blueprint brouillon dans le format `.blueprint.json` existant : l'acceptance d'un node **complété** par la cascade est remplacée par la commande réellement exécutée, réinférée en `{"run_need": ...}` sur correspondance caractère pour caractère avec un besoin résolu (#205) — jamais sur une correspondance partielle (une commande observée avec des arguments en plus reste `{"run": ...}` verbatim, deviner la coupure serait une invention). Un node **jamais dispatché** (classe V2, ou exécuté par un hôte interactif) garde son acceptance d'origine verbatim ; un node **jamais atteint** (run incomplet ou abandonné) est marqué `not_reached`. Chaque node du brouillon porte un objet `"extraction"` (`status`, `note`) traçant lequel de ces trois cas s'est produit — jamais une inférence depuis la prose. Le brouillon extrait est rejouable : `flow run`/`resume` le chargent comme n'importe quel blueprint et reproduisent la même séquence de nodes que le run source. Aucun changement côté `rust/grimoire-flows-core/` : l'extraction ne fait que lire `FlowRunMeta`/le Mission Ledger via l'API publique déjà existante de `FlowEngine` (`run_meta`, `status`), aucune nouvelle fonction pure candidate au portage.
- feat(flows): le flow porte sa preuve, le registre local l'affiche — lot 5 réduit de l'épic moteur de flows (#208). `TraceLedger.dispatch_outcome_stats()` gagne une troisième ventilation `by_flow` (en plus de `by_class`/`by_provider`, #442) : le préfixe `blueprint_id` du tag `replay:<blueprint_id>:<node_id>` qu'un node de flow porte déjà (#205/#210), jamais un dispatch hors flow (qui retombe sur l'id de tâche, sans `:`). `grimoire dispatch stats` affiche cette ventilation ; nouvelle commande `grimoire flow list [--require-measure <blueprint-id>]` — le registre local des flows (runs connus groupés par blueprint, dernier run, nœuds résolus/coût/escalade quand une mesure de dispatch existe) ; `--require-measure` refuse (sortie 1) si le blueprint désigné n'a aucune mesure. Calculé en Python dans les deux backends (jamais un sixième port Rust : une dimension de plus sur les mêmes enregistrements que `by_class`/`by_provider`, pas une fonction assez chaude pour le justifier) — vérifié : `to_dict()` reste identique entre `GRIMOIRE_TRACES_BACKEND=python` et `=rust` sur le corpus de parité. Pas de nouveau registre publié (#206 reste verrouillé) : une lecture locale, jamais un second calcul.
- feat(flows): le pilote — la fonction qui décide combien dépenser par node, pas une couche (transversale T1, issue #209). Nouveau module `flows/pilot.py` : `decide()` nomme et rassemble la décision déjà éparpillée entre `missions.dispatch.start_tier_for`/`_tier_chain` (inchangées, toujours pures, toujours portées Rust — `grimoire-dispatch-core`) et le refus V2 de `flows.dispatch_executor` ; une politique de projet optionnelle `_grimoire/standard/pilot.yaml` (`start_tier` par classe, `max_escalations`, `max_cost_usd_per_node`) la paramètre, absente elle ne change rien. `missions.dispatch.run_dispatch` gagne le mécanisme réellement neuf : un paramètre `max_cost_usd` qui arrête l'escalade entre deux paliers (jamais au milieu d'une tentative, jamais après un vert déjà acquis) avec un refus nommé (`DispatchReport.cost_capped`, même mécanique que `unrunnable` — aucun changement Rust requis). `DispatchExecutor` charge la politique une fois par run ; `--max-tier` explicite de la CLI reste prioritaire sur le plafond d'escalade de la politique. Aucune CLI nouvelle. Un `pilot.yaml` malformé refuse nommément le chargement plutôt que de retomber en silence (contrairement à `orchestration-policy.yaml` : un plafond de coût est un mécanisme de sécurité).
- fix(framework): cinq parseurs mémoire de `framework/tools/` avalaient `OSError`/`UnicodeDecodeError` et rendaient `[]`/`None`/`continue` — un fichier illisible (encodage étranger, verrouillé, tronqué) disparaissait du résumé, de la synchro Qdrant et de l'index RAG sans une ligne, `"errors": []` en tête. `SectionParser.parse_file` (`context-summarizer.py`), `MemoryParser.parse_file` (`memory-sync.py`) et `ChunkingStrategy.chunk_file` (`rag-indexer.py`) prennent désormais un `errors: list[str] | None` optionnel, nommant le fichier et la cause dans `report.errors` côté `summarize()`/`push()`/`index_collection()` (chemins déjà outillés d'un rapport). `ToolDiscoverer._inspect_python_tool`/`_inspect_shell_tool`/`_inspect_markdown_tool` (`tool-registry.py`) et le fallback fichier de `rag-retriever.py` (aucun rapport structuré sur ces chemins) écrivent la même information sur stderr. Diff net nul sur chacun des cinq fichiers (`framework/` gelé, shrink-only) : le garde anti-boucle est fusionné avec le `return`/`continue` d'origine sur la même ligne. (#264)
- fix(framework): un pheromone board corrompu de `framework/tools/` (`stigmergy.py` et `stigmergy_hooks/scripts/stigmergy_hook.py`) était remplacé par un board vide au dépôt suivant — `load_board` rendait un board vide sur `JSONDecodeError`/`OSError` sans le signaler, et `deposit_pheromone`/`save_board` écrasait ensuite le fichier corrompu avec un board ne contenant que le nouveau signal : une écriture tronquée (disque plein, deux hooks concurrents) détruisait tout l'historique stigmergique, `deposit_pheromone` rendant une `Pheromone` comme si de rien n'était. `load_board` met désormais le fichier corrompu de côté (`<nom>.corrupt-<horodatage>`) avant de rendre un board vide, et l'annonce sur stderr. La copie `src/grimoire/tools/stigmergy.py` a le même correctif depuis #270 ; celle-ci porte le correctif dans `framework/` (gelé, shrink-only) à diff net négatif sur les deux fichiers (garde compacté sur la même ligne que le `return`, plus deux signatures et un appel resserrés sur une ligne). (#265)
- fix(cli): `grimoire.sh help` bloquait indéfiniment (timeout 30s) sous Git Bash Windows — `find_project_root()` remontait l'arborescence via `dirname` jusqu'à `/`, mais sous Git Bash Windows `dirname` peut cesser de progresser (ex. se stabiliser sur `.`) avant d'atteindre `/`, transformant la boucle en boucle infinie. La boucle s'arrête désormais dès que `dirname` ne progresse plus, en plus de la condition d'arrêt à `/`. `TestGrimoireShRouting` repasse de `@requires_bash_posix_only` à `@requires_bash` (couverte sous Windows) (#461).
- fix(tests): `TestInstallSh::test_install_sh_has_shebang` levait un `UnicodeDecodeError` cp1252 sous Windows — `INSTALL_SH.read_text()` sans `encoding=` explicite décodait le fichier (légitimement UTF-8, avec des caractères accentués) selon la locale par défaut de Windows plutôt qu'en UTF-8 (même famille que #192). `read_text(encoding="utf-8")` corrige le test ; `install.sh` lui-même n'a pas changé. `TestInstallSh` repasse de `@requires_bash_posix_only` à `@requires_bash` ; `requires_bash_posix_only` n'ayant plus d'utilisateur (dernier hors-scope #231 traité), il est retiré (#462).

## [3.47.0] - 2026-09-13

- feat(cockpit): le wizard de setup (espace Piloter) exécute réellement le projet au lieu d'écrire une commande à copier-coller — `POST /api/setup` appelle `grimoire up` en direct (`cmd_up.run_up_pipeline`, jamais un sous-processus), needs transmis (B3 rebranché sur B2, `GET /api/needs`), refus fail-closed avant toute écriture (archétype/backend/need inconnu, chemin non inscriptible), rapport d'exécution persistant (`_grimoire/setup-run.json`, doctor compris) affiché dans la fiche projet. `_select_cwd_project` honore désormais un `--project-root` explicite même sur un dossier vierge (sans quoi aucune écriture n'était jamais possible sur le cas que l'issue décrit) ; le mode « copier-coller la commande » reste un repli explicite (#171).
- feat(standard): le need `project-discovery` scaffolde réellement `_grimoire/cadrage/` par appel de code direct (`cmd_up.py::_step_cadrage`), plus par une simple suggestion textuelle de `needs_suggest.py`. Sa complétude devient un contrôle du standard agentique (`_verify_cadrage`, dimension `artifacts`) visible dans `standard verify`/`gate`/`score`/`audit` : `info`/`warning` par défaut, `error` sur les phases gate (exigences, cahier des charges) uniquement quand `project-discovery` a été explicitement choisi. Documentation dédiée `docs/cadrage.md`, référencée depuis le README et `docs/cli-reference.md` (#173).
- feat(cockpit): « nouveau projet » depuis le portefeuille (espace Piloter, niveau Flotte) — un bouton ouvre le même formulaire que le wizard de setup (#171) sur un chemin choisi ; `POST /api/projects/create` (cmd_cockpit.py) exécute le plan sur ce chemin (chaîne `execute_setup_plan`/`grimoire up`, comme `/api/setup`), refuse un chemin hors des racines permises ou déjà un projet Grimoire, puis enregistre le projet dans le registre — `grimoire up`/`init` l'enrôlent comme n'importe quel autre. Le sélecteur de projets (le tableau Flotte) le montre aussitôt, sans redémarrer le cockpit. Point 1 (API réelle de lecture) déjà livré par #143 ; l'agrégation mémoire multi-projets (point 3) reste hors périmètre — toujours des lectures projet par projet (#172).
- feat(hosts): déclarer les hôtes activés (`hosts.enabled` dans `project-context.yaml`, schéma/validateur/Rust en parité), détection filesystem par défaut quand la clé est absente (`claude` seul si rien n'est détecté), `grimoire init` écrit la clé avec le résultat de la détection. `grimoire host sync`/`up` n'émettent plus que les hôtes activés ; un `--host <x>` désactivé est un refus nommé sauf `--force-host` ; `grimoire host status --host all` liste les fichiers orphelins d'un hôte désactivé, `grimoire host sync --prune-disabled` les retire (opt-in). `grimoire doctor` ajoute INFO (orphelins) et WARN (hôte activé sans fichier émis). Alternative « canal plugin » Claude Code écartée par décision (#177).
- fix(policies): quatre défauts de conception de la règle de budget de session, révélés le 2026-09-12 par un incident réel sur la Forge (`per_session: {max_writes: 800, max_tool_calls: 3000}` sans `tool_pattern` a fini par refuser tous les outils de la session, y compris l'édition du fichier de règles qui la portait). (1) `max_writes` comptait tout appel `Bash` portant une commande, lectures comprises (`cat`, `grep`, `find`, `git status`...) ; `classify_tool` distingue désormais un `Bash` de lecture seule (`grimoire.hosts.decisions.tool_facts.is_read_only_command`), conservateur par construction. (2) Une règle `per_session` en `block` n'atteint plus jamais un appel non-mutant ni une écriture ciblant `_grimoire/standard/policies.yaml`/`_grimoire-output/.runs/session-*.json` — exemption de réparation codée en dur, miroir Rust compris (`rust/grimoire-policies-core`). (3) `per_session.subagents` (`shared`/`separate`) nomme le partage de budget entre une session et ses sous-agents (le payload de hook de Claude Code ne les distingue pas) ; `separate` échoue au chargement (`GR-POL-003`) plutôt que de simuler une isolation absente. (4) `grimoire policies reset-session [--session-id]` supprime l'état d'une session, `grimoire policies status` avertit sous 10 % de plafond restant, et le message de refus d'un budget nomme désormais cette commande et le fichier de règles. `grimoire doctor` signale (`WARN`) toute règle `per_session` sans `tool_pattern` en `verdict_on_match: block` (#463).
- fix(cli): sous Windows, `grimoire-init.sh reset`/`uninstall`/`quick-update` restaient non prouvés -- les seize (puis vingt-deux) tests correspondants etaient satures depuis #256 sans jamais avoir tourne verts. La vraie cause n'etait pas les commandes elles-memes (elles repondent en <1s une fois invoquees) mais le harnais de test : `subprocess.run(..., timeout=30)` tue bien le `bash` direct a l'expiration, puis redraine les tubes sans timeout -- si un petit-fils Windows (`cp.exe`, `mkdir.exe`...) tenait encore le tube stdout/stderr ouvert, ce second appel bloquait indefiniment le job jusqu'a son plafond. `_run()` tue desormais l'arbre de processus complet (`taskkill /T /F`) avant de redrainer. Les vingt-deux tests des trois commandes tournent et passent sous `windows-latest` (matrice restauree, job bloquant conserve) ; `grimoire.sh help` et `install.sh` restent hors scope avec leurs propres bugs Windows distincts (#461, #462) (#231).

## [3.46.1] - 2026-09-12

- feat(cli): `grimoire init` et `grimoire up` enrôlaient chaque projet dans le registre cockpit réel (`~/.grimoire/cockpit/registry.json`), même les jetables (`/tmp`, un scratchpad, une recette) — seule la variable d'environnement non documentée `GRIMOIRE_NO_COCKPIT` pouvait l'éviter, et rien ne la mentionnait à côté des options des deux commandes. Ajoute `--no-cockpit` à `init` et à `up` (même effet que la variable, documentée au même endroit dans `--help`) (#305).
- fix(cli): `grimoire up` annonçait `refresh: done` alors que `host sync --dry-run` trouvait encore des dizaines de fichiers à écrire pour Claude Code, Copilot, Codex, Cursor et Gemini CLI juste après — `refresh` ne régénère que le tier kit (`_grimoire/kit/`), pas les surfaces hôtes qui en découlent (chemin de code séparé, `grimoire.hosts.emitters`). `up` exécute désormais une étape `host_sync` après `refresh` et `standard` qui appelle la même synchronisation que `grimoire host sync --host all` ; `host sync --dry-run` exécuté juste après `up` ne trouve plus rien à écrire (#296).
- fix(cli): un projet qui déclare déjà l'archétype `agentic-standard` restait en FAIL permanent après `grimoire up` — le profil `starter` (choix par défaut sans `--needs`) ne couvre pas les acceptance criteria hard de la DNA de cet archétype (`llm-provider-registry.yaml`, `compliance-declaration.md`). `up` résout désormais le need `provider-neutral` (-> profil `controlled`) par défaut quand cet archétype est déjà déclaré ; `knowledge-source-registry.yaml`, écrit seulement à partir du profil `orchestrated`, rejoint les chemins « déclarés avant usage récurrent » que `doctor` ne réclame plus tant qu'aucune source n'est indexée (#295).
- fix(cli): `grimoire doctor -o json .` (l'option après le sous-commande, telle que documentée par `doctor --help`) échouait avec « No such option: -o » — seule la forme globale `grimoire -o json doctor .` fonctionnait. `doctor` accepte désormais un `--output`/`-o` local (même contrat que le `-o` global : mêmes contrôles, même structure JSON) (#293).
- fix(policies): `tool_pattern` des politiques temporelles n'était comparé qu'au nom nu de l'outil, si bien qu'un motif documenté comme `Bash(git push:*)` ou `Bash(rm:*)` ne matchait jamais — `require_approval: true` répondait `allow` dès le premier appel. Introduit une clé d'outil façon permissions Claude Code (`Bash(<commande complète>)`, `<Tool>(<file_path>)`) contre laquelle le motif parenthésé est désormais comparé, rétro-compatible avec les motifs nus (`"*"`, `"Bash"`) (#449).
- fix(core): déclarer `source.assist` (`model`, `allow_lan`) dans le schéma et le validateur, Python et Rust, pour que `grimoire check .` accepte l'exemple documenté de l'assistant Source au lieu de refuser `source` comme clé inconnue (#451).
- fix(cockpit): l'assistant Source distingue le chargement du modèle local d'un dépassement de délai — `GET /api/workspace/assist` sonde `/api/ps`, déclenche lui-même le chargement (`keep_alive`, sans générer) et répond « chargement du modèle » ; le bouton **Suggérer** reste visible mais désactivé et se re-sonde toutes les 3 s au lieu d'échouer au premier clic (#450).

## [3.46.0] - 2026-09-12

- **feat(policies): politiques temporelles par session sur la médiation d'outils — budgets, approbation préalable, refroidissement (#439).**
  Point 3 de l'audit de positionnement 2026-09-12 : `PolicyRule` gagne quatre
  clés optionnelles et rétrocompatibles (`tool_pattern`, `require_approval`,
  `per_session`, `cooldown_after`, voir `_grimoire/standard/policies.yaml`),
  validées au chargement (`GrimoirePolicyError` nommée sur clé inconnue). L'état
  de session (compteurs, approbations, horodatages — jamais de secret ni de
  contenu d'outil) vit dans `_grimoire-output/.runs/session-<id>.json`, écrit
  atomiquement, remis à zéro à `SessionStart` ; un fichier absent ou corrompu
  redevient une session neuve. La décision pure (règle + état → verdict) est
  portée à l'identique en Python (`grimoire.policies.temporal`) et en Rust
  (`rust/grimoire-policies-core`, `evaluate_temporal`), testée en parité ; le
  hook `PreToolUse` reste sous +5 ms de surcoût mesuré. `grimoire policies
  status` affiche les compteurs et budgets restants de la session — le
  cockpit n'est pas dans ce lot.

- **feat(hosts): un override d'agent peut désormais rester partiel (`extends: kit`) et signale sa dérive au lieu de figer silencieusement une copie (#427).**
  Migration réelle 3.38.0 → 3.44.2 : quatre overrides en copie intégrale
  n'avaient plus reçu une seule mise à niveau de leur agent depuis des mois,
  `doctor` étant 22/22. Trois changements : (1) tout override écrit par un
  chemin qui comprend le kit (cockpit, `grimoire agent override convert`)
  enregistre `kit_source_hash:` (empreinte tronquée du fichier kit au moment
  de l'écriture) ; `doctor` et le cockpit comparent cette empreinte à
  l'actuelle et signalent en WARN (jamais FAIL) une dérive, avec un résumé
  (sections de frontmatter ajoutées/retirées, delta du corps, ou champs
  figés pour un override partiel), et en INFO une empreinte inconnue
  (override antérieur à cette issue). (2) `extends: kit` dans le frontmatter
  d'un override ne redéfinit plus que les champs qu'il liste
  (`model_affinity`, `context`, `skills`, `tools`, `use_when`,
  `dont_use_when`, `max_turns`, `description`, `tool_boundary`) — le corps et
  le reste du frontmatter viennent du fichier kit de même nom, fusionnés
  dans `hosts/collect.py` (dicts Python, avant tout appel au port Rust
  optionnel — parité inchangée sous `GRIMOIRE_HOSTS_BACKEND=rust`) ; un
  `extends: kit` sans agent kit de même nom refuse au chargement, nommant
  l'agent. Le cockpit (assigner un skill, éditer une clause) écrit désormais
  un override partiel dès qu'un agent kit du même nom existe, une copie
  intégrale sinon. (3) `grimoire up` liste, après avoir rafraîchi le palier
  kit, les overrides à revoir — sans jamais les toucher — et
  `grimoire agent override convert <nom> [--dry-run]` convertit une copie
  intégrale en override partiel équivalent, refusant (lignes citées) quand
  le corps de la copie a divergé du kit plutôt que de fusionner du texte.

- **feat(dispatch): coût par tâche résolue et pass^k, comptabilité continue et contrôle dans les gates (#442).**
  Point 5 de l'audit de positionnement du 2026-09-12 : le kit dispatchait déjà des tâches en cascade sans jamais agréger en continu ce que ça coûte ni si ça marche de façon fiable — seules des campagnes d'évals manuelles (#308) répondaient à ces questions. `grimoire task dispatch`/`grimoire flow run --executor dispatch` écrivent maintenant un événement `dispatch.outcome` par cascade réellement tentée dans le journal de traces (classe, paliers tentés, coût total, verdict d'acceptance, résolu ou non — aucun contenu de prompt). Nouvelle commande `grimoire dispatch stats [--since 30d] [--json]`, sœur de `providers history` : coût par tâche résolue, taux d'escalade, part d'inexécutable (par classe et par fournisseur), et pass^k sur les nœuds rejoués (une série est « toute au vert » seulement si toutes ses exécutions ont résolu la tâche). Les agrégations pures vivent dans `rust/grimoire-traces-core/` (extension du sixième port), avec parité de test sous les deux backends. Le standard gagne le contrôle `dispatch.cost_slo` (pattern `provider-cost-slo`) : `INFO` faute de données, `WARN` en cas de dépassement (coût ou pass^k), `FAIL` uniquement si le projet déclare `dispatch_cost_slo.enforce: true` — jamais bloquant par défaut. Le cockpit n'est pas concerné par ce lot.

- **feat(cockpit): timeline unifiée par tâche dans la vue de travail, l'export OTel de #322 devient une source lue plutôt qu'un mécanisme mort (#139).**
  Audit de positionnement du 2026-09-12, point 2 : `TraceLedger.export_otel_jsonl`
  produit des spans GenAI conformes depuis #322, mais rien ne les consommait —
  `/api/otel` sert une pile d'événements différente (`blueprint_telemetry`,
  `events.jsonl`), sans rapport avec le TraceLedger. `grimoire.missions.trace`
  (déjà livré par #276) gagne une cinquième source, `otel` : si un export
  existe à l'emplacement conventionnel (`<traces>/otel-export.jsonl`), ses
  spans sont lus et corrélés par `grimoire.task_id` puis par `traceId`
  partagé avec les spans enfants — jamais par heuristique textuelle. Deux
  autres traces disparaissaient aussi en silence de la timeline avant ce
  correctif : les dispatchs d'agent (`agent.dispatch`, `agent.miss`) et tout
  futur fait du TraceLedger qui n'est ni un gate ni un appel d'outil — un
  repli générique les reprend désormais sous la source `hooks`. Côté cockpit :
  l'espace Exécuter (`web/workspace/spaces/executer.js`) ouvre la timeline
  d'une tâche depuis sa carte (bouton « Voir la timeline »), la filtre par
  source et par gravité, et détaille chaque ligne en accordéon ; l'espace
  Observer (`observer.js`) y renvoie depuis un span qui porte
  `grimoire.task_id`. Lecture seule (ADR-007) : aucune écriture, aucune
  reconstruction d'événement absent — une tâche sans trace montre « aucun
  événement » et nomme les sources lues. Tests : `tests/unit/missions/test_trace.py`
  (corrélation par identifiants, dispatch non perdu, otel présent/absent),
  `tests/unit/test_workspace_api.py`, `tests/e2e/test_workspace_lot4_spaces.py`
  (dispatch et transition refusée visibles, filtre par source). Docs :
  `docs/cli-reference.md`, `docs/serve-blueprints.md`,
  `docs/audits/positionnement-2026-09-12.md` et
  `framework/agentic-industry-reference.md` (section 10) mis à jour avec la
  date de correction.

- **feat(cockpit): suggestions de contenu par un petit modèle local (Ollama), toujours derrière l'IntelliSense déterministe de l'espace Source, jamais à sa place (#280).**
  Voie 2 de #280, derrière la voie 1 (IntelliSense déterministe, PR #303) :
  `project-context.yaml: source.assist.model` (vide par défaut, opt-in) plus
  la même sonde qu'`grimoire providers audit` (`GET /api/tags`) décident si
  le bouton **Suggérer** de l'éditeur Source apparaît — sinon l'interface ne
  montre rien et ne tente rien. `GET /api/workspace/assist` (sans coût) rend
  ce statut ; `POST /api/workspace/assist` (`src/grimoire/tools/source_assist.py`,
  projet d'accueil seulement) appelle réellement le modèle en local
  (`http://127.0.0.1:11434`, délai borné à 10 s), avec les identifiants du
  paquet de langage (agents, skills, workflows, patterns) injectés dans le
  prompt, et vérifie après coup tout identifiant cité par la réponse contre
  ce même paquet — marqué « inconnu » plutôt que corrigé à la place de
  l'utilisateur. Panneau d'aperçu dans l'éditeur (`Ctrl+Maj+Espace`, ou le
  bouton) avec **Insérer**/**Ignorer** ; l'insertion passe par le même
  chemin que la frappe clavier, la colorisation et les diagnostics se
  recalculent dessus. Aucun fournisseur distant, aucune clé, aucune écriture
  de fichier par la route. Garde de relecture : une URL Ollama résolue
  (`OLLAMA_HOST`) hors bouclage (`127.0.0.1`, `::1`, `localhost`) est
  refusée par défaut — `source.assist.allow_lan: true` l'autorise
  explicitement.

- **feat(mcp): migrer le pont MCP vers la révision de protocole 2026-07-28 (#436).**
  Le plancher `mcp>=1.10,<3` laissait un résolveur retenir un SDK qui plafonne
  à la révision 2025-11-25 (pas de `server/discover`, pas de mode sans état).
  Relevé à `mcp>=2.0,<3` : à partir de 2.0.0, le SDK négocie 2026-07-28 par
  défaut (`server/discover`, auto-dérivé des outils/prompts/ressources
  enregistrés) tout en servant encore, sur la même connexion, un hôte qui ne
  connaît que le handshake `initialize` (2025-06-18, 2025-11-25) —
  `serve_dual_era_loop` côté SDK. Aucune ligne du pont
  (`src/grimoire/mcp/server.py`) n'a dû changer : il ne câblait déjà ni
  Roots, ni Sampling, ni Logging (les trois fonctionnalités que 2026-07-28
  déprécie, retrait possible à partir de 2027-07-28), et ne garde aucun état
  entre deux appels d'outil en dehors des fichiers du projet ciblé. Nouveaux
  tests (`tests/unit/mcp/test_protocol_revision.py`) qui pilotent un vrai
  `ClientSession` sur des flux en mémoire : négociation 2026-07-28 par
  `server/discover`, compatibilité `initialize` à 2025-06-18 et 2025-11-25,
  et absence d'état partagé entre deux connexions successives. Documentation
  (`docs/mcp-integration.md`) et carte de correspondance
  (`framework/agentic-industry-reference.md`, section 10) mises à jour.
  Hors périmètre : transport HTTP/SSE, authentification, exposition réseau
  distante — le pont reste stdio, en local.

- fix(yaml): `grimoire upgrade` round-trippait `project-context.yaml` via un chargeur `safe` (aucune métadonnée de commentaire) puis un dumper round-trip — tous les commentaires du fichier disparaissaient silencieusement à chaque migration v2→v3 (#430).

- fix(flows): `grimoire.runtime.kernel.create_instance` tronquait silencieusement `recipe_id`/`blueprint_id` aux 16 derniers caractères pour construire `wfi_id`/`run_id` (`WFI-...`) — deux blueprints partageant ce suffixe (par ex. `tasklib-hardening` perdait déjà son premier caractère) pouvaient obtenir le même `run_id` sur des kernels indépendants. L'identifiant complet est gardé tant qu'il tient dans une borne large (64 caractères) ; au-delà, il est raccourci et désambiguïsé par une empreinte de 8 hex de `sha256(recipe_id)` plutôt qu'une simple coupe. Les runs déjà persistés sous l'ancien format restent lisibles par `flow status`/`flow list` (#446).

## [3.45.0] - 2026-09-12

- **fix(flows): le gate de `flow run --executor dispatch` exécute l'acceptance structurée d'un node, pas seulement l'enveloppe (#428).**
  Rejeu réel du 2026-09-11 (épic #307, lot 3) : un nœud V0 déclaré vert alors
  que sa vraie suite `pytest` ne pouvait pas être collectée (dépendance
  absente) — le gate ne vérifiait que la conformité de l'enveloppe JSON de
  l'ouvrier au contrat de sortie, jamais le texte de l'acceptance. `node.acceptance`
  accepte désormais, en plus du texte libre (rétrocompatible), une forme
  structurée exécutable (`{"run": "...", "expect_exit": 0, "cwd": ".",
  "timeout_s": 120, "expect_stdout_contains": "..."}`) ou une evidence
  mécanique (`{"path_exists": "..."}`, `{"test": "..."}`) — validée au
  chargement du blueprint (`blueprint_loader.py`), refus nommé sur une forme
  inconnue. Le gate (`missions/dispatch.py`) exécute ces commandes après la
  réponse de l'ouvrier et rend trois verdicts, jamais un quatrième :
  exécutée-verte (succès), exécutée-rouge (échec, suit la cascade normale :
  réessai/escalade), ou **acceptance inexécutable** (refus nommé — binaire
  absent, `pytest` 5/4, erreur d'import dans la sortie — qui arrête la
  cascade net, jamais un succès). `flow status` et le rapport de dispatch
  montrent par nœud le statut d'acceptance (exécutée/inexécutable/jugée) et
  la sortie tronquée à 2 Ko. **La classe V0 exige une acceptance
  structurée** : un nœud dont le texte seul le classerait V0 mais qui n'en
  déclare aucune est rétrogradé en V1 (cascade démarrant à `mid`, jamais
  fermé sur la seule enveloppe — marqué à relire), avec un avertissement
  nommé posé au chargement du blueprint et transmis à la fois au démarrage
  de la cascade et au rapport, pour que `flow run` et `flow status`
  s'accordent sur la même classe. Un nœud V1 **déclaré** (vocabulaire de
  revue) garde le comportement actuel du kit, documenté sans être étendu.
  Aucun changement côté port Rust des flows (`rust/grimoire-flows-core/`) :
  seule `NodeContract.outputs` (pins) y traverse la frontière PyO3, inchangée
  par ce correctif.

- **feat(cockpit): le board de l'espace Exécuter montre le corps réel d'une tâche (description, garde-fous, dépendances) et les commandes d'intention restent gated par la preuve en multi-projet (#140).**
  L'inspecteur de tâche affichait déjà les critères d'acceptation et les
  preuves attendues ; il gagne des blocs Description, Garde-fous et
  Dépendances, tirés du `MissionTask` du ledger — ce que `task-board.yaml`
  n'a jamais su porter (ADR-005). `kanban.html` reste la vitrine statique et
  gagne un lien vers le board vivant. Aucune écriture nouvelle : les
  commandes de transition, le gate de preuve et la restriction au projet de
  lancement du cockpit existaient déjà ; ce lot les couvre par des tests
  dédiés (unitaires sur la route `/api/workspace/tasks/<id>/<action>` —
  succès, refus nommant l'artefact manquant, refus hors projet de lancement —
  et navigateur sur le critère d'acceptation à la lettre : une carte visée
  vers *review* sans evidence pack est refusée avec l'artefact nommé, et le
  board change quand on change de projet).

- feat(traces): sixième port Rust optionnel du cœur du système d'artefact émergent — les agrégations du journal de traces (`TraceLedger.agent_dispatch_counts`/`.agent_miss_counts`/`.oldest_started_at`), la règle de fraîcheur (`compute_agent_freshness`, issue #396) et le déclencheur de propositions d'artefact (`grimoire.proposals` : nommage mécanique, résolution du porteur issue #402, décision de synchronisation seuil/refus issue #395/#394/#389) (issue #354). `rust/grimoire-traces-core/`, bascule `GRIMOIRE_TRACES_BACKEND=python|rust|auto` (lue indépendamment par `grimoire.traces.ledger` et `grimoire.proposals`, qui ne s'importent pas l'un l'autre), jobs CI `rust-traces / cargo` et `rust-traces / parity` dans `.github/workflows/rust-cores.yml`, roue toujours `py3-none-any`. Défaut trouvé par l'oracle Rust et corrigé dans cette même PR : `grimoire.traces.ledger._parse_iso` attrapait `ValueError` mais pas la `TypeError` non rattrapée levée ensuite par `datetime.now(tz=UTC) - datetime.fromisoformat(...)` sur un horodatage ISO-8601 *naïf* (sans décalage) — un journal JSONL édité à la main peut en porter un, et `compute_agent_freshness` revendique explicitement ne jamais lever sur un journal arbitraire ; corrigé en traitant un horodatage naïf comme UTC des deux côtés (même correctif que `grimoire_traces_core`, qui n'a jamais eu ce trou). Invariants de la doctrine rendus impossibles à violer par construction (clampés/exclus dans la fonction de décision elle-même, jamais par un appelant qui pourrait l'oublier) : seuil de proposition jamais sous 2, aucune proposition au premier non-choix, une proposition refusée ne revient que si son compte a doublé depuis le refus, la persona d'entrée est exclue de la recherche de porteur avant même de lire son `use_when`, le match de catégorie se fait par mot entier (limite de mot Unicode) et non par sous-chaîne. Corpus fixture de dix journaux JSONL réalistes (`tests/fixtures/traces_ledgers/`) et fuzz léger (200 enregistrements aléatoires) prouvant qu'aucune des fonctions d'agrégation/fraîcheur ne lève jamais, sous aucun backend, et qu'un champ de contenu libre (`prompt`/`request`) écrit à la main n'est jamais agrégé.

- **fix(up): `identity` ne corrompt plus un scalaire commenté de `project-context.yaml` (#426).**
  `cmd_setup._apply_project_context` réécrivait chaque ligne `user:` connue
  avec une regex `.+` qui avalait tout le reste de la ligne — valeur *et*
  commentaire inline — comme si c'était « la valeur », puis rewrappait ce
  texte entier entre guillemets. `skill_level: "expert"  # beginner |
  intermediate | expert` devenait `skill_level: ""expert"  # beginner |
  intermediate | expert"` à chaque `grimoire up`, y compris quand la valeur
  ne changeait pas — et cassait ensuite `grimoire doctor` (`GR002`, YAML
  imparsable). Un nouveau `_split_scalar_and_comment` sépare correctement
  le scalaire (quoté ou non) du commentaire qui le suit avant toute lecture
  ou réécriture, et `_apply_project_context` recolle le commentaire original
  après la nouvelle valeur au lieu de le jeter.

## [3.44.2] - 2026-09-11

- **fix(hosts): `collect_agents` résout ses propres skills quand `known_skills` n'est pas fourni — `SessionStart` et le porteur de proposition ne plantent plus sur un agent à skills attachés (#423).**
  `entry_persona_context` et `_category_carrier` (déclencheur de propositions)
  appelaient `collect_agents(project_root)` sans lui passer l'inventaire des
  skills du projet ; le défaut retombait alors sur un ensemble vide, donc
  *tout* agent déclarant un `skills:` en frontmatter — la forme par défaut
  des archétypes depuis #377/#387 — échouait fail-closed comme si ses
  skills n'existaient pas. `collect_agents(project_root, *, known_skills=None, …)`
  résout maintenant lui-même l'inventaire via `collect_skills(project_root)`
  quand `known_skills` vaut `None`, pour qu'aucun appelant ne puisse
  retomber dans ce trou ; un inventaire explicite reste accepté pour les
  appelants qui en réutilisent un déjà collecté (`build_surface`, etc.).

- **perf(hosts): cache JSON de l'état du standard, invalidé par empreinte — `grimoire-hook` évite `ruamel.yaml` sur cache chaud (#419).**
  Suite du découpage de `hosts/decisions.py` (#420) : le profil résiduel
  identifiait `grimoire.core.standard_state` (`active_task_id`/`active_profile_id`)
  + `ruamel.yaml` comme le seul poste encore compressible, ~9 ms sur
  `PreToolUse` destructif. Deux changements distincts s'additionnent :
  l'import de `ruamel.yaml` dans `standard_state.py` était au niveau module
  (payé par *tout* appel de hook, même sur un projet non enrôlé, sans
  standard-profile.yaml ni task-board.yaml à lire) — il est maintenant local
  à `_load_mapping`, donc jamais payé quand ces fichiers n'existent pas. Sur
  un projet enrôlé, où ils existent, un cache JSON (stdlib `json`, jamais
  `ruamel`) sous `_grimoire-output/.runs/standard-state-cache.json` — même
  emplacement que l'état de session éphémère existant, gitignoré — mémorise
  `profile_id` et les tâches `in_progress` du board, invalidé par empreinte
  fichier (`[mtime_ns, size]`) : une source modifiée est relue et le cache
  régénéré ; un cache absent, tronqué ou au mauvais format retombe
  silencieusement sur la lecture YAML, jamais une erreur de hook. Écriture
  atomique (fichier temporaire + `os.replace`). Un outil MCP annoté
  `readOnlyHint` (`grimoire_host_status`, `task_recall`) ne doit rien écrire :
  `active_profile_id`/`resolve_active_task`/`active_task_id` et
  `hosts.collect.build_surface` acceptent un `write_cache: bool = True` que
  ces deux tools seuls mettent à `False` — ils lisent un cache déjà chaud
  sans jamais le créer ni le corriger. Le cache est aussi invalidé
  (supprimé, best-effort) juste après une écriture connue de ces YAML —
  `setup_standard_profile`, `TaskService.project_board`, `grimoire task
  board export` — pour que le changement soit visible dès l'appel de hook
  suivant plutôt que celui d'après ; pas requis pour la correction, l'empreinte
  périmée suffit, seulement pour la latence d'un cas rare.

  Mesuré par `scripts/bench-rust-cores.py --macro-runs 15` (même machine,
  projet de bench isolé non enrôlé — c'est le seul fixture du script, donc la
  seule chose qu'il mesure ici est l'import `ruamel` devenu conditionnel) :

  | commande | avant | après | cible | statut |
  |---|---|---|---|---|
  | `grimoire-hook PreToolUse` (tool-policy, destructif — mesure officielle #418/#419) | 62,7 ms | 55,1 ms | < 50 ms | **non atteinte** |

  Le cache lui-même n'a rien à faire sur un projet non enrôlé (aucun YAML à
  mettre en cache) : mesuré séparément sur un projet **enrôlé** (`standard
  init --profile governed`, `_grimoire/standard/standard-profile.yaml` +
  `task-board.yaml` réels), même méthodologie (médiane de 15 exécutions) :

  | événement (décision) | avant | après |
  |---|---|---|
  | `PreToolUse` (tool-policy) | 75,7 ms | 54,2 ms |
  | `SessionStart` (activation) | 73,1 ms | 70,3 ms |
  | `UserPromptSubmit` (task-context) | 58,9 ms | 39,6 ms |
  | `PostToolUse` (evidence-trace) | 57,0 ms | 49,0 ms |
  | `SubagentStop` (subagent-gate) | 131,7 ms | 127,9 ms |
  | `PreCompact` (context-capsule) | 132,1 ms | 121,2 ms |
  | `Stop` (evidence-gate) | 144,4 ms | 127,9 ms |

  (Les trois derniers événements sont plus lents dans l'absolu sur un projet
  `governed` : `evidence-gate`/`context-capsule`/`subagent-gate` y exercent de
  vraies vérifications de gate, hors périmètre de ce cache.) `cProfile` sur
  cache chaud (`PreToolUse`, projet enrôlé) : ni `ruamel` ni
  `grimoire.core.standard_state._load_mapping` n'apparaissent plus dans le
  profil — confirmé aussi par un `sys.modules` vide de tout module `ruamel.*`
  en sous-processus frais. **Cible des 50 ms toujours non atteinte sur la
  mesure officielle (projet non enrôlé)**, assumé : il ne reste, sur ce
  chemin, que le coût `dataclasses`/`inspect` de la première dataclass
  chargée dans le process (stdlib, ~6-9 ms, inhérent à `@dataclass` sur
  `HookInput`/`Decision`/`ToolFacts`/`ActiveTask` — `dataclasses.py` importe
  `inspect` sans condition, aucune combinaison de `frozen`/`slots` n'y
  change quoi que ce soit) et le moteur de politique lui-même
  (`policies.engine`/`policies.schemas`, ~5 ms, propre à `tool-policy`,
  aucun fichier lu). Les deux sont documentés comme incompressibles avec ce
  mécanisme par #419 lui-même ; ni l'un ni l'autre n'a de solution triviale
  sans réécrire ces classes à la main.
  Tests : `tests/unit/core/test_standard_state.py` (cache créé/lu/régénéré
  sur source modifiée/corrompu/absent, `invalidate_cache`) ; garde de
  non-régression `tests/unit/test_hook_cost.py::test_a_warm_standard_state_cache_never_imports_ruamel`
  (sous-processus froid puis chaud, `sys.modules` sans aucun `ruamel.*` au
  second) ; `tests/unit/mcp/test_server.py::TestReadOnlyToolsWriteNothing`
  (déjà existant, aucune modification requise) couvre le contrat
  `write_cache=False`. Aucun changement de décision : les tests existants de
  `hosts/decisions.py` et `test_hosts.py` passent sans modification.

## [3.44.1] - 2026-09-11

- **perf(hosts): découper `hosts/decisions.py` par décision — `grimoire-hook` payait la moitié du fichier pour chaque appel (#419).**
  Suite de mesure de #418 : `grimoire-hook PreToolUse` restait à 69-70 ms
  malgré le chargement paresseux du CLI, parce que ce point d'entrée
  (`grimoire.hosts.runtime:main`) ne passe jamais par `grimoire.cli.app`.
  `grimoire.hosts.decisions` était un seul module de 918 lignes portant les
  sept décisions (`grimoire.activation`, `.task-context`, `.tool-policy`,
  `.evidence-trace`, `.evidence-gate`, `.subagent-gate`, `.context-capsule`) :
  quelle que soit la décision demandée, tout le fichier — et donc le moteur
  de politique (`grimoire.policies.engine`/`.schemas`) — était importé. C'est
  devenu un paquet, un module par décision, résolu paresseusement par
  `grimoire.hosts.decisions.run_decision` via un registre id → module ;
  `__init__.py` ré-exporte tous les noms publics existants (et le privé
  `_gate_summary` qu'un test pinne) par `__getattr__` de module, donc aucun
  import existant ne change. `grimoire.hosts.surface` (446 lignes : IR
  agent/modèle, fingerprinting, sonde du cœur Rust) est passé par le même
  chemin : les trois enums que le hook lit sur *chaque* appel (`HookEvent`,
  `ToolVerb`, `Enforcement`) vivent maintenant dans `grimoire.hosts.events`
  (nouveau, réexporté par `surface.py` pour compatibilité) — et
  `grimoire/hosts/__init__.py`, qui importait `ProjectSurface` (donc tout
  `surface.py`) au niveau module rien que pour une ré-export dont rien dans
  le dépôt ne se sert (`from grimoire.hosts import ProjectSurface`), le fait
  maintenant paresseusement lui aussi.
  Mesuré par `scripts/bench-rust-cores.py --macro-runs 15` (même machine) :

  | commande | avant | après | cible | statut |
  |---|---|---|---|---|
  | `grimoire-hook PreToolUse` (tool-policy, destructif) | 70 ms | 64 ms | < 50 ms | **non atteinte** |
  | `grimoire-hook PreToolUse` (lecture seule) | 69 ms | 58 ms | — | — |
  | `grimoire-hook SessionStart` | 77 ms | 72 ms | — | — |
  | `grimoire-hook UserPromptSubmit` | 63 ms | 49 ms | — | atteinte |
  | `grimoire-hook PostToolUse` | 64 ms | 57 ms | — | — |
  | `grimoire-hook SubagentStop` | 64 ms | 50 ms | — | — |
  | `grimoire-hook PreCompact` | 65 ms | 50 ms | — | — |
  | `grimoire-hook Stop` | 69 ms | 56 ms | — | — |

  **Cible des 50 ms non atteinte pour `PreToolUse`, assumé.** Le profil
  restant après découpage tient en trois postes incompressibles avec ce
  mécanisme : le coût `dataclasses`/`inspect` de la première dataclass
  chargée dans le process (stdlib, ~6-9 ms), `grimoire.core.standard_state`
  + `ruamel.yaml` (~9 ms — nécessaire : même une commande destructive lit
  `active_task_id`/`active_profile_id` avant que le moteur de politique ne
  tranche, sur tous les événements sauf aucun), et le moteur de politique
  lui-même (`policies.engine`/`policies.schemas`, ~5 ms, propre à
  `tool-policy`). Découper `decisions.py` plus finement ne change rien à ces
  trois postes ; les réduire est un chantier distinct (bascule de parseur
  YAML pour le premier, hors périmètre — `ruamel.yaml` est la seule
  dépendance YAML déclarée du kit, `pyyaml` n'est que transitive).
  Garde de régression : `tests/unit/test_hook_cost.py` — un sous-processus
  propre par décision vérifie qu'exécuter une décision n'importe jamais le
  module d'une décision sœur (`test_running_one_decision_imports_only_its_own_submodule`,
  paramétré sur les sept), plus un budget de temps large et portable dans le
  même esprit que `_COCKPIT_REFRESH_BUDGET_SECONDS`
  (`tests/test_invariants.py`).

- **perf(cli): charger les sous-commandes à la demande — 60 % du temps de `doctor` était l'arbre Typer (#405).**
  `grimoire.cli.app` importait sans condition les 20 modules `cmd_*` derrière
  chaque sous-commande (`cmd_flow`, `cmd_host`, `cmd_memory_lexical`,
  `cmd_cockpit`, `cmd_up`...) pour construire l'arbre Typer/Click, quelle que
  soit la commande demandée — `grimoire --version` payait donc l'import de
  `grimoire.flows`, `grimoire.missions.dispatch`, `grimoire.memory`, etc.
  `grimoire.cli._lazy.LazyTyperGroup` (nouveau, `cls=` du `typer.Typer`
  racine) remplace `add_typer()`/`command()` pour ces sous-commandes par un
  registre nom → (module, attribut, aide courte, panneau, visibilité) ; le
  module n'est importé qu'à la résolution réelle de la commande (dispatch, ou
  son propre `--help`) — `grimoire --version` et `grimoire doctor .` ne
  touchent plus aucun d'entre eux. `grimoire.cli.cmd_up` importait en outre
  `cmd_init`/`core.scaffold`/`hosts.sync` à son propre niveau module rien que
  pour les fonctions que `doctor` utilise réellement (`run_env_checks`) —
  ces imports descendent maintenant dans les fonctions qui les utilisent
  vraiment (`up()`, `_step_init`, `repair_project_artifacts`).
  `grimoire --help` et l'aide de chaque sous-commande restent identiques au
  caractère près (ordre des panneaux et des lignes inclus, garanti par un
  registre `order` explicite dans `LazyTyperGroup.configure`) —
  `scripts/compare-cli-help.py` (nouveau) le vérifie avant/après pour les 47
  commandes. Garde de régression : `tests/unit/cli/test_lazy_startup.py`
  (sous-processus propre) échoue si l'import de `grimoire.cli.app` amène
  `grimoire.flows`, `grimoire.missions.dispatch`, `grimoire.memory` ou
  `grimoire.cli.cmd_cockpit` dans `sys.modules`.
  Mesuré par `scripts/bench-rust-cores.py` (médiane de 15, même machine) :

  | commande | avant | après | cible | statut |
  |---|---|---|---|---|
  | `grimoire --version` | 313 ms | 89 ms | < 150 ms | atteinte |
  | `grimoire doctor .` | 448 ms | 270 ms | < 300 ms | atteinte |
  | `grimoire-hook PreToolUse` | 70 ms | 69 ms | < 50 ms | **non atteinte** |

  `grimoire-hook` (point d'entrée séparé, `grimoire.hosts.runtime:main`) ne
  passe jamais par `grimoire.cli.app` et n'est donc pas concerné par ce
  mécanisme ; son coût restant vient de `grimoire.hosts.decisions` (918
  lignes, tous les types de décision dans un seul module) et de
  `grimoire.hosts.capabilities`/`.surface` — hors du périmètre de ce ticket
  (« enregistrer les sous-commandes sans importer leur module », pas refondre
  le moteur de décision des hooks). `grimoire-mcp` (`grimoire.mcp.server`)
  vérifié : n'importe déjà pas `grimoire.cli.app`, rien à faire.

- fix(flows): un abandon (ou un refus MAST) avant tout progrès n'affiche plus tous les nodes comme complétés dans `grimoire flow status` (#414). `FlowEngine.status()` dérivait `completed_nodes`/`pending_nodes` de la seule position de `current_node` dans l'ordre topologique — sur un run ABORTED/REFUSED, `current_node` devient `None` (terminal) exactement comme un run réussi, donc l'ancien découpage rendait `completed_nodes == order` en entier même quand *aucun* node n'était réellement fait. `completed_nodes` reflète désormais les `completed_steps` du dernier checkpoint réel du `RuntimeKernel` sur un run mort (liste vide si aucun checkpoint n'existe encore, c'est-à-dire un abandon avant le premier `resume()`) ; `pending_nodes` reste vide par convention (on ne sait pas si l'hôte comptait reprendre). Corrigé des deux côtés en même temps, même verdict : `rust/grimoire-flows-core/src/lib.rs` (`status_slices_core`) miroité, `grimoire flow status` (texte et JSON) en hérite sans changement de code propre. Nouveaux tests de parité sous les deux backends (`GRIMOIRE_FLOWS_BACKEND=python|rust`) : abandon avant tout progrès, abandon après k nodes, refus avant progrès, complétion normale (inchangée) — `tests/unit/test_flows_rust_parity.py`.

## [3.44.0] - 2026-09-11

- feat(dispatch): cinquième port Rust optionnel de la cascade de dispatch et du routage de fournisseurs — `start_tier_for`/`_tier_chain` (plancher et chaîne de paliers par classe de vérifiabilité), la résolution `--start-tier` explicite contre le plancher, `render_invocation` (rendu d'un gabarit en argv), `_looks_rate_limited`, `_extract_cost_usd`/`_uncertainties_search_text`/`_extract_uncertainties` (analyse de la sortie d'un ouvrier délégué), `_matches_review_surface`/`_classify_review` (classification de revue), `DispatchReport.succeeded`/`.exit_code`/`.refusal_message`, et l'ordre/filtre de refroidissement de `providers.routing.candidates` (issue #354). `rust/grimoire-dispatch-core/` (dépendance `serde_json` épinglée `=1.0.151`, strict RFC 8259), bascule `GRIMOIRE_DISPATCH_BACKEND=python|rust|auto` (lue indépendamment par `grimoire.missions.dispatch` et `grimoire.providers.routing`, qui ne s'importent pas l'un l'autre), jobs CI `rust-dispatch / cargo` et `rust-dispatch / parity` dans `.github/workflows/rust-cores.yml`, roue toujours `py3-none-any`. Deux défauts trouvés par l'oracle Rust en portant `_extract_cost_usd`, corrigés côté Python dans cette même PR : `isinstance(True, int)` est vrai en Python, donc un `total_cost_usd` JSON `true`/`false` était jusqu'ici coercé en `1.0`/`0.0` — désormais exclu explicitement des deux côtés ; `json.loads` de CPython accepte par défaut les jetons hors RFC 8259 `NaN`/`Infinity`/`-Infinity`, que le cœur Rust (`serde_json`, strict) n'accepte pas nativement — **corrigé côté Python : JSON strict** (`_reject_non_standard` passé en `parse_constant` à chaque `json.loads` de `dispatch.py`, rattrapé comme un JSON invalide), un document portant un de ces jetons — même ailleurs que dans le champ lu — est désormais rejeté en bloc des deux côtés (coût absent, aucune incertitude extraite), jamais l'inverse (Rust est l'oracle, pas Python). Nouveau corpus fixture de dix sorties d'ouvrier réalistes (`tests/fixtures/dispatch_worker_outputs/`, aucune sortie enregistrée réelle trouvée dans le dépôt) et fuzz léger (200 chaînes aléatoires) prouvant qu'aucune des deux fonctions d'analyse ne lève jamais, sous aucun backend.

- feat(flows): quatrième port Rust optionnel de la machine à états du moteur de flows — `check_output_against_contract`, le calcul du node courant (`FlowEngine._current_node`), la décision de `resume()`/le calcul de `ResumeOutcome`, le découpage completed/pending de `status()`, et la table de transitions de `RuntimeKernel` (`_transition`/`advance_step`) (issue #354). `rust/grimoire-flows-core/`, bascule `GRIMOIRE_FLOWS_BACKEND=python|rust|auto` (lue indépendamment par `grimoire.flows.engine` et `grimoire.runtime.kernel`), jobs CI `rust-flows / cargo` et `rust-flows / parity` dans `.github/workflows/rust-cores.yml`, roue toujours `py3-none-any`. Divergence trouvée par l'oracle Rust et corrigée dans cette PR : `FlowEngine._TERMINAL_STATUSES` omettait `REFUSED` (terminal côté `RuntimeKernel` depuis les plafonds MAST, B11) — `resume()` sur un run REFUSED levait une erreur au message générique du kernel au lieu du message nommé de `resume()`, et `status()` affichait un `current_node`/`pending_nodes` périmés comme si le run continuait de progresser ; `REFUSED` ajouté à `_TERMINAL_STATUSES`, les deux backends s'accordent désormais. Comportement documenté et volontairement non changé : `status()` dérive `completed_nodes` de la position de `current_node` dans l'ordre topologique — un run ABORTED/REFUSED avant tout progrès affiche tous les nodes comme complétés, les deux backends reproduisant ce même comportement à l'identique (voir `rust/grimoire-flows-core/src/lib.rs` et l'issue de suivi citée dans la PR). Nouveau test de parité sur le corpus réel (`registry/blueprints/*.blueprint.json`) et sur la grille complète des 9×9 transitions de `WorkflowStatus`.

- feat(hosts): troisième port Rust optionnel de `grimoire.hosts.collect`/`grimoire.hosts.surface` — lecture du frontmatter d'agent et garde de distinction (issue #354). `rust/grimoire-hosts-core/`, bascule `GRIMOIRE_HOSTS_BACKEND=python|rust|auto`, jobs CI `rust-hosts / cargo` et `rust-hosts / parity` dans `.github/workflows/rust-cores.yml`, roue toujours `py3-none-any`. Divergences trouvées par l'oracle Rust : `_max_turns` plantait avec un `ValueError` non rattrapé sur un chiffre Unicode non-ASCII (`"²"`, `str.isdigit()` vrai mais `int()` refuse) — corrigé dans cette PR (`str.isascii()` en plus) ; `_tool_verbs` ignore un verbe d'outil hors de `ToolVerb` — le périmètre d'outils reste inchangé (changer ça modifierait silencieusement celui d'agents existants), mais le rejet n'est plus silencieux : `collect_agents`/`build_surface` remonte désormais une note `ProjectSurface.notes` par agent nommant les jetons rejetés, identique sous les deux backends. Nouveau test de parité sur le corpus réel (`archetypes/*/agents/*.md`) prouvant que `yaml-rust2` et ruamel s'accordent sur ce que le kit expédie effectivement.

## [3.43.1] - 2026-09-11
### Corrigé

- **fix(core): le validateur ne plante plus sur les champs énumérés et type-vérifie enfin cinq champs déclarés chaînes (#409).**
  `src/grimoire/core/validator.py` comparait `valeur not in <frozenset>` pour
  neuf champs énumérés (`project.type`, `user.skill_level`,
  `memory.backend`/`short_term_backend`, les cinq modes de couche mémoire,
  `agents.archetype`) — une liste ou une table en entrée levait
  `TypeError: unhashable type` au lieu d'une erreur de validation propre (un
  scalaire hachable non-chaîne, lui, ne plantait pas et reste traité comme
  n'importe quelle valeur inconnue : comportement inchangé, aligné sur
  l'oracle Rust). `project.repos[].name`, `project.repos[].path`/
  `default_branch`, les éléments d'`installed_archetypes[]` et
  `user.name`/`language`/`document_language` n'étaient en outre jamais
  type-vérifiés, bien que `schema.py` les déclare comme des chaînes. Le cœur
  Rust du validateur (#392) rejetait déjà proprement ces cas et servait
  d'oracle ; `tests/unit/test_schema_validator_rust_parity.py` passe des
  divergences documentées à la parité stricte entre les deux backends.

- **fix(doctor): ne plus lire un spec de paquet npx/uvx/pipx/bunx/docker/podman comme un chemin (#393).**
  `.mcp.json server '<nom>': path '<spec>' does not exist` était un faux
  positif dès que `command` était un lanceur de paquet (ex. `npx`) et que le
  premier argument contenait un `/` et une version (`@playwright/mcp@0.0.80`
  ressemblait à un chemin selon l'heuristique). Pour ces lanceurs, `doctor`
  vérifie désormais seulement que le lanceur est sur le PATH (FAIL sinon,
  remède « installer <lanceur> ») et rapporte le paquet en INFO sans le
  tester comme chemin. Le contrôle est inchangé pour les commandes qui
  pointent vers un fichier ou un binaire.

- fix(core): la persona d'entrée (`concierge`) n'est plus jamais retenue
  comme porteuse d'un skill proposé (#402). Le déclencheur de propositions
  (#395) traitait tout agent de repli comme un porteur valide ; comme le
  concierge est presque toujours ce repli (c'est lui qui fait le triage),
  presque toute proposition devenait « skill attaché à concierge », contraire
  à la doctrine (un skill s'attache à l'agent qui fait le travail). Un repli
  qui est la persona d'entrée est désormais traité comme une absence de
  repli : une recherche par catégorie parmi les agents déclarés (`use_when`,
  ou faisceau `execute` pour les catégories d'exécution) propose un porteur à
  sa place si un seul agent convient, sinon la proposition redevient un
  agent. `fallback_agent` reste le fait brut observé ; le nouveau champ
  `carrier_reason` explique le choix, affiché par `grimoire proposals list`
  et le cockpit.

### Ajouté

- **`rust-gate` devient l'unique check requis pour les deux cœurs Rust
  optionnels (#407, #408).** `rust-policies-core.yml` et
  `rust-schema-core.yml` étaient filtrés par `paths:` : absents de toute PR
  hors de leur périmètre, un check requis qui ne se déclare jamais bloquait
  ces PR-là (#397 a fusionné avec `cargo fmt` rouge faute de protection
  possible). `rust-cores.yml` se déclenche sans filtre de chemin sur toute
  PR vers `main` ; un job `changes` calcule quel(s) crate(s) sont touchés,
  les jobs par crate hors périmètre sont sautés (satisfaisant un check
  requis), et `rust-gate` agrège les quatre jobs comme seul check requis à
  ajouter à la protection de `main`.

- **chore(bench): mesurer le gain réel des cœurs Rust optionnels (#354).**
  `scripts/bench-rust-cores.py` compare Python et Rust pour les deux cœurs
  livrés à ce jour (`grimoire.policies.engine.PolicyEngine.evaluate` #363,
  `grimoire.core.schema.generate_schema` / `grimoire.core.validator.validate_config`
  #392) sur trois plans : micro (temps par appel, `timeit`, entrée réaliste
  et entrée volumineuse), macro (`grimoire doctor`, `host sync`, `standard
  verify`, décision `PreToolUse`, médiane de 10 exécutions contre le coût
  fixe de `grimoire --version`), et profil (`importtime` + `cProfile` sur
  `grimoire doctor .`). Verdict mesuré publié en commentaire sur #354 :
  aucun des deux ports ne change un temps perceptible par l'utilisateur,
  le surcoût de la frontière PyO3 dépassant le gain sur ces tailles
  d'entrée. `docs/rust-cores-benchmark.md` documente comment le relancer.

## [3.43.0] - 2026-09-11
### Ajouté

- **feat(core): instrumenter les non-choix du concierge (#389).** Symétrique
  du choix d'agent (#366) : quand le concierge cherche un spécialiste et n'en
  trouve aucun, ou se rabat sur un généraliste, `grimoire agent-miss`
  journalise le fait dans le même TraceLedger — catégorie de la demande,
  spécialité cherchée si nommable, agent de repli, raison, jamais le contenu
  de la demande. La résolution se fait dans le raisonnement de la persona
  concierge (`archetypes/meta/agents/concierge.md`, désormais instruite
  d'appeler cette commande), pas dans du code du kit ; c'est donc le seul
  canal d'écriture. `grimoire registry dispatches` lit désormais les deux
  côte à côte : choix par agent, non-choix agrégés par spécialité manquante
  avec leur compte. Écriture best-effort, comme son symétrique.

- **feat(core): proposer un artefact à la répétition d'un non-choix (#395).**
  Le déclencheur lit `TraceLedger.agent_miss_counts()` (#394, désormais enrichi
  de la catégorie et de l'agent de repli les plus récents) et, quand une même
  spécialité manquante atteint un seuil configurable (`proposals.threshold`
  dans `project-context.yaml`, défaut 2, jamais 1), écrit une proposition
  `pending` sous `_grimoire-output/proposals/<slug>.yaml` — nom, rôle,
  `use_when`/`dont_use_when`/`tools` déduits mécaniquement des étiquettes
  observées, jamais d'appel LLM. Type d'artefact selon la doctrine
  (`docs/artifact-doctrine.md`) : un agent de repli déjà observé propose un
  skill à lui attacher, son absence propose un agent. Jamais de création sans
  acceptation explicite (`grimoire proposals accept <slug>`, aucune option
  `--auto`), qui écrit l'artefact réel via le même chemin que l'outil d'ajout
  d'agent réparé par #367 (`grimoire.tools.agent_creation`, désormais partagé)
  et annule si la garde de distinction (#372) refuse le faisceau. Refuser
  (`grimoire proposals reject <slug>`) marque la proposition ; la même
  spécialité n'est reproposée que si son compte a doublé depuis le refus.
  `grimoire proposals list` en CLI, section Propositions du cockpit (espace
  Piloter, #382) avec les mêmes deux actions, et une ligne SessionStart à côté
  des fournisseurs (« N proposition(s) d'artefact en attente ») ; clé
  `proposals` dans le schéma (`grimoire.core.schema`) et le port Rust
  (`rust/grimoire-schema-core/`).

- **feat(core): règle de fraîcheur des agents — signaler, jamais retirer (#396).**
  Un agent livré ou en override qui n'apparaît dans aucun `agent.dispatch`
  du journal de traces depuis `agents.freshness_threshold_days` jours
  (défaut 90, configurable dans `project-context.yaml`) est signalé, jamais
  retiré ni déprécié automatiquement. `grimoire doctor` gagne un contrôle
  `agent_freshness` (INFO si le journal a moins de N jours d'historique —
  l'absence de données n'est jamais une absence d'usage —, WARN sinon,
  jamais FAIL) ; `grimoire registry dispatches` gagne la liste des agents
  jamais choisis ou périmés sur la période ; le cockpit (section agents,
  #382) affiche « jamais invoqué » ou « il y a N jours » dans la colonne
  d'usage et un badge « périmé » au-delà du seuil. Calcul centralisé dans
  `grimoire.traces.ledger.compute_agent_freshness`, composé pour les trois
  surfaces par `grimoire.core.agent_freshness`. Le seuil est aussi porté au
  port Rust du schéma (`rust/grimoire-schema-core/`).

- feat(core): second port Rust optionnel, `rust/grimoire-schema-core/` — `grimoire.core.schema.generate_schema` et `grimoire.core.validator.validate_config` bascule sur `GRIMOIRE_SCHEMA_BACKEND=python|rust|auto` (sœur de `GRIMOIRE_POLICIES_BACKEND`), repli Python inchangé par défaut, sans roue publiée (#354). Le cœur Rust rejette explicitement des entrées que le validateur Python de référence laisse aujourd'hui passer sans erreur (`installed_archetypes[]`, `project.repos[].name`, `user.name`/`language`/`document_language` jamais type-vérifiés malgré `schema.py`) ou fait planter (`TypeError: unhashable type` sur un `type`/`backend`/... de forme liste ou table) — vérifié dans les deux configurations, contrat des tests existants inchangé.

### Corrigé

- fix(core): la règle de fraîcheur ne juge pas un agent plus jeune que le seuil (fichier de définition récent, marqué « trop récent ») ; job cargo remis au vert (#398)

## [3.42.0] - 2026-09-11
### Ajouté

- **Doctrine de création d'artefact et sa garde (#370).** `docs/artifact-doctrine.md`
  pose le critère de nécessité d'un artefact (décision écrite d'avance, qui
  l'invoque) et la règle de frontière d'outils, avec renvois depuis
  `creating-agents.md` et `workflow-taxonomy.md`. `use_when`, `dont_use_when`,
  `tools` et `tool_boundary` deviennent des champs obligatoires du frontmatter
  de tout agent livré sous `archetypes/*/agents/` ; les 33 agents existants
  reçoivent une frontière écrite à la main, et `test_artifact_employment_clause.py`
  fait échouer tout agent livré sans les quatre champs.
- **Le contexte déclaré d'un agent entre dans le contrat qu'on lui remet (#378).**
  `build_prompt` lit désormais le `context` déclaré de l'agent dispatché
  (`--agent` de `grimoire task dispatch`, ou l'agent unique d'un
  `DispatchExecutor` de flow) et l'ajoute au contrat de la tâche, sans
  régression pour un agent sans contexte déclaré ou un `--agent` inconnu.
  `grimoire host status` affiche aussi les collisions de faisceau entre
  agents livrés par le kit, jusqu'ici invisibles.
- **Archétype `platform-engineering` refait — 2 agents à faisceau distinct plus
  2 skills attachés (#375).** `platform-architect` et `backend-engineer`
  restent des agents ; `deploy-orchestrator` et `reliability-engineer`, au
  même faisceau `{read, edit, execute}` que `backend-engineer` sans frontière
  propre, deviennent les skills attachés `platform-deploy-release` et
  `platform-reliability-sre`. Aucun savoir-faire supprimé.
- **Archétype `creative-studio` refait — 3 agents à faisceau distinct plus 1
  skill attaché (#375).** `blender-expert` et `illustration-expert` restent
  des agents (contexte propre via MCP + boucle vision) ; `content-creator`,
  au même faisceau que `brand-designer` sans frontière propre, devient le
  skill attaché `creative-content-copywriting`. Aucun savoir-faire supprimé.
- **Refonte de l'archétype `meta` — généraliste plus skills attachés (#375).**
  Les 3 agents à faisceau distinct (`concierge`, `agent-optimizer`, `security-auditor`)
  restent ; `art-director`, `creative-toolsmith`, `memory-keeper` et
  `project-navigator`, tous indiscernables au sens de la garde de #372,
  deviennent quatre skills attachés à `agent-optimizer` sans rien perdre du
  savoir-faire ; `security-auditor` gagne un `context:` propre qui le distingue
  désormais de `fix-loop-orchestrator`. `scaffold._plan_meta_agents` copie
  maintenant `archetypes/meta/skills/*.md`, meta étant déployé à tout projet
  indépendamment de l'archétype choisi.
- **Archétype `stack` refait — un généraliste plus sept skills attachés (#375).** Les sept experts par techno (`python-expert`, `go-expert`, `typescript-expert`, `docker-expert`, `terraform-expert`, `ansible-expert`, `k8s-expert`) partageaient le même faisceau outils et aucun contexte propre — c'était un seul agent décrit sept fois. Ils deviennent des skills attachés à un nouvel agent généraliste, `stack-engineer` ; le corps de chaque agent devient le corps de son skill, rien n'est supprimé. `grimoire init --archetype stack` livre désormais un agent, et une composition `web-app,stack` reste sans note de collision pour cet archétype.
- **Archétypes `web-app` et `fix-loop`/`minimal` alignés sur la forme validée
  d'`infra-ops` (#375).** `web-app` livrait deux agents (`frontend-specialist`,
  `fullstack-dev`) au même faisceau d'outils exact `{read, edit, execute}`,
  sans contexte ni skill propres — la garde de distinction (#372) les
  déclarait indiscernables. `frontend-specialist` devient le skill attaché
  `web-frontend-ux` (composants, accessibilité WCAG 2.1 AA, revue UX,
  performance UI) porté par `fullstack-dev`, son corps repris sans perte.
  `fix-loop` (un seul agent, contexte propre) et `minimal` (gabarit `.tpl`
  jamais scaffoldé comme agent) étaient déjà conformes, aucun changement.
  Connu et hors périmètre : `fix-loop-orchestrator` partage son faisceau avec
  `security-auditor` (archétype `meta`) — dette à régler côté méta.
- **Archétype `infra-ops` refait — un généraliste plus quatre skills attachés
  (#380).** infra-ops livrait sept agents dont cinq au même faisceau exact
  `{read, edit, execute}`, sans contexte ni skill propres. Réduit à trois
  agents à faisceau distinct (`ops-engineer` généraliste, `monitoring-specialist`,
  `systems-debugger`) ; `pipeline-architect`, `security-hardener`,
  `backup-dr-specialist` et `k8s-navigator` deviennent quatre skills attachés
  à `ops-engineer`, corps repris sans perte. `layout.skill_dirs()` et
  `scaffold._plan_archetype_agents` transportent désormais les skills
  d'archétype, jusque-là sans mécanisme de copie.
- **Équipes fantômes retirées, périmètre de `infra-ops` documenté (#350).**
  Les trois manifestes d'équipe livrés (team-build, team-ops, team-vision)
  nommaient neuf agents d'une pile jamais fournie par aucun des neuf
  archétypes du kit ; supprimés, avec un test de garde qui échoue si un
  manifeste cite un agent absent. `infra-ops` documente désormais son
  périmètre réel (laboratoire personnel Proxmox/K3s/FluxCD) dans le wizard
  `grimoire init` et `archetype.dna.yaml`, plutôt que de laisser croire à un
  usage cloud/entreprise sous un nom générique.
- **Nouvel agent de sécurité offensive `security-auditor` (#357).**
  Cartographie de surface, fuzzing (`hypothesis` en cœur de métier, `atheris`
  pour les formats non structurés) et rétro-ingénierie Ghidra cadrée aux
  seuls binaires — jamais au code Python source. Première campagne réelle
  sur `grimoire.providers.registry.read_registry` : 0 plantage sur ~850
  exemples générés plus 11 graines fixées, rejouables via un corpus versionné.
- **Une seule commande de service, ouverte sur le projet courant (#356).**
  `serve` et `cockpit serve` ouvraient la même interface pour la seule
  différence mono/multi-projets. `cockpit serve`/`start` détecte le dossier
  courant, enrôle le projet au registre s'il n'y était pas, et l'ouvre
  sélectionné ; `serve` devient un alias déprécié qui délègue. Un port
  occupé propose une alternative libre au lieu d'afficher seulement
  l'exception.
- **Voir et configurer les agents du projet depuis le cockpit (#382).** La
  fiche projet de Piloter gagne une table des agents (couche, outils,
  skills, usage réel agrégé depuis le journal de traces) ; l'inspecteur
  permet d'assigner ou retirer des skills et d'éditer clause d'emploi,
  outils et contexte, avec revalidation via `collect_agents` avant toute
  écriture — un skill ou un contexte inconnu annule l'écriture.
- **Skills attachés aux agents par défaut, payées seulement à l'usage
  (#377).** `AgentSpec` gagne `skills` (résolus contre l'inventaire collecté,
  fail-closed sur un slug inconnu) et `context` ; un skill déclaré par un
  agent est replié dans le fichier de cet agent (Claude Code, Copilot)
  plutôt qu'émis au niveau du projet, où chaque tour de session en paierait
  la description qu'il serve ou non. Mesuré : 1480 tokens/tour avec dix
  skills transversales contre 0 avec les mêmes dix attachées. Une garde de
  distinction fait échouer `build_surface()` sur deux agents des overrides
  d'un projet qui partagent le même faisceau outils + contexte + skills
  (note non bloquante quand les deux viennent du kit — dette connue).
- **Le fichier d'agent émis ne charge que le contexte déclaré (#379).** Un
  agent qui déclare `context:` reçoit dans son fichier `.claude/agents/*.md`
  (et `.github/agents/*.agent.md`) une instruction d'activation qui charge
  ces chemins-là, et eux seuls, à la place du contexte partagé par défaut
  qu'il n'a pas demandé. Un agent sans déclaration reçoit exactement ce qu'il
  recevait avant — testé bit à bit. Mesuré sur trois agents de nature
  différente (navigation, mémoire, sécurité) : le contexte partagé du kit
  (~193 tokens) disparaît de leur activation au profit du seul contexte
  qu'ils déclarent. Suite de #378, qui avait câblé la même déclaration côté
  dispatch de tâche.
- **Enregistrer quel agent a été choisi (#366).** Le choix de la persona
  d'entrée laisse désormais un fait dans le journal de traces existant :
  quel agent, quand, dans quel projet — rien du contenu échangé, aucune
  télémétrie sortante. Écriture best-effort : un journal indisponible ne
  fait jamais échouer l'activation qu'il se contente d'observer.
- **Premier port Rust, optionnel — `grimoire.policies` (#354).** Le moteur
  de règles a désormais un second cœur, en Rust, exposé via PyO3 depuis
  `rust/grimoire-policies-core/` ; `PolicyEngine.evaluate` l'utilise quand il
  est disponible et retombe silencieusement sur l'implémentation Python
  pure sinon (`GRIMOIRE_POLICIES_BACKEND=python|rust|auto` force le choix
  des deux côtés pour les tests). Rien n'est publié : la wheel PyPI reste la
  wheel universelle actuelle, le crate porte le classifieur `Private :: Do
  Not Upload`, et construire l'extension localement (`maturin develop`,
  voir CONTRIBUTING.md) n'est jamais nécessaire pour contribuer au kit. Un
  job CI dédié (`rust-policies-core.yml`) construit et teste le crate sans
  toucher à la publication. `tests/unit/test_policies.py` tourne sans
  modification dans les deux configurations ;
  `tests/unit/test_policies_rust_parity.py` prouve que les deux
  implémentations rendent le même verdict sur les mêmes entrées.

### Corrigé

- **Identité d'agent unifiée entre `installed_agents` et `collect_agents`
  (#383).** Les deux fonctions nommaient différemment le même fichier
  gabarit non rendu (`name: "{{agent_tag}}"`) : invisible pour l'une, nommé
  d'après le fichier pour l'autre. `layout.agent_identity()` devient la
  lecture unique, sans jamais retomber sur le nom de fichier quand un
  `name:` est déclaré mais inutilisable.
- **`grimoire_add_agent` crée un vrai fichier dans `overrides/agents`
  (#367).** L'outil se contentait d'ajouter un nom à une liste
  (`agents.custom_agents`) jamais lue pour du chargement ou du routage ; il
  rend désormais le gabarit `custom-agent` vers la couche que le diagnostic
  et la carte de routage résolvent réellement, en refusant un doublon ou un
  nom de fichier non sûr.
- **Couche overrides rendue visible à `doctor` et à la carte de routage
  (#349).** Un agent déposé dans `overrides/agents/` — le moyen sanctionné
  de personnaliser un projet — était invisible au diagnostic et non
  routable par la persona d'entrée ; les deux lecteurs passent désormais
  par `layout.agent_dirs()` avec la même priorité de tiers que le reste du
  kit.
- **`grimoire up` persiste les `needs` et refuse de rétrograder le profil du
  standard (#348).** Sans `--needs`, `up` réécrivait le manifeste du
  standard avec le profil `starter` par défaut, désenregistrant
  silencieusement un profil supérieur (governed, orchestrated…) installé
  via `--needs`. Les `needs` choisis à l'installation sont désormais relus
  depuis `install-manifest.yaml`, et une garde anti-rétrogradation préserve
  le profil en place quand il couvre plus que celui résolu.
- **Les écritures du cockpit répondaient 404 dès qu'un projet était
  sélectionné (#358).** L'interface ajoute `?project=<slug>` à toute
  requête, POST comprises, dès qu'un projet est sélectionné ; `do_POST`
  comparait le chemin, query string incluse, à des routes exactes — la
  totalité des écritures du cockpit (sélection de projet, alignement de
  kit, ajout et scan de dossier, actions mémoire) était morte.
- **Registre de test du cockpit isolé, chemins disparus ignorés au lieu
  d'être purgés d'office (#343).** Un registre pollué de 276 chemins
  `/tmp/pytest-of-*` disparus faisait passer `cockpit refresh` de 31s à
  1,15s une fois filtré. Une empreinte du vrai registre en début et fin de
  suite fait échouer tout test qui écrit hors de l'isolation `HOME` ;
  `gen-site-data.py` ignore les chemins morts au lieu de les traiter, et le
  cockpit nomme leur nombre plutôt que de les retirer sans le demander —
  `cockpit prune` reste le geste de l'utilisateur.
- **Étape d'enregistrement d'un projet rendue visible (#342).** Le cockpit
  ne scanne jamais le disque, il lit le registre — un projet Grimoire valide
  et jamais enregistré disparaissait donc en silence. `cockpit list` et
  `cockpit serve` signalent désormais le dossier courant quand il porte un
  marqueur Grimoire absent du registre, et nomment la commande d'ajout
  attendue.
- **Bouton d'action principale retiré de l'en-tête du cockpit (#362).**
  Aucun écouteur d'événement n'existait pour ce bouton, qui changeait de
  libellé selon l'espace actif sans jamais rien déclencher au clic. Un test
  de non-régression échoue si un futur bouton littéral de l'en-tête est
  ajouté sans écouteur ni câblage glossaire.
- **Le budget de temps du cockpit tient compte des runners Windows (#390),**
  trois fois plus lents sur cent sous-processus git, sans cesser d'attraper
  la régression de #340.

## [3.41.0] - 2026-09-08
### Ajouté

- **Classe de vérifiabilité et dispatch en cascade — épic routage par
  vérifiabilité (#307).** Le contrat d'une tâche dérive une classe V0/V1/V2
  (#316) ; `grimoire task dispatch` cascade dessus (#325) ; le palier de
  départ du dispatch s'ajuste à l'historique des verdicts (#335) ; une classe
  de relisibilité et les incertitudes déclarées viennent affiner la décision
  (#334) ; la politique de dispatch résultante est émise aux hôtes, avec
  l'état des fournisseurs au `SessionStart` (#332).
- **Fournisseurs et coût.** Registre de fournisseurs étendu par palier de
  coût, état runtime et `grimoire providers status` (#317) ; `grimoire
  providers audit` sonde les fournisseurs sans dépenser (#331) ; le choix de
  modèle croise reasoning et coût, Copilot déclare l'écart (#314).
- **Moteur de flow (#204).** `grimoire flow run|status|resume|abort`
  conduit un node à la fois (#326) ; l'exécuteur par dispatch en cascade du
  routage par vérifiabilité est branché dans `flow run` (#336).
- **Garde-fous runtime et hôtes.** Plafonds de tours et de budget par
  instance, garde des échanges pair-à-pair (#320) ; claims à fichiers
  exclusifs, sous-agents bornés, événements `SubagentStart` et
  `PostToolUseFailure` (#321).
- **Sécurité des écritures mémoire et médiation d'outils (#324).** Écritures
  mémoire validées, contrat MCP annoté, contenu externe marqué, médiation
  d'outils vérifiée.
- **Référence agentique industrielle (#315).** Chargée à la demande par les
  agents, plutôt que systématiquement.

### Corrigé

- **Export OTel GenAI et évals avec pass^k (#322).** L'export de traces est
  conforme au schéma GenAI d'OpenTelemetry ; les évals calculent pass^k.
- **Accord pluriel et catalogue de digests après #319 (#333).** Correction
  d'accord introduite par le retrait d'AORA/DCF, et catalogue de digests
  rafraîchi en conséquence.
- **Le garde de CHANGELOG accepte un repli par numéro de PR.** Seize PR de
  cette version ont fusionné sans toucher `CHANGELOG.md` à leur propre
  commit ; `scripts/check-changelog-release.py` accepte désormais un commit
  qui ne le touche pas si son sujet cite `(#NNN)` et que ce numéro apparaît
  dans la section de la version publiée — jamais dans `[Unreleased]` ni une
  version antérieure. La règle par commit reste la référence.

### Retiré

- **Le socle contredisait la doctrine qu'il citait lui-même.** AORA et DCF étaient
  présentés comme actifs dans `framework/agent-base.md`,
  `framework/agent-base-compact.md` et `framework/orchestrator-gateway.md`, alors que
  le retrait des deux (2026-07-12, zéro usage constaté) n'était consigné que dans ce
  changelog et dans la doctrine des projets consommateurs. Les trois fichiers
  distribuaient donc un protocole que le produit ne tenait plus. Les deux sections sont
  remplacées par un bandeau de retrait ; les deux règles opposables qu'AORA portait
  (circuit breaker, cascading initiative) survivent sans son nom sous « Plan
  d'exécution ». PIP passe en statut observer-only, explicite dans les trois fichiers :
  décrit, non instrumenté, aucune obligation. La calibration exécuter/proposer/escalader
  que portait DCF reste assurée par ALS seul.
- **Deux fiches obsolètes qui documentaient un produit imaginaire.**
  `framework/mcp/grimoire-mcp-server.md` décrivait neuf outils MCP fictifs et une
  roadmap « MCP v2 Sampling » alors que Sampling est déprécié et que le serveur réel
  (`src/grimoire/mcp/server.py`) expose vingt-deux outils sans sampling.
  `framework/workflows/state-checkpoint.md` (BM-06) documentait en prose un mécanisme
  de checkpoint doublon du kernel d'exécution réel (`src/grimoire/runtime/kernel.py`,
  machine à états, checkpoints JSONL, reprise). Les deux fiches sont conservées —
  chacune référencée ailleurs dans le dépôt — mais leur contenu est remplacé par un
  bandeau d'obsolescence de moins de dix lignes pointant vers l'implémentation réelle.

## [3.40.0] - 2026-09-07
### Ajouté

- **Source : IntelliSense déterministe — colorisation, correcteur, complétion
  (issue 280).** L'éditeur de l'espace Source colorie le frontmatter, les
  blocs `<agent>`/`<activation>`/`<step>`, les placeholders et les
  identifiants d'agents, de patterns et de workflows, par recouvrement sur la
  `<textarea>` — aucune dépendance ni bundler (ADR-006 D2). Un correcteur
  signale, recalculé à l'enregistrement et au repos de saisie : un chemin du
  kit qui ne se résout pas (même logique que `grimoire doctor`,
  `grimoire.core.integrity`), un agent routé mais absent du manifeste, une
  clé de frontmatter inconnue de son schéma, un terme du glossaire cité sans
  entrée, un pattern ou un workflow inconnu du catalogue — repris dans
  l'onglet Problèmes du dock. La complétion (`Ctrl+Espace`, ou déclenchée par
  `{`, `@`, `/`) propose agents, workflows, patterns, chemins du kit et clés
  de schéma ; le survol d'un identifiant lié au glossaire ouvre la même bulle
  épinglable que le reste de la vue de travail. Nouvelle route en lecture
  seule, servie par les deux hôtes : `GET /api/workspace/language`
  (`src/grimoire/tools/workspace_language.py`). La piste d'un petit modèle
  local (Ollama) pour des suggestions de contenu reste hors périmètre — elle
  brancherait derrière cette IntelliSense déterministe, jamais à sa place.
- **Les codes de patterns convergent vers un seul catalogue, et les cinq DOIT
  restants de #246 sont couverts (#246).** `tools/blueprint_*.py`,
  `tools/handoff.py`, `tools/cost_model.py`, huit extensions et le bridge du
  standard (`framework/agentic-standard/`) citaient chacun des codes
  `ORG-`/`ORC-`/`COG-`/`KNO-`/`MOD-`/`GOV-`/`QUA-`/`RUN-` sans qu'aucun ne
  référence l'autre. Les 36 patterns du bridge
  (`templates/pattern-catalog.yaml`) portent désormais un `catalog_ref` vers
  le ou les codes du catalogue de 78 patterns
  (`web/data/catalogue-export.json`, même révision upstream que
  `profile-map.yaml`) qu'ils implémentent ; `evidence/schemas.py` (QUA-04) et
  `missions/ledger.py` (QUA-03), qui implémentaient chacun un pattern du
  catalogue sans le citer, le citent maintenant. Nouveau module partagé
  `grimoire.core.standard_checks.pattern_codes` ; `standard verify` refuse un
  `catalog_ref` qui ne nomme pas un code réel
  (`patterns.catalog_ref_unknown`) ; `tests/unit/
  test_pattern_code_convergence.py` (27 cas) refuse un code cité n'importe où
  dans le dépôt sans entrée au catalogue.

  Les cinq exigences DOIT que la matrice ne montrait pas comme trous
  (AG-MIS-005, AG-ORC-005, AG-LLM-004, AG-OBS-003, AG-OBS-005) sont
  désormais couvertes par un artefact réel, chacune au niveau où la norme
  les attend : `grimoire task record-model-call` journalise un appel modèle
  (modèle, rôle, coût, latence, erreur) dans le TraceLedger — les champs
  `model`/`token_usage` existaient dans le schéma sans qu'aucun code ne les
  peuple ; `grimoire task handoff` donne enfin un appelant réel à
  `tools.handoff.build_handoff` (#275 l'avait laissé orphelin) et trace la
  communication inter-agents en événement append-only au Mission Ledger ; le
  gabarit `task-envelope.md` gagne une section « Ambiguïtés résolues » ; le
  gabarit `retention-registry.yaml` gagne une entrée RET-004 (type incident,
  rétention durable) ; le gabarit `observability-policy.yaml` gagne une
  entrée `trace_ledger` déclarant les dimensions réellement capturées
  (modèle, outil, hook, verdict, preuve) plutôt que le seul
  `runtime-journal.jsonl`, qu'aucun code n'écrit. `standard traceability
  --profile governed` : zéro trou non justifié ; `standard verify` sur les
  cinq profils, projet jetable : mêmes quatre erreurs préexistantes
  qu'avant sur `governed`/`production` (#269), aucune nouvelle.

- **Le claim rappelle ce que la mémoire sait de la tâche — L6 Task memory
  (#141).** `grimoire task recall <id>`, l'outil MCP `task_recall` et le hook
  `SessionStart` (entre la persona d'entrée et la directive du standard,
  seulement pour une tâche réclamée) rendent le même rappel : l'historique
  propre de la tâche (tentatives précédentes bloquées ou échouées), ses
  voisines — liées par `task link`, de la même mission, ou au titre proche —
  avec la cause de leur arrêt même si elles ont depuis été rouvertes, et ce
  que la mémoire du projet a consolidé sur des sujets voisins quand un backend
  mémoire est configuré. Borné en tokens. `TaskService` consolide dans cette
  mémoire ce qu'une clôture (`decisions`) ou un blocage (`failures`) enseigne
  — jamais un mouvement ordinaire, conformément aux garde-fous de
  `planning/memory-os-roadmap.md` (étape 6, Kanban Task Memory). Nouveau
  module `grimoire.missions.recall` ; `grimoire.missions.service.TaskService`
  gagne `.recall()` et une résolution paresseuse de la mémoire du projet.
  L'espace Exécuter (`spaces/executer.js`) affiche le rappel dans
  l'inspecteur, avec parcimonie — pas de section pour un rappel vide.

## [3.39.1] - 2026-09-07

### Corrigé

- **Piloter distingue kit aligné et kit installé (#288).** Le badge Kit
  affichait « à jour » à côté d'un sous-texte `aligné sur 3.36.0, installé
  3.38.0` — deux versions différentes contredisant un badge d'alignement
  exact. `kit.upToDate` (aucun fichier livré n'a de révision plus récente au
  catalogue) et `kit.aligned === kit.installed` (synchronisation exacte) sont
  deux affirmations distinctes ; le badge dit maintenant « aligné » quand la
  première est vraie sans la seconde, « à jour » seulement quand les deux
  versions coïncident. Aucune donnée serveur ne change
  (`project_health.kit_alignment` reste correct) : la correction est dans le
  rendu, `web/workspace/spaces/piloter.js`.
- **Le glossaire se charge sur une installation nue de la wheel.** Le serveur
  de la vue de travail importait PyYAML alors que la dépendance déclarée est
  ruamel : sur `pip install grimoire-kit` sans extra, `/api/workspace/glossary`
  tombait et les six espaces s'ouvraient avec une erreur (#292, trouvé par le
  passage consommateur sur un projet réel). Le glossaire et le registre des
  plans passent par le chargeur du kit ; un test bloque le module `yaml` et un
  autre refuse tout `import yaml` direct dans ces modules.

### Retiré

- **Onze accesseurs publics sans appelant ni test (#275).** `MemoryManager.
  hot_store/hot_recall/hot_delete/hot_acquire_lease/hot_release_lease`
  (Redis hot memory — R&D non portée, #94) ; `CodeGraph.get_node` et
  `get_dependents` ; `MemPalaceBackend.search_preview` (« convenience method
  used during experiments », jamais adoptée) ; `RecipeRegistry.get_or_raise`
  et `list_recipes` (la classe entière n'a aucun appelant) ;
  `ProjectSurface.hooks_for` (même mode de panne qu'`entry_agent()`, #233).
  Trois autres (`MissionLedger.blocked_tasks`/`events_for`,
  `missions.projections.build_cockpit_from_paths`) restent orphelins par
  choix — zone `missions/` active en parallèle au moment de cette passe,
  documentés dans `KNOWN_ORPHANS` (`tests/unit/test_public_accessor_
  inventory.py`) plutôt que tranchés en silence.

### Ajouté

- **Trois accesseurs orphelins branchés sur un appelant réel (#275).**
  `grimoire task pack <task-id> <pack-id>` lit un pack de preuve complet
  (`EvidenceService.get_pack`), jusqu'ici accessible seulement en liste.
  `grimoire task trace-export <dest> --format otel|langfuse` exporte le
  `TraceLedger` (`export_otel_jsonl` et `export_langfuse` n'avaient tous les
  deux aucun appelant produit). `grimoire memory graph coverage` liste les
  nœuds publics sans arête `TESTED_BY` (`CodeGraph.uncovered_nodes`), sans
  dépendance Neo4j.
- **Garde-fou anti-régression (#275)** :
  `tests/unit/test_public_accessor_inventory.py` réplique l'inventaire AST
  qui a produit #275 et échoue si un nouvel accesseur public de
  `src/grimoire/` se retrouve sans appelant ni test hors de sa propre
  définition.

## [3.39.0] - 2026-09-06
### Corrigé

- **La racine de `grimoire serve` et du cockpit ouvre la vue de travail.** Le
  basculement redirigeait les dix pages d'outil mais laissait `/` sur la page
  vitrine ; la racine mène maintenant à l'espace Piloter, `index.html` demandé
  explicitement reste la vitrine.

### Modifié

- **Basculement, pas 2 (`docs/adr-006-vue-de-travail.md`) — la vue de travail
  devient la page par défaut.** Les cinq lots sont mergés : `grimoire serve`
  et `grimoire cockpit serve` ouvrent désormais `workspace/index.html` dans le
  navigateur (`--open`, activé par défaut) au lieu de `atelier.html` et
  `portfolio.html`. Les quatorze pages historiques restent servies à
  l'identique — rien n'est supprimé avant le pas 3, une mineure ultérieure —
  mais les dix pages *outil* (`atelier.html`, `portfolio.html`, `kanban.html`,
  `observability.html`, `memory.html`, `blueprints.html`, `patterns.html`,
  `extensions.html`, `labs.html`, `documentation.html`) redirigent maintenant
  (302) vers l'espace de la coque qui les remplace ; `?legacy=1` sur l'ancienne
  URL sert encore l'ancienne page sans redirection, pour quiconque n'est pas
  prêt à basculer. Table partagée par les deux hôtes :
  `grimoire.tools.workspace_legacy`. Les quatre pages vitrine (`index.html`,
  `demo.html`, `anatomy.html`, `game-ui.html`) ne redirigent jamais — hors
  périmètre (spec §7).

### Ajouté

- **Squelette de la vue de travail — une coque, deux hôtes, cinq lots.**
  `web/workspace/` porte la coque unique que `grimoire serve` et
  `grimoire cockpit serve` servent à l'identique : six espaces navigables
  (Piloter, Concevoir, Exécuter, Observer, Mémoire, Source), panneaux à trois
  états, dock, palette `⌘K`, mode concentration, thèmes sombre et clair,
  densités Découverte et Concentration. Les espaces sont des stubs qui exposent
  `mount(root, ctx)` : les écrans viennent avec les lots d'implémentation.
  Décisions, alternatives écartées et découpage : `docs/adr-006-vue-de-travail.md`.
- **Lot 1 de la vue de travail — la coque et ses mécaniques.** Les trois états
  de panneau tiennent leur contrat complet : clic sur le rail ouvre en
  surimpression, survol 450 ms entrouvre (jamais au survol du contenu),
  cadenas ou `⌘`/`Ctrl` + clic épingle dans la grille, et le panneau épinglé se
  redimensionne à la souris par une poignée sur son bord. L'état — replié,
  entrouvert, épinglé, largeur — est mémorisé **par espace de travail et par
  projet**, dans `localStorage`, en échec silencieux hors navigation privée.
  Geist et Geist Mono sont embarquées en woff2 (400/500/600 et 400/500, 260 Ko,
  licence SIL OFL 1.1 dans `web/workspace/fonts/OFL.txt`) : plus aucun appel à
  Google Fonts. Neuf tests Playwright de plus couvrent chaque mécanique prise
  séparément (ouverture par clic, entrouverture par survol, non-ouverture au
  survol du contenu, épinglage au cadenas et au clic modifié, redimensionnement
  à la poignée, navigation clavier de la palette, bascule de densité,
  persistance par espace après rechargement).
- **Contrats d'API de la vue de travail**, sous le préfixe `/api/workspace/`.
  Lectures servies par **les deux hôtes** via la table partagée
  `forge_routes.api_get` : glossaire, tâches, détail de tâche avec les preuves
  que chaque pas suivant exigera, timeline unifiée (`task trace`), fichiers par
  étage avec leur empreinte confrontée au catalogue des digests du kit, contenu
  et diff d'un fichier, catalogue des commandes, diagnostic. Écritures réservées
  à l'hôte mono-projet, parce que le cockpit se déclare `readOnly` : claim,
  move, block, close d'une tâche (gate de preuve compris — un refus revient en
  200 avec la preuve manquante nommée), prise d'override, écriture d'un fichier
  d'un étage éditable, exécution d'une sous-commande `grimoire`.
- **`framework/glossary.yaml`** — 61 concepts, source unique des infobulles et
  de la documentation. Un projet peut le surcharger comme n'importe quel fichier
  du kit. Un test refuse un terme cité par l'interface sans entrée au glossaire,
  sur les sources comme sur le DOM rendu.
- **La pile d'infobulles épinglables** (`web/workspace/glossary.js`, lot 2 de
  la vue de travail — spécification §3.2). Survol de 500 ms (800 ms en densité
  Concentration, réduite au nom et au raccourci) ; Alt fige la bulle avec son
  cadenas, le pointeur peut y entrer, sa croix ou Échap la referment ; les
  termes liés ouvrent une bulle enfant, trois niveaux au plus — un quatrième
  est refusé par `glossary.open()` lui-même, pas seulement laissé sans bouton
  pour l'ouvrir. Un clic ailleurs referme les bulles non épinglées et laisse
  les épinglées. Accessible : tout `[data-term]` reçoit un `tabindex` s'il n'en
  avait pas, le focus clavier ouvre la bulle sans délai, `aria-describedby`
  relie l'ancre à sa bulle. `docs/glossaire.md` dérive du glossaire
  (`scripts/gen-glossaire-doc.py`) ; un test unitaire échoue si la page est en
  retard sur le YAML.
- **Console du dock** : `grimoire.tools.workspace_exec` exécute 23
  sous-commandes `grimoire` de lecture, sans shell, sur liste blanche stricte,
  avec drapeaux déclarés et délai maximal. `init`, `up`, `migrate`, `serve`,
  `cockpit`, `upgrade` et `ext` en sont volontairement absents.
- **Harnais de test** : `tests/e2e/` lance `grimoire serve` sur un port haut avec
  `GRIMOIRE_COCKPIT_HOME` détourné, et mesure sur le DOM rendu ce que la
  spécification exige — plancher typographique, contraste, mécaniques au
  clavier. Il se skippe proprement sans Playwright, jamais en faux vert.
- **Lot 3 de la vue de travail — l'espace Concevoir.** Zoom Projet → Workflow
  → Nœud (+ Flotte en lecture seule sur le cockpit) ; au niveau Projet, les
  blueprints du projet servi comme conteneurs en Carte, Board (groupés par
  genre — blueprint classique ou Studio v2) ou Liste (nom, genre, agents
  délégués, équipe, validation, dernière modification) ; au niveau Workflow,
  un éditeur de graphe réécrit contre les routes existantes de bp2
  (`/api/blueprints/<id>`, `/validate`, `/simulate`, `/compile`) — nœuds mis en
  page par rang topologique, bibliothèque des sept primitives en tiroir,
  résultats de validation dans le dock ; au niveau Nœud, le nœud et ses voisins
  directs avec un inspecteur à quatre onglets (Propriétés, Validation, Coût,
  Preuves — ces deux derniers lus sur la pression de contexte et les preuves
  exigées que rend `/simulate`). Nouvelle route partagée
  `GET /api/workspace/blueprints` (`workspace_api.blueprints_view`) : enrichit
  `/api/blueprints` avec ce que la Liste réclame et que
  `project_health.flows` ne porte pas. Limite assumée : l'éditeur de graphe
  n'existe que sur l'atelier — `/api/blueprints/<id>` et ses routes filles ne
  sont câblées que sur le serveur mono-projet ; le cockpit l'affiche en le
  disant plutôt que de tenter un appel qui rendrait 404.
- **Lot 4 de la vue de travail — Piloter, Exécuter, Observer, Mémoire.**
  Piloter : niveau Flotte (cockpit, tableau sur bureau / cartes sur mobile,
  KPI en une carte divisée, « À traiter » toujours visible) et niveau Projet
  (fiche du projet servi, même signaux) ; inspecteur avec mise à jour derrière
  aperçu (`/api/projects/update`, `confirm: false` par défaut) puis
  confirmation. Exécuter : Board 4 colonnes (états repliés nommés sous le
  titre) et Board 8, Liste, Timeline ; carte de tâche à trois niveaux ;
  inspecteur avec critères d'acceptation, preuves déclarées, prochaine porte
  actionnable (`claim`/`move`/`block`/`close`, gate compris) et sa commande
  équivalente dans le dock. Observer : six KPI, coût par modèle (`--s1..3`),
  latence p50/p95/p99 (une teinte neutre — une seule série), spans lents,
  traces par agent ; un seul état vide sur un projet sans trace, jamais un mur
  de zéros. Mémoire : store et graphe d'abord (`/api/memory/status`), couches
  ensuite, l'explication d'architecture derrière un onglet. Les quatre espaces
  ajoutent `api.health(project?)`, `api.memoryStatus(project?)`,
  `api.doctor(project?)`, `api.costModel()` et `api.updateProject()` à
  `web/workspace/api.js` (routes déjà servies par les deux hôtes, ou route
  cockpit existante pour `updateProject`) ; aucun ne dépend d'un jeu de
  données de démonstration.
- **Lot 5 de la vue de travail — l'espace Source et la Console du dock.**
  `web/workspace/spaces/source.js` remplit l'écran nouveau : arbre par étage
  (overrides possédés, kit généré, projections des hôtes) avec badge de
  dérive sur un override qui diverge de son homologue kit ; éditeur à trois
  onglets — Source (textarea avec numéros de ligne), Diff contre le kit
  (unifié, rendu par le serveur, ajouts et retraits par tokens), Rendu
  (Markdown minimal, sans dépendance, ADR-006 D2) ; bandeau « Ce fichier est
  généré par le kit » avec prise d'override en un geste ; enregistrement
  `⌘S` avec confirmation dans le dock. Inspecteur à trois onglets — Fichier
  (étage, version, empreinte au catalogue, override), Utilisé par (projections
  vers `.claude/`, `.github/`, `AGENTS.md` et fichiers qui citent celui-ci,
  deux nouvelles routes `GET /api/workspace/file/usage` et
  `GET /api/workspace/file/history`), Historique (`git log --follow`, ou un
  vide honnête hors dépôt git). La Console du dock — invite, historique au
  clavier, complétion sur la liste blanche, exécution via
  `POST /api/workspace/command`, refus explicite affiché — est rendue
  entièrement à travers le contrat `ctx.dock.log/clear` du lot 1, sans jamais
  toucher le DOM du dock. Neuf tests Python de plus (badge de dérive, usage,
  historique, symlink refusé, hôte étranger refusé sur `/api/workspace/`) et
  cinq tests Playwright sur un projet jetable réel : ouvrir, éditer, override,
  diff, `grimoire doctor` vert après, Console qui exécute et qui refuse.

### Corrigé

- **Le portefeuille peut désormais dire ce qu'il ne sait pas.**
  `project_health()` rend `commits_total` (compté sur `HEAD`, `None` hors
  dépôt git ou sans commit) et `ci_status` (`"unknown"`, honnêtement — aucune
  sonde locale ne le mesure encore) sous ces noms exacts : la vue héritée lisait
  `p.ci` et `p.commits`, deux champs que la donnée réelle n'a jamais portés.
  `antifragile` reste `None`, avec `antifragile_note: "pas encore mesurée"` —
  jamais un score à zéro pour un score qui n'a simplement jamais été calculé.
  `demo` vaut toujours `False` : ce module ne lit aucune donnée de vitrine.

- **Trois valeurs de la palette ne tenaient pas le contraste que la
  spécification exige d'elle-même**, trouvées par la mesure et non par la
  relecture. En thème clair, `--ink3` valait 3,78:1 sur `--e1` là où la spec
  annonce 4,6 ; il passe à `#656a72`. En thème sombre, `--ink3` tenait sur
  `--e1` mais tombait à 4,35:1 sur `--bar`, où la barre d'état et l'écho du dock
  le rendent ; il passe à `#818891`. Le libellé blanc de l'action primaire en
  thème clair valait 4,29:1 ; `--pri` passe à `#d24619`. Détail des mesures :
  `docs/adr-006-vue-de-travail.md`, section « Ce que la mise en œuvre a corrigé ».

- **Intégration des cinq lots — la palette `⌘K` listait les fichiers de Source
  mais n'en ouvrait aucun.** `run()` faisait `goto('source')` sans le chemin
  choisi, et `shell.js` écrasait `location.hash` avant que `source.js` n'ait pu
  le voir. `goto(id, params)` relaie désormais `{ file }` dans `ctx.params`,
  lu une fois au montage suivant — sans persister dans le hash.

- **Intégration des cinq lots — le raccourci `2` du rail (bibliothèque)
  n'activait rien**, ni au clic ni au clavier, faute d'un panneau de la coque
  à cet identifiant. `ctx.rail.on(id, handler)` laisse l'espace monté
  enregistrer ce que fait un rail sans panneau dédié ; Concevoir y branche le
  tiroir de bibliothèque de nœuds, remis à zéro à chaque changement d'espace.

## [3.38.0] - 2026-09-04

### Évalué

- **Campagne evals 2026-09-04 — bras `enforced` contre `activated-v3` : effet
  non démontré, indicatif.** Première campagne pré-enregistrée après
  l'amendement A2 (`evals/reports/2026-09-04/`). Le bras `enforced` ajoute à
  l'activation les deux hooks bloquants du kit (refus PreToolUse, gate Stop)
  au profil `governed`. Sur 24 runs par bras (3 répétitions, sous puissance :
  règle d'arrêt budgétaire puis limite de dépense du compte), le gate obtient
  l'artefact qu'il exige (`context-bundle` 23/24 contre 0/24) et rien d'autre :
  complétion 3 contre 5, coût par run +41 %, 0 régression dure contre 1. Le
  blocage change le volume de preuve, pas ce qui est livré. Verdict hors
  compteur de la clause 2 d'A2. Le mécanisme `enforced` est committé
  (`evals/witnesses/web-app-todo/enforced/`), ainsi que le runner de campagne,
  les paquets de jugement aveugle et l'agrégateur (`evals/runner.py`,
  `evals/judge.py`, `evals/aggregate.py`).

### Modifié

- **La borne `chromadb<0.7` est un choix instruit, plus un héritage.** Mesuré
  sur deux venv jetables (#174) : les sept appels que le backend `mempalace`
  fait au client embarqué rendent les mêmes clés en 0.6.3 et 1.5.9, un palais
  0.6 se relit tel quel en 1.x — et pas l'inverse — et la suite mémoire est
  verte sous 1.5.9. Lever la borne n'apporte rien et ajouterait une CVE
  d'injection pré-authentification au périmètre audité ; les trois CVE
  waivées n'ont de correctif sur aucune version. La borne reste, son
  commentaire dit pourquoi, et les trois waivers sont reconduits au
  2027-02-28 après revérification OSV du 2026-09-04.

- **L'étage TestPyPI, qui n'a jamais tourné, disparaît de `publish.yml`.** Le
  projet n'a jamais été enregistré sur test.pypi.org : le job échouait à
  chaque tag et son `continue-on-error` le faisait passer pour une
  pré-vérification (#195). Ce qui vérifie réellement qu'une version
  s'installe est nommé et documenté dans `CONTRIBUTING.md` : `make
  wheel-check` (wheel installée dans un venv neuf) avant le tag, les jobs
  `build` et `test` de `publish.yml` au tag, dont `publish-pypi` dépend sans
  `continue-on-error`. La cible `make publish-test` disparaît avec l'étage.

### Ajouté

- **`grimoire task trace <id>` — la cause d'un arrêt sans ouvrir un fichier
  (L4, #139).** Timeline unifiée d'une tâche, lue depuis les quatre journaux
  qui portaient déjà le `task_id` sans que rien ne les lise ensemble : Mission
  Ledger (transitions, incidents), TraceLedger des hooks (outils refusés par la
  policy, clôtures refusées), RuntimeKernel (run events, checkpoints, abort et
  sa raison), EvidenceService (packs, verdicts). Les entrées qui expliquent un
  arrêt sont marquées et reprises en « Cause(s) d'arrêt » ; `--causes` les
  isole, `--output json` rend tout. Aucune source absente n'est inventée, aucun
  dossier n'est créé en lisant, et la stack legacy `observatory.py` n'est pas
  sollicitée. Deux écritures rendent cela possible : le gateway de hooks —
  seul écrivain du TraceLedger — porte désormais le `task_id` résolu sur chaque
  événement (les refus de policy étaient journalisés sous un identifiant vide,
  donc introuvables par tâche), et un gate de transition rouge laisse une trace
  `grimoire.task-gate` (au TraceLedger, jamais au Mission Ledger : un refus
  n'est pas un changement d'état).

- **Aucun niveau de la norme ne laisse plus d'exigence obligatoire sans
  artefact.** `grimoire standard traceability` listait dix-sept exigences
  `AG-*` obligatoires jusqu'à N4 que le kit ne couvrait par rien (#246). Six
  artefacts les ferment, chacun avec son gabarit, son vérificateur et ses
  tests : le dossier d'acceptation (AG-QUA-003, tous profils), le registre de
  rétention (AG-RET-001/003/004/005, tous profils), le registre d'outils avec
  contrats MCP et capture des erreurs (AG-TOL-001/003/005, dès `controlled`),
  le registre d'incidents avec politique de containment (AG-INC-001/002/003,
  dès `controlled`), la matrice risques/contrôles/preuves pré-remplie des
  défauts IA que la norme nomme (AG-AUD-003, AG-QUA-005, dès `controlled`) et
  le registre des capacités dynamiques (AG-DYN-001 à -005, dès `governed`).
  Deux exigences tiennent dans un champ : le bloc `wip:` d'`orchestration-policy`
  (AG-ORC-004 — une délégation `allowed` est une erreur) et la section
  `Traceability` de la déclaration de conformité (AG-AUD-001). Pour celle-ci,
  `grimoire standard traceability <projet>` joint désormais à la matrice le
  verdict que `standard verify` rend sur chaque artefact, et compte les
  exigences effectivement vérifiées. Un projet neuf vérifie sans erreur
  nouvelle sur les cinq profils ; un projet enrôlé reçoit les fichiers par
  `standard fix --apply`. Deux tests interdisent le retour des trous : les
  dix-sept identifiants doivent rester couverts par un artefact requis au
  niveau où la norme les attend, et la section `gaps` doit rester vide.
- **Les agents lisent, réclament et clôturent leurs tâches par MCP (L3,
  #138).** Le serveur MCP expose `task_list_ready`, `task_show`, `task_claim`,
  `task_update` (move / block / close) et `task_context`. Ils appellent le
  service que `grimoire task` appelle désormais aussi
  (`grimoire.missions.service.TaskService`) : même machine à états, même gate
  de preuve, même refus structuré — la preuve manquante et le remède, et rien
  d'écrit au ledger. Un contrôle négatif échoue si l'un des deux contourne le
  gate. Prouvé par un vrai client du SDK `mcp` sur un projet enrôlé en
  `governed` : lister, réclamer, déplacer, être refusé sans pack de preuve,
  prouver, clore ; le board projeté passe `ready → in_progress → accepted`.

### Corrigé

- **Le rendu des surfaces hôtes dit quand il échoue.** `init`, `doctor --fix`
  et `standard init` régénéraient agents, skills, commandes et hooks derrière
  trois copies d'un `except Exception: return []`, chacune justifiée par
  « `host status` rapportera la dérive ». Un projet dont les surfaces ne se
  rendaient pas sortait donc d'`init` avec un rapport vert et aucun hook, et
  `doctor --fix` affichait « 22/22 checks passed ». Un seul écrivain désormais
  (`grimoire.hosts.sync`), qui rend l'échec en donnée et l'écrit sur stderr ;
  les trois commandes affichent l'avertissement, et `standard init -o json`
  le porte dans `host_surfaces`.

- **Un pheromone board corrompu est mis de côté, plus écrasé.** Un board
  tronqué (disque plein, deux hooks concurrents sans `flock`) était lu comme
  vide, puis réécrit par le dépôt suivant : tout l'historique stigmergique
  disparaissait et `deposit_pheromone` rendait un `Pheromone` comme si de
  rien n'était. Le fichier illisible est désormais renommé
  `pheromone-board.json.corrupt-<horodatage>` avant qu'un board vide reparte,
  et une ligne sur stderr le dit. La copie autonome `framework/tools/` a le
  même défaut, documenté en #265 (zone gelée).

- **Un fichier de gates illisible ne laisse plus passer toutes les
  transitions.** `evidence-gates.yaml` cassé était lu comme `{}` : aucune
  transition déclarée, donc aucune gardée, donc `task move` et `task close`
  passaient sans preuve — le gate était d'autant plus vert que son fichier
  était abîmé, à rebours de ce que le module promet. Le lecteur distingue
  désormais « absent » (aucune transition) de « illisible » (`GatesFileError`)
  : `check_transition` rend un verdict bloquant `hard_fail` qui nomme le
  fichier et la cause, `task show` le signale au lieu de se taire, et un
  registre de fournisseurs cassé dit « illisible » au lieu du faux diagnostic
  « aucun fournisseur activé ».

- **Un manifeste d'équipe illisible est nommé, plus omis.** `parse_team`
  répondait `None` pour « pas une équipe » comme pour « YAML cassé » : l'équipe
  manquait à `workflows teams` sans une ligne. Le chargeur distingue les deux
  (`TeamManifestError`), `load_team_catalog` rend les équipes lues et les
  fichiers illisibles avec leur cause, et `workflows teams` les affiche — en
  texte et dans `unreadable` en JSON.

- **Chaque PR exécute la CI complète.** `ci-sdk.yml` et `ci-validate.yml`
  filtraient les `pull_request` par chemin : une PR qui ne touchait ni `src/`
  ni `tests/` ne déclenchait aucun des checks que la protection de `main`
  exige depuis le 2026-09-04, et restait bloquée sans jamais avoir été
  vérifiée — le contraire de ce qu'un check requis promet. Les filtres
  restent sur `push` ; sur une PR, tout tourne. Même retrait pour
  `agentic-standard.yml`, dont le check `standard` est requis.
- **Les hooks ne dégradent plus en silence.** Quatre gardes pouvaient passer
  au vert sans l'avoir mérité, et le défaut n'est apparu qu'en écrivant le
  contrôle négatif. (1) Une décision qui plantait rendait `ALLOW` — sur
  `PreToolUse`, c'est `permissionDecision: allow`, et l'hôte n'affiche pas le
  contexte explicatif : le plantage du moteur de politique était une
  auto-approbation sans trace. Un appel que la politique n'a pas pu juger
  rend désormais `ask`, avec la cause dans le motif ; les hôtes qui ne savent
  pas demander la reçoivent en contexte. (2) Un gate de preuve qu'on ne sait
  pas évaluer — task-board illisible — laissait fermer une tâche `governed`
  avec un contexte que l'hôte ne lit pas sur `Stop` ; il bloque désormais une
  fois, en nommant le fichier à réparer, et `stop_hook_active` garantit que
  le second `Stop` passe. Les profils non bloquants sont prévenus, pas
  bloqués. (3) Le message système annonçait « capsule écrite avant
  compaction » même quand le disque avait refusé ; il dit maintenant qu'elle
  n'a pas été écrite, et pourquoi — et la capsule est écrite même quand les
  gates sont inévaluables, avec l'identifiant de tâche et le profil, les deux
  faits que la fenêtre suivante ne sait pas reconstruire. (4) Un verdict
  non bloquant sur `Stop` ne vivait que dans `additionalContext`, qu'aucun
  hôte ne lit à cet événement ; il est aussi rendu en `systemMessage`.

- **Deux étapes de CI ne pouvaient pas échouer, une troisième ne pouvait pas
  se prononcer.** Le verdict sur `grimoire-init.sh doctor` soustrayait trois
  `grep -c` l'un de l'autre — toute ligne citant Qdrant ou un chemin attendu
  comptait en négatif, erreur ou non, et un doctor qui n'avait pas tourné
  laissait un fichier vide, zéro erreur, étape verte ; `scripts/ci-doctor-gate.py`
  exige la bannière du doctor et fait échouer l'étape sur toute ligne `✗`
  hors manques attendus, en la nommant (six contrôles négatifs). L'étape de
  dérive du standard amont sortait en 2 ou 3 sous `continue-on-error` : rouge
  en permanence, ignorée par tous, et une vraie erreur de la commande passait
  au même rouge ignoré ; les deux cas connus sont dits en avertissement et
  sortent en 0, tout autre statut échoue. Enfin le job `standard`, check
  requis par la protection de `main`, était filtré par chemins : sur une PR
  qui ne touchait pas le standard il restait « attendu » sans jamais se
  prononcer et la PR ne pouvait pas être fusionnée — un garde qui bloque tout
  ce qu'il ne regarde pas. Il tourne sur chaque PR.

- **Le hook SessionStart nommait toujours `bootstrap`, quelle que soit la
  tâche réclamée.** Deux causes, toutes deux vérifiées sur un projet neuf :
  `.claude/activation-context.md`, écrit par `standard init`, portait
  `bootstrap` en dur et primait sur la tâche résolue ; et la résolution ne
  lisait que le board, une projection que rien ne régénérait après un claim.
  Le fichier installé est désormais un gabarit (`{task_id}`), un fichier
  ancien resté au défaut est rendu avec la tâche courante, et la résolution
  préfère le **claim actif du Mission Ledger** (`claimed` / `running`,
  restreint à `GRIMOIRE_ACTOR` s'il est posé) au board, sous `GRIMOIRE_TASK_ID`.
  Chaque écriture de `grimoire task` reprojette le board du standard ;
  `grimoire standard activation-context` sans `--task-id` résout la tâche
  courante au lieu de `bootstrap`. La règle est documentée dans la référence
  CLI (« Quelle tâche la session porte »).

- **Le score ne route plus les checks par correspondance de préfixe.** Un
  préfixe non déclaré tombait silencieusement dans le bucket `artifacts`.
  Mesuré sur les 262 identifiants que le kit émet aujourd'hui : **26 étaient
  mal dirigés** — les huit `gates.*`, que le préfixe déclaré `gate.` ne peut
  pas atteindre ; les douze `patterns.*` et les quatre `remote.*`, qu'aucun
  préfixe ne nommait. Chaque check émis est désormais déclaré dans
  `core/standard_checks/registry.py`, et trois tests interdisent la dérive : un
  check sans déclaration, une dimension inconnue du score, une entrée de
  registre sans check correspondant. La table de préfixes subsiste en repli
  pour les checks qu'un projet émet lui-même. Les dix-sept checks `claims.*` et
  `surfaces.*` arrivés avec la 3.37.0 sont déclarés avec les autres.

- **Deux checks étaient comptés sur une dimension sans poids, donc jamais
  comptés.** `observability_cockpit` figurait dans la table de routage sans
  entrée dans les poids ; or le calcul rend `100` dès que le poids est nul
  (`percentage = ... if weight else 100`). Les deux `promptver.*` marquaient
  donc 100 % quoi qu'il arrive, tout en ne pesant rien : verts à l'affichage,
  absents du résultat. Ils rejoignent `runtime_journal`, la dimension
  d'observabilité réellement pondérée.

  Ces deux corrections laissent le score inchangé sur un projet fraîchement
  scaffoldé — vérifié sur les cinq profils, dimension par dimension, aucun des
  checks concernés ne s'y déclenche. Elles ne changent le score que là où ces
  checks tombent, ce qui était précisément le défaut.

- **Le contrat d'adapter de runtime externe est unifié.** Il existait trois
  fois : un `_slugify` identique dans les trois adapters, une méthode d'entrée
  nommée successivement `import_flow`, `import_graph` puis `convert`, et aucune
  surface commune entre les trois rapports. `runtime/adapter_base.py` fournit
  l'unique `slugify`, un protocole `ImportReport` et un protocole
  `RecipeAdapter` dont la méthode canonique est `to_recipe`. Les anciens noms
  restent en alias, conformément à l'ADR-002. `grimoire.runtime` exporte
  désormais `Recipe`, `RecipeStep` et `VerificationGate`, qu'un auteur
  d'adapter devait importer depuis un sous-module.

  La valeur de `VerificationGate.blocking` n'est **pas** uniformisée, et c'est
  délibéré : le `blocking=False` de CrewAI et LangGraph est ce qui fait
  atterrir une tâche importée en `NEEDS_VERIFICATION` au lieu de se clôturer
  seule ; le `blocking=True` de Gas City porte sur les molécules qui déclarent
  exiger une preuve. Les deux sémantiques sont justes.

- **`GasCityConverter` vérifie enfin l'`output_schema`.** Il le transportait
  jusqu'à la `Recipe` sans jamais l'exiger, là où CrewAI et LangGraph refusent
  une définition qui ne déclare pas sa sortie : une formule invérifiable
  rendait `ok`. Son rapport gagne `missing_output_schema`, présent dans
  `to_dict()`. **Rupture assumée** : une formule sans `output_schema` est
  désormais refusée.

## [3.37.0] - 2026-09-03

### Ajouté

- **Le bridge trace chaque artefact vers les exigences de la norme.** Le
  profile-map parlait en noms d'artefacts propres au kit ; `grep AG-` sur le
  bridge rendait zéro ligne. `traceability.yaml` relie les 43 types d'artefacts
  et les familles de vérificateurs aux `AG-*` et `CTRL-*` qu'ils satisfont, avec
  la citation de la matrice normative qui justifie chaque lien — et une raison
  pour chaque artefact qui n'en a pas (dix-sept). Chaque profil porte son niveau
  de conformité, N1 à N5. `grimoire standard traceability` rend la matrice d'un
  profil et les exigences obligatoires à son niveau que rien ne couvre encore.
  C'est l'artefact qu'AG-AUD-001 demande.
- **Le garde de release vérifie que chaque changement fusionné a son entrée,
  au bon endroit.** Il vérifiait qu'`[Unreleased]` était vide et que la section
  la plus récente portait le numéro publié — deux propriétés vraies sur la
  3.36.0 alors que deux PR n'avaient aucune entrée et que trente-huit blocs de
  deux autres avaient glissé sous `[3.35.0]`, une version publiée sans eux : une
  PR ouverte avant une release et fusionnée après voit git recaler ses lignes
  par contexte. Pour chaque commit `feat`, `fix` ou `perf` depuis le dernier
  tag, le garde exige qu'il touche `CHANGELOG.md` et que chaque titre d'entrée
  qu'il a ajouté soit aujourd'hui sous la version publiée ou sous
  `[Unreleased]`. Sans tag atteignable, la couverture est déclarée non vérifiée
  — et non vérifié n'est pas vérifié.
- **Deux artefacts que la norme rend obligatoires et que le kit ne livrait
  pas.** Le claim ledger (AG-QUA-002, exigé dès le premier niveau) relie chaque
  affirmation qui pèse sur une décision à ce qui la prouve : `claim-ledger.md`
  est généré aux côtés de l'evidence pack pour tous les profils. Le registre des
  surfaces runtime (AG-TOL-007 et AG-RET-006, exigés dès `governed`) donne à
  chaque hook, agent, policy ou sortie un owner, un mode, une rétention et un
  statut : `runtime-surface-registry.yaml` est généré pour `governed` et
  `production`. Deux vérificateurs les lisent : un registre encore vierge est un
  avertissement, il attend d'être rempli ; une affirmation dite prouvée sans
  preuve, ou — en profil gouverné — utilisée sans l'être, et une surface sans
  owner sont des erreurs. Un projet déjà enrôlé les reçoit par
  `grimoire standard fix --apply`.

- **La persona d'entrée se choisit par projet.** Elle était `concierge` en dur :
  `collect_agents` acceptait un autre nom, mais son seul appelant ne le passait
  jamais. Un projet qui porte déjà son propre point d'entrée — un orchestrateur
  chargé par `CLAUDE.md` — en recevait un second à chaque `session_start`, sans
  rien qui dise lequel prime. `agents.entry` dans `project-context.yaml` nomme
  la persona (`concierge` par défaut) ou, vide, déclare qu'il n'en faut aucune.
  `host status` montre la persona retenue et signale une clé qui nomme un agent
  absent au lieu d'en inventer un. Le schéma et `lint` connaissent la clé.
- **Le bridge épingle la révision du standard qu'il trace.** `profile-map.yaml`
  nommait le corpus normatif par nom de dépôt et chemin de fichier, sans SHA ni
  date : impossible de dire si le bridge avait été relu après le dernier commit
  de la norme autrement qu'en comparant les deux dépôts à la main.
  `metadata.upstream_standard` porte désormais `remote`, `commit` et
  `pinned_on` ; `grimoire standard upstream` compare la révision épinglée à la
  tête distante et sort 0, 2 (le standard a avancé), 3 (injoignable, donc non
  vérifié) ou 1 (aucun pin). La CI du bridge l'exécute en avertissement.

### Corrigé

- **Quarante-six outils de `framework/tools/` ne meurent plus sur une console
  cp1252.** Filets de tableau, flèches, coches : chacun levait
  `UnicodeEncodeError` sur une simple commande de lecture chez un utilisateur
  Windows — une classe, pas un incident, révélée par la matrice Windows de
  #191. Le correctif est de classe et tient en deux lignes en tête de chaque
  `main()` : la sortie est reconfigurée en UTF-8 avec remplacement, et un flux
  sans `reconfigure` est laissé en paix. Pas de module partagé : ces outils
  sont chargés de trois façons — script, `import_module`, chargeur par chemin —
  et seul un code sans import survit aux trois. Un test prouve d'abord qu'une console
  cp1252 meurt bien sur un filet, puis que le même texte passe ; un autre
  refuse tout outil qui imprime hors cp1252 sans forcer l'UTF-8. Aucune variable
  d'environnement posée en CI : la matrice ne verdit que si le bug est corrigé.

- **`grimoire setup` écrit la source de vérité qu'il déclare.** Les options
  `--user`, `--lang`, `--doc-lang` et `--skill-level` vivaient dans un objet en
  mémoire : `apply` ne réécrivait que `copilot-instructions.md`, puis vérifiait
  ce miroir contre l'objet en mémoire et annonçait « All config files are in
  sync » — au-dessus de la divergence qu'il venait de créer, que `setup --check`
  signalait la seconde d'après. `apply` écrit désormais la section `user:` de
  `project-context.yaml` en premier — en la créant si le projet est antérieur à
  son introduction, sans toucher `project.name` — puis les miroirs, puis vérifie
  les miroirs contre le fichier relu. Un « in sync » ne peut plus être annoncé
  au-dessus d'une divergence.

## [3.36.0] - 2026-09-03

### Ajouté

- **La persona d'entrée entre dans la session.** `collect_agents` marquait
  depuis toujours une persona `entry_point`, et `ProjectSurface.entry_agent`
  savait la retrouver ; l'accesseur n'avait aucun appelant. Aucun hôte ne sait
  ouvrir une session *à l'intérieur* d'un agent — Claude Code n'instancie un
  sous-agent que par son outil Agent, Copilot que par le sélecteur de chat. Le
  manque est nommé : `HostProfile.agent_autostart`, faux sur les cinq profils,
  et `gaps_for` en donne le substitut hôte par hôte. Le substitut est fourni :
  `decide_activation` compose désormais la persona puis la directive du
  standard. La session n'est pas ouverte dans l'agent, sa persona est remise à
  la boucle principale, qui garde toute la surface d'outils de l'hôte.

- **Les workflows se déclarent.** Un frontmatter `kind` / `description` /
  `agents` / `team` / `triggers` fait entrer un workflow au catalogue, plutôt
  que de le deviner depuis son emplacement — sous `workflows/` vivent aussi
  des gabarits de rapport rendus à chaque run. Les six orchestrations et les
  sept commandes livrées le portent désormais.

- **`grimoire workflows teams`** — les équipes disponibles, leurs membres et
  la chaîne de handoff.


- **« Est-ce que ça tourne, et où ? » a une réponse.** Le noyau d'exécution du
  kit tenait déjà le registre — instances, événements, checkpoints sous
  `_grimoire-runtime-output/runtime/` — mais rien ne le lisait. Le portefeuille
  et le tableau de bord affichent maintenant les exécutions en vol avec l'étape
  courante nommée par leur dernier checkpoint, et ce qui reste à faire.
  Un processus interrompu n'écrit jamais son statut terminal : au-delà d'une
  heure sans signal, son exécution est montrée « sans nouvelles » plutôt que
  comptée comme active.

- **Le portefeuille pilote la flotte.** Chaque carte porte désormais trois faits
  vérifiables, tous issus de `grimoire.tools.project_health` :
  l'**alignement kit** (par digest de contenu — un fichier est en retard quand
  le kit connaît une révision plus récente du même chemin, pas quand son
  numéro de version est ancien), les **flows** réellement composés dans le
  projet, et l'**activité** : dernière trace écrite, avec sa fraîcheur, plus les
  tâches que le board déclare en cours. Rien n'affirme qu'un processus tourne —
  on rapporte ce qui est écrit sur le disque et ce que le projet dit de lui-même.

- **Mettre un projet à jour depuis l'UI.** `POST /api/projects/update` lance
  `grimoire up` sur le projet, sur les deux hôtes. L'aperçu (`--dry-run`) est le
  défaut ; l'écriture réelle exige `confirm: true` et laisse une trace gouvernée.
  Le portefeuille en fait un parcours en deux temps : on voit ce qui changerait,
  puis on décide.

- **`GET /api/health`** — alignement, flows et activité d'un projet, servi par
  l'atelier comme par le cockpit. La couche `health.json` est générée avec les
  autres.

- **Le tableau de bord de l'atelier porte les mêmes indicateurs** que le
  portefeuille — alignement kit et dernière activité, lus sur `/api/health` :
  les deux surfaces disent la même chose du même projet.

- **Le sélecteur de projets arrive sur le portefeuille.** Il vit maintenant dans
  `web/project-picker.js`, chargé à la demande par l'atelier comme par le
  cockpit : la page d'accueil du cockpit était le seul endroit sans entrée pour
  enrôler un projet, alors que c'est le plus naturel. Une seule implémentation —
  deux copies auraient divergé à la première correction.

- **Découverte des projets depuis l'atelier.** Le bouton de projet de la barre
  latérale ouvre un vrai sélecteur : les projets connus de la machine, une
  navigation dossier par dossier (ou un chemin collé), et un scan borné d'une
  racine qui propose sans enrôler. Choisir un projet re-route le serveur en
  cours — `grimoire serve` devient multi-projets sans second processus.

- **Nouvelles routes locales** : `GET /api/projects`, `GET /api/fs/browse`,
  `GET /api/data/status`, `POST /api/projects/{select,add,scan}`,
  `POST /api/data/refresh`. Le cockpit sert lui aussi la découverte
  (`/api/fs/browse`, `/api/projects/add|scan`) : les pages Mémoire, Kanban et
  Observatoire portent le chrome de l'atelier et y affichent le sélecteur, dont
  deux entrées sur trois auraient sinon renvoyé un 404.

### Modifié

- **Le seuil de couverture passe de 70 % à 75 %.** La couverture mesurée est
  de 75,6 %, identique en local et sur les trois plateformes de la matrice :
  le seuil laissait cinq points acquis qu'une régression pouvait rendre sans
  que rien n'échoue. Le seuil se relève quand la couverture monte, jamais
  l'inverse.

- **`cmd_memory` sort de la liste des fichiers hérités du ratchet.** Les
  commandes `memory graph` et `memory vector` partent dans
  `cmd_memory_projections.py` — même convention que `cmd_memory_ops.py`. Le
  module principal passe de 1554 à 1284 lignes, sous le seuil de 1500 : trois
  fichiers hérités deviennent deux. Aucun changement de surface CLI.

- **La récupération hybride est le chemin par défaut.** La fusion RRF du
  classement vectoriel et du classement BM25 existait, était testée, et
  n'était atteignable que derrière un `--hybrid` que rien n'activait : le
  serveur MCP — le seul chemin de lecture qu'empruntent les agents — appelait
  la recherche mono-backend. L'index compagnon était donc écrit à chaque
  `store` et interrogé par personne. La fusion s'applique désormais partout où
  il y a deux classements à fusionner ; `--no-hybrid` force le backend seul.


- **Les journaux d'événements sont lus par la fin.** `activity()` chargeait
  chaque fichier en entier pour n'en garder que la dernière ligne horodatée —
  204 Kio sur ce poste pour le journal task-flow, et ces fichiers ne font que
  grossir (un autre y atteint 14 Mo). Fenêtre de 64 Kio, ligne tronquée écartée.

- **Pas de couche `health.json` générée.** La fiche d'en-tête porte ce dont le
  portefeuille a besoin et `/api/health` sert la vérité fraîche sur les deux
  hôtes : une couche de plus se serait périmée entre deux régénérations et
  aurait pu contredire l'API.

- **La surface HTTP de l'atelier quitte `forge_server`** pour
  `grimoire.tools.forge_http` : gardes d'hôte, table de routage, service de
  fichiers et flux SSE d'un côté, `ForgeAPI` de l'autre. Le module dépassait le
  seuil de 1500 lignes du ratchet de taille ; la coupe suit une frontière réelle
  — ce qui se teste par une requête, et ce qui se teste par un appel de méthode.
  `make_handler`, `serve` et `main` restent ré-exportés à leur ancienne place.

- **Un seul parcours du dossier des blueprints.** `blueprints_list` et la vue
  santé lisaient chacune `_grimoire/blueprints`, avec deux définitions de son
  emplacement : elles auraient fini par répondre différemment sur le même
  projet. L'inventaire est désormais unique (`/api/blueprints` gagne au passage
  l'état de validation et la date de compilation).

- **Une mise à jour par projet à la fois.** `grimoire up` est idempotente, pas
  réentrante : deux exécutions concurrentes écriraient les mêmes fichiers en
  même temps. Un verrou par projet refuse la seconde au lieu de la lancer.

- **Un processus qui ne démarre pas est rapporté, pas levé.** L'`OSError` de
  `subprocess.run` remontait jusqu'au handler HTTP, qui ne l'attrapait pas —
  500 sans explication pour une UI qui attend un compte rendu.

### Corrigé

- **L'atelier local ne propose plus la démo ni « pip install » à qui
  l'exécute déjà.** `grimoire-mode.js` force `index.html` et `portfolio.html`
  en habillage vitrine, et `forge-nav.js` y rendait la nav publique telle
  quelle : un lien « DÉMO » vers la vitrine marketing et un bouton
  « LANCER L'ATELIER → pip install grimoire-kit », sur la page même qui pilote
  les projets de la machine. L'origine est une dimension à part du mode :
  `grimoire-mode.js` expose `window.GrimoireLocal`, et la nav s'en sert pour
  omettre la démo et remplacer l'invitation à installer par un lien direct vers
  l'atelier. Le test exécute les deux scripts sous Node et lit ce que la nav
  rend selon l'origine.

- **Quatre prompts livrés redisaient une commande du SDK.**
  `/grimoire-status`, `/grimoire-health-check`, `/grimoire-self-heal` et
  `/grimoire-pre-push` refont ce que `grimoire status`, `doctor`,
  `doctor --fix` et `check` font déjà — et occupaient la moitié du catalogue,
  qui donnait l'impression d'un produit sachant seulement diagnostiquer. Ils
  déclarent désormais la commande qui les remplace, sortent de la vue par
  défaut (`--all` les montre) et ne sont plus déployés dans les projets neufs.
  Ils restent livrés et installables : un projet qui les a ne les perd pas, et
  `workflows doctor` ne les réclame plus. Les trois autres —
  `/grimoire-changelog`, `/grimoire-dream`, `/grimoire-session-bootstrap` —
  lisent l'historique et la mémoire pour en tirer une synthèse, ce qu'aucune
  commande ne fait : ils restent de plein droit.

- **Les champs `team` et `patterns` étaient déclarés et vides.** Deux
  workflows de plus déclarent leur équipe — `subagent-orchestration` nomme le
  roster de `team-build`, et la spécialité de `team-ops` contient « Incident
  Response » — et trois citent le pattern qu'ils instancient (`ORC-01` pour
  les deux orchestrateurs, `ORC-09` pour le checkpoint d'état). Les trois
  restants n'en déclarent aucun : leurs fichiers ne l'ancrent pas, et le
  deviner contredirait la règle qui veut qu'une description soit dérivée de
  son artefact. Deux tests refusent un identifiant de pattern hors catalogue
  et une équipe qui ne se résout pas.

- **Deux workflows livrés perdaient leur frontmatter en silence.** Leur
  description contenait un `:` non échappé ; le YAML échouait, le parseur
  renvoyait un dictionnaire vide, et le workflow arrivait sans nom ni agents.
  Un test paramétré sur les fichiers livrés ferme le cas.

- **`grimoire serve` ne montre plus les données d'un autre projet.** Le site
  embarqué dans la wheel contient l'instantané de la vitrine publique : des
  projets inventés (« Atlas Ops », « Sentinel Sec », « Ledger Data ») et les
  chiffres du dépôt du kit. Comme `serve` n'exposait aucune surface projet,
  l'UI retombait sur cet index, n'y trouvait pas le projet ouvert et servait le
  primaire : Mémoire affichait « aucun backend · 141 entrées » sur un dépôt
  vide, Kanban dix tâches inventées, Observatoire des traces d'agents datées
  d'il y a deux minutes. Les couches de télémétrie (`meta`, `taskboard`,
  `observatory`, `activity`, `insights`, `memory`, `projects.json`) ne viennent
  désormais que d'une génération faite sur le projet servi ; leur absence est
  un 404, et les pages affichent leur état vide. Les références du kit
  (catalogue de patterns, marketplace, anatomie, couverture) restent servies.

- **Le nuage de la page Mémoire montre de vrais embeddings, ou rien.** Il était
  tiré au sort — `random.Random(42)` autour de cinq centres, avec les vrais noms
  de types dessus. Il projette maintenant les vecteurs que le backend possède
  réellement, par analyse en composantes principales (une trentaine de lignes,
  aucune dépendance nouvelle). Un backend lexical ou fichier n'a pas
  d'embedding : la réponse est alors « rien », et le panneau dit lequel des deux
  cas il montre au lieu d'un « vecteurs absents » qui se lisait comme une panne.
  Nouveau `MemoryBackend.vectors()`, vide par défaut, implémenté pour Qdrant.

- **Le remplissage de démonstration du générateur devient opt-in.**
  `gen-site-data.py` fabriquait des traces horodatées à l'instant, un board
  garni de dix cartes du template et un nuage vectoriel tiré au sort dès que le
  projet n'avait rien à montrer. Réservé à la vitrine publique, qui n'a pas de
  runtime propre, il s'active maintenant par `--demo`
  (`GRIMOIRE_SITE_DEMO=1` pour `serve-site.sh`).

- **Les liens d'un projet non servi passent par un re-routage.** Sur l'atelier,
  qui ne sert qu'un projet et ignore `?project=`, ouvrir la mémoire d'un autre
  projet affichait celle du projet servi sous son nom.

- **Le projet non initialisé a enfin un bouton.** L'action `grimoire up`
  n'apparaissait que sur les projets *en retard* — jamais sur ceux qui n'ont
  aucune couche Grimoire, c'est-à-dire ceux qui en ont le plus besoin.

- **L'étiquette d'alignement montre la version installée.** « KIT 3.32.0 ·
  ALIGNÉ » sous un kit 3.34.2 se lisait comme un retard alors que c'était
  l'inverse.

- **Travailler dans l'atelier compte comme une activité.** Les mutations
  servies sont journalisées dans un fichier que la télémétrie du runtime ne
  référence pas : un projet qu'on était en train de manipuler affichait
  « aucune trace ».

- **Le portefeuille n'invente plus sa flotte.** `portfolio.html`, page d'accueil
  du cockpit local, portait un repli codé en dur de quatre projets — « Grimoire
  Core », « Atlas Ops », « Sentinel Sec », « Ledger Data » — affiché dès que
  `data/projects.json` manquait, c'est-à-dire précisément sur un cockpit dont le
  registre est vide. Le repli est supprimé au profit d'un état vide qui donne la
  commande d'enrôlement.

- **Les cartes du portefeuille mènent quelque part.** Sur un cockpit local,
  « Observabilité » et « Mémoire » ouvrent la page du projet
  (`?project=<slug>`) au lieu d'un panneau expliquant comment lancer l'atelier —
  comportement qui n'a de sens que sur la vitrine publique. Le chemin du projet
  sur le disque est affiché sur la carte.

- **Le cockpit n'amorce plus sa couche de données avec la démo bundlée.** Un
  registre vide donne un cockpit vide, et la commande pour le remplir. Sur un
  poste qui avait déjà lancé le cockpit, la démo semée par une version
  antérieure est **purgée** : ne plus amorcer ne suffisait pas, les projets
  inventés étaient déjà sur le disque. Le critère est l'octet près — une couche
  produite par `cockpit refresh` diffère forcément du bundle et n'est jamais
  supprimée.

- **Deux projets homonymes ne partagent plus leur cache de données.** Un projet
  hors registre n'avait que son nom de dossier pour clé : deux dépôts nommés
  `web` se marchaient dessus, et le second affichait les chiffres du premier.
  La clé porte désormais une empreinte du chemin.

- **`grimoire serve` refuse un `Host` étranger sur les lectures aussi.** Le
  garde anti-rebinding DNS ne couvrait que les mutations. Le rebinding rend la
  page attaquante same-origin, donc CORS ne protège plus la lecture des
  réponses : le nouveau `GET /api/fs/browse` serait devenu un oracle sur les
  dossiers de la machine. Le garde s'applique maintenant à toute requête.

- **La découverte est bornée à des racines permises.** Un chemin venu d'une
  requête HTTP pouvait désigner n'importe quel dossier du système — signalé par
  CodeQL comme `py/path-injection` sur les trois entrées. Le garde d'hôte
  empêche une page tierce d'appeler ces routes, mais ce n'est pas une raison de
  laisser la surface illimitée. Sont autorisés : le répertoire personnel, le
  projet servi et son voisinage, et le dossier parent de chaque projet enrôlé —
  de quoi ouvrir un dépôt hors de `$HOME` et scanner ses voisins dès le premier
  usage. Pour une racine entièrement nouvelle, on passe par
  `grimoire cockpit add`, qui n'est pas exposé au réseau. La résolution précède
  la comparaison, sinon `~/../../etc` passerait.

- **Le cockpit refuse aussi un `Host` étranger.** Il ne vérifiait que l'adresse
  du pair : sous rebinding DNS la requête arrive bien depuis la loopback, donc
  ce contrôle passe et la page attaquante est same-origin. Les routes de
  découverte ajoutées à cet hôte — dont `GET /api/fs/browse` — devenaient un
  oracle sur les dossiers de la machine. Trouvé par CodeQL ; l'atelier, lui,
  était déjà fermé, et un test verrouille désormais l'absence de divergence.

- **La profondeur de scan est plafonnée** (8) : elle vient d'une requête HTTP,
  et un scan de `/` sans limite immobilisait un thread du serveur.

- **Le cache de l'atelier sort de la racine web du cockpit.** Il vit sous
  `~/.grimoire/cockpit/atelier/<slug>/data/` et non plus sous `serve/`, que
  `grimoire cockpit serve` publie tel quel : la couche générée de chaque projet
  de la machine y aurait été exposée à `/<slug>/data/…`, et un projet dont le
  slug est `data` aurait écrit dans le dossier de données du cockpit lui-même.


## [3.35.4] - 2026-08-30

### Corrigé

- **C'est le marqueur qui dit ce que le kit a écrit, plus une liste de
  répertoires.** La version précédente restreignait la vérification à une liste
  de sous-arbres hôtes ; mais un projet peut poser sa propre compétence à côté
  de celles du kit, dans le même répertoire, et elle était alors lue comme une
  livraison. Les émetteurs hôtes marquent déjà chaque fichier qu'ils
  régénèrent : le marqueur répond à la bonne question — qui a écrit *ce*
  fichier — là où une liste répond à qui écrit d'habitude dans *ce* dossier, et
  se périme. Seuls `.github/prompts/` et `.github/instructions/`, que le
  scaffolder remplit sans marquer, restent nommés.
  Mesuré : une installation saine ne rapporte plus rien, une installation
  3.34.2 défectueuse rend toujours ses 56 chemins morts et ses neuf agents
  fantômes, et un atelier réel passe de 3 signalements à 2 — les deux résidus
  d'une installation antérieure qu'il lui restait à supprimer.

## [3.35.3] - 2026-08-30

### Corrigé

- **La vérification d'intégrité lit ce que le kit a livré, plus tout le
  projet.** Elle parcourait l'arbre entier et lisait le travail propre du
  projet comme s'il venait du kit : un audit daté qui *rapporte* un chemin
  cassé, une ligne de journal générée qui en cite un, un hook écrit à la main.
  Trois versions de suite ont retiré une de ces sources de bruit — dépôts
  imbriqués, archives, désormais artefacts du projet ; c'étaient trois
  symptômes d'un seul parcours trop large.
  Ce que le kit livre est connaissable, pas devinable : les arbres qu'il
  régénère (`_grimoire/kit/`, `_grimoire/overrides/`, `_grimoire/_memory/`, et
  les sous-arbres hôtes que ses propres conventions lui attribuent), plus les
  fichiers portant le marqueur `grimoire:managed` dans les répertoires qu'il
  partage avec le projet. `.github/hooks/` et `.github/workflows/` restent au
  projet. Mesuré sur un atelier : 21 signalements deviennent 3, tous réels,
  sans rien perdre sur une installation défectueuse (56 chemins morts
  toujours détectés sur une 3.34.2, et les neuf agents fantômes).

- **Un chemin situé dans un autre arbre n'est plus compté comme manquant
  ici.** `grimoire-kit/_grimoire/kit/x` désigne un fichier d'un dépôt voisin ;
  la recherche en attrapait la fin et le déclarait absent du projet courant.
  L'ancrage laisse évidemment passer `{project-root}/_grimoire/…`, la forme
  qu'emploient presque toutes les personas.

## [3.35.2] - 2026-08-30

### Corrigé

- **La vérification d'intégrité honore la convention d'archive.** Un agent
  retiré et parqué sous `_archived/` conservait sa carte de routage d'époque ;
  la vérification la lisait comme une carte vivante et ressuscitait les agents
  que le projet avait délibérément retirés. La convention est déjà connue du
  code de migration du kit — un fichier archivé est un fichier que le projet a
  retiré, pas un fichier que le kit a livré. Une archive est un compte rendu de
  ce qui fut vrai, pas une promesse.

## [3.35.1] - 2026-08-30

### Corrigé

- **La vérification d'intégrité ignore les dépôts imbriqués.** Les deux checks
  introduits en 3.35.0 parcouraient tout l'arbre du projet, clones vendorisés,
  sous-modules et worktrees compris : un projet hébergeant un dépôt se voyait
  reprocher les chemins de ce dépôt comme s'il les avait installés lui-même.
  Constaté immédiatement à la mise à jour d'un atelier hébergeant un clone du
  kit : 395 références mortes annoncées, dont 345 venaient du clone — non
  réparables depuis `grimoire doctor`, et noyant les 50 qui étaient vraies.
  Un répertoire portant son propre `.git` répond à son arbre, pas à celui du
  projet englobant.

## [3.35.0] - 2026-08-30

### Corrigé

- **Les chemins que le kit écrit dans un projet s'y résolvent.** Le passage aux
  trois étages (`_grimoire/kit/`, `_grimoire/overrides/`) avait migré le code et
  laissé le contenu livré derrière : une installation neuve recevait 99
  références de chemin mortes, dont 23 vers `_grimoire/_config/`, le layout
  pré-frontière que `core/layout.py` déclare dépassé. `agent-base.md` — le
  fichier que tout agent charge en premier — en portait trois, dont le
  Completion Contract, donc inatteignable pour qui suit le socle à la lettre.
  La couche mémoire avait la même dérive : huit appels cherchaient
  `maintenance.py` sous `_grimoire/_memory/` alors qu'il est déployé sous
  `_grimoire/kit/memory/`. Les dix protocoles que le socle dit de charger à la
  demande — vérification croisée, incertitude honnête, remontée des questions,
  réseau d'agents — n'étaient déployés nulle part ; ils le sont. Les journaux
  qu'un agent reçoit l'ordre de charger existent à l'installation, fût-ce
  vides : `dependency-graph.md` était cité ligne 33 de huit personas, leur
  étape d'activation, sans qu'aucun gabarit ne le livre.

- **`grimoire hooks install` génère des hooks qui s'exécutent.** Le
  `.pre-commit-config.yaml` produit pointait vers `framework/hooks/*.sh`, un
  chemin que seul un clone du dépôt du kit possède : les quatre hooks Grimoire
  échouaient au premier `pre-commit run`, dans tout projet. Les scripts sont
  désormais miroités dans `_grimoire/kit/hooks/`, où la configuration et la
  chaîne Mnemo les trouvent. Au passage, `_framework_hooks_dir()` résolvait les
  données du paquet à la main et ratait l'installation editable ; elle passe
  par `framework_path()`, qui gère les deux cas.

- **Les outils qu'une persona appelle sont livrés.** `failure-museum.py`
  existait dans le kit sans jamais sortir de la wheel, alors que le concierge
  l'invoque dans son triage. Sont désormais déployés les outils qu'un contenu
  livré appelle vraiment — cinq universels, trois de plus avec
  `creative-studio` — et inventoriés dans un `tool-manifest.csv` généré comme
  l'est déjà `agent-manifest.csv`. Les quarante que rien ne nomme restent hors
  contrat. `mem0-bridge.py`, cité quinze fois dont depuis le socle et déployé
  nulle part, cède la place au CLI `grimoire memory`, qui couvre chacune de ses
  commandes — étape 2 de la transition prévue par l'ADR-003.

- **La carte de routage du point d'entrée décrit ce projet.** Le concierge
  livrait une liste d'agents écrite en dur, héritée d'une autre famille
  d'archétypes : sur un projet d'infrastructure, neuf de ses onze agents
  n'existaient pas, et dix-sept des dix-neuf réellement installés n'y
  figuraient pas. Ce n'était pas une substitution manquée — le fichier ne
  contenait aucun placeholder, et rien ne savait produire cette carte. Elle est
  générée depuis les agents effectivement déployés. Un agent absent de la carte
  n'est pas installé, et réciproquement.

- **Les journaux de mémoire portent le nom du projet.** Deux conventions
  restaient brutes pour deux raisons distinctes : `{{project_name}}` et
  `{{init_date}}` parce que le répertoire mémoire n'était pas dans le périmètre
  de rendu et qu'`init_date` n'existait dans aucune table de variables ;
  `$project_name` parce que le journal de décisions était écrit sans passer par
  le moteur de gabarit, alors que la valeur par défaut du contexte partagé,
  huit lignes plus haut dans le même fichier, y passe. Chaque projet recevait
  un musée des échecs intitulé `{{project_name}}`.

- **La légende qui documente les placeholders ne se substitue plus
  elle-même.** Le rendu remplaçait dans tout le fichier, y compris le bloc de
  commentaire qui *explique* les marqueurs : la légende installée annonçait
  « Stack — Nom de l'agent développement (ex: Amelia) ». Les légendes se
  marquent `<!-- grimoire:legend` et traversent le rendu intactes.

- **Les modèles de run ne sont plus rangés parmi les workflows.**
  `workflow-graph.tpl.yaml` et `workflow-status.tpl.md` sont des formes à
  copier dans un répertoire d'exécution — leur en-tête le dit. Le retrait du
  suffixe `.tpl` leur ôtait ce signal et les installait à côté des vrais
  workflows, si bien que leur distribution d'illustration (`dev/Amelia`,
  `qa/Quinn`) se lisait comme la table de routage du projet. Ils vont dans
  `workflows/examples/`.

- **`standard fix --apply` n'annonce plus manquant ce qu'il vient d'écrire.**
  Le reste à faire était calculé avant les écritures et imprimé après : un
  succès se lisait comme un échec.

- **La politique de garde juge l'action, plus la donnée qu'elle transporte.**
  Les motifs destructifs étaient cherchés dans toute la chaîne de commande :
  écrire un runbook par heredoc, rédiger un message de commit citant une
  commande interdite ou créer une fixture de test suffisait à déclencher un
  refus, alors que rien n'était exécuté — c'est-à-dire précisément le travail
  attendu sur un archétype `infra-ops`. Les corps de heredoc et les chaînes
  entre quotes sortent de l'inspection, sauf quand un shell est sur le point
  de les lancer : ce qui suit `bash -c`, `eval`, `xargs`, `su -c`, `ssh` ou
  `timeout` reste examiné, sans quoi le correctif ouvrait un contournement
  d'une ligne.

- **L'archive publiée par `publish_extension` ne dépend plus de l'heure qu'il
  est.** La docstring promettait une archive déterministe et seuls les membres
  du tar étaient normalisés ; `tarfile.open(..., "w:gz")` grave l'heure
  courante dans l'en-tête gzip (champ MTIME, RFC 1952). Republier une extension
  inchangée produisait deux sommes de contrôle dès que les deux publications
  tombaient de part et d'autre d'une frontière de seconde. Le test associé ne
  pouvait donc échouer que par malchance d'horloge, et rougissait au hasard en
  intégration continue.

- **`docs/sdk-guide.md` documente l'API qui existe.** L'exemple de résolution de
  chemins montrait quatre propriétés de `PathResolver` — `grimoire_dir`,
  `config_dir`, `memory_dir`, `agents_dir` — dont aucune n'a jamais existé.

- **Le catalogue de workflows ne montrait que la moitié de ce que le kit
  installe.** `grimoire workflows list` n'indexait que `.github/prompts/` :
  sept prompts d'hygiène, qui doublent pour la plupart une commande CLI. Les
  six workflows d'orchestration multi-agents — boomerang, subagent,
  party-mode, incident-response, state-checkpoint, repo-map-generator — sont
  déposés par le scaffold dans le tier kit et n'étaient listés par aucune
  surface : installés dans chaque projet, invocables depuis nulle part. Le
  catalogue indexe désormais les deux familles, avec la nature de chaque
  workflow, les agents qu'il mobilise et sa provenance ; `--kind` filtre.
- **Les manifestes d'équipe avaient un schéma, trois fichiers et aucun
  lecteur.** `framework/teams/` décrit la chaîne vision → build → ops :
  membres et rôles, contrats d'entrée et de sortie, phases de livraison,
  outils autorisés, condition de handoff. Rien dans le SDK ne les chargeait.
  Ils sont désormais installés dans le projet, résolus par tier, affichés par
  `grimoire workflows teams`, et rendus par `workflows show` quand un workflow
  déclare `team:`.
- **`workflows show` et `workflows install` ne pouvaient pas atteindre une
  orchestration.** Les deux résolvaient en `<slug>.prompt.md`, un nom que les
  workflows d'orchestration ne portent pas. `install` résout désormais dans le
  cadre livré, sans la précédence de lecture qui lui rendait la copie du
  projet, et dépose chaque workflow là où sa nature l'exige.
- **`host sync` ne remplace plus en silence un hook écrit à la main.**
  L'émetteur Copilot posait `managed=False` sur ses fichiers de hook, ce qui
  désactivait entièrement le contrôle de préservation : le drapeau confondait
  « ne peut pas porter de marqueur de gestion » — un JSON n'a pas de
  commentaires — avec « peut être écrasé sans prévenir ». Un projet ayant sa
  propre chaîne de gouvernance la perdait au premier sync, sans message, sans
  sauvegarde, et le dry-run l'annonçait comme un `[OK]` ordinaire.
  Un fichier qui ne peut pas porter de marqueur prouve désormais son
  appartenance autrement : par la commande qu'il invoque. Le sync réécrit les
  hooks qui appellent `grimoire-hook`, préserve les autres et les signale
  `[!]`, comme il le faisait déjà pour un agent écrit à la main. La liste des
  commandes reconnues, jusque-là propre à l'émetteur Claude Code, devient
  partagée — la même question ne doit pas recevoir deux réponses.

- **La porte de preuve refuse une tâche que le board ne connaît pas.**
  `grimoire standard gate check --task-id <inconnu>` répondait `ok`. Toutes les
  exigences de `check_evidence_gates` sont indexées sur des états nommés ; une
  tâche absente du board a un état vide, donc aucune exigence n'était évaluée
  et le verdict était favorable. Un identifiant mal orthographié dans un hook
  ou un job de CI rendait la porte décorative. Deux cas voisins sont
  préservés, chacun avec son test : une tâche `proposed` réellement inscrite
  ne doit toujours aucun artefact, et un projet sans board n'est pas gouverné
  au niveau tâche.
  **Changement de comportement** : une CI qui appelle `gate check` avec un
  identifiant absent du board passait au vert, elle passe désormais au rouge.
  C'est l'objet du correctif, mais la bascule est visible sans que rien n'ait
  changé côté appelant.
- **La page publique cesse d'affirmer ce que le dépôt ne mesure pas.** La
  section « Preuves » affichait six métriques et un témoignage que rien
  n'adosse, alors que le protocole d'évals du même dépôt interdit tout claim.
  Remplacées par des mesures reprises des rapports committés et de la CI, et
  le témoignage laisse place au verdict pré-enregistré — « effet non
  démontré ». Deux cartes annonçaient encore `UDF` et `AORA`, retirés le
  2026-07-12 pour usage nul ; elles décrivent désormais des capacités qui
  existent. Deux chiffres périmés recomptés : 164 commandes CLI, 5517 tests.
- **`from grimoire.cli import *` et `from grimoire.mcp import *` ne lèvent
  plus.** Les deux paquets déclaraient un `__all__` sans importer ce qu'ils
  annonçaient. La résolution est désormais paresseuse : les noms existent sans
  que l'import du paquet charge Typer et Rich. `app` n'est volontairement pas
  réexporté — le sous-module `grimoire.cli.app` et l'instance Typer portent le
  même nom, donc la valeur dépendrait de l'ordre des imports ; l'instance
  reste atteignable par `from grimoire.cli.app import app`.
- **Le job CI « Python Unit Tests » ne pouvait pas échouer.** Son code de
  sortie était celui de `tee`, la valeur réelle partait dans une sortie que
  rien ne consommait, et l'étape suivante pouvait écrire « Some tests failed »
  pendant que le workflow restait vert. Ses tests étant couverts par le SDK CI
  et par *Framework Tools Tests*, le job est supprimé plutôt que réparé.
- **L'index lexical compagnon est créé pour tout backend vectoriel.** Il exigeait
  `retrieval_mode == "vector"` au caractère près, ce qui excluait silencieusement
  tous les autres modes déclarés — dont celui dont la fusion est l'unique raison
  d'être. Seul `none` s'en exclut désormais.
- **Un store détecté reçoit ses réglages de connexion quelle que soit la
  composition.** Un projet généré sur une machine faisant tourner Weaviate
  sortait sans `weaviate_url` dès que la composition n'était pas celle des
  graphes, et `grimoire doctor` le signalait à chaque exécution.
- **Le serveur MCP lit et écrit dans le projet visé.** `grimoire_memory_store`
  et `grimoire_memory_search` construisaient leur `MemoryManager` sans
  `project_root`, donc avec un repli sur le répertoire courant du serveur.

- **`POST /api/projects/update` traite le projet demandé, pas celui qui est
  servi.** L'atelier ignorait la cible : le portefeuille liste tous les projets
  de la machine et il est servi par les deux hôtes, donc cliquer « mettre à
  jour » sur un projet lançait `grimoire up` dans le dépôt servi — avec
  confirmation, cela écrivait dans le mauvais dépôt. Une cible inconnue est
  désormais refusée plutôt que repliée silencieusement.
### Ajouté

- **`grimoire doctor` vérifie que ce qu'il installe tient debout.** Il
  contrôlait que ses répertoires existaient, jamais que les chemins écrits dans
  les fichiers qu'il venait d'installer menaient quelque part, ni que les agents
  vers lesquels ils routent étaient installés : un projet passait 20/20 en
  portant 99 chemins morts et une carte de routage dont neuf agents sur onze
  n'existaient pas. Deux vérifications comblent l'angle mort — résolution des
  chemins d'entrée livrés, cohérence de la carte de routage avec le manifeste.
  Les deux se taisent sur un projet sans étage `_grimoire/kit/` : un arbre fait
  main ou d'avant la frontière n'a rien fait livrer par le kit, et un contrôle
  qu'on ignore est pire que pas de contrôle.
- **Deux plans cibles** : `docs/flow-engine-target-plan.md` — brancher le
  noyau d'exécution qui existe mais n'est appelé par rien — et
  `docs/product-quality-target-plan.md`, qui le situe parmi les douze
  dimensions du produit et dit où sont les points.
- **Une règle opposable et ses gardes** : ce qui décrit un artefact doit être
  dérivé de cet artefact, ou testé contre lui. Trois tests l'appliquent —
  `test_docs_derivation.py` refuse un README d'évals qui nie ou omet une
  campagne publiée, `test_cli_reference_drift.py` refuse une commande exposée
  et absente de la référence, `test_public_exports.py` refuse un `__all__` qui
  promet des objets absents. Le deuxième a trouvé neuf commandes livrées et
  documentées nulle part.
- **La mémoire se choisit comme une composition, plus comme un backend.**
  `project-context.yaml` décrivait déjà sept couches indépendantes (mémoire
  courte, sémantique, sidecar structuré, graphes de connaissances, de
  souvenirs, de code, de tâches), mais le setup ne posait qu'une question —
  quel backend ? — et n'écrivait que deux blocs codés en dur. Deux
  compositions sur toutes les possibles étaient donc atteignables, et tous les
  autres projets héritaient des mêmes défauts. Quatre profils déclaratifs
  (`lexical`, `standard`, `graphe`, `complet`) fixent les sept couches d'un
  coup ; `grimoire init --memory-profile` et le wizard les exposent. Le wizard
  ne propose que les compositions que la machine peut réellement servir et
  affiche ce qui manque aux autres. Le profil `weaviate-neo4j` des versions
  antérieures reste reconnu — il désigne `graphe`.

- Le projet servi est enrôlé au registre à l'ouverture s'il porte un marqueur
  de projet. Ouvrir un projet n'écrit rien dans son arbre : registre et couche
  de données vivent sous `~/.grimoire/`.

### Modifié

- Le registre de projets et la découverte quittent `grimoire.cli.cmd_cockpit`
  pour `grimoire.tools.project_registry` : le cockpit et l'atelier lisent et
  écrivent le même fichier, et deux copies de cette logique auraient divergé.

## [3.34.2] - 2026-08-28

### Corrigé

- **Le kit ne meurt plus sur une console qui ne parle pas UTF-8.** Il imprime
  des filets (`─`), des flèches (`→`) et des marqueurs d'état dans presque
  toutes ses sorties ; sur une console Windows par défaut — cp1252 — `print`
  levait `UnicodeEncodeError` et la commande échouait, y compris sur une simple
  lecture. Ce n'était pas un défaut de l'ère shell : **Rich échoue de la même
  façon** dès que le texte à afficher, et non sa propre bordure, porte ces
  caractères. Les trois points d'entrée (`grimoire`, `grimoire-mcp`,
  `grimoire-hook`) reconfigurent désormais la sortie en UTF-8 tolérant, et
  posent `PYTHONIOENCODING` pour les sous-processus. Un caractère non
  représentable devient `?` au lieu d'interrompre la commande.


## [3.34.1] - 2026-08-28

### Corrigé

- **Le catalogue de contenu livré ne recense plus du bytecode.**
  `gen-kit-hashes.py` parcourait le disque : un `__pycache__` laissé par une
  exécution de tests suffisait à y injecter des digests de `.pyc`, propres à une
  version de Python. 304 entrées de ce type traînaient dans le catalogue, dont
  249 depuis la 3.32.0. Le scan lit désormais `git ls-files`, comme le scan d'un
  tag lisait déjà `git ls-tree` — les deux vues répondent enfin à la même
  question — et les entrées parasites sont retirées.
- **Le job CI *Framework Tools Tests* ne se déclarait vert qu'en apparence.** Il
  neutralisait le code de sortie de pytest (`|| true`) puis devinait le résultat
  par `grep` sur la dernière ligne. Il échouait en réalité à la collecte depuis
  un moment — `typer` et `ruamel.yaml` absents de ses dépendances — et personne
  ne le voyait. Il tourne désormais sur `ubuntu-latest` et `windows-latest`, et
  c'est le code de sortie qui décide.

### Modifié

- **Les vérificateurs du standard vivent dans `grimoire.core.standard_checks`**
  (`base`, `controls`, `verifiers`), extraits de `agentic_standard.py` qui perd
  1272 lignes. Aucun changement de comportement : la surface publique du module
  est inchangée à quatre constantes près, internes au moteur de vérification.

## [3.34.0] - 2026-08-28

### Ajouté

- **Surfaces hôtes (`grimoire host`)** — un projet ne se décrivait aux hôtes
  qu'en prose : `CLAUDE.md` pointait vers `.github/copilot-instructions.md`, et
  tout ce qu'un hôte sait exécuter — sous-agents à contexte propre, compétences
  chargées à la demande, commandes utilisateur, hooks capables de refuser,
  permissions déclaratives — restait inutilisé. Le kit construit désormais une
  **description host-neutre** du projet (`grimoire.hosts.surface`) et la rend
  par un émetteur dédié : `.claude/{agents,skills,commands}` et `settings.json`
  pour Claude Code, `.github/{agents,skills,prompts,hooks}` pour Copilot, un
  catalogue explicite pour Codex, Cursor et Gemini CLI. Commandes :
  `grimoire host list | surface | sync | status | run`. La synchronisation est
  déclenchée automatiquement par `grimoire init`, `grimoire up --fix` et
  `grimoire standard init`. Voir `docs/hosts.md`.

- **Gouvernance opposable, identique sur tous les hôtes** — les règles vivent
  dans un module de décisions host-neutre (`grimoire.hosts.decisions`), traduit
  dans le JSON de chaque hôte par `grimoire.hosts.runtime`. Un refus sous
  Copilot est le même texte que sous Claude Code, parce que c'est la même
  décision. Le hook `stop` refuse la clôture d'une tâche gouvernée dont les
  gates de preuve sont rouges — la consigne « une clôture sans gates verts est
  une tâche non terminée » devient une contrainte. Trois garde-fous : pas de
  blocage répété (`stop_hook_active`), pas de blocage sur un projet non enrôlé
  ou un profil non gouverné, pas de panne fatale (un projet cassé sort en
  « autorisé » avec l'erreur en contexte).

- **`PolicyEngine` branché en production** — le moteur de politique et ses
  règles OWASP n'étaient instanciés que par les fixtures d'évals. Le hook
  `pre_tool_use` lui soumet désormais chaque appel mutant, en lisant le
  vocabulaire d'outils de n'importe quel hôte (`Bash` comme `run_in_terminal`,
  `Edit` comme `replace_string_in_file`). Suppressions récursives, force push,
  destructions d'infrastructure et lectures de fichiers de secrets sont
  refusées ou soumises à confirmation selon le profil de risque.

- **Frontière d'outils par persona** — le champ `tools:` du frontmatter d'un
  agent fixe sa frontière ; sans lui, elle est déduite du texte de la persona
  (lecture et recherche toujours, écriture et exécution sur signal explicite).
  `grimoire host status` liste les personas dont la frontière est déduite.

- **Compétences et commandes livrées** — protocole de preuve, dispatch de
  persona et mémoire projet comme compétences chargées à la demande ; six
  commandes (`grimoire-status`, `-gate`, `-proof`, `-verify`, `-recall`,
  `-doctor`) rendues comme commandes natives sur les hôtes qui en ont.

- **Outils MCP `grimoire_host_status`, `grimoire_skill`, `grimoire_command`** —
  un client MCP sans émetteur dédié atteint la même surface, compétences et
  commandes chargeables à la demande comprises.

- **Hôtes Cursor et Gemini CLI** au registre de capacités, avec leurs manifestes.

- **Frontière kit/overrides** — un projet Grimoire sépare désormais ce que le kit
  génère (`_grimoire/kit/`, régénéré à chaque mise à jour) de ce que le projet
  possède (`_grimoire/overrides/`, jamais écrasé, prioritaire à la résolution).
  Une customisation se fait en déposant un fichier dans `overrides/` — elle
  survit à toutes les mises à jour par construction, sans plus dépendre d'une
  heuristique.

- **`grimoire migrate`** — opération unique qui fait passer un projet existant
  sur cette frontière : le contenu que le kit a déjà livré (reconnu par
  empreinte dans `registry/kit-file-hashes.json`) est régénéré, tout le reste
  part dans `overrides/`. Snapshot systématique, `--restore <horodatage>` pour
  revenir en arrière, `--adopt-kit` pour reprendre la version du kit sur les
  fichiers qui la masquaient sans raison. Après migration, `grimoire up` suffit.

- **Manifeste de génération du standard** (`_grimoire/standard/.generated.json`)
  — enregistre l'empreinte de chaque artefact écrit par le kit, ce qui permet de
  distinguer « policy que le kit a générée » de « décision que le projet a
  prise ». Les premières suivent les mises à jour, les secondes ne sont jamais
  touchées.

- **`grimoire memory shared`** — mémoire transverse entre projets, pour qu'un
  agent spécialiste accumule du savoir réutilisable sans corrompre celui des
  autres. Trois règles traitent les modes de corruption connus :
  **la frontière est physique** (un store séparé, pas une collection filtrée
  par métadonnée — un filtre oublié mélange deux projets sans rien signaler),
  **la promotion est refusée par défaut** (un souvenir ne monte que s'il reste
  vrai quand on efface le nom du projet : « l'app X utilise Postgres 16 » est
  un fait de projet, « les migrations Alembic cassent quand deux heads
  coexistent » est un motif), et **la confiance décroît** (un motif non
  revérifié est servi comme hypothèse, calcul fait à la lecture — une
  décroissance qui dépend d'un ordonnanceur est une décroissance qui n'arrive
  pas). `promote` écrit avec provenance, `confirm` restaure la fraîcheur,
  `recall` restitue en deux passes étiquetées, jamais fusionnées : un motif
  appris ailleurs ne doit pas être présenté avec l'assurance d'un fait vérifié
  ici. Opt-in via `memory.shared_collection`, vide par défaut.

- **`grimoire memory up`** — met en place la stack mémoire complète, que
  `grimoire init` laissait à moitié câblée : il détecte un backend vectoriel et
  écrit `memory.backend`, mais `neo4j_uri`, `knowledge_graph`, `memory_graph`,
  `code_graph`, `task_memory` et `redis_url` restaient commentés dans
  `project-context.tpl.yaml` sans que rien ne les décommente. Trois profils
  (`lexical`, `vector`, `full`). Règle centrale : **on n'active que ce qui
  répond** — écrire `memory_graph: neo4j` alors que Neo4j est éteint produirait
  une config qui échoue en silence au runtime, donc un service injoignable est
  signalé avec sa commande de démarrage, pas activé. La comparaison porte sur ce
  qui est écrit dans le fichier et non sur les valeurs par défaut : sinon
  `neo4j_password_env` ne serait jamais écrit et rien n'indiquerait quelle
  variable exporter. Plan par défaut, écriture sur `--apply`, idempotent,
  commentaires du YAML préservés.

- **`grimoire memory bundle`** — transport d'un modèle d'embedding vers un site
  sans accès sortant. `export` construit une archive depuis un repo Hub ou un
  répertoire local, `install` refuse toute archive dont un fichier ne correspond
  pas au SHA-256 déclaré au manifeste, `verify` recontrôle les empreintes puis
  charge le modèle avec les sockets sortantes bloquées — un moteur qui retombe
  sur un téléchargement distant échoue au lieu de réussir. `install --configure`
  renseigne `memory.embedding_model` dans `project-context.yaml` en préservant
  les commentaires. Grimoire ne redistribue aucun poids : l'archive est produite
  par l'opérateur depuis la source de son choix. Voir `docs/memory-system.md`.

- **Sondes Weaviate, Neo4j et Redis dans `grimoire doctor`** — la commande ne
  vérifiait que Qdrant et Ollama, donc la stack cible du Memory OS était
  invisible du diagnostic. Les sondes ne parlent que si le projet route
  réellement la couche, pour qu'un projet en `local` ne récolte pas trois
  avertissements pour des services qu'il n'utilise pas. La sonde Neo4j signale
  le cas où la socket répond alors que la variable de mot de passe est absente :
  chaque écriture de graphe échouerait alors silencieusement à
  l'authentification.

- **Bloc `parity` dans `grimoire memory status`** — compare les entrées du store
  aux nœuds mémoire Neo4j et à leurs références `WeaviateObject`. C'est le
  signal qui détecte un objet écrit d'un côté sans contrepartie de l'autre.
  Trois `COUNT`, assez léger pour une commande de statut, là où
  `memory graph verify` reconstruit tout le code graph.

- **`grimoire cockpit prune`** — retire du registre les projets dont le chemin a
  disparu. Le registre accumulait une entrée par projet enrôlé sans jamais en
  retirer : chaque projet supprimé ou déplacé y laissait un pointeur mort, et
  rien n'offrait de les nettoyer autrement qu'un par un via `cockpit remove`.
  Sur un poste de développement, 6 327 entrées mortes sur 6 460. Prudent par
  défaut — un répertoire encore présent a pu être enrôlé délibérément, donc
  seule l'absence du chemin justifie un retrait ; `--stale` élargit aux chemins
  présents mais sans marqueur Grimoire. `--dry-run` montre le plan, la purge
  demande confirmation sauf `--yes`.

- **Prompts et resources MCP — la surface qui ne demande aucun émetteur** — le
  protocole MCP a trois primitives, le serveur du kit en exposait une : quinze
  outils, zéro prompt, zéro resource. Or MCP est la seule surface que *tous* les
  hôtes partagent. Les six commandes deviennent des **prompts MCP**, donc des
  slash commands dans n'importe quel client — Claude Code, Copilot, Cursor,
  Codex, Gemini CLI, Zed, Continue — et les trois compétences deviennent des
  **resources** chargeables à la demande sous `grimoire://skill/<slug>`.
  Codex, Cursor et Gemini CLI cessent ainsi de recevoir un catalogue en prose là
  où une commande réelle était disponible sans écrire une ligne d'émetteur.
  La façade du serveur n'a été élargie qu'après vérification **fonctionnelle**
  sur mcp 1.29.1 et mcp 2.x — enregistrement dynamique, listing, `get_prompt`,
  arguments déclarés — et non sur un simple `hasattr` : c'est la discipline qui
  avait déjà évité une borne de version erronée. L'enregistrement ne peut jamais
  empêcher le serveur de démarrer.

- **La gouvernance laisse enfin une trace** — le ledger et les hooks de cycle de
  vie avaient été conçus l'un pour l'autre sans jamais être reliés :
  `ToolCallTrace` porte un `policy_verdict_id`, et `policy_block_rate()` se
  documente comme « fraction of tool calls that were blocked » — un nombre qui
  ne pouvait que valoir zéro tant que les hooks n'écrivaient rien. Chaque appel
  d'outil réellement évalué et chaque décision de clôture sont désormais
  consignés dans `_grimoire-output/traces/`. Mesuré sur un projet gouverné :
  `policy_block_rate` passe de 0.0 structurel à 0.5 réel.
  Trois propriétés le rendent sûr à garder : le chemin en lecture seule n'écrit
  rien (il sort avant toute évaluation), les arguments sont **hachés** et jamais
  stockés tels quels — le ledger part sur disque et s'exporte en OTel —, et un
  ledger impossible à écrire ne fait pas échouer la session.

- **`grimoire task board export`** — le task board gouverné est désormais une
  projection du Mission Ledger, régénérée depuis lui (ADR-005). La carte porte
  enfin ce que le YAML ignorait : description, garde-fous, preuves attendues,
  propriétaire réel (à défaut, le porteur du claim), priorité dérivée du profil
  de risque, et un motif sur toute carte bloquée.

- **ADR-005** — « Le Mission Ledger est la source, le task board une
  projection ». Tranche la coexistence de deux modèles de tâches concurrents :
  neuf états côté ledger, huit côté board, aucune conversion nulle part.

### Modifié

- **`grimoire standard verify` ne laisse plus supprimer un artefact activé par un
  besoin.** La vérification recalculait l'ensemble requis depuis le seul profil,
  alors que `setup_standard_profile` avait déjà persisté la liste complète —
  extras compris — dans `_grimoire/standard/standard-profile.yaml`. Après
  `standard init --needs solo-prototyping`, supprimer
  `_grimoire/standard/evidence-gates.yaml` laissait `ok=true, missing=[]`. La
  liste enregistrée fait désormais foi. **Rupture assumée** : un projet dont les
  artefacts d'extras ont disparu passe de vert à rouge sans qu'une ligne de son
  code ait bougé — c'est précisément ce que le contrôle doit signaler. La remise
  en conformité se fait par `grimoire up`, qui régénère le tier kit en préservant
  waivers, scores et task board.

- **`grimoire standard gate check --strict` sort en 2 sur les cinq profils.**
  L'escalade était réservée à `governed` et `production` : un projet `starter`,
  `controlled` ou `orchestrated` ne pouvait pas casser un pipeline, quel que soit
  le nombre de preuves manquantes.

- **Les sorties JSON du standard portent un schéma versionné.** `verify`, `audit`,
  `score` et `gate check` émettent une clé `schema`
  (`grimoire.standard-<verbe>/v1`) et leur ensemble de clés de premier niveau est
  verrouillé par un test : une CI tierce qui les parse dispose enfin d'un contrat,
  là où le schéma pouvait changer en correctif.

- Les blocs `paths:` de `ci-sdk.yml` incluent `framework/agentic-standard/**` :
  une PR qui ne modifie que du YAML du standard déclenchait `agentic-standard.yml`
  et `ci-validate.yml`, mais pas les tests de `tests/test_agentic_standard.py`.

- **`grimoire up` met réellement à jour un projet existant.** Auparavant il
  s'arrêtait dès que `project-context.yaml` existait : agents, framework,
  workflows, prompts et instructions restaient gelés à la version d'installation
  pour toute la vie du projet. Il régénère maintenant le tier kit et rafraîchit
  les artefacts standard non modifiés. L'écriture est différentielle : sans
  nouvelle version, rien n'est réécrit et rien n'est rapporté.

- Les prompts, fichiers d'instructions, passerelles d'assistants et wrappers
  d'agents ne sont plus protégés par un `if fichier existe : ne rien faire` —
  ils appartiennent au kit et suivent sa version. `.mcp.json`, le contexte
  projet et les journaux mémoire restent, eux, écrits une seule fois.

- **`.github/agents/` a un seul propriétaire** — le scaffolder générait un
  wrapper à frontière d'outils fixe (`read, search` ou `read, search, execute`)
  pointant vers un chemin d'agent codé en dur, et l'émetteur Copilot réécrivait
  le même fichier avec la frontière réellement résolue et le vrai chemin de la
  persona. Deux écrivains pour un chemin : `_plan_agent_wrappers` est retiré du
  scaffolder, l'émetteur est seul propriétaire. Les garanties que les tests du
  scaffolder épinglaient (frontmatter, `user-invocable`, référence au fichier
  d'agent, fichier écrit à la main préservé) sont épinglées sur l'émetteur.
  Le wrapper pointe la définition résolue par la frontière kit/overrides :
  l'override du projet quand il existe, la version du kit sinon.

- **`grimoire memory status` ne sort plus en erreur sur un backend mort.** Un
  diagnostic qui échoue quand son sujet échoue ne sert à rien : la commande
  reporte désormais le contrat des sept couches, calculé depuis la config, plus
  la raison de l'indisponibilité. Le marqueur de santé `[OK]` / `[XX]` était par
  ailleurs invisible, Rich interprétant les crochets comme des balises.

- **`memory_link_status()` porte le contrat de couches et la parité**, donc
  l'atelier et le cockpit lisent la même source au lieu de la déduire chacun de
  son côté.

- **La page mémoire du cockpit lit l'état réel.** Elle rendait un instantané
  généré qui devinait le backend depuis la présence d'un répertoire et lisait le
  store legacy ; ses cinq pseudo-couches ne correspondaient à aucune couche du
  runtime. Elle interroge maintenant l'API locale et affiche les sept couches
  réelles avec leur état, avec repli sur l'instantané si aucune API ne répond.

- `memory up` et `memory status` vivent dans `grimoire.cli.cmd_memory_ops`,
  chaîné depuis `cmd_memory_lexical` : le ratchet R2 interdit à `cmd_memory` de
  grossir, et il rétrécit de 56 lignes.

- **fastembed remplace sentence-transformers et torch** dans les extras
  `[qdrant]` et `[weaviate]`. Mesure : la pile passe de **4,8 Go à 203 Mo** pour
  le même modèle par défaut, dont 2,7 Go de wheels `nvidia/*` et 689 Mo de
  triton qui n'avaient aucune raison d'être là — la CI les retéléchargeait à
  chaque run. Aucun re-index n'est nécessaire : les deux moteurs produisent des
  vecteurs identiques à 2e-7 près par composante (écart de cosinus 5e-13, top-1
  à top-10 inchangés sur 40 entrées et 10 requêtes), l'export ONNX de Qdrant
  étant fidèle et non quantifié. `sentence-transformers` reste utilisé à
  l'exécution s'il est déjà installé. Nouveau module
  `grimoire.memory.embedding`, partagé par les backends Qdrant et Weaviate.

- **La dimension des vecteurs n'est plus lue dans une table** — elle vient d'un
  vecteur sonde au chargement, donc elle est juste pour tout modèle, y compris
  inconnu. L'ancienne table retombait silencieusement sur 384.

- **Le backend Qdrant refuse une collection d'une autre largeur** que le modèle
  courant, au lieu d'écrire des vecteurs incohérents dans un store existant.

- Nouvelles clés `memory.embedding_model_path`, `memory.embedding_cache_dir` et
  `memory.embedding_offline`. `memory bundle verify --embed` prouve désormais le
  chargement avec le moteur réellement installé, fastembed compris.

- **`grimoire init` interroge le réseau, plus le service** — la question porte
  désormais sur l'egress, et un projet déclaré sans accès sortant est généré en
  `retrieval_mode: lexical`. Le démarrage de Qdrant via Docker reste proposé
  quand l'egress existe, mais **par défaut non** au lieu de par défaut oui.

- **Sonde `env_embedding_model`** dans `grimoire up` et `grimoire doctor` :
  signale sans réseau ni téléchargement un `embedding_model_path` cassé, un
  `embedding_offline` sans modèle local, ou un bundle installé mais non câblé.

- La conversion d'états ledger ↔ board vit dans un module unique
  (`grimoire.missions.board`), testée dans les deux sens. Un état ajouté d'un
  seul côté casse un test plutôt que de faire disparaître une carte du tableau.

### Corrigé

- La propagation d'identité vers `.github/copilot-instructions.md` écrasait le
  champ suivant lorsqu'une valeur était vide (`\s*` franchissait le saut de
  ligne). Un projet sans `user.name` y perdait son réglage de langue.

- Le kit copiait ses propres `__pycache__` dans les projets.

- **Détection d'hôte** — `HostBridge.detect()` identifiait Claude Code sur la
  présence d'`ANTHROPIC_API_KEY` et Codex sur `OPENAI_API_KEY`. Une clé
  d'API dit qui paie les jetons, pas quel hôte s'exécute : toute session
  exportant les deux était mal routée. La détection repose désormais sur des
  marqueurs de processus (`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CODEX_ENV`…).

- **`CLAUDE_CODE_CLI_MANIFEST`** déclarait `user_prompt_submit: False`. Claude
  Code expose bien cet événement ; le manifeste en excluait le seul hook capable
  d'enrichir un prompt avant que le modèle ne le lise.

- **Fenêtres de contexte des variantes longues** — `resolve_window` résolvait
  `claude-opus-5[1m]` vers la fenêtre standard de sa famille, sous-évaluant le
  budget d'un facteur cinq. Un marqueur explicite (`[1m]`, `-1m`, `:1m`) est
  désormais lu avant la famille.

- **Fusion JSON des émetteurs** — une variable de boucle réutilisée faisait
  passer le texte du fichier précédent à la fonction de fusion quand le fichier
  cible n'existait pas encore.

- **Suite de tests rouge sans l'extra `mcp`** — `tests/unit/mcp/test_server.py`
  importait `grimoire.mcp.server` au niveau module : sans `grimoire-kit[mcp]`
  installé, pytest remontait une *erreur de collecte* et toute la suite passait
  au rouge. Un `pytest.importorskip` énonce le même fait sans en faire un échec.
  C'est ce qui rendait le hook pre-commit systématiquement rouge en local, et
  donc `--no-verify` systématique.

- **`grimoire up` n'écrase plus les instructions du projet.** En 3.33.0,
  `.github/copilot-instructions.md` et les passerelles `CLAUDE.md` /
  `AGENTS.md` / `GEMINI.md` / `.cursorrules` étaient régénérés à chaque mise à
  jour. Sur un projet réel, le fichier d'instructions est passé de 227 à 112
  lignes, perdant la doctrine qui gouvernait le dépôt. Ces fichiers sont
  désormais semés une fois puis laissés au projet — contrepartie assumée : la
  table des agents installés s'y périme.

- **`grimoire migrate --adopt-kit` ne supprime plus les fichiers archivés par
  le projet.** Le masquage était détecté par nom de fichier, si bien qu'un
  `agents/_archived/concierge.md` passait pour un doublon de l'agent
  `concierge` livré par le kit ; l'adoption le supprimait sans que rien ne le
  régénère. La détection compare désormais le chemin complet.

- **La propagation d'identité n'efface plus un champ que la config ne déclare
  pas.** `project-context.yaml` n'impose pas de section `user:` ; en son
  absence, `grimoire up` vidait `**User**` dans le fichier d'instructions.

- **Le hook de cycle de vie coûtait 391 ms par appel d'outil** — et il était
  câblé sur `Read`, donc sur chaque lecture de fichier de chaque session.
  Trois causes, toutes mesurées :
  **le point d'entrée** (`grimoire host hook` construisait l'arbre Typer complet,
  chaque module `cmd_*` importé pour résoudre une sous-commande) — un script
  console dédié `grimoire-hook` le remplace dans les configurations générées,
  391 ms → 102 ms ;
  **les ré-exports impatients** (`grimoire/__init__.py` et
  `grimoire/core/__init__.py` importaient onze modules dont le scaffolder et le
  résolveur d'archétypes) — résolution paresseuse PEP 562, API inchangée ;
  **le moteur du standard importé pour rien** (`check_evidence_gates` au niveau
  module alors que la décision d'outil ne l'appelle jamais) — import différé, et
  les chemins de sortie du standard déménagent dans le module léger
  `standard_manifest`.
  Enfin `Read` sort du matcher sur les hôtes qui ont une table de permissions :
  les mêmes fichiers y sont déjà refusés déclarativement, à coût nul. L'accès par
  commande shell reste couvert, `Bash` restant dans le matcher.
  Deux tests épinglent le résultat, dont un qui échoue si le chemin des hooks
  réimporte le moteur au chargement.

- **Motifs de secrets et règles déclaratives avaient dérivé** — la détection
  couvrait neuf familles de fichiers de credentials, la table `deny` six. Trois
  familles (`.npmrc`, `credentials.json`, `service-account*.json` entre autres)
  n'étaient donc pas protégées du côté qui ne coûte rien. Les deux formes sont
  désormais déclarées ensemble dans `grimoire.hosts.secrets`, et un test refuse
  qu'une famille existe sans ses deux expressions.

### Sécurité

- Alerte CodeQL `py/command-line-injection` du dispatch cockpit classée après
  correction du risque réel (injection d'argument) : les valeurs issues d'une
  requête passent après `--` et refusent le préfixe `-`.

### Supprimé

- **Références déclaratives qui ne résolvaient nulle part.** `_verify_pattern_catalog`
  exigeait la *présence* des clés `check_refs`, `rule_refs` et `check_id`, jamais leur
  résolution : le catalogue promettait des contrôles que le moteur n'émet pas, et
  `docs/governed-controls.md` publiait ces promesses. Elles sont retirées plutôt
  qu'implémentées — on retire des promesses, pas des contrôles : `standard verify` sur un
  projet neuf produit exactement les mêmes identifiants de checks qu'avant, vérifié sur
  les cinq profils. Un test d'intégrité référentielle interdit désormais toute
  déclaration sans exécutant.
  - 17 `check_refs` sans check émis : `events.invalid_line`, `hooks.destructive_bypass`, `hooks.gateway_missing`, `knowledge.source_unindexed`, `ledger.mission_unlinked`, `memory.graph_projection_unverified`, `memory.hot_memory_partial`, `observability.cockpit_mutation`, `observability.input_undeclared`, `observability.secret_export`, `orchestration.handoff_unverified`, `orchestration.role_undeclared`, `provider.cost_unbudgeted`, `provider.slo_undeclared`, `skills.classification_missing`, `tools.threat_unmapped`, `tools.unmediated_call`.
  - 21 `rule_refs` sans règle correspondante dans `rule-packs.yaml` : `context.compression-preserves-provenance`, `decision.council-before-irreversible`, `governance.cluster-action-dry-run`, `governance.env-policy-declared`, `guardrail.versioned-four-faces`, `knowledge.doc-to-graph-sourced`, `memory.integrity-validated`, `merge.fault-classified-before-retry`, `observability.prompt-version-tracked`, `orchestration.flow-manifest-exportable`, `orchestration.workflow-state-declared`, `privilege.controller-agent-separated`, `prompt.external-content-isolated`, `provider.cost-and-slo-declared`, `quality.browser-evidence-required`, `quality.visual-evidence-required`, `remote.freshness-verified`, `runtime.k8s-agent-declared`, `runtime.provider-contract-uniform`, `security.workspace-isolated`, `tools.blast-radius-bounded`.
  - 13 `check_id` de règles pointant vers un check inexistant : `evidence.minimum_missing`, `hooks.destructive_bypass`, `hooks.gateway_missing`, `knowledge.source_unindexed`, `memory.freshness_missing`, `memory.hot_memory_partial`, `observability.cockpit_mutation`, `observability.input_undeclared`, `observability.secret_export`, `orchestration.handoff_unverified`, `provider.cost_unbudgeted`, `skills.classification_missing`, `tools.unmediated_call`.

- `check_id` n'est plus une clé obligatoire d'une règle de `rule-packs.yaml`. Une règle
  sans check déclare honnêtement qu'aucun contrôle ne l'applique ; celles qui en
  déclarent un doivent maintenant qu'il existe.

## [3.32.0] - 2026-08-18

### Ajouté

- **Workflow `party-mode`** — 25 agents livrés exposaient un menu `[PM] Party Mode`
  pointant vers `_grimoire/core/workflows/party-mode/workflow.md`, un chemin que
  le kit ne fournissait nulle part et qu'aucun installeur ne crée : la capacité
  était déclarée dans `framework/agent-base.md`, générée dans chaque nouvel agent
  par `agent-forge.py` et citée par la taxonomie, sans implémentation. Le playbook
  existe désormais (`framework/workflows/party-mode.md`) et toutes les références
  pointent vers son emplacement réel, `_grimoire/_config/custom/workflows/party-mode.md`.
  Panel de 3 à 5 agents, premier tour sans lecture croisée pour éviter la
  convergence, second tour limité aux désaccords, aucun vote, arbitrage rendu à
  l'utilisateur et tracé dans `decisions-log.md`.
- **Bornes du gauntlet** (archétype `fix-loop` 1.1.0, workflow closed-loop-fix
  v2.7) — CHALLENGER + GATEKEEPER restent opt-in, mais gagnent trois règles qui
  décident quand ils tournent et quand ils s'arrêtent. **Déclencheurs**
  (Phase 1.5) : cinq signaux objectifs escaladent la sévérité et rallument le
  passage adversarial sur un cycle classé trop bas — `T1-repeat` (2e tentative
  sur le même symptôme), `T2-security`, `T3-prod`, `T4-surface` (≥ 3 composants
  impactés), `T5-data` (écriture non réversible) ; la sévérité ne redescend
  jamais en cours de cycle. **Gate oracle** (Phase 2.4bis) : le gauntlet exige
  une commande avec `exit_code` attendu qui échoue avant le fix et passe après,
  les deux exécutions capturées ; sans oracle les deux phases sont désactivées,
  le rapport est marqué « appliqué, non certifié » et aucun pattern n'est écrit.
  **Arrêt sur boucle stérile** (Phase 4.6) : deux itérations dont la signature
  d'échec (commande + `exit_code` + 1re ligne `stderr`) est identique escaladent
  à l'humain sans consommer `max_iterations`. Nouveaux champs FER (v3.1) :
  `severity_escalated_from`, `gauntlet_trigger`, `oracle_available`,
  `failure_signatures[]`. La question 7 de la META-REVIEW mesure si un
  déclencheur a servi ou coûté pour rien, de quoi resserrer les seuils sur
  données réelles plutôt qu'à l'intuition.

### Corrigé

- **Les placeholders `{{…}}` n'étaient jamais résolus** — les agents et workflows
  arrivaient dans le projet avec leurs marqueurs bruts là où les noms d'agents de
  l'utilisateur devaient apparaître (25 dans le seul `closed-loop-fix`). Une passe
  de rendu résout à l'installation ce qui est connaissable : rôles de délégation
  (`{{ops_agent_name}}`, `{{debug_agent}}`, …) résolus depuis les agents réellement
  installés — un rôle sans agent rend « aucun » et laisse le workflow en mode SOLO,
  son défaut documenté —, plus `{{tech_stack_list}}`, `{{user_name}}`, `{{project_name}}`.
  La substitution est **opt-in par clé**, jamais un balayage : les trois autres
  familles de placeholders du kit survivent intactes — les slots runtime que le LLM
  remplit à chaque exécution (`{{current_step}}`, `{{progress_bar}}`), l'infra que
  le kit ne peut pas deviner (`{{lxc_id}}`, `{{host_ip}}`), et l'agent vierge de
  l'archétype `minimal`.

- **Les workflows d'archétype n'étaient jamais installés** — `grimoire init`
  (chemin recommandé) et `grimoire-init.sh --archetype` copiaient les agents
  d'un archétype, sa DNA et son `shared-context`, mais pas son dossier
  `workflows/`. Seul `framework/workflows/` atterrissait dans le projet. Le
  workflow `closed-loop-fix` de `fix-loop` — le seul workflow porté par un
  archétype — n'existait donc dans aucun projet initialisé, et le menu `[FX]`
  de son agent pointait vers `_grimoire/bmb/workflows/fix-loop/…`, un chemin
  qu'aucun installeur ne crée. La cible est désormais
  `_grimoire/_config/custom/workflows/`, là où vivent déjà les workflows du
  framework. Corrigé sur le SDK et sur `grimoire-init.sh install --archetype`.
  L'init complet de `grimoire-init.sh` n'est pas touché : la correction y ferait
  grossir un entrypoint gelé, ce que `framework/FREEZE.md` désigne comme le
  signal de porter la capacité sous `src/` — `grimoire init` est le chemin
  recommandé et il est correct.
- **Le suffixe `.tpl` fuitait dans les projets** — `fix-loop-orchestrator.tpl.md`
  et `workflow-closed-loop-fix.tpl.md` étaient copiés tels quels. `.tpl` marque
  une source du kit ; il est retiré à l'installation, ce qui aligne le nom
  installé sur celui que la documentation et les références utilisaient déjà.
- Test de contrat associé : toute cible `exec=` d'un agent qui désigne un
  workflow livré par le kit doit correspondre à un fichier réellement installé.

## [3.31.0] - 2026-08-15

Aucune entrée n'a été consignée pour cette publication. Le détail est dans
l'historique git (`git log v3.30.0..v3.31.0`) et dans les notes de release
GitHub ; il n'est pas reconstitué ici, pour ne pas attribuer après coup des
changements à une version au jugé.

## [3.30.0] - 2026-08-15

Aucune entrée consignée — voir `git log v3.29.0..v3.30.0`.

## [3.29.0] - 2026-08-15

Aucune entrée consignée — voir `git log v3.28.0..v3.29.0`.

## [3.28.0] - 2026-08-15

Aucune entrée consignée — voir `git log v3.27.0..v3.28.0`.

## [3.27.0] - 2026-08-15

Aucune entrée consignée — voir `git log v3.26.1..v3.27.0`.

## [3.26.1] - 2026-08-15

Aucune entrée consignée — voir `git log v3.26.0..v3.26.1`.

## [3.26.0] - 2026-08-14

### Ajouté

- **Verdict de sécurité — surface d'attaque agrégée** (blueprint, P2.4) — la
  couche guardrails (`Gate(guardrail, in|out)`, `Gate(mcp-trust)`) était déjà
  déclarable et lintée (R-G1/R-G2/R-G5) ; on ajoute la **vue de synthèse** :
  `security_verdict` agrège points d'entrée (sources externes + couverture
  mcp-trust/guardrail d'entrée), points de sortie (couverture guardrail de
  sortie), points de filtrage déclarés et expositions résiduelles, avec un
  verdict global `secure|exposed`. `blueprint_lint` renvoie un champ `security`
  additif et la compilation émet une section « Surface d'attaque (sécurité) ».
  Cohérent par construction avec `gate_lint` (mêmes helpers) — le flow où un
  node externe alimente une sortie sans guardrail refuse toujours de compiler
  (R-G2). Nouveau module `grimoire.tools.blueprint_security`.

- **Évals comportementaux first-class** (blueprint, P1.2) — une suite d'évals
  versionnée s'attache à un node (`config.evals`) ou au blueprint entier
  (`evals` top-level) : cas d'entrée + assertions typées (`contract`, `cost`,
  `no-refusal`, `verdict`, `path-taken`). Le lint valide la forme (R-E1 :
  versionnée) et **recoupe `path-taken` avec le plan de défaillance déclaré**
  (R-E3, jonction P3.1) ; un node externe sans preuve est signalé (R-E2). Le
  panneau santé expose un **taux de réussite d'éval par node** (`blueprint_lint`
  renvoie un champ `evals` additif), et la compilation émet une section « Évals
  (preuve comportementale) » — checks exécutés par l'hôte (`agent-test`), jamais
  par le Studio. Nouveau module `grimoire.tools.blueprint_evals` ; `$def`
  `evalSuite` au schéma.

- **Injection d'échec en simulation** (blueprint, P3.1) — le what-if de
  résilience : `blueprint_simulate` accepte une cible `{nodeId, class}` (ou
  `GET/POST …/simulate?injectNode=&injectClass=`) et trace le plan de
  défaillance réellement suivi — retry borné → fallback (edge `failure`) →
  escalade (edge `escalation`) → terminaison `onExhaustion` —, avec le `path`
  des nodes traversés (assertion `path-taken`). La simulation nominale reste le
  plan happy (`failureInjection: null`). Déterministe ; l'hôte reste
  l'exécutant.

### Corrigé

- **`grimoire update` échouait sur les installations `uv tool`** — la commande
  ne connaissait que `pipx` et `pip`, alors qu'un environnement `uv tool`
  (voie d'installation recommandée) n'expose pas `pip` : le repli
  `python -m pip install --upgrade` échouait. La détection de méthode teste
  désormais `uv tool` en premier, puis `pipx`, puis `pip`, et affiche la
  commande manuelle correspondante en cas d'échec.
- **`grimoire update` pouvait rester bloqué sur une version périmée** — la
  version cible venait de `info.version`, qui accuse un retard de propagation
  CDN de quelques minutes après une publication (« already up to date » à tort,
  ou montée vers une version périmée). La résolution prend maintenant le max
  des clés `releases` (hors versions retirées et pré-versions) et compare en
  sémantique stricte. La logique de mise à jour est extraite dans
  `grimoire/cli/updater.py`.

## [3.25.0] - 2026-07-23

### Ajouté

- **Famille résilience** (blueprint, P2.2) — comment un flow échoue, en format :
  politique node-local `config.resilience` (retry **borné** `max` 1-10 +
  `backoffMs`/`strategy`, `timeoutMs`, `onExhaustion`) et les quatre motifs
  (retry, fallback, compensation, dead-letter/escalade) exprimés via les edges
  `failure`/`escalation` de P0.2 portant le contrat `error-envelope`. Lint
  opposable : **R-F1** (retry sans `max` refuse de compiler), **R-F2** (edge de
  défaillance dont le contrat n'est pas `error-envelope`), **R-F4** (escalation
  non terminale) — bloquants ; **R-F3** (node externe sans chemin de
  défaillance ni résilience) — avertissement. La compilation émet une section
  `on_failure` par node résilient ; l'hôte reste l'exécutant. `$def
  resiliencePolicy` documenté au schéma.

### Corrigé

- **Robustesse des payloads de setup et du cadrage** : `POST /api/setup` avec
  `needs: null` ne plante plus (`TypeError`), et `extensions` en chaîne n'est
  plus itéré caractère par caractère (un `"demo"` installait `d/e/m/o`) ;
  `name`/`user` `null` ne produisent plus `--name "None"`. Les suggestions de
  needs tolèrent un catalogue `needs: null` ou des entrées non-dict.
  `grimoire cadrage status`/`check` ne plantent plus sur un fichier de phase
  non-UTF8 (octets invalides remplacés).

### Ajouté

- **`grimoire cadrage`** (B4) — comprendre avant de construire : un flux guidé
  en cinq phases (brief → brainstorm → compréhension → exigences → cahier des
  charges) matérialisé en artefacts gouvernés sous `_grimoire/cadrage/`.
  Discipline embarquée : brainstorm qui note ce qu'il écarte, faits séparés
  des hypothèses, exigences MoSCoW avec critères d'acceptation, périmètre ET
  hors-périmètre. `cadrage status` mesure la progression ; `cadrage check` est
  un **gate de complétude** (exigences + CDC exigés — on ne construit pas sur
  un engagement flou). Nouveau need `project-discovery` au catalogue.

- **Suggestions de needs pilotées par le projet** (B3) : `grimoire up` sans
  `--needs` analyse le projet réel (docs, CI + conteneurs, hooks/skills,
  configs MCP, agents déclarés, multi-stack) et suggère les needs du catalogue
  qui collent — avec la raison et la preuve de chaque suggestion, et la
  commande d'install sur mesure prête à copier. Best-effort : le défaut
  `starter` s'applique toujours, la suggestion n'interrompt jamais l'install
  (`grimoire.core.needs_suggest`).

- **Lien projet ↔ base mémoire visible et piloté** (B1) : nouveau
  `GET /api/memory/status` (backend configuré, backend résolu, disponibilité,
  volumétrie — best-effort, ne casse jamais si un serveur est éteint) et
  `GET /api/backends` (catalogue des backends avec descriptions humaines).
  Le hub de l'atelier affiche l'état du lien mémoire du projet.
- **Wizard de setup modernisé** (B2) : une étape « Mémoire / BDD » (choix du
  backend, validé côté serveur) et le plan compile désormais vers
  **`grimoire up`** — plus jamais vers l'installeur shell legacy. Logique
  extraite dans `grimoire.tools.project_setup`, testée.
- **Gate universel paramétré** (blueprint, P2.1) : une primitive, six modes —
  `human` (HITL riche : approve/edit/input/sample/escalate-on-uncertainty),
  `budget`, `evidence`, `output-contract`, `guardrail`, `mcp-trust` — déclarés
  en `config.gate {mode, onReject, params}`. Un seul compilateur (switch
  unique) ; le rejet réutilise les edges typés de P0.2 (`escalation`,
  `failure`, ou arrêt dur). Frontière de confiance opposable : **R-G1**
  (bloquant — node externe sans `Gate(mcp-trust)` en amont) et **R-G2**
  (bloquant — contenu externe atteignant une sortie sans `Gate(guardrail, in)`),
  plus R-G3 (block sans alternative), R-G4 (schéma non résolu), R-G5
  (sortie sans guardrail out). `$def gatePolicy` documenté au schéma.

### Modifié

- **Compilation reproductible** (blueprint, P0.1) : plus aucun horodatage dans
  le contenu compilé — `generatedAt` sort du mission pack, la date de
  compilation vit dans les métadonnées (`compiled.at`). Même blueprint + même
  catalogue ⇒ même contenu ⇒ même hash (preuve : test de double compilation).

## [3.24.0] - 2026-07-22

### Ajouté

- **`grimoire context-pack`** : matérialise un context-pack durable de repo,
  conforme au contrat `context-pack` du catalogue (sources incluses/exclues avec
  statut et confiance, scorecard de suffisance, expiry avec invalidation sur
  changement de HEAD), sous l'ordre d'autorité ORC-06. Capacité produit rapatriée
  depuis un hook d'atelier vers `grimoire.tools.context_pack` — testée et
  couverte par la CI.
- **`grimoire.tools.handoff`** : dérive de façon déterministe un `handoff-packet`
  conforme au contrat catalogue (ORC-03) depuis une capsule de SubagentStop —
  champs dérivables (`task_id`, `summary`, `evidence`, `next_trigger`, statut)
  remplis, champs d'analyse (`changes`, `assumptions`, `risks`,
  `memory_candidates`) marqués « à enrichir » plutôt qu'inventés. Capacité
  produit rapatriée d'un hook d'atelier, testée.
- **Régions d'isolation** (blueprint, C3) : un tableau `boundaries` déclare des
  régions `{id, mode: isolation, members}` — plusieurs nodes partageant une
  fenêtre quarantinée (patron orchestrateur-worker), le cas multi-nodes de
  l'isolation de node C1. La compilation émet **un seul dispatch quarantiné par
  région** (preuve : une région multi-nodes → un dispatch), la simulation
  expose la pression agrégée par région, et le lint **R-C7** refuse qu'une
  région exporte un contrat non-digest (quarantaine : seul un digest sort).
  Additif — sans `boundaries`, comportement inchangé.
- **Classe sémantique de node `role`** (blueprint, P0.3) : algèbre de 7
  primitives orthogonales — `Unit` (la seule « qui fait »), `Route`, `Scatter`,
  `Gather`, `Gate`, `Boundary`, `Reference`. `role` est orthogonal à `kind`
  (d'où vient le node vs ce qu'il fait), additif et optionnel. Les ~20 cases de
  la palette XXL deviennent des **paramètres** de ces 7 primitives (source de
  vérité `grimoire.tools.blueprint_primitives`, exposée par
  `GET /api/primitives`) : plus de bestiaire de `kind`, un tableau de
  configurations éprouvées. Validation du `role` à la sauvegarde.
- **Typage d'edge `channel`** (blueprint, P0.2) : chaque edge porte un canal
  `happy` (défaut) `| failure | escalation`. Additif et rétro-compatible —
  l'absence vaut `happy`, les blueprints existants migrent sans perte. La
  simulation ne suit que le canal nominal pour l'ordre et la pression de
  contexte (les chemins d'échec/escalade sont des routes alternatives), expose
  la répartition `channels`, et l'éditeur distingue visuellement ces chemins.
  Débloque la famille résilience (edges `failure`) sans nouveau `kind`.
- **Modèle de coût calibré** (ingénierie de contexte, tranche C2) : la table de
  coût par pattern, jusqu'ici en dur dans `web/bp2-cost.js`, devient une source
  de vérité serveur (`grimoire.tools.cost_model`) exposée par
  `GET /api/cost-model`. La simulation de pression de contexte calibre le coût
  d'entrée de chaque node sur son pattern (au lieu d'un forfait plat), la vue
  COÛT du Studio bascule sur « calibrée » quand le serveur répond (repli
  statique sinon), et l'assertion d'éval `cost-under` (`estimate_usd` /
  `cost_under`) se vérifie contre les mêmes taux — une seule source pour design,
  gate et éval.
- **`grimoire hooks`** (install/list/status) : port Python de
  `grimoire-init.sh hooks` — première étape du plan de résorption bash
  (`docs/resorption-bash.md`). Résolution correcte dans les worktrees git
  (`git rev-parse --git-path hooks`), sources depuis le checkout du kit ou
  les données embarquées du wheel, préservation des hooks tiers, sortie
  `-o json`.
- **Plan de résorption bash** : inventaire complet des 28 sous-commandes de
  `grimoire-init.sh` (couvert / wrapper mince / gap) et séquence de port
  dans `docs/resorption-bash.md`.
- **Backend mémoire `lexical`** : implémentation SQLite FTS5 avec classement
  BM25 et matching insensible aux diacritiques (`unicode61 remove_diacritics 2`),
  zéro dépendance externe. Honore le contrat `backend: lexical` /
  `retrieval_mode: lexical` déjà déclaré dans le schéma de configuration mais
  jamais implémenté. Migration automatique du store JSON local historique
  (IDs et timestamps préservés).
- **Backend mémoire `tantivy-local`** (extra `search`) : moteur full-text
  embarqué Tantivy (Rust, classe Lucene) avec BM25 et stemming français +
  anglais — `harmonisé` matche `harmonisation`. Prévu pour les corpus
  volumineux (code, docs). Installation : `pip install grimoire-kit[search]`.
- **Retrieval hybride** : module `grimoire.memory.retrieval` avec fusion
  reciprocal rank fusion (`rrf_fuse`) et `HybridRetriever` multi-backends
  tolérant aux pannes. `MemoryManager.hybrid_search()` fusionne le classement
  vectoriel et un index compagnon lexical FTS5, mirroré automatiquement à
  chaque écriture ; `reindex_lexical_companion()` pour le backfill.
- **Surface CLI retrieval** : `grimoire memory search --hybrid` (fusion RRF)
  et `grimoire memory reindex-lexical` (backfill du compagnon).
- **Projection docs** : `grimoire memory vector sync-docs` indexe les pages
  markdown (`docs/`, `README.md` par défaut) dans le backend mémoire actif —
  scope `docs` interrogeable via la recherche BM25/hybride. Le scope `code`
  est couvert par la projection backend-agnostique existante
  (`memory vector sync-code`), compatible avec les nouveaux backends.
- **Evals retrieval** : gold set recall@k
  (`tests/unit/memory/test_retrieval_quality.py`) gardant l'échelle de
  qualité — lexical jamais sous local, stemming tantivy complet sur les
  requêtes morphologiques françaises, fusion RRF récupérant les deux
  classements.
- **Tantivy insensible aux diacritiques** : champ `text_folded` (NFD) — les
  requêtes accentuées et non accentuées matchent dans les deux sens.

### Corrigé

- **`framework/hooks/pre-commit-cc.sh`** : le venv du projet est préfixé au
  PATH — le Completion Contract utilise le pytest/ruff/mypy du projet au
  lieu de l'outillage système.
- **`framework/hooks/pre-push.sh`** : étape quickcheck avec résolution de
  layout correcte (kit direct `framework/tools/` ou kit nested
  `grimoire-kit/framework/tools/`) — l'ancien hook installé cherchait un
  chemin valable uniquement depuis un projet hôte.

### Modifié

- **Résolution `backend: auto`** : sans serveur vectoriel configuré, le défaut
  local devient `lexical` (FTS5 BM25) quand SQLite le supporte, avec repli sur
  le backend JSON `local` sinon. `retrieval_mode: lexical` ou
  `vector_database: false` forcent désormais le backend lexical même si une
  URL serveur est présente.

## [3.23.0] - 2026-07-08

### Ajouté

- **`grimoire serve`** : commande de premier niveau lançant l'atelier local
  (UI Forge + API blueprints) sur `127.0.0.1`. Remplace l'ancien
  `python -m grimoire.tools.forge_server`, qui reste disponible pour l'usage
  avancé (`--ui-dir`, `--kit-root`).
- **Rework Vitrine/Atelier** : le site v2 est branché de bout en bout sur le
  réel — catalogue normatif (78 patterns), marketplace, éditeur de blueprints
  (Studio), wizard de setup, observatoire et mémoire lisent l'API locale et
  les données générées. Plus aucune donnée de démo dans le mode atelier.
- **`grimoire stigmergy`** *(canal beta)* : coordination indirecte par
  phéromones — `emit/sense/amplify/resolve/trails/evaporate/stats`, plus
  `install-hooks`/`uninstall-hooks` pour câbler l'émission et la captation
  automatiques via des hooks **non bloquants** (SessionStart, PostToolUse,
  Stop). Vue live dans l'observatoire.
- **`grimoire features`** : canaux de maturité stable / beta / experimental,
  activables par projet (`_grimoire/features.json`), avec page **Labs** dans
  l'atelier et journalisation des usages pour la promotion sur métriques.
- **Packaging** : le wheel embarque désormais `extensions/` et `version.txt`
  — un `pip install` dispose d'un marketplace réel et de la bonne version.

### Corrigé

- **Robustesse & concurrence** (audit du kit contre son propre catalogue) :
  écritures atomiques (board, features, journal), verrou inter-process contre
  les pertes de mise à jour des hooks concurrents, cap par zone anti
  signal-storm, journal stigmergique borné et versionné.
- **Sécurité du serveur local** : garde CSRF / DNS-rebinding sur les mutations
  (refus des Host non-loopback et Origin cross-origin), correctif de préfixe
  dans le service statique, télémétrie gouvernée des mutations.
- **`install_hooks`** : rollback transactionnel sur échec partiel.
- **`quick-check.sh`** : bit exécutable rétabli (déblocage des pre-push
  consommateurs).

## [3.22.0] - 2026-07-03

### Ajouté

- **UI embarquée** : les pages marketplace, blueprint et setup rejoignent le
  wheel (`grimoire/data/web`) — `grimoire serve` sans `--ui-dir` sert
  l'expérience complète après un simple `pip install grimoire-kit` (#57).
- **UX v2 de l'éditeur blueprint** : drag de connexion Maj+glisser avec
  contrats vérifiés au drop, palette latérale cliquable (recherche, groupes),
  panneau propriétés du node (label, contrats de pins), undo Ctrl+Z,
  layout automatique, aide contextuelle (#59).
- **Extension fennara-godot** : premier `mcp-toolbox` du marketplace
  (QUA-12, QUA-04, RUN-08) (#50).
- **Campagne evals web-app-todo** : cadrage du témoin, baseline, grille de
  jugement pré-enregistrée, mécanique de run standard-null hors bras
  governed (#53, #54, #55, #56).

### Corrigé

- Test `test_baseline_record_on_bare_project` aligné sur le protocole
  standard-null (#58).

## [3.21.1] - 2026-07-03

### Corrigé (issue #39 — suite)

- **Sanitisation MCP durcie** (C8) : les entrées sont normalisées avant scan
  (percent-decoding, caractères zero-width) — `%2e%2e%2f` et les mots-clés
  d'injection obfusqués ne contournent plus le filtre ; la traversée de chemin
  est détectée dès 2 segments même non consécutifs (`../a/../b`), le `../`
  isolé restant permis (chemins relatifs légitimes). Patterns d'injection
  élargis (disregard/forget/prior/earlier, marqueurs `<|im_start|>`),
  explicitement documentés comme heuristiques. 7 tests.
- **Commentaires « Silent exception » obsolètes retirés** (C5) : 100
  occurrences dans 42 outils pointaient « add logging » au-dessus de lignes
  qui loggent déjà — le cœur de C5 (chemins de routage) avait été traité
  par #41 avec des warnings contextualisés.


## [3.21.0] - 2026-07-03

### Ajouté

- **Extensions** : `grimoire ext add|list|remove|verify|publish` — bundles
  d'artefacts gouvernés décrits par `extension.json` (schéma versionné),
  installation locale ou depuis le registry dédié
  [grimoire-extensions-registry](https://github.com/Guilhem-Bonnet/grimoire-extensions-registry)
  avec checksum sha256 vérifié et extraction sûre. Six extensions publiées :
  crewai, langfuse, langgraph, autogen, browser-use, haystack — chacune
  ancrée sur le catalogue de patterns agentiques (`patterns.implements`
  obligatoire, hooks toujours en mode shadow).
- **`grimoire serve`** : mode local UI + API (127.0.0.1) — wizard de setup
  par archetypes, vue des artefacts gouvernés, gestion d'extensions, CRUD et
  validation de blueprints, stream SSE des events.jsonl.
- **Blueprints** : format `.blueprint.json` avec pins typés bloquants (une
  connexion sans contrat commun ne compile pas), lint normatif dérivé du
  catalogue (dépendances de patterns, heuristique Faux Done, nodes isolés)
  et replay de télémétrie via bindings.
- **Écriture mémoire typée dans le SDK** (`grimoire memory remember` /
  `recall`) — parité complète avec le protocole agent legacy : 5 types
  (shared-context, decisions, agent-learnings, failures, stories),
  déduplication UUID5 identique à mem0-bridge
  (`uuid5(DNS, "grimoire-{proj}:{agent}:{text[:150]}")`), upsert idempotent
  avec fallback anti-doublon pour les backends sans `upsert`. 23 tests.

- **Simulation pré-exécution des blueprints** et **publication de blueprints
  au marketplace** (workflow extensions).

### Corrigé (issue #39 — merci @zavrocKk)

- **Routage LLM réparé** (C1/C2) : l'agent-caller appelait le routeur avec un
  kwarg inexistant (`TypeError` avalé silencieusement → toujours le modèle par
  défaut) et `_resolve_model` de l'agent-worker retournait un objet
  `TaskClassification` au lieu d'un id de modèle.
- **SSRF avec résolution DNS** (C4) : les 4 outils fetch (web-browser,
  docs-fetcher, doc-fetcher, rag-indexer) filtraient par préfixe de chaîne
  sans résoudre le hostname — DNS rebinding et IP décimales/octales/hex
  passaient. La validation résout désormais via `getaddrinfo` et rejette
  loopback/privé/link-local/réservé ; `rag-indexer` conserve sa sémantique
  `allow_localhost` (LAN autorisé, metadata toujours bloqué). Le risque
  résiduel TOCTOU (pas de pinning d'IP) est documenté. 18 tests offline.

### Modifié

- **`agent-base.md` bascule sur le SDK** (étape 2 de l'ADR-003) : le protocole
  mémoire des agents pointe vers `grimoire memory remember`/`recall`,
  `mem0-bridge.py` devient le fallback documenté (SDK absent). Idem
  `agent-base-compact.md` et `grimoire-trace.md`. `export-md` reste legacy
  (pas d'équivalent SDK).

## [3.20.0] - 2026-07-02

### Ajouté

- **`grimoire doctor` : check « agents découvrables »** (suivi issue #33) — si des
  agents sont déployés mais qu'aucun wrapper `*.agent.md` n'existe dans
  `.github/agents/`, doctor échoue avec la remédiation (`grimoire init . --force`)
  au lieu d'annoncer un projet sain.

### Modifié

- **Console 100 % cp1252-safe** : derniers glyphes non-ASCII purgés des sorties
  terminal de `framework/tools/` (flèches `→` U+27A1 → `->`, barres `█▓░` →
  `#=-!`) — clôt la purge emoji étapes 2-3.
- **Docs SDK-first** : `archetype-guide.md` et `onboarding.md` présentent le
  chemin SDK en premier ; les commandes shell restent documentées (mode
  maintenance, certaines n'ont pas d'équivalent SDK).
- ADR-003 : prérequis de parité documenté — `agent-base.md` reste sur
  `mem0-bridge.py` tant que la CLI SDK n'offre pas d'écriture mémoire typée
  (`remember --type` + dédup UUID5).


## [3.19.0] - 2026-07-02

### Corrigé (issue #33 — merci @zavrocKk)

- **Windows : agents découvrables** — la détection des fichiers agents utilisait
  un test de sous-chaîne `"/agents/"` qui ne matche jamais avec des backslashes ;
  `.github/agents/` restait vide sous Windows. Remplacé par un test sur
  `path.parts` (helper `_is_agent_markdown`, 4 sites, tests PureWindowsPath).
- **Template `custom-agent.tpl.md` réparé** — 12 ouvertures de commentaires HTML
  avaient été écrasées par un search/replace débordant ; chaque `grimoire init`
  propageait le bruit. Les deux copies (archetypes + _grimoire/_config) sont
  restaurées (13 `<!--` = 13 `-->`).
- **`grimoire init . -y` fonctionne** — l'option `--yes/-y` documentée n'existait
  qu'au niveau global (`grimoire -y init`) ; elle est maintenant aussi locale à
  `init`, ce qui rétablit le mode express non-interactif (CI/scripts).
- **Portabilité Windows** — `stigmergy.py` : verrou fichier portable
  (fcntl POSIX / msvcrt Windows / no-op sinon) au lieu d'un `import fcntl`
  top-level fatal ; `agent-caller.py` : séparateurs box-drawing → ASCII
  (UnicodeEncodeError sur console cp1252) ; wizard `init` : indicateurs de
  progression `[■□□□]` → `[#---]`.
- **README.fr : config MCP réelle** — la section pointait vers un
  `framework/mcp/server.js` inexistant avec 7 outils fictifs ; remplacée par le
  vrai serveur (`grimoire-mcp`, Python) et la liste réelle des 12 outils.

### Modifié

- **`agent-caller.py` : statut `simulated`** — en mode standalone (sans backend
  LLM), `call` renvoyait `status="success"` et polluait les métriques aval
  (success_rate, fitness, dashboard) avec des exécutions n'ayant jamais eu
  lieu. Nouveau statut `simulated`, compté séparément dans `get_stats`.

## [3.18.0] - 2026-07-01

### Ajouté

- **Démo animée quickstart** (`docs/assets/demo-quickstart.svg`) intégrée aux
  README EN/FR — sorties réelles validées en sandbox (init → standard init
  `--needs solo-prototyping` → verify OK → score 81/70 → gate check OK).
- **`docs/evals-protocol.md`** : protocole pré-enregistré (bras governed vs
  baseline, métriques, règles d'honnêteté) pour mesurer l'effet du standard
  avant tout claim d'efficacité public.
- **Transition shell → SDK** : `grimoire-init.sh` passe en mode maintenance —
  avis non bloquant au lancement pointant vers `grimoire init` (SDK),
  supprimable via `GRIMOIRE_SUPPRESS_INIT_NOTICE=1` ; le README.fr présente le
  chemin SDK en premier. Le script reste fonctionnel (`validate --all` vert).
- **MCP — standard gouverné consommable par les agents** : 4 nouveaux outils MCP
  (`grimoire_standard_verify`, `grimoire_standard_audit`, `grimoire_standard_score`,
  `grimoire_standard_gate`) exposent verify/audit/score/gate au travers de
  `grimoire-mcp` ; l'audit inclut les actions de remédiation proposées. 12 tests.
- **Waivers gouvernés pour l'audit de dépendances** (issue #20) :
  `.github/security/dependency-waivers.yaml` (schéma waivers du standard, borné par
  `expires_at`) + `scripts/depaudit-waivers.py` qui traduit les waivers actifs en
  `--ignore-vuln` ; un waiver expiré re-durcit automatiquement le job dep-audit.
  Waiver initial : CVE-2025-3000 (torch, transitif, sans fix amont). 5 tests.
- **Garde anti-drift de version** : `tests/unit/test_version_sync.py` échoue si
  `version.txt` (consommé par grimoire.sh / grimoire-init.sh / smoke-test) diverge
  de `src/grimoire/__version__.py`.
- `docs/rnd.md` : les features expérimentales (session branching, darwinism,
  stigmergy, dream mode…) documentées séparément du cœur mûr.

### Modifié

- **README anglais** recentré sur le différenciateur (standard agentique gouverné,
  ≈180 lignes) ; la version française complète devient `README.fr.md`.
- Section MCP du README corrigée : liste réelle des 12 outils (l'ancienne liste
  documentait 10 outils inexistants) ; retrait du flag `--transport sse` non
  implémenté ; `grimoire standard gate` → `grimoire standard gate check`.
- `version.txt` resynchronisé (3.4.2 → 3.17.0) ; badge de version statique retiré
  du README (le badge PyPI dynamique fait foi) ; version en dur retirée
  d'`ARCHITECTURE.md`.
- **`framework/memory/` lint-clean** (36 erreurs ruff → 0) : corrections mécaniques
  (implicit Optional, contextlib.suppress, pathlib, ClassVar, FURB162) +
  `per-file-ignores` justifiés pour les patterns intentionnels (S110/S310 probing
  tolérant, SIM112 env vars legacy rétro-compat). Zone ajoutée au scope lint
  (Makefile + CI).

### Supprimé

- **Distribution npm mort-née** : `npm/`, `package.json` racine (version figée
  3.4.3) et workflow `npm-publish.yml` retirés — le paquet n'a jamais été publié
  sur npm ; PyPI est le canal de distribution.

## [3.17.0] - 2026-06-29

### Ajouté

- **Cockpit local — dashboard multi-projets** (`grimoire cockpit`) — un site web local,
  embarqué dans le paquet, qui gouverne tous les projets Grimoire de la machine :
  portefeuille, observabilité (coûts/traces), santé CI, et gestion mémoire gouvernée.
  - Mode daemon convivial : `start` (arrière-plan + ouverture navigateur, non bloquant),
    `stop`, `status`, `open`, plus `serve` (premier-plan). Cross-platform.
  - API locale (`127.0.0.1` only) : introspection en lecture (statut, lint, recherche,
    taxonomie) et écritures gouvernées (`gc`, `delete`, `sync`) sous confirmation
    explicite — toujours via l'API Memory OS, jamais d'accès brut.
  - Registre `~/.grimoire/cockpit/registry.json` géré par `add`/`remove`/`list` ;
    `grimoire init` auto-enregistre le projet scaffoldé (opt-out `GRIMOIRE_NO_COCKPIT`).
  - Vitrine publique vs cockpit : les actions de pilotage sont actives en local et
    verrouillées sur la vitrine (`*.github.io`) ; données de démo multi-projets pour la
    vitrine via `scripts/gen-demo-projects.py`.
- **Mémoire sans base de données vectorielle** — nouveau backend `lexical` (sqlite FTS5
  BM25, accent-insensible) offrant une recherche sans aucune DB vectorielle, service ni
  réseau. Pour les environnements (corpo, régulés, air-gapped) qui interdisent une base
  vectorielle locale.
  - Option de setup `memory.vector_database` (true|false) et `memory.retrieval_mode`
    (vector|lexical) dans `project-context.yaml`, émises par `grimoire init` et validées
    par le schéma. `vector_database: false` force le backend `lexical` et court-circuite
    l'auto-détection réseau (aucune sonde ollama/qdrant).
  - Profil gouverné `no_vector_target` (sqlite-fts5) dans le template `memory-policy.yaml`.
  - `mem0-bridge seed` — peuple le backend depuis la source-of-truth markdown (mémoire
    projet + dossier optionnel), avec gate evidence/redaction et idempotence.

## [3.16.0] - 2026-06-26

### Changé

- **Purge emoji — sortie terminal (étape 3, finale)** — Balayage déterministe de toute la
  sortie CLI : `framework/tools/*.py`, le SDK `src/grimoire/**` et les tests (126 fichiers,
  ~1900 occurrences).
  - Glyphes de statut → marqueurs ASCII maison : `✅✔✓`→`[OK]`, `❌✖✗`→`[x]`, `⚠`→`[!]`,
    `ℹ`→`[i]`, pastilles de sévérité `🔴`→`[!!]` / `🟡🟠`→`[!]` / `🟢`→`[ok]` / `🔵`→`[i]`,
    `🚫⛔🛑`→`[STOP]`.
  - Emojis purement décoratifs (en-têtes de section, icônes de catégorie) supprimés.
  - Symboles typographiques conservés (flèches `→ ← ↑ ↓`, tirets, points de suite) — ce ne
    sont pas des emojis.
  - Ternaires devenus identiques après strip remédiés (markers distincts : `[fix]`, `[sem]`/
    `[lex]`, `[~]`/`[+]`, `[+]`/`[-]`), échelle de santé `dashboard` re-distinguée
    (`[ok]`/`[~]`/`[!]`/`[!!]`).
  - Correctif de parsing : `antifragile-score._count_contradictions` ne dépend plus d'un
    glyphe `⏳` supprimé (active = non-résolu).
  - Suite complète verte : 5996 passed, 4 skipped ; ruff clean.

### Corrigé (release/hygiène)

- **Pipeline release robuste** — `release.yml` génère désormais `RELEASE_NOTES.md` en
  best-effort depuis la trace puis **retombe systématiquement sur `git log`** si le fichier
  est absent ou vide (corrige l'échec v3.15.0 où `cat RELEASE_NOTES.md` plantait).
- **Artefacts générés dé-trackés** — `_grimoire-output/Grimoire_TRACE.md` et
  `_grimoire/_memory/*.sqlite3` retirés du suivi git et ajoutés au `.gitignore` (ils avaient
  été inclus par erreur via `git add -A` en v3.15.0, ce qui faussait le workflow release).

## [3.15.0] - 2026-06-26

### Supprimé / Changé

- **Nettoyage du layout legacy — résidu complet** — Tout le code fonctionnel et l'outillage sont débarrassés de l'ancien layout de modules :
  - `agent-lint.py` retargeté de l'ancien layout `*/agents/` vers `_grimoire/*/agents/` (variable `grimoire_dir`, manifeste, messages) ;
  - `observatory.py` ne supporte plus l'ancien layout de sortie / fichier de trace (Grimoire uniquement) ; tests alignés ;
  - `github-cc-check.yml.tpl` (framework + copie déployée) rebrandé « Grimoire Completion Contract », chemin `_grimoire/_config/custom/cc-verify.sh`, hint `grimoire init` ;
  - `bug-finder.py` ignore désormais `.grimoire-rnd` (ancien nom obsolète) ;
  - docstrings/commentaires/aides nettoyés : `grimoire-setup.py`, `agent-test.py`, `skill-validator.py` ;
  - `grimoire-completion.zsh` : suppression des alias legacy (`*-master`, `compdef`) ;
  - `.github/CODEOWNERS`, `.vscode/settings.json` (`git.branchPrefix`), `.vscode/snippets` (préfixes `grimoire-*`), `examples/web-app-todo`, `tests/smoke-test.sh`, `tests/run-coverage.sh`, `_grimoire/_memory/requirements-full.txt` : rebrand `_grimoire`.

## [3.14.0] - 2026-06-25

### Changé

- **Purge emoji — terminal & exemples docs (étape 2)** — `framework/tools/context-guard.py` : `status_icon`/`role_icon` renvoient des marqueurs texte maison (`[OK]`/`[WARN]`/`[CRIT]`, `[agent]`/`[mem]`…) au lieu d'emojis (un SVG ne s'affiche pas en terminal) ; `test_python_tools` aligné. Exemples docs `creating-agents`/`archetype-guide` : emojis `icon:` → noms d'icônes maison / texte.
- **Layout legacy retiré de l'outil shell** — `framework/tools/grimoire-setup.py` ne synchronise plus les modules legacy `{bmm,core,cis,tea,bmb}` (suppression `MODULE_CONFIGS`/`check_config_file`/`apply_config_file`) ; propage l'identité vers `project-context.yaml` + `.github/copilot-instructions.md`. `grimoire.sh` inchangé. `test_grimoire_setup` aligné. **Reste** : références legacy résiduelles dans ~10 autres outils framework (scanners) + emojis dans les `print()` framework — sweep dédié.

## [3.13.0] - 2026-06-25

### Changé

- **Layout legacy retiré de `grimoire setup` (SDK)** — `grimoire setup` ne synchronise plus les configs de modules legacy `{bmm,core,cis,tea,bmb}/config.yaml` (taxonomie legacy que le scaffold actuel ne crée plus) ; il propage l'identité utilisateur (source `project-context.yaml`) vers `.github/copilot-instructions.md` uniquement. Docstrings (`app.py`, `project.py`) et docs (getting-started, grimoire-yaml-reference, onboarding) nettoyés de la marque d'origine (« Master » d'origine → « Grimoire Master »). Noms de modules internes (bmm/core/cis/tea/bmb) conservés. **Reste à traiter** : l'outil shell legacy `framework/tools/grimoire-setup.py` (+ `grimoire.sh`, test non-CI) — décision standalone vs délégation SDK.

## [3.12.0] - 2026-06-25

### Changé

- **Icônes maison pour les champs `icon:` (zéro emoji) — étape 1 de la purge emoji** — les valeurs `icon:` des archétypes, agents et de la taxonomie `agent_forge` ne sont plus des emojis Unicode mais des **noms d'icônes maison** (réf `docs/assets/icons/*.svg` : `server`, `shield-pulse`, `sparkle`, `plug`, `flask`, `wrench`, `network`, `chart`, `clipboard`, `bolt`, `grimoire`, `hexagon`, `temple`, `microscope`, `lightbulb`, `boomerang`, `seal`). 16 fichiers DNA + taxonomie SDK & framework + tests alignés. Politique : aucun emoji Unicode, icônes maison uniquement.

## [3.11.5] - 2026-06-25

### Corrigé

- **Review documentation web — couverture du standard agentique** — `index.md`, `concepts.md` et `cli-reference.md` couvrent désormais le standard agentique gouverné (fonctionnalité clé, concept dédié, groupe de commandes `grimoire standard …`), jusqu'ici absent de toute la doc cœur malgré v3.5–v3.11. Arbre d'architecture corrigé (mémoire : Weaviate/Neo4j/Qdrant). Emojis de diagramme (✅/🔴) remplacés par marques typographiques (✓/✗). Build `mkdocs --strict` propre ; nav 36/36 sans orphelin ni lien cassé.

## [3.11.4] - 2026-06-25

### Corrigé

- **Icônes maison uniquement (zéro emoji Unicode)** — purge des emojis Unicode du README : marqueur expérimental → icône maison `flask.svg`, section SDK Python (`🐍`) → `server.svg`. Politique projet : aucun emoji Unicode dans la documentation, toutes les icônes sont des SVG maison (`docs/assets/icons/`).

## [3.11.3] - 2026-06-25

### Corrigé

- **Passe d'honnêteté + maturité sur le README** — marqueurs « expérimental » (icône maison flask) sur les features exploratoires (Session Branching, Agent Darwinism, Stigmergy, Dream Mode, R&D Engine, les 15+ avancées) + légende de maturité ; reformulation des claims sur-vendus (« blockchain légère » → journal **hash-chaîné** sha256 ; « reinforcement learning » → **bandit ε-greedy** ; « Protocole BFT » → quorum ; « intelligence émergente » → coordination émergente) ; mise en avant du **standard agentique gouverné** comme différenciateur mûr. Aucune feature retirée — toutes sont réelles et testées.

## [3.11.2] - 2026-06-25

### Corrigé

- **`framework/memory` : env var canonique `GRIMOIRE_*`** — la sélection de backend lit désormais `GRIMOIRE_QDRANT_URL`/`GRIMOIRE_OLLAMA_URL` (casse de l'écosystème) avec repli **rétro-compatible** sur l'ancienne casse `Grimoire_*` (helper `_env_url`). Corrige le non-respect silencieux des overrides d'environnement sans casser les setups existants. Couvert par `tests/unit/test_framework_memory_backends.py`.
- **Durcissement lint `framework/memory`** — chaînage `raise … from None` sur les ré-émissions d'`ImportError` (B904), nettoyage `F401`/`RUF013`/`F541`/`E401`, `E741` reporté (script legacy non testé). Les patterns de probing tolérant aux pannes (`S110`/`S310`) sont conservés intentionnellement.

## [3.11.1] - 2026-06-25

### Corrigé

- **Parcours getting-started complété** — ajout des sections « Adopter le standard agentique gouverné » (`grimoire standard needs/init/verify/audit/score/gate`) et « Portabilité multi-assistant » (entrypoints CLAUDE/AGENTS/GEMINI/.cursorrules + `.mcp.json`), absentes du guide de démarrage malgré les releases v3.5–v3.11.

## [3.11.0] - 2026-06-25

### Ajouté

- **Page de référence des contrôles gouvernés** (`docs/governed-controls.md`) — les 36 patterns regroupés par catégorie (intention, profil minimal, artefact, checks clés), **générée** depuis `pattern-catalog.yaml` via `docs/gen-governed-controls.py` (source unique, zéro drift) et ajoutée à la navigation. Test anti-drift `test_governed_controls_doc_covers_all_patterns` : tout pattern de `capability-map.yaml` doit être documenté. Comble le manque de documentation par-contrôle (jusqu'ici seulement dans les YAML).

## [3.10.2] - 2026-06-25

### Corrigé

- **Hygiène lint `framework/memory`** — corrections ruff *sans impact comportemental* (tri d'imports, f-strings sans placeholder, mode `open()` redondant) sur le bridge mémoire legacy. Les patterns intentionnels (probing backend `S110`/`S310`) et les items risqués à toucher en code non testé (`B904`/`E741`) restent en dette tracée.
- **Flag : convention d'env var `framework/memory`** — le code lit `Grimoire_*` (casse mixte), divergente de `GRIMOIRE_*` (reste de l'écosystème) → un override `GRIMOIRE_QDRANT_URL` n'y est pas pris en compte. Cohérent dans tout `framework/memory` (legacy, non testé) ; **non corrigé** (casserait les setups existants) — signalé en code + backlog.

## [3.10.1] - 2026-06-25

### Corrigé

- **Attribution de score des contrôles gouvernés** — les checks des contrôles ajoutés en v3.6–v3.8 sont désormais routés vers leur dimension de score naturelle (`compression.`→context_contract, `integrity.`→memory_policy, `cost.`→provider_policy, `council.`→decision_graph, `guardrail.`→rule_packs, `merge.`/`cluster.`/`env.`→ci_release_gate, `wsm.`/`flowdsl.`/`runtime.`/`k8s.`→orchestration_policy, `visual.`/`browser.`→evidence_gates, `privilege.`/`firewall.`/`workspace.`/`tools.blast_radius`→hook_registry, `promptver.`→observability_cockpit) au lieu du bucket générique `artifacts`. `grimoire standard score` reflète ainsi correctement ces contrôles. Aucun impact sur les profils par défaut (contrôles optionnels, non scaffoldés).

## [3.10.0] - 2026-06-25

### Ajouté

- **`.mcp.json` portable généré par `grimoire init`** — enregistre le serveur MCP Grimoire via l'entrypoint console `grimoire-mcp` (OS-neutre, aucun chemin absolu codé en dur), lu par Claude Code, Cursor et autres clients MCP. Complète l'adaptivité multi-assistant : le MCP fonctionne out-of-the-box après `pip install 'grimoire-kit[mcp]'`. Non écrasé s'il existe déjà.

## [3.9.0] - 2026-06-25

### Ajouté

- **Entrypoints multi-assistant portables** — `grimoire init` génère désormais, à côté de `.github/copilot-instructions.md`, des entrypoints `CLAUDE.md` (Claude Code, via import `@`), `AGENTS.md` (standard cross-tool : Codex et autres), `GEMINI.md` (Gemini CLI) et `.cursorrules` (Cursor), tous pointant vers le fichier canonique (source unique, zéro drift, non écrasés s'ils existent). Un projet Grimoire fonctionne ainsi avec Copilot, Claude, Codex, Gemini et Cursor sans configuration manuelle. Comble le gap d'adaptivité multi-assistant (jusqu'ici Copilot/VS Code-first + MCP uniquement).

## [3.8.0] - 2026-06-25

### Ajouté

- **2 contrats déclaratifs (clôture du backlog déclaratif)** — `workflow-state-manifest` (machine à états de mission durable : états, transitions gardées, interrupts ; exécution déléguée à LangGraph/Conductor) et `k8s-agent-manifest` (contrat K8s déclaratif : CRD, resource limits, network allowlist, service account, OTel ; provider natif délégué à kagent). Catalogue de patterns **34 → 36** ; **`planned_capabilities` désormais vide** — tout le déclarable est implémenté, ne restent que les adapters runtime externes (LangGraph, kagent).

### Corrigé

- **README à jour** — badge de version corrigé (3.1.0 → 3.8.0) et ajout de la section « Standard agentique gouverné » (profils, 36 patterns, `verify`/`audit`/`score`/`gate`) qui manquait totalement malgré les releases v3.5–v3.7.
- **CITATION.cff** — version et date alignées (3.1.0/2025 → 3.8.0/2026).

## [3.7.0] - 2026-06-25

### Ajouté

- **8 contrats déclaratifs (lot benchmark v3.7)** — concrétisation des capacités `planned_capabilities` purement déclaratives, recette `capability-map` + template + `_verify_*` fail-closed + test : `workspace-isolation`, `policy-by-environment`, `browser-tool-contract`, `runtime-provider-contract`, `prompt-version-observability`, `cluster-action-dry-run`, `doc-to-graph-pipeline`, `flow-dsl-minimal`. Catalogue de patterns **26 → 34**. Chaque template vérifie *clean* (test paramétré `test_control_template_verifies_clean`). Promotion `planned_capabilities` → `mapped_capabilities` dans les profils concernés (controlled/orchestrated/governed/production) ; restent en `planned` les 2 sous-systèmes à adapter externe (`workflow-state-engine`/LangGraph, `kubernetes-agent-control-plane`/kagent).

## [3.6.1] - 2026-06-25

### Corrigé

- **Cohérence capability-map ↔ profils** — `mapped_capabilities` de chaque profil ne référence plus que des patterns réels ; les capacités encore non implémentées sont déplacées dans un nouveau champ `planned_capabilities`. Les 11 contrôles v3.6.0 sont rattachés aux bons profils (ex. `agent-privilege-boundary`/`decision-council-gate` → governed, `prompt-injection-firewall`/`guardrail-contract` → controlled). Test garde-fou `test_profile_mapped_capabilities_are_real_patterns` (mapped ⊆ patterns ; planned ∩ patterns = ∅) — l'incohérence ne peut plus réapparaître.
- **Vérificateur Completion Contract (`framework/cc-verify.sh`)** — résout désormais l'interpréteur du virtualenv projet (`.venv/bin/python`) pour pytest/ruff, avec fallback PATH et saut gracieux si indisponible (corrige un `ModuleNotFoundError` bloquant quand pytest n'est pas installé globalement).

## [3.6.0] - 2026-06-25

### Ajouté

- **11 contrôles gouvernés benchmark-driven** — issus de la comparaison avec le corpus agentique de référence (37 projets), concrétisant des capacités jusqu'ici seulement nommées dans `profile-map.yaml` : `tool-blast-radius-limiter`, `agent-privilege-boundary` (ScrubTokenEnv controller/agent), `prompt-injection-firewall` (GOV-12), `remote-hygiene-guard` (GOV-13), `decision-council-gate` (GOV-14), `context-compression-gate`, `memory-integrity-validator`, `merge-lane-fault-classifier`, `llm-cost-registry` (coût + SLO CrashRate/UnhealthyRate), `guardrail-contract` (input/output/tool/model versionnés), `visual-evidence-gate` (QUA-12). Chacun : pattern (`capability-map.yaml` + `pattern-catalog.yaml`), artefact + template, vérification `_verify_*` fail-closed dans `grimoire standard verify`. Catalogue de patterns 15 → 26.
- **Benchmark corpus & matrice d'écarts** — `docs/agentic-standard-benchmark-corpus-2026Q2.md` (22 patterns + 15 contrôles cibles vs couverture réelle) et `docs/travaux-inacheves-2026Q2.md` (backlog priorisé : v3.7.0+, Memory OS, R&D à porter, dette repo, branches/PR en attente).
- **Rampe « commencer petit » pour l'installation par besoins** — le `needs-catalog.yaml` est désormais **tiéré** (`essential` / `advanced` / `enterprise`) avec un besoin de départ recommandé (`solo-prototyping`, marqué `▶`). `grimoire standard needs` regroupe les besoins par tier et affiche leur **empreinte** (profil · nombre de patterns · nombre de services externes) ; `grimoire standard needs --explain` révèle à la demande les patterns derrière chaque besoin (divulgation progressive). L'assistant `standard init --interactive` ordonne les besoins essentiels d'abord et pré-sélectionne le besoin recommandé (Entrée = recommandé). Documentation : section « Commencer petit (rampe progressive) » dans `docs/agentic-standard-install-by-needs.md`.

### Changé

- **Défaut minimal de `grimoire standard init`** — sans `--needs`/`--profile`, l'init scaffolde désormais le profil **`starter`** (au lieu de `orchestrated`), avec un rappel pour choisir par besoin. Le comportement résolu via `--needs`/`--pattern` est inchangé.

## [3.5.0] - 2026-06-08

### Ajouté

- **Installation par besoins** — nouvelle couche d'installation custom : `grimoire standard needs`, `standard plan --needs ...`, `standard init --needs/--pattern/--memory/--interactive` et `standard doctor`. Deux fichiers déclaratifs (`framework/agentic-standard/capability-map.yaml`, `needs-catalog.yaml`) résolvent un besoin projet en profil + patterns + artefacts + extras technologiques, et écrivent un `install-manifest.yaml` auditable. Auto-install des extras opt-in via `--install-extras`.
- **Parité patterns R8/R9/R10** — back-port dans les templates Kit des patterns `redis-hot-memory-soft-gate`, `governed-hook-gateway`, `skill-classification-matrix`, `governed-observability-cockpit`, des familles de règles `hooks`/`skills`/`observability`, du contrat `observability-policy.yaml`, de la taxonomie `managed_sources` et de la dimension de score `observability_cockpit`.
- **Catalogue de patterns étendu (9 → 15)** — ajout de `code-graph-projection` (neo4j), `governed-agent-orchestration`, `governed-knowledge-indexing`, `mission-evidence-ledger`, `tool-mediation-gate` (mcp) et `provider-cost-slo`, câblés dans `capability-map.yaml` et `needs-catalog.yaml`.
- **Memory OS cible** — portage du socle Weaviate + Neo4j + SQLite sidecar, migration Qdrant -> Weaviate/Neo4j, projections graph/vector, commandes `grimoire memory graph`, `memory vector`, `memory gate` et noyaux missions/evidence/policies/runtime/traces/bridges/evals.
- **Standard Memory OS** — `grimoire standard init/verify/audit/score/gate` vérifie maintenant un contrat Memory OS cible : Redis hot memory, Weaviate mémoire sémantique durable, Neo4j projection graphe, SQLite sidecar/fallback et Qdrant en source legacy/migration uniquement.

### Changé

- **Détection mémoire** — `grimoire init --backend auto` privilégie désormais Weaviate quand il est disponible localement, conserve Qdrant comme fallback compatible (`qdrant-local`), puis Ollama et le backend local.

## [3.4.4] - 2026-05-29

### Corrigé

- **CI SDK multi-OS** — stabilisation complète de `Grimoire SDK CI` : assertions CLI robustes face aux rendus Typer/Rich, couverture agentic standard incluse, smoke Windows ciblé et workflow de tests portable.
- **Runtime standard** — sorties JSON et tests du runtime agentique rendus portables entre Linux, macOS et Windows, notamment les chemins `context`/`knowledge`.
- **Release readiness** — correction ShellCheck, test de backoff déterministe et durcissement des tests d’édition de configuration pour débloquer la publication PyPI.

## [3.4.3] - 2026-05-28

### Ajouté

- **Agentic Standard Bridge** — profils `minimal`, `orchestrated` et `governed`, génération des artefacts ISO/design-pattern, vérification/audit CLI et baseline de preuves.
- **Provider onboarding** — détection non-secrète des providers, activation explicite via `standard init --provider/--providers`, politiques `hosted-safe`, `local-first` et `mixed`.
- **Package npm préparé** — launcher `grimoire-kit` ajouté, publication npm différée en attendant l’authentification npm dédiée.

### Corrigé

- **Sécurité standard** — durcissement des chemins générés, rejet des `task_id` traversants, confinement des locators knowledge locaux et échappement des valeurs projet injectées dans les templates.
- **CI/Docs** — workflow ciblé agentic standard, documentation d’extension des profils et pin explicite du bridge consommé par Forge.

## [3.4.2] - 2026-03-30

### Corrigé

- **Init mémoire: durcissement non-interactif** — la réutilisation auto d'un setup détecté valide désormais la reachability (Qdrant/Ollama) avant sélection backend.
- **Secrets: non persistance dans project-context.yaml** — `qdrant_api_key` détectée n'est plus écrite automatiquement dans la configuration projet; usage recommandé via variable d'environnement.
- **Init YAML: échappement des remplacements sed** — les URLs injectées sont échappées pour éviter la corruption YAML quand des caractères spéciaux sont présents.
- **Backend Qdrant: compat env vars** — prise en charge de `GRIMOIRE_QDRANT_API_KEY` en plus de `Grimoire_QDRANT_API_KEY`.
- **README: rendu architecture GitHub** — suppression du wrapper HTML autour du diagramme Mermaid pour un rendu fiable sur GitHub.

### Ajouté

- **CLI: A1 — `--debug` / `-D` flag global** — Expose GRIMOIRE_DEBUG en flag CLI (à la ruff/uv). Fonctionne aussi via `GRIMOIRE_DEBUG=1` env var. Active les tracebacks complets via Rich. Message d'erreur mis à jour : « Use --debug or set GRIMOIRE_DEBUG=1 » (Round 37)
- **CLI: A5 — détection env vars conflictuelles** — La commande `env` détecte et signale les combinaisons incohérentes (ex: GRIMOIRE_DEBUG + GRIMOIRE_QUIET). Affiché en texte et JSON (champ `conflicts`) (Round 37)
- **Tests: +12** — R37 : DebugFlag (4) + OnlineDNS (2) + RepairAuditTrim (2) + ConfigSetExitCode (1) + EnvConflicts (3) → 373 tests CLI (Round 37)

### Corrigé

- **CLI: A2 — `repair` audit trim race condition** — Même pattern que R36-F2 : `splitlines()` + `write_text()` remplacés par lecture ligne par ligne + `writelines()` propre (Round 37)
- **CLI: A3 — `config set` exit codes sémantiques** — `config set` utilisait `Exit(1)` pour key-not-found alors que `_resolve_config_key` utilise `_EXIT_CONFIG=2`. Cohérence rétablie (Round 37)
- **CLI: A4 — `_is_online` DNS-based** — Remplace le socket brut vers 1.1.1.1:53 par une résolution DNS (`getaddrinfo`) en premier, avec fallback socket. Fonctionne derrière proxy/firewall corporate (Round 37)

### Précédent (Round 36)

#### Ajouté

- **CLI: E6 — exit codes sémantiques** — Constantes `_EXIT_OK=0`, `_EXIT_USER=1`, `_EXIT_CONFIG=2`. Appliquées à `_resolve_config_key` (key not found) et `GrimoireConfigError` dans status. Prêtes pour migration progressive (Round 36)
- **Tests: +13** — R36 : ExitCodeConstants (3) + LogOperationTruncate (1) + MergeCommand (5) + PluginsList (4) → 361 tests CLI (Round 36)
- **CLI: I2 — `history --clear`** — Nouveau flag `--clear` pour purger l'audit log avec confirmation (ou `--yes` pour skip). Supporte JSON output (Round 35)
- **Tests: +12** — R35 review complète : GetFmtHelper (3) + DoctorFixAudit (2) + DoctorJsonOptionals (2) + CompletionInstallAudit (1) + HistoryClear (4) → 348 tests CLI (Round 35)

### Corrigé

- **CLI: F2 — `_log_operation` truncate race condition** — Le truncate utilisait `splitlines()` + `write("\n".join(...))` en deux opérations distinctes. Remplacé par `readlines()` + `writelines()` + `truncate()` dans un seul handle (Round 36)
- **CLI: F1 — `doctor --fix` sans audit trail** — Les commandes mutatives loguent toutes via `_log_operation` sauf `doctor --fix`. Ajout de l'appel audit quand des répertoires sont créés (Round 35)
- **CLI: I4 — `doctor --json` omet les optionnels manquants** — Les packages optionnels non installés n'apparaissaient pas dans le JSON. Ils sont maintenant inclus avec `"optional": true` (Round 35)
- **CLI: I5 — `completion install` sans audit trail** — Commande mutatrice (écrit dans ~/.bashrc/.zshrc) sans trace. Ajout `_log_operation("completion_install", {"shell": shell})` (Round 35)
- **CLI: H4 — DRY output format** — Le pattern `(ctx.obj or {}).get("output", "text")` était répété 26× dans le code. Extrait helper `_get_fmt(ctx)` (Round 35)

### Précédent (Round 34)

#### Ajouté

- **Tests: +6** — R34 lint global output + env enrichment : LintGlobalOutput (2) + NullcontextImport (1) + EnvVarsComplete (3) → 336 tests CLI (Round 34)

#### Corrigé

- **CLI: H1 — lint ignore le flag global `-o`** — Seule des ~25 commandes, `lint` utilisait `--format/-f` au lieu de `ctx.obj["output"]`. Ajout de `ctx: typer.Context` ; `--format` reste comme fallback rétrocompat (Round 34)
- **CLI: H2 — `_status_spinner` lazy import** — `contextlib` importé localement alors que `nullcontext` peut être importé au top-level. Remplacé par import direct (Round 34)
- **CLI: H3 — `env` ne montre que 2/6 env vars** — Manquait GRIMOIRE_OUTPUT, GRIMOIRE_QUIET, GRIMOIRE_OFFLINE, NO_COLOR. Ajouté les 4 (Round 34)
- **CLI: I1 — `env` sans statut réseau** — `env` est utilisé pour le debug et les bug reports. Ajout de `is_online()` dans la sortie text et JSON (Round 34)

### Précédemment (Round 33)

#### Ajouté

- **Tests: +10** — R33 DRY refactors + history enhancement : CompletionDRY (4) + ConfigKeyResolver (3) + HistoryVersionColumn (3) → 330 tests CLI (Round 33)

#### Corrigé

- **CLI: H1+H4 — DRY completion** — `completion_install` et `completion_export` partageaient ~15 lignes identiques (subprocess + validation). Extrait helper `_generate_completion_script(shell)` + constante `_SUPPORTED_SHELLS = frozenset({"bash", "zsh", "fish"})` (Round 33)
- **CLI: H2 — DRY config traversal** — `config_show` et `config_get` partageaient 28 lignes de traversée dot-notation YAML. Extrait helper `_resolve_config_key(data, key)` (Round 33)
- **CLI: H3 — history sans colonne version** — `history` n'affichait pas le champ `"v"` ajouté en R32. Ajout colonne « Version » dans la table Rich (fallback « — » pour anciennes entrées) (Round 33)
- **CLI: I1 — history total_entries** — En mode JSON, `history` n'exposait que le nombre filtré (`total`). Ajout de `total_entries` (total brut du fichier) (Round 33)

### Précédemment (Round 32)

#### Ajouté

- **Tests: +9** — R32 audit & housekeeping : AuditVersionField (2) + RepairAuditLog (2) + SetupAuditLog (1) + DoctorNumbering (1) + SelfVersionImport (1) + CompletionParentDir (2) → 320 tests CLI (Round 32)

#### Corrigé

- **CLI: I1 — Audit log sans version** — `_log_operation()` n'incluait pas la version de grimoire-kit. Ajout d'un champ `"v": __version__` dans chaque enregistrement JSONL (Round 32)
- **CLI: H1 — repair sans audit** — `repair` ne loguait pas dans l'audit trail. Ajout de `_log_operation("repair", …)` après actions non-dry-run (Round 32)
- **CLI: H2 — setup sans audit** — `setup` ne loguait pas dans l'audit trail. Ajout de `_log_operation("setup", …)` après apply dans les deux chemins sync/override et défaut (Round 32)
- **CLI: H4 — doctor numérotation cassée** — Les commentaires de checks sautaient de 3 à 5 (check 4 supprimé sans renuméroter). Renuméroté séquentiellement 1→8 (Round 32)
- **CLI: H5 — self_version import redondant** — `self_version` faisait `import json as _json` localement alors que `json` est importé au niveau module. Supprimé en faveur de `json.loads()` (Round 32)
- **CLI: H6 — completion parent dir manquant** — `completion install` pour bash/zsh n'appelait pas `mkdir(parents=True)` sur le répertoire parent du fichier RC cible. Ajouté (fish l'avait déjà) (Round 32)

### Précédemment (Round 31)

#### Ajouté

- **Tests: +8** — R31 review fixes : EnvCmdNarrowException (2) + VersionCmdGrimoireError (1) + InterruptedRemoved (2) + CtxObjGuard (1) + SetupGlobalJson (2) → 311 tests CLI (Round 31)

#### Corrigé

- **CLI: C1 — env_cmd exception trop large** — `env` catchait `(typer.Exit, Exception)` masquant tout. Réduit à `(typer.Exit, GrimoireError)` — même correctif que R30 M4 sur `version_cmd` (Round 31)
- **CLI: C2 — version_cmd rate GrimoireProjectError** — `version` catchait `GrimoireConfigError` mais pas `GrimoireProjectError` (classes sœurs). Élargi à `GrimoireError` (base commune) (Round 31)
- **CLI: H1 — _interrupted dead variable** — Le flag `_interrupted` était set par `_handle_signal` mais jamais lu (`SystemExit` raised immédiatement). Supprimé (Round 31)
- **CLI: H2 — ctx.obj guard inconsistant** — 8 commandes utilisaient `ctx.obj.get()` sans guard None alors que d'autres utilisaient `(ctx.obj or {}).get()`. Standardisé vers le pattern sûr partout (Round 31)
- **CLI: H3 — setup ignore -o json global** — `setup` avait un flag `--json` dédié mais ignorait le flag global `-o json`. Ajout de `ctx: typer.Context` et unification : les deux méthodes fonctionnent (Round 31)

### Précédemment (Round 30)

#### Ajouté

- **Tests: +13** — R29 review fixes : EditorValidation (2) + SuggestIncludesAliases (1) + FlattenLists (3) + RequiredDirsConstant (2) + AuditLogAtomic (1) + HistorySkipCount (1) + RepairJsonOk (1) + VersionEnvFindConfig (2) → 294 tests CLI (Round 29)

#### Corrigé

- **CLI: C1 — Audit log race condition** — `_log_operation()` avait un TOCTOU entre `read_text()` et `write_text()` pour la troncation du log. Remplacé par un mode `r+` atomique (seek + truncate dans le même file handle) (Round 29)
- **CLI: C2 — Editor validation** — `config edit` appelait `os.execvp()` sans vérifier l'existence de l'éditeur. Ajout de `shutil.which()` avec suggestion `$VISUAL/$EDITOR` si absent (Round 29)
- **CLI: H3 — version/env hardcoded path** — `version` et `env` utilisaient `Path.cwd() / "project-context.yaml"` au lieu de `_find_config()`, ne fonctionnaient pas depuis un sous-répertoire (Round 29)
- **CLI: H4 — _flatten ignore lists-of-dicts** — `_flatten()` traitait les listes de dicts comme des valeurs opaques. Ajout de la récursion avec clés indexées : `repos.0.name`, `repos.1.path` (Round 29)
- **CLI: H8 — Aliases absent des suggestions** — `_suggest_command()` ne considérait que les commandes enregistrées, pas les alias. Ajout de `_KNOWN_COMMANDS.update(_ALIASES)` (Round 29)
- **CLI: H9 — DRY violation répertoires** — 8+ occurrences de tuples `("_grimoire", "_grimoire-output")` hardcodés. Extraction en constantes module `_REQUIRED_DIRS` + `_MEMORY_DIR` (Round 29)
- **CLI: M10 — config set acceptait des listes** — `config set` splittait les virgules pour créer des listes, comportement error-prone. Remplacé par un refus explicite avec guidance vers `config edit` (Round 29)
- **CLI: M13 — _log_operation muet sur erreur** — Le handler `OSError` ignorait silencieusement les erreurs. Ajout d'un avertissement console quand `GRIMOIRE_DEBUG` est défini (Round 29)
- **CLI: M14 — history ignore les entrées corrompues** — `history` sautait silencieusement les lignes JSONL invalides. Ajout d'un compteur `skipped` (affiché en texte et en JSON) (Round 29)
- **CLI: M15 — repair JSON manquait ok** — La sortie JSON de `repair` n'incluait pas le champ `"ok": true` contrairement aux autres commandes (Round 29)

### Précédemment (Round 28)

#### Ajouté

- **CLI: `grimoire config edit`** — Ouvre `project-context.yaml` dans `$VISUAL` / `$EDITOR` / `vi` (Round 28)
- **CLI: `grimoire config validate`** — Validation du schema config en place, JSON output `{valid, warnings}`, exit code 1 si invalide (Round 28)
- **CLI: `--profile` sur `check`** — 3 phases instrumentées : `check/lint`, `check/validate`, `check/structure` (Round 28)
- **Tests: +20** — R28 review fixes (13) + config edit (3) + config validate (4) → 281 tests CLI (Round 28)

### Corrigé

- **CLI: C1 — Audit filename in repair** — `repair` utilisait `"audit.jsonl"` au lieu de `_AUDIT_FILENAME` (`.grimoire-audit.jsonl`), le trimming du log ne fonctionnait jamais (Round 28)
- **CLI: C3 — Latence _is_online()** — Suppression du probe réseau 500ms dans le callback `main()` exécuté à chaque commande. Remplacé par `is_online()` lazy (cache une fois par process) (Round 28)
- **CLI: C4 — Config commands depuis subdirectory** — Les 5 commandes config (`show`, `get`, `path`, `set`, `list`) utilisaient un path hardcodé au lieu de `_find_config()` (Round 28)
- **CLI: C5 — Accumulation phase timings** — `_phase_timings` module-level jamais vidé entre invocations. Ajout de `.clear()` dans `cli()` (Round 28)
- **CLI: H6 — Newline échappé** — `\\n` dans l'affichage `--time` au lieu de `\n` (Round 28)
- **CLI: H7 — self version offline** — `self version` n'utilisait pas le flag offline, probe PyPI inutile quand hors-ligne (Round 28)
- **CLI: M5 — `_flatten` dupliqué** — Suppression de `_flatten_dict()` redondant, réutilisation de `_flatten()` dans `config list` (Round 28)

- **CLI: Command suggestions** — `_suggest_command()` détecte les fautes de frappe et propose des commandes proches via `difflib.get_close_matches()` (Round 27)
- **CLI: Signal handling** — Gestion propre de SIGINT/SIGTERM avec message et code de sortie Unix standard (128+signal) (Round 27)
- **CLI: `--profile` flag** — Breakdown timing par phase avec arbre Rich (`_timed_phase` context manager), instrumenté sur `doctor` (Round 27)
- **CLI: `grimoire repair`** — Auto-réparation des problèmes courants : création répertoires manquants, trim du audit log >90j, `--dry-run`, JSON output (Round 27)
- **CLI: Offline mode detection** — `_is_online()` avec test de connectivité rapide, `GRIMOIRE_OFFLINE=1` env var, `ctx.obj["offline"]` flag (Round 27)
- **Tests: +21** — TestCommandSuggestions (4) + TestSignalHandling (3) + TestPerformanceProfiling (4) + TestRepairCommand (6) + TestOfflineMode (4) → 261 tests CLI (Round 27)
- **CLI: Config auto-discovery** — `_find_config()` remonte l'arborescence pour trouver `project-context.yaml` quand on est dans un sous-répertoire (Round 26)
- **CLI: Rich spinners** — `_status_spinner()` affiche un spinner animé sur `upgrade` et `merge` (respecte `--quiet` et `-o json`) (Round 26)
- **CLI: Exemples dans l'aide** — Rich markup examples ajoutés aux docstrings de 8 commandes : init, doctor, validate, add, remove, status, check, upgrade (Round 26)
- **CLI: `grimoire history`** — Audit trail des opérations CLI récentes avec `--limit`, `--filter`, JSON output (`_grimoire/_memory/.grimoire-audit.jsonl`) (Round 26)
- **CLI: Audit log** — `_log_operation()` trace automatiquement init, add, remove, config_set, upgrade, merge dans un fichier JSONL (Round 26)
- **CLI: Deprecation framework** — `_DEPRECATED_FLAGS` dict + `_warn_deprecated()` pour gérer proprement les flags obsolètes dans les futures versions (Round 26)
- **Tests: +28** — TestAutoDiscovery (4) + TestSpinnerHelper (2) + TestSubcommandExamples (8) + TestAuditLog (4) + TestHistoryCommand (5) + TestDeprecationWarnings (3) + TestAuditIntegration (2) → 995 tests CLI (Round 26)
- **CLI: `--yes/-y` global flag** — Skip les confirmations interactives sur `remove` et `merge --undo` ; implicite en mode JSON (Round 25)
- **CLI: Confirmations interactives** — `remove` et `merge --undo` demandent confirmation avant toute action destructive (Round 25)
- **CLI: JSON output `upgrade`** — Sortie JSON structurée `{ok, version, dry_run, warnings, actions}` (Round 25)
- **CLI: Rich help panels** — Commandes organisées par catégorie : Project, Agents, Validation, Configuration, Utilities, Info (Round 25)
- **CLI: Error handler amélioré** — Affichage du code d'erreur et suggestions de récupération (`_format_error`, `_RECOVERY_HINTS`) (Round 25)
- **Tests: conftest.py CLI** — Fixtures `cli_project` et helper `assert_json_output` pour réduire la duplication de tests (Round 25)
- **Tests: +23** — TestYesFlag (5) + TestUpgradeJson (4) + TestHelpPanels (6) + TestErrorHandler (4) + TestConftestFixtures (3) + TestEpilog (1) → 967 tests (Round 25)
- **CLI: JSON output `init`** — Sortie JSON structurée `{ok, project, path, archetype, backend, directories}` (Round 24)
- **CLI: JSON output `up`** — Sortie JSON structurée `{ok, project, actions, dry_run, agents_count}` (Round 24)
- **CLI: `doctor --fix`** — Auto-correction des répertoires manquants avec rapport `fixed` en JSON (Round 24)
- **CLI: `--time` flag** — Affiche le temps d'exécution en ms après chaque commande (Round 24)
- **Tests: +19** — TestInitJson (3) + TestUpJson (2) + TestDoctorFix (4) + TestTimeFlag (2) + TestJsonOutputParametrized (8) → 944 tests (Round 24)
- **CLI: `config set KEY VALUE`** — Modification de clé config par dot-notation avec coercion de type, `--dry-run`, JSON (Round 23)
- **CLI: JSON output `add`/`remove`** — Sortie JSON structurée `{ok, action, agent}` pour scripting CI/CD (Round 23)
- **CLI: "Did you mean?" sur init** — Suggestions fuzzy pour archétypes et backends mal typés (Round 23)
- **Tests: +16** — TestConfigSet (7) + TestAddRemoveJson (6) + TestDidYouMean (3) → 925 tests (Round 23)
- **CLI: Command aliases** — Raccourcis courts : `i`=init, `d`=doctor, `s`=status, `v`=validate, `l`=lint, `ck`=check, `u`=up, `c`=config, `r`=registry (Round 22)
- **CLI: Env var overrides** — `GRIMOIRE_OUTPUT=json`, `GRIMOIRE_QUIET=1`, `NO_COLOR=1` pour scripting/CI sans flags (Round 22)
- **CLI: `add --dry-run` / `remove --dry-run`** — Flag `-n/--dry-run` sur les commandes add/remove pour prévisualiser sans modifier (Round 22)
- **CLI: `check` phases structurées** — Helper `_phase_header()` avec support `--quiet` pour une sortie plus propre (Round 22)
- **Tests: +16** — TestCommandAliases (5) + TestAddRemoveDryRun (7) + TestEnvVarOverrides (4) → 909 tests (Round 22)
- **CLI: `grimoire version`** — Commande standalone avec version, Python, plateforme, projet actif (text/JSON) (Round 21)
- **CLI: `grimoire self version`** — Version installée + vérification de mise à jour PyPI (text/JSON) (Round 21)
- **CLI: `grimoire self diagnose`** — Auto-diagnostic : dépendances, Python, entry point, statut global (text/JSON) (Round 21)
- **CLI: `grimoire config get KEY`** — Lecture d'une clé config par dot-notation (text/JSON) (Round 21)
- **CLI: `grimoire config path`** — Affiche le chemin résolu vers project-context.yaml (Round 21)
- **CLI: `grimoire config list`** — Liste toutes les clés config avec valeurs actuelles en table Rich (text/JSON) (Round 21)
- **CLI: Epilog Rich** — Exemples et aide rapide dans `grimoire --help` (Round 21)
- **Validator: détection clés inconnues** — Avertissements pour clés non reconnues avec suggestions "Did you mean?" (Round 21)
- **Tests: +27** — TestVersionCommand (3) + TestConfigGet (4) + TestConfigPath (2) + TestConfigList (3) + TestSelfVersion (2) + TestSelfDiagnose (3) + TestEpilog (1) + TestUnknownKeys (9) → 893 tests (Round 21)
- **CLI: `completion export`** — Export script de complétion vers stdout pour piping/dotfiles (Round 20)
- **CLI: Rich traceback** — Stack traces Rich avec `show_locals=True` quand `GRIMOIRE_DEBUG=1` (Round 20)
- **Makefile: `release`** — Target `make release VERSION=x.y.z` : bump, build, instructions tag (Round 20)
- **Makefile: `bench`** — Target `make bench` pour benchmarks de performance (Round 20)
- **Makefile: `audit`** — Target `make audit` pour pip-audit de sécurité (Round 20)
- **Tests: fixture `init_project`** — Fixture partagée dans conftest.py pour projets pré-initialisés (Round 20)
- **Tests: +8** — TestCompletionExport (3) + TestDoctorFixture (5) → 866 tests (Round 20)
- **pyproject.toml: marker `bench`** — Nouveau marker pytest pour les tests de performance (Round 20)
- **CLI: `--quiet` / `--no-color`** — Flags globaux pour le scripting et l'intégration CI (Round 19)
- **CLI: JSON output `doctor`** — Sortie JSON structurée pour `grimoire doctor` avec checks détaillés (Round 19)
- **CLI: JSON output `validate`** — Sortie JSON pour `grimoire validate` : `{valid, errors, count}` (Round 19)
- **CLI: JSON output `check`** — Sortie JSON pour `grimoire check` avec phases détaillées (Round 19)
- **Docs: section "JSON scripting"** — Tableau récapitulatif de toutes les commandes avec support JSON (Round 19)
- **Tests: +24** — TestDoctorJson (3) + TestValidateJson (3) + TestQuietNoColor (4) + TestCheck JSON (2) + TestSchema core (12) → 858 tests (Round 19)
- **CLI `grimoire schema`** — Export JSON Schema Draft 2020-12 pour `project-context.yaml` (validation IDE et CI) (Round 18)
- **CLI `grimoire check`** — Commande compound : lint + validate + structure check en une passe (Round 18)
- **Core: `__all__` exports** — Ajout de `__all__` dans `config.py`, `validator.py`, `project.py`, `schema.py` (Round 18)
- **Core: `schema.py`** — Nouveau module `grimoire.core.schema` : générateur JSON Schema depuis la structure config (Round 18)
- **Tests: +10** — TestSchema (5 cas) + TestCheck (5 cas) → 834 tests unitaires (Round 18)
- **CLI JSON output** — Sortie JSON (`-o json`) pour `status`, `registry list`, `registry search` (Round 17)
- **Ruff: +4 catégories** — Ajout FLY (f-string), FURB (refurb), RSE (raise), ERA (dead code) → 20 catégories
- **GitHub: Issue templates** — Ajout `docs-improvement.yml` et `performance-regression.yml`
- **Docs: ADR-002 SemVer** — Architecture Decision Record sur la politique de versionnage et stabilité API
- **SECURITY.md enrichi** — Classification de sévérité, processus de divulgation, scope, timeline
- **CLI `grimoire lint`** — Commande de lint YAML avancée : validation structure, types, contraintes et références (sortie text/JSON)
- **CLI `grimoire diff`** — Affiche le drift de config entre le projet et les défauts de l'archétype (sortie text/JSON)
- **CLI `init --dry-run`** — Flag `--dry-run` sur `grimoire init` pour prévisualiser sans écrire
- **GitHub: Release Drafter** — Workflow `release-drafter.yml` + config : génère automatiquement les notes de release à partir des PRs
- **GitHub: Stale issue closer** — Workflow `stale.yml` : ferme automatiquement les issues/PRs inactives (60j stale + 14j close)
- **GitHub: PR auto-labeler** — Workflow `auto-label.yml` + `labeler.yml` : labellise automatiquement les PRs selon les fichiers modifiés
- **Docs: API reference mkdocstrings** — Autodoc Python intégrée dans mkdocs via `mkdocstrings[python]`
- **Docs: Référence config** — Page `docs/config-reference.md` : toutes les clés, types, défauts, valeurs valides, variables d'environnement
- **Docs: Plugin Development Guide** — Page `docs/plugin-development.md` : création d'outils, backends, archétypes, entry points
- **mypy étendu aux tests** — `[[tool.mypy.overrides]]` pour tests/ avec relaxation `disallow_untyped_defs`
- **Ruff PERF102** — Fix `.items()` → `.values()` dans 2 fichiers de tests
- **Ruff: 3 catégories de règles** — Ajout PIE (misc), PERF (performance), LOG (logging) au linter
- **Public API enrichie** — `GrimoireProject` exporté dans `grimoire.__init__` aux côtés de `GrimoireConfig` et `GrimoireError`
- **Docs: Référence CLI** — Page `docs/cli-reference.md` : toutes les commandes, flags, options, variables d'environnement
- **Docs: FAQ** — Page `docs/faq.md` : installation, backends, agents, plugins, migration, dépannage
- **Tests lint** — 7 tests (no config, valid, JSON valid, JSON invalid, direct YAML, invalid config, help)
- **Tests init --dry-run** — 6 tests (plan affiché, aucun fichier créé, validation archetype/backend)
- **Tests diff** — 5 tests (no config, fresh project, JSON output, archetype, help)
- **CI: Dependency audit** — Job `pip-audit --strict --desc` dans `ci-sdk.yml` pour détecter les CVE dans les dépendances
- **CI: Codecov upload** — Upload automatique de `coverage.xml` vers Codecov avec `codecov-action@v5`
- **CI: Cross-platform** — Matrice étendue à `ubuntu-latest`, `windows-latest`, `macos-latest` (Win/Mac sur Python 3.12)
- **CI: SBOM CycloneDX** — Génération SBOM JSON (`sbom.cdx.json`) dans le workflow publish, attaché aux releases GitHub
- **CLI `grimoire config show`** — Commande lecture de config (YAML complet ou clé dot-notation) avec sortie text/JSON
- **CLI `grimoire completion install`** — Installation automatique shell completion (bash/zsh/fish)
- **Tests integration** — Nouveau répertoire `tests/integration/` avec 12 tests end-to-end (init→doctor, config show, env+plugins flow)
- **Tests config + completion** — 10 tests unitaires pour `config show` (dot-key, JSON, missing, help) et `completion install`
- **pyproject.toml URLs** — Ajout Changelog + Issues dans `[project.urls]` pour PyPI
- **`GrimoireConfig.validate()`** — Méthode de validation sémantique : détecte les incohérences config (backend sans URL, nom vide)
- **CLI `grimoire plugins list`** — Commande listant les plugins installés (tools + backends) avec sortie text/JSON
- **Doctor amélioré** — 3 nouveaux checks : validation config, dépendances optionnelles (qdrant/ollama/mcp), version Python
- **Tests config validation** — 6 tests pour `GrimoireConfig.validate()` (warnings qdrant, ollama, blank name, cas valides)
- **Tests env + plugins** — 12 tests pour `grimoire env` (text/JSON) et `grimoire plugins list` (text/JSON/mocked)
- **`__all__` corrigés** — CLI exporte `["app", "cli"]`, MCP exporte `["main"]`
- **README badges** — Badges Ruff + Mypy strict ajoutés

- **Plugin discovery** — Module `grimoire.registry.discovery` : `discover_tools()` / `discover_backends()` via `importlib.metadata` entry points
- **Error codes en production** — Tous les `raise GrimoireConfigError` assignent maintenant un `error_code` (GR001–GR003)
- **Tests CLI global flags** — 9 tests pour `--verbose`, `--log-format`, `--output` (mock + intégration)
- **Tests plugin discovery** — 6 tests pour `discover_tools()` / `discover_backends()` (chargement, erreurs, multiples)
- **API Reference** — Page `docs/api-reference.md` : GrimoireConfig, exceptions, logging, retry, plugins, error codes
- **Docs nav** — Section « Référence » dans mkdocs.yml avec API reference et Changelog
- **CI hardening** — `permissions: contents: read` ajouté au workflow `ci-sdk.yml`
- **CLI `--output`/`-o`** — Flag global `--output text|json` pour sortie machine-readable (implémenté sur `grimoire env`)
- **Rich markup mode** — `rich_markup_mode="rich"` activé dans le Typer app pour panel/markup dans `--help`
- **Plugin entry points** — `[project.entry-points."grimoire.tools"]` et `"grimoire.backends"` dans pyproject.toml
- **Feature request template** — `.github/ISSUE_TEMPLATE/feature-request.yml` (formulaire structuré)
- **FUNDING.yml** — Sponsor GitHub activé via `.github/FUNDING.yml`
- **Tests `@deprecated()`** — 11 tests : warning emission, version/alternative dans message, functools.wraps
- **Tests `error_codes`** — 27 tests : ErrorCode class, CODES registry, catégories, `__slots__`
- **Tests `configure_logging`** — 16 tests : niveaux, env vars, handler setup, JSONFormatter
- **Tests `@with_retry()`** — 11 tests : success/failure, backoff, jitter, préservation nom/retour
- **JSON logging** — `configure_logging(fmt="json")` + `JSONFormatter` pour logs structurés machine-readable
- **CLI `--log-format`** — Flag global `--log-format text|json` pour choisir le format de sortie des logs
- **CLI `grimoire env`** — Commande de diagnostic (version, OS, dépendances, projet) pour les bug reports
- **CLI error handler** — Gestionnaire global d'erreurs avec messages rich ; `GRIMOIRE_DEBUG=1` pour traceback complet
- **`@with_retry()`** — Décorateur retry avec backoff exponentiel + jitter dans `grimoire.core.retry`
- **Error codes** — Codes stables `GR0xx`–`GR5xx` dans `grimoire.core.error_codes` + attribut `error_code` sur `GrimoireError`
- **Shell completion** — Documentation dans README (bash/zsh/fish via Typer natif)
- **CLI `--verbose`/`-v`** — Flag global de verbosité intégré à `configure_logging()` (`-v` = INFO, `-vv` = DEBUG)
- **CodeQL/SAST** — Workflow GitHub Actions `codeql-analysis.yml` (scans hebdomadaires + PR)
- **CITATION.cff** — Fichier de citation académique CFF 1.2.0
- **`@deprecated()`** — Décorateur de dépréciation dans `grimoire.core.deprecation`
- **Branch coverage** — `branch = true` ajouté à la config coverage
- **Classifiers PyPI** — Ajout `Environment :: Console` et `Topic :: Scientific/Engineering :: Artificial Intelligence`
- **Logging centralisé** — `grimoire.core.log.configure_logging()` + env var `GRIMOIRE_LOG_LEVEL`
- **Exceptions** — `GrimoireTimeoutError`, `GrimoireNetworkError` dans la hiérarchie
- **`__all__`** — Exports explicites pour `cli`, `mcp`, `registry`, `exceptions`
- **`python -m grimoire`** — Support PEP 302 via `__main__.py`
- **DevContainer** — `.devcontainer/devcontainer.json` pour onboarding en 1 clic
- **MkDocs** — Site de documentation Material + workflow GitHub Pages
- **Pre-commit** — Enrichi avec mypy strict, yamllint, check-toml, large file check
- **CI** — Coverage enforced (`--cov-fail-under=70`), pip caching, artifact XML
- **Makefile** — 16 targets (lint, test, check, pre-push, docs, clean…)
- **Tests** — +143 tests scaffolding pour 11 outils non couverts

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [3.1.0] — 2026-03-11

### User Config Sync + HPE Parallel Execution + Architecture Doc

#### Ajouté

- **`grimoire setup`** — Nouvelle commande CLI pour synchroniser la configuration utilisateur
  (nom, langue, niveau) depuis `project-context.yaml` vers tous les fichiers de configuration.
  Modes : `--sync`, `--check` (CI-friendly), `--json`, overrides CLI (`--user`, `--lang`, `--skill-level`)
- **`grimoire-setup.py`** — Outil standalone (stdlib-only) pour la même synchronisation,
  utilisable sans pip via `grimoire.sh setup`
- **HPE — High-Performance Execution** — Moteur d'exécution parallèle pour les outils :
  `hpe-runner.py` (orchestrateur), `hpe-executors.py` (ThreadPool/ProcessPool/Async),
  `hpe-monitor.py` (métriques temps réel), `agent-task-system.py` (dispatch intelligent)
- **ARCHITECTURE.md** — Documentation détaillée de l'architecture du projet
- **Tests** — +3200 lignes de tests : `test_grimoire_setup.py` (50),
  `test_hpe_runner.py`, `test_hpe_executors.py`, `test_hpe_monitor.py`,
  `test_agent_task_system.py`
- **Archetypes bundled** — Les archetypes sont désormais inclus dans le wheel Python

#### Documentation

- `getting-started.md` — Ajout de `grimoire setup` + section "Configurer votre identité"
- `onboarding.md` — `grimoire setup` intégré dans le parcours J1
- `grimoire-yaml-reference.md` — Section "Synchronisation avec grimoire setup"
- Installation : `pipx` et `venv` documentés comme alternatives à `pip install` système

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [3.0.0] — 2026-03-08

### Réécriture complète — SDK Python pur + Indépendance totale

Le projet prend son indépendance sous le nom **Grimoire Kit** avec un **package Python installable**
(`pip install grimoire-kit`), architecture modulaire, API typée, et couverture de tests extensive.

#### Ajouté

- **SDK Core** (`grimoire.core`) — Modèles immutables (`@dataclass(frozen=True, slots=True)`),
  `GrimoireConfig` pour le chargement de `project-context.yaml`, résolution de chemins,
  système d'exceptions typées (`GrimoireConfigError`, `GrimoireProjectError`, `GrimoireRegistryError`)
- **CLI complète** (`grimoire.cli`) — 12 commandes Typer : `init`, `doctor`, `status`,
  `add`, `remove`, `validate`, `up`, `upgrade`, `merge`, `registry list`, `registry search`
- **MCP Server** (`grimoire.mcp`) — Intégration Model Context Protocol avec 6 tools
  et 4 resources pour les IDE compatibles MCP
- **Outils portés** (`grimoire.tools`) — `harmony-check`, `preflight-check`, `memory-lint`
  réécrits en modules Python avec API programmatique (`run()` / `RunResult`)
- **Système de registre** (`grimoire.registry`) — Résolution d'agents, workflows, tasks
  depuis les manifests CSV avec support multi-modules
- **Système de mémoire** (`grimoire.memory`) — Architecture à backends : fichier JSON,
  Ollama (embeddings), Qdrant (vector store) avec interface `MemoryBackend` abstraite
- **Archétypes** — 8 templates de projet : `web-app`, `creative-studio`, `fix-loop`,
  `infra-ops`, `meta`, `minimal`, `stack`, `features`
- **Merge engine** (`grimoire merge`) — Fusion intelligente de fichiers YAML/Markdown
  avec détection de conflits et dry-run
- **Upgrade engine** (`grimoire upgrade`) — Migration entre versions avec diff et backup
- **Documentation** — `getting-started.md`, `concepts.md`, `onboarding.md`,
  `memory-system.md`, `workflow-design-patterns.md`, `workflow-taxonomy.md`,
  `creating-agents.md`, `archetype-guide.md`, `vscode-setup.md`, `troubleshooting.md`
- **CI / Qualité** — 694 tests unitaires, 96% couverture, ruff lint, mypy strict,
  `py.typed` marker

#### Modifié

- **Rebranding complet** — Toutes les références de la marque d'origine renommées en `grimoire` dans le code source,
  tests, documentation, CI, shell scripts, et noms de répertoires (ancien layout → `_grimoire/`)
- **Entry points** — `grimoire` (CLI) et `grimoire-mcp` (serveur MCP) enregistrés
  dans `pyproject.toml`
- **Build** — Migration vers `hatchling` comme build backend
- **URLs** — Repo renommé en `Grimoire-kit`
- **MemoryManager** — Paramètre `project_root` explicite (déterministe, plus de `os.getcwd()`)
- **Atomic writes** — `LocalMemoryBackend._save()` utilise `tempfile` + `os.replace`

#### Supprimé

- Scripts shell standalone (remplacés par le SDK Python)
- Dépendance à `bash` pour l'exécution des outils
- Toute dépendance au package npm d'origine

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.4.1] — 2026-03-03

### Corrigé — Bug hunt (3 fichiers, 10 corrections)

- **cognitive-flywheel.py** — `cmd_analyze()` n'écrivait pas dans l'historique →
 la tendance (trend) restait toujours "stable" car `compute_score` n'avait
 jamais de cycle précédent. Ajout d'un `append_history()` à chaque analyse.
- **cognitive-flywheel.py** — Variable morte `high` dans `apply_gates()` :
 construite mais jamais utilisée dans le return (dead code supprimé).
- **tests/test_maintenance_advanced.py** — 6 appels `open()` sans
 `encoding="utf-8"` : crash potentiel sur Windows/locales non-UTF8.

### Vérifié — Aucun problème trouvé

- Division par zéro : 12 sites vérifiés, tous protégés (max, or, if guards)
- Pyflakes (F) : 0 erreur sur 48 outils + 53 tests
- Bare except : 0 (tous les except ont un type)
- eval/exec : 0 appel dangereux
- assert en production : 0
- Fonctions dupliquées : 0
- Mutable default args : 0
- Shadowing builtins : 0
- 82 swallowed-exception (`except ... pass`) : tous intentionnels (graceful degradation)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.4.0] — 2026-03-02

### Ajouté — Cross-pollination depuis zav-sandbox (GSANE)

- **cognitive-flywheel.py** (outil #47) — Boucle d'auto-amélioration continue :
 analyse Grimoire_TRACE.md pour détecter les patterns récurrents (failures,
 AC-FAIL), calcule un score de santé (A+ à D), génère des corrections
 automatiques avec système de gates (max 5 corrections, collision → escalade).
 6 commandes CLI : `analyze`, `report`, `apply`, `history`, `score`, `dashboard`
- **failure-museum.py** (outil #48) — Catalogue structuré des échecs :
 enregistre chaque failure avec root-cause, règle ajoutée, sévérité et tags.
 Persistance JSONL + sync markdown automatique.
 7 commandes CLI : `add`, `list`, `search`, `stats`, `export`, `lessons`, `check`
- **cleanup-branches.yml** — Workflow CI GitHub Actions pour supprimer
 automatiquement les branches mergées (protège main/develop/release/*)
- **tests/test_cognitive_flywheel.py** — 45 tests couvrant dataclasses,
 parsing trace, extraction de patterns, scoring, corrections, gates,
 persistence report/history, scoreboard, commandes, CLI, constantes
- **tests/test_failure_museum.py** — 43 tests couvrant dataclasses,
 persistence JSONL, markdown sync, commandes, CLI, intégration, constantes
- Total : **1 875 tests**, 0 échecs

### Inspiré par

- [zav-sandbox](https://github.com/zavrocKk/zav-sandbox) (framework GSANE) :
 Cognitive Flywheel, Failure Museum, branch cleanup CI

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.3.0] — 2026-03-02

### Ajouté — Couverture de tests complète (31 fichiers, +787 tests)

- **31 fichiers de tests** générés pour couvrir les 46 outils du framework :
 `bias-toolkit`, `context-guard`, `context-router`, `crescendo`, `crispr`,
 `dark-matter`, `dashboard`, `decision-log`, `desire-paths`, `digital-twin`,
 `distill`, `early-warning`, `harmony-check`, `immune-system`, `incubator`,
 `mirror-agent`, `mycelium`, `new-game-plus`, `nudge-engine`, `oracle`,
 `preflight-check`, `project-graph`, `quantum-branch`, `r-and-d`, `rosetta`,
 `self-healing`, `semantic-chain`, `sensory-buffer`, `swarm-consensus`,
 `time-travel`, `workflow-adapt`
- Chaque fichier teste : dataclasses, fonctions pures, fonctions projet,
 formats de sortie, constantes, parser CLI, intégration CLI
- **_gen_tests.py** — générateur automatique de tests par analyse AST
- Total : **1 787 tests**, 0 échecs, ~130 s d'exécution

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.2.1] — 2026-03-02

### Corrigé — Audit multi-cycles (15 cycles, 3 fichiers)

- **nso.py:323** — condition morte `status = "ok" if ... else "ok"` corrigée
 en `"warn"` — le NSO signale désormais correctement les erreurs memory-lint
- **gen-tests.py** — ajout `encoding="utf-8"` sur 2 appels `open()` (L167, L260)
 — évite les erreurs d'encodage sur Windows avec des fichiers contenant des
 accents/emojis
- **r-and-d.py:849** — ajout `encoding="utf-8"` sur `tool_file.open()` dans
 l'analyse de gap (même correctif portabilité Windows)

### Vérifié — Aucun problème trouvé

- Division par zéro : 15+ sites vérifiés, tous protégés par des gardes
- Regex : toutes les regex compilées valides
- Références croisées `_load_tool()` : 8 appels, tous vers des fichiers existants
- Aucun `open()` sans `with`, aucun chemin absolu hardcodé
- Aucune variable non-initialisée dans `finally`, aucun dict muté pendant itération
- Aucun import inutilisé, aucune variable morte (ruff F401/F841 clean)
- Chemins mémoire/output cohérents entre tous les outils

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.2.0] — 2026-03-02

### Corrigé — Fuites mémoire Python

- **r-and-d.py** — 5 correctifs mémoire :
 - `save_memory()` : ajout cap `MAX_MEMORY_SIZE = 500` — tronque aux N entrées
 les plus récentes au lieu de grossir indéfiniment
 - `_load_tool()` : cache via `sys.modules` — évite de recréer le module à chaque
 appel (14 exec_module/cycle → 1 par outil)
 - `load_cycle_reports()` : paramètre `last_n` — ne charge que les N derniers
 rapports au lieu de tout l'historique
 - `next_cycle_id()` : extraction directe depuis le nom du dernier fichier
 au lieu de charger et parser tous les rapports JSON
 - `tool_file.open()` L817 : ajout `with` context manager (file descriptor leak)
- **nso.py** — `_load_tool()` : même cache `sys.modules`
- **dream.py** — `emit_to_stigmergy()` : cache `sys.modules` pour stigmergy
- **memory-lint.py** — `emit_to_stigmergy()` : cache `sys.modules` pour stigmergy

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.1.1] — 2026-03-01

### Supprimé

- **workflow-snippets.py** (389 lignes) — aucune intégration CLI, aucun test, aucune
 cross-référence. Overlap avec `workflow-design-patterns.md`
- **quorum.py** (400 lignes) — aucune intégration CLI, aucun test. Overlap fonctionnel
 avec `antifragile-score.py` (signaux SIL) et `stigmergy.py` (seuils phéromoniques)
- **confidence-scores.py** (572 lignes) — aucune intégration CLI, aucun test. Heuristiques
 simplistes, overlap avec `reasoning-stream.py` (niveaux de confiance)

### Corrigé

- Nettoyage des références aux 3 outils supprimés dans `docs/concepts.md`
- Total : **−1361 lignes** de dead code, 49 → 46 outils

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.1.0] — 2026-03-01

### Ajouté

- **CHANGELOG.md** — suivi formel des changements (issue R&D oracle-swot)
- **Multi-projet** pour `antifragile-score.py` — comparer la santé entre projets
 via `--multi-project dir1 dir2 ...`
- **Multi-projet** pour `dream.py` — croiser les insights entre projets
 via `--multi-project dir1 dir2 ...`
- **Moteur R&D v2.1** — filtre anti-chaîne de mutations + pénalité actionnabilité

### Corrigé

- Nettoyage du TODO orphelin dans le template prototype de `r-and-d.py`
- Moteur R&D : les mutations de mutations (profondeur > 1) sont progressivement
 pénalisées dans le challenge, réduisant le bruit combinatoire

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [2.0.0] — 2026-02-28

### Ajouté

- **r-and-d.py v2.0** — Moteur d'Innovation R&D avec apprentissage par renforcement
 - Closed-loop reward via health snapshots du projet réel
 - Challenge durci : GO threshold 0.60, CONDITIONAL 0.40, quota 20% rejet
 - Générateur de mutations des gagnants passés (transposition, escalade,
 inverse, fusion)
 - Générateur gap-driven (gaps réels : tests manquants, docs absentes,
 domaines sous-représentés, dépendances fragiles)
 - Commande `seed` pour initialiser la mémoire
 - Commande `health` — snapshot de santé du projet
 - Commande `prototype` — génération de squelettes Python
 - 13 sources de récolte (dream, oracle-swot, oracle-attract, early-warning,
 dna-drift, workflow-adapt, antifragile, harmony, stigmergy, incubator,
 synthetic, mutation, gap-analysis)

### Corrigé

- Déduplication inter-cycles dans le moteur R&D (idées recyclées filtrées)
- Générateur synthétique enrichi (21 templates de concept blending)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.6.0] — 2026-02-27

### Ajouté

- **Vague 6** — 7 outils d'exploration avancée :
 - `digital-twin.py` — simulation de l'écosystème projet
 - `quantum-branch.py` — exploration parallèle de décisions
 - `time-travel.py` — machine à remonter le temps projet
 - `crispr-rules.py` — mutation ciblée de règles agents
 - `decision-log.py` — journal structuré des décisions
 - `mirror-agent.py` — audit croisé inter-agents
 - `sensory-buffer.py` — tampon sensoriel entre sessions

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.5.0] — 2026-02-26

### Ajouté

- **Vague 5** — Dream Nervous System :
 - `dream.py` v2 — mémoire cross-session, décroissance temporelle, bigram keywords
 - Boucle fermée nervous system avec feedback loop et trigger intelligent
 - `memory-lint.py` — vérificateur d'hygiène mémoire
 - `nso.py` — orchestrateur du système nerveux

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.4.0] — 2026-02-25

### Ajouté

- **Vague 4** — Stigmergy :
 - `stigmergy.py` — coordination indirecte par phéromones numériques

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.3.0] — 2026-02-24

### Ajouté

- **Vague 3** — Cross-Project Migration + Agent Darwinism :
 - `cross-migrate.py` — migration d'artefacts entre projets
 - `agent-darwinism.py` — sélection naturelle des agents

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.2.0] — 2026-02-23

### Ajouté

- **Vague 2** — Anti-Fragile Score + Reasoning Stream :
 - `antifragile-score.py` — scoring de résilience adaptative
 - `reasoning-stream.py` — flux de raisonnement structuré

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.1.0] — 2026-02-22

### Ajouté

- **Vague 1** — Dream Mode + Adversarial Consensus :
 - `dream.py` — consolidation hors-session et insights émergents
 - `adversarial-consensus.py` — protocole de consensus adversarial

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [1.0.0] — 2026-02-20

### Ajouté

- Vagues précédentes : 25 outils de base, protocole cognitif Completion Contract,
 Modal Team Engine, Self-Improvement Loop, Vector DB, web-app archetype
- Architecture framework : agent-base, agent-rules, hooks, mémoire, sessions,
 outils, registre, équipes, workflows
- Archetypes : web-app, infra-ops, minimal, stack, meta, features, fix-loop
- Documentation : getting-started, archetype-guide, memory-system, troubleshooting,
 workflow-design-patterns, creating-agents
- Tests : smoke-test.sh + suite de tests Python (122 tests)

<img src="docs/assets/divider.svg" width="100%" alt="">

## <img src="docs/assets/icons/branch.svg" width="28" height="28" alt=""> [0.1.0] — 2026-02-15

### Ajouté

- Initial commit — Grimoire Custom Kit structure de base
