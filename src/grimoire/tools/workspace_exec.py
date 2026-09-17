"""La Console du dock — exécuter une sous-commande ``grimoire``, et rien d'autre.

La spécification (§4, critère 6) demande qu'une commande `grimoire` lancée
depuis le dock s'exécute et que sa sortie s'affiche, et qu'une commande hors
`grimoire` soit refusée. Ce module est ce refus.

Quatre garanties, dans cet ordre :

1. **Pas de shell.** ``subprocess.run`` reçoit une liste d'arguments. Il n'y a
   aucun chemin par lequel ``;``, ``|``, ``$(…)`` ou un chevron soient
   interprétés — ils arrivent comme des caractères d'argument, et sont de toute
   façon rejetés par la garde d'argument.
2. **Liste blanche de sous-commandes**, pas de liste noire. ``grimoire`` en
   expose une cinquantaine ; celles qui écrivent hors du projet servi, qui
   parlent au réseau ou qui relancent un serveur n'y sont pas. Une sous-commande
   inconnue est refusée en nommant celles qui sont ouvertes.
3. **Arguments littéraux.** Chaque argument est borné en taille, sans octet nul
   ni retour à la ligne, et un argument qui commence par ``-`` doit figurer dans
   les drapeaux déclarés pour cette sous-commande. Un chemin passé en argument
   n'est pas résolu ici : la sous-commande s'exécute avec ``cwd`` sur le projet,
   et c'est elle qui a ses propres gardes.
4. **Local seulement, et borné dans le temps.** Le transport n'expose cette
   surface que sur l'hôte mono-projet, derrière la garde d'origine ; ici on
   ajoute le délai maximal et la troncature de sortie.

Ce module ne décide pas *qui* a le droit d'appeler : c'est le rôle du
transport. Il décide *ce qui* peut être lancé.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ALLOWED",
    "DEFAULT_TIMEOUT",
    "MAX_ARGS",
    "MAX_ARG_LEN",
    "MAX_OUTPUT",
    "CommandRefusedError",
    "catalogue",
    "run_command",
]

#: Une commande du dock est interactive : au-delà, l'utilisateur a déjà changé
#: d'écran. Les commandes longues restent au terminal, c'est assumé.
DEFAULT_TIMEOUT = 45.0
#: Au-delà, la sortie est coupée par la fin — c'est le verdict qui compte.
MAX_OUTPUT = 64 * 1024
MAX_ARGS = 12
MAX_ARG_LEN = 512


@dataclass(frozen=True, slots=True)
class _Sub:
    """Une sous-commande ouverte à la Console, et sa surface exacte."""

    #: Les mots qui suivent ``grimoire`` — ``("task", "list")`` pour un verbe imbriqué.
    argv: tuple[str, ...]
    #: Ce que la commande fait, en une phrase — la palette l'affiche.
    summary: str
    #: Drapeaux acceptés. Tout autre argument commençant par ``-`` est refusé.
    flags: frozenset[str] = frozenset()
    #: Combien d'arguments positionnels au plus (identifiants de tâche, chemins).
    positionals: int = 0
    #: Vrai quand la commande écrit dans le projet. Le transport l'exige explicitement.
    mutates: bool = False


def _sub(
    *argv: str,
    summary: str,
    flags: tuple[str, ...] = (),
    positionals: int = 0,
    mutates: bool = False,
) -> _Sub:
    return _Sub(argv, summary, frozenset(flags), positionals, mutates)


#: La liste blanche. Clé = ce que l'utilisateur tape après ``grimoire``.
#:
#: Absentes, et volontairement : ``init``, ``up``, ``migrate``, ``serve``,
#: ``cockpit``, ``upgrade``, ``self``, ``update``, ``ext``, ``plugins`` — elles
#: réécrivent l'arbre du projet, installent depuis le réseau, ou relancent un
#: serveur depuis un serveur. Les ouvrir demande un geste explicite dans
#: l'interface, pas une ligne de terminal dans un onglet.
ALLOWED: dict[str, _Sub] = {
    "version": _sub("version", summary="Version du kit installé."),
    "status": _sub("status", summary="Registre des agents et état du projet."),
    "doctor": _sub(
        "doctor",
        summary="Diagnostic du projet : chemins, hôtes, standard.",
        flags=("--check-paths", "--json"),
    ),
    "env": _sub("env", summary="Environnement résolu par le kit."),
    "config": _sub("config", summary="Configuration effective du projet.", positionals=1),
    "task list": _sub("task", "list", summary="Tâches du ledger et leur colonne de board.", flags=("--mission", "--status")),
    "task show": _sub("task", "show", summary="Détail d'une tâche et sa prochaine porte.", positionals=1),
    "task trace": _sub("task", "trace", summary="Timeline unifiée d'une tâche.", positionals=1),
    "task board": _sub("task", "board", summary="Board gouverné, projeté depuis le ledger."),
    "task context": _sub("task", "context", summary="Context bundle d'une tâche.", positionals=1),
    "standard verify": _sub("standard", "verify", summary="Conformité au profil déclaré.", flags=("--profile",)),
    "standard score": _sub("standard", "score", summary="Score de conformité du standard."),
    "standard audit": _sub("standard", "audit", summary="Écarts au standard et remèdes proposés."),
    "workflows list": _sub("workflows", "list", summary="Workflows résolus pour ce projet."),
    "workflows show": _sub("workflows", "show", summary="Définition d'un workflow.", positionals=1),
    "memory status": _sub("memory", "status", summary="État des couches et backends de mémoire."),
    "memory search": _sub("memory", "search", summary="Recherche dans la mémoire du projet.", positionals=1),
    "memory list": _sub("memory", "list", summary="Entrées de mémoire du projet."),
    "hooks status": _sub("hooks", "status", summary="Hooks déclarés et leur mode."),
    "host status": _sub("host", "status", summary="Hôtes équipés et fraîcheur de leurs projections."),
    "validate": _sub("validate", summary="Validation statique des artefacts.", positionals=1),
    "lint": _sub("lint", summary="Lint des artefacts du projet.", positionals=1),
    "blueprint list": _sub("blueprint", "list", summary="Blueprints du projet."),
}


class CommandRefusedError(ValueError):
    """La commande n'est pas dans la surface ouverte à la Console."""


