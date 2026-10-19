#!/usr/bin/env python3
"""Bras ``aidlc`` du banc comparatif (B-06, POS-3) : AI-DLC 2.11.0, harness claude, HOME isolé.

Ce module ajoute le troisième bras du protocole comparatif
(``docs/bench-three-arms.md`` §15) sans toucher aux bras ``nu`` et ``kit-gov``
de ``three_arms.py`` : mêmes tâches (``sample_tasks``, graine 551), même
préparation de dépôt (W1-08a), même environnement d'agent (``run_environment``),
même contrôle de transcription (W1-08b), mêmes tests cachés, même ligne
``RunRecord`` dans ``state/results.jsonl``, sous l'étiquette ``aidlc``.

Configuration épinglée (pré-enregistrée par ``--dry-run`` dans
``state/aidlc-preregistration.json``, puis exigée telle quelle par ``--run``) :

- AI-DLC ``2.11.0`` (tag ``v2.11.0``, commit :data:`AIDLC_TAG_COMMIT`), vérifié
  dans un clone de référence lu sans écriture (``git rev-parse``, ``git show``) ;
- installation par l'installeur du tag (``scripts/install.sh --version 2.11.0``)
  dans un ``HOME`` isolé propre au bras (``AIDLC_BIN_DIR``, ``AIDLC_INSTALL_ROOT``
  et ``XDG_DATA_HOME`` sous ce ``HOME``), jamais dans celui de l'opérateur ;
- ``aidlc config --harness claude --mcp none --yes`` dans chaque dépôt de tâche,
  sans politique de modèle : tous les agents héritent du modèle de session,
  celui de ``--model`` (B-01) ;
- scope ``express`` (10 stages) par défaut, ``bugfix`` (9 stages) en option ;
- deux pilotes, au choix du pré-enregistrement (``--driver``) :
  ``unattended`` (``AIDLC_UNATTENDED=1``, le mode sans humain que l'outil
  documente : il retient l'autorité humaine, donc aucun gate ne se lève) et
  ``scripted-person`` (configuration spéciale déclarée : une réponse fixe,
  ``Approved.``, rejouée par ``claude -p --resume`` à chaque fin de tour, sur le
  modèle du ``PersonScript`` des tests de bout en bout d'AI-DLC).

Garde-fou de budget, fermé par défaut (:class:`BudgetGuard`) : ``--run`` refuse
de partir sans ``--max-total-usd`` explicite ; avant CHAQUE appel ``claude -p``
(y compris une reprise de session), le coût cumulé connu est comparé au plafond,
et l'appel reçoit le reste en ``--max-budget-usd`` (arrêt par Claude Code
lui-même pendant l'appel) ; un coût inconnu (pas de ``total_cost_usd``, appel
commencé mais jamais terminé dans le journal) ne vaut jamais 0 et bloque tout
appel suivant. Le cumul vit dans ``state/aidlc-budget.jsonl`` (ou le journal
partagé de ``--budget-ledger``), écrit avant et après chaque appel : une
campagne relancée repart du cumul, jamais de zéro.

Sous ``--resume``, ``total_cost_usd`` et ``modelUsage`` cumulent toute la
session (mesuré au pilote B-06, Claude Code 2.1.302) : le coût d'un appel est
la différence avec le total précédent de la même session, jamais le total.

``--dry-run`` ne lance aucun modèle : il vérifie l'épinglage, prépare les
dépôts, installe et configure AI-DLC (local, sans fournisseur), écrit le
pré-enregistrement et les commandes ``claude`` qui seraient lancées, et
confronte l'estimation de coût (``--estimate``) au plafond donné.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

sys.path.append(str(Path(__file__).resolve().parent))
import three_arms as ta
from cost_gate import filter_tasks
from hidden_reads import check_transcript
from model_pin import ModelMismatchError, detect_claude_code_version, ensure_results_match_model
from results_io import read_results_rows
from task_repo import (
    ContaminatedTaskRepoError,
    assert_task_repo_clean,
    prepare_task_repo,
    reset_run_dir,
    run_dir_for,
)

# ── Configuration épinglée ───────────────────────────────────────────────────

ARM = "aidlc"
AIDLC_VERSION = "2.11.0"
AIDLC_TAG = "v2.11.0"
#: ``git rev-parse v2.11.0^{commit}`` dans github.com/awslabs/aidlc-workflows.
AIDLC_TAG_COMMIT = "6a378b53c0a4fe0641ed7d8de8dfff94264d5b6a"
AIDLC_HARNESS = "claude"

#: Stages qui s'exécutent par scope, d'après la section « Membership » de
#: ``core/scopes/aidlc-<scope>.md`` au tag (10 / 33 et 9 / 33 dans
#: ``docs/guide/workflow-profiles.md``). Reverse Engineering et la queue
#: Operation sont CONDITIONAL : ils peuvent se sauter d'eux-mêmes.
SCOPE_STAGES: dict[str, tuple[str, ...]] = {
    "express": (
        "workspace-scaffold",
        "workspace-detection",
        "state-init",
        "reverse-engineering",
        "requirements-analysis",
        "code-generation",
        "build-and-test",
        "deployment-pipeline",
        "deployment-execution",
        "observability-setup",
    ),
    "bugfix": (
        "workspace-scaffold",
        "workspace-detection",
        "state-init",
        "reverse-engineering",
        "requirements-analysis",
        "code-generation",
        "build-and-test",
        "deployment-pipeline",
        "deployment-execution",
    ),
}
DEFAULT_SCOPE = "express"

DRIVER_UNATTENDED = "unattended"
DRIVER_SCRIPTED = "scripted-person"
DRIVERS: tuple[str, ...] = (DRIVER_UNATTENDED, DRIVER_SCRIPTED)
#: Réponse rejouée par le pilote ``scripted-person`` : un mot d'approbation que
#: le lecteur de réponses d'AI-DLC 2.11.0 accepte tel quel (CHANGELOG 2.11.0,
#: « "approved", a number, or "looks fine but..." each do what they say »).
SCRIPTED_REPLY = "Approved."
DEFAULT_MAX_PERSON_REPLIES = 12
#: Un run AI-DLC enchaîne plusieurs stages et sessions : 15 min (le défaut des
#: autres bras, qui finissent en ~25 s) ne suffit pas. Pré-enregistré.
DEFAULT_RUN_TIMEOUT_S = 60 * 60
#: Le conducteur relance ``aidlc engine orchestrate next`` en boucle par
#: construction ; le seuil de boucle des autres bras (4) le couperait à tort.
#: 8 reprend le plafond « no-progress » d'AI-DLC lui-même.
LOOP_REPEAT_THRESHOLD = 8

BUDGET_LEDGER = "aidlc-budget.jsonl"
RUNS_META = "aidlc-runs.jsonl"
PREREGISTRATION = "aidlc-preregistration.json"
PLANNED_COMMANDS = "aidlc-planned-commands.json"


class PinError(RuntimeError):
    """Le clone de référence ne porte pas AI-DLC 2.11.0 tel qu'épinglé."""


