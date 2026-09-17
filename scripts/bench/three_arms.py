#!/usr/bin/env python3
"""Banc à trois bras — Claude Code nu / + ecc / + grimoire-kit.

Issue Grimoire-kit#551 (plan produit 2026-Q4, phase 1, lot 1.1). Compare, sur
un sous-ensemble reproductible du benchmark polyglot d'Aider (Exercism), trois
façons de gouverner Claude Code en mode non interactif (``claude -p``) :

- ``nu``  : aucune configuration — dépôt de tâche vierge.
- ``ecc`` : le paquet ecc (github.com/affaan-m/ECC, licence MIT) installé en
  scope projet (plugin Claude Code ``ecc@ecc``).
- ``kit`` : ``grimoire init --backend local --no-cockpit`` + ``host sync``
  (grimoire-kit >= 3.53.0).

Voir ``docs/bench-three-arms.md`` pour le protocole complet, rejouable par un
tiers. Ce module n'importe volontairement rien de ``src/grimoire`` : il doit
pouvoir tourner contre n'importe quelle version installée du kit (celle que
l'opérateur a dans son environnement), exactement comme le bras ``kit`` est
censé être consommé par un vrai projet.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tarfile
import time
import urllib.request
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── Constantes du protocole ──────────────────────────────────────────────────

POLYGLOT_REPO_URL = "https://github.com/Aider-AI/polyglot-benchmark.git"
ECC_REPO_URL = "https://github.com/affaan-m/ECC.git"
GO_TOOLCHAIN_URL = "https://go.dev/dl/go1.23.4.linux-amd64.tar.gz"

LANGUAGES: tuple[str, ...] = ("python", "javascript", "go", "rust")
PER_LANGUAGE = 5
DEFAULT_SEED = 551  # issue Grimoire-kit#551
K_REPLAY = 3
ARMS: tuple[str, ...] = ("nu", "ecc", "kit")

DEFAULT_RUN_TIMEOUT_S = 15 * 60  # garde-fou par run (§ « Arrêt et garde-fous »)
DEFAULT_TEST_TIMEOUT_S = 180
DEFAULT_LOOP_REPEAT_THRESHOLD = 4  # même commande répétée N fois de suite
DEFAULT_POLL_INTERVAL_S = 2.0
MIN_TASKS_BEFORE_STOP_CHECK = 4  # pas de verdict avant un minimum de données

# ── Modèles de données ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class TaskMeta:
    """Un exercice sélectionné, avant préparation du dépôt de tâche."""

    task_id: str  # "<langue>/<slug>"
    language: str
    slug: str
    exercise_dir: Path  # chemin absolu dans le clone polyglot-benchmark
    solution_files: tuple[str, ...]
    test_files: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "language": self.language,
            "slug": self.slug,
            "exercise_dir": str(self.exercise_dir),
            "solution_files": list(self.solution_files),
            "test_files": list(self.test_files),
        }


@dataclass
class RunOutcome:
    """Résultat brut d'un seul appel ``claude -p`` (avant vérification)."""

    success: bool | None = None  # rempli après exécution des tests
    total_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    num_turns: int = 0
    wall_seconds: float = 0.0
    terminated_reason: str = "completed"  # completed|timeout|loop|error
    model_usage: dict[str, Any] = field(default_factory=dict)
    tool_commands_seen: int = 0
    raw_result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class RunRecord:
    """Une ligne de ``results.jsonl`` : une tâche x un bras x un rejeu."""

    task_id: str
    language: str
    arm: str
    run_index: int
    success: bool
    total_cost_usd: float
    input_tokens: int
    output_tokens: int
    num_turns: int
    wall_seconds: float
    terminated_reason: str
    dispatch_stats: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ── Fonctions pures (testées sans LLM) ───────────────────────────────────────


def discover_catalog(bench_root: Path, languages: Sequence[str] = LANGUAGES) -> list[TaskMeta]:
    """Recense les exercices disposant d'un ``.meta/config.json`` exploitable.

    Un exercice est retenu s'il déclare au moins un fichier ``solution`` et un
    fichier ``test`` dans son ``config.json`` — c'est le contrat que suit tout
    le reste du harnais (fichiers à donner à l'agent vs fichiers cachés).
    """
    catalog: list[TaskMeta] = []
    for language in languages:
        practice_dir = bench_root / language / "exercises" / "practice"
        if not practice_dir.is_dir():
            continue
        for exercise_dir in sorted(practice_dir.iterdir()):
            if not exercise_dir.is_dir():
                continue
            config_path = exercise_dir / ".meta" / "config.json"
            if not config_path.is_file():
                continue
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            files = config.get("files", {})
            solution = tuple(files.get("solution", ()))
            test = tuple(files.get("test", ()))
            if not solution or not test:
                continue
            catalog.append(
                TaskMeta(
                    task_id=f"{language}/{exercise_dir.name}",
                    language=language,
                    slug=exercise_dir.name,
                    exercise_dir=exercise_dir,
                    solution_files=solution,
                    test_files=test,
                )
            )
    return catalog


