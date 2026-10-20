<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/wordmark-dark.svg">
    <img src="docs/assets/brand/wordmark-light.svg" width="220" alt="Grimoire">
  </picture>
</p>

# grimoire-kit

[![PyPI](https://img.shields.io/pypi/v/grimoire-kit?style=flat&label=PyPI)](https://pypi.org/project/grimoire-kit/)
[![CI](https://img.shields.io/github/actions/workflow/status/Guilhem-Bonnet/Grimoire-kit/ci-sdk.yml?branch=main&style=flat&label=CI)](https://github.com/Guilhem-Bonnet/Grimoire-kit/actions/workflows/ci-sdk.yml)
[![Licence](https://img.shields.io/github/license/Guilhem-Bonnet/Grimoire-kit?style=flat&label=licence)](LICENSE)

Grimoire s'installe dans ton dépôt et te donne, pour le code que tu confies à des agents
(Claude Code, Copilot), un tableau de bord local, des tâches, la mémoire du projet et un
contrôle des preuves.

[Le site](https://guilhem-bonnet.github.io/Grimoire-kit/) ·
[Le manuel](https://guilhem-bonnet.github.io/Grimoire-kit/docs/) ·
[English](README.md)

```bash
uv tool install grimoire-kit                   # ou : pipx install grimoire-kit
grimoire init . --lite -y                      # équipe le dépôt courant, en profil léger (aucun service)
grimoire standard init . --profile governed    # rend le contrôle des preuves bloquant
```

## Les cinq outils

### Le tableau de bord

Une page web locale qui réunit les projets Grimoire de ta machine et leurs tâches. Lancée
depuis un projet, elle l'ajoute et s'ouvre dessus ; elle ne lance pas de session, cela reste
dans ton éditeur.

```bash
grimoire cockpit start    # démarre en arrière-plan et ouvre le navigateur
```

### Les tâches

Chaque chantier devient une tâche avec un critère d'acceptation. Une session prend une tâche
et te rend son travail à relire.

```bash
grimoire task add "Corriger l'export CSV" -a "pytest tests/test_export.py passe" --ready
```

### La mémoire du projet

Ce que le projet a décidé reste d'une session à l'autre. La mémoire est propre à chaque
projet ; en profil léger, elle ne demande aucun service.

```bash
grimoire memory remember "On garde SQLite tant que le volume tient"
```

### Le contrôle des preuves

Avant qu'une tâche change d'état, Grimoire vérifie que ses preuves existent : un test lancé,
une revue écrite, un fichier produit. Il refuse pour de bon en profil `governed` et prévient
ailleurs ; dans les deux cas, il nomme ce qui manque.

```bash
grimoire standard gate check    # dit quelle preuve manque, s'il en manque une
```

### L'installation

On commence petit et on n'active que ce dont le projet a besoin. La mise à jour passe par une
sauvegarde et un aperçu ; ce qui demande ton arbitrage t'est proposé au lieu d'être appliqué.

```bash
grimoire upgrade-flow run    # mise à jour guidée, avec sauvegarde préalable
```

## Sous le capot

- [Premiers pas](https://guilhem-bonnet.github.io/Grimoire-kit/docs/getting-started/) ·
  [Référence des commandes](https://guilhem-bonnet.github.io/Grimoire-kit/docs/cli-reference/)
- [La mémoire du projet](https://guilhem-bonnet.github.io/Grimoire-kit/docs/memory-system/) ·
  [Le contrôle des preuves](https://guilhem-bonnet.github.io/Grimoire-kit/docs/standard/) ·
  [La mise à jour](https://guilhem-bonnet.github.io/Grimoire-kit/docs/upgrade/)
- [Les hôtes, surface par surface](https://guilhem-bonnet.github.io/Grimoire-kit/docs/hosts/)
- [Architecture du dépôt](ARCHITECTURE.md) · [Journal des versions](CHANGELOG.md)

## Contribuer

Les issues et les propositions de changement sont bienvenues. Le parcours (environnement,
tests, règles de contribution) est dans [CONTRIBUTING.md](CONTRIBUTING.md). Un changement de
commande met à jour sa page de référence dans le même commit.

## Licence

MIT — voir [LICENSE](LICENSE).
