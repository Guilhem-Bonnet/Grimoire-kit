"""Tests for vscode-terminal-prune.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# Import module with hyphenated filename.
_TOOL_PATH = (
    Path(__file__).resolve().parent.parent / "framework" / "tools" / "vscode-terminal-prune.py"
)
_spec = importlib.util.spec_from_file_location("vscode_terminal_prune", _TOOL_PATH)
vtp = importlib.util.module_from_spec(_spec)
sys.modules["vscode_terminal_prune"] = vtp
_spec.loader.exec_module(vtp)

ProcEntry = vtp.ProcEntry


class TestParseProcessTable:
    def test_empty_string(self):
        assert vtp._parse_process_table("") == []

    def test_single_valid_line(self):
        raw = "1234  567  3600  0.5  /usr/bin/zsh -f"
        entries = vtp._parse_process_table(raw)
        assert len(entries) == 1
        assert entries[0].pid == 1234
        assert entries[0].ppid == 567
        assert entries[0].etimes == 3600
        assert entries[0].pcpu == 0.5
        assert entries[0].args == "/usr/bin/zsh -f"

    def test_skips_blank_lines(self):
        raw = "\n  \n1234  567  3600  0.0  test\n\n"
        entries = vtp._parse_process_table(raw)
        assert len(entries) == 1

    def test_skips_malformed_less_than_5_parts(self):
        raw = "1234  567  3600  malformed"
        entries = vtp._parse_process_table(raw)
        assert entries == []

    def test_skips_invalid_int(self):
        raw = "bad  567  3600  0.0  /usr/bin/zsh -f"
        entries = vtp._parse_process_table(raw)
        assert entries == []

    def test_comma_decimal_pcpu(self):
        """French locale may use comma as decimal separator."""
        raw = "100  1  3600  1,5  /usr/bin/zsh -f"
        entries = vtp._parse_process_table(raw)
        assert len(entries) == 1
        assert entries[0].pcpu == 1.5

    def test_multiple_lines(self):
        raw = (
            "100  1  3600  0.0  /usr/bin/zsh -f\n"
            "200  1  7200  0.1  pty-host\n"
        )
        entries = vtp._parse_process_table(raw)
        assert len(entries) == 2
        assert entries[0].pid == 100
        assert entries[1].pid == 200


class TestFormatDuration:
    def test_zero(self):
        assert vtp._format_duration(0) == "0m00s"

    def test_under_one_minute(self):
        assert vtp._format_duration(45) == "0m45s"

    def test_exact_minute(self):
        assert vtp._format_duration(60) == "1m00s"

    def test_minutes_and_seconds(self):
        assert vtp._format_duration(90) == "1m30s"

    def test_exact_hour(self):
        assert vtp._format_duration(3600) == "1h00m"

    def test_hours_and_minutes(self):
        assert vtp._format_duration(3665) == "1h01m"


class TestAncestorPids:
    def test_includes_start_pid(self):
        entries = [ProcEntry(pid=100, ppid=50, etimes=0, pcpu=0.0, args="test")]
        result = vtp._ancestor_pids(entries, 100)
        assert 100 in result

    def test_traverses_chain(self):
        entries = [
            ProcEntry(pid=300, ppid=200, etimes=0, pcpu=0.0, args="a"),
            ProcEntry(pid=200, ppid=100, etimes=0, pcpu=0.0, args="b"),
            ProcEntry(pid=100, ppid=1, etimes=0, pcpu=0.0, args="c"),
        ]
        result = vtp._ancestor_pids(entries, 300)
        assert {100, 200, 300} <= result

    def test_empty_process_table(self):
        result = vtp._ancestor_pids([], 100)
        assert 100 in result


class TestHasAncestor:
    def test_direct_parent(self):
        ppid_by_pid = {200: 100}
        assert vtp._has_ancestor(ppid_by_pid, 200, {100})

    def test_grandparent(self):
        ppid_by_pid = {300: 200, 200: 100}
        assert vtp._has_ancestor(ppid_by_pid, 300, {100})

    def test_no_match(self):
        ppid_by_pid = {300: 200, 200: 100}
        assert not vtp._has_ancestor(ppid_by_pid, 300, {999})

    def test_no_parent_in_table(self):
        ppid_by_pid = {}
        assert not vtp._has_ancestor(ppid_by_pid, 300, {100})

    def test_self_loop_exits_cleanly(self):
        ppid_by_pid = {100: 100}
        assert not vtp._has_ancestor(ppid_by_pid, 100, {99})


class TestPickShellsToPrune:
    def test_empty_candidates(self):
        assert vtp._pick_shells_to_prune([], 2, 4) == []

    def test_no_max_returns_all(self):
        c = [ProcEntry(pid=1, ppid=0, etimes=3600, pcpu=0.0, args="test")]
        assert vtp._pick_shells_to_prune(c, 5, None) == c

    def test_within_limit_returns_empty(self):
        c = [ProcEntry(pid=1, ppid=0, etimes=3600, pcpu=0.0, args="test")]
        assert vtp._pick_shells_to_prune(c, 3, 4) == []

    def test_exact_limit_returns_empty(self):
        c = [ProcEntry(pid=1, ppid=0, etimes=3600, pcpu=0.0, args="test")]
        assert vtp._pick_shells_to_prune(c, 4, 4) == []

    def test_excess_returns_first_n(self):
        entries = [
            ProcEntry(pid=i, ppid=0, etimes=3600 * i, pcpu=0.0, args="test")
            for i in range(1, 7)
        ]
        # 6 shells, max 4 → prune 2
        result = vtp._pick_shells_to_prune(entries, 6, 4)
        assert len(result) == 2


class TestFindIdleSafeShells:
    def test_no_pty_host(self, monkeypatch):
        monkeypatch.setattr(vtp.os, "getpid", lambda: 9999)
        entries = [
            ProcEntry(pid=100, ppid=1, etimes=3600, pcpu=0.0, args="/usr/bin/zsh -f"),
        ]
        candidates, safe_count, all_count, pty_count = vtp._find_idle_safe_shells(
            entries, min_idle_seconds=60, max_cpu_percent=0.2
        )
        assert candidates == []
        assert pty_count == 0
        assert all_count == 1
        assert safe_count == 0

    def test_shell_too_fresh(self, monkeypatch):
        monkeypatch.setattr(vtp.os, "getpid", lambda: 9999)
        entries = [
            ProcEntry(pid=1, ppid=0, etimes=7200, pcpu=0.0, args="pty-host"),
            ProcEntry(pid=100, ppid=1, etimes=30, pcpu=0.0, args="/usr/bin/zsh -f"),
        ]
        candidates, _, _, _ = vtp._find_idle_safe_shells(
            entries, min_idle_seconds=60, max_cpu_percent=0.2
        )
        assert candidates == []

    def test_idle_shell_returned(self, monkeypatch):
        monkeypatch.setattr(vtp.os, "getpid", lambda: 9999)
        entries = [
            ProcEntry(pid=1, ppid=0, etimes=7200, pcpu=0.0, args="pty-host"),
            ProcEntry(pid=100, ppid=1, etimes=3600, pcpu=0.0, args="/usr/bin/zsh -f"),
        ]
        candidates, safe_count, all_count, pty_count = vtp._find_idle_safe_shells(
            entries, min_idle_seconds=60, max_cpu_percent=0.2
        )
        assert len(candidates) == 1
        assert candidates[0].pid == 100
        assert safe_count == 1
        assert all_count == 1
        assert pty_count == 1

    def test_shell_with_children_excluded(self, monkeypatch):
        monkeypatch.setattr(vtp.os, "getpid", lambda: 9999)
        entries = [
            ProcEntry(pid=1, ppid=0, etimes=7200, pcpu=0.0, args="pty-host"),
            ProcEntry(pid=100, ppid=1, etimes=3600, pcpu=0.0, args="/usr/bin/zsh -f"),
            ProcEntry(pid=101, ppid=100, etimes=600, pcpu=0.0, args="some-child"),
        ]
        candidates, _, _, _ = vtp._find_idle_safe_shells(
            entries, min_idle_seconds=60, max_cpu_percent=0.2
        )
        assert candidates == []

    def test_high_cpu_excluded(self, monkeypatch):
        monkeypatch.setattr(vtp.os, "getpid", lambda: 9999)
        entries = [
            ProcEntry(pid=1, ppid=0, etimes=7200, pcpu=0.0, args="pty-host"),
            ProcEntry(pid=100, ppid=1, etimes=3600, pcpu=5.0, args="/usr/bin/zsh -f"),
        ]
        candidates, _, _, _ = vtp._find_idle_safe_shells(
            entries, min_idle_seconds=60, max_cpu_percent=0.2
        )
        assert candidates == []

    def test_sorted_by_etimes_descending(self, monkeypatch):
        monkeypatch.setattr(vtp.os, "getpid", lambda: 9999)
        entries = [
            ProcEntry(pid=1, ppid=0, etimes=7200, pcpu=0.0, args="pty-host"),
            ProcEntry(pid=100, ppid=1, etimes=1800, pcpu=0.0, args="/usr/bin/zsh -f"),
            ProcEntry(pid=200, ppid=1, etimes=3600, pcpu=0.0, args="/usr/bin/zsh -f"),
        ]
        candidates, _, _, _ = vtp._find_idle_safe_shells(
            entries, min_idle_seconds=60, max_cpu_percent=0.2
        )
        assert len(candidates) == 2
        assert candidates[0].pid == 200
        assert candidates[1].pid == 100


class TestWriteReport:
    def test_creates_report_file(self, tmp_path):
        payload = {"version": "1.0", "mode": "list"}
        report_file = str(tmp_path / "sub" / "report.json")
        vtp._write_report(payload, report_file)
        content = json.loads(Path(report_file).read_text(encoding="utf-8"))
        assert content == payload

    def test_noop_when_none(self):
        vtp._write_report({"test": True}, None)  # Must not raise


class TestBuildParser:
    def test_defaults(self):
        args = vtp.build_parser().parse_args([])
        assert args.min_idle_seconds == 1800
        assert args.max_cpu_percent == 0.2
        assert args.max_shells == 4
        assert not args.apply
        assert not args.list
        assert not args.json

    def test_apply_flag(self):
        args = vtp.build_parser().parse_args(["--apply"])
        assert args.apply

    def test_min_idle_custom(self):
        args = vtp.build_parser().parse_args(["--min-idle-seconds", "600"])
        assert args.min_idle_seconds == 600

    def test_report_file(self):
        args = vtp.build_parser().parse_args(["--report-file", "/tmp/report.json"])
        assert args.report_file == "/tmp/report.json"


class TestMain:
    def test_list_mode_no_processes(self, monkeypatch):
        monkeypatch.setattr(vtp, "_collect_processes", list)
        assert vtp.main(["--list"]) == 0

    def test_apply_mode_no_processes(self, monkeypatch):
        monkeypatch.setattr(vtp, "_collect_processes", list)
        assert vtp.main(["--apply"]) == 0

    def test_default_mode_no_processes(self, monkeypatch):
        monkeypatch.setattr(vtp, "_collect_processes", list)
        assert vtp.main([]) == 0

    def test_collect_error_returns_1(self, monkeypatch):
        def fail():
            raise RuntimeError("ps failed")

        monkeypatch.setattr(vtp, "_collect_processes", fail)
        assert vtp.main([]) == 1

    def test_mutually_exclusive_flags(self):
        with pytest.raises(SystemExit) as exc_info:
            vtp.main(["--list", "--apply"])
        assert exc_info.value.code != 0

    def test_json_list_output(self, monkeypatch, capsys):
        monkeypatch.setattr(vtp, "_collect_processes", list)
        assert vtp.main(["--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["mode"] == "list"
        assert data["idle_candidates"] == []

    def test_json_apply_output(self, monkeypatch, capsys):
        monkeypatch.setattr(vtp, "_collect_processes", list)
        assert vtp.main(["--apply", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["mode"] == "apply"
        assert data["killed_pids"] == []

    def test_report_file_written(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vtp, "_collect_processes", list)
        report_path = str(tmp_path / "report.json")
        assert vtp.main(["--report-file", report_path]) == 0
        data = json.loads(Path(report_path).read_text(encoding="utf-8"))
        assert "version" in data
        assert "mode" in data
