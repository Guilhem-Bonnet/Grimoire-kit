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
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
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
#: ``kit-gov`` (lot F, issue #582) : le bras ``kit``, plus un projet réellement
#: enrôlé au standard (``grimoire standard init`` + une tâche de board posée
#: en ``in_progress``). Le rejeu du lot E (docs/bench/rejeu-lot-e-2026-09-17.md
#: §3) a mesuré que le bras ``kit`` seul ne fait jamais passer
#: ``_is_governed()`` (lot A) à ``True`` : le lot B (``gate run-tests``) reste
#: structurellement invisible sur ce banc sans ce quatrième bras.
ARMS: tuple[str, ...] = ("nu", "ecc", "kit", "kit-gov")

DEFAULT_RUN_TIMEOUT_S = 15 * 60  # garde-fou par run (§ « Arrêt et garde-fous »)
DEFAULT_TEST_TIMEOUT_S = 180
DEFAULT_LOOP_REPEAT_THRESHOLD = 4  # même commande répétée N fois de suite
DEFAULT_POLL_INTERVAL_S = 2.0
MIN_TASKS_BEFORE_STOP_CHECK = 4  # pas de verdict avant un minimum de données
DISK_GUARD_MIN_FREE_GB = 5.0  # sous ce seuil, arrêt propre plutôt qu'un run qui crashe

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
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    num_turns: int = 0
    wall_seconds: float = 0.0
    terminated_reason: str = "completed"  # completed|timeout|loop|error
    model_usage: dict[str, Any] = field(default_factory=dict)
    tool_commands_seen: int = 0
    raw_result: dict[str, Any] | None = None
    # Lot I (#582) : combien des commandes Bash vues dans ce run sont un
    # symptôme de friction de découverte de toolchain (``which``, ``command
    # -v``, ``find / -name``, ``rustup``, ``npm install`` lancés PAR L'AGENT
    # lui-même) — voir :func:`count_toolchain_friction_bash_calls`. Calculé
    # pour tous les bras (pas seulement ``kit-gov``) : c'est le rapport qui
    # décide lequel exploiter.
    toolchain_friction_bash_calls: int = 0

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
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    # ``RunOutcome.model_usage`` était rempli mais jamais recopié ici avant
    # #551/#552 lot A : ``results.jsonl`` perdait la répartition par modèle
    # que le diagnostic du surcoût kit avait besoin de relire.
    model_usage: dict[str, Any] | None = None
    # Horodatage d'écriture (lot E, #582) : seul moyen de dater un bras
    # "repris" tel quel par ``--resume --arms`` plutôt que rejoué. Absent des
    # lignes écrites par les campagnes antérieures à ce lot — ``None`` alors,
    # jamais reconstruit a posteriori (voir ``carried_over_label``).
    recorded_at: str | None = None
    # Uniquement pour le bras ``kit`` (lot E, #582) : ``True``/``False`` selon
    # qu'un ``_grimoire-output/evidence/<task>/test-run.json`` existe dans le
    # dépôt de tâche à la fin du run (preuve que ``gate run-tests`` a tourné,
    # lot B) ; ``None`` pour les bras ``nu``/``ecc`` où la question ne se pose
    # pas, et pour les lignes écrites avant ce lot.
    kit_test_run_evidence: bool | None = None
    # Lot H (#582) : ``True``/``False`` si un ``package.json`` a été détecté
    # dans le dépôt de tâche et que ``npm install`` a été tenté par
    # ``setup_arm_*`` (voir ``install_test_dependencies``), ``None`` si aucun
    # manifeste JS n'a été trouvé (tâche non-JS) ou pour les lignes écrites
    # avant ce lot. Tous les bras sont concernés, pas seulement `kit`/`kit-gov`
    # — équité de méthode : avant ce lot, seule la vérification finale du
    # harnais (``run_hidden_tests``) installait les dépendances JS, jamais la
    # session de l'agent elle-même, qui recevait donc systématiquement un
    # `npm test`/`npx jest` en échec `exit 127` (« jest absent du bac à
    # sable »).
    test_deps_install_ok: bool | None = None
    # Lot I (#582) : voir ``RunOutcome.toolchain_friction_bash_calls`` — copié
    # tel quel dans le rapport persistant. ``0`` (jamais ``None``) pour les
    # lignes écrites avant ce lot : un run sans transcription connue n'a pas
    # plus de friction mesurée qu'un run qui n'en a montré aucune, les deux
    # sont indiscernables a posteriori et ce champ ne prétend jamais le
    # contraire.
    toolchain_friction_bash_calls: int = 0
    # Lot J (#582) : mode d'authentification du lanceur pour CE run — voir
    # ``resolve_auth_mode``/``AUTH_MODES``. ``None`` pour les lignes écrites
    # avant ce lot (toutes en ``oauth-copy`` de fait, jamais reconstruit a
    # posteriori — même convention que ``recorded_at``).
    auth_mode: str | None = None

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


#: Lot I (#582) : motifs de friction de découverte de toolchain, mesurés en
#: détail sur 8 transcriptions par le rejeu du lot H
#: (``docs/bench/rejeu-lot-h-2026-09-18.md`` §3) : ``command -v go``,
#: ``which -a go``, un ``find / -name gofmt`` jusqu'à 6 niveaux, ``rustup
#: toolchain list``/``rustup default stable``. Un poste qui n'existe QUE sur
#: les runs gouvernés (``gate check --strict`` exécute réellement les tests,
#: lot G1) et qui ne devrait plus apparaître une fois que
#: :func:`run_environment` donne à l'agent la même toolchain que la
#: vérification finale du harnais.
_TOOLCHAIN_FRICTION_PATTERN = re.compile(
    r"(?<![\w-])which\b"
    r"|\bcommand\s+-v\b"
    r"|\bfind\s+\S+.*-name\b"
    r"|\brustup\b"
    r"|\bnpm\s+install\b"
)


def is_toolchain_friction_command(command: str) -> bool:
    """Vrai si *command* (une commande Bash lancée par l'agent) est un
    symptôme de friction de découverte de toolchain — voir
    :data:`_TOOLCHAIN_FRICTION_PATTERN`."""
    return bool(_TOOLCHAIN_FRICTION_PATTERN.search(command))


def count_toolchain_friction_bash_calls(commands: Sequence[str]) -> int:
    """Combien de *commands* (issues de :func:`extract_bash_command`) relèvent
    de la friction de découverte de toolchain (lot I, #582)."""
    return sum(1 for command in commands if is_toolchain_friction_command(command))


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


def parse_arms(value: str | None) -> tuple[str, ...]:
    """Parse ``--arms`` en un sous-ensemble de :data:`ARMS`.

    ``None`` ou une chaîne vide/blanche replient sur les trois bras (repli
    documenté par ``--arms``, testé par ``test_parse_arms_*``). L'ordre de
    retour est toujours l'ordre canonique de ``ARMS``, quel que soit l'ordre
    donné en entrée (``"kit,nu"`` et ``"nu,kit"`` donnent le même résultat) —
    c'est cet ordre qui pilote ensuite le filtrage de ``select_run_order``.
    """
    if value is None or not value.strip():
        return ARMS
    requested = [part.strip() for part in value.split(",") if part.strip()]
    if not requested:
        return ARMS
    unknown = sorted(set(requested) - set(ARMS))
    if unknown:
        raise ValueError(f"bras inconnu(s) : {', '.join(unknown)} (attendus : {', '.join(ARMS)})")
    selected = tuple(arm for arm in ARMS if arm in requested)
    return selected


def has_test_run_evidence(task_dir: Path) -> bool:
    """``True`` si un ``_grimoire-output/evidence/<task>/test-run.json`` existe.

    Preuve, côté banc, que ``grimoire standard gate run-tests`` a bien tourné
    dans la session (lot B, #582/#585) — peu importe le ``--task-id`` choisi
    par l'agent, d'où le ``glob`` plutôt qu'un chemin figé.
    """
    evidence_root = task_dir / "_grimoire-output" / "evidence"
    if not evidence_root.is_dir():
        return False
    return any(evidence_root.glob("*/test-run.json"))


def carried_over_label(records: Sequence[RunRecord], arm: str) -> str:
    """Date (``AAAA-MM-JJ``) du run le plus ancien connu pour *arm*.

    Utilisé pour la mention « bras repris de la campagne du <date> » quand
    ``--arms`` restreint le rejeu et qu'un bras n'est présent que via des
    lignes ``results.jsonl`` reprises telles quelles (``--resume``). Les
    lignes écrites avant le lot E n'ont pas de ``recorded_at`` : le repli est
    alors explicite plutôt qu'une date inventée.
    """
    dates = sorted(record.recorded_at[:10] for record in records if record.arm == arm and record.recorded_at)
    if dates:
        return dates[0]
    return "date inconnue (données antérieures au suivi recorded_at)"


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


def _extract_go_archive(archive: Path, dest: Path) -> None:
    """Dézippe *archive* sous *dest* — factorisé pour rester testable sans réseau.

    `filter="data"` (issue CodeQL py/tarslip) : même une archive officielle
    pinnée peut, en cas de compromission de la source ou de MITM, contenir un
    membre `../`/absolu ; le filtre refuse ces entrées au lieu de leur faire
    confiance implicitement.
    """
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="data")


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
    _extract_go_archive(archive, go_dir)
    archive.unlink(missing_ok=True)
    return go_bin if go_bin.is_file() else None


#: Rust et Node ne sont JAMAIS provisionnés par ce harnais (contrairement à
#: Go, voir :func:`ensure_go_toolchain`) : langue -> binaire système attendu
#: sur ``PATH``. Un besoin qui n'a pas d'entrée ici (python) n'a pas de
#: toolchain système à vérifier — l'interpréteur qui fait tourner ce script
#: est déjà celui utilisé pour ``python -m pytest``.
_SYSTEM_TOOLCHAIN_BINARIES: dict[str, str] = {
    "rust": "cargo",
    "javascript": "npm",
}


