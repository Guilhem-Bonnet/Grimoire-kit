"""Tests for docs-fetcher.py — Indexation de documentation externe Grimoire."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "framework" / "tools"
sys.path.insert(0, str(TOOLS))

_spec = importlib.util.spec_from_file_location("docs_fetcher", TOOLS / "docs-fetcher.py")
df = importlib.util.module_from_spec(_spec)
sys.modules["docs_fetcher"] = df
_spec.loader.exec_module(df)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path):
    (tmp_path / "_grimoire-output").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def index(tmp_project):
    return df.DocsIndex(tmp_project)


@pytest.fixture
def sample_html():
    return """<!DOCTYPE html>
<html>
<head><title>Test Docs</title></head>
<body>
<nav>Navigation links</nav>
<h1>Main Title</h1>
<p>Introduction paragraph with some content.</p>
<h2>Section One</h2>
<p>Content of section one with <strong>bold</strong> and <em>italic</em>.</p>
<pre><code>def hello():
    print("world")</code></pre>
<h2>Section Two</h2>
<p>Content of section two with a <a href="https://example.com">link</a>.</p>
<ul>
<li>Item one</li>
<li>Item two</li>
</ul>
<script>console.log("removed");</script>
<footer>Footer content</footer>
</body>
</html>"""


@pytest.fixture
def sample_markdown():
    return """# Main Title

Introduction paragraph.

## Section One

Content of section one with some details here that make the chunk long enough to pass the threshold.

## Section Two