def catalogue() -> list[dict[str, Any]]:
    """Ce que la palette et la Console ont le droit de proposer."""
    return [
        {
            "command": f"grimoire {key}",
            "key": key,
            "summary": sub.summary,
            "flags": sorted(sub.flags),
            "positionals": sub.positionals,
            "mutates": sub.mutates,
        }
        for key, sub in sorted(ALLOWED.items())
    ]


def _normalise(argv: list[str] | tuple[str, ...] | None) -> list[str]:
    if not argv:
        raise CommandRefusedError("commande vide")
    words = [str(a) for a in argv]
    if words and words[0] == "grimoire":
        words = words[1:]
    if not words:
        raise CommandRefusedError("commande vide")
    if len(words) > MAX_ARGS:
        raise CommandRefusedError(f"trop d'arguments ({len(words)} > {MAX_ARGS})")
    for word in words:
        if len(word) > MAX_ARG_LEN:
            raise CommandRefusedError("argument trop long")
        if "\x00" in word or "\n" in word or "\r" in word:
            raise CommandRefusedError("argument non littéral")
    return words


def _match(words: list[str]) -> tuple[_Sub, list[str]]:
    """La plus longue sous-commande de la liste blanche qui préfixe *words*."""
    for depth in (2, 1):
        if len(words) >= depth:
            key = " ".join(words[:depth])
            sub = ALLOWED.get(key)
            if sub is not None:
                return sub, words[depth:]
    raise CommandRefusedError(
        f"sous-commande refusée : {' '.join(words[:2])!r} — la Console n'exécute que "
        f"{len(ALLOWED)} sous-commandes `grimoire` de lecture (voir /api/workspace/commands)"
    )


def _check_rest(sub: _Sub, rest: list[str]) -> None:
    positionals = 0
    index = 0
    while index < len(rest):
        word = rest[index]
        if word.startswith("-"):
            name = word.split("=", 1)[0]
            if name not in sub.flags:
                raise CommandRefusedError(
                    f"drapeau refusé : {name} — acceptés : {', '.join(sorted(sub.flags)) or 'aucun'}"
                )
            # Un drapeau à valeur consomme le mot suivant s'il n'est pas lui-même un drapeau.
            if "=" not in word and index + 1 < len(rest) and not rest[index + 1].startswith("-"):
                index += 1
        else:
            positionals += 1
            if positionals > sub.positionals:
                raise CommandRefusedError(
                    f"argument positionnel inattendu : {word!r} "
                    f"({sub.positionals} attendu(s) au plus)"
                )
        index += 1


