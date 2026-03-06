"""Tests for grimoire-log.py — Structured logging module."""
import importlib.util
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

KIT_DIR = Path(__file__).resolve().parent.parent

TOOL = KIT_DIR / "framework" / "tools" / "grimoire-log.py"


def _load_module():
    mod_name = "grimoire_log"
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


gl = _load_module()


class TestSetup(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        # Reset configured flag for each test
        gl._CONFIGURED = False
        # Remove any existing handlers from the root grimoire logger
        root = logging.getLogger(gl.ROOT_LOGGER_NAME)
        root.handlers.clear()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        gl._CONFIGURED = False
        root = logging.getLogger(gl.ROOT_LOGGER_NAME)
        root.handlers.clear()

    def test_setup_creates_log_dir(self):
        gl.setup(self.tmpdir)
        log_dir = self.tmpdir / gl.LOG_DIR
        self.assertTrue(log_dir.exists())

    def test_setup_creates_log_files(self):
        gl.setup(self.tmpdir)
        logger = logging.getLogger("grimoire.test_tool")
        logger.info("test message")
        # Flush handlers
        for h in logging.getLogger(gl.ROOT_LOGGER_NAME).handlers:
            h.flush()
        self.assertTrue((self.tmpdir / gl.LOG_DIR / gl.LOG_FILE).exists())
        self.assertTrue((self.tmpdir / gl.LOG_DIR / gl.JSON_LOG_FILE).exists())

    def test_setup_idempotent(self):
        gl.setup(self.tmpdir)
        handler_count = len(logging.getLogger(gl.ROOT_LOGGER_NAME).handlers)
        gl.setup(self.tmpdir)
        self.assertEqual(len(logging.getLogger(gl.ROOT_LOGGER_NAME).handlers), handler_count)

    def test_get_logger(self):
        logger = gl.get_logger("my-tool", self.tmpdir)
        self.assertEqual(logger.name, "grimoire.my-tool")

    def test_json_format(self):
        gl.setup(self.tmpdir)
        logger = logging.getLogger("grimoire.test_json")
        logger.warning("test json %s", "msg")
        for h in logging.getLogger(gl.ROOT_LOGGER_NAME).handlers:
            h.flush()
        jsonl = (self.tmpdir / gl.LOG_DIR / gl.JSON_LOG_FILE).read_text(encoding="utf-8")
        lines = [ln for ln in jsonl.strip().splitlines() if ln.strip()]
        # Find the test_json line
        found = False
        for line in lines:
            entry = json.loads(line)
            if entry.get("logger") == "grimoire.test_json":
                self.assertEqual(entry["level"], "WARNING")
                self.assertIn("test json msg", entry["msg"])
                found = True
                break
        self.assertTrue(found, "JSON log entry not found")

    def test_text_format(self):
        gl.setup(self.tmpdir)
        logger = logging.getLogger("grimoire.test_text")
        logger.error("a test error")
        for h in logging.getLogger(gl.ROOT_LOGGER_NAME).handlers:
            h.flush()
        text = (self.tmpdir / gl.LOG_DIR / gl.LOG_FILE).read_text(encoding="utf-8")
        self.assertIn("grimoire.test_text", text)
        self.assertIn("a test error", text)


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        log_dir = self.tmpdir / gl.LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        # Write sample log
        (log_dir / gl.LOG_FILE).write_text(
            "2026-03-05T10:00:00 DEBUG [grimoire.test] hello\n"
            "2026-03-05T10:00:01 WARNING [grimoire.test] oops\n"
            "2026-03-05T10:00:02 ERROR [grimoire.test] big error\n",
            encoding="utf-8",
        )
        (log_dir / gl.JSON_LOG_FILE).write_text(
            '{"ts":"2026-03-05T10:00:00","level":"DEBUG","logger":"grimoire.test","msg":"hello"}\n'
            '{"ts":"2026-03-05T10:00:01","level":"WARNING","logger":"grimoire.test","msg":"oops"}\n'
            '{"ts":"2026-03-05T10:00:02","level":"ERROR","logger":"grimoire.test","msg":"big error"}\n',
            encoding="utf-8",
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_tail(self):
        rc = gl.main(["--project-root", str(self.tmpdir), "tail", "--lines", "2"])
        self.assertEqual(rc, 0)

    def test_search_found(self):
        rc = gl.main(["--project-root", str(self.tmpdir), "search", "--query", "oops"])
        self.assertEqual(rc, 0)

    def test_search_not_found(self):
        rc = gl.main(["--project-root", str(self.tmpdir), "search", "--query", "zzznotfound"])
        self.assertEqual(rc, 0)

    def test_stats(self):
        rc = gl.main(["--project-root", str(self.tmpdir), "stats"])
        self.assertEqual(rc, 0)

    def test_rotate(self):
        rc = gl.main(["--project-root", str(self.tmpdir), "rotate"])
        self.assertEqual(rc, 0)

    def test_clear(self):
        rc = gl.main(["--project-root", str(self.tmpdir), "clear"])
        self.assertEqual(rc, 0)
        log_dir = self.tmpdir / gl.LOG_DIR
        self.assertEqual(len(list(log_dir.iterdir())), 0)


class TestMCPInterface(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        log_dir = self.tmpdir / gl.LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / gl.LOG_FILE).write_text(
            "line1\nline2\nline3\nfoobar line4\n", encoding="utf-8",
        )
        (log_dir / gl.JSON_LOG_FILE).write_text(
            '{"ts":"t","level":"DEBUG","logger":"x","msg":"a"}\n'
            '{"ts":"t","level":"ERROR","logger":"y","msg":"b"}\n',
            encoding="utf-8",
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_mcp_tail(self):
        r = gl.mcp_grimoire_log(str(self.tmpdir), action="tail", lines=2)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(len(r["data"]), 2)

    def test_mcp_search(self):
        r = gl.mcp_grimoire_log(str(self.tmpdir), action="search", query="foobar")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["matches"], 1)

    def test_mcp_stats(self):
        r = gl.mcp_grimoire_log(str(self.tmpdir), action="stats")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["data"]["total"], 2)

    def test_mcp_unknown_action(self):
        r = gl.mcp_grimoire_log(str(self.tmpdir), action="badcmd")
        self.assertEqual(r["status"], "error")


if __name__ == "__main__":
    unittest.main()
