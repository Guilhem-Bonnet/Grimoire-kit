"""Bloc K du lot M (B-08) : des bugs réels de grimoire-kit, rejoués comme tâches du banc.

Une tâche K = un commit parent de grimoire-kit (le dépôt de l'agent, avec un
seul commit), un énoncé (``TASK.md``) et le test de régression du commit de
correctif comme test caché. Les données vivent dans un fichier versionné
(``docs/bench/lot-m-kit-tasks.json``, schéma ``grimoire.bench.kit-tasks.v1``).

Le module ne réinvente pas le banc : il MATÉRIALISE chaque tâche au format
polyglotte (``<racine>/kit/exercises/practice/<slug>/``, ``.docs/instructions.md``
= énoncé, ``.meta/config.json`` avec ``files.test`` = tests cachés) pour que
``discover_catalog``, ``prepare_task_repo``, ``assert_task_repo_clean``,
``hidden_tests_dir`` et ``hidden_reads`` servent tels quels. Ce qu'il ajoute :

- la vérification, avant toute dépense, que chaque tâche est rouge au parent et
  verte au correctif DANS l'environnement de test du banc (:func:`verify_tasks`) :
  un environnement cassé rendrait toutes les tâches « discriminantes » ;
- un venv de test qui ne contient PAS grimoire-kit (:func:`ensure_test_python`) :
  le ``site-packages`` d'un kit récent contient les correctifs ;
- l'environnement de run K (:func:`k_environment`) : ce venv en tête de ``PATH``,
  ``PYTHONPATH=src``, backend Python des politiques, pip hors ligne,
  ``PYTHONNOUSERSITE``, et un ``CLAUDE_ENV_FILE`` qui pointe les proxys des
  commandes ``Bash`` de l'agent sur un port fermé ;
- ``--disallowedTools WebFetch WebSearch`` pour l'agent (:func:`claude_extra_args`) ;
- la détection, dans la transcription, d'un accès à la solution publique
  (:func:`check_k_transcript`) : le run devient ``invalid``, comme une lecture
  de test caché (W1-08b) ;
- le refus des bras ``kit`` et ``kit-gov`` sur le bloc K (:func:`refuse_governed_arms`,
  décision documentée au §17.7 de ``docs/bench-three-arms.md``).

Ce qui n'est PAS fermé, dit sans détour : le système de fichiers de l'opérateur
(un agent en ``bypassPermissions`` peut lire un clone du kit avec son historique,
c'est seulement détecté s'il cite le chemin), un contournement volontaire des
proxys (``env -u``, socket brute) et tout réseau de Claude Code lui-même.

Aucune dépendance à ``three_arms`` (qui importe ce module).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hidden_reads import check_transcript
from task_repo import prepare_task_repo

SCHEMA = "grimoire.bench.kit-tasks.v1"
LANGUAGE = "kit"
DEFAULT_TASKS_FILE = Path(__file__).resolve().parents[2] / "docs" / "bench" / "lot-m-kit-tasks.json"
GOVERNED_ARMS = frozenset({"kit", "kit-gov"})
TEST_TIMEOUT_S = 900
K_META = "k-task.json"
_SLUG = re.compile(r"^k\d{2}-[a-z0-9][a-z0-9-]*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")

#: Consigne du bloc K : même ton que ``BUILD_PROMPT_TEMPLATE``, adaptée à un dépôt réel.
K_PROMPT = (
    "Tu travailles dans le dépôt courant, un projet Python (grimoire-kit). Lis TASK.md : il décrit "
    "un défaut constaté et le comportement attendu. Corrige le code source (sous src/) pour obtenir "
    "ce comportement sans casser le reste. Tu peux lancer les tests du dépôt (`python -m pytest`) et "
    "en ajouter. Termine dès que tu es raisonnablement confiant dans la correction."
)

#: Ce qui, dans un appel d'outil de l'agent, désigne la solution publiée (GitHub, PyPI, kit installé).
_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("kit installé", re.compile(r"site-packages[/\\]+(?:grimoire|_?grimoire_kit)", re.IGNORECASE)),
    ("dépôt GitHub du kit", re.compile(r"github\.com[/:]+guilhem-bonnet/grimoire", re.IGNORECASE)),
    ("PyPI", re.compile(r"pypi\.org/(?:project|simple|pypi)/grimoire|files\.pythonhosted\.org", re.IGNORECASE)),
    (
        "installation du kit",
        re.compile(r"\b(?:pip3?|uv\s+pip|pipx)\s+(?:download|install)\b[^\n]*grimoire", re.IGNORECASE),
    ),
    ("git distant", re.compile(r"\bgit\s+(?:clone|fetch|pull|ls-remote|remote\s+add)\b")),
)
_NETWORK_TOOLS = frozenset({"WebFetch", "WebSearch"})


class KitLotError(RuntimeError):
    """Une tâche K ne peut pas être préparée ou vérifiée : aucun run n'est lancé."""