def run_command(
    project_root: Path,
    argv: list[str] | tuple[str, ...] | None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    allow_mutation: bool = False,
) -> dict[str, Any]:
    """Exécute une sous-commande ``grimoire`` du projet servi, ou refuse.

    Lève :class:`CommandRefusedError` avant tout lancement quand la commande sort de
    la surface ouverte. Un dépassement de délai n'est pas une exception : c'est
    un résultat, avec ``timed_out: true`` — la Console doit pouvoir l'afficher
    comme le reste.
    """
    words = _normalise(argv)
    sub, rest = _match(words)
    _check_rest(sub, rest)
    if sub.mutates and not allow_mutation:
        raise CommandRefusedError(f"`grimoire {' '.join(sub.argv)}` écrit : confirmation explicite requise")

    command = [sys.executable, "-m", "grimoire", *sub.argv, *rest]
    env = dict(os.environ)
    # Sortie stable, sans couleur ni curseur : le dock affiche du texte, pas un TTY.
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "command": f"grimoire {' '.join([*sub.argv, *rest])}",
            "argv": [*sub.argv, *rest],
            "code": None,
            "stdout": "",
            "stderr": f"délai dépassé après {timeout:.0f} s",
            "output": f"délai dépassé après {timeout:.0f} s",
            "timed_out": True,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
    except OSError as exc:  # pragma: no cover — interpréteur introuvable
        raise CommandRefusedError(f"exécution impossible : {exc}") from exc
    # La console riche du kit écrit son rendu sur stderr : afficher stdout seul
    # donnerait un terminal vide sur des commandes qui ont pourtant répondu.
    # `output` est ce que le dock montre ; les deux flux restent séparés pour
    # qui veut les distinguer.
    return {
        "ok": proc.returncode == 0,
        "command": f"grimoire {' '.join([*sub.argv, *rest])}",
        "argv": [*sub.argv, *rest],
        "code": proc.returncode,
        "stdout": proc.stdout[-MAX_OUTPUT:],
        "stderr": proc.stderr[-MAX_OUTPUT:],
        "output": (proc.stdout + proc.stderr)[-MAX_OUTPUT:],
        "timed_out": False,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def doctor_view(
    project_root: Path, *, timeout: float = DEFAULT_TIMEOUT, probe: bool = False
) -> dict[str, Any]:
    """L'onglet Problèmes du dock : ce que ``grimoire doctor`` trouve.

    Mis en cache (:mod:`grimoire.tools.view_cache`), keyé sur la signature de
    mtimes de la surface que ``doctor`` inspecte (config, agents/skills,
    kit, overrides, wrappers VS Code, policies du standard) — mesuré à
    ~350ms par appel avant ce cache, la route la plus chère des sept que la
    fiche Piloter faisait partir (issue #548) : ``doctor`` relance un
    processus ``grimoire`` complet (démarrage de l'arbre Typer compris) à
    chaque appel, même quand rien n'a bougé dans le projet. ``probe=True``
    (``?probe=1``) force un recalcul frais, même contrat que
    ``memory_link_view``.

    Le diagnostic n'est pas importable aujourd'hui — il vit dans la couche CLI
    — donc il est appelé comme la Console appelle tout le reste, par la même
    liste blanche. Le jour où il est extrait en module, seule
    :func:`_doctor_view_uncached` change.
    """
    from grimoire.core import layout
    from grimoire.core.standard_generation import STANDARD_DIR
    from grimoire.tools import view_cache

    root = project_root.resolve()
    key = f"doctor_view:{root}"
    if probe:
        view_cache.invalidate(key)
    signature = view_cache.path_signature([
        root / "project-context.yaml",
        *layout.agent_dirs(root),
        *layout.skill_dirs(root),
        layout.kit_dir(root),
        layout.overrides_dir(root),
        root / ".github" / "agents",
        root / STANDARD_DIR,
    ])
    return view_cache.cached(key, signature, lambda: _doctor_view_uncached(root, timeout=timeout))


def _doctor_view_uncached(project_root: Path, *, timeout: float) -> dict[str, Any]:
    result = run_command(project_root, ["doctor"], timeout=timeout)
    lines = [line for line in result["output"].splitlines() if line.strip()]
    return {
        "ok": result["ok"],
        "code": result["code"],
        "timed_out": result["timed_out"],
        "command": result["command"],
        "lines": lines[-400:],
        "stderr": result["stderr"],
    }
