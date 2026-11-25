"""AcpClient.close() : stdin se ferme sans trace quand l'agent est déjà sorti (#1313)."""

from __future__ import annotations

import gc
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.acp import AcpClient


def _client_with_exited_agent(tmp_path: Path) -> AcpClient:
    client = AcpClient((sys.executable, "-c", "pass"), tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    proc.wait()
    assert proc.stdin is not None
    proc.stdin.write("{}")  # reste dans le tampon : le flush de close() cassera le tube
    client._proc = proc
    return client


@pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
def test_close_after_agent_exit_leaves_no_trace(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    client = _client_with_exited_agent(tmp_path)
    proc = client._proc
    assert proc is not None
    client.close()
    assert proc.stdin is not None and proc.stdin.closed
    del proc, client
    gc.collect()
    assert capfd.readouterr().err == ""


@pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
def test_failed_start_closes_stdin_without_trace(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    client = AcpClient((sys.executable, "-c", "pass"), tmp_path, request_timeout=5)
    with pytest.raises(Exception), client:  # noqa: B017 - l'agent sort avant `initialize`
        pass
    assert client._proc is None
    del client
    gc.collect()
    assert capfd.readouterr().err == ""
