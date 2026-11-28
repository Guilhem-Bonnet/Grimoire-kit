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
| `core/cadrage_conversation_redaction.py` | Le geste « proposer une rédaction », sa validation, la proposition enregistrée (lot E) |
| `core/cadrage_conversation_redaction_ops.py` | Proposer, accepter, écarter, reprendre une rédaction ; la trace de chaque passage (lot E) |
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
| `proposer_redaction` (facultatif, lot E) | `EntreeRedaction` | une `Redaction` (phrases, fondements) ; voir « Le document à la fin » |

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
| `GET …/conversation/redaction` | rubriques rédigeables, propositions en attente, passages écrits et leur trace | lecture seule |
| `GET …/conversation/redaction/<id>` | une rédaction (tout statut) et la trace de chaque phrase | lecture seule |
| `POST …/conversation/redaction/proposer` | propose une rédaction ; n'écrit rien dans le document | `rev`, `rubrique` (clé de la liste fermée) |
| `POST …/conversation/redaction/<id>/accepter` | écrit la rédaction (le seul geste qui écrit) | `rev`, `texte` (le texte exact affiché) |
| `POST …/conversation/redaction/<id>/ecarter`, `reprendre` | ferme la proposition, sans rien écrire | `rev` |

Aucun corps n'accepte un autre champ que ceux du tableau : pas de phase, de section ni de chemin. La seule rubrique
qu'un corps peut nommer est la clé `phase/section` de `proposer`, retrouvée dans la liste fermée (sinon 400). Un élément
déjà tranché ou une conversation en pause, terminée ou classée donne 409 ou 400 sans rien écrire.

## Les moteurs à modèle (lot B2)

Le moteur sans modèle reste le défaut. Un vrai modèle ne parle que s'il est **autorisé pour l'usage** par l'humain (page
« Modèles autorisés », voir [Fournisseurs](cli/fournisseurs.md)) : sans aucune autorisation, rien n'est détecté, rien n'est
appelé, la conversation fonctionne comme avant.

| Module (`tools/atelier_engines/`) | Rôle |
|---|---|
| `fabrique.py` | À chaque tour : `choose_for(usage)` décide ; rend le moteur et ce que la vue en dira (`Rendu`) |
| `moteur.py` | `MoteurModeles` : le protocole `Moteur`, un geste = un appel à sortie JSON validée |
| `gestes.py`, `schema.py` | L'invite et le schéma de sortie de chaque geste ; validateur strict (aucun champ en trop) |
| `transport.py` | Transport minimal (https, ou http vers la machine ; ni redirection ni proxy) et erreurs `Engine*` |
| `ollama.py`, `openai_compat.py`, `anthropic.py`, `gemini.py` | Les adaptateurs |

**Quel modèle, quand.** Les usages sont ceux de `direct_use` : `conversation` (le modèle rapide : classer, extraire, formuler,
reformuler, répondre) et `brainstorm` (le modèle plus fort : proposer des pistes ; il demande au moins un palier
« intermédiaire »). L'usage `redaction` porte le geste « proposer une rédaction » (lot E), mais la fabrique ne lui donne
pas encore de modèle : la rédaction est celle du programme (voir « Le document à la fin »). La décision est recalculée à CHAQUE tour et à chaque lecture,
sans mémoire : un modèle devenu indisponible, une autorisation retirée ou une empreinte périmée change le moteur au tour
suivant, sans casser la conversation. Le modèle « courant » de chaque usage est le premier de la liste de l'humain : s'il
tombe, on ne bascule que vers un modèle ni plus coûteux ni plus exposé ; sinon `choose_for` PROPOSE, le tour est rendu par
le moteur sans modèle, et la vue dit pourquoi et ce qui est proposé. La politique de données, la règle d'usage et les plafonds
de jetons s'appliquent comme partout.

**Adaptateurs.**

- Ollama : seulement une adresse de la machine reconnue (`127.0.0.1`, `localhost`, `::1`), sortie contrainte par le schéma.
- Serveur au format OpenAI : adresse passée par `admitted_gateway_url` ; une passerelle (`provider_type: litellm`) reçoit la
  clé du magasin, sauf si son identifiant est celui d'un fournisseur documenté ; un serveur `local` en boucle locale n'a pas de clé.
