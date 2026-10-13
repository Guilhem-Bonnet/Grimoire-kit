"""W6-02 — fraîcheur du code graph : verdict par empreinte, gate, MCP, project_kind."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from grimoire.codegraph.freshness import RECEIPT_PATH, check_freshness, record_index
from grimoire.core.project_kind import detect_project_kind


def _project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir(parents=True)
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


class TestGateScope:
    """W6-02-S1-02 : le gate juge la transition vers review, pas l'état global du code après coup."""

    def _stale_project(self, tmp_path: Path) -> Path:
        from grimoire.core.agentic_standard import setup_standard_profile

        setup_standard_profile(tmp_path, profile_id="starter", project_name="cg")
        root = _project(tmp_path)
        record_index(root)
        (root / "src" / "a.py").write_text("y = 2\n", encoding="utf-8")
        return root

    @pytest.mark.parametrize("state", ["accepted", "released"])
    def test_closed_task_is_not_judged_on_later_source_changes(self, tmp_path: Path, state: str) -> None:
        from grimoire.core.agentic_standard import check_evidence_gates

        root = self._stale_project(tmp_path)
        result = check_evidence_gates(root, task_id="bootstrap", target_state=state)
        assert not any(c.id == "gate.codegraph_fresh" for c in result.checks)

    def test_review_transition_still_blocked_on_stale_graph(self, tmp_path: Path) -> None:
        from grimoire.core.agentic_standard import check_evidence_gates

        root = self._stale_project(tmp_path)
        result = check_evidence_gates(root, task_id="bootstrap", target_state="review")
        assert any(c.id == "gate.codegraph_fresh" and c.is_error for c in result.checks)


class TestExclusionsFollowTheRelativePath:
    """W6-02-S1-03 : un projet posé sous un dossier nommé build/ ou venv/ est indexé comme les autres."""

    def test_project_under_build_dir_is_indexed_as_many_files_as_fingerprinted(self, tmp_path: Path) -> None:
        root = _project(tmp_path / "work" / "build" / "proj")
        _, receipt = record_index(root)
        assert sorted(receipt["files"]) == ["a.py", "b.py"]
        assert receipt["stats"]["files"] == len(receipt["files"])
        assert check_freshness(root).verdict == "current"

    def test_parser_filters_on_the_path_below_the_scanned_directory(self, tmp_path: Path) -> None:
        from grimoire.codegraph.graph import CodeGraph

        root = _project(tmp_path / "venv" / "proj")
        (root / "src" / "build").mkdir()
        (root / "src" / "build" / "gen.py").write_text("z = 3\n", encoding="utf-8")
        graph = CodeGraph()
        graph.index_directory(root / "src", exclude={"build", "venv"})
        assert graph.stats()["files"] == 2


def _non_utf8_name_or_skip(directory: Path) -> Path:
    """Fichier dont le nom n'est pas de l'UTF-8 valide, sans littéral de substitut dans la source."""
    target = Path(os.fsdecode(os.fsencode(directory) + b"/caf\xe9.py"))
    try:
        target.write_text("w = 4\n", encoding="utf-8")
    except (OSError, UnicodeError):
        pytest.skip("système de fichiers qui refuse les noms non UTF-8")
    return target


