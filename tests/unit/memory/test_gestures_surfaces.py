"""P23 — la CLI et l'outil MCP : mêmes gestes, mêmes gardes, mêmes refus.

``memory retract|unretract|tags`` (CLI) et ``grimoire_memory_retract|unretract|
update_tags`` (MCP) passent tous par ``MemoryManager`` via ``run_gesture`` et
écrivent le journal des écritures (P14). Aucun outil MCP n'est plus permissif que
son équivalent CLI : on le prouve en rejouant les mêmes scénarios des deux côtés.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.config import GrimoireConfig
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.journal import JOURNAL_RELPATH
from grimoire.memory.manager import MemoryManager
from grimoire.memory.supersession import RETRACTED_KEY

pytest.importorskip("mcp", reason="extra optionnel grimoire-kit[mcp] non installé")

runner = CliRunner()
TEXT = "Décision : le backend vectoriel retenu est Qdrant."


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)


def _project(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "project-context.yaml").write_text(
        "project:\n  name: demo\n  type: library\n  stack: [python]\n"
        "user:\n  name: G\n  language: Français\n  skill_level: expert\n"
        "memory:\n  backend: local\nagents:\n  archetype: minimal\n",
        encoding="utf-8",
    )
    (root / "_grimoire" / "_memory").mkdir(parents=True)
    return root


def _manager(root: Path) -> MemoryManager:
    return MemoryManager.from_config(GrimoireConfig.find_and_load(root), project_root=root)


def _seed(root: Path) -> tuple[str, str]:
    """Un souvenir du projet et un souvenir d'un autre projet, dans le même store."""
    mgr = _manager(root)
    own = mgr.store(TEXT, user_id="u", tags=("adr",)).id
    foreign = mgr.backend.store(
        "Souvenir d'un autre projet", user_id="u", tags=("t",),
        metadata={"project_name": "autre", "source_kind": "memory"},
    ).id
    return own, foreign


def _journal(root: Path) -> list[dict[str, Any]]:
    path = root / JOURNAL_RELPATH
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _comparable(body: dict[str, Any]) -> dict[str, Any]:
    """Le corps sans ce qui diffère d'une surface à l'autre par nature : l'id, et
    l'horodatage du retrait (deux appels successifs peuvent changer de seconde)."""
    out: dict[str, Any] = {**body, "id": "ID"}
    if isinstance(out.get("retracted"), dict):
        out["retracted"] = {**out["retracted"], "at": "AT"}
    return out


def _mcp_body(result: Any) -> tuple[bool, dict[str, Any]]:
    """``(est_une_erreur, corps_json)`` d'un résultat d'outil MCP.

    Selon la version du SDK, un appel direct rend une chaîne JSON ou un ``CallToolResult`` : on lit
    le corps dans les deux cas, et l'erreur est ``isError`` OU la clé ``error`` du corps (contrat
    stable des outils du kit, voir ``_tool_error``).
    """
    if isinstance(result, str):
        body = json.loads(result)
        flagged = False
    else:
        body = json.loads("".join(getattr(block, "text", "") for block in result.content))
        flagged = bool(getattr(result, "isError", False))
    return flagged or "error" in body, body