- API Messages d'Anthropic : le client de `atelier_model.py` (URL fixe, clé de l'environnement ou du magasin) ; la
  consommation est inscrite au compteur du jour et au quota du kit (`account_response`), donc les plafonds la voient.
- API Gemini de Google (`gemini.py`, fournisseur `google-gemini`, alias `gemini` et `google`) : voie native `generateContent`
  avec sortie JSON contrainte (`responseMimeType` + `responseJsonSchema`), clé en en-tête `x-goog-api-key` (jamais dans l'URL),
  adresse officielle construite par l'adaptateur (jamais lue du registre ; un fournisseur Gemini avec `base_url` n'a pas
  d'adaptateur). Le registre par défaut ne le déclare pas ; l'entrée à ajouter est dans `docs/reference/cli/fournisseurs.md`
  (« Gemini dans la conversation de cadrage »). Étiquettes : le texte sort de la machine, payant à l'usage, aucun appel sans
  autorisation de l'humain ; sa consommation n'est pas comptée par le kit. Sources :
  https://ai.google.dev/gemini-api/docs/structured-output et https://ai.google.dev/api/generate-content.
- L'agent installé (« Claude Code » et autres hôtes ACP) n'a **pas d'adaptateur** : le kit sait sonder sa connexion, pas lui poser
  une question sans lancer un agent autonome. Il n'est jamais appelé.

**Destination liée à l'autorisation.** La fabrique relit la spec du registre UNE fois par tour, recalcule son empreinte et la
compare à celle que l'autorisation désigne : si le registre a changé depuis (même dans la fenêtre du cache de détection ou
entre la décision et la construction du client), rien ne part, ni texte ni clé, le tour retombe sur le moteur sans modèle et
la vue dit « ce modèle a changé depuis ton autorisation ». L'échéance du délai vaut pour l'appel entier (lecture par morceaux),
pas seulement pour chaque opération de socket.

**Limites connues.**

- Les plafonds de jetons ne voient que la consommation d'Anthropic (compteur de l'Atelier) : les appels aux passerelles et à
  Ollama ne sont pas comptés, un plafond posé sur eux n'est pas vérifiable. Une consommation annoncée absurde ou nulle n'est
  pas prise telle quelle : une estimation plancher est comptée et dite.
- Le caviardage des secrets couvre les formats de clés connus ; un mot de passe écrit en clair dans un message n'est pas reconnu
  et part avec le texte vers un modèle distant.
- Une passerelle ne reçoit la clé du magasin que si son identifiant n'est pas celui d'un fournisseur documenté (l'identifiant
  `litellm` est admis, comme par la sonde de capacités). La sonde connaît aussi une table d'adresses de fournisseurs qui n'est
  pas exposée par `providers` : un identifiant que seule cette table reconnaît n'est pas refusé par la fabrique.
- Un agent installé placé en tête d'une liste n'est jamais appelé : il est écarté de la décision, les autres modèles autorisés
  parlent à sa place et la vue le dit.

**Un geste, un appel borné.** L'entrée est caviardée des secrets puis bornée (3 000 caractères au plus), citée comme donnée
entre `<donnee>` (balises neutralisées), sans chemin ni document. La sortie est un objet JSON validé par le schéma du geste :
champ en trop, type faux, texte hors bornes, JSON invalide, réponse de plus de 256 Kio ou de plus de 20 000 caractères sont
refusés. Le gardien revalide ensuite (classe et rubrique de la liste fermée) : un modèle ne peut ni rendre « oui » (le
programme seul le reconnaît), ni viser une rubrique inconnue, ni faire écrire quoi que ce soit. Les phrases du programme
(pause, fin, passage au document, « pourquoi ») ne passent pas par le modèle. Délai court, réglable (défaut 6 s, de 0,5 à 7,5 s ;
le gardien abandonne un appel à 8 s) :

```yaml
cockpit:
  atelier_conversation:
    moteur_delai_s: 6
```