@dataclass(frozen=True)
class KitTask:
    id: str
    slug: str
    issue: int
    fix_commit: str
    parent_commit: str
    test_files: tuple[str, ...]
    pytest_k: str | None
    statement: str

    @property
    def task_id(self) -> str:
        return f"{LANGUAGE}/{self.slug}"

    def digest(self) -> str:
        payload = json.dumps(
            [
                self.id,
                self.slug,
                self.fix_commit,
                self.parent_commit,
                list(self.test_files),
                self.pytest_k,
                self.statement,
            ],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bad(path: Path, msg: str) -> KitLotError:
    return KitLotError(f"{path} : {msg}")


def load_kit_tasks(path: Path) -> list[KitTask]:
    """Lit et valide le fichier des tâches K ; :class:`KitLotError` sur toute forme inattendue."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _bad(path, f"illisible ({exc})") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise _bad(path, f"« schema » doit valoir {SCHEMA!r}")
    raw = data.get("tasks")
    if not isinstance(raw, list) or not raw:
        raise _bad(path, "« tasks » doit être une liste non vide")
    tasks: list[KitTask] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise _bad(path, f"tâche n°{i} : objet attendu")
        tid, slug = entry.get("id"), entry.get("slug")
        fix, parent = entry.get("fix_commit"), entry.get("parent_commit")
        tests, statement, k = entry.get("test_files"), entry.get("statement"), entry.get("pytest_k")
        if not (
            isinstance(tid, str) and isinstance(slug, str) and _SLUG.match(slug) and slug.startswith(tid.lower() + "-")
        ):
            raise _bad(path, f"tâche n°{i} : id/slug invalides ({tid!r}, {slug!r} ; attendu k01-<mots>)")
        if not (isinstance(fix, str) and _SHA.match(fix) and isinstance(parent, str) and _SHA.match(parent)):
            raise _bad(path, f"{tid} : fix_commit et parent_commit doivent être des sha complets")
        if not (isinstance(tests, list) and tests and all(isinstance(t, str) for t in tests)):
            raise _bad(path, f"{tid} : « test_files » doit être une liste non vide de chemins")
        for rel in tests:
            if (
                rel.startswith(("/", "\\"))
                or ".." in Path(rel).parts
                or "\\" in rel
                or Path(rel).parts[0] in (".meta", ".docs")
            ):
                raise _bad(path, f"{tid} : chemin de test invalide {rel!r}")
        if k is not None and not (isinstance(k, str) and k.strip()):
            raise _bad(path, f"{tid} : « pytest_k » doit être une expression non vide ou absent")
        if not (isinstance(statement, str) and len(statement.strip()) >= 200):
            raise _bad(path, f"{tid} : énoncé absent ou trop court (200 caractères au moins)")
        issue = entry.get("issue")
        if not isinstance(issue, int) or isinstance(issue, bool):
            raise _bad(path, f"{tid} : « issue » doit être un numéro")
        refused = statement_leaks(statement, fix_commit=fix, fix_pr=entry.get("fix_pr"))
        if refused:
            raise _bad(path, f"{tid} : l'énoncé révèle le correctif ({', '.join(refused)})")
        tasks.append(KitTask(tid, slug, issue, fix, parent, tuple(tests), k, statement))
    for attr in ("id", "slug"):
        values = [getattr(t, attr) for t in tasks]
        dup = sorted({v for v in values if values.count(v) > 1})
        if dup:
            raise _bad(path, f"{attr} en double : {', '.join(dup)}")
    return tasks


def statement_leaks(statement: str, *, fix_commit: str, fix_pr: object = None) -> list[str]:
    """Garde mécanique (la relecture humaine reste la vraie garde) : sha ou PR du correctif, URL GitHub ou PyPI."""
    found = []
    lowered = statement.lower()
    if any(fix_commit[:n] in lowered for n in (7, 9, 40)):
        found.append("sha du correctif")
    if isinstance(fix_pr, int) and re.search(rf"(?<![\w#])#{fix_pr}\b|pull/{fix_pr}\b", statement):
        found.append(f"PR #{fix_pr}")
    if re.search(r"https?://(?:[\w.-]*\.)?(?:github\.com|pypi\.org|pythonhosted\.org)", statement, re.IGNORECASE):
        found.append("URL du dépôt ou de PyPI")
    return found


# ── Matérialisation au format polyglotte ─────────────────────────────────────


def _git(repo: Path, *args: str, binary: bool = False) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=not binary, timeout=300, check=False
    )


def exercise_dir(root: Path, task: KitTask) -> Path:
    return root / LANGUAGE / "exercises" / "practice" / task.slug


def _extract_tree(repo: Path, commit: str, dest: Path) -> None:
    archive = _git(repo, "archive", "--format=tar", commit, binary=True)
    if archive.returncode != 0:
        raise KitLotError(f"`git archive {commit}` a échoué dans {repo} : {archive.stderr[-300:]!r}")
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(dest, filter="data")


def materialize(tasks: Sequence[KitTask], kit_repo: Path, root: Path) -> Path:
    """Écrit chaque tâche sous ``root/kit/exercises/practice/<slug>`` (réutilisée si son empreinte n'a pas bougé).

    Refus (:class:`KitLotError`) : parent déclaré qui n'est pas le parent du
    correctif dans *kit_repo*, test caché absent au correctif, arbre du parent
    qui porte déjà un ``.meta``/``.docs`` de premier niveau (ils seraient pris
    pour ceux de l'exercice). *kit_repo* est lu, jamais écrit.
    """
    for task in tasks:
        target = exercise_dir(root, task)
        meta_path = target / ".meta" / K_META
        if meta_path.is_file():
            try:
                if json.loads(meta_path.read_text(encoding="utf-8")).get("digest") == task.digest():
                    continue
            except json.JSONDecodeError:
                pass
        parent = _git(kit_repo, "rev-parse", "--verify", f"{task.fix_commit}^")
        if parent.returncode != 0 or parent.stdout.strip() != task.parent_commit:
            raise KitLotError(
                f"{task.id} : le parent de {task.fix_commit[:9]} dans {kit_repo} est "
                f"{parent.stdout.strip() or 'illisible'}, le fichier déclare {task.parent_commit}"
            )
        if target.exists():
            shutil.rmtree(target)
        _extract_tree(kit_repo, task.parent_commit, target)
        clash = [name for name in (".meta", ".docs") if (target / name).exists()]
        if clash:
            raise KitLotError(f"{task.id} : l'arbre du parent porte déjà {', '.join(clash)} au premier niveau")
        for rel in task.test_files:
            shown = _git(kit_repo, "show", f"{task.fix_commit}:{rel}", binary=True)
            if shown.returncode != 0:
                raise KitLotError(f"{task.id} : test caché {rel} absent au correctif {task.fix_commit[:9]}")
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            (target / rel).write_bytes(shown.stdout)
        (target / ".docs").mkdir()
        (target / ".docs" / "instructions.md").write_text(
            task.statement.rstrip() + "\n", encoding="utf-8", newline="\n"
        )
        (target / ".meta").mkdir()
        config = {"files": {"solution": ["src/grimoire"], "test": list(task.test_files)}}
        (target / ".meta" / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        meta = {
            "id": task.id,
            "issue": task.issue,
            "parent_commit": task.parent_commit,
            "test_files": list(task.test_files),
            "pytest_k": task.pytest_k,
            # Marqueurs de fuite propres à la tâche : le dépôt source (historique complet) et le sha du correctif.
            "leak_markers": [kit_repo.resolve().as_posix(), task.fix_commit[:9]],
            "digest": task.digest(),
        }
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return root


def task_meta(task: Any) -> dict[str, Any]:
    """Le ``.meta/k-task.json`` d'une tâche du catalogue (``TaskMeta``) ; :class:`KitLotError` s'il manque."""
    path = Path(task.exercise_dir) / ".meta" / K_META
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KitLotError(f"{path} illisible ({exc}) : tâche K non matérialisée") from None
    return dict(data)


def lot_root(task: Any) -> Path:
    """``<racine>`` de ``<racine>/kit/exercises/practice/<slug>``."""
    return Path(task.exercise_dir).parents[3]


def is_k(task: Any) -> bool:
    return getattr(task, "language", None) == LANGUAGE


# ── Environnement de test et de run ──────────────────────────────────────────


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_test_python(venv: Path, requirements: Sequence[str], *, base_python: str = sys.executable) -> Path:
    """Le Python du venv de test K, construit s'il manque, besoins installés, puis vérifié : pytest présent, grimoire ABSENT.

    Construction par ``uv`` s'il est sur le ``PATH`` (cache local), sinon ``venv`` + ``pip`` (réseau du
    lanceur, avant tout run). Un venv qui importe ``grimoire`` est refusé : il donnerait la solution.
    """
    python = venv_python(venv)
    uv = shutil.which("uv")
    create = (
        [uv, "venv", "--quiet", "--python", base_python, str(venv)] if uv else [base_python, "-m", "venv", str(venv)]
    )
    install = (
        [uv, "pip", "install", "--quiet", "--python", str(python), *requirements]
        if uv
        else [str(python), "-m", "pip", "install", "--quiet", *requirements]
    )
    # L'installation est rejouée à chaque préparation (sans effet si tout est là) : un besoin ajouté au fichier
    # de données atteint un venv déjà construit.
    for cmd in ([] if python.is_file() else [create]) + [install]:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=False)
        if done.returncode != 0:
            raise KitLotError(
                f"venv de test K : `{' '.join(cmd[:4])}…` a échoué : {(done.stdout + done.stderr)[-600:]}"
            )
    verify_test_python(python)
    return python


def verify_test_python(python: Path) -> None:
    probe = "import importlib.util as u, sys; sys.exit(3 if u.find_spec('grimoire') else (0 if u.find_spec('pytest') else 4))"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    done = subprocess.run(
        [str(python), "-I", "-c", probe], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    if done.returncode == 3:
        raise KitLotError(f"{python} importe `grimoire` sans PYTHONPATH : ce venv contient le kit, donc les correctifs")
    if done.returncode != 0:
        raise KitLotError(f"{python} : pytest absent ou interpréteur inutilisable (code {done.returncode})")


#: Exportés avant chaque commande ``Bash`` de l'agent (``CLAUDE_ENV_FILE``) : un proxy sur un port fermé.
#: Claude Code lui-même garde son accès à l'API (le fichier n'est pas lu par son processus).
OFFLINE_ENV_FILE = """# Banc grimoire-kit, bloc K (B-08) : réseau des commandes de l'agent pointé sur un port fermé.
export HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9
export http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 all_proxy=http://127.0.0.1:9
export NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1
export PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 UV_OFFLINE=1 npm_config_offline=true GIT_TERMINAL_PROMPT=0
"""


def write_offline_env_file(root: Path) -> Path:
    path = root / "offline-env.sh"
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(OFFLINE_ENV_FILE, encoding="utf-8", newline="\n")
    return path


def k_environment(env: Mapping[str, str], task_dir: Path, python: Path, env_file: Path | None) -> dict[str, str]:
    """L'environnement d'un run K : le même dict sert à l'agent et aux tests cachés (règle du lot I)."""
    out = dict(env)
    out["PATH"] = os.pathsep.join([str(python.parent), out.get("PATH", os.defpath)])
    out["VIRTUAL_ENV"] = str(python.parent.parent)
    out["PYTHONPATH"] = str(task_dir / "src")
    out.pop("PYTHONHOME", None)
    out.update(
        {
            "GRIMOIRE_POLICIES_BACKEND": "python",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_NO_INDEX": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "UV_OFFLINE": "1",
        }
    )
    if env_file is not None:
        out["CLAUDE_ENV_FILE"] = str(env_file)
    return out


def run_environment_for(task: Any, task_dir: Path, env: Mapping[str, str]) -> dict[str, str]:
    """``env`` inchangé pour une tâche polyglotte ; enrichi par :func:`k_environment` pour une tâche K."""
    if not is_k(task):
        return dict(env)
    conf = _test_env(task)
    return k_environment(env, task_dir, Path(conf["python"]), Path(conf["env_file"]))


def _test_env(task: Any) -> dict[str, Any]:
    path = lot_root(task) / "test-env.json"
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise KitLotError(f"{path} illisible ({exc}) : venv de test K non préparé") from None


def claude_extra_args(task: Any) -> list[str]:
    """Outils réseau de l'agent retirés pour une tâche K (le correctif est public)."""
    return ["--disallowedTools", "WebFetch", "WebSearch"] if is_k(task) else []


def pytest_command(python: Path, meta: Mapping[str, Any]) -> list[str]:
    files = [f for f in meta["test_files"] if f.endswith(".py") and Path(f).name != "conftest.py"]
    cmd = [
        str(python),
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-rs",
        *files,
    ]  # sans -q : la ligne de bilan doit rester
    return [*cmd, "-k", str(meta["pytest_k"])] if meta.get("pytest_k") else cmd


def run_hidden_tests(
    task: Any, task_dir: Path, hidden_dir: Path, *, env: Mapping[str, str] | None, timeout_s: int = TEST_TIMEOUT_S
) -> tuple[bool, str]:
    """Pose les tests cachés du correctif puis lance pytest ; succès = code 0, au moins un test passé, aucun sauté."""
    meta = task_meta(task)
    for rel in meta["test_files"]:
        src = hidden_dir / rel
        if not src.is_file():
            return (False, f"fichier de test absent : {rel}")
        (task_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, task_dir / rel)
    run_env = dict(env) if env is not None else run_environment_for(task, task_dir, os.environ)
    python = Path(_test_env(task)["python"])
    try:
        done = subprocess.run(
            pytest_command(python, meta),
            cwd=task_dir,
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (False, "timeout des tests")
    out = done.stdout[-2000:] + done.stderr[-2000:]
    return (done.returncode == 0 and pytest_all_passed(done.stdout), out)


def pytest_all_passed(stdout: str) -> bool:
    """Au moins un test passé, aucun sauté ni attendu en échec : un test caché sauté n'a rien vérifié."""
    tail = stdout.strip().splitlines()[-1:] or [""]
    counts = {word: int(n) for n, word in re.findall(r"(\d+) (passed|skipped|xfailed|xpassed|deselected)", tail[0])}
    return counts.get("passed", 0) >= 1 and not any(counts.get(w) for w in ("skipped", "xfailed", "xpassed"))


def verify_tasks(tasks: Sequence[Any], kit_repo: Path) -> list[dict[str, Any]]:
    """Avant toute dépense : chaque tâche K rouge au parent ET verte au correctif, dans l'environnement du banc.

    Le dépôt au correctif n'existe que le temps du contrôle (effacé ensuite : jamais à portée d'un agent).
    Un succès est mis en cache par empreinte de tâche et interpréteur ; tout échec lève :class:`KitLotError`.
    """
    report: list[dict[str, Any]] = []
    for task in tasks:
        meta = task_meta(task)
        root = lot_root(task)
        conf = _test_env(task)
        cache_path = root / "verified.json"
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
        except json.JSONDecodeError:
            cache = {}
        reqs_digest = hashlib.sha256(json.dumps(conf.get("requirements") or []).encode("utf-8")).hexdigest()[:16]
        key = f"{meta['digest']}:{conf['python']}:{reqs_digest}"  # tâche, interpréteur et besoins du venv
        if cache.get(task.task_id) == key:
            report.append({"task_id": task.task_id, "red_at_parent": True, "green_at_fix": True, "cached": True})
            continue
        work = root / "_verify" / task.slug
        shutil.rmtree(work, ignore_errors=True)
        try:
            parent_dir, fix_dir = work / "parent", work / "fix"
            prepare_task_repo(task, parent_dir)
            red_ok, red_out = run_hidden_tests(task, parent_dir, Path(task.exercise_dir), env=None)
            fix_commit = _fix_commit(kit_repo, meta["parent_commit"], task)
            _extract_tree(kit_repo, fix_commit, fix_dir)
            green_ok, green_out = run_hidden_tests(task, fix_dir, Path(task.exercise_dir), env=None)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        row = {"task_id": task.task_id, "red_at_parent": not red_ok, "green_at_fix": green_ok, "cached": False}
        report.append(row)
        if red_ok or not green_ok:
            tail = (red_out if red_ok else green_out).strip().splitlines()[-3:]
            raise KitLotError(f"{task.task_id} : {row} — la tâche ne discrimine pas dans ce venv ; {' | '.join(tail)}")
        cache[task.task_id] = key
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    return report


def _fix_commit(kit_repo: Path, parent: str, task: Any) -> str:
    """Le correctif, retrouvé dans le fichier de données par l'id de la tâche (jamais stocké à portée d'agent)."""
    for kt in load_kit_tasks(Path(_test_env(task).get("tasks_file") or DEFAULT_TASKS_FILE)):
        if kt.task_id == task.task_id and kt.parent_commit == parent:
            return kt.fix_commit
    raise KitLotError(f"{task.task_id} : correctif introuvable dans le fichier des tâches K ({kit_repo})")


def prepare_block(
    tasks: Sequence[Any],
    *,
    workspace: Path,
    kit_repo: Path | None,
    tasks_file: Path,
    test_venv: Path | None,
    requirements: Sequence[str] | None = None,
) -> list[dict[str, Any]] | None:
    """Prépare le bloc K si la sélection en contient : venv de test, fichier d'environnement, vérification rouge/vert.

    ``None`` si aucune tâche K. Lève ``SystemExit`` (0 $ dépensé) sur tout échec.
    """
    k_tasks = [t for t in tasks if is_k(t)]
    if not k_tasks:
        return None
    if kit_repo is None:
        raise SystemExit("bloc K : --kit-repo (clone de grimoire-kit, lu sans écriture) est obligatoire")
    root = lot_root(k_tasks[0])
    try:
        reqs = requirements if requirements is not None else load_test_requirements(tasks_file)
        python = ensure_test_python(test_venv or workspace / "kit-test-venv", reqs)
        env_file = write_offline_env_file(root)
        (root / "test-env.json").write_text(
            json.dumps(
                {
                    "python": str(python),
                    "env_file": str(env_file),
                    "tasks_file": str(tasks_file),
                    "requirements": list(reqs),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return verify_tasks(k_tasks, kit_repo)
    except KitLotError as exc:
        raise SystemExit(f"bloc K : {exc} — aucun run lancé, 0 $ dépensé.") from None


def load_test_requirements(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    reqs = data.get("test_requirements")
    if not (isinstance(reqs, list) and reqs and all(isinstance(r, str) for r in reqs)):
        raise KitLotError(f"{path} : « test_requirements » doit être une liste non vide")
    return list(reqs)


def catalog_root(workspace: Path, kit_repo: Path | None, tasks_file: Path) -> Path | None:
    """Matérialise le bloc K sous ``workspace/kit-lot`` si ``--kit-repo`` est donné ; ``None`` sinon."""
    if kit_repo is None:
        return None
    try:
        return materialize(load_kit_tasks(tasks_file), kit_repo, workspace / "kit-lot")
    except KitLotError as exc:
        raise SystemExit(f"bloc K : {exc} — aucun run lancé, 0 $ dépensé.") from None


def refuse_governed_arms(tasks: Sequence[Any], arms: Sequence[str]) -> None:
    """Bras ``kit``/``kit-gov`` refusés sur le bloc K (§17.7) : avant tout run, jamais une exclusion silencieuse."""
    bad = sorted(GOVERNED_ARMS & set(arms))
    if bad and any(is_k(t) for t in tasks):
        raise SystemExit(
            f"bloc K : bras {', '.join(bad)} refusé(s) — le kit installé contient les correctifs et un kit installé "
            "au parent porterait le défaut dans ses propres gardes (docs/bench-three-arms.md §17.7). "
            "Lancer le bloc K avec --arms nu (ou ecc). Aucun run lancé, 0 $ dépensé."
        )


# ── Transcription : accès à la solution publiée ──────────────────────────────


def scan_solution_leaks(log_path: Path, markers: Sequence[str]) -> list[dict[str, Any]]:
    """Appels d'outil de l'agent qui désignent la solution publiée (W1-08b étendu au bloc K)."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    hits: list[dict[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (
            (event.get("message") or {}).get("content")
            if isinstance(event, dict) and event.get("type") == "assistant"
            else None
        )
        for item in content if isinstance(content, list) else []:
            if not (isinstance(item, dict) and item.get("type") == "tool_use"):
                continue
            name, text = str(item.get("name")), json.dumps(item.get("input"), ensure_ascii=False)
            reasons = [label for label, pattern in _LEAK_PATTERNS if pattern.search(text)]
            reasons += [f"marqueur {m}" for m in markers if m and m in text]
            if name in _NETWORK_TOOLS:
                reasons.append(f"outil réseau {name}")
            if reasons:
                hits.append({"kind": "solution_leak", "tool": name, "reasons": reasons, "excerpt": text[:200]})
    return hits


def check_k_transcript(
    log_path: Path, task: Any, *, run_dir: Path, hidden_dir: Path
) -> tuple[str, list[dict[str, Any]] | None]:
    """``hidden_reads.check_transcript``, plus la fuite de solution pour une tâche K : une fuite rend ``invalid``."""
    verdict, reads = check_transcript(
        log_path,
        test_files=task.test_files,
        run_dir=run_dir,
        hidden_dir=hidden_dir,
        canonical_dirs=(task.exercise_dir,),
    )
    if not is_k(task):
        return verdict, reads
    leaks = scan_solution_leaks(log_path, task_meta(task).get("leak_markers") or [])
    return ("invalid", [*(reads or []), *leaks]) if leaks else (verdict, reads)
