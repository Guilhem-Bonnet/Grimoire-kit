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
        INSTALL_HINT,
        EngineUnavailableError,
        availability,
        get_spec,
        voice_extra_installed,
    )
    from grimoire.voice.settings import settings_from_mapping

    settings = settings_from_mapping(project_data)
    entries: list[dict[str, Any]] = [
        {"name": "voice_settings", "passed": True, "detail": problem, "level": "warn"} for problem in settings.problems
    ]

    extra = voice_extra_installed()
    mode = f"interaction.mode: {settings.mode}"
    if extra:
        entries.append(
            {"name": "voice_extra", "passed": True, "level": "ok", "detail": f"Vocal : extra voice installé ({mode})"}
        )
    else:
        entries.append(
            {
                "name": "voice_extra",
                "passed": True,
                "level": "warn" if settings.enabled else "info",
                "detail": f"Vocal : extra voice non installé ({mode}) — {INSTALL_HINT}",
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
        level = "ok" if ok else ("warn" if settings.enabled else "info")
        entries.append(
            {
                "name": f"voice_{kind}",
                "passed": True,
                "level": level,
                "engine": choice.engine,
                "available": ok,
                "detail": f"Vocal, {label} (voice.{kind}: {choice.engine}) : {reason}",
            }
        )

    for entry in entries:
        results.append(entry)
        if fmt != "json":
            console.print(f"  {_TAGS[entry['level']]}  {entry['detail']}")
