"""W6-02 — fraîcheur du code graph : verdict par empreinte, gate, MCP, project_kind."""

from __future__ import annotations

import json
from pathlib import Path

from grimoire.codegraph.freshness import RECEIPT_PATH, check_freshness, record_index
from grimoire.core.project_kind import detect_project_kind


def _project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    return tmp_path


class TestVerdict:
    def test_never_indexed_is_unverified_not_current(self, tmp_path: Path) -> None:
        fresh = check_freshness(_project(tmp_path))
        assert fresh.verdict == "unverified"
        assert fresh.reason == "no_receipt"

    def test_current_after_indexing(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        record_index(root)
        assert check_freshness(root).verdict == "current"

    def test_stale_after_modification_and_names_the_file(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        record_index(root)
        (root / "src" / "a.py").write_text("def a():\n    return 99\n", encoding="utf-8")
        fresh = check_freshness(root)
        assert fresh.verdict == "stale"
        assert fresh.modified == ("a.py",)

    def test_stale_on_added_and_removed_files(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        record_index(root)
        (root / "src" / "c.py").write_text("x = 1\n", encoding="utf-8")
        (root / "src" / "b.py").unlink()
        fresh = check_freshness(root)
        assert (fresh.verdict, fresh.added, fresh.removed) == ("stale", ("c.py",), ("b.py",))

    def test_touching_without_changing_content_stays_current(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        record_index(root)
        target = root / "src" / "a.py"
        target.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        assert check_freshness(root).verdict == "current"

    def test_caches_do_not_change_the_verdict(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        record_index(root)
        (root / "src" / "__pycache__").mkdir()
        (root / "src" / "__pycache__" / "a.py").write_text("junk\n", encoding="utf-8")
        assert check_freshness(root).verdict == "current"

    def test_corrupt_receipt_is_unverified_never_current(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        record_index(root)
        (root / RECEIPT_PATH).write_text("{not json", encoding="utf-8")
        fresh = check_freshness(root)
        assert (fresh.verdict, fresh.reason) == ("unverified", "receipt_unreadable")

    def test_missing_source_dir_is_unverified(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        record_index(root)
        for f in (root / "src").glob("*.py"):
            f.unlink()
        (root / "src").rmdir()
        assert check_freshness(root).reason == "source_dir_missing"


class TestGate:
    def _gate(self, root: Path) -> list:
        from grimoire.core.standard_checks.gate_codegraph import codegraph_fresh_checks

        return list(codegraph_fresh_checks(root))

    def test_no_receipt_gate_does_not_apply(self, tmp_path: Path) -> None:
        assert self._gate(_project(tmp_path)) == []

    def test_current_passes_stale_blocks_with_remedy(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        record_index(root)
        assert self._gate(root) == []
        (root / "src" / "a.py").write_text("y = 2\n", encoding="utf-8")
        (check,) = self._gate(root)
        assert check.id == "gate.codegraph_fresh"
        assert check.is_error
        assert "~a.py" in check.message
        assert "grimoire_codegraph_index" in check.message

    def test_unreadable_receipt_blocks(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        record_index(root)
        (root / RECEIPT_PATH).write_text("[]", encoding="utf-8")
        (check,) = self._gate(root)
        assert check.is_error

    def test_gate_is_wired_into_check_evidence_gates(self, tmp_path: Path) -> None:
        from grimoire.core.agentic_standard import check_evidence_gates, setup_standard_profile

        setup_standard_profile(tmp_path, profile_id="starter", project_name="cg")
        root = _project(tmp_path)
        record_index(root)
        (root / "src" / "a.py").write_text("y = 2\n", encoding="utf-8")
        result = check_evidence_gates(root, task_id="bootstrap", target_state="review")
        assert any(c.id == "gate.codegraph_fresh" and c.is_error for c in result.checks)

    def test_check_id_is_registered(self) -> None:
        from grimoire.core.standard_checks.registry import dimension_for

        assert dimension_for("gate.codegraph_fresh") == "evidence_gates"


class TestMcp:
    def test_freshness_tool_flips_from_current_to_stale(self, tmp_path: Path) -> None:
        from grimoire.mcp.server import grimoire_codegraph_freshness, grimoire_codegraph_index

        root = _project(tmp_path)
        before = json.loads(grimoire_codegraph_freshness(str(root)))["freshness"]
        assert before["verdict"] == "unverified"
        indexed = json.loads(grimoire_codegraph_index(str(root)))
        assert indexed["freshness"]["verdict"] == "current"
        assert indexed["stats"]["files"] == 2
        (root / "src" / "a.py").write_text("y = 2\n", encoding="utf-8")
        after = json.loads(grimoire_codegraph_freshness(str(root)))["freshness"]
        assert after["verdict"] == "stale"

    def test_index_refuses_a_source_dir_outside_the_project(self, tmp_path: Path) -> None:
        from grimoire.mcp.server import grimoire_codegraph_index

        root = _project(tmp_path)
        result = grimoire_codegraph_index(str(root), source_dir="..")
        assert getattr(result, "isError", False)
        assert "error" in "".join(getattr(b, "text", "") for b in result.content)
        assert not (root / RECEIPT_PATH).exists()


class TestProjectKind:
    def test_empty_dir_is_greenfield(self, tmp_path: Path) -> None:
        kind = detect_project_kind(tmp_path)
        assert (kind.kind, kind.source, kind.evidence) == ("greenfield", "source-files", ())

    def test_source_file_makes_brownfield_and_names_it(self, tmp_path: Path) -> None:
        kind = detect_project_kind(_project(tmp_path))
        assert kind.kind == "brownfield"
        assert kind.evidence == ("src/a.py", "src/b.py")

    def test_kit_scaffolding_and_dependencies_do_not_count(self, tmp_path: Path) -> None:
        for rel in ("_grimoire/kit/x.py", ".claude/hooks/h.py", "node_modules/m/i.js", "venv/lib/z.py"):
            f = tmp_path / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x\n", encoding="utf-8")
        assert detect_project_kind(tmp_path).kind == "greenfield"
