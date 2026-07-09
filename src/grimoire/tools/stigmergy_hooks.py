"""Câblage des hooks stigmergiques dans un projet (install / retrait / état).

Logique partagée par la CLI (``grimoire stigmergy install-hooks``), le canal
de features (``grimoire features enable stigmergy-hooks``) et l'API locale
(``POST /api/features/...``). Les hooks sont non bloquants par construction ;
s'il existe, le registre de sûreté du projet les journalise en mode shadow.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from grimoire.data import framework_path

HOOK_MANIFESTS = ("stigmergy-sense.json", "stigmergy-emit.json")
HOOK_SCRIPTS = (
    "stigmergy_hook.py",
    "stigmergy-sense.sh",
    "stigmergy-emit-post-edit.sh",
    "stigmergy-emit-stop.sh",
)
REGISTRY_RELPATH = Path("_grimoire-runtime") / "_config" / "hook-safety-registry.json"

__all__ = ["HOOK_MANIFESTS", "HOOK_SCRIPTS", "hooks_installed", "install_hooks", "uninstall_hooks"]


def _assets_dir() -> Path:
    return framework_path() / "tools" / "stigmergy_hooks"


def hooks_installed(project_root: Path) -> bool:
    hooks_dir = project_root / ".github" / "hooks"
    return all((hooks_dir / name).is_file() for name in HOOK_MANIFESTS)


def _register_shadow(project_root: Path, install: bool) -> str:
    registry_path = project_root / REGISTRY_RELPATH
    if not registry_path.is_file():
        return ("registre de sûreté absent — hooks copiés, non journalisés "
                "(ils restent non bloquants par construction)")
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "registre de sûreté illisible — journalisation ignorée"
    hooks = registry.setdefault("hooks", {})
    entries = {
        "stigmergy-sense": (".github/hooks/scripts/stigmergy-sense.sh",
                            ".github/hooks/stigmergy-sense.json"),
        "stigmergy-emit": (".github/hooks/scripts/stigmergy-emit-post-edit.sh",
                           ".github/hooks/stigmergy-emit.json"),
    }
    for name, (script, control) in entries.items():
        if install:
            hooks[name] = {"mode": "shadow", "script": script,
                           "control_file": control, "origin": "stigmergy"}
        else:
            hooks.pop(name, None)
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return "hooks journalisés en mode shadow" if install else "hooks retirés du registre"


def install_hooks(project_root: Path) -> str:
    """Copie les hooks dans ``.github/hooks/`` et journalise (best-effort).

    Transactionnel (RUN-14) : si une copie échoue en cours, tous les fichiers
    déjà écrits par cet appel sont retirés — jamais d'installation partielle.
    """
    src = _assets_dir()
    if not src.is_dir():
        msg = f"assets de hooks introuvables : {src}"
        raise FileNotFoundError(msg)
    hooks_dir = project_root / ".github" / "hooks"
    scripts_dir = hooks_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    try:
        for name in HOOK_MANIFESTS:
            dest = hooks_dir / name
            shutil.copy2(src / name, dest)
            written.append(dest)
        for name in HOOK_SCRIPTS:
            dest = scripts_dir / name
            shutil.copy2(src / "scripts" / name, dest)
            dest.chmod(0o755)
            written.append(dest)
    except OSError:
        for dest in written:
            dest.unlink(missing_ok=True)
        raise
    return _register_shadow(project_root, install=True)


def uninstall_hooks(project_root: Path) -> tuple[int, str]:
    """Retire les fichiers de hooks ; renvoie (nb retirés, note registre)."""
    hooks_dir = project_root / ".github" / "hooks"
    removed = 0
    for name in HOOK_MANIFESTS:
        target = hooks_dir / name
        if target.exists():
            target.unlink()
            removed += 1
    for name in HOOK_SCRIPTS:
        target = hooks_dir / "scripts" / name
        if target.exists():
            target.unlink()
            removed += 1
    return removed, _register_shadow(project_root, install=False)
