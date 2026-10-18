"""Bundle hors ligne : une wheel, son ``SHA256SUMS`` et, au besoin, ses dépendances, dans un dossier.

``grimoire self update --from <dossier|wheel>`` installe sans ouvrir un seul
socket : ni PyPI, ni GitHub, ni index pour les dépendances (``--no-index`` /
``--offline``, wheels seulement). Ce qui est vérifié, et ce qui ne l'est pas :

* **Vérifié** : la wheel du kit figure dans le ``SHA256SUMS`` du bundle avec la
  même empreinte, et chaque autre wheel du dossier (dépendances) aussi, parce
  que le gestionnaire les lirait à son tour. Une wheel absente de la liste ou
  altérée refuse tout le bundle.
* **Non vérifié** : l'origine. Le ``SHA256SUMS`` voyage avec les fichiers, donc
  un bundle entièrement remplacé reste cohérent. Le niveau rendu est
  ``bundle``, jamais ``verified``. Pour remonter à la source, joindre la liste
  à celle de la release (``gh attestation verify``, à faire en ligne ailleurs).
* **Ce que le gestionnaire voit** : jamais le dossier du bundle, seulement un
  dossier temporaire où ne sont copiées que les wheels listées, hachées *après*
  copie (ce qui est installé est ce qui est vérifié, sans fenêtre entre la
  vérification et l'installation). Le reste du dossier (une page ``links.html``
  qui renverrait vers un fichier extérieur, un sous-dossier, tout fichier qui
  n'est pas une wheel) n'est pas copié, donc jamais lu : ``--find-links`` suit
  les pages HTML, et une wheel que le bundle ne liste pas ne peut pas entrer par
  là.
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
    #: Autres wheels du dossier (dépendances), copiées et vérifiées elles aussi.
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
        wheel = kits[0]
        others = tuple(p for p in wheels if p != wheel)
    else:
        wheel, directory, others = source, source.parent, ()
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
    return Bundle(wheel, version, sums, others)


def _check_listed(path: Path, sums: dict[str, str]) -> None:
    expected = sums.get(path.name)
    if expected is None:
        raise TrustError(f"{path.name} n'est pas dans {SUMS_NAME} du bundle")
    actual = sha256_file(path)
    if actual != expected:
        raise TrustError(
            f"{path.name} : l'empreinte du fichier ({actual[:16]}...) diffère de {SUMS_NAME} ({expected[:16]}...)"
        )


def _stage(source: Path, scratch: Path) -> Path:
    target = scratch / source.name
    try:
        shutil.copyfile(source, target)
    except OSError as exc:
        raise TrustError(f"{source.name} illisible ({type(exc).__name__})") from None
    return target


def stage_and_verify(bundle: Bundle, scratch: Path) -> tuple[Path, Verdict]:
    """Copie dans *scratch* la wheel du kit et les autres wheels listées, puis vérifie les copies.

    *scratch* est le seul dossier à donner au gestionnaire (``--find-links``) : il ne contient
    que des fichiers vérifiés, rien de ce que le dossier du bundle abrite d'autre.
    """
    staged = _stage(bundle.wheel, scratch)
    _check_listed(staged, bundle.sums)
    for other in bundle.others:
        _check_listed(_stage(other, scratch), bundle.sums)
    count = 1 + len(bundle.others)
    return staged, Verdict(
        "bundle",
        f"{count} wheel(s) conformes au {SUMS_NAME} du bundle ; origine non confrontée à une source indépendante (hors ligne)",
    )