def sample_tasks(
    catalog: Sequence[TaskMeta],
    *,
    languages: Sequence[str] = LANGUAGES,
    per_language: int = PER_LANGUAGE,
    seed: int = DEFAULT_SEED,
) -> list[TaskMeta]:
    """Tirage reproductible : ``per_language`` exercices par langue.

    Le tri par ``task_id`` avant tirage garantit que le résultat ne dépend pas
    de l'ordre de retour du système de fichiers — seule la graine compte.
    """
    by_language: dict[str, list[TaskMeta]] = defaultdict(list)
    for task in catalog:
        by_language[task.language].append(task)

    selected: list[TaskMeta] = []
    for language in languages:
        pool = sorted(by_language.get(language, ()), key=lambda t: t.task_id)
        if len(pool) < per_language:
            raise ValueError(
                f"Catalogue insuffisant pour {language} : {len(pool)} exercices "
                f"trouvés, {per_language} requis."
            )
        rng = random.Random(f"{seed}:{language}")  # noqa: S311 - reproductibilité, pas cryptographie
        selected.extend(rng.sample(pool, per_language))
    return selected


def _read_instructions(exercise_dir: Path) -> str:
    docs_dir = exercise_dir / ".docs"
    parts = []
    main = docs_dir / "instructions.md"
    if main.is_file():
        parts.append(main.read_text(encoding="utf-8"))
    appendix = docs_dir / "instructions.append.md"
    if appendix.is_file():
        parts.append(appendix.read_text(encoding="utf-8"))
    return "\n\n".join(parts).strip() + "\n"


