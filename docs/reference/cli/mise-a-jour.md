---
audience: référence
---

# La mise à jour

## Utilitaires

| Commande | Description |
| --- | --- |
| `grimoire context-pack` | Écrire un context-pack durable (contrat catalogue) pour le dépôt |
| `grimoire update` | Mettre à jour grimoire-kit — alias de `grimoire self update` ; la wheel est vérifiée avant installation (`--channel stable\|preview`, `--check`, `--offline`, `--from <bundle>`) |
| `grimoire verify-install` | Vérifier l'installation de grimoire-kit (`--online` : contre la wheel et le `SHA256SUMS` publiés) |
| `grimoire debugger status` | État de la réalité et du débogage des agents |
| `grimoire debugger claims` | Affirmations enregistrées par les agents |
| `grimoire debugger plan` | Plan de débogage courant |
| `grimoire debugger generate` | Générer les artefacts de débogage |
| `grimoire debugger serve` | Servir la vue de débogage |

L'alias court `grimoire dbg` existe pour `debugger`.

### Confiance dans l'installation (`trust_level`)

`grimoire self update` ne laisse plus le gestionnaire (uv, pipx, pip) retélécharger « la dernière version » : il
télécharge la wheel, la confronte à l'empreinte annoncée par PyPI **et** au `SHA256SUMS` de la release GitHub,
puis installe ce fichier. Un écart, un fichier absent de la liste ou une liste injoignable refuse l'installation
(« Nothing was installed »). Seule exception, affichée : une version antérieure à la première qui publie
`SHA256SUMS` s'installe avec `trust_level: pypi-only`.

Sous `uv tool`, les `--with` et le `--python` de l'installation d'origine (lus dans `uv-receipt.toml`) sont rejoués ;
une option qu'on ne sait pas rejouer (`constraints`, `overrides`, `options`, un `--with` par chemin, dépôt git ou
index) refuse la mise à jour avec la commande manuelle correspondante. Une fois l'installation faite, le chemin de
la wheel temporaire est retiré du reçu (`uv-receipt.toml`, ou `pipx_metadata.json` pour pipx) : `uv tool upgrade` /
`pipx upgrade` continuent de fonctionner ensuite. Si ce reçu a une forme inconnue, il n'est pas touché et la commande
qui refait une installation du registre est affichée.

### Canaux, contrôle sans réseau et bundle hors ligne

| Option de `grimoire self update` | Effet |
| --- | --- |
| `--channel stable` (défaut) | Plus haute version **stable** de PyPI. Ne choisit jamais une pré-version, même quand PyPI rend une pré-version comme `info.version` faute de stable. |
| `--channel preview` | La plus haute entre la stable de PyPI et la pré-version publiée sur GitHub (une pré-version `vX.Y.Z-rc1` n'est pas envoyée à PyPI, seulement à une release GitHub *pre-release*). Une pré-version ne s'installe que si le `SHA256SUMS` de sa release la liste : `trust_level: release-only`, jamais d'exception. |
| `--check [--json]` | Rend `up-to-date`, `update-available` ou `offline`, sans rien télécharger ni installer. Code de sortie 0 dans ces trois cas, 1 si la vérification elle-même échoue. |
| `--offline` | Aucune requête réseau, pas même la sonde de connectivité. Avec `--check` : statut `offline`. Sans `--check` : exige `--from`. |
| `--from <dossier\|wheel>` | Installe un bundle hors ligne. Aucune requête, index fermé (`--no-index` / `--offline`), wheels seulement (`--only-binary :all:` / `--no-build`). |

Un bundle est un dossier contenant la wheel de grimoire-kit, son `SHA256SUMS` (celui de la release) et, si
l'environnement n'a pas déjà les dépendances, leurs wheels (`pip download grimoire-kit==X -d <dossier>`, à faire une
fois en ligne ; ajouter leurs empreintes à `SHA256SUMS`). Toute wheel du dossier doit figurer dans `SHA256SUMS` avec la
bonne empreinte, sinon le bundle entier est refusé. Le gestionnaire n'est jamais pointé sur le dossier du bundle : `self update`
copie la wheel du kit et les autres wheels du dossier (toutes listées, hachées après copie) dans un dossier temporaire et ne
donne que celui-là à `--find-links`. Le reste du dossier (une page `links.html`, un sous-dossier, tout fichier qui n'est pas
une wheel) est ignoré : `pip` suit les pages HTML d'un `--find-links`, et une wheel que `SHA256SUMS` ne liste pas ne peut pas
entrer par là. Une pré-version dans un bundle exige `--channel preview`.

Limite écrite : le `SHA256SUMS` d'un bundle voyage avec ses fichiers, donc le niveau rendu est `bundle`, jamais
`verified` : il garantit l'intégrité du bundle, pas son origine. Les dépendances sont copiées puis hachées comme la wheel du kit :
rien n'est lu dans le dossier du bundle après la vérification. Hors ligne,
`gh attestation verify` n'est pas rejoué.

| `trust_level` | Sens |
| --- | --- |
| `verified` | La wheel installée est celle de la release (PyPI et `SHA256SUMS` concordent, `RECORD` identique) |
| `pypi-only` | Empreinte conforme à PyPI seul : la release ne publie pas de `SHA256SUMS` |
| `release-only` | Pré-version GitHub : conforme au `SHA256SUMS` de sa release, seule source (PyPI n'a pas cette version) |
| `bundle` | Bundle hors ligne : conforme au `SHA256SUMS` livré avec la wheel ; origine non confrontée à une source indépendante |
| `intact` | Les fichiers installés correspondent à leur `RECORD` ; provenance non vérifiée |
| `dev` | Installation éditable : rien à attester |
| `unknown` | Pas de `RECORD` pour comparer |
| `modified` | Au moins un fichier diffère de ce qui a été installé : `doctor` échoue, `verify-install` sort en 1 |

`grimoire doctor` affiche le niveau hors ligne ; `grimoire verify-install --online` va jusqu'à la comparaison avec
la wheel publiée (pour une pré-version installée : celle de sa release GitHub). Les fichiers de chaque release (wheel, sdist, SBOM CycloneDX de la wheel seule, `SHA256SUMS`)
portent aussi une attestation de provenance Sigstore : `gh attestation verify <fichier> --repo Guilhem-Bonnet/Grimoire-kit`.

Limites : les dépendances de grimoire-kit viennent de l'index sans empreinte publiée par le kit ; une somme et une
attestation rendent la falsification plus coûteuse, pas impossible ; `install.sh --legacy` (clone de `main`) n'est
pas couvert.

---