Délai dépassé, erreur réseau ou sortie hors cadre : le geste est rendu par le moteur sans modèle pour ce tour, et la note du
tour dit la raison (`moteur.notes` : le nom de l'erreur, `EngineDelai`, `EngineReseau`, `EngineSortie`…). Aucune clé n'apparaît
dans une erreur, une note, un journal ou la vue : les messages sont fixes.

**La vue dit toujours qui parle.** L'état et chaque réaction portent `moteur` :

| Champ | Sens |
|---|---|
| `id`, `libelle` | le modèle qui parle (`sans-modele` sinon) |
| `local`, `sortie_de_texte` | rien ne sort de la machine, ou du texte sort (vrai dès qu'UN usage actif en envoie) |
| `raison` | pourquoi ce modèle, ou pourquoi aucun |
| `proposition` | `{key, libelle, raison}` : un modèle proposé, jamais utilisé seul |
| `reflexion`, `etat` | `true` / `"reflechit"` tant qu'un appel du modèle fort est en cours |
| `par_usage` | le même détail pour `conversation` et `brainstorm` |

**La réflexion.** Le moteur est synchrone par tour : l'appel du modèle fort (les pistes du brainstorm) reste synchrone. Pendant
cet appel, un marqueur EN MÉMOIRE, borné et daté, est posé pour le chantier (levé à la fin, même en erreur, périmé tout seul) :
la réaction du tour porte `lourd: true` et une lecture de la conversation qui arrive pendant l'appel (le serveur est à fils)
voit `moteur.reflexion: true`. Aucun fil de fond, aucun état sur disque. Rendre le résultat « au tour de lecture suivant »
demanderait de changer le contrat du moteur (un appel détaché dont le résultat serait repris au tour suivant, donc un
état partagé entre requêtes, un fil par appel et une règle de péremption) : non fait ici.

Essai manuel contre l'Ollama local, jamais lancé par les tests : `python scripts/atelier-moteur-essai.py --modele <nom>`.

## Où brancher les lots suivants

- **Lot B (modèles)** : fait, voir ci-dessus. `workspace_atelier_conversation.set_moteur(fabrique)` reste l'unique point
  d'injection (la fabrique par défaut est celle de `atelier_engines` ; `set_moteur(None)` rétablit le moteur sans modèle) ; la
  fabrique reçoit la racine et le slug et rend un objet qui respecte `Moteur`, ou un `Rendu` (moteur et présentation). Le
  gardien reste autour.
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
- **Lot E (rédaction finale)** : côté serveur, fait (voir ci-dessous). L'interface viendra avec la scène d'appel : elle
  affiche `proposition.texte` tel quel et le renvoie à l'acceptation, et montre pour chaque phrase ses `fondements` et
  `tours` (« voir d'où vient cette phrase »).

## Le document à la fin (lot E)

Quand ce qui est compris est confirmé, la mascotte peut proposer une **rédaction** plus lisible d'une rubrique. Le
principe ne change pas : le moteur propose, l'humain décide, et rien n'entre dans le document sans un geste explicite sur
le texte exact affiché.

**Le geste borné.** `proposer_redaction(EntreeRedaction) -> Redaction`. L'entrée : la rubrique (`phase/section`), son nom
en mots simples, et les éléments **confirmés et rangés** de cette rubrique, et eux seuls (identifiant, texte). La sortie :
la rubrique visée et des phrases, chacune avec la liste des identifiants des éléments qui la fondent. Le gardien la
revalide (`valider_redaction`) et la refuse si :

- elle n'est pas une `Redaction`, ou vise une autre rubrique que celle demandée (ou « Questions ouvertes », hors cadre) ;
- elle a zéro ou plus de 12 phrases, une phrase sans fondement ou avec plus de 12, un identifiant inconnu ou non confirmé ;
- une phrase dépasse 800 caractères, le tout 4 000, ou une phrase commence par une marque de mise en forme (`#`, `*`, `-`,
  `>`, `|`, `<`, `` ` ``…) ; les retours à la ligne sont repliés (une phrase ne peut pas créer de titre) ;
- un élément confirmé n'est cité par aucune phrase (l'accepter le retirerait du document).

Refusée (ou délai dépassé, exception), elle est remplacée par la **rédaction fidèle** du programme, avec une note : chaque
élément confirmé, tel quel, une phrase chacun. Sans modèle, c'est elle qui est proposée. Un moteur qui n'a pas ce geste
donne la rédaction fidèle, sans note. Côté modèle, `MoteurModeles.proposer_redaction` est un appel à sortie JSON contrainte
(rubrique et identifiants en liste fermée dans le schéma) sur le seul modèle autorisé pour l'usage `redaction`, jamais
celui de la conversation à sa place.

**Une proposition n'écrit rien.** Elle est enregistrée dans `conversation.json` (clé `redactions`, absente tant qu'aucune
rédaction n'a été proposée) avec le statut `attente`, ses phrases, pour chacune ses fondements et leurs **tours d'origine**,
les éléments confirmés au moment de la proposition (`base`), le tour où la mascotte l'a présentée et l'**empreinte** du
document (fichier de phase et `entretien.json`). Le moteur rédige hors du verrou ; sous le verrou, la conversation et le
document doivent être ceux qu'il a vus, sinon 409. Une nouvelle proposition pour la même rubrique remplace celle en attente.
`cadrage check` est inchangé par une proposition non acceptée.

**Les trois gestes de l'humain.**

- **Accepter** renvoie le texte exact affiché (`texte`, une puce par phrase). Refus si ce n'est pas le texte de la
  proposition (409 `proposition-differente`), si les éléments confirmés de la rubrique ont changé (409 `base-changee`), si
  le document a changé depuis la proposition (409 `document-change`, « le document a changé depuis la proposition : relis la
  proposition »), si le document est au statut accepté. Sinon, dans la rubrique, les **seules lignes que la conversation
  possède** sont remplacées par la rédaction, à la place de la première ; les lignes écrites par l'humain restent où elles
  sont. Les réponses de l'entretien ne bougent pas : le gate ne voit rien changer.
- **Écarter** ferme la proposition, le document reste tel quel.
- **Reprendre ce point** ferme la proposition et rouvre la discussion sur la rubrique : la conversation repasse en mode
  `cadrage`, la question en cours vise cette rubrique, et la réponse suivante de l'humain y est rangée comme un élément
  proposé (à confirmer), jamais écrite d'office.

**Quelles lignes la conversation possède.** Celles que la trace de l'entretien lui attribue : les lignes de ses éléments
(`origin` = `conv:<création>:<élément>`) et celles de sa dernière rédaction acceptée, inscrites dans `entretien.json` sous la
clé `redactions` (`{"<phase>/<section>": {"origin": "conv:<création>:<rédaction>", "lines": [...]}}`). Comme pour `origin`,
c'est la trace qui fait foi, jamais `conversation.json` : une rédaction « acceptée » forgée dans `conversation.json` ne
donne la propriété d'aucune ligne. Fail-closed : proposer (et accepter) est refusé, sans rien écrire, si une ligne possédée
est plusieurs fois dans la rubrique (l'humain l'a recopiée), si la rédaction acceptée a été retouchée en partie, si la ligne
d'un élément confirmé n'y est plus telle quelle (elle est à l'humain désormais), ou si la rubrique a disparu du document.
Une trace `redactions` mal formée ne donne aucune propriété.

**La traçabilité.** `GET …/conversation/redaction` rend, pour chaque passage écrit par acceptation : son texte, la ligne
exacte, ses fondements (identifiant, texte au moment de la proposition, statut actuel, tour source), ses tours d'origine
(numéro, auteur, texte coupé à 300 caractères, date), `perimee` (les éléments confirmés de la rubrique ont changé depuis) et
`dans_le_document` (ses lignes y sont encore telles quelles). Il rend aussi les rubriques rédigeables, les propositions en
attente et l'historique des rédactions (statuts `attente`, `acceptee`, `ecartee`, `reprise`, `remplacee` ; 60 au plus, les
plus anciennes déjà tranchées partent les premières).

**Limites connues.**

- Le gardien vérifie la structure (fondements cités, connus, confirmés, tous couverts), pas le sens : une phrase peut citer
  le bon élément et dire autre chose. C'est pourquoi l'humain accepte le texte exact, avec la liste des fondements de chaque
  phrase sous les yeux.
- Après une acceptation, corriger ou écarter un élément de la rubrique par la conversation laisse la rédaction en place
  (son ancienne ligne n'y est plus, rien n'est retiré) et la marque `perimee` ; la proposition suivante la remplace. Si plus
  aucun élément de la rubrique n'est confirmé, il n'y a plus rien à proposer : la rédaction reste, à retoucher à la main.
- La fabrique des moteurs ne décide pas encore de l'usage `redaction` : en service, la rédaction proposée est la rédaction
  fidèle du programme, tant qu'un lot suivant ne branche pas cet usage.

Un essai sans interface : `python scripts/cadrage-converse.py --chantier <slug>`.