def ensure_system_toolchains_present(languages: Sequence[str]) -> None:
    """Garde-fou lot I (#582) : Rust/Node doivent déjà être sur le ``PATH``
    système AVANT toute dépense — même principe que
    :func:`resolve_grimoire_bin`/``disk_guard_ok``, jamais un ``cargo
    test``/``npm test`` qui échoue en pleine session ``claude -p`` payante
    faute d'un message clair en amont.

    Appelée pour les langues réellement présentes dans la sélection de
    tâches — un rejeu qui ne porte pas de tâche Rust n'a pas à exiger
    ``cargo``. Lève ``SystemExit`` (jamais une exception avalée) au premier
    binaire manquant, avec la liste complète des manques, pas seulement le
    premier trouvé.
    """
    missing = [
        f"{binary} ({language})"
        for language in languages
        if (binary := _SYSTEM_TOOLCHAIN_BINARIES.get(language)) is not None and shutil.which(binary) is None
    ]
    if missing:
        raise SystemExit(
            "toolchain(s) système manquante(s) sur PATH avant toute dépense : "
            + ", ".join(missing)
            + " — installe-les (ex. rustup pour Rust, nvm/le gestionnaire de paquets du "
            "système pour Node.js) avant de lancer une campagne réelle. 0 $ dépensé."
        )


#: Bras qui doivent voir le binaire ``grimoire`` résolu sur leur ``PATH`` —
#: ``nu``/``ecc`` restent délibérément sans lui, pour ne jamais laisser un
#: agent « nu » découvrir ``grimoire`` par accident (voir
#: :func:`run_environment`).
_GRIMOIRE_ARMS: frozenset[str] = frozenset({"kit", "kit-gov"})


