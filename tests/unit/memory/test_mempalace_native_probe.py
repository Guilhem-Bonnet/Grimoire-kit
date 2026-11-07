"""Sonde de l'index natif de chromadb avant d'ouvrir le backend ``mempalace`` (#1059).

Constat : sur un processeur sans les instructions de la roue native de
``hnswlib``, ``chromadb.PersistentClient`` charge ``local_hnsw`` dans le
processus courant et le tue d'une instruction illégale (SIGILL) — pas une
exception Python, aucun message. La sonde essaie cet import dans un
sous-processus, cache son verdict sous l'état utilisateur (clé : version de
chromadb + machine) et refuse ``mempalace`` avec les replis ``local`` /
``lexical``.

Aucun test ici n'exige chromadb : le sous-processus est simulé.
"""

from __future__ import annotations

import json
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from grimoire.memory import chromadb_native_probe as probe
from grimoire.memory.chromadb_native_probe import (
    ChromaNativeIndexUnavailableError,
    NativeIndexVerdict,
    ensure_native_index,
    probe_native_index,
    refusal_message,
)

_VERSION = "0.6.2"


@pytest.fixture(autouse=True)
def _fake_chromadb_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """La sonde est sans objet si chromadb n'est pas installé : on fige sa version."""
    monkeypatch.setattr(probe, "installed_chromadb_version", lambda: _VERSION)


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["python", "-c"], returncode=returncode, stdout="", stderr="")


class _Runner:
    """Sous-processus simulé : rend le code demandé et compte ses lancements."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls += 1
        return _completed(self.returncode)


def _forbidden_runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
    raise AssertionError("un second sous-processus a été lancé alors que le cache était valide")


def test_signal_death_is_refused_with_a_clear_fallback(tmp_path: Path) -> None:
    """Le sous-processus meurt de SIGILL : refus Python clair, jamais le processus appelant."""
    runner = _Runner(-signal.SIGILL)

    verdict = probe_native_index(cache_path=tmp_path / "probe.json", runner=runner)

    assert verdict.ok is False
    assert verdict.signal_name == "SIGILL"
    assert "SIGILL" in verdict.detail
    assert verdict.from_cache is False
    assert runner.calls == 1

    with pytest.raises(ChromaNativeIndexUnavailableError) as excinfo:
        ensure_native_index(cache_path=tmp_path / "probe.json", runner=_forbidden_runner)

    message = str(excinfo.value)
    assert "SIGILL" in message
    assert "local" in message, "le refus doit proposer le repli local"
    assert "lexical" in message, "le refus doit proposer le repli lexical"


def test_signal_verdict_is_cached_and_not_reprobed(tmp_path: Path) -> None:
    """Le verdict fatal est relu dans le cache : aucun second sous-processus."""
    cache = tmp_path / "probe.json"
    first = _Runner(-signal.SIGILL)

    initial = probe_native_index(cache_path=cache, runner=first)
    second = _Runner(0)  # dirait « sain » s'il était lancé
    cached = probe_native_index(cache_path=cache, runner=second)

    assert initial.from_cache is False
    assert first.calls == 1
    assert second.calls == 0, "le cache doit éviter toute nouvelle sonde"
    assert cached.ok is False
    assert cached.signal_name == "SIGILL"
    assert cached.from_cache is True

    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["chromadb_version"] == _VERSION
    assert payload["signal"] == "SIGILL"
    assert payload["machine_key"]


def test_healthy_native_import_is_unchanged(tmp_path: Path) -> None:
    """Un import sain ne refuse rien, et son verdict sain est lui aussi relu du cache."""
    cache = tmp_path / "probe.json"
    runner = _Runner(0)

    verdict = probe_native_index(cache_path=cache, runner=runner)

    assert verdict.ok is True
    assert verdict.signal_name == ""
    assert verdict.from_cache is False
    assert _VERSION in verdict.detail

    accepted = ensure_native_index(cache_path=cache, runner=runner)
    assert accepted.ok is True
    assert runner.calls == 1, "le second appel doit relire le cache sain, pas re-sonder"


def test_cache_key_follows_the_chromadb_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une autre version de chromadb invalide le verdict : la roue native peut changer."""
    cache = tmp_path / "probe.json"
    probe_native_index(cache_path=cache, runner=_Runner(-signal.SIGILL))
    monkeypatch.setattr(probe, "installed_chromadb_version", lambda: "0.7.0")

    runner = _Runner(0)
    verdict = probe_native_index(cache_path=cache, runner=runner)

    assert runner.calls == 1, "un cache d'une autre version ne doit pas être relu"
    assert verdict.ok is True


