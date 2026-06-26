"""Tests pour la sélection de backend de framework/memory.

Ce module legacy (côté shell-framework, hors package ``grimoire``) est chargé par
chemin de fichier. On valide la résolution d'env var canonique GRIMOIRE_* avec
repli legacy Grimoire_*.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_BACKENDS = Path(__file__).resolve().parents[2] / "framework/memory/backends/__init__.py"


def _load():
    spec = importlib.util.spec_from_file_location("grimoire_fw_memory_backends", _BACKENDS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_url_prefers_canonical_with_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    backends = _load()

    # Casse canonique GRIMOIRE_* prioritaire
    monkeypatch.setenv("GRIMOIRE_QDRANT_URL", "http://canonical:6333")
    monkeypatch.setenv("Grimoire_QDRANT_URL", "http://legacy:6333")
    assert backends._env_url("QDRANT_URL") == "http://canonical:6333"

    # Repli sur l'ancienne casse Grimoire_* (compat)
    monkeypatch.delenv("GRIMOIRE_QDRANT_URL", raising=False)
    assert backends._env_url("QDRANT_URL") == "http://legacy:6333"

    # Défaut quand aucune n'est définie
    monkeypatch.delenv("Grimoire_QDRANT_URL", raising=False)
    assert backends._env_url("QDRANT_URL", "http://default:6333") == "http://default:6333"
