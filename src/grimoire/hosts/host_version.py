"""La version de l'hôte d'une session, lue là où l'hôte l'écrit (K3, issue #768).

Une preuve de fencing vaut pour la version d'hôte sur laquelle elle a été
capturée (:func:`grimoire.hosts.capabilities.contract_for`). Or aucun payload de
hook ne porte cette version (fixtures de K0). Claude Code, lui, l'écrit dans
chaque ligne de son journal de session (``"version":"2.1.301"``), dont le
payload donne le chemin (``transcript_path``) : on lit la fin de ce fichier,
jamais sa totalité, et on ne garde que ce qui a la forme d'une version.

Ce canal n'est pas une fixture de K0 : il a été relu sur le journal réel d'une
session 2.1.301, pas capturé. Faute de version lisible, la réponse est ``""``
et l'hôte reste non fencé — l'erreur échoue du côté sûr.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId

#: Variable qui force la version (hôte sans journal lisible, tests, déclaration d'opérateur).
VERSION_ENV = "GRIMOIRE_HOST_VERSION"
_TAIL_BYTES = 64 * 1024
_VERSION = re.compile(r"\d+(?:\.\d+){1,3}")
_IN_LINE = re.compile(r'"version"\s*:\s*"(\d+(?:\.\d+){1,3})"')


def valid_version(value: str) -> str:
    cleaned = value.strip()
    return cleaned if _VERSION.fullmatch(cleaned) else ""


def claude_version_from_transcript(path: str | os.PathLike[str]) -> str:
    """La dernière ``version`` écrite dans la fin du journal de session Claude Code, ``""`` sinon."""
    try:
        with Path(path).open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _TAIL_BYTES))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    found = _IN_LINE.findall(tail)
    return found[-1] if found else ""


def detect_host_version(host_id: HostId | None, payload: Mapping[str, Any], env: Mapping[str, str] | None = None) -> str:
    """La version de l'hôte d'un hook, ``""`` si rien ne la donne.

    ``GRIMOIRE_HOST_VERSION`` d'abord (déclaration explicite), puis, pour Claude
    Code, le journal de session nommé par le payload.
    """
    environ = os.environ if env is None else env
    explicit = valid_version(str(environ.get(VERSION_ENV, "")))
    if explicit:
        return explicit
    if host_id is HostId.CLAUDE_CODE_CLI:
        raw = payload.get("transcript_path") or payload.get("transcriptPath")
        if isinstance(raw, str) and raw:
            return claude_version_from_transcript(raw)
    return ""
