"""Quel hôte parle au chat de l'orchestrateur, et dans quel mode (ACP3).

Trois modes, un seul choix par projet :

* ``acp`` — l'agent de l'hôte tourne en sous-processus et parle ACP (:mod:`grimoire.acp`) ;
* ``kanban`` — repli pour un hôte sans interface externe (Copilot dans VS Code) : le chat
  écrit une consigne sur une tâche, les sessions y répondent par leurs notes. En différé,
  mais disponible partout.

Le réglage est un fichier du runtime (``_grimoire-runtime-output/cockpit/chat-host.json``),
pas la config du projet : c'est une préférence de poste, réversible, que le cockpit pose.
Sans réglage, l'hôte détecté (:func:`grimoire.hosts.detection.resolve_enabled_hosts`) sert.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.hosts.capabilities import HostProfile, all_profiles, profile_for, resolve_host
from grimoire.hosts.detection import alias_for_host, resolve_enabled_hosts

__all__ = [
    "HOST_ENV",
    "KANBAN",
    "AcpHostChoice",
    "acp_availability",
    "chosen_alias",
    "host_choices",
    "set_chosen_alias",
]

#: Hôte forcé par l'environnement (``grimoire chat --host`` et les tests) ; passe avant le réglage.
HOST_ENV = "GRIMOIRE_CHAT_HOST"
#: Pseudo-hôte : le chat écrit dans le kanban au lieu de lancer un agent.
KANBAN = "kanban"
SETTING_RELPATH = Path("_grimoire-runtime-output") / "cockpit" / "chat-host.json"


@dataclass(frozen=True, slots=True)
class AcpHostChoice:
    """Un hôte proposé au chat : disponible, ou grisé avec sa raison."""

    alias: str
    display_name: str
    mode: str  # "acp" | "kanban"
    available: bool
    reason: str
    command: tuple[str, ...]
    preview: bool
    note: str
    selected: bool = False
    detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "display_name": self.display_name,
            "mode": self.mode,
            "available": self.available,
            "reason": self.reason,
            "command": list(self.command),
            "preview": self.preview,
            "note": self.note,
            "selected": self.selected,
            "detected": self.detected,
        }


def acp_availability(profile: HostProfile) -> tuple[bool, str]:
    """``(disponible, raison)`` : l'hôte déclare-t-il un mode ACP et est-il installé ?

    Ne lance rien : une recherche dans le ``PATH``, sans coût.
    """
    if not profile.acp_command:
        return False, f"{profile.display_name} n'a pas de mode ACP connu — repli par le kanban"
    launcher = profile.acp_command[0]
    if shutil.which(launcher) is None:
        return False, f"« {launcher} » est introuvable dans le PATH — hôte non installé"
    binary = profile.acp_binary
    if binary and shutil.which(binary) is None:
        return False, f"« {binary} » est introuvable dans le PATH — hôte non installé"
    return True, ""


def _stored_alias(root: Path) -> str | None:
    try:
        data = json.loads((root / SETTING_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("host") if isinstance(data, dict) else None
    return value if isinstance(value, str) and _valid(value) else None


def _valid(alias: str) -> bool:
    return alias == KANBAN or resolve_host(alias) is not None


def _detected_alias(root: Path) -> str:
    """Le premier hôte activé du projet dont le mode ACP répond au PATH, sinon le premier hôte activé."""
    enabled = resolve_enabled_hosts(root)
    for alias in enabled:
        host_id = resolve_host(alias)
        if host_id is not None and acp_availability(profile_for(host_id))[0]:
            return alias
    return enabled[0] if enabled else "claude"


def chosen_alias(root: Path) -> str:
    """L'hôte du chat de *root* : l'environnement, puis le réglage du projet, puis l'hôte détecté."""
    forced = os.environ.get(HOST_ENV, "").strip()
    if forced:
        return KANBAN if forced.lower() == KANBAN else forced
    return _stored_alias(root) or _detected_alias(root)


def set_chosen_alias(root: Path, alias: str) -> str:
    """Pose l'hôte du chat ; ``""`` revient à l'hôte détecté. Rend l'alias canonique en vigueur."""
    value = alias.strip().lower()
    path = root / SETTING_RELPATH
    if not value:
        path.unlink(missing_ok=True)
        return _detected_alias(root)
    if not _valid(value):
        raise ValueError(f"hôte inconnu : {alias!r}")
    host_id = resolve_host(value)
    canonical = KANBAN if value == KANBAN else alias_for_host(host_id)  # type: ignore[arg-type]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"host": canonical}, ensure_ascii=False) + "\n", encoding="utf-8")
    return canonical


def host_choices(root: Path) -> list[AcpHostChoice]:
    """Tous les hôtes que le chat sait parler, plus le repli kanban ; les absents portent leur raison."""
    current = chosen_alias(root)
    detected = _detected_alias(root)
    current_id = resolve_host(current)
    choices: list[AcpHostChoice] = []
    for profile in all_profiles():
        alias = alias_for_host(profile.host_id)
        available, reason = acp_availability(profile)
        choices.append(
            AcpHostChoice(
                alias=alias,
                display_name=profile.display_name,
                mode="acp",
                available=available,
                reason=reason,
                command=profile.acp_command,
                preview=profile.acp_preview,
                note=profile.acp_note,
                selected=current_id == profile.host_id,
                detected=detected == alias,
            )
        )
    choices.append(
        AcpHostChoice(
            alias=KANBAN,
            display_name="Kanban (en différé)",
            mode="kanban",
            available=True,
            reason="",
            command=(),
            preview=False,
            note="Sans interface externe (Copilot dans VS Code) : le chat dépose une consigne sur une tâche "
            "et affiche les réponses que ses sessions y déposent.",
            selected=current == KANBAN,
            detected=detected == KANBAN,
        )
    )
    return choices
