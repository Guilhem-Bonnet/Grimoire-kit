#!/usr/bin/env python3
"""
vscode-terminal-prune.py
========================

Detecte et purge (optionnellement) les terminaux d'agents inactifs dans VS Code.

Strategie volontairement conservative:
- cible uniquement les shells `/usr/bin/zsh -f`
- ignore les shells recents
- ignore les shells avec processus enfant actif

Usage:
  python3 framework/tools/vscode-terminal-prune.py --list
    python3 framework/tools/vscode-terminal-prune.py --apply --min-idle-seconds 1800 --max-shells 4
    python3 framework/tools/vscode-terminal-prune.py --apply --json --report-file _grimoire-runtime-output/test-artifacts/vscode/terminal-prune.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

VSCODE_TERMINAL_PRUNE_VERSION = "1.1.1"


@dataclass(frozen=True, slots=True)
class ProcEntry:
    pid: int
    ppid: int
    etimes: int
    pcpu: float
    args: str


def _parse_process_table(raw: str) -> list[ProcEntry]:
    entries: list[ProcEntry] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 4)
        if len(parts) < 5:
            continue

        pid_s, ppid_s, etimes_s, pcpu_s, args = parts
        try:
            pid = int(pid_s)
            ppid = int(ppid_s)
            etimes = int(etimes_s)
            pcpu = float(pcpu_s.replace(",", "."))
        except ValueError:
            continue

        entries.append(ProcEntry(pid=pid, ppid=ppid, etimes=etimes, pcpu=pcpu, args=args))
    return entries


def _collect_processes() -> list[ProcEntry]:
    proc = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,etimes=,pcpu=,args="],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("Impossible de lire la table process via ps")
    return _parse_process_table(proc.stdout)


def _collect_pty_host_pids(entries: list[ProcEntry]) -> set[int]:
    direct_matches = {entry.pid for entry in entries if "pty-host" in entry.args}
    if direct_matches:
        return direct_matches

    try:
        proc = subprocess.run(
            ["code", "--status"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return set()

    if proc.returncode != 0:
        return set()

    raw = (proc.stdout or "") + (proc.stderr or "")
    pattern = re.compile(r"^\s*\S+\s+\S+\s+(?P<pid>\d+)\s+pty-host\b", re.MULTILINE)
    known_pids = {entry.pid for entry in entries}

    pty_hosts: set[int] = set()
    for match in pattern.finditer(raw):
        pid = int(match.group("pid"))
        if pid in known_pids:
            pty_hosts.add(pid)

    return pty_hosts


def _ancestor_pids(entries: list[ProcEntry], start_pid: int) -> set[int]:
    ppid_by_pid = {entry.pid: entry.ppid for entry in entries}
    protected = {start_pid}

    cursor = start_pid
    while cursor in ppid_by_pid:
        parent = ppid_by_pid[cursor]
        if parent <= 0 or parent in protected:
            break
        protected.add(parent)
        cursor = parent

    return protected


def _has_ancestor(
    ppid_by_pid: dict[int, int],
    start_pid: int,
    candidate_ancestors: set[int],
) -> bool:
    cursor = start_pid
    # Limite defensive: evite une boucle infinie sur table process incoherente.
    for _ in range(128):
        parent = ppid_by_pid.get(cursor)
        if parent is None or parent <= 0:
            return False
        if parent in candidate_ancestors:
            return True
        if parent == cursor:
            return False
        cursor = parent
    return False


def _find_idle_safe_shells(
    entries: list[ProcEntry],
    min_idle_seconds: int,
    max_cpu_percent: float,
) -> tuple[list[ProcEntry], int, int, int]:
    ppid_by_pid = {entry.pid: entry.ppid for entry in entries}
    children_count: dict[int, int] = {}
    for entry in entries:
        children_count[entry.ppid] = children_count.get(entry.ppid, 0) + 1

    protected = _ancestor_pids(entries, os.getpid())
    pty_hosts = _collect_pty_host_pids(entries)

    all_safe_shells = [entry for entry in entries if entry.args.startswith("/usr/bin/zsh -f")]
    safe_shells = [
        entry for entry in all_safe_shells if _has_ancestor(ppid_by_pid, entry.pid, pty_hosts)
    ]

    idle_candidates: list[ProcEntry] = []
    for entry in safe_shells:
        if entry.pid in protected:
            continue
        if entry.etimes < min_idle_seconds:
            continue
        if entry.pcpu > max_cpu_percent:
            continue
        if children_count.get(entry.pid, 0) > 0:
            continue
        idle_candidates.append(entry)

    return (
        sorted(idle_candidates, key=lambda item: item.etimes, reverse=True),
        len(safe_shells),
        len(all_safe_shells),
        len(pty_hosts),
    )


def _pick_shells_to_prune(
    candidates: list[ProcEntry],
    safe_shell_count: int,
    max_shells: int | None,
) -> list[ProcEntry]:
    if not candidates:
        return []

    if max_shells is None:
        return candidates

    excess = safe_shell_count - max_shells
    if excess <= 0:
        return []
    return candidates[:excess]


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Existence inconnue mais le process est bien present pour cet utilisateur.
        return True


def _terminate_pid(pid: int) -> tuple[bool, str]:
    def _wait_exit(timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not _pid_exists(pid):
                return True
            time.sleep(0.02)
        return not _pid_exists(pid)

    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return True, "already-exited"
        except PermissionError:
            return False, "permission-denied"
        except OSError as exc:
            return False, str(exc)

        if _wait_exit(0.25):
            return True, sig.name

    return (not _pid_exists(pid), "signal-escalation")


def _format_duration(seconds: int) -> str:
    mins, sec = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}h{mins:02d}m"
    return f"{mins}m{sec:02d}s"


def _emit_payload(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _write_report(payload: dict[str, object], report_file: str | None) -> None:
    if report_file is None:
        return
    report_path = Path(report_file).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vscode-terminal-prune",
        description="Detecte et purge les shells d'agents inactifs dans le pty-host VS Code.",
    )
    parser.add_argument("--list", action="store_true", help="Liste les shells inactifs sans les tuer (defaut).")
    parser.add_argument("--apply", action="store_true", help="Applique la purge des shells candidats.")
    parser.add_argument(
        "--min-idle-seconds",
        type=int,
        default=1800,
        help="Temps d'inactivite minimal en secondes (defaut: 1800).",
    )
    parser.add_argument(
        "--max-cpu-percent",
        type=float,
        default=0.2,
        help="CPU max pour considerer un shell inactif (defaut: 0.2).",
    )
    parser.add_argument(
        "--max-shells",
        type=int,
        default=4,
        help="Nombre max de shells zsh -f a conserver (defaut: 4).",
    )
    parser.add_argument("--json", action="store_true", help="Sortie machine-readable JSON.")
    parser.add_argument(
        "--report-file",
        type=str,
        default=None,
        help="Fichier de rapport JSON a ecrire (optionnel).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VSCODE_TERMINAL_PRUNE_VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list and args.apply:
        parser.error("--list et --apply sont mutuellement exclusifs")

    apply_mode = args.apply
    try:
        entries = _collect_processes()
    except RuntimeError as exc:
        print(f"Erreur: {exc}")
        return 1

    candidates, safe_shell_count, all_safe_shell_count, pty_host_count = _find_idle_safe_shells(
        entries,
        min_idle_seconds=max(0, args.min_idle_seconds),
        max_cpu_percent=max(0.0, args.max_cpu_percent),
    )
    to_prune = _pick_shells_to_prune(candidates, safe_shell_count, args.max_shells)

    payload: dict[str, object] = {
        "version": VSCODE_TERMINAL_PRUNE_VERSION,
        "mode": "apply" if apply_mode else "list",
        "pty_host_count": pty_host_count,
        "zsh_safe_total": all_safe_shell_count,
        "zsh_safe_attached_to_pty": safe_shell_count,
        "idle_candidates": [
            {
                "pid": entry.pid,
                "idle_seconds": entry.etimes,
                "cpu_percent": entry.pcpu,
                "cmd": entry.args,
            }
            for entry in candidates
        ],
        "planned_prune_count": len(to_prune),
    }

    if not args.json:
        print("VS Code Agent Terminal Prune")
        print("============================")
        print(f"pty-host total: {pty_host_count}")
        print(f"zsh -f total: {all_safe_shell_count}")
        print(f"zsh -f attached to pty-host: {safe_shell_count}")
        print(f"idle candidates: {len(candidates)}")
        print(f"target max shells: {args.max_shells}")

        if candidates:
            print("\nIdle candidates:")
            for entry in candidates[:20]:
                print(
                    f"  - pid={entry.pid} idle={_format_duration(entry.etimes)} "
                    f"cpu={entry.pcpu:.1f}% cmd={entry.args}"
                )

    if not apply_mode:
        payload["killed_pids"] = []
        payload["failures"] = []
        if not args.json:
            if to_prune:
                print(f"\nPrune preview: {len(to_prune)} shell(s) would be terminated.")
            else:
                print("\nNo prune needed.")

        _write_report(payload, args.report_file)
        _emit_payload(payload, args.json)
        return 0

    if not to_prune:
        payload["killed_pids"] = []
        payload["failures"] = []
        if not args.json:
            print("\nNo shell to terminate.")

        _write_report(payload, args.report_file)
        _emit_payload(payload, args.json)
        return 0

    killed: list[int] = []
    failures: list[str] = []
    for entry in to_prune:
        ok, details = _terminate_pid(entry.pid)
        if ok:
            killed.append(entry.pid)
        else:
            failures.append(f"pid={entry.pid}: {details}")

    if killed:
        if not args.json:
            print("\nTerminated shells:")
            for pid in killed:
                print(f"  - pid={pid}")

    payload["killed_pids"] = killed
    payload["failures"] = failures

    if failures:
        if not args.json:
            print("\nFailures:")
            for item in failures:
                print(f"  - {item}")

        _write_report(payload, args.report_file)
        _emit_payload(payload, args.json)
        return 1

    _write_report(payload, args.report_file)
    _emit_payload(payload, args.json)

    return 0


if __name__ == "__main__":
    sys.exit(main())