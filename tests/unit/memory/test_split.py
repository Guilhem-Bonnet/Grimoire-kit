"""P28 — ``memory split`` : découpe d'un segment partagé, identifiants conservés, source intacte.

Fixture de la classe héritée : 23 entrées ``project_name=TTS-Voice`` et 1 ``Brainstrom``
dans un même segment, backends factices en mémoire qui supportent l'upsert.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

# Les sous-modules de `memory` (shared, ops, projections, bundle...) sont chargés au premier appel de
# `memory ...` ; importés sous un `patch("grimoire.cli.cmd_memory._load_config_context")`, ils lieraient
# le double pour de bon et fausseraient tous les tests suivants du même processus.
import grimoire.cli.cmd_memory_lexical  # noqa: F401
from grimoire.cli.app import app
from grimoire.core.config import GrimoireConfig
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.manager import MemoryManager
from grimoire.memory.split import SplitError, apply_split, plan_split, set_collection_prefix, target_collection

runner = CliRunner()
SERVER = "http://qdrant.test:6333"


class _Segments:
    """Fabrique de segments en mémoire, un par nom de collection."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.backends: dict[str, LocalMemoryBackend] = {}

    def backend(self, collection: str) -> LocalMemoryBackend:
        if collection not in self.backends:
            self.backends[collection] = LocalMemoryBackend(self.tmp / "segments" / f"{collection}.json")
        return self.backends[collection]

    def manager(self, collection: str) -> MemoryManager:
        return MemoryManager.from_backend(self.backend(collection))


@pytest.fixture
def legacy(tmp_path: Path) -> tuple[_Segments, list[str], list[str]]:
    segments = _Segments(tmp_path)
    shared = segments.backend("grimoire")
    tts = [str(uuid.uuid4()) for _ in range(23)]
    brain = [str(uuid.uuid4())]
    for n, entry_id in enumerate(tts):
        shared.upsert(entry_id, f"voix {n}", user_id="g", tags=("tts",), metadata={"project_name": "TTS-Voice"})
    shared.upsert(brain[0], "idée", user_id="g", metadata={"project_name": "Brainstrom", "superseded_by": tts[0]})
    return segments, tts, brain


def _snapshot(backend: LocalMemoryBackend) -> str:
    return json.dumps(sorted((e.to_dict() for e in backend.get_all()), key=lambda d: d["id"]), sort_keys=True)


class TestPlan:
    def test_groups_the_inherited_segment_by_project(self, legacy: Any) -> None:
        segments, tts, brain = legacy
        plan = plan_split(segments.backend("grimoire").get_all(), source_collection="grimoire")

        by_project = {g.project: g for g in plan.groups}
        assert plan.total == 24 and plan.unassigned == ()
        assert (by_project["TTS-Voice"].count, by_project["Brainstrom"].count) == (23, 1)
        assert set(by_project["TTS-Voice"].ids) == set(tts) and by_project["Brainstrom"].ids == tuple(brain)
        assert by_project["TTS-Voice"].collection == target_collection("grimoire", "TTS-Voice") == "grimoire-tts-voice"

    def test_entries_without_project_stay_in_the_shared_segment(self) -> None:
        from grimoire.memory.backends.base import MemoryEntry

        plan = plan_split(
            [MemoryEntry(id="a", text="x"), MemoryEntry(id="b", text="y", metadata={"project_name": "P"})],
            source_collection="c",
        )
        assert plan.unassigned == ("a",) and [g.project for g in plan.groups] == ["P"]

    def test_only_restricts_to_the_named_projects(self, legacy: Any) -> None:
        segments, _, _ = legacy
        plan = plan_split(segments.backend("grimoire").get_all(), source_collection="grimoire", only=["Brainstrom"])
        assert [g.project for g in plan.groups] == ["Brainstrom"]