def prepare_task_repo(task: TaskMeta, dest: Path, *, include_tests: bool = False) -> None:
    """Construit un dépôt de tâche jetable pour ``task`` dans ``dest``.

    Copie l'énoncé (``TASK.md``) et les fichiers stub/support de l'exercice ;
    les fichiers de test sont exclus par défaut (« tests cachés à l'agent »),
    tout comme tout ce qui vit sous ``.meta/`` et ``.docs/`` (solution de
    référence, générateurs). ``git init`` pour que l'agent puisse diffs/commits
    s'il le souhaite.
    """
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "TASK.md").write_text(_read_instructions(task.exercise_dir), encoding="utf-8")

    test_set = set(task.test_files)
    for item in sorted(task.exercise_dir.rglob("*")):
        if item.is_dir():
            continue
        rel = item.relative_to(task.exercise_dir)
        rel_parts = rel.parts
        if rel_parts[0] in (".meta", ".docs"):
            continue
        rel_str = rel.as_posix()
        if rel_str in test_set and not include_tests:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)

    subprocess.run(["git", "init", "-q", "."], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run(
        ["git", "-c", "user.email=bench@grimoire-kit.local", "-c", "user.name=grimoire-bench", "commit", "-q", "-m", "task: état initial"],
        cwd=dest,
        check=True,
    )


def hidden_tests_dir(task: TaskMeta, dest: Path) -> Path:
    """Copie les fichiers de test (et leurs dépendances évidentes) à part.

    Utilisé uniquement par la vérification, jamais donné au dépôt de tâche de
    l'agent.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for rel_str in task.test_files:
        rel = Path(rel_str)
        src = task.exercise_dir / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    return dest


def detect_success(returncode: int, *, timed_out: bool = False) -> bool:
    """Succès = code de sortie 0 du lanceur de tests, jamais un timeout."""
    return returncode == 0 and not timed_out


def pass_hat_k(successes: Sequence[bool]) -> int:
    """1 si toutes les répétitions d'une même tâche x bras sont vertes, sinon 0.

    Définition alignée sur ``pass_k_fully_green / pass_k_observations`` du
    kit (``src/grimoire/traces/ledger.py``) : une série de k rejeux compte
    comme un seul « pass^k » binaire par tâche.
    """
    if not successes:
        return 0
    return 1 if all(successes) else 0


def bootstrap_ci(
    values: Sequence[float],
    *,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    """Intervalle de confiance par bootstrap non paramétrique sur la moyenne."""
    n = len(values)
    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        return (float(values[0]), float(values[0]))
    rng = random.Random(seed)  # noqa: S311 - bootstrap statistique, pas cryptographie
    means = []
    for _ in range(n_boot):
        resample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.fmean(resample))
    means.sort()
    lo_idx = int((alpha / 2) * n_boot)
    hi_idx = min(n_boot - 1, int((1 - alpha / 2) * n_boot))
    return (means[lo_idx], means[hi_idx])


def cis_disjoint(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Deux intervalles [lo, hi] sont disjoints s'ils ne se chevauchent pas."""
    a_lo, a_hi = a
    b_lo, b_hi = b
    return a_hi < b_lo or b_hi < a_lo


def detect_loop(commands: Sequence[str], *, repeat_threshold: int = DEFAULT_LOOP_REPEAT_THRESHOLD) -> bool:
    """Un agent « tourne en rond » : la même commande, répétée d'affilée.

    ``repeat_threshold`` répétitions consécutives et identiques (en fin de
    liste) suffisent — peu importe ce qui s'est passé avant.
    """
    if repeat_threshold <= 0:
        return False
    if len(commands) < repeat_threshold:
        return False
    tail = commands[-repeat_threshold:]
    return len(set(tail)) == 1


def extract_bash_command(stream_json_line: str) -> str | None:
    """Extrait la commande Bash d'une ligne ``--output-format stream-json``.

    Retourne ``None`` pour toute ligne qui n'est pas un appel d'outil Bash
    (assistant text, résultat, événements système…).
    """
    try:
        event = json.loads(stream_json_line)
    except (json.JSONDecodeError, TypeError):
        return None
    if event.get("type") != "assistant":
        return None
    message = event.get("message") or {}
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use" and block.get("name") == "Bash":
            command = (block.get("input") or {}).get("command")
            if isinstance(command, str):
                return command
    return None


def parse_result_event(stream_json_lines: Iterable[str]) -> dict[str, Any] | None:
    """Retient le dernier événement ``type: result`` d'un flux stream-json."""
    result: dict[str, Any] | None = None
    for line in stream_json_lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result = event
    return result


def select_run_order(arms: Sequence[str], *, task_id: str, seed: int) -> list[str]:
    """Ordre des bras tiré au sort, mais reproductible, par tâche."""
    rng = random.Random(f"{seed}:arm-order:{task_id}")  # noqa: S311 - tirage reproductible, pas cryptographie
    order = list(arms)
    rng.shuffle(order)
    return order


def expected_cost_report(pilot_records: Sequence[RunRecord], *, planned_runs_per_arm: dict[str, int]) -> dict[str, Any]:
    """Extrapole le coût attendu de la campagne complète depuis le pilote."""
    by_arm: dict[str, list[float]] = defaultdict(list)
    for record in pilot_records:
        by_arm[record.arm].append(record.total_cost_usd)

    report: dict[str, Any] = {"per_arm": {}, "total_expected_usd": 0.0}
    total = 0.0
    for arm in ARMS:
        costs = by_arm.get(arm, [])
        avg = statistics.fmean(costs) if costs else 0.0
        planned = planned_runs_per_arm.get(arm, 0)
        expected = avg * planned
        total += expected
        report["per_arm"][arm] = {
            "pilot_runs": len(costs),
            "avg_cost_usd": avg,
            "planned_runs": planned,
            "expected_cost_usd": expected,
        }
    report["total_expected_usd"] = total
    return report


def should_alert_cost_overrun(expected_usd: float, actual_usd: float, *, factor: float = 3.0) -> bool:
    if expected_usd <= 0:
        return False
    return actual_usd > factor * expected_usd


def aggregate_success_by_arm(records: Sequence[RunRecord]) -> dict[str, list[bool]]:
    by_arm: dict[str, list[bool]] = defaultdict(list)
    for record in records:
        by_arm[record.arm].append(record.success)
    return dict(by_arm)


def should_stop_early(
    records: Sequence[RunRecord],
    *,
    total_tasks: int,
    min_tasks: int = MIN_TASKS_BEFORE_STOP_CHECK,
    seed: int = 0,
) -> tuple[bool, str]:
    """Critère d'arrêt : IC disjoints entre `kit` et les deux autres bras.

    Retourne ``(arrêt?, motif)``. Ne se prononce pas avant ``min_tasks``
    tâches entièrement rejouées sur les trois bras (bruit sinon).
    """
    tasks_done = len({record.task_id for record in records})
    if tasks_done < min_tasks:
        return (False, f"seulement {tasks_done}/{total_tasks} tâches, minimum {min_tasks}")
    if tasks_done >= total_tasks:
        return (True, "les 20 tâches ont été rejouées")

    by_arm = aggregate_success_by_arm(records)
    if "kit" not in by_arm or not by_arm["kit"]:
        return (False, "pas encore de données pour le bras kit")

    kit_values = [1.0 if s else 0.0 for s in by_arm["kit"]]
    kit_ci = bootstrap_ci(kit_values, seed=seed)

    for other in ("nu", "ecc"):
        other_values = by_arm.get(other)
        if not other_values:
            return (False, f"pas encore de données pour le bras {other}")
        other_ci = bootstrap_ci([1.0 if s else 0.0 for s in other_values], seed=seed)
        if not cis_disjoint(kit_ci, other_ci):
            return (False, f"IC succès kit {kit_ci} chevauche {other} {other_ci}")

    return (True, f"IC succès disjoints : kit {kit_ci} vs nu/ecc, {tasks_done} tâches")


# ── Provisionnement des bras (dépend du système, non testé unitairement) ────


def _run(cmd: Sequence[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )


def ensure_polyglot_benchmark(workspace: Path) -> Path:
    dest = workspace / "polyglot-benchmark"
    if not dest.is_dir():
        _run(["git", "clone", "--depth", "1", POLYGLOT_REPO_URL, str(dest)])
    return dest


def ensure_ecc_repo(workspace: Path) -> tuple[Path, str]:
    dest = workspace / "ecc"
    if not dest.is_dir():
        _run(["git", "clone", "--depth", "1", ECC_REPO_URL, str(dest)])
    commit = _run(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip()
    return dest, commit


def ensure_go_toolchain(workspace: Path) -> Path | None:
    """Go portable, téléchargé une fois dans le workspace — jamais sudo/système."""
    existing = shutil.which("go")
    if existing:
        return Path(existing)
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        return None  # documenté : provisioning portable limité à linux/amd64
    go_dir = workspace / "tools" / "go"
    go_bin = go_dir / "go" / "bin" / "go"
    if go_bin.is_file():
        return go_bin
    go_dir.mkdir(parents=True, exist_ok=True)
    archive = go_dir / "go.tar.gz"
    urllib.request.urlretrieve(GO_TOOLCHAIN_URL, archive)  # noqa: S310 - URL fixe, pinnée
    with tarfile.open(archive) as tar:
        tar.extractall(go_dir)  # noqa: S202 - archive officielle, contenu de confiance
    archive.unlink(missing_ok=True)
    return go_bin if go_bin.is_file() else None


CREDENTIALS_REL_PATH = Path(".claude") / ".credentials.json"


def ensure_isolated_home(home: Path) -> None:
    """Crée le squelette d'un HOME isolé, jamais d'identifiant à demeure.

    Les identifiants ne sont copiés que pour la durée d'un appel ``claude -p``
    (voir ``credentials_provisioned``) — un ``HOME`` isolé au repos (avant le
    premier run, entre deux runs, après la campagne) ne doit jamais en
    porter.
    """
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude").mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def credentials_provisioned(home: Path, *, real_home: Path | None = None) -> Iterator[Path | None]:
    """Copie les identifiants Claude Code dans ``home`` pour la durée du bloc.

    ``ne lis ni n'affiche jamais une clé`` — on se contente de ``copyfile``,
    le contenu ne transite jamais par du texte que ce script émet. Le fichier
    copié est supprimé dans un ``finally`` : succès, erreur ou timeout du run
    qu'il aura couvert ne changent rien, il ne doit jamais survivre au bloc.
    """
    real_home = real_home or Path.home()
    src_creds = real_home / CREDENTIALS_REL_PATH
    dest_creds = home / CREDENTIALS_REL_PATH
    copied = False
    try:
        if src_creds.is_file():
            dest_creds.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_creds, dest_creds)
            dest_creds.chmod(0o600)
            copied = True
        yield dest_creds if copied else None
    finally:
        if copied:
            dest_creds.unlink(missing_ok=True)


def find_leftover_credentials(root: Path) -> list[Path]:
    """Identifiants oubliés sous ``root`` — doit être vide en fin de campagne.

    Balaie tous les ``HOME`` isolés (``root/homes/*``) à la recherche d'un
    ``.claude/.credentials.json`` qui aurait survécu à un run (bug de
    nettoyage, process tué avant le ``finally``, etc.).
    """
    homes_dir = root / "homes"
    if not homes_dir.is_dir():
        return []
    return sorted(homes_dir.glob(f"*/{CREDENTIALS_REL_PATH.as_posix()}"))


def setup_arm_nu(task_dir: Path) -> dict[str, Any]:
    """Bras nu : rien à ajouter, le dépôt de tâche reste tel quel."""
    return {"arm": "nu", "added": []}


def setup_arm_ecc(task_dir: Path, *, ecc_repo: Path, ecc_home: Path, timeout: int = 120) -> dict[str, Any]:
    """Installe le plugin ``ecc@ecc`` en scope projet (voir docs pour la preuve)."""
    env = {**os.environ, "HOME": str(ecc_home)}
    add = _run(
        ["claude", "plugin", "marketplace", "add", str(ecc_repo), "--scope", "local"],
        cwd=task_dir,
        env=env,
        timeout=timeout,
    )
    install = _run(
        ["claude", "plugin", "install", "ecc@ecc", "--scope", "local"],
        cwd=task_dir,
        env=env,
        timeout=timeout,
    )
    return {
        "arm": "ecc",
        "added": [".claude/settings.local.json"],
        "marketplace_add_rc": add.returncode,
        "plugin_install_rc": install.returncode,
        "marketplace_add_stdout": add.stdout[-2000:],
        "plugin_install_stdout": install.stdout[-2000:],
    }


def setup_arm_kit(task_dir: Path, *, kit_home: Path, timeout: int = 180) -> dict[str, Any]:
    """``grimoire init --backend local --no-cockpit`` + ``host sync --host claude``."""
    env = {**os.environ, "HOME": str(kit_home), "GRIMOIRE_NO_COCKPIT": "1"}
    before = {p.relative_to(task_dir).as_posix() for p in task_dir.rglob("*") if p.is_file()}

    init = _run(
        ["grimoire", "init", ".", "--backend", "local", "--no-cockpit"],
        cwd=task_dir,
        env=env,
        timeout=timeout,
    )
    sync = _run(
        ["grimoire", "host", "sync", "--host", "claude"],
        cwd=task_dir,
        env=env,
        timeout=timeout,
    )

    after = {p.relative_to(task_dir).as_posix() for p in task_dir.rglob("*") if p.is_file()}
    added = sorted(after - before)
    return {
        "arm": "kit",
        "added": added,
        "init_rc": init.returncode,
        "sync_rc": sync.returncode,
        "init_stdout": init.stdout[-2000:],
        "sync_stdout": sync.stdout[-2000:],
    }


# ── Exécution de l'agent (LLM, non testée unitairement) ─────────────────────


def run_claude_headless(
    task_dir: Path,
    prompt: str,
    *,
    home: Path,
    timeout_s: int = DEFAULT_RUN_TIMEOUT_S,
    repeat_threshold: int = DEFAULT_LOOP_REPEAT_THRESHOLD,
    poll_interval: float = DEFAULT_POLL_INTERVAL_S,
    log_path: Path | None = None,
) -> RunOutcome:
    """Lance ``claude -p`` en tête sans tête, avec garde-fou timeout + boucle.

    Permissions limitées au dépôt de tâche via le cwd (``bypassPermissions``
    n'est acceptable ici que parce que chaque dépôt de tâche est jetable et
    isolé sous le workspace du banc — voir docs/bench-three-arms.md).
    """
    log_path = log_path or (task_dir.parent / f"{task_dir.name}.stream.jsonl")
    env = {**os.environ, "HOME": str(home)}
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

    outcome = RunOutcome()
    start = time.monotonic()
    seen_commands: list[str] = []

    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(cmd, cwd=task_dir, env=env, stdout=logf, stderr=subprocess.STDOUT)
        last_pos = 0
        while True:
            returncode = proc.poll()
            elapsed = time.monotonic() - start

            try:
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    f.seek(last_pos)
                    new_data = f.read()
                    last_pos = f.tell()
            except OSError:
                new_data = ""

            for line in new_data.splitlines():
                command = extract_bash_command(line)
                if command is not None:
                    seen_commands.append(command)
                    outcome.tool_commands_seen += 1

            if detect_loop(seen_commands, repeat_threshold=repeat_threshold):
                outcome.terminated_reason = "loop"
                _terminate(proc)
                break
            if returncode is not None:
                break
            if elapsed > timeout_s:
                outcome.terminated_reason = "timeout"
                _terminate(proc)
                break
            time.sleep(poll_interval)

    outcome.wall_seconds = time.monotonic() - start

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    result = parse_result_event(lines)
    if result is not None:
        outcome.raw_result = result
        outcome.total_cost_usd = float(result.get("total_cost_usd") or 0.0)
        usage = result.get("usage") or {}
        outcome.input_tokens = int(usage.get("input_tokens") or 0)
        outcome.output_tokens = int(usage.get("output_tokens") or 0)
        outcome.num_turns = int(result.get("num_turns") or 0)
        outcome.model_usage = result.get("modelUsage") or {}
        if outcome.terminated_reason == "completed" and result.get("is_error"):
            outcome.terminated_reason = "error"
    elif outcome.terminated_reason == "completed":
        outcome.terminated_reason = "error"  # jamais de résultat final : anomalie

    return outcome


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


# ── Vérification (exécution des tests cachés) ───────────────────────────────

_TEST_COMMANDS: dict[str, list[str]] = {
    "python": [sys.executable, "-m", "pytest", "-q"],
    "rust": ["cargo", "test", "--quiet"],
}


def run_hidden_tests(
    task: TaskMeta,
    task_dir: Path,
    hidden_dir: Path,
    *,
    go_bin: Path | None = None,
    timeout_s: int = DEFAULT_TEST_TIMEOUT_S,
) -> tuple[bool, str]:
    """Copie les tests cachés dans le dépôt de tâche puis les exécute."""
    for rel_str in task.test_files:
        src = hidden_dir / rel_str
        if not src.is_file():
            return (False, f"fichier de test absent : {rel_str}")
        target = task_dir / rel_str
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)

    if task.language == "javascript":
        install = _run(["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"], cwd=task_dir, timeout=timeout_s)
        if install.returncode != 0:
            return (False, f"npm install a échoué : {install.stdout[-1000:]}{install.stderr[-1000:]}")
        cmd = ["npx", "--no-install", "jest", "--silent"]
    elif task.language == "go":
        if go_bin is None:
            return (False, "toolchain go indisponible")
        cmd = [str(go_bin), "test", "./..."]
    else:
        cmd = _TEST_COMMANDS[task.language]

    try:
        result = _run(cmd, cwd=task_dir, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return (False, "timeout des tests")
    success = detect_success(result.returncode)
    return (success, (result.stdout[-2000:] + result.stderr[-2000:]))


# ── Rapport ──────────────────────────────────────────────────────────────


def _median(values: Sequence[float]) -> float:
    return statistics.median(values) if values else 0.0


def build_report(
    records: Sequence[RunRecord],
    *,
    total_tasks: int,
    expected_cost: dict[str, Any] | None,
    seed: int,
) -> dict[str, Any]:
    """Construit la structure de rapport (utilisée pour le .md et le .json)."""
    by_arm: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        by_arm[record.arm].append(record)

    per_arm: dict[str, Any] = {}
    for arm in ARMS:
        arm_records = by_arm.get(arm, [])
        successes = [r.success for r in arm_records]
        success_values = [1.0 if s else 0.0 for s in successes]
        success_ci = bootstrap_ci(success_values, seed=seed)
        resolved = [r for r in arm_records if r.success]
        by_task: dict[str, list[bool]] = defaultdict(list)
        for r in arm_records:
            by_task[r.task_id].append(r.success)
        pass_k_values = [pass_hat_k(v) for v in by_task.values()]
        pass_k_ci = bootstrap_ci([float(v) for v in pass_k_values], seed=seed)
        per_arm[arm] = {
            "n_runs": len(arm_records),
            "n_tasks": len(by_task),
            "success_rate": statistics.fmean(success_values) if success_values else 0.0,
            "success_rate_ci95": list(success_ci),
            "pass_hat_k_rate": statistics.fmean(pass_k_values) if pass_k_values else 0.0,
            "pass_hat_k_ci95": list(pass_k_ci),
            "median_wall_seconds": _median([r.wall_seconds for r in arm_records]),
            "median_cost_usd_resolved": _median([r.total_cost_usd for r in resolved]),
            "total_cost_usd": sum(r.total_cost_usd for r in arm_records),
            "median_num_turns": _median([r.num_turns for r in arm_records]),
            "terminated_reasons": {
                reason: sum(1 for r in arm_records if r.terminated_reason == reason)
                for reason in sorted({r.terminated_reason for r in arm_records})
            },
        }

    by_task_arm: dict[str, dict[str, Any]] = {}
    task_ids = sorted({r.task_id for r in records})
    for task_id in task_ids:
        task_records = [r for r in records if r.task_id == task_id]
        row: dict[str, Any] = {"language": task_records[0].language if task_records else "?"}
        for arm in ARMS:
            arm_task_records = [r for r in task_records if r.arm == arm]
            row[arm] = {
                "runs": len(arm_task_records),
                "successes": sum(1 for r in arm_task_records if r.success),
                "pass_hat_k": pass_hat_k([r.success for r in arm_task_records]),
            }
        by_task_arm[task_id] = row

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "seed": seed,
        "total_tasks": total_tasks,
        "tasks_run": len(task_ids),
        "k": K_REPLAY,
        "expected_cost": expected_cost,
        "per_arm": per_arm,
        "by_task": by_task_arm,
    }


def render_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Banc à trois bras — Claude Code nu / ecc / grimoire-kit",
        "",
        f"Généré le {report['generated_at']} · graine {report['seed']} · "
        f"{report['tasks_run']}/{report['total_tasks']} tâches rejouées (k={report['k']}).",
        "",
        "Benchmark de tâches : [Aider polyglot-benchmark]"
        f"({POLYGLOT_REPO_URL}) (exercices Exercism, licence MIT par piste). "
        f"Paquet ecc : [{ECC_REPO_URL}]({ECC_REPO_URL}) (MIT).",
        "",
    ]

    expected = report.get("expected_cost")
    if expected:
        actual_total = sum(a["total_cost_usd"] for a in report["per_arm"].values())
        expected_total = expected.get("total_expected_usd", 0.0)
        lines.append(f"Coût attendu (extrapolé du pilote) : **${expected_total:.2f}** — coût réel constaté : **${actual_total:.2f}**.")
        if should_alert_cost_overrun(expected_total, actual_total):
            lines.append(f"⚠️ **Alerte coût** : le coût réel dépasse 3x l'attendu (${expected_total:.2f} → ${actual_total:.2f}).")
        lines.append("")

    lines.append("## Par bras")
    lines.append("")
    lines.append("| Bras | Runs | Tâches | Succès | IC95% succès | pass^k | Temps médian (s) | Coût médian/tâche résolue | Coût total |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for arm in ARMS:
        a = report["per_arm"].get(arm)
        if not a:
            continue
        ci = a["success_rate_ci95"]
        lines.append(
            f"| {arm} | {a['n_runs']} | {a['n_tasks']} | {a['success_rate']:.0%} | "
            f"[{ci[0]:.0%}, {ci[1]:.0%}] | {a['pass_hat_k_rate']:.0%} | "
            f"{a['median_wall_seconds']:.0f} | ${a['median_cost_usd_resolved']:.3f} | "
            f"${a['total_cost_usd']:.2f} |"
        )
    lines.append("")

    lines.append("## Par tâche")
    lines.append("")
    lines.append("| Tâche | Langue | nu | ecc | kit |")
    lines.append("|---|---|---|---|---|")
    for task_id, row in sorted(report["by_task"].items()):
        cells = []
        for arm in ARMS:
            arm_row = row.get(arm, {})
            cells.append(f"{arm_row.get('successes', 0)}/{arm_row.get('runs', 0)}")
        lines.append(f"| {task_id} | {row.get('language', '?')} | " + " | ".join(cells) + " |")
    lines.append("")

    return "\n".join(lines) + "\n"


def write_report(records: Sequence[RunRecord], out_dir: Path, *, total_tasks: int, expected_cost: dict[str, Any] | None, seed: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(records, total_tasks=total_tasks, expected_cost=expected_cost, seed=seed)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(render_report_markdown(report), encoding="utf-8")


# ── CLI ───────────────────────────────────────────────────────────────────

BUILD_PROMPT_TEMPLATE = (
    "Tu travailles dans le dépôt courant. Lis TASK.md : il décrit un exercice "
    "de programmation avec un fichier de départ à compléter. Modifie "
    "uniquement les fichiers de solution nécessaires ({solution_files}) pour "
    "que l'implémentation soit correcte. N'écris ni ne modifie aucun fichier "
    "de test. Termine dès que tu es raisonnablement confiant dans la "
    "correction, sans lancer de suite de tests externe si elle n'est pas "
    "déjà présente dans le dépôt."
)


def build_prompt(task: TaskMeta) -> str:
    return BUILD_PROMPT_TEMPLATE.format(solution_files=", ".join(task.solution_files))


def _default_workspace() -> Path:
    return Path(os.environ.get("GRIMOIRE_BENCH_WORKSPACE", "bench-workspace")).resolve()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="prépare les dépôts de tâche sans appeler de modèle")
    mode.add_argument("--pilot", action="store_true", help="2 tâches x 3 bras x 1 rejeu, appels réels")
    mode.add_argument("--full", action="store_true", help="20 tâches x 3 bras x k=3, appels réels")
    mode.add_argument("--report-only", action="store_true", help="régénère report.md/json depuis results.jsonl, sans appel LLM")
    parser.add_argument("--workspace", type=Path, default=None, help="racine de travail (clones, homes, dépôts de tâche, résultats)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--run-timeout-s", type=int, default=DEFAULT_RUN_TIMEOUT_S)
    parser.add_argument("--resume", action="store_true", help="ne rejoue pas les (tâche, bras, run) déjà dans results.jsonl")
    parser.add_argument("--report-dir", type=Path, default=None, help="dossier de sortie du rapport (défaut : <workspace>/reports/<date>)")
    args = parser.parse_args(argv)

    if args.report_only:
        workspace = args.workspace or _default_workspace()
        return _do_report_only(workspace, seed=args.seed, report_dir=args.report_dir)

    workspace = args.workspace or _default_workspace()
    workspace.mkdir(parents=True, exist_ok=True)

    bench_root = ensure_polyglot_benchmark(workspace)
    ecc_repo, ecc_commit = ensure_ecc_repo(workspace)
    catalog = discover_catalog(bench_root)
    tasks = sample_tasks(catalog, seed=args.seed)

    state_dir = workspace / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "selection.json").write_text(
        json.dumps({"seed": args.seed, "ecc_commit": ecc_commit, "tasks": [t.to_dict() for t in tasks]}, indent=2),
        encoding="utf-8",
    )

    homes = {arm: workspace / "homes" / arm for arm in ARMS}
    for home in homes.values():
        ensure_isolated_home(home)

    go_bin = ensure_go_toolchain(workspace) if any(t.language == "go" for t in tasks) else None

    if args.dry_run:
        return _do_dry_run(tasks, workspace=workspace, ecc_repo=ecc_repo, homes=homes)

    results_path = state_dir / "results.jsonl"
    already_done: set[tuple[str, str, int]] = set()
    if args.resume and results_path.is_file():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            already_done.add((record["task_id"], record["arm"], record["run_index"]))

    if args.pilot:
        selected = tasks[:2]
        k = 1
    else:
        selected = tasks
        k = K_REPLAY

    records: list[RunRecord] = []
    if args.resume and results_path.is_file():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            records.append(RunRecord(**d))

    with open(results_path, "a", encoding="utf-8") as results_f:
        for task in selected:
            order = select_run_order(ARMS, task_id=task.task_id, seed=args.seed)
            for arm in order:
                for run_index in range(k):
                    key = (task.task_id, arm, run_index)
                    if key in already_done:
                        continue
                    record = _run_one(task, arm, run_index, workspace=workspace, ecc_repo=ecc_repo, homes=homes, go_bin=go_bin, run_timeout_s=args.run_timeout_s)
                    records.append(record)
                    results_f.write(json.dumps(record.to_dict()) + "\n")
                    results_f.flush()

            if args.full:
                stop, reason = should_stop_early(records, total_tasks=len(tasks), seed=args.seed)
                print(f"[stop-check] {reason}")
                if stop:
                    print(f"[stop] arrêt anticipé : {reason}")
                    break

    expected_cost = None
    expected_cost_path = state_dir / "expected_cost.json"
    if args.pilot:
        planned = {arm: len(tasks) * K_REPLAY for arm in ARMS}
        expected_cost = expected_cost_report(records, planned_runs_per_arm=planned)
        expected_cost_path.write_text(json.dumps(expected_cost, indent=2), encoding="utf-8")
        print(json.dumps(expected_cost, indent=2))
    elif expected_cost_path.is_file():
        expected_cost = json.loads(expected_cost_path.read_text(encoding="utf-8"))

    if args.full:
        report_dir = args.report_dir or (workspace / "reports" / datetime.now(tz=UTC).strftime("%Y-%m-%d"))
        write_report(records, report_dir, total_tasks=len(tasks), expected_cost=expected_cost, seed=args.seed)
        print(f"[report] écrit sous {report_dir}")
        actual_total = sum(r.total_cost_usd for r in records)
        if expected_cost and should_alert_cost_overrun(expected_cost.get("total_expected_usd", 0.0), actual_total):
            print(
                f"[ALERTE COÛT] réel ${actual_total:.2f} > 3x attendu "
                f"${expected_cost.get('total_expected_usd', 0.0):.2f}",
                file=sys.stderr,
            )

    leftovers = find_leftover_credentials(workspace)
    if leftovers:
        print(
            f"[ALERTE SÉCURITÉ] {len(leftovers)} identifiant(s) Claude Code oublié(s) sous des HOME isolés "
            f"(le nettoyage `finally` n'a pas tourné, probablement un process tué avant terme) : "
            + ", ".join(str(p) for p in leftovers),
            file=sys.stderr,
        )
        for p in leftovers:
            p.unlink(missing_ok=True)
        print(f"[ALERTE SÉCURITÉ] {len(leftovers)} identifiant(s) supprimé(s) rétroactivement.", file=sys.stderr)
    else:
        print("[sécurité] aucun identifiant oublié sous un HOME isolé — vérifié en fin de campagne.")

    return 0


