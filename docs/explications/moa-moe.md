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

Une tâche livrée par des agents reste la décision de quelqu'un. Écrire le nom de la MOA dans le projet dit à qui revient la recette, et évite qu'un agent la prononce à sa place. La déclaration pose le vocabulaire ; le blocage vient avec le gate d'exigence (voir « Une tâche part d'une exigence validée » plus bas).

## La déclaration

Dans `project-context.yaml` :

```yaml
governance:
  roles:
    moa: "Prénom Nom"        # obligatoire dès que la section existe : un humain
    moe: "orchestrateur"     # facultatif, « orchestrateur » par défaut
```

Une valeur vide ou qui n'est pas du texte rend la configuration invalide, avec le chemin de la clé en cause.

## Ce que vérifie `grimoire doctor`

Le contrôle `moa_moe` n’échoue jamais :

- section absente (projet existant) : avertissement, avec la ligne à ajouter ;
- section présente : `OK`, avec le nom de la MOA.

`grimoire init` pose la question (« Qui porte le besoin ? », le nom saisi pour vous par défaut) et écrit la section. Avec `-y`, la MOA est le nom git de la machine ; sans nom git, la section est omise et le doctor avertit.

## Une tâche part d'une exigence validée

L'agent propose des exigences ; seule la MOA les valide, et le travail ne démarre que d'une exigence validée.

- **Valider** : `grimoire cadrage approve REQ-001`, depuis votre terminal. C'est le geste de `task answer` et `task accept` : un tour humain, jamais une session d'agent, jamais un orchestrateur, même sous mandat. La validation est scellée au journal d'autorité du kit, pas au ledger du projet que les workers écrivent : une ligne forgée au ledger ne compte pas. Modifier l'exigence ensuite retire la validation, car la MOA a validé ce texte-là.
- **Rattacher** : `grimoire task add --requirement REQ-001`. `grimoire cadrage plan` le pose seul sur les tâches qu'il crée.
- **Le gate** : le passage `proposed → ready` d'une tâche sans exigence validée est **refusé** quand le projet est en mode bloquant, **signalé** en mode signalé (événement `task.requirement_unvalidated`, avis « À entretenir » du cockpit). Le refus dit quoi faire : mener l'entretien de cadrage (lot M2 de l'épique, commande « cadrage interview »), puis faire valider l'exigence par la MOA.
- **Le mode** : bloquant dès que `governance.roles` est déclaré, signalé sinon ; un projet existant sans cette section n'est jamais bloqué. `governance.requirement_gate: blocking` ou `flagged` le fixe explicitement.
- **Exemptées** : les natures `question` et `incident` (`task add --nature incident`) ; un incident reçoit son exigence au post-mortem. Chaque passage exempté laisse l'événement `task.requirement_exempted`.

Limite connue : la nature est déclarée à la création de la tâche. Tant que l'intake n'en propose pas une (lot M2b), l'événement `task.requirement_exempted` est la trace à relire.
