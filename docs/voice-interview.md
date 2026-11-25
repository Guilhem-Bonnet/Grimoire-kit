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
  d'entretien illisible, de plus de 1 Mio ou hors du projet donne une ligne d'erreur.
