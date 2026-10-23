---
audience: utilisateur
---

# Travail en équipe : unités partagées par git

Le Mission Ledger sert un opérateur et ses agents sur une machine : il vit dans
`_grimoire-runtime-output/ledger/events.jsonl`, hors du dépôt, avec des baux de
20 minutes rafraîchis par battement de cœur, des sessions, des traces. Rien de
cela n'a de sens entre deux humains sur deux postes. Cette page décrit le plus
petit modèle de travail en équipe qui ajoute de la valeur sans toucher au mode
mono-opérateur, et la première tranche livrée (`grimoire team unit …`).

## Conception

### Ce qui se partage : des unités, pas le ledger

Une **unité** est un morceau de travail qu'un humain prend pour lui : un titre,
des critères d'acceptation, un titulaire, un bail grossier (en heures ou en
jours), une livraison prouvée et sa revue. Tout le reste reste local : un
humain qui tient une unité la découpe comme il veut en tâches de son ledger,
avec ses agents, ses claims, ses baux fins.

Partager le ledger entier a été écarté : il est fait d'événements à haute
fréquence (battements de cœur, transitions d'agents, sessions), ses identifiants
de session et ses chemins sont propres au poste, et sa sémantique suppose un
seul écrivain sous verrou. Le fusionner entre clones produirait des conflits
qu'aucune règle locale ne sait trancher.

### Support du partage : git, un fichier par événement

Les unités vivent dans le dépôt, sous `_grimoire/team/units/<unité>/`. Chaque
geste écrit **un nouveau fichier** `<id>.json`, jamais réécrit, où `id` est
l'empreinte SHA-256 (32 caractères hexadécimaux) du contenu de l'événement, qui
inclut un nonce aléatoire. Deux clones qui écrivent en même temps écrivent deux
fichiers de noms différents : `git pull` ne produit jamais de conflit textuel.

Chaque événement cite dans `prev` les têtes qu'il a vues. L'ordre se lit dans ce
graphe, jamais dans les horodatages : les horloges de deux postes ne se
comparent pas, et l'horloge de Windows a un grain d'environ 15 ms. Le champ `at`
est informatif ; seul le bail l'utilise (voir plus bas).

L'empreinte est recalculée depuis le JSON relu, pas depuis les octets : un
dépôt extrait avec `core.autocrlf=true` reste valide. Un fichier modifié à la
main ne l'est plus.

Le transport, c'est git et rien d'autre : `commit`, `pull`, `push`, une revue de
PR si l'équipe en fait. L'option `--commit` committe le seul fichier écrit ; la
commande ne pousse jamais.

### Identité : déclarée, pas prouvée

Un humain est identifié par l'adresse de `git config user.email` de son clone,
ou par `--as <qui>`. L'événement dit d'où vient l'identité (`identity` :
`git-config` ou `option`) et la sortie la présente comme **déclarée**. Une
identité inconnue (pas d'adresse configurée, pas de `--as`) est refusée.

Ce n'est pas une attestation : n'importe qui peut écrire `user.email`. Les
attestations du kit (tour humain d'une session, W2-01) prouvent qu'un humain a
parlé dans une session de l'hôte ; elles ne disent pas *lequel* parmi plusieurs
humains. Le chemin vers une identité prouvée est la signature des commits
(`git commit -S`, clés connues de l'équipe) vérifiée à la lecture : tranche
suivante.

### Cycle d'une unité

| Geste | Depuis | Vers | Qui | Garde |
|---|---|---|---|---|
| `publish` | (rien) | `open` | tout membre | titre et au moins un critère ; identifiant en minuscules (`U1` et `u1` sont le même dossier sous Windows et macOS) |
| `claim` | `open` | `claimed` | tout membre | exclusif ; bail de 48 h par défaut (`--hours`) |
| `renew` | `claimed` | `claimed` | le titulaire | repousse le bail, même expiré tant que personne n'a adopté |
| `release` | `claimed` | `open` | le titulaire | |
| `adopt` | `claimed`, bail expiré | `claimed` | un autre membre | l'événement cite le bail repris (`from`, `from_expires_at`) |
| `deliver` | `claimed` | `delivered` | le titulaire | au moins une preuve qui se résout contre git : un commit (SHA complet noté) ou un fichier du dépôt (empreinte notée) |
| `review --accept` | `delivered` | `done` | un autre que le livreur | |
| `review --reject` | `delivered` | `claimed` | un autre que le livreur | le livreur redevient titulaire, nouveau bail |
| `resolve --keep <tête>` | `contested` | état de la tête retenue | tout membre | cite toutes les têtes |

Le bail partagé est volontairement grossier. Il ne remplace pas le bail fin du
ledger local : il dit seulement aux autres humains « quelqu'un s'en occupe
jusqu'à telle date ». L'expiration est jugée par l'horloge de celui qui adopte,
au moment où il adopte ; l'événement le dit, il ne le prouve pas.

### Conflits : personne ne gagne d'office

Deux réservations de la même unité dans deux clones, écrites sur la même tête,
deviennent après fusion **deux têtes**. L'unité passe `contested`, et :

- toute écriture est refusée, sauf `resolve` ;
- `grimoire team unit list` montre chaque branche (titulaire, état) ;
- `grimoire team unit check` sort en code 1 : à lancer après `git pull`, dans un
  hook `post-merge` ou en CI.

Il n'y a pas de règle de départage automatique (plus petite empreinte, horodatage
le plus ancien) : une telle règle donnerait raison à l'un des deux sans
qu'aucun humain l'ait décidé, et l'autre découvrirait après coup que son travail
était fait pour rien. `resolve --keep <tête>` est le geste explicite ; la branche
écartée reste dans le dépôt, lisible. Le même mécanisme couvre tous les gestes
concurrents : une adoption pendant que l'ancien titulaire renouvelle sans avoir
tiré, deux publications du même identifiant, deux `resolve` différents.

Si un événement est illisible, porte une empreinte fausse ou décrit une
transition impossible, l'unité est `invalid`. S'il cite un parent absent
(clone partiel, fichier supprimé), elle est `incomplete`. Dans les deux cas rien
ne s'écrit : l'état inconnu n'est jamais pris pour un état valide. La réparation
passe par git (rétablir ou retirer le fichier fautif).

Dans un même clone, deux commandes simultanées ne s'intercalent pas : chaque
écriture relit l'unité sous un verrou posé dans le dossier git commun du clone
(sous `.git/`), jamais versionné.

### Ce qui reste local

Sessions, claims d'agents, baux fins et battements de cœur, réservations de
fichiers (W1-07), questions, pauses, traces, preuves de tâche : tout ce que porte
le Mission Ledger. Une unité partagée et les tâches locales qui la réalisent ne
sont pas encore liées (voir les tranches suivantes).

### Cockpit et kanban

Pas de vue d'équipe dans cette tranche. `grimoire -o json team unit list` donne
l'état complet de chaque unité (branches comprises), prêt à être lu par une
colonne « équipe » du cockpit.

### Ce qui est refusé

- **Un serveur central** (base partagée, API d'équipe) : une dépendance
  d'infrastructure de plus pour un outil local ; git suffit au rythme d'une
  réservation par jour.
- **Le temps réel** : une réservation n'est visible des autres qu'après leur
  `git pull`. Le conflit est détecté à la fusion, pas empêché à l'écriture. C'est
  le prix de l'absence de serveur ; il est rendu visible et sans perte.
- **Les refs git comme registre** (choix d'AI-DLC, `claim/<intent>/<unit>` en
  compare-and-swap) : exclusion forte, mais exige un `push` réussi au moment du
  claim et un remote commun ; ici le dépôt peut circuler par PR, par bundle ou
  hors ligne, et la contestation reste possible et visible.

## Commandes

```bash
grimoire team unit publish export-csv --title "Export CSV" --criterion "un test d'export vert" --commit
grimoire team unit list
grimoire team unit claim export-csv --hours 24 --commit
git push
# … sur un autre poste, après git pull
grimoire team unit check
grimoire team unit adopt export-csv --commit          # seulement si le bail est expiré
grimoire team unit deliver export-csv --evidence HEAD --evidence docs/export.md --commit
grimoire team unit review export-csv --accept --commit   # par un autre que le livreur
grimoire team unit resolve export-csv --keep 3f9a2c1b   # sur une unité contestée
```

`--as <qui>` remplace l'identité git ; `--project-root` désigne le dépôt.

## Tranches suivantes

1. Lier une unité aux tâches du ledger local (`task create --unit`) et refuser
   `deliver` tant que ces tâches ne sont pas `done` avec leurs gates.
2. Vue d'équipe du cockpit (colonne « équipe » du kanban, unités contestées en
   tête) sur la sortie JSON de `team unit list`.
3. Identité prouvée : commits signés, liste des clés de l'équipe versionnée,
   vérification à la lecture (une unité dont un événement n'est pas signé par une
   clé connue est signalée).
4. Dépendances entre unités (une unité ne se réserve que si celles dont elle
   dépend sont `done`), et réservation de fichiers à l'échelle de l'équipe.
5. Hook `post-merge` installé par `grimoire init` qui lance `team unit check`.
6. Revue multiple (N relecteurs), et lien entre la revue d'une unité et la revue
   de la PR qui la livre.