class TestNonUtf8FileName:
    """W6-02-S1-04 : un nom de fichier non UTF-8 ne fait ni planter le verdict ni le gate."""

    def test_record_and_check_survive_the_name(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        record_index(root)
        bad = _non_utf8_name_or_skip(root / "src")
        fresh = check_freshness(root)
        assert fresh.verdict == "stale"
        assert len(fresh.added) == 1
        fresh.added[0].encode("utf-8")  # affichable : rien ne casse la frontière UTF-8 stricte
        json.dumps(fresh.to_dict(), ensure_ascii=False).encode("utf-8")
        _, receipt = record_index(root)
        assert len(receipt["files"]) == 3
        assert check_freshness(root).verdict == "current"
        bad.unlink()
        assert check_freshness(root).verdict == "stale"

    def test_receipt_is_strict_ascii_json(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        _non_utf8_name_or_skip(root / "src")
        record_index(root)
        (root / RECEIPT_PATH).read_bytes().decode("ascii")

    def test_gate_and_mcp_tools_do_not_raise(self, tmp_path: Path) -> None:
        from grimoire.core.standard_checks.gate_codegraph import codegraph_fresh_checks
        from grimoire.mcp.server import grimoire_codegraph_freshness, grimoire_codegraph_index

        root = _project(tmp_path)
        record_index(root)
        _non_utf8_name_or_skip(root / "src")
        (check,) = codegraph_fresh_checks(root)
        check.message.encode("utf-8")
        json.loads(grimoire_codegraph_freshness(str(root)))
        indexed = json.loads(grimoire_codegraph_index(str(root)))
        assert indexed["freshness"]["verdict"] == "current"

    def test_unhashable_source_is_unverified_not_an_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.codegraph import freshness

        root = _project(tmp_path)
        record_index(root)

        def boom(_hashes: dict[str, str]) -> str:
            raise UnicodeEncodeError("utf-8", "x", 0, 1, "boom")

        monkeypatch.setattr(freshness, "_combine", boom)
        fresh = check_freshness(root)
        assert (fresh.verdict, fresh.reason) == ("unverified", "source_unreadable")


class TestNeo4jProjectionReceipt:
    """W6-02-S1-01 : `memory graph sync-code` écrit lui-même le reçu, le verdict suit."""

    def test_sync_code_writes_a_receipt_and_the_verdict_follows(self, tmp_path: Path) -> None:
        from grimoire.memory.projections import sync_code_graph_projection

        root = _project(tmp_path)
        assert check_freshness(root).reason == "no_receipt"
        sync_code_graph_projection(MagicMock(), project_root=root, paths=[Path("src")])
        fresh = check_freshness(root)
        assert (fresh.verdict, fresh.target) == ("current", "neo4j")
        receipt = json.loads((root / RECEIPT_PATH).read_text(encoding="utf-8"))
        assert receipt["target"] == "neo4j"
        assert sorted(receipt["files"]) == ["src/a.py", "src/b.py"]
        (root / "src" / "a.py").write_text("y = 2\n", encoding="utf-8")
        stale = check_freshness(root)
        assert (stale.verdict, stale.modified, stale.target) == ("stale", ("src/a.py",), "neo4j")

    def test_receipt_uses_the_exclusions_of_the_sync(self, tmp_path: Path) -> None:
        from grimoire.memory.projections import sync_code_graph_projection

        root = _project(tmp_path)
        (root / "src" / "gen").mkdir()
        (root / "src" / "gen" / "g.py").write_text("g = 1\n", encoding="utf-8")
        stats = sync_code_graph_projection(MagicMock(), project_root=root, paths=[Path("src")], exclude={"gen"})
        receipt = json.loads((root / RECEIPT_PATH).read_text(encoding="utf-8"))
        assert sorted(receipt["files"]) == ["src/a.py", "src/b.py"]
        assert stats["files"] == len(receipt["files"])
        assert check_freshness(root).verdict == "current"

    def test_failed_upsert_leaves_no_receipt(self, tmp_path: Path) -> None:
        from grimoire.memory.projections import sync_code_graph_projection

        root = _project(tmp_path)
        graph = MagicMock()
        graph.upsert_code_edges.side_effect = RuntimeError("neo4j down")
        with pytest.raises(RuntimeError):
            sync_code_graph_projection(graph, project_root=root, paths=[Path("src")])
        assert not (root / RECEIPT_PATH).exists()

    def test_gate_names_the_sync_command_as_remedy(self, tmp_path: Path) -> None:
        from grimoire.core.standard_checks.gate_codegraph import codegraph_fresh_checks
        from grimoire.memory.projections import sync_code_graph_projection

        root = _project(tmp_path)
        sync_code_graph_projection(MagicMock(), project_root=root, paths=[Path("src")])
        (root / "src" / "a.py").write_text("y = 2\n", encoding="utf-8")
        (check,) = codegraph_fresh_checks(root)
        assert "neo4j" in check.message.lower()
        assert "sync-code" in check.message
