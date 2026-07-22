"""Tests du lien projet <-> BDD mémoire (brique B1)."""

from __future__ import annotations

from pathlib import Path

from grimoire.tools import memory_link as ml


class TestBackendCatalogue:
    def test_catalogue_matches_cli_known_backends(self) -> None:
        from grimoire.cli.cmd_init import KNOWN_BACKENDS

        ids = {b["id"] for b in ml.BACKEND_CATALOGUE}
        assert ids == set(KNOWN_BACKENDS)

    def test_payload_shape(self) -> None:
        cat = ml.backend_catalogue()
        assert cat["schemaVersion"] == ml.MEMORY_LINK_SCHEMA_VERSION
        for b in cat["backends"]:
            assert b["id"] and b["label"] and b["detail"]
            assert b["kind"] in ("local", "server")


class TestMemoryLinkStatus:
    def test_uninitialized_project(self, tmp_path: Path) -> None:
        status = ml.memory_link_status(tmp_path)
        assert status["state"] == "uninitialized"
        assert status["configuredBackend"] is None
        assert status["available"] is False

    def test_initialized_local_backend(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: demo\nmemory:\n  backend: local\n",
            encoding="utf-8",
        )
        status = ml.memory_link_status(tmp_path)
        assert status["configuredBackend"] == "local"
        assert status["state"] == "ok"
        assert status["available"] is True
        assert status["resolvedBackend"] == "local"
        assert isinstance(status["entries"], int)

    def test_no_tree_walk_to_parent_config(self, tmp_path: Path) -> None:
        # Un parent initialisé ne doit PAS contaminer un sous-dossier vierge.
        (tmp_path / "project-context.yaml").write_text(
            "project:\n  name: parent\nmemory:\n  backend: local\n",
            encoding="utf-8",
        )
        child = tmp_path / "sub"
        child.mkdir()
        assert ml.memory_link_status(child)["state"] == "uninitialized"

    def test_broken_config_reports_error(self, tmp_path: Path) -> None:
        (tmp_path / "project-context.yaml").write_text(
            ":: not yaml ::", encoding="utf-8"
        )
        status = ml.memory_link_status(tmp_path)
        assert status["state"] in ("uninitialized", "unavailable")
        assert status["error"]
