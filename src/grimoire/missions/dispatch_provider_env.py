"""L'environnement du fournisseur lancé par ``dispatch._run_provider_call``.

Deux contrats se rencontrent ici :

- les secrets d'un fournisseur ``currency: api`` viennent du magasin
  ``~/.env-store/`` (``grimoire.providers.secrets``), jamais du registre ni
  d'un argument — fusionnés pour que le script d'invocation n'ait rien à en
  savoir ;
- le pipe de sortie est relu en UTF-8, donc un fournisseur Python doit y
  écrire en UTF-8, pas dans l'encodage de la console Windows (cp1252), qui
  rendrait les accents illisibles à la relecture. ``PYTHONIOENCODING`` et
  ``PYTHONUTF8`` sont **imposés** (pas ``setdefault``) : le contrat de lecture
  est ici, pas dans l'environnement hérité de l'appelant. ``PYTHONUTF8``
  couvre aussi les fichiers que le script du fournisseur lirait lui-même.
"""

from __future__ import annotations

import os

from grimoire.providers.secrets import env_for_provider


def provider_env(provider_id: str) -> dict[str, str]:
    """Environnement du sous-processus qui réalise l'invocation du fournisseur."""
    env = {**os.environ, **env_for_provider(provider_id)}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env
