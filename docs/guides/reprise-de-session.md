---
audience: utilisateur
---

# Reprise de session sans perte

Une session longue relit tout son contexte à chaque appel. Repartir d'une session
neuve, ou compacter plus tôt, coûte moins cher à condition de ne pas perdre l'état
de travail. La capsule de reprise porte cet état.

## Contenu

Au plus 1 500 caractères, sous `_grimoire-output/context/<tâche>/compaction-capsule.md` :

- le bloc de gouvernance (tâche, profil, gates, flow en vol) ;
- état : titre et état de la tâche, branche, modifications non commitées, PR citées ;
- dernier message de la session (non attesté) : le dernier message substantiel reçu, 160 caractères au plus ;
- prochaine étape : note de tâche non lue, sinon dernier test rouge, sinon gates
  rouges, sinon premier critère d'acceptation ;
- fichiers touchés et dernier test (journal de preuve de la tâche) ;
- notes de la tâche (origine non prouvée) et incidents ouverts (ledger).

Aucune collecte nouvelle : seules des sources déjà tenues par le kit sont lues. Les
secrets reconnaissables, le dossier de contrôle de Grimoire et les fichiers de secrets
sont retirés à l'écriture et à la relecture (affectations `NOM=valeur` préfixées, `mot de passe …`, URL à identifiants, JWT, `Authorization`, clés privées, `.ssh/id_*`…).

## Provenance

Le fichier est sous `_grimoire-output/context/`, qu'un agent peut écrire : rien de ce qu'il contient n'est une preuve. À la relecture, seules les lignes d'une liste fermée sont reprises (gouvernance, état, prochaine étape, fichiers, dernier test) ; le dernier message et les notes sont relus à la source (mémoire de session, ledger) et jamais attribués à l'humain, car ni l'une ni l'autre n'en prouve l'origine. Le tout est encadré comme donnée à vérifier, pas comme instruction. Les appels `git` n'ont lieu qu'à l'écriture (2 s au total), jamais au démarrage de session.

## Quand elle est écrite et relue

| Moment | Effet |
| --- | --- |
| Hook `PreCompact` | écrit la capsule |
| `grimoire host handoff` | l'écrit à la demande et imprime `claude '...'` à coller |
| `SessionStart` `source=compact` | injecte la capsule ; le flow en vol est relu en direct |
| `SessionStart` `source=resume` | idem ; sur Claude Code seulement, n'ajoute plus la persona d'entrée ni la politique de dispatch (déjà dans le transcript restauré) |
| `SessionStart` `startup`/`clear` | injecte la capsule seulement si la tâche active est une vraie tâche (pas `bootstrap`) et si elle a moins de 24 h |

Règle de session : le fichier nomme sa session écrivante. Celle-ci la relit sans limite d'âge sur `compact`/`resume` ; toute autre session (autre session de la même tâche, écrivain inconnu) ne la reçoit que si elle a moins de 24 h.

Sans capsule, le démarrage est strictement celui d'avant.

## Avertissement de taille de contexte

Non fait : le payload des hooks n'expose pas la taille du contexte (seulement
`transcript_path`, dont la taille sur disque n'est pas celle de la fenêtre). Rien
n'est deviné.
