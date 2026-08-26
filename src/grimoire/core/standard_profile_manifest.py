"""Read the generated ``_grimoire/standard/standard-profile.yaml`` manifest.

``setup_standard_profile`` persists the *complete* artifact set — the profile's own
artifacts plus the extras activated by the project's needs — into the manifest. Any
verification that recomputes the required set from the profile id alone therefore
misses the extras, and an artifact activated by a need can be deleted without
``verify`` ever noticing.

This module reads the recorded set back. It takes the manifest path directly rather
than importing ``STANDARD_PROFILE_FILE`` so that ``agentic_standard`` can depend on
it without a cycle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


def _load(manifest: Path) -> dict[str, Any] | None:
    if not manifest.is_file():
        return None
    yaml = YAML(typ="safe")
    data = yaml.load(manifest)
    if not isinstance(data, dict):
        msg = f"{manifest.name} must be a YAML mapping."
        raise ValueError(msg)
    return data


def read_profile(manifest: Path) -> str | None:
    """Return the profile id recorded in the manifest, or ``None`` if absent."""
    data = _load(manifest)
    if data is None:
        return None
    profile = data.get("profile")
    return str(profile) if profile else None


def read_artifact_paths(manifest: Path) -> list[Path]:
    """Return every artifact path the manifest records as generated.

    Includes the ``extra_artifacts`` activated by needs, which cannot be
    reconstructed from the profile id alone.  Returns an empty list when the
    manifest is absent or records no artifacts — callers keep their own baseline.
    """
    data = _load(manifest)
    if data is None:
        return []
    entries = data.get("artifacts")
    if not isinstance(entries, list):
        return []
    paths: list[Path] = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("path"):
            paths.append(Path(str(entry["path"])))
    return paths