class InstallError(RuntimeError):
    """Installation ou configuration d'AI-DLC impossible (aucun modèle lancé)."""


class BudgetError(RuntimeError):
    """Le garde-fou de budget refuse l'appel suivant."""


class PreregistrationError(RuntimeError):
    """``--run`` sans pré-enregistrement, ou avec une configuration différente."""


# ── Garde-fou de budget (fermé par défaut) ───────────────────────────────────


def parse_cap(raw: object) -> float:
    """Le plafond en dollars, ou :class:`BudgetError` : absent, non numérique, non fini ou ≤ 0."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise BudgetError("plafond en dollars absent : --max-total-usd est obligatoire pour lancer un run payant")
    if isinstance(raw, bool):
        raise BudgetError(f"plafond invalide : {raw!r}")
    try:
        cap = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise BudgetError(f"plafond invalide : {raw!r} n'est pas un montant en dollars") from None
    if not math.isfinite(cap) or cap <= 0:
        raise BudgetError(f"plafond invalide : {raw!r} (attendu : un montant fini strictement positif)")
    return cap


@dataclass
class BudgetGuard:
    """Cumul des coûts d'appels ``claude -p`` du bras, adossé à un journal append-only.

    Chaque appel est encadré par deux lignes (``begin`` puis ``end``). À la
    relecture, un ``begin`` sans ``end`` (processus tué pendant l'appel) et un
    ``end`` sans coût connu valent *inconnu* ; une ligne illisible aussi. Un
    seul inconnu bloque tout appel suivant : il ne compte jamais pour 0.
    """

    cap_usd: float
    ledger_path: Path | None = None
    spent_usd: float = 0.0
    unknown_calls: list[str] = field(default_factory=list)
    calls: int = 0

    @classmethod
    def open(cls, cap: object, ledger_path: Path | None) -> BudgetGuard:
        guard = cls(cap_usd=parse_cap(cap), ledger_path=ledger_path)
        if ledger_path is not None and ledger_path.is_file():
            guard._replay(ledger_path)
        return guard

    def _replay(self, path: Path) -> None:
        open_calls: dict[str, int] = {}
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                self.unknown_calls.append(f"{path.name}:{lineno} (ligne illisible)")
                continue
            if not isinstance(row, dict):
                self.unknown_calls.append(f"{path.name}:{lineno} (ligne non objet)")
                continue
            call_id = str(row.get("call_id"))
            if row.get("event") == "begin":
                open_calls[call_id] = lineno
            elif row.get("event") == "end":
                open_calls.pop(call_id, None)
                self.calls += 1
                cost = row.get("cost_usd")
                if (
                    row.get("cost_known") is True
                    and isinstance(cost, (int, float))
                    and not isinstance(cost, bool)
                    and math.isfinite(cost)
                ):
                    self.spent_usd += float(cost)
                else:
                    self.unknown_calls.append(call_id)
            else:
                self.unknown_calls.append(f"{path.name}:{lineno} (événement inconnu)")
        for call_id in open_calls:
            self.unknown_calls.append(f"{call_id} (commencé, jamais terminé)")

    def check_before_call(self) -> None:
        if self.unknown_calls:
            raise BudgetError(
                f"coût inconnu pour {len(self.unknown_calls)} appel(s) ({', '.join(self.unknown_calls[:3])}) : "
                "un coût inconnu ne compte pas pour 0, aucun appel de plus n'est lancé"
            )
        if self.spent_usd >= self.cap_usd:
            raise BudgetError(
                f"plafond atteint : {self.spent_usd:.4f} $ dépensés pour un plafond de {self.cap_usd:.2f} $"
            )

    def _append(self, row: Mapping[str, Any]) -> None:
        if self.ledger_path is None:
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
            f.flush()
            with contextlib.suppress(OSError):
                os.fsync(f.fileno())

    def begin(self, call_id: str, **meta: Any) -> None:
        self.check_before_call()
        self._append({"event": "begin", "call_id": call_id, "at": _now(), **meta})

    def record(self, call_id: str, cost_usd: float, cost_known: bool) -> None:
        self.calls += 1
        known = bool(cost_known) and math.isfinite(cost_usd)
        if known:
            self.spent_usd += cost_usd
        else:
            self.unknown_calls.append(call_id)
        self._append(
            {
                "event": "end",
                "call_id": call_id,
                "at": _now(),
                "cost_usd": cost_usd if known else None,
                "cost_known": known,
            }
        )

    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    def snapshot(self) -> dict[str, Any]:
        return {
            "cap_usd": self.cap_usd,
            "spent_usd": round(self.spent_usd, 6),
            "calls": self.calls,
            "unknown_calls": list(self.unknown_calls),
            "blocked": bool(self.unknown_calls) or self.spent_usd >= self.cap_usd,
        }


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


# ── Épinglage et installation (aucun modèle) ─────────────────────────────────


def _git(clone: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return ta._run(["git", "-C", str(clone), *args], timeout=60)


def verify_reference_clone(clone: Path, *, scope: str) -> dict[str, Any]:
    """Vérifie, sans rien écrire, que *clone* porte le tag épinglé et le fichier du scope."""
    if scope not in SCOPE_STAGES:
        raise PinError(f"scope inconnu : {scope} (attendus : {', '.join(SCOPE_STAGES)})")
    resolved = _git(clone, "rev-parse", "--verify", f"{AIDLC_TAG}^{{commit}}")
    commit = resolved.stdout.strip()
    if resolved.returncode != 0 or commit != AIDLC_TAG_COMMIT:
        raise PinError(
            f"{clone} : {AIDLC_TAG} résout en {commit or '(rien)'}, attendu {AIDLC_TAG_COMMIT} — clone de référence non conforme"
        )
    scope_file = _git(clone, "show", f"{AIDLC_TAG}:core/scopes/aidlc-{scope}.md")
    if scope_file.returncode != 0:
        raise PinError(f"{clone} : core/scopes/aidlc-{scope}.md absent du tag {AIDLC_TAG}")
    return {"commit": commit, "scope_frontmatter": parse_frontmatter(scope_file.stdout)}


def parse_frontmatter(text: str) -> dict[str, str]:
    """Les clés simples ``clé: valeur`` du front-matter YAML d'un fichier de scope (listes ignorées)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
        if match and match.group(2).strip():
            out[match.group(1)] = match.group(2).strip().strip('"')
    return out


def stage_installer(clone: Path, dest_dir: Path) -> Path:
    """Copie l'installeur du tag (``git show``, le clone n'est jamais modifié) sous *dest_dir*."""
    name = "install.ps1" if os.name == "nt" else "install.sh"
    shown = _git(clone, "show", f"{AIDLC_TAG}:scripts/{name}")
    if shown.returncode != 0:
        raise InstallError(f"{AIDLC_TAG}:scripts/{name} illisible dans {clone}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{AIDLC_VERSION}-{name}"
    target.write_text(shown.stdout, encoding="utf-8", newline="\n" if name.endswith(".sh") else None)
    return target


def aidlc_bin_dir(home: Path) -> Path:
    return home / ".local" / "bin"


def aidlc_install_env(home: Path, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """L'environnement d'installation et d'exécution d'AI-DLC : tout sous le ``HOME`` isolé."""
    env = dict(base_env) if base_env is not None else dict(os.environ)
    env.pop("AIDLC_UNATTENDED", None)
    env["HOME"] = str(home)
    if os.name == "nt":
        env["USERPROFILE"] = str(home)
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env["AIDLC_BIN_DIR"] = str(aidlc_bin_dir(home))
    env["AIDLC_INSTALL_ROOT"] = str(home / ".local" / "share" / "aidlc")
    env["PATH"] = os.pathsep.join([str(aidlc_bin_dir(home)), env.get("PATH", os.defpath)])
    return env


def install_command(installer: Path) -> list[str]:
    if installer.suffix == ".ps1":
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer),
            "-Version",
            AIDLC_VERSION,
            "-Yes",
            "-Quiet",
            "-NoModifyPath",
        ]
    return ["sh", str(installer), "--version", AIDLC_VERSION, "--yes", "--quiet", "--no-color"]


def aidlc_executable(home: Path) -> str:
    for name in ("aidlc.exe", "aidlc.cmd", "aidlc") if os.name == "nt" else ("aidlc",):
        candidate = aidlc_bin_dir(home) / name
        if candidate.is_file():
            return str(candidate)
    return str(aidlc_bin_dir(home) / "aidlc")


def config_command(aidlc: str) -> list[str]:
    return [aidlc, "config", "--harness", AIDLC_HARNESS, "--mcp", "none", "--yes", "--quiet", "--no-color"]


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def install_aidlc(clone: Path, workspace: Path, home: Path, *, run: Runner = ta._run) -> dict[str, Any]:
    """Installe AI-DLC 2.11.0 sous *home* (réseau GitHub, aucun modèle), puis lit ``aidlc version``."""
    installer = stage_installer(clone, workspace / "aidlc")
    env = aidlc_install_env(home)
    installed = run(install_command(installer), env=env, timeout=900)
    if installed.returncode != 0:
        raise InstallError(
            f"installation d'AI-DLC {AIDLC_VERSION} en échec (code {installed.returncode}) : {(installed.stdout + installed.stderr)[-800:]}"
        )
    aidlc = aidlc_executable(home)
    version = run([aidlc, "version"], env=env, timeout=60)
    if version.returncode != 0 or AIDLC_VERSION not in version.stdout:
        raise InstallError(
            f"`aidlc version` ne rapporte pas {AIDLC_VERSION} : {(version.stdout + version.stderr)[-400:]}"
        )
    return {"installer": installer.name, "aidlc": aidlc, "version_output": version.stdout.strip()[-200:]}


def configure_task_repo(task_dir: Path, home: Path, *, run: Runner = ta._run) -> dict[str, Any]:
    """``aidlc config --harness claude`` dans le dépôt de tâche ; exige des hooks dans ``.claude/settings.json``."""
    env = aidlc_install_env(home)
    done = run(config_command(aidlc_executable(home)), cwd=task_dir, env=env, timeout=600)
    if done.returncode != 0:
        raise InstallError(
            f"`aidlc config` en échec dans {task_dir} (code {done.returncode}) : {(done.stdout + done.stderr)[-800:]}"
        )
    settings = task_dir / ".claude" / "settings.json"
    try:
        hooks = json.loads(settings.read_text(encoding="utf-8")).get("hooks")
    except (OSError, json.JSONDecodeError, AttributeError):
        hooks = None
    if not hooks:
        raise InstallError(f"{settings} sans hooks après `aidlc config` : AI-DLC ne tournerait pas, aucun run lancé")
    return {"settings_hooks": sorted(hooks), "config_output": done.stdout.strip()[-300:]}


# ── Pilote d'un run (le seul endroit qui appelle le modèle) ──────────────────


def aidlc_prompt(scope: str, task_prompt: str) -> str:
    """Le premier message : la commande du scope, puis la même consigne que les autres bras."""
    return f"/aidlc {scope} {task_prompt}"


class Invoker(Protocol):
    def __call__(
        self,
        task_dir: Path,
        prompt: str,
        *,
        env: dict[str, str],
        timeout_s: int,
        model: str | None,
        resume: str | None,
        log_path: Path,
        max_budget_usd: float | None = None,
    ) -> ta.RunOutcome: ...


def claude_invoker(
    task_dir: Path,
    prompt: str,
    *,
    env: dict[str, str],
    timeout_s: int,
    model: str | None,
    resume: str | None,
    log_path: Path,
    max_budget_usd: float | None = None,
) -> ta.RunOutcome:
    """Un appel ``claude -p`` réel, jamais ``--bare`` (qui couperait les hooks et la skill d'AI-DLC)."""
    return ta.run_claude_headless(
        task_dir,
        prompt,
        home=Path(env["HOME"]),
        env=env,
        timeout_s=timeout_s,
        repeat_threshold=LOOP_REPEAT_THRESHOLD,
        log_path=log_path,
        model=model,
        extra_args=_extra_args(resume=resume, max_budget_usd=max_budget_usd),
        append_log=True,
        bare=False,
        arm="aidlc",
    )


def _extra_args(*, resume: str | None, max_budget_usd: float | None) -> list[str]:
    """``--max-budget-usd`` (le reste du plafond, appliqué par Claude Code pendant l'appel), puis ``--resume``."""
    args = ["--max-budget-usd", f"{max_budget_usd:.4f}"] if max_budget_usd is not None else []
    return [*args, "--resume", resume] if resume else args


def planned_claude_command(
    prompt: str, *, model: str | None, resume: str | None, max_budget_usd: float | None = None
) -> list[str]:
    """La commande que :func:`claude_invoker` lancerait (affichée par ``--dry-run``, jamais exécutée)."""
    cmd = [
        "claude",
        "-p",
        prompt,
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "stream-json",
        "--verbose",
        "--setting-sources",
        "project,local",
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.extend(_extra_args(resume=resume, max_budget_usd=max_budget_usd))
    return cmd


_WORKFLOW_COMPLETED = re.compile(r"^\*\*Event\*\*\s*:\s*WORKFLOW_COMPLETED\s*$", re.MULTILINE)


def workflow_completed(task_dir: Path) -> bool:
    """``WORKFLOW_COMPLETED`` présent dans un fichier ``audit/*.md`` du dépôt (``audit-format.md``)."""
    for here, dirs, _names in os.walk(task_dir):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "target"}]
        if Path(here).name != "audit":
            continue
        for path in Path(here).glob("*.md"):
            with contextlib.suppress(OSError):
                if _WORKFLOW_COMPLETED.search(path.read_text(encoding="utf-8", errors="replace")):
                    return True
    return False


