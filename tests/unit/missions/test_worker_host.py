"""Lot L4 (#1281) : le worker multi-hôte — interface ``WorkerHost``, worker opencode, ``roles.worker.host``.

Aucun vrai ``opencode`` ni ``claude`` : ``FakeOpencode`` répond à ``opencode --version``, ``debug config`` et
``session list`` ; le processus détaché est un faux ``spawn`` qui note l'argv et l'environnement. La garde du kit
est prouvée par la liste ``plugin`` que rend ``opencode debug config`` (capture réelle de la 1.17.11 : une entrée
``file:///…/.opencode/plugin/<nom>.js`` par plugin local).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from lot2_doubles import FakeHost, make_repo

from grimoire.missions.admission import Admit
from grimoire.missions.launcher import (
    LAUNCH_FAILED_EVENT,
    LAUNCHED_EVENT,
    WORKER_DEFERRED_EVENT,
    WORKER_HALTED_EVENT,
    WORKER_RESUMED_EVENT,
    ExecResult,
    WorkerLauncher,
    halt_idle_workers,
    launched_sessions,
    release_ended_workers,
    renew_live_workers,
    resume_provider_failed_workers,
    resume_worker,
)
from grimoire.missions.orchestrator_policy import load_orchestrator_policy
from grimoire.missions.schemas import TaskCheck, TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import find_task_worktree
from grimoire.missions.worker_host import (
    CLAUDE_CODE_HOST,
    OPENCODE_HOST,
    SILENCE_S,
    ClaudeCodeWorkerHost,
    LaunchPlan,
    OpencodeRoute,
    OpencodeWorkerHost,
    SessionSpec,
    SpawnResult,
    opencode_route,
    prepare_opencode_worktree,
    worker_host,
)
from grimoire.providers.secrets import set_secret

POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
REGISTRY = Path("_grimoire/standard/llm-provider-registry.yaml")
SECRET = "sk-secret-opencode-de-test"  # noqa: S105 - valeur factice, jamais une vraie clé
PLUGIN = Path(".opencode/plugin/grimoire-guard.js")
KIT_PLUGIN = (
    '// grimoire:managed — régénéré\nexport const G = async () => ({ "tool.execute.before": async () => {} })\n'
)

_REGISTRE = """\
$schema: "grimoire-llm-provider-registry/v1"
metadata: {project: demo, owner: "", policy: x}
providers:
  - id: deepseek
    enabled: true
    provider_type: hosted
    allowed_capabilities: [code]
    default_models: [deepseek-v4-flash]
    currency: api
    anthropic_base_url: https://api.deepseek.com/anthropic
    models:
      - {id: deepseek-v4-flash, tier: cheap}
      - {id: deepseek-v4-pro, tier: mid}
    data_policy: {allowed_data_classes: [project-source], forbidden_data_classes: [], retention_notes: ""}
    fallback_order: []
    audit: {log_prompts: false, log_metadata: true}
  - id: moonshot
    enabled: true
    provider_type: hosted
    allowed_capabilities: [code]
    default_models: [kimi-k2.6]
    currency: api
    models:
      - {id: kimi-k2.6, tier: cheap}
    data_policy: {allowed_data_classes: [project-source], forbidden_data_classes: [], retention_notes: ""}
    fallback_order: []
    audit: {log_prompts: false, log_metadata: true}
  - id: ollama
    enabled: true
    provider_type: local
    allowed_capabilities: [code]
    default_models: [qwen3-coder]
    currency: none
    anthropic_base_url: http://localhost:11434
    models:
      - {id: qwen3-coder, tier: cheap}
    data_policy: {allowed_data_classes: [project-source], forbidden_data_classes: [], retention_notes: ""}
    fallback_order: []
    audit: {log_prompts: false, log_metadata: true}
  - id: lointain
    enabled: true
    provider_type: local
    allowed_capabilities: [code]
    default_models: [m]
    currency: none
    anthropic_base_url: http://10.0.0.9:11434
    models:
      - {id: m, tier: cheap}
    data_policy: {allowed_data_classes: [project-source], forbidden_data_classes: [], retention_notes: ""}
    fallback_order: []
    audit: {log_prompts: false, log_metadata: true}
  - id: openai
    enabled: true
    provider_type: hosted
    allowed_capabilities: [code]
    default_models: [gpt-5]
    currency: api
    models:
      - {id: gpt-5, tier: cheap}
    data_policy: {allowed_data_classes: [project-source], forbidden_data_classes: [], retention_notes: ""}
    fallback_order: []
    audit: {log_prompts: false, log_metadata: true}
  - id: kimi
    enabled: true
    provider_type: hosted
    allowed_capabilities: [code]
    default_models: []
    currency: api
    models: []
    data_policy: {allowed_data_classes: [project-source], forbidden_data_classes: [], retention_notes: ""}
    fallback_order: []
    audit: {log_prompts: false, log_metadata: true}
