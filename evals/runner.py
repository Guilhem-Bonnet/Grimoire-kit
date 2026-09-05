"""Runner de campagne — témoin web-app-todo, campagne 2026-09-04.

Exécute des runs ``(tâche, bras, répétition)`` selon RUN-PROTOCOL.md à partir
d'une copie propre de la baseline, lance Claude Code CLI en mode ``-p``, puis
produit pour chaque run : ``result.json`` (sortie CLI), ``mechanical.json``
(suites go/npm réellement exécutées, tests baseline en surcouche),
``governance.json`` (ledger des hooks, engagement), ``diff.patch`` et
``record.json`` (run-record du collecteur, métriques externes mesurables
renseignées, ``completed``/``regressions`` laissés à ``null`` pour le jugement).

Le runner n'invente aucune métrique : ce qu'il ne mesure pas reste ``null``.

Usage (depuis la racine du dépôt, venv activé) :

    python evals/runner.py plan --date 2026-09-04 --arms enforced activated-v3 --reps 5
    python evals/runner.py campaign --date 2026-09-04 --concurrency 3 --hard-cap 60
    python evals/runner.py mechanical --run-dir evals/runs/2026-09-04/<task>/<arm>/rep-1
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
WITNESS = EVALS / "witnesses" / "web-app-todo"
BASELINE_APP = WITNESS / "app"
TASKS_YAML = EVALS / "tasks" / "web-app-todo.yaml"
RUNS = EVALS / "runs"
TOOLING = {
    "claude": RUNS / "_runner" / "node_modules" / ".bin" / "claude",
    "go_bin": RUNS / "_toolchain" / "go" / "bin",
    "venv_bin": ROOT / ".venv" / "bin",
    "config_dir": RUNS / "_claude-config",
    "node_modules_cache": RUNS / "_cache" / "node_modules",
}
MODEL = "claude-sonnet-4-6"
MAX_TURNS = 100
TIMEOUT_S = 1800
EXCLUDE_DIFF = ("node_modules", "_grimoire", "_grimoire-output", ".claude", ".github", ".grimoire", "CLAUDE.md")
GO_TEST_GLOB = "api/*_test.go"
WEB_TEST_GLOB = "web/src/**/*.test.tsx"
ARM_SETUP = {
    "activated-v3": ("solo-prototyping", None, WITNESS / "activated" / "install.sh"),
    "enforced": (None, "governed", WITNESS / "enforced" / "install.sh"),
}

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = yaml.safe_load(TASKS_YAML.read_text(encoding="utf-8"))["tasks"]
    return tasks


def run_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{TOOLING['venv_bin']}:{TOOLING['go_bin']}:{env['PATH']}"
    env["CLAUDE_CONFIG_DIR"] = str(TOOLING["config_dir"])
    env["GOTOOLCHAIN"] = "auto"
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    return env


def sh(
    cmd: list[str], cwd: Path, timeout: int = 600, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env or run_env(), check=False
    )


# ── préparation ────────────────────────────────────────────────────────────────


def prepare(run_dir: Path, arm: str) -> Path:
    app = run_dir / "app"
    if app.exists():
        raise SystemExit(f"{app} existe déjà — un run ne repart jamais d'un état modifié")
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BASELINE_APP, app, symlinks=True)
    needs, profile, installer = ARM_SETUP[arm]
    standard_opts = ["--needs", needs] if needs else ["--profile", str(profile)]
    for cmd in (
        ["grimoire", "init", ".", "-a", "web-app", "-b", "local"],
        ["grimoire", "standard", "init", ".", *standard_opts],
    ):
        res = sh(cmd, cwd=app)
        if res.returncode != 0:
            raise SystemExit(f"enrôlement échoué ({' '.join(cmd)}) : {res.stderr[-800:]}")
    res = sh([str(installer), str(app)], cwd=ROOT)
    if res.returncode != 0:
        raise SystemExit(f"installateur {installer} échoué : {res.stderr[-800:]}")
    (run_dir / "setup.log").write_text(res.stdout + res.stderr, encoding="utf-8")
    cache = TOOLING["node_modules_cache"]
    web = app / "web"
    if cache.is_dir():
        shutil.copytree(cache, web / "node_modules", symlinks=True)
    else:
        res = sh(["npm", "ci", "--no-audit", "--no-fund"], cwd=web)
        if res.returncode != 0:
            raise SystemExit(f"npm ci échoué : {res.stderr[-800:]}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(web / "node_modules", cache, symlinks=True)
    return app


# ── exécution ──────────────────────────────────────────────────────────────────


def launch(run_dir: Path, prompt: str) -> dict[str, Any]:
    app = run_dir / "app"
    cmd = [
        str(TOOLING["claude"]),
        "-p",
        prompt,
        "--model",
        MODEL,
        "--max-turns",
        str(MAX_TURNS),
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
    ]
    started = time.time()
    meta: dict[str, Any] = {"started_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), "timed_out": False}
    try:
        res = subprocess.run(
            cmd, cwd=app, capture_output=True, text=True, timeout=TIMEOUT_S, env=run_env(), check=False
        )
        stdout, stderr, rc = res.stdout, res.stderr, res.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        rc, meta["timed_out"] = -1, True
    meta.update(
        {
            "exit_code": rc,
            "wall_s": round(time.time() - started, 1),
            "ended_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        }
    )
    (run_dir / "stdout.raw").write_text(stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    result: dict[str, Any] | None = None
    with contextlib.suppress(json.JSONDecodeError):
        result = json.loads(stdout)
    if result is not None:
        (run_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    meta["parsed"] = result is not None
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


# ── jugement mécanique ─────────────────────────────────────────────────────────


def _go_mod_version(app: Path) -> str | None:
    m = re.search(r"^go\s+(\S+)", (app / "api" / "go.mod").read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1) if m else None


def _go_test_names(path: Path) -> set[str]:
    return set(re.findall(r"^func (Test\w+)\(", path.read_text(encoding="utf-8"), re.MULTILINE))


def _web_test_names(path: Path) -> set[str]:
    return {m[1] for m in re.findall(r"(?:^|\s)(?:it|test)\(\s*(['\"`])(.+?)\1", path.read_text(encoding="utf-8"))}


def _suite(cmd: list[str], cwd: Path, timeout: int = 900) -> dict[str, Any]:
    try:
        res = sh(cmd, cwd=cwd, timeout=timeout)
        return {"ok": res.returncode == 0, "exit": res.returncode, "tail": (res.stdout + res.stderr)[-3000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit": None, "tail": "timeout"}


def mechanical(run_dir: Path) -> dict[str, Any]:
    app = run_dir / "app"
    out: dict[str, Any] = {"go_mod_version": _go_mod_version(app)}
    # suites du run, état final
    out["go"] = _suite(["go", "test", "./..."], app / "api")
    out["go_vet"] = _suite(["go", "vet", "./..."], app / "api")
    lock_changed = (app / "web" / "package-lock.json").read_text() != (
        BASELINE_APP / "web" / "package-lock.json"
    ).read_text()
    if not (app / "web" / "node_modules").is_dir() or lock_changed:
        sh(["npm", "install", "--no-audit", "--no-fund"], cwd=app / "web")
    out["npm"] = _suite(["npm", "test", "--silent"], app / "web")
    out["tests_green"] = bool(out["go"]["ok"] and out["npm"]["ok"])

    # tests baseline : supprimés / modifiés / cassés en surcouche
    baseline_tests = sorted(
        p.relative_to(BASELINE_APP)
        for p in list(BASELINE_APP.glob(GO_TEST_GLOB)) + list(BASELINE_APP.glob(WEB_TEST_GLOB))
    )
    files: dict[str, dict[str, Any]] = {}
    for rel in baseline_tests:
        src, dst = BASELINE_APP / rel, app / rel
        names = _go_test_names(src) if rel.suffix == ".go" else _web_test_names(src)
        entry: dict[str, Any] = {"baseline_tests": sorted(names)}
        if not dst.is_file():
            entry.update({"status": "deleted", "missing_tests": sorted(names)})
        else:
            same = src.read_bytes() == dst.read_bytes()
            run_names = _go_test_names(dst) if rel.suffix == ".go" else _web_test_names(dst)
            entry.update({"status": "identical" if same else "modified", "missing_tests": sorted(names - run_names)})
        files[str(rel)] = entry
    out["baseline_test_files"] = files
    # surcouche : les fichiers de test baseline recopiés sur l'état final, suites relancées
    overlay = run_dir / "_overlay"
    if overlay.exists():
        shutil.rmtree(overlay)
    shutil.copytree(
        app, overlay, symlinks=True, ignore=shutil.ignore_patterns("node_modules", "_grimoire-output", ".git")
    )
    for rel in baseline_tests:
        (overlay / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BASELINE_APP / rel, overlay / rel)
    if (app / "web" / "node_modules").is_dir():
        (overlay / "web" / "node_modules").symlink_to(app / "web" / "node_modules")
    out["overlay_go"] = _suite(["go", "test", "./..."], overlay / "api")
    out["overlay_npm"] = _suite(["npm", "test", "--silent"], overlay / "web")
    shutil.rmtree(overlay, ignore_errors=True)
    deleted = [f for f, e in files.items() if e["status"] == "deleted"]
    modified = [f for f, e in files.items() if e["status"] == "modified"]
    removed_tests = sum(len(e["missing_tests"]) for e in files.values())
    out["baseline_summary"] = {
        "deleted_files": deleted,
        "modified_files": modified,
        "removed_tests": removed_tests,
        "overlay_green": bool(out["overlay_go"]["ok"] and out["overlay_npm"]["ok"]),
    }
    # Règle secondaire pré-enregistrée : dure = test baseline cassé ou supprimé
    # sur l'état final du run (suite rouge, fichier ou fonction de test absents) ;
    # adaptée = fichier de test baseline modifié, suites vertes. La surcouche
    # (tests baseline recopiés tels quels) est informative : elle est rouge dès
    # que le contrat de `Store` s'étend (fakeStore à compléter), ce qui n'est pas
    # une régression — le juge tranche sur le diff.
    hard = bool(deleted or removed_tests or not out["tests_green"])
    out["baseline_summary"]["hard_regression_candidate"] = hard
    out["baseline_summary"]["adapted_candidate"] = bool(modified and not hard)
    (run_dir / "mechanical.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def diff_patch(run_dir: Path) -> dict[str, Any]:
    app = run_dir / "app"
    cmd = ["diff", "-ruN"] + [x for e in EXCLUDE_DIFF for x in ("-x", e)] + [str(BASELINE_APP), str(app)]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    patch = res.stdout.replace(str(BASELINE_APP), "a").replace(str(app), "b")
    (run_dir / "diff.patch").write_text(patch, encoding="utf-8")
    changed = sorted(set(re.findall(r"^diff -ruN .* b/(\S+)$", patch, re.MULTILINE)))
    summary = {"changed_files": changed, "patch_lines": patch.count("\n")}
    (run_dir / "diff.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


# ── gouvernance / engagement ───────────────────────────────────────────────────


def governance(run_dir: Path) -> dict[str, Any]:
    app = run_dir / "app"
    out: dict[str, Any] = {
        "ledger": {
            "pretool_allow": 0,
            "pretool_block": 0,
            "pretool_ask": 0,
            "stop_block": 0,
            "stop_allow": 0,
            "records": 0,
        },
        "envelope_filled": None,
        "evidence_rows": None,
        "context_bundle_present": (app / "_grimoire-output/context/bootstrap/context-bundle.yaml").is_file(),
        "board_bootstrap_status": None,
    }
    ledger = app / "_grimoire-output" / "traces" / "traces.jsonl"
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            out["ledger"]["records"] += 1
            tags = set(rec.get("tags") or [])
            if "pre_tool_use" in tags:
                key = "pretool_block" if "block" in tags else "pretool_ask" if "ask" in tags else "pretool_allow"
                out["ledger"][key] += 1
            elif "stop" in tags:
                out["ledger"]["stop_block" if "block" in tags else "stop_allow"] += 1
    env_path = app / "_grimoire-output/evidence/bootstrap/task-envelope.md"
    if env_path.is_file():
        text = env_path.read_text(encoding="utf-8")
        scaffold = _scaffold_text(run_dir, "task-envelope.md")
        out["envelope_filled"] = text != scaffold and not re.search(
            r"TODO|placeholder|à compléter|to be filled", text, re.IGNORECASE
        )
    pack = app / "_grimoire-output/evidence/bootstrap/evidence-pack.md"
    if pack.is_file():
        rows = [
            ln
            for ln in pack.read_text(encoding="utf-8").splitlines()
            if ln.startswith("|") and not re.match(r"^\|[\s\-|:]+\|$", ln)
        ]
        out["evidence_rows"] = max(0, len(rows) - 1) if rows else 0
    board = app / "_grimoire/standard/task-board.yaml"
    if board.is_file():
        m = re.search(r'task_id: "bootstrap"\n(?:.*\n)*?\s+status: "([^"]+)"', board.read_text(encoding="utf-8"))
        out["board_bootstrap_status"] = m.group(1) if m else None
    (run_dir / "governance.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def _scaffold_text(run_dir: Path, name: str) -> str:
    snap = run_dir / "scaffold" / name
    return snap.read_text(encoding="utf-8") if snap.is_file() else ""


def snapshot_scaffold(run_dir: Path) -> None:
    src = run_dir / "app" / "_grimoire-output/evidence/bootstrap"
    dst = run_dir / "scaffold"
    dst.mkdir(exist_ok=True)
    for name in ("task-envelope.md", "evidence-pack.md"):
        if (src / name).is_file():
            shutil.copy2(src / name, dst / name)


# ── collecte ───────────────────────────────────────────────────────────────────


def collect(run_dir: Path, task_id: str, arm: str) -> dict[str, Any]:
    sys.path.insert(0, str(EVALS))
    from collect import collect_record

    record = collect_record(run_dir / "app", "web-app-todo", task_id, arm)
    result = (
        json.loads((run_dir / "result.json").read_text(encoding="utf-8")) if (run_dir / "result.json").is_file() else {}
    )
    mech = (
        json.loads((run_dir / "mechanical.json").read_text(encoding="utf-8"))
        if (run_dir / "mechanical.json").is_file()
        else {}
    )
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8")) if (run_dir / "meta.json").is_file() else {}
    record["external"].update(
        {
            "tests_green": mech.get("tests_green"),
            "tokens_cost": result.get("total_cost_usd"),
            "human_interventions": 0,
        }
    )
    record["run"] = {
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
        "timed_out": meta.get("timed_out"),
        "exit_code": meta.get("exit_code"),
        "usage": result.get("usage"),
    }
    record["mechanical"] = mech.get("baseline_summary")
    record["governance"] = (
        json.loads((run_dir / "governance.json").read_text(encoding="utf-8"))
        if (run_dir / "governance.json").is_file()
        else None
    )
    (run_dir / "record.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


# ── campagne ───────────────────────────────────────────────────────────────────


def execute_run(date: str, task: dict[str, Any], arm: str, rep: int) -> dict[str, Any]:
    run_dir = RUNS / date / task["id"] / arm / f"rep-{rep}"
    label = f"{task['id']}/{arm}/rep-{rep}"
    if (run_dir / "record.json").is_file():
        log(f"{label} : déjà exécuté, ignoré")
        return json.loads((run_dir / "record.json").read_text(encoding="utf-8"))
    log(f"{label} : préparation")
    prepare(run_dir, arm)
    snapshot_scaffold(run_dir)
    log(f"{label} : lancement")
    meta = launch(run_dir, task["prompt"])
    log(
        f"{label} : terminé exit={meta['exit_code']} timeout={meta['timed_out']} ({meta['wall_s']} s) — jugement mécanique"
    )
    mechanical(run_dir)
    diff_patch(run_dir)
    governance(run_dir)
    record = collect(run_dir, task["id"], arm)
    cost = record["external"]["tokens_cost"]
    log(
        f"{label} : coût {cost} USD, tours {record['run']['num_turns']}, tests_green {record['external']['tests_green']}"
    )
    return record


def plan(arms: list[str], reps: int, tasks: list[dict[str, Any]]) -> list[tuple[str, str, int]]:
    """Blocs entrelacés par répétition ; ordre des bras alterné selon la parité de la répétition."""
    order: list[tuple[str, str, int]] = []
    for rep in range(1, reps + 1):
        arm_order = list(arms) if rep % 2 == 1 else list(reversed(arms))
        for task in tasks:
            for arm in arm_order:
                order.append((task["id"], arm, rep))
    return order


def spent(date: str) -> tuple[float, int]:
    total, n = 0.0, 0
    for rec in (RUNS / date).glob("*/*/rep-*/record.json"):
        cost = json.loads(rec.read_text(encoding="utf-8"))["external"].get("tokens_cost")
        if cost is not None:
            total, n = total + cost, n + 1
    return round(total, 2), n


def campaign(
    date: str, arms: list[str], reps: int, concurrency: int, hard_cap: float, max_runs: int | None, est_cost: float
) -> None:
    tasks = {t["id"]: t for t in load_tasks()}
    order = plan(arms, reps, list(tasks.values()))
    lock = RUNS / date / "campaign.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.is_file():
        pid = int(lock.read_text().strip() or 0)
        if pid and Path(f"/proc/{pid}").exists():
            raise SystemExit(f"campagne déjà en cours (pid {pid}) — verrou {lock}")
    lock.write_text(str(os.getpid()))
    stop = threading.Event()
    launched = 0
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures: list[Future[dict[str, Any]]] = []
            for task_id, arm, rep in order:
                if (RUNS / date / task_id / arm / f"rep-{rep}" / "record.json").is_file():
                    continue
                if max_runs is not None and launched >= max_runs:
                    break
                while sum(1 for f in futures if not f.done()) >= concurrency:
                    time.sleep(5)
                total, n = spent(date)
                running = sum(1 for f in futures if not f.done())
                unit = max(est_cost, total / n if n else 0)
                if total + (running + 1) * unit > hard_cap:
                    log(
                        f"plafond dur : dépensé {total} USD + {running + 1} run(s) × {unit:.2f} > {hard_cap} — arrêt des lancements"
                    )
                    stop.set()
                    break
                futures.append(pool.submit(execute_run, date, tasks[task_id], arm, rep))
                launched += 1
                time.sleep(2)
            for f in futures:
                try:
                    f.result()
                except Exception as exc:
                    log(f"run en échec : {exc}")
    finally:
        lock.unlink(missing_ok=True)
    total, n = spent(date)
    log(f"campagne : {n} runs enregistrés, {total} USD dépensés")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("campaign")
    p.add_argument("--date", required=True)
    p.add_argument("--arms", nargs="+", default=["enforced", "activated-v3"])
    p.add_argument("--reps", type=int, default=5)
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--hard-cap", type=float, default=60.0)
    p.add_argument("--max-runs", type=int, default=None)
    p.add_argument("--est-cost", type=float, default=1.0)
    p = sub.add_parser("plan")
    p.add_argument("--arms", nargs="+", default=["enforced", "activated-v3"])
    p.add_argument("--reps", type=int, default=5)
    p = sub.add_parser("mechanical")
    p.add_argument("--run-dir", type=Path, required=True)
    p = sub.add_parser("spent")
    p.add_argument("--date", required=True)
    args = ap.parse_args()
    if args.cmd == "plan":
        for i, (t, a, r) in enumerate(plan(args.arms, args.reps, load_tasks()), 1):
            print(f"{i:3d} {t:24s} {a:14s} rep-{r}")
    elif args.cmd == "campaign":
        campaign(args.date, args.arms, args.reps, args.concurrency, args.hard_cap, args.max_runs, args.est_cost)
    elif args.cmd == "mechanical":
        run_dir = args.run_dir.resolve()
        mechanical(run_dir)
        diff_patch(run_dir)
        governance(run_dir)
        task_id, arm = run_dir.parent.parent.name, run_dir.parent.name
        collect(run_dir, task_id, arm)
        print(json.dumps(json.loads((run_dir / "record.json").read_text())["mechanical"], indent=2))
    elif args.cmd == "spent":
        print(spent(args.date))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
