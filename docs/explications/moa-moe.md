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

Une tâche livrée par des agents reste la décision de quelqu'un. Écrire le nom de la MOA dans le projet dit à qui revient la recette, et évite qu'un agent la prononce à sa place. Cette étape ne fait que poser le vocabulaire et la déclaration : rien n'est bloqué (le blocage viendra d'un lot ultérieur de l'épique #1342).

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
