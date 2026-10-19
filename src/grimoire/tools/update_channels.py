"""Canaux de mise à jour (``stable`` / ``preview``) et sélection d'une pré-version publiée.

Deux faits sur la distribution du kit décident de ce module :

* PyPI ne reçoit que les tags stables (``publish.yml`` : ``publish-pypi`` est
  ignoré pour ``vX.Y.Z-rc1``). Une pré-version n'existe que sur GitHub, comme
  release marquée *pre-release*, avec sa wheel et son ``SHA256SUMS``.
* ``info.version`` de PyPI peut être une pré-version quand aucune version
  stable n'est publiée : s'y rabattre ferait choisir une pré-version au canal
  ``stable``.

``stable`` ne rend donc jamais une pré-version. ``preview`` rend la plus haute
des deux sources, et ne s'installe que sur une wheel dont la release publie la
somme (jamais de repli : voir :func:`grimoire.tools.install_trust.check_release_wheel`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from grimoire.tools.install_trust import DIST_NAME, REPO, WheelRef

CHANNELS = ("stable", "preview")
GITHUB_HOST = "github.com"


@dataclass(frozen=True)
class ReleaseRef:
    """Une pré-version publiée sur GitHub : son numéro, son tag et sa wheel."""

    version: str
    tag: str
    wheel: WheelRef


def releases_url() -> str:
    return f"https://api.github.com/repos/{REPO}/releases?per_page=30"


def _parse(raw: object) -> Version | None:
    if not isinstance(raw, str):
        return None
    try:
        return Version(raw.strip().removeprefix("v"))
    except InvalidVersion:
        return None


def stable_only(version: object) -> str | None:
    """*version* si c'est une version stable lisible, sinon ``None`` (jamais une pré-version)."""
    parsed = _parse(version)
    if parsed is None or parsed.is_prerelease or parsed.is_devrelease:
        return None
    return str(parsed)


def _release_wheel(assets: object, tag: str, version: Version) -> WheelRef | None:
    """La wheel pure-Python de la release, si elle porte bien ce numéro et vient du dépôt du kit."""
    if not isinstance(assets, list):
        return None
    prefix = f"/{REPO}/releases/download/{tag}/"
    found: list[WheelRef] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name, url = str(asset.get("name", "")), str(asset.get("browser_download_url", ""))
        if not name.endswith(".whl") or "/" in name or "\\" in name:
            continue
        try:
            dist, wheel_version, _, _ = parse_wheel_filename(name)
        except InvalidWheelFilename:
            continue
        parsed = urlparse(url)
        if canonicalize_name(dist) != canonicalize_name(DIST_NAME) or wheel_version != version:
            continue
        if parsed.scheme != "https" or parsed.hostname != GITHUB_HOST or parsed.path != prefix + name:
            continue
        found.append(WheelRef(name, url, None))
    found.sort(key=lambda w: "none-any" not in w.filename)
    return found[0] if found else None


def select_preview(releases: object, version: str | None = None) -> ReleaseRef | None:
    """La plus haute pré-version publiée (ou exactement *version*) dans la liste des releases GitHub.

    Ne compte que les releases marquées *pre-release*, hors brouillons, dont le
    tag se lit comme une pré-version et dont une wheel porte le même numéro : une
    liste mal formée, un tag étranger ou une wheel d'un autre dépôt sont ignorés.
    """
    if not isinstance(releases, list):
        return None
    wanted = _parse(version) if version is not None else None
    best: ReleaseRef | None = None
    best_version: Version | None = None
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") or not release.get("prerelease"):
            continue
        tag = release.get("tag_name")
        parsed = _parse(tag)
        if parsed is None or not parsed.is_prerelease or not isinstance(tag, str):
            continue
        if wanted is not None and parsed != wanted:
            continue
        wheel = _release_wheel(release.get("assets"), tag, parsed)
        if wheel is None:
            continue
        if best_version is None or parsed > best_version:
            best, best_version = ReleaseRef(str(parsed), tag, wheel), parsed
    return best


def choose(stable: str | None, preview: ReleaseRef | None) -> tuple[str, ReleaseRef | None] | None:
    """Canal ``preview`` : la plus haute entre la stable de PyPI et la pré-version de GitHub.

    Rend ``(version, release)`` ; *release* est ``None`` quand la version vient de PyPI.
    """
    stable_v, preview_v = _parse(stable), _parse(preview.version if preview else None)
    if preview is not None and preview_v is not None and (stable_v is None or preview_v > stable_v):
        return preview.version, preview
    return (stable, None) if stable is not None else None


def releases_payload(raw: bytes) -> Any:
    """Décode la réponse de l'API GitHub ; ``None`` si ce n'est pas du JSON."""
    import json

    try:
        return json.loads(raw)
    except ValueError:
        return None
