"""Chemins d'erreur et gardes du catalogue d'expertises (#618, lot onboarding) :
registre invalide, chemins de skill refusés, `_safe_skill_source`."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core import expertises
from grimoire.core.exceptions import GrimoireRegistryError


class TestLoadCatalogRefusals:
    def _load(self, tmp_path: Path, text: str) -> None:
        path = tmp_path / "expertises.yaml"
        path.write_text(text, encoding="utf-8")
        expertises.load_catalog(catalog_path=path)

    def test_non_mapping_registry_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(GrimoireRegistryError, match="pas un mapping"):
            self._load(tmp_path, "- a\n- b\n")

    def test_unknown_family_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(GrimoireRegistryError, match="famille inconnue"):
            self._load(tmp_path, "version: 1\nmagie:\n  - id: x\n")

    def test_duplicate_id_is_refused(self, tmp_path: Path) -> None:
        entry = (
            "  - id: rust\n    name: Rust\n    summary: s\n    skill: framework/expertises/skills/expertise-rust.md\n"
            "    porteur: stack-engineer\n"
        )
        with pytest.raises(GrimoireRegistryError, match="dupliqué"):
            self._load(tmp_path, "languages:\n" + entry + entry)


class TestResolveKitRelative:
    def test_empty_path_is_refused(self) -> None:
        with pytest.raises(GrimoireRegistryError, match="vide"):
            expertises._resolve_kit_relative("")

    def test_unsupported_root_is_refused(self) -> None:
        with pytest.raises(GrimoireRegistryError, match="non supporté"):
            expertises._resolve_kit_relative("docs/x.md")

    def test_archetypes_and_framework_roots_resolve(self) -> None:
        assert expertises._resolve_kit_relative("archetypes/stack/skills/stack-python.md").name == "stack-python.md"
        assert expertises._resolve_kit_relative("framework/expertises/skills/expertise-rust.md").is_file()


class TestSafeSkillSource:
    @pytest.mark.parametrize("slug", ["", "..", "Stack-Python", "stack_python", "../etc", "a/b", "-x"])
    def test_malformed_slug_is_none(self, tmp_path: Path, slug: str) -> None:
        assert expertises._safe_skill_source(tmp_path, slug) is None

    def test_well_formed_slug_maps_to_a_file_under_the_root(self, tmp_path: Path) -> None:
        got = expertises._safe_skill_source(tmp_path, "stack-python")
        assert got == tmp_path / "stack-python.md"


class TestBundledCatalogPath:
    def test_falls_back_to_the_repo_file_when_the_package_has_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import importlib.resources

        monkeypatch.setattr(expertises, "files", lambda *_a, **_k: Path("/nonexistent"), raising=False)
        monkeypatch.setattr(importlib.resources, "files", lambda *_a, **_k: Path("/nonexistent"))
        path = expertises.bundled_expertise_catalog_path()
        assert path.is_file()
        assert path.name == "expertises.yaml"

    def test_read_agent_skills_of_a_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert expertises._read_agent_skills(tmp_path / "absent.md") == []


class TestTerraformProviders:
    def test_unreadable_tf_file_is_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "main.tf").write_text('provider "aws" {}\n', encoding="utf-8")
        (tmp_path / "broken.tf").write_text('provider "gcp" {}\n', encoding="utf-8")
        real = Path.read_text

        def _read(self: Path, *a: object, **k: object) -> str:
            if self.name == "broken.tf":
                raise OSError("boom")
            return real(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", _read)
        assert expertises._terraform_providers(tmp_path) == {"aws"}
