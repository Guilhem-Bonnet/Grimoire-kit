# Template: design-tokens

> Définition structurée de design tokens pour un projet.
> Utilisé par les agents UX, art-director, et les archétypes web-app/creative-studio.

## Structure

```
{{agent_alias}} génère les design tokens pour le projet {{project_name}}.

CONTEXTE :
- Style visuel : {{visual_style}} (ex: minimaliste, corporate, ludique, brutalist)
- Marque : {{brand_identity}} (ex: nom, valeurs, ambiance)
- Cible : {{target_audience}}
- Framework CSS : {{css_framework}} (ex: tailwind, vanilla, css-modules)

TOKENS À GÉNÉRER :

### 1. Couleurs
- primary : couleur dominante
- secondary : couleur d'accent
- neutral : gamme de gris (50 à 900)
- semantic : success, warning, error, info
- surface : arrière-plans (default, muted, elevated)
{{#if dark_mode}}
- Variantes dark mode pour chaque token
{{/if}}

### 2. Typographie
- font-family : heading, body, mono
- font-size : scale (xs, sm, base, lg, xl, 2xl, 3xl, 4xl)
- font-weight : light, regular, medium, semibold, bold
- line-height : tight, normal, relaxed

### 3. Espacement
- spacing scale : 0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24
- Unité de base : {{spacing_unit}} (ex: 4px, 0.25rem)

### 4. Bordures & Ombres
- border-radius : none, sm, md, lg, xl, full
- border-width : 0, 1, 2, 4
- shadow : sm, md, lg, xl

### 5. Breakpoints
- sm : 640px
- md : 768px
- lg : 1024px
- xl : 1280px
- 2xl : 1536px

### 6. Animation
- duration : fast (150ms), normal (300ms), slow (500ms)
- easing : ease-in, ease-out, ease-in-out, spring

FORMAT DE SORTIE :
{{#if format_css}}
:root {
  --color-primary: {{primary_color}};
  --color-secondary: {{secondary_color}};
  /* ... */
}
{{/if}}

{{#if format_json}}
{
  "colors": { "primary": "{{primary_color}}", ... },
  "typography": { ... },
  "spacing": { ... }
}
{{/if}}

{{#if format_tailwind}}
// tailwind.config.js extend
module.exports = {
  theme: {
    extend: {
      colors: { primary: '{{primary_color}}', ... },
      ...
    }
  }
}
{{/if}}

VALIDATION :
- Contraste WCAG AA ≥ 4.5:1 pour text/background
- Contraste WCAG AAA ≥ 7:1 pour les éléments critiques
- Cohérence de la scale typographique (ratio ~1.25 ou ~1.333)
- Tokens nommés sémantiquement (pas de "blue-500" mais "primary")
```

## Variables

| Variable | Type | Requis | Description |
|---|---|---|---|
| `project_name` | string | ✅ | Nom du projet |
| `visual_style` | string | ✅ | Style visuel cible |
| `brand_identity` | string | ❌ | Identité de marque |
| `target_audience` | string | ❌ | Public cible |
| `css_framework` | string | ❌ | Framework CSS (tailwind, vanilla) |
| `dark_mode` | boolean | ❌ | Générer les variantes dark mode |
| `spacing_unit` | string | ❌ | Unité de base (défaut: 4px) |
| `format_css` | boolean | ❌ | Sortie CSS custom properties |
| `format_json` | boolean | ❌ | Sortie JSON |
| `format_tailwind` | boolean | ❌ | Sortie Tailwind config |

## Exemple d'utilisation

```
Sally génère les design tokens pour Grimoire.
Style : minimaliste dark-mode first.
Cible : développeurs.
Framework : Tailwind CSS.
Format : JSON + Tailwind config.
```
