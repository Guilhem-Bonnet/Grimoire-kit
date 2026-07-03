"""Gestionnaire d'extensions Grimoire — bundles d'artefacts gouvernés.

Une extension est un dossier contenant un ``extension.json`` (manifeste,
schéma de référence dans ``extensions/extension.schema.json``) et des
artefacts copiés vers les surfaces gouvernées du projet cible
(``.github/agents/``, ``.github/skills/``, ``.github/hooks/``...).

Distinct de ``grimoire plugins`` (découverte d'entry-points Python) :
une extension s'installe dans un projet, pas dans l'environnement Python.

Contraintes:
- Tout hook fourni par une extension démarre en mode ``shadow``.
- Les chemins du manifeste sont relatifs, sans remontée (``..``).
- L'état installé vit dans ``_grimoire/extensions/installed.json``.

Usage standalone::

    python3 -m grimoire.tools.ext_manager --project-root . add <dir>
    python3 -m grimoire.tools.ext_manager --project-root . list
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

__all__ = [
    "ExtensionError",
    "InstallResult",
    "install_blueprint_from_registry",
    "install_extension",
    "install_from_registry",
    "list_installed",
    "load_manifest",
    "publish_blueprint",
    "publish_extension",
    "remove_extension",
    "validate_manifest",
    "verify_extension",
]

MANIFEST_NAME = "extension.json"
STATE_RELPATH = Path("_grimoire") / "extensions" / "installed.json"

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
PATTERN_ID_RE = re.compile(r"^(ORG|ORC|GOV|QUA|KNO|RUN|COG|MOD)-\d{2}$")

REQUIRED_KEYS = (
    "manifestVersion",
    "id",
    "name",
    "version",
    "description",
    "license",
    "authors",
    "compat",
    "provides",
    "patterns",
    "permissions",
    "install",
)
STEP_KINDS = ("copy", "script", "pip", "npm")
FILESYSTEM_PERMS = ("none", "artifacts", "workspace")
MEMORY_PERMS = ("none", "read", "readwrite")


class ExtensionError(RuntimeError):
    """Erreur de manifeste ou d'installation d'extension."""


@dataclass(frozen=True, slots=True)
class InstallResult:
    """Bilan d'une installation."""

    extension_id: str
    version: str
    copied: tuple[str, ...] = ()
    executed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


def _is_safe_relpath(value: str) -> bool:
    if not value or value.startswith(("/", "\\")):
        return False
    return ".." not in Path(value).parts


