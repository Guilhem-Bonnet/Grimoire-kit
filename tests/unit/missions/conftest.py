"""Aucun test de missions n'appelle le vrai ``gh`` : la sonde de checks du tick est muette par défaut."""

from __future__ import annotations

import pytest

from grimoire.missions import ci_slots


@pytest.fixture(autouse=True)
def _mute_ci_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ci_slots, "default_ci_probe", lambda _branch, _repo: ci_slots.CheckReading(ci_slots.UNKNOWN, "muette"))
