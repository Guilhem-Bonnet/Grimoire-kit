---
audience: utilisateur
---

# Du besoin à la recette

Ce guide suit un projet fictif de bout en bout : une demande floue devient une exigence validée par un humain, des tâches livrées par l'orchestrateur, puis une recette prononcée contre l'exigence. Trois demandes de nature différente le traversent : une fonctionnalité, une anomalie, un incident. Pour les raisons du découpage, voir [MOA et MOE](../explications/moa-moe.md) ; pour le cadrage seul, voir [Cadrage produit](cadrage-produit.md).

## Le projet d'exemple

« Arrosoir » rappelle par mail à Camille d'arroser ses plantes. Camille est la **MOA** : elle porte le besoin, valide les exigences et prononce la recette. L'orchestrateur et ses workers sont la **MOE** : ils posent les questions, rédigent les exigences, réalisent, prouvent.

Chaque étape dit qui agit. Les sorties ci-dessous viennent d'une exécution sur un projet jetable, abrégées ; elles ne couvrent pas `approve` ni `recette` avec verdict, qui exigent un terminal interactif humain (voir plus bas).

## Déclarer les rôles

Dans `project-context.yaml`, la MOA est un humain nommé :

```yaml
governance:
  roles:
    moa: "Camille Durand"
  requirement_gate: block
```

`grimoire doctor` le confirme :

```text
  OK  MOA : Camille Durand — MOE : orchestrateur — gate d'exigence : block
```

Un projet créé par `grimoire init` propose ce réglage lui-même (voir la fin de ce guide pour un projet existant).

## Une fonctionnalité : « rappel par mail »

### L'orchestrateur conduit l'entretien

Camille dit : « je veux être prévenue quand une plante a soif ». La demande est floue, l'orchestrateur lance l'entretien :

```text
$ grimoire cadrage interview
Entretien de cadrage — profondeur standard (intake (standard, implementation)), 2 question(s) par angle
Nature : Nouvelle fonctionnalité (intake (aucun signal net : nature par défaut, à confirmer)) — corrigez avec --kind
  livrable : exigences, tâches ; recette MOA : l'usage attendu fonctionne
  ? Qui s'en sert et pour quoi faire ?
  ...
ouvert   utilisateurs — Utilisateurs et usage réel (obligatoire)
  ? Qui utilisera cela, et dans quelle situation concrète ?
ouvert   criteres — Critères de réussite (obligatoire)
  ...
```

Il pose les questions une à une, adaptées à la demande, et consigne chaque réponse. Un « je ne sais pas » devient une hypothèse inscrite, pas un blocage :

```text
$ grimoire cadrage interview answer criteres "Un rappel arrive par mail le jour où une plante doit être arrosée."
criteres → couvert
encore ouverts (obligatoires) : hors-perimetre, contraintes, risques

$ grimoire cadrage interview answer contraintes --unknown "Hébergement du serveur de mails non décidé."
contraintes → couvert
tous les angles obligatoires sont traités.
```

La nature (ici « nouvelle fonctionnalité ») est proposée par l'intake, affichée, et se corrige avec `--kind`. Elle fixe les questions, le livrable et ce que la MOA recettera.

### L'orchestrateur propose des exigences

Il écrit dans `_grimoire/cadrage/04-exigences.md` :

```markdown
- REQ-001 [Must] : Camille reçoit un rappel par mail le jour où une plante doit être arrosée
  - pytest tests/test_rappel.py passe
  - le mail nomme la plante et la date du dernier arrosage
- REQ-002 [Should] : le rappel part avant 9 h, heure de Camille
  - pytest tests/test_heure.py passe
```

Il propose, il ne valide pas. Puis il en tire les tâches :

```text
$ grimoire cadrage plan -y
Cadrage — 2 exigence(s) identifiée(s) (04-exigences.md, 05-cahier-des-charges.md)
  REQ-001   couverte      V2 GAO-req-001-cami-001 « Camille reçoit un rappel par mail le jour où une plante doit être arrosée »
  REQ-002   couverte      V1 GAO-req-002-le-r-001 « le rappel part avant 9 h, heure de Camille »
  ? [non bloquante] REQ-001 « … » : comment vérifier « le mail nomme la plante et la date du dernier arrosage » ? (posée sur GAO-cadrage-tran-001)
écrit — 2 tâche(s) créée(s) : GAO-req-001-cami-001, GAO-req-002-le-r-001
```