def load_manifest(ext_dir: Path) -> dict[str, Any]:
    manifest_path = ext_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ExtensionError(f"{MANIFEST_NAME} introuvable dans {ext_dir}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExtensionError(f"{manifest_path} : JSON invalide ({exc})") from exc
    if not isinstance(manifest, dict):
        raise ExtensionError(f"{manifest_path} : objet JSON attendu")
    return manifest


def validate_manifest(manifest: dict[str, Any], ext_dir: Path) -> list[str]:
    """Validation structurelle du manifeste. Retourne la liste des erreurs."""
    errors: list[str] = []

    for key in REQUIRED_KEYS:
        if key not in manifest:
            errors.append(f"champ requis manquant : {key}")
    if errors:
        return errors

    if manifest["manifestVersion"] != 1:
        errors.append("manifestVersion non supporté (attendu : 1)")
    if not ID_RE.match(str(manifest["id"])):
        errors.append(f"id invalide (kebab-case attendu) : {manifest['id']}")
    if not SEMVER_RE.match(str(manifest["version"])):
        errors.append(f"version non semver : {manifest['version']}")
    if not manifest["authors"]:
        errors.append("authors vide")

    implements = manifest["patterns"].get("implements", [])
    if not implements:
        errors.append("patterns.implements vide (mapping catalogue obligatoire)")
    errors.extend(
        f"pattern id invalide : {pid}"
        for pid in [*implements, *manifest["patterns"].get("requires", [])]
        if not PATTERN_ID_RE.match(str(pid))
    )

    perms = manifest["permissions"]
    if perms.get("filesystem") not in FILESYSTEM_PERMS:
        errors.append(f"permissions.filesystem invalide : {perms.get('filesystem')}")
    if perms.get("memory") not in MEMORY_PERMS:
        errors.append(f"permissions.memory invalide : {perms.get('memory')}")
    if not isinstance(perms.get("network"), bool):
        errors.append("permissions.network doit être booléen")

    for kind, paths in manifest["provides"].items():
        if kind == "nodes":
            continue
        for rel in paths:
            if not _is_safe_relpath(rel):
                errors.append(f"provides.{kind} : chemin non sûr : {rel}")
            elif not (ext_dir / rel).exists():
                errors.append(f"provides.{kind} : fichier absent : {rel}")

    steps = manifest["install"].get("steps", [])
    if not steps:
        errors.append("install.steps vide")
    for i, step in enumerate(steps):
        kind = step.get("kind")
        if kind not in STEP_KINDS:
            errors.append(f"step {i} : kind invalide : {kind}")
            continue
        if kind == "copy":
            if not _is_safe_relpath(step.get("from", "")):
                errors.append(f"step {i} : from non sûr : {step.get('from')}")
            elif not (ext_dir / step["from"]).exists():
                errors.append(f"step {i} : source absente : {step['from']}")
            if not _is_safe_relpath(step.get("to", "")):
                errors.append(f"step {i} : to non sûr : {step.get('to')}")
        elif kind == "script":
            if not _is_safe_relpath(step.get("path", "")):
                errors.append(f"step {i} : path non sûr : {step.get('path')}")
            elif not (ext_dir / step["path"]).is_file():
                errors.append(f"step {i} : script absent : {step['path']}")
        elif not step.get("packages"):
            errors.append(f"step {i} : packages vide")

    verify = manifest["install"].get("verify")
    if verify and not (ext_dir / verify).is_file():
        errors.append(f"install.verify : script absent : {verify}")

    return errors


def _load_state(project_root: Path) -> dict[str, Any]:
    state_path = project_root / STATE_RELPATH
    if state_path.is_file():
        return cast(dict[str, Any], json.loads(state_path.read_text(encoding="utf-8")))
    return {}


def _save_state(project_root: Path, state: dict[str, Any]) -> None:
    state_path = project_root / STATE_RELPATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _run_script(
    ext_dir: Path, project_root: Path, script: str, args: list[str] | None = None
) -> None:
    result = subprocess.run(
        ["bash", str(ext_dir / script), *(args or [])],
        cwd=ext_dir,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GRIMOIRE_PROJECT_ROOT": str(project_root),
            "GRIMOIRE_EXT_DIR": str(ext_dir),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ExtensionError(
            f"script {script} a échoué (code {result.returncode}) :\n{result.stderr.strip()}"
        )


def install_extension(
    ext_dir: Path, project_root: Path, *, skip_scripts: bool = False, force: bool = False
) -> InstallResult:
    ext_dir = ext_dir.resolve()
    project_root = project_root.resolve()
    manifest = load_manifest(ext_dir)
    errors = validate_manifest(manifest, ext_dir)
    if errors:
        raise ExtensionError("manifeste invalide :\n  - " + "\n  - ".join(errors))

    ext_id = manifest["id"]
    state = _load_state(project_root)
    if ext_id in state and not force:
        raise ExtensionError(
            f"extension déjà installée : {ext_id} v{state[ext_id]['version']} "
            "(utiliser --force pour réinstaller)"
        )

    copied: list[str] = []
    executed: list[str] = []
    skipped: list[str] = []

    for step in manifest["install"]["steps"]:
        kind = step["kind"]
        if kind == "copy":
            source = ext_dir / step["from"]
            target = project_root / step["to"]
            if target.exists() and not force:
                raise ExtensionError(f"cible existante : {step['to']} (utiliser --force)")
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=force)
            else:
                shutil.copy2(source, target)
            copied.append(step["to"])
        elif kind == "script":
            if skip_scripts:
                skipped.append(f"script:{step['path']}")
                continue
            _run_script(ext_dir, project_root, step["path"], step.get("args"))
            executed.append(f"script:{step['path']}")
        elif kind == "pip":
            pip = project_root / ".venv" / "bin" / "pip"
            if not pip.is_file():
                skipped.append(f"pip:{','.join(step['packages'])} (pas de .venv projet)")
                continue
            result = subprocess.run(
                [str(pip), "install", "--quiet", *step["packages"]],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise ExtensionError(f"pip install a échoué :\n{result.stderr.strip()}")
            executed.append(f"pip:{','.join(step['packages'])}")
        else:  # npm
            if shutil.which("npm") is None:
                skipped.append(f"npm:{','.join(step['packages'])} (npm absent)")
                continue
            result = subprocess.run(
                ["npm", "install", "--prefix", str(project_root), *step["packages"]],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise ExtensionError(f"npm install a échoué :\n{result.stderr.strip()}")
            executed.append(f"npm:{','.join(step['packages'])}")

    state[ext_id] = {
        "version": manifest["version"],
        "source": str(ext_dir),
        "installedAt": datetime.now(UTC).isoformat(),
        "patterns": manifest["patterns"]["implements"],
        "files": copied,
        "skipped": skipped,
    }
    _save_state(project_root, state)
    return InstallResult(
        extension_id=ext_id,
        version=manifest["version"],
        copied=tuple(copied),
        executed=tuple(executed),
        skipped=tuple(skipped),
    )


def list_installed(project_root: Path) -> dict[str, Any]:
    return _load_state(project_root.resolve())


def remove_extension(ext_id: str, project_root: Path, *, skip_scripts: bool = False) -> None:
    project_root = project_root.resolve()
    state = _load_state(project_root)
    if ext_id not in state:
        raise ExtensionError(f"extension non installée : {ext_id}")

    entry = state[ext_id]
    registry_tmp = _registry_source_dir(entry["source"])
    source = Path(registry_tmp.name) if registry_tmp else Path(entry["source"])
    try:
        if not skip_scripts and source.is_dir():
            manifest = load_manifest(source)
            for step in manifest.get("uninstall", {}).get("steps", []):
                if step.get("kind") == "script":
                    _run_script(source, project_root, step["path"], step.get("args"))
    finally:
        if registry_tmp:
            registry_tmp.cleanup()

    for rel in entry.get("files", []):
        target = project_root / rel
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file():
            target.unlink()

    del state[ext_id]
    _save_state(project_root, state)


REGISTRY_INDEX = "registry.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _deterministic_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    return info


def publish_extension(ext_dir: Path, registry_dir: Path) -> dict[str, Any]:
    """Publie une extension dans un registry local (archive + entrée d'index).

    L'archive est déterministe (mtime/uid normalisés) : republier une
    extension inchangée produit le même checksum.
    """
    ext_dir = ext_dir.resolve()
    registry_dir = registry_dir.resolve()
    manifest = load_manifest(ext_dir)
    errors = validate_manifest(manifest, ext_dir)
    if errors:
        raise ExtensionError("manifeste invalide :\n  - " + "\n  - ".join(errors))

    ext_id, version = manifest["id"], manifest["version"]
    dist_rel = Path("dist") / f"{ext_id}-{version}.tar.gz"
    dist_path = registry_dir / dist_rel
    dist_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dist_path, "w:gz") as tar:
        for path in sorted(p for p in ext_dir.rglob("*") if p.is_file()):
            tar.add(path, arcname=str(path.relative_to(ext_dir)), filter=_deterministic_filter)
    checksum = _sha256(dist_path)

    index_path = registry_dir / REGISTRY_INDEX
    index: dict[str, Any] = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.is_file()
        else {"registryVersion": 1, "extensions": {}}
    )
    entry = index["extensions"].setdefault(ext_id, {"versions": []})
    release = {
        "version": version,
        "archive": str(dist_rel),
        "checksum": checksum,
        "publishedAt": datetime.now(UTC).isoformat(),
        "summary": {
            "name": manifest["name"],
            "description": manifest["description"],
            "license": manifest["license"],
            "patterns": manifest["patterns"]["implements"],
            "permissions": manifest["permissions"],
            "upstream": manifest.get("upstream", {}).get("repository"),
        },
    }
    entry["versions"] = [r for r in entry["versions"] if r["version"] != version]
    entry["versions"].append(release)
    entry["versions"].sort(key=lambda r: r["version"])
    entry["latest"] = entry["versions"][-1]["version"]
    index["updatedAt"] = datetime.now(UTC).isoformat()
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return release


def validate_blueprint_file(bp_path: Path) -> tuple[dict[str, Any], list[str]]:
    """Validation structurelle légère d'un fichier .blueprint.json."""
    errors: list[str] = []
    try:
        blueprint = json.loads(bp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtensionError(f"{bp_path} : illisible ({exc})") from exc
    if blueprint.get("blueprintVersion") != 1:
        errors.append("blueprintVersion non supporté (attendu : 1)")
    if not ID_RE.match(str(blueprint.get("id", ""))):
        errors.append(f"id invalide : {blueprint.get('id')}")
    if not isinstance(blueprint.get("nodes"), list) or not blueprint["nodes"]:
        errors.append("nodes vide ou absent")
    if not isinstance(blueprint.get("edges"), list):
        errors.append("edges absent")
    return blueprint, errors


def publish_blueprint(bp_path: Path, registry_dir: Path) -> dict[str, Any]:
    """Publie un blueprint dans le registry (fichier + entrée d'index)."""
    bp_path = bp_path.resolve()
    registry_dir = registry_dir.resolve()
    blueprint, errors = validate_blueprint_file(bp_path)
    if errors:
        raise ExtensionError("blueprint invalide :\n  - " + "\n  - ".join(errors))

    bp_id = blueprint["id"]
    file_rel = Path("blueprints") / f"{bp_id}.blueprint.json"
    target = registry_dir / file_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(blueprint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    checksum = _sha256(target)

    index_path = registry_dir / REGISTRY_INDEX
    index: dict[str, Any] = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.is_file()
        else {"registryVersion": 1, "extensions": {}}
    )
    entry = {
        "file": str(file_rel),
        "checksum": checksum,
        "publishedAt": datetime.now(UTC).isoformat(),
        "summary": {
            "name": blueprint.get("name", bp_id),
            "description": blueprint.get("description", ""),
            "catalogVersion": blueprint.get("catalogRef", {}).get("version"),
            "nodes": len(blueprint["nodes"]),
            "edges": len(blueprint["edges"]),
            "extensions": [e.get("id") for e in blueprint.get("extensions", [])],
        },
    }
    index.setdefault("blueprints", {})[bp_id] = entry
    index["updatedAt"] = datetime.now(UTC).isoformat()
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return entry


def install_blueprint_from_registry(
    bp_id: str, registry_dir: Path, project_root: Path, *, force: bool = False
) -> dict[str, Any]:
    """Installe un blueprint publié : checksum vérifié, extensions requises rapportées."""
    registry_dir = registry_dir.resolve()
    project_root = project_root.resolve()
    index_path = registry_dir / REGISTRY_INDEX
    if not index_path.is_file():
        raise ExtensionError(f"registry introuvable : {index_path}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = index.get("blueprints", {}).get(bp_id)
    if not entry:
        raise ExtensionError(f"blueprint absent du registry : {bp_id}")
    source = registry_dir / entry["file"]
    if not source.is_file():
        raise ExtensionError(f"fichier absent : {source}")
    if _sha256(source) != entry["checksum"]:
        raise ExtensionError(f"checksum invalide pour le blueprint {bp_id}")

    target = project_root / "_grimoire" / "blueprints" / f"{bp_id}.blueprint.json"
    if target.exists() and not force:
        raise ExtensionError(f"blueprint déjà présent : {target} (utiliser --force)")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)

    installed = list_installed(project_root)
    blueprint = json.loads(source.read_text(encoding="utf-8"))
    missing = [
        e.get("id") for e in blueprint.get("extensions", [])
        if e.get("id") not in installed
    ]
    return {
        "installed": bp_id,
        "path": str(target.relative_to(project_root)),
        "missingExtensions": missing,
    }


def _safe_extract(archive: Path, dest: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            if not _is_safe_relpath(member.name) or not (member.isfile() or member.isdir()):
                raise ExtensionError(f"archive refusée, membre non sûr : {member.name}")
        tar.extractall(dest, filter="data")


def install_from_registry(
    ext_id: str,
    registry_dir: Path,
    project_root: Path,
    *,
    version: str | None = None,
    skip_scripts: bool = False,
    force: bool = False,
) -> InstallResult:
    """Installe une extension depuis un registry, checksum vérifié avant extraction."""
    registry_dir = registry_dir.resolve()
    index_path = registry_dir / REGISTRY_INDEX
    if not index_path.is_file():
        raise ExtensionError(f"registry introuvable : {index_path}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = index.get("extensions", {}).get(ext_id)
    if not entry:
        raise ExtensionError(f"extension absente du registry : {ext_id}")
    wanted = version or entry["latest"]
    release = next((r for r in entry["versions"] if r["version"] == wanted), None)
    if release is None:
        raise ExtensionError(f"version absente du registry : {ext_id} {wanted}")

    archive = registry_dir / release["archive"]
    if not archive.is_file():
        raise ExtensionError(f"archive absente : {archive}")
    actual = _sha256(archive)
    if actual != release["checksum"]:
        raise ExtensionError(
            f"checksum invalide pour {ext_id} {wanted} : attendu "
            f"{release['checksum']}, obtenu {actual}"
        )

    with tempfile.TemporaryDirectory(prefix=f"grimoire-ext-{ext_id}-") as tmp:
        _safe_extract(archive, Path(tmp))
        result = install_extension(
            Path(tmp), project_root, skip_scripts=skip_scripts, force=force
        )

    # Provenance registry (le dossier temporaire disparaît) : remove/verify
    # sauront re-extraire l'archive.
    state = _load_state(project_root.resolve())
    state[ext_id]["source"] = f"registry:{registry_dir}#{ext_id}@{wanted}"
    state[ext_id]["checksum"] = release["checksum"]
    _save_state(project_root.resolve(), state)
    return result


def _registry_source_dir(source: str) -> tempfile.TemporaryDirectory[str] | None:
    """Re-extrait l'archive d'une provenance ``registry:<dir>#<id>@<version>``."""
    if not source.startswith("registry:"):
        return None
    location, _, ref = source[len("registry:"):].partition("#")
    ext_id, _, version = ref.partition("@")
    archive = Path(location) / "dist" / f"{ext_id}-{version}.tar.gz"
    if not archive.is_file():
        return None
    tmp = tempfile.TemporaryDirectory(prefix=f"grimoire-ext-{ext_id}-")
    _safe_extract(archive, Path(tmp.name))
    return tmp


def verify_extension(ext_id: str, project_root: Path) -> None:
    project_root = project_root.resolve()
    state = _load_state(project_root)
    if ext_id not in state:
        raise ExtensionError(f"extension non installée : {ext_id}")
    registry_tmp = _registry_source_dir(state[ext_id]["source"])
    source = Path(registry_tmp.name) if registry_tmp else Path(state[ext_id]["source"])
    try:
        if not source.is_dir():
            raise ExtensionError(f"source introuvable : {source}")
        manifest = load_manifest(source)
        verify = manifest["install"].get("verify")
        if not verify:
            print(f"{ext_id} : pas de script de vérification déclaré")
            return
        _run_script(source, project_root, verify)
        print(f"{ext_id} : vérification OK")
    finally:
        if registry_tmp:
            registry_tmp.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grimoire ext", description="Gestion des extensions Grimoire"
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Installer une extension (dossier ou registry)")
    p_add.add_argument("source", help="Dossier d'extension, ou id si --registry")
    p_add.add_argument("--registry", type=Path, default=None)
    p_add.add_argument("--version", default=None)
    p_add.add_argument("--skip-scripts", action="store_true")
    p_add.add_argument("--force", action="store_true")

    sub.add_parser("list", help="Extensions installées")

    p_publish = sub.add_parser(
        "publish", help="Publier une extension ou un blueprint dans un registry"
    )
    p_publish.add_argument("source", type=Path)
    p_publish.add_argument("--registry", type=Path, required=True)

    p_addbp = sub.add_parser("add-blueprint", help="Installer un blueprint publié")
    p_addbp.add_argument("id")
    p_addbp.add_argument("--registry", type=Path, required=True)
    p_addbp.add_argument("--force", action="store_true")

    p_remove = sub.add_parser("remove", help="Désinstaller une extension")
    p_remove.add_argument("id")
    p_remove.add_argument("--skip-scripts", action="store_true")

    p_verify = sub.add_parser("verify", help="Vérifier une extension installée")
    p_verify.add_argument("id")

    args = parser.parse_args(argv)
    try:
        if args.command == "publish":
            if str(args.source).endswith(".blueprint.json"):
                bp = publish_blueprint(args.source, args.registry)
                print(
                    f"Blueprint publié : {bp['summary']['name']} — {bp['file']} "
                    f"({bp['checksum'][:19]}…)"
                )
            else:
                release = publish_extension(args.source, args.registry)
                print(
                    f"Publié : {release['summary']['name']} {release['version']} "
                    f"— {release['archive']} ({release['checksum'][:19]}…)"
                )
        elif args.command == "add-blueprint":
            result_bp = install_blueprint_from_registry(
                args.id, args.registry, args.project_root, force=args.force
            )
            print(f"Blueprint installé : {result_bp['installed']} -> {result_bp['path']}")
            if result_bp["missingExtensions"]:
                print(
                    "  Extensions requises non installées : "
                    + ", ".join(result_bp["missingExtensions"])
                )
        elif args.command == "add":
            if args.registry:
                result = install_from_registry(
                    str(args.source),
                    args.registry,
                    args.project_root,
                    version=args.version,
                    skip_scripts=args.skip_scripts,
                    force=args.force,
                )
            else:
                result = install_extension(
                    Path(args.source),
                    args.project_root,
                    skip_scripts=args.skip_scripts,
                    force=args.force,
                )
            print(f"Installé : {result.extension_id} v{result.version}")
            for rel in result.copied:
                print(f"  copie   : {rel}")
            for item in result.executed:
                print(f"  exécuté : {item}")
            for item in result.skipped:
                print(f"  ignoré  : {item}")
        elif args.command == "list":
            state = list_installed(args.project_root)
            if not state:
                print("Aucune extension installée.")
            for ext_id, entry in sorted(state.items()):
                patterns = ", ".join(entry.get("patterns", []))
                print(f"{ext_id} v{entry['version']} — patterns : {patterns}")
        elif args.command == "remove":
            remove_extension(args.id, args.project_root, skip_scripts=args.skip_scripts)
            print(f"Désinstallé : {args.id}")
        elif args.command == "verify":
            verify_extension(args.id, args.project_root)
    except ExtensionError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
