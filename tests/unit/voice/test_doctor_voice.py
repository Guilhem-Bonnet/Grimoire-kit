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
    out: dict[str, dict] = {}
    for r in results:  # plusieurs problèmes de réglages : on garde leurs textes ensemble
        if r["name"] in out:
            r = {**r, "detail": out[r["name"]]["detail"] + " | " + r["detail"]}
        out[r["name"]] = r
    return out


def test_text_mode_is_a_single_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: False)
    out = _run({})
    assert set(out) == {"voice_extra"}
    assert out["voice_extra"]["level"] == "info"
    assert "grimoire-kit[voice]" in out["voice_extra"]["detail"]


def test_text_mode_with_extra_is_still_a_single_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: True)
    assert set(_run({})) == {"voice_extra"}


def test_voice_mode_flags_a_model_missing_from_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: True)
    monkeypatch.setattr("grimoire.voice.faster_whisper_engine.model_cached", lambda _o: False)
    monkeypatch.setattr("grimoire.voice.pocket_tts_engine.model_cached", lambda _o: True)
    out = _run({"interaction": {"mode": "voice"}, "voice": {"tts": "pocket-tts"}})
    assert out["voice_stt"]["level"] == "warn" and out["voice_stt"]["available"] is False
    assert "absent du cache" in out["voice_stt"]["detail"]
    assert out["voice_tts"]["level"] == "ok"


def test_voice_mode_with_download_consent_skips_the_cache_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: True)
    monkeypatch.setattr("grimoire.voice.faster_whisper_engine.model_cached", lambda _o: False)
    out = _run({"interaction": {"mode": "voice"}, "voice": {"allow_download": True}})
    assert out["voice_stt"]["level"] == "ok"


def test_voice_mode_without_extra_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: False)
    out = _run({"interaction": {"mode": "voice"}})
    assert out["voice_extra"]["level"] == "warn"
    assert out["voice_stt"]["level"] == "warn"
    assert "interaction.mode: voice" in out["voice_extra"]["detail"]


def test_voice_mode_with_engines_installed_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: True)
    monkeypatch.setattr("grimoire.voice.engines.model_cache", lambda *_a: None)
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
    assert "Vocal : désactivé" in text and "voice.stt" not in text


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


def test_voice_mode_reports_qwen_engine_memory_and_missing_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda _m: True)
    monkeypatch.setattr("grimoire.voice.faster_whisper_engine.model_cached", lambda _o: True)
    monkeypatch.setattr("grimoire.voice.qwen_tts_engine.model_cached", lambda _o=None: False)
    out = _run({"interaction": {"mode": "voice"}})  # qwen_tts « installé » : moteur retenu par défaut
    tts = out["voice_tts"]
    assert tts["engine"] == "qwen3-tts-clone"
    assert tts["level"] == "warn" and "absent du cache" in tts["detail"]
    assert "6 Go" in tts["detail"]


def test_voice_mode_qwen_missing_package_points_at_its_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.voice.engines._module_installed", lambda m: m != "qwen_tts")
    out = _run({"interaction": {"mode": "voice"}, "voice": {"tts": "qwen3-tts-clone"}})
    assert out["voice_tts"]["level"] == "warn"
    assert "voice-qwen" in out["voice_tts"]["detail"]
