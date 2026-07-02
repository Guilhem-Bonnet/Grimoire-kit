# Registry d'extensions Grimoire

Index des extensions publiées (modèle Homebrew tap : un repo git, pas de
backend). Ce dossier vit temporairement dans grimoire-kit et est conçu pour
être extrait tel quel dans un repo dédié `grimoire-extensions-registry`.

## Structure

| Élément | Rôle |
| --- | --- |
| [registry.json](registry.json) | Index : extensions, versions, checksums, résumés de manifestes |
| [registry.schema.json](registry.schema.json) | Schéma JSON de l'index |
| `dist/` | Archives `tar.gz` déterministes (mtime/uid normalisés) |
| [scripts/validate-registry.py](scripts/validate-registry.py) | CI de conformité |
| `.github/workflows/validate.yml` | Workflow du futur repo dédié |

## Publier

```bash
grimoire ext publish extensions/mon-extension --registry registry/
```

La publication échoue si le manifeste est invalide. L'archive est
déterministe : republier une extension inchangée produit le même checksum.

## Installer depuis le registry

```bash
grimoire ext add crewai --registry registry/           # dernière version
grimoire ext add crewai --registry registry/ --version 0.1.0
```

Le checksum est vérifié avant extraction ; l'extraction refuse tout chemin
absolu ou remontant.

## CI de conformité

```bash
python3 scripts/validate-registry.py --registry . \
  --catalogue <chemin>/catalogue-export.json
```

Vérifie : schéma de l'index, existence et checksum de chaque archive,
manifeste de chaque archive (validation structurelle complète), existence
des patterns déclarés dans le catalogue, permissions cohérentes avec les
hooks fournis.

## Curation

- Extensions internes (ce repo) : fast-track — la CI suffit.
- Extensions tierces (via PR sur le futur repo dédié) : CI + revue humaine obligatoire des scripts d'installation et des permissions déclarées.
- Règle non négociable : tout hook démarre en mode `shadow` dans le projet hôte.
