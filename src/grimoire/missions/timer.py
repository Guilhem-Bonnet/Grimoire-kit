"""Le minuteur optionnel du tick : une unité systemd utilisateur ``oneshot`` et son ``.timer`` (K12, §4.5, D14).

Le tick n'est pas résident. Sans minuteur, après une coupure de courant ou un forfait épuisé, rien ne
reprend avant la prochaine session ou commande. ``grimoire orchestrate install-timer`` écrit, pour **un**
projet, ``grimoire-tick-<nom>-<empreinte>.service`` (``Type=oneshot``, ``orchestrate tick --apply``) et son
``.timer`` sous ``$XDG_CONFIG_HOME/systemd/user`` ; ``uninstall-timer`` les retire. Désactivé par défaut :
rien n'est posé sans la commande. Sans systemd, la ligne crontab équivalente est rendue, à poser à la main.

Limite écrite : sans ``loginctl enable-linger``, un minuteur utilisateur ne tourne pas tant que
l'utilisateur ne s'est pas reconnecté après un redémarrage. Le tick ne fait aucun appel de LLM : il tourne
même avec un forfait épuisé, et il sort tout de suite sous une pause.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core.fsio import atomic_write

__all__ = [
    "DEFAULT_INTERVAL",
    "LINGER_NOTE",
    "TimerPlan",
    "cron_line",
    "install_timer",
    "parse_interval",
    "plan_timer",
    "timer_status",
    "uninstall_timer",
    "unit_dir",
]

DEFAULT_INTERVAL = "5m"
LINGER_NOTE = (
    "sans `loginctl enable-linger`, un minuteur utilisateur ne tourne pas tant que l'utilisateur ne s'est pas "
    "reconnecté après un redémarrage"
)
_INTERVAL = re.compile(r"^\s*(\d+)\s*(m|min|h)\s*$")
_SAFE = re.compile(r"[^a-z0-9-]+")

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def parse_interval(text: str) -> int:
    """``5m``, ``15min``, ``1h`` → minutes ; entre 1 min et 24 h, sinon ``ValueError``."""
    match = _INTERVAL.match(text or "")
    if not match:
        raise ValueError(f"intervalle illisible : {text!r} — attendu par exemple 5m, 15min ou 1h")
    minutes = int(match.group(1)) * (60 if match.group(2) == "h" else 1)
    if not 1 <= minutes <= 24 * 60:
        raise ValueError(f"intervalle hors bornes : {text!r} — entre 1 min et 24 h")
    return minutes


def unit_dir() -> Path:
    """``$XDG_CONFIG_HOME/systemd/user`` (``~/.config/systemd/user`` par défaut)."""
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(base) if base else Path.home() / ".config") / "systemd" / "user"


def _unit_name(project_root: Path) -> str:
    resolved = project_root.resolve()
    slug = _SAFE.sub("-", resolved.name.lower()).strip("-")[:32] or "projet"
    digest = hashlib.sha256(str(resolved).encode("utf-8", "surrogatepass")).hexdigest()[:8]
    return f"grimoire-tick-{slug}-{digest}"


def _systemd_quote(value: str) -> str:
    """Un argument d'``ExecStart`` : guillemets doubles, ``%`` et ``$`` neutralisés (spécificateurs, variables)."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def _spec(value: str) -> str:
    """Une valeur hors ``ExecStart`` : seuls les spécificateurs ``%`` sont neutralisés (un chemin s'y écrit sans guillemets)."""
    return value.replace("%", "%%")


def _command(project_root: Path, python: str) -> list[str]:
    return [python, "-m", "grimoire", "orchestrate", "tick", "--apply", "--project-root", str(project_root.resolve())]


@dataclass(frozen=True, slots=True)
class TimerPlan:
    """Ce que ``install-timer`` écrirait : les deux unités, leur dossier, et la ligne crontab de repli."""

    name: str
    directory: Path
    service: str
    timer: str
    cron: str
    minutes: int
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def service_path(self) -> Path:
        return self.directory / f"{self.name}.service"

    @property
    def timer_path(self) -> Path:
        return self.directory / f"{self.name}.timer"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "service_path": str(self.service_path), "timer_path": str(self.timer_path),
            "minutes": self.minutes, "cron": self.cron, "notes": list(self.notes),
        }


