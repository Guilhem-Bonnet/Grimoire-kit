"""Portable embedding-model bundles for air-gapped installs.

A bundle is a ``.tar.gz`` built on a connected machine and carried to a site
with no egress.  It holds the model files plus a manifest that pins every file
by SHA-256, so the receiving side can prove it got exactly what was shipped
before anything loads the weights.

Layout::

    grimoire-embedding-bundle/
      manifest.json
      model/...            # whatever the embedding engine needs to load

The bundle is engine-agnostic: it ships a *directory*, and both
``sentence-transformers`` and ``fastembed`` can load a model from a local path.
Nothing here re-hosts third-party weights — the operator downloads them from
their own source, on their own machine.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import socket
import tarfile
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from grimoire.__version__ import __version__

#: Manifest schema identifier. Bump on any breaking layout change.
BUNDLE_SCHEMA = "grimoire.embedding-bundle/1"

_ROOT_DIR = "grimoire-embedding-bundle"
_MODEL_DIR = "model"
_MANIFEST_NAME = "manifest.json"
_CHUNK = 1 << 20

#: Directories never carried into a bundle (download metadata, VCS noise).
_SKIP_DIRS = frozenset({".git", ".cache", "__pycache__"})


class BundleError(RuntimeError):
    """A bundle could not be built, installed or verified."""


class OfflineViolationError(BundleError):
    """Code attempted a network connection inside a ``no_network()`` block."""


# ── manifest ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BundleFile:
    """One file carried by a bundle, pinned by digest."""

    path: str
    sha256: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundleFile:
        return cls(path=str(data["path"]), sha256=str(data["sha256"]), size=int(data["size"]))


@dataclass(frozen=True, slots=True)
class BundleManifest:
    """What a bundle contains and where it came from."""

    model: str
    dim: int | None
    files: tuple[BundleFile, ...]
    created_at: str = ""
    grimoire_version: str = __version__
    schema: str = BUNDLE_SCHEMA

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "model": self.model,
            "dim": self.dim,
            "created_at": self.created_at,
            "grimoire_version": self.grimoire_version,
            "files": [f.to_dict() for f in self.files],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundleManifest:
        schema = str(data.get("schema", ""))
        if schema != BUNDLE_SCHEMA:
            msg = f"Unsupported bundle schema '{schema}' (expected '{BUNDLE_SCHEMA}')"
            raise BundleError(msg)
        raw_dim = data.get("dim")
        return cls(
            model=str(data.get("model", "")),
            dim=int(raw_dim) if raw_dim is not None else None,
            files=tuple(BundleFile.from_dict(f) for f in data.get("files", [])),
            created_at=str(data.get("created_at", "")),
            grimoire_version=str(data.get("grimoire_version", "")),
            schema=schema,
        )


@dataclass(frozen=True, slots=True)
class InstalledBundle:
    """Result of installing a bundle on the receiving side."""

    model_dir: Path
    manifest: BundleManifest


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """Outcome of verifying an installed bundle."""

    model_dir: Path
    model: str
    files_checked: int
    mismatched: tuple[str, ...]
    missing: tuple[str, ...]
    embedded: bool
    embed_engine: str
    embed_dim: int | None
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.mismatched and not self.missing and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "model_dir": str(self.model_dir),
            "model": self.model,
            "files_checked": self.files_checked,
            "mismatched": list(self.mismatched),
            "missing": list(self.missing),
            "embedded": self.embedded,
            "embed_engine": self.embed_engine,
            "embed_dim": self.embed_dim,
            "errors": list(self.errors),
        }


# ── helpers ───────────────────────────────────────────────────────────────────


def default_install_root() -> Path:
    """Where installed bundles live by default (honours ``XDG_CACHE_HOME``)."""
    override = os.environ.get("GRIMOIRE_EMBEDDING_CACHE", "").strip()
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "grimoire" / "embeddings"


def slugify_model(model: str) -> str:
    """Turn a model id into a single safe directory name."""
    safe = "".join(c if (c.isalnum() or c in "-._") else "_" for c in model.strip())
    return safe.strip("_") or "model"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_model_files(root: Path) -> list[Path]:
    """Sorted regular files under *root*, skipping metadata dirs and symlinks."""
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        found.append(path)
    return found


def detect_dim(model_dir: Path) -> int | None:
    """Read the embedding dimension from the model's own config, if declared."""
    pooling = model_dir / "1_Pooling" / "config.json"
    if pooling.is_file():
        with contextlib.suppress(OSError, ValueError, KeyError):
            value = json.loads(pooling.read_text())["word_embedding_dimension"]
            return int(value)
    config = model_dir / "config.json"
    if config.is_file():
        with contextlib.suppress(OSError, ValueError, KeyError):
            value = json.loads(config.read_text())["hidden_size"]
            return int(value)
    return None