routing: {default_data_class: project-source, default_provider: deepseek, default_fallback_chain: [], require_capability_match: true, require_data_policy_match: true}
"""


class FakeOpencode(FakeHost):
    """Le faux ``claude``/``gh`` des lots 2 à 4, plus un faux ``opencode`` et un faux ``spawn``."""

    def __init__(self) -> None:
        super().__init__()
        self.spawned: list[tuple[list[str], Path, dict[str, str], Path]] = []
        self.sessions: list[dict[str, Any]] = []
        self.loaded = True  # ``debug config`` liste le plugin de garde du worktree
        self.debug_fails = False
        self.list_fails = False
        self.spawn_error = ""
        self.register = True  # le lancement crée la session que ``session list`` rendra
        self.live: set[int] = set()
        self.killed: list[int] = []

    def __call__(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None) -> ExecResult:
        if argv[0] != "opencode":
            return super().__call__(argv, cwd, env)
        self.calls.append((list(argv), cwd, env))
        if list(argv[1:]) == ["--version"]:
            return ExecResult(0, "1.17.11\n")
        if list(argv[1:3]) == ["debug", "config"]:
            if self.debug_fails:
                return ExecResult(1, "", "config illisible")
            plugins = [(cwd / PLUGIN).resolve().as_uri()] if self.loaded else []
            return ExecResult(0, json.dumps({"plugin": plugins, "username": "x"}))
        if list(argv[1:3]) == ["session", "list"]:
            if self.list_fails:
                return ExecResult(1, "", "base illisible")
            return ExecResult(0, json.dumps(self.sessions))
        return ExecResult(2, "", "sous-commande inconnue")

    def spawn(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str], log: Path) -> SpawnResult:
        self.spawned.append((list(argv), cwd, dict(env), log))
        if self.spawn_error:
            return SpawnResult(0, self.spawn_error)
        pid = 7000 + len(self.spawned)
        self.live.add(pid)
        if self.register and "--title" in argv:
            self.sessions.append(
                {
                    "id": f"ses_{pid}",
                    "title": argv[argv.index("--title") + 1],
                    "directory": str(cwd),
                    "projectId": "global",
                    "created": 1,
                    "updated": 1,
                }
            )
        return SpawnResult(pid)

    def kill(self, pid: int) -> bool:
        self.killed.append(pid)
        self.live.discard(pid)
        return True

    def host(self) -> OpencodeWorkerHost:
        return OpencodeWorkerHost(
            spawn=self.spawn,
            alive=lambda pid: pid in self.live,
            kill=self.kill,
            resolve_attempts=2,
            resolve_pause_s=0.0,
        )


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


def write_policy(
    root: Path,
    extra: str = "      host: opencode\n      provider: deepseek\n      tier: cheap\n",
    mode: str = "acceptEdits",
) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    worker:\n      enabled: true\n      wip: 3\n      sessions_per_day: 9\n"
        f"      permission_mode: {mode}\n{extra}",
        encoding="utf-8",
    )


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    root = make_repo(tmp_path)
    write_policy(root)
    (root / REGISTRY).write_text(_REGISTRE, encoding="utf-8")
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)
    return root


@pytest.fixture
def fake() -> FakeOpencode:
    return FakeOpencode()


def ready(root: Path, title: str = "travail", *, v0: bool = True) -> str:
    service = TaskService(root)
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(
        title="L4", origin="test", created_by="test"
    )
    checks = (TaskCheck(cmd="pytest tests/x.py"),) if v0 else ()
    acceptance = ("pytest tests/x.py : le code de sortie vaut 0",) if v0 else ("le code est propre",)
    task = service.ledger.create_task(mission.id, title, acceptance=acceptance, owner="amelia", checks=checks)
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def launcher(root: Path, fake: FakeOpencode) -> WorkerLauncher:
    policy = load_orchestrator_policy(root).policy
    assert policy is not None
    return WorkerLauncher(TaskService(root), policy, exec_=fake, opencode=fake.host())


@pytest.fixture
def plugin_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le worktree reçoit le plugin de garde du kit (la synchronisation réelle est testée à part)."""
    from grimoire.bridges.schemas import HostId
    from grimoire.hosts import sync

    real = sync.sync_host_surfaces

    def fake_sync(wt: Path, **kw: Any) -> Any:
        if HostId.OPENCODE not in (kw.get("only") or ()):
            return real(wt, **kw)
        (wt / PLUGIN).parent.mkdir(parents=True, exist_ok=True)
        (wt / PLUGIN).write_text(KIT_PLUGIN, encoding="utf-8")
        (wt / "opencode.json").write_text("{}", encoding="utf-8")
        from grimoire.hosts.sync import HostSyncOutcome

        return HostSyncOutcome(("opencode",))

    monkeypatch.setattr("grimoire.hosts.sync.sync_host_surfaces", fake_sync)
    monkeypatch.setattr("grimoire.hosts.emitters.base.MANAGED_MARKER", "grimoire:managed")


