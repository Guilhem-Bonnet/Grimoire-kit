---
audience: contributeur
---

# Atelier de spec : le moteur de la conversation de cadrage

Le cadrage de l'Atelier devient une conversation. Le principe d'architecture est simple : **le programme tient le
cadre, les modèles ne font que des gestes bornés**. Ce document décrit le moteur côté serveur (lot A de l'épique
#1477) : l'état d'une conversation, la machine qui décide de la suite, les gestes qu'un moteur peut faire, le
rangement dans le document, l'enregistrement et les routes. Il n'y a ni interface nouvelle ni modèle d'IA : le moteur
fourni, `MoteurSansModele`, fonctionne par règles.

## Les modules

| Module | Rôle |
|---|---|
| `core/cadrage_conversation.py` | L'état (tours, éléments, notes, question en cours), sa validation à la lecture |
| `core/cadrage_conversation_flow.py` | La machine à états : l'étape suivante, le « pourquoi », le résumé en deux lignes |
| `core/cadrage_conversation_engine.py` | L'interface `Moteur`, le moteur sans modèle, le gardien `Garde` |
| `core/cadrage_conversation_document.py` | La seule porte d'écriture dans les fichiers de phase |
| `core/cadrage_conversation_turn.py` | Les opérations : démarrer, tour, décision, pause, classement, lectures |
| `core/cadrage_conversation_store.py` | Le fichier `conversation.json` : lecture validée, écriture atomique sous verrou |
| `core/cadrage_conversation_hygiene.py` | Sommeil, classement doux, réouverture, liste des conversations |
| `tools/workspace_atelier_conversation.py` | Les routes et le point d'injection du moteur |

## L'état d'une conversation

Une conversation appartient à un chantier et vit dans `_grimoire/cadrage/<chantier>/conversation.json`. Elle porte :

- les **tours** (qui parle, texte, date, geste) ;
- **ce qui est compris** : des éléments rangés par rubrique, chacun avec sa **source** (le numéro du tour) et son
  **statut** : `propose`, `confirme`, `corrige` ou `ecarte` ;
- les **questions ouvertes** et la liste **pour plus tard** (le hors-sujet noté) ;
- le **mode** : `cadrage`, `brainstorm`, `pause` ou `termine` ;
- la **question en cours** (genre, texte, rubrique visée, pourquoi on la pose) ;
- un numéro de version `rev`, qui change à chaque écriture, et un champ de schéma.

Le fichier suit le statut de `comments.json` et `entretien.json` : il est **versionnable** (seuls ses verrous sont
ignorés par git).

## La machine à états

`prochaine_etape` est une fonction pure de la conversation et de l'avancement réel de l'entretien. Règles, dans cet
ordre :

1. le mode `termine` ou `pause` l'emporte ;
2. une demande qui n'appelle pas d'entretien (`rien`) ou une action à rétablir d'abord (`reporte`) ne pose aucune
   question ;
3. tant qu'un élément compris attend un oui, le programme **reformule** (`confirmer`) ;
4. en mode brainstorm, il invite à lister des pistes ;
5. un angle **obligatoire à cette profondeur** pas encore couvert donne la prochaine question, dans l'ordre des angles
   du kit ; le programme dit ce qu'il manque (« il ne me manque plus que… ») ;
6. l'essentiel couvert (ce qui compte pour le gate de l'entretien), il **propose de passer au document** ;
7. après un refus, il affine (seconde question des angles obligatoires), puis pose les questions facultatives, puis
   dit qu'il n'a plus de question.

