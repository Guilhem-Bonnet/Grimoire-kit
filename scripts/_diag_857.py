"""Diagnostic temporaire (#857) : où part le temps d'un sous-processus ``grimoire`` ?"""

from __future__ import annotations

import contextlib
import cProfile
import io
import os
import pstats
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def timed(label: str, cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    t0 = time.perf_counter()
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False, timeout=600, env=env)
    print(f"{time.perf_counter() - t0:8.2f}s  rc={done.returncode}  {label}", flush=True)
    if done.returncode != 0:
        print(done.stderr[-600:], flush=True)


def main() -> None:
    base = Path(tempfile.mkdtemp(prefix="diag857-"))
    py = sys.executable
    timed("python -c pass", [py, "-c", "pass"], base)
    timed("import grimoire.cli", [py, "-c", "import grimoire.cli"], base)
    timed("grimoire --version", [py, "-m", "grimoire", "--version"], base)
    timed("docker info", ["docker", "info", "--format", "{{.ServerVersion}}"], base)
    for i, extra in enumerate(([], ["--backend", "local"], [])):
        root = base / f"p{i}"
        root.mkdir()
        timed(
            f"grimoire init -y {' '.join(extra)}",
            [py, "-m", "grimoire", "init", ".", "-y", "--name", f"p{i}", *extra],
            root,
        )
    root = base / "std"
    root.mkdir()
    timed("grimoire init -y (pour standard init)", [py, "-m", "grimoire", "init", ".", "-y", "--name", "std"], root)
    timed(
        "grimoire standard init --profile governed",
        [py, "-m", "grimoire", "standard", "init", "--profile", "governed"],
        root,
    )
    timed("grimoire doctor", [py, "-m", "grimoire", "doctor"], root)
    print(f"fichiers écrits par init : {sum(1 for _ in (base / 'p0').rglob('*') if _.is_file())}", flush=True)

    # Profil in-process d'un init : les 45 fonctions les plus coûteuses (cumulatif).
    root = base / "prof"
    root.mkdir()
    os.chdir(root)
    sys.argv = ["grimoire", "init", ".", "-y", "--name", "prof"]
    from grimoire.cli.app import cli as cli_main

    prof = cProfile.Profile()
    t0 = time.perf_counter()
    with contextlib.suppress(SystemExit):
        prof.runcall(cli_main)
    print(f"{time.perf_counter() - t0:8.2f}s  init in-process (profilé)", flush=True)
    out = io.StringIO()
    pstats.Stats(prof, stream=out).sort_stats("cumulative").print_stats(45)
    print(out.getvalue()[:12000], flush=True)


if __name__ == "__main__":
    main()
