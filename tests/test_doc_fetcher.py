#!/usr/bin/env python3
"""
Tests pour doc-fetcher.py — Indexation de documentation externe Grimoire.

Fonctions testées :
  - _validate_url()
  - extract_text_from_html()
  - _chunk_text()
  - _find_best_snippet()
  - load_doc_index() / save_doc_index()
  - search_docs()
  - format_list()
  - format_search_results()
  - build_parser()
"""

import importlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "doc-fetcher.py"


def _import_mod():
    """Import le module doc-fetcher via importlib."""
    mod_name = "doc_fetcher"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "doc-fetcher.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project(root: Path) -> Path:
    (root / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire-output").mkdir(parents=True, exist_ok=True)
    return root


# ── Tests SSRF Protection ────────────────────────────────────────────────────

class TestUrlValidation(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_valid_https(self):
        result = self.mod._validate_url("https://docs.python.org/3/")
        self.assertEqual(result, "https://docs.python.org/3/")

    def test_valid_http(self):
        result = self.mod._validate_url("http://example.com")
        self.assertEqual(result, "http://example.com")

    def test_reject_ftp(self):
        with self.assertRaises(ValueError):
            self.mod._validate_url("ftp://evil.com/file")

    def test_reject_file(self):
        with self.assertRaises(ValueError):
            self.mod._validate_url("file:///etc/passwd")

    def test_reject_metadata(self):
        with self.assertRaises(ValueError):
            self.mod._validate_url("http://169.254.169.254/latest/meta-data/")

    def test_reject_private_ip(self):
        with self.assertRaises(ValueError):
            self.mod._validate_url("http://192.168.1.1/admin")

    def test_reject_localhost(self):
        with self.assertRaises(ValueError):
            self.mod._validate_url("http://127.0.0.1:8080/secret")

    def test_reject_empty_host(self):
        with self.assertRaises(ValueError):
            self.mod._validate_url("http:///path")


# ── Tests HTML Parser ────────────────────────────────────────────────────────

class TestHtmlExtractor(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_basic_extraction(self):
        html = "<html><body><p>Hello world</p></body></html>"
        text, title = self.mod.extract_text_from_html(html)
        self.assertIn("Hello world", text)

    def test_title_extraction(self):
        html = "<html><head><title>My Page</title></head><body><p>Content</p></body></html>"
        text, title = self.mod.extract_text_from_html(html)
        self.assertEqual(title, "My Page")

    def test_skip_script(self):
        html = "<html><body><script>alert('xss')</script><p>Safe</p></body></html>"
        text, title = self.mod.extract_text_from_html(html)
        self.assertNotIn("alert", text)
        self.assertIn("Safe", text)

    def test_skip_style(self):
        html = "<html><body><style>.x{color:red}</style><p>Visible</p></body></html>"
        text, title = self.mod.extract_text_from_html(html)
        self.assertNotIn("color", text)
        self.assertIn("Visible", text)

    def test_headings_converted(self):
        html = "<html><body><h2>Section</h2><p>Text</p></body></html>"
        text, title = self.mod.extract_text_from_html(html)
        self.assertIn("##", text)
        self.assertIn("Section", text)

    def test_empty_html(self):
        text, title = self.mod.extract_text_from_html("")
        self.assertEqual(text, "")
        self.assertEqual(title, "")


# ── Tests Chunker ─────────────────────────────────────────────────────────────

class TestChunkText(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_short_text_single_chunk(self):
        chunks = self.mod._chunk_text("Hello world", max_chars=1000)
        self.assertEqual(len(chunks), 1)

    def test_long_text_multiple_chunks(self):
        text = "Paragraph one.\n\n" * 100
        chunks = self.mod._chunk_text(text, max_chars=200)
        self.assertGreater(len(chunks), 1)

    def test_empty_text(self):
        chunks = self.mod._chunk_text("")
        # May be empty or single empty chunk
        self.assertTrue(all(c == "" or c.strip() == "" for c in chunks) or len(chunks) == 0
                        or len(chunks) == 1)


# ── Tests Snippet Finder ──────────────────────────────────────────────────────

class TestFindBestSnippet(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_finds_matching_context(self):
        text = "Prefix text. " * 20 + "The pathlib module provides classes." + " Suffix text." * 20
        snippet = self.mod._find_best_snippet(text, {"pathlib"})
        self.assertIn("pathlib", snippet)

    def test_no_match_returns_start(self):
        text = "Some content here with no matching terms anywhere."
        snippet = self.mod._find_best_snippet(text, {"zzznotfound"})
        # Should still return something (from start)
        self.assertTrue(len(snippet) > 0)


# ── Tests Index Persistence ──────────────────────────────────────────────────

class TestDocIndexPersistence(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_empty(self):
        idx = self.mod.load_doc_index(self.tmpdir)
        self.assertEqual(len(idx.sources), 0)

    def test_save_and_load(self):
        idx = self.mod.DocIndex()
        source = self.mod.DocSource(
            name="python",
            base_url="https://docs.python.org/3/",
            paths=["library/pathlib.html"],
            last_refresh="2026-01-01",
            total_chunks=10,
        )
        source.pages.append(self.mod.DocPage(
            url="https://docs.python.org/3/library/pathlib.html",
            title="pathlib",
            text="",
            hash="abc123",
            fetched_at="2026-01-01",
            size=5000,
        ))
        idx.sources["python"] = source
        self.mod.save_doc_index(self.tmpdir, idx)

        loaded = self.mod.load_doc_index(self.tmpdir)
        self.assertIn("python", loaded.sources)
        self.assertEqual(len(loaded.sources["python"].pages), 1)
        self.assertEqual(loaded.sources["python"].total_chunks, 10)

    def test_corrupted_index(self):
        index_path = self.tmpdir / "_grimoire-output" / ".doc-index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("{broken", encoding="utf-8")
        idx = self.mod.load_doc_index(self.tmpdir)
        self.assertEqual(len(idx.sources), 0)


# ── Tests Search ──────────────────────────────────────────────────────────────

class TestSearchDocs(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        _make_project(self.tmpdir)

        # Setup a cached doc
        idx = self.mod.DocIndex()
        source = self.mod.DocSource(
            name="test-docs",
            base_url="https://example.com",
            total_chunks=5,
        )
        page = self.mod.DocPage(
            url="https://example.com/guide",
            title="Guide",
            text="",
            hash="xxx",
            fetched_at="2026-01-01",
            size=500,
        )
        source.pages.append(page)
        idx.sources["test-docs"] = source
        self.mod.save_doc_index(self.tmpdir, idx)

        # Write cache file
        cache_dir = self.tmpdir / "_grimoire-output" / ".doc-cache" / "test-docs"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{page.id}.md"
        cache_file.write_text(
            "# Guide\n\nThis guide explains how to use pathlib for file operations.\n"
            "The Path class provides methods like exists(), iterdir(), and glob().\n",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_search_finds_result(self):
        results = self.mod.search_docs(self.tmpdir, "pathlib Path")
        self.assertGreater(len(results), 0)
        self.assertIn("test-docs", results[0]["source"])

    def test_search_no_results(self):
        results = self.mod.search_docs(self.tmpdir, "zzznonexistent")
        self.assertEqual(len(results), 0)

    def test_search_with_source_filter(self):
        results = self.mod.search_docs(self.tmpdir, "pathlib", source_filter="nonexistent")
        self.assertEqual(len(results), 0)


# ── Tests Format ──────────────────────────────────────────────────────────────

class TestFormat(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_format_empty_list(self):
        idx = self.mod.DocIndex()
        output = self.mod.format_list(idx)
        self.assertIn("Aucune source", output)

    def test_format_list_with_source(self):
        idx = self.mod.DocIndex()
        idx.sources["python"] = self.mod.DocSource(
            name="python",
            base_url="https://docs.python.org/3/",
            total_chunks=10,
        )
        output = self.mod.format_list(idx)
        self.assertIn("python", output)
        self.assertIn("docs.python.org", output)

    def test_format_list_json(self):
        idx = self.mod.DocIndex()
        idx.sources["test"] = self.mod.DocSource(
            name="test", base_url="https://example.com")
        output = self.mod.format_list(idx, as_json=True)
        data = json.loads(output)
        self.assertIn("test", data)

    def test_format_search_empty(self):
        output = self.mod.format_search_results([])
        self.assertIn("Aucun résultat", output)

    def test_format_search_results(self):
        results = [{"source": "test", "url": "https://example.com",
                     "title": "Guide", "score": 0.5, "snippet": "Some text"}]
        output = self.mod.format_search_results(results)
        self.assertIn("Guide", output)
        self.assertIn("example.com", output)

    def test_format_search_json(self):
        results = [{"source": "test", "url": "https://example.com",
                     "title": "Guide", "score": 0.5, "snippet": "x"}]
        output = self.mod.format_search_results(results, as_json=True)
        data = json.loads(output)
        self.assertEqual(len(data), 1)


# ── Tests CLI ─────────────────────────────────────────────────────────────────

class TestCli(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_build_parser(self):
        parser = self.mod.build_parser()
        self.assertIsNotNone(parser)

    def test_version(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--version"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertIn("doc-fetcher", result.stdout)

    def test_no_command(self):
        result = subprocess.run(
            [sys.executable, str(TOOL)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
