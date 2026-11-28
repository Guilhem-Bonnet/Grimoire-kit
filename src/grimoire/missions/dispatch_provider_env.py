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

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from grimoire.providers.key_command import command_fingerprint
from grimoire.providers.key_endpoint import key_targets
from grimoire.providers.registry import ProviderSpec
from grimoire.providers.secrets import KeyEndpointError, env_for_provider, has_secret

_LOG = logging.getLogger(__name__)
#: Un identifiant de modèle devient un argument de la commande : jamais vide, jamais une option, jamais hors de ce jeu.
_MODEL_RE = re.compile(r"[A-Za-z0-9._:/@][A-Za-z0-9._:/@-]{0,127}", re.ASCII)


@dataclass(frozen=True, slots=True)
class Refusal:
    """Pourquoi l'appel n'est pas lancé. ``key_binding`` : la liaison de la clé (le fournisseur n'est pas en panne)."""

    kind: str
    message: str


def provider_call(
    root: Path, provider: ProviderSpec, model: str, prompt: str
) -> tuple[list[str], dict[str, str]] | Refusal:
    """``(argv, environnement)`` de l'appel, ou le :class:`Refusal` qui le ferme ; aucun sous-processus n'est lancé."""
    from grimoire.missions.dispatch import render_invocation

    if not _MODEL_RE.fullmatch(model):
        return Refusal("error", f"{provider.id} : identifiant de modèle refusé (vide, option ou hors [A-Za-z0-9._:/@-])")
    try:
        env = provider_env(provider, root)
    except KeyEndpointError as exc:
        _LOG.warning("%s : %s", provider.id, exc)  # une seule tentative : le motif et la commande à lancer sont dits ici
        return Refusal("key_binding", f"{provider.id} : {exc}")
    return render_invocation(provider.invocation or "", prompt=prompt, model=model), env


def provider_env(provider: ProviderSpec, root: Path) -> dict[str, str]:
    """Environnement du sous-processus qui réalise l'invocation du fournisseur.

    Les clés du magasin n'y entrent que pour les adresses que la déclaration du fournisseur désigne aujourd'hui
    (:func:`~grimoire.providers.key_endpoint.key_targets`), pour la commande d'invocation approuvée à la saisie et
    pour le programme qu'elle lance (chemin résolu et contenu, jamais sous *root*) : une clé ancienne, sans ces
    approbations, n'est jamais remise à une commande du registre ; une clé saisie pour une autre adresse, commande
    ou programme lève :class:`~grimoire.providers.secrets.KeyEndpointError`, sans qu'aucune valeur sorte.
    """
    command = invocation = None
    if has_secret(provider.id):
        invocation = provider.invocation or ""
        command = command_fingerprint(invocation, root, provider.id) if invocation.strip() else None
    stored = env_for_provider(provider.id, endpoints=key_targets(provider), invocation=invocation, command=command)
    env = {**os.environ, **stored}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env
