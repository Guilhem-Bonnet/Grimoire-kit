# Shared Context — $project_name

> Contexte partagé entre tous les agents du studio créatif.
> Charger ce fichier au démarrage de chaque agent.
> **Remplis les sections marquées ✏️ — c'est la chose la plus utile que tu puisses faire.**

---

## 🎨 Identité de Marque

| Élément | Valeur |
|---|---|
| Nom du projet | $project_name |
| Baseline / Tagline | ✏️ _à compléter_ |
| Ton de voix | ✏️ _à compléter_ (ex: professionnel mais accessible, ludique, technique) |
| Public cible | ✏️ _à compléter_ |

## 🎨 Design Tokens ✏️

> Générer avec : `python3 framework/tools/image-prompt.py` et `framework/prompt-templates/design-tokens.md`

| Token | Valeur |
|---|---|
| Couleur primaire | ✏️ _à compléter_ |
| Couleur secondaire | ✏️ _à compléter_ |
| Police titres | ✏️ _à compléter_ |
| Police corps | ✏️ _à compléter_ |
| Espacement base | ✏️ _à compléter_ |

## 📐 Formats de Livraison

| Livrable | Format | Dimensions |
|---|---|---|
| Logo principal | SVG + PNG @2x | variable |
| Favicon | ICO + PNG | 16×16, 32×32, 192×192 |
| Open Graph | PNG | 1200×630 |
| Bannière sociale | PNG | 1500×500 (Twitter), 1200×628 (LinkedIn) |
| Illustration | SVG ou PNG @2x | selon contexte |

## Conventions

- Langue de communication : $language
- Toujours vérifier le contraste WCAG (4.5:1 minimum)
- Aucun asset sans licence vérifiée
- Design tokens = source de vérité (pas de couleurs ad-hoc)
- Cycle obligatoire : draft → feedback → refine → validate
