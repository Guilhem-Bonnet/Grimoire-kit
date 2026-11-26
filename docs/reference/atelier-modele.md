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

## Qui peut déclencher un geste payant

Une fois l'option allumée et la clé présente, **tout processus local qui peut lire la page du cockpit** (donc aussi un
agent qui dispose d'un shell sur ta machine) peut déclencher un geste, donc une dépense, sans que tu cliques. Seuls le
plafond par jour et par chantier la bornent. **Fixe un plafond bas** (`daily_tokens`, `chantier_tokens`), adapté à ce que
tu acceptes de payer sans regarder ; laisse l'option éteinte sur un poste où tu ne fais pas confiance aux processus locaux.

## Les gestes

Sur un bloc, « Challenger ce bloc » ouvre un menu. Les trois postures existantes passent par l'agent de l'hôte (marquées
« sans modèle »). Quand l'option est active et la clé présente, le menu ajoute un sélecteur (modèle par défaut du geste,
haiku ou sonnet) et quatre gestes :

| Geste | Modèle par défaut | Résultat |
| --- | --- | --- |
| `modele-reformuler` | haiku | suggestion (posture simplificateur) |
| `modele-suggerer` | sonnet | suggestion (posture explorer) |
| `modele-challenger` | sonnet | suggestion (posture sceptique) |
| `modele-relance` | haiku | commentaire de l'agent : une question de relance |

La sortie du modèle est une **proposition** : une suggestion que tu acceptes ou refuses, ou un commentaire. Elle n'écrit
jamais le document. Elle est traitée comme un texte non fiable (caviardée, bornée, affichée en texte brut).

## Ce qui part, ce qui est compté

- Part au modèle : le texte du bloc visé (4000 caractères au plus) et un contexte minimal (titre, phase, type de bloc),
  après le caviardage des secrets du kit.
- Le plafond est réservé **avant** l'appel (une estimation prudente : un jeton par caractère d'entrée, plus la sortie
  permise et une marge de 64) ; un compteur illisible, verrouillé, non inscriptible ou en lien symbolique refuse l'appel.
  La consommation réelle remplace ensuite l'estimation. La réservation est **rendue** quand le service répond autre chose
  que 200 ou que la connexion échoue ; elle est **gardée** sur un délai dépassé (la requête a pu être facturée) et sur une
  réponse 200 inutilisable.
- Si l'usage déclaré par la réponse dépasse ce qui a été réservé, la réponse est ignorée avec le refus distinct
  « usage déclaré incohérent » (la réservation reste comptée, jamais le chiffre déclaré).
- Le transport n'utilise aucun proxy d'environnement (`HTTPS_PROXY`, etc.) et ne suit aucune redirection : la clé ne part
  que vers `api.anthropic.com`, en HTTPS.
- Le compteur vit dans `_grimoire-runtime-output/atelier/modele-usage.json` ; ses plafonds (jour, chantier) restent en
  vigueur.
- **Quota du kit et règles d'usage.** L'usage déclaré par chaque réponse valide est aussi inscrit au ledger
  (`quota.consumed`, origine `atelier`, modèle, jetons d'entrée et de sortie, chantier) par le seul point d'entrée
  `missions/quota_usage.py::record_kit_call`, que le client appelle après chaque réponse facturée. Il entre ainsi dans la
  consommation du jour, les règles d'usage (`capacity.yaml`) et la fiche Capacité du cockpit. L'inscription est
  idempotente (clé `kit:atelier:<condensé de l'identifiant distant>.<identifiant de l'appel local>` : l'identifiant du
  service, absent, vide ou hors motif compris, n'est jamais pris tel quel ni perdu, et deux appels distincts de même
  identifiant s'inscrivent tous deux ; seul le rejeu du même appel local est sans effet, sous le verrou du ledger), n'est
  jamais un second compte du compteur local, et n'a lieu que si le projet a un ledger de missions. Un échec d'inscription
  ne fait pas échouer le geste (le compteur local tient) : il est journalisé et la réponse porte un `avertissement`.
- Ces jetons **s'additionnent** à la consommation du jour mais ne la rendent jamais « connue » : si des sessions lancées
  aujourd'hui n'ont pas de mesure, le statut reste « inconnu » ou « partiel » (et `quota.on_unknown` s'applique), les
  règles d'usage restent incomplètes. Les clés `kit:` sont réservées au kit ; un doublon ne compte jamais deux fois.
- Avant tout appel, le client lit la même source que l'admission : une règle d'usage de portée `project` ou `day`
  franchie dont l'action est `hold` ou `pause` **refuse le geste** (« règle d'usage … : geste du modèle refusé »), sans
  appel ni réservation. `warn` ne retient rien ; une règle de portée `task` ne vise pas un geste d'Atelier. Un
  `capacity.yaml` ou des règles illisibles refusent aussi (échec fermé). Pour lever : un humain relève le seuil ou met
  `enabled: false` sur la règle ; la portée `day` se lève à minuit UTC.
- Aucun outil MCP, aucune route ni commande n'inscrit une consommation ni n'en efface : le point d'entrée n'est appelé que
  par le client, avec une origine d'une liste fermée et les jetons que la réponse déclare (un test le vérifie).

## Route

`POST /api/workspace/atelier/chantiers/<slug>/assistant` avec `{"geste": "modele-…", "block_id": "…", "modele": "haiku"|"sonnet"|""}`.
`GET …/entretien` rend `modele` : `enabled`, `available`, la raison, les modèles, les défauts et le budget, jamais la clé.
