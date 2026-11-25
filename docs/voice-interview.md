---
audience: utilisateur
---

# Vocal : questions et entretien de cadrage

Cette page décrit le comportement de la boîte vocale quand l'orchestrateur pose une question (lot V4).

## Ce que la boîte affiche

- Une question de l'orchestrateur est un message distinct, marqué « Question », en tête de la boîte tant
  qu'elle n'a pas de réponse. Un compte rendu suivant ne la remplace jamais.
- Le cockpit affiche le texte complet d'origine de la question, avec la raison pour laquelle elle vous
  revient. Le texte parlé (bref, sans code ni chemin) ne sert qu'à l'audio.
- La raison pour laquelle la question vous revient est placée avant le texte. Si la question est trop longue
  (plus de 4000 caractères), seule la question est coupée, avec la marque « texte tronqué : la question complète
  est dans le fil », et aucune option n'est cliquable.
- Les boutons d'option n'apparaissent que lorsque le texte est entièrement visible (non replié, ou déplié par
  « Tout afficher »). Une option contenant un caractère de contrôle ou de direction, coupée à 200 caractères, ou
  au-delà de huit options, n'a pas de bouton : la boîte indique « N autres options dans le fil ».
- Une question est réservée à l'humain sauf si le serveur dit explicitement le contraire.
- Un clic sur une option ne répond pas : il cible la question dans le fil et préremplit la saisie. L'envoi
  reste un geste explicite.
- Une question de catégorie réservée à l'humain (permission, fusion, action destructive) n'a aucune option
  cliquable dans la boîte : elle se traite dans le fil.
- Au-delà de vingt questions sans réponse, la plus ancienne quitte la boîte ; elle reste dans le fil, et la boîte
  indique combien.

## Entretien de cadrage

- Pendant un entretien en cours, l'orchestrateur pose une seule question courte par message.
- Un message de la personne pendant un entretien marque les questions d'entretien en attente comme répondues,
  à l'affichage seulement : rien n'est consigné dans le cadrage par ce geste. C'est l'orchestrateur qui consigne
  la réponse transcrite avec `grimoire cadrage interview answer`.
- L'état des angles (ouvert, couvert, écarté) vient de `GET voice/interview`, en lecture seule. Un état
  d'entretien illisible, de plus de 1 Mio, hors du projet ou qui n'est pas un fichier ordinaire (lien, FIFO, périphérique) donne une ligne d'erreur, sans jamais bloquer. Seuls les dossiers à nom de chantier sûr qui portent un `entretien.json` comptent dans la limite de cinquante.