def test_non_signal_failure_is_not_cached(tmp_path: Path) -> None:
    """Un échec d'installation (code > 0) peut se corriger : il ne fige pas le refus."""
    cache = tmp_path / "probe.json"

    failed = probe_native_index(cache_path=cache, runner=_Runner(1))
    assert failed.ok is False
    assert not cache.exists(), "seul un signal est un verdict machine stable"

    runner = _Runner(0)
    retried = probe_native_index(cache_path=cache, runner=runner)
    assert runner.calls == 1
    assert retried.ok is True


def test_refusal_message_names_both_fallbacks() -> None:
    verdict = NativeIndexVerdict(
        ok=False,
        detail="l'import natif de chromadb meurt d'un signal SIGILL (Illegal instruction)",
        chromadb_version=_VERSION,
        signal_name="SIGILL",
    )

    message = refusal_message(verdict)

    assert _VERSION in message
    assert "SIGILL" in message
    assert "« local »" in message
    assert "« lexical »" in message


def _doctor_config(backend: str) -> SimpleNamespace:
    """Le strict nécessaire de ``GrimoireConfig`` pour les contrôles mémoire de doctor."""
    return SimpleNamespace(memory=SimpleNamespace(backend=backend, layer_profile=""))


def _doctor_results(monkeypatch: pytest.MonkeyPatch, backend: str) -> list[dict[str, object]]:
    """Exécute les contrôles mémoire de ``doctor`` avec chromadb « installé » et une sonde fausse."""
    import importlib.util
    import io

    from rich.console import Console

    from grimoire.cli import doctor_memory

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        probe,
        "probe_native_index",
        lambda: NativeIndexVerdict(
            ok=False,
            detail="l'import natif de chromadb meurt d'un signal SIGILL",
            chromadb_version=_VERSION,
            signal_name="SIGILL",
        ),
    )
    monkeypatch.setattr(doctor_memory, "_memory_stack_check", lambda *args, **kwargs: None)
    results: list[dict[str, object]] = []
    doctor_memory.apply_memory_checks(_doctor_config(backend), results, fmt="json", console=Console(file=io.StringIO()))
    return results


def test_doctor_fails_when_the_configured_backend_is_mempalace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backend ``mempalace`` sur une machine fatale : doctor doit FAIL, avec les replis."""
    results = _doctor_results(monkeypatch, "mempalace")

    entry = next(item for item in results if item["name"] == "mempalace_native_index")
    assert entry["passed"] is False
    assert "local" in str(entry["detail"]) and "lexical" in str(entry["detail"])


def test_doctor_warns_when_another_backend_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un autre backend n'est pas affecté : WARN, jamais un FAIL qui ferait sortir doctor en erreur."""
    results = _doctor_results(monkeypatch, "local")

    entry = next(item for item in results if item["name"] == "mempalace_native_index")
    assert entry["passed"] is True
    assert entry["level"] == "warn"


def test_backend_probes_before_opening_persistent_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le backend refuse avant ``PersistentClient`` — l'ordre qui évite le SIGILL."""
    from grimoire.memory.backends import mempalace as backend_module

    class _RefusingChromadb:
        class PersistentClient:
            def __init__(self, **kwargs: object) -> None:
                raise AssertionError("PersistentClient ouverte avant la sonde")

    def _refuse() -> None:
        raise ChromaNativeIndexUnavailableError("index natif fatal")

    monkeypatch.setattr(backend_module, "_require_chromadb", lambda: _RefusingChromadb)
    monkeypatch.setattr(backend_module, "ensure_native_index", _refuse)

    with pytest.raises(ChromaNativeIndexUnavailableError):
        backend_module.MemPalaceBackend(palace_path=str(tmp_path / "palace"))
