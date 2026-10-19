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
pointe. Comme ``core.hooksPath`` est unique, la fence relaie : à l'installation elle lit le
dossier de hooks effectif du dépôt (``core.hooksPath`` du dépôt — husky, par exemple — sinon
``<git-common-dir>/hooks``), le note dans ``fence.json`` (``hooks_dir``) et écrit, pour
chaque hook git client, un relais qui exécute le hook d'origine avec ses arguments et son
entrée. Le ``pre-push`` lance d'abord le contrôle d'epoch, puis celui d'origine : la fence
s'ajoute, elle ne remplace rien.

Limites écrites : le dossier d'origine est lu à l'installation, pas à chaque hook (un
``core.hooksPath`` changé ensuite demande de réinstaller) ; ``--no-verify`` contourne tout hook git (le frein l'interdit là
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

#: Les hooks git côté client (githooks(5)) que la fence relaie vers le dossier d'origine. Hors
#: liste : les hooks serveur (rien à relayer dans un worktree) et ``fsmonitor-watchman``, que git
#: ne cherche pas dans ``core.hooksPath`` mais par ``core.fsmonitor``.
RELAYED_HOOKS: tuple[str, ...] = (
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
    "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout", "post-merge",
    "pre-push", "pre-auto-gc", "post-rewrite", "reference-transaction", "post-index-change",
)

_RELAY_TEMPLATE = """#!/bin/sh
# Grimoire : relais de la fence de la tache (K3, issue #768) vers le hook d'origine du depot.
orig={orig}
if [ -x "$orig" ]; then
    exec "$orig" "$@"
fi
exit 0
"""

_PRE_PUSH_TEMPLATE = """#!/bin/sh
# Grimoire : fence git de la tache (K3, issue #768). Refuse le push d'une session dont
# le bail n'est plus le sien, puis rend la main au pre-push d'origine du depot s'il y en a un.
fence={fence}
orig={orig}
input="$(mktemp)" || exit 1
trap 'rm -f "$input"' EXIT
cat > "$input"
{python} -m grimoire.missions.prepush_fence check "$fence" || exit 1
if [ -x "$orig" ]; then
    "$orig" "$@" < "$input"
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


def _git_value(worktree: Path, *args: str) -> str:
    """Une valeur de config git, ``""`` si elle n'existe pas (``git config --get`` sort en 1)."""
    done = subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def _origin_hooks_dir(worktree: Path, root: Path) -> Path:
    """Le dossier de hooks que git utiliserait sans la fence : celui qu'elle doit relayer.

    ``core.hooksPath`` effectif (``--type=path`` développe ``~``) ; un chemin relatif se lit
    depuis la racine du worktree, là où git lance les hooks. Sans ``core.hooksPath``,
    ``<git-common-dir>/hooks``. Réinstaller : si ``core.hooksPath`` est déjà celui de la fence,
    on garde l'origine notée la première fois — jamais la fence elle-même, qui se relaierait.
    """
    ours = (root / "hooks").resolve()
    configured = _git_value(worktree, "config", "--type=path", "--get", "core.hooksPath")
    if configured:
        top = Path(_git(worktree, "rev-parse", "--show-toplevel"))
        candidate = (top / configured).resolve()
        if candidate != ours:
            return candidate
        noted = _noted_hooks_dir(root / "fence.json")
        if noted is not None:
            return noted
    common = Path(_git(worktree, "rev-parse", "--git-common-dir"))
    return ((worktree / common) if not common.is_absolute() else common).resolve() / "hooks"


def _noted_hooks_dir(fence_file: Path) -> Path | None:
    try:
        raw = json.loads(fence_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    noted = raw.get("hooks_dir") if isinstance(raw, dict) else None
    return Path(noted) if isinstance(noted, str) and noted else None


def install_fence(worktree: Path, ledger_root: Path, task_id: str, epoch: int, *, python: str | None = None) -> Path:
    """Pose la fence de *task_id* à l'epoch *epoch* dans *worktree* ; rend le chemin du hook ``pre-push``.

    *epoch* est celui que le titulaire tient à l'installation. Réinstaller après un
    nouveau claim remplace l'ancien epoch : c'est le geste du nouveau titulaire. Tous les
    hooks git client du dépôt restent actifs (:data:`RELAYED_HOOKS`, relayés vers le dossier
    d'origine) ; seul ``pre-push`` ajoute le contrôle d'epoch.
    """
    root = fence_dir(worktree)
    hooks = root / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    origin = _origin_hooks_dir(worktree, root)
    lease = lease_path(ledger_root.resolve(), task_id)
    state = {
        "$schema": _SCHEMA, "task_id": task_id, "epoch": epoch, "lease_file": str(lease), "hooks_dir": origin.as_posix(),
    }
    (root / "fence.json").write_text(json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    for name in RELAYED_HOOKS:
        quoted = shlex.quote((origin / name).as_posix())
        body = (
            _PRE_PUSH_TEMPLATE.format(
                fence=shlex.quote((root / "fence.json").as_posix()), orig=quoted,
                python=shlex.quote(Path(python or sys.executable).as_posix()),
            )
            if name == "pre-push"
            else _RELAY_TEMPLATE.format(orig=quoted)
        )
        hook = hooks / name
        hook.write_bytes(body.encode("utf-8"))  # LF strict : un CRLF casserait le shebang sous Windows
        hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _git(worktree, "config", "extensions.worktreeConfig", "true")
    _git(worktree, "config", "--worktree", "core.hooksPath", str(hooks))
    return hooks / "pre-push"


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
        message = (
            f"[Grimoire] push refusé — {verdict.reason}.\n"
            "Cette session ne tient plus la tâche : ne pousse pas. `grimoire task show` dit où elle en est.\n"
        )
        # UTF-8 explicite : sous Windows, stderr est en code page locale et git/le lecteur attendent de l'UTF-8.
        buffer = getattr(sys.stderr, "buffer", None)
        if buffer is not None:
            buffer.write(message.encode("utf-8"))
            buffer.flush()
        else:
            sys.stderr.write(message)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée du hook
    raise SystemExit(main())
