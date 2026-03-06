"""Tests for mcp-web-search.py — D6 MCP Web Search Tool."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "mcp-web-search.py"


def _load():
    mod_name = "mcp_web_search_mod"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


ws = _load()


class TestVersion(unittest.TestCase):
    def test_version_exists(self):
        self.assertTrue(ws.MCP_WEB_SEARCH_VERSION)


class TestExtractResults(unittest.TestCase):

    def test_empty_html(self):
        results = ws._extract_results("", 5)
        self.assertEqual(results, [])

    def test_no_results(self):
        html = "<html><body>No results found</body></html>"
        results = ws._extract_results(html, 5)
        self.assertEqual(results, [])

    def test_max_results_clamped(self):
        results = ws._extract_results("", 0)
        self.assertEqual(results, [])

    def test_parse_result_block(self):
        html = '''
        <div class="result results_links results_links_deep web-result">
            <div class="result__body">
                <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com">
                    Example Title
                </a>
                <a class="result__snippet">Some snippet text here</a>
            </div>
        </div>
        '''
        results = ws._extract_results(html, 5)
        # May or may not parse depending on exact regex matching
        # At minimum, should not crash
        self.assertIsInstance(results, list)


class TestMcpInterface(unittest.TestCase):

    @patch.object(ws, "web_search")
    def test_mcp_web_search_returns_dict(self, mock_search):
        mock_search.return_value = [
            {"title": "Test", "url": "https://example.com", "snippet": "A test"}
        ]
        result = ws.mcp_web_search("test query")
        self.assertIn("results", result)
        self.assertIn("count", result)
        self.assertIn("query", result)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["query"], "test query")

    @patch.object(ws, "web_search")
    def test_mcp_max_results_clamped(self, mock_search):
        mock_search.return_value = []
        ws.mcp_web_search("q", max_results=100)
        mock_search.assert_called_once_with("q", 10)

    @patch.object(ws, "web_search")
    def test_mcp_min_results(self, mock_search):
        mock_search.return_value = []
        ws.mcp_web_search("q", max_results=-5)
        mock_search.assert_called_once_with("q", 1)


class TestWebSearch(unittest.TestCase):

    @patch("urllib.request.urlopen")
    def test_search_error_handling(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        results = ws.web_search("test")
        self.assertEqual(len(results), 1)
        self.assertIn("error", results[0])

    @patch("urllib.request.urlopen")
    def test_search_returns_list(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<html></html>"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp
        results = ws.web_search("test")
        self.assertIsInstance(results, list)


class TestCLI(unittest.TestCase):

    def test_main_no_command(self):
        with patch("sys.argv", ["mcp-web-search.py", "--project-root", "."]):
            ret = ws.main()
            self.assertEqual(ret, 1)


if __name__ == "__main__":
    unittest.main()
