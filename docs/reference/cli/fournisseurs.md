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
| `grimoire providers probe [<id>] [--json]` | Sonder (comme `audit`) puis rouvrir les fournisseurs fermés par une date de forfait illisible (`unknown`) ; une suspension à date n'est jamais levée |
| `grimoire providers quota [--json]` | Fenêtre de forfait lue dans les flux d'appel : `lower_bound` (minorant daté) ou `unknown`, jamais 0 |
| `grimoire providers history [--ledger-root <chemin>] [--json]` | Historique des dispatchs par couple (type de tâche, classe) et palier de départ recommandé |

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

**Famine** : une tâche différée depuis plus de 24 h (`queue.famine_hours`) monte au niveau 2
d'attention, sa raison est affichée.

## La pause : drain et hold, jamais un arrêt brutal

`PAUSED` n'était émis nulle part et rien ne disait « stoppe tout » sans tuer les
sessions. Le lot K11 de l'épopée #705 ajoute un **drapeau fichier**, lu sans ouvrir
le ledger, et un tick qui le lit en premier.

| Commande | Description |
| --- | --- |
| `grimoire orchestrate pause [--drain\|--hold] [--project\|--global] [--task <id>...] [--reason ...]` | Pose le drapeau (`drain` par défaut) ; `--global` (toute la machine) est un geste d'humain |
| `grimoire orchestrate resume [--global] [--task <id>...]` | Lève le drapeau, ou seulement ces tâches ; geste d'humain (l'origine humaine est exigée, comme pour `queue release`) |
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
