"""Fence git d'une tâche : un ``pre-push`` posé dans son worktree (K3, issue #768).

Le frein ``PreToolUse`` (:mod:`grimoire.hosts.decisions.lease_brake`) n'existe
que là où l'hôte prouve qu'un refus arrête l'outil (K0). Pour tout le reste, un
titulaire périmé pousserait encore sa branche. Ce module pose, dans le worktree
``task/<id>``, un ``pre-push`` qui compare l'epoch noté à l'installation à celui
du fichier de bail (:mod:`grimoire.missions.lease`) : un epoch qui n'est plus le
sien, un bail rendu ou annulé, et le push est refusé — quel que soit l'hôte.

Mécanisme : le dossier git du worktree reçoit ``grimoire-fence/fence.json``
(tâche, epoch, chemin du fichier de bail) et ``grimoire-fence/hooks/pre-push``,
et ``core.hooksPath`` de *ce worktree seul* (``extensions.worktreeConfig``) y
pointe. Le hook rend ensuite la main au ``pre-push`` du dépôt s'il y en a un :
la fence s'ajoute, elle ne remplace rien.

Limites écrites : ``--no-verify`` contourne tout hook git (le frein l'interdit là
où l'hôte le tient, et rien d'autre) ; un fichier de bail illisible ou une fence
mal formée refuse le push (fail-closed, avec le motif) ; la fence ne protège que
le worktree où elle est posée.
"""

from __future__ import annotations

import json
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from grimoire.missions.lease import ORIGIN_CANCEL, lease_path, read_lease

FENCE_DIRNAME = "grimoire-fence"
_SCHEMA = "grimoire-task-fence/v1"

_HOOK_TEMPLATE = """#!/bin/sh
# Grimoire : fence git de la tache (K3, issue #768). Refuse le push d'une session dont
# le bail n'est plus le sien, puis rend la main au pre-push du depot s'il y en a un.
fence={fence}
input="$(mktemp)" || exit 1
trap 'rm -f "$input"' EXIT
cat > "$input"
{python} -m grimoire.missions.prepush_fence check "$fence" || exit 1
common="$(git rev-parse --git-common-dir)"
if [ -x "$common/hooks/pre-push" ]; then
    "$common/hooks/pre-push" "$@" < "$input"
    exit $?
fi
exit 0
"""


@dataclass(frozen=True, slots=True)
class FenceVerdict:
    """Le verdict de la fence : ``ok``, et le motif quand le push est refusé."""

    ok: bool
    reason: str = ""


def _git(worktree: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
    )
    if done.returncode != 0:
        msg = f"git {' '.join(args)} a échoué dans {worktree} : {done.stderr.strip() or done.stdout.strip()}"
        raise RuntimeError(msg)
    return done.stdout.strip()


def fence_dir(worktree: Path) -> Path:
    """Le dossier de la fence, dans le dossier git propre au worktree."""
    return Path(_git(worktree, "rev-parse", "--absolute-git-dir")) / FENCE_DIRNAME


def install_fence(worktree: Path, ledger_root: Path, task_id: str, epoch: int, *, python: str | None = None) -> Path:
    """Pose la fence de *task_id* à l'epoch *epoch* dans *worktree* ; rend le chemin du hook.

    *epoch* est celui que le titulaire tient à l'installation. Réinstaller après un
    nouveau claim remplace l'ancien epoch : c'est le geste du nouveau titulaire.
    """
    root = fence_dir(worktree)
    hooks = root / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    lease = lease_path(ledger_root.resolve(), task_id)
    state = {"$schema": _SCHEMA, "task_id": task_id, "epoch": epoch, "lease_file": str(lease)}
    (root / "fence.json").write_text(json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    hook = hooks / "pre-push"
    hook.write_text(
        _HOOK_TEMPLATE.format(fence=shlex.quote(str(root / "fence.json")), python=shlex.quote(python or sys.executable)),
        encoding="utf-8",
    )
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _git(worktree, "config", "extensions.worktreeConfig", "true")
    _git(worktree, "config", "--worktree", "core.hooksPath", str(hooks))
    return hook


def check_fence(fence_file: Path) -> FenceVerdict:
    """Compare l'epoch de la fence au fichier de bail ; tout ce qui est illisible refuse."""
    try:
        raw = json.loads(fence_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return FenceVerdict(False, f"fence illisible ({fence_file}) : {type(exc).__name__}")
    if not isinstance(raw, dict) or raw.get("$schema") != _SCHEMA:
        return FenceVerdict(False, f"fence d'un autre schéma ({fence_file})")
    task_id, epoch, lease_file = raw.get("task_id"), raw.get("epoch"), raw.get("lease_file")
    if not isinstance(task_id, str) or isinstance(epoch, bool) or not isinstance(epoch, int) or not isinstance(lease_file, str):
        return FenceVerdict(False, f"fence mal formée ({fence_file})")
    lease = read_lease(Path(lease_file))
    if lease is None:
        return FenceVerdict(False, f"fichier de bail de {task_id} absent ou illisible ({lease_file})")
    if lease.holder and lease.epoch == epoch:
        return FenceVerdict(True)
    if lease.origin == ORIGIN_CANCEL:
        return FenceVerdict(False, f"la tâche {task_id} a été annulée (epoch du worktree {epoch}, bail à {lease.epoch})")
    return FenceVerdict(
        False, f"epoch du worktree {epoch} périmé : le bail de {task_id} est à l'epoch {lease.epoch}"
        + ("" if lease.holder else " (rendu, sans titulaire)"),
    )


def main(argv: list[str] | None = None) -> int:
    """``python -m grimoire.missions.prepush_fence check <fence.json>`` : 0 si le push passe, 1 sinon."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "check":
        sys.stderr.write("usage : prepush_fence check <fence.json>\n")
        return 2
    verdict = check_fence(Path(args[1]))
    if not verdict.ok:
        sys.stderr.write(
            f"[Grimoire] push refusé — {verdict.reason}.\n"
            "Cette session ne tient plus la tâche : ne pousse pas. `grimoire task show` dit où elle en est.\n"
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée du hook
    raise SystemExit(main())
