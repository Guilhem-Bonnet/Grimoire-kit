#!/usr/bin/env python3
"""CI de conformité du registry d'extensions Grimoire.

Vérifie l'index, les checksums des archives, le manifeste contenu dans
chaque archive (validation structurelle complète via grimoire-kit), la
cohérence hooks/permissions et, si un export catalogue est fourni,
l'existence des patterns déclarés.

Usage:
    python3 scripts/validate-registry.py --registry . [--catalogue export.json]

Dépendances : grimoire-kit (pip install grimoire-kit) ; jsonschema optionnel
pour la validation du schéma d'index.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import tempfile
from pathlib import Path

from grimoire.tools.ext_manager import load_manifest, validate_manifest


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def check_release(
    registry: Path, ext_id: str, release: dict, catalogue_patterns: set[str] | None
) -> list[str]:
    errors: list[str] = []
    ref = f"{ext_id}@{release['version']}"
    archive = registry / release["archive"]
    if not archive.is_file():
        return [f"{ref} : archive absente ({release['archive']})"]
    if sha256(archive) != release["checksum"]:
        errors.append(f"{ref} : checksum invalide")
        return errors

    with tempfile.TemporaryDirectory(prefix="registry-ci-") as tmp:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                name = member.name
                if name.startswith(("/", "..")) or "/../" in name:
                    errors.append(f"{ref} : membre d'archive non sûr : {name}")
                    return errors
            tar.extractall(tmp, filter="data")
        ext_dir = Path(tmp)
        manifest = load_manifest(ext_dir)
        errors.extend(f"{ref} : {e}" for e in validate_manifest(manifest, ext_dir))
        if manifest.get("id") != ext_id:
            errors.append(f"{ref} : id du manifeste ({manifest.get('id')}) != index")
        if manifest.get("version") != release["version"]:
            errors.append(f"{ref} : version du manifeste != index")
        if manifest.get("provides", {}).get("hooks") and not manifest.get(
            "permissions", {}
        ).get("hooks"):
            errors.append(f"{ref} : hooks fournis mais permissions.hooks vide")
        if catalogue_patterns is not None:
            errors.extend(
                f"{ref} : pattern inconnu du catalogue : {pid}"
                for pid in manifest.get("patterns", {}).get("implements", [])
                if pid not in catalogue_patterns
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validation du registry d'extensions")
    parser.add_argument("--registry", type=Path, default=Path(__file__).parents[1])
    parser.add_argument("--catalogue", type=Path, default=None)
    args = parser.parse_args()

    registry = args.registry.resolve()
    index_path = registry / "registry.json"
    if not index_path.is_file():
        print(f"ERREUR : {index_path} introuvable", file=sys.stderr)
        return 1
    index = json.loads(index_path.read_text(encoding="utf-8"))

    try:
        import jsonschema

        schema = json.loads(
            (registry / "registry.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.validate(index, schema)
        print("OK : index conforme au schéma")
    except ImportError:
        print("INFO : jsonschema absent, validation du schéma d'index sautée")

    catalogue_patterns = None
    if args.catalogue:
        catalogue = json.loads(args.catalogue.read_text(encoding="utf-8"))
        catalogue_patterns = {p["id"] for p in catalogue["patterns"]}
        print(f"OK : catalogue chargé ({len(catalogue_patterns)} patterns)")

    errors: list[str] = []
    releases = 0
    for ext_id, entry in sorted(index.get("extensions", {}).items()):
        versions = {r["version"] for r in entry["versions"]}
        if entry["latest"] not in versions:
            errors.append(f"{ext_id} : latest ({entry['latest']}) absent des versions")
        for release in entry["versions"]:
            releases += 1
            errors.extend(check_release(registry, ext_id, release, catalogue_patterns))

    if errors:
        for error in errors:
            print(f"ERREUR : {error}", file=sys.stderr)
        return 1
    print(f"OK : {releases} release(s) de {len(index.get('extensions', {}))} extension(s) conformes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
