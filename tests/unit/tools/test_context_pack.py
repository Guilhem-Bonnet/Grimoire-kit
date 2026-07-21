"""Tests du context-pack durable de repo (porté d'un hook d'atelier)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from grimoire.tools import context_pack as cp


def _fixed_now() -> datetime:
    return datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)


class TestContextPack:
    def test_conforms_to_catalogue_contract(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Repo\n", encoding="utf-8")
        pack = cp.build_context_pack(tmp_path, now=_fixed_now())
        required = {
            "mission_id",
            "context_profile",
            "budget",
            "objective",
            "included_sources",
            "constraints",
        }
        assert required <= set(pack)
        assert pack["contract"] == "context-pack"
        assert pack["schemaVersion"] == cp.CONTEXT_PACK_SCHEMA_VERSION

    def test_included_and_excluded_sources(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("gouvernance\n", encoding="utf-8")
        pack = cp.build_context_pack(tmp_path, now=_fixed_now())
        included = {s["path"] for s in pack["included_sources"]}
        excluded = {s["path"] for s in pack["excluded_sources"]}
        assert "CLAUDE.md" in included
        assert "README.md" in excluded  # absent → exclu
        gov = next(s for s in pack["included_sources"] if s["path"] == "CLAUDE.md")
        assert gov["confidence"] == "high"
        assert "sha256" in gov

    def test_sufficiency_and_open_questions(self, tmp_path: Path) -> None:
        # Sans source de gouvernance → partial + question ouverte.
        (tmp_path / "pyproject.toml").write_text("[x]\n", encoding="utf-8")
        pack = cp.build_context_pack(tmp_path, now=_fixed_now())
        assert pack["scorecard"]["sufficiency"] == "partial"
        assert pack["open_questions"]
        # Avec README → sufficient, pas de question.
        (tmp_path / "README.md").write_text("# R\n", encoding="utf-8")
        pack2 = cp.build_context_pack(tmp_path, now=_fixed_now())
        assert pack2["scorecard"]["sufficiency"] == "sufficient"
        assert pack2["open_questions"] == []

    def test_expiry_ttl(self, tmp_path: Path) -> None:
        pack = cp.build_context_pack(tmp_path, now=_fixed_now(), ttl_days=7)
        assert pack["expiry"]["ttlDays"] == 7
        assert pack["expiry"]["expiresAt"] == "2026-07-28T12:00:00+00:00"
        assert pack["expiry"]["invalidateOn"] == "git HEAD change"

    def test_default_output_path(self, tmp_path: Path) -> None:
        out = cp.default_output_path(tmp_path)
        assert out.parent.name == "repo-contexts"
        assert out.name.endswith(".context-pack.json")
