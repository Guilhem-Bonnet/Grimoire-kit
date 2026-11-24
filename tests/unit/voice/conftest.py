"""Moteurs simulés : des modules factices dans ``sys.modules``, jamais une bibliothèque audio réelle."""

from __future__ import annotations

import importlib.machinery
import sys
import types
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from grimoire.voice import engines


@pytest.fixture
def fake_module(monkeypatch: pytest.MonkeyPatch) -> Callable[..., types.ModuleType]:
    """Installe un module factice, trouvable par ``find_spec`` et importable, retiré en fin de test."""

    def install(name: str, **attrs: Any) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    return install


@pytest.fixture
def isolated_registry() -> Iterator[dict]:
    """Le registre de moteurs restauré après le test."""
    saved = dict(engines.ENGINES)
    try:
        yield engines.ENGINES
    finally:
        engines.ENGINES.clear()
        engines.ENGINES.update(saved)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ManualTimer:
    """Un minuteur qui ne part jamais seul : le test le déclenche."""

    def __init__(self, delay: float, callback: Callable[[], None]) -> None:
        self.delay = delay
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class TimerLog:
    def __init__(self) -> None:
        self.timers: list[ManualTimer] = []

    def __call__(self, delay: float, callback: Callable[[], None]) -> ManualTimer:
        timer = ManualTimer(delay, callback)
        self.timers.append(timer)
        return timer

    @property
    def armed(self) -> ManualTimer | None:
        live = [t for t in self.timers if not t.cancelled]
        return live[-1] if live else None


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def timers() -> TimerLog:
    return TimerLog()
