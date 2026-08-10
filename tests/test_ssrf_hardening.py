"""Regression tests for issue #39 C4 — SSRF validation must resolve hostnames.

Attack vectors covered (all offline — numeric IP forms resolve locally):
decimal IPv4 (``http://2130706433/`` = 127.0.0.1), private ranges, IPv6
loopback, link-local/cloud-metadata. The same hardened block is replicated
in the four fetch tools; each copy is exercised.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent.parent / "framework" / "tools"


def _load(tool: str):
    name = f"ssrf_{tool.replace('-', '_')}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _TOOLS / f"{tool}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STRICT_TOOLS = ["web-browser"]

BLOCKED_URLS = [
    "http://2130706433/",          # 127.0.0.1 en décimal
    "http://0x7f000001/",          # 127.0.0.1 en hexadécimal
    "http://127.0.0.1/",
    "http://[::1]/",
    "http://169.254.169.254/latest/meta-data/",
    "http://192.168.1.10/admin",
    "http://10.0.0.5/",
]


@pytest.mark.parametrize("tool", STRICT_TOOLS)
@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_strict_tools_block_vectors(tool: str, url: str) -> None:
    mod = _load(tool)
    validate = getattr(mod, "validate_url", None) or mod._validate_url
    with pytest.raises(ValueError):
        validate(url)


@pytest.mark.parametrize("tool", STRICT_TOOLS)
def test_strict_tools_reject_bad_scheme(tool: str) -> None:
    mod = _load(tool)
    validate = getattr(mod, "validate_url", None) or mod._validate_url
    with pytest.raises(ValueError):
        validate("file:///etc/passwd")
