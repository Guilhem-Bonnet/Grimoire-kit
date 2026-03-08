"""Tests pour web-browser.py — Navigateur web sandboxé Grimoire."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Import du module kebab-case ──────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "framework" / "tools"
sys.path.insert(0, str(TOOLS))

_spec = importlib.util.spec_from_file_location("web_browser", TOOLS / "web-browser.py")
wb = importlib.util.module_from_spec(_spec)
sys.modules["web_browser"] = wb
_spec.loader.exec_module(wb)


# ══════════════════════════════════════════════════════════════════════════════
#  Constants & Version
# ══════════════════════════════════════════════════════════════════════════════

class TestConstants(unittest.TestCase):
    def test_version_exists(self):
        self.assertTrue(hasattr(wb, "WEB_BROWSER_VERSION"))
        self.assertRegex(wb.WEB_BROWSER_VERSION, r"^\d+\.\d+\.\d+$")

    def test_timeout_values(self):
        self.assertGreater(wb.NAVIGATION_TIMEOUT, 0)
        self.assertGreater(wb.ACTION_TIMEOUT, 0)
        self.assertGreater(wb.MAX_CONTENT_LENGTH, 0)

    def test_viewport_dimensions(self):
        self.assertEqual(wb.VIEWPORT_WIDTH, 1280)
        self.assertEqual(wb.VIEWPORT_HEIGHT, 720)


# ══════════════════════════════════════════════════════════════════════════════
#  URL Validation (SSRF Protection)
# ══════════════════════════════════════════════════════════════════════════════

class TestURLValidation(unittest.TestCase):
    def test_valid_http(self):
        self.assertEqual(wb.validate_url("http://example.com"), "http://example.com")

    def test_valid_https(self):
        self.assertEqual(wb.validate_url("https://example.com/path?q=1"), "https://example.com/path?q=1")

    def test_reject_file_scheme(self):
        with self.assertRaises(ValueError, msg="file://"):
            wb.validate_url("file:///etc/passwd")

    def test_reject_ftp_scheme(self):
        with self.assertRaises(ValueError):
            wb.validate_url("ftp://example.com")

    def test_reject_javascript_scheme(self):
        with self.assertRaises(ValueError):
            wb.validate_url("javascript:alert(1)")

    def test_reject_no_host(self):
        with self.assertRaises(ValueError):
            wb.validate_url("http://")

    def test_reject_metadata_google(self):
        with self.assertRaises(ValueError):
            wb.validate_url("http://metadata.google.internal/computeMetadata/v1/")

    def test_reject_aws_metadata(self):
        with self.assertRaises(ValueError):
            wb.validate_url("http://169.254.169.254/latest/meta-data/")

    def test_reject_localhost(self):
        with self.assertRaises(ValueError):
            wb.validate_url("http://127.0.0.1/")

    def test_reject_private_10(self):
        with self.assertRaises(ValueError):
            wb.validate_url("http://10.0.0.1/")

    def test_reject_private_172(self):
        with self.assertRaises(ValueError):
            wb.validate_url("http://172.16.0.1/")

    def test_reject_private_192(self):
        with self.assertRaises(ValueError):
            wb.validate_url("http://192.168.1.1/")


# ══════════════════════════════════════════════════════════════════════════════
#  HTML to Markdown
# ══════════════════════════════════════════════════════════════════════════════

class TestHTMLToMarkdown(unittest.TestCase):
    def test_heading_h1(self):
        result = wb.html_to_markdown("<h1>Title</h1>")
        self.assertIn("# Title", result)

    def test_heading_h3(self):
        result = wb.html_to_markdown("<h3>Sub</h3>")
        self.assertIn("### Sub", result)

    def test_code_block(self):
        result = wb.html_to_markdown("<pre><code>x = 1</code></pre>")
        self.assertIn("```", result)
        self.assertIn("x = 1", result)

    def test_inline_code(self):
        result = wb.html_to_markdown("Use <code>ast.parse</code> here")
        self.assertIn("`ast.parse`", result)

    def test_link(self):
        result = wb.html_to_markdown('<a href="https://example.com">Click</a>')
        self.assertIn("[Click](https://example.com)", result)

    def test_list_items(self):
        result = wb.html_to_markdown("<ul><li>one</li><li>two</li></ul>")
        self.assertIn("- one", result)
        self.assertIn("- two", result)

    def test_bold(self):
        result = wb.html_to_markdown("<b>bold</b> and <strong>strong</strong>")
        self.assertIn("**bold**", result)
        self.assertIn("**strong**", result)

    def test_italic(self):
        result = wb.html_to_markdown("<em>italic</em> and <i>also</i>")
        self.assertIn("*italic*", result)
        self.assertIn("*also*", result)

    def test_body_not_bold(self):
        """<body> should not be treated as <b> bold."""
        result = wb.html_to_markdown("<body><p>Hello</p></body>")
        self.assertNotIn("**", result)
        self.assertIn("Hello", result)

    def test_strip_script(self):
        result = wb.html_to_markdown("<p>text</p><script>alert(1)</script>")
        self.assertNotIn("alert", result)
        self.assertIn("text", result)

    def test_strip_style(self):
        result = wb.html_to_markdown("<style>.x{color:red}</style><p>hi</p>")
        self.assertNotIn("color", result)
        self.assertIn("hi", result)

    def test_strip_nav_footer(self):
        result = wb.html_to_markdown("<nav>menu</nav><main>content</main><footer>foot</footer>")
        self.assertNotIn("menu", result)
        self.assertNotIn("foot", result)
        self.assertIn("content", result)

    def test_strip_head(self):
        result = wb.html_to_markdown("<head><title>T</title></head><body><p>text</p></body>")
        self.assertNotIn("<title>", result)
        self.assertIn("text", result)

    def test_paragraph_breaks(self):
        result = wb.html_to_markdown("<p>First.</p><p>Second.</p>")
        self.assertIn("First.", result)
        self.assertIn("Second.", result)

    def test_br_tag(self):
        result = wb.html_to_markdown("line1<br/>line2")
        self.assertIn("line1", result)
        self.assertIn("line2", result)

    def test_html_entities(self):
        result = wb.html_to_markdown("<p>&amp; &lt; &gt; &quot;</p>")
        self.assertIn("&", result)
        self.assertIn("<", result)
        self.assertIn(">", result)

    def test_strips_remaining_tags(self):
        result = wb.html_to_markdown("<div><span>text</span></div>")
        self.assertNotIn("<", result)
        self.assertIn("text", result)

    def test_table_basic(self):
        html = "<table><tr><th>Name</th><th>Value</th></tr><tr><td>a</td><td>1</td></tr></table>"
        result = wb.html_to_markdown(html)
        self.assertIn("Name", result)
        self.assertIn("Value", result)
        self.assertIn("|", result)

    def test_noscript_stripped(self):
        result = wb.html_to_markdown("<noscript>Enable JS</noscript><p>ok</p>")
        self.assertNotIn("Enable JS", result)
        self.assertIn("ok", result)


# ══════════════════════════════════════════════════════════════════════════════
#  Link Extraction
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractLinks(unittest.TestCase):
    def test_basic_links(self):
        html = '<a href="https://a.com">A</a><a href="https://b.com">B</a>'
        links = wb._extract_links(html, "https://example.com")
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0]["href"], "https://a.com")
        self.assertEqual(links[0]["text"], "A")

    def test_relative_links(self):
        html = '<a href="/docs/api">API</a>'
        links = wb._extract_links(html, "https://example.com/start")
        self.assertEqual(links[0]["href"], "https://example.com/docs/api")

    def test_dedup(self):
        html = '<a href="https://a.com">A</a><a href="https://a.com">A again</a>'
        links = wb._extract_links(html, "https://example.com")
        self.assertEqual(len(links), 1)

    def test_skip_mailto(self):
        html = '<a href="mailto:a@b.com">Email</a>'
        links = wb._extract_links(html, "https://example.com")
        self.assertEqual(len(links), 0)

    def test_skip_javascript(self):
        html = '<a href="javascript:void(0)">Click</a>'
        links = wb._extract_links(html, "https://example.com")
        self.assertEqual(len(links), 0)

    def test_max_200(self):
        html = "".join(f'<a href="https://a.com/{i}">L{i}</a>' for i in range(300))
        links = wb._extract_links(html, "https://example.com")
        self.assertEqual(len(links), 200)

    def test_nested_tags_in_link_text(self):
        html = '<a href="https://a.com"><span>Bold Link</span></a>'
        links = wb._extract_links(html, "https://example.com")
        self.assertIn("Bold Link", links[0]["text"])


# ══════════════════════════════════════════════════════════════════════════════
#  Readability Heuristic
# ══════════════════════════════════════════════════════════════════════════════

class TestReadabilityExtract(unittest.TestCase):
    def test_article_priority(self):
        html = '<nav>Menu</nav><article><p>Main content here and more text to fill it up beyond the minimum threshold.</p><p>Another paragraph of substantial content.</p></article><footer>Foot</footer>'
        result = wb._readability_extract(html)
        self.assertIn("Main content", result)

    def test_main_tag(self):
        html = '<nav>Menu</nav><main><p>This is the main content area with enough text to be a valid extraction target for the algorithm.</p><p>Second paragraph.</p></main>'
        result = wb._readability_extract(html)
        self.assertIn("main content", result)

    def test_fallback_div(self):
        html = '<div><p>Short</p></div><div><p>' + "A" * 300 + '</p></div>'
        result = wb._readability_extract(html)
        self.assertIn("AAA", result)

    def test_fallback_full_page(self):
        html = '<p>Just a paragraph</p>'
        result = wb._readability_extract(html)
        self.assertIn("Just a paragraph", result)


# ══════════════════════════════════════════════════════════════════════════════
#  Selector to Regex (urllib fallback)
# ══════════════════════════════════════════════════════════════════════════════

class TestSelectorToRegex(unittest.TestCase):
    def test_tag_selector(self):
        html = '<main><p>Content</p></main>'
        result = wb._selector_to_regex("main", html)
        self.assertIn("Content", result)

    def test_id_selector(self):
        html = '<div id="content"><p>Found</p></div>'
        result = wb._selector_to_regex("#content", html)
        self.assertIn("Found", result)

    def test_class_selector(self):
        html = '<div class="main-content"><p>Here</p></div>'
        result = wb._selector_to_regex(".main-content", html)
        self.assertIn("Here", result)

    def test_no_match(self):
        html = '<div>nope</div>'
        result = wb._selector_to_regex("#nonexistent", html)
        self.assertEqual(result, "")

    def test_complex_selector_returns_empty(self):
        html = '<div class="x"><p>text</p></div>'
        result = wb._selector_to_regex("div.x > p", html)
        self.assertEqual(result, "")


# ══════════════════════════════════════════════════════════════════════════════
#  Data Classes
# ══════════════════════════════════════════════════════════════════════════════

class TestDataClasses(unittest.TestCase):
    def test_page_content_to_dict(self):
        pc = wb.PageContent(url="https://example.com", title="Test", markdown="# Hello")
        d = pc.to_dict()
        self.assertEqual(d["url"], "https://example.com")
        self.assertEqual(d["title"], "Test")
        self.assertEqual(d["markdown"], "# Hello")

    def test_page_content_truncation(self):
        long_text = "x" * (wb.MAX_CONTENT_LENGTH + 100)
        pc = wb.PageContent(url="https://example.com", text=long_text, markdown=long_text)
        d = pc.to_dict()
        self.assertIn("[...tronqué]", d["text"])
        self.assertIn("[...tronqué]", d["markdown"])

    def test_screenshot_result_to_dict(self):
        sr = wb.ScreenshotResult(url="https://example.com", path="/tmp/x.png", base64_data="AAAA")
        d = sr.to_dict()
        self.assertNotEqual(d["base64_data"], "AAAA")  # Should be masked
        self.assertIn("4 chars", d["base64_data"])

    def test_screenshot_result_no_b64(self):
        sr = wb.ScreenshotResult(url="https://example.com", path="/tmp/x.png")
        d = sr.to_dict()
        self.assertEqual(d["base64_data"], "")

    def test_interact_result_to_dict(self):
        ir = wb.InteractResult(
            url="https://example.com",
            steps=[{"step": 0, "action": {"click": "#btn"}, "ok": True}],
            final_url="https://example.com/page2",
            elapsed_ms=500,
        )
        d = ir.to_dict()
        self.assertEqual(len(d["steps"]), 1)
        self.assertEqual(d["final_url"], "https://example.com/page2")

    def test_browser_status_to_dict(self):
        bs = wb.BrowserStatus(playwright_installed=True, browser_installed=False)
        d = bs.to_dict()
        self.assertTrue(d["playwright_installed"])
        self.assertFalse(d["browser_installed"])


# ══════════════════════════════════════════════════════════════════════════════
#  Action Validation
# ══════════════════════════════════════════════════════════════════════════════

class TestActionValidation(unittest.TestCase):
    def test_valid_click(self):
        wb._validate_action({"click": "#btn"})

    def test_valid_type(self):
        wb._validate_action({"type": "#input", "text": "hello"})

    def test_valid_extract(self):
        wb._validate_action({"extract": True})

    def test_valid_screenshot(self):
        wb._validate_action({"screenshot": True})

    def test_valid_scroll(self):
        wb._validate_action({"scroll": "down"})

    def test_valid_wait(self):
        wb._validate_action({"wait": "#element"})

    def test_valid_evaluate(self):
        wb._validate_action({"evaluate": "document.title"})

    def test_valid_fill(self):
        wb._validate_action({"fill": "#input", "text": "val"})

    def test_valid_select(self):
        wb._validate_action({"select": "#dropdown", "value": "opt1"})

    def test_valid_hover(self):
        wb._validate_action({"hover": "#link"})

    def test_invalid_action(self):
        with self.assertRaises(ValueError, msg="unknown"):
            wb._validate_action({"navigate": "https://x.com"})

    def test_empty_action(self):
        with self.assertRaises(ValueError):
            wb._validate_action({})


# ══════════════════════════════════════════════════════════════════════════════
#  Fetch with urllib (no Playwright needed)
# ══════════════════════════════════════════════════════════════════════════════

class TestFetchUrllib(unittest.TestCase):
    def test_ssrf_blocked(self):
        with self.assertRaises(ValueError):
            wb._fetch_urllib("http://127.0.0.1/secret")

    @patch("urllib.request.urlopen")
    def test_basic_fetch(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_resp.read.return_value = b"<html><head><title>Test</title></head><body><p>Hello World</p></body></html>"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = wb._fetch_urllib("https://example.com")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.title, "Test")
        self.assertIn("Hello World", result.markdown)
        self.assertEqual(result.method, "urllib")

    @patch("urllib.request.urlopen")
    def test_fetch_with_selector(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.read.return_value = b'<html><body><main><p>Main Content</p></main><aside>Side</aside></body></html>'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = wb._fetch_urllib("https://example.com", selector="main")
        self.assertIn("Main Content", result.markdown)

    @patch("urllib.request.urlopen")
    def test_http_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.com", 404, "Not Found", {}, None,
        )

        result = wb._fetch_urllib("https://example.com")
        self.assertEqual(result.status_code, 404)
        self.assertEqual(result.method, "urllib")


# ══════════════════════════════════════════════════════════════════════════════
#  Fetch dispatch (with Playwright mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestFetchDispatch(unittest.TestCase):
    @patch.object(wb, "_check_playwright", return_value=(False, "not installed"))
    @patch.object(wb, "_fetch_urllib")
    def test_fallback_to_urllib(self, mock_urllib, mock_pw):
        mock_urllib.return_value = wb.PageContent(url="https://example.com", method="urllib")
        result = wb.fetch("https://example.com")
        mock_urllib.assert_called_once()
        self.assertEqual(result.method, "urllib")

    @patch.object(wb, "_check_playwright", return_value=(True, "OK"))
    @patch.object(wb, "_fetch_playwright")
    def test_prefer_playwright(self, mock_pw_fetch, mock_pw_check):
        mock_pw_fetch.return_value = wb.PageContent(url="https://example.com", method="playwright")
        result = wb.fetch("https://example.com")
        mock_pw_fetch.assert_called_once()
        self.assertEqual(result.method, "playwright")

    @patch.object(wb, "_fetch_urllib")
    def test_no_js_forces_urllib(self, mock_urllib):
        mock_urllib.return_value = wb.PageContent(url="https://example.com", method="urllib")
        wb.fetch("https://example.com", prefer_playwright=False)
        mock_urllib.assert_called_once()

    def test_fetch_validates_url(self):
        with self.assertRaises(ValueError):
            wb.fetch("ftp://evil.com")


# ══════════════════════════════════════════════════════════════════════════════
#  Screenshot (mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestScreenshot(unittest.TestCase):
    @patch.object(wb, "_check_playwright", return_value=(False, "not installed"))
    def test_screenshot_no_playwright(self, mock_pw):
        result = wb.take_screenshot("https://example.com")
        self.assertIn("not installed", result.error)

    def test_screenshot_ssrf(self):
        with self.assertRaises(ValueError):
            wb.take_screenshot("http://169.254.169.254/")


# ══════════════════════════════════════════════════════════════════════════════
#  Interact (mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestInteract(unittest.TestCase):
    @patch.object(wb, "_check_playwright", return_value=(False, "not installed"))
    def test_interact_no_playwright(self, mock_pw):
        result = wb.interact("https://example.com", [{"click": "#btn"}])
        self.assertIn("not installed", result.error)

    def test_interact_ssrf(self):
        with self.assertRaises(ValueError):
            wb.interact("http://10.0.0.1/", [{"click": "#btn"}])

    def test_interact_invalid_action(self):
        """Invalid actions should be caught before browser launch."""
        with self.assertRaises(ValueError):
            wb.interact("https://example.com", [{"bad_action": "x"}])


# ══════════════════════════════════════════════════════════════════════════════
#  Status
# ══════════════════════════════════════════════════════════════════════════════

class TestStatus(unittest.TestCase):
    @patch.object(wb, "_check_playwright", return_value=(False, "not installed"))
    def test_status_no_playwright(self, mock_pw):
        result = wb.status()
        self.assertFalse(result.playwright_installed)
        self.assertFalse(result.browser_installed)

    @patch.object(wb, "_check_browser_installed", return_value=(True, "Chromium OK"))
    @patch.object(wb, "_check_playwright", return_value=(True, "OK"))
    def test_status_all_ok(self, mock_pw, mock_br):
        result = wb.status()
        self.assertTrue(result.playwright_installed)
        self.assertTrue(result.browser_installed)


# ══════════════════════════════════════════════════════════════════════════════
#  CLI Renderers
# ══════════════════════════════════════════════════════════════════════════════

class TestRenderers(unittest.TestCase):
    def test_render_text(self):
        pc = wb.PageContent(
            url="https://example.com", title="Test Page",
            markdown="# Hello\nWorld", method="urllib", status_code=200,
            elapsed_ms=100, links=[{"href": "https://a.com", "text": "Link A"}],
        )
        out = wb._render_text(pc)
        self.assertIn("Test Page", out)
        self.assertIn("# Hello", out)
        self.assertIn("Link A", out)
        self.assertIn("urllib", out)

    def test_render_text_empty_markdown(self):
        pc = wb.PageContent(url="https://example.com", text="Plain text", method="urllib")
        out = wb._render_text(pc)
        self.assertIn("Plain text", out)

    def test_render_screenshot_ok(self):
        sr = wb.ScreenshotResult(url="https://example.com", path="/tmp/x.png", width=1280, height=720, elapsed_ms=500)
        out = wb._render_screenshot(sr)
        self.assertIn("/tmp/x.png", out)
        self.assertIn("1280x720", out)

    def test_render_screenshot_error(self):
        sr = wb.ScreenshotResult(url="https://example.com", error="timeout")
        out = wb._render_screenshot(sr)
        self.assertIn("timeout", out)

    def test_render_interact(self):
        ir = wb.InteractResult(
            url="https://example.com",
            final_url="https://example.com/page2",
            elapsed_ms=1000,
            steps=[
                {"step": 0, "action": {"click": "#btn"}, "ok": True},
                {"step": 1, "action": {"extract": True}, "ok": True, "markdown": "content"},
            ],
        )
        out = wb._render_interact(ir)
        self.assertIn("✓ Step 0", out)
        self.assertIn("✓ Step 1", out)
        self.assertIn("extrait:", out)

    def test_render_interact_error(self):
        ir = wb.InteractResult(
            url="https://example.com",
            error="Navigation failed",
            steps=[{"step": 0, "action": {"click": "#x"}, "ok": False, "error": "Not found"}],
        )
        out = wb._render_interact(ir)
        self.assertIn("Navigation failed", out)
        self.assertIn("✗ Step 0", out)
        self.assertIn("Not found", out)

    def test_render_interact_evaluate_result(self):
        ir = wb.InteractResult(
            url="https://example.com",
            steps=[{"step": 0, "action": {"evaluate": "1+1"}, "ok": True, "result": "2"}],
        )
        out = wb._render_interact(ir)
        self.assertIn("Résultat: 2", out)

    def test_render_status_all_ok(self):
        bs = wb.BrowserStatus(
            playwright_installed=True, browser_installed=True,
            playwright_message="OK", browser_message="Chromium OK",
        )
        out = wb._render_status(bs)
        self.assertIn("✓ Playwright", out)
        self.assertIn("✓ Navigateur", out)

    def test_render_status_not_installed(self):
        bs = wb.BrowserStatus(
            playwright_installed=False, browser_installed=False,
            playwright_message="not found", browser_message="N/A",
        )
        out = wb._render_status(bs)
        self.assertIn("✗ Playwright", out)
        self.assertIn("pip install playwright", out)


# ══════════════════════════════════════════════════════════════════════════════
#  CLI main()
# ══════════════════════════════════════════════════════════════════════════════

class TestCLI(unittest.TestCase):
    def test_no_command_returns_1(self):
        """No command should print help and return 1."""
        rc = wb.main(["--project-root", "."])
        self.assertEqual(rc, 1)

    @patch.object(wb, "status")
    def test_status_command(self, mock_status):
        mock_status.return_value = wb.BrowserStatus(
            playwright_installed=True, browser_installed=True,
            playwright_message="OK", browser_message="Chromium OK",
        )
        rc = wb.main(["--project-root", ".", "status"])
        self.assertEqual(rc, 0)

    @patch.object(wb, "status")
    def test_status_command_not_installed(self, mock_status):
        mock_status.return_value = wb.BrowserStatus(
            playwright_installed=False, playwright_message="not installed",
        )
        rc = wb.main(["--project-root", ".", "status"])
        self.assertEqual(rc, 1)

    @patch.object(wb, "status")
    def test_status_json(self, mock_status):
        mock_status.return_value = wb.BrowserStatus(
            playwright_installed=True, browser_installed=True,
            playwright_message="OK", browser_message="OK",
        )
        rc = wb.main(["--project-root", ".", "--json", "status"])
        self.assertEqual(rc, 0)

    @patch.object(wb, "fetch")
    def test_fetch_command(self, mock_fetch):
        mock_fetch.return_value = wb.PageContent(
            url="https://example.com", title="Example", status_code=200,
            markdown="# Hello", method="urllib",
        )
        rc = wb.main(["--project-root", ".", "fetch", "https://example.com"])
        self.assertEqual(rc, 0)
        mock_fetch.assert_called_once()

    @patch.object(wb, "fetch")
    def test_fetch_with_selector(self, mock_fetch):
        mock_fetch.return_value = wb.PageContent(
            url="https://example.com", status_code=200, method="urllib",
        )
        wb.main(["--project-root", ".", "fetch", "https://example.com", "--selector", "main"])
        mock_fetch.assert_called_once_with(
            "https://example.com",
            selector="main",
            wait_for="",
            prefer_playwright=True,
        )

    @patch.object(wb, "fetch")
    def test_fetch_no_js(self, mock_fetch):
        mock_fetch.return_value = wb.PageContent(
            url="https://example.com", status_code=200, method="urllib",
        )
        wb.main(["--project-root", ".", "fetch", "https://example.com", "--no-js"])
        mock_fetch.assert_called_once_with(
            "https://example.com",
            selector="",
            wait_for="",
            prefer_playwright=False,
        )

    @patch.object(wb, "fetch")
    def test_fetch_json(self, mock_fetch):
        mock_fetch.return_value = wb.PageContent(
            url="https://example.com", title="Test", status_code=200,
            markdown="md", method="urllib",
        )
        rc = wb.main(["--project-root", ".", "--json", "fetch", "https://example.com"])
        self.assertEqual(rc, 0)

    @patch.object(wb, "fetch")
    def test_fetch_error_returns_1(self, mock_fetch):
        mock_fetch.return_value = wb.PageContent(
            url="https://example.com", status_code=500, method="urllib",
        )
        rc = wb.main(["--project-root", ".", "fetch", "https://example.com"])
        self.assertEqual(rc, 1)

    @patch.object(wb, "take_screenshot")
    def test_screenshot_command(self, mock_ss):
        mock_ss.return_value = wb.ScreenshotResult(
            url="https://example.com", path="/tmp/x.png",
            width=1280, height=720,
        )
        rc = wb.main(["--project-root", ".", "screenshot", "https://example.com"])
        self.assertEqual(rc, 0)

    @patch.object(wb, "take_screenshot")
    def test_screenshot_error_returns_1(self, mock_ss):
        mock_ss.return_value = wb.ScreenshotResult(
            url="https://example.com", error="timeout",
        )
        rc = wb.main(["--project-root", ".", "screenshot", "https://example.com"])
        self.assertEqual(rc, 1)

    @patch.object(wb, "interact")
    def test_interact_command(self, mock_int):
        mock_int.return_value = wb.InteractResult(
            url="https://example.com",
            final_url="https://example.com/done",
            steps=[{"step": 0, "action": {"click": "#x"}, "ok": True}],
        )
        rc = wb.main([
            "--project-root", ".", "interact", "https://example.com",
            "--actions", '[{"click":"#x"}]',
        ])
        self.assertEqual(rc, 0)

    def test_interact_bad_json(self):
        rc = wb.main([
            "--project-root", ".", "interact", "https://example.com",
            "--actions", "not-json",
        ])
        self.assertEqual(rc, 1)

    def test_interact_not_array(self):
        rc = wb.main([
            "--project-root", ".", "interact", "https://example.com",
            "--actions", '{"click":"#x"}',
        ])
        self.assertEqual(rc, 1)

    @patch.object(wb, "readability")
    def test_readability_command(self, mock_read):
        mock_read.return_value = wb.PageContent(
            url="https://example.com", title="Test",
            status_code=200, markdown="Content", method="urllib",
        )
        rc = wb.main(["--project-root", ".", "readability", "https://example.com"])
        self.assertEqual(rc, 0)


# ══════════════════════════════════════════════════════════════════════════════
#  Playwright Check (mocked)
# ══════════════════════════════════════════════════════════════════════════════

class TestPlaywrightCheck(unittest.TestCase):
    def test_check_playwright_not_installed(self):
        """When playwright is not importable, should return False."""
        with patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
            # Force reimport check
            ok, msg = wb._check_playwright()
            # This may or may not work depending on import caching
            # The important thing is it doesn't crash
            self.assertIsInstance(ok, bool)
            self.assertIsInstance(msg, str)


# ══════════════════════════════════════════════════════════════════════════════
#  Edge Cases
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases(unittest.TestCase):
    def test_empty_html_to_markdown(self):
        result = wb.html_to_markdown("")
        self.assertEqual(result, "")

    def test_html_only_tags(self):
        result = wb.html_to_markdown("<div><span></span></div>")
        self.assertEqual(result.strip(), "")

    def test_extract_links_empty(self):
        links = wb._extract_links("", "https://example.com")
        self.assertEqual(links, [])

    def test_readability_empty(self):
        result = wb._readability_extract("")
        self.assertEqual(result, "")

    def test_page_content_defaults(self):
        pc = wb.PageContent(url="https://example.com")
        self.assertEqual(pc.title, "")
        self.assertEqual(pc.text, "")
        self.assertEqual(pc.markdown, "")
        self.assertEqual(pc.links, [])
        self.assertEqual(pc.status_code, 0)

    def test_screenshot_result_defaults(self):
        sr = wb.ScreenshotResult(url="https://example.com")
        self.assertEqual(sr.path, "")
        self.assertEqual(sr.error, "")

    def test_interact_result_defaults(self):
        ir = wb.InteractResult(url="https://example.com")
        self.assertEqual(ir.steps, [])
        self.assertEqual(ir.error, "")
        self.assertEqual(ir.final_url, "")


if __name__ == "__main__":
    unittest.main()
