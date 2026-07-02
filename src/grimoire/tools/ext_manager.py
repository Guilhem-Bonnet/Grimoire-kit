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
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "ExtensionError",
    "InstallResult",
    "install_extension",
    "list_installed",
    "load_manifest",
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


def load_manifest(ext_dir: Path) -> dict:
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


def validate_manifest(manifest: dict, ext_dir: Path) -> list[str]:
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


def _load_state(project_root: Path) -> dict:
    state_path = project_root / STATE_RELPATH
    if state_path.is_file():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {}


def _save_state(project_root: Path, state: dict) -> None:
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


def list_installed(project_root: Path) -> dict:
    return _load_state(project_root.resolve())


def remove_extension(ext_id: str, project_root: Path, *, skip_scripts: bool = False) -> None:
    project_root = project_root.resolve()
    state = _load_state(project_root)
    if ext_id not in state:
        raise ExtensionError(f"extension non installée : {ext_id}")

    entry = state[ext_id]
    source = Path(entry["source"])
    if not skip_scripts and source.is_dir():
        manifest = load_manifest(source)
        for step in manifest.get("uninstall", {}).get("steps", []):
            if step.get("kind") == "script":
                _run_script(source, project_root, step["path"], step.get("args"))

    for rel in entry.get("files", []):
        target = project_root / rel
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file():
            target.unlink()

    del state[ext_id]
    _save_state(project_root, state)


def verify_extension(ext_id: str, project_root: Path) -> None:
    project_root = project_root.resolve()
    state = _load_state(project_root)
    if ext_id not in state:
        raise ExtensionError(f"extension non installée : {ext_id}")
    source = Path(state[ext_id]["source"])
    if not source.is_dir():
        raise ExtensionError(f"source introuvable : {source}")
    manifest = load_manifest(source)
    verify = manifest["install"].get("verify")
    if not verify:
        print(f"{ext_id} : pas de script de vérification déclaré")
        return
    _run_script(source, project_root, verify)
    print(f"{ext_id} : vérification OK")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grimoire ext", description="Gestion des extensions Grimoire"
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Installer une extension depuis un dossier")
    p_add.add_argument("source", type=Path)
    p_add.add_argument("--skip-scripts", action="store_true")
    p_add.add_argument("--force", action="store_true")

    sub.add_parser("list", help="Extensions installées")

    p_remove = sub.add_parser("remove", help="Désinstaller une extension")
    p_remove.add_argument("id")
    p_remove.add_argument("--skip-scripts", action="store_true")

    p_verify = sub.add_parser("verify", help="Vérifier une extension installée")
    p_verify.add_argument("id")

    args = parser.parse_args(argv)
    try:
        if args.command == "add":
            result = install_extension(
                args.source,
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
