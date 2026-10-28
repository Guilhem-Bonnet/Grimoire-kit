---
audience: contributeur
---

# Marque Grimoire

Les fichiers de ce dossier, hors cette page, sont générés par `uv run scripts/build-brand.py`
(`--check` échoue si un SVG ou le sprite est périmé). Ne pas les éditer à la main.

## Le signe

- **Mot-symbole « › Grimoire »** (`wordmark-*.svg`, `wordmark-*.png`) : chevron tracé,
  rouille `#b4441d` sur fond clair, `#f08a5d` sur fond sombre ; mot en Instrument Sans 600,
  encre `#14171d` / `#eef0f4`, converti en tracés. Le mot est « Grimoire », sans suffixe ;
  le paquet s'écrit `grimoire-kit` en texte courant.
- **Tuile** (`tile.svg`, `favicon*`, `apple-touch-icon.png`, `org-icon-512.png`) : carré
  arrondi rouille, chevron blanc. Le rouille en aplat n'existe que dans cette forme.
- **Chevron nu** (`mark.svg` en `currentColor`, `mark-mono.svg`) : filigrane, logo du manuel.
- **Aperçu social** (`social-preview.png`, 1280 × 640) : mot-symbole, phrase produit courte,
  commande d'installation. Texte seul.

Interdits : le livre, l'hexagramme, les particules, tout dégradé ou ombre dans le signe,
tout chiffre ou version dans une image ; une image ne porte que le nom, la phrase produit
et la commande d'installation.

## Couleurs

Une seule couleur d'action, l'indigo `#3451d1` (sombre `#8ea2ff`). La rouille signe, elle
n'est jamais cliquable ni un fond, sauf dans la tuile. Sur un fond sombre, le chevron passe
en `#f08a5d` : d'où les deux fichiers clair et sombre du mot-symbole dans le README.

## Icônes

Une seule famille, celle du site : `docs/assets/icons/` (grille 24, trait 1,75, extrémités
rondes, `currentColor`, gris `#4a515c` / `#b9c0cb` quand le fichier est chargé en `<img>`).
`icons.svg` est le sprite `<symbol>` généré depuis ce dossier. Taille d'usage : 16, 20 ou 24,
jamais plus de 32. Pas d'icône devant les titres du README.

Exception P38 : les dix-neuf icônes d'action `i-act-*` (`docs/assets/action-icons/`) reprennent
les tracés de Lucide (licence ISC, `web/workspace/icons/NOTICE`), trait ramené à 1,75 et
`<title>` en français. Elles ne servent que des gestes (copier, enregistrer, relancer, mettre en pause…) :
aucune icône d'identité empruntée, ni logo, ni marque, ni mascotte, ni icône de produit.

## Polices

Instrument Sans et JetBrains Mono, sous licence OFL, auto-hébergées dans `web/fonts/`.
