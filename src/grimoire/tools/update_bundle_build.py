"""Construire un bundle hors ligne que ``grimoire self update --from`` accepte (D-15).

Le lecteur est :mod:`grimoire.tools.update_bundle` ; ce module produit exactement ce qu'il lit
et rien d'autre : un dossier avec **une** wheel ``grimoire-kit``, les wheels de ses dépendances
(``--with-deps``), un ``SHA256SUMS`` qui les liste toutes, et un ``PROVENANCE.json`` (lui aussi
listé) qui dit d'où vient chaque fichier et ce qui a été vérifié à la construction.

Sources de la wheel du kit (une seule) :

* ``version`` (ou, par défaut, la plus haute stable) : PyPI, confrontée à l'empreinte de PyPI
  *et* au ``SHA256SUMS`` de la release (même règle que ``self update``) ;
* ``release`` (``vX.Y.Z`` ou ``vX.Y.Z-rc1``) : l'asset de la release GitHub, confronté à son
  ``SHA256SUMS`` ;
* ``wheel`` : un fichier local (build du dépôt), aucune vérification de source possible.

Provenance : une wheel venue de PyPI ou de GitHub est passée à ``gh attestation verify`` quand ``gh``
est présent. ``gh`` absent : l'attestation est marquée ``unverified`` (écrit, pas un succès). ``gh``
présent et vérification en échec : la construction est refusée (``skip_attestation`` pour passer
outre, et le fichier le dit). Le niveau que verra l'installation reste ``bundle`` : la provenance
est consignée, pas rejouée hors ligne.

Ce qui n'est pas garanti : les dépendances viennent d'un index sans empreinte publiée par le kit ;
pip évalue les marqueurs d'environnement (``sys_platform``...) sur la machine qui construit, pas
sur la plateforme cible : une dépendance réservée à une autre plateforme que celle de la
construction n'est pas téléchargée (``--platform`` ne change que le choix des fichiers).

Le bundle est monté dans un dossier voisin puis déplacé, et relu par le lecteur d'installation
avant d'être rendu : un bundle que ``self update --from`` refuserait n'est jamais livré.
"""

from __future__ import annotations

import importlib.util
import json
import platform as host_platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from grimoire.tools import install_trust as trust
from grimoire.tools import update_bundle as bundles
from grimoire.tools import update_channels as channels

PROVENANCE_NAME = "PROVENANCE.json"
PYPI_JSON = "https://pypi.org/pypi/grimoire-kit/json"
Runner = Callable[..., Any]


class BuildError(Exception):
    """La construction a échoué ; rien n'est laissé à l'endroit demandé."""


@dataclass(frozen=True)
class BuildReport:
    directory: Path
    version: str
    source: str
    build_trust: str
    attestation: str
    wheel: str
    dependencies: tuple[str, ...]