### Le gate arrête la MOE tant que la MOA n'a pas validé

Rien n'est validé : `grimoire cadrage status` montre les exigences « proposées », et le gate `requirement_validated` refuse de rendre la tâche réclamable.

```text
$ grimoire cadrage status
...
┃ Exigence ┃ État                   ┃ Tâches                          ┃ Formulation                          ┃
│ REQ-001  │ proposée (non validée) │ GAO-req-001-cami-001 (proposed) │ Camille reçoit un rappel par mail... │
│ REQ-002  │ proposée (non validée) │ GAO-req-002-le-r-001 (proposed) │ le rappel part avant 9 h, heure de...│

$ grimoire task move GAO-req-001-cami-001 --to ready
✗ Gate de preuve « requirement_validated » : 1 exigence(s) non satisfaite(s)
  - requirement_validated : exigence REQ-001 non validée par la MOA (Camille Durand) — à faire : `grimoire
cadrage interview` pour cadrer le besoin et écrire l'exigence, puis la faire valider par la MOA depuis son
terminal (`grimoire cadrage approve <exigence>`), et rattacher la tâche (`task add --requirement <id>`)
```

En mode `block`, la tâche reste simplement `proposed` ; en mode `warn` (voir plus bas), elle passerait, et le cockpit afficherait un item **À valider par la MOA** avec la commande exacte à copier.

### La MOA valide, depuis son terminal

Camille lance elle-même :

```bash
grimoire cadrage approve REQ-001
```

La commande affiche l'exigence et ses critères, puis demande à l'écran si c'est bien la MOA qui répond. Si oui, la validation est scellée au journal d'autorité avec l'empreinte du texte. Cette commande n'a pas été exécutée pour ce guide : un agent la voit refusée, par conception. Ce comportement vient des tests du kit (`tests/unit/missions/test_moa_requirement_gate.py`). Modifier ensuite la formulation, la priorité ou un critère retire la validation : la MOA revalide.

Elle valide aussi REQ-002 de la même façon. `grimoire cadrage status` montre alors les deux exigences « validée ».

### L'orchestrateur réalise et prouve

La tâche passe `ready` (le gate est satisfait), un worker la réclame, la réalise, apporte ses preuves, la PR est fusionnée, la tâche est livrée. Quand toutes les tâches d'une exigence sont livrées, `grimoire cadrage status` la montre « livrée, en attente de recette », et le cockpit dans « À décider » un item **À recetter par la MOA**.

### La MOA recette

```bash
grimoire cadrage recette REQ-001
```

Sans verdict, la commande présente et ne prononce rien : l'exigence validée, le critère de recette fixé par la nature (« l'usage attendu fonctionne »), chaque tâche rattachée avec son état et ses preuves. Ces preuves sont étiquetées « déclaré par la MOE, non vérifié par le kit » : elles informent Camille, elles ne jugent pas à sa place. La présentation est lisible en JSON avec `--json`. Sur une exigence encore non validée, elle ne montre que ceci :

```text
$ grimoire cadrage recette REQ-001
REQ-001 — Camille reçoit un rappel par mail le jour où une plante doit être arrosée (must)
  - pytest tests/test_rappel.py passe
  - le mail nomme la plante et la date du dernier arrosage
Nature : nouvelle-fonctionnalite — critère de recette : l'usage attendu fonctionne
État : proposée (non validée) (exigence REQ-001 non validée par la MOA (Camille Durand))
  GAO-req-001-cami-001 proposed — REQ-001 — Camille reçoit un rappel par mail le jour où une plante doit être arrosée

La MOA prononce, depuis son terminal : grimoire cadrage recette REQ-001 --verdict conforme|conforme-avec-reserve|non-conforme --motif "…"
```

Puis Camille prononce son verdict, depuis son terminal :

```bash
grimoire cadrage recette REQ-001 --verdict conforme
grimoire cadrage recette REQ-001 --verdict conforme-avec-reserve --reserve "libellé du mail à revoir"
grimoire cadrage recette REQ-001 --verdict non-conforme --motif "le mail n'indique pas la plante"
```

Même contrôle que pour la validation : écran de confirmation, refus pour un agent. Les tests du kit (`tests/unit/missions/test_moa_acceptance.py`) fixent l'effet :

