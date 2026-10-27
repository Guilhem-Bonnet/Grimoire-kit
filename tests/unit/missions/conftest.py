"""Aucun test de missions n'appelle le vrai ``gh`` : la sonde de checks du tick est muette par défaut."""

from __future__ import annotations

import pytest

from grimoire.missions import ci_slots


@pytest.fixture(autouse=True)
def _mute_ci_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ci_slots,
        "default_ci_probe",
        lambda branches, _repo, _cwd: {b: ci_slots.CheckReading(ci_slots.UNKNOWN, "muette") for b in branches},
    )
    # Le dépôt vient de la politique ou du remote ``origin`` : aucun test ne lance le vrai ``git`` pour le trouver.
    monkeypatch.setattr(ci_slots, "ci_repository", lambda _home, _project: "acme/kit")


@pytest.fixture(autouse=True)
def _no_resolve_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """La résolution d'une session de fond relit `claude agents` avec une pause réelle : aucune dans les tests (#966)."""
    from grimoire.missions import launcher

    monkeypatch.setattr(launcher, "_RESOLVE_PAUSE_S", 0.0)