@dataclass
class Invocation:
    index: int
    kind: str  # "task" | "reply"
    cost_usd: float  # coût de CET appel (delta du total de session sous --resume)
    cost_known: bool
    num_turns: int
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    wall_seconds: float
    terminated_reason: str
    session_id: str | None
    model_usage: dict[str, Any] = field(default_factory=dict)  # cumulé sur la session, tel que rendu
    session_total_usd: float | None = None  # ``total_cost_usd`` brut rendu par l'appel (cumulé sur la session)


@dataclass
class DriveResult:
    driver: str
    invocations: list[Invocation] = field(default_factory=list)
    person_replies: int = 0
    workflow_completed: bool = False
    stopped_by_budget: str | None = None
    terminated_reason: str = "completed"

    @property
    def total_cost_usd(self) -> float:
        return sum(i.cost_usd for i in self.invocations if i.cost_known)

    @property
    def cost_known(self) -> bool:
        return bool(self.invocations) and all(i.cost_known for i in self.invocations)

    def total(self, name: str) -> int:
        return sum(int(getattr(i, name)) for i in self.invocations)

    @property
    def wall_seconds(self) -> float:
        return sum(i.wall_seconds for i in self.invocations)

    def model_usage(self) -> dict[str, Any]:
        """``modelUsage`` cumule la session : la dernière valeur de chaque session, sommée entre sessions."""
        last_per_session: dict[str, Invocation] = {}
        unsessioned: list[Invocation] = []
        for inv in self.invocations:
            if inv.session_id is None:
                unsessioned.append(inv)
            else:
                last_per_session[inv.session_id] = inv
        merged: dict[str, dict[str, Any]] = {}
        for inv in [*last_per_session.values(), *unsessioned]:
            for model, usage in inv.model_usage.items():
                slot = merged.setdefault(model, {})
                for key, value in (usage or {}).items():
                    if (
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and key not in {"contextWindow", "maxOutputTokens"}
                    ):
                        slot[key] = slot.get(key, 0) + value
                    else:
                        slot.setdefault(key, value)
        return merged


