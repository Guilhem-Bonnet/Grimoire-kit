"""Réglages ``interaction.mode`` et ``voice.*`` de project-context.yaml (#1343 V1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.voice.settings import (
    DEFAULT_IDLE_UNLOAD_S,
    EngineChoice,
    VoiceSettings,
    load_voice_settings,
    settings_from_mapping,
)


def test_defaults_are_text_mode_with_local_engines_and_no_download() -> None:
    s = settings_from_mapping({"project": {"name": "x"}})
    assert s == VoiceSettings()
    assert s.mode == "text" and not s.enabled
    assert s.stt == EngineChoice("faster-whisper")
    assert s.tts == EngineChoice("pocket-tts")
    assert s.idle_unload_s == DEFAULT_IDLE_UNLOAD_S
    assert s.allow_download is False
    assert s.problems == ()


def test_full_settings_are_read() -> None:
    s = settings_from_mapping(
        {
            "interaction": {"mode": "Voice"},
            "voice": {
                "stt": {"engine": "faster-whisper", "model": "medium"},
                "tts": "browser",
                "idle_unload_seconds": 30,
                "language": "en",
                "allow_download": True,
            },
        }
    )
    assert s.enabled
    assert s.stt == EngineChoice("faster-whisper", {"model": "medium"})
    assert s.tts == EngineChoice("browser")
    assert s.idle_unload_s == 30.0
    assert s.language == "en"
    assert s.allow_download is True
    assert s.problems == ()


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ({"interaction": {"mode": "telepathy"}}, "interaction.mode"),
        ({"voice": "on"}, "voice doit être une table"),
        ({"voice": {"stt": 3}}, "voice.stt illisible"),
        ({"voice": {"tts": {"model": "x"}}}, "voice.tts illisible"),
        ({"voice": {"idle_unload_seconds": -1}}, "idle_unload_seconds"),
        ({"voice": {"idle_unload_seconds": True}}, "idle_unload_seconds"),
        ({"voice": {"language": ""}}, "voice.language"),
        ({"voice": {"allow_download": "yes"}}, "allow_download"),
    ],
)
def test_unreadable_value_falls_back_to_default_and_is_reported(data: dict, fragment: str) -> None:
    s = settings_from_mapping(data)
    assert len(s.problems) == 1
    assert fragment in s.problems[0]
    defaults = VoiceSettings()
    assert (s.mode, s.stt, s.tts, s.idle_unload_s, s.language, s.allow_download) == (
        defaults.mode,
        defaults.stt,
        defaults.tts,
        defaults.idle_unload_s,
        defaults.language,
        defaults.allow_download,
    )


def test_zero_idle_means_never_unload() -> None:
    assert settings_from_mapping({"voice": {"idle_unload_seconds": 0}}).idle_unload_s == 0.0


def test_non_mapping_root_gives_defaults() -> None:
    assert settings_from_mapping(None) == VoiceSettings()


def test_load_from_project_file(tmp_path: Path) -> None:
    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: demo\ninteraction:\n  mode: voice\nvoice:\n  tts: browser\n",
        encoding="utf-8",
    )
    s = load_voice_settings(tmp_path)
    assert s.enabled and s.tts == EngineChoice("browser")


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert load_voice_settings(tmp_path) == VoiceSettings()


def test_unreadable_file_gives_defaults_and_a_reason(tmp_path: Path) -> None:
    (tmp_path / "project-context.yaml").write_text("project: [unclosed\n", encoding="utf-8")
    s = load_voice_settings(tmp_path)
    assert s.mode == "text"
    assert s.problems and "illisible" in s.problems[0]


def test_interaction_given_as_a_string_is_reported() -> None:
    s = settings_from_mapping({"interaction": "voice"})
    assert s.mode == "text"
    assert len(s.problems) == 1 and "interaction" in s.problems[0] and "table" in s.problems[0]


@pytest.mark.parametrize("kind", ["stt", "tts"])
def test_unknown_engine_is_reported(kind: str) -> None:
    s = settings_from_mapping({"voice": {kind: "qwen9"}})
    assert len(s.problems) == 1 and f"voice.{kind}" in s.problems[0] and "inconnu" in s.problems[0]
