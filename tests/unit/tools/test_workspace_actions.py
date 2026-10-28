"""Actions typées A et B (ADR-008) : catalogue, plan, apply, journal, rollback.

Docker est rejoué par un faux démon qui garde un état (``up -d``, ``stop``,
``update --restart`` le modifient, ``inspect`` le relit) : ces tests ne
demandent ni Docker ni service mémoire.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_actions as wact
from grimoire.tools import workspace_machine as wmach
from grimoire.tools.workspace_routes import GET_ROUTES, PREFIX, WORKSPACE_UNHANDLED, workspace_get, workspace_post

QDRANT_MEMORY = "  backend: qdrant-server\n  qdrant_url: http://localhost:6333\n"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))


def _project(root: Path, name: str, memory: str = QDRANT_MEMORY) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-context.yaml").write_text(f"project:\n  name: {name}\nmemory:\n{memory}", encoding="utf-8")
    return root


class FakeDocker:
    """Un démon à état : un conteneur Qdrant, mutable par les commandes du catalogue."""

    def __init__(self, workdir: Path | None, *, status: str = "running", restart: str = "unless-stopped") -> None:
        self.workdir, self.status, self.restart = workdir, status, restart
        self.mutations: list[list[str]] = []
        self.present = workdir is not None

    def _ps(self) -> dict[str, Any]:
        up = self.status == "running"
        return {"Names": "kit-qdrant-1", "Image": "qdrant/qdrant:v1.19.1", "State": self.status,
                "Ports": "0.0.0.0:6333->6333/tcp" if up else ""}

    def _inspect(self) -> dict[str, Any]:
        labels = {}
        if self.workdir:
            labels = {"com.docker.compose.project": self.workdir.name, "com.docker.compose.service": "qdrant",
                      "com.docker.compose.project.working_dir": str(self.workdir)}
        return {
            "Name": "/kit-qdrant-1",
            "Config": {"Image": "qdrant/qdrant:v1.19.1", "Labels": labels, "Env": ["SECRET=x"]},
            "State": {"Status": self.status, "ExitCode": 0},
            "HostConfig": {"RestartPolicy": {"Name": self.restart},
                           "PortBindings": {"6333/tcp": [{"HostPort": "6333"}]}},
            "Mounts": [],
        }

    def read(self, argv: list[str]) -> tuple[int, str, str]:
        if argv[:2] == ["docker", "ps"]:
            return 0, json.dumps(self._ps()) if self.present else "", ""
        if argv[:2] == ["docker", "inspect"] and "--format" in argv:
            return 0, self.restart + "\n", ""
        return 0, json.dumps([self._inspect()] if self.present else []), ""

    def run(self, argv: list[str], cwd: Path | None) -> tuple[int, str, str]:
        if argv[:2] in (["docker", "ps"], ["docker", "inspect"]):
            return self.read(argv)
        self.mutations.append(argv)
        if argv[:2] == ["docker", "update"]:
            self.restart = argv[2].split("=", 1)[1]
        elif argv[-2:] == ["up", "-d"]:
            self.status, self.present = "running", True
            self.workdir = self.workdir or cwd
        elif argv[-1] == "stop":
            self.status = "exited"
        else:
            raise AssertionError(argv)
        return 0, "", ""


def _env(docker: FakeDocker, kit: Path, sessions: list[dict[str, Any]] | None = None,
         now: datetime | None = None) -> wact.ActionEnv:
    def tcp(kind: str, _url: str) -> str:
        return "en_ligne" if kind == "qdrant" and docker.status == "running" else "arrete"

    probes = wmach.MachineProbes(
        docker=docker.read, tcp=tcp, version=lambda *_a: (None, "muet"), which=lambda _n: None,
        module_installed=lambda _m: True, env={},
    )
    moment = now or datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    return wact.ActionEnv(run=docker.run, probes=probes, sessions=lambda _root: sessions or [],
                          memory_dir=lambda: kit, now=lambda: moment)


@pytest.fixture
def kit(tmp_path: Path) -> Path:
    memory = tmp_path / "kit-memory"
    memory.mkdir()
    (memory / "docker-compose.memory.tpl.yml").write_text("services:\n  qdrant: {}\n", encoding="utf-8")
    (memory / "docker-compose.memory-target.tpl.yml").write_text("services:\n  weaviate: {}\n", encoding="utf-8")
    return memory


def _events(root: Path) -> list[str]:
    return [json.loads(line)["event"] for line in wact.journal_path(root).read_text().splitlines()]


# ── Catalogue et routes ───────────────────────────────────────────────────────


def test_catalogue_cinq_elements_et_classe_c_sans_apply(tmp_path: Path) -> None:
    for spec in wact.CATALOGUE.values():
        assert spec.preconditions and spec.plan and spec.effects and spec.verification and spec.inverse
    assert {s.id: s.klass for s in wact.CATALOGUE.values()} == {
        "memory.compose": "A", "memory.slot": "A", "memory.split": "D", "stack.start": "B", "stack.stop": "B", "stack.restart_policy": "B", "pip.extras": "C",
    }
    root = _project(tmp_path / "kit", "Kit")
    assert workspace_post(root, f"{PREFIX}data/actions/pip.extras/apply", {}) is WORKSPACE_UNHANDLED
    assert workspace_post(root, f"{PREFIX}data/actions/pip.extras/rollback", {}) is WORKSPACE_UNHANDLED
    plan = workspace_post(root, f"{PREFIX}data/actions/pip.extras/plan", {"params": {"extras": ["qdrant"]}})
    assert plan["applicable"] is False and "qdrant" in plan["plan"]["notes"][0]
    assert not wact.journal_path(root).exists()


def test_routes_get_catalogue_et_journal(tmp_path: Path) -> None:
    root = _project(tmp_path / "kit", "Kit")
    assert f"{PREFIX}data/actions" in GET_ROUTES and f"{PREFIX}data/actions/journal" in GET_ROUTES
    catalogue = workspace_get(root, f"{PREFIX}data/actions", {})
    assert [a["id"] for a in catalogue["actions"]][:2] == ["memory.compose", "memory.slot"]
    assert workspace_get(root, f"{PREFIX}data/actions/journal", {})["events"] == []


def test_workspace_exec_n_autorise_aucune_action_d_infrastructure() -> None:
    from grimoire.tools import workspace_exec

    names = {" ".join(sub.argv) for sub in workspace_exec.ALLOWED.values()}
    assert not any(n.split()[0] in {"up", "init", "migrate", "serve", "cockpit", "upgrade", "ext", "plugins"} for n in names)
    assert not any(sub.mutates for sub in workspace_exec.ALLOWED.values())


# ── Action A : memory.compose ─────────────────────────────────────────────────


def test_memory_compose_plan_apply_rollback(tmp_path: Path, kit: Path) -> None:
    root = _project(tmp_path / "kit", "Kit", "  backend: lexical\n")
    config = root / "project-context.yaml"
    before = config.read_text()
    env = _env(FakeDocker(None), kit)

    plan = wact.plan_action(root, "memory.compose", {"params": {"profile": "standard"}}, env=env)
    assert plan["ok"], plan
    assert config.read_text() == before  # le plan n'écrit pas
    applied = wact.apply_action(root, "memory.compose", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"]}, env=env)
    assert applied["ok"] and applied["statut"] == "applied" and applied["rollback"]
    assert config.read_text() != before

    undone = wact.rollback_action(root, "memory.compose", {"run_id": plan["plan_id"]}, env=env)
    assert undone["ok"], undone
    assert config.read_text() == before  # copie restaurée à l'octet près
    assert _events(root) == ["plan", "apply_started", "effects", "verification", "applied", "rollback_started", "rollback_applied"]
    again = wact.rollback_action(root, "memory.compose", {"run_id": plan["plan_id"]}, env=env)
    assert not again["ok"] and "déjà" in again["raison"]


def test_memory_compose_rollback_refuse_si_fichier_modifie(tmp_path: Path, kit: Path) -> None:
    root = _project(tmp_path / "kit", "Kit", "  backend: lexical\n")
    env = _env(FakeDocker(None), kit)
    plan = wact.plan_action(root, "memory.compose", {"params": {"profile": "standard"}}, env=env)
    wact.apply_action(root, "memory.compose", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"]}, env=env)
    config = root / "project-context.yaml"
    config.write_text(config.read_text() + "# édité à la main\n", encoding="utf-8")
    undone = wact.rollback_action(root, "memory.compose", {"run_id": plan["plan_id"]}, env=env)
    assert not undone["ok"] and "modifié" in undone["raison"]
    assert "# édité à la main" in config.read_text()


# ── Contrat plan → apply ──────────────────────────────────────────────────────


def test_apply_refuse_plan_perime_expire_inconnu_et_rejoue(tmp_path: Path, kit: Path) -> None:
    root = _project(tmp_path / "kit", "Kit", "  backend: lexical\n")
    config = root / "project-context.yaml"
    env = _env(FakeDocker(None), kit)
    plan = wact.plan_action(root, "memory.compose", {"params": {"profile": "standard"}}, env=env)
    body = {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"]}

    assert "requis" in wact.apply_action(root, "memory.compose", {}, env=env)["raison"]
    assert "aucun plan" in wact.apply_action(root, "memory.compose", {"plan_id": "act-x", "empreinte": "x"}, env=env)["raison"]
    assert "empreinte différente" in wact.apply_action(root, "memory.compose", {**body, "empreinte": "0" * 64}, env=env)["raison"]
    late = _env(FakeDocker(None), kit, now=datetime(2026, 10, 28, 12, 0, tzinfo=UTC) + wact.PLAN_TTL + timedelta(seconds=1))
    assert "expiré" in wact.apply_action(root, "memory.compose", body, env=late)["raison"]

    before = config.read_text()
    config.write_text(before.replace("lexical", "lexical\n  extra_key: 1"), encoding="utf-8")  # l'état change
    stale = wact.apply_action(root, "memory.compose", body, env=env)
    assert not stale["ok"] and "l'état a changé" in stale["raison"]
    assert "extra_key" in config.read_text() and "apply_started" not in _events(root)


def test_apply_refuse_sans_journal_inscriptible(tmp_path: Path, kit: Path) -> None:
    root = _project(tmp_path / "kit", "Kit", "  backend: lexical\n")
    env = _env(FakeDocker(None), kit)
    plan = wact.plan_action(root, "memory.compose", {"params": {"profile": "standard"}}, env=env)
    journal = wact.journal_path(root)
    before = (root / "project-context.yaml").read_text()
    journal.chmod(0o444)
    journal.parent.chmod(0o555)
    try:
        refused = wact.apply_action(root, "memory.compose", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"]}, env=env)
    finally:
        journal.parent.chmod(0o755)
        journal.chmod(0o644)
    if refused["ok"]:  # exécuté en root : les permissions ne s'appliquent pas
        pytest.skip("permissions non appliquées (root)")
    assert "pas de journal, pas d'effet" in refused["raison"]
    assert (root / "project-context.yaml").read_text() == before


def test_apply_refuse_journal_corrompu(tmp_path: Path, kit: Path) -> None:
    root = _project(tmp_path / "kit", "Kit", "  backend: lexical\n")
    env = _env(FakeDocker(None), kit)
    plan = wact.plan_action(root, "memory.compose", {"params": {"profile": "standard"}}, env=env)
    with wact.journal_path(root).open("a", encoding="utf-8") as fh:
        fh.write("pas du json\n")
    refused = wact.apply_action(root, "memory.compose", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"]}, env=env)
    assert not refused["ok"] and "pas de journal, pas d'effet" in refused["raison"]


# ── Classe B : démarrer / réutiliser / refuser ────────────────────────────────


def _run(root: Path, action: str, params: dict[str, Any], env: wact.ActionEnv, **extra: Any) -> dict[str, Any]:
    plan = wact.plan_action(root, action, {"params": params}, env=env)
    assert plan["ok"], plan["blockers"]
    return wact.apply_action(root, action, {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"], **extra}, env=env)


def test_demarrer_une_pile_depuis_le_compose_du_kit(tmp_path: Path, kit: Path) -> None:
    root = _project(tmp_path / "kit", "Kit")
    docker = FakeDocker(None)
    env = _env(docker, kit)
    result = _run(root, "stack.start", {"stack": "qdrant"}, env)
    assert result["ok"], result
    assert docker.mutations == [["docker", "compose", "-f", "docker-compose.memory.yml", "up", "-d"]]
    assert (root / "docker-compose.memory.yml").read_text() == (kit / "docker-compose.memory.tpl.yml").read_text()
    assert not {"-v", "--volumes", "down"} & set(docker.mutations[0])

    undone = wact.rollback_action(root, "stack.start", {"run_id": result["run_id"]}, env=env)
    assert undone["ok"] and docker.status == "exited" and not (root / "docker-compose.memory.yml").exists()


def test_une_commande_libre_ou_un_volume_est_refuse(tmp_path: Path, kit: Path) -> None:
    root = _project(tmp_path / "kit", "Kit")
    env = _env(FakeDocker(None), kit)
    plan = wact.plan_action(root, "stack.start", {"params": {"stack": "../../etc"}}, env=env)
    assert not plan["ok"] and "pile inconnue" in plan["blockers"][0]
    for argv in (["docker", "compose", "down", "-v"], ["docker", "rm", "x"], ["sh", "-c", "x"],
                 ["docker", "compose", "-f", "f", "down", "--volumes"]):
        with pytest.raises(ValueError, match="refusée"):
            wact._check_argv(argv)


def test_reutiliser_une_pile_existante(tmp_path: Path, kit: Path) -> None:
    root = _project(tmp_path / "kit", "Kit")
    docker = FakeDocker(root)
    env = _env(docker, kit)
    result = _run(root, "stack.start", {"stack": "qdrant"}, env)
    assert result["ok"] and result["effects"] == ["pile réutilisée"] and docker.mutations == []
    assert not (root / "docker-compose.memory.yml").exists()


def test_refuse_d_agir_sur_le_conteneur_d_un_autre_projet(tmp_path: Path, kit: Path) -> None:
    owner = _project(tmp_path / "brainstrom", "Brainstrom")
    root = _project(tmp_path / "kit", "Kit")
    reg.save_registry([{"slug": "brainstrom", "name": "Brainstrom", "path": str(owner)},
                       {"slug": "kit", "name": "Kit", "path": str(root)}])
    docker = FakeDocker(owner)
    env = _env(docker, kit)
    for action, params in (("stack.start", {"stack": "qdrant"}), ("stack.stop", {"stack": "qdrant"}),
                           ("stack.restart_policy", {"service": "qdrant", "policy": "no"})):
        plan = wact.plan_action(root, action, {"params": params}, env=env)
        assert not plan["ok"]
        assert "empruntée" in plan["blockers"][0] and "Brainstrom" in plan["blockers"][0]
        forged = wact.apply_action(root, action, {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"]}, env=env)
        assert not forged["ok"] and "empruntée" in forged["raison"]
    assert docker.mutations == []


# ── Redémarrer avec la machine ────────────────────────────────────────────────


def test_interrupteur_redemarrer_avec_la_machine(tmp_path: Path, kit: Path) -> None:
    root = _project(tmp_path / "kit", "Kit")
    docker = FakeDocker(root)
    env = _env(docker, kit)
    result = _run(root, "stack.restart_policy", {"service": "qdrant", "policy": "no"}, env)
    assert result["ok"] and result["verification"]["detail"] == "docker inspect relit « no »"
    assert docker.mutations == [["docker", "update", "--restart=no", "kit-qdrant-1"]] and docker.restart == "no"
    undone = wact.rollback_action(root, "stack.restart_policy", {"run_id": result["run_id"]}, env=env)
    assert undone["ok"] and docker.restart == "unless-stopped"
    same = wact.plan_action(root, "stack.restart_policy", {"params": {"service": "qdrant", "policy": "unless-stopped"}}, env=env)
    assert not same["ok"] and "déjà" in same["blockers"][0]
    bad = wact.plan_action(root, "stack.restart_policy", {"params": {"service": "qdrant", "policy": "always"}}, env=env)
    assert not bad["ok"]


# ── Arrêt d'une pile partagée ─────────────────────────────────────────────────


def test_arreter_une_pile_partagee_exige_confirmation_listant_projets_et_sessions(tmp_path: Path, kit: Path) -> None:
    root = _project(tmp_path / "kit", "Kit")
    other = _project(tmp_path / "forge", "Forge")
    reg.save_registry([{"slug": "kit", "name": "Kit", "path": str(root)}, {"slug": "forge", "name": "Forge", "path": str(other)}])
    docker = FakeDocker(root)
    (root / "docker-compose.memory.yml").write_text("services: {}\n", encoding="utf-8")
    sessions = [
        {"id": "s-live", "state": "active", "project": {"slug": "forge", "name": "Forge"}},
        {"id": "s-old", "state": "ended", "project": {"slug": "forge", "name": "Forge"}},
        {"id": "s-elsewhere", "state": "active", "project": {"slug": "autre", "name": "Autre"}},
    ]
    env = _env(docker, kit, sessions)
    plan = wact.plan_action(root, "stack.stop", {"params": {"stack": "qdrant"}}, env=env)
    assert plan["ok"] and plan["plan"]["needsConfirmation"]
    touches = plan["plan"]["facts"]["touches"]
    assert touches["projects"] == ["Forge", "Kit"] and [s["id"] for s in touches["sessions"]] == ["s-live"]

    body = {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"]}
    refused = wact.apply_action(root, "stack.stop", body, env=env)
    assert not refused["ok"] and "confirmation" in refused["raison"] and refused["touches"] == touches
    assert docker.mutations == []

    done = wact.apply_action(root, "stack.stop", {**body, "confirm": True}, env=env)  # le même plan, confirmé
    assert done["ok"] and docker.status == "exited"


def test_arreter_avec_confirmation(tmp_path: Path, kit: Path) -> None:
    root = _project(tmp_path / "kit", "Kit")
    docker = FakeDocker(root)
    (root / "docker-compose.memory.yml").write_text("services: {}\n", encoding="utf-8")
    env = _env(docker, kit, [{"id": "s1", "state": "waiting", "project": {"slug": "", "name": "Kit"}}])
    plan = wact.plan_action(root, "stack.stop", {"params": {"stack": "qdrant"}}, env=env)
    assert plan["plan"]["needsConfirmation"]  # une session vivante suffit
    result = wact.apply_action(root, "stack.stop", {"plan_id": plan["plan_id"], "empreinte": plan["empreinte"], "confirm": True}, env=env)
    assert result["ok"], result
    assert docker.mutations == [["docker", "compose", "-f", "docker-compose.memory.yml", "stop"]]
    undone = wact.rollback_action(root, "stack.stop", {"run_id": result["run_id"]}, env=env)
    assert undone["ok"] and docker.status == "running"
