# Shared Context — Creative Studio

> Contexte partagé entre tous les agents du studio créatif.
> Charger ce fichier au démarrage de chaque agent.

---

## 🎨 Identité de Marque

| Élément | Valeur |
|---|---|
| Nom du projet | {{project_name}} |
| Baseline / Tagline | {{tagline}} |
| Ton de voix | {{tone_of_voice}} (ex: professionnel mais accessible, ludique, technique) |
| Public cible | {{target_audience}} |

## 🎨 Design Tokens

> Générer avec : `python3 framework/tools/image-prompt.py` et `framework/prompt-templates/design-tokens.md`

| Token | Valeur |
|---|---|
| Couleur primaire | {{primary_color}} |
| Couleur secondaire | {{secondary_color}} |
| Police titres | {{heading_font}} |
| Police corps | {{body_font}} |
| Espacement base | {{spacing_unit}} |

## 📐 Formats de Livraison

| Livrable | Format | Dimensions |
|---|---|---|
| Logo principal | SVG + PNG @2x | variable |
| Favicon | ICO + PNG | 16×16, 32×32, 192×192 |
| Open Graph | PNG | 1200×630 |
| Bannière sociale | PNG | 1500×500 (Twitter), 1200×628 (LinkedIn) |
| Illustration | SVG ou PNG @2x | selon contexte |

## 🗣️ Points de Vigilance

- Toujours vérifier le contraste WCAG (4.5:1 minimum)
- Aucun asset sans licence vérifiée
- Design tokens = source de vérité (pas de couleurs ad-hoc)
- Cycle obligatoire : draft → feedback → refine → validate