Content of section two with even more details here that make the chunk definitely long enough.
"""


# ── Constants ────────────────────────────────────────────────────────────────


class TestConstants:
    def test_version_format(self):
        parts = df.DOCS_FETCHER_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_max_page_size(self):
        assert df.MAX_PAGE_SIZE == 5 * 1024 * 1024

    def test_allowed_schemes(self):
        assert "http" in df._ALLOWED_SCHEMES
        assert "https" in df._ALLOWED_SCHEMES
        assert "ftp" not in df._ALLOWED_SCHEMES

    def test_blocked_hosts(self):
        assert "169.254.169.254" in df._BLOCKED_HOSTS


# ── URL Validation ───────────────────────────────────────────────────────────


class TestValidateUrl:
    def test_valid_https(self):
        assert df.validate_url("https://docs.python.org/3/") == "https://docs.python.org/3/"

    def test_valid_http(self):
        assert df.validate_url("http://example.com") == "http://example.com"

    def test_rejects_ftp(self):
        with pytest.raises(ValueError, match="non autorisé"):
            df.validate_url("ftp://example.com/file")

    def test_rejects_file_scheme(self):
        with pytest.raises(ValueError, match="non autorisé"):
            df.validate_url("file:///etc/passwd")

    def test_rejects_cloud_metadata(self):
        with pytest.raises(ValueError, match="cloud metadata"):
            df.validate_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_google_metadata(self):
        with pytest.raises(ValueError, match="cloud metadata"):
            df.validate_url("http://metadata.google.internal/computeMetadata/v1/")

    def test_rejects_private_ip(self):
        with pytest.raises(ValueError, match="IP privée"):
            df.validate_url("http://192.168.1.1/admin")

    def test_rejects_localhost(self):
        with pytest.raises(ValueError, match="IP privée"):
            df.validate_url("http://127.0.0.1:8080/")

    def test_rejects_10_prefix(self):
        with pytest.raises(ValueError, match="IP privée"):
            df.validate_url("http://10.0.0.1/")

    def test_rejects_empty_host(self):
        with pytest.raises(ValueError, match="sans hostname"):
            df.validate_url("https:///path")

    def test_rejects_javascript_scheme(self):
        with pytest.raises(ValueError, match="non autorisé"):
            df.validate_url("javascript:alert(1)")


# ── Slug Generation ─────────────────────────────────────────────────────────


class TestMakeSlug:
    def test_simple(self):
        assert df.make_slug("Python AST") == "python-ast"

    def test_special_chars(self):
        assert df.make_slug("docs/lib.html") == "docs-lib-html"

    def test_trailing_dashes(self):
        assert df.make_slug("---hello---") == "hello"

    def test_empty_string(self):
        assert df.make_slug("") == "unnamed"

    def test_max_length(self):
        long_name = "a" * 200
        assert len(df.make_slug(long_name)) <= 80

    def test_unicode(self):
        slug = df.make_slug("café résumé")
        assert slug  # Non-empty


# ── HTML to Markdown ─────────────────────────────────────────────────────────


class TestHtmlToMarkdown:
    def test_headings(self, sample_html):
        md = df.html_to_markdown(sample_html)
        assert "# Main Title" in md
        assert "## Section One" in md
        assert "## Section Two" in md

    def test_removes_script(self, sample_html):
        md = df.html_to_markdown(sample_html)
        assert "console.log" not in md

    def test_removes_nav(self, sample_html):
        md = df.html_to_markdown(sample_html)
        assert "Navigation links" not in md

    def test_removes_footer(self, sample_html):
        md = df.html_to_markdown(sample_html)
        assert "Footer content" not in md

    def test_bold(self, sample_html):
        md = df.html_to_markdown(sample_html)
        assert "**bold**" in md

    def test_italic(self, sample_html):
        md = df.html_to_markdown(sample_html)
        assert "*italic*" in md

    def test_code_block(self, sample_html):
        md = df.html_to_markdown(sample_html)
        assert "```" in md
        assert "def hello():" in md

    def test_links(self, sample_html):
        md = df.html_to_markdown(sample_html)
        assert "[link](https://example.com)" in md

    def test_lists(self, sample_html):
        md = df.html_to_markdown(sample_html)
        assert "- Item one" in md

    def test_entities_decoded(self):
        md = df.html_to_markdown("<p>A &amp; B &lt; C</p>")
        assert "A & B < C" in md

    def test_strips_remaining_tags(self):
        md = df.html_to_markdown("<div><span>hello</span></div>")
        assert "<" not in md
        assert "hello" in md

    def test_empty_input(self):
        assert df.html_to_markdown("") == ""

    def test_plain_text_passthrough(self):
        md = df.html_to_markdown("Just plain text, no HTML")
        assert "Just plain text" in md


# ── Chunking ─────────────────────────────────────────────────────────────────


class TestChunkMarkdown:
    def test_splits_by_heading(self, sample_markdown):
        chunks = df.chunk_markdown(sample_markdown, "https://example.com", "Test")
        assert len(chunks) >= 2  # At least 2 sections

    def test_chunk_has_heading(self, sample_markdown):
        chunks = df.chunk_markdown(sample_markdown, "https://example.com", "Test")
        headings = [c.heading for c in chunks]
        assert any("Section" in h for h in headings)

    def test_chunk_has_source(self, sample_markdown):
        chunks = df.chunk_markdown(sample_markdown, "https://example.com", "Test")
        assert all(c.source_url == "https://example.com" for c in chunks)
        assert all(c.source_name == "Test" for c in chunks)

    def test_chunk_has_tokens_estimate(self, sample_markdown):
        chunks = df.chunk_markdown(sample_markdown, "https://example.com", "Test")
        for c in chunks:
            assert c.estimated_tokens > 0

    def test_empty_content_no_chunks(self):
        chunks = df.chunk_markdown("", "url", "name")
        assert len(chunks) == 0

    def test_short_content_single_chunk(self):
        # Content must be > 30 chars for a chunk
        content = "# Title\n\n" + "x" * 50
        chunks = df.chunk_markdown(content, "url", "name")
        assert len(chunks) >= 1

    def test_large_content_split(self):
        # Multiple headings force multiple chunks
        content = "# Section A\n\n" + "word " * 200 + "\n\n## Section B\n\n" + "more " * 200
        chunks = df.chunk_markdown(content, "url", "name")
        assert len(chunks) > 1

    def test_fallback_no_headings(self):
        content = "Just a long paragraph " * 20
        chunks = df.chunk_markdown(content, "url", "name")
        assert len(chunks) >= 1
        assert chunks[0].source_name == "name"

    def test_to_dict(self, sample_markdown):
        chunks = df.chunk_markdown(sample_markdown, "https://example.com", "Test")
        for c in chunks:
            d = c.to_dict()
            assert "text" in d
            assert "source_url" in d
            assert "heading" in d


# ── DocSource ────────────────────────────────────────────────────────────────


class TestDocSource:
    def test_to_dict(self):
        src = df.DocSource(name="Test", url="https://test.com", slug="test")
        d = src.to_dict()
        assert d["name"] == "Test"
        assert d["url"] == "https://test.com"
        assert d["slug"] == "test"


# ── FetchResult ──────────────────────────────────────────────────────────────


class TestFetchResult:
    def test_error_result(self):
        r = df.FetchResult(
            source=df.DocSource(name="x", url="u", slug="x"),
            error="timeout",
        )
        d = r.to_dict()
        assert d["error"] == "timeout"
        assert d["chunks_count"] == 0

    def test_success_result(self):
        chunk = df.DocChunk(text="hello", source_url="u", source_name="n")
        r = df.FetchResult(
            source=df.DocSource(name="n", url="u", slug="n", chunks_count=1),
            chunks=[chunk],
        )
        d = r.to_dict()
        assert d["chunks_count"] == 1
        assert d["error"] == ""


# ── DocsIndex ────────────────────────────────────────────────────────────────


class TestDocsIndex:
    def test_empty_index(self, index):
        assert index.list_sources() == []

    def test_store_and_list(self, index):
        chunk = df.DocChunk(
            text="Hello world content that is long enough to be meaningful here",
            source_url="https://test.com",
            source_name="Test",
            heading="Title",
        )
        result = df.FetchResult(
            source=df.DocSource(
                name="Test", url="https://test.com", slug="test",
                chunks_count=1, fetched_at="2026-01-01", content_hash="abc", size_bytes=100,
            ),
            chunks=[chunk],
        )
        index.store(result)
        sources = index.list_sources()
        assert len(sources) == 1
        assert sources[0].name == "Test"

    def test_store_creates_cache_files(self, index, tmp_project):
        chunk = df.DocChunk(text="content " * 10, source_url="u", source_name="n")
        result = df.FetchResult(
            source=df.DocSource(name="n", url="u", slug="test-doc",
                                chunks_count=1, fetched_at="now", content_hash="h", size_bytes=50),
            chunks=[chunk],
        )
        index.store(result)
        assert (tmp_project / df.CACHE_DIR / "test-doc.md").exists()
        assert (tmp_project / df.CACHE_DIR / "test-doc.chunks.json").exists()

    def test_store_error_result_no_op(self, index):
        result = df.FetchResult(
            source=df.DocSource(name="x", url="u", slug="x"),
            error="fail",
        )
        index.store(result)
        assert index.list_sources() == []

    def test_remove(self, index, tmp_project):
        chunk = df.DocChunk(text="content " * 10, source_url="u", source_name="n")
        result = df.FetchResult(
            source=df.DocSource(name="n", url="u", slug="to-remove",
                                chunks_count=1, fetched_at="now", content_hash="h", size_bytes=50),
            chunks=[chunk],
        )
        index.store(result)
        assert index.remove("to-remove") is True
        assert index.list_sources() == []
        assert not (tmp_project / df.CACHE_DIR / "to-remove.md").exists()

    def test_remove_nonexistent(self, index):
        assert index.remove("nope") is False

    def test_search(self, index):
        chunk = df.DocChunk(text="Python ast module usage guide content here",
                            source_url="u", source_name="n", heading="AST")
        result = df.FetchResult(
            source=df.DocSource(name="n", url="u", slug="search-test",
                                chunks_count=1, fetched_at="now", content_hash="h", size_bytes=50),
            chunks=[chunk],
        )
        index.store(result)
        found = index.search("ast module")
        assert len(found) >= 1
        assert "ast" in found[0].text.lower()

    def test_search_no_match(self, index):
        found = index.search("nonexistent query xyz")
        assert found == []

    def test_status(self, index):
        chunk = df.DocChunk(text="content " * 10, source_url="u", source_name="n")
        result = df.FetchResult(
            source=df.DocSource(name="n", url="u", slug="stat-test",
                                chunks_count=3, fetched_at="now", content_hash="h", size_bytes=200),
            chunks=[chunk],
        )
        index.store(result)
        report = index.status()
        assert report.total_chunks == 3
        assert report.total_size_bytes == 200
        assert len(report.sources) == 1

    def test_persistence(self, tmp_project):
        # Write with one index, read with another
        idx1 = df.DocsIndex(tmp_project)
        chunk = df.DocChunk(text="persist " * 10, source_url="u", source_name="n")
        result = df.FetchResult(
            source=df.DocSource(name="Persist", url="u", slug="persist",
                                chunks_count=1, fetched_at="now", content_hash="h", size_bytes=50),
            chunks=[chunk],
        )
        idx1.store(result)

        idx2 = df.DocsIndex(tmp_project)
        sources = idx2.list_sources()
        assert len(sources) == 1
        assert sources[0].name == "Persist"

    def test_needs_refresh(self, index):
        chunk = df.DocChunk(text="content " * 10, source_url="u", source_name="n")
        result = df.FetchResult(
            source=df.DocSource(name="n", url="u", slug="refresh-test",
                                chunks_count=1, fetched_at="now", content_hash="abc123", size_bytes=50),
            chunks=[chunk],
        )
        index.store(result)
        assert index.needs_refresh("refresh-test", "abc123") is False
        assert index.needs_refresh("refresh-test", "different") is True
        assert index.needs_refresh("nonexistent", "any") is True

    def test_get(self, index):
        chunk = df.DocChunk(text="content " * 10, source_url="u", source_name="n")
        result = df.FetchResult(
            source=df.DocSource(name="Get Me", url="u", slug="get-test",
                                chunks_count=1, fetched_at="now", content_hash="h", size_bytes=50),
            chunks=[chunk],
        )
        index.store(result)
        assert index.get("get-test").name == "Get Me"
        assert index.get("nope") is None

    def test_corrupt_index_handled(self, tmp_project):
        idx_path = tmp_project / df.INDEX_FILE
        idx_path.parent.mkdir(parents=True, exist_ok=True)
        idx_path.write_text("{corrupt json!!!}")
        idx = df.DocsIndex(tmp_project)
        assert idx.list_sources() == []


# ── Local File Fetch ─────────────────────────────────────────────────────────


class TestFetchLocalFile:
    def test_markdown_file(self, tmp_path):
        md = tmp_path / "docs.md"
        md.write_text("# My Docs\n\nContent that is long enough to be a real chunk of text here.\n")
        result = df.fetch_local_file(md, name="My Docs")
        assert not result.error
        assert result.source.name == "My Docs"
        assert result.source.chunks_count > 0
        assert len(result.chunks) > 0

    def test_html_file(self, tmp_path):
        html_f = tmp_path / "docs.html"
        html_f.write_text("<html><body><h1>Hello</h1><p>World content paragraph long enough.</p></body></html>")
        result = df.fetch_local_file(html_f, name="HTML Doc")
        assert not result.error
        assert len(result.chunks) >= 1

    def test_nonexistent_file(self, tmp_path):
        result = df.fetch_local_file(tmp_path / "nope.md")
        assert result.error
        assert "not found" in result.error.lower()

    def test_auto_name(self, tmp_path):
        md = tmp_path / "my-lib.md"
        md.write_text("# Content\n\nEnough text to be chunked properly here in this doc.\n")
        result = df.fetch_local_file(md)
        assert result.source.name == "my-lib"


# ── Manifest ─────────────────────────────────────────────────────────────────


class TestLoadManifest:
    def test_valid_manifest(self, tmp_path):
        m = tmp_path / "sources.yaml"
        m.write_text("""sources:
  - name: "Python AST"
    url: https://docs.python.org/3/library/ast.html
  - name: "FastAPI"
    url: https://fastapi.tiangolo.com/