def launch(root: Path, fake: FakeOpencode, tid: str) -> Any:
    (outcome,) = launcher(root, fake)((Admit(tid),))
    return outcome


# ── roles.worker.host dans la politique ─────────────────────────────────────────


def test_sans_host_le_worker_et_le_relecteur_restent_sur_claude_code(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    write_policy(root, "")
    policy = load_orchestrator_policy(root).policy
    assert policy is not None
    assert policy.worker.host == CLAUDE_CODE_HOST and policy.reviewer.host == CLAUDE_CODE_HOST
    assert policy.worker.to_dict()["host"] == "claude-code" and policy.reviewer.to_dict()["host"] == "claude-code"


@pytest.mark.parametrize(
    ("extra", "error"),
    [
        ("      host: opencode\n", "provider est obligatoire avec host: opencode"),
        ("      host: gemini\n", "'gemini' inconnu"),
        ("      host: 3\n", "doit être du texte"),
    ],
)
def test_un_host_mal_pose_rend_la_politique_illisible(tmp_path: Path, extra: str, error: str) -> None:
    root = make_repo(tmp_path)
    write_policy(root, extra)
    read = load_orchestrator_policy(root)
    assert read.policy is None and error in read.error


def test_le_relecteur_ne_passe_pas_sur_opencode_dans_ce_lot(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    reviewer:\n      enabled: true\n      permission_mode: acceptEdits\n"
        "      host: opencode\n",
        encoding="utf-8",
    )
    read = load_orchestrator_policy(root)
    assert read.policy is None and "reviewer.host" in read.error and "claude-code" in read.error


def test_worker_host_rend_l_hote_nomme() -> None:
    assert isinstance(worker_host("opencode"), OpencodeWorkerHost)
    assert isinstance(worker_host(""), ClaudeCodeWorkerHost) and worker_host("claude-code").name == CLAUDE_CODE_HOST


# ── ClaudeCodeWorkerHost : l'argv d'avant ───────────────────────────────────────


def test_claude_code_plan_neuf_et_reprise() -> None:
    host = ClaudeCodeWorkerHost()
    fresh = host.plan(
        SessionSpec(
            name="grimoire-T", prompt="p", model="opus", permission_mode="plan", lean=True, settings=Path("/s.json")
        )
    )
    assert fresh.argv == (
        "claude",
        "--bg",
        "--name",
        "grimoire-T",
        "--settings",
        "/s.json",
        "--strict-mcp-config",
        "--tools=Bash,Read,Edit,Write,Glob,Grep",
        "--model",
        "opus",
        "--permission-mode",
        "plan",
        "p",
    )
    assert fresh.environ() == {"GRIMOIRE_UNATTENDED": "1"} and not fresh.detached
    resumed = host.plan(SessionSpec(name="grimoire-T", prompt="m", model="opus", resume="abc"))
    assert resumed.argv == ("claude", "--bg", "--resume", "abc", "m")
    assert host.handle("backgrounded · 0d310bc4 (running)") == "0d310bc4" and host.handle("rien") == ""


# ── OpencodeWorkerHost : argv, environnement, clé ───────────────────────────────


ROUTE = OpencodeRoute("deepseek", "deepseek", "deepseek-v4-flash", key_var="DEEPSEEK_API_KEY", source="registry")


def test_opencode_plan_la_cle_ne_passe_que_par_l_environnement(projet: Path) -> None:
    plan = OpencodeWorkerHost().plan(
        SessionSpec(name="grimoire-T", prompt="consigne", permission_mode="acceptEdits", opencode=ROUTE)
    )
    assert isinstance(plan, LaunchPlan)
    assert plan.argv == (
        "opencode",
        "run",
        "--format",
        "json",
        "--title",
        "grimoire-T",
        "--model",
        "deepseek/deepseek-v4-flash",
        "consigne",
    )
    assert "--pure" not in plan.argv and SECRET not in " ".join(plan.argv)
    env = plan.environ()
    assert env["DEEPSEEK_API_KEY"] == SECRET and plan.secret_keys == ("DEEPSEEK_API_KEY",)
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert config == {
        "model": "deepseek/deepseek-v4-flash",
        "provider": {"deepseek": {"options": {"apiKey": "{env:DEEPSEEK_API_KEY}"}}},
    }
    assert SECRET not in env["OPENCODE_CONFIG_CONTENT"] and env["GRIMOIRE_UNATTENDED"] == "1"
    assert plan.traced_env()["DEEPSEEK_API_KEY"] == "***" and plan.detached


@pytest.mark.parametrize(
    ("mode", "args"),
    [("plan", ("--agent", "plan")), ("bypassPermissions", ("--dangerously-skip-permissions",)), ("dontAsk", ())],
)
def test_opencode_traduit_le_mode_de_permission(projet: Path, mode: str, args: tuple[str, ...]) -> None:
    plan = OpencodeWorkerHost().plan(SessionSpec(name="n", prompt="p", permission_mode=mode, opencode=ROUTE))
    assert isinstance(plan, LaunchPlan)
    assert plan.argv[-1 - len(args) : -1] == args


def test_opencode_refuse_un_mode_sans_traduction_ou_sans_route_ou_sans_cle(tmp_path: Path) -> None:
    host = OpencodeWorkerHost()
    assert "sans traduction" in str(
        host.plan(SessionSpec(name="n", prompt="p", permission_mode="yolo", opencode=ROUTE))
    )
    assert "sans fournisseur" in str(host.plan(SessionSpec(name="n", prompt="p", permission_mode="default")))
    assert "clé absente" in str(host.plan(SessionSpec(name="n", prompt="p", permission_mode="default", opencode=ROUTE)))


def test_opencode_reprise_et_fournisseur_local(projet: Path) -> None:
    local = OpencodeRoute("ollama", "ollama", "qwen3-coder", base_url="http://localhost:11434/v1")
    plan = OpencodeWorkerHost().plan(
        SessionSpec(name="n", prompt="p", permission_mode="default", resume="ses_1", opencode=local)
    )
    assert isinstance(plan, LaunchPlan)
    assert plan.argv[4:8] == ("--session", "ses_1", "--model", "ollama/qwen3-coder") and "--title" not in plan.argv
    config = json.loads(plan.environ()["OPENCODE_CONFIG_CONTENT"])
    assert config["provider"]["ollama"]["options"] == {"baseURL": "http://localhost:11434/v1"}
    assert config["provider"]["ollama"]["npm"] == "@ai-sdk/openai-compatible" and plan.secret_keys == ()


# ── opencode_route : fournisseur, modèle, refus ─────────────────────────────────


def _task(root: Path, *, v0: bool = True) -> Any:
    return TaskService(root).require(ready(root, v0=v0))


def test_opencode_route_modele_de_la_table_puis_du_registre(projet: Path) -> None:
    task = _task(projet)
    route, why = opencode_route(projet, "deepseek", "mid", task)
    assert why == "" and route is not None and (route.model, route.source) == ("deepseek-v4-pro", "registry")
    (projet / POLICY).write_text(
        (projet / POLICY).read_text(encoding="utf-8")
        + "dispatch:\n  models:\n    opencode:\n      deepseek:\n        mid: deepseek-reasoner\n",
        encoding="utf-8",
    )
    route, _ = opencode_route(projet, "deepseek", "mid", task)
    assert route is not None and (route.model, route.source) == ("deepseek-reasoner", "table")
    route, _ = opencode_route(projet, "deepseek", "mid", task, model="deepseek-chat")
    assert route is not None and (route.model, route.source) == ("deepseek-chat", "policy")
    route, _ = opencode_route(projet, "deepseek", "strong", task)  # aucun strong : repli cheap du registre
    assert route is not None and route.model == "deepseek-v4-flash"


def test_opencode_route_moonshot_et_local(projet: Path) -> None:
    task = _task(projet)
    route, why = opencode_route(projet, "moonshot", "cheap", task)
    assert route is None and "MOONSHOT_API_KEY" in why
    set_secret("moonshot", "MOONSHOT_API_KEY", "sk-kimi")
    route, _ = opencode_route(projet, "moonshot", "cheap", task)
    assert route is not None and route.qualified_model == "moonshotai/kimi-k2.6" and route.key_var == "MOONSHOT_API_KEY"
    route, _ = opencode_route(projet, "ollama", "cheap", task)
    assert route is not None and route.base_url == "http://localhost:11434/v1" and route.key_var == ""
    route, why = opencode_route(projet, "ollama", "cheap", task, busy_providers=frozenset({"ollama"}))
    assert route is None and "un seul worker" in why


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("", "provider vide"),
        ("absent", "absent ou désactivé"),
        ("openai", "non pris en charge"),
        ("lointain", "boucle locale"),
        ("kimi", "KIMI_API_KEY"),
    ],
)
def test_opencode_route_refus_motives(projet: Path, provider: str, expected: str) -> None:
    route, why = opencode_route(projet, provider, "cheap", _task(projet))
    assert route is None and expected in why