def session_call_cost(session_total: float, previous_total: float | None) -> float:
    """Coût d'un appel : son total moins le total précédent de la session reprise.

    Sans total précédent (premier appel), ou si le total baisse (copie de
    session, compteur remis à zéro), l'appel compte en entier : jamais un
    delta négatif, au pire un surcompte.
    """
    if previous_total is None or session_total < previous_total:
        return session_total
    return session_total - previous_total


def drive_run(
    task_dir: Path,
    task_prompt: str,
    *,
    scope: str,
    driver: str,
    env: Mapping[str, str],
    model: str | None,
    guard: BudgetGuard,
    log_path: Path,
    call_prefix: str,
    invoke: Invoker = claude_invoker,
    run_timeout_s: int = DEFAULT_RUN_TIMEOUT_S,
    max_person_replies: int = DEFAULT_MAX_PERSON_REPLIES,
) -> DriveResult:
    """Joue un run complet : premier message, puis, en ``scripted-person``, une réponse fixe par fin de tour.

    Le garde-fou est consulté avant chaque appel : un refus arrête le run
    (``terminated_reason = "budget"``) sans lancer l'appel.
    """
    if driver not in DRIVERS:
        raise ValueError(f"pilote inconnu : {driver} (attendus : {', '.join(DRIVERS)})")
    run_env = dict(env)
    if driver == DRIVER_UNATTENDED:
        run_env["AIDLC_UNATTENDED"] = "1"
    else:
        run_env.pop("AIDLC_UNATTENDED", None)
    result = DriveResult(driver=driver)
    prompt, kind, session = aidlc_prompt(scope, task_prompt), "task", None
    session_totals: dict[str, float] = {}
    remaining = float(run_timeout_s)
    while True:
        call_id = f"{call_prefix}#{len(result.invocations)}"
        try:
            guard.begin(call_id, kind=kind)
        except BudgetError as exc:
            result.stopped_by_budget = str(exc)
            result.terminated_reason = "budget"
            break
        outcome = invoke(
            task_dir,
            prompt,
            env=run_env,
            timeout_s=max(1, int(remaining)),
            model=model,
            resume=session,
            log_path=log_path,
            max_budget_usd=guard.remaining_usd(),
        )
        known = bool(outcome.cost_known)
        raw = outcome.raw_result or {}
        session_id = raw.get("session_id") if isinstance(raw.get("session_id"), str) else None
        call_cost = session_call_cost(outcome.total_cost_usd, session_totals.get(session) if session else None)
        if known and session_id is not None:
            session_totals[session_id] = outcome.total_cost_usd
        guard.record(call_id, call_cost, known)
        result.invocations.append(
            Invocation(
                index=len(result.invocations),
                kind=kind,
                cost_usd=call_cost,
                cost_known=known,
                num_turns=outcome.num_turns,
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                cache_read_input_tokens=outcome.cache_read_input_tokens,
                cache_creation_input_tokens=outcome.cache_creation_input_tokens,
                wall_seconds=outcome.wall_seconds,
                terminated_reason=outcome.terminated_reason,
                session_id=session_id,
                model_usage=dict(outcome.model_usage or {}),
                session_total_usd=outcome.total_cost_usd if known else None,
            )
        )
        remaining -= outcome.wall_seconds
        if outcome.terminated_reason != "completed":
            result.terminated_reason = outcome.terminated_reason
            break
        if workflow_completed(task_dir):
            result.workflow_completed = True
            break
        if driver == DRIVER_UNATTENDED:
            break  # personne pour répondre : le mode documenté s'arrête au premier tour rendu
        if session_id is None:
            result.terminated_reason = "error"  # rien à reprendre
            break
        if result.person_replies >= max_person_replies:
            result.terminated_reason = "reply_cap"
            break
        if remaining <= 0:
            result.terminated_reason = "timeout"
            break
        prompt, kind, session = SCRIPTED_REPLY, "reply", session_id
        result.person_replies += 1
    return result


