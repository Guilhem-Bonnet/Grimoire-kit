"""Gardes du dépôt du kit : une commande locale, la même que la CI.

Le dépôt a des gardes qui ne vivaient que dans des endroits différents : le
cliquet de taille et les actions épinglées dans ``ci-sdk.yml``, la cohérence
du CHANGELOG dans ``publish.yml`` (au tag, trop tard), la prose émise dans le
pre-commit, les waivers de dépendances dans un job d'audit qui ne les juge pas.
Aucune commande locale ne les lançait toutes, donc un contributeur découvrait
sa garde rouge en CI, et le pre-commit n'en lançait qu'une.

``grimoire gates repo`` les exécute toutes, avec les mêmes scripts que la CI,
et rend non zéro si l'une échoue **ou n'a pas pu être évaluée**. Une garde dont
le script manque, qui dépasse son délai ou qui ne démarre pas est
``unevaluated`` : non vérifié n'est pas vérifié, ça ne passe pas.

La limite : ce sont des gardes de dépôt source. Hors d'un clone du kit (une
wheel installée, un projet utilisateur), il n'y a rien à garder et la commande
le dit au lieu de répondre « OK ».
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Status = Literal["passed", "failed", "unevaluated"]

#: ``dev`` : ce qu'une branche doit satisfaire. ``release`` : ce que le tag exige
#: en plus (``[Unreleased]`` vide, ``CHANGELOG`` à la version de ``version.txt``).
PROFILES: tuple[str, ...] = ("dev", "release")

#: Un script de garde qui ne rend pas la main en dix minutes est coincé.
GATE_TIMEOUT_SECONDS = 600


@dataclass(frozen=True, slots=True)
class Gate:
    """Une garde : un script du dépôt, ses arguments par profil, ce qu'elle refuse."""

    id: str
    script: str
    refuses: str
    dev_args: tuple[str, ...] = ()
    release_args: tuple[str, ...] = ()

    def args_for(self, profile: str) -> tuple[str, ...]:
        return self.release_args if profile == "release" else self.dev_args


GATES: tuple[Gate, ...] = (
    Gate(
        "ratchet",
        "check-code-ratchet.py",
        "un fichier de framework/ qui grossit ou apparaît, un module src/ au-delà de 1500 lignes",
    ),
    Gate(
        "changelog",
        "check-changelog-release.py",
        "une branche feat/fix/perf sans entrée sous [Unreleased] (dev) ; un [Unreleased] plein au tag (release)",
        dev_args=("--dev",),
    ),
    Gate(
        "prose",
        "check-emitted-prose.py",
        "un gabarit émis qui demande un chiffre à remplir de tête",
    ),
    Gate(
        "waivers",
        "depaudit-waivers.py",
        "un waiver de dépendance expiré, illisible ou sans justification",
        dev_args=("--check",),
        release_args=("--check",),
    ),
    Gate(
        "actions-pinned",
        "check-actions-pinned.py",
        "une action GitHub référencée par un tag mobile au lieu d'un SHA",
    ),
    Gate(
        "tenets",
        "check-contributing.py",
        "un tenet de CONTRIBUTING.md sans test existant, un gabarit de PR qui cite une commande disparue",
    ),
    Gate(
        "doc-audience",
        "check-doc-audience.py",
        "un fichier de docs/ sans audience déclarée dans son front-matter",
    ),
    Gate(
        "doc-commands",
        "check-doc-commands.py",
        "une commande `grimoire ...` citée en documentation qui n'existe pas dans l'arbre de la CLI",
    ),
    Gate(
        "doc-claims",
        "check-doc-claims.py",
        "un chiffre de vitrine (%, ×, nombre de tests, badge numérique) sans ancre `<!-- source: ... -->`",
    ),
    Gate(
        "skills",
        "check-skills.py",
        "un skill livré hors de la spécification Agent Skills, ou sans evals.json d'au moins un cas",
    ),
)

GATE_IDS: tuple[str, ...] = tuple(gate.id for gate in GATES)


@dataclass(frozen=True, slots=True)
class GateResult:
    id: str
    status: Status
    detail: str
    seconds: float

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "status": self.status, "detail": self.detail, "seconds": round(self.seconds, 2)}


