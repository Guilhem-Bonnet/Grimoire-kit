"""Regression tests for issue #33 — agent detection must survive Windows paths."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from grimoire.core.scaffold import _is_agent_markdown


class TestIsAgentMarkdown:
    def test_posix_agent_path(self) -> None:
        assert _is_agent_markdown(PurePosixPath("/proj/_grimoire/agents/dev.md"))

    def test_windows_agent_path(self) -> None:
        # str() of this path contains backslashes: the old substring check
        # ("/agents/" in str(path)) silently skipped every agent on Windows.
        assert _is_agent_markdown(PureWindowsPath(r"C:\proj\_grimoire\agents\dev.md"))

    def test_non_markdown_rejected(self) -> None:
        assert not _is_agent_markdown(PureWindowsPath(r"C:\proj\_grimoire\agents\dev.yaml"))

    def test_markdown_outside_agents_rejected(self) -> None:
        assert not _is_agent_markdown(PurePosixPath("/proj/_grimoire/workflows/dev.md"))
        assert not _is_agent_markdown(PureWindowsPath(r"C:\proj\docs\dev.md"))

    def test_github_agents_wrapper_path(self) -> None:
        assert _is_agent_markdown(PureWindowsPath(r"C:\proj\.github\agents\dev.agent.md"))
