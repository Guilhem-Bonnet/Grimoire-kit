# Changelog

## Dernière release

### 3.41.0 — Le dispatch choisit sa preuve, le flow conduit un node à la fois

- **Le dispatch choisit sa cascade selon ce qu'il faut prouver — épic
  routage par vérifiabilité (#307).** Chaque tâche dérive une classe de
  vérifiabilité (V0/V1/V2) de son contrat, qui pilote la cascade de
  `grimoire task dispatch` ; le palier de départ s'ajuste à l'historique des
  verdicts, la relisibilité et les incertitudes déclarées affinent la
  décision, et la politique qui en résulte est émise aux hôtes avec l'état
  des fournisseurs dès le `SessionStart`.
- **Les fournisseurs se pilotent au coût.** Registre étendu par palier de
  coût, `grimoire providers status` et `grimoire providers audit` pour
  sonder sans dépenser, choix de modèle qui croise reasoning et coût.
- **Le moteur de flow conduit un node à la fois (#204).** `grimoire flow
  run|status|resume|abort`, avec l'exécuteur par dispatch en cascade du
  routage par vérifiabilité branché dedans.
- **Garde-fous runtime et hôtes.** Plafonds de tours et de budget par
  instance, garde des échanges pair-à-pair, claims à fichiers exclusifs,
  sous-agents bornés.
- **Les écritures mémoire sont validées et la médiation d'outils vérifiée.**
  Contrat MCP annoté, contenu externe marqué comme tel.
- Export de traces OTel GenAI conforme, évals avec pass^k.

## Releases précédentes

### 3.40.0 — La tâche se souvient, l'éditeur comprend

- **Le claim rappelle ce que la mémoire sait de la tâche.** Historique,
  tâches voisines avec leur cause d'arrêt, mémoire consolidée : en CLI
  (`grimoire task recall`), en MCP (`task_recall`), au SessionStart et dans
  l'inspecteur d'Exécuter. La consolidation n'écrit qu'à la clôture ou au
  blocage.
- **L'éditeur Source comprend le langage du kit.** Colorisation, diagnostics
  (chemin mort, agent inconnu, clé de frontmatter inconnue, terme absent du
  glossaire, pattern ou workflow inconnu) et complétion, sans dépendance,
  adossés au doctor, au manifeste, au catalogue et au glossaire.
- **Le bridge du standard converge avec la norme.** Trente-six patterns
  rattachés au catalogue de la norme, cinq exigences obligatoires couvertes,
  plus aucun trou de N1 à N5.
- **Moins de code mort.** Onze accesseurs sans appelant retirés, trois
  branchés sur une vraie commande, un inventaire qui refuse le retour.
- Piloter distingue un kit aligné d'un kit installé.

## Releases précédentes

### 3.39.1 — Le glossaire sur une installation nue

- **La vue de travail s'ouvre sur `pip install grimoire-kit` sans extra.** Le
  glossaire importait PyYAML alors que la dépendance déclarée est ruamel ;
  les six espaces s'ouvraient avec une erreur sur une installation nue (#292,
  trouvé par le passage consommateur sur un projet réel). Chargeur du kit à la
  place, avec un test qui bloque `yaml`.
- Voir `CHANGELOG.md` pour le détail des autres changements de cette version.

## Releases précédentes

### 3.39.0 — La vue de travail

- **Une coque unique pour l'atelier et le cockpit.** Six espaces de travail,
  Piloter, Concevoir, Exécuter, Observer, Mémoire et Source, remplacent les
  pages d'outil ; les anciennes redirigent vers l'espace correspondant
  (`?legacy=1` pour y revenir). Panneaux repliés, entrouverts ou épinglés,
  raccourcis 1 à 5, mode concentration, palette ⌘K qui atteint projets,
  workflows, tâches, fichiers et commandes du kit.
- **Des infobulles qui expliquent le système.** Survol, Alt pour épingler,
  termes liés en bulles enfants, Échap ferme la pile ; une seule source, le
  glossaire du kit, dont la documentation est générée.
- **Source.** Les fichiers du projet par étage, kit généré, overrides et
  projections des hôtes ; éditer un fichier du kit crée l'override ; diff
  contre le catalogue de digests, provenance, historique. Une console qui
  n'exécute que les sous-commandes autorisées du kit, sans shell.
- **Des données réelles ou rien.** Le portefeuille lit les vrais champs, un
  état vide dit d'où viendra la donnée, la démo reste opt-in.
- **Palette Encre, cinq surfaces, sombre et clair**, contrastes mesurés par un
  test ; fontes Geist embarquées. Tests de navigateur Chromium en CI.

## Releases précédentes

### 3.38.0 — Les agents connaissent leur tâche, les gardes parlent, la campagne enforced est mesurée

- **Les agents lisent, réclament et clôturent leurs tâches.** Outils MCP
  `task_list_ready`, `task_show`, `task_claim`, `task_update`, `task_context`
  sur le même service que le CLI ; la tâche courante se résout depuis le claim
  actif du Mission Ledger ; `grimoire task trace <id>` rend la timeline unifiée
  d'une tâche, refus de policy et gates rouges compris.
- **Les gardes n'échouent plus en silence.** Une exception dans la politique
  de hook rend `ask`, un fichier de gates illisible ferme toutes les portes, un
  board stigmergique corrompu est mis de côté, un manifeste d'équipe cassé est
  nommé, le rendu des surfaces hôtes dit quand il échoue, et la CI ne porte
  plus d'étape qui ne peut pas échouer.
- **Six artefacts de plus pour la norme.** Registres d'acceptation, de
  rétention, d'outils, d'incidents, de capacités et matrice risques-contrôles :
  plus aucune exigence obligatoire sans artefact de N1 à N5.
- **Campagne enforced contre activated, pré-enregistrée.** Vingt-quatre runs
  par bras : zéro régression dure sous enforcement, l'artefact de preuve
  garanti, ni complétion ni test supplémentaire, 39 % de tours en plus.
- **Release plus honnête.** L'étage TestPyPI qui n'a jamais tourné est retiré
  au profit de `make wheel-check` ; chaque PR exécute la CI complète.

## Releases précédentes

### 3.37.0 — Le bridge trace la norme, Windows compte, l'identité se déclare

- **Le bridge du standard est tracé.** La révision de la norme est épinglée et
  `grimoire standard upstream` détecte quand elle avance ; `traceability.yaml`
  relie chaque artefact et chaque famille de vérificateurs aux exigences `AG-*`
  et contrôles `CTRL-*` avec citation, et `grimoire standard traceability` rend
  la matrice et les trous par niveau. Deux artefacts que la norme rend
  obligatoires sont livrés : le claim ledger (tous profils) et le registre des
  surfaces runtime (`governed`, `production`). Voir
  [Intégration du standard](standard/integration.md).
- **La persona d'entrée se choisit par projet** (`agents.entry`) ; un projet
  qui porte déjà son orchestrateur déclare `""`. Voir
  [Surfaces hôtes](hosts.md#persona-dentree).
- **`grimoire setup` écrit la source de vérité** qu'il déclare, puis vérifie les
  miroirs contre le fichier relu.
- **Windows est bloquant en CI.** Quarante-six outils ne meurent plus sur une
  console cp1252, le dernier rouge réel est corrigé, la jambe Windows des
  tests d'outils compte comme ubuntu.
- **Le garde de release vérifie que chaque changement fusionné a son entrée,
  au bon endroit** — le cas des trente-huit blocs égarés de la 3.36.0 ne peut
  plus se reproduire en silence.

## Historique complet

Consultez le [CHANGELOG complet](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/CHANGELOG.md) sur GitHub.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/)
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).