Il ne pose jamais une question sans objet pour ce besoin (les angles non obligatoires viennent après l'essentiel) et
chaque étape porte son **pourquoi**, en mots simples.

## Les gestes d'un moteur

`Moteur` est le protocole que le lot B implémentera avec de vrais modèles. Une méthode par geste, entrées et sorties
petites et typées :

| Geste | Entrée | Sortie |
|---|---|---|
| `formuler_question` | `EntreeQuestion` | un texte |
| `classer_reponse` | `EntreeClassement` | une classe de la liste fermée `CLASSES` (jamais « oui », voir plus bas) |
| `extraire_elements` | `EntreeExtraction` | des `ElementExtrait` (texte, rubrique) |
| `reformuler` | `EntreeReformulation` | un texte |
| `proposer_pistes` | `EntreePistes` | une liste de pistes |
| `repondre_question` | texte, pourquoi | un texte |
| `est_tour_lourd` | texte, étape | un booléen (l'interface montre la réflexion) |
| `limites` | rien | ce que le moteur ne sait pas faire, dit honnêtement |

Un moteur ne reçoit **jamais** de chemin, de document ni de moyen d'écrire. `Garde` enveloppe tout moteur : type,
longueur, classe et rubrique de la liste fermée sont vérifiés ; chaque appel est borné dans le temps ; une exception,
une lenteur ou une sortie hors cadre est refusée et remplacée par le moteur sans modèle, avec une note que l'interface
peut montrer.

### Le moteur sans modèle

Questions tirées des amorces du kit, redites au tutoiement et sans jargon ; classement par règles simples (réponse,
« je ne sais pas », « sans objet », « plus tard », « pause », « on brainstorme », « pourquoi tu me demandes ça ? ») ;
reformulation par reprise fidèle (« Si je comprends bien : … ») ; aucune piste inventée, la personne liste les siennes
et le programme les range. Limites, dites à l'écran : pas de reformulation avec ses mots, pas de réponse à une question
autre que « pourquoi », pas de détection fine d'une contradiction (seulement les tournures explicites).

## Ce que le moteur ne décide jamais

Rien n'entre dans le document, et aucun angle n'est marqué couvert ou écarté, sans un geste **explicite de l'humain,
reconnu par le programme**, portant sur un texte qu'il a vu tel qu'il sera écrit. Un moteur (demain un modèle de langage)
est traité comme un auteur hostile :

- **« Oui » n'est pas une classe de moteur.** Le programme le reconnaît seul, par une règle lexicale stricte
  (`est_un_oui` : seulement des mots d'accord, cinq mots au plus, aucune négation, aucun ajout), et seulement quand une
  confirmation est en attente. « Oui mais… », « non », « oui, et aussi X » ne sont pas un oui. Une classe hors de la liste
  fermée rendue par un moteur est refusée. **Une question n'est jamais un oui** : tout texte contenant un point
  d'interrogation (`?`, pleine chasse `？`, espagnol `¿`, arabe, grec, interrobang `‽`, point d'ironie `⸮`…) est refusé : la
  liste connue, puis tout caractère dont le nom Unicode contient « QUESTION » ou « INTERROBANG » ; « d'accord ? »,
  « oui ? » ou « c'est bon ? » demandent quelque chose et n'accordent rien.
- **Ce que l'humain confirme, c'est la liste exacte** des textes qui seront écrits, avec leur rubrique en mots simples,
  construite par le programme à partir des éléments réels (`a_confirmer`). La reformulation du moteur l'accompagne, jamais
  ne la remplace.
- **« Je ne sais pas » et « sans objet » ne font que proposer.** Le classement sans modèle ne les retient que si la réponse
  n'est QUE la formule (« ça dépend, en gros un export » est une réponse, dont le texte est conservé). Même retenus, ils
  produisent une proposition (« Tu veux qu'on note que tu ne sais pas encore ? ») appliquée par le seul « oui » de l'humain,
  avec les mêmes fichiers qu'un `answer(unknown=True)` ou `skip=True` fait à la main. Si l'humain dit « je ne sais pas »
  après avoir dit autre chose, ce qu'il avait dit devient la note de la question ouverte.
- **Un « oui » ne confirme que ce que le message montre.** Le groupe d'un « oui » est borné à ce que le message rendu à ce
  tour écrit en entier : trois éléments au plus (`MAX_GROUPE`) et 700 caractères de texte au plus (`MAX_GROUPE_CHARS`),
  toujours au moins un. Le message énumère tout le groupe, jamais « et N autres » ; le reste attend et se propose au tour
  suivant, avec son propre « oui ». L'essai en terminal (`scripts/cadrage-converse.py`) affiche cette liste exacte
  (texte, rubrique, origine) sous le message.
- **Un élément se confirme seul** (jamais par le « oui » du groupe) quand il vise une autre rubrique que celle de la
  question, ou quand son texte n'est pas « les mots de l'humain » (`origine` = `moteur`, mesurée par le programme). Un
  élément n'est « les mots de l'humain » que s'il reprend le **tour entier** ou une **proposition entière** de ce tour
  (découpée par `. ! ? ;`, `…` et retours à la ligne), à la casse, aux accents et à la ponctuation près. Un fragment plus
  court (« export CSV » tiré de « je ne veux pas d'export CSV », « sauf les bots », « ni les invités ») a perdu son contexte,
  donc peut-être sa négation : il est du moteur. Seule exception, un élément **tronqué par le programme** reprend le début
  d'une proposition, à condition que la suite coupée ne contienne aucune négation ni restriction (ne, pas, jamais, aucun,
  sans, sauf, ni, mais, hors, à condition, seulement si, exclu, interdit, plutôt que, à l'exception de…). La troncature est
  un **drapeau** (`ElementExtrait.tronque`) posé par le programme à l'instant où il coupe, jamais déduit du texte : le
  gardien ne le retient que de **sa propre instance** du moteur sans modèle (il la construit lui-même : une instance fournie,
  même de cette classe, est remplacée, donc une méthode patchée ne sert à rien). Un texte venu d'un autre moteur qui finit
  par « … » n'est « les mots de l'humain » que si l'humain a lui-même écrit ces points de suspension. La liste de mots de
  restriction ne sert qu'à cette troncature. **Un élément ne se confirme en groupe que si le tour ne contient que lui** :
  l'un des éléments est le tour entier, ou chaque proposition du tour est elle-même un élément du groupe. Dès qu'une autre
  proposition du tour est hors du groupe (« Sauf les bots », « Uniquement en lecture », « Jamais le week-end », « Les
  invités non »…), elle peut retirer ou restreindre ce que les autres accordent : confirmation individuelle, sans liste de
  mots. Cinq éléments au plus par tour, longueur bornée.

## Le rangement dans le document

Un élément n'entre dans un fichier de phase qu'une fois **confirmé** (ou corrigé par l'humain), et il y entre par
`cadrage_interview.answer` : même ligne (`- *question* réponse`), même trace dans `entretien.json`, même refus
d'écrire dans un document au statut accepté. Retirer ou corriger un élément déjà rangé passe par `retract`, qui retire
la dernière réponse d'un angle : le programme retire ce qui a été rangé après lui, puis lui, et réécrit ce qui reste.
Le gate de `cadrage check` n'est jamais modifié.

- **La trace dit qui a écrit.** La trace de l'entretien porte, pour chaque ligne écrite par une conversation, une clé
  `origin` (`conv:<création>:<élément>`), validée à la lecture ; l'entretien guidé n'en met pas. Le programme ne retire
  que les lignes dont la trace porte l'origine de l'élément. Un `conversation.json` forgé qui annonce `ecrit: true` sur le
  texte d'une réponse de l'humain n'y change rien : l'élément repasse « non écrit » et l'alerte est rendue
  (`alertes`).
- **Écrire dans le bon ordre.** Avant la première écriture, tout ce qui peut échouer est vérifié (document accepté,
  place dans le fichier). L'intention est enregistrée dans la conversation (`intention: ecrire`), le document est écrit,
  l'intention est levée. Après une panne, l'opération suivante termine (la trace porte l'origine) ou annule (elle ne
  la porte pas) et le dit. Quand un retrait précède l'écriture (correction, remplacement ou écart d'un élément déjà rangé),
  le journal porte aussi l'élément retiré et ceux rangés après lui : une panne en route ne laisse jamais une ligne de
  l'humain « confirmée » mais absente du document, elle repasse à confirmer (ou, si son remplaçant est écrit, est écartée).
  Une conversation forgée dont la question en cours est « confirmer » sans rien en attente ne lève pas : rien n'est écrit,
  la question est recalculée.
- **Reprise d'une panne pendant une correction.** Si la trace de l'entretien d'un élément écrit porte un autre texte que
  celui consigné dans `conversation.json` (panne pendant la réécriture de ce qui suit un élément corrigé), la vue et la
  reprise relisent la LIGNE RÉELLE du fichier de phase (repérée par l'origine) : si elle concorde avec la trace, une alerte
  lisible est levée et l'élément repasse « à confirmer » avec ce texte EXACT (affiché en entier, confirmation individuelle,
  proposé en premier, `origine` = `document`, libellé « texte relu dans le document »). Fail-closed : si la trace et le
  fichier divergent entre eux, si le texte n'est pas identique à lui-même après nettoyage, ou s'il y a plusieurs lignes de
  même origine, il n'y a que l'alerte : rien n'est proposé, écrit ni retiré. Rien n'est écrit ni retiré seul : l'élément
  reste propriétaire de sa ligne, donc le confirmer ne la réécrit pas, et le corriger ou l'écarter agit sur ce que le
  document contient vraiment. Jamais deux lignes ni deux réponses de même origine : écrire refuse si l'origine existe déjà.
  `document_modifie` n'est vrai que si un fichier a réellement changé (reconfirmer une ligne conforme ne le change pas).
- **Reprise d'une panne pendant un remplacement.** Le remplaçant est proposé d'abord, seul ; une fois confirmé, l'ancien
  élément (redevenu « à confirmer », sa ligne ayant été retirée) est écarté sans nouvelle question.
- **Limite de tours.** Près de 500 tours, la conversation se termine d'elle-même en le disant, sans erreur ; corriger ou
  écarter ce qui est compris reste possible.
- **Identifiants.** Le compteur rattrape le plus grand numéro existant ; un fichier à identifiants en double reste
  lisible (champ `reparer`) mais refuse toute écriture jusqu'à réparation à la main.

## Le moteur travaille hors du verrou

Chaque opération est jouée deux fois. D'abord à blanc, **hors du verrou** du fichier, sur une copie jetable des fichiers du
chantier, avec le vrai moteur : ses sorties validées sont mémorisées. Puis sous le verrou, avec `rev` revalidé (409 s'il a
changé), l'opération est rejouée avec ces sorties, sans rappeler le moteur. Un moteur qui bloque ne retient donc jamais le
fichier. Les appels abandonnés au délai sont comptés : au-delà de quatre fils encore en vie, plus aucun appel n'est lancé
(repli immédiat). La lecture d'état n'attend le moteur que le temps de lui demander ses limites (une fraction de seconde).
Une fabrique de moteur qui lève, tarde ou ne rend rien donne le moteur sans modèle avec une note, jamais une erreur 500.

## Enregistrement, pause, reprise, hygiène

La conversation est enregistrée à **chaque** opération, sous le verrou du fichier. La reprise redit où l'on en est en
deux lignes, calculées par le programme (avancement puis prochaine question). Une conversation sans activité passe
**en sommeil** (3 jours par défaut), puis **classée** (14 jours) ; classée veut dire conservée, retrouvable, hors de la
liste active, jamais supprimée. Le statut se déduit de la date et d'une horloge injectée. Au plus 5 conversations sont
proposées comme actives. Réglages, dans `project-context.yaml` :

```yaml
cockpit:
  atelier_conversation:
    sommeil_jours: 3
    classee_jours: 14
    max_actives: 5
```

## Les routes et leurs gardes

Toutes sont sous `/api/workspace/atelier/`, donc derrière les gardes communes de l'Atelier (drapeau
`cockpit.test_features.atelier`, jeton de page, origine, JSON, journal des mutations). Le chantier est retrouvé dans la
liste réelle des chantiers, jamais construit depuis le slug.

| Route | Effet | Garde propre |
|---|---|---|
| `GET chantiers/<slug>/conversation` | l'état | lecture seule, fichier hors cadre : `erreur` |
| `GET chantiers/<slug>/conversation/compris` | ce qui est compris, par rubrique | lecture seule |
| `GET conversations` | actives, autres, classées, en erreur | lecture seule |
| `POST …/conversation/demarrer` | démarre ou reprend | corps vide |
| `POST …/conversation/tour` | un tour de l'humain | `rev`, `texte` ; 409 si `rev` a changé |
| `POST …/conversation/elements/<id>` | confirmer, corriger, écarter | `rev`, `decision`, `texte` |
| `POST …/conversation/pause`, `terminer`, `classer`, `rouvrir` | change le mode ou le classement | `rev` |

Aucun corps n'accepte un autre champ que ceux du tableau : pas de phase, de rubrique ni de chemin. Un élément déjà
tranché ou une conversation en pause, terminée ou classée donne 409 ou 400 sans rien écrire.

## Où brancher les lots suivants

- **Lot B (modèles)** : `workspace_atelier_conversation.set_moteur(fabrique)` est l'unique point d'injection ; la
  fabrique reçoit la racine et le slug et rend un objet qui respecte `Moteur`. Le gardien reste autour. Les choix
  d'autorisation et de repli vivent dans la fabrique, pas dans le programme.
- **Lot C (scène d'appel)** : lit `GET …/conversation` (vue Conversation) et `…/compris` (vue Document) ; `rev` est à
  renvoyer à chaque écriture. Pour que la confirmation porte sur le texte exact, l'interface affiche **`a_confirmer`**
  (dans l'état et dans la réaction d'un tour), pas seulement le message de la mascotte : pour chaque entrée, `texte` (ce
  qui sera écrit, mot pour mot), `rubrique.nom` (où), `action` (noter, noter comme question ouverte, écarter comme sans
  objet), `note` (ce que l'humain avait dit avant un « je ne sais pas »), `origine` (`humain`, `moteur` ou `document` : à
  signaler, « reformulé par le moteur » ou « texte relu dans le document ») et `individuel` (pas de « oui » de groupe : un bouton Confirmer par entrée, qui appelle
  `elements/<id>`). Elle affiche aussi `alertes` (ce que le programme a refusé de croire ou repris après une panne) et
  `reparer` (fichier à réparer), et grise les gestes quand `mode` n'est plus `cadrage`. Le tour enregistré (1500
  caractères) et la question en cours (600) sont coupés, pas `a_confirmer` : la vue la recalcule à l'identique depuis l'état,
  texte entier de chaque élément, y compris en pause ; les réactions de `pause` et de `demarrer` portent la même liste.
- **Lot D (voix)** : la réaction d'un tour porte `accuse` (texte du programme, immédiat), `message` et `lourd` : une
  interface peut dire l'accusé tout de suite et attendre le message.
- **Lot E (rédaction finale)** : lit `ranges()` et les sources (numéro de tour) pour relier chaque passage à l'échange ;
  il n'a pas d'autre porte d'écriture que `cadrage_conversation_document`.

Un essai sans interface : `python scripts/cadrage-converse.py --chantier <slug>`.
