---
audience: utilisateur
---

# MOA et MOE : qui porte le besoin, qui réalise

Grimoire sépare deux rôles, avec les mots du métier du bâtiment et de l'informatique de gestion.

| Rôle | Qui | Ce qu'il fait |
|---|---|---|
| **MOA**, maîtrise d'ouvrage | un humain | porte le besoin, valide les exigences, prononce la recette |
| **MOE**, maîtrise d'œuvre | l'orchestrateur et ses workers | conçoit, réalise, apporte les preuves |

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

## Ce que vérifie `grimoire doctor`

Le contrôle `moa_moe` n’échoue jamais :

- section absente (projet existant) : avertissement, avec la ligne à ajouter ;
- section mal formée : avertissement, avec l'erreur, au lieu de disparaître derrière l'erreur générale de configuration ;
- section présente : `OK`, avec le nom de la MOA et le mode du gate ;
- validations d'exigence scellées pour ce projet alors que le gate n'est plus en `block` (repassé à `warn`, section retirée) : avertissement.

`grimoire init` pose la question (« Qui porte le besoin ? », le nom saisi pour vous par défaut) et écrit la section avec `requirement_gate: block`. Avec `-y`, la MOA est le nom git de la machine, mais personne ne l'a confirmée : la section est écrite avec `requirement_gate: warn` et un commentaire, et l'init le dit. Passez à `block` une fois le nom vérifié. Sans nom git, ou si c'est celui d'un bot, la section est omise et le doctor avertit.