# ── Estimation de coût (aucun appel) ─────────────────────────────────────────

#: Tarif implicite, USD par million de jetons, ajusté par moindres carrés sur le
#: ``modelUsage`` ``claude-opus-5-5`` des 120 runs du lot L (résidu < 1e-14 :
#: c'est le tarif exact appliqué par ``claude -p``). Données brutes hors dépôt,
#: ``_scratch/bench-l/workspace/state/results.jsonl`` de la Forge.
LOT_L_PRICES_PER_MTOK: dict[str, float] = {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 8.0}

#: Coût moyen par run mesuré au lot L (``docs/bench/rejeu-lot-l-2026-10-02.md``,
#: l.17 : 60 runs nu 10,15 $, 60 runs kit-gov 12,69 $), même modèle (Opus 5.5).
MEASURED_MEAN_COST_PER_RUN: dict[str, float] = {"nu": 10.15 / 60, "kit-gov": 12.69 / 60}


@dataclass(frozen=True)
class RunScenario:
    """Hypothèse de forme d'un run : appels, contexte, jetons écrits en cache, sortie."""

    label: str
    main_calls: int
    start_context_tokens: int
    final_context_tokens: int
    main_cache_write_tokens: int
    main_output_tokens: int
    sub_calls: int = 0
    sub_avg_context_tokens: int = 0
    sub_cache_write_tokens: int = 0
    sub_output_tokens: int = 0

    def cost_usd(self, prices: Mapping[str, float] = LOT_L_PRICES_PER_MTOK) -> float:
        avg_context = (self.start_context_tokens + self.final_context_tokens) / 2
        cache_read = self.main_calls * avg_context + self.sub_calls * self.sub_avg_context_tokens
        cache_write = self.main_cache_write_tokens + self.sub_cache_write_tokens
        output = self.main_output_tokens + self.sub_output_tokens
        return (
            cache_read * prices["cache_read"] + cache_write * prices["cache_write"] + output * prices["output"]
        ) / 1_000_000


#: Hypothèses du chiffrage de ``docs/bench-three-arms.md`` §15.3 (rien n'est mesuré sur AI-DLC).
SCENARIOS: dict[str, RunScenario] = {
    "scripted-bas": RunScenario("scripted-bas", 35, 55_000, 100_000, 90_000, 18_000, 10, 45_000, 40_000, 5_000),
    "scripted-haut": RunScenario("scripted-haut", 90, 60_000, 200_000, 300_000, 75_000, 30, 70_000, 80_000, 20_000),
    "unattended-bas": RunScenario("unattended-bas", 15, 55_000, 85_000, 60_000, 6_000),
    "unattended-haut": RunScenario("unattended-haut", 25, 55_000, 110_000, 90_000, 10_000),
}


def campaign_estimate(n_tasks: int, *, k: int = ta.K_REPLAY, driver: str = DRIVER_SCRIPTED) -> dict[str, Any]:
    """Coût d'un protocole complet : *n_tasks* tâches × 3 bras (nu, kit-gov, aidlc) × *k* rejeux."""
    prefix = "scripted" if driver == DRIVER_SCRIPTED else "unattended"
    low, high = SCENARIOS[f"{prefix}-bas"].cost_usd(), SCENARIOS[f"{prefix}-haut"].cost_usd()
    runs = n_tasks * k
    measured = {arm: runs * cost for arm, cost in MEASURED_MEAN_COST_PER_RUN.items()}
    return {
        "n_tasks": n_tasks,
        "k": k,
        "runs_per_arm": runs,
        "aidlc_per_run_usd": [round(low, 4), round(high, 4)],
        "aidlc_usd": [round(runs * low, 2), round(runs * high, 2)],
        "nu_usd": round(measured["nu"], 2),
        "kit_gov_usd": round(measured["kit-gov"], 2),
        "total_usd": [round(runs * low + sum(measured.values()), 2), round(runs * high + sum(measured.values()), 2)],
    }


