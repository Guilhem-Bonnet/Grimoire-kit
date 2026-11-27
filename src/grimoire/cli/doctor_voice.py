"""Le contrôle vocal de `grimoire doctor` (#1343 V1) : réglages lisibles, extra présent, moteurs disponibles.

Hors de ``cli/app.py`` : ce fichier est sous un cliquet de taille qui ne le laisse que rétrécir.
Rien n'est importé des moteurs : leur présence est lue par ``find_spec``, aucun modèle n'est chargé.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

_TAGS = {"ok": "[green]OK[/green]", "warn": "[yellow]WARN[/yellow]", "info": "[dim]○[/dim]"}


def apply_voice_check(project_data: Any, results: list[dict[str, Any]], *, fmt: str, console: Console) -> None:
    """Jamais FAIL : un vocal indisponible laisse le texte intact. WARN seulement quand le vocal est allumé."""
    from grimoire.voice.engines import (
        EngineUnavailableError,
        availability,
        get_spec,
        install_hint,
        model_cache,
        voice_extra_installed,
    )
    from grimoire.voice.settings import settings_from_mapping

    settings = settings_from_mapping(project_data)
    entries: list[dict[str, Any]] = [
        {"name": "voice_settings", "passed": True, "detail": problem, "level": "warn"} for problem in settings.problems
    ]

    extra = voice_extra_installed()
    mode = f"interaction.mode: {settings.mode}"
    if not settings.enabled:
        # Mode texte : une seule ligne, quel que soit l'état de l'extra.
        state = "installé" if extra else f"non installé — {install_hint()}"
        entries.append(
            {
                "name": "voice_extra",
                "passed": True,
                "level": "ok" if extra else "info",
                "detail": f"Vocal : désactivé ({mode}), extra voice {state}",
            }
        )
        _emit(entries, results, fmt, console)
        return

    if extra:
        entries.append(
            {"name": "voice_extra", "passed": True, "level": "ok", "detail": f"Vocal : extra voice installé ({mode})"}
        )
    else:
        entries.append(
            {
                "name": "voice_extra",
                "passed": True,
                "level": "warn",
                "detail": f"Vocal : extra voice non installé ({mode}) — {install_hint()}",
            }
        )

    for kind, choice in (("stt", settings.stt), ("tts", settings.tts)):
        label = "reconnaissance" if kind == "stt" else "synthèse"
        try:
            spec = get_spec(kind, choice.engine)  # type: ignore[arg-type]
        except EngineUnavailableError as exc:
            ok, reason = False, exc.reason
        else:
            avail = availability(spec)
            ok, reason = avail.ok, avail.reason
            if ok and not settings.allow_download:
                cache = model_cache(spec, dict(choice.options))
                if cache is not None and not cache.ok:
                    ok, reason = False, cache.reason
        detail = f"Vocal, {label} (voice.{kind}: {choice.engine}) : {reason}"
        if kind == "tts" and choice.engine == "qwen3-tts-clone":
            from grimoire.voice.qwen_tts_engine import EXPECTED_MEMORY_GB

            detail += f" ; mémoire attendue : environ {EXPECTED_MEMORY_GB} Go"
        entries.append(
            {
                "name": f"voice_{kind}",
                "passed": True,
                "level": "ok" if ok else "warn",
                "engine": choice.engine,
                "available": ok,
                "detail": detail,
            }
        )

    _emit(entries, results, fmt, console)


def _emit(entries: list[dict[str, Any]], results: list[dict[str, Any]], fmt: str, console: Console) -> None:
    for entry in entries:
        results.append(entry)
        if fmt != "json":
            console.print(f"  {_TAGS[entry['level']]}  {entry['detail']}")
