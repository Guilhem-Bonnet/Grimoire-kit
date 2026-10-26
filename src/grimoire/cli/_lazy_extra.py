"""Groupes paresseux enregistrés hors de ``app.py`` (plafond de taille du module, cliquet R2).

``app.py`` est un fichier gelé en taille (``scripts/code-ratchet-baseline.json``) : un groupe de
premier niveau nouveau s'enregistre ici, et :meth:`LazyTyperGroup.configure` l'ajoute aux groupes
déclarés par ``app.py``, à la fin de l'ordre d'affichage. Même contrat que là-bas : le module n'est
importé qu'à la résolution de la commande.
"""

from __future__ import annotations

from grimoire.cli._lazy import LazyGroupSpec

#: Nom → groupe, dans l'ordre d'affichage de ``grimoire --help --all``.
EXTRA_GROUPS: dict[str, LazyGroupSpec] = {
    "steward": LazyGroupSpec("grimoire.cli.cmd_steward", "steward_app", rich_help_panel="Project"),
}
