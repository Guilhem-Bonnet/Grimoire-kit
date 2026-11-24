"""Le contrôle vocal de `grimoire doctor` (#1343 V1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from grimoire.cli.doctor_voice import apply_voice_check


def _run(data: dict, *, fmt: str = "json") -> dict[str, dict]:
    results: list[dict] = []
    apply_voice_check(data, results, fmt=fmt, console=Console(quiet=True))
    assert all(r["passed"] for r in results)  # jamais FAIL : le texte reste intact
    return {r["name"]: r for r in results}


def test_text_mode_without_extra_is_informational(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: False)
    out = _run({})
    assert out["voice_extra"]["level"] == "info"
    assert "grimoire-kit[voice]" in out["voice_extra"]["detail"]
    assert out["voice_stt"]["level"] == "info" and out["voice_stt"]["available"] is False
    assert out["voice_tts"]["engine"] == "pocket-tts"


def test_voice_mode_without_extra_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: False)
    out = _run({"interaction": {"mode": "voice"}})
    assert out["voice_extra"]["level"] == "warn"
    assert out["voice_stt"]["level"] == "warn"
    assert "interaction.mode: voice" in out["voice_extra"]["detail"]


def test_voice_mode_with_engines_installed_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: True)
    out = _run({"interaction": {"mode": "voice"}})
    assert {out[k]["level"] for k in ("voice_extra", "voice_stt", "voice_tts")} == {"ok"}


def test_browser_fallback_is_available_without_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: False)
    out = _run({"interaction": {"mode": "voice"}, "voice": {"stt": "browser", "tts": "browser"}})
    assert out["voice_stt"]["level"] == "ok" and out["voice_tts"]["level"] == "ok"


def test_unknown_engine_and_bad_settings_are_reported() -> None:
    out = _run({"interaction": {"mode": "voice"}, "voice": {"tts": "qwen9", "idle_unload_seconds": "soon"}})
    assert "inconnu" in out["voice_tts"]["detail"] and out["voice_tts"]["level"] == "warn"
    assert "idle_unload_seconds" in out["voice_settings"]["detail"]


def test_text_output_prints_each_line() -> None:
    console = Console(record=True, width=200)
    apply_voice_check({}, [], fmt="text", console=console)
    text = console.export_text()
    assert "Vocal : extra voice" in text and "voice.stt: faster-whisper" in text


def test_grimoire_doctor_reports_voice_entries(tmp_path: Path) -> None:
    from grimoire.cli.app import app

    runner = CliRunner()
    runner.invoke(app, ["init", str(tmp_path)])
    ctx = tmp_path / "project-context.yaml"
    ctx.write_text(
        ctx.read_text(encoding="utf-8") + "\ninteraction:\n  mode: voice\nvoice:\n  tts: browser\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["doctor", str(tmp_path), "--output", "json"])
    payload = json.loads(result.output[result.output.index("{") :])
    checks = payload.get("checks") or payload.get("results") or []
    names = {c["name"]: c for c in checks}
    assert {"voice_extra", "voice_stt", "voice_tts"} <= set(names)
    assert names["voice_tts"]["engine"] == "browser" and names["voice_tts"]["level"] == "ok"
