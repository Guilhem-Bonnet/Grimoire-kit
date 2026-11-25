"""GAO-orchestrateu-001 : les rôles autonomes sur l'hôte du projet — Gemini CLI derrière ``WorkerHost``.

Aucun vrai ``gemini`` n'est lancé : un **faux binaire** (script de fixture écrit sous ``tmp_path``) émet le flux
``stream-json`` que la documentation décrit (``init`` avec ``session_id``, ``message``, ``result``). Il est lancé pour
de bon, détaché, par :func:`~grimoire.missions.worker_host.default_spawn` : lancement, identifiant, reprise, arrêt et
session morte se jugent sur un vrai processus. Le ``HOME`` est isolé sous ``tmp_path`` (journal d'autorité, preuves
de contrat d'hôte) : rien ne touche l'état réel de l'utilisateur.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from lot2_doubles import FakeHost, git, make_repo

from grimoire.bridges.schemas import HostId
from grimoire.missions.admission import Admit
from grimoire.missions.gemini_host import (
    GEMINI_APPROVAL_MODES,
    GEMINI_ENV_ALLOWED,
    GeminiWorkerHost,
    gemini_log,
    gemini_worktree_refusal,
    process_identity,
    read_events,
    same_process_identity,
)
from grimoire.missions.launcher import (
    WORKER_HALTED_EVENT,
    WORKER_RESUMED_EVENT,
    ExecResult,
    WorkerLauncher,
    default_exec,
    halt_idle_workers,
    launched_sessions,
    release_ended_workers,
    renew_live_workers,
    resume_provider_failed_workers,
    resume_worker,
)
from grimoire.missions.orchestrator_policy import claude_only_refusal, load_orchestrator_policy
from grimoire.missions.schemas import TaskCheck, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.worker_guard import ENV_ALLOWED
from grimoire.missions.worker_host import CLAUDE_CODE_HOST, GEMINI_HOST, SessionSpec, worker_host

POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
VERSION = "0.64.0"
SID = "123e4567-e89b-42d3-a456-426614174000"
POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="le worker Gemini est suivi par pid POSIX")

#: Le faux ``gemini`` : ``--version``, puis le flux ``stream-json`` documenté. Le fichier ``mode`` voisin choisit la
#: fin : ``done`` (rend la main), ``hang`` (reste vivant jusqu'au signal), ``crash`` (meurt sans événement ``init``).
#: Rien ne passe par l'environnement (liste fermée) : le chemin du journal des argv est écrit dans le script, et
#: chaque lancement note aussi l'environnement reçu dans ``env.jsonl``.
FAKE_GEMINI = r"""
import json, os, pathlib, sys, time, uuid
args = sys.argv[1:]
if args == ["--version"]:
    print("__VERSION__")
    sys.exit(0)
here = pathlib.Path(__file__).resolve().parent
mode_file = here / "mode"
mode = mode_file.read_text(encoding="utf-8").strip() if mode_file.is_file() else "done"
with open(__SEEN__, "a", encoding="utf-8") as seen:
    seen.write(json.dumps(args) + "\n")
with open(__ENV__, "a", encoding="utf-8") as seen:
    seen.write(json.dumps(dict(os.environ)) + "\n")
if mode == "crash":
    sys.stderr.write("panne simulée\n")
    sys.exit(3)
resume = [a.split("=", 1)[1] for a in args if a.startswith("--resume=")]
session = resume[0] if resume else str(uuid.UUID(int=len(args) + 1))
def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()
emit({"type": "init", "timestamp": "2026-11-23T00:00:00Z", "session_id": session, "model": "gemini-x"})
emit({"type": "message", "role": "user", "content": "consigne"})
emit({"type": "message", "role": "assistant", "content": "PR ouverte", "delta": True})
emit({"type": "message", "role": "assistant", "content": " : #42", "delta": True})
if mode == "hang":
    time.sleep(60)
emit({"type": "result", "status": "success", "stats": {"total_tokens": 10}})
""".replace("__VERSION__", VERSION)

KIT_SETTINGS = {
    "hooks": {
        "BeforeTool": [
            {
                "matcher": ".*",
                "hooks": [{"name": "grimoire", "type": "command", "command": "grimoire-hook gemini PreToolUse"}],
            }
        ]
    }
}


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in list(os.environ):
        if var.startswith("CLAUDE") or var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_SESSION_ID", "GRIMOIRE_HOST_PROBE"):
            monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def gemini_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Le faux ``gemini`` exécutable ; chaque lancement note son argv dans ``argv.jsonl``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    path = bin_dir / "gemini"
    script = FAKE_GEMINI.replace("__SEEN__", repr(str(tmp_path / "argv.jsonl")))
    path.write_text(
        f"#!{sys.executable}\n{script.replace('__ENV__', repr(str(tmp_path / 'env.jsonl')))}", encoding="utf-8"
    )
    path.chmod(0o755)
    return path


ENV = Path("/usr/bin/env")
ENV_ONLY = pytest.mark.skipif(os.name != "posix" or not ENV.is_file(), reason="shebang #!/usr/bin/env")


@pytest.fixture
def gemini_env_bin(gemini_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Le même faux ``gemini``, installé comme par npm : ``#!/usr/bin/env python3``, interpréteur trouvé dans PATH.

    Le processus est d'abord ``env``, puis, sous le même pid et la même heure de démarrage, l'interpréteur.
    """
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "python3").symlink_to(sys.executable)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")
    body = gemini_bin.read_text(encoding="utf-8").split("\n", 1)[1]
    gemini_bin.write_text(f"#!{ENV} python3\n{body}", encoding="utf-8")
    return gemini_bin


