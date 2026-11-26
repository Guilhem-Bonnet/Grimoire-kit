---
audience: utilisateur
---

# Atelier de spec : le modèle optionnel du kit

Par défaut, l'assistant de l'Atelier n'appelle aucun modèle : son moteur est l'agent de l'hôte, piloté par le chat du
cockpit. Cette option, **éteinte par défaut**, laisse le kit appeler lui-même un petit modèle avec ta clé d'API, pour
quatre gestes sur un bloc du document. Sans clé ou option éteinte, rien ne change.

## Activer

Dans `project-context.yaml` (le drapeau `cockpit.test_features.atelier` doit déjà être allumé) :

```yaml
cockpit:
  atelier_model:
    enabled: true          # vrai seulement s'il vaut exactement true
    daily_tokens: 50000    # plafond dur par jour (défaut 50000)
    chantier_tokens: 20000 # plafond dur par chantier et par jour (défaut 20000)
    timeout_s: 20          # délai de l'appel, 1 à 60 (défaut 20), aucun nouvel essai
    max_output_tokens: 1024
    models:                # facultatif : surcharge par alias (haiku, sonnet) ou par geste
      haiku: claude-haiku-5-5
      sonnet: claude-sonnet-5-5
      modele-suggerer: claude-sonnet-5-5
```

La clé se lit dans la variable d'environnement `ANTHROPIC_API_KEY`, sinon dans le magasin de clés du kit
(`grimoire providers set-key anthropic`). Elle n'est jamais écrite dans un fichier du projet, jamais journalisée,
jamais renvoyée au navigateur. Sans clé, la fonction est indisponible et la page le dit.

## Les gestes

Sur un bloc, « Challenger ce bloc » ouvre un menu. Les trois postures existantes passent par l'agent de l'hôte (marquées
« sans modèle »). Quand l'option est active et la clé présente, le menu ajoute un sélecteur (modèle par défaut du geste,
haiku ou sonnet) et quatre gestes :

| Geste | Modèle par défaut | Résultat |
|---|---|---|
| `modele-reformuler` | haiku | suggestion (posture simplificateur) |
| `modele-suggerer` | sonnet | suggestion (posture explorer) |
| `modele-challenger` | sonnet | suggestion (posture sceptique) |
| `modele-relance` | haiku | commentaire de l'agent : une question de relance |

La sortie du modèle est une **proposition** : une suggestion que tu acceptes ou refuses, ou un commentaire. Elle n'écrit
jamais le document. Elle est traitée comme un texte non fiable (caviardée, bornée, affichée en texte brut).

## Ce qui part, ce qui est compté

- Part au modèle : le texte du bloc visé (4000 caractères au plus) et un contexte minimal (titre, phase, type de bloc),
  après le caviardage des secrets du kit.
- Le plafond est réservé **avant** l'appel (une estimation prudente : un jeton pour trois caractères d'entrée plus la sortie
  permise) ; un compteur illisible refuse l'appel. La consommation réelle remplace ensuite l'estimation.
- Le compteur vit dans `_grimoire-runtime-output/atelier/modele-usage.json` ; la consommation va aussi au ledger par
  l'événement `quota.consumed`, quand le projet en a un.

## Route

`POST /api/workspace/atelier/chantiers/<slug>/assistant` avec `{"geste": "modele-…", "block_id": "…", "modele": "haiku"|"sonnet"|""}`.
`GET …/entretien` rend `modele` : `enabled`, `available`, la raison, les modèles, les défauts et le budget, jamais la clé.