@contextlib.contextmanager
def no_network() -> Iterator[None]:
    """Block outbound sockets for the duration of the block.

    This is what makes an offline claim testable rather than assumed: an engine
    that silently falls back to a remote download raises instead of succeeding.
    """

    def _blocked(*_args: Any, **_kwargs: Any) -> NoReturn:
        msg = "network access attempted while offline verification was active"
        raise OfflineViolationError(msg)

    original_connect = socket.socket.connect
    original_create = socket.create_connection
    socket.socket.connect = _blocked  # type: ignore[method-assign]
    socket.create_connection = _blocked
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.create_connection = original_create


# ── export ────────────────────────────────────────────────────────────────────


def _snapshot_from_hub(model: str, dest: Path) -> None:
    """Download *model* from the Hugging Face hub into *dest*."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        msg = (
            f"'{model}' is not a local directory and huggingface-hub is not installed.\n"
            "  → install it on the connected machine: pip install huggingface-hub\n"
            "  → or pass an already-downloaded model directory to --model"
        )
        raise BundleError(msg) from None
    snapshot_download(repo_id=model, local_dir=str(dest))


def resolve_model_dir(model: str, dest: Path) -> None:
    """Materialise *model* into *dest*, from a local directory or the hub."""
    source = Path(model).expanduser()
    if source.is_dir():
        shutil.copytree(source, dest, dirs_exist_ok=True, symlinks=False)
        return
    _snapshot_from_hub(model, dest)


def export_bundle(model: str, out_path: Path, *, model_name: str = "") -> BundleManifest:
    """Build a portable bundle for *model* at *out_path*.

    Runs on a connected machine. *model* is either a local model directory or a
    hub repo id; *model_name* overrides the name recorded in the manifest, which
    matters when exporting from a directory whose basename is not the model id.
    """
    out_path = out_path.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="grimoire-bundle-") as tmp:
        staging = Path(tmp) / _ROOT_DIR
        model_dir = staging / _MODEL_DIR
        model_dir.mkdir(parents=True)

        resolve_model_dir(model, model_dir)
        files = _iter_model_files(model_dir)
        if not files:
            msg = f"No model files resolved for '{model}' — nothing to bundle"
            raise BundleError(msg)

        entries = tuple(
            BundleFile(
                path=f"{_MODEL_DIR}/{path.relative_to(model_dir).as_posix()}",
                sha256=_sha256(path),
                size=path.stat().st_size,
            )
            for path in files
        )
        manifest = BundleManifest(
            model=model_name or model,
            dim=detect_dim(model_dir),
            files=entries,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        (staging / _MANIFEST_NAME).write_text(json.dumps(manifest.to_dict(), indent=2) + "\n")

        with tarfile.open(out_path, "w:gz") as tar:
            tar.add(staging, arcname=_ROOT_DIR)

    return manifest


# ── install ───────────────────────────────────────────────────────────────────


def _read_manifest(root: Path) -> BundleManifest:
    manifest_path = root / _MANIFEST_NAME
    if not manifest_path.is_file():
        msg = f"Bundle is missing {_MANIFEST_NAME}"
        raise BundleError(msg)
    try:
        data = json.loads(manifest_path.read_text())
    except ValueError as exc:
        msg = f"Bundle manifest is not valid JSON: {exc}"
        raise BundleError(msg) from None
    return BundleManifest.from_dict(data)


def _check_files(root: Path, manifest: BundleManifest) -> tuple[list[str], list[str]]:
    """Return (mismatched, missing) declared paths under *root*."""
    mismatched: list[str] = []
    missing: list[str] = []
    for entry in manifest.files:
        if entry.path.startswith("/") or ".." in Path(entry.path).parts:
            msg = f"Manifest declares an unsafe path: {entry.path}"
            raise BundleError(msg)
        target = root / entry.path
        if not target.is_file():
            missing.append(entry.path)
            continue
        if _sha256(target) != entry.sha256:
            mismatched.append(entry.path)
    return mismatched, missing


def install_bundle(archive: Path, *, dest_root: Path | None = None, force: bool = False) -> InstalledBundle:
    """Extract, verify and install *archive*; returns the installed model dir.

    Fail-closed: a digest mismatch or a missing declared file aborts the install
    and leaves nothing behind.
    """
    archive = archive.expanduser().resolve()
    if not archive.is_file():
        msg = f"Bundle not found: {archive}"
        raise BundleError(msg)

    root_dir = (dest_root or default_install_root()).expanduser()

    with tempfile.TemporaryDirectory(prefix="grimoire-install-") as tmp:
        staging = Path(tmp)
        with tarfile.open(archive, "r:gz") as tar:
            # filter="data" (3.12+) rejects absolute paths, traversal and
            # special files. The manifest check below is the second gate.
            tar.extractall(staging, filter="data")

        extracted = staging / _ROOT_DIR
        if not extracted.is_dir():
            msg = f"Bundle does not contain a '{_ROOT_DIR}/' root"
            raise BundleError(msg)

        manifest = _read_manifest(extracted)
        mismatched, missing = _check_files(extracted, manifest)
        if mismatched or missing:
            details = []
            if mismatched:
                details.append(f"{len(mismatched)} file(s) with a wrong digest: {', '.join(mismatched[:3])}")
            if missing:
                details.append(f"{len(missing)} declared file(s) absent: {', '.join(missing[:3])}")
            msg = "Bundle verification failed — " + "; ".join(details)
            raise BundleError(msg)

        target = root_dir / slugify_model(manifest.model)
        if target.exists():
            if not force:
                msg = f"{target} already exists — pass --force to replace it"
                raise BundleError(msg)
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extracted), str(target))

    return InstalledBundle(model_dir=target / _MODEL_DIR, manifest=_read_manifest(target))


# ── verify ────────────────────────────────────────────────────────────────────


def _embed_offline(model_dir: Path, model_name: str = "") -> tuple[str, int]:
    """Load *model_dir* with no network and embed one probe string.

    Delegates to :func:`grimoire.memory.embedding.build_embedder`, so whichever
    engine the site actually has — fastembed or sentence-transformers — is the
    one being proven. *model_name* carries the manifest's model id, which
    fastembed needs to resolve pooling and normalisation even when the weights
    come from a local directory.

    Returns ``(engine, dim)``. Raises :class:`BundleError` when no engine is
    installed, and :class:`OfflineViolationError` when one reaches the network.
    """
    from grimoire.memory.embedding import DEFAULT_MODEL, EmbeddingEngineError, build_embedder

    with no_network():
        try:
            embedder = build_embedder(
                model_name or DEFAULT_MODEL,
                model_path=str(model_dir),
                offline=True,
            )
        except EmbeddingEngineError as exc:
            raise BundleError(str(exc)) from None
        return embedder.engine, embedder.dim


def verify_bundle(model_dir: Path, *, embed: bool = True) -> VerifyReport:
    """Re-check an installed bundle's digests, then load it with no network."""
    model_dir = model_dir.expanduser().resolve()
    bundle_root = model_dir.parent if model_dir.name == _MODEL_DIR else model_dir
    manifest = _read_manifest(bundle_root)
    mismatched, missing = _check_files(bundle_root, manifest)

    errors: list[str] = []
    embedded = False
    engine = ""
    dim: int | None = None

    if embed and not mismatched and not missing:
        try:
            engine, dim = _embed_offline(bundle_root / _MODEL_DIR, manifest.model)
            embedded = True
        except OfflineViolationError as exc:
            errors.append(f"offline load failed — the engine tried to reach the network: {exc}")
        except BundleError as exc:
            errors.append(str(exc))
        except Exception as exc:  # engine failures are reported, not raised
            errors.append(f"offline load failed: {exc}")

    if dim is not None and manifest.dim is not None and dim != manifest.dim:
        errors.append(f"dimension mismatch — manifest says {manifest.dim}, the model produced {dim}")

    return VerifyReport(
        model_dir=bundle_root / _MODEL_DIR,
        model=manifest.model,
        files_checked=len(manifest.files),
        mismatched=tuple(mismatched),
        missing=tuple(missing),
        embedded=embedded,
        embed_engine=engine,
        embed_dim=dim,
        errors=tuple(errors),
    )


# ── project wiring ────────────────────────────────────────────────────────────


def configure_project(config_path: Path, model_dir: Path) -> None:
    """Point ``memory.embedding_model`` at *model_dir*, preserving comments.

    ``project-context.yaml`` is a heavily commented, hand-edited file, so this
    uses ruamel's round-trip mode rather than a load/dump that would strip every
    comment in it.
    """
    from ruamel.yaml import YAML

    if not config_path.is_file():
        msg = f"No project config at {config_path}"
        raise BundleError(msg)

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 120
    data = yaml.load(config_path)
    if data is None:
        msg = f"{config_path} is empty"
        raise BundleError(msg)
    if "memory" not in data or data["memory"] is None:
        data["memory"] = {}
    data["memory"]["embedding_model"] = str(model_dir)
    with config_path.open("w") as fh:
        yaml.dump(data, fh)
