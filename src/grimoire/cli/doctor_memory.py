"""Contrôles mémoire de ``grimoire doctor``.

Hors de ``cli/app.py`` : ce fichier est sous un cliquet de taille qui ne le
laisse que rétrécir. Deux contrôles :

- ``memory_stack_started`` (#619) — une pile mémoire configurée mais jamais
  démarrée est un WARN nommé, jamais un FAIL : c'est un écart à combler, pas
  un projet cassé ;
- ``mempalace_native_index`` (#1059) — l'index natif de chromadb peut tuer le
  processus (SIGILL) sur certains processeurs. FAIL quand le backend configuré
  est ``mempalace`` (le refus est réel), WARN sinon, rien si chromadb n'est
  pas installé.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from grimoire.core.config import GrimoireConfig


def apply_memory_checks(cfg: GrimoireConfig, results: list[dict[str, Any]], *, fmt: str, console: Console) -> None:
    """Ajoute les contrôles mémoire de ``doctor`` et les affiche."""
    _memory_stack_check(cfg, results, fmt=fmt, console=console)
    _native_index_check(cfg, results, fmt=fmt, console=console)


def _memory_stack_check(cfg: GrimoireConfig, results: list[dict[str, Any]], *, fmt: str, console: Console) -> None:
    """Une pile configurée mais non démarrée : WARN avec la commande qui la démarre.

    ``grimoire init`` peut écrire une composition ``complet``/``graphe``
    (backend épinglé, réglages de connexion écrits) sans jamais démarrer les
    conteneurs qu'elle nomme, faute de consentement (exécution nue non-TTY,
    sans ``-y`` ni ``--memory-stack up``).
    """
    from grimoire.tools.memory_setup import unreached_configured_services

    missing = unreached_configured_services(cfg.memory)
    if not missing:
        return
    detail = (
        f"memory stack not started ({', '.join(missing)}): "
        f"grimoire memory up --profile {cfg.memory.layer_profile} --start --apply"
    )
    results.append({"name": "memory_stack_started", "passed": True, "detail": detail, "level": "warn"})
    if fmt != "json":
        console.print(f"  [yellow]WARN[/yellow]  {detail}")


def _native_index_check(cfg: GrimoireConfig, results: list[dict[str, Any]], *, fmt: str, console: Console) -> None:
    """Le verdict de la sonde chromadb (#1059), relu au cache par version et machine."""
    from importlib.util import find_spec

    if find_spec("chromadb") is None:
        return

    from grimoire.memory.chromadb_native_probe import probe_native_index

    verdict = probe_native_index()
    if verdict.ok:
        results.append({"name": "mempalace_native_index", "passed": True, "detail": verdict.detail})
        if fmt != "json":
            console.print(f"  [green]OK[/green]  {verdict.detail}")
        return

    mempalace_configured = cfg.memory.backend == "mempalace"
    detail = (
        f"{verdict.detail} — replis : « local » ou « lexical »"
        if mempalace_configured
        else f"{verdict.detail} — sans effet sur ce projet (backend « {cfg.memory.backend} »)"
    )
    entry: dict[str, Any] = {
        "name": "mempalace_native_index",
        "passed": not mempalace_configured,
        "detail": detail,
    }
    if not mempalace_configured:
        entry["level"] = "warn"
    results.append(entry)
    if fmt != "json":
        tag = "[red]FAIL[/red]" if mempalace_configured else "[yellow]WARN[/yellow]"
        console.print(f"  {tag}  {detail}")
