"""``grimoire up --prove`` — prouver que le hook tire, pas seulement qu'il est installé.

``grimoire up`` puis ``grimoire doctor`` répondent à « le projet est-il en état ? »
(des fichiers, des sondes d'environnement). Aucun des deux n'exécute la commande
de hook que l'hôte lancera, ni ne regarde si elle a un effet : un
``.claude/settings.json`` dont le hook a été retiré, ou un ``grimoire-hook`` absent du
``PATH`` de l'hôte, laisse tous les checks verts. ``--prove`` ferme cet écart par
une expérience, pas par une lecture de configuration :

1. une tâche jetable est ouverte dans le Mission Ledger ;
2. le gate de preuve de cette tâche est évalué : il doit être **rouge** (ses
   artefacts n'existent pas), sinon il ne discrimine rien et rien n'est attribué
   au hook ;
3. la commande ``SessionStart`` **telle que la configuration de l'hôte la
   déclare** est exécutée (``grimoire-hook`` résolu dans le ``PATH``, charge utile
   d'hôte, tâche désignée par ``GRIMOIRE_TASK_ID``). Ce hook scaffolde les
   artefacts de la tâche active ;
4. le gate est réévalué : il doit être **vert**, et au moins un artefact doit
   être apparu. Un hook qui sort 0 sans rien faire échoue donc ;
5. la tâche est annulée et les artefacts que le hook a créés sont retirés ;
6. ce que l'expérience a fait basculer est rendu : un projet qui n'avait ni board
   ni ledger les retrouve absents (le board est ce qui fait « gouverné » pour le
   hook ; le laisser derrière changerait la réponse du projet à la session
   suivante).

Limites, écrites ici parce que le message de sortie ne les porte pas :

- seules les configurations JSON de Claude Code (``.claude/settings*.json``) et de
  Copilot (``.github/hooks/*.json``) sont lues ; les hôtes sans configuration de ce
  type (plugin opencode, règles Cursor) ne sont pas évalués, et ne comptent ni
  pour ni contre ;
- seule une commande dont le premier mot est ``grimoire-hook`` est exécutée. Une
  autre commande déclarée dans un hook SessionStart n'est jamais lancée par ce
  contrôle (un dépôt cloné ne doit pas faire exécuter ce qu'il veut à ``up``) ; elle
  est rendue sous ``prove.skipped`` comme non prouvée, et ne compte ni dans
  ``hooks_fired`` ni dans ``first_gate`` (la Forge lance son propre script
  de passerelle : il n'a pas à faire échouer la preuve du hook du kit) ;
- sur un projet non gouverné (ni board ni ledger), l'expérience ouvre le ledger et
  projette le board le temps de la preuve : elle mesure donc le hook **dans l'état
  gouverné**, que le projet n'a pas encore. Board et ledger sont retirés ensuite,
  et le rapport le dit (``prove.notes``) ; en session réelle le hook y répond
  « projet non gouverné » et ne scaffolde rien tant que le standard n'est pas
  adopté ;
- ``time_to_first_proof_s`` part du début de la commande ``up`` (imports du
  processus exclus) et s'arrête au premier gate vert, avant le nettoyage ;
- sur un projet qui avait déjà un ledger, le journal d'événements garde la trace
  de la tâche jetable (créée puis annulée) et le board la projette archivée :
  c'est voulu, la preuve est auditable. Le journal d'exécution (traces,
  ``_grimoire-output/events``) et les fichiers de session ``up-prove-*`` restent
  dans tous les cas.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireError

if TYPE_CHECKING:
    from grimoire.cli.cmd_up import _UpState

__all__ = ["ConfiguredHook", "HookProof", "ProveReport", "discover_session_start_hooks", "run_prove_step"]

#: L'exécutable que les émetteurs d'hôte écrivent (``Emitter.hook_command``).
HOOK_BINARY = "grimoire-hook"
SESSION_START = "sessionstart"
_ACTOR = "grimoire-up-prove"
_DEFAULT_HOOK_TIMEOUT_S = 30.0
_MAX_HOOK_TIMEOUT_S = 120.0
_TASK_TITLE = "grimoire up --prove (tâche jetable)"
_TASK_CRITERION = "le gate de preuve de la tâche jetable passe après le hook SessionStart"
_DETAIL_LIMIT = 300


@dataclass(frozen=True, slots=True)
class ConfiguredHook:
    """Une commande SessionStart lue dans la configuration d'un hôte."""

    source: str
    command: str
    timeout: float = _DEFAULT_HOOK_TIMEOUT_S