def _cli(root: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> tuple[int, dict[str, Any]]:
    monkeypatch.chdir(root)
    out = runner.invoke(app, ["-o", "json", "memory", *args])
    body = json.loads(out.output) if out.output.strip().startswith("{") else {"raw": out.output}
    return out.exit_code, body


class TestSameGestures:
    def test_retract_unretract_tags_give_the_same_payload_on_both_surfaces(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from grimoire.mcp import memory_gestures as mcp

        cli_root, mcp_root = _project(tmp_path / "cli"), _project(tmp_path / "mcp")
        cli_id, _ = _seed(cli_root)
        mcp_id, _ = _seed(mcp_root)

        steps = [
            (("retract", cli_id, "--reason", "périmée"), lambda: mcp.grimoire_memory_retract(mcp_id, "périmée", str(mcp_root))),
            (("unretract", cli_id), lambda: mcp.grimoire_memory_unretract(mcp_id, project_path=str(mcp_root))),
            (
                ("tags", cli_id, "--add", "revue,x", "--remove", "adr"),
                lambda: mcp.grimoire_memory_update_tags(
                    mcp_id, add=["revue", "x"], remove=["adr"], project_path=str(mcp_root),
                ),
            ),
            (
                ("tags", cli_id, "--set", "seul"),
                lambda: mcp.grimoire_memory_update_tags(mcp_id, replace=["seul"], project_path=str(mcp_root)),
            ),
        ]
        for cli_args, call in steps:
            code, cli_body = _cli(cli_root, monkeypatch, *cli_args)
            is_error, mcp_body = _mcp_body(call())
            assert code == 0 and not is_error, (cli_args, cli_body, mcp_body)
            assert _comparable(cli_body) == _comparable(mcp_body)

        cli_rows = [r["op"] for r in _journal(cli_root) if r["op"] != "store"]
        mcp_rows = [r["op"] for r in _journal(mcp_root) if r["op"] != "store"]
        assert cli_rows == mcp_rows == ["retract", "unretract", "update_tags", "update_tags"]

    def test_both_call_the_same_entry_point_with_the_same_arguments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from grimoire.mcp import memory_gestures as mcp

        calls: list[tuple[Any, ...]] = []

        def spy(mgr: Any, gesture: str, entry_id: str, **kw: Any) -> dict[str, Any]:
            calls.append((gesture, entry_id, kw.get("reason"), sorted(kw)))
            return {"ok": True, "gesture": gesture, "id": entry_id, "state": "live", "tags": []}

        monkeypatch.setattr("grimoire.memory.gestures.run_gesture", spy)
        root = _project(tmp_path / "p")
        _cli(root, monkeypatch, "retract", "E1", "--reason", "r")
        mcp.grimoire_memory_retract("E1", "r", str(root))
        assert calls[0] == calls[1]


class TestSameRefusals:
    def test_a_foreign_projects_memory_is_refused_on_both_surfaces(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from grimoire.mcp import memory_gestures as mcp

        root = _project(tmp_path / "p")
        _, foreign = _seed(root)
        attempts = [
            (("retract", foreign, "--reason", "x"), lambda: mcp.grimoire_memory_retract(foreign, "x", str(root))),
            (("unretract", foreign), lambda: mcp.grimoire_memory_unretract(foreign, project_path=str(root))),
            (
                ("tags", foreign, "--add", "x"),
                lambda: mcp.grimoire_memory_update_tags(foreign, add=["x"], project_path=str(root)),
            ),
        ]
        for cli_args, call in attempts:
            code, cli_body = _cli(root, monkeypatch, *cli_args)
            is_error, mcp_body = _mcp_body(call())
            assert code == 1 and is_error, (cli_args, cli_body, mcp_body)
            assert "appartient au projet" in cli_body["error"]
            assert "appartient au projet" in mcp_body["error"]
        kept = _manager(root).backend.recall(foreign)
        assert kept is not None
        assert kept.tags == ("t",)
        assert not kept.metadata.get(RETRACTED_KEY)
        assert not [r for r in _journal(root) if r["op"] != "store"]

    def test_a_backend_that_cannot_update_is_refused_on_both_surfaces_and_nothing_is_deleted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from grimoire.mcp import memory_gestures as mcp

        root = _project(tmp_path / "p")
        own, _ = _seed(root)

        def no_update(*_a: Any, **_k: Any) -> None:
            raise NotImplementedError("pas de update")

        monkeypatch.setattr(LocalMemoryBackend, "update", no_update)
        code, cli_body = _cli(root, monkeypatch, "retract", own, "--reason", "x")
        is_error, mcp_body = _mcp_body(mcp.grimoire_memory_retract(own, "x", str(root)))
        assert code == 1 and is_error
        assert "ne sait pas modifier" in cli_body["error"]
        assert "ne sait pas modifier" in mcp_body["error"]
        kept = _manager(root).backend.recall(own)
        assert kept is not None
        assert kept.text == TEXT

    def test_a_reason_is_required_on_both_surfaces(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.mcp import memory_gestures as mcp

        root = _project(tmp_path / "p")
        own, _ = _seed(root)
        code, _ = _cli(root, monkeypatch, "retract", own)
        is_error, _ = _mcp_body(mcp.grimoire_memory_retract(own, "", str(root)))
        assert code == 1 and is_error

    def test_an_unknown_id_is_a_refusal_not_a_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.mcp import memory_gestures as mcp

        root = _project(tmp_path / "p")
        _seed(root)
        code, cli_body = _cli(root, monkeypatch, "retract", "fantome", "--reason", "x")
        is_error, mcp_body = _mcp_body(mcp.grimoire_memory_retract("fantome", "x", str(root)))
        assert code == 1 and is_error
        assert "introuvable" in cli_body["error"]
        assert "introuvable" in mcp_body["error"]


class TestReadsThroughBothSurfaces:
    def test_retracted_is_hidden_by_default_and_shown_on_request_on_both(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from grimoire.mcp.server import grimoire_memory_search

        root = _project(tmp_path / "p")
        own, _ = _seed(root)
        _cli(root, monkeypatch, "retract", own, "--reason", "périmée")

        _, hidden = _mcp_body(grimoire_memory_search("backend vectoriel", project_path=str(root)))
        _, shown = _mcp_body(grimoire_memory_search("backend vectoriel", project_path=str(root), include_retracted=True))
        assert own not in {r["id"] for r in hidden["results"]}
        assert own in {r["id"] for r in shown["results"]}

        monkeypatch.chdir(root)
        out = runner.invoke(app, ["-o", "json", "memory", "search", "backend vectoriel", "--no-hybrid"])
        assert own not in {e["id"] for e in json.loads(out.output)}
        out = runner.invoke(app, ["-o", "json", "memory", "search", "backend vectoriel", "--no-hybrid", "--include-retracted"])
        assert own in {e["id"] for e in json.loads(out.output)}

    def test_show_reads_a_retracted_memory_with_its_state(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _project(tmp_path / "p")
        own, _ = _seed(root)
        _cli(root, monkeypatch, "retract", own, "--reason", "périmée")
        code, body = _cli(root, monkeypatch, "show", own)
        assert code == 0
        assert body["state"] == "retracted"
        assert body["text"] == TEXT
        assert body["metadata"][RETRACTED_KEY]["reason"] == "périmée"

    def test_export_keeps_retracted_memories(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _project(tmp_path / "p")
        own, _ = _seed(root)
        _cli(root, monkeypatch, "retract", own, "--reason", "périmée")
        code, body = _cli(root, monkeypatch, "export")
        assert code == 0
        assert own in {e["id"] for e in body["entries"]}

    def test_reads_command_reports_recall_count_from_the_journal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from grimoire.mcp.server import grimoire_memory_search

        root = _project(tmp_path / "p")
        own, _ = _seed(root)
        grimoire_memory_search("backend vectoriel", project_path=str(root))
        grimoire_memory_search("backend vectoriel", project_path=str(root))
        code, body = _cli(root, monkeypatch, "reads", "--days", "7")
        assert code == 0
        assert body["recall_count_7d"][own] == 2
        assert body["reads"] == 2


class TestReadReceiptsStayReadOnly:
    """``readOnlyHint`` reste vrai : le reçu est la seule écriture admise (voir TestReadOnlyToolsWriteNothing)."""

    def test_search_and_task_recall_are_read_only_and_write_only_the_receipt(self, tmp_path: Path) -> None:
        from grimoire.mcp import server
        from grimoire.mcp.server import annotation_hints

        tools = {t.name: t for t in server.mcp._tool_manager.list_tools()}
        for name in ("grimoire_memory_search", "task_recall"):
            assert annotation_hints(tools[name].annotations).get("readOnlyHint") is True
        for name in ("grimoire_memory_retract", "grimoire_memory_unretract", "grimoire_memory_update_tags"):
            hints = annotation_hints(tools[name].annotations)
            assert not hints.get("readOnlyHint")
            assert hints.get("destructiveHint") is False, f"{name} ne supprime rien"

        root = _project(tmp_path / "p")
        _seed(root)
        before = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        server.grimoire_memory_search("backend vectoriel", project_path=str(root))
        after = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        assert after - before == {"_grimoire-output/memory-reads.jsonl"}


class _RecordingGraph:
    """Un graphe qui retient ce que les commandes de maintenance lui demandent."""

    def __init__(self) -> None:
        self.upserted: list[str] = []
        self.known: frozenset[str] = frozenset()
        self.purged: list[str] = []

    def upsert_memory(self, entry: Any) -> None:
        self.upserted.append(entry.id)

    def find_orphan_memory_nodes(self, *, known_ids: frozenset[str], collection: str) -> list[dict[str, str]]:
        self.known = known_ids
        return []

    def purge_memory_nodes(self, ids: list[str]) -> int:
        self.purged.extend(ids)
        return len(ids)

    def close(self) -> None:
        pass


class TestMaintenancePathsLoseNothing:
    """Retirer ne doit rien faire perdre aux chemins de maintenance : le souvenir retiré existe encore."""

    @pytest.fixture
    def retracted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, _RecordingGraph]:
        root = _project(tmp_path / "p")
        own, _ = _seed(root)
        _cli(root, monkeypatch, "retract", own, "--reason", "périmée")
        graph = _RecordingGraph()
        monkeypatch.setattr("grimoire.cli.cmd_memory_ops._load_neo4j_graph", lambda *a, **k: graph)
        return root, own, graph

    def test_purge_orphans_does_not_treat_a_retracted_memory_as_an_orphan(
        self, retracted: tuple[Path, str, _RecordingGraph], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root, own, graph = retracted
        code, _ = _cli(root, monkeypatch, "graph", "purge-orphans", "--apply")
        assert code == 0
        assert own in graph.known, "le souvenir retiré doit compter parmi les souvenirs connus du store"

    def test_sync_memories_projects_a_retracted_memory_with_its_mark(
        self, retracted: tuple[Path, str, _RecordingGraph], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root, own, graph = retracted
        code, _ = _cli(root, monkeypatch, "graph", "sync-memories")
        assert code == 0
        assert own in graph.upserted
