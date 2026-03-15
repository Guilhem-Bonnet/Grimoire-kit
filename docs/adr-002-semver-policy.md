# ADR-002 — Politique de versionnage et stabilité API

**Statut** : Accepté  
**Date** : 2026-03-13  
**Auteur** : Guilhem Bonnet

## Contexte

Grimoire-kit est en version 3.x (Beta). Les utilisateurs et plugins tiers ont besoin de garanties claires sur la compatibilité ascendante et le cycle de dépréciation.

## Décision

### Semantic Versioning (SemVer 2.0.0)

Grimoire-kit suit [SemVer 2.0.0](https://semver.org/) :

| Bump | Signification | Exemple |
|------|--------------|---------|
| **Major** (4.0.0) | Breaking changes dans l'API publique | Suppression d'une classe exportée, changement de signature |
| **Minor** (3.2.0) | Nouvelles fonctionnalités rétrocompatibles | Nouvelle commande CLI, nouveau backend |
| **Patch** (3.1.1) | Corrections de bugs, améliorations internes | Fix d'erreur, optimisation |

### API publique

L'API publique inclut :

- `grimoire.__init__` : `GrimoireConfig`, `GrimoireError`, `GrimoireProject`, `__version__`
- `grimoire.core.config` : toutes les dataclasses de config
- `grimoire.core.exceptions` : toute la hiérarchie d'exceptions
- `grimoire.core.log` : `configure_logging`
- `grimoire.core.deprecation` : `deprecated`
- `grimoire.core.retry` : `with_retry`
- `grimoire.core.validator` : `validate_config`, `ValidationError`
- CLI : toutes les commandes et leurs flags documentés dans `cli-reference.md`
- Entry points : `grimoire.tools`, `grimoire.backends`

**Non publique** (peut changer sans préavis) :

- Modules sous `grimoire.tools._*` (préfixe `_`)
- Fonctions internes commençant par `_`
- Structure des fichiers dans `_grimoire/`

### Politique de dépréciation

1. **Annonce** : décorateur `@deprecated(version="X.Y.Z", alternative="...")` + avertissement `DeprecationWarning`
2. **Durée minimale** : 2 versions mineures (ex: deprecated en 3.2, supprimé au plus tôt en 3.4 ou 4.0)
3. **CHANGELOG** : chaque dépréciation est documentée dans la section `### Déprécié`
4. **Migration** : les guides de migration (`docs/migration-*.md`) documentent les changements

### Stabilité actuelle

| Module | Stabilité |
|--------|-----------|
| `grimoire.core.*` | **Stable** — changements rétrocompatibles uniquement |
| `grimoire.cli.*` | **Stable** — les commandes existantes ne cassent pas |
| `grimoire.mcp.*` | **Beta** — peut évoluer entre mineures |
| `grimoire.registry.*` | **Stable** |
| `grimoire.tools.*` | **Interne** — non garanti |

## Conséquences

- Les plugins tiers peuvent dépendre de `grimoire-kit>=3.0,<4.0` en confiance
- Les breaking changes nécessitent un bump major avec guide de migration
- Le décorateur `@deprecated` est obligatoire avant toute suppression

## Références

- [SemVer 2.0.0](https://semver.org/)
- [PEP 387 — Backwards Compatibility Policy](https://peps.python.org/pep-0387/)
- [httpx Versioning](https://www.python-httpx.org/compatibility/)