class EnvFirst:
    """``process_identity``, mais la première lecture de chaque pid voit ``env`` : ce que rend une lecture faite au
    retour de ``Popen``, avant que ``env`` n'exécute l'interpréteur (la course réelle n'est pas reproductible)."""

    def __init__(self) -> None:
        self.seen: set[int] = set()

    def __call__(self, pid: int) -> str:
        identity = process_identity(pid)
        if pid in self.seen or not identity:
            return identity
        self.seen.add(pid)
        return f"{identity.partition('|')[0]}|{os.path.realpath(ENV)}"


def set_mode(gemini_bin: Path, mode: str) -> None:
    """La fin du prochain lancement du faux ``gemini`` (``done``, ``hang``, ``crash``)."""
    (gemini_bin.parent / "mode").write_text(mode, encoding="utf-8")


def seen_env(tmp_path: Path) -> list[dict[str, str]]:
    path = tmp_path / "env.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def seen_argv(tmp_path: Path) -> list[list[str]]:
    path = tmp_path / "argv.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def wait_for(predicate, timeout: float = 10.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class Spawned:
    """``default_spawn`` pour de bon, en gardant les ``Popen`` pour les récolter (pas de zombie qui « vit »)."""

    def __init__(self) -> None:
        self.procs: dict[int, subprocess.Popen[bytes]] = {}

    def spawn(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str], log: Path):  # type: ignore[no-untyped-def]
        from grimoire.missions.worker_host import SpawnResult

        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as out:
            proc = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.procs[proc.pid] = proc
        return SpawnResult(proc.pid)

    def alive(self, pid: int) -> bool:
        proc = self.procs.get(pid)
        return proc is not None and proc.poll() is None

    def kill(self, pid: int) -> bool:
        proc = self.procs.get(pid)
        if proc is None:
            return False
        if proc.poll() is None:
            os.killpg(pid, 15)
            proc.wait(timeout=10)
        return True

    def close(self) -> None:
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)


@pytest.fixture
def spawned() -> Spawned:
    holder = Spawned()
    yield holder  # type: ignore[misc]
    holder.close()


def host_for(gemini_bin: Path, spawned: Spawned) -> GeminiWorkerHost:
    return GeminiWorkerHost(
        str(gemini_bin),
        spawn=spawned.spawn,
        alive=spawned.alive,
        kill=spawned.kill,
        resolve_attempts=200,
        resolve_pause_s=0.05,
    )


class GeminiExec(FakeHost):
    """Le faux ``claude``/``gh`` du lot 2, et le faux ``gemini`` lancé pour de bon (``--version``)."""

    def __init__(self, gemini_bin: Path) -> None:
        super().__init__()
        self.gemini_bin = str(gemini_bin)

    def __call__(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None) -> ExecResult:
        if argv and argv[0] == self.gemini_bin:
            self.calls.append((list(argv), cwd, env))
            return default_exec(argv, cwd, env)
        return super().__call__(argv, cwd, env)


def write_policy(root: Path, top: str = "  host: gemini\n", worker: str = "", mode: str = "acceptEdits") -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        f"orchestrator:\n{top}  roles:\n    worker:\n      enabled: true\n      wip: 3\n      sessions_per_day: 9\n"
        f"      permission_mode: {mode}\n{worker}",
        encoding="utf-8",
    )


def prove(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, version: str = VERSION, *, ok: bool = True) -> None:
    """Consigne une preuve de frein pour Gemini CLI *version*, dans un fichier de preuves sous ``tmp_path``."""
    from grimoire.hosts import contract_probe

    monkeypatch.setattr(contract_probe, "proofs_path", lambda: tmp_path / "proofs" / "host-contracts.json")
    contract_probe._record(contract_probe.ProbeProof(HostId.GEMINI_CLI.value, version, ok, "2026-11-23T00:00:00+00:00"))


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    """Un dépôt dont ``main`` porte la surface Gemini du kit (``.gemini/settings.json`` et son hook BeforeTool)."""
    root = make_repo(tmp_path)
    settings = root / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps(KIT_SETTINGS), encoding="utf-8")
    git(root, "add", "--", ".gemini/settings.json")
    git(root, "commit", "-q", "-m", "surface gemini")
    git(root, "push", "-q", "origin", "main")
    write_policy(root)
    return root


def ready(root: Path, title: str = "travail") -> str:
    service = TaskService(root)
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(
        title="gemini", origin="test", created_by="test"
    )
    task = service.ledger.create_task(
        mission.id,
        title,
        acceptance=("pytest tests/x.py : le code de sortie vaut 0",),
        owner="amelia",
        checks=(TaskCheck(cmd="pytest tests/x.py"),),
    )
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def launcher(root: Path, gemini_bin: Path, spawned: Spawned) -> tuple[WorkerLauncher, GeminiExec]:
    policy = load_orchestrator_policy(root).policy
    assert policy is not None
    exec_ = GeminiExec(gemini_bin)
    return WorkerLauncher(TaskService(root), policy, exec_=exec_, gemini=host_for(gemini_bin, spawned)), exec_


# ── orchestrator.host dans la politique ────────────────────────────────────────


