"""``detect_expertises`` (issue #616) : recommander, jamais attacher.

Réutilise :class:`grimoire.core.scanner.StackScanner` pour les marqueurs de
fichier (même mécanisme que la détection de pile à l'``init``) et ajoute une
détection de fournisseur Terraform pour la famille cloud. Le scénario de
preuve demandé par le chantier : un projet jetable Rust + Terraform AWS doit
recommander ``rust`` et ``aws``, chacun avec une raison nommée.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.expertises import detect_expertises, load_catalog

_CATALOG = load_catalog()


def _ids(recommendations: list) -> set[str]:  # type: ignore[type-arg]
    return {r.id for r in recommendations}


class TestFileMarkerDetection:
    def test_empty_project_recommends_nothing(self, tmp_path: Path) -> None:
        assert detect_expertises(tmp_path, catalog=_CATALOG) == []

    def test_rust_project_is_recommended_with_a_reason(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")

        recommendations = detect_expertises(tmp_path, catalog=_CATALOG)

        rust = next(r for r in recommendations if r.id == "rust")
        assert "Cargo.toml" in rust.reason
        assert rust.confidence > 0

    def test_engineering_family_is_never_recommended(self, tmp_path: Path) -> None:
        """Les compétences d'ingénierie transversales (`detect: null` dans le
        registre) ne sont jamais suggérées automatiquement — seul un choix
        explicite (`grimoire expertise add`) les attache."""
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
        (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")

        recommendations = detect_expertises(tmp_path, catalog=_CATALOG)

        engineering_ids = {e.id for e in _CATALOG if e.family == "engineering"}
        assert not (_ids(recommendations) & engineering_ids)

    def test_stack_scanner_backed_languages_are_recommended(self, tmp_path: Path) -> None:
        """python/typescript/go/csharp/java réutilisent StackScanner — pas un
        second mécanisme de détection."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname = \"demo\"\n", encoding="utf-8")

        recommendations = detect_expertises(tmp_path, catalog=_CATALOG)

        assert "python" in _ids(recommendations)


class TestTerraformProviderDetection:
    def test_aws_provider_block_is_recommended_with_a_reason(self, tmp_path: Path) -> None:
        (tmp_path / "main.tf").write_text('provider "aws" {\n  region = "eu-west-3"\n}\n', encoding="utf-8")

        recommendations = detect_expertises(tmp_path, catalog=_CATALOG)

        aws = next(r for r in recommendations if r.id == "aws")
        assert "aws" in aws.reason
        assert aws.confidence >= 0.9

    def test_unrelated_provider_does_not_recommend_aws(self, tmp_path: Path) -> None:
        (tmp_path / "main.tf").write_text('provider "google" {\n  project = "demo"\n}\n', encoding="utf-8")

        recommendations = detect_expertises(tmp_path, catalog=_CATALOG)

        assert "aws" not in _ids(recommendations)
        assert "gcp" in _ids(recommendations)


class TestTheProofScenario:
    def test_rust_project_with_terraform_aws_recommends_both(self, tmp_path: Path) -> None:
        """Le scénario de preuve du chantier #616 : un projet jetable Rust +
        Terraform AWS -> `expertise list --detected` recommande rust + aws,
        chacun avec une raison nommée."""
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
        (tmp_path / "main.tf").write_text('provider "aws" {\n  region = "eu-west-3"\n}\n', encoding="utf-8")

        recommendations = detect_expertises(tmp_path, catalog=_CATALOG)
        by_id = {r.id: r for r in recommendations}

        assert "rust" in by_id
        assert "aws" in by_id
        assert by_id["rust"].reason
        assert by_id["aws"].reason
