"""Tests for mcp-proxy.py — D14 MCP Proxy."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "mcp-proxy.py"


def _load():
    mod_name = "mcp_proxy_mod"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


mp = _load()


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertTrue(mp.MCP_PROXY_VERSION)


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_default_config(self):
        config = mp._load_config(Path(self.tmpdir))
        self.assertIn("servers", config)
        self.assertTrue(len(config["servers"]) > 0)

    def test_save_and_load(self):
        root = Path(self.tmpdir)
        config = {"servers": [{"name": "test", "command": "echo", "enabled": True}]}
        mp._save_config(root, config)
        loaded = mp._load_config(root)
        self.assertEqual(len(loaded["servers"]), 1)
        self.assertEqual(loaded["servers"][0]["name"], "test")


class TestListServers(unittest.TestCase):
    def test_default_servers(self):
        tmpdir = tempfile.mkdtemp()
        servers = mp.list_servers(Path(tmpdir))
        self.assertIsInstance(servers, list)
        self.assertTrue(len(servers) > 0)


class TestCheckServerStatus(unittest.TestCase):
    def test_disabled_server(self):
        server = {"name": "test", "command": "echo", "enabled": False}
        result = mp.check_server_status(server)
        self.assertEqual(result["status"], "disabled")
        self.assertFalse(result["available"])

    def test_no_command(self):
        server = {"name": "test", "command": "", "enabled": True}
        result = mp.check_server_status(server)
        self.assertFalse(result["available"])

    @patch("shutil.which", return_value="/usr/bin/echo")
    def test_available_server(self, mock_which):
        server = {"name": "test", "command": "echo hello", "enabled": True}
        result = mp.check_server_status(server)
        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "available")

    @patch("shutil.which", return_value=None)
    def test_unavailable_server(self, mock_which):
        server = {"name": "test", "command": "nonexistent-cmd", "enabled": True}
        result = mp.check_server_status(server)
        self.assertFalse(result["available"])


class TestMcpInterface(unittest.TestCase):
    def test_mcp_list(self):
        tmpdir = tempfile.mkdtemp()
        result = mp.mcp_proxy_list(str(tmpdir))
        self.assertIn("servers", result)
        self.assertIn("count", result)

    def test_mcp_status(self):
        tmpdir = tempfile.mkdtemp()
        result = mp.mcp_proxy_status(str(tmpdir))
        self.assertIn("status", result)
        self.assertIn("total", result)


if __name__ == "__main__":
    unittest.main()
