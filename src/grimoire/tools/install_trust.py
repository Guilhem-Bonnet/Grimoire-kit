"""Confiance dans l'installation : la wheel reçue est celle que la release a publiée.

Trois questions, trois réponses qui échouent fermées :

* **Avant d'installer** (``grimoire self update``) : la wheel téléchargée a-t-elle
  l'empreinte annoncée par PyPI *et* celle du ``SHA256SUMS`` de la release
  GitHub ? Deux canaux, deux hébergeurs : altérer l'un ne suffit pas.
  Une empreinte qui diffère, un fichier absent de la liste ou une liste
  illisible refusent l'installation. Seule exception, écrite : une version
  antérieure à la première qui publie le fichier (``SUMS_REQUIRED_FROM``) n'a
  pas de ``SHA256SUMS``, et le niveau rendu le dit (``pypi-only``).
* **Après installation** (``grimoire doctor``) : les fichiers installés
  correspondent-ils au ``RECORD`` que l'installateur a écrit ?
* **Bout en bout** (``grimoire verify-install --online``) : le ``RECORD``
  installé est-il celui de la wheel vérifiée de la release ?

Niveaux de confiance (``trust_level``), du plus fort au plus faible :
``verified`` (chaîne complète), ``pypi-only`` (PyPI seul, release sans somme),
``intact`` (fichiers conformes à leur ``RECORD``, provenance non vérifiée),
``dev`` (installation éditable : rien à attester), ``unknown`` (pas de
``RECORD``), ``modified`` (au moins un fichier diffère : refus).

Limite écrite : une somme et une attestation rendent la falsification plus
coûteuse, pas impossible. Un attaquant qui contrôle à la fois PyPI et la release
GitHub n'est pas arrêté par ce module ; l'attestation de provenance
(``gh attestation verify``) ajoute la signature du workflow de release.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse

DIST_NAME = "grimoire-kit"
SUMS_NAME = "SHA256SUMS"
REPO = "Guilhem-Bonnet/Grimoire-kit"
#: Première version dont la release publie ``SHA256SUMS`` : à partir d'elle, son
#: absence est un refus, pas un repli (sinon un 404 servi à dessein désarmerait
#: la vérification).
SUMS_REQUIRED_FROM = "3.63.0"
#: Hôte des fichiers PyPI : une URL de wheel ailleurs n'est pas téléchargée.
PYPI_FILES_HOST = "files.pythonhosted.org"
MAX_WHEEL_BYTES = 200 * 1024 * 1024

_SUMS_LINE = re.compile(r"^([0-9a-fA-F]{64}) [ *](\S.*)$")
_RECORD_SKIP = ("RECORD", "INSTALLER", "REQUESTED", "direct_url.json")


class TrustError(Exception):
    """Une vérification a échoué : l'installation doit être refusée."""


@dataclass(frozen=True)
class Verdict:
    """Résultat d'une vérification : un niveau et la phrase qui le justifie."""

    level: str
    detail: str
    problems: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.level != "modified"


@dataclass(frozen=True)
class WheelRef:
    filename: str
    url: str
    sha256: str | None


# ── Empreintes ────────────────────────────────────────────────────────────────


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sha256sums(text: str) -> dict[str, str]:
    """``SHA256SUMS`` -> ``{fichier: empreinte}``. Une ligne illisible refuse tout le fichier."""
    sums: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        match = _SUMS_LINE.match(line)
        if match is None:
            raise TrustError(f"{SUMS_NAME} illisible, ligne {number}")
        digest, name = match.group(1).lower(), match.group(2)
        if "/" in name or "\\" in name:
            raise TrustError(f"{SUMS_NAME} ligne {number} : nom de fichier avec chemin ({name})")
        if sums.setdefault(name, digest) != digest:
            raise TrustError(f"{SUMS_NAME} contradictoire pour {name}")
    return sums


def sums_required(version: str) -> bool:
    """La release de *version* devait-elle publier ``SHA256SUMS`` ?"""
    parsed = _release_tuple(version)
    if parsed is None:
        return True  # version illisible : on exige la somme, on ne la devine pas
    return parsed >= (_release_tuple(SUMS_REQUIRED_FROM) or (0,))


def _release_tuple(version: str) -> tuple[int, ...] | None:
    """Partie numérique de tête (``3.63.0rc1`` -> ``(3, 63, 0)``) ; ``None`` si illisible."""
    match = re.match(r"^v?(\d+(?:\.\d+)*)", version.strip())
    return tuple(int(p) for p in match.group(1).split(".")) if match else None