def is_kit_checkout(root: Path) -> bool:
    """Vrai si ``root`` est un clone du kit (scripts de garde, version, sources)."""
    return (
        (root / "scripts" / "check-code-ratchet.py").is_file()
        and (root / "version.txt").is_file()
        and (root / "src" / "grimoire").is_dir()
    )


def find_kit_root(start: Path) -> Path | None:
    """Remonte depuis ``start`` jusqu'à un clone du kit, ou ``None``."""
    start = start.resolve()
    for candidate in (start, *start.parents):
        if is_kit_checkout(candidate):
            return candidate
    return None


def foreign_package_note(root: Path) -> str | None:
    """Dit d'où vient le ``grimoire`` qui orchestre, quand ce n'est pas ``<root>/src``.

    Les gardes, elles, tournent sur le code du clone (``gate_environment``) ; le
    registre des gardes et le profil viennent en revanche du paquet qui exécute
    cette commande. S'il est étranger au clone (release installée, autre clone),
    on le dit au lieu de laisser croire que tout vient de la branche.
    """
    import grimoire

    origin = Path(grimoire.__file__).resolve() if getattr(grimoire, "__file__", None) else None
    if origin is not None and (root.resolve() / "src") in origin.parents:
        return None
    return (
        f"le paquet grimoire qui orchestre vient de {origin.parent if origin else 'un emplacement inconnu'}, "
        f"pas de {root / 'src'} : les gardes jugent le code du clone, mais le registre des gardes est celui de ce paquet"
    )


def _tail(output: str, lines: int = 12) -> str:
    kept = [line for line in output.strip().splitlines() if line.strip()]
    return "\n".join(kept[-lines:])


def gate_environment(root: Path) -> dict[str, str]:
    """L'environnement d'une garde : le code du clone passe avant tout ``grimoire`` installé.

    Deux gardes (``tenets``, ``doc-commands``) importent ``grimoire``. Sans cela
    elles jugent le paquet que l'interpréteur trouve (un autre clone, une release
    installée) et leur verdict dépend de l'environnement, pas de la branche.
    ``<root>/src`` vient donc en tête de ``PYTHONPATH``, le reste suit.
    """
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    inherited = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(root / "src") + (os.pathsep + inherited if inherited else "")
    return env


def run_gate(gate: Gate, root: Path, profile: str = "dev", timeout: float = GATE_TIMEOUT_SECONDS) -> GateResult:
    """Exécute une garde. Ne lève jamais : tout ce qui empêche le verdict devient ``unevaluated``."""
    script = root / "scripts" / gate.script
    started = time.monotonic()
    if not script.is_file():
        return GateResult(gate.id, "unevaluated", f"script absent : scripts/{gate.script}", 0.0)
    env = gate_environment(root)
    try:
        proc = subprocess.run(
            [sys.executable, str(script), *gate.args_for(profile)],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GateResult(gate.id, "unevaluated", f"délai de {timeout:.0f} s dépassé", time.monotonic() - started)
    except OSError as exc:
        return GateResult(gate.id, "unevaluated", f"démarrage impossible : {exc}", time.monotonic() - started)
    elapsed = time.monotonic() - started
    output = _tail(proc.stderr + "\n" + proc.stdout if proc.returncode else proc.stdout)
    if proc.returncode == 0:
        return GateResult(gate.id, "passed", output, elapsed)
    return GateResult(gate.id, "failed", output or f"code de sortie {proc.returncode}", elapsed)


def select_gates(only: list[str] | None) -> tuple[Gate, ...]:
    """Les gardes demandées ; un identifiant inconnu est une erreur, pas un filtre vide."""
    if not only:
        return GATES
    unknown = [gate_id for gate_id in only if gate_id not in GATE_IDS]
    if unknown:
        raise ValueError(f"garde inconnue : {', '.join(unknown)} (connues : {', '.join(GATE_IDS)})")
    return tuple(gate for gate in GATES if gate.id in only)


def run_repo_gates(root: Path, profile: str = "dev", only: list[str] | None = None) -> list[GateResult]:
    if profile not in PROFILES:
        raise ValueError(f"profil inconnu : {profile} (connus : {', '.join(PROFILES)})")
    return [run_gate(gate, root, profile) for gate in select_gates(only)]


def all_passed(results: list[GateResult]) -> bool:
    """Vrai seulement si au moins une garde a tourné et que toutes ont passé."""
    return bool(results) and all(result.status == "passed" for result in results)
