"""Réglages ``interaction`` et ``voice`` de ``project-context.yaml``.

.. code-block:: yaml

    interaction:
      mode: text            # text | voice — défaut text
    voice:
      stt: faster-whisper   # ou une table : {engine: faster-whisper, model: small}
      tts: pocket-tts       # ou {engine: pocket-tts, language: french, voice: estelle}
      idle_unload_seconds: 300
      language: fr
      max_record_seconds: 120 # plafond d'un enregistrement (1 à 600)
      allow_download: false # true = l'utilisateur accepte le téléchargement des modèles
                            # (le seul interrupteur : il prime sur toute option d'un moteur)

Un réglage illisible ne casse rien : il retombe sur son défaut et le problème est
rapporté dans :attr:`VoiceSettings.problems`, que ``grimoire doctor`` affiche.
Ce module n'importe aucune bibliothèque audio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_IDLE_UNLOAD_S",
    "INTERACTION_MODES",
    "EngineChoice",
    "VoiceSettings",
    "load_voice_settings",
    "settings_from_mapping",
]

INTERACTION_MODES = ("text", "voice")
#: Cinq minutes : assez pour enchaîner deux messages sans recharger le modèle,
#: assez court pour rendre la mémoire quand la conversation est finie.
DEFAULT_IDLE_UNLOAD_S = 300.0
DEFAULT_STT = "faster-whisper"
DEFAULT_TTS = "pocket-tts"
#: Plafond d'un enregistrement : deux minutes tiennent dans un message, et bornent le poids de l'audio.
DEFAULT_MAX_RECORD_S = 120.0
MAX_RECORD_CEILING_S = 600.0


@dataclass(frozen=True, slots=True)
class EngineChoice:
    """Un moteur choisi et ses options (modèle, voix…), passées telles quelles au moteur."""

    engine: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    """Les réglages vocaux résolus, défauts compris."""

    mode: str = "text"
    stt: EngineChoice = field(default_factory=lambda: EngineChoice(DEFAULT_STT))
    tts: EngineChoice = field(default_factory=lambda: EngineChoice(DEFAULT_TTS))
    idle_unload_s: float = DEFAULT_IDLE_UNLOAD_S
    language: str = "fr"
    allow_download: bool = False
    max_record_s: float = DEFAULT_MAX_RECORD_S
    problems: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return self.mode == "voice"


def _engine_choice(raw: Any, key: str, default: str, problems: list[str]) -> EngineChoice:
    if raw is None:
        return EngineChoice(default)
    if isinstance(raw, str) and raw.strip():
        return EngineChoice(raw.strip())
    if isinstance(raw, dict):
        name = raw.get("engine")
        if isinstance(name, str) and name.strip():
            return EngineChoice(name.strip(), {str(k): v for k, v in raw.items() if k != "engine"})
    problems.append(f"voice.{key} illisible (attendu un nom de moteur ou une table avec `engine`) : {default} retenu")
    return EngineChoice(default)


def _check_known(kind: str, choice: EngineChoice, problems: list[str]) -> None:
    """Un moteur hors registre est rapporté ; le choix reste, la charge dira « inconnu » le moment venu."""
    from grimoire.voice.engines import engine_names

    known = engine_names(kind)  # type: ignore[arg-type]
    if choice.engine not in known:
        problems.append(f"voice.{kind}={choice.engine!r} inconnu (connus : {', '.join(known)})")


def settings_from_mapping(data: Any) -> VoiceSettings:
    """Résout les réglages depuis le contenu de ``project-context.yaml`` (ou ``GrimoireConfig.extra``)."""
    problems: list[str] = []
    data = data if isinstance(data, dict) else {}

    interaction = data.get("interaction")
    if interaction is not None and not isinstance(interaction, dict):
        problems.append(
            f"interaction={interaction!r} doit être une table (`interaction: {{mode: voice}}`) : text retenu"
        )
    raw_mode = interaction.get("mode") if isinstance(interaction, dict) else None
    mode = "text"
    if raw_mode is not None:
        if isinstance(raw_mode, str) and raw_mode.strip().lower() in INTERACTION_MODES:
            mode = raw_mode.strip().lower()
        else:
            problems.append(f"interaction.mode={raw_mode!r} inconnu (attendu text ou voice) : text retenu")

    voice = data.get("voice")
    if voice is not None and not isinstance(voice, dict):
        problems.append("voice doit être une table : réglages par défaut retenus")
        voice = {}
    voice = voice or {}

    idle = voice.get("idle_unload_seconds", DEFAULT_IDLE_UNLOAD_S)
    if isinstance(idle, bool) or not isinstance(idle, (int, float)) or idle < 0:
        problems.append(
            f"voice.idle_unload_seconds={idle!r} invalide (nombre ≥ 0, 0 = jamais) : {DEFAULT_IDLE_UNLOAD_S:g} retenu"
        )
        idle = DEFAULT_IDLE_UNLOAD_S

    language = voice.get("language", "fr")
    if not isinstance(language, str) or not language.strip():
        problems.append(f"voice.language={language!r} invalide : fr retenu")
        language = "fr"

    allow = voice.get("allow_download", False)
    if not isinstance(allow, bool):
        problems.append(f"voice.allow_download={allow!r} invalide (true ou false) : false retenu")
        allow = False

    stt = _engine_choice(voice.get("stt"), "stt", DEFAULT_STT, problems)
    tts = _engine_choice(voice.get("tts"), "tts", DEFAULT_TTS, problems)
    _check_known("stt", stt, problems)
    _check_known("tts", tts, problems)

    max_record = voice.get("max_record_seconds", DEFAULT_MAX_RECORD_S)
    if (
        isinstance(max_record, bool)
        or not isinstance(max_record, (int, float))
        or not 1 <= max_record <= MAX_RECORD_CEILING_S
    ):
        problems.append(
            f"voice.max_record_seconds={max_record!r} invalide (entre 1 et {MAX_RECORD_CEILING_S:g}) : "
            f"{DEFAULT_MAX_RECORD_S:g} retenu"
        )
        max_record = DEFAULT_MAX_RECORD_S

    return VoiceSettings(
        mode=mode,
        stt=stt,
        tts=tts,
        idle_unload_s=float(idle),
        language=language.strip(),
        allow_download=allow,
        max_record_s=float(max_record),
        problems=tuple(problems),
    )


def load_voice_settings(project_root: Path) -> VoiceSettings:
    """Lit ``project-context.yaml`` à la racine du projet ; absent ou illisible, les défauts."""
    path = project_root / "project-context.yaml"
    if not path.is_file():
        return VoiceSettings()
    try:
        from ruamel.yaml import YAML

        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return VoiceSettings(problems=(f"project-context.yaml illisible ({type(exc).__name__}) : défauts retenus",))
    return settings_from_mapping(data)
