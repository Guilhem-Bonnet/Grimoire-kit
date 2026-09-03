# Changelog

## Dernière release

### 3.36.0 — L'atelier montre le vrai projet, la persona d'entrée entre en session

- **`grimoire serve` pilote les projets de la machine.** `GET /api/projects`,
  découverte par `scan`, sélection qui re-racine le serveur, `grimoire up`
  depuis l'interface. Les couches de télémétrie inventées disparaissent : un
  projet sans activité affiche son état vide, et la démonstration devient
  opt-in (`--demo`, `GRIMOIRE_SITE_DEMO=1`), réservée à la vitrine publique.
  Voir [Atelier local & blueprints](serve-blueprints.md).
- **La persona d'entrée entre dans la session.** Aucun hôte ne sait ouvrir une
  session dans un agent ; le hook `session_start` remet la persona à la boucle
  principale. Le manque est déclaré par hôte (`agent_autostart`) avec son
  substitut. Voir [Surfaces hôtes](hosts.md#persona-dentree).
- **Les six workflows d'orchestration sont invocables.** Boomerang, subagent,
  party-mode, incident-response, state-checkpoint, repo-map-generator étaient
  installés dans chaque projet et listés nulle part. `grimoire workflows list`
  indexe les deux familles, `workflows teams` rend les manifestes d'équipe,
  `install` et `show` atteignent une orchestration. Voir
  [Référence CLI](cli-reference.md#workflows).
- **L'atelier local ne propose plus la démo ni « pip install »** à qui
  l'exécute déjà.

## Historique complet

Consultez le [CHANGELOG complet](https://github.com/Guilhem-Bonnet/Grimoire-kit/blob/main/CHANGELOG.md) sur GitHub.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/)
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).
