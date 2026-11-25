---
audience: utilisateur
---

# MOA et MOE : qui porte le besoin, qui réalise

Grimoire sépare deux rôles, avec les mots du métier du bâtiment et de l'informatique de gestion.

| Rôle | Qui | Ce qu'il fait |
|---|---|---|
| **MOA**, maîtrise d'ouvrage | un humain | porte le besoin, valide les exigences, prononce la recette |
| **MOE**, maîtrise d'œuvre | l'orchestrateur et ses workers | conçoit, réalise, apporte les preuves |

## Le parcours complet

1. **Entretien** : l'orchestrateur conduit l'entretien de cadrage (`grimoire cadrage interview`), proportionné à la nature de la demande, et consigne réponses et hypothèses.
2. **Exigences** : il en tire des exigences `REQ-001` dans `04-exigences.md` ou `05-cahier-des-charges.md`. Il les propose, il ne les valide pas.
3. **Validation** : la MOA valide chaque exigence depuis son terminal (`grimoire cadrage approve`), scellée avec l'empreinte du texte.
4. **Tâches** : `grimoire cadrage plan` ou `task add --requirement` rattache chaque tâche à l'exigence qu'elle sert ; le gate `ready` vérifie que l'exigence est validée.
5. **Recette** : une fois les tâches livrées, la MOA prononce la recette contre l'exigence (`grimoire cadrage recette`), pas contre les critères que l'agent s'est donnés. Les écarts repartent en MOE sous forme de tâches.

`grimoire cadrage status` montre où en est chaque exigence : proposée, validée, livrée en attente de recette, recettée, recettée avec réserve, non conforme.

## Pourquoi les déclarer

Une tâche livrée par des agents reste la décision de quelqu'un. Écrire le nom de la MOA dans le projet dit à qui revient la validation des exigences et la recette, et évite qu'un agent les prononce à sa place.

**La déclaration seule ne prouve rien.** Le nom écrit dans `project-context.yaml` dit qui doit décider ; il n'atteste pas qu'une décision a été prise. Seule compte la validation faite par la MOA depuis son propre terminal, un tour humain attesté (voir plus bas), scellée au journal d'autorité.

## La déclaration

Dans `project-context.yaml` :

```yaml
governance:
  roles:
    moa: "Prénom Nom"        # obligatoire dès que la section existe : un humain
    moe: "orchestrateur"     # facultatif, « orchestrateur » par défaut
  requirement_gate: block    # block ou warn ; absent : warn
```

Une valeur vide ou qui n'est pas du texte rend la configuration invalide, avec le chemin de la clé en cause. La MOA est un humain nommé sur une ligne : un compte de bot (`…[bot]`, `github-actions`, `dependabot`…), le nom de la MOE par défaut ou un nom sur plusieurs lignes sont refusés.

## Valider une exigence

Une exigence est une ligne `REQ-001` du cadrage (`04-exigences.md`, `05-cahier-des-charges.md`). L'orchestrateur la propose, l'entretien de cadrage (`grimoire cadrage interview`) l'affine, la MOA la valide :

```bash
grimoire cadrage approve REQ-001                    # cadrage principal
grimoire cadrage approve REQ-002 --chantier atelier
```