| Verdict | Effet | État de l'exigence |
|---|---|---|
| `conforme` | aucun | recettée |
| `conforme-avec-reserve` | une tâche `proposed` pour la réserve | recettée avec réserve, tant qu'elle est en cours |
| `non-conforme` | une tâche MOE `proposed`, le motif pour critère | non conforme, tant que la reprise est en cours |

Si la tâche de suite n'a pas pu être créée, le verdict reste scellé, la commande donne la ligne `grimoire task add … --requirement REQ-001` à lancer, et le cockpit affiche **Suite à créer** (la MOE crée la tâche).

## Une anomalie : « rappel envoyé en double »

Plus tard, Camille reçoit deux mails le même jour. C'est une **anomalie** : même parcours, autres questions (attendu contre observé, reproduction, depuis quand, impact, contournement) et autre critère de recette : « le cas ne se reproduit plus ». Un chantier à part garde ses exigences séparées :

```text
$ grimoire cadrage new "Rappel envoyé en double" --slug rappel-double
chantier créé — rappel-double (idee)

$ grimoire cadrage interview --chantier rappel-double --kind anomalie
Entretien de cadrage — profondeur standard (intake (standard, implementation)), 2 question(s) par angle
Nature : Anomalie (bug) (forcée) — corrigez avec --kind
  livrable : correctif et test de non-régression ; recette MOA : le cas ne se reproduit plus
  ? Quel comportement attendu, quel comportement observé ?
  ? Comment le reproduire ?
  ...
```

L'exigence est rédigée dans `_grimoire/cadrage/rappel-double/04-exigences.md`, les tâches en sortent, la MOA valide `rappel-double/REQ-001` puis la recette le moment venu :

```text
$ grimoire cadrage plan -y --chantier rappel-double
  REQ-001   couverte      V1 GAO-req-001-un-r-001 « un rappel d'arrosage ne part qu'une fois par plante et par jour »
écrit — 1 tâche(s) créée(s) : GAO-req-001-un-r-001
```

```bash
grimoire cadrage approve rappel-double/REQ-001
grimoire cadrage recette rappel-double/REQ-001
```

## Un incident : « les mails ne partent plus »

Un incident en cours n'attend pas. L'entretien se limite à trois questions, l'action immédiate n'est jamais bloquée, le cadrage complet vient au post-mortem :

```text
$ grimoire cadrage interview --chantier panne-mails --kind incident
Entretien de cadrage — profondeur standard (intake (standard, implementation)), 0 question(s) par angle
Nature : Incident en cours (forcée) — corrigez avec --kind
  livrable : service rétabli, puis post-mortem ; recette MOA : service rétabli, causes et actions validées
  ? Quel est l'impact et le périmètre touché, et qui est touché ?
  ? Depuis quand, et quel a été le dernier changement ?
  ? Que peut-on faire tout de suite pour rétablir le service ?
Rétablir d'abord : l'action immédiate n'est jamais bloquée ; l'entretien complet est reporté au post-mortem, ...
```

La tâche passe `ready` sans exigence validée, même en mode `block` :

```text
$ grimoire task add "Incident : les mails de rappel ne partent plus" -a "pytest tests/test_envoi.py passe" --nature incident --ready
OK GAO-incident-les-001 — Incident : les mails de rappel ne partent plus (ready)
```

L'exemption demande que le titre se lise lui-même comme un incident : une nature déclarée seule n'exempte pas (le gate le dit, avec la marche à suivre). Tant qu'aucune exigence n'est rattachée à l'incident, le cockpit affiche **Post-mortem dû** : une fois le service rétabli, l'orchestrateur mène l'entretien complet et rédige l'exigence, la MOA la valide, puis recette.

## Ce que montrent les états

`grimoire cadrage status` donne l'état de chaque exigence. Pour un programme, `--json` rend la même chose dans un format stable (`schema: grimoire-cadrage-status/v1`) : phases, puis par exigence sa clé, son chantier, son état, sa nature, ses tâches et le dernier verdict scellé (`null` sans).

