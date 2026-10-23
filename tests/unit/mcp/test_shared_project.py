"""Serveur MCP partagé : le projet est celui de la requête, jamais celui du processus (#723).

Deux projets jetables, un seul serveur en mémoire sous ``GRIMOIRE_MCP_TRANSPORT=http``,
un vrai ``ClientSession`` : les appels alternent A et B et chaque réponse porte le
projet demandé ; sans projet résolu, l'appel est refusé (``isError``) — jamais
servi sur le ``cwd``, même quand le ``cwd`` est un projet valide.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp", reason="extra optionnel grimoire-kit[mcp] non installé")

import anyio
import mcp.types as types
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from grimoire.mcp import project as project_module
from grimoire.mcp import server as server_module
from grimoire.mcp.project import ProjectUnresolvedError, resolve_project_path, root_dir, transport
from grimoire.mcp.server import grimoire_config, mcp
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import DEFAULT_LEDGER_RELPATH


def _lowlevel() -> Any:
    return getattr(mcp, "_lowlevel_server", None) or mcp._mcp_server


async def _with_client(scenario: Any, roots: list[Path] | None) -> Any:
    async def list_roots(*_args: Any, **_kwargs: Any) -> types.ListRootsResult:
        return types.ListRootsResult(roots=[types.Root(uri=p.as_uri()) for p in roots or []])  # type: ignore[arg-type]

    async with create_client_server_memory_streams() as (client_streams, server_streams), anyio.create_task_group() as tg:
        low = _lowlevel()
        tg.start_soon(low.run, server_streams[0], server_streams[1], low.create_initialization_options())
        kwargs: dict[str, Any] = {"list_roots_callback": list_roots} if roots is not None else {}
        async with ClientSession(client_streams[0], client_streams[1], **kwargs) as session:
            await session.initialize()
            result = await scenario(session)
        tg.cancel_scope.cancel()
    return result


def via_client(scenario: Any, *, roots: list[Path] | None = None) -> Any:
    return anyio.run(_with_client, scenario, roots)


def _is_error(result: Any) -> bool:
    dump = result.model_dump(by_alias=True)
    return bool(dump.get("isError", False))


def _body(result: Any) -> dict[str, Any]:
    text = "".join(getattr(block, "text", "") for block in result.content)
    return dict(json.loads(text))


def _projet(root: Path, name: str) -> Path:
    root.mkdir(parents=True)
    (root / "project-context.yaml").write_text(
        f"project:\n  name: {name}\n  type: webapp\n  stack:\n    - python\n"
        "user:\n  name: Guilhem\n  language: Français\n  skill_level: expert\n"
        "memory:\n  backend: local\nagents:\n  archetype: minimal\n",
        encoding="utf-8",
    )
    (root / "_grimoire" / "_memory").mkdir(parents=True)
    (root / "_grimoire-output").mkdir()
    ledger = MissionLedger(root / DEFAULT_LEDGER_RELPATH)
    mission = ledger.create_mission(title=f"Travaux {name}", origin="test")
    task = ledger.create_task(mission.id, f"Tâche de {name}", acceptance=("ok",), owner="amelia")
    ledger.transition_task(task.id, TaskState.READY, actor_id="amelia")
    return root


@pytest.fixture
def deux_projets(tmp_path: Path) -> tuple[Path, Path]:
    return _projet(tmp_path / "alpha", "alpha"), _projet(tmp_path / "beta", "beta")


@pytest.fixture
def partage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(project_module.TRANSPORT_ENV, "http")
    assert transport() == "http"


# ── résolution pure ──────────────────────────────────────────────────────────

class TestResolution:
    def test_absolu_existant(self, tmp_path: Path) -> None:
        assert resolve_project_path(str(tmp_path)) == tmp_path.resolve()

    def test_absolu_inexistant_refuse(self, tmp_path: Path) -> None:
        with pytest.raises(ProjectUnresolvedError) as exc:
            resolve_project_path(str(tmp_path / "nulle-part"))
        assert exc.value.payload["code"] == "mcp.project_unresolved"
        assert exc.value.payload["project_path"] == str(tmp_path / "nulle-part")

    def test_point_sans_racine_refuse(self) -> None:
        with pytest.raises(ProjectUnresolvedError, match="declared no workspace root"):
            resolve_project_path(".")

    def test_vide_sans_racine_refuse(self) -> None:
        with pytest.raises(ProjectUnresolvedError):
            resolve_project_path("")

    def test_point_prend_la_premiere_racine(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert resolve_project_path(".", [a.as_uri(), b.as_uri()]) == a.resolve()

    def test_relatif_se_joint_a_la_racine(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        assert resolve_project_path("sub", [tmp_path.as_uri()]) == (tmp_path / "sub").resolve()

    def test_relatif_sans_racine_refuse(self) -> None:
        with pytest.raises(ProjectUnresolvedError, match="relative project_path"):
            resolve_project_path("sub")

    def test_racines_non_locales_ignorees(self, tmp_path: Path) -> None:
        with pytest.raises(ProjectUnresolvedError, match="none of the client roots"):
            resolve_project_path(".", ["https://example.org/x", "file:///nulle/part/ici"])
        assert root_dir("https://example.org/x") is None
        assert root_dir("relatif/sans/racine") is None
        assert root_dir(str(tmp_path)) == tmp_path.resolve()
        assert root_dir(tmp_path.as_uri()) == tmp_path.resolve()

    def test_transport_inconnu_vaut_stdio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(project_module.TRANSPORT_ENV, "HTTP ")
        assert transport() == "http"
        monkeypatch.setenv(project_module.TRANSPORT_ENV, "n-importe-quoi")
        assert transport() == "stdio"
        monkeypatch.delenv(project_module.TRANSPORT_ENV)
        assert transport() == "stdio"


# ── isolation entre projets, par un vrai client ──────────────────────────────

class TestIsolationParRequete:
    @pytest.mark.usefixtures("partage")
    def test_alterner_deux_projets_rend_chacun_le_sien(self, deux_projets: tuple[Path, Path]) -> None:
        alpha, beta = deux_projets

        async def scenario(session: ClientSession) -> list[tuple[str, list[str]]]:
            seen: list[tuple[str, list[str]]] = []
            for root in (alpha, beta, alpha, beta, beta, alpha):
                cfg = _body(await session.call_tool("grimoire_config", {"project_path": str(root)}))
                tasks = _body(await session.call_tool("task_list_ready", {"project_path": str(root)}))
                status = _body(await session.call_tool("grimoire_status", {"project_path": str(root)}))
                assert status["project_root"] == str(root.resolve())
                seen.append((cfg["project"]["name"], [t["title"] for t in tasks["tasks"]]))
            return seen

        assert via_client(scenario) == [
            ("alpha", ["Tâche de alpha"]), ("beta", ["Tâche de beta"]),
            ("alpha", ["Tâche de alpha"]), ("beta", ["Tâche de beta"]),
            ("beta", ["Tâche de beta"]), ("alpha", ["Tâche de alpha"]),
        ]

    @pytest.mark.usefixtures("partage")
    def test_la_memoire_ecrite_dans_un_projet_ne_se_lit_pas_dans_l_autre(self, deux_projets: tuple[Path, Path]) -> None:
        alpha, beta = deux_projets

        async def scenario(session: ClientSession) -> tuple[int, int]:
            stored = await session.call_tool(
                "grimoire_memory_store", {"text": "le port du cockpit est 8420", "project_path": str(alpha)}
            )
            assert not _is_error(stored), _body(stored)
            found_a = _body(await session.call_tool("grimoire_memory_search", {"query": "cockpit", "project_path": str(alpha)}))
            found_b = _body(await session.call_tool("grimoire_memory_search", {"query": "cockpit", "project_path": str(beta)}))
            return found_a["count"], found_b["count"]

        assert via_client(scenario) == (1, 0)

    @pytest.mark.usefixtures("partage")
    def test_sans_projet_resolu_refus_jamais_le_cwd(
        self, deux_projets: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        alpha, _beta = deux_projets
        monkeypatch.chdir(alpha)  # un cwd valide : le serveur ne doit PAS s'en servir

        async def scenario(session: ClientSession) -> list[Any]:
            default = await session.call_tool("grimoire_status", {})
            dot = await session.call_tool("grimoire_config", {"project_path": "."})
            relative = await session.call_tool("task_list_ready", {"project_path": "alpha"})
            missing = await session.call_tool("grimoire_status", {"project_path": str(alpha / "absent")})
            return [default, dot, relative, missing]

        results = via_client(scenario)  # client sans capacité roots
        for result in results:
            assert _is_error(result), _body(result)
            body = _body(result)
            assert body["code"] == "mcp.project_unresolved"
            assert "project not resolved" in body["error"]
            assert str(alpha) not in json.dumps(body.get("checks", []))
        assert _body(results[0])["client_roots"] == []
        assert _body(results[2])["project_path"] == "alpha"

    @pytest.mark.usefixtures("partage")
    def test_la_racine_declaree_par_le_client_sert_de_projet(
        self, deux_projets: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        alpha, beta = deux_projets
        monkeypatch.chdir(alpha)  # le cwd dit alpha, le client dit beta : beta gagne

        async def scenario(session: ClientSession) -> tuple[str, str, str]:
            dot = _body(await session.call_tool("grimoire_config", {"project_path": "."}))
            omitted = _body(await session.call_tool("grimoire_status", {}))
            explicit = _body(await session.call_tool("grimoire_config", {"project_path": str(alpha)}))
            return dot["project"]["name"], omitted["project_root"], explicit["project"]["name"]

        assert via_client(scenario, roots=[beta]) == ("beta", str(beta.resolve()), "alpha")

    @pytest.mark.usefixtures("partage")
    def test_le_refus_d_une_requete_ne_colle_pas_a_la_suivante(self, deux_projets: tuple[Path, Path]) -> None:
        alpha, beta = deux_projets

        async def scenario(session: ClientSession) -> tuple[bool, str, bool, str]:
            first = await session.call_tool("grimoire_config", {"project_path": "."})
            second = await session.call_tool("grimoire_config", {"project_path": str(beta)})
            third = await session.call_tool("grimoire_config", {"project_path": "."})
            fourth = await session.call_tool("grimoire_config", {"project_path": str(alpha)})
            return _is_error(first), _body(second)["project"]["name"], _is_error(third), _body(fourth)["project"]["name"]

        assert via_client(scenario) == (True, "beta", True, "alpha")


# ── stdio : rien ne change ───────────────────────────────────────────────────

class TestStdioInchange:
    def test_point_reste_le_cwd(self, deux_projets: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
        alpha, _beta = deux_projets
        monkeypatch.delenv(project_module.TRANSPORT_ENV, raising=False)
        monkeypatch.chdir(alpha)

        async def scenario(session: ClientSession) -> str:
            return _body(await session.call_tool("grimoire_config", {"project_path": "."}))["project"]["name"]

        assert via_client(scenario) == "alpha"
        # et la fonction nue reste appelable en direct, comme toute la suite le fait
        assert json.loads(grimoire_config("."))["project"]["name"] == "alpha"

    def test_le_schema_des_outils_ne_montre_pas_le_contexte(self) -> None:
        async def scenario(session: ClientSession) -> dict[str, list[str]]:
            listed = await session.list_tools()
            return {
                t.name: sorted(t.model_dump(by_alias=True)["inputSchema"]["properties"]) for t in listed.tools
            }

        schemas = via_client(scenario)
        assert "grimoire_ctx" not in schemas["grimoire_memory_store"]
        assert schemas["grimoire_memory_store"] == ["project_path", "text", "user_id"]
        assert all("project_path" in props for props in schemas.values()), schemas


# ── point d'entrée ───────────────────────────────────────────────────────────

class TestMain:
    def test_http_pose_le_transport_et_n_enregistre_pas_la_surface(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(project_module.TRANSPORT_ENV, raising=False)
        served: list[tuple[str, int]] = []
        registered: list[Any] = []
        monkeypatch.setattr(server_module, "run_http", lambda host, port: served.append((host, port)))
        monkeypatch.setattr(server_module, "_register_surface", lambda *a: registered.append(a))
        monkeypatch.setattr(server_module.mcp, "run", lambda *a, **k: pytest.fail("stdio lancé en mode http"))
        server_module.main(["--http", "--port", "7390"])
        assert served == [("127.0.0.1", 7390)]
        assert registered == []
        assert transport() == "http"

    def test_stdio_enregistre_la_surface_du_cwd_puis_sert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(project_module.TRANSPORT_ENV, raising=False)
        order: list[str] = []
        monkeypatch.setattr(server_module, "_register_surface", lambda *a: order.append("surface") or (0, 0))
        monkeypatch.setattr(server_module.mcp, "run", lambda *a, **k: order.append("run"))
        monkeypatch.setattr(server_module, "run_http", lambda host, port: pytest.fail("http lancé en mode stdio"))
        server_module.main([])
        assert order == ["surface", "run"]
        assert transport() == "stdio"
