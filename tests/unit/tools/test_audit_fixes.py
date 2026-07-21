"""Tests des correctifs d'audit agentique (robustesse & gouvernance).

Couvre : écriture atomique + verrou du board (RUN-14, lost-update), journal
borné (KNO-01), état de features versionné + atomique, rollback transactionnel
de l'installation de hooks (effet-partiel-oublié), garde CSRF/Host du serveur
(backend-permissif) et télémétrie des mutations (QUA-08).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from grimoire.tools import features as feat
from grimoire.tools import stigmergy as stig
from grimoire.tools import stigmergy_hooks as sh
from grimoire.tools.forge_server import ForgeAPI, serve


class TestAtomicAndLock:
    def test_atomic_write_replaces_cleanly(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "f.json"
        stig.atomic_write_text(target, '{"a":1}')
        assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
        # pas de fichier temporaire résiduel
        assert list(target.parent.glob(".*tmp")) == []

    def test_update_board_persists_under_lock(self, tmp_path: Path) -> None:
        stig.update_board(tmp_path, lambda b: stig.emit_pheromone(
            b, ptype="NEED", location="x", text="t", emitter="e"))
        board = stig.load_board(tmp_path)
        assert len(board.pheromones) == 1

    def test_concurrent_updates_no_lost_update(self, tmp_path: Path) -> None:
        """20 émissions concurrentes → 20 signaux (aucune perte)."""
        def worker(i: int) -> None:
            stig.update_board(tmp_path, lambda b, i=i: stig.emit_pheromone(
                b, ptype="PROGRESS", location=f"z{i}", text="t", emitter="a"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(stig.load_board(tmp_path).pheromones) == 20


class TestJournalBounded:
    def test_journal_capped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(stig, "EVENTS_MAX_LINES", 10)
        for _ in range(25):
            stig.log_event(tmp_path, "emit", ptype="NEED")
        events = stig.read_events(tmp_path)
        assert len(events) == 10
        assert events[0]["v"] == stig.EVENTS_SCHEMA_VERSION


class TestFeaturesVersioned:
    def test_schema_version_written(self, tmp_path: Path) -> None:
        feat.set_enabled(tmp_path, "stigmergy-hooks", True)
        state = json.loads((tmp_path / "_grimoire" / "features.json").read_text(encoding="utf-8"))
        assert state["schemaVersion"] == feat.STATE_SCHEMA_VERSION
        # schemaVersion ne pollue pas la liste des features
        ids = {e["id"] for e in feat.list_features(tmp_path)}
        assert "schemaVersion" not in ids

    def test_old_format_read_as_v1(self, tmp_path: Path) -> None:
        """Un état pré-versionnage (sans schemaVersion) se lit comme du v1."""
        path = tmp_path / "_grimoire" / "features.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"stigmergy-hooks": {"enabled": True}}), encoding="utf-8")
        assert feat.is_enabled(tmp_path, "stigmergy-hooks") is True
        # La migration est silencieuse : la prochaine sauvegarde pose le champ
        # sans perdre l'état existant.
        feat.set_enabled(tmp_path, "stigmergy-hooks", False)
        state = json.loads(path.read_text(encoding="utf-8"))
        assert state["schemaVersion"] == feat.STATE_SCHEMA_VERSION
        assert state["stigmergy-hooks"] == {"enabled": False}

    def test_new_format_read(self, tmp_path: Path) -> None:
        path = tmp_path / "_grimoire" / "features.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"schemaVersion": 1, "stigmergy-hooks": {"enabled": True}}),
            encoding="utf-8",
        )
        assert feat.is_enabled(tmp_path, "stigmergy-hooks") is True


class TestInstallRollback:
    def test_partial_failure_rolls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}
        import shutil

        real_copy = shutil.copy2

        def flaky_copy(src, dst, *a, **k):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("disk full (simulé)")
            return real_copy(src, dst, *a, **k)

        monkeypatch.setattr(sh.shutil, "copy2", flaky_copy)
        with pytest.raises(OSError, match="disk full"):
            sh.install_hooks(tmp_path)
        # aucun fichier de hook ne subsiste
        hooks_dir = tmp_path / ".github" / "hooks"
        remaining = list(hooks_dir.rglob("*")) if hooks_dir.exists() else []
        assert [p for p in remaining if p.is_file()] == []


class TestBehaviorThreshold:
    def test_behavior_exposes_target_and_readiness(self, tmp_path: Path) -> None:
        kit = tmp_path / "kit"
        (kit / "extensions").mkdir(parents=True)
        api = ForgeAPI(tmp_path / "proj", kit, ui_dir=None)
        (tmp_path / "proj").mkdir()
        view = api.stigmergy_view()
        b = view["behavior"]
        assert b["targetUsefulRatio"] == 0.4
        assert b["minEmitted"] == 20
        assert isinstance(b["hypothesis"], str) and b["hypothesis"]
        assert b["promotionReady"] is False


class TestServeGuard:
    @pytest.fixture
    def base_url(self, tmp_path: Path):
        kit = tmp_path / "kit"
        (kit / "extensions").mkdir(parents=True)
        (tmp_path / "proj").mkdir()
        server = serve(tmp_path / "proj", kit, ui_dir=None, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_address[1]}"
        server.shutdown()

    def _put(self, url: str, headers: dict[str, str]) -> int:
        body = json.dumps({"id": "x", "nodes": [], "edges": []}).encode()
        req = urllib.request.Request(url, data=body, method="PUT",  # noqa: S310
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_normal_put_allowed(self, base_url: str) -> None:
        assert self._put(base_url + "/api/blueprints/x", {}) == 200

    def test_cross_origin_rejected(self, base_url: str) -> None:
        assert self._put(base_url + "/api/blueprints/x", {"Origin": "https://evil.example"}) == 403

    def test_foreign_host_rejected(self, base_url: str) -> None:
        assert self._put(base_url + "/api/blueprints/x", {"Host": "evil.example"}) == 403

    def test_mutation_is_journaled(self, tmp_path: Path) -> None:
        kit = tmp_path / "kit"
        (kit / "extensions").mkdir(parents=True)
        proj = tmp_path / "proj"
        proj.mkdir()
        server = serve(proj, kit, ui_dir=None, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            body = json.dumps({"enabled": True}).encode()
            req = urllib.request.Request(  # noqa: S310
                url + "/api/features/stigmergy-hooks", data=body, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5):  # noqa: S310
                pass
        finally:
            server.shutdown()
        ledger = proj / "_grimoire-runtime-output" / "hook-runtime" / "serve-mutations.jsonl"
        assert ledger.is_file()
        entry = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
        assert entry["action"] == "feature.toggle"
        assert entry["source"] == "serve"

    def test_blueprint_put_is_journaled(self, tmp_path: Path) -> None:
        kit = tmp_path / "kit"
        (kit / "extensions").mkdir(parents=True)
        proj = tmp_path / "proj"
        proj.mkdir()
        server = serve(proj, kit, ui_dir=None, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            assert self._put(url + "/api/blueprints/x", {}) == 200
        finally:
            server.shutdown()
        ledger = proj / "_grimoire-runtime-output" / "hook-runtime" / "serve-mutations.jsonl"
        assert ledger.is_file()
        entry = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
        assert entry["action"] == "blueprint.put"
        assert entry["id"] == "x"
