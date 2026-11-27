"""Messages vocaux avec l'orchestrateur — le socle (#1343, lot V1).

Le kit tourne sur le poste de l'utilisateur : tant que le vocal est éteint, il ne
coûte rien. Ce paquet n'importe donc aucune bibliothèque audio à son import, ni
à celui de ses sous-modules ``settings`` et ``engines`` ; un moteur
(faster-whisper, pocket-tts) n'est importé qu'au premier appel qui en a besoin,
et déchargé après un délai d'inactivité. Aucun modèle n'est téléchargé sans une
action explicite (``allow_download``) : le cache local, sinon une raison lisible.

Le vocal est un transmetteur : l'intelligence reste dans l'orchestrateur.
"""
