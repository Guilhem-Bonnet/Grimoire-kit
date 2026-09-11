#!/usr/bin/env python3
"""Measure whether the optional Rust cores (issue #354) change anything real.

Two cores exist, both PyO3-compiled, both with a pure-Python fallback and an
environment-variable backend switch:

- ``rust/grimoire-policies-core/`` -> ``grimoire.policies.engine.PolicyEngine.evaluate``
  (``GRIMOIRE_POLICIES_BACKEND=python|rust|auto``, PR #363).
- ``rust/grimoire-schema-core/`` -> ``grimoire.core.schema.generate_schema`` and
  ``grimoire.core.validator.validate_config``
  (``GRIMOIRE_SCHEMA_BACKEND=python|rust|auto``, PR #392).

Neither PR shipped a measurement of what the port actually buys the user.
This script produces three numbers, in increasing order of relevance:

1. **Micro** — per-call time of the ported function itself, Python vs Rust
   (``timeit``, median of 5 series of >=2000 calls), on a realistic input
   (a real ``grimoire init``-generated project) and a deliberately large one
   (100 policy rules / a 200-key config).
2. **Macro** — wall time of the CLI commands that actually call these
   functions (``grimoire doctor``, ``grimoire host sync``, ``grimoire
   standard verify``, and the ``PreToolUse`` hook decision), Python vs Rust
   backend, median of 10 runs, against the fixed cost of ``grimoire
   --version`` (process startup + import of the whole Typer tree).
3. **Profile** — ``python -X importtime`` and ``cProfile`` on ``grimoire
   doctor .``, to say what fraction of the macro time is even reachable by
   either core.

Usage
-----
    python scripts/bench-rust-cores.py                       # human table
    python scripts/bench-rust-cores.py --json-out bench.json  # + JSON dump
    python scripts/bench-rust-cores.py --micro-number 5000 --macro-runs 20

Requires both compiled cores to be importable (built with ``maturin build
--release`` + installed, see ``CONTRIBUTING.md#coeurs-optionnels-en-rust``
and ``docs/rust-cores-benchmark.md``); when a core is absent the script still
runs and reports the Python-only numbers for that core, with a note that the
Rust comparison could not be measured.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import timeit
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _median_per_call(fn: Any, *, number: int, repeat: int) -> float:
    """Median seconds/call across *repeat* series of *number* calls each."""
    series = timeit.repeat(fn, number=number, repeat=repeat)
    return statistics.median(t / number for t in series)


def _median_seconds(samples: list[float]) -> float:
    return statistics.median(samples)


@dataclass
class MicroResult:
    function: str
    input_kind: str
    python_us: float | None
    rust_us: float | None
    speedup: float | None
    note: str = ""


@dataclass
class MacroResult:
    command: str
    baseline_ms: float
    python_ms: float | None
    rust_ms: float | None
    delta_ms: float | None
    pct_of_total: float | None
    note: str = ""


@dataclass
class BenchReport:
    kit_version: str
    python_exe: str
    micro: list[MicroResult] = field(default_factory=list)
    macro: list[MacroResult] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Realistic fixtures
# --------------------------------------------------------------------------


def build_realistic_project(tmp_root: Path, python_exe: str) -> tuple[Path, Path]:
    """Run ``grimoire init -y`` in an isolated HOME and return (project_dir, home_dir).

    This is what "the real policy/config of an initialised project" means in
    practice: a project nobody hand-wrote for the benchmark.
    """
    project_dir = tmp_root / "bench-project"
    home_dir = tmp_root / "home"
    project_dir.mkdir(parents=True, exist_ok=True)
    home_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(home_dir)}
    subprocess.run(
        [python_exe, "-m", "grimoire.cli.app", "init", ".", "-y"],
        cwd=project_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return project_dir, home_dir


def large_config(n_extra_keys: int = 190) -> dict[str, Any]:
    """A config with ~200 keys: valid sections plus many unknown top-level keys.

    Unknown keys are what forces ``validate_config`` to walk its full
    suggestion path on every one of them — the worst case for the loop, not
    the common case.
    """
    cfg: dict[str, Any] = {
        "project": {"name": "bench", "description": "", "type": "generic", "stack": []},
        "user": {"name": "Dev", "language": "Français", "skill_level": "intermediate"},
    }
    for i in range(n_extra_keys):
        cfg[f"unknown_section_{i:03d}"] = {"value": i, "nested": {"a": 1, "b": [1, 2, 3]}}
    return cfg


def large_rule_set(n: int) -> list[Any]:
    """*n* synthetic rules that do not match the benchmark request.

    They exist to make the matching loop walk all of them before the
    builtin rule further down decides the verdict — the scenario the "100
    rules" input is meant to stress.
    """
    from grimoire.policies.schemas import ActionKind, MutationClass, PolicyRule, VerdictKind

    return [
        PolicyRule(
            id=f"synthetic-{i:03d}",
            description="synthetic rule for benchmark",
            action_kinds=(ActionKind.FILE_WRITE,),
            mutation_classes=(MutationClass.MUTATION_CONTROLLED,),
            risk_profiles=("standard",),
            verdict_on_match=VerdictKind.WARN,
            reason_template="synthetic",
        )
        for i in range(n)
    ]


def destructive_request() -> Any:
    """A realistic PolicyRequest: the shape a ``rm -rf`` PreToolUse call takes."""
    import uuid
    from datetime import UTC, datetime

    from grimoire.policies.schemas import ActionKind, MutationClass, PolicyAction, PolicyActor, PolicyRequest

    return PolicyRequest(
        id=str(uuid.uuid4()),
        run_id="bench-run",
        task_id="bench-task",
        actor=PolicyActor(actor_id="bench", host_id="claude-code"),
        action=PolicyAction(
            kind=ActionKind.FILE_WRITE,
            tool="Bash",
            mutation_class=MutationClass.DESTRUCTIVE,
            command="rm -rf _grimoire-output/tmp",
            target_files=("_grimoire-output/tmp",),
        ),
        risk_profile="light",
        created_at=datetime.now(tz=UTC).isoformat(),
    )


# --------------------------------------------------------------------------
# Micro benchmarks
# --------------------------------------------------------------------------


def bench_policy_engine(number: int, repeat: int) -> list[MicroResult]:
    from grimoire.policies.engine import PolicyEngine, rust_backend_available

    rust_ok = rust_backend_available()
    results = []

    for label, engine_factory in (
        ("realistic (builtin rules only)", lambda: PolicyEngine()),
        ("large (builtin + 100 rules)", lambda: _engine_with_extra_rules(100)),
    ):
        request = destructive_request()
        engine = engine_factory()

        os.environ["GRIMOIRE_POLICIES_BACKEND"] = "python"
        py_us = _median_per_call(lambda e=engine, r=request: e.evaluate(r), number=number, repeat=repeat) * 1e6

        rust_us: float | None = None
        note = ""
        if rust_ok:
            os.environ["GRIMOIRE_POLICIES_BACKEND"] = "rust"
            rust_us = _median_per_call(lambda e=engine, r=request: e.evaluate(r), number=number, repeat=repeat) * 1e6
        else:
            note = "grimoire_policies_core absent — non mesurable"
        os.environ.pop("GRIMOIRE_POLICIES_BACKEND", None)

        results.append(
            MicroResult(
                function="PolicyEngine.evaluate",
                input_kind=label,
                python_us=py_us,
                rust_us=rust_us,
                speedup=(py_us / rust_us) if rust_us else None,
                note=note,
            )
        )
    return results


def _engine_with_extra_rules(n: int) -> Any:
    from grimoire.policies.engine import PolicyEngine

    engine = PolicyEngine()
    for rule in large_rule_set(n):
        engine.register_rule(rule)
    return engine


def bench_schema_generate(number: int, repeat: int) -> list[MicroResult]:
    from grimoire.core import schema as schema_mod

    rust_ok = schema_mod.rust_backend_available()

    os.environ["GRIMOIRE_SCHEMA_BACKEND"] = "python"
    py_us = _median_per_call(schema_mod.generate_schema, number=number, repeat=repeat) * 1e6

    rust_us: float | None = None
    note = "sortie statique — pas de variante volumineuse"
    if rust_ok:
        os.environ["GRIMOIRE_SCHEMA_BACKEND"] = "rust"
        rust_us = _median_per_call(schema_mod.generate_schema, number=number, repeat=repeat) * 1e6
    else:
        note = "grimoire_schema_core absent — non mesurable"
    os.environ.pop("GRIMOIRE_SCHEMA_BACKEND", None)

    return [
        MicroResult(
            function="generate_schema",
            input_kind="unique (schéma fixe)",
            python_us=py_us,
            rust_us=rust_us,
            speedup=(py_us / rust_us) if rust_us else None,
            note=note,
        )
    ]


def bench_validate_config(number: int, repeat: int, realistic_data: dict[str, Any]) -> list[MicroResult]:
    from grimoire.core import validator as validator_mod

    rust_ok = validator_mod.rust_backend_available()
    results = []

    for label, data in (
        ("realistic (project-context.yaml réel)", realistic_data),
        ("large (200 clés, sections inconnues)", large_config()),
    ):
        os.environ["GRIMOIRE_SCHEMA_BACKEND"] = "python"
        py_us = _median_per_call(lambda d=data: validator_mod.validate_config(d), number=number, repeat=repeat) * 1e6

        rust_us: float | None = None
        note = ""
        if rust_ok:
            os.environ["GRIMOIRE_SCHEMA_BACKEND"] = "rust"
            rust_us = (
                _median_per_call(lambda d=data: validator_mod.validate_config(d), number=number, repeat=repeat) * 1e6
            )
        else:
            note = "grimoire_schema_core absent — non mesurable"
        os.environ.pop("GRIMOIRE_SCHEMA_BACKEND", None)

        results.append(
            MicroResult(
                function="validate_config",
                input_kind=label,
                python_us=py_us,
                rust_us=rust_us,
                speedup=(py_us / rust_us) if rust_us else None,
                note=note,
            )
        )
    return results


# --------------------------------------------------------------------------
# Macro benchmarks
# --------------------------------------------------------------------------


def _time_subprocess(cmd: list[str], *, cwd: Path, env: dict[str, str], runs: int) -> float:
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        subprocess.run(cmd, cwd=cwd, env=env, check=False, capture_output=True, text=True)
        samples.append((time.perf_counter() - start) * 1000)
    return _median_seconds(samples)


def bench_macro(
    python_exe: str, project_dir: Path, home_dir: Path, runs: int, rust_available: dict[str, bool]
) -> list[MacroResult]:
    base_env = {**os.environ, "HOME": str(home_dir)}
    baseline_ms = _time_subprocess([python_exe, "-m", "grimoire.cli.app", "--version"], cwd=project_dir, env=base_env, runs=runs)

    commands: list[tuple[str, list[str], str]] = [
        ("grimoire doctor .", [python_exe, "-m", "grimoire.cli.app", "doctor", "."], "schema"),
        (
            "grimoire host sync --dry-run",
            [python_exe, "-m", "grimoire.cli.app", "host", "sync", "--project-root", ".", "--dry-run"],
            "policies",
        ),
        (
            "grimoire standard verify .",
            [python_exe, "-m", "grimoire.cli.app", "standard", "verify", "."],
            "schema",
        ),
    ]

    results = []
    for label, cmd, backend_kind in commands:
        env_var = "GRIMOIRE_SCHEMA_BACKEND" if backend_kind == "schema" else "GRIMOIRE_POLICIES_BACKEND"

        py_env = {**base_env, env_var: "python"}
        py_ms = _time_subprocess(cmd, cwd=project_dir, env=py_env, runs=runs)

        rust_ms: float | None = None
        note = ""
        if rust_available[backend_kind]:
            rust_env = {**base_env, env_var: "rust"}
            rust_ms = _time_subprocess(cmd, cwd=project_dir, env=rust_env, runs=runs)
        else:
            note = "cœur Rust absent — non mesurable"

        delta = (py_ms - rust_ms) if rust_ms is not None else None
        pct = (delta / py_ms * 100) if delta is not None and py_ms else None
        results.append(
            MacroResult(
                command=label,
                baseline_ms=baseline_ms,
                python_ms=py_ms,
                rust_ms=rust_ms,
                delta_ms=delta,
                pct_of_total=pct,
                note=note,
            )
        )

    # PreToolUse hook decision, if the CLI entry point is installed.
    hook_bin = Path(python_exe).parent / "grimoire-hook"
    if hook_bin.exists():
        payload = '{"tool_name":"Bash","tool_input":{"command":"rm -rf _grimoire-output/tmp"}}'
        hook_cmd_base = [str(hook_bin), "--host", "claude-code", "--event", "PreToolUse", "--project-root", ".", "--decision", "grimoire.tool-policy"]

        def _time_hook(runs: int, env: dict[str, str]) -> float:
            samples = []
            for _ in range(runs):
                start = time.perf_counter()
                subprocess.run(hook_cmd_base, cwd=project_dir, env=env, input=payload, check=False, capture_output=True, text=True)
                samples.append((time.perf_counter() - start) * 1000)
            return _median_seconds(samples)

        py_env = {**base_env, "GRIMOIRE_POLICIES_BACKEND": "python"}
        py_ms = _time_hook(runs, py_env)
        rust_ms = None
        note = ""
        if rust_available["policies"]:
            rust_env = {**base_env, "GRIMOIRE_POLICIES_BACKEND": "rust"}
            rust_ms = _time_hook(runs, rust_env)
        else:
            note = "cœur Rust absent — non mesurable"
        delta = (py_ms - rust_ms) if rust_ms is not None else None
        pct = (delta / py_ms * 100) if delta is not None and py_ms else None
        results.append(
            MacroResult(
                command="grimoire-hook PreToolUse (tool-policy)",
                baseline_ms=baseline_ms,
                python_ms=py_ms,
                rust_ms=rust_ms,
                delta_ms=delta,
                pct_of_total=pct,
                note=note,
            )
        )
    else:
        results.append(
            MacroResult(
                command="grimoire-hook PreToolUse (tool-policy)",
                baseline_ms=baseline_ms,
                python_ms=None,
                rust_ms=None,
                delta_ms=None,
                pct_of_total=None,
                note="binaire grimoire-hook introuvable dans le venv — non mesuré",
            )
        )

    return results


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------


def profile_importtime(python_exe: str, project_dir: Path, home_dir: Path) -> list[dict[str, Any]]:
    import re

    env = {**os.environ, "HOME": str(home_dir)}
    proc = subprocess.run(
        [python_exe, "-X", "importtime", "-m", "grimoire.cli.app", "doctor", "."],
        cwd=project_dir,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    pattern = re.compile(r"^import time:\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(.*)$")
    rows = []
    for line in proc.stderr.splitlines():
        m = pattern.match(line)
        if not m:
            continue
        self_us, cumulative_us, name = m.groups()
        rows.append({"module": name.strip(), "self_us": int(self_us), "cumulative_us": int(cumulative_us)})
    rows.sort(key=lambda r: r["cumulative_us"], reverse=True)
    return rows[:10]


def profile_cprofile(python_exe: str, project_dir: Path, home_dir: Path, tmp_root: Path) -> dict[str, Any]:
    import pstats

    env = {**os.environ, "HOME": str(home_dir)}
    prof_path = tmp_root / "doctor.prof"
    subprocess.run(
        [python_exe, "-m", "cProfile", "-o", str(prof_path), "-m", "grimoire.cli.app", "doctor", "."],
        cwd=project_dir,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    stats = pstats.Stats(str(prof_path))
    total_calls = stats.total_calls  # type: ignore[attr-defined]
    total_tt = stats.total_tt  # type: ignore[attr-defined]

    ported_markers = (
        "policies/engine.py",
        "core/schema.py",
        "core/validator.py",
        "grimoire_policies_core",
        "grimoire_schema_core",
    )
    ported_tottime = 0.0
    top: list[dict[str, Any]] = []
    for (filename, lineno, funcname), (_cc, _nc, tt, ct, _callers) in stats.stats.items():  # type: ignore[attr-defined]
        if any(marker in filename for marker in ported_markers):
            ported_tottime += tt
        top.append(
            {
                "location": f"{filename}:{lineno}({funcname})",
                "tottime_s": tt,
                "cumtime_s": ct,
            }
        )
    top.sort(key=lambda r: r["cumtime_s"], reverse=True)

    return {
        "total_calls": total_calls,
        "total_tottime_s": total_tt,
        "ported_functions_tottime_s": ported_tottime,
        "ported_functions_pct_of_tottime": (ported_tottime / total_tt * 100) if total_tt else 0.0,
        "top10_by_cumtime": top[:10],
    }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _fmt(value: float | None, unit: str, digits: int = 1) -> str:
    if value is None:
        return "n/d"
    return f"{value:.{digits}f}{unit}"


def print_report(report: BenchReport) -> None:
    print(f"# Bench cœurs Rust — grimoire-kit {report.kit_version}\n")

    print("## Micro (temps par appel, médiane de 5 séries)\n")
    print(f"{'fonction':<20} {'entrée':<38} {'python':>10} {'rust':>10} {'x':>6}  note")
    for micro_r in report.micro:
        speed = f"{micro_r.speedup:.2f}x" if micro_r.speedup else "n/d"
        print(
            f"{micro_r.function:<20} {micro_r.input_kind:<38} {_fmt(micro_r.python_us, 'us'):>10} "
            f"{_fmt(micro_r.rust_us, 'us'):>10} {speed:>6}  {micro_r.note}"
        )

    print("\n## Macro (temps de bout en bout, médiane de N exécutions)\n")
    print(f"{'commande':<40} {'baseline':>10} {'python':>10} {'rust':>10} {'gain':>10} {'% total':>8}  note")
    for macro_r in report.macro:
        gain = f"{macro_r.delta_ms:+.1f}ms" if macro_r.delta_ms is not None else "n/d"
        pct = f"{macro_r.pct_of_total:.1f}%" if macro_r.pct_of_total is not None else "n/d"
        print(
            f"{macro_r.command:<40} {_fmt(macro_r.baseline_ms, 'ms'):>10} {_fmt(macro_r.python_ms, 'ms'):>10} "
            f"{_fmt(macro_r.rust_ms, 'ms'):>10} {gain:>10} {pct:>8}  {macro_r.note}"
        )

    print("\n## Profil — top 10 par temps cumulé (python -X importtime, grimoire doctor .)\n")
    for row in report.profile.get("importtime_top10", []):
        print(f"  {row['cumulative_us']:>8} us  {row['module']}")

    cprof = report.profile.get("cprofile", {})
    if cprof:
        print("\n## Profil — cProfile, grimoire doctor . (top 10 par temps cumulé)\n")
        for row in cprof.get("top10_by_cumtime", []):
            print(f"  cumtime={row['cumtime_s']:.4f}s  tottime={row['tottime_s']:.4f}s  {row['location']}")
        pct = cprof.get("ported_functions_pct_of_tottime", 0.0)
        print(
            f"\n  Temps self (tottime) dans les fonctions portées "
            f"(policies/engine, core/schema, core/validator) : {pct:.2f}% du tottime total "
            f"({cprof.get('ported_functions_tottime_s', 0.0):.4f}s / {cprof.get('total_tottime_s', 0.0):.4f}s)."
        )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json-out", type=Path, default=None, help="Also write the full report as JSON to this path.")
    parser.add_argument("--micro-number", type=int, default=2000, help="Calls per timeit series (default: 2000).")
    parser.add_argument("--micro-repeat", type=int, default=5, help="Number of timeit series (default: 5).")
    parser.add_argument("--macro-runs", type=int, default=10, help="Subprocess runs per macro measurement (default: 10).")
    parser.add_argument("--skip-macro", action="store_true", help="Skip the CLI-level (subprocess) measurements.")
    parser.add_argument("--skip-profile", action="store_true", help="Skip importtime/cProfile.")
    parser.add_argument("--keep-project", action="store_true", help="Do not delete the generated benchmark project.")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT / "src"))
    from grimoire.__version__ import __version__
    from grimoire.core.schema import rust_backend_available as schema_rust_available
    from grimoire.policies.engine import rust_backend_available as policies_rust_available

    python_exe = sys.executable
    rust_available = {"policies": policies_rust_available(), "schema": schema_rust_available()}

    with tempfile.TemporaryDirectory(prefix="grimoire-bench-") as tmp:
        tmp_root = Path(tmp)
        project_dir, home_dir = build_realistic_project(tmp_root, python_exe)
        realistic_config = _load_yaml(project_dir / "project-context.yaml")

        report = BenchReport(kit_version=__version__, python_exe=python_exe)
        report.micro.extend(bench_policy_engine(args.micro_number, args.micro_repeat))
        report.micro.extend(bench_schema_generate(args.micro_number, args.micro_repeat))
        report.micro.extend(bench_validate_config(args.micro_number, args.micro_repeat, realistic_config))

        if not args.skip_macro:
            report.macro = bench_macro(python_exe, project_dir, home_dir, args.macro_runs, rust_available)

        if not args.skip_profile:
            report.profile["importtime_top10"] = profile_importtime(python_exe, project_dir, home_dir)
            report.profile["cprofile"] = profile_cprofile(python_exe, project_dir, home_dir, tmp_root)

        if args.keep_project:
            kept = ROOT / ".bench-project-kept"
            print(f"(--keep-project: {project_dir} won't survive tmpdir cleanup; not copied to {kept})")

    print_report(report)

    if args.json_out:
        args.json_out.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nJSON écrit : {args.json_out}")

    return 0


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml  # type: ignore[import-untyped]

    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
