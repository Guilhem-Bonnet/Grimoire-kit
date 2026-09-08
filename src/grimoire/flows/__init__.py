"""Le moteur de flows — le kernel conduit un blueprint node par node.

Contexte (issue #204, épic #201) : ``RuntimeKernel`` (checkpoints, reprise,
journal d'événements) existait déjà sans appelant applicatif, et un blueprint
compilé s'aplatissait en un unique prompt markdown que le modèle improvisait
de bout en bout. Ce paquet est la couche d'étape qui manquait entre les deux :
elle dérive du blueprint une séquence de nodes contractuels et pilote le
kernel un node à la fois, sans jamais appeler de modèle elle-même — c'est
l'hôte qui exécute, le kit ne fait que conduire et vérifier.

``blueprint compile`` reste un repli valide pour les hôtes sans exécuteur de
node ; ce paquet ne le remplace pas, il ajoute une seconde sortie.
"""

from __future__ import annotations
