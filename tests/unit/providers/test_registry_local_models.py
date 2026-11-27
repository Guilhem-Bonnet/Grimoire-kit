"""``add_local_models`` : déclarer au registre des modèles Ollama détectés (épique #1477, lot B1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.providers.registry import (
    ModelSpec,
    ProviderRegistryError,
    ProviderWriteError,
    add_local_models,
    read_registry,
)

REGISTRY_PATH = Path("_grimoire/standard/llm-provider-registry.yaml")


def _project(tmp_path: Path, body: str = "providers: []\n") -> Path:
    (tmp_path / REGISTRY_PATH).parent.mkdir(parents=True)
    (tmp_path / REGISTRY_PATH).write_text(body, encoding="utf-8")
    return tmp_path


def test_cree_le_fournisseur_local_puis_ajoute_sans_dupliquer(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert add_local_models(root, (ModelSpec("a:1b", "cheap"), ModelSpec("b:8b", "mid"))) == ("a:1b", "b:8b")
    provider = next(p for p in read_registry(root) if p.id == "ollama")
    assert (provider.provider_type, provider.currency, provider.invocation) == ("local", "local", "ollama run {model} {prompt}")
    assert add_local_models(root, (ModelSpec("a:1b", "cheap"), ModelSpec("c:3b", "cheap"))) == ("c:3b",)
    assert [m.id for p in read_registry(root) if p.id == "ollama" for m in p.models] == ["a:1b", "b:8b", "c:3b"]
    assert add_local_models(root, (ModelSpec("a:1b", "cheap"),)) == ()


def test_liste_vide_ne_fait_rien_et_palier_invalide_est_refuse(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert add_local_models(root, ()) == ()
    assert not any(p.id == "ollama" for p in read_registry(root))
    with pytest.raises(ProviderWriteError, match="doit être l'un de"):
        add_local_models(root, (ModelSpec("a:1b", "premium"),))


def test_registre_absent_est_une_erreur_claire(tmp_path: Path) -> None:
    with pytest.raises(ProviderRegistryError, match="standard init"):
        add_local_models(tmp_path, (ModelSpec("a:1b", "cheap"),))
