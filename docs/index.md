---
audience: utilisateur
---

# Grimoire

Grimoire s'installe dans ton dépôt et te donne, pour le code que tu confies à des agents (Claude Code, Copilot), un tableau de bord local, des tâches, la mémoire du projet et un contrôle des preuves.

## Commencer

```bash
uv tool install grimoire-kit
grimoire init . --lite -y
grimoire standard init . --profile governed
```

Le détail, pas à pas, est dans [Premier projet](tutoriels/premier-projet.md).

## Les cinq outils

| Outil | Ce qu'il fait | Page |
|---|---|---|
| Le tableau de bord | affiche tes projets, leurs tâches et leur mémoire, sur ta machine | [Le tableau de bord](explications/le-tableau-de-bord.md) |
| Les tâches | une session prend une tâche et te rend son travail à relire | [Les tâches et les sessions](explications/les-taches-et-sessions.md) |
| La mémoire du projet | ce que le projet a décidé reste d'une session à l'autre | [La mémoire du projet](explications/memoire.md) |
| Le contrôle des preuves | une tâche n'avance pas sans la preuve attendue | [Le contrôle des preuves](standard/index.md) |
| L'installation | s'installe par besoins, se met à jour, se diagnostique | [Installer par besoins](guides/installer-par-besoins.md) |

## Le manuel, par ce que tu cherches

- [Tutoriels](tutoriels/index.md) : apprendre en faisant, une séance à la fois.
- [Guides](guides/index.md) : atteindre un but précis.
- [Référence](reference/index.md) : les commandes, la configuration, l'API, exactes et complètes.
- [Explications](explications/index.md) : comprendre pourquoi les choses sont faites ainsi, et où elles s'arrêtent, dans [Les limites](explications/les-limites.md).
- [Cadrages](cadrage/portail-connaissance.md) : les chantiers proposés, avant décision, à commencer par le portail de connaissance local.
