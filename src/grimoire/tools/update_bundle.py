"""Bundle hors ligne : une wheel, son ``SHA256SUMS`` et, au besoin, ses dépendances, dans un dossier.

``grimoire self update --from <dossier|wheel>`` installe sans ouvrir un seul
socket : ni PyPI, ni GitHub, ni index pour les dépendances (``--no-index`` /
``--offline``, wheels seulement). Ce qui est vérifié, et ce qui ne l'est pas :

* **Vérifié** : la wheel du kit figure dans le ``SHA256SUMS`` du bundle avec la
  même empreinte, et chaque autre wheel du dossier (dépendances) aussi, parce
  que le gestionnaire les lirait à son tour (``--find-links``). Une wheel
  absente de la liste ou altérée refuse tout le bundle.
* **Non vérifié** : l'origine. Le ``SHA256SUMS`` voyage avec les fichiers, donc
  un bundle entièrement remplacé reste cohérent. Le niveau rendu est
  ``bundle``, jamais ``verified``. Pour remonter à la source, joindre la liste
  à celle de la release (``gh attestation verify``, à faire en ligne ailleurs).
* **Fenêtre** : la wheel du kit est copiée puis hachée *après* copie (ce qui est
  installé est ce qui est vérifié). Les dépendances sont vérifiées en place,
  entre la vérification et l'installation un processus qui écrit dans le
  dossier peut les remplacer.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import Version

from grimoire.tools.install_trust import DIST_NAME, SUMS_NAME, TrustError, Verdict, parse_sha256sums, sha256_file


@dataclass(frozen=True)
class Bundle:
    wheel: Path
    version: Version
    sums: dict[str, str]
    #: Dossier à fournir au gestionnaire pour les dépendances ; ``None`` pour une wheel seule.
    link_dir: Path | None
    #: Autres wheels du dossier, vérifiées elles aussi.
    others: tuple[Path, ...]


def _kit_version(name: str) -> Version | None:
    try:
        dist, version, _, _ = parse_wheel_filename(name)
    except InvalidWheelFilename:
        return None
    return version if canonicalize_name(dist) == canonicalize_name(DIST_NAME) else None


def open_bundle(source: Path) -> Bundle:
    """Lit un bundle (dossier ou wheel seule). Lève :class:`TrustError` si rien d'installable n'y est."""
    if not source.exists():
        raise TrustError(f"{source} n'existe pas")
    if source.is_dir():
        directory = source
        wheels = sorted(p for p in directory.glob("*.whl") if p.is_file())
        kits = [p for p in wheels if _kit_version(p.name) is not None]
        if not kits:
            raise TrustError(f"aucune wheel {DIST_NAME} dans {source}")
        if len(kits) > 1:
            raise TrustError(f"plusieurs wheels {DIST_NAME} dans {source} ({', '.join(p.name for p in kits)}) : en garder une")
        wheel, link_dir = kits[0], directory
        others = tuple(p for p in wheels if p != wheel)
    else:
        wheel, directory, link_dir, others = source, source.parent, None, ()
        if _kit_version(wheel.name) is None:
            raise TrustError(f"{wheel.name} n'est pas une wheel {DIST_NAME}")
    version = _kit_version(wheel.name)
    if version is None:  # déjà exclu plus haut ; ne repose pas sur un assert
        raise TrustError(f"{wheel.name} n'est pas une wheel {DIST_NAME}")
    sums_file = directory / SUMS_NAME
    if not sums_file.is_file():
        raise TrustError(f"{SUMS_NAME} absent de {directory} : un bundle sans somme ne s'installe pas")
    try:
        sums = parse_sha256sums(sums_file.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise TrustError(f"{SUMS_NAME} illisible (UTF-8 invalide)") from None
    return Bundle(wheel, version, sums, link_dir, others)


def _check_listed(path: Path, sums: dict[str, str]) -> None:
    expected = sums.get(path.name)
    if expected is None:
        raise TrustError(f"{path.name} n'est pas dans {SUMS_NAME} du bundle")
    actual = sha256_file(path)
    if actual != expected:
        raise TrustError(
            f"{path.name} : l'empreinte du fichier ({actual[:16]}...) diffère de {SUMS_NAME} ({expected[:16]}...)"
        )


def stage_and_verify(bundle: Bundle, scratch: Path) -> tuple[Path, Verdict]:
    """Copie la wheel du kit dans *scratch*, vérifie la copie et les dépendances du dossier."""
    staged = scratch / bundle.wheel.name
    try:
        shutil.copyfile(bundle.wheel, staged)
    except OSError as exc:
        raise TrustError(f"{bundle.wheel.name} illisible ({type(exc).__name__})") from None
    _check_listed(staged, bundle.sums)
    for other in bundle.others:
        _check_listed(other, bundle.sums)
    count = 1 + len(bundle.others)
    return staged, Verdict(
        "bundle",
        f"{count} wheel(s) conformes au {SUMS_NAME} du bundle ; origine non confrontée à une source indépendante (hors ligne)",
    )