def test_sans_orchestrator_host_rien_ne_change(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_policy(root, top="")
    policy = load_orchestrator_policy(root).policy
    assert policy is not None and policy.host == CLAUDE_CODE_HOST and policy.worker.host == CLAUDE_CODE_HOST
    assert policy.to_dict()["host"] == CLAUDE_CODE_HOST and claude_only_refusal(policy, "relecteur") == ""


def test_orchestrator_host_gemini_porte_le_worker_et_ferme_les_autres_roles(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_policy(root)
    policy = load_orchestrator_policy(root).policy
    assert policy is not None and policy.host == GEMINI_HOST and policy.worker.host == GEMINI_HOST
    for role in ("relecteur", "arbitre", "orchestrateur"):
        assert "n'est outillé que pour Claude Code" in claude_only_refusal(policy, role)
    # Un hôte de rôle explicite l'emporte sur celui du projet.
    write_policy(root, worker="      host: claude-code\n")
    explicit = load_orchestrator_policy(root).policy
    assert explicit is not None and explicit.host == GEMINI_HOST and explicit.worker.host == CLAUDE_CODE_HOST


def test_sans_bloc_worker_le_role_herite_de_l_hote_du_projet(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text("orchestrator:\n  host: gemini\n", encoding="utf-8")
    policy = load_orchestrator_policy(root).policy
    assert policy is not None and policy.worker.host == GEMINI_HOST and not policy.worker.enabled


@pytest.mark.parametrize(
    ("top", "worker", "error"),
    [
        ("  host: cursor\n", "", "orchestrator.host : 'cursor' inconnu"),
        ("  host: opencode\n", "", "orchestrator.host : 'opencode' inconnu"),
        ("  host: 3\n", "", "orchestrator.host doit être du texte"),
        ('  host: ""\n', "", "orchestrator.host : vide"),
        ("  host: '  '\n", "", "orchestrator.host : vide"),
        ("", '      host: ""\n', "orchestrator.roles.worker.host : vide"),
        ("  host: gemini\n", "      provider: deepseek\n", "ne s'applique pas à host: gemini"),
        ("  host: gemini\n", "      lean: true\n", "pas d'équivalent sur host: gemini"),
    ],
)
def test_un_hote_de_projet_mal_pose_rend_la_politique_illisible(
    tmp_path: Path, top: str, worker: str, error: str
) -> None:
    root = make_repo(tmp_path)
    write_policy(root, top=top, worker=worker)
    read = load_orchestrator_policy(root)
    assert read.policy is None and error in read.error


# ── le plan de lancement ───────────────────────────────────────────────────────


def test_plan_neuf_et_reprise() -> None:
    host = GeminiWorkerHost("gemini")
    plan = host.plan(
        SessionSpec(name="grimoire-T", prompt="fais", model="gemini-2.5-pro", permission_mode="acceptEdits")
    )
    assert not isinstance(plan, str) and plan.detached
    assert plan.argv == (
        "gemini",
        "--prompt=fais",
        "--output-format",
        "stream-json",
        "--approval-mode",
        "auto_edit",
        "--model=gemini-2.5-pro",
    )
    assert plan.environ()["GRIMOIRE_UNATTENDED"] == "1"
    resumed = host.plan(SessionSpec(name="n", prompt="-renvoi", permission_mode="plan", resume=SID))
    assert not isinstance(resumed, str)
    # Un message qui commence par « - » reste la valeur de --prompt, jamais une option ; l'identifiant, un seul jeton.
    assert resumed.argv[1] == "--prompt=-renvoi" and resumed.argv[-1] == f"--resume={SID}"
    assert "--resume" not in resumed.argv and not any(a.startswith("--model") for a in resumed.argv)


@pytest.mark.parametrize(
    "session_id",
    ["--yolo", "-x", "abc", "latest", "1", f"{SID} --yolo", f"{SID}\n", f"x{SID}", "ses-42", " " + SID],
)
def test_un_identifiant_de_reprise_hors_format_est_refuse(session_id: str) -> None:
    refusal = GeminiWorkerHost().plan(SessionSpec(name="n", prompt="p", permission_mode="plan", resume=session_id))
    assert isinstance(refusal, str) and "hors format pour --resume" in refusal


def test_l_environnement_du_worker_est_une_liste_fermee(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "FAKE_TOKEN_X": "t",
        "ANTHROPIC_API_KEY": "sk-ant",
        "OPENAI_API_KEY": "sk-oai",
        "DEEPSEEK_API_KEY": "sk-ds",
        "GH_TOKEN": "ghp",
        "GITHUB_TOKEN": "ghs",
        "AWS_SECRET_ACCESS_KEY": "aws",
        "GEMINI_CLI_SYSTEM_SETTINGS_PATH": "/tmp/ailleurs.json",
        "GEMINI_CLI_HOME": "/tmp/ailleurs",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": "/tmp/h",
        "GEMINI_API_KEY": "AIza-gemini",
        "GOOGLE_CLOUD_PROJECT": "projet",
        "HTTPS_PROXY": "http://proxy:3128",
        "PATH": "/usr/bin",
    }.items():
        monkeypatch.setenv(name, value)
    plan = GeminiWorkerHost().plan(SessionSpec(name="n", prompt="p", permission_mode="plan"))
    assert not isinstance(plan, str)
    env = plan.environ()
    leaked = sorted(
        k for k in env if k.endswith(("_API_KEY", "_TOKEN", "_SECRET_ACCESS_KEY")) and k != "GEMINI_API_KEY"
    )
    assert leaked == []
    assert not any(k.startswith(("GEMINI_CLI_", "GIT_CONFIG_")) for k in env)
    assert env["GEMINI_API_KEY"] == "AIza-gemini" and env["GOOGLE_CLOUD_PROJECT"] == "projet"
    assert env["PATH"] == "/usr/bin" and env["HTTPS_PROXY"] == "http://proxy:3128"
    assert env["GRIMOIRE_UNATTENDED"] == "1"
    assert plan.secret_keys == ("GEMINI_API_KEY",) and plan.traced_env()["GEMINI_API_KEY"] == "***"
    assert set(env) <= {*ENV_ALLOWED, *GEMINI_ENV_ALLOWED, "GRIMOIRE_UNATTENDED"}


@pytest.mark.parametrize(("mode", "expected"), sorted(GEMINI_APPROVAL_MODES.items()))
def test_le_mode_de_permission_se_traduit(mode: str, expected: str) -> None:
    plan = GeminiWorkerHost().plan(SessionSpec(name="n", prompt="p", permission_mode=mode))
    assert not isinstance(plan, str) and plan.argv[plan.argv.index("--approval-mode") + 1] == expected


@pytest.mark.parametrize("mode", ["auto", "", "yolo"])
def test_un_mode_sans_traduction_refuse_le_lancement(mode: str) -> None:
    refusal = GeminiWorkerHost().plan(SessionSpec(name="n", prompt="p", permission_mode=mode))
    assert isinstance(refusal, str) and "sans traduction pour Gemini CLI" in refusal


def test_worker_host_rend_l_hote_gemini() -> None:
    host = worker_host(GEMINI_HOST)
    assert isinstance(host, GeminiWorkerHost) and host.claim_host == "gemini-cli"
    assert host.session_host == HostId.GEMINI_CLI.value and host.handle(" 4242\n") == "4242" and host.handle("x") == ""


# ── le faux binaire : lancement, identifiant, reprise, arrêt, session morte ───


@POSIX_ONLY
def test_lancement_identifiant_et_resultat(tmp_path: Path, gemini_bin: Path, spawned: Spawned) -> None:
    host = host_for(gemini_bin, spawned)
    assert host.version(default_exec, tmp_path) == VERSION
    plan = host.plan(SessionSpec(name="grimoire-T", prompt="fais", permission_mode="acceptEdits"))
    assert not isinstance(plan, str)
    log = tmp_path / "run" / "T.gemini.jsonl"
    started = host.start(plan, tmp_path, log)
    assert started.pid and not started.error
    session = host.resolve(default_exec, tmp_path, str(started.pid), "grimoire-T", log=log)
    assert session and session == str(read_events(log)[0]["session_id"])
    marks = {"pid": str(started.pid), "pid_identity": host.identify(started.pid), "log": str(log)}
    assert wait_for(lambda: host.state(marks) == "ended")
    result = host.result(log)
    assert result.finished and result.status == "success" and result.text == "PR ouverte : #42"
    assert result.session_id == session and result.error == ""
    assert seen_argv(tmp_path)[0][:3] == ["--prompt=fais", "--output-format", "stream-json"]


@POSIX_ONLY
def test_reprise_par_resume_ne_relit_pas_l_ancien_init(tmp_path: Path, gemini_bin: Path, spawned: Spawned) -> None:
    host = host_for(gemini_bin, spawned)
    log = tmp_path / "T.gemini.jsonl"
    log.write_text(json.dumps({"type": "init", "session_id": "ancienne"}) + "\n", encoding="utf-8")
    offset = log.stat().st_size
    plan = host.plan(SessionSpec(name="n", prompt="renvoi", permission_mode="plan", resume=SID))
    assert not isinstance(plan, str)
    started = host.start(plan, tmp_path, log)
    assert host.resolve(default_exec, tmp_path, str(started.pid), "n", log=log, since=offset) == SID
    assert seen_argv(tmp_path)[-1][-1] == f"--resume={SID}"
    # Deux init différents depuis l'octet 0 : on ne choisit pas.
    assert wait_for(lambda: not spawned.alive(started.pid))
    assert host.resolve(default_exec, tmp_path, str(started.pid), "n", log=log) == ""


@POSIX_ONLY
def test_arret_d_une_session_vivante(
    tmp_path: Path, gemini_bin: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_mode(gemini_bin, "hang")
    host = host_for(gemini_bin, spawned)
    plan = host.plan(SessionSpec(name="n", prompt="p", permission_mode="acceptEdits"))
    assert not isinstance(plan, str)
    log = tmp_path / "T.gemini.jsonl"
    started = host.start(plan, tmp_path, log)
    identity = host.identify(started.pid)
    assert identity and host.resolve(default_exec, tmp_path, str(started.pid), "n", log=log, identity=identity)
    marks = {"pid": str(started.pid), "pid_identity": identity, "log": str(log)}
    assert host.state(marks) == "busy" and not host.result(log).finished
    assert host.state(marks, now=time.time() + 3600) == "silent"
    assert host.halt(default_exec, tmp_path, str(started.pid)) == (True, f", processus gemini {started.pid} arrêté")
    assert host.state(marks) == "ended"


@POSIX_ONLY
def test_session_morte_sans_init_detectee(
    tmp_path: Path, gemini_bin: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_mode(gemini_bin, "crash")
    host = host_for(gemini_bin, spawned)
    plan = host.plan(SessionSpec(name="n", prompt="p", permission_mode="acceptEdits"))
    assert not isinstance(plan, str)
    log = tmp_path / "T.gemini.jsonl"
    started = host.start(plan, tmp_path, log)
    began = time.monotonic()
    assert host.resolve(default_exec, tmp_path, str(started.pid), "n", log=log) == ""
    assert time.monotonic() - began < 8  # mort : on n'attend pas les 200 lectures
    assert host.state({"pid": str(started.pid), "log": str(log)}) == "ended"
    assert not host.result(log).finished


def test_sondes_sans_processus(tmp_path: Path) -> None:
    host = GeminiWorkerHost(alive=lambda _p: False, kill=lambda _p: False)
    assert host.resolve(default_exec, tmp_path, "1", "n") == ""  # sans journal : rien à lire
    assert host.state({"pid": "x"}) == "ended" and host.stop("x") is False
    assert host.halt(default_exec, tmp_path, "") == (False, ", processus gemini ? NON arrêté")
    alive = GeminiWorkerHost(alive=lambda _p: True, identify=lambda _p: "100|/usr/bin/node")
    marks = {"pid": "12", "pid_identity": "100|/usr/bin/node"}
    assert alive.state(marks) == "busy"  # vivant, pas encore de journal (jamais le dossier courant)
    # Le même pid, un autre processus (heure de démarrage ou exécutable différents) : mort.
    assert alive.state({**marks, "pid_identity": "99|/usr/bin/node"}) == "ended"
    assert alive.state({**marks, "pid_identity": "100|/usr/bin/python3"}) == "ended"
    # Aucune identité notée au lancement : on n'affirme pas qu'il vit.
    assert alive.state({"pid": "12"}) == "ended"
    assert read_events(tmp_path / "absent.jsonl") == []
    broken = tmp_path / "x.jsonl"
    broken.write_text('pas du json\n[1]\n{"type": "error", "message": "quota"}\n', encoding="utf-8")
    assert GeminiWorkerHost.result(broken).error == "quota" and not GeminiWorkerHost.result(broken).finished
    assert GeminiWorkerHost().version(lambda *_a: ExecResult(1, "", "absent"), tmp_path) == ""


@POSIX_ONLY
def test_l_identite_d_un_processus_est_son_heure_de_demarrage_et_son_executable(tmp_path: Path) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        identity = process_identity(proc.pid)
        assert identity and identity == process_identity(proc.pid)  # stable tant que le processus vit
        started, _, exe = identity.partition("|")
        assert started and Path(exe).name == Path(os.path.realpath(sys.executable)).name
        host = GeminiWorkerHost()
        assert host.state({"pid": str(proc.pid), "pid_identity": identity}) == "busy"
        # Même pid, autre heure de démarrage (pid recyclé) ou autre exécutable : mort.
        assert host.state({"pid": str(proc.pid), "pid_identity": f"0|{exe}"}) == "ended"
        assert host.state({"pid": str(proc.pid), "pid_identity": f"{started}|/usr/bin/gemini"}) == "ended"
        assert host.state({"pid": str(proc.pid)}) == "ended"
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert process_identity(proc.pid) == "" and process_identity(0) == ""


@pytest.mark.parametrize(
    ("noted", "current", "same"),
    [
        ("100|/usr/bin/node", "100|/usr/bin/node", True),
        # ``#!/usr/bin/env node`` : même processus, ``env`` remplacé par l'interpréteur.
        ("100|/usr/bin/env", "100|/usr/bin/node", True),
        ("Mon Nov 24 14:49:00 2026|env", "Mon Nov 24 14:49:00 2026|/usr/local/bin/node", True),
        # Aucun autre changement d'exécutable, et jamais vers ``env`` ni sans la même heure de démarrage.
        ("100|/usr/bin/node", "100|/usr/bin/python3", False),
        ("100|/usr/bin/node", "100|/usr/bin/env", False),
        ("100|/usr/bin/env", "101|/usr/bin/node", False),
        ("|/usr/bin/env", "|/usr/bin/node", False),
        ("100", "100|/usr/bin/node", False),
        ("100|/usr/bin/env", "100", False),
        ("", "100|/usr/bin/node", False),
        ("100|/usr/bin/node", "", False),
        ("", "", False),
    ],
)
def test_l_identite_admet_la_seule_transition_depuis_env(noted: str, current: str, same: bool) -> None:
    assert same_process_identity(noted, current) is same


def test_un_env_multi_appel_est_reconnu(monkeypatch: pytest.MonkeyPatch) -> None:
    import grimoire.missions.gemini_host as module

    monkeypatch.setattr(module, "_env_executables", lambda: frozenset({"/usr/lib/cargo/bin/coreutils"}))
    assert same_process_identity("7|/usr/lib/cargo/bin/coreutils", "7|/usr/bin/node")
    assert not same_process_identity("7|/usr/lib/cargo/bin/ls", "7|/usr/bin/node")


def test_l_identite_se_releve_de_nouveau_a_l_init() -> None:
    def host(*readings: str | Exception) -> GeminiWorkerHost:
        queue = list(readings)

        def identify(_pid: int) -> str:
            value = queue.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        return GeminiWorkerHost(identify=identify)

    assert host("5|/usr/bin/node").settled_identity(9, "5|/usr/bin/env") == "5|/usr/bin/node"
    assert host("5|/usr/bin/env").settled_identity(9, "5|/usr/bin/env") == "5|/usr/bin/env"
    # Mort entre-temps, pid repris, lecture en échec : l'identité notée reste, la sonde suivante tranchera.
    assert host("").settled_identity(9, "5|/usr/bin/env") == "5|/usr/bin/env"
    assert host("6|/usr/bin/node").settled_identity(9, "5|/usr/bin/env") == "5|/usr/bin/env"
    assert host(OSError("illisible")).settled_identity(9, "5|/usr/bin/env") == "5|/usr/bin/env"


@ENV_ONLY
def test_un_gemini_en_shebang_env_reste_vivant(tmp_path: Path, gemini_env_bin: Path, spawned: Spawned) -> None:
    set_mode(gemini_env_bin, "hang")
    host = host_for(gemini_env_bin, spawned)
    host.identify = EnvFirst()
    plan = host.plan(SessionSpec(name="n", prompt="p", permission_mode="acceptEdits"))
    assert not isinstance(plan, str)
    log = tmp_path / "T.gemini.jsonl"
    started = host.start(plan, tmp_path, log)
    noted = host.identify(started.pid)
    assert noted.endswith(f"|{os.path.realpath(ENV)}")
    # L'identité notée au retour de Popen (``env``) ne fait ni abandonner la résolution, ni déclarer mort le worker.
    session = host.resolve(default_exec, tmp_path, str(started.pid), "n", log=log, identity=noted)
    assert session
    assert host.state({"pid": str(started.pid), "pid_identity": noted, "log": str(log)}) == "busy"
    settled = host.settled_identity(started.pid, noted)
    assert settled.partition("|")[0] == noted.partition("|")[0]
    assert settled.partition("|")[2] == os.path.realpath(sys.executable)
    assert host.state({"pid": str(started.pid), "pid_identity": settled, "log": str(log)}) == "busy"
    assert host.stop(str(started.pid))
    assert host.state({"pid": str(started.pid), "pid_identity": noted, "log": str(log)}) == "ended"


LINUX_ONLY = pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="/proc")


@LINUX_ONLY
def test_un_zombie_n_a_pas_d_identite() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        assert wait_for(lambda: Path(f"/proc/{proc.pid}/stat").read_text().split(") ", 1)[1][0] == "Z")
        assert process_identity(proc.pid) == ""  # mort, pas encore récolté : rien d'affirmé
    finally:
        proc.wait(timeout=10)


@POSIX_ONLY
def test_l_identite_par_ps_hors_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    import grimoire.missions.gemini_host as module

    if shutil.which("ps") is None:
        pytest.skip("ps absent")
    monkeypatch.setattr(module, "_has_proc", lambda: False)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        identity = process_identity(proc.pid)
        started, sep, exe = identity.partition("|")
        assert sep and ":" in started and exe  # ``lstart`` porte des ``:`` : le séparateur n'en est pas un
        assert same_process_identity(identity, process_identity(proc.pid))
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert process_identity(proc.pid) == ""
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(OSError("ps")))
    assert process_identity(os.getpid()) == ""


@POSIX_ONLY
def test_l_existence_d_un_pid() -> None:
    import grimoire.missions.gemini_host as module

    assert module._process_exists(os.getpid()) and not module._process_exists(0)
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    assert not module._process_exists(proc.pid)  # récolté : plus personne sous ce pid
    if os.getuid() != 0:
        assert module._process_exists(1)  # un autre utilisateur : il existe, son identité dira s'il est le nôtre


def test_une_identite_illisible_compte_pour_mort() -> None:
    def broken(_pid: int) -> str:
        raise OSError("illisible")

    host = GeminiWorkerHost(alive=lambda _p: True, identify=broken)
    assert host.state({"pid": "12", "pid_identity": "100|/usr/bin/node"}) == "ended"


def test_windows_refuse_le_lancement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import grimoire.missions.gemini_host as module

    monkeypatch.setattr(module, "_POSIX", False)
    plan = GeminiWorkerHost().plan(SessionSpec(name="n", prompt="p", permission_mode="plan"))
    assert not isinstance(plan, str)
    assert "Windows" in GeminiWorkerHost().start(plan, tmp_path, tmp_path / "l").error


# ── le frein : sans preuve, rien ne part ───────────────────────────────────────


def test_le_frein_se_prouve_pour_la_version_exacte(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "illisible" in GeminiWorkerHost.proof_refusal("")
    prove(monkeypatch, tmp_path, "0.63.0")
    assert "frein non prouvé pour Gemini CLI 0.64.0" in GeminiWorkerHost.proof_refusal(VERSION)
    prove(monkeypatch, tmp_path, VERSION, ok=False)  # une sonde en échec ne prouve rien
    assert "#1304" in GeminiWorkerHost.proof_refusal(VERSION)
    prove(monkeypatch, tmp_path, VERSION)
    assert GeminiWorkerHost.proof_refusal(VERSION) == ""


@POSIX_ONLY
def test_sans_preuve_aucun_worker_ne_part_et_la_raison_est_dite(
    projet: Path, gemini_bin: Path, spawned: Spawned
) -> None:
    tid = ready(projet)
    run, exec_ = launcher(projet, gemini_bin, spawned)
    (outcome,) = run((Admit(tid),))
    assert outcome.status == "skipped"
    assert (
        outcome.detail == f"worker Gemini CLI non lancé : frein non prouvé pour Gemini CLI {VERSION} : "
        "aucune capture d'un refus BeforeTool honoré pour cette version (#1304)"
    )
    task = TaskService(projet).require(tid)
    assert task.status is TaskState.READY and task.claim is None and not spawned.procs
    assert [a[1:] for a, _c, _e in exec_.calls if a[0] == str(gemini_bin)] == [["--version"]]
    assert not [a for a, _c, _e in exec_.calls if a[0] == "claude"]  # aucun repli sur Claude Code


# ── le worker Gemini dans le tick ──────────────────────────────────────────────


def _launched(projet: Path, gemini_bin: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    prove(monkeypatch, tmp_path)
    tid = ready(projet)
    run, _exec = launcher(projet, gemini_bin, spawned)
    (outcome,) = run((Admit(tid),))
    assert outcome.status == "launched", outcome.detail
    return tid


@POSIX_ONLY
def test_le_worker_gemini_part_s_inscrit_et_se_rattache(
    projet: Path, gemini_bin: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_mode(gemini_bin, "hang")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fuite")
    monkeypatch.setenv("FAKE_TOKEN_X", "jeton-fuite")
    tid = _launched(projet, gemini_bin, spawned, monkeypatch, tmp_path)
    service = TaskService(projet)
    payload = launched_sessions(service)[tid]
    assert payload["host"] == GEMINI_HOST and payload["host_version"] == VERSION and payload["pid"].isdigit()
    # L'identité exacte du processus est notée au lancement : c'est elle que les sondes comparent.
    assert payload["pid_identity"] and payload["pid_identity"] == process_identity(int(payload["pid"]))
    # Le processus détaché n'a reçu ni la clé d'un autre fournisseur ni un jeton non requis.
    (env,) = seen_env(tmp_path)
    assert "ANTHROPIC_API_KEY" not in env and "FAKE_TOKEN_X" not in env and env["GRIMOIRE_UNATTENDED"] == "1"
    assert payload["log"] == str(gemini_log(projet, tid)) and payload["permission_mode"] == "acceptEdits"
    assert payload["session_id"] and payload["provider"] == ""
    task = service.require(tid)
    assert task.status is TaskState.CLAIMED and task.claim is not None
    assert task.claim.session_id == payload["session_id"] and task.claim.host_id == "gemini-cli"
    (argv,) = seen_argv(tmp_path)
    assert argv[0].startswith(f"--prompt=Tu es le worker de la tâche {tid}") and "task attach" not in argv[0]
    assert argv[argv.index("--approval-mode") + 1] == "auto_edit"


@ENV_ONLY
def test_le_worker_gemini_en_shebang_env_part_et_garde_l_interpreteur(
    projet: Path, gemini_env_bin: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Relecture du 2026-11-24 : l'identité lue au retour de Popen vaut ``env`` ; le worker ne doit pas mourir."""
    set_mode(gemini_env_bin, "hang")
    prove(monkeypatch, tmp_path)
    tid = ready(projet)
    run, exec_ = launcher(projet, gemini_env_bin, spawned)
    run._gemini.identify = EnvFirst()
    (outcome,) = run((Admit(tid),))
    assert outcome.status == "launched", outcome.detail
    service = TaskService(projet)
    payload = launched_sessions(service)[tid]
    # Le ledger garde l'identité relevée à l'init : l'interpréteur, plus ``env``.
    assert payload["pid_identity"].partition("|")[2] == os.path.realpath(sys.executable)
    host = host_for(gemini_env_bin, spawned)
    assert renew_live_workers(service, exec_, gemini=host) == (tid,)
    assert release_ended_workers(service, exec_, gemini=host) == ()


@POSIX_ONLY
def test_battement_arret_au_repos_reprise_et_fin(
    projet: Path, gemini_bin: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_mode(gemini_bin, "hang")
    tid = _launched(projet, gemini_bin, spawned, monkeypatch, tmp_path)
    host = host_for(gemini_bin, spawned)
    exec_ = GeminiExec(gemini_bin)
    service = TaskService(projet)
    assert renew_live_workers(service, exec_, gemini=host) == (tid,)
    assert release_ended_workers(service, exec_, gemini=host) == ()
    assert not [a for a, _c, _e in exec_.calls if a[:2] == ["claude", "agents"]]  # aucune sonde Claude Code
    assert resume_provider_failed_workers(service, exec_) == ()  # aucun journal Claude Code à relire
    ledger = service.ledger
    ledger.transition_task(tid, TaskState.RUNNING, actor_id="worker")
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="worker")
    first_pid = int(launched_sessions(service)[tid]["pid"])
    assert halt_idle_workers(TaskService(projet), exec_, gemini=host) == (tid,)
    assert not spawned.alive(first_pid)
    halted = [e for e in ledger.events_for(tid) if e.event_type == WORKER_HALTED_EVENT][-1]
    assert halted.payload["pid"] == str(first_pid)
    # Le relecteur renvoie : la conversation reprend par --resume, sur le même worktree.
    service = TaskService(projet)
    launch = launched_sessions(service)[tid]
    result = resume_worker(service, exec_, "claude", tid, launch, "renvoi", gemini=host)
    assert result.returncode == 0, result.stderr
    assert wait_for(lambda: len(seen_argv(tmp_path)) == 2)  # le processus repris a démarré
    assert seen_argv(tmp_path)[-1][0] == "--prompt=renvoi"
    assert seen_argv(tmp_path)[-1][-1] == f"--resume={launch['session_id']}"
    resumed = [e for e in ledger.events_for(tid) if e.event_type == WORKER_RESUMED_EVENT][-1]
    assert resumed.payload["host"] == GEMINI_HOST and resumed.payload["pid"] == result.stdout
    assert resumed.payload["pid_identity"] == process_identity(int(result.stdout))
    # Le processus repris meurt : la tâche, de nouveau tenue, est rendue.
    ledger.transition_task(tid, TaskState.RUNNING, actor_id="worker")
    assert host.stop(result.stdout)
    assert release_ended_workers(TaskService(projet), exec_, gemini=host) == (tid,)
    assert TaskService(projet).require(tid).status is TaskState.READY


@POSIX_ONLY
def test_reprise_refusee_sans_preuve_ou_sans_session(
    projet: Path, gemini_bin: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tid = _launched(projet, gemini_bin, spawned, monkeypatch, tmp_path)
    host = host_for(gemini_bin, spawned)
    service = TaskService(projet)
    launch = launched_sessions(service)[tid]
    exec_ = GeminiExec(gemini_bin)
    no_session = resume_worker(service, exec_, "claude", tid, {**launch, "session_id": ""}, "m", gemini=host)
    assert no_session.returncode == 1 and "aucune session" in no_session.stderr
    launches = len(seen_argv(tmp_path))
    forged = resume_worker(service, exec_, "claude", tid, {**launch, "session_id": "--yolo"}, "m", gemini=host)
    assert forged.returncode == 1 and "hors format pour --resume" in forged.stderr
    assert len(seen_argv(tmp_path)) == launches  # rien n'est parti
    bad_mode = resume_worker(service, exec_, "claude", tid, launch, "m", permission_mode="auto", gemini=host)
    assert bad_mode.returncode == 1 and "sans traduction" in bad_mode.stderr
    prove(monkeypatch, tmp_path, VERSION, ok=False)
    refused = resume_worker(service, exec_, "claude", tid, launch, "m", gemini=host)
    assert refused.returncode == 1 and "frein non prouvé" in refused.stderr
    assert not [a for a, _c, _e in exec_.calls if a[0] == "claude"]  # jamais un repli sur Claude Code


@POSIX_ONLY
def test_sans_identite_lisible_rien_ne_reste_lance(
    projet: Path, gemini_bin: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_mode(gemini_bin, "hang")
    prove(monkeypatch, tmp_path)
    tid = ready(projet)
    run, exec_ = launcher(projet, gemini_bin, spawned)
    run._gemini.identify = lambda _pid: ""
    (outcome,) = run((Admit(tid),))
    assert outcome.status == "failed" and "sans identité lisible" in outcome.detail
    assert spawned.procs and not any(spawned.alive(pid) for pid in spawned.procs)  # arrêté, pas laissé sans suivi
    # La reprise : même règle, le processus repris est arrêté et la reprise échoue.
    tid = _launched(projet, gemini_bin, spawned, monkeypatch, tmp_path)
    service = TaskService(projet)
    launch = launched_sessions(service)[tid]
    host = host_for(gemini_bin, spawned)
    host.identify = lambda _pid: ""
    before = set(spawned.procs)
    resumed = resume_worker(service, exec_, "claude", tid, launch, "renvoi", gemini=host)
    assert resumed.returncode == 1 and "sans identité lisible" in resumed.stderr
    (pid,) = set(spawned.procs) - before
    assert not spawned.alive(pid)
    assert not [e for e in service.ledger.events_for(tid) if e.event_type == WORKER_RESUMED_EVENT]


@POSIX_ONLY
def test_identite_introuvable_le_processus_est_arrete(
    projet: Path, gemini_bin: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_mode(gemini_bin, "crash")
    prove(monkeypatch, tmp_path)
    tid = ready(projet)
    run, _exec = launcher(projet, gemini_bin, spawned)
    (outcome,) = run((Admit(tid),))
    assert outcome.status == "failed" and "aucun événement init" in outcome.detail
    assert TaskService(projet).require(tid).status is TaskState.READY
    assert tid not in launched_sessions(TaskService(projet))


@POSIX_ONLY
def test_worktree_sans_hook_du_kit_rien_ne_part(
    tmp_path: Path, gemini_bin: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_repo(tmp_path)
    write_policy(root)
    prove(monkeypatch, tmp_path)
    tid = ready(root)
    run, _exec = launcher(root, gemini_bin, spawned)
    (outcome,) = run((Admit(tid),))
    assert outcome.status == "skipped" and "hook BeforeTool Grimoire" in outcome.detail and not spawned.procs
    assert TaskService(root).require(tid).status is TaskState.READY
    assert "grimoire host sync" in gemini_worktree_refusal(tmp_path)
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / "settings.json").write_text('{"hooks": {"BeforeTool": [{"command": "rm"}]}}', "utf-8")
    assert gemini_worktree_refusal(tmp_path)  # un hook étranger ne vaut pas celui du kit


@POSIX_ONLY
def test_un_palier_sans_modele_gemini_fait_attendre(
    projet: Path, gemini_bin: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write_policy(projet, worker="      tier: strong\n")
    monkeypatch.setattr("grimoire.missions.gemini_lifecycle.gemini_tier_model", lambda _root, _tier: "")
    prove(monkeypatch, tmp_path)
    tid = ready(projet)
    run, _exec = launcher(projet, gemini_bin, spawned)
    (outcome,) = run((Admit(tid),))
    assert outcome.status == "deferred" and "aucun modèle Gemini pour le palier strong" in outcome.detail
    assert not spawned.procs


@POSIX_ONLY
def test_le_janitor_arrete_le_processus_gemini(
    projet: Path, gemini_bin: Path, spawned: Spawned, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from grimoire.missions.janitor import Janitor

    set_mode(gemini_bin, "hang")
    tid = _launched(projet, gemini_bin, spawned, monkeypatch, tmp_path)
    policy = load_orchestrator_policy(projet).policy
    assert policy is not None
    host = host_for(gemini_bin, spawned)
    janitor = Janitor(TaskService(projet), policy, exec_=GeminiExec(gemini_bin), gemini=host)
    session = launched_sessions(TaskService(projet))[tid]["session"]
    assert janitor._session(tid) == ("done", f"worker gemini {session} arrêté")
    assert not spawned.alive(int(launched_sessions(TaskService(projet))[tid]["pid"]))
    identity = str(launched_sessions(TaskService(projet))[tid]["pid_identity"])
    refusing = GeminiWorkerHost(alive=lambda _p: True, kill=lambda _p: False, identify=lambda _p: identity)
    assert Janitor(TaskService(projet), policy, gemini=refusing)._session(tid)[0] == "failed"
    # Le pid noté porte désormais un autre processus : il n'est pas tué, le worker est dit arrêté.
    recycled = GeminiWorkerHost(alive=lambda _p: True, kill=lambda _p: False, identify=lambda _p: "1:/bin/autre")
    assert Janitor(TaskService(projet), policy, gemini=recycled)._session(tid)[0] == "done"


def test_le_tick_ne_lance_ni_relecteur_ni_arbitre_ni_orchestrateur_sur_gemini(tmp_path: Path) -> None:
    from grimoire.missions import orchestrate

    root = make_repo(tmp_path)
    write_policy(root)
    policy = load_orchestrator_policy(root).policy
    assert policy is not None
    attention: list[str] = []
    assert orchestrate._host_refused(policy, "arbitre", attention) and "arbitre non lancé" in attention[0]
    write_policy(root, top="")
    default = load_orchestrator_policy(root).policy
    assert default is not None and not orchestrate._host_refused(default, "arbitre", attention)
    assert len(attention) == 1
