# Plan cible du moteur de flows

Ce plan décrit la trajectoire pour que les flows Grimoire **s'exécutent** au lieu
de se compiler en consignes, et pour que chaque flow réutilisable **porte la
mesure** de ce qu'il produit.

Il ne propose pas de nouveau format : le blueprint existant est conservé. Il ne
propose pas non plus de nouveau moteur : `grimoire.runtime` en contient déjà un.
Il propose de brancher ce qui existe, et de financer ce branchement par des
suppressions.

## Constat

Le dépôt contient les deux extrémités d'un moteur de flows gouverné, sans le
câble entre elles. Inventaire établi par recherche d'appelants, pas par lecture
de la documentation.

| Brique | Ce qu'elle fait | Appelants hors tests et read-models |
|---|---|---|
| `runtime/kernel.py` | Instances, checkpoints, reprise, journal d'événements, médiation d'outils, effets de bord | **aucun** |
| `runtime/recipes.py` | `RecipeStep`, `VerificationGate`, profils de reprise, registre | **aucun** |
| `evals/harness.py` | Harnais d'évaluation, cas, rapports, fixtures | **aucun** |
| `missions/ledger.py` | Machine à états des tâches, `acceptance`, `expected_evidence` | mémoire et projections |
| Blueprint | Graphe de nodes typés, pins contractuels, gates, évals par node | Studio et `compile` |
| Standard | Preuve, gates, score, journal runtime | niveau tâche |
| Émetteurs d'hôtes | Sous-agents, skills, commandes, hooks bloquants | `host sync` |

Le format de flow est riche : les pins portent des contrats
(`task-envelope`, `handoff-packet`, `evidence-pack`), une arête sans contrat
commun est refusée à la composition, les gates déclarent un `onReject`, et un
node peut déclarer ses propres évals avec des assertions de contrat, de refus et
de coût.

Tout cela disparaît à la compilation. `grimoire blueprint compile` écrit
`.github/prompts/<id>.blueprint.prompt.md` : un document que le modèle est prié
de suivre. Le Studio l'assume — « le serveur lit, valide et écrit des artefacts ;
il n'exécute rien » — mais rien d'autre n'exécute non plus.

C'est le même diagnostic que celui de l'[ADR-005](adr-005-mission-ledger-source-of-truth.md)
sur le `MissionLedger` : « un moteur complet sans surface ». La décision prise
là-bas — faire du moteur la source et du reste une projection — s'applique
identiquement ici.

## Pourquoi maintenant : ce que les campagnes établissent

Trois campagnes pré-enregistrées sur le témoin `web-app-todo` mesurent
exactement la différence entre un protocole **posé** et un protocole
**imposé par un mécanisme**.

| Constat | Mesure | Source |
|---|---|---|
| Artefacts présents mais passifs : engagement du protocole | **0/40** | rapport 2026-07-03 |
| Même protocole imposé par un hook de session | **40/40** | rapport 2026-07-09 |
| Régressions dures, bras activés cumulés | **0 sur 96 runs** | rapports 07-09 et 08-27 |
| Régressions dures, baseline contemporaine | **9/40** | rapport 2026-08-27 |
| Divulgation des critères d'acceptation : complétion | **×2,5**, coût par tâche complétée divisé par deux | rapport 2026-08-27, hypothèse H2 |

Un flow compilé en markdown est de la présence passive. Un flow exécuté est un
mécanisme. La première ligne du tableau est le coût de la situation actuelle ; la
troisième est ce que le mécanisme achète, répliqué trois fois.

La cinquième ligne fixe une exigence de conception, pas seulement une intention :
**les critères d'acceptation doivent voyager dans le contrat du node**. Le
`MissionLedger` porte déjà les champs `acceptance` et `expected_evidence` ; le
moteur doit les transmettre au modèle à chaque node plutôt que de les laisser
dormir dans le ledger.

Aucun claim composite n'est fait ici : la règle décisionnelle A1-v3 reste
**non démontrée**, et le seul constat publiable reste celui, borné, de
l'élimination des régressions dures sur ce témoin.

## Cible

```text
Blueprint typé
→ instance de flow (kernel)
→ node : contrat d'entrée + frontière d'outils + critères d'acceptation
→ exécution par l'hôte, un node à la fois
→ vérification du contrat de sortie
→ checkpoint
→ node suivant, ou suspension nommée
→ relevé d'exécution
→ évals du flow rejouées sur ce relevé
→ registre : flow publié avec sa mesure
```

L'inversion tient en une phrase : **le moteur conduit, le modèle exécute un node
à la fois.** Aujourd'hui le modèle reçoit tout le flow et improvise l'ordre ;
demain il reçoit un node et son contrat, et le moteur décide de la suite.