def cron_line(project_root: Path, minutes: int, python: str | None = None) -> str:
    """La ligne crontab équivalente — pour un poste sans systemd, posée à la main."""
    command = " ".join(shlex.quote(part) for part in _command(project_root, python or sys.executable))
    schedule = f"*/{minutes} * * * *" if minutes < 60 and 60 % minutes == 0 else (
        f"0 */{minutes // 60} * * *" if minutes % 60 == 0 and 24 % (minutes // 60) == 0 else None
    )
    if schedule is None:
        raise ValueError(f"{minutes} min ne s'écrit pas en crontab : choisir un diviseur de 60 min ou de 24 h")
    return f"{schedule} {command} >/dev/null 2>&1"


def plan_timer(project_root: Path, interval: str = DEFAULT_INTERVAL, *, python: str | None = None) -> TimerPlan:
    """Les unités d'un projet, sans rien écrire."""
    minutes = parse_interval(interval)
    root = project_root.resolve()
    exe = python or sys.executable
    name = _unit_name(root)
    exec_start = " ".join(_systemd_quote(part) for part in _command(root, exe))
    service = (
        "[Unit]\n"
        f"Description=Grimoire : tick d'orchestration de {_spec(root.name)} (aucun appel de LLM)\n"
        "\n[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={_spec(str(root))}\n"
        f"ExecStart={exec_start}\n"
    )
    timer = (
        "[Unit]\n"
        f"Description=Grimoire : tick de {_spec(root.name)} toutes les {minutes} min\n"
        "\n[Timer]\n"
        "OnBootSec=2min\n"
        f"OnUnitActiveSec={minutes}min\n"
        f"Unit={name}.service\n"
        "\n[Install]\n"
        "WantedBy=timers.target\n"
    )
    try:
        cron = cron_line(root, minutes, exe)
    except ValueError as exc:
        cron = f"# {exc}"
    return TimerPlan(name, unit_dir(), service, timer, cron, minutes, (LINGER_NOTE,))


def _systemctl(run: Runner | None) -> Runner | None:
    """Le lanceur de ``systemctl --user`` ; ``None`` sans systemd sur le poste."""
    if run is not None:
        return run
    if shutil.which("systemctl") is None:
        return None

    def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # arguments fixes, sans shell
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False
        )

    return _run


def install_timer(
    project_root: Path, interval: str = DEFAULT_INTERVAL, *, enable: bool = True, run: Runner | None = None,
    python: str | None = None,
) -> tuple[TimerPlan, list[str]]:
    """Écrit les deux unités ; avec *enable* et systemd présent, ``daemon-reload`` puis ``enable --now``.

    Rend le plan et ce qui a été fait (ou pourquoi pas). Sans systemd, rien n'est écrit : la ligne
    crontab du plan est à poser à la main.
    """
    plan = plan_timer(project_root, interval, python=python)
    runner = _systemctl(run)
    if runner is None:
        return plan, [f"systemd absent : rien n'est écrit ; ligne crontab à poser à la main — {plan.cron}"]
    atomic_write(plan.service_path, plan.service)
    atomic_write(plan.timer_path, plan.timer)
    done = [f"écrit : {plan.service_path}", f"écrit : {plan.timer_path}"]
    if not enable:
        return plan, [*done, f"non activé : `systemctl --user enable --now {plan.name}.timer` l'active"]
    for argv in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", f"{plan.name}.timer"]):
        result = runner(argv)
        if result.returncode != 0:
            return plan, [*done, f"échec de `{' '.join(argv)}` ({result.returncode}) : {(result.stderr or '').strip()[:300]}"]
    return plan, [*done, f"activé : {plan.name}.timer"]


def uninstall_timer(project_root: Path, *, run: Runner | None = None) -> tuple[TimerPlan, list[str]]:
    """Désactive puis retire les unités du projet ; sans systemd, rappelle la ligne crontab à ôter."""
    plan = plan_timer(project_root)
    runner = _systemctl(run)
    if runner is None:
        return plan, [f"systemd absent : retirer à la main la ligne crontab du projet ({plan.cron})"]
    done: list[str] = []
    if plan.timer_path.exists():
        result = runner(["systemctl", "--user", "disable", "--now", f"{plan.name}.timer"])
        if result.returncode != 0:
            done.append(f"désactivation refusée ({result.returncode}) : {(result.stderr or '').strip()[:300]}")
    for path in (plan.timer_path, plan.service_path):
        if path.exists():
            path.unlink()
            done.append(f"retiré : {path}")
    if not done:
        return plan, ["aucun minuteur installé pour ce projet"]
    runner(["systemctl", "--user", "daemon-reload"])
    return plan, done


def timer_status(project_root: Path) -> str:
    """Une ligne : le minuteur du projet est-il posé, et ce que son absence veut dire."""
    plan = plan_timer(project_root)
    if plan.timer_path.exists():
        return f"minuteur : {plan.timer_path} (activation non vérifiée ici : `systemctl --user status {plan.name}.timer`)"
    return "minuteur : absent — sans lui, rien ne reprend avant la prochaine session ou commande (`orchestrate install-timer`)"