def _do_report_only(workspace: Path, *, seed: int, report_dir: Path | None) -> int:
    state_dir = workspace / "state"
    results_path = state_dir / "results.jsonl"
    if not results_path.is_file():
        print(f"[report-only] aucun {results_path}", file=sys.stderr)
        return 1
    records = [RunRecord(**json.loads(line)) for line in results_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    selection_path = state_dir / "selection.json"
    total_tasks = len(json.loads(selection_path.read_text(encoding="utf-8"))["tasks"]) if selection_path.is_file() else len({r.task_id for r in records})
    expected_cost_path = state_dir / "expected_cost.json"
    expected_cost = json.loads(expected_cost_path.read_text(encoding="utf-8")) if expected_cost_path.is_file() else None
    out_dir = report_dir or (workspace / "reports" / datetime.now(tz=UTC).strftime("%Y-%m-%d"))
    write_report(records, out_dir, total_tasks=total_tasks, expected_cost=expected_cost, seed=seed)
    print(f"[report] écrit sous {out_dir}")
    return 0


def _do_dry_run(tasks: Sequence[TaskMeta], *, workspace: Path, ecc_repo: Path, homes: dict[str, Path]) -> int:
    tasks_root = workspace / "tasks"
    for task in tasks:
        for arm in ARMS:
            task_dir = tasks_root / task.task_id.replace("/", "__") / arm / "prep"
            prepare_task_repo(task, task_dir)
            hidden_tests_dir(task, tasks_root / task.task_id.replace("/", "__") / "hidden-tests")
            if arm == "ecc":
                setup_arm_ecc(task_dir, ecc_repo=ecc_repo, ecc_home=homes["ecc"])
            elif arm == "kit":
                setup_arm_kit(task_dir, kit_home=homes["kit"])
            print(f"[dry-run] préparé {task.task_id} / {arm} -> {task_dir}")
    print(f"[dry-run] {len(tasks)} tâches x {len(ARMS)} bras préparées sous {tasks_root}")
    return 0


def _run_one(
    task: TaskMeta,
    arm: str,
    run_index: int,
    *,
    workspace: Path,
    ecc_repo: Path,
    homes: dict[str, Path],
    go_bin: Path | None,
    run_timeout_s: int,
) -> RunRecord:
    run_dir = workspace / "tasks" / task.task_id.replace("/", "__") / arm / f"run{run_index}"
    prepare_task_repo(task, run_dir)
    hidden_dir = hidden_tests_dir(task, workspace / "tasks" / task.task_id.replace("/", "__") / "hidden-tests")

    if arm == "ecc":
        setup_arm_ecc(run_dir, ecc_repo=ecc_repo, ecc_home=homes["ecc"])
    elif arm == "kit":
        setup_arm_kit(run_dir, kit_home=homes["kit"])

    home = homes[arm]
    # Les identifiants ne vivent dans `home` que le temps de cet appel : le
    # `finally` de `credentials_provisioned` les efface, que le run réussisse,
    # échoue, ou soit tué pour timeout/boucle — jamais laissés à demeure.
    with credentials_provisioned(home) as creds:
        if creds is None:
            raise RuntimeError(
                f"aucun identifiant Claude Code trouvé sous {Path.home()}/.claude — "
                "authentifie-toi (`claude /login`) avant de lancer une campagne réelle."
            )
        outcome = run_claude_headless(run_dir, build_prompt(task), home=home, timeout_s=run_timeout_s)

    if outcome.terminated_reason in ("timeout", "loop"):
        success = False
    else:
        success, _ = run_hidden_tests(task, run_dir, hidden_dir, go_bin=go_bin)

    dispatch_stats = None
    if arm == "kit":
        dispatch_stats = _collect_dispatch_stats(run_dir, home)

    return RunRecord(
        task_id=task.task_id,
        language=task.language,
        arm=arm,
        run_index=run_index,
        success=success,
        total_cost_usd=outcome.total_cost_usd,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        num_turns=outcome.num_turns,
        wall_seconds=outcome.wall_seconds,
        terminated_reason=outcome.terminated_reason,
        dispatch_stats=dispatch_stats,
    )


def _collect_dispatch_stats(run_dir: Path, home: Path) -> dict[str, Any] | None:
    env = {**os.environ, "HOME": str(home)}
    result = _run(["grimoire", "dispatch", "stats", "--json"], cwd=run_dir, env=env, timeout=30)
    if result.returncode != 0:
        return {"error": result.stderr[-500:]}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "sortie non JSON", "stdout": result.stdout[-500:]}


if __name__ == "__main__":
    raise SystemExit(main())