class TestApply:
    def _run(self, segments: _Segments, tmp_path: Path, **kw: Any) -> Any:
        source = segments.backend("grimoire")
        plan = plan_split(source.get_all(), source_collection="grimoire", **kw)
        return plan, apply_split(
            plan, source.get_all(), export_path=tmp_path / "export.json", open_target=segments.manager,
        )

    def test_copies_by_upsert_with_identical_ids_and_verified_counts(self, legacy: Any, tmp_path: Path) -> None:
        segments, tts, brain = legacy
        before = _snapshot(segments.backend("grimoire"))

        _, report = self._run(segments, tmp_path)

        assert report.copied == {"Brainstrom": 1, "TTS-Voice": 23}
        assert all(report.verified.values())
        assert {e.id for e in segments.backend("grimoire-tts-voice").get_all()} == set(tts)
        assert [e.id for e in segments.backend("grimoire-brainstrom").get_all()] == brain
        # superseded_by garde sa valeur : l'identifiant visé est conservé (dans l'autre segment).
        moved = segments.backend("grimoire-brainstrom").recall(brain[0])
        assert moved is not None and moved.metadata["superseded_by"] == tts[0]
        # La source n'a pas bougé d'un octet et rien n'y est supprimé.
        assert _snapshot(segments.backend("grimoire")) == before

    def test_export_is_written_before_the_first_copy(self, legacy: Any, tmp_path: Path) -> None:
        segments, tts, _ = legacy
        seen: list[bool] = []
        real = segments.manager

        def open_target(collection: str) -> MemoryManager:
            seen.append((tmp_path / "export.json").is_file())
            return real(collection)

        source = segments.backend("grimoire")
        plan = plan_split(source.get_all(), source_collection="grimoire")
        apply_split(plan, source.get_all(), export_path=tmp_path / "export.json", open_target=open_target)

        assert seen and all(seen)
        exported = json.loads((tmp_path / "export.json").read_text(encoding="utf-8"))
        assert exported["count"] == 24 and {e["id"] for e in exported["entries"]} >= set(tts)

    def test_rerun_creates_no_duplicate(self, legacy: Any, tmp_path: Path) -> None:
        segments, _, _ = legacy
        self._run(segments, tmp_path)
        self._run(segments, tmp_path)
        assert segments.backend("grimoire-tts-voice").count() == 23

    def test_failed_copy_updates_no_config_and_leaves_the_source_intact(self, legacy: Any, tmp_path: Path) -> None:
        segments, _, _ = legacy
        config = tmp_path / "project-context.yaml"
        config.write_text("project:\n  name: TTS-Voice\nmemory:\n  collection_prefix: grimoire\n", encoding="utf-8")
        before = _snapshot(segments.backend("grimoire"))
        calls = {"n": 0}

        class _Flaky:
            def __init__(self, inner: MemoryManager) -> None:
                self.inner = inner

            def upsert(self, *a: Any, **k: Any) -> Any:
                calls["n"] += 1
                if calls["n"] == 5:
                    raise OSError("serveur coupé")
                return self.inner.upsert(*a, **k)

            def get_all(self) -> Any:
                return self.inner.get_all()

        source = segments.backend("grimoire")
        plan = plan_split(source.get_all(), source_collection="grimoire", configs={"TTS-Voice": config, "Brainstrom": config})
        updated: list[Path] = []
        with pytest.raises(SplitError, match="intactes"):
            apply_split(
                plan, source.get_all(), export_path=tmp_path / "e.json",
                open_target=lambda c: _Flaky(segments.manager(c)),
                update_config=lambda p, c: updated.append(p),
            )

        assert updated == []
        assert "collection_prefix: grimoire\n" in config.read_text(encoding="utf-8")
        assert _snapshot(segments.backend("grimoire")) == before
        assert (tmp_path / "e.json").is_file()

    def test_a_count_mismatch_is_caught_by_the_verification(self, legacy: Any, tmp_path: Path) -> None:
        segments, _, _ = legacy

        class _Lossy:
            def __init__(self, inner: MemoryManager) -> None:
                self.inner = inner
                self.seen = 0

            def upsert(self, entry_id: str, *a: Any, **k: Any) -> Any:
                self.seen += 1  # la première écriture est « perdue » en silence
                return None if self.seen == 1 else self.inner.upsert(entry_id, *a, **k)

            def get_all(self) -> Any:
                return self.inner.get_all()

        source = segments.backend("grimoire")
        plan = plan_split(source.get_all(), source_collection="grimoire", only=["TTS-Voice"])
        with pytest.raises(SplitError, match="vérification échouée"):
            apply_split(
                plan, source.get_all(), export_path=tmp_path / "e.json", open_target=lambda c: _Lossy(segments.manager(c)),
            )

    def test_source_changed_since_the_preview_is_refused(self, legacy: Any, tmp_path: Path) -> None:
        segments, _, _ = legacy
        source = segments.backend("grimoire")
        plan = plan_split(source.get_all(), source_collection="grimoire")
        source.upsert(str(uuid.uuid4()), "arrivée tardive", metadata={"project_name": "TTS-Voice"})
        with pytest.raises(SplitError, match="a changé"):
            apply_split(plan, source.get_all(), export_path=tmp_path / "e.json", open_target=segments.manager)


class TestConfigRewrite:
    def test_sets_collection_prefix_and_keeps_comments(self, tmp_path: Path) -> None:
        config = tmp_path / "project-context.yaml"
        config.write_text("project:\n  name: P  # nom\nmemory:\n  backend: qdrant-server  # serveur\n", encoding="utf-8")
        set_collection_prefix(config, "grimoire-p")
        text = config.read_text(encoding="utf-8")
        assert "collection_prefix: grimoire-p" in text and "# nom" in text and "# serveur" in text


# ── CLI ──────────────────────────────────────────────────────────────────────


