"""Mechanical steps and V1 proposal builders behind ``grimoire upgrade-flow`` (issue #490).

``grimoire up`` regenerates the kit tier and syncs host surfaces — one step
of a real project migration. A real migration (Terraform-HouseServer,
3.38.0 → 3.44.2, 2026-09-11) also needed: a tarball and a memory manifest
taken *before* anything was touched; a decision about agent/wrapper files a
retired kit version left behind (never delete — archive); a decision about
overrides that a newer kit's shape would otherwise mask entirely; memory
fiches nobody's ``context:`` points at yet; execution needs and hosts the
project never declared; and a final check that nothing beyond what was
expected moved.

Every function here backs one node of ``registry/blueprints/project-upgrade
.blueprint.json``. The V0 ones (``backup_project``, ``preview_upgrade``,
``apply_upgrade``, ``find_orphans``/``archive_orphans``, ``verify_upgrade``)
do real work and their result is mechanically checkable. The V1 ones
(``propose_override_migrations``, ``propose_memory_links``,
``propose_needs_hosts``) only ever call
:func:`grimoire.proposals.create_manual_proposal` — never write an override,
a `context:` entry or a `needs`/`hosts` declaration themselves. That door is
:func:`grimoire.proposals.accept_proposal`, and only a human opens it.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireRuntimeError

__all__ = [
    "ApplyResult",
    "BackupResult",
    "OrphanAgent",
    "OrphanReport",
    "PreviewResult",
    "ProbeResult",
    "VerifyResult",
    "apply_upgrade",
    "archive_orphans",
    "backup_project",
    "bundled_blueprint_path",
    "find_orphans",
    "preview_upgrade",
    "probe_hook",
    "propose_memory_links",
    "propose_needs_hosts",
    "propose_override_migrations",
    "propose_repairs",
    "verify_upgrade",
]

_BLUEPRINT_RELPATH = Path("registry") / "blueprints" / "project-upgrade.blueprint.json"


def bundled_blueprint_path() -> Path:
    """Filesystem path to the packaged ``project-upgrade.blueprint.json`` (issue #490).

    Same dual dev/wheel resolution as :func:`grimoire.archetypes.bundled_path`:
    a wheel install force-includes the blueprint under ``grimoire/data/
    registry/blueprints/``; an editable/dev install reads the repository's
    own ``registry/blueprints/`` — never a packaged copy that could drift
    from the one ``grimoire blueprint validate`` and the tests both exercise.
    """
    pkg = Path(str(files("grimoire"))) / "data" / _BLUEPRINT_RELPATH
    if pkg.is_file():
        return pkg
    repo_root = Path(__file__).resolve().parents[3]
    dev = repo_root / _BLUEPRINT_RELPATH
    if dev.is_file():
        return dev
    raise GrimoireRuntimeError(
        f"blueprint project-upgrade introuvable (ni grimoire/data/{_BLUEPRINT_RELPATH.as_posix()}, "
        f"ni {_BLUEPRINT_RELPATH.as_posix()} du dépôt) — installation grimoire-kit incomplète"
    )

#: Project-relative paths a backup captures — the exact list the 2026-09-11
#: migration tarball used, minus nothing: every one of them either carries
#: project identity (``project-context.yaml``, ``.mcp.json``) or an
#: agent/host surface ``up``/``host sync`` are about to rewrite.
BACKUP_PATHS: tuple[str, ...] = (
    "_grimoire",
    ".claude",
    ".github/agents",
    ".github/prompts",
    ".github/instructions",
    "GEMINI.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".mcp.json",
    "project-context.yaml",
)

#: Files a real ``up`` is expected to touch inside ``_grimoire/_memory/`` —
#: any other manifest diff is an anomaly, not an expected side effect.
#: ``config.yaml``: the version header ``up``'s identity step rewrites.
EXPECTED_MEMORY_DIFFS: frozenset[str] = frozenset({"_grimoire/_memory/config.yaml"})

_UPGRADE_OUTPUT_DIR = Path("_grimoire-output") / "upgrade"


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _kit_version() -> str:
    try:
        from grimoire import __version__

        return str(__version__)
    except ImportError:  # pragma: no cover - defensive only
        return "unknown"


def archive_root(target: Path, *, version: str | None = None, date: str | None = None) -> Path:
    """``_archive/<date>-pre-<version>/`` under *target* — never written to by anything but this module and the backup/orphans nodes."""
    return target / "_archive" / f"{date or _today()}-pre-{version or _kit_version()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def run_output_dir(target: Path) -> Path:
    out = target / _UPGRADE_OUTPUT_DIR / _today()
    out.mkdir(parents=True, exist_ok=True)
    return out


# ── backup ────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class BackupResult:
    tarball: Path
    manifest: Path
    tarball_entries: int
    memory_files: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "tarball": str(self.tarball),
            "manifest": str(self.manifest),
            "tarball_entries": self.tarball_entries,
            "memory_files": self.memory_files,
        }


def _iter_memory_files(target: Path) -> list[Path]:
    mem = target / "_grimoire" / "_memory"
    if not mem.is_dir():
        return []
    return sorted(p for p in mem.rglob("*") if p.is_file())


def write_memory_manifest(target: Path, dest: Path) -> int:
    """Write a ``sha256sum -c``-compatible manifest of ``_grimoire/_memory/`` to *dest*."""
    memory_files = _iter_memory_files(target)
    lines = [f"{_sha256(f)}  {f.relative_to(target).as_posix()}" for f in memory_files]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(memory_files)


def _backup_digest(target: Path) -> str:
    """Content-only SHA-256 over every real file under :data:`BACKUP_PATHS`.

    Immune to the gzip stream's own embedded timestamp, which makes two
    ``tarfile.open(..., "w:gz")`` writes of byte-identical *input* produce
    different tarball *bytes* — comparing the tarballs themselves could never
    tell "nothing changed" from "something changed" the way this can.
    """
    entries: dict[str, str] = {}
    for rel in BACKUP_PATHS:
        base = target / rel
        if not base.exists():
            continue
        files = [base] if base.is_file() else (p for p in base.rglob("*") if p.is_file())
        for f in files:
            arcname = f"{rel}/{f.relative_to(base).as_posix()}" if base.is_dir() else rel
            entries[arcname] = _sha256(f)

    # Sorted by name — matching `_tarball_digest`'s own `sorted(..., key=name)`
    # over the tarball's members, not `BACKUP_PATHS`' declaration order,
    # is what lets the two ever agree on the same input.
    digest = hashlib.sha256()
    for arcname in sorted(entries):
        digest.update(arcname.encode("utf-8"))
        digest.update(entries[arcname].encode("ascii"))
    return digest.hexdigest()


def _tarball_digest(tarball: Path) -> str | None:
    """Recompute :func:`_backup_digest`'s digest from an existing tarball's own members.

    ``None`` on any read failure (missing, truncated, unreadable) — treated
    as "does not match", which routes the caller to write a fresh archive
    rather than trust a corrupt one.
    """
    try:
        with tarfile.open(tarball, "r:gz") as tar:
            digest = hashlib.sha256()
            for member in sorted((m for m in tar.getmembers() if m.isfile()), key=lambda m: m.name):
                extracted = tar.extractfile(member)
                if extracted is None:
                    return None
                digest.update(member.name.encode("utf-8"))
                digest.update(hashlib.sha256(extracted.read()).hexdigest().encode("ascii"))
            return digest.hexdigest()
    except (OSError, tarfile.TarError):
        return None


def backup_project(target: Path) -> BackupResult:
    """Tarball + memory manifest under ``_archive/<date>-pre-<version>/`` — the node's whole job.

    A second ``backup`` run on the same day, at the same kit version, whose
    tracked files (:data:`BACKUP_PATHS`) have not changed since an earlier
    one is a no-op read of that earlier snapshot — the common case (the
    blueprint's own acceptance re-checking a node's output). One where that
    content genuinely changed since every existing snapshot of the day — a
    dry run, then a real run some time later with something touched in
    between — gets its own ``grimoire-state-2.tar.gz``/
    ``memory-manifest-sha256-2.txt`` (``-3``, …) instead: no existing
    snapshot, canonical or suffixed, is ever overwritten. A real migration
    found the previous, unconditional "tarball exists → skip" guard silently
    reusing a stale first snapshot here (issue #490 follow-up) —
    ``verify_upgrade``'s fixed-path lookup only stays correct if the
    canonical (unsuffixed) manifest keeps meaning "the true first
    pre-upgrade snapshot of the day", which reusing *any* existing tarball
    regardless of content did not guarantee.
    """
    root = archive_root(target)
    root.mkdir(parents=True, exist_ok=True)
    digest = _backup_digest(target)

    suffix, n = "", 2
    while True:
        candidate = root / f"grimoire-state{suffix}.tar.gz"
        if not candidate.is_file():
            break
        if _tarball_digest(candidate) == digest:
            manifest = root / f"memory-manifest-sha256{suffix}.txt"
            with tarfile.open(candidate, "r:gz") as tar:
                entries = len(tar.getnames())
            memory_files = (
                len([ln for ln in manifest.read_text(encoding="utf-8").splitlines() if ln.strip()])
                if manifest.is_file()
                else write_memory_manifest(target, manifest)
            )
            return BackupResult(tarball=candidate, manifest=manifest, tarball_entries=entries, memory_files=memory_files)
        suffix = f"-{n}"
        n += 1

    tarball = root / f"grimoire-state{suffix}.tar.gz"
    manifest = root / f"memory-manifest-sha256{suffix}.txt"

    with tarfile.open(tarball, "w:gz") as tar:
        for rel in BACKUP_PATHS:
            path = target / rel
            if not path.exists():
                continue
            tar.add(path, arcname=rel)
    with tarfile.open(tarball, "r:gz") as check:
        entries = len(check.getnames())
    memory_files = write_memory_manifest(target, manifest)
    return BackupResult(tarball=tarball, manifest=manifest, tarball_entries=entries, memory_files=memory_files)


# ── preview ───────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class PreviewResult:
    report_path: Path
    up_ok: bool
    host_sync_ok: bool
    doctor_baseline: tuple[str, ...] = ()
    """Dead ``_grimoire/...`` references already present *before* `apply`
    touches anything (issue #502, third defect) — see
    :func:`_dead_reference_strings`. Persisted alongside the preview report
    so `apply`, a separate process invocation, can read it back and only
    fail on a reference `up` itself introduced."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_path": str(self.report_path),
            "up_ok": self.up_ok,
            "host_sync_ok": self.host_sync_ok,
            "doctor_baseline": list(self.doctor_baseline),
        }


def _run_grimoire(args: list[str], *, cwd: Path, timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "grimoire", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def _doctor_baseline_path(target: Path) -> Path:
    """Where :func:`preview_upgrade` persists its dead-reference baseline."""
    return run_output_dir(target) / "doctor-baseline.json"


def _dead_reference_strings(target: Path) -> tuple[str, ...]:
    """Every dead ``_grimoire/...`` reference in *target*, as ``DeadReference.__str__``.

    That string form (``"<source>:<line> → <target>"``) is also the exact
    line the ``paths_resolve`` doctor check shows, and the identity a
    reference is compared by between the ``preview`` baseline and `apply`'s
    fresh read — a reference is the same one iff both the citing line and the
    dead target match; a project that moves the same broken mention to a
    different line counts as a new one, never silently carried over.
    """
    from grimoire.core.integrity import dead_path_references

    return tuple(str(ref) for ref in dead_path_references(target))


def _read_doctor_baseline(target: Path) -> tuple[str, ...]:
    """Read back :func:`preview_upgrade`'s baseline, or ``()`` if there is none.

    No baseline (``apply`` run without a prior ``preview``, or an older run
    output already cleaned up) means no softening: every dead reference
    `apply` finds is treated as new, the strict pre-#502 behaviour — this
    node never invents a baseline it did not itself see written.
    """
    path = _doctor_baseline_path(target)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    refs = data.get("dead_references") if isinstance(data, dict) else None
    if not isinstance(refs, list):
        return ()
    return tuple(str(r) for r in refs)


def preview_upgrade(target: Path) -> PreviewResult:
    """``up --dry-run`` + ``host sync --dry-run``, diffed into ``_grimoire-output/upgrade/<date>/preview.md``.

    Also captures the dead-reference baseline (issue #502, third defect):
    three real projects had `apply` refuse over ``_grimoire/...`` references
    that were already stale *before* the upgrade — leftovers in
    ``_memory/decisions-log.md``, ``.github/copilot-instructions.md``,
    ``.claude/skills/*/SKILL.md`` — leaving the project "mis à niveau mais
    flow en échec" with no path forward. The baseline lets `apply` tell that
    apart from a reference the upgrade itself just broke.
    """
    up_proc = _run_grimoire(["up", str(target), "--dry-run"], cwd=target)
    host_proc = _run_grimoire(["host", "sync", "--project-root", str(target), "--dry-run"], cwd=target)
    baseline = _dead_reference_strings(target)

    report = run_output_dir(target) / "preview.md"
    report.write_text(
        "# Aperçu de mise à niveau\n\n"
        f"Généré le {datetime.now(UTC).isoformat()} par `grimoire upgrade-flow`.\n\n"
        "## `grimoire up --dry-run`\n\n"
        f"```\n{up_proc.stdout}{up_proc.stderr}\n```\n\n"
        "## `grimoire host sync --dry-run`\n\n"
        f"```\n{host_proc.stdout}{host_proc.stderr}\n```\n",
        encoding="utf-8",
    )
    _doctor_baseline_path(target).write_text(
        json.dumps({"dead_references": list(baseline)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return PreviewResult(
        report_path=report,
        up_ok=up_proc.returncode == 0,
        host_sync_ok=host_proc.returncode == 0,
        doctor_baseline=baseline,
    )


# ── probe-hook ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class ProbeResult:
    ok: bool
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "detail": self.detail, "payload": self.payload}


#: The exact marker :func:`grimoire.hosts.decisions._failed_decision` writes
#: when a decision function crashes (``hosts/decisions/__init__.py``) — the
#: *only* string this probe treats as a genuine hook failure. Anchored at the
#: start of a line (``re.MULTILINE``) because ``additionalContext`` is a
#: newline-joined stack of independent blocks (persona, task recall,
#: standing directive, providers/proposals status —
#: ``decisions/activation.py::decide_activation``): a crashed decision
#: replaces the whole context with just this one block, it is never appended
#: mid-sentence to another block's prose.
_HOOK_FAILURE_MARKER = re.compile(r"(?m)^\[Grimoire\] hook \S+ en erreur\b")


def _hook_rendered_text_blocks(payload: dict[str, Any]) -> list[str]:
    """Every string field :func:`grimoire.hosts.runtime.render` may have set.

    Never the whole payload serialised to text — a project's memory recall
    can legitimately contain the word "erreur" (issue #502, second rejeu
    réel: TTS-Voice, a task recall naming a past error, hook perfectly
    healthy) and a free-text search over the full JSON dump treated that as
    a hook failure. Only these fields ever carry rendered prose for a human
    or an agent to read; a JSON key or value nested anywhere else is never
    scanned.
    """
    blocks: list[str] = []
    specific = payload.get("hookSpecificOutput")
    if isinstance(specific, dict):
        for key in ("additionalContext", "permissionDecisionReason"):
            value = specific.get(key)
            if isinstance(value, str):
                blocks.append(value)
    for key in ("systemMessage", "reason"):
        value = payload.get(key)
        if isinstance(value, str):
            blocks.append(value)
    return blocks


def _hook_reports_failure(payload: dict[str, Any]) -> bool:
    """Structured-only verdict on whether *payload* names a real hook failure.

    Exactly two signals, both structural — never a free substring search
    over rendered prose:

    - a top-level ``error`` key (a hook that could not even render a normal
      decision payload sets this itself, see the crash paths of
      :mod:`grimoire.hosts.runtime`);
    - :data:`_HOOK_FAILURE_MARKER` at the start of a line in one of
      :func:`_hook_rendered_text_blocks` — the runtime's own, one and only
      way of saying a decision crashed.
    """
    if "error" in payload:
        return True
    return any(_HOOK_FAILURE_MARKER.search(block) for block in _hook_rendered_text_blocks(payload))


def probe_hook(target: Path, *, host: str = "claude", event: str = "SessionStart") -> ProbeResult:
    """Replay the lifecycle hook and flag anything that looks like the #423 regression.

    The hook (``grimoire.hosts.runtime.main``) always exits 0 by design — the
    verdict lives in the JSON payload, not the process exit code — so this
    probe reads the payload itself (:func:`_hook_reports_failure`) for a
    structured failure signal — never a free substring search over its
    rendered text, which flagged a project's own memory recall as a hook
    failure the moment it happened to contain the word "erreur" (issue #502).
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "grimoire.hosts.runtime", "--host", host, "--event", event, "--project-root", str(target)],
            input="{}",
            cwd=target,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ProbeResult(ok=False, detail=f"hook non exécutable : {exc}")

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return ProbeResult(ok=False, detail=f"sortie du hook illisible : {proc.stdout!r}")
    if not isinstance(payload, dict):
        return ProbeResult(ok=False, detail=f"sortie du hook inattendue : {payload!r}")

    has_error = _hook_reports_failure(payload)
    ok = proc.returncode == 0 and not has_error
    rendered = json.dumps(payload, ensure_ascii=False)
    detail = "hook rejoué sans erreur" if ok else f"hook en erreur : {rendered[:400]}"
    return ProbeResult(ok=ok, detail=detail, payload=payload)


# ── apply ─────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class ApplyResult:
    ok: bool
    up_ok: bool
    doctor_failures: tuple[str, ...]
    hook: ProbeResult
    preexisting_failures: tuple[str, ...] = ()
    """Dead references already in the ``preview`` baseline (issue #502) —
    never a reason `ok` is False on their own; see :func:`propose_repairs`."""
    repairs_proposed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "up_ok": self.up_ok,
            "doctor_failures": list(self.doctor_failures),
            "hook": self.hook.to_dict(),
            "preexisting_failures": list(self.preexisting_failures),
            "repairs_proposed": self.repairs_proposed,
        }


def apply_upgrade(target: Path, *, host: str = "claude") -> ApplyResult:
    """``up`` (kit refresh + host sync), then the two mechanical checks the flow's acceptance runs against.

    A dead ``_grimoire/...`` reference (the ``paths_resolve`` doctor check)
    only fails this node when it is a *regression* — absent from the
    ``preview`` baseline (:func:`_read_doctor_baseline`, issue #502, third
    defect). Three real projects had `apply` refuse over references that
    were stale before the upgrade ever touched them, leaving the project
    upgraded but the flow stuck with no path forward. A pre-existing dead
    reference instead becomes a ``"repair"`` proposal
    (:func:`propose_repairs`) — a real defect, just never this node's to
    fix silently, and never automatically. Every other doctor check keeps
    failing this node exactly as before; only ``paths_resolve`` is softened.
    """
    up_proc = _run_grimoire(["up", str(target)], cwd=target)
    if up_proc.returncode != 0:
        return ApplyResult(
            ok=False,
            up_ok=False,
            doctor_failures=(f"up a échoué : {up_proc.stderr.strip() or up_proc.stdout.strip()}",),
            hook=ProbeResult(ok=False, detail="non exécuté : up a échoué"),
        )

    doctor_proc = _run_grimoire(["-o", "json", "doctor", str(target)], cwd=target)
    try:
        payload = json.loads(doctor_proc.stdout or "{}")
        checks = payload.get("checks", [])
    except json.JSONDecodeError:
        checks = None

    failures: list[str] = []
    preexisting: tuple[str, ...] = ()
    if checks is None:
        failures.append("doctor : sortie JSON illisible")
    else:
        baseline = set(_read_doctor_baseline(target))
        current_refs = _dead_reference_strings(target)
        new_refs = [r for r in current_refs if r not in baseline]
        stale_refs = tuple(r for r in current_refs if r in baseline)
        for check in checks:
            if check.get("passed", True):
                continue
            name = check.get("name")
            if name == "paths_resolve":
                if new_refs:
                    shown = ", ".join(new_refs[:3])
                    more = f" (+{len(new_refs) - 3})" if len(new_refs) > 3 else ""
                    failures.append(f"{len(new_refs)} chemin(s) du kit cité(s) mais absent(s), nouveau(x) : {shown}{more}")
                continue
            failures.append(str(check.get("detail") or name))
        preexisting = stale_refs

    repairs_proposed = 0
    if preexisting:
        repairs_proposed = len(propose_repairs(target, preexisting))

    hook = probe_hook(target, host=host)
    return ApplyResult(
        ok=not failures and hook.ok,
        up_ok=True,
        doctor_failures=tuple(failures),
        hook=hook,
        preexisting_failures=preexisting,
        repairs_proposed=repairs_proposed,
    )


# ── orphans ───────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class OrphanAgent:
    name: str
    paths: tuple[Path, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "paths": [str(p) for p in self.paths]}


@dataclass(slots=True)
class OrphanReport:
    orphans: list[OrphanAgent] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [o.name for o in self.orphans]

    def to_dict(self) -> dict[str, Any]:
        return {"orphans": [o.to_dict() for o in self.orphans]}


#: Per-host agent directories a managed projection can live under, beyond
#: the kit/overrides tiers ``layout.installed_agents`` already covers.
_HOST_AGENT_DIRS: tuple[str, ...] = (".claude/agents", ".cursor/agents", ".codex/agents", ".gemini/agents")


def _carries_managed_marker(path: Path) -> bool:
    from grimoire.hosts.emitters.base import MANAGED_MARKER

    try:
        head = path.read_text(encoding="utf-8")[:4096]
    except OSError:
        return False
    return MANAGED_MARKER in head


def find_orphans(target: Path) -> OrphanReport:
    """Agent names installed on disk that the current kit version no longer delivers (issue #490).

    ``refresh_kit_tier``/``grimoire up`` never prunes ``_grimoire/kit/
    agents/`` — it only (re)writes what the configured archetype(s) still
    produce. An orphan is a name :func:`~grimoire.core.layout.
    installed_agents` finds on disk that ALL of the following are true for:

    - its installed file lives in the kit tier itself
      (:func:`~grimoire.core.layout.kit_dir`) — this is the one and only
      pile this node ever archives from. Anything living in a tier the
      *project* owns — the overrides tier
      (:func:`~grimoire.core.layout.overrides_dir`), the legacy custom tier
      (``_grimoire/_config/custom/agents/``, still read by
      :func:`~grimoire.core.layout.agent_dirs` for an unmigrated project),
      or any other legacy agent directory — is never a candidate, full stop,
      *regardless* of whether the project also declares it under ``agents.
      custom_agents``. A second rejeu réel (three projects, 2026-09-14)
      found live agents in the legacy custom tier that were *not* declared
      there either, archived anyway because the old check only asked
      "declared, or override tier?" instead of "kit tier at all?";
    - :func:`~grimoire.cli.cmd_up.fresh_kit_agent_roster` — the same
      resolver ``up`` uses — would not (re)write it today for this
      project's configuration (base + archetype(s) + features);
    - the project does not declare it explicitly under ``agents.
      custom_agents`` in ``project-context.yaml`` — for a kit-tier file,
      this is the project's own way of saying "keep this one anyway", the
      exact shape a real migration (Terraform-HouseServer, 2026-09-11)
      described for ``fix-loop-orchestrator``.

    That same 2026-09-11 migration also found this node archiving
    ``vectus``, a ``vector-memory`` feature agent the roster resolver did
    not yet infer (see :func:`~grimoire.cli.cmd_up._infer_resolved`) —
    leaving the project broken until repaired by hand. In doubt, this
    function keeps the agent installed rather than archiving it; it never
    guesses.

    Managed per-host projections (``.claude/agents/<name>.md``, ``.github/
    agents/<name>.agent.md``, …) for a genuine orphan are still reported
    alongside its kit/override file, so :func:`archive_orphans` moves the
    whole set together.
    """
    from grimoire.cli.cmd_up import _load_config_quiet, fresh_kit_agent_roster
    from grimoire.core import layout

    roster = fresh_kit_agent_roster(target)
    installed = layout.installed_agents(target)
    cfg = _load_config_quiet(target)
    declared = set(cfg.agents.custom_agents) if cfg is not None else set()

    report = OrphanReport()
    for name in sorted(installed):
        if name in roster or name in declared:
            continue
        _, primary_path = installed[name]
        if not primary_path.is_relative_to(layout.kit_dir(target)):
            # Lives in a tier the project owns — overrides, the legacy
            # custom tier, or any other legacy agent directory. Never ours
            # to archive, whether or not it is declared under
            # `agents.custom_agents`: only the kit tier ever produces
            # orphans, by construction.
            continue
        paths: list[Path] = [primary_path]
        for tree in _HOST_AGENT_DIRS:
            candidate = target / tree / f"{name}.md"
            if candidate.is_file() and candidate != primary_path and _carries_managed_marker(candidate):
                paths.append(candidate)
        github_candidate = target / ".github" / "agents" / f"{name}.agent.md"
        if github_candidate.is_file() and _carries_managed_marker(github_candidate):
            paths.append(github_candidate)
        report.orphans.append(OrphanAgent(name=name, paths=tuple(paths)))
    return report


def archive_orphans(target: Path, report: OrphanReport) -> list[str]:
    """Move every orphan's files under ``_archive/<date>-pre-<version>/orphans/`` — never delete.

    Idempotent: a path already moved (a second run over the same report, or
    a report recomputed after a partial failure) is skipped rather than
    raising on an already-missing source.
    """
    dest_root = archive_root(target) / "orphans"
    moved: list[str] = []
    for orphan in report.orphans:
        for path in orphan.paths:
            if not path.exists():
                continue
            rel = path.relative_to(target)
            dest = dest_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))
            moved.append(rel.as_posix())
    if moved:
        readme = dest_root / "README.md"
        names = ", ".join(sorted({o.name for o in report.orphans}))
        readme.write_text(
            f"# Agents archivés — mise à niveau du {_today()}\n\n"
            f"Le kit installé ({_kit_version()}) ne livre plus : {names}.\n"
            "Déplacés ici par `grimoire upgrade-flow`, jamais supprimés — "
            "restaurez-les à la main si l'un d'eux était encore voulu.\n",
            encoding="utf-8",
        )
    return moved


# ── repair (V1 proposals) ────────────────────────────────────────────────────


def _suggest_repair_substitution(target: Path, dead_target: str) -> str:
    """An evident v3 replacement for a stale ``_grimoire/...`` reference, or ``""``.

    Never a guess: strips a known legacy root
    (:data:`~grimoire.core.layout.LEGACY_KIT_ROOTS` — the very prefixes
    ``grimoire migrate`` already treats as pre-boundary, see
    ``cli/cmd_migrate.py``) from *dead_target* and checks whether the same
    relative path exists in the kit tier today. A reference this cannot
    resolve this way (e.g. an even older, unrecognised root) gets no
    suggested substitution — :func:`propose_repairs` still proposes the
    repair, just without a carrier reason claiming a fix that is not there.
    """
    from grimoire.core import layout

    for legacy_root in layout.LEGACY_KIT_ROOTS:
        prefix = f"{legacy_root}/"
        if not dead_target.startswith(prefix):
            continue
        relative = dead_target[len(prefix):]
        if (layout.kit_dir(target) / relative).exists():
            return f"{layout.KIT_DIR}/{relative}"
    return ""


def propose_repairs(target: Path, stale_refs: Iterable[str]) -> list[Any]:
    """One ``"repair"`` proposal per pre-existing dead path reference (issue #502).

    *stale_refs* are :func:`_dead_reference_strings`-shaped
    (``"<source>:<line> → <target>"``) references :func:`apply_upgrade`
    found in the ``preview`` baseline too — real defects, present before the
    upgrade touched anything, that this flow never fixes on its own
    initiative. Names an evident v3 substitution
    (:func:`_suggest_repair_substitution`) when one exists; otherwise says so
    plainly and leaves the fix to a human. :func:`grimoire.proposals.
    accept_proposal` is the only door that ever edits the citing file, and
    only when a substitution was actually named.
    """
    from grimoire.proposals import create_manual_proposal

    proposals = []
    for ref in stale_refs:
        source, _, rest = ref.partition(":")
        line, _, dead_target = rest.partition(" → ")
        substitution = _suggest_repair_substitution(target, dead_target)
        carrier_reason = (
            f"substitution évidente : {dead_target} -> {substitution}"
            if substitution
            else f"pas de remplacement évident pour {dead_target} — revue humaine"
        )
        slug_hint = re.sub(r"[^a-z0-9]+", "-", f"{source}-{line}-{dead_target}".lower()).strip("-")
        proposals.append(
            create_manual_proposal(
                target,
                slug=f"repair-{slug_hint}",
                specialty=f"référence périmée : {source}:{line} → {dead_target}",
                artifact_type="repair",
                carrier_reason=carrier_reason,
                artifact_ref=ref,
                category="dead-reference",
            )
        )
    return proposals


# ── overrides (V1 proposals) ─────────────────────────────────────────────────


def propose_override_migrations(target: Path) -> list[Any]:
    """One ``"override-migration"`` proposal per override in drift (issue #490).

    ``project_override_drift`` already tells "fresh" (nothing to do) from
    everything else. For everything else, a dry-run ``convert_override``
    decides the wording: it refuses (naming the diverging lines) exactly
    when the override's body has genuinely drifted from the kit's — that
    refusal *is* "revue nécessaire, diff joint" here, never re-derived.
    """
    from grimoire.core.override_drift import OverrideConversionRefusedError, convert_override, project_override_drift
    from grimoire.proposals import create_manual_proposal

    proposals = []
    for drift in project_override_drift(target):
        if drift.status == "fresh":
            continue
        try:
            convert_override(target, drift.name, dry_run=True)
            carrier_reason = (
                "conversion sûre : le corps de l'override est identique à celui du kit — "
                f"`grimoire agent override convert {drift.name}` suffit"
            )
        except OverrideConversionRefusedError as exc:
            carrier_reason = f"revue nécessaire : {exc}"
        except FileNotFoundError:
            continue
        proposals.append(
            create_manual_proposal(
                target,
                slug=f"override-migration-{drift.name}",
                specialty=f"override en dérive : {drift.name}",
                artifact_type="override-migration",
                target_agent=drift.name,
                carrier_reason=carrier_reason,
                category="override-drift",
            )
        )
    return proposals


# ── memory (V1 proposals) ─────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-z0-9]+")


def _fiche_words(rel: str) -> set[str]:
    stem = Path(rel).stem
    return {w for w in _WORD_RE.split(stem.lower().replace("-", " ").replace("_", " ")) if w}


def _carrier_for_words(project_root: Path, agents: list[Any], words: set[str], *, exclude: str = "") -> str:
    """Exactly one declared agent whose ``use_when`` names one of *words* as a whole word, else ``""``."""
    from grimoire.hosts.collect import effective_agent_frontmatter

    if not words:
        return ""
    candidates: list[str] = []
    for agent in agents:
        if exclude and agent.name == exclude:
            continue
        use_when = str(effective_agent_frontmatter(project_root, agent).get("use_when") or "").lower()
        if any(re.search(rf"\b{re.escape(w)}\b", use_when) for w in words):
            candidates.append(agent.name)
    return candidates[0] if len(candidates) == 1 else ""


def _referenced_memory_paths(agents: list[Any]) -> set[str]:
    """Every path named in any collected agent's own ``context:`` (:attr:`AgentSpec.context`, already resolved and verified to exist)."""
    referenced: set[str] = set()
    for agent in agents:
        referenced.update(str(entry) for entry in getattr(agent, "context", ()))
    return referenced


def propose_memory_links(target: Path) -> list[Any]:
    """One ``"memory-link"`` proposal per unreferenced memory fiche (issue #490).

    Scope matches the migration report's own list: ``agent-learnings/*.md``,
    ``decisions-log.md``, ``failure-museum.md``, ``network-topology.md`` —
    the fiches an agent's ``context:`` is meant to name. A fiche's path
    counts as referenced the moment *any* agent's effective ``context:``
    names it; nothing here ever edits a fiche's content, only proposes a
    frontmatter addition elsewhere (:func:`grimoire.proposals.
    accept_proposal` is the only writer, on a human's explicit accept).
    """
    from grimoire.hosts.collect import collect_agents
    from grimoire.proposals import create_manual_proposal

    mem_dir = target / "_grimoire" / "_memory"
    if not mem_dir.is_dir():
        return []

    candidates: list[Path] = []
    learnings = mem_dir / "agent-learnings"
    if learnings.is_dir():
        candidates.extend(sorted(learnings.rglob("*.md")))
    for name in ("decisions-log.md", "failure-museum.md", "network-topology.md"):
        p = mem_dir / name
        if p.is_file():
            candidates.append(p)

    try:
        agents = list(collect_agents(target))
    except Exception:
        agents = []
    referenced = _referenced_memory_paths(agents)

    entry_name = ""
    try:
        from grimoire.hosts.collect import entry_agent_name

        entry_name = entry_agent_name(target)
    except Exception:
        entry_name = ""

    proposals = []
    for fiche in candidates:
        mem_rel = fiche.relative_to(mem_dir).as_posix()
        full_rel = fiche.relative_to(target).as_posix()
        if mem_rel in referenced or full_rel in referenced:
            continue
        carrier = _carrier_for_words(target, agents, _fiche_words(mem_rel), exclude=entry_name)
        carrier_reason = f"porteur par mot entier : {carrier}" if carrier else "aucun porteur plausible : à placer à la main"
        proposals.append(
            create_manual_proposal(
                target,
                slug=f"memory-link-{re.sub(r'[^a-z0-9]+', '-', mem_rel.lower()).strip('-')}",
                specialty=f"fiche mémoire non raccordée : {mem_rel}",
                artifact_type="memory-link",
                target_agent=carrier,
                carrier_reason=carrier_reason,
                # Project-root-relative, like every real agent's `context:` entry
                # (e.g. `_grimoire/_memory/notes-securite.md`) — `mem_rel` above is
                # only for the human-readable slug/specialty, never for the value
                # `accept_proposal` writes into an agent's frontmatter, which
                # `collect_agents` validates against `project_root / c`.
                artifact_ref=full_rel,
                category="memory-unlinked",
            )
        )
    return proposals


# ── needs / hosts (V0 + V1 proposal) ─────────────────────────────────────────


def propose_needs_hosts(target: Path) -> list[Any]:
    """Resolve execution needs and declared hosts; propose a declaration for whatever is missing (issue #490)."""
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.exceptions import GrimoireConfigError
    from grimoire.core.execution_needs import resolve_execution_needs
    from grimoire.hosts.detection import detect_enabled_hosts
    from grimoire.proposals import create_manual_proposal

    proposals = []
    resolved = resolve_execution_needs(target)
    unresolved = sorted(need_id for need_id, r in resolved.items() if not r.resolved)
    if unresolved:
        proposals.append(
            create_manual_proposal(
                target,
                slug="needs-declare-commands",
                specialty="besoins d'exécution non résolus",
                artifact_type="needs-hosts",
                carrier_reason=f"déclarer needs.commands pour : {', '.join(unresolved)}",
                artifact_ref=",".join(unresolved),
                category="needs-unresolved",
            )
        )

    try:
        cfg: GrimoireConfig | None = GrimoireConfig.from_yaml(target / "project-context.yaml")
    except (GrimoireConfigError, OSError):
        cfg = None
    if cfg is None or cfg.hosts.enabled is None:
        detected = detect_enabled_hosts(target)
        proposals.append(
            create_manual_proposal(
                target,
                slug="hosts-declare-enabled",
                specialty="hosts.enabled non déclaré",
                artifact_type="needs-hosts",
                carrier_reason=f"déclarer hosts.enabled: [{', '.join(detected)}] (détecté sur le disque)",
                artifact_ref=",".join(detected),
                category="hosts-undeclared",
            )
        )
    return proposals


# ── verify ────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class VerifyResult:
    ok: bool
    unexpected_diffs: tuple[str, ...]
    expected_diffs: tuple[str, ...]
    missing: tuple[str, ...]
    report_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "unexpected_diffs": list(self.unexpected_diffs),
            "expected_diffs": list(self.expected_diffs),
            "missing": list(self.missing),
            "report_path": str(self.report_path),
        }


def verify_upgrade(target: Path, manifest: Path, *, extra_expected: frozenset[str] = frozenset()) -> VerifyResult:
    """Re-compare the pre-upgrade memory manifest; only :data:`EXPECTED_MEMORY_DIFFS` may differ.

    Raises :class:`GrimoireRuntimeError` if *manifest* itself is unreadable —
    a missing manifest means ``backup`` never ran, which is a refusal this
    node makes loudly, not a silent "nothing to verify".
    """
    if not manifest.is_file():
        raise GrimoireRuntimeError(f"manifeste introuvable : {manifest} — le nœud backup a-t-il tourné ?")

    expected = EXPECTED_MEMORY_DIFFS | extra_expected
    unexpected: list[str] = []
    expected_hit: list[str] = []
    missing: list[str] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        path = target / rel
        if not path.is_file():
            missing.append(rel)
            continue
        if _sha256(path) != digest:
            (expected_hit if rel in expected else unexpected).append(rel)

    report = run_output_dir(target) / "report.md"
    ok = not unexpected and not missing
    report.write_text(
        "# Rapport de mise à niveau\n\n"
        f"Généré le {datetime.now(UTC).isoformat()} par `grimoire upgrade-flow`.\n\n"
        f"Verdict : {'OK' if ok else 'ANOMALIE'}\n\n"
        f"- écarts attendus (ex. `_memory/config.yaml`) : {', '.join(expected_hit) or 'aucun'}\n"
        f"- écarts inattendus : {', '.join(unexpected) or 'aucun'}\n"
        f"- fichiers manquants : {', '.join(missing) or 'aucun'}\n",
        encoding="utf-8",
    )
    return VerifyResult(
        ok=ok,
        unexpected_diffs=tuple(unexpected),
        expected_diffs=tuple(expected_hit),
        missing=tuple(missing),
        report_path=report,
    )
