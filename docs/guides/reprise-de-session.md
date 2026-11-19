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
- objectif : dernier message humain substantiel de la session ;
- prochaine étape : consigne humaine non lue, sinon dernier test rouge, sinon gates
  rouges, sinon premier critère d'acceptation ;
- fichiers touchés et dernier test (journal de preuve de la tâche) ;
- consignes humaines récentes et incidents ouverts (ledger).

Aucune collecte nouvelle : seules des sources déjà tenues par le kit sont lues. Les
secrets reconnaissables, le dossier de contrôle de Grimoire et les fichiers de secrets
sont retirés à l'écriture et à la relecture.

## Quand elle est écrite et relue

| Moment | Effet |
| --- | --- |
| Hook `PreCompact` | écrit la capsule |
| `grimoire host handoff` | l'écrit à la demande et imprime `claude '...'` à coller |
| `SessionStart` `source=compact` | injecte la capsule, quel que soit son âge ; le flow en vol est relu en direct |
| `SessionStart` `source=resume` | idem, et n'ajoute plus la persona d'entrée ni la politique de dispatch (déjà dans le transcript restauré) |
| `SessionStart` `startup`/`clear` | injecte la capsule seulement si la tâche active est une vraie tâche (pas `bootstrap`) et si elle a moins de 24 h |

Sans capsule, le démarrage est strictement celui d'avant.

## Avertissement de taille de contexte

Non fait : le payload des hooks n'expose pas la taille du contexte (seulement
`transcript_path`, dont la taille sur disque n'est pas celle de la fenêtre). Rien
n'est deviné.