""")
        sources = df.load_manifest(m)
        assert len(sources) == 2
        assert sources[0]["name"] == "Python AST"
        assert sources[0]["url"] == "https://docs.python.org/3/library/ast.html"
        assert sources[1]["name"] == "FastAPI"

    def test_empty_manifest(self, tmp_path):
        m = tmp_path / "empty.yaml"
        m.write_text("")
        assert df.load_manifest(m) == []

    def test_missing_manifest(self, tmp_path):
        assert df.load_manifest(tmp_path / "nope.yaml") == []


# ── Renderers ────────────────────────────────────────────────────────────────


class TestRenderers:
    def test_render_status_empty(self):
        report = df.DocsReport()
        text = df.render_text_status(report)
        assert "DOCS FETCHER" in text
        assert "aucune source" in text

    def test_render_status_with_sources(self):
        report = df.DocsReport(
            sources=[df.DocSource(name="Test", url="u", slug="test",
                                  chunks_count=5, fetched_at="2026-01-01")],
            total_chunks=5, total_size_bytes=1024,
        )
        text = df.render_text_status(report)
        assert "Test" in text
        assert "5" in text

    def test_render_fetch_success(self):
        r = df.FetchResult(
            source=df.DocSource(name="Test", url="u", slug="test",
                                chunks_count=3, size_bytes=500),
        )
        text = df.render_text_fetch(r)
        assert "Test" in text
        assert "3 chunks" in text

    def test_render_fetch_error(self):
        r = df.FetchResult(
            source=df.DocSource(name="Err", url="u", slug="err"),
            error="timeout",
        )
        text = df.render_text_fetch(r)
        assert "❌" in text
        assert "timeout" in text

    def test_render_search_empty(self):
        text = df.render_text_search([], "query")
        assert "0 chunk" in text

    def test_render_search_with_results(self):
        chunks = [df.DocChunk(text="hello world", source_url="u",
                              source_name="Test", heading="H")]
        text = df.render_text_search(chunks, "hello")
        assert "1 chunk" in text
        assert "Test" in text

    def test_render_search_truncates(self):
        chunks = [df.DocChunk(text=f"chunk {i}", source_url="u",
                              source_name="T", heading="H") for i in range(30)]
        text = df.render_text_search(chunks, "q")
        assert "supplémentaires" in text


# ── DocsReport ───────────────────────────────────────────────────────────────


class TestDocsReport:
    def test_empty_report(self):
        r = df.DocsReport()
        d = r.to_dict()
        assert d["sources_count"] == 0
        assert d["total_chunks"] == 0

    def test_report_with_data(self):
        r = df.DocsReport(
            sources=[df.DocSource(name="a", url="u", slug="a")],
            total_chunks=10, total_size_bytes=2048,
        )
        d = r.to_dict()
        assert d["sources_count"] == 1
        assert d["total_chunks"] == 10


# ── fetch_and_parse (mocked) ─────────────────────────────────────────────────


class TestFetchAndParse:
    def test_infers_name_from_url(self):
        with patch.object(df, "fetch_url", return_value=("# Hello\n\nContent here enough.", "")):
            result = df.fetch_and_parse("https://example.com/docs/my-lib.html")
        assert result.source.name == "my-lib"
        assert result.source.slug == "my-lib"

    def test_custom_name(self):
        with patch.object(df, "fetch_url", return_value=("# Hello\n\nContent enough here.", "")):
            result = df.fetch_and_parse("https://example.com/page", name="Custom Docs")
        assert result.source.name == "Custom Docs"
        assert result.source.slug == "custom-docs"

    def test_fetch_error_propagated(self):
        with patch.object(df, "fetch_url", return_value=("", "Connection refused")):
            result = df.fetch_and_parse("https://example.com")
        assert result.error == "Connection refused"
        assert result.chunks == []

    def test_html_content_parsed(self):
        html_content = "<html><body><h1>Title</h1><p>Long paragraph content here enough for chunk.</p></body></html>"
        with patch.object(df, "fetch_url", return_value=(html_content, "")):
            result = df.fetch_and_parse("https://example.com/docs.html", name="Test")
        assert not result.error
        assert result.source.chunks_count > 0

    def test_markdown_content_passthrough(self):
        md_content = "# Title\n\nLong enough paragraph content for a chunk here definitely.\n"
        with patch.object(df, "fetch_url", return_value=(md_content, "")):
            result = df.fetch_and_parse("https://example.com/docs.md", name="MD")
        assert not result.error
        assert len(result.chunks) > 0


# ── CLI ──────────────────────────────────────────────────────────────────────


class TestCli:
    def test_build_parser(self):
        p = df.build_parser()
        assert p is not None

    def test_status_command(self, tmp_project, capsys):
        ret = df.main(["--project-root", str(tmp_project), "status"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "DOCS FETCHER" in out

    def test_list_command(self, tmp_project, capsys):
        ret = df.main(["--project-root", str(tmp_project), "list"])
        assert ret == 0

    def test_search_command(self, tmp_project, capsys):
        ret = df.main(["--project-root", str(tmp_project), "search", "test"])
        assert ret == 0

    def test_remove_nonexistent(self, tmp_project):
        ret = df.main(["--project-root", str(tmp_project), "remove", "nope"])
        assert ret == 1

    def test_status_json(self, tmp_project, capsys):
        ret = df.main(["--project-root", str(tmp_project), "--json", "status"])
        assert ret == 0
        data = json.loads(capsys.readouterr().out)
        assert "sources_count" in data

    def test_fetch_with_mock(self, tmp_project, capsys):
        html_content = "<html><body><h1>Docs</h1><p>Content paragraph long enough for sure here.</p></body></html>"
        with patch.object(df, "fetch_url", return_value=(html_content, "")):
            ret = df.main(["--project-root", str(tmp_project), "fetch",
                           "https://example.com/docs.html", "--name", "Test"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "Test" in out

    def test_manifest_missing(self, tmp_project, capsys):
        ret = df.main(["--project-root", str(tmp_project), "manifest", "nope.yaml"])
        assert ret == 1

    def test_no_command_shows_help(self, tmp_project):
        ret = df.main(["--project-root", str(tmp_project)])
        assert ret == 0
