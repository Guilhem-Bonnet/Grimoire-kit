---
audience: utilisateur
---

# Le refus de preuve, ligne par ligne

Cette page montre, sortie de terminal à l'appui, ce que le contrôle des preuves répond quand une tâche n'a pas ce qu'il lui faut, et ce qu'il répond une fois le manque comblé. Les sorties ci-dessous sont des captures exactes de la version publiée du paquet, dans un projet de démonstration neuf (`grimoire init . --lite -y --no-cockpit`, puis `grimoire standard init . --profile governed`, couleurs coupées). Seuls les titres des tâches ont été choisis pour la démonstration.

Le contrôle refuse pour de bon en profil `governed` ; dans les autres profils, il prévient. Les identifiants que tu vas lire (`context_bundle`, `provider_policy`, `GAO-…`) sont expliqués une fois, ici.

## Le refus, puis le passage

```text
$ grimoire task add "Corriger l'export CSV" -a 'pytest tests/test_export.py passe' --owner guilhem --ready
OK GAO-corriger-l-e-001 — Corriger l'export CSV (ready)

$ grimoire task claim GAO-corriger-l-e-001
✗ Gate de preuve « ready_to_in_progress » : 2 exigence(s) non satisfaite(s)
  - context_bundle : context bundle absent
    attendu : _grimoire-output/context/GAO-corriger-l-e-001/context-bundle.yaml
  - provider_policy : aucun fournisseur activé dans le registre
    activer un fournisseur dans _grimoire/standard/llm-provider-registry.yaml
[code de sortie 1]

(remède 2 : `enabled: true` sur le fournisseur anthropic dans llm-provider-registry.yaml, édition du fichier)

$ grimoire task context GAO-corriger-l-e-001
OK context bundle de GAO-corriger-l-e-001 →
_grimoire-output/context/GAO-corriger-l-e-001/context-bundle.yaml

$ grimoire task claim GAO-corriger-l-e-001
OK GAO-corriger-l-e-001 — Corriger l'export CSV (ready → claimed) · V0 → haiku
```

Ligne par ligne :

- `GAO-corriger-l-e-001` est l'identifiant de la tâche, fabriqué à partir de son titre.
- `Gate de preuve « ready_to_in_progress »` nomme la porte franchie : le passage de « prête » à « en cours ». Chaque transition d'état a la sienne.
- `context_bundle` est la première exigence : le dossier de contexte de la tâche, que la session lira. Le refus donne le chemin attendu ; la commande `grimoire task context` le produit.
- `provider_policy` est la seconde : au moins un fournisseur de modèle doit être activé dans le registre du projet. Le refus donne le fichier à éditer ; c'est le seul remède de la séquence qui n'est pas une commande.
- Les deux exigences sont non satisfaites : le refus les liste toutes, il ne s'arrête pas à la première.
- `[code de sortie 1]` : le refus est lisible par un script, rien n'a été écrit.
- Au passage, `ready → claimed` change l'état, et la fin de ligne (`V0 → haiku`) donne la classe de vérifiabilité et le modèle suggéré, expliqués plus bas.

### Un refus dès la création

```text
$ grimoire task add "Corriger l'export CSV" -a 'pytest tests/test_export.py passe' --ready
✗ Gate de preuve « proposed_to_ready » : 1 exigence(s) non satisfaite(s)
  - owner_or_agent_role : ni propriétaire ni claim : personne n'en répond
    `--owner` à la création, ou réclamer la tâche
```

La porte `proposed_to_ready` exige que quelqu'un réponde de la tâche : d'où `--owner` dans la première commande de la séquence précédente.

## Le tri par vérifiabilité

Trois tâches réclamées pour de bon, trois critères de nature différente :

```text
OK GAO-corriger-l-e-001 — Corriger l'export CSV (ready → claimed) · V0 → haiku
  critère : « pytest tests/test_export.py passe »
OK GAO-refondre-la-001 — Refondre la page de tarifs (ready → claimed) · V1 → sonnet
  critère : « revue approuvée par Guilhem »
OK GAO-choisir-le-n-001 — Choisir le nom de la future offre (ready → claimed) · V2 → aucun — omets le paramètre model, garde celui de la session
  critère : « le nom convient à l'équipe »
```

- **V0** : le critère nomme un verdict qu'un programme rend seul. Le modèle suggéré est le plus léger.
- **V1** : le critère passe par une revue. Le modèle suggéré est intermédiaire, et la relecture reste nécessaire.
- **V2** : le critère ne se vérifie pas par une machine. Aucun modèle n'est suggéré : la session garde le sien.

La classe est dérivée du texte des critères, jamais déclarée ; la définition exacte est dans la [référence des tâches](../reference/cli/taches.md#verifiabilite-dune-tache).

## Quand la session veut s'arrêter trop tôt

En profil `governed`, le hook de fin de tour (`Stop`) refuse que la session conclue tant que les preuves de sa tâche sont rouges. Voici la sortie exacte du hook, invoqué avec la charge utile que Claude Code envoie à la fin d'un tour, après `task context`, l'activation du fournisseur et un `task claim` (seule coupe : le chemin absolu du projet, remplacé par `<chemin du projet>`) :

```text
{"decision": "block", "reason": "[Grimoire] Tâche GAO-corriger-l-e-001 non terminée : les gates de preuve sont rouges (profil governed).\n  - Artefact de gate manquant : task_envelope — attendu à _grimoire-output/evidence/GAO-corriger-l-e-001/task-envelope.md ; remède : grimoire standard task scaffold <chemin du projet> --task-id GAO-corriger-l-e-001\nComplète _grimoire-output/evidence/GAO-corriger-l-e-001/ puis relance `grimoire standard gate check --task-id GAO-corriger-l-e-001 --strict`. Si la tâche doit rester ouverte, dis-le explicitement à l'utilisateur au lieu de conclure."}
```

Ce que l'hôte affiche à l'écran n'a pas été capturé ; seule la sortie du hook l'est. La ligne à retenir : « Tâche GAO-corriger-l-e-001 non terminée : les gates de preuve sont rouges (profil governed). »

## Voir aussi

- [Les tâches et les sessions](les-taches-et-sessions.md)
- [Le standard agentique](../standard/index.md)
- [Rendre la preuve bloquante](../guides/rendre-la-preuve-bloquante.md)
- [Les limites](les-limites.md)