def check_wheel(
    wheel: Path,
    *,
    version: str,
    pypi_sha256: str | None,
    sums: dict[str, str] | None,
) -> Verdict:
    """Vérifie une wheel téléchargée. Lève :class:`TrustError` au moindre écart."""
    if not pypi_sha256:
        raise TrustError("PyPI n'annonce aucune empreinte pour ce fichier : rien à vérifier")
    actual = sha256_file(wheel)
    if actual != pypi_sha256.lower():
        raise TrustError(
            f"{wheel.name} : l'empreinte du fichier reçu ({actual[:16]}...) diffère de celle de PyPI "
            f"({pypi_sha256[:16]}...)"
        )
    if sums is None:
        if sums_required(version):
            raise TrustError(f"la release v{version} devrait publier {SUMS_NAME} et ne le fait pas")
        return Verdict("pypi-only", f"empreinte conforme à PyPI ; la release v{version} ne publie pas {SUMS_NAME}")
    expected = sums.get(wheel.name)
    if expected is None:
        raise TrustError(f"{wheel.name} n'est pas dans {SUMS_NAME} de la release v{version}")
    if expected != actual:
        raise TrustError(
            f"{wheel.name} : l'empreinte du fichier reçu ({actual[:16]}...) diffère de {SUMS_NAME} "
            f"({expected[:16]}...)"
        )
    return Verdict("verified", f"empreinte conforme à PyPI et à {SUMS_NAME} de la release v{version}")


# ── Réseau (injectable) ───────────────────────────────────────────────────────

Opener = Callable[..., Any]


def sums_url(version: str) -> str:
    return f"https://github.com/{REPO}/releases/download/v{version}/{SUMS_NAME}"


def fetch_sums(version: str, opener: Opener) -> dict[str, str] | None:
    """``SHA256SUMS`` de la release, ou ``None`` si elle n'existe pas (404). Toute autre panne refuse."""
    try:
        with opener(sums_url(version), timeout=15) as resp:
            body = resp.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise TrustError(f"{SUMS_NAME} injoignable (HTTP {exc.code})") from None
    except Exception as exc:
        raise TrustError(f"{SUMS_NAME} injoignable ({type(exc).__name__})") from None
    try:
        return parse_sha256sums(body.decode("utf-8"))
    except UnicodeDecodeError:
        raise TrustError(f"{SUMS_NAME} illisible (UTF-8 invalide)") from None


def select_wheel(pypi_data: dict[str, Any], version: str) -> WheelRef:
    """La wheel pure-Python de *version* dans la réponse JSON de PyPI (complète ou propre à la version)."""
    releases = pypi_data.get("releases")
    files = releases.get(version) if isinstance(releases, dict) else pypi_data.get("urls")
    if not isinstance(files, list):
        raise TrustError(f"PyPI ne liste aucun fichier pour la version {version}")
    wheels = [
        f for f in files
        if isinstance(f, dict) and f.get("packagetype") == "bdist_wheel" and not f.get("yanked")
        and str(f.get("filename", "")).endswith(".whl")
    ]
    if not wheels:
        raise TrustError(f"PyPI ne liste aucune wheel pour la version {version}")
    wheels.sort(key=lambda f: "none-any" not in str(f["filename"]))
    chosen = wheels[0]
    url = str(chosen.get("url", ""))
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != PYPI_FILES_HOST:
        raise TrustError(f"URL de wheel hors de {PYPI_FILES_HOST} refusée : {url}")
    digests = chosen.get("digests")
    sha = digests.get("sha256") if isinstance(digests, dict) else None
    return WheelRef(str(chosen["filename"]), url, sha if isinstance(sha, str) else None)


def download_wheel(ref: WheelRef, dest_dir: Path, opener: Opener) -> Path:
    """Télécharge *ref* sous son nom d'origine (pip lit le nom et la version dans le nom du fichier)."""
    name = Path(ref.filename).name
    if name != ref.filename or not name.endswith(".whl"):
        raise TrustError(f"nom de wheel suspect : {ref.filename}")
    target = dest_dir / name
    size = 0
    try:
        with opener(ref.url, timeout=60) as resp, target.open("wb") as out:
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                size += len(chunk)
                if size > MAX_WHEEL_BYTES:
                    raise TrustError(f"{name} dépasse {MAX_WHEEL_BYTES} octets : abandon")
                out.write(chunk)
    except TrustError:
        raise
    except Exception as exc:
        raise TrustError(f"téléchargement de {name} impossible ({type(exc).__name__})") from None
    return target