C'est cette inversion qui rend possibles la reprise, le budget, l'éventail et le
rejeu — aucune de ces quatre choses n'est exprimable dans un document de
consignes.

## Les sept formes

Ces genres de node sont la différence de fond avec les cadres de développement
piloté par spécification. Aucun n'est exprimable en statique ; tous deviennent
directs dès que le moteur tient l'état.

| Genre | Ce qu'il fait | Pourquoi il n'existe pas ailleurs |
|---|---|---|
| `fanout` | Un node produit N éléments, le moteur instancie N sous-flows et recolle | La liste de travail n'est pas connue à l'écriture du flow |
| `verify-panel` | k vérificateurs indépendants, angles distincts, instruits de réfuter, majorité requise | Transforme une affirmation d'agent en vote traçable |
| `loop-until-dry` | Relance jusqu'à k tours consécutifs sans nouveauté, déduplication contre tout le vu | Un compteur fixe rate la queue de distribution |
| `judge` | N tentatives sous angles imposés, notées en parallèle, synthèse depuis la gagnante | Bat une tentative itérée quand l'espace de solution est large |
| `checkpoint` | Approuver, refuser ou amender, motif écrit dans le graphe de décision, reprise exacte | Le kernel sait déjà reprendre depuis un checkpoint |
| `budget` | Plafond de coût déclaré, abandon des passes optionnelles avant dépassement | Le coût est la composante qui fait échouer A1-v3, à 0,05 USD près |
| `replay-diff` | Rejouer le même flow sur la même entrée et comparer les traces | Seule façon d'établir qu'un flow est stable, et de mesurer la dérive du modèle |

## Lots

Chaque lot indique ce qu'il supprime. Un mainteneur unique sur cette surface ne
peut pas ajouter sans retrancher : le financement fait partie du lot, il n'est
pas une intention séparée.

### Lot 0 — Solder la crédibilité

Prérequis, pas amélioration. « Le flow porte sa preuve » ne veut rien dire tant
qu'un gate répond favorablement sur une tâche qui n'existe pas, et ne se
communique pas depuis une page publique qui affiche des chiffres invérifiables.

- `grimoire standard gate check` échoue sur une tâche introuvable. Aujourd'hui
  l'état vide d'une tâche absente n'exige aucun artefact, et le verdict est
  favorable : le garde échoue ouvert.
- Retirer du site public les métriques et le témoignage non adossés à une mesure,
  et les remplacer par ce qui est vérifiable.
- Supprimer le job CI « Python Unit Tests » de `ci-validate.yml` : son code de
  sortie est celui de `tee`, la valeur réelle est écrite dans une sortie que
  rien ne consomme, et ses tests sont déjà couverts ailleurs.
- Poser la notice de licence amont manquante sur les artefacts dérivés
  redistribués par l'atelier.

**Financement** : un job CI et une section de page en moins. Coût net négatif.

### Lot 1 — Brancher le noyau

Le pivot. `grimoire flow run`, `status`, `resume`, `abort`, adossés au
`RuntimeKernel` existant.

- Un node exécuté reçoit : son contrat d'entrée, sa frontière d'outils, ses
  critères d'acceptation issus du ledger, et rien d'autre.
- Les contrats de pins deviennent des invariants d'exécution, pas seulement de
  composition. Un contrat de sortie non satisfait suspend le flow en nommant le
  node et l'élément fautifs.
- Chaque node produit un checkpoint. Un flow interrompu ne recommence jamais du
  début.
- `blueprint compile` survit comme repli pour les hôtes sans exécuteur, mais
  cesse d'être la sortie normale.

**Financement** : `runtime/recipes.py` fusionné dans le modèle de node ou
supprimé ; `evals/harness.py` supprimé ou replié sur le chemin de rejeu réel.
Deux modules sans appelant en moins.

### Lot 2 — Portable par résolution

Un flow ne référence pas une commande concrète : il déclare des besoins. À
l'installation, le catalogue de besoins et le résolveur d'archétypes — qui
existent déjà et servent à `grimoire standard init` — lient ces besoins aux
commandes réelles du projet.

Un flow qui ne peut pas se lier **refuse de s'installer** en nommant la capacité
manquante, plutôt que de s'installer et d'échouer au troisième node.

**Financement** : les workflows dupliqués par archétype se replient sur un flow
unique résolu à l'installation.

### Lot 3 — Composition et registre

Un flow expose des pins comme n'importe quel node : il devient donc un node. Un
flow de release appelle les flows de vérification, de changelog et de gate sans
les réécrire.

Le registre porte, par flow : version, plage de compatibilité, empreinte
d'intégrité — le mécanisme de hachage des fichiers du kit existe déjà — besoins
requis, et relevé de mesure du lot 5.

