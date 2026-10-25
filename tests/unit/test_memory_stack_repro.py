"""P16 — setup mémoire reproductible : images épinglées, pile existante détectée, bon installeur.

Aucun Docker réel : ``subprocess.run`` est remplacé par un double, les gabarits sont lus sur disque.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import updater
from grimoire.tools import memory_setup as ms

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "framework" / "memory" / "docker-compose.memory.tpl.yml"
K8S = ROOT / "framework" / "memory" / "k8s-qdrant.tpl.yaml"
PINNED = re.compile(r":v?\d+\.\d+\.\d+")


class TestComposeTemplate:
    def test_qdrant_image_is_pinned_to_an_exact_version(self) -> None:
        image = re.search(r"image:\s*(\S+)", COMPOSE.read_text(encoding="utf-8"))
        assert image is not None
        assert image.group(1).startswith("qdrant/qdrant:") and PINNED.search(image.group(1))
        assert "latest" not in image.group(1)

    def test_k8s_template_is_pinned_too(self) -> None:
        assert "latest" not in K8S.read_text(encoding="utf-8")

    def test_no_fixed_container_name(self) -> None:
        """Deux projets sur le même poste ne doivent plus se disputer « grimoire-qdrant »."""
        assert not re.search(r"^\s*container_name:", COMPOSE.read_text(encoding="utf-8"), re.MULTILINE)

    def test_port_reads_the_canonical_variable_and_still_accepts_the_old_spelling(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8")
        # ${GRIMOIRE_QDRANT_PORT:-${Grimoire_QDRANT_PORT:-6333}} : la nouvelle graphie prime, l'ancienne repli.
        assert "${GRIMOIRE_QDRANT_PORT:-${Grimoire_QDRANT_PORT:-6333}}:6333" in text
        assert "${GRIMOIRE_QDRANT_GRPC_PORT:-${Grimoire_QDRANT_GRPC_PORT:-6334}}:6334" in text

    def test_restart_policy_kept_and_volume_is_per_project(self) -> None:
        """Sans `name:` fixe, Compose préfixe le volume du nom du projet : deux Qdrant ne partagent plus un stockage."""
        text = COMPOSE.read_text(encoding="utf-8")
        assert "restart: unless-stopped" in text
        assert "grimoire-qdrant-storage" not in text.replace("`grimoire-qdrant-storage`", "")
        assert not re.search(r"^\s+name:\s*\S+", text.split("volumes:\n", 1)[-1], re.MULTILINE)


class TestRedisCommand:
    def test_command_has_a_restart_policy_and_a_pinned_image(self) -> None:
        cmd = ms._START_COMMANDS["redis"]
        assert "--restart unless-stopped" in cmd
        assert PINNED.search(cmd.split()[-1]) and "latest" not in cmd

    def test_other_docker_run_remedies_are_pinned(self) -> None:
        from grimoire.cli import cmd_up

        assert PINNED.search(cmd_up._QDRANT_DOCKER_RUN) and "--restart unless-stopped" in cmd_up._QDRANT_DOCKER_RUN


class _Docker:
    """Double de ``subprocess.run`` : ``docker ps -a`` répond ce qu'on lui dit, le reste est enregistré."""

    def __init__(self, ps: str = "", ps_code: int = 0) -> None:
        self.ps, self.ps_code = ps, ps_code
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_: Any) -> Any:
        self.calls.append(list(argv))
        if argv[:3] == ["docker", "ps", "-a"]:
            return subprocess.CompletedProcess(argv, self.ps_code, stdout=self.ps, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def created(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["docker", "run"]]

    def started(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["docker", "start"]]


def _ps(name: str, image: str, ports: str, state: str) -> str:
    return f"{name}\t{image}\t{ports}\t{state}\n"


class TestExistingStackIsFoundBeforeCreating:
    def test_finds_a_running_container_by_image_and_published_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        docker = _Docker(_ps("autre-redis", "redis:7-alpine", "0.0.0.0:6379->6379/tcp", "running"))
        monkeypatch.setattr(ms.subprocess, "run", docker)

        found = ms.find_existing_container("redis", 6379)

        assert found == ms.ExistingContainer(name="autre-redis", image="redis:7-alpine", running=True)

    def test_same_port_other_image_is_not_a_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms.subprocess, "run", _Docker(_ps("web", "nginx:1", "0.0.0.0:6379->80/tcp", "running")))
        assert ms.find_existing_container("redis", 6379) is None

    def test_same_image_other_port_is_not_a_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms.subprocess, "run", _Docker(_ps("r2", "redis:7", "0.0.0.0:6380->6379/tcp", "running")))
        assert ms.find_existing_container("redis", 6379) is None

    def test_registry_prefixed_image_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ms.subprocess, "run", _Docker(_ps("r", "docker.io/library/redis:7.4.2-alpine", "0.0.0.0:6379->6379/tcp", "running")),
        )
        assert ms.find_existing_container("redis", 6379) is not None

    def test_a_stopped_container_is_recognised_by_its_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms.subprocess, "run", _Docker(_ps("grimoire-redis", "redis:7", "", "exited")))
        found = ms.find_existing_container("redis", 6379, name="grimoire-redis")
        assert found is not None and found.running is False

    def test_docker_failure_means_nothing_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms.subprocess, "run", _Docker(ps_code=1))
        assert ms.find_existing_container("redis", 6379) is None

        def boom(*a: object, **k: object) -> None:
            raise OSError("pas de docker")

        monkeypatch.setattr(ms.subprocess, "run", boom)
        assert ms.find_existing_container("redis", 6379) is None

    def _redis_down(self) -> ms.ServiceProbe:
        return ms.ServiceProbe(id="redis", url=ms._DEFAULT_URLS["redis"], reachable=False, extra="redis", extra_installed=True)

    def test_running_stack_is_never_duplicated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        docker = _Docker(_ps("autre-redis", "redis:7-alpine", "0.0.0.0:6379->6379/tcp", "running"))
        monkeypatch.setattr(ms.subprocess, "run", docker)

        message = ms._start_redis(self._redis_down())

        assert docker.created() == [] and docker.started() == []
        assert "autre-redis" in message and "aucun nouveau conteneur" in message

    def test_stopped_stack_is_restarted_not_recreated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        docker = _Docker(_ps("grimoire-redis", "redis:7", "", "exited"))
        monkeypatch.setattr(ms.subprocess, "run", docker)
        monkeypatch.setattr(ms, "_wait_reachable", lambda *a, **k: True)

        message = ms._start_redis(self._redis_down())

        assert docker.created() == [] and docker.started() == [["docker", "start", "grimoire-redis"]]
        assert "redémarré" in message

    def test_nothing_found_creates_with_the_pinned_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        docker = _Docker("")
        monkeypatch.setattr(ms.subprocess, "run", docker)
        monkeypatch.setattr(ms, "_wait_reachable", lambda *a, **k: True)

        assert ms._start_redis(self._redis_down()) == "Redis : démarré"
        assert docker.created() == [ms._START_COMMANDS["redis"].split()]