def estimate_report(n_values: Sequence[int] = (20, 99, 393), *, k: int = ta.K_REPLAY) -> dict[str, Any]:
    return {
        "prices_per_mtok": LOT_L_PRICES_PER_MTOK,
        "measured_mean_cost_per_run": {arm: round(v, 4) for arm, v in MEASURED_MEAN_COST_PER_RUN.items()},
        "scenarios": {name: {**asdict(s), "cost_usd": round(s.cost_usd(), 4)} for name, s in SCENARIOS.items()},
        "campaigns": {driver: [campaign_estimate(n, k=k, driver=driver) for n in n_values] for driver in DRIVERS},
    }


def cap_coverage(cap_usd: float, *, driver: str) -> dict[str, Any]:
    """Combien de runs ``aidlc`` le plafond couvre, aux deux bornes de l'estimation."""
    prefix = "scripted" if driver == DRIVER_SCRIPTED else "unattended"
    low, high = SCENARIOS[f"{prefix}-bas"].cost_usd(), SCENARIOS[f"{prefix}-haut"].cost_usd()
    return {"cap_usd": cap_usd, "runs_at_low": math.floor(cap_usd / low), "runs_at_high": math.floor(cap_usd / high)}


# ── Pré-enregistrement ───────────────────────────────────────────────────────


def preregistration(
    *,
    scope: str,
    driver: str,
    model: str | None,
    k: int,
    seed: int,
    task_ids: Sequence[str],
    pin: Mapping[str, Any],
    run_timeout_s: int,
    max_person_replies: int,
) -> dict[str, Any]:
    config = {
        "arm": ARM,
        "aidlc_version": AIDLC_VERSION,
        "aidlc_tag": AIDLC_TAG,
        "aidlc_tag_commit": AIDLC_TAG_COMMIT,
        "harness": AIDLC_HARNESS,
        "scope": scope,
        "scope_stages": list(SCOPE_STAGES[scope]),
        "scope_frontmatter": dict(pin.get("scope_frontmatter") or {}),
        "driver": driver,
        "special_configuration": driver == DRIVER_SCRIPTED,
        "scripted_reply": SCRIPTED_REPLY if driver == DRIVER_SCRIPTED else None,
        "max_person_replies": max_person_replies if driver == DRIVER_SCRIPTED else 0,
        "declared_interventions_per_run": 1 if driver == DRIVER_UNATTENDED else 0,
        "env": {"AIDLC_UNATTENDED": "1"} if driver == DRIVER_UNATTENDED else {},
        "install": "scripts/install.sh --version 2.11.0 sous HOME isolé (AIDLC_BIN_DIR, AIDLC_INSTALL_ROOT, XDG_DATA_HOME)",
        "config": " ".join(config_command("aidlc")),
        "model_policy": "aucune (tous les agents héritent du modèle de session)",
        "model_id": model,
        "prompt_template": aidlc_prompt(scope, ta.BUILD_PROMPT_TEMPLATE),
        "run_timeout_s": run_timeout_s,
        "loop_repeat_threshold": LOOP_REPEAT_THRESHOLD,
        "never_bare": True,
        "per_call_cap": "--max-budget-usd = reste du plafond",
        "resumed_call_cost": "delta du total de session",
        "k": k,
        "seed": seed,
        "tasks": list(task_ids),
    }
    digest = hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return {
        "schema": "grimoire.bench.aidlc-prereg.v1",
        "registered_at": _now(),
        "config_digest": digest,
        "config": config,
    }


def ensure_preregistered(state_dir: Path, expected: Mapping[str, Any]) -> None:
    """``--run`` exige le pré-enregistrement d'un ``--dry-run`` antérieur, à la configuration identique."""
    path = state_dir / PREREGISTRATION
    if not path.is_file():
        raise PreregistrationError(
            f"{path} absent : lancer d'abord --dry-run avec la même configuration (pré-enregistrement)"
        )
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PreregistrationError(f"{path} illisible : {exc}") from None
    if recorded.get("config_digest") != expected["config_digest"]:
        diff = sorted(k for k in expected["config"] if recorded.get("config", {}).get(k) != expected["config"][k])
        raise PreregistrationError(
            f"configuration différente du pré-enregistrement ({', '.join(diff) or 'empreinte'}) : aucun run lancé"
        )


# ── Campagne ─────────────────────────────────────────────────────────────────


def _task_list(
    workspace: Path, *, bench_root: Path | None, seed: int, subset: Path | None, limit: int | None
) -> list[ta.TaskMeta]:
    root = bench_root or ta.ensure_polyglot_benchmark(workspace)
    tasks = filter_tasks(ta.sample_tasks(ta.discover_catalog(root), seed=seed), subset)
    return tasks[:limit] if limit else tasks


def run_record(
    task: ta.TaskMeta,
    run_index: int,
    drive: DriveResult,
    *,
    success: bool,
    transcript: tuple[str, Any],
    model: str | None,
    claude_code_version: str | None,
    auth_mode: str,
) -> ta.RunRecord:
    return ta.RunRecord(
        task_id=task.task_id,
        language=task.language,
        arm=ARM,
        run_index=run_index,
        success=success,
        total_cost_usd=drive.total_cost_usd,
        cost_known=drive.cost_known,
        input_tokens=drive.total("input_tokens"),
        output_tokens=drive.total("output_tokens"),
        cache_read_input_tokens=drive.total("cache_read_input_tokens"),
        cache_creation_input_tokens=drive.total("cache_creation_input_tokens"),
        num_turns=drive.total("num_turns"),
        wall_seconds=drive.wall_seconds,
        terminated_reason=drive.terminated_reason,
        model_usage=drive.model_usage() or None,
        recorded_at=_now(),
        auth_mode=auth_mode,
        transcript_check=transcript[0],
        hidden_test_reads=transcript[1],
        model_id=model,
        claude_code_version=claude_code_version,
    )


