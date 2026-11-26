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
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.hosts.capabilities import HostProfile, all_profiles, profile_for, resolve_host
from grimoire.hosts.detection import alias_for_host, resolve_enabled_hosts

__all__ = [
    "HOST_ENV",
    "KANBAN",
    "AcpHostChoice",
    "acp_availability",
    "acp_usable",
    "chosen_alias",
    "clear_probe_cache",
    "default_alias",
    "host_choices",
    "install_hint",
    "no_host_reason",
    "set_chosen_alias",
    "start_failure_remedy",
    "usable_map",
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


#: Ce qu'il faut lancer pour installer l'hôte (clé : l'exécutable que le chat cherche). Indicatif : les noms de paquets
#: sont ceux publiés par chaque éditeur, à vérifier dans sa documentation si la commande échoue.
INSTALL_HINTS: dict[str, str] = {
    "claude-agent-acp": "npm install -g @agentclientprotocol/claude-agent-acp",
    "copilot": "npm install -g @github/copilot",
    "codex": "npm install -g @openai/codex",
    "agent": "curl https://cursor.com/install -fsS | bash",
    "gemini": "npm install -g @google/gemini-cli",
    "opencode": "npm install -g opencode-ai",
    "kimi": "uv tool install --python 3.13 kimi-cli",
}

#: Délai de la sonde ``initialize`` : un hôte qui ne répond pas dans ce temps n'est pas utilisable pour un chat.
PROBE_TIMEOUT_SECONDS = 8.0
#: Un hôte qui répond reste « utilisable » ce temps-là sans être resondé ; un hôte muet est resondé plus tôt, pour
#: que l'installation ou la réparation soit vue sans redémarrer le cockpit.
OK_TTL_SECONDS = 6 * 3600.0
FAIL_TTL_SECONDS = 120.0

#: ``(commande, dossier) -> None`` ; lève :class:`AcpError` si ``initialize`` ne répond pas (même contrat que ``doctor``).
Probe = Callable[[tuple[str, ...], Path], None]

# ``(commande, exécutable résolu, date de l'exécutable) -> (instant, utilisable, raison)`` : un exécutable remplacé ou
# réinstallé change la clé, donc la sonde est refaite. Gardé en mémoire du processus : jamais écrit sur disque.
_PROBES: dict[tuple[object, ...], tuple[float, bool, str]] = {}
_PROBES_LOCK = threading.Lock()


def clear_probe_cache() -> None:
    """Oublie les sondes gardées (tests, et « Réessayer » du cockpit)."""
    with _PROBES_LOCK:
        _PROBES.clear()


def install_hint(profile: HostProfile) -> str:
    """La commande d'installation de l'hôte, ou ``""`` si on n'en connaît pas."""
    key = profile.acp_binary or (profile.acp_command[0] if profile.acp_command else "")
    return INSTALL_HINTS.get(key, "")


def _probe_initialize(command: tuple[str, ...], cwd: Path) -> None:
    from grimoire.acp import AcpClient

    with AcpClient(command, cwd, request_timeout=PROBE_TIMEOUT_SECONDS):
        pass


def _short(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= 160 else text[:159] + "…"


def acp_usable(
    profile: HostProfile, root: Path, *, probe: Probe | None = None, clock: Callable[[], float] = time.monotonic
) -> tuple[bool, str]:
    """``(utilisable, raison)`` : l'exécutable est là ET l'agent répond à ``initialize``.

    La recherche dans le ``PATH`` ne suffit pas : un exécutable homonyme (le raccourci ``copilot`` de VS Code, un
    paquet à moitié installé) passe ``shutil.which`` sans parler ACP. ``initialize`` est la sonde de ``grimoire
    doctor`` : rien d'autre n'est envoyé, aucune session, aucun prompt, donc aucun coût de modèle. Le verdict est
    gardé (:data:`OK_TTL_SECONDS` / :data:`FAIL_TTL_SECONDS`) : ouvrir le chat n'en relance pas une à chaque fois.
    Un hôte lancé par ``npx`` n'est jamais sondé (il téléchargerait un paquet) : on s'en tient à son exécutable.
    """
    available, reason = acp_availability(profile)
    if not available:
        return False, _with_hint(profile, reason)
    if profile.acp_command[0] == "npx":
        return True, ""
    launcher = shutil.which(profile.acp_command[0]) or profile.acp_command[0]
    try:
        stamp = Path(launcher).stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = (profile.acp_command, launcher, stamp)
    now = clock()
    with _PROBES_LOCK:
        hit = _PROBES.get(key)
    if hit is not None and now - hit[0] < (OK_TTL_SECONDS if hit[1] else FAIL_TTL_SECONDS):
        return hit[1], hit[2]
    from grimoire.acp import AcpError

    try:
        (probe or _probe_initialize)(profile.acp_command, root)
        verdict = (True, "")
    except (AcpError, OSError) as exc:
        verdict = (False, _with_hint(profile, f"le mode ACP ne répond pas à initialize — {_short(exc)}"))
    with _PROBES_LOCK:
        _PROBES[key] = (now, *verdict)
    return verdict


def _with_hint(profile: HostProfile, reason: str) -> str:
    hint = install_hint(profile)
    return f"{reason} ; installer ou réparer : `{hint}`" if hint else reason


def usable_map(
    profiles: Sequence[HostProfile], root: Path, *, probe: Probe | None = None
) -> dict[HostId, tuple[bool, str]]:
    """:func:`acp_usable` de plusieurs hôtes, les sondes manquantes en parallèle (le plus lent fixe la durée)."""
    if not profiles:
        return {}
    with ThreadPoolExecutor(max_workers=len(profiles)) as pool:
        results = list(pool.map(lambda p: acp_usable(p, root, probe=probe), profiles))
    return {p.host_id: r for p, r in zip(profiles, results, strict=True)}


def _stored_alias(root: Path) -> str | None:
    try:
        data = json.loads((root / SETTING_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("host") if isinstance(data, dict) else None
    return value if isinstance(value, str) and _valid(value) else None


def _valid(alias: str) -> bool:
    return alias == KANBAN or resolve_host(alias) is not None


def _candidates(root: Path) -> list[HostProfile]:
    """Les hôtes à essayer, dans l'ordre : ceux du projet (déclarés ou détectés), puis tous les autres hôtes ACP."""
    ordered: list[HostProfile] = []
    seen: set[HostId] = set()
    for alias in resolve_enabled_hosts(root):
        host_id = resolve_host(alias)
        if host_id is not None and host_id not in seen and profile_for(host_id).acp_command:
            seen.add(host_id)
            ordered.append(profile_for(host_id))
    ordered.extend(p for p in all_profiles() if p.acp_command and p.host_id not in seen)
    return ordered


def default_alias(root: Path, *, probe: Probe | None = None) -> str:
    """L'hôte par défaut du chat : le premier dont l'ACP est RÉELLEMENT utilisable ici, ``""`` s'il n'y en a aucun.

    Les hôtes du projet passent d'abord, puis les autres : un hôte « détecté » par ses seuls fichiers
    (``.github/copilot-instructions.md``) mais sans exécutable, ou dont l'exécutable ne parle pas ACP, n'est jamais
    choisi tant qu'un autre répond. Un hôte « utilisable » a un exécutable présent et répond à ``initialize``
    (:func:`acp_usable`) ; les verdicts sont gardés, rouvrir le chat ne sonde rien de nouveau.
    """
    candidates = _candidates(root)
    verdicts = usable_map(candidates, root, probe=probe)
    for profile in candidates:
        if verdicts[profile.host_id][0]:
            return alias_for_host(profile.host_id)
    return ""


def _detected_alias(root: Path) -> str:
    return default_alias(root)


def no_host_reason(root: Path) -> str:
    """Pourquoi aucun hôte n'est utilisable, et quoi lancer pour en avoir un (phrase pour le fil et l'assistant)."""
    candidates = _candidates(root)
    verdicts = usable_map(candidates, root)
    broken = [
        f"{p.display_name} : {verdicts[p.host_id][1].split(' ; ')[0]}"
        for p in candidates
        if acp_availability(p)[0] and not verdicts[p.host_id][0]
    ]
    parts = ["Aucun hôte d'agent n'est utilisable sur ce poste"]
    if broken:
        parts.append(" (" + " ; ".join(broken) + ")")
    first = next((p for p in all_profiles() if p.acp_command and install_hint(p)), None)
    hint = f" Pour en installer un : `{install_hint(first)}` ({first.display_name})." if first else ""
    return "".join(parts) + "." + hint + " Le mode « Kanban (en différé) » reste choisissable dans le sélecteur."


def start_failure_remedy(root: Path) -> str:
    """Le geste de réparation à écrire après l'échec du démarrage de l'agent de l'hôte choisi (jamais un chemin)."""
    alias = chosen_alias(root)
    host_id = resolve_host(alias) if alias else None
    if host_id is None:
        return " Choisissez un hôte dans le sélecteur « Hôte »."
    profile = profile_for(host_id)
    hint = install_hint(profile)
    fix = f"installez ou réparez-le : `{hint}`" if hint else f"vérifiez `{' '.join(profile.acp_command)}`"
    return f" L'hôte « {profile.display_name} » ne démarre pas : {fix}, ou choisissez un autre hôte dans le sélecteur « Hôte »."


def chosen_alias(root: Path) -> str:
    """L'hôte du chat de *root* : l'environnement, puis le réglage du projet, puis le premier hôte utilisable.

    ``""`` : aucun choix explicite et aucun hôte utilisable (le chat le dit, il ne démarre pas sur un hôte mort).
    """
    forced = os.environ.get(HOST_ENV, "").strip()
    if forced:
        return KANBAN if forced.lower() == KANBAN else forced
    return _stored_alias(root) or default_alias(root)


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
    profiles = all_profiles()
    verdicts = usable_map(profiles, root)  # une sonde par hôte installé, en parallèle, gardée ensuite
    detected = default_alias(root)
    forced = os.environ.get(HOST_ENV, "").strip()
    current = (KANBAN if forced.lower() == KANBAN else forced) if forced else (_stored_alias(root) or detected)
    current_id = resolve_host(current) if current else None
    choices: list[AcpHostChoice] = []
    for profile in profiles:
        alias = alias_for_host(profile.host_id)
        available, reason = verdicts[profile.host_id]
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
