"""Écriture de l'identité dans ``project-context.yaml`` pour ``grimoire up``.

Extrait de :mod:`grimoire.cli.cmd_up` (fichier sous cliquet de taille) ; ``cmd_up`` le réexporte sous son ancien nom.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["set_config_user_name"]


def set_config_user_name(config_path: Path, user_name: str) -> bool:
    """Update ``user.name`` in project-context.yaml (round-trip safe)."""
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    with open(config_path, encoding="utf-8") as fh:
        data = yaml.load(fh)
    if not isinstance(data, dict):
        return False
    user = data.get("user") or {}
    if str(user.get("name", "")) == user_name:
        return False
    user["name"] = user_name
    data["user"] = user
    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
    return True