@dataclass(slots=True)
class HookProof:
    """Le verdict de l'expérience pour une commande de hook."""

    source: str
    command: str
    fired: bool = False
    gate: str = "unevaluated"  # "green" | "red" | "unevaluated"
    task_id: str = ""
    created: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "command": self.command,
            "fired": self.fired,
            "gate": self.gate,
            "task_id": self.task_id,
            "created": list(self.created),
            "detail": self.detail,
        }


@dataclass(slots=True)
class ProveReport:
    """Les trois champs que ``up --prove`` publie, et ce qui les fonde."""

    hooks: list[HookProof] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    #: Commandes SessionStart étrangères au kit : jamais lancées, hors verdict.
    skipped: list[HookProof] = field(default_factory=list)
    #: Ce que l'expérience a dû supposer ou défaire, sans que ce soit un échec.
    notes: list[str] = field(default_factory=list)
    time_to_first_proof_s: float = 0.0

    @property
    def hooks_fired(self) -> bool:
        return bool(self.hooks) and all(h.fired for h in self.hooks)

    @property
    def first_gate(self) -> str:
        gates = [h.gate for h in self.hooks]
        if not gates:
            return "unevaluated"
        if all(g == "green" for g in gates):
            return "green"
        return "red" if "red" in gates else "unevaluated"

    @property
    def ok(self) -> bool:
        return self.hooks_fired and self.first_gate == "green"

    def to_payload(self) -> dict[str, Any]:
        """Les champs ajoutés à la sortie JSON de ``up`` : trois au premier niveau, le reste en détail."""
        return {
            "hooks_fired": self.hooks_fired,
            "first_gate": self.first_gate,
            "time_to_first_proof_s": self.time_to_first_proof_s,
            "prove": {
                "hooks": [h.to_dict() for h in self.hooks],
                "problems": list(self.problems),
                "skipped": [h.to_dict() for h in self.skipped],
                "notes": list(self.notes),
            },
        }


# ── Découverte : ce que l'hôte exécuterait ────────────────────────────────────


def _hook_config_files(target: Path) -> list[Path]:
    claude = [target / ".claude" / "settings.json", target / ".claude" / "settings.local.json"]
    copilot_dir = target / ".github" / "hooks"
    copilot = sorted(copilot_dir.glob("*.json")) if copilot_dir.is_dir() else []
    return [p for p in (*claude, *copilot) if p.is_file()]


def _commands_of(entry: Any) -> list[tuple[str, float]]:
    """Les commandes d'une entrée de hook, qu'elle soit à plat (Copilot) ou imbriquée (Claude Code)."""
    if not isinstance(entry, dict):
        return []
    found: list[tuple[str, float]] = []
    nested = entry.get("hooks")
    items = [entry, *nested] if isinstance(nested, list) else [entry]
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("command"), str):
            continue
        raw = item.get("timeout")
        timeout = float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) and raw > 0 else _DEFAULT_HOOK_TIMEOUT_S
        found.append((item["command"], min(timeout, _MAX_HOOK_TIMEOUT_S)))
    return found


def discover_session_start_hooks(target: Path) -> tuple[list[ConfiguredHook], list[HookProof]]:
    """Les commandes SessionStart configurées, et les configurations dont l'hôte ne tirerait rien.

    ``disableAllHooks`` (Claude Code) ou un JSON illisible font que l'hôte ne
    lancerait rien depuis ce fichier : c'est un hook qui ne tire pas, rendu
    comme tel (un :class:`HookProof` non tiré), pas une configuration ignorée.
    """
    hooks: list[ConfiguredHook] = []
    problems: list[HookProof] = []
    for path in _hook_config_files(target):
        rel = path.relative_to(target).as_posix()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(HookProof(rel, "", detail=f"configuration illisible ({type(exc).__name__}) : l'hôte n'en tirerait aucun hook"))
            continue
        if not isinstance(data, dict):
            problems.append(HookProof(rel, "", detail="configuration inattendue (objet JSON attendu)"))
            continue
        if data.get("disableAllHooks") is True:
            problems.append(HookProof(rel, "", detail="disableAllHooks=true : l'hôte ne lance aucun hook"))
            continue
        events = data.get("hooks")
        if not isinstance(events, dict):
            continue
        for event, entries in events.items():
            if str(event).replace("_", "").replace("-", "").lower() != SESSION_START or not isinstance(entries, list):
                continue
            for entry in entries:
                hooks.extend(ConfiguredHook(rel, command, timeout) for command, timeout in _commands_of(entry))
    return hooks, problems


# ── Exécution de la commande telle que l'hôte la lancerait ────────────────────


def is_kit_hook_command(command: str) -> bool:
    """Si le premier mot de *command* est ``grimoire-hook`` : la seule commande que ``--prove`` lance."""
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        argv = command.split()
    return bool(argv) and argv[0] == HOOK_BINARY


