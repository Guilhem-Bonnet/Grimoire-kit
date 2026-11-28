---
audience: référence
---

# Fournisseurs, routeur, file, pause et tick

## Fournisseurs LLM

Le groupe `grimoire providers` croise le registre déclaratif
(`llm-provider-registry.yaml`, voir [Compatibilité multi-provider](../../guides/rendre-la-preuve-bloquante.md#compatibilité-multi-provider-llm))
et l'état de refroidissement/audit runtime pour répondre à : quel fournisseur
appeler maintenant, pour quel palier de coût (`cheap`/`mid`/`strong`) ?

| Commande | Description |
| --- | --- |
| `grimoire providers status [--json]` | Fournisseurs activés, modèles par palier, disponibilité (refroidissement + dernier audit), prochain choix |
| `grimoire providers audit [--json]` | Sonder chaque fournisseur activé sans dépenser (PATH, `--version`, modèles Ollama) et journaliser `available`/`models_seen`/`probe_note` dans l'état |
| `grimoire providers cooldown <id> --reason rate_limit\|timeout\|overloaded` | Enregistrer un échec à la main (429, timeout, 529) |
| `grimoire providers probe [<id>] [--capabilities] [--json]` | Sonder (comme `audit`) puis rouvrir les fournisseurs fermés par une date de forfait illisible (`unknown`) ; une suspension à date n'est jamais levée. Avec `--capabilities`, lire aussi les capacités sur l'API du fournisseur, sans génération |
| `grimoire providers capabilities [<id>] [--json]` | Capacités connues de chaque fournisseur et modèle, avec leur provenance, sans réseau |
| `grimoire providers quota [--json]` | Fenêtre de forfait lue dans les flux d'appel : `lower_bound` (minorant daté) ou `unknown`, jamais 0 |
| `grimoire providers history [--ledger-root <chemin>] [--json]` | Historique des dispatchs par couple (type de tâche, classe) et palier de départ recommandé |
| `grimoire providers direct [--json]` | Modèles autorisés pour l'usage direct, par usage, et celui que Grimoire choisirait maintenant, avec la trace (lecture seule) |

`providers direct` (épique #1477, lot B1) lit la page « Modèles autorisés » du cockpit : ce qui est détecté
(fournisseurs du registre, modèles d'Ollama sur une adresse de la machine, agents installés), ce que l'humain a autorisé
par usage (`conversation`, `brainstorm`, `redaction`, `autres`) et la décision de maintenant. Par défaut aucun modèle n'est
autorisé : Grimoire fonctionne sans modèle. Les autorisations (ordre de préférence, plafond de jetons par jour) vivent
dans la zone de contrôle de l'humain, un fichier par projet, jamais dans le dépôt ; elles se posent dans le cockpit, sous
la clé de contrôle. Le repli automatique ne va jamais vers un modèle plus coûteux (`gratuit-local` < `abonnement` <
`payant`) ni plus exposé (`rien` < `texte`) que le modèle courant : il le propose. La clé d'API d'un fournisseur se pose au
terminal (`grimoire providers set-key <fournisseur> <VARIABLE>`), jamais dans la page.

« Vérifier les connexions » n'envoie une clé d'API qu'aux points d'accès que la page a montrés à l'humain : la page
renvoie la liste affichée (`confirmed_endpoints`, vide comprise), le serveur lit le registre une seule fois et refuse
(« la liste a changé, relis-la ») si cette lecture donne une autre liste ; rien n'est alors envoyé. Un modèle que
l'Ollama de la machine liste mais exécute à distance (entrée portant `remote_host` ou `remote_model`, nom en `-cloud` ou
`:cloud`) est toujours étiqueté exposé et payant, jamais « rien ne sort de la machine ». Un exécutable `ollama` qui se
trouve sous la racine du projet n'est pas reconnu comme l'Ollama local.

Limites connues du lot :

- L'empreinte d'une autorisation lie l'autorisation à ce que le nom désigne, mais elle n'authentifie pas le fichier
  d'autorisations lui-même : la protection de ce fichier est la zone de contrôle de l'humain, hors du dépôt.
- L'empreinte d'« Ollama local » retient le chemin réel du binaire, pas son contenu : un binaire remplacé sur place à
  la même adresse garde la même empreinte.
- La liste confirmée de « Vérifier les connexions » lie un fournisseur et une URL, pas la résolution de leurs noms de
  domaine : ce que le nom désigne au moment de l'envoi relève du réseau.
- Une course résiduelle subsiste sur les dossiers parents du fichier d'autorisations : la vérification de droits et de
  liens se fait au moment de la lecture, pas de façon atomique avec elle.

`providers audit` ne fait jamais l'appel réel : présence du premier mot de
`invocation` sur le `PATH`, `--version` pour un exécutable reconnu (claude,
gemini, copilot, codex, ollama), et pour un fournisseur local (invocation
`ollama ...` ou `provider_type: local`) la liste des modèles via
`GET /api/tags` (hôte surchargeable par `OLLAMA_HOST`, timeout 3 s). Un
fournisseur jugé `available: false` est écarté par `choose`/`candidates`
jusqu'au prochain audit qui le retrouve — l'audit n'active ni ne désactive
jamais un fournisseur, `enabled` reste une décision de gouvernance.

`providers history` (issue #312) lit les événements `task.dispatched` du
Mission Ledger et compte, par couple (type de tâche, classe de
vérifiabilité) : le nombre de dispatchs et le taux d'escalade depuis
`cheap` et depuis `mid` (part des dispatchs où le palier de départ n'a pas
suffi). Pas de classifieur, pas d'entraînement — des compteurs. Avec au
moins 5 observations pour un couple, un taux d'escalade dépassant 40 %
depuis `cheap` fait recommander `mid`, dépassant 40 % depuis `mid` fait
recommander `strong` ; un couple monté à `mid` dont les 5 dernières
observations à ce palier n'ont plus escaladé, et qui n'a pas retenté
`cheap` depuis autant d'observations, voit `cheap` recommandé à nouveau
(re-sondage) — jamais sous le plancher qu'impose la classe (`mid` reste le
plancher d'une tâche V1). La colonne « départ recommandé » est exactement
ce que `grimoire task dispatch` retient par défaut pour ce couple.

## Capacités des fournisseurs

Le registre peut dire ce que chaque fournisseur et chaque modèle sait faire.
`grimoire providers capabilities` l'affiche, `providers status --json`, l'outil
MCP `grimoire_providers_status` et l'API du cockpit (`GET /api/workspace/providers`)
le portent sous `capabilities`. Le routage ne s'en sert pas encore.

| Capacité | Niveau | Valeur |
| --- | --- | --- |
| `openai_compatible` | fournisseur | l'API accepte le format OpenAI (chat completions) |
| `anthropic_compatible` | fournisseur | l'API accepte le format Anthropic (Messages) |
| `tools` | modèle | appel d'outils |
| `structured_output` | modèle | sortie structurée ou mode JSON |
| `vision` | modèle | images en entrée |
| `context_window` | modèle | fenêtre de contexte, en tokens (entier) |
| `thinking` | modèle | raisonnement (« thinking ») |
| `prompt_cache` | modèle | cache de prompt |

Chaque valeur porte sa **provenance**, de la plus forte à la plus faible :

- `declared` : écrite dans le registre, sous `capabilities:` du fournisseur ou
  d'un modèle. Celle du modèle l'emporte.
- `probed` : mesurée par `grimoire providers probe --capabilities`. La source
  cite l'URL interrogée et la date.
- `documented` : valeur par défaut du kit, tirée de la doc du fournisseur. La
  source est l'URL de cette doc. Elle décrit l'API entière, pas chaque modèle :
  ce qui dépend du modèle (vision, raisonnement, contexte) n'y figure pas.
- `unknown` : rien de ce qui précède. Une capacité absente vaut « inconnue »,
  jamais « oui ».

```yaml
providers:
  - id: "deepseek"
    enabled: true
    capabilities:            # facultatif, vaut pour tous les modèles du fournisseur
      vision: false
    models:
      - id: "deepseek-flash"
        tier: "cheap"
        capabilities:        # facultatif, prime sur le niveau fournisseur
          vision: true
          context_window: 1048576
```

Un registre sans `capabilities:` se lit comme avant. Une valeur du mauvais type
(`context_window: true`, `tools: "yes"`) est ignorée et reste inconnue.

Valeurs documentées livrées par le kit (lues le 2026-11-22) :

| Fournisseur (id du registre) | Documenté |
| --- | --- |
| `anthropic` (`claude`) | API Anthropic et OpenAI, outils, cache de prompt |
| `openai` (`codex`) | API OpenAI, outils, sortie structurée, cache de prompt |
| `google-gemini` (`gemini`, `google`) | API OpenAI, outils, sortie structurée, cache |
| `deepseek` | API OpenAI et Anthropic, outils, mode JSON, raisonnement, cache |
| `moonshot` (`kimi`) | API OpenAI et Anthropic, outils, mode JSON |
| `local` (`ollama`, ou tout `provider_type: local`) | API OpenAI et Anthropic d'Ollama ; pas de cache de prompt |
| `litellm` (tout `provider_type: litellm`) | API OpenAI (`/chat/completions`, `/models`) et Anthropic (`/v1/messages`) du proxy LiteLLM |

Les URL exactes sont dans `grimoire providers capabilities --json` (champ
`source`) et dans `src/grimoire/providers/capabilities.py`. Le kit ne
documente ni ne sonde `github-copilot` : ses capacités restent inconnues tant
qu'elles ne sont pas déclarées.

**La sonde ne dépense rien.** `providers probe --capabilities` lit la liste des
modèles du fournisseur (`GET /models`, ou `GET /api/tags` puis `POST /api/show`
par modèle déclaré pour Ollama) et en tire ce qu'elle porte : contexte, vision,
raisonnement, sortie structurée selon le fournisseur. Elle ne fait aucun appel
de génération, et aucune option ne l'y autorise : ce qu'une génération seule
prouverait reste documenté ou inconnu. Un fournisseur sans point d'entrée connu
est sondé sur `<anthropic_base_url>/v1/models` si cette URL est en `https://`.

- **Clé d'un fournisseur documenté** : lue dans le magasin de secrets
  (`grimoire providers set-key`), puis dans l'environnement, sous
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY` ou
  `MOONSHOT_API_KEY`, et envoyée uniquement à l'hôte officiel du fournisseur.
- **Clé d'un autre fournisseur** (hôte lu dans le registre) : lue dans le
  magasin seulement, sous `<ID>_API_KEY`, jamais dans l'environnement. Un nom
  qui serait celui d'un fournisseur documenté est refusé : une clé ne peut pas
  partir vers un hôte choisi par le registre.
- **Identifiant** : ASCII strict, `[a-z0-9][a-z0-9-]{0,63}`. Un autre
  identifiant n'est pas sondé (« non mesuré : id invalide ») et n'a pas de
  fichier dans le magasin.
- **Transport** : la clé part en en-tête seulement, jamais dans l'URL, la
  sortie, les notes ni le fichier écrit. Aucune redirection n'est suivie
  (« redirection refusée »).
- **Contenu borné** : une réponse de plus de 2 Mio est refusée, au plus 2000
  modèles sont lus, et un identifiant de modèle hors de
  `[A-Za-z0-9._:/@+-]{1,128}` est ignoré, à la sonde comme à la relecture du
  fichier.
- **Sans clé, sans réseau, ou réponse illisible** : le fournisseur est « non
  mesuré », avec son motif (clé absente, clé refusée, injoignable). Ce n'est
  pas une erreur : la commande sort en 0.
- **Résultat** : `_grimoire-output/providers-capabilities.json`, état runtime.
  Le registre n'est jamais écrit. Une nouvelle sonde remplace la précédente du
  même fournisseur : une valeur qu'on ne mesure plus ne reste pas « sondée ».

## Gemini dans la conversation de cadrage

La conversation de cadrage de l'Atelier sait appeler Google Gemini (API native `generateContent`). Le registre par défaut du
kit ne déclare PAS Gemini : c'est une décision du propriétaire du projet. Pour l'activer, ajouter l'entrée au registre
du projet, poser la clé, puis autoriser le modèle sur la page « Modèles autorisés » :

```yaml
  - id: "google-gemini"        # ou l'alias "gemini" ou "google" ; aucun base_url ni anthropic_base_url
    enabled: true
    provider_type: "hosted"
    currency: "api"
    invocation: "gemini -p {prompt}"
    models:
      - {id: "gemini-4-neon", tier: "strong"}
    data_policy: {allowed_data_classes: ["public-docs"], forbidden_data_classes: ["secrets"]}
```

- **Étiquettes** : le texte de la conversation SORT de la machine (vers Google) ; payant au jeton, sans offre gratuite pour
  ce modèle. Rien ne part, et rien ne coûte, tant que l'humain n'a pas autorisé ce modèle pour un usage.
- **Clé** : le magasin de secrets du kit SEULEMENT, jamais l'environnement (une variable générique comme `GOOGLE_API_KEY`,
  exportée pour un autre service, ne part pas vers Gemini). Avec l'id `google-gemini` :
  `grimoire providers set-key google-gemini GOOGLE_GEMINI_API_KEY` ; la variable est toujours `<ID>_API_KEY`
  (`GEMINI_API_KEY` pour l'id `gemini`, `GOOGLE_API_KEY` pour l'id `google`). Limite : la détection de disponibilité
  regarde aussi l'environnement ; une clé seulement exportée peut y paraître présente, mais ce tour répond alors « saisis la
  clé avec `grimoire providers set-key …` » et rien ne part. Elle part en en-tête
  `x-goog-api-key` vers `https://generativelanguage.googleapis.com/v1beta/models/<modèle>:generateContent` seulement, jamais
  dans l'URL. Un fournisseur Gemini qui déclare un `base_url` n'a pas cet adaptateur ; une adresse réécrite après
  l'autorisation change l'empreinte : rien ne part.
- **Limite** : la consommation de Gemini n'est pas inscrite au compteur ni au quota du kit.

## Passerelle LiteLLM

Un proxy LiteLLM se déclare comme un fournisseur de type `litellm`. `base_url`
est la racine du proxy, avec ou sans `/v1` :

```yaml
providers:
  - id: "proxy"
    enabled: true
    provider_type: "litellm"
    base_url: "https://llm.example.com"   # ou http://127.0.0.1:4000 en boucle locale
    allowed_capabilities: ["chat", "code"]
    default_models: ["claude-haiku"]
    models:
      - id: "claude-haiku"               # un model_name du proxy
        tier: "cheap"
    data_policy:
      allowed_data_classes: ["public-docs", "project-source"]
      forbidden_data_classes: ["secrets", "credentials"]
```

- **Format.** Le proxy sert le format OpenAI (`/chat/completions`, `/models`,
  [quick start](https://docs.litellm.ai/docs/proxy/quick_start)) et le format
  Anthropic sur `/v1/messages`, celui que Claude Code utilise
  ([Claude Code](https://docs.litellm.ai/docs/proxy/client_setup/claude_code)).
  Les deux sont documentés pour tout le fournisseur. Le reste dépend du modèle
  derrière le proxy et reste inconnu tant qu'il n'est ni sondé ni déclaré.
- **Clé.** Une clé virtuelle ou la clé maître du proxy, rangée dans le magasin
  sous `<ID>_API_KEY` (`grimoire providers set-key`). Elle n'est jamais lue
  dans l'environnement et part en en-tête `Authorization: Bearer`. Un `id`
  dont la variable serait celle d'un fournisseur documenté (`openai`) n'est
  pas sondé.
- **URL admise.** `https://`, ou `http://` vers `127.0.0.1`, `localhost` ou
  `::1`. Une URL qui porte des identifiants, une requête ou un fragment n'est
  jamais contactée.
- **Sonde.** `providers probe --capabilities` lit `GET <base_url>/v1/models`
  ([model discovery](https://docs.litellm.ai/docs/proxy/model_discovery)),
  puis `GET <base_url>/v1/model/info`. De cette seconde route, seuls
  `max_input_tokens` (contexte), `supports_function_calling` (outils) et
  `supports_vision` sont lus, et seulement à `true` : la doc prévient qu'un
  champ non renseigné vaut `null`. Si le proxy refuse `/model/info` à une clé
  virtuelle (la doc ne la montre qu'avec la clé maître), les modèles restent
  listés, sans détail. Aucune génération, aucune redirection suivie, et les
  bornes de la section précédente s'appliquent (2 Mio, 2000 modèles,
  identifiants filtrés).
- **Vérification.** `grimoire standard verify` exige `base_url` et des
  `forbidden_data_classes` : le proxy transmet les prompts à ses propres
  fournisseurs, il est traité comme un hébergé par la politique de données.
- **Table des modèles.** L'`id` du fournisseur sert de clé sous
  `dispatch.models.<hôte>` comme pour tout fournisseur (voir la section
  suivante). Le worker du tick ne passe pas encore par `base_url` : il route
  vers `anthropic_base_url`, qui peut viser le même proxy en `https://`.

## Table palier → modèle : hôte × fournisseur

Le kit raisonne en paliers : `cheap`, `mid` et `strong`. Une tâche V0 part au
palier `cheap`, une V1 au palier `mid`, une V2 reste sur le modèle de la
session. Un palier ne nomme aucun modèle : une table le traduit pour un hôte et
un fournisseur donnés. Elle sert au hook `dispatch-model`, à la politique de
dispatch injectée au `SessionStart`, au `model:` des personas émises, au worker
(`tier`, `model_by_class`) et à la route forte (`sensitive_route: strong`).

La classe V0, V1 ou V2 d'une tâche ne change pas : seule la traduction du palier
en nom de modèle passe par la table.

| Commande | Description |
| --- | --- |
| `grimoire dispatch models [--json]` | Table résolue pour le projet : hôte, fournisseur, palier, modèle et provenance du choix |

**Défauts livrés par le kit.** Seulement là où le nom est connu de l'hôte :

| Hôte | Fournisseur | `cheap` | `mid` | `strong` |
| --- | --- | --- | --- | --- |
| Claude Code | `anthropic` | `haiku` | `sonnet` | `opus` |

Sous Claude Code, sans surcharge, le comportement est celui d'avant la table, à
l'octet près : un test compare la sortie du hook et les fiches émises à celles
de la version précédente.

**Surcharge par projet.** Elle se déclare dans la politique d'orchestration du
standard :

```yaml
# _grimoire/standard/orchestration-policy.yaml
dispatch:
  impose_model: on
  models:
    claude:                    # hôte : claude, copilot, cursor, codex, gemini, opencode
      anthropic:               # fournisseur du registre, ou "*" pour tout fournisseur
        cheap: claude-haiku-4-5
    copilot:
      cheap: claude-haiku-4.5  # un palier posé sous l'hôte vaut pour tout fournisseur
      mid: gpt-5.4
```

**Ordre de résolution**, du plus fort au plus faible :

1. `dispatch.models.<hôte>.<fournisseur>.<palier>` (provenance `project`) ;
2. `dispatch.models.<hôte>.<palier>` ou `dispatch.models.<hôte>."*".<palier>`
   (`project`) ;
3. les alias historiques `dispatch.copilot_models` et `dispatch.cursor_models`,
   toujours lus, avec les clés `haiku`, `sonnet` et `opus` ou les noms de
   paliers (`legacy`) ;
4. les défauts du kit (`kit`).

**Aucune correspondance inventée.** Un palier qu'aucune couche ne nomme pour cet
hôte et ce fournisseur n'a pas de modèle. L'appelant omet alors `model` : le
modèle de la session, de l'agent parent ou du sélecteur s'applique. C'est le cas
de Copilot, de Cursor, de Gemini CLI et d'opencode tant que le projet ne déclare
rien. Les fiches de sous-agent de Cursor (`.cursor/agents/`), de Gemini CLI
(`.gemini/agents/`) et d'opencode (`.opencode/agent/`) reçoivent `model` pour le
seul palier que la table traduit ; opencode exige la forme `provider/model-id`
(un nom sans `/` est écarté et signalé par la dégradation `model affinity`).

**Capacités.** Le worker et le relecteur exigent l'appel d'outils (`tools`). Un
modèle du projet dont cette capacité est connue à « non » dans le registre
(déclarée ou sondée, voir la section précédente) est écarté, et la couche
suivante prend le relais. Une capacité inconnue n'écarte jamais un modèle, et un
défaut du kit n'est jamais écarté : ni la sonde ni le registre ne peuvent vider
la table.

**Ce que la table ne peut pas faire descendre.**

- La route sensible (`sensitive_route: strong`) garde le défaut fort du kit,
  `opus` sous Claude Code, quelle que soit la surcharge du projet.
- Le relecteur prend, dans l'ordre, le palier du rôle, `mid`, puis `strong`,
  `mid` et `cheap`. Il retient le premier modèle que la double relecture tient
  pour distinct de celui du worker. Si aucun ne l'est, la relecture n'est pas
  lancée et la tâche revient à l'humain avec ce motif.
- Un palier demandé au worker que la table ne traduit pas ne lance rien : la
  tâche attend, avec le motif, au lieu de tourner sur le modèle de la session.
- `orchestrator.roles.worker.tier` n'accepte que `cheap`, `mid` ou `strong`.

**Valeurs illisibles.** Un hôte inconnu, un palier hors `cheap`, `mid` ou
`strong`, un nom de modèle hors `[A-Za-z0-9._:[]/@-]`, ou un nom que YAML
relirait comme un autre type (`null`, `true`, `1.5`) sont ignorés. Ils
apparaissent sous la table de `grimoire dispatch models` et dans `errors` de la
sortie `--json`. Le hook ne lève jamais : une politique illisible vaut une table
vide.

## Politique de données : ce qui peut partir chez un fournisseur

Une tâche ne part sur un fournisseur que si sa classe de données est déclarée et
admise par ce fournisseur. La règle vaut pour le worker du tick et pour
`grimoire task dispatch`. Elle ne concerne pas `provider_type: local`, dont rien
ne sort de la machine.

- **Déclarer :** `routing.default_data_class` dans `llm-provider-registry.yaml`
  pour le projet, ou `grimoire task add --data-class <classe>` pour une tâche.
  La classe de la tâche prime. `grimoire task show` affiche la classe retenue et
  son origine.
- **Admettre :** la classe figure dans `data_policy.allowed_data_classes` du
  fournisseur et pas dans `forbidden_data_classes`.
- **Sans déclaration, ou classe non admise :** pas de route vers ce
  fournisseur. `task dispatch` s'arrête avec le refus `data_policy` et son
  motif, ou passe au fournisseur suivant si un autre est admis.
- **Le repli du worker sur le modèle de l'hôte est un appel hébergé.** Si la
  classe déclarée n'est admise par **aucun** fournisseur hébergé activé, le
  worker ne se rabat pas dessus : la tâche retourne en `ready` avec son motif
  (`task.worker_deferred`), visible dans `task show` et dans la note
  d'attention du tick, sans question bloquante à l'humain. Si la classe est
  admise par au moins un fournisseur hébergé, le repli reste possible et son
  motif s'inscrit dans `provider_refused` de l'événement de lancement, que
  `task show` affiche aussi. Sans classe déclarée, le comportement est
  inchangé : le worker garde le modèle de l'hôte.

**Ce que cette règle garantit :** une tâche déclarée interdite n'est pas confiée
à ce fournisseur, et une classe qu'aucun fournisseur hébergé n'admet ne part pas
non plus sur le modèle de l'hôte.

**Ce qu'elle ne garantit pas :** une classe n'est qu'une déclaration, personne ne
vérifie ce que contient le projet. Une fois routé, un worker lit avec Bash tout
ce que son compte peut lire, et le fournisseur reçoit ce qu'il lit. Pour des
données qui ne doivent vraiment pas sortir, écartez-les du poste du worker :
conteneur ou copie isolée, sans ces données.

## Routeur de LLM : une politique déclarative, tracée avant d'être activée

`grimoire task dispatch` choisit un palier de départ par la classe de
vérifiabilité et l'historique d'escalade, puis prend le premier modèle du palier
que le forfait n'a pas fermé. Le routeur (épopée #705, lot K9, issue #774) y
ajoute une décision **déclarative et rejouable**, sans rien changer à ce chemin
tant qu'on ne l'active pas : `route(task, history, provider_state)` choisit un
modèle d'après la tâche seule (classe, type, difficulté), l'historique de
réussite par modèle et la disponibilité du fournisseur. Il ne lit jamais le
quota, le budget ni les tokens restants, et ne change jamais de modèle pour
économiser : ces notions relèvent de l'admission, pas du choix. Un fournisseur
suspendu par son forfait y figure comme indisponible, jusqu'à sa date de
réouverture, comme un fournisseur injoignable.

| Commande | Description |
| --- | --- |
| `grimoire route explain <tâche> [--json]` | Rejoue la dernière décision `task.routed` à partir de ses entrées et de `inputs_digest` ; sortie 1 si le rejeu diffère, 2 sans décision |
| `grimoire route report [--json]` | Réussite au premier essai (pass^1) par (type, classe, modèle), escalade par couple, accord entre le routeur et le dispatch en mode `shadow` seulement (une issue `session`, `defer` ou `refuse` suivie d'un appel compte comme un désaccord) ; « non mesuré » sous 5 observations |
| `grimoire route eval --set <jeu> [--json]` | Seuil d'activation D16 hors ligne ; sortie 0 (tenu), 1 (non tenu), 3 (non mesuré) |

**Politique** : `_grimoire/standard/routing-policy.yaml`, sinon le défaut livré
par le kit. Toutes les règles qui correspondent s'appliquent et le palier retenu
est le maximum de leurs `min_tier` : le résultat ne dépend pas de l'ordre des
règles. `route: session` est absorbant, V2 n'a jamais de route autonome, aucune
règle qui correspond donne `session`, et le palier ne descend jamais sous le
plancher de la classe (V0 `cheap`, V1 `mid`). Les conditions admises sont
`class`, `type`, `risk_any`, `exclusive_files_gt`, `needs_local` : une règle qui
nomme un quota ou un budget invalide la politique. `type` et `risk_any` se comparent
en minuscules, sans espaces autour (comme les étiquettes de la tâche) : `risk_any: [Security]`
vaut `[security]`, une étiquette vide est refusée. Une politique malformée, sans
version ou de version inconnue n'est jamais remplacée par le défaut : aucune
route autonome (`on_invalid: refuse`) : la décision tracée est `refuse` et
`task dispatch` ne lance aucune cascade (refus `routing_policy_invalid`, sortie 2),
en `shadow` comme en `active`. Seul un routeur en panne (exception) laisse le
dispatch suivre l'ancien chemin, avec une décision `refuse` et son erreur tracées.

```yaml
version: 1
mode: shadow            # défaut ; `active` fait gouverner la décision
rules:
  - {id: r-risk, when: {risk_any: [security, migration]}, min_tier: strong}
  - {id: r-v1, when: {class: V1}, min_tier: mid}
```

**Tentatives** : `max_attempts` (défaut 3) borne le nombre d'appels de la cascade
en mode `active` (appels en échec compris, tous paliers confondus) ; en `shadow`
rien n'est appliqué, la valeur n'est que tracée dans les entrées de `task.routed`.

**Mode** : `shadow` (défaut) émet `task.routed` avant chaque cascade et laisse le
dispatch suivre l'ancien chemin ; `active` fait gouverner la décision (palier de
départ sauf `--start-tier` explicite, ordre des fournisseurs, modèle dans le
palier). Dans le palier, le départage est : réussite au premier essai (au moins 5
observations, fenêtre de 50), puis, à réussite égale seulement, prix catalogue
croissant (un prix inconnu passe après), puis ordre déclaré. Les modèles
suspendus ou refroidis sont listés dans `excluded[]` avec leur raison. Sans
candidat, la décision est `defer` : jamais de descente de palier. Revenir à
`shadow` se fait en un seul champ.

En `active`, la décision n'est pas contournée : une issue `session` (règle
`route: session`, V2, aucune règle qui correspond) ou `defer` ne lance aucune
cascade (refus `routed_to_session` ou `routed_defer`, sortie 2, aucun
`task.dispatched`). Les fournisseurs appelés sont ceux que la décision admet : une
règle `providers: {currency: local}` vaut pour tous les paliers de la cascade, un
fournisseur d'une autre monnaie n'est jamais appelé, et au palier retenu seuls les
fournisseurs du classement le sont. En `shadow`, ces issues ne restent qu'une trace.

**Attribution** : chaque tentative (`task.dispatched`) compte pour le couple
(type, classe, modèle) de cette tentative ; un 429, un timeout ou une panne
d'appel sont non attribués et ne comptent jamais comme un succès.

**Évaluation** : le jeu est un fichier `version: 1` / `tasks:` de tâches V0/V1
closes, chacune avec ses résultats enregistrés par modèle
(`results: {<modèle>: {passed, cost_usd}}`). Le routeur est comparé à la base
« fixe » (V0 vers `cheap`, V1 vers `mid`) : pass^1 du routeur >= base - 1 tâche
et coût connu <= celui de la base. Un coût inconnu, un résultat manquant ou
moins de 20 tâches rendent « non mesuré » : on n'active pas.

## La file et l'admission : des réservoirs déclarés, jamais devinés

`task_list_ready` rendait les tâches dans l'ordre du ledger, rien ne bornait la
concurrence au claim et aucune sonde de ressource n'existait. Le lot K10 de
l'épopée #705 ajoute une file triée et une admission pure (aucun appel de LLM,
aucun démon) ; le routeur ne lit pas ce module, le quota relève de l'admission seule.

| Commande | Description |
| --- | --- |
| `grimoire queue plan [--json] [--no-routes]` | Ce que l'admission déciderait maintenant, à blanc : l'ordre de la file, les tâches admises, et pour chaque `Defer` sa raison ; sortie 1 si la capacité déclarée est illisible, 2 si une tâche est en famine |
| `grimoire queue rules [--json]` | Les règles d'usage de `capacity.yaml`, la consommation du jour (borne basse) et l'état de chaque règle ; lecture seule, sortie 1 si `capacity.yaml` est illisible, une règle invalide est listée « ignorée » |
| `grimoire queue release <tâche> [--pool ci] [--reason ...]` | Libère un créneau CI dont la fin des checks n'est pas observable (`capacity.released`) ; geste d'humain, l'origine humaine est exigée comme pour `task comment` |

**File** : priorité, puis vieillissement (+1 cran toutes les 24 h passées à l'état `ready`,
plafonné à `critical`), puis ancienneté. `TaskService.list_ready` et l'outil MCP `task_list_ready` rendent cet ordre.
Une tâche dont une dépendance `blocks` n'est pas close, ou dont le backoff de réadmission n'est
pas échu, reste `ready` mais sort de la file du plan (`hors file`).

**Admission** : `Admit`, ou `Defer(raison)` avec la date de reprise ou `unknown`. Toutes les
gardes échouent fermées. Ordre : pause, route différée par le routeur, WIP, `ci`, `host`, `llm`, quota.
Le **WIP** global et par rôle (rôle = `owner` de la tâche) vient de `capacity.yaml`, sinon de
`orchestration-policy.yaml` (`wip.limit_global`, `wip.limit_per_role`) ; il est appliqué **au claim**,
sous le verrou du ledger. Une limite illisible refuse le claim. Sans limite déclarée, rien n'est refusé.
Seules les tâches `claimed` et `running` comptent : un bail perdu occupe son créneau jusqu'à `task reap`.
La **reprise** `waiting → running` (simple ou attestée) repasse par la même garde : sans place, elle est
refusée (« Reprise refusée … elle attend une place ») et la tâche reste `waiting`.

**Capacité** : `_grimoire/standard/capacity.yaml` (facultatif, `version: 1`). Un pool non
déclaré n'est pas sondé ; une section invalide refuse la garde qu'elle porte, les autres continuent. La table `pools` est une seule section : une clé inconnue ou mal casée (`gpu`, `Ci`) la rend illisible et fait refuser `ci`, `host` et `llm` (`Defer(unknown)`), jamais ignorer un pool déclaré.

```yaml
version: 1
quota:
  tokens_per_day: unlimited   # défaut ; ou un entier > 0. 0 et les valeurs illisibles sont refusés, jamais lus comme « illimité »
pools:
  ci:
    runners: 3                # défaut 3 ; analysis et architecture ne prennent pas de créneau (exempt_types)
  host:                       # facultatif : lu dans /proc ; hors Linux ou illisible, Defer(unknown)
    max_load_per_cpu: 1.5
    min_mem_available_mb: 2048
  llm:
    claude: {sessions: 2}     # sessions concurrentes par fournisseur, d'après la route du dernier task.routed
wip: {limit_global: 5, limit_per_role: 2}
queue: {aging_hours: 24, famine_hours: 24}
```

**Occupation** : dérivée du ledger à chaque appel, aucun compteur ni fichier `held/`. Un créneau
`ci` est réservé dès le claim (type non exempté) ou par un push observé (`task.ci_pushed`) et reste
pris jusqu'à `task.ci_finished` ou `capacity.released`, tant que la tâche est `claimed`, `running`,
`needs_verification` ou `waiting` : se garer ne rend pas le créneau, les checks n'ont pas fini.
La clôture, l'annulation, l'échec ou le retour à `ready` le rendent. Si la fin des checks
n'est pas observable, le créneau reste pris et visible dans `queue plan` jusqu'à `queue release`.

**Quota** : `unlimited` est la valeur par défaut, acceptée par la configuration : le kit ne pose
alors jamais `Defer(quota)`, seul un refus du fournisseur le peut. Fini, le jour (UTC) est atteint
à la consommation connue, un minimum lu dans `quota.consumed` ; la décision porte la route du
routeur, inchangée. Si des appels ont eu lieu aujourd'hui sans chiffre de tokens, la consommation
est `unknown` : refusée en autonome (`GRIMOIRE_UNATTENDED=1`), affichée en interactif.

**Consommation enregistrée** : à chaque tick appliqué, le kit lit le journal de chaque session qu'il a lancée
(worker, relecteur, arbitre, session orchestrateur) et écrit un événement `quota.consumed` : `tokens` (le total de
`grimoire task usage` : entrée, sortie, cache lu et écrit), `cost_usd` si tous les appels sont tarifés, `model`,
`role`, `task_id`, `session`. Une session n'est comptée qu'une fois : la clé est son identifiant (le condensé SHA-256
pour les rôles dont le ledger ne garde que le condensé), l'événement porte le *delta* depuis le dernier et le total
cumulé, rejouer le tick n'écrit rien. C'est une **borne basse** (`status: lower_bound`) : le kit voit le journal de
l'hôte, pas la facture. Journal introuvable, illisible, au-dessus de 50 Mo ou d'un hôte non lu : `status: unknown` avec
la `reason` (stable, sans grandeur qui varie : un seul événement par changement d'état réel), jamais un chiffre
inventé ; un lancement sans identifiant de session est inconnu lui aussi ; une session lancée depuis moins de
10 minutes n'est pas déclarée inconnue faute de journal (il peut ne pas exister encore).

**Suivi tournant** : au plus 20 journaux sont lus par tick. Un journal dont la taille et la date n'ont pas changé
depuis sa dernière lecture n'en consomme pas (cache `_grimoire-output/control/quota-reads.json`, perdu il ne coûte
qu'une relecture) ; les autres passent par ordre de dernière lecture, la plus ancienne d'abord, donc aucune session
n'est affamée. Une session dont le journal a changé sans pouvoir être relue à ce tick est marquée inconnue
(« relecture en attente »), un journal déjà compté devenu trop gros aussi : la consommation du jour est alors
`partial` (jamais « complète » à tort) et suit `quota.on_unknown`, le dernier total connu restant la borne basse.
`grimoire queue rules` et la fiche Capacité disent combien de sessions sont suivies, inconnues, en attente de
relecture. La consommation est écrite **avant** le plan d'admission du tick.

Le cache ne porte que taille, date, instant de lecture et total vu : **jamais le chemin** d'un journal, recalculé à
chaque pas et confiné au dossier des journaux de l'hôte ; un cache faux ne fait donc au pire que relire. Il est lu comme
fichier ordinaire seulement (ni lien, ni tube, ni périphérique, taille bornée), sinon ignoré, et élagué des sessions
sorties du suivi. L'étape de lecture a aussi un budget de **temps** (20 s cumulées par pas, la première lecture ayant
toujours lieu) ; le reste est reporté au tick suivant, la consommation étant dite `partial`. Un journal trop gros ou à
relire pèse **tant que son fichier change** (sans limite de durée) ; il cesse de peser quand il reste immobile 48 h
(un événement `ongoing: false` l'acte, la consommation au-delà du dernier total restant non mesurée), qu'il a disparu ou
qu'il est relu. Une session dont le journal reste introuvable (hôte non lu) n'est plus cherchée après 24 h, mais reste
comptée inconnue pour son jour. Quand une consommation inconnue ou partielle diffère les lancements autonomes sous
`on_unknown: refuse`, la liste d'attention le dit une fois par jour et par cause, avec le geste pour en sortir.

**Jour d'un delta** : un delta est daté de son écriture, mais les appels dont l'horodatage précède minuit UTC se
comptent pour la veille (`day` dans l'événement). Limite restante : un appel sans horodatage lisible compte pour le
jour de la mesure, et une veille mesurée plusieurs jours plus tard est datée de la veille entière ; le coût d'un delta
coupé en deux est réparti au prorata des jetons.

**Consommation partiellement inconnue** (`quota.on_unknown`) : sous un plafond fini, quand une partie des sessions du
jour n'a pas de chiffre, le défaut est `refuse` (sens fermé : `Defer(unknown)` en autonome), sauf si le connu seul
atteint déjà le plafond (`Defer(quota)`). `count_known` ne compte que le connu : un réglage explicite du projet, à
choisir en sachant que le plafond réel peut alors être dépassé.

```yaml
quota:
  tokens_per_day: 2000000
  on_unknown: refuse          # défaut ; ou count_known
```

**Plafonds de sessions par jour du relecteur et du merger** : `orchestrator.roles.reviewer.sessions_per_day` et
`orchestrator.roles.merger.sessions_per_day` (`orchestration-policy.yaml`), même forme que celui du worker. **Pas de
limite par défaut** : en poser une aurait changé le comportement des projets existants, et le worker (5), l'arbitre (5)
et l'orchestrateur (12) en ont déjà une. Le relecteur compte ses lancements du jour (réussis ou non) ; le merger compte
les **reprises du worker qu'il déclenche** (`task.merge_bounced` du jour, escalades exclues), seul cas où il lance une
session. Au plafond, rien n'est lancé, aucune session en cours n'est touchée, la tâche ne change pas d'état ; le refus
est écrit au ledger (`role.session_cap_reached`, une fois par rôle et par jour) et une ligne de la liste d'attention
le dit jusqu'à minuit UTC.

**Règles d'usage** (`usage_rules:` dans `capacity.yaml`, par projet, activables une à une) :

```yaml
usage_rules:
  - {id: jour-gros, scope: day, metric: tokens, threshold: 2000000, action: warn}
  - {id: tache-chere, scope: task, metric: usd, threshold: 5, action: hold}
  - {id: trop-de-sessions, scope: project, metric: sessions, threshold: 200, action: pause, enabled: false}
```

`scope` : `task` (tous les lancements d'une tâche), `project` (depuis le début), `day` (aujourd'hui, UTC) ; la portée
« session » est déjà celle des politiques `per_session`. `metric` : `tokens`, `usd` (coût connu), `sessions`.
`action` : `warn`, `hold` ou `pause`. Les règles s'appliquent **à l'admission et au lancement seulement** : aucune
session en cours n'est tuée.

| Action | Effet |
| --- | --- |
| `warn` | une ligne dans la liste d'attention du tick ; rien n'est retenu |
| `hold` | le lancement est différé (`Defer(usage_rule)`, avec la règle franchie en détail) ; l'événement `usage_rule.hold` est écrit une fois par règle, tâche et jour ; la ligne d'attention dit comment lever |
| `pause` | pose la pause existante en mode `drain` (les sessions en cours finissent), l'acteur `usage-rule:<portée>:<id>` et la règle franchie comme motif ; une règle de portée `task` ne met en pause que cette tâche |

`hold` s'appelle ainsi, et non « question à l'humain » : une tâche `ready` ne peut pas passer en attente humaine
(le ledger l'interdit), le kit ne promet donc pas une question qui n'existe pas. Il retient le lancement et dit, dans
la liste d'attention, quelle règle est franchie. **Pour lever** : un humain relève le seuil ou met `enabled: false` sur la
règle dans `capacity.yaml` ; une règle de portée `day` se lève d'elle-même à minuit UTC. Aucun agent ne le fait.

**Pause posée par une règle** : jamais écrasée si une pause existe déjà (humaine ou autre), la règle le dit seulement.
Elle porte une origine propre (`usage-rule`, qu'aucune option `--actor` ne peut imiter), donc seul un humain la lève
(`grimoire orchestrate resume`) ; elle ne se lève **pas** quand la consommation repasse sous le seuil. Seule exception :
une pause de portée `day` (origine `usage-rule` ET acteur `usage-rule:day:<id>`) est levée par le tick au changement de
jour UTC. Une pause humaine, ou posée par une règle de portée `project` ou `task`, ne se lève jamais d'elle-même ; une
erreur à la levée remonte dans la liste d'attention sans faire échouer le tick.

**Pause d'une session d'agent** (`orchestrate pause`, `orchestrate resume`) : une session d'agent ne remplace jamais une
pause qui n'est pas la sienne (humaine, d'une règle, d'une autre session) : le geste est refusé et la pause reste ; elle
ne lève que sa propre pause. Un humain remplace et lève tout, comme avant.

**Règle invalide** : signalée (`capacity.yaml : usage_rules[2] ignorée : …` dans la liste d'attention et sous
« ignorées » dans `queue rules`) et ignorée ; les autres règles tiennent. Un `capacity.yaml` illisible reste fermé
(aucune règle, admission refusée comme avant). La consommation d'une règle est une borne basse : franchie sur le
connu, elle l'est pour de bon ; sous le seuil sur une consommation partiellement inconnue, elle le dit. Un coût
inconnu n'est jamais compté zéro. L'action « modèle moins cher » viendra avec la mesure par modèle.

```text
$ grimoire queue rules
Aujourd'hui (2026-11-26 UTC) : 0 tokens, 0.0000 USD, 0 session(s) — plafond 2000000 tokens/jour, consommation inconnue : refuse
                                  Règles d'usage
┏━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ règle       ┃ portée ┃ mesure ┃ seuil   ┃ action ┃ consommation ┃ état          ┃
┡━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│ jour-gros   │ day    │ tokens │ 2000000 │ warn   │ 0            │ sous le seuil │
│ tache-chere │ task   │ usd    │ 5       │ hold   │ 0            │ sous le seuil │
└─────────────┴────────┴────────┴─────────┴────────┴──────────────┴───────────────┘
usage_rules[3] (casse) ignorée : scope doit valoir task | project | day, pas 'nope'
```

**Qui peut changer quoi** : le plafond, les règles et le journal de consommation sont hors de portée des agents. Aucun
outil MCP, aucune route ni commande de la console du cockpit n'écrit `capacity.yaml` ni `quota.consumed` (un test le
prouve) ; la fiche Capacité du cockpit affiche la consommation du jour et les règles, en lecture seule.

**Appels de modèle du kit** : un appel que le kit facture lui-même (le modèle optionnel de l'Atelier,
[atelier-modele.md](../atelier-modele.md)) inscrit sa consommation connue par `quota_usage.record_kit_call` (origine
`atelier`, idempotent par identifiant de réponse) : elle entre dans la consommation du jour, les règles d'usage et la
fiche Capacité, sans second compte. Avant l'appel, le client lit la même source que l'admission : une règle `hold` ou
`pause` de portée `project` ou `day` franchie, ou un `capacity.yaml` illisible, refuse le geste. Le plafond local du
client reste en vigueur en plus.

**Famine** : une tâche différée depuis plus de 24 h (`queue.famine_hours`) monte au niveau 2
d'attention, sa raison est affichée.

## La pause : drain et hold, jamais un arrêt brutal

`PAUSED` n'était émis nulle part et rien ne disait « stoppe tout » sans tuer les
sessions. Le lot K11 de l'épopée #705 ajoute un **drapeau fichier**, lu sans ouvrir
le ledger, et un tick qui le lit en premier.

| Commande | Description |
| --- | --- |
| `grimoire orchestrate pause [--drain\|--hold] [--project\|--global] [--task <id>...] [--reason ...]` | Pose le drapeau (`drain` par défaut) ; `--global` (toute la machine) est un geste d'humain |
| `grimoire orchestrate resume [--global] [--task <id>...]` | Lève le drapeau, ou seulement ces tâches ; un humain lève tout, une session ne lève que la pause qu'elle a posée elle-même (son identifiant est noté dans le drapeau) ; `--global` reste un geste d'humain (l'origine humaine est exigée, comme pour `queue release`) |
| `grimoire orchestrate status [--json]` | Les drapeaux en vigueur et, pour chaque tâche tenue, ce que `hold` fait sur son hôte (« hold dégradé en drain (hôte sans frein) ») |
| `grimoire orchestrate tick [--apply\|--plan] [--json]` | Un tick : lit la pause, puis récolte, rend les attentes levées, constate les effets externes et calcule l'admission ; sans `--apply` (ou avec `--plan`), le même tick joué sur une copie du ledger, rien n'est écrit ; sous pause, sort en 0 sans ouvrir le ledger ; un second tick simultané saute, en 0 |
| `grimoire orchestrate install-timer [--interval 5m] [--no-enable]` | Pose le minuteur optionnel du tick (unité systemd utilisateur `oneshot` et son `.timer`) ; sans systemd, rend la ligne crontab à poser à la main |
| `grimoire orchestrate uninstall-timer` | Désactive et retire le minuteur du projet |

**Portée.** `_grimoire-output/control/pause.json` porte sur le projet (avec `tasks: [...]`, seulement
sur ces tâches) ; `$GRIMOIRE_CONTROL_HOME/pause.json` (par défaut `~/.grimoire/control/pause.json`) porte
sur tous les projets de la machine. Le tick et le frein lisent les deux et appliquent l'**union** :
`hold` l'emporte sur `drain`. Un drapeau **illisible** (JSON invalide, permission, mode inconnu) vaut `drain`
à son niveau, avec une ligne d'attention ; `resume` le supprime.

- **`drain`** : rien n'est admis (`Defer(pause)` au plan, claim refusé sous le verrou du ledger), les agents
  en cours finissent leur tâche. Une attente `waiting(ci|quota)` reprend ; une attente `waiting(paused)` attend la fin de la pause.
- **`hold`**, en plus de `drain`, sur un hôte dont le refus d'outil est prouvé pour sa version
  (`hosts.capabilities`) : le `PreToolUse` refuse les outils mutants d'une session qui tient une tâche, avec
  « pause : dépose ton point de reprise ». Le premier refus ouvre **une** écriture, `grimoire task wait <tâche> --reason paused`
  (ou l'action MCP `wait` avec `to=paused`), qui note la branche et le HEAD du worktree. Ensuite tout outil mutant
  reste refusé, les lectures passent et le `Stop` est permis. Sur un hôte sans frein, `hold` se dégrade en `drain`
  et le dit : la consigne « pause demandée : gare-toi » est injectée au tour suivant.
- **Flows.** À la frontière de node (le node vient d'être accepté, le suivant n'est pas encore ouvert), un `hold`
  pose le run en `PAUSED` (`workflow.paused`) : le node suivant n'est ni présenté ni exécuté, `flow resume` rend
  `paused: true` et les pilotes (`--executor dispatch`, `upgrade-flow`) s'arrêtent. Pendant la pause, `flow resume`
  refuse sans rien écrire ; une fois levée, il rouvre le node suivant (`workflow.resumed`, une seule fois).
  `drain` ne pose aucun run : ils finissent.
- **Tâche garée et frein du bail.** Une tâche `waiting` garde le bail de sa session pour la reprise, mais il ne se renouvelle
  pas pendant l'attente. Tant que l'outil tient dans le bail, rien ne change ; au-delà, le `PreToolUse` refuse les commandes
  de la session **sauf** `grimoire task resume|show|attach|renew <sa tâche>` (options `--epoch`, `--actor`, `--session-id`
  seulement, sans guillemets ni enchaînement) : la session reprend elle-même par `grimoire task resume <id> --epoch N`,
  qui renouvelle aussitôt le battement de cœur. Une autre tâche, une écriture ou une forme non reconnue restent refusées.
- **`resume`** : le drapeau est supprimé. Le tick suivant rend les `waiting(paused)` en `ready`, **dans l'ordre
  de la file** et sans leur faire perdre l'ancienneté d'avant leur claim. Une session restée ouverte lit à son tour
  « Pause levée : ta tâche … est de nouveau ready » et peut la re-claimer ; une session fermée retrouve `resume_command`.

Limites écrites : un outil MCP tiers n'est pas classé mutant par le kit (seuls `task_claim`, `task_update` et `task_add`
du kit le sont pour le frein) ; la pause ne reprend pas un effet distant déjà parti ; le bail d'une tâche garée reste tenu
jusqu'au réveil par le tick (K6) ; le frein ne juge pas une session qui ne tient aucune tâche ; un run de flow n'est pas
refusé au démarrage sous `hold`.

## Le tick : idempotent, rejouable, et son minuteur

Le lot K12 de l'épopée #705 rend le tick sûr à relancer, à interrompre et à prévisualiser.

- **Un seul tick à la fois.** `_grimoire-output/control/.tick.lock` (verrou `fsio`, sans attente) : un second tick
  qui le trouve pris saute, code 0. Avec `flock` (POSIX), un tick tué rend le verrou avec son processus. Sans `flock`
  (Windows), le repli est un fichier dont la date fait foi : le tick la rafraîchit tant qu'il tourne, pour qu'un tick
  de plus de 60 s ne soit pas cassé par un second ; un tick tué laisse ce fichier jusqu'à sa péremption (60 s). Le
  suivant reprend, et chaque étape relit le ledger sans réécrire ce qui y est déjà (récolte à barrière, différée
  unique, réveil rejoué) ; un effet relit l'état sur le disque sous son verrou, jamais un cache chargé avant lui.
- **Le plan est l'application.** `orchestrate tick --plan` (le défaut sans `--apply`) joue le tick appliqué sur une
  copie du ledger sous `_grimoire-output/control/tick-plan-*`, jamais sous `/tmp`, puis la retire : mêmes récoltes,
  mêmes réveils, et une admission qui voit les tâches que ces gestes rendent. Ni board, ni mémoire, ni dispatch ne bougent.
- **Le travail local n'est pas rendu.** Un bail perdu dont le worktree `task/<id>` porte des commits absents de toute
  branche distante, un arbre modifié ou un état git illisible passe en `waiting(human, resume_branch)` avec la question,
  au lieu de `ready`. Un worktree de tâche n'est jamais créé sous un dossier temporaire du système (`tempfile.gettempdir()`, qui suit `TMPDIR`, et aussi `/tmp`, `/var/tmp`, `/dev/shm` ; `%TEMP%` et `%TMP%` sous Windows).
- **Effets externes réconciliés.** Un push ou une PR passe par `effect.intended` puis `effect.observed` ; une intention
  sans constat est vérifiée sur la remote (`git ls-remote`, `gh pr list --head`) avant tout nouvel essai : présent,
  constaté sans être refait ; absent, fait une fois ; invérifiable, rien n'est fait. Un push est « présent » quand le
  commit poussé est la tête de la branche distante **ou son ancêtre** (la branche a avancé depuis) ; une branche
  disparue de la remote alors qu'une branche distante suivie localement contient le commit est « invérifiable »
  (supprimée après le push : la refaire la ressusciterait). Le tick constate, il ne refait pas. Les `git` de la
  récolte (`resume_branch`) tournent avant le verrou du journal, jamais dessous.
- **Journal tronqué.** Une dernière ligne coupée par une coupure de courant est ignorée au rejeu, l'ajout suivant ne s'y
  colle plus, et le tick l'affiche en attention.
- **Minuteur optionnel, désactivé par défaut.** `install-timer` écrit `grimoire-tick-<nom>-<empreinte>.service` et `.timer`
  sous `$XDG_CONFIG_HOME/systemd/user`, puis `systemctl --user enable --now` (sauf `--no-enable`). `orchestrate status`
  dit s'il est posé. Sans minuteur, après une coupure ou un forfait épuisé, rien ne reprend avant la prochaine session
  ou commande.

Limites écrites : sans `loginctl enable-linger`, un minuteur utilisateur ne tourne pas tant que l'utilisateur ne s'est pas
reconnecté après un redémarrage (non vérifié sur un poste) ; le banc de coupure (`pytest -m crash_bench`, scénarios A à H)
simule les points de coupure, il ne tue aucun processus ; un backoff se compte depuis l'horodatage d'écriture de la récolte,
si bien que plan et application diffèrent de quelques millisecondes sur cette date ; aucun job du kit n'appelle encore
`effects.perform` (la primitive et la réconciliation du tick existent, le push et la PR restent à brancher).