La commande affiche l'exigence, puis demande à l'écran si c'est bien la MOA qui répond. Elle refuse quand elle détecte une session d'agent (variables de session ou de terminal d'agent, exécution déclarée sans humain), sans terminal interactif, et pour l'orchestrateur, même sous mandat ; l'API Python fait les mêmes contrôles, l'origine n'y est pas un paramètre. La validation s'inscrit au journal d'autorité de la machine (chaîné par hash, écrit par le kit seul) avec le projet, la clé de l'exigence, la MOA déclarée et l'empreinte du texte validé :

- une ligne écrite au ledger ou un statut ajouté dans un fichier du cadrage ne vaut pas validation ;
- modifier la formulation, la priorité ou un critère de l'exigence retire la validation : la MOA revalide ;
- changer de MOA retire aussi les validations de la précédente ;
- une validation ne vaut que pour le projet où elle a été faite.

Limite : ces contrôles lisent l'environnement et le terminal du processus. Un programme du même compte qui efface ces signaux, ou qui écrit lui-même au journal d'autorité, peut forger une validation ; seule la garde de zone de l'hôte, qui refuse aux outils d'agent l'écriture dans la zone de contrôle, l'en empêche.

## Le gate « exigence validée »

Une tâche cite l'exigence qu'elle sert : `grimoire task add --requirement REQ-001`, ou automatiquement par `grimoire cadrage plan`. Au passage `proposed → ready` :

| `requirement_gate` | Tâche sans exigence validée |
|---|---|
| `block` | refusée, avec la marche à suivre (`grimoire cadrage interview`, puis `grimoire cadrage approve` par la MOA) ; une tâche `ready` sans exigence validée est écartée de la file du tick et son claim est refusé |
| `warn`, ou absent | acceptée ; un événement `task.requirement_unvalidated` est inscrit et le cockpit l'affiche dans « À entretenir » |

Les natures `question` et `incident` (`task add --nature`) sont exemptées, à condition que l'intake lise le titre de la tâche de la même façon : une nature déclarée seule n'exempte pas. Un incident se rétablit d'abord, son exigence se pose au post-mortem. Le passage exempté laisse un événement ; le cockpit liste les questions exemptées encore ouvertes et les incidents dont le post-mortem est dû (aucune exigence rattachée). La tâche ancre des questions du cadrage, créée par `grimoire cadrage plan`, est exemptée : c'est elle qui produit les exigences.

Une tâche rattachée à l'exigence d'un chantier lié à une autre mission que la sienne ne passe pas : l'exigence doit être celle de son chantier.

Un projet sans section `governance.roles` n'est jamais bloqué, quelle que soit la valeur de `requirement_gate`. Une section mal formée qui demandait `block` reste bloquante, et rien ne se valide tant qu'elle n'est pas corrigée.

Limites connues :

- le gate passe par le service des tâches ; les adaptateurs qui transitent sans lui (task-flow, unification, pont A2A) ne sont rattrapés qu'à l'admission et au claim, en mode `block` ;
- une exigence d'un chantier qui n'est lié à aucune mission peut être citée par n'importe quelle tâche.

## La recette

L'agent écrit les critères d'une tâche puis les vérifie : il est juge et partie. La recette l'en sort : elle porte sur le **besoin**, l'exigence que la MOA a validée.

```bash
grimoire cadrage recette REQ-001                     # présentation seule
grimoire cadrage recette REQ-001 --json              # la même, en JSON (lecture, jamais de verdict)
grimoire cadrage recette REQ-001 --verdict conforme
grimoire cadrage recette REQ-001 --verdict conforme-avec-reserve --reserve "libellé du bouton à revoir"
grimoire cadrage recette REQ-001 --verdict non-conforme --motif "le lien n'expire pas"
```

La commande présente l'exigence validée et ses critères, le critère de recette fixé par la nature de la demande (une anomalie : « le cas ne se reproduit plus » ; une migration : « bascule faite, retour arrière prouvé »…), puis chaque tâche rattachée avec son état et ses preuves : dernier verdict et pack de preuve, PR fusionnée. Ces preuves sont étiquetées « déclaré par la MOE, non vérifié par le kit » (champ `source` en JSON). Les textes venus du ledger, du cadrage ou de la saisie sont débarrassés de leurs caractères de contrôle (séquences d'échappement, contrôles bidirectionnels) avant d'être affichés ou scellés. La nature est celle des tâches si elles s'accordent, sinon celle de l'entretien de cadrage ; sans nature déclarée, la MOA recette l'exigence telle qu'elle l'a validée.

Le verdict passe par les mêmes contrôles que la validation, par le même code : refus d'emblée pour une session d'agent, l'orchestrateur ou une exécution sans humain, puis terminal interactif et « oui » à l'écran. Il est refusé aussi quand :