def run_environment(
    task_dir: Path,
    arm: str,
    *,
    home: Path,
    grimoire_bin: str,
    workspace: Path,
    go_bin: Path | None = None,
    npm_cache_dir: Path | None = None,
    real_home: Path | None = None,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Environnement d'exécution UNIQUE pour (*task_dir*, *arm*) — lot I (#582).

    Le rejeu du lot H (``docs/bench/rejeu-lot-h-2026-09-18.md`` §3,
    2026-09-18) a mesuré que ``grimoire standard gate check --strict``
    exécute la commande de test du projet DANS la session de l'agent (lot
    G1), mais que le harnais ne provisionnait la toolchain Go
    (:func:`ensure_go_toolchain`) que pour SA PROPRE vérification finale
    (:func:`run_hidden_tests`) — deux environnements différents pour la même
    commande de test, jamais transmis à l'agent : ``go`` absent du ``PATH``
    de la session, jusqu'à 28 tours perdus à chercher le binaire
    (``command -v go``, ``find / -name gofmt``). Cette fonction est
    désormais le SEUL endroit qui calcule cet environnement — appelée une
    fois par run dans ``_run_one``, le même ``dict`` est transmis à la fois
    à :func:`run_claude_headless` (la session de l'agent, ses hooks, et donc
    ``gate check --strict``) et à :func:`run_hidden_tests` (la vérification
    finale du harnais) : jamais deux calculs qui pourraient diverger.

    - **Go** : le binaire résolu par :func:`ensure_go_toolchain` (provisionné
      sous le workspace, ou système) est mis EN TÊTE du ``PATH``, pour tous
      les bras — un agent ``nu``/``ecc`` sur une tâche Go affronte la même
      absence de toolchain qu'un agent gouverné, ce n'est pas spécifique à
      ``kit-gov``. ``GOROOT`` n'est fixé que si le binaire est celui
      provisionné par ce harnais (jamais pour un ``go`` système, dont la
      disposition des répertoires n'est pas garantie) ; ``GOPATH``/``GOCACHE``
      sont TOUJOURS redirigés sous le workspace, partagés entre bras et
      tâches (cache de compilation réutilisé, jamais écrit sous le ``HOME``
      réel de l'opérateur ni sous un ``HOME`` isolé éphémère).
    - **Rust/Node** : jamais provisionnés (voir
      :func:`ensure_system_toolchains_present`) — ``CARGO_HOME``/
      ``RUSTUP_HOME`` sont explicitement repointés sur ceux de *real_home*
      (``Path.home()`` par défaut) SI l'environnement de base ne les
      redirige pas déjà lui-même : sans ça, le ``HOME`` isolé (``home``,
      utilisé pour l'authentification Claude Code) ferait chercher à
      ``cargo``/``rustup`` une toolchain sous un ``$HOME/.cargo`` qui
      n'existe pas pour cet utilisateur isolé, quand bien même Rust est
      installé sur le poste — cause probable des ``rustup toolchain
      list``/``rustup default stable`` observés sur les runs gouvernés Rust
      du rejeu lot H. ``CARGO_TARGET_DIR`` (le cache de compilation, pas le
      registre) est lui TOUJOURS redirigé sous le workspace.
    - **npm** : ``npm_config_cache`` sous *npm_cache_dir* si fourni — même
      convention que :func:`install_test_dependencies`.
    - **grimoire** : son répertoire n'est ajouté en tête de ``PATH`` (et
      ``GRIMOIRE_NO_COCKPIT=1`` posé) que pour les bras de
      :data:`_GRIMOIRE_ARMS` — ``nu``/``ecc`` ne doivent jamais découvrir
      ``grimoire`` par un effet de bord de ce harnais.

    *task_dir* n'influence pas encore le calcul (aucun besoin par-tâche
    identifié à ce jour) ; il reste dans la signature pour que chaque appel
    documente sans ambiguïté DE QUEL run l'environnement est construit — un
    futur besoin par-tâche (ex. un ``.tool-versions`` local au dépôt) n'aurait
    alors qu'un seul endroit à changer.
    """
    del task_dir  # voir docstring : réservé, pas encore utilisé par le calcul
    base = dict(base_env) if base_env is not None else dict(os.environ)
    real_home = real_home or Path.home()

    env = dict(base)
    env["HOME"] = str(home)

    path_entries: list[str] = []
    if go_bin is not None:
        path_entries.append(str(go_bin.parent))
    if arm in _GRIMOIRE_ARMS:
        path_entries.append(str(Path(grimoire_bin).parent))
        env["GRIMOIRE_NO_COCKPIT"] = "1"
    path_entries.append(base.get("PATH", os.defpath))
    env["PATH"] = os.pathsep.join(entry for entry in path_entries if entry)

    if go_bin is not None:
        go_tools_dir = (workspace / "tools" / "go").resolve()
        try:
            provisioned = go_bin.resolve().is_relative_to(go_tools_dir)
        except OSError:
            provisioned = False
        if provisioned:
            env["GOROOT"] = str(go_bin.parent.parent)
        env["GOPATH"] = str(workspace / "tools" / "go-path")
        env["GOCACHE"] = str(workspace / "tools" / "go-build-cache")

    # ``setdefault`` sur ``env`` (déjà initialisé depuis ``base``) : si
    # l'opérateur redirige déjà CARGO_HOME/RUSTUP_HOME (poste avec plusieurs
    # toolchains Rust), sa redirection l'emporte — jamais un `HOME` isolé ne
    # doit silencieusement l'écraser par un défaut qui n'existerait pas pour
    # cet utilisateur isolé.
    env.setdefault("CARGO_HOME", str(real_home / ".cargo"))
    env.setdefault("RUSTUP_HOME", str(real_home / ".rustup"))
    env["CARGO_TARGET_DIR"] = str(workspace / "tools" / "cargo-target")

    if npm_cache_dir is not None:
        env["npm_config_cache"] = str(npm_cache_dir)

    return env


def resolve_grimoire_bin(explicit: str | None) -> str:
    """Chemin ABSOLU du binaire ``grimoire`` à invoquer PARTOUT dans le harnais.

    Incident lot H (#582, 2026-09-18) : lancer le harnais avec un ``PATH``
    RELATIF (``PATH=".venv/bin:$PATH"``) a fait retomber chaque appel
    ``grimoire`` d'un sous-processus (``cwd`` = dépôt de tâche jetable) sur
    le ``grimoire`` suivant du ``PATH`` — celui de la Forge, une version
    antérieure aux lots G — parce qu'une entrée ``PATH`` relative se résout
    par rapport au ``cwd`` du sous-processus, jamais au répertoire de
    lancement du harnais. 27 runs (44 $) ont rejoué l'ancien gabarit de
    directive sans qu'aucun message d'erreur ne le signale (voir
    :func:`verify_grimoire_binary_matches_template`, ajoutée par ce lot pour
    que ce silence devienne impossible).

    Cette fonction ne renvoie jamais une entrée ``PATH`` : ``explicit`` (typiquement
    ``--grimoire-bin``) est résolu en chemin absolu et doit exister ; à défaut,
    ``shutil.which("grimoire")`` est résolu en absolu à son tour. Lève
    ``SystemExit`` avec un message actionnable si aucun binaire n'est trouvable —
    jamais un repli silencieux sur la chaîne littérale ``"grimoire"``.
    """
    if explicit:
        resolved = Path(explicit).expanduser().resolve()
        if not resolved.is_file():
            raise SystemExit(f"--grimoire-bin {explicit!r} introuvable (résolu en {resolved}).")
        return str(resolved)
    found = shutil.which("grimoire")
    if not found:
        raise SystemExit(
            "grimoire introuvable sur PATH — installe-le dans l'environnement du "
            "harnais ou passe --grimoire-bin <chemin absolu>."
        )
    return str(Path(found).resolve())


#: Modes d'authentification Claude Code acceptés par ``--auth`` (lot J, #582).
AUTH_MODES: tuple[str, ...] = ("oauth-copy", "api-key")


def resolve_auth_mode(explicit: str | None, *, env: dict[str, str] | None = None) -> str:
    """Résout le mode d'authentification du lanceur pour cette campagne (lot J, #582).

    Incident répété (lots E, F, H, J — 4 fois en deux jours, 30 runs perdus
    au lot J) : ``credentials_provisioned`` copiait les identifiants OAuth de
    l'opérateur (``~/.claude/.credentials.json``) dans le ``HOME`` isolé de
    CHAQUE run. Ces jetons sont rafraîchis par rotation ; quand une copie
    rafraîchit son jeton, la session interactive de l'opérateur ET les
    autres copies en cours deviennent invalides (« OAuth session expired and
    could not be refreshed »). Deux modes, jamais un repli silencieux :

    - ``"api-key"`` : aucune copie d'identifiant — ``ANTHROPIC_API_KEY`` est
      transmise telle quelle à ``claude -p --bare`` (voir
      :func:`run_claude_headless`). Documentation Claude Code
      (``code.claude.com/docs/en/authentication.md`` et ``.../headless.md``,
      consultées 2026-09-18) : ``ANTHROPIC_API_KEY`` prime sur les
      identifiants OAuth dans l'ordre de précédence, et le mode ``--bare``
      ignore explicitement le trousseau OAuth — jamais de rotation partagée
      à craindre.
    - ``"oauth-copy"`` : comportement historique (copie temporaire, voir
      :func:`credentials_provisioned`), avec l'avertissement de rotation
      affiché une fois par campagne.

    *explicit* (``--auth``) est prioritaire. À défaut, ``"api-key"`` si
    ``ANTHROPIC_API_KEY`` est présente dans *env* (``os.environ`` par
    défaut), sinon ``"oauth-copy"`` — jamais l'inverse : une clé API présente
    ne doit jamais être ignorée au profit d'une copie de jeton qui expose
    l'opérateur au bug de rotation.

    Lève ``SystemExit`` si ``--auth api-key`` est demandé explicitement sans
    ``ANTHROPIC_API_KEY`` dans l'environnement — jamais un repli silencieux
    vers ``oauth-copy`` quand l'opérateur a explicitement choisi l'autre mode.
    """
    env = env if env is not None else dict(os.environ)
    if explicit is not None:
        if explicit not in AUTH_MODES:
            raise SystemExit(f"--auth {explicit!r} inconnu — attendu l'un de {AUTH_MODES}.")
        if explicit == "api-key" and not env.get("ANTHROPIC_API_KEY"):
            raise SystemExit(
                "--auth api-key demandé mais ANTHROPIC_API_KEY est absente de l'environnement "
                "du lanceur — exporte-la avant de relancer, ou choisis --auth oauth-copy."
            )
        return explicit

    if env.get("ANTHROPIC_API_KEY"):
        return "api-key"

    print(
        "[auth] ANTHROPIC_API_KEY absente de l'environnement : repli sur oauth-copy — "
        "ATTENTION, les jetons OAuth de Claude Code sont rafraîchis par rotation ; une "
        "copie qui rafraîchit son jeton peut invalider la session interactive de "
        "l'opérateur ET les autres copies en cours (observé lots E, F, H, J — voir "
        "docs/bench-three-arms.md §3). Exporte ANTHROPIC_API_KEY ou passe --auth api-key "
        "pour l'éviter.",
        file=sys.stderr,
    )
    return "oauth-copy"


def _expected_activation_directive_template() -> str:
    """Gabarit de directive attendu, lu depuis le CODE SOURCE de ce worktree.

    Lecture de texte, jamais un ``import grimoire`` : ce module tourne
    volontairement contre n'importe quelle version installée du kit (voir
    le docstring de ce fichier) — importer ``grimoire.core.claude_activation``
    depuis le processus qui exécute *ce script* comparerait le gabarit servi
    à un package qui peut ne même pas être celui que ``--grimoire-bin``
    désigne, ou pas être installé du tout dans l'environnement du harnais.

    Extrait ``_DIRECTIVE_TEMPLATE`` de
    ``src/grimoire/core/claude_activation.py`` — le fichier réellement
    présent dans CE worktree, celui que ``--grimoire-bin`` est censé servir.
    Une première version de ce garde-fou comparait au contraire le gabarit
    servi au rendu de ``activation_directive_template()`` importé depuis
    l'interpréteur SIBLING de *grimoire_bin* : un binaire installé ailleurs
    (l'incident lot H) sert un gabarit *cohérent avec son propre code*, donc
    cette comparaison ne pouvait jamais échouer — corrigé avant tout usage
    réel (vérifié : contre le grimoire 3.55.0 de la Forge, cette première
    version ne détectait rien).
    """
    repo_root = Path(__file__).resolve().parents[2]
    source_path = repo_root / "src" / "grimoire" / "core" / "claude_activation.py"
    if not source_path.is_file():
        raise RuntimeError(
            f"garde-fou lot H : {source_path} introuvable — ce script doit vivre sous "
            "scripts/bench/ d'un worktree grimoire-kit complet pour que la vérification "
            "du gabarit fonctionne."
        )
    source_text = source_path.read_text(encoding="utf-8")
    match = re.search(r'_DIRECTIVE_TEMPLATE = """(.*?)"""\n', source_text, re.DOTALL)
    if not match:
        raise RuntimeError(
            f"garde-fou lot H : `_DIRECTIVE_TEMPLATE` introuvable dans {source_path} — "
            "le gabarit attendu ne peut pas être déterminé."
        )
    return match.group(1)


def verify_grimoire_binary_matches_template(grimoire_bin: str, *, check_dir: Path) -> None:
    """Garde-fou lot H (#582) : refuse toute dépense modèle si *grimoire_bin*
    ne sert pas le gabarit de directive du CODE SOURCE de ce worktree.

    Provisionne un dépôt JETABLE ET UNIQUE sous *check_dir* (``git init`` +
    ``<grimoire_bin> init`` + ``host sync --host claude`` + ``standard init``
    — c'est ``standard init`` qui écrit ``.claude/activation-context.md``,
    vérifié en isolation : ``init``/``host sync`` seuls installent les
    agents/commandes/hooks Claude Code mais jamais ce fichier — ``HOME``
    isolé), lit le ``.claude/activation-context.md`` qui en résulte, et le
    compare CARACTÈRE PAR CARACTÈRE à :func:`_expected_activation_directive_template`.

    Lot J (#582) : *check_dir* n'est plus le dépôt lui-même mais son
    RÉPERTOIRE PARENT — chaque appel provisionne un sous-répertoire jetable
    distinct via :func:`tempfile.mkdtemp`, supprimé dans un ``finally`` que la
    vérification réussisse, échoue, ou lève. Avant ce lot, *check_dir* était
    le dépôt lui-même, à un chemin FIXE
    (``workspace / "_grimoire_bin_check"``) : au second appel sur le même
    workspace (ex. un ``--resume`` après un incident), ``grimoire init``
    retombait sur un dépôt déjà initialisé et refusait (« Use --force to
    overwrite »), faisant échouer la garde elle-même — jamais rejouable sans
    intervention manuelle pour supprimer l'ancien dépôt.

    Appelée une seule fois, avant le tout premier appel ``claude -p`` d'une
    campagne ``--pilot``/``--full`` (jamais pour ``--dry-run``/``--report-only``,
    qui ne dépensent rien). Lève ``RuntimeError`` avec un message actionnable
    au premier écart — 0 $ dépensé.
    """
    version = _run([grimoire_bin, "--version"], timeout=30)
    if version.returncode != 0:
        raise RuntimeError(
            f"garde-fou lot H : `{grimoire_bin} --version` a échoué (code "
            f"{version.returncode}) : {version.stdout}{version.stderr}"
        )

    expected_template = _expected_activation_directive_template()

    check_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="_grimoire_bin_check-", dir=check_dir))
    try:
        fake_home = run_dir / "_fake_home"
        (fake_home / ".claude").mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "HOME": str(fake_home),
            "GRIMOIRE_NO_COCKPIT": "1",
            "PATH": f"{Path(grimoire_bin).parent}{os.pathsep}{os.environ.get('PATH', '')}",
        }
        _run(["git", "init", "-q", "."], cwd=run_dir, timeout=30)
        init = _run(
            [grimoire_bin, "init", ".", "--backend", "local", "--no-cockpit"],
            cwd=run_dir,
            env=env,
            timeout=120,
        )
        sync = _run([grimoire_bin, "host", "sync", "--host", "claude"], cwd=run_dir, env=env, timeout=120)
        standard_init = _run([grimoire_bin, "standard", "init", "."], cwd=run_dir, env=env, timeout=120)
        if init.returncode != 0 or sync.returncode != 0 or standard_init.returncode != 0:
            raise RuntimeError(
                "garde-fou lot H : impossible de provisionner le dépôt de vérification "
                f"du gabarit ({grimoire_bin} init/host sync/standard init) : "
                f"{init.stdout}{init.stderr}{sync.stdout}{sync.stderr}"
                f"{standard_init.stdout}{standard_init.stderr}"
            )

        activation_path = run_dir / ".claude" / "activation-context.md"
        if not activation_path.is_file():
            raise RuntimeError(
                f"garde-fou lot H : {activation_path} absent après provisionnement — "
                "vérification du gabarit impossible."
            )
        served_template = activation_path.read_text(encoding="utf-8")

        if served_template != expected_template:
            raise RuntimeError(
                "GARDE-FOU LOT H : le binaire grimoire résolu "
                f"({grimoire_bin}) sert un gabarit de directive DIFFÉRENT du code de ce "
                f"worktree — {len(served_template)} caractères servis contre "
                f"{len(expected_template)} attendus. Cause probable : un PATH relatif ou "
                "un `grimoire` d'un autre environnement (voir l'incident lot H, #582 : "
                "27 runs / 44 $ rejoués sur l'ancien gabarit avant que ce garde-fou "
                "n'existe). Arrêt avant tout appel modèle — 0 $ dépensé."
            )
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


#: Motifs qui, dans la sortie d'une commande de test, trahissent une
#: toolchain absente plutôt qu'un test qui échoue légitimement faute de
#: solution — best-effort, en complément du signal fiable (exit 127 /
#: ``FileNotFoundError``), jamais le seul critère.
_TOOLCHAIN_NOT_FOUND_MARKERS: tuple[str, ...] = (
    "command not found",
    "not found",  # ex. `sh: 1: jest: not found`, `bash: cargo: not found`
    "no such file or directory",
)


def _toolchain_failure_reason(returncode: int, stdout: str, stderr: str) -> str | None:
    """``None`` si l'échec éventuel de la commande de test est un échec de
    test légitime (aucune solution écrite) ; une raison actionnable sinon.

    Le code de sortie de la commande de test N'EST PAS, à lui seul, un
    signal de toolchain absente — un test qui échoue faute de solution est
    le fonctionnement normal du banc (lot I, #582 : ``code de sortie non
    pertinent``). Seuls comptent : l'exit 127 conventionnel (« commande
    introuvable » pour un shell POSIX) et un motif explicite de
    :data:`_TOOLCHAIN_NOT_FOUND_MARKERS` dans la sortie (ex. ``jest: not
    found`` quand les dépendances JS n'ont pas pu être installées).
    """
    if returncode == 127:
        combined = (stdout + stderr).strip()
        return f"exit 127 (commande introuvable) — sortie : {combined[-300:]!r}"
    combined_lower = f"{stdout}\n{stderr}".lower()
    for marker in _TOOLCHAIN_NOT_FOUND_MARKERS:
        if marker in combined_lower:
            return f"motif « {marker} » détecté dans la sortie de la commande de test"
    return None


def verify_agent_toolchain_environment(
    tasks: Sequence[TaskMeta],
    *,
    workspace: Path,
    grimoire_bin: str,
    go_bin: Path | None,
    npm_cache_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Garde-fou lot I (#582) : AVANT le premier appel ``claude -p`` d'une
    campagne, pour CHAQUE langue présente dans *tasks*, prouve que la
    commande de test résolue par ``grimoire needs resolve`` (celle-là même
    que ``grimoire standard gate check --strict`` exécute dans la session de
    l'agent, lot G1) s'exécute réellement dans :func:`run_environment` —
    l'environnement transmis à l'agent, pas un environnement séparé propre
    au harnais.

    Le code de sortie de cette commande n'est PAS vérifié : les tests
    peuvent légitimement échouer faute de solution écrite. Seule l'ABSENCE
    d'un échec de toolchain (commande introuvable, exit 127, motif « ... not
    found ») est exigée — voir :func:`_toolchain_failure_reason`. Lève
    ``RuntimeError`` avec un message actionnable au premier échec de ce
    type : 0 $ dépensé, exactement comme
    :func:`verify_grimoire_binary_matches_template`.

    Un dépôt de vérification est provisionné par langue (la première tâche
    de cette langue dans *tasks*, tests cachés compris — jamais donnés à un
    agent réel, uniquement à cette vérification interne du harnais) sous
    ``workspace/_agent_toolchain_check/<langue>``, réutilisé s'il existe déjà
    (pas de reconstruction à chaque ``--resume``).
    """
    first_task_by_language: dict[str, TaskMeta] = {}
    for task in tasks:
        first_task_by_language.setdefault(task.language, task)

    report: dict[str, dict[str, Any]] = {}
    for language, task in sorted(first_task_by_language.items()):
        check_dir = workspace / "_agent_toolchain_check" / language
        if not (check_dir / "TASK.md").is_file():
            prepare_task_repo(task, check_dir, include_tests=True)

        home = check_dir / "_home"
        ensure_isolated_home(home)
        env = run_environment(
            check_dir,
            "kit-gov",
            home=home,
            grimoire_bin=grimoire_bin,
            workspace=workspace,
            go_bin=go_bin,
            npm_cache_dir=npm_cache_dir,
        )

        if language == "javascript":
            install = install_test_dependencies(check_dir, npm_cache_dir=npm_cache_dir)
            if install is not None and not install.get("ok", True):
                raise RuntimeError(
                    "GARDE-FOU LOT I : `npm install` a échoué dans le dépôt de vérification "
                    f"toolchain javascript ({check_dir}) — la session de l'agent affronterait "
                    f"le même échec sur toute tâche javascript : "
                    f"{install.get('stderr') or install.get('error')}"
                )

        needs = _run(
            [grimoire_bin, "needs", "resolve", "--project-root", ".", "--json"],
            cwd=check_dir,
            env=env,
            timeout=60,
        )
        if needs.returncode != 0:
            raise RuntimeError(
                "GARDE-FOU LOT I : `grimoire needs resolve` a échoué dans le dépôt de "
                f"vérification toolchain {language} ({check_dir}) : {needs.stdout}{needs.stderr}"
            )
        try:
            resolved = json.loads(needs.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"GARDE-FOU LOT I : sortie non JSON de `grimoire needs resolve` ({check_dir}) : "
                f"{needs.stdout[-500:]}"
            ) from exc

        command = (resolved.get("test-runner") or {}).get("command")
        if not command:
            report[language] = {
                "command": None,
                "returncode": None,
                "ok": True,
                "skipped": "test-runner non résolu pour cette tâche (voir docs/bench-three-arms.md §4)",
            }
            continue

        argv = shlex.split(command)
        try:
            result = _run(argv, cwd=check_dir, env=env, timeout=DEFAULT_TEST_TIMEOUT_S)
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"GARDE-FOU LOT I : `{command}` introuvable dans l'environnement de l'agent "
                f"pour {language} ({exc}) — la session de l'agent échouerait de la même façon "
                "sur toute tâche de cette langue. Arrêt avant tout appel modèle — 0 $ dépensé."
            ) from exc

        failure = _toolchain_failure_reason(result.returncode, result.stdout, result.stderr)
        if failure is not None:
            raise RuntimeError(
                f"GARDE-FOU LOT I : `{command}` a échoué en toolchain pour {language} "
                f"({check_dir}) — {failure}. La session de l'agent échouerait de la même "
                "façon (même environnement, voir `run_environment`). Arrêt avant tout appel "
                f"modèle — 0 $ dépensé.\n{result.stdout[-1000:]}{result.stderr[-1000:]}"
            )

        report[language] = {"command": command, "returncode": result.returncode, "ok": True}

    return report


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


def find_leaked_api_keys(root: Path, *, api_key: str | None) -> list[Path]:
    """Garde de fin de campagne, mode ``api-key`` (lot J, #582) : la clé
    ``ANTHROPIC_API_KEY`` transmise à ``claude -p --bare`` ne doit jamais
    avoir été ÉCRITE quelque part — ni dans un ``HOME`` isolé (``root/homes``:
    un fichier de config quelconque qui la citerait en clair), ni dans un
    journal de run (``root/tasks/**/*.stream.jsonl``, la transcription
    ``stream-json`` de chaque session).

    Recherche du littéral de la clé (jamais affichée ni journalisée par ce
    script : seuls les CHEMINS des fichiers concernés sont retournés, jamais
    leur contenu). ``[]`` si *api_key* est ``None`` (mode ``oauth-copy``, la
    question ne se pose pas ici — voir :func:`find_leftover_credentials`) ou
    si rien n'a été trouvé.

    Balayage borné à ces deux emplacements (jamais tout ``root`` : les
    dépôts de tâche eux-mêmes peuvent contenir des Go/Rust/JS build
    artifacts volumineux, hors périmètre d'un secret du harnais).
    """
    if not api_key:
        return []
    needle = api_key.encode("utf-8")
    candidates: list[Path] = []
    homes_dir = root / "homes"
    if homes_dir.is_dir():
        candidates.extend(p for p in homes_dir.rglob("*") if p.is_file())
    tasks_dir = root / "tasks"
    if tasks_dir.is_dir():
        candidates.extend(tasks_dir.rglob("*.stream.jsonl"))
    hits: list[Path] = []
    for path in candidates:
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if needle in content:
            hits.append(path)
    return sorted(hits)


def install_test_dependencies(
    task_dir: Path, *, npm_cache_dir: Path, timeout: int = 120
) -> dict[str, Any] | None:
    """Installe les dépendances de test JS de *task_dir* si un manifeste les demande.

    Lot H (#582) : les lots E/F ont mesuré que l'agent, DANS sa propre
    session, ne pouvait jamais faire aboutir un `npm test`/`npx jest` sur les
    tâches JavaScript — `node_modules/` n'existait pas encore à ce moment-là,
    seule la vérification finale du harnais (:func:`run_hidden_tests`,
    exécutée APRÈS la fin du run) installait les dépendances. Résultat :
    `exit 127` systématique côté agent (« jest absent du bac à sable »),
    quel que soit le bras — un confondu de méthode, pas une différence entre
    bras. Cette fonction est appelée par ``setup_arm_*`` de TOUS les bras
    (nu compris), avant le lancement de l'agent, pour que la comparaison
    porte sur la gouvernance, pas sur un outillage de test manquant.

    Best-effort et jamais bloquant : un `npm install` qui échoue (réseau
    coupé, registre indisponible, timeout) est journalisé dans le résultat
    du setup de ce run et compté comme tel dans le rapport
    (``RunRecord.test_deps_install_ok``), sans jamais lever d'exception — la
    campagne continue avec un `node_modules` absent, comme avant ce lot.

    Le cache npm est forcé sous *npm_cache_dir* (un sous-dossier du
    workspace du banc, partagé entre bras et tâches pour éviter de
    retélécharger jest/babel à chaque run), jamais dans le `HOME` réel de
    l'opérateur ni dans l'un des `HOME` isolés par bras.

    ``None`` si *task_dir* ne contient aucun ``package.json`` (rien à
    installer, la tâche n'est pas JavaScript).
    """
    if not (task_dir / "package.json").is_file():
        return None
    npm_cache_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "npm_config_cache": str(npm_cache_dir)}
    try:
        result = _run(
            ["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"],
            cwd=task_dir,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"attempted": True, "ok": False, "returncode": None, "error": "timeout npm install"}
    return {
        "attempted": True,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout[-1000:],
        "stderr": result.stderr[-1000:],
    }


def setup_arm_nu(task_dir: Path, *, npm_cache_dir: Path | None = None) -> dict[str, Any]:
    """Bras nu : rien à ajouter, sauf les dépendances de test JS (équité, lot H, #582)."""
    result: dict[str, Any] = {"arm": "nu", "added": []}
    if npm_cache_dir is not None:
        test_deps = install_test_dependencies(task_dir, npm_cache_dir=npm_cache_dir)
        if test_deps is not None:
            result["test_deps_install"] = test_deps
    return result


def setup_arm_ecc(
    task_dir: Path,
    *,
    ecc_repo: Path,
    ecc_home: Path,
    timeout: int = 120,
    npm_cache_dir: Path | None = None,
) -> dict[str, Any]:
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
    result = {
        "arm": "ecc",
        "added": [".claude/settings.local.json"],
        "marketplace_add_rc": add.returncode,
        "plugin_install_rc": install.returncode,
        "marketplace_add_stdout": add.stdout[-2000:],
        "plugin_install_stdout": install.stdout[-2000:],
    }
    if npm_cache_dir is not None:
        test_deps = install_test_dependencies(task_dir, npm_cache_dir=npm_cache_dir)
        if test_deps is not None:
            result["test_deps_install"] = test_deps
    return result


#: Board synthétique pour le bras ``kit-gov`` : une seule tâche, posée
#: directement en ``in_progress``, à l'id de la tâche du banc (barres
#: obliques remplacées par des doubles underscores — voir
#: :func:`_governed_task_id`). ``grimoire task migrate-standard`` (ADR-007)
#: l'importe ensuite dans le Mission Ledger en préservant cet id exact et en
#: marchant la machine à états jusqu'à ``RUNNING`` (``_walk_to_state``,
#: `src/grimoire/missions/task_unification.py`) — sans passer par
#: ``TaskService.transition``, donc sans jamais buter sur un gate de preuve :
#: c'est un projet qu'on simule comme réellement enrôlé, pas une tâche qu'on
#: fait mentir sur son état d'avancement.
_SYNTHETIC_BOARD_TEMPLATE = """$schema: grimoire-agentic-standard-task-board/v1
metadata:
  project: grimoire-bench-kit-gov
  generated_by: scripts/bench/three_arms.py (bras kit-gov, issue #582 lot F)
  purpose: Board synthétique posé avant `grimoire task migrate-standard`.
states:
- proposed
- ready
- in_progress
- blocked
- review
- accepted
- released
- archived
tasks:
- task_id: {task_id}
  title: "Bench task: {task_id}"
  status: in_progress
  priority: medium
  owner: bench
  acceptance_criteria:
  - tests cachés verts
"""


def _governed_task_id(task_id: str) -> str:
    """``task_id`` du banc (``"<langue>/<slug>"``), rendu valide pour Grimoire.

    ``core/standard_generation.py::TASK_ID_PATTERN`` refuse ``/`` — un board
    ``in_progress`` avec un id invalide ferait lever ``normalize_task_id``
    dans ``resolve_active_task`` et casserait le hook ``SessionStart`` de
    CHAQUE run kit-gov. Même convention que le harnais utilise déjà pour les
    chemins de dépôt de tâche (``task_id.replace("/", "__")``, voir
    ``_run_one``) : un seul système de remplacement, pas deux.
    """
    return task_id.replace("/", "__")


def setup_arm_kit_gov(
    task_dir: Path,
    *,
    kit_home: Path,
    task_id: str,
    grimoire_bin: str,
    timeout: int = 180,
    npm_cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Bras ``kit-gov`` : le bras ``kit``, PLUS un projet réellement enrôlé au standard.

    ``setup_arm_kit()`` ne lance jamais ``grimoire standard init`` : le rejeu
    du lot E (docs/bench/rejeu-lot-e-2026-09-17.md §3) a constaté que
    ``_is_governed()`` (lot A, ``src/grimoire/hosts/decisions/activation.py``)
    renvoie donc toujours ``False`` sur le bras ``kit`` — le lot B (``gate
    run-tests``) n'est jamais exercé sur ce banc, quel que soit le nombre de
    rejeux. Ce bras ferme cette lacune de méthode, sans toucher au bras
    ``kit`` existant (comparaison directe préservée) :

    1. Le provisionnement du bras ``kit`` (``grimoire init`` + ``host sync``).
    2. ``grimoire standard init .`` — profil par défaut (``starter``, celui
       qu'un développeur seul obtient sans option ; ``--profile``/``--needs``
       non fournis).
    3. Une tâche de board à l'id de la tâche du banc, posée directement en
       ``in_progress`` (:data:`_SYNTHETIC_BOARD_TEMPLATE`), puis importée
       dans le Mission Ledger par ``grimoire task migrate-standard`` (ADR-007,
       Grimoire-kit#587/#588) — seule voie CLI qui préserve un id exact
       plutôt que d'en dériver un du titre (``grimoire task add`` n'expose
       aucune option ``--task-id``).

    Vérifié en isolation (dépôt jetable sous ``_scratch/``, non versionné) :
    après ces trois étapes, ``grimoire standard activation-context`` rend la
    directive complète avec ``gate run-tests --task-id <id>``,
    ``_is_governed()`` vaut ``True`` et ``active_task_id()`` résout bien cet
    id — jamais ``bootstrap``.
    """
    governed_id = _governed_task_id(task_id)
    kit_result = setup_arm_kit(
        task_dir, kit_home=kit_home, grimoire_bin=grimoire_bin, timeout=timeout, npm_cache_dir=npm_cache_dir
    )
    env = {
        **os.environ,
        "HOME": str(kit_home),
        "GRIMOIRE_NO_COCKPIT": "1",
        # Lot H (#582) : PATH ABSOLU en tête, jamais une entrée relative — un
        # sous-processus dont le `cwd` est un dépôt de tâche jetable résout
        # une entrée PATH relative PAR RAPPORT À CE `cwd`, pas au répertoire
        # de lancement du harnais (cause de l'incident du 2026-09-18, voir
        # `resolve_grimoire_bin`).
        "PATH": f"{Path(grimoire_bin).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    }

    standard_init = _run(
        [grimoire_bin, "standard", "init", "."],
        cwd=task_dir,
        env=env,
        timeout=timeout,
    )

    board_path = task_dir / "_grimoire" / "standard" / "task-board.yaml"
    board_path.parent.mkdir(parents=True, exist_ok=True)
    board_path.write_text(_SYNTHETIC_BOARD_TEMPLATE.format(task_id=governed_id), encoding="utf-8")

    migrate = _run(
        [grimoire_bin, "task", "migrate-standard", "."],
        cwd=task_dir,
        env=env,
        timeout=timeout,
    )

    result = {
        "arm": "kit-gov",
        "added": kit_result.get("added", []),
        "init_rc": kit_result.get("init_rc"),
        "sync_rc": kit_result.get("sync_rc"),
        "standard_init_rc": standard_init.returncode,
        "standard_init_profile": "starter",
        "migrate_rc": migrate.returncode,
        "governed_task_id": governed_id,
        "standard_init_stdout": standard_init.stdout[-2000:],
        "migrate_stdout": migrate.stdout[-2000:],
    }
    if kit_result.get("test_deps_install") is not None:
        result["test_deps_install"] = kit_result["test_deps_install"]
    return result


def setup_arm_kit(
    task_dir: Path,
    *,
    kit_home: Path,
    grimoire_bin: str,
    timeout: int = 180,
    npm_cache_dir: Path | None = None,
) -> dict[str, Any]:
    """``grimoire init --backend local --no-cockpit`` + ``host sync --host claude``."""
    env = {
        **os.environ,
        "HOME": str(kit_home),
        "GRIMOIRE_NO_COCKPIT": "1",
        # Lot H (#582) : PATH absolu en tête — voir `resolve_grimoire_bin`.
        "PATH": f"{Path(grimoire_bin).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    before = {p.relative_to(task_dir).as_posix() for p in task_dir.rglob("*") if p.is_file()}

    init = _run(
        [grimoire_bin, "init", ".", "--backend", "local", "--no-cockpit"],
        cwd=task_dir,
        env=env,
        timeout=timeout,
    )
    sync = _run(
        [grimoire_bin, "host", "sync", "--host", "claude"],
        cwd=task_dir,
        env=env,
        timeout=timeout,
    )

    after = {p.relative_to(task_dir).as_posix() for p in task_dir.rglob("*") if p.is_file()}
    added = sorted(after - before)
    result = {
        "arm": "kit",
        "added": added,
        "init_rc": init.returncode,
        "sync_rc": sync.returncode,
        "init_stdout": init.stdout[-2000:],
        "sync_stdout": sync.stdout[-2000:],
    }
    if npm_cache_dir is not None:
        test_deps = install_test_dependencies(task_dir, npm_cache_dir=npm_cache_dir)
        if test_deps is not None:
            result["test_deps_install"] = test_deps
    return result


# ── Exécution de l'agent (LLM, non testée unitairement) ─────────────────────


def run_claude_headless(
    task_dir: Path,
    prompt: str,
    *,
    home: Path,
    env: dict[str, str] | None = None,
    timeout_s: int = DEFAULT_RUN_TIMEOUT_S,
    repeat_threshold: int = DEFAULT_LOOP_REPEAT_THRESHOLD,
    poll_interval: float = DEFAULT_POLL_INTERVAL_S,
    log_path: Path | None = None,
    auth_mode: str = "oauth-copy",
) -> RunOutcome:
    """Lance ``claude -p`` en tête sans tête, avec garde-fou timeout + boucle.

    Permissions limitées au dépôt de tâche via le cwd (``bypassPermissions``
    n'est acceptable ici que parce que chaque dépôt de tâche est jetable et
    isolé sous le workspace du banc — voir docs/bench-three-arms.md).

    *env* (lot I, #582) : l'environnement complet de la session, construit
    UNE SEULE FOIS par :func:`run_environment` et transmis tel quel — jamais
    recalculé ici. ``None`` (défaut, préservé pour les appelants existants
    qui ne fournissent que *home*) replie sur l'ancien comportement
    (``os.environ`` + ``HOME`` isolé seul), sans la toolchain Go/Rust/npm
    unifiée.

    *auth_mode* (lot J, #582) : ``"oauth-copy"`` (défaut, comportement
    historique inchangé) ou ``"api-key"`` — voir :func:`resolve_auth_mode`.
    En mode ``"api-key"``, ``--bare`` est ajouté à la commande : d'après la
    documentation Claude Code (``code.claude.com/docs/en/headless.md``,
    « Start faster with bare mode »), ce mode ignore explicitement les
    identifiants OAuth et le trousseau système, et exige
    ``ANTHROPIC_API_KEY`` (ou un ``apiKeyHelper``) — déjà présente dans
    *env* puisque :func:`run_environment` part de ``os.environ``. Aucun
    identifiant n'est donc jamais copié pour ce mode (voir ``_run_one``).
    """
    log_path = log_path or (task_dir.parent / f"{task_dir.name}.stream.jsonl")
    effective_env = dict(env) if env is not None else {**os.environ, "HOME": str(home)}
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
    if auth_mode == "api-key":
        cmd.append("--bare")

    outcome = RunOutcome()
    start = time.monotonic()
    seen_commands: list[str] = []

    with open(log_path, "wb") as logf:
        proc = subprocess.Popen(cmd, cwd=task_dir, env=effective_env, stdout=logf, stderr=subprocess.STDOUT)
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
    outcome.toolchain_friction_bash_calls = count_toolchain_friction_bash_calls(seen_commands)

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
        outcome.cache_read_input_tokens = int(usage.get("cache_read_input_tokens") or 0)
        outcome.cache_creation_input_tokens = int(usage.get("cache_creation_input_tokens") or 0)
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
    env: dict[str, str] | None = None,
    timeout_s: int = DEFAULT_TEST_TIMEOUT_S,
) -> tuple[bool, str]:
    """Copie les tests cachés dans le dépôt de tâche puis les exécute.

    *env* (lot I, #582) : le MÊME environnement que celui transmis à la
    session de l'agent (voir :func:`run_environment`, calculé une seule fois
    par ``_run_one``) — ``None`` (défaut) replie sur l'environnement du
    processus harnais, pour les appelants existants (tests, ``--dry-run``)
    qui n'en fournissent pas.
    """
    for rel_str in task.test_files:
        src = hidden_dir / rel_str
        if not src.is_file():
            return (False, f"fichier de test absent : {rel_str}")
        target = task_dir / rel_str
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)

    if task.language == "javascript":
        install = _run(
            ["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"],
            cwd=task_dir,
            env=env,
            timeout=timeout_s,
        )
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
        result = _run(cmd, cwd=task_dir, env=env, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return (False, "timeout des tests")
    success = detect_success(result.returncode)
    return (success, (result.stdout[-2000:] + result.stderr[-2000:]))


_BUILD_ARTIFACT_DIRS = ("target", "node_modules")


def cleanup_build_artifacts(task_dir: Path) -> None:
    """Delete Rust/Go ``target/`` and JS ``node_modules/`` left under *task_dir*.

    Called after every single run, not just at the end of a campaign: over
    20 tâches x 3 bras x k rejeux, the build artifacts of a Rust or JS
    exercise add up fast enough to exhaust the workspace's disk mid-campaign
    — exactly the failure :func:`disk_guard_ok` exists to catch before it
    happens. Best-effort by construction: a tree partially torn down by a
    killed subprocess must never turn a benign cleanup into a crashed run.
    """
    for dirname in _BUILD_ARTIFACT_DIRS:
        for candidate in task_dir.rglob(dirname):
            if candidate.is_dir():
                shutil.rmtree(candidate, ignore_errors=True)


def disk_guard_ok(workspace: Path, *, min_free_gb: float = DISK_GUARD_MIN_FREE_GB) -> bool:
    """True while *workspace* still has enough free disk to launch another run.

    Checked before every run in the main campaign loop (issue Grimoire-kit#551
    #552, harnais lot A) — a campaign that fills the disk mid-run used to
    crash a ``claude -p`` subprocess partway through instead of stopping
    cleanly with whatever it had already measured.
    """
    usage = shutil.disk_usage(workspace)
    free_gb = usage.free / (1024**3)
    return free_gb >= min_free_gb


# ── Rapport ──────────────────────────────────────────────────────────────


def _median(values: Sequence[float]) -> float:
    return statistics.median(values) if values else 0.0


def build_report(
    records: Sequence[RunRecord],
    *,
    total_tasks: int,
    expected_cost: dict[str, Any] | None,
    seed: int,
    rerun_arms: Sequence[str] | None = None,
    label: str | None = None,
    toolchain_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construit la structure de rapport (utilisée pour le .md et le .json).

    ``rerun_arms`` (lot E, #582) : les bras effectivement rejoués par CETTE
    exécution (``--arms``). ``None`` signifie « pas de restriction connue »
    (mode par défaut, ou ``--report-only`` sans ``--arms``) : aucun bras
    n'est alors marqué comme repris. Un bras présent dans ``records`` mais
    absent de ``rerun_arms`` est un bras repris tel quel via ``--resume``,
    signalé dans le rapport par :func:`carried_over_label`.

    ``toolchain_check`` (lot I, #582) : le résultat de
    :func:`verify_agent_toolchain_environment`, persisté par ``main()`` sous
    ``state/toolchain_check.json`` — ``None`` pour un rapport reconstruit
    sans ce fichier (campagnes antérieures à ce lot).
    """
    by_arm: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        by_arm[record.arm].append(record)

    carried_over_notes: dict[str, str] = {}
    if rerun_arms is not None:
        for arm in ARMS:
            if arm not in rerun_arms and by_arm.get(arm):
                carried_over_notes[arm] = carried_over_label(records, arm)

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
            # Lot H (#582) : combien de runs de ce bras avaient un manifeste
            # ``package.json`` (donc une tentative d'installation JS) et
            # combien ont abouti — voir ``RunRecord.test_deps_install_ok``.
            "test_deps_install_attempted": sum(1 for r in arm_records if r.test_deps_install_ok is not None),
            "test_deps_install_ok": sum(1 for r in arm_records if r.test_deps_install_ok),
            # Lot I (#582) : combien d'appels Bash de ce bras relèvent de la
            # friction de découverte de toolchain — voir
            # ``count_toolchain_friction_bash_calls``. L'indicateur qui décide
            # si ``run_environment()`` a réellement retiré ce poste.
            "toolchain_friction_bash_calls_total": sum(r.toolchain_friction_bash_calls for r in arm_records),
            "toolchain_friction_bash_calls_median": _median(
                [float(r.toolchain_friction_bash_calls) for r in arm_records]
            ),
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

    def _governed_run_rows(arm: str) -> list[dict[str, Any]]:
        return [
            {
                "task_id": r.task_id,
                "run_index": r.run_index,
                "num_turns": r.num_turns,
                "total_cost_usd": r.total_cost_usd,
                "wall_seconds": r.wall_seconds,
                "test_run_evidence": r.kit_test_run_evidence,
                # Lot H (#582) : ``True``/``False`` si une tâche JS a demandé
                # une installation de dépendances de test pour ce run précis,
                # ``None`` sinon (tâche non-JS).
                "test_deps_install_ok": r.test_deps_install_ok,
                # Lot I (#582) : voir ``per_arm[...].toolchain_friction_bash_calls_total``.
                "toolchain_friction_bash_calls": r.toolchain_friction_bash_calls,
            }
            for r in sorted(by_arm.get(arm, []), key=lambda r: (r.task_id, r.run_index))
        ]

    kit_runs = _governed_run_rows("kit")
    # ``kit-gov`` (lot F, #582) : même détail par run que ``kit``, clé
    # séparée pour garder ``kit_runs``/``kit_gov_runs`` indépendants dans le
    # rapport, même si leur forme évolue ensemble (lot H, #582).
    kit_gov_runs = _governed_run_rows("kit-gov")

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "seed": seed,
        "label": label,
        "total_tasks": total_tasks,
        "tasks_run": len(task_ids),
        "k": K_REPLAY,
        "expected_cost": expected_cost,
        "per_arm": per_arm,
        "by_task": by_task_arm,
        "kit_runs": kit_runs,
        "kit_gov_runs": kit_gov_runs,
        "carried_over_notes": carried_over_notes,
        "agent_toolchain_check": toolchain_check,
        # Lot J (#582) : mode(s) d'authentification effectivement utilisés
        # par les runs de CE rapport — voir ``RunRecord.auth_mode``. Vide
        # pour un rapport reconstruit depuis des lignes antérieures à ce lot
        # (``auth_mode`` alors toujours ``None``), jamais reconstruit a
        # posteriori.
        "auth_modes": sorted({r.auth_mode for r in records if r.auth_mode}),
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

    if report.get("label"):
        lines.append(f"Étiquette : **{report['label']}**.")
        lines.append("")

    auth_modes = report.get("auth_modes") or []
    if auth_modes:
        lines.append(f"Authentification (lot J, #582) : **{', '.join(auth_modes)}** — voir docs/bench-three-arms.md §3.")
        lines.append("")

    carried_over_notes = report.get("carried_over_notes") or {}
    for arm in ARMS:
        if arm in carried_over_notes:
            lines.append(f"> Bras `{arm}` repris de la campagne du {carried_over_notes[arm]} (non rejoué dans cette exécution).")
    if carried_over_notes:
        lines.append("")

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
    lines.append("| Tâche | Langue | " + " | ".join(ARMS) + " |")
    lines.append("|---|---|" + "---|" * len(ARMS))
    for task_id, row in sorted(report["by_task"].items()):
        cells = []
        for arm in ARMS:
            arm_row = row.get(arm, {})
            cells.append(f"{arm_row.get('successes', 0)}/{arm_row.get('runs', 0)}")
        lines.append(f"| {task_id} | {row.get('language', '?')} | " + " | ".join(cells) + " |")
    lines.append("")

    def _render_governed_detail(title: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        lines.append(f"## Détail par run — bras {title}")
        lines.append("")
        lines.append(
            "| Tâche | Run | Tours | Coût | Temps (s) | `test-run.json` (lot B) | "
            "Dépendances JS installées (lot H) | Friction toolchain (lot I) |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for row in rows:
            evidence = row.get("test_run_evidence")
            evidence_cell = "oui" if evidence else ("non" if evidence is False else "?")
            deps = row.get("test_deps_install_ok")
            deps_cell = "oui" if deps else ("non" if deps is False else "n/a")
            friction = row.get("toolchain_friction_bash_calls", 0)
            lines.append(
                f"| {row['task_id']} | {row['run_index']} | {row['num_turns']} | "
                f"${row['total_cost_usd']:.3f} | {row['wall_seconds']:.0f} | {evidence_cell} | {deps_cell} | "
                f"{friction} |"
            )
        lines.append("")

    _render_governed_detail("kit", report.get("kit_runs") or [])
    # ``kit-gov`` (lot F, #582) : seul bras où ce tableau devrait, une fois le
    # lot B réellement exercé, montrer une colonne « lot B » majoritairement
    # à « oui ».
    _render_governed_detail("kit-gov", report.get("kit_gov_runs") or [])

    kit_gov_stats = report["per_arm"].get("kit-gov")
    if kit_gov_stats and report.get("kit_gov_runs"):
        lines.append(
            "Friction de découverte de toolchain (lot I, #582) — bras `kit-gov` : "
            f"**{kit_gov_stats['toolchain_friction_bash_calls_total']}** appel(s) Bash "
            "`which`/`command -v`/`find / -name`/`rustup`/`npm install` au total sur "
            f"{kit_gov_stats['n_runs']} run(s), médiane "
            f"{kit_gov_stats['toolchain_friction_bash_calls_median']:.1f} par run — voir "
            "`count_toolchain_friction_bash_calls`. Indicateur destiné à mesurer l'effet de "
            "`run_environment()` : il doit baisser une fois que l'agent reçoit la même "
            "toolchain que la vérification finale du harnais."
        )
        lines.append("")

    toolchain_check = report.get("agent_toolchain_check")
    if toolchain_check:
        lines.append("## Environnement de l'agent (lot I, #582)")
        lines.append("")
        lines.append(
            "Vérifié avant le tout premier appel `claude -p` de cette campagne "
            "(`verify_agent_toolchain_environment`) : pour chaque langue de la sélection, "
            "la commande de test résolue par `grimoire needs resolve` a pu s'exécuter dans "
            "le MÊME environnement (`run_environment()`) que celui transmis à la session de "
            "l'agent — code de sortie non pertinent (un test peut légitimement échouer faute "
            "de solution), seule l'absence d'un échec de toolchain (commande introuvable, "
            "exit 127, motif « ... not found ») est exigée."
        )
        lines.append("")
        lines.append("| Langue | Commande | Code de sortie | Résultat |")
        lines.append("|---|---|---|---|")
        for language in sorted(toolchain_check):
            entry = toolchain_check[language]
            command = entry.get("command") or "(non résolue)"
            returncode = entry.get("returncode")
            returncode_cell = str(returncode) if returncode is not None else "—"
            result_cell = entry.get("skipped") or ("toolchain disponible" if entry.get("ok") else "ÉCHEC")
            lines.append(f"| {language} | `{command}` | {returncode_cell} | {result_cell} |")
        lines.append("")

    lines.append("## Dépendances de test installées avant l'agent (lot H, #582)")
    lines.append("")
    lines.append(
        "Manifestes `package.json` détectés dans le dépôt de tâche et "
        "installation tentée (``npm install``) par `setup_arm_*`, AVANT le "
        "lancement de l'agent — voir `install_test_dependencies`. Objectif : "
        "que `npm test`/`npx jest` puisse aboutir dans la session de l'agent "
        "elle-même, sur tous les bras également (avant ce lot, seule la "
        "vérification finale du harnais installait ces dépendances, jamais "
        "l'agent)."
    )
    lines.append("")
    lines.append("| Bras | Manifestes détectés | Installations réussies |")
    lines.append("|---|---|---|")
    for arm in ARMS:
        a = report["per_arm"].get(arm)
        if not a:
            continue
        lines.append(f"| {arm} | {a['test_deps_install_attempted']} | {a['test_deps_install_ok']} |")
    lines.append("")

    return "\n".join(lines) + "\n"


def write_report(
    records: Sequence[RunRecord],
    out_dir: Path,
    *,
    total_tasks: int,
    expected_cost: dict[str, Any] | None,
    seed: int,
    rerun_arms: Sequence[str] | None = None,
    label: str | None = None,
    toolchain_check: dict[str, Any] | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = build_report(
        records,
        total_tasks=total_tasks,
        expected_cost=expected_cost,
        seed=seed,
        rerun_arms=rerun_arms,
        label=label,
        toolchain_check=toolchain_check,
    )
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
    parser.add_argument(
        "--arms",
        type=str,
        default=None,
        help="sous-ensemble de bras à rejouer, ex. 'kit' ou 'nu,ecc,kit' (défaut : les trois) ; "
        "combiné à --resume, les lignes results.jsonl des autres bras restent comptées dans le rapport",
    )
    parser.add_argument("--label", type=str, default=None, help="étiquette libre reprise dans report.md")
    parser.add_argument(
        "--grimoire-bin",
        type=str,
        default=None,
        help="chemin ABSOLU du binaire grimoire à invoquer (défaut : shutil.which('grimoire'), "
        "résolu en absolu) — jamais une entrée PATH relative (incident lot H, #582 : un "
        "PATH=\".venv/bin:$PATH\" retombe, pour un sous-processus dont le cwd est un dépôt de "
        "tâche jetable, sur le grimoire suivant du PATH)",
    )
    parser.add_argument(
        "--auth",
        type=str,
        choices=AUTH_MODES,
        default=None,
        help="mode d'authentification Claude Code (lot J, #582) : 'api-key' (aucune copie "
        "d'identifiant, ANTHROPIC_API_KEY transmise à `claude -p --bare`) ou 'oauth-copy' "
        "(comportement historique, copie temporaire des identifiants OAuth de l'opérateur — "
        "voir docs/bench-three-arms.md §3 pour le risque de rotation partagée) ; défaut : "
        "'api-key' si ANTHROPIC_API_KEY est présente dans l'environnement du lanceur, sinon "
        "'oauth-copy' avec un avertissement",
    )
    args = parser.parse_args(argv)

    try:
        selected_arms = parse_arms(args.arms)
    except ValueError as exc:
        parser.error(str(exc))

    if args.report_only:
        workspace = args.workspace or _default_workspace()
        return _do_report_only(workspace, seed=args.seed, report_dir=args.report_dir, arms=args.arms, label=args.label)

    workspace = args.workspace or _default_workspace()
    workspace.mkdir(parents=True, exist_ok=True)

    grimoire_bin = resolve_grimoire_bin(args.grimoire_bin)

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
        return _do_dry_run(tasks, workspace=workspace, ecc_repo=ecc_repo, homes=homes, grimoire_bin=grimoire_bin)

    # Lot J (#582) : résolu avant tout appel `claude -p`, jamais recalculé
    # par run — voir `resolve_auth_mode`. Lève `SystemExit` si `--auth
    # api-key` est demandé explicitement sans `ANTHROPIC_API_KEY`.
    auth_mode = resolve_auth_mode(args.auth)
    print(f"[auth] mode : {auth_mode}")

    # Garde-fou lot H (#582) : avant tout appel `claude -p` (jamais pour
    # --dry-run/--report-only, qui ne dépensent rien), vérifie que le binaire
    # résolu sert le gabarit de directive de CE worktree. Incident du
    # 2026-09-18 : un PATH relatif a fait tourner 27 runs (44 $) sur un
    # `grimoire` installé ailleurs, servant l'ancien gabarit, sans le moindre
    # message d'erreur — voir `verify_grimoire_binary_matches_template`.
    try:
        verify_grimoire_binary_matches_template(grimoire_bin, check_dir=workspace / "_grimoire_bin_check")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Garde-fous lot I (#582) : le rejeu du lot H (§3, 2026-09-18) a mesuré
    # que la session de l'agent, dans les mêmes runs gouvernés, cherchait à
    # la main une toolchain Go/Rust invisible pour elle (jusqu'à 28 tours) —
    # deux problèmes distincts, vérifiés avant tout appel `claude -p` :
    # (1) Rust/Node doivent être sur le PATH système (jamais provisionnés par
    #     ce harnais, contrairement à Go) ;
    # (2) la commande de test résolue par `grimoire needs resolve` doit
    #     réellement s'exécuter dans `run_environment()` — l'environnement
    #     transmis à l'agent, pas un environnement séparé propre au harnais.
    npm_cache_dir = workspace / "npm-cache"
    languages_present = sorted({t.language for t in tasks})
    ensure_system_toolchains_present(languages_present)
    try:
        toolchain_check = verify_agent_toolchain_environment(
            tasks, workspace=workspace, grimoire_bin=grimoire_bin, go_bin=go_bin, npm_cache_dir=npm_cache_dir
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    (state_dir / "toolchain_check.json").write_text(json.dumps(toolchain_check, indent=2), encoding="utf-8")

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

    disk_exhausted = False
    with open(results_path, "a", encoding="utf-8") as results_f:
        for task in selected:
            if disk_exhausted:
                break
            order = [arm for arm in select_run_order(ARMS, task_id=task.task_id, seed=args.seed) if arm in selected_arms]
            for arm in order:
                if disk_exhausted:
                    break
                for run_index in range(k):
                    key = (task.task_id, arm, run_index)
                    if key in already_done:
                        continue
                    if not disk_guard_ok(workspace):
                        disk_exhausted = True
                        print(
                            f"[ALERTE DISQUE] moins de {DISK_GUARD_MIN_FREE_GB:.0f} Go libres sous "
                            f"{workspace} — arrêt propre de la campagne, aucun nouveau run lancé.",
                            file=sys.stderr,
                        )
                        break
                    record = _run_one(
                        task,
                        arm,
                        run_index,
                        workspace=workspace,
                        ecc_repo=ecc_repo,
                        homes=homes,
                        go_bin=go_bin,
                        run_timeout_s=args.run_timeout_s,
                        grimoire_bin=grimoire_bin,
                        auth_mode=auth_mode,
                    )
                    records.append(record)
                    results_f.write(json.dumps(record.to_dict()) + "\n")
                    results_f.flush()

            # Le critère d'arrêt statistique compare les IC de succès de
            # ``kit`` à ceux de ``nu``/``ecc`` sur les tâches DÉJÀ rejouées
            # dans CETTE exécution. Avec ``--arms`` restreint + ``--resume``,
            # ``records`` contient aussi les lignes reprises telles quelles
            # des bras non rejoués ici (ex. nu/ecc au complet) : leur
            # ``task_id`` compterait à tort dans ``tasks_done`` et
            # déclencherait un arrêt immédiat (« les 20 tâches ont été
            # rejouées ») dès la première tâche kit. Le critère ne s'applique
            # donc qu'à une campagne qui rejoue bien les trois bras.
            if not disk_exhausted and args.full and selected_arms == ARMS:
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

    if args.full or disk_exhausted:
        report_dir = args.report_dir or (workspace / "reports" / datetime.now(tz=UTC).strftime("%Y-%m-%d"))
        write_report(
            records,
            report_dir,
            total_tasks=len(tasks),
            expected_cost=expected_cost,
            seed=args.seed,
            rerun_arms=selected_arms,
            label=args.label,
            toolchain_check=toolchain_check,
        )
        if disk_exhausted:
            print(f"[report] rapport PARTIEL (arrêt disque) écrit sous {report_dir}")
        else:
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

    # Lot J (#582) : pendant du garde ci-dessus pour le mode `api-key` — la
    # clé n'est jamais copiée, mais elle ne doit pas non plus avoir fuité
    # dans un fichier de config d'un HOME isolé ou dans un journal de run.
    # Best-effort, non bloquant (voir `find_leaked_api_keys`) : jamais de
    # suppression automatique (on ignore la structure du fichier trouvé),
    # seulement une alerte bruyante.
    leaked_keys = find_leaked_api_keys(
        workspace, api_key=os.environ.get("ANTHROPIC_API_KEY") if auth_mode == "api-key" else None
    )
    if leaked_keys:
        print(
            f"[ALERTE SÉCURITÉ] ANTHROPIC_API_KEY retrouvée en clair dans {len(leaked_keys)} fichier(s) "
            "sous des HOME isolés ou des journaux de run — jamais attendu en mode api-key : "
            + ", ".join(str(p) for p in leaked_keys),
            file=sys.stderr,
        )
    elif auth_mode == "api-key":
        print("[sécurité] aucune fuite d'ANTHROPIC_API_KEY détectée sous un HOME isolé ou un journal.")

    return 0


def _do_report_only(
    workspace: Path,
    *,
    seed: int,
    report_dir: Path | None,
    arms: str | None = None,
    label: str | None = None,
) -> int:
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
    toolchain_check_path = state_dir / "toolchain_check.json"
    toolchain_check = (
        json.loads(toolchain_check_path.read_text(encoding="utf-8")) if toolchain_check_path.is_file() else None
    )
    out_dir = report_dir or (workspace / "reports" / datetime.now(tz=UTC).strftime("%Y-%m-%d"))
    # ``--report-only`` ne rejoue rien : ``--arms`` sert ici uniquement à
    # annoter le rapport (quels bras la campagne dont ``results.jsonl`` est
    # issu a réellement rejoués), pas à filtrer quoi que ce soit.
    rerun_arms = parse_arms(arms) if arms else None
    write_report(
        records,
        out_dir,
        total_tasks=total_tasks,
        expected_cost=expected_cost,
        seed=seed,
        rerun_arms=rerun_arms,
        label=label,
        toolchain_check=toolchain_check,
    )
    print(f"[report] écrit sous {out_dir}")
    return 0


def _do_dry_run(
    tasks: Sequence[TaskMeta], *, workspace: Path, ecc_repo: Path, homes: dict[str, Path], grimoire_bin: str
) -> int:
    tasks_root = workspace / "tasks"
    npm_cache_dir = workspace / "npm-cache"
    for task in tasks:
        for arm in ARMS:
            task_dir = tasks_root / task.task_id.replace("/", "__") / arm / "prep"
            prepare_task_repo(task, task_dir)
            hidden_tests_dir(task, tasks_root / task.task_id.replace("/", "__") / "hidden-tests")
            if arm == "nu":
                setup_arm_nu(task_dir, npm_cache_dir=npm_cache_dir)
            elif arm == "ecc":
                setup_arm_ecc(task_dir, ecc_repo=ecc_repo, ecc_home=homes["ecc"], npm_cache_dir=npm_cache_dir)
            elif arm == "kit":
                setup_arm_kit(task_dir, kit_home=homes["kit"], grimoire_bin=grimoire_bin, npm_cache_dir=npm_cache_dir)
            elif arm == "kit-gov":
                setup_arm_kit_gov(
                    task_dir,
                    kit_home=homes["kit-gov"],
                    task_id=task.task_id,
                    grimoire_bin=grimoire_bin,
                    npm_cache_dir=npm_cache_dir,
                )
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
    grimoire_bin: str,
    auth_mode: str = "oauth-copy",
) -> RunRecord:
    run_dir = workspace / "tasks" / task.task_id.replace("/", "__") / arm / f"run{run_index}"
    prepare_task_repo(task, run_dir)
    hidden_dir = hidden_tests_dir(task, workspace / "tasks" / task.task_id.replace("/", "__") / "hidden-tests")

    npm_cache_dir = workspace / "npm-cache"
    setup_result: dict[str, Any] | None
    if arm == "nu":
        setup_result = setup_arm_nu(run_dir, npm_cache_dir=npm_cache_dir)
    elif arm == "ecc":
        setup_result = setup_arm_ecc(run_dir, ecc_repo=ecc_repo, ecc_home=homes["ecc"], npm_cache_dir=npm_cache_dir)
    elif arm == "kit":
        setup_result = setup_arm_kit(
            run_dir, kit_home=homes["kit"], grimoire_bin=grimoire_bin, npm_cache_dir=npm_cache_dir
        )
    elif arm == "kit-gov":
        setup_result = setup_arm_kit_gov(
            run_dir,
            kit_home=homes["kit-gov"],
            task_id=task.task_id,
            grimoire_bin=grimoire_bin,
            npm_cache_dir=npm_cache_dir,
        )
    else:
        setup_result = None

    test_deps_install_ok: bool | None = None
    if setup_result is not None:
        test_deps = setup_result.get("test_deps_install")
        if test_deps is not None:
            test_deps_install_ok = test_deps.get("ok")

    home = homes[arm]
    # Lot I (#582) : UN SEUL calcul d'environnement pour ce run, transmis tel
    # quel à la session de l'agent (``run_claude_headless``) ET à la
    # vérification finale du harnais (``run_hidden_tests``) — jamais deux
    # environnements qui pourraient diverger (voir ``run_environment``).
    env = run_environment(
        run_dir,
        arm,
        home=home,
        grimoire_bin=grimoire_bin,
        workspace=workspace,
        go_bin=go_bin,
        npm_cache_dir=npm_cache_dir,
    )

    if auth_mode == "api-key":
        # Lot J (#582) : AUCUNE copie d'identifiant — l'agent s'authentifie
        # par `ANTHROPIC_API_KEY`, déjà présente dans `env` (héritée de
        # `os.environ` par `run_environment`) et transmise telle quelle par
        # `run_claude_headless` (`--bare`, voir sa docstring). C'est
        # précisément ce qui élimine le bug de rotation OAuth partagée :
        # rien n'est jamais écrit dans `home`.
        outcome = run_claude_headless(
            run_dir, build_prompt(task), home=home, env=env, timeout_s=run_timeout_s, auth_mode=auth_mode
        )
    else:
        # Les identifiants ne vivent dans `home` que le temps de cet appel : le
        # `finally` de `credentials_provisioned` les efface, que le run
        # réussisse, échoue, ou soit tué pour timeout/boucle — jamais laissés
        # à demeure.
        with credentials_provisioned(home) as creds:
            if creds is None:
                raise RuntimeError(
                    f"aucun identifiant Claude Code trouvé sous {Path.home()}/.claude — "
                    "authentifie-toi (`claude /login`) avant de lancer une campagne réelle, "
                    "ou passe --auth api-key avec ANTHROPIC_API_KEY exportée."
                )
            outcome = run_claude_headless(
                run_dir, build_prompt(task), home=home, env=env, timeout_s=run_timeout_s, auth_mode=auth_mode
            )

    if outcome.terminated_reason in ("timeout", "loop"):
        success = False
    else:
        success, _ = run_hidden_tests(task, run_dir, hidden_dir, go_bin=go_bin, env=env)

    # Après CHAQUE run, pas seulement en fin de campagne : voir
    # ``cleanup_build_artifacts`` — un `target/`(Rust) ou `node_modules/`(JS)
    # par run non nettoyé est ce qui remplit le disque en plein milieu d'une
    # campagne de 180 runs.
    cleanup_build_artifacts(run_dir)

    dispatch_stats = None
    kit_test_run_evidence = None
    if arm in ("kit", "kit-gov"):
        dispatch_stats = _collect_dispatch_stats(run_dir, home, grimoire_bin=grimoire_bin)
        kit_test_run_evidence = has_test_run_evidence(run_dir)

    return RunRecord(
        task_id=task.task_id,
        language=task.language,
        arm=arm,
        run_index=run_index,
        success=success,
        total_cost_usd=outcome.total_cost_usd,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        recorded_at=datetime.now(tz=UTC).isoformat(),
        kit_test_run_evidence=kit_test_run_evidence,
        test_deps_install_ok=test_deps_install_ok,
        cache_read_input_tokens=outcome.cache_read_input_tokens,
        cache_creation_input_tokens=outcome.cache_creation_input_tokens,
        model_usage=outcome.model_usage or None,
        num_turns=outcome.num_turns,
        wall_seconds=outcome.wall_seconds,
        terminated_reason=outcome.terminated_reason,
        dispatch_stats=dispatch_stats,
        toolchain_friction_bash_calls=outcome.toolchain_friction_bash_calls,
        auth_mode=auth_mode,
    )


def _collect_dispatch_stats(run_dir: Path, home: Path, *, grimoire_bin: str) -> dict[str, Any] | None:
    env = {
        **os.environ,
        "HOME": str(home),
        # Lot H (#582) : PATH absolu en tête — voir `resolve_grimoire_bin`.
        "PATH": f"{Path(grimoire_bin).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    result = _run([grimoire_bin, "dispatch", "stats", "--json"], cwd=run_dir, env=env, timeout=30)
    if result.returncode != 0:
        return {"error": result.stderr[-500:]}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "sortie non JSON", "stdout": result.stdout[-500:]}


if __name__ == "__main__":
    raise SystemExit(main())