def _run_configured_hook(hook: ConfiguredHook, target: Path, task_id: str, session_id: str) -> str:
    """Lance *hook* ; rend ``""`` s'il a tourné, sinon la raison pour laquelle il n'a pas pu."""
    try:
        argv = shlex.split(hook.command, posix=os.name != "nt")
    except ValueError as exc:
        return f"commande illisible ({exc})"
    if not argv or argv[0] != HOOK_BINARY:
        return f"commande non reconnue, non exécutée : {hook.command[:80]!r} (seul `{HOOK_BINARY}` est lancé)"
    resolved = shutil.which(HOOK_BINARY)
    if resolved is None:
        return (
            f"`{HOOK_BINARY}` introuvable dans le PATH : l'hôte ne pourrait pas lancer ce hook "
            "(installe le kit avec pipx, ou ajoute son dossier de scripts au PATH de l'hôte)"
        )
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(target), "GRIMOIRE_TASK_ID": task_id}
    payload = json.dumps({"session_id": session_id, "hook_event_name": "SessionStart", "cwd": str(target)})
    try:
        proc = subprocess.run(
            [resolved, *argv[1:]], input=payload, cwd=target, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=hook.timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return f"timeout de {hook.timeout:.0f} s dépassé : l'hôte tuerait ce hook"
    except OSError as exc:
        return f"hook non exécutable : {exc}"
    if proc.returncode != 0:
        return f"code de sortie {proc.returncode} : {(proc.stderr or proc.stdout).strip()[:_DETAIL_LIMIT]}"
    return ""


# ── L'expérience, une commande à la fois ──────────────────────────────────────


def _discard(target: Path, service: Any, proof: HookProof, report: ProveReport) -> None:
    """Annule la tâche jetable et retire les seuls fichiers que le hook a créés."""
    if proof.task_id:
        try:
            service.cancel(proof.task_id, "tâche jetable de `grimoire up --prove`", _ACTOR)
        except GrimoireError as exc:
            report.problems.append(f"{proof.task_id} : annulation impossible ({exc}) — à annuler à la main")
    for rel in proof.created:
        path = target / rel
        try:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
        except OSError:
            continue  # silent-ok — un dossier non vide n'est pas à nous, on ne touche à rien d'autre


def _prove_one(target: Path, service: Any, hook: ConfiguredHook) -> HookProof:
    from grimoire.core.agentic_standard import check_evidence_gates
    from grimoire.core.standard_task_scaffold import missing_task_artifacts, task_artifact_relpaths

    proof = HookProof(source=hook.source, command=hook.command)
    try:
        added = service.add(
            _TASK_TITLE, (_TASK_CRITERION,), owner=_ACTOR, actor=_ACTOR, origin="up-prove", ready=True,
        )
    except (GrimoireError, OSError) as exc:
        proof.detail = f"tâche jetable refusée par le ledger : {str(exc)[:_DETAIL_LIMIT]}"
        return proof
    proof.task_id = added.task.id
    before = set(missing_task_artifacts(target, proof.task_id))
    if check_evidence_gates(target, task_id=proof.task_id).ok:
        proof.detail = "le gate était déjà vert avant le hook : rien ne peut lui être attribué"
        return proof
    failure = _run_configured_hook(hook, target, proof.task_id, f"up-prove-{uuid.uuid4().hex[:12]}")
    after = set(missing_task_artifacts(target, proof.task_id))
    relpaths = task_artifact_relpaths(proof.task_id)
    proof.created = sorted(relpaths[key].as_posix() for key in before - after)
    gate = check_evidence_gates(target, task_id=proof.task_id)
    if failure:
        proof.detail = failure
    elif not proof.created:
        proof.detail = "le hook a tourné mais n'a produit aucun artefact : il est sans effet"
    else:
        proof.fired = True
        proof.detail = f"{len(proof.created)} artefact(s) créé(s) par le hook"
    if not gate.ok:
        proof.gate = "red"
        proof.detail += f" ; gate rouge, il manque : {', '.join(gate.missing) or 'voir `grimoire standard gate check`'}"
    elif proof.fired:
        proof.gate = "green"
    return proof


def _is_governed_now(target: Path) -> bool:
    """Ce que le hook SessionStart répondrait aujourd'hui : gouverné ou non (échec de lecture = non)."""
    try:
        from grimoire.hosts.decisions.activation import _is_governed

        return bool(_is_governed(target))
    except Exception:
        return False


def _restore_governance(service: Any, *, board_existed: bool, ledger_existed: bool, report: ProveReport) -> None:
    """Retire le board et le ledger que l'expérience a créés ; ne touche jamais à ceux qui préexistaient."""
    from grimoire.core.standard_state import invalidate_cache
    from grimoire.missions.service import BOARD_RELPATH

    board = service.home_root / BOARD_RELPATH
    ledger_root: Path = service.ledger_root
    try:
        if not board_existed:
            board.unlink(missing_ok=True)
        if not ledger_existed and ledger_root.is_dir():
            shutil.rmtree(ledger_root)
            with contextlib.suppress(OSError):
                ledger_root.parent.rmdir()  # silent-ok — non vide : ce n'est pas à nous
        invalidate_cache(service.home_root)
    except OSError as exc:
        report.problems.append(
            f"board/ledger créés par la preuve non retirés ({exc}) — à supprimer à la main : {board}, {ledger_root}"
        )


def run_prove(target: Path, *, started: float) -> ProveReport:
    """Mène l'expérience pour chaque commande SessionStart du kit ; les autres sont rapportées, jamais lancées."""
    from grimoire.missions.service import BOARD_RELPATH, TaskService

    report = ProveReport()
    found, unfired = discover_session_start_hooks(target)
    hooks = [h for h in found if is_kit_hook_command(h.command)]
    for hook in found:
        if not is_kit_hook_command(hook.command):
            report.skipped.append(HookProof(
                hook.source, hook.command,
                detail=f"commande non reconnue, non exécutée : {hook.command[:80]!r} (seul `{HOOK_BINARY}` est lancé) ; non prouvée",
            ))
    if not hooks and not unfired:
        foreign = f" ({len(report.skipped)} commande(s) étrangère(s) non prouvée(s))" if report.skipped else ""
        report.problems.append(
            f"aucune commande SessionStart `{HOOK_BINARY}` configurée dans .claude/settings.json ni .github/hooks/{foreign} : "
            "rien ne tirerait au démarrage d'une session (relance `grimoire host sync`)"
        )
    service = TaskService(target)
    board_existed = (service.home_root / BOARD_RELPATH).is_file()
    ledger_existed = service.ledger_root.exists()
    governed_before = _is_governed_now(target)
    proofs: list[HookProof] = list(unfired)
    first_green: float | None = None
    try:
        for hook in hooks:
            proofs.append(_prove_one(target, service, hook))
            if first_green is None and proofs[-1].gate == "green":
                first_green = time.monotonic()
        report.time_to_first_proof_s = round((first_green or time.monotonic()) - started, 3)
        for proof in proofs:
            _discard(target, service, proof, report)
    finally:
        if hooks:
            _restore_governance(service, board_existed=board_existed, ledger_existed=ledger_existed, report=report)
    if hooks and not governed_before:
        report.notes.append(
            "projet non gouverné avant la preuve : board et ledger ont été ouverts le temps de l'expérience, "
            "puis retirés ; en session réelle le hook répond « projet non gouverné » et ne scaffolde rien "
            "(`grimoire standard init` pour adopter le standard)"
        )
    report.hooks = proofs
    return report


def run_prove_step(
    state: _UpState, target: Path, *, started: float, dry_run: bool, no_standard: bool
) -> ProveReport:
    """Étape ``prove`` de ``grimoire up`` : refuse ce qu'elle ne peut pas prouver, sinon mène l'expérience."""
    from grimoire.cli.cmd_up import StepResult, _load_config_quiet

    report = ProveReport(time_to_first_proof_s=round(time.monotonic() - started, 3))
    blocker = ""
    if dry_run:
        blocker = "--prove exécute un hook et ouvre une tâche : incompatible avec --dry-run"
    elif no_standard:
        blocker = "--prove s'appuie sur le standard (gates de preuve) : incompatible avec --no-standard"
    elif _load_config_quiet(target) is None:
        blocker = "projet non initialisé : aucune configuration à prouver"
    if blocker:
        report.problems.append(blocker)
        state.steps.append(StepResult("prove", "failed", blocker))
        return report

    report = run_prove(target, started=started)
    if report.ok:
        state.steps.append(StepResult(
            "prove", "done",
            " ; ".join([
                f"hook SessionStart tiré ({len(report.hooks)} configuration(s) d'hôte), gate vert en {report.time_to_first_proof_s:.1f} s",
                *([f"{len(report.skipped)} commande(s) étrangère(s) non prouvée(s)"] if report.skipped else []),
                *report.notes,
                *report.problems,
            ]),
        ))
    else:
        causes = [f"{h.source} : {h.detail}" for h in report.hooks if not (h.fired and h.gate == "green")]
        state.steps.append(StepResult("prove", "failed", " ; ".join([*causes, *report.problems])[:600]))
    return report