# ── Installation locale ───────────────────────────────────────────────────────


def _hash_of(path: Path) -> str:
    return base64.urlsafe_b64encode(bytes.fromhex(sha256_file(path))).rstrip(b"=").decode("ascii")


def parse_record(text: str) -> dict[str, str]:
    """``RECORD`` -> ``{chemin: sha256=...}``, sans les entrées qui varient d'une installation à l'autre."""
    entries: dict[str, str] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or not row[1]:
            continue
        path = row[0]
        if path.endswith(".pyc") or path.startswith("../") or Path(path).name in _RECORD_SKIP:
            continue
        entries[path] = row[1]
    return entries


def installed_distribution() -> metadata.Distribution | None:
    try:
        return metadata.distribution(DIST_NAME)
    except metadata.PackageNotFoundError:
        return None


def _is_dev_install(dist: metadata.Distribution) -> bool:
    raw = dist.read_text("direct_url.json")
    if not raw:
        return False
    try:
        info = json.loads(raw)
    except ValueError:
        return False
    return isinstance(info, dict) and isinstance(info.get("dir_info"), dict)


def local_trust(dist: metadata.Distribution | None = None) -> Verdict:
    """Les fichiers installés correspondent-ils à leur ``RECORD`` ? Hors ligne, sans rien écrire."""
    dist = dist or installed_distribution()
    if dist is None:
        return Verdict("unknown", f"{DIST_NAME} n'est pas installé comme distribution")
    if _is_dev_install(dist):
        return Verdict("dev", "installation éditable ou depuis un dossier : aucune wheel à attester")
    record = dist.read_text("RECORD")
    if not record:
        return Verdict("unknown", "pas de RECORD : l'installateur n'a pas consigné les empreintes")
    problems: list[str] = []
    checked = 0
    for rel, expected in parse_record(record).items():
        located = Path(str(dist.locate_file(rel)))
        algo, _, value = expected.partition("=")
        if algo != "sha256":
            continue
        if not located.is_file():
            problems.append(f"absent : {rel}")
            continue
        checked += 1
        if _hash_of(located) != value:
            problems.append(f"modifié : {rel}")
    if problems:
        return Verdict("modified", f"{len(problems)} fichier(s) ne correspondent plus au RECORD", tuple(problems[:20]))
    return Verdict("intact", f"{checked} fichiers conformes au RECORD ; provenance non vérifiée (grimoire verify-install --online)")


def wheel_record(wheel: Path) -> dict[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if n.endswith(".dist-info/RECORD")]
        if len(names) != 1:
            raise TrustError(f"{wheel.name} : RECORD introuvable ou ambigu")
        return parse_record(archive.read(names[0]).decode("utf-8"))


def same_as_wheel(wheel: Path, dist: metadata.Distribution | None = None) -> tuple[str, ...]:
    """Écarts entre le ``RECORD`` de la wheel vérifiée et celui de l'installation (vide = identique)."""
    dist = dist or installed_distribution()
    if dist is None:
        return ("distribution non installée",)
    installed = parse_record(dist.read_text("RECORD") or "")
    published = wheel_record(wheel)
    return tuple(
        [f"absent de l'installation : {p}" for p in sorted(published.keys() - installed.keys())]
        + [f"ajouté à l'installation : {p}" for p in sorted(installed.keys() - published.keys())]
        + [f"empreinte différente : {p}" for p in sorted(published.keys() & installed.keys()) if published[p] != installed[p]]
    )


def installed_extras(dist: metadata.Distribution | None = None) -> list[str]:
    """Extras dont toutes les dépendances sont déjà là : une réinstallation ne doit pas en perdre."""
    dist = dist or installed_distribution()
    if dist is None:
        return []
    wanted: dict[str, list[str]] = {}
    for raw in dist.requires or []:
        name = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", raw)
        if name is None or ";" not in raw:
            continue
        marker = raw.split(";", 1)[1]
        for extra in sorted(set(re.findall(r"""extra\s*==\s*['"]([^'"]+)['"]""", marker))):
            wanted.setdefault(extra, []).append(name.group(1))

    def _present(name: str) -> bool:
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            return False
        return True

    return sorted(extra for extra, names in wanted.items() if all(_present(n) for n in names))