```text
$ grimoire cadrage status --json --chantier rappel-double
{
  "schema": "grimoire-cadrage-status/v1",
  "chantier": "rappel-double",
  ...
  "requirements": [
    {
      "requirement": "rappel-double/REQ-001",
      "chantier": "rappel-double",
      "title": "un rappel d'arrosage ne part qu'une fois par plante et par jour",
      "state": "proposee",
      "state_label": "proposée (non validée)",
      "nature": "anomalie",
      "tasks": [
        {"id": "GAO-req-001-un-r-001", "title": "REQ-001 — ...", "status": "proposed", "delivered": false}
      ],
      "last_verdict": null
    }
  ]
}
```

Les états : `proposee`, `validee`, `livree` (en attente de recette), `recettee`, `recettee-avec-reserve`, `recettee-en-cours`, `non-conforme`, `suite-a-creer`. Le code de sortie est 1 quand le cadrage n'est pas initialisé, comme en mode texte.

`grimoire doctor` résume le parcours quand une MOA est déclarée :

```text
  OK  MOA : Camille Durand — MOE : orchestrateur — gate d'exigence : block
  OK  Exigences : 3 proposée(s), 0 validée(s), 0 en attente de recette, 0 recettée(s)
```

Un projet sans gouvernance ne voit pas cette ligne et ne paie aucune lecture de plus.

## Qui fait quoi

| Étape | MOE (orchestrateur, workers) | MOA (l'humain) |
|---|---|---|
| Entretien | pose les questions, consigne, challenge | répond |
| Exigences | rédige et propose | relit |
| Validation | attend, ne peut pas la prononcer | `grimoire cadrage approve`, depuis son terminal |
| Réalisation | tâches, code, preuves | rien |
| Recette | présente les preuves | `grimoire cadrage recette`, depuis son terminal |
| Écart | ouvre la tâche de reprise ou de réserve | décide si l'exigence change |

Le cockpit, dans « À traiter », dit qui doit agir et affiche la commande à copier. Il ne valide ni ne recette jamais : ces gestes restent dans un terminal.

## Les deux modes du gate

`requirement_gate` règle ce qui arrive à une tâche sans exigence validée au passage `proposed → ready` :

| Mode | Effet |
|---|---|
| `block` | la tâche est refusée, avec la marche à suivre ; une tâche déjà `ready` sans exigence validée est écartée de la file du tick et son claim est refusé |
| `warn`, ou absent | la tâche est acceptée ; un événement `task.requirement_unvalidated` est inscrit et le cockpit l'affiche dans « À entretenir », sous « À valider par la MOA » |

Les natures `question` et `incident` sont exemptées, sous la condition vue plus haut.

### Passer un projet existant à `block`

Un projet existant démarre en `warn` : rien ne casse, tout se signale.

1. Déclarez la MOA dans `governance.roles` (`grimoire doctor` avertit tant que c'est absent).
2. Laissez `requirement_gate: warn` et ouvrez « À traiter » : « À valider par la MOA » liste les tâches passées `ready` sans exigence validée, avec la commande à lancer.
3. Pour chaque tâche, rédigez ou rattachez l'exigence (`grimoire cadrage interview`, `task add --requirement`), puis la MOA la valide.
4. Quand « À valider par la MOA » est vide, passez à `requirement_gate: block`. `grimoire doctor` avertit si des validations scellées existent alors que le gate n'est plus en `block` : un retour en arrière à `warn` est visible.

## Limites

- La déclaration seule ne prouve rien : le nom dans `project-context.yaml` dit qui doit décider, il n'atteste pas qu'une décision a été prise. Seule compte la validation faite depuis le terminal de la MOA, scellée au journal d'autorité.
- Les contrôles d'origine lisent l'environnement et le terminal du processus : un programme du même compte qui efface ces signaux, ou qui écrit lui-même au journal d'autorité, peut forger une validation ou un verdict. Seule la garde de zone de l'hôte, qui refuse aux outils d'agent l'écriture dans la zone de contrôle, l'en empêche.
- Les preuves affichées à la recette viennent du ledger et du registre de preuves du projet, que le worker écrit : elles informent la MOA, elles ne jugent pas à sa place.
- Le gate passe par le service des tâches ; les adaptateurs qui transitent sans lui ne sont rattrapés qu'à l'admission et au claim, en mode `block`.
- Une exigence d'un chantier qui n'est lié à aucune mission peut être citée par n'importe quelle tâche.
- La synthèse du doctor et `cadrage status --json` comptent les exigences du chantier principal et des chantiers qui ont leur propre `04-exigences.md` ; une exigence écrite ailleurs n'y figure pas.