**Financement** : la famille `grimoire workflows` — huit sous-commandes sur des
prompts markdown — devient un alias mince, puis disparaît à la majeure suivante,
au même titre que le chemin shell.

### Lot 4 — Les sept formes

Chaque forme devient un genre de node : comportement du moteur, donc
reproductible et mesurable, et non convention de rédaction.

Ordre recommandé : `checkpoint` et `budget` d'abord — les moins coûteux, et le
budget conditionne la viabilité économique du reste. Puis `fanout`, qui débloque
les cas d'usage réels. Puis `verify-panel` et `judge`. `replay-diff` en dernier.

**Financement** : les patterns du catalogue qui ne décrivent qu'une de ces formes
en prose deviennent des références au genre de node, et cessent d'être des
documents à maintenir.

### Lot 5 — Le flow porte sa preuve

Le moteur produit déjà le relevé d'exécution : c'est son journal d'événements.
`grimoire blueprint evals` cesse donc de dépendre d'un enregistrement fourni par
l'hôte et vérifie ce que le flow vient réellement de faire.

Le registre affiche alors, par flow : nombre de runs, complétion, régressions
dures, coût médian, écart de rejeu. Un flow publié sans mesure est étiqueté
**non mesuré** — jamais recommandé.

Les règles d'honnêteté du [protocole d'évaluation](evals-protocol.md)
s'appliquent telles quelles : non exécuté n'est pas échoué, et aucune
revendication sans critère pré-enregistré.

**Financement** : le chemin de rejeu en double disparaît ; un seul producteur de
relevé, celui du moteur.

### Lot 6 — Gouverner les moteurs externes

Un node délègue à un crew CrewAI ou à un graphe LangGraph via les adaptateurs
existants ; la trace revient normalisée dans le ledger et subit les mêmes gates.

À engager seulement sur demande d'un usage réel. Sinon c'est de la surface, et la
surface est le risque principal.

**Financement** : aucun. Ce lot est un pari, pas une dette à solder — d'où sa
dernière place.

## Ce qui n'est pas fait

Un plan sans refus n'est pas un plan. Chaque ligne est une chose que le kit a la
capacité technique de faire et ne devrait pas faire dans cette trajectoire.

| Écarté | Raison |
|---|---|
| Nouveau backend mémoire | Huit existent, aucun n'est appelé par un usage externe |
| Nouvel hôte | Deux hôtes natifs suffisent à établir la thèse ; un troisième multiplie la matrice sans rien prouver |
| Extension du cockpit | Le portefeuille multi-projets n'a pas de sens sans projet tiers |
| Patterns supplémentaires | Le manque n'est pas le vocabulaire, c'est l'exécution |
| Nouveau format de flow | Le réécrire coûterait le Studio, les évals par node et le validateur, pour un gain nul |

## Critère d'arrêt, à pré-enregistrer avant le lot 1

Le risque de ce plan n'est pas technique, il est économique. Conduire un flow
node par node produit plus d'allers-retours qu'un seul long document, donc plus
de tokens — et le coût est exactement la composante qui a fait échouer A1-v3, à
0,05 USD près.

Le critère doit donc être figé **avant** la première ligne de moteur, dans la
forme que le protocole impose déjà, et jugé sous la règle primaire que fixera
l'amendement A2 plutôt que sous une règle inventée pour l'occasion.

Le moteur est déclaré utile si, sur le même témoin, le même runner et le même
modèle que la campagne 2026-08-27 :

| Composante | Seuil |
|---|---|
| Complétion | ≥ celle du bras `activated-v2` (10/40) |
| Régressions dures | 0, comme sur les 96 runs activés cumulés |
| Coût par tâche complétée | ≤ celui de la baseline contemporaine (3,33 USD) |

Si le moteur échoue à ce critère, la conclusion n'est pas d'insister : c'est que
la conduite node par node coûte plus qu'elle ne rapporte, et que le flow doit
rester compilé. Ce résultat serait coûteux, et il vaut mieux le connaître au
lot 1 qu'au lot 5.

## Première étape

Avant toute ligne de moteur : exécuter un blueprint existant node par node en
pilotant le `RuntimeKernel` depuis un script jetable, et relever ce qui casse.
Ce prototype tranche la question qui commande tout le reste du plan — le lot 1
est-il un câblage ou une réécriture.

## Références

- [ADR-005 — Le Mission Ledger est la source](adr-005-mission-ledger-source-of-truth.md)
- [Protocole d'évaluation](evals-protocol.md)
- [Mode local et blueprints](serve-blueprints.md)
- [Plan cible du runtime normatif agentique](agentic-standard-target-plan.md)