- l'exigence n'est pas validée, ou son texte a changé depuis la validation ;
- une tâche qui la sert reste ouverte : la commande la nomme, il n'y a pas de recette partielle ; une tâche annulée ne sert plus l'exigence ;
- aucune tâche livrée ne la sert.

Le verdict s'inscrit au journal d'autorité avec le projet, le chantier, l'exigence, l'empreinte de son texte, le verdict, le motif et les tâches jugées. Puis :

| Verdict | Effet | État de l'exigence |
|---|---|---|
| `conforme` | aucun | recettée |
| `conforme-avec-reserve` (réserve obligatoire) | une tâche `proposed` pour la réserve, rattachée à l'exigence | recettée avec réserve, tant que la réserve est en cours |
| `non-conforme` (motif obligatoire) | une tâche MOE `proposed`, rattachée à l'exigence, le motif pour critère d'acceptation | non conforme, tant que la reprise est en cours |

Une tâche close ne se rouvre pas : la reprise est une tâche nouvelle qui cite celles qu'elle reprend. Cette tâche de suite cite l'exigence déjà validée : elle peut passer `ready` sans nouvelle validation. Si le motif ou la réserve change le périmètre, la MOA fait évoluer l'exigence elle-même, ce qui change son empreinte et redemande une validation. Si la tâche de suite n'a pas pu être créée, la commande donne la ligne `grimoire task add … --requirement <exigence>` à lancer, et `grimoire cadrage status` signale l'exigence comme « suite à créer ».

Une tâche rattachée après un verdict `conforme` ne fait pas oublier la recette : l'exigence s'affiche « recettée, nouvelle tâche en cours », puis revient en attente de recette quand cette tâche est livrée. Quand elle est livrée, l'exigence revient « livrée, en attente de recette » et la MOA recette de nouveau : une exigence n'est recettée qu'à `conforme`. Une tâche livrée après le dernier verdict remet de même l'exigence en attente.

Comme pour la validation, seul le sceau compte : une ligne écrite au ledger ou un statut ajouté dans un fichier du cadrage ne vaut pas recette. Modifier l'exigence après coup retire validation et recette ; une fois l'exigence revalidée, l'ancienne recette ne vaut pas pour le nouveau texte.

Le cockpit liste, dans « À décider », les exigences livrées en attente de recette. Il lit le mode du gate avant tout : un projet sans MOA déclarée ne paie ni la lecture du ledger ni celle du journal.

Limites, les mêmes que pour la validation :

- les contrôles d'origine lisent l'environnement et le terminal du processus ; un programme du même compte qui efface ces signaux, ou qui écrit lui-même au journal d'autorité, peut forger un verdict ; seule la garde de zone de l'hôte l'en empêche ;
- les preuves affichées viennent du ledger et du registre de preuves du projet, que le worker écrit : elles informent la MOA, elles ne jugent pas à sa place ;
- si la tâche de suite ne peut pas être créée après le verdict, le verdict reste scellé et la commande le dit (code de sortie 1) : la tâche est à ouvrir à la main.

## Ce que vérifie `grimoire doctor`

Le contrôle `moa_moe` n’échoue jamais :

- section absente (projet existant) : avertissement, avec la ligne à ajouter ;
- section mal formée : avertissement, avec l'erreur, au lieu de disparaître derrière l'erreur générale de configuration ;
- section présente : `OK`, avec le nom de la MOA et le mode du gate ;
- validations d'exigence scellées pour ce projet alors que le gate n'est plus en `block` (repassé à `warn`, section retirée) : avertissement.

`grimoire init` pose la question (« Qui porte le besoin ? », le nom saisi pour vous par défaut) et écrit la section avec `requirement_gate: block`. Avec `-y`, la MOA est le nom git de la machine, mais personne ne l'a confirmée : la section est écrite avec `requirement_gate: warn` et un commentaire, et l'init le dit. Passez à `block` une fois le nom vérifié. Sans nom git, ou si c'est celui d'un bot, la section est omise et le doctor avertit.