def _project(root: Path, name: str, prefix: str = "grimoire") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-context.yaml").write_text(
        f"project:\n  name: {name}\n  type: generic\n  stack: []\n"
        f"memory:\n  backend: qdrant-server\n  qdrant_url: {SERVER}\n  collection_prefix: {prefix}\n"
        "agents:\n  archetype: minimal\n",
        encoding="utf-8",
    )
    return root


class TestCli:
    @pytest.fixture
    def world(self, tmp_path: Path, legacy: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        segments, tts, brain = legacy
        tts_root = _project(tmp_path / "tts", "TTS-Voice")
        brain_root = _project(tmp_path / "brain", "Brainstrom")
        other_root = _project(tmp_path / "autre", "Autre", prefix="ailleurs")
        home = tmp_path / "cockpit"
        home.mkdir()
        (home / "registry.json").write_text(
            json.dumps([{"name": n, "path": str(p), "slug": n.lower()} for n, p in
                        (("TTS-Voice", tts_root), ("Brainstrom", brain_root), ("Autre", other_root))]),
            encoding="utf-8",
        )
        monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(home))
        return {"segments": segments, "tts": tts, "brain": brain, "roots": (tts_root, brain_root, other_root), "tmp": tmp_path}

    def _invoke(self, world: dict[str, Any], *args: str) -> Any:
        tts_root = world["roots"][0]
        cfg = GrimoireConfig.from_yaml(tts_root / "project-context.yaml")
        segments: _Segments = world["segments"]
        with (
            patch("grimoire.cli.cmd_memory_ops._load_config_context", return_value=(cfg, tts_root)),
            patch(
                "grimoire.cli.cmd_memory_ops.MemoryManager.from_config",
                side_effect=lambda c, project_root=None: segments.manager(c.memory.collection_prefix),
            ),
        ):
            return runner.invoke(app, ["-o", "json", "memory", "split", *args])

    def test_preview_by_default_writes_nothing(self, world: dict[str, Any]) -> None:
        segments: _Segments = world["segments"]
        before = _snapshot(segments.backend("grimoire"))

        result = self._invoke(world)

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["apply"] is False and data["report"] is None
        assert {(g["project"], g["count"]) for g in data["groups"]} == {("TTS-Voice", 23), ("Brainstrom", 1)}
        assert set(segments.backends) == {"grimoire"}  # aucune collection cible créée
        assert _snapshot(segments.backend("grimoire")) == before
        assert not list((world["roots"][0] / "_grimoire").rglob("split-*.json"))
        assert "grimoire-tts-voice" not in (world["roots"][0] / "project-context.yaml").read_text(encoding="utf-8")

    def test_apply_exports_copies_verifies_and_repoints_each_registry_project(self, world: dict[str, Any]) -> None:
        segments: _Segments = world["segments"]
        before = _snapshot(segments.backend("grimoire"))
        export = world["tmp"] / "sauvegarde.json"

        result = self._invoke(world, "--apply", "--export", str(export))

        assert result.exit_code == 0, result.output
        report = json.loads(result.output)["report"]
        assert report["copied"] == {"Brainstrom": 1, "TTS-Voice": 23} and all(report["verified"].values())
        assert json.loads(export.read_text(encoding="utf-8"))["count"] == 24
        assert {e.id for e in segments.backend("grimoire-tts-voice").get_all()} == set(world["tts"])
        assert [e.id for e in segments.backend("grimoire-brainstrom").get_all()] == world["brain"]
        tts_root, brain_root, other_root = world["roots"]
        assert "collection_prefix: grimoire-tts-voice" in (tts_root / "project-context.yaml").read_text(encoding="utf-8")
        assert "collection_prefix: grimoire-brainstrom" in (brain_root / "project-context.yaml").read_text(encoding="utf-8")
        # Un projet qui ne lisait pas ce segment n'est pas touché.
        assert "collection_prefix: ailleurs" in (other_root / "project-context.yaml").read_text(encoding="utf-8")
        assert _snapshot(segments.backend("grimoire")) == before  # aucune suppression dans le même geste

    def test_failure_midway_exits_1_and_keeps_every_config(self, world: dict[str, Any]) -> None:
        segments: _Segments = world["segments"]
        real = segments.manager

        def flaky(collection: str) -> Any:
            mgr = real(collection)
            if collection == "grimoire-tts-voice":
                original = mgr.upsert
                count = {"n": 0}

                def upsert(*a: Any, **k: Any) -> Any:
                    count["n"] += 1
                    if count["n"] == 10:
                        raise OSError("coupure")
                    return original(*a, **k)

                mgr.upsert = upsert  # type: ignore[method-assign]
            return mgr

        segments.manager = flaky  # type: ignore[method-assign]
        result = self._invoke(world, "--apply", "--export", str(world["tmp"] / "s.json"))

        assert result.exit_code == 1
        assert "intactes" in result.output
        for root in world["roots"][:2]:
            assert "collection_prefix: grimoire\n" in (root / "project-context.yaml").read_text(encoding="utf-8")
        assert segments.backend("grimoire").count() == 24