def _fetch_json(url: str, opener: Callable[..., Any]) -> Any:
    try:
        with opener(url, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        raise BuildError(f"{url} injoignable ou illisible ({type(exc).__name__})") from None


def _latest_stable(data: Any) -> str:
    releases = data.get("releases") if isinstance(data, dict) else None
    best: Version | None = None
    if isinstance(releases, dict):
        for raw, files in releases.items():
            shown = channels.stable_only(raw)
            has_file = isinstance(files, list) and any(isinstance(f, dict) and not f.get("yanked") for f in files)
            if shown is not None and has_file and (best is None or Version(shown) > best):
                best = Version(shown)
    if best is None:
        raise BuildError("PyPI ne liste aucune version stable de grimoire-kit")
    return str(best)


def _fetch_pypi(version: str | None, scratch: Path, opener: Callable[..., Any]) -> tuple[Path, str, trust.Verdict, str]:
    chosen = version
    if chosen is None:
        chosen = _latest_stable(_fetch_json(PYPI_JSON, opener))
    try:
        parsed = Version(chosen)
    except InvalidVersion:
        raise BuildError(f"version illisible : {chosen}") from None
    if parsed.is_prerelease or parsed.is_devrelease:
        raise BuildError(f"{chosen} est une pré-version : PyPI ne la publie pas, utiliser --from-release v{chosen}")
    data = _fetch_json(f"https://pypi.org/pypi/grimoire-kit/{chosen}/json", opener)
    try:
        ref = trust.select_wheel(data, chosen)
        wheel = trust.download_wheel(ref, scratch, opener)
        sums = trust.fetch_sums(chosen, opener)
        verdict = trust.check_wheel(wheel, version=chosen, pypi_sha256=ref.sha256, sums=sums)
    except trust.TrustError as exc:
        raise BuildError(str(exc)) from None
    return wheel, str(parsed), verdict, ref.url


def _fetch_release(tag: str, scratch: Path, opener: Callable[..., Any]) -> tuple[Path, str, trust.Verdict, str]:
    if not tag.startswith("v") or "/" in tag or "\\" in tag:
        raise BuildError(f"tag de release attendu sous la forme vX.Y.Z (reçu {tag!r})")
    release = channels.release_wheel(_fetch_json(f"https://api.github.com/repos/{trust.REPO}/releases/tags/{tag}", opener))
    if release is None or release.tag != tag:
        raise BuildError(f"la release {tag} n'existe pas ou ne porte pas de wheel {trust.DIST_NAME} conforme")
    try:
        wheel = trust.download_wheel(release.wheel, scratch, opener)
        sums = trust.fetch_sums(release.version, opener, tag=release.tag)
        verdict = trust.check_release_wheel(wheel, version=release.version, sums=sums)
    except trust.TrustError as exc:
        raise BuildError(str(exc)) from None
    return wheel, release.version, verdict, release.wheel.url


def _local_wheel(path: Path, scratch: Path) -> tuple[Path, str, trust.Verdict, str]:
    if not path.is_file():
        raise BuildError(f"{path} n'est pas un fichier")
    try:
        dist, version, _, _ = parse_wheel_filename(path.name)
    except InvalidWheelFilename:
        raise BuildError(f"{path.name} n'est pas un nom de wheel valide") from None
    if canonicalize_name(dist) != canonicalize_name(trust.DIST_NAME):
        raise BuildError(f"{path.name} n'est pas une wheel {trust.DIST_NAME}")
    target = scratch / path.name
    shutil.copyfile(path, target)
    verdict = trust.Verdict("local", "wheel locale : aucune source indépendante à confronter")
    return target, str(version), verdict, path.name


def _attest(wheel: Path, kind: str, *, skip: bool, runner: Runner, which: Callable[[str], str | None]) -> dict[str, str]:
    if kind == "local-wheel":
        return {"status": "not-applicable", "detail": "wheel locale : pas de release à attester"}
    if skip:
        return {"status": "skipped", "detail": "--skip-attestation : non vérifiée à la demande de l'opérateur"}
    gh = which("gh")
    if gh is None:
        return {"status": "unverified", "detail": "gh absent : attestation de la release non vérifiée"}
    try:
        done = runner(
            [gh, "attestation", "verify", str(wheel), "--repo", trust.REPO],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildError(f"gh attestation verify n'a pas pu s'exécuter ({type(exc).__name__}) ; --skip-attestation pour construire sans") from None
    if done.returncode != 0:
        tail = " ".join(str(done.stderr or done.stdout or "").split())[-300:]
        raise BuildError(f"gh attestation verify a refusé {wheel.name} : {tail} (--skip-attestation pour construire sans)")
    return {"status": "verified", "detail": f"gh attestation verify --repo {trust.REPO}"}


def pip_command(pip_python: str | None, which: Callable[[str], str | None] = shutil.which) -> list[str]:
    """Le pip qui télécharge : l'interpréteur demandé, celui-ci s'il a pip, un ``pip`` du PATH, sinon ``uv tool run pip``."""
    if pip_python:
        return [pip_python, "-m", "pip"]
    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip"]
    for name in ("pip", "pip3"):
        found = which(name)
        if found:
            return [found]
    uv = which("uv")
    if uv:
        return [uv, "tool", "run", "pip"]
    raise BuildError("aucun pip pour télécharger les dépendances : installer pip ou donner --python <interpréteur avec pip>")


def download_argv(
    pip: Sequence[str], wheel: Path, dest: Path, *, platforms: Sequence[str] = (), python_version: str | None = None,
    implementation: str | None = None, abis: Sequence[str] = (), index_url: str | None = None, find_links: Sequence[str] = (),
) -> list[str]:
    """``pip download`` des dépendances de *wheel* : wheels seulement, jamais une source à compiler."""
    argv = [*pip, "download", "--only-binary=:all:", "--no-input", "--disable-pip-version-check", "--dest", str(dest)]
    if index_url:
        argv += ["--index-url", index_url]
    for link in find_links:
        argv += ["--find-links", link]
    for plat in platforms:
        argv += ["--platform", plat]
    if python_version:
        argv += ["--python-version", python_version]
    if implementation:
        argv += ["--implementation", implementation]
    for abi in abis:
        argv += ["--abi", abi]
    return [*argv, str(wheel)]


def _download_deps(argv: list[str], kit: Path, depdir: Path, runner: Runner) -> list[Path]:
    try:
        done = runner(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildError(f"pip download n'a pas pu s'exécuter ({type(exc).__name__})") from None
    if done.returncode != 0:
        tail = " ".join(str(done.stderr or done.stdout or "").split())[-400:]
        raise BuildError(f"pip download a échoué : {tail}")
    found: list[Path] = []
    for item in sorted(depdir.iterdir()):
        if item.name == kit.name:
            continue  # selon la version de pip, le fichier local est recopié : la wheel du kit est déjà là
        if not item.is_file() or item.suffix != ".whl":
            raise BuildError(f"pip a rendu autre chose qu'une wheel ({item.name}) : refusé")
        if bundles.is_kit_wheel(item.name):
            raise BuildError(f"les dépendances contiennent une autre wheel {trust.DIST_NAME} ({item.name}) : refusé")
        found.append(item)
    return found


def _write_lf(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def build_bundle(
    dest: Path,
    *,
    wheel: Path | None = None,
    version: str | None = None,
    release: str | None = None,
    with_deps: bool = False,
    platforms: Sequence[str] = (),
    python_version: str | None = None,
    implementation: str | None = None,
    abis: Sequence[str] = (),
    index_url: str | None = None,
    find_links: Sequence[str] = (),
    pip_python: str | None = None,
    skip_attestation: bool = False,
    opener: Callable[..., Any] = urllib.request.urlopen,
    runner: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> BuildReport:
    """Construit le bundle dans *dest* (absent ou vide). Lève :class:`BuildError`, sans rien laisser derrière."""
    if sum(x is not None for x in (wheel, version, release)) > 1:
        raise BuildError("une seule source de wheel : --version, --from-release ou --wheel")
    target_flags = bool(platforms or python_version or implementation or abis)
    if target_flags and not with_deps:
        raise BuildError("--platform, --python-version, --implementation et --abi ne servent qu'avec --with-deps")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise BuildError(f"{dest} existe et n'est pas un dossier vide : un bundle ne s'écrit pas par-dessus autre chose")
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".grimoire-bundle-", dir=parent))
    try:
        built = _assemble(
            staging, wheel=wheel, version=version, release=release, with_deps=with_deps, platforms=platforms,
            python_version=python_version, implementation=implementation, abis=abis, index_url=index_url,
            find_links=find_links, pip_python=pip_python, skip_attestation=skip_attestation,
            opener=opener, runner=runner, which=which,
        )
        staging.chmod(0o755)  # mkdtemp crée en 0700 : le bundle se copie, il doit rester lisible
        if dest.exists():
            dest.rmdir()
        shutil.move(str(staging), str(dest))  # dest a été retiré : un simple renommage
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return BuildReport(dest, *built)


def _assemble(
    staging: Path, *, wheel: Path | None, version: str | None, release: str | None, with_deps: bool,
    platforms: Sequence[str], python_version: str | None, implementation: str | None, abis: Sequence[str],
    index_url: str | None, find_links: Sequence[str], pip_python: str | None, skip_attestation: bool,
    opener: Callable[..., Any], runner: Runner, which: Callable[[str], str | None],
) -> tuple[str, str, str, str, str, tuple[str, ...]]:
    if wheel is not None:
        kind = "local-wheel"
        kit, kit_version, verdict, origin = _local_wheel(wheel, staging)
    elif release is not None:
        kind = "github-release"
        kit, kit_version, verdict, origin = _fetch_release(release, staging, opener)
    else:
        kind = "pypi"
        kit, kit_version, verdict, origin = _fetch_pypi(version, staging, opener)
    attestation = _attest(kit, kind, skip=skip_attestation, runner=runner, which=which)

    deps: list[Path] = []
    if with_deps:
        depdir = staging / ".deps"
        depdir.mkdir()
        pip = pip_command(pip_python, which)
        argv = download_argv(
            pip, kit, depdir, platforms=platforms, python_version=python_version, implementation=implementation,
            abis=abis, index_url=index_url, find_links=find_links,
        )
        for found in _download_deps(argv, kit, depdir, runner):
            deps.append(Path(shutil.move(str(found), staging / found.name)))
        shutil.rmtree(depdir)  # y reste, au besoin, la copie du kit que pip y dépose

    sums = {p.name: trust.sha256_file(p) for p in (kit, *deps)}
    provenance = {
        "schema": 1,
        "tool": "grimoire self bundle",
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "built_on": {"system": host_platform.system(), "machine": host_platform.machine(), "python": host_platform.python_version()},
        "kit": {"name": kit.name, "version": kit_version, "sha256": sums[kit.name]},
        "source": {"kind": kind, "origin": origin},
        "build_trust": {"level": verdict.level, "detail": verdict.detail},
        "attestation": attestation,
        "dependencies": [{"name": p.name, "sha256": sums[p.name]} for p in deps],
        "target": {
            "with_deps": with_deps, "platform": list(platforms), "python_version": python_version,
            "implementation": implementation, "abi": list(abis),
        },
        "install_trust_level": "bundle",
    }
    _write_lf(staging / PROVENANCE_NAME, json.dumps(provenance, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    sums[PROVENANCE_NAME] = trust.sha256_file(staging / PROVENANCE_NAME)
    _write_lf(staging / trust.SUMS_NAME, "".join(f"{digest}  {name}\n" for name, digest in sorted(sums.items())))

    _self_check(staging, kit.name)
    return kit_version, kind, verdict.level, attestation["status"], kit.name, tuple(p.name for p in deps)


def _self_check(staging: Path, kit_name: str) -> None:
    """Relit le bundle comme ``self update --from`` : s'il le refuserait, il n'est pas livré."""
    with tempfile.TemporaryDirectory(prefix="grimoire-bundle-check-") as scratch:
        try:
            bundle = bundles.open_bundle(staging)
            _, verdict = bundles.stage_and_verify(bundle, Path(scratch))
        except trust.TrustError as exc:
            raise BuildError(f"le bundle construit serait refusé par l'installation : {exc}") from None
        if bundle.wheel.name != kit_name or verdict.level != "bundle":
            raise BuildError("le bundle construit ne se relit pas comme prévu")