def test_opencode_route_sans_modele(projet: Path) -> None:
    set_secret("kimi", "KIMI_API_KEY", "x")
    route, why = opencode_route(projet, "kimi", "cheap", _task(projet))
    assert route is None and "aucun modèle" in why


def test_opencode_route_respecte_la_politique_de_donnees(projet: Path) -> None:
    service = TaskService(projet)
    tid = ready(projet)
    task = service.require(tid)
    from dataclasses import replace

    route, why = opencode_route(projet, "deepseek", "cheap", replace(task, data_class="secrets"))
    assert route is None and why


# ── La garde du kit doit être chargée ───────────────────────────────────────────


def _plugin(wt: Path, text: str = KIT_PLUGIN) -> None:
    (wt / PLUGIN).parent.mkdir(parents=True, exist_ok=True)
    (wt / PLUGIN).write_text(text, encoding="utf-8")


def test_garde_prouvee_par_debug_config(tmp_path: Path, fake: FakeOpencode, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.hosts.emitters.base.MANAGED_MARKER", "grimoire:managed")
    host = fake.host()
    assert "absent" in host.guard_refusal(fake, tmp_path, {})
    _plugin(tmp_path, "export const X = 1\n")
    assert "n'est pas le plugin" in host.guard_refusal(fake, tmp_path, {})
    _plugin(tmp_path)
    assert host.guard_refusal(fake, tmp_path, {"OPENCODE_CONFIG_CONTENT": "{}", "DEEPSEEK_API_KEY": SECRET}) == ""
    (argv, _cwd, env) = fake.calls[-1]
    assert argv == ["opencode", "debug", "config"] and env == {"OPENCODE_CONFIG_CONTENT": "{}"}  # jamais la clé
    fake.loaded = False
    assert "ne charge pas" in host.guard_refusal(fake, tmp_path, {})
    fake.debug_fails = True
    assert "a échoué" in host.guard_refusal(fake, tmp_path, {})


def test_garde_debug_config_illisible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.hosts.emitters.base.MANAGED_MARKER", "grimoire:managed")
    _plugin(tmp_path)
    host = OpencodeWorkerHost()
    assert "illisible" in host.guard_refusal(lambda *_a: ExecResult(0, "pas du json"), tmp_path, {})
    assert "ne charge pas" in host.guard_refusal(lambda *_a: ExecResult(0, '{"plugin": "x"}'), tmp_path, {})
    rel = json.dumps({"plugin": [str(tmp_path / PLUGIN)]})
    assert host.guard_refusal(lambda *_a: ExecResult(0, rel), tmp_path, {}) == ""


def test_le_vrai_plugin_emis_par_le_kit_passe_la_garde(tmp_path: Path) -> None:
    """Le plugin que l'émetteur opencode produit porte le marqueur et ``tool.execute.before`` : il est reconnu."""
    from grimoire.hosts.emitters.opencode import _guard_plugin
    from grimoire.hosts.surface import Enforcement, HookEvent, HookSpec

    hook = HookSpec(
        event=HookEvent.PRE_TOOL_USE,
        decision="grimoire.guard",
        enforcement=Enforcement.BLOCKING,
        rationale="garde",
        timeout=10,
    )
    emitted = _guard_plugin(hook)
    _plugin(tmp_path, emitted.content)
    loaded = json.dumps({"plugin": [(tmp_path / PLUGIN).resolve().as_uri()]})
    assert OpencodeWorkerHost().guard_refusal(lambda *_a: ExecResult(0, loaded), tmp_path, {}) == ""


def test_prepare_le_worktree_pose_le_plugin_et_retire_la_racine(tmp_path: Path, plugin_ready: None) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    assert prepare_opencode_worktree(wt) == ""
    assert (wt / PLUGIN).is_file() and not (wt / "opencode.json").exists()
    assert (wt / ".opencode" / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert prepare_opencode_worktree(wt) == ""  # déjà posé : rien à refaire


def test_prepare_le_worktree_sans_plugin_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.hosts.sync import HostSyncOutcome

    monkeypatch.setattr("grimoire.hosts.sync.sync_host_surfaces", lambda wt, **_k: HostSyncOutcome((), "panne"))
    assert "sans plugin de garde" in prepare_opencode_worktree(tmp_path)


# ── Identité, fin, silence ──────────────────────────────────────────────────────


def test_identite_par_titre_et_dossier(tmp_path: Path, fake: FakeOpencode) -> None:
    host = fake.host()
    fake.sessions = [
        {"id": "ses_a", "title": "grimoire-T", "directory": str(tmp_path)},
        {"id": "ses_b", "title": "grimoire-T", "directory": "/ailleurs"},
        "pas un objet",
    ]
    assert host.resolve(fake, tmp_path, "7001", "grimoire-T") == "ses_a"
    fake.sessions.append({"id": "ses_c", "title": "grimoire-T", "directory": str(tmp_path)})
    assert host.resolve(fake, tmp_path, "7001", "grimoire-T") == ""  # deux candidates : on ne choisit pas
    fake.list_fails = True
    assert host.resolve(fake, tmp_path, "7001", "grimoire-T") == ""
    assert host.sessions(lambda *_a: ExecResult(0, "{}"), tmp_path) is None
    assert host.sessions(lambda *_a: ExecResult(0, "x"), tmp_path) is None
    assert host.handle(" 7001\n") == "7001" and host.handle("rien") == ""
    assert host.version(fake, tmp_path) == "1.17.11"


def test_etat_vivant_muet_fini(tmp_path: Path, fake: FakeOpencode) -> None:
    host = fake.host()
    log = tmp_path / "w.jsonl"
    fake.live.add(42)
    assert host.state({"pid": "42", "log": str(log)}) == "busy"  # pas encore de sortie
    log.write_text("{}\n", encoding="utf-8")
    now = log.stat().st_mtime
    assert host.state({"pid": "42", "log": str(log)}, now=now + 1) == "busy"
    assert host.state({"pid": "42", "log": str(log)}, now=now + SILENCE_S + 1) == "silent"
    assert host.state({"pid": "x"}) == "ended" and host.state({"pid": "43"}) == "ended"
    assert host.halt(fake, tmp_path, "42") == (True, ", processus opencode 42 arrêté") and fake.killed == [42]
    refuse = OpencodeWorkerHost(kill=lambda _p: False)
    assert refuse.halt(fake, tmp_path, "")[0] is False


@pytest.mark.skipif(os.name != "posix", reason="suivi par pid POSIX")
def test_sondes_par_defaut_du_processus() -> None:
    from grimoire.missions.worker_host import _kill_group, _pid_alive

    assert _pid_alive(os.getpid()) in (True, False)  # le processus de test n'est pas opencode : faux sous /proc
    assert _pid_alive(0) is False and _kill_group(0) is False
    assert _pid_alive(2**22 + 12345) is False and _kill_group(2**22 + 12345) is True


def test_windows_refuse_le_lancement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from grimoire.missions import worker_host as wh

    monkeypatch.setattr(wh, "_POSIX", False)
    plan = LaunchPlan(("opencode", "run"), ())
    assert "Windows" in OpencodeWorkerHost().start(plan, tmp_path, tmp_path / "w.jsonl").error
    assert wh._pid_alive(123) is False and wh._kill_group(123) is False


def test_default_spawn_binaire_absent(tmp_path: Path) -> None:
    from grimoire.missions.worker_host import default_spawn

    result = default_spawn(["grimoire-binaire-qui-n-existe-pas"], tmp_path, {}, tmp_path / "log" / "w.jsonl")
    assert result.pid == 0 and "introuvable" in result.error


@pytest.mark.skipif(os.name != "posix", reason="suivi par pid POSIX")
def test_default_spawn_lance_detache(tmp_path: Path) -> None:
    import sys

    from grimoire.missions.worker_host import default_spawn

    log = tmp_path / "w.jsonl"
    result = default_spawn([sys.executable, "-c", "print('ok')"], tmp_path, {"X": "1"}, log)
    assert result.pid > 0 and result.error == ""
    os.waitpid(result.pid, 0)
    assert log.read_text(encoding="utf-8").strip() == "ok"


def test_default_spawn_dossier_illisible(tmp_path: Path) -> None:
    from grimoire.missions.worker_host import default_spawn

    result = default_spawn(["true"], tmp_path / "absent", {}, tmp_path / "w.jsonl")
    assert result.pid == 0 and result.error


# ── Le lanceur avec host: opencode ──────────────────────────────────────────────


def test_le_worker_opencode_part_avec_sa_garde_et_sans_cle_tracee(
    projet: Path, fake: FakeOpencode, plugin_ready: None
) -> None:
    tid = ready(projet)
    outcome = launch(projet, fake, tid)
    assert outcome.status == "launched", outcome.detail
    ((argv, cwd, env, log),) = fake.spawned
    wt = find_task_worktree(TaskService(projet), tid)
    assert wt is not None and cwd == wt.path
    assert argv[:7] == ["opencode", "run", "--format", "json", "--title", f"grimoire-{tid}", "--model"]
    assert argv[7] == "deepseek/deepseek-v4-flash" and "task attach" not in argv[-1]
    assert env["DEEPSEEK_API_KEY"] == SECRET and SECRET not in " ".join(argv)
    assert log.is_relative_to(projet / "_grimoire-output")  # hors du worktree
    # Les gestes de garde passent par le faux opencode, jamais un vrai binaire.
    assert ["opencode", "debug", "config"] in [a for a, _c, _e in fake.calls]
    service = TaskService(projet)
    payload = launched_sessions(service)[tid]
    assert payload["host"] == OPENCODE_HOST and payload["session_id"] == "ses_7001" and payload["pid"] == "7001"
    assert payload["model"] == "deepseek-v4-flash" and payload["provider"] == "deepseek"
    task = service.require(tid)
    assert task.status is TaskState.CLAIMED and task.claim is not None and task.claim.session_id == "ses_7001"
    assert task.claim.host_id == "opencode"
    ledger_text = "".join(p.read_text(encoding="utf-8") for p in projet.rglob("*.jsonl"))
    assert SECRET not in ledger_text
    # Le relecteur reste indépendant : il choisit un modèle Claude que models_distinct tient pour un autre.
    from types import SimpleNamespace

    from grimoire.missions.dual_review import models_distinct
    from grimoire.missions.reviewer import ReviewStage

    stage = SimpleNamespace(_policy=SimpleNamespace(reviewer=SimpleNamespace(model="", tier="")), _service=service)
    chosen = ReviewStage._model(stage, payload["model"])  # type: ignore[arg-type]
    assert chosen and models_distinct(chosen, payload["model"])


def test_route_sensible_et_v2_restent_sur_claude_code(projet: Path, fake: FakeOpencode, plugin_ready: None) -> None:
    write_policy(
        projet, "      host: opencode\n      provider: deepseek\n      tier: cheap\n      sensitive_route: strong\n"
    )
    sensitive = ready(projet, "modifier pyproject.toml")
    v2 = ready(projet, "nettoyer", v0=False)
    outcomes = launcher(projet, fake)((Admit(sensitive), Admit(v2)))
    assert [o.status for o in outcomes] == ["launched", "launched"]
    assert fake.spawned == []  # aucun opencode
    first, second = fake.of("claude", "--bg")
    assert first[first.index("--model") + 1] == "opus" and "--settings" not in first
    payloads = launched_sessions(TaskService(projet))
    assert "route sensible" in payloads[sensitive]["host_fallback"]
    assert "V2" in payloads[v2]["host_fallback"] and "host" not in payloads[v2]
    assert second[0] == "claude"


def test_garde_non_prouvee_rien_ne_part(projet: Path, fake: FakeOpencode, plugin_ready: None) -> None:
    fake.loaded = False
    tid = ready(projet)
    outcome = launch(projet, fake, tid)
    assert outcome.status == "skipped" and "garde du kit inactive" in outcome.detail
    assert fake.spawned == [] and TaskService(projet).require(tid).status is TaskState.READY


def test_sans_route_la_tache_attend_et_ne_retombe_pas_sur_claude(
    projet: Path, fake: FakeOpencode, plugin_ready: None
) -> None:
    write_policy(projet, "      host: opencode\n      provider: moonshot\n      tier: cheap\n")
    tid = ready(projet)
    outcome = launch(projet, fake, tid)
    assert outcome.status == "deferred" and "MOONSHOT_API_KEY" in outcome.detail
    assert fake.spawned == [] and fake.of("claude", "--bg") == []
    events = [e.event_type for e in TaskService(projet).ledger.events_for(tid)]
    assert WORKER_DEFERRED_EVENT in events


def test_mode_sans_traduction_la_tache_attend(projet: Path, fake: FakeOpencode, plugin_ready: None) -> None:
    write_policy(projet, mode="yolo")
    outcome = launch(projet, fake, ready(projet))
    assert outcome.status == "deferred" and "sans traduction" in outcome.detail


def test_un_lancement_qui_ne_demarre_pas_compte_comme_echec(
    projet: Path, fake: FakeOpencode, plugin_ready: None
) -> None:
    fake.spawn_error = "opencode : binaire introuvable"
    tid = ready(projet)
    outcome = launch(projet, fake, tid)
    assert outcome.status == "failed" and "binaire introuvable" in outcome.detail
    assert TaskService(projet).require(tid).status is TaskState.READY


def test_identite_introuvable_le_processus_est_arrete(projet: Path, fake: FakeOpencode, plugin_ready: None) -> None:
    fake.register = False
    tid = ready(projet)
    outcome = launch(projet, fake, tid)
    assert outcome.status == "failed" and "identité non résolue" in outcome.detail
    assert fake.killed == [7001] and "processus opencode 7001 arrêté" in outcome.detail
    events = [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == LAUNCH_FAILED_EVENT]
    assert events and events[-1].payload["halted"] is True


def test_inscription_refusee_le_processus_est_arrete(
    projet: Path, fake: FakeOpencode, plugin_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("grimoire.missions.worker_lifecycle.attest_session", lambda *_a: ("refusé", "role_conflict"))
    outcome = launch(projet, fake, ready(projet))
    assert outcome.status == "failed" and "refusé" in outcome.detail and fake.killed == [7001]


# ── Battement, fin, arrêt, reprise d'un worker opencode ─────────────────────────


def _launched(projet: Path, fake: FakeOpencode) -> str:
    tid = ready(projet)
    assert launch(projet, fake, tid).status == "launched"
    return tid


def test_battement_et_fin(projet: Path, fake: FakeOpencode, plugin_ready: None) -> None:
    tid = _launched(projet, fake)
    service = TaskService(projet)
    host = fake.host()
    assert renew_live_workers(service, fake, opencode=host) == (tid,)
    assert release_ended_workers(service, fake, opencode=host) == ()
    assert not [a for a, _c, _e in fake.calls if a[:2] == ["claude", "agents"]]  # aucune sonde Claude Code
    fake.live.clear()
    assert renew_live_workers(service, fake, opencode=host) == ()
    assert release_ended_workers(service, fake, opencode=host) == (tid,)
    assert TaskService(projet).require(tid).status is TaskState.READY


def test_arret_au_repos_et_reprise(projet: Path, fake: FakeOpencode, plugin_ready: None) -> None:
    tid = _launched(projet, fake)
    ledger = TaskService(projet).ledger
    ledger.transition_task(tid, TaskState.RUNNING, actor_id="worker")
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="worker")
    host = fake.host()
    assert halt_idle_workers(TaskService(projet), fake, opencode=host) == (tid,)
    assert fake.killed == [7001]
    halted = [e for e in ledger.events_for(tid) if e.event_type == WORKER_HALTED_EVENT][-1]
    assert halted.payload["pid"] == "7001" and halted.payload["session"] == "ses_7001"
    assert halt_idle_workers(TaskService(projet), fake, opencode=host) == ()  # déjà arrêté
    service = TaskService(projet)
    result = resume_worker(
        service,
        fake,
        "claude",
        tid,
        launched_sessions(service)[tid],
        "renvoi",
        permission_mode="acceptEdits",
        opencode=host,
    )
    assert result.returncode == 0
    argv = fake.spawned[-1][0]
    assert argv[4:6] == ["--session", "ses_7001"] and argv[-1] == "renvoi" and SECRET not in " ".join(argv)
    resumed = [e for e in ledger.events_for(tid) if e.event_type == WORKER_RESUMED_EVENT][-1]
    assert resumed.payload["host"] == OPENCODE_HOST and resumed.payload["pid"] == "7002"


def test_arret_d_un_processus_deja_fini_ou_refuse(projet: Path, fake: FakeOpencode, plugin_ready: None) -> None:
    tid = _launched(projet, fake)
    ledger = TaskService(projet).ledger
    ledger.transition_task(tid, TaskState.RUNNING, actor_id="worker")
    ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="worker")
    refuse = OpencodeWorkerHost(alive=lambda _p: True, kill=lambda _p: False)
    assert halt_idle_workers(TaskService(projet), fake, opencode=refuse) == ()
    fake.live.clear()
    assert halt_idle_workers(TaskService(projet), fake, opencode=fake.host()) == (tid,)
    halted = [e for e in ledger.events_for(tid) if e.event_type == WORKER_HALTED_EVENT][-1]
    assert halted.payload["already_gone"] is True


def test_reprise_refusee(projet: Path, fake: FakeOpencode, plugin_ready: None) -> None:
    tid = _launched(projet, fake)
    service = TaskService(projet)
    launch_payload = launched_sessions(service)[tid]
    host = fake.host()
    fake.loaded = False
    refused = resume_worker(service, fake, "claude", tid, launch_payload, "m", opencode=host)
    assert refused.returncode == 1 and "garde du kit inactive" in refused.stderr
    fake.loaded, fake.spawn_error = True, "panne"
    failed = resume_worker(service, fake, "claude", tid, launch_payload, "m", opencode=host)
    assert failed.returncode == 1 and "n'a pas démarré" in failed.stderr
    no_route = resume_worker(service, fake, "claude", tid, {**launch_payload, "provider": "absent"}, "m", opencode=host)
    assert no_route.returncode == 1 and "impossible" in no_route.stderr
    bad_mode = resume_worker(service, fake, "claude", tid, launch_payload, "m", permission_mode="yolo", opencode=host)
    assert bad_mode.returncode == 1 and "refusée" in bad_mode.stderr


def test_la_reprise_apres_erreur_du_fournisseur_ignore_opencode(
    projet: Path, fake: FakeOpencode, plugin_ready: None
) -> None:
    tid = _launched(projet, fake)
    assert tid and resume_provider_failed_workers(TaskService(projet), fake) == ()


def test_le_janitor_arrete_le_processus_opencode(
    projet: Path, fake: FakeOpencode, plugin_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.missions.janitor import Janitor

    tid = _launched(projet, fake)
    policy = load_orchestrator_policy(projet).policy
    assert policy is not None
    janitor = Janitor(TaskService(projet), policy, exec_=fake)
    killed: list[int] = []
    monkeypatch.setattr("grimoire.missions.worker_host._kill_group", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr("grimoire.missions.worker_host._pid_alive", lambda pid: pid in fake.live)
    assert janitor._session(tid) == ("done", "worker opencode ses_7001 arrêté") and killed == [7001]
    monkeypatch.setattr("grimoire.missions.worker_host._kill_group", lambda _pid: False)
    assert janitor._session(tid)[0] == "failed"
    fake.live.clear()
    assert janitor._session(tid)[0] == "done"


def test_un_lancement_claude_d_un_projet_opencode_garde_son_battement(
    projet: Path, fake: FakeOpencode, plugin_ready: None
) -> None:
    """Un worker Claude Code (repli V2) et un worker opencode tenus ensemble : chacun sa sonde."""
    oc = _launched(projet, fake)
    v2 = ready(projet, "nettoyer", v0=False)
    assert launch(projet, fake, v2).status == "launched"
    host = fake.host()
    assert set(renew_live_workers(TaskService(projet), fake, opencode=host)) == {oc, v2}
    fake.agents_fail = True
    assert renew_live_workers(TaskService(projet), fake, opencode=host) == (oc,)
    assert release_ended_workers(TaskService(projet), fake, opencode=host) == ()
    assert LAUNCHED_EVENT