class TestExtrasInstaller:
    def test_already_installed_extras_are_carried_over(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`--force` recrée l'environnement : sans report, ajouter redis retirerait neo4j."""
        from grimoire.tools import install_trust

        monkeypatch.setattr(install_trust, "installed_extras", lambda dist=None: ["neo4j", "qdrant"])
        monkeypatch.setattr(updater, "_carried_uv_options", lambda kind, binary: [])
        argv, hint = updater.extras_install_cmd(["redis"], manager=("uv", "/u/uv"))
        assert argv[-1] == "grimoire-kit[neo4j,qdrant,redis]" and "neo4j,qdrant,redis" in hint
        argv, _ = updater.extras_install_cmd(["redis"], manager=("pipx", "/p/pipx"))
        assert argv[-1] == "grimoire-kit[neo4j,qdrant,redis]"
        argv, _ = updater.extras_install_cmd(["redis"], manager=("pip", None))
        assert argv[-1] == "grimoire-kit[redis]"  # pip ajoute sans rien retirer

    def test_uv_tool_install_keeps_the_manager_and_the_extras(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.tools import install_trust

        monkeypatch.setattr(install_trust, "installed_extras", lambda dist=None: [])
        monkeypatch.setattr(updater, "_carried_uv_options", lambda kind, binary: ["--with", "rich"])
        argv, hint = updater.extras_install_cmd(["neo4j", "redis"], manager=("uv", "/u/uv"))
        assert argv == ["/u/uv", "tool", "install", "--force", "--with", "rich", "grimoire-kit[neo4j,redis]"]
        assert hint.startswith("uv tool install --force") and "grimoire-kit[neo4j,redis]" in hint

    def test_pipx_uses_pipx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.tools import install_trust

        monkeypatch.setattr(install_trust, "installed_extras", lambda dist=None: [])
        argv, hint = updater.extras_install_cmd(["qdrant"], manager=("pipx", "/p/pipx"))
        assert argv == ["/p/pipx", "install", "--force", "grimoire-kit[qdrant]"]
        assert hint.startswith("pipx install")

    def test_plain_environment_falls_back_to_the_interpreters_pip(self) -> None:
        import sys

        argv, hint = updater.extras_install_cmd(["search"], manager=("pip", None))
        assert argv == [sys.executable, "-m", "pip", "install", "grimoire-kit[search]"]
        assert "pip install" in hint

    def test_memory_setup_remedies_come_from_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        from grimoire.tools import install_trust

        monkeypatch.setattr(install_trust, "installed_extras", lambda dist=None: [])
        monkeypatch.setattr(updater, "_detect_manager", lambda: ("pipx", "/p/pipx"))
        online_without_extra = ms.ServiceProbe(
            id="neo4j", url=ms._DEFAULT_URLS["neo4j"], reachable=True, extra="neo4j", extra_installed=False,
        )
        plan = SimpleNamespace(warnings=[])

        ms._warn_unusable(plan, online_without_extra)  # type: ignore[arg-type]

        assert len(plan.warnings) == 1 and "pipx install --force" in plan.warnings[0]
        assert 'pip install "grimoire-kit[' not in plan.warnings[0]

    def test_standard_init_installs_extras_through_the_same_function(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from grimoire.cli import cmd_standard
        from grimoire.tools import install_trust

        seen: list[list[str]] = []
        monkeypatch.setattr(install_trust, "installed_extras", lambda dist=None: [])
        monkeypatch.setattr(updater, "_detect_manager", lambda: ("pipx", "/p/pipx"))
        monkeypatch.setattr(
            subprocess, "run", lambda argv, **_: seen.append(list(argv)) or subprocess.CompletedProcess(argv, 0),
        )

        assert cmd_standard._run_pip_install("grimoire-kit[redis,weaviate]") is True
        assert seen == [["/p/pipx", "install", "--force", "grimoire-kit[redis,weaviate]"]]

    def test_no_hardcoded_pip_install_remedy_for_extras_remains(self) -> None:
        pattern = re.compile(r"""pip install ["']grimoire-kit\[""")
        offenders = [
            str(p.relative_to(ROOT))
            for p in (ROOT / "src" / "grimoire").rglob("*.py")
            if pattern.search(p.read_text(encoding="utf-8"))
        ]
        assert offenders == []