def _do_dry_run(
    args: argparse.Namespace, workspace: Path, tasks: Sequence[ta.TaskMeta], prereg: dict[str, Any], *, run: Runner
) -> int:
    state_dir = workspace / "state"
    home = workspace / "homes" / ARM
    ta.ensure_isolated_home(home)
    install = None if args.no_install else install_aidlc(args.aidlc_clone, workspace, home, run=run)
    planned: list[dict[str, Any]] = []
    for task in tasks:
        task_dir = workspace / "tasks" / task.task_id.replace("/", "__") / ARM / "prep"
        reset_run_dir(task_dir)
        prepare_task_repo(task, task_dir)
        configured = None if args.no_install else configure_task_repo(task_dir, home, run=run)
        try:
            assert_task_repo_clean(task, task_dir)
        except ContaminatedTaskRepoError as exc:
            print(f"[contaminated] {task.task_id} / {ARM} : {exc} — arrêt, 0 $ dépensé.", file=sys.stderr)
            return 1
        first = planned_claude_command(aidlc_prompt(args.scope, ta.build_prompt(task)), model=args.model, resume=None)
        reply = (
            planned_claude_command(SCRIPTED_REPLY, model=args.model, resume="<session_id>")
            if args.driver == DRIVER_SCRIPTED
            else None
        )
        planned.append({"task_id": task.task_id, "first_call": first, "reply_call": reply, "configured": configured})
        print(f"[dry-run] préparé {task.task_id} / {ARM} -> {task_dir}")
    (state_dir / PREREGISTRATION).write_text(json.dumps(prereg, indent=2, ensure_ascii=False), encoding="utf-8")
    (state_dir / PLANNED_COMMANDS).write_text(
        json.dumps({"install": install, "runs": planned}, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    estimate = estimate_report()
    print(
        json.dumps(
            {
                "estimate": estimate["campaigns"][args.driver],
                "aidlc_per_run_usd": estimate["campaigns"][args.driver][0]["aidlc_per_run_usd"],
            },
            indent=2,
        )
    )
    if args.max_total_usd is not None:
        print(json.dumps({"cap_coverage": cap_coverage(parse_cap(args.max_total_usd), driver=args.driver)}, indent=2))
    print(
        f"[dry-run] {len(tasks)} tâche(s) préparée(s), pré-enregistrement {prereg['config_digest'][:12]} écrit, aucun modèle lancé."
    )
    return 0


def _do_run(
    args: argparse.Namespace,
    workspace: Path,
    tasks: Sequence[ta.TaskMeta],
    guard: BudgetGuard,
    *,
    invoke: Invoker,
    auth_mode: str,
    run: Runner,
) -> int:
    state_dir = workspace / "state"
    results_path = state_dir / "results.jsonl"
    home = workspace / "homes" / ARM
    ta.ensure_isolated_home(home)
    install_aidlc(args.aidlc_clone, workspace, home, run=run)
    claude_code_version = detect_claude_code_version()
    previous = read_results_rows(results_path) if results_path.is_file() else []
    done = {(r["task_id"], r["arm"], r["run_index"]) for r in previous}
    go_bin = ta.ensure_go_toolchain(workspace) if any(t.language == "go" for t in tasks) else None
    npm_cache_dir = workspace / "npm-cache"
    with (
        open(results_path, "a", encoding="utf-8") as results_f,
        open(state_dir / RUNS_META, "a", encoding="utf-8") as meta_f,
    ):
        for task in tasks:
            for run_index in range(args.k):
                if (task.task_id, ARM, run_index) in done:
                    continue
                try:
                    guard.check_before_call()
                except BudgetError as exc:
                    print(
                        f"[budget] arrêt de la campagne avant {task.task_id} / run{run_index} : {exc}", file=sys.stderr
                    )
                    return 3
                run_dir = run_dir_for(workspace, task, ARM, run_index)
                reset_run_dir(run_dir)
                prepare_task_repo(task, run_dir)
                hidden_dir = ta.hidden_tests_dir(
                    task, workspace / "tasks" / task.task_id.replace("/", "__") / "hidden-tests"
                )
                configure_task_repo(run_dir, home, run=run)
                assert_task_repo_clean(task, run_dir)
                ta.install_test_dependencies(run_dir, npm_cache_dir=npm_cache_dir)
                env = ta.run_environment(
                    run_dir,
                    ARM,
                    home=home,
                    grimoire_bin="grimoire",
                    workspace=workspace,
                    go_bin=go_bin,
                    npm_cache_dir=npm_cache_dir,
                )
                env = aidlc_install_env(home, env)
                log_path = run_dir.parent / f"{run_dir.name}.stream.jsonl"
                log_path.unlink(missing_ok=True)
                provision = contextlib.nullcontext() if auth_mode == "api-key" else ta.credentials_provisioned(home)
                with provision:
                    drive = drive_run(
                        run_dir,
                        ta.build_prompt(task),
                        scope=args.scope,
                        driver=args.driver,
                        env=env,
                        model=args.model,
                        guard=guard,
                        log_path=log_path,
                        call_prefix=f"{task.task_id}/run{run_index}",
                        invoke=invoke,
                        run_timeout_s=args.run_timeout_s,
                        max_person_replies=args.max_person_replies,
                    )
                transcript = check_transcript(
                    log_path,
                    test_files=task.test_files,
                    run_dir=run_dir,
                    hidden_dir=hidden_dir,
                    canonical_dirs=(task.exercise_dir,),
                )
                success = False
                if drive.invocations and drive.terminated_reason not in ("timeout", "loop", "budget"):
                    success, _ = ta.run_hidden_tests(task, run_dir, hidden_dir, go_bin=go_bin, env=env)
                ta.cleanup_build_artifacts(run_dir)
                if not drive.invocations:
                    print(
                        f"[budget] {task.task_id} / run{run_index} : aucun appel lancé ({drive.stopped_by_budget})",
                        file=sys.stderr,
                    )
                    return 3
                record = run_record(
                    task,
                    run_index,
                    drive,
                    success=success,
                    transcript=transcript,
                    model=args.model,
                    claude_code_version=claude_code_version,
                    auth_mode=auth_mode,
                )
                results_f.write(json.dumps(record.to_dict()) + "\n")
                results_f.flush()
                meta = {
                    "task_id": task.task_id,
                    "run_index": run_index,
                    "driver": drive.driver,
                    "scope": args.scope,
                    "aidlc_version": AIDLC_VERSION,
                    "person_replies": drive.person_replies,
                    "declared_interventions": 1 if drive.driver == DRIVER_UNATTENDED else 0,
                    "workflow_completed": drive.workflow_completed,
                    "stopped_by_budget": drive.stopped_by_budget,
                    "invocations": [asdict(i) for i in drive.invocations],
                    "budget": guard.snapshot(),
                }
                meta_f.write(json.dumps(meta, ensure_ascii=False) + "\n")
                meta_f.flush()
                print(
                    f"[run] {task.task_id} / run{run_index} : succès={success}, {drive.total_cost_usd:.4f} $, cumul {guard.spent_usd:.4f} $ / {guard.cap_usd:.2f} $"
                )
                if drive.stopped_by_budget:
                    print(f"[budget] arrêt de la campagne : {drive.stopped_by_budget}", file=sys.stderr)
                    return 3
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="prépare, installe, pré-enregistre ; aucun modèle lancé")
    mode.add_argument("--run", action="store_true", help="runs réels (payants), sous plafond --max-total-usd")
    mode.add_argument(
        "--estimate", action="store_true", help="affiche l'estimation de coût (aucun réseau, aucun modèle)"
    )
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument(
        "--aidlc-clone", type=Path, default=None, help="clone de référence d'aidlc-workflows (lu, jamais écrit)"
    )
    parser.add_argument(
        "--bench-root",
        type=Path,
        default=None,
        help="clone polyglot-benchmark existant (défaut : cloné sous le workspace)",
    )
    parser.add_argument("--scope", choices=sorted(SCOPE_STAGES), default=DEFAULT_SCOPE)
    parser.add_argument(
        "--driver", choices=DRIVERS, default=None, help="obligatoire pour --dry-run et --run (pré-enregistrement)"
    )
    parser.add_argument("--model", type=str, default=None, help="B-01 : obligatoire pour --run")
    parser.add_argument(
        "--max-total-usd", type=str, default=None, help="plafond en dollars de la campagne, obligatoire pour --run"
    )
    parser.add_argument("--k", type=int, default=ta.K_REPLAY)
    parser.add_argument("--seed", type=int, default=ta.DEFAULT_SEED)
    parser.add_argument("--subset", type=Path, default=None)
    parser.add_argument("--tasks", type=int, default=None, help="ne garder que les N premières tâches du tirage")
    parser.add_argument("--run-timeout-s", type=int, default=DEFAULT_RUN_TIMEOUT_S)
    parser.add_argument("--max-person-replies", type=int, default=DEFAULT_MAX_PERSON_REPLIES)
    parser.add_argument("--auth", choices=ta.AUTH_MODES, default=None)
    parser.add_argument(
        "--budget-ledger",
        type=Path,
        default=None,
        help="journal de budget partagé entre workspaces (un seul plafond pour plusieurs pilotes) ; défaut : state/",
    )
    parser.add_argument(
        "--no-install", action="store_true", help="--dry-run seulement : ne pas installer ni configurer AI-DLC"
    )
    parser.add_argument("--n", type=str, default="20,99,393", help="--estimate : tailles de suite (tâches par bras)")
    return parser


def main(argv: Sequence[str] | None = None, *, invoke: Invoker = claude_invoker, run: Runner = ta._run) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.estimate:
        print(
            json.dumps(
                estimate_report([int(n) for n in args.n.split(",") if n.strip()], k=args.k),
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    if args.run:
        # Refus avant tout travail (réseau, disque, installation) : plafond, modèle, pilote.
        try:
            parse_cap(args.max_total_usd)
        except BudgetError as exc:
            parser.error(str(exc))
        if not (args.model or "").strip():
            parser.error("--model est obligatoire pour --run : un run payant ne part pas sur le modèle par défaut")
    if args.driver is None:
        parser.error(
            "--driver est obligatoire (unattended ou scripted-person) : le pilote fait partie du pré-enregistrement"
        )
    if args.no_install and args.run:
        parser.error("--no-install ne vaut que pour --dry-run")
    if args.aidlc_clone is None:
        parser.error("--aidlc-clone est obligatoire : chemin du clone de référence d'aidlc-workflows")
    args.model = args.model.strip() if args.model else None
    workspace = (args.workspace or ta._default_workspace()).resolve()
    state_dir = workspace / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        pin = verify_reference_clone(args.aidlc_clone, scope=args.scope)
    except PinError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    tasks = _task_list(workspace, bench_root=args.bench_root, seed=args.seed, subset=args.subset, limit=args.tasks)
    prereg = preregistration(
        scope=args.scope,
        driver=args.driver,
        model=args.model,
        k=args.k,
        seed=args.seed,
        task_ids=[t.task_id for t in tasks],
        pin=pin,
        run_timeout_s=args.run_timeout_s,
        max_person_replies=args.max_person_replies,
    )
    if args.dry_run:
        try:
            return _do_dry_run(args, workspace, tasks, prereg, run=run)
        except InstallError as exc:
            print(f"[install] {exc} — 0 $ dépensé.", file=sys.stderr)
            return 1
    try:
        ensure_preregistered(state_dir, prereg)
        ensure_results_match_model(state_dir / "results.jsonl", args.model)
        guard = BudgetGuard.open(args.max_total_usd, args.budget_ledger or state_dir / BUDGET_LEDGER)
        guard.check_before_call()
    except (PreregistrationError, ModelMismatchError, BudgetError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    auth_mode = ta.resolve_auth_mode(args.auth)
    try:
        return _do_run(args, workspace, tasks, guard, invoke=invoke, auth_mode=auth_mode, run=run)
    except (InstallError, ContaminatedTaskRepoError) as exc:
        print(f"{exc} — campagne arrêtée, cumul {guard.spent_usd:.4f} $.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
