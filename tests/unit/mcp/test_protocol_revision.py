"""Négociation de la révision de protocole MCP (issue #436).

Le reste de la suite (`test_server.py`, `test_task_tools.py`) appelle les
fonctions-outils du pont directement, en process : ça vérifie leur contrat,
jamais ce qu'un vrai client MCP négocie sur le fil. Ce module fait tourner le
serveur `grimoire` (`grimoire.mcp.server.mcp`) sur des flux en mémoire et le
pilote avec un vrai `ClientSession`, pour prouver deux choses que
`pyproject.toml` promet désormais (`mcp>=2.0,<3`) sans qu'aucune ligne du pont
n'ait dû changer pour ça :

- un hôte moderne (`server/discover`) négocie 2026-07-28 ;
- un hôte plus ancien (`initialize` à 2025-06-18) est toujours servi —
  `serve_dual_era_loop`, côté SDK, sert les deux ères sur la même connexion.

Et une troisième que le mode sans état de 2026-07-28 exige : aucun état ne
survit d'une connexion à l'autre, hors les fichiers du projet lui-même.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest

pytest.importorskip("mcp", reason="extra optionnel grimoire-kit[mcp] non installé")

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from grimoire.mcp.server import mcp as _grimoire_mcp

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    """`trio` n'est pas une dépendance du kit ; ne négocier que `asyncio`."""
    return "asyncio"


def _lowlevel_server() -> Any:
    """Le serveur bas niveau que `MCPServer` enveloppe.

    `MCPServer._lowlevel_server` n'a pas d'accesseur public au 2.2.0— le SDK
    lui-même le traverse de la même façon dans son propre outillage de test
    (`mcp.client._memory.InMemoryTransport`, sous le même commentaire « à
    rendre public »). Vérifié contre mcp 2.0.0 à 2.2.0, pas supposé.
    """
    return _grimoire_mcp._lowlevel_server  # type: ignore[attr-defined]


@asynccontextmanager
async def _connected_session() -> AsyncIterator[ClientSession]:
    """Le serveur grimoire réel, sur des flux en mémoire, sans poignée de main
    automatique — chaque test choisit `discover()` ou un `initialize` explicite.
    """
    server = _lowlevel_server()
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        server_done = anyio.Event()

        async def _run_server() -> None:
            try:
                await server.run(server_read, server_write, server.create_initialization_options())
            finally:
                server_done.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run_server)
            async with ClientSession(client_read, client_write) as session:
                yield session
            await client_write.aclose()
            await server_write.aclose()
            with anyio.move_on_after(2.0):
                await server_done.wait()
            if not server_done.is_set():
                tg.cancel_scope.cancel()


class TestNegociationModerneParDiscover:
    """`server/discover` : la sonde que 2026-07-28 rend obligatoire côté serveur."""

    async def test_discover_annonce_2026_07_28(self) -> None:
        async with _connected_session() as session:
            result = await session.discover()

        assert "2026-07-28" in result.supported_versions
        assert session.protocol_version == "2026-07-28"

    async def test_discover_annonce_la_capacite_outils(self) -> None:
        """Le pont expose des outils : la capacité doit apparaître sans `initialize`."""
        async with _connected_session() as session:
            result = await session.discover()
            assert result.capabilities.tools is not None
            listed = await session.list_tools()

        names = {t.name for t in listed.tools}
        assert "grimoire_status" in names
        assert "task_list_ready" in names


class TestCompatibiliteHandshakeLegacy:
    """Un hôte qui ne connaît que l'ère `initialize` reste servi (SEP-2575)."""

    async def test_initialize_2025_06_18_est_neglocie_a_l_identique(self) -> None:
        async with _connected_session() as session:
            result = await session.send_request(
                types.InitializeRequest(
                    params=types.InitializeRequestParams(
                        protocol_version="2025-06-18",
                        capabilities=types.ClientCapabilities(),
                        client_info=types.Implementation(name="hote-legacy-test", version="1.0"),
                    ),
                ),
                types.InitializeResult,
            )
            session.adopt(result)
            await session.send_notification(types.InitializedNotification())
            listed = await session.list_tools()

        # Un hôte 2025-06-18 n'a pas de raison de voir une révision plus
        # récente que celle qu'il a proposée : le serveur ne doit pas monter
        # la mise de son côté.
        assert result.protocol_version == "2025-06-18"
        assert {"grimoire_status", "grimoire_project_context"} <= {t.name for t in listed.tools}

    async def test_initialize_2025_11_25_reste_servi(self) -> None:
        """La révision juste avant 2026-07-28, elle aussi côté handshake legacy."""
        async with _connected_session() as session:
            result = await session.initialize()
            await session.list_tools()

        assert result.protocol_version == "2025-11-25"


class TestAucunEtatEntreDeuxConnexions:
    """2026-07-28 rend le protocole sans état : rien ne doit fuiter d'une
    connexion à l'autre en dehors des fichiers du projet ciblé.
    """

    async def test_deux_connexions_independantes_repondent_pareil(self, tmp_path: Path) -> None:
        async def _status_pour(project_path: str) -> dict[str, Any]:
            async with _connected_session() as session:
                await session.discover()
                result = await session.call_tool("grimoire_status", {"project_path": project_path})
            assert result.is_error is not True
            import json

            return json.loads(result.content[0].text)  # type: ignore[union-attr]

        projet_a = tmp_path / "projet-a"
        projet_b = tmp_path / "projet-b"
        projet_a.mkdir()
        projet_b.mkdir()

        status_a_1 = await _status_pour(str(projet_a))
        status_b = await _status_pour(str(projet_b))
        status_a_2 = await _status_pour(str(projet_a))

        # Chaque connexion ne voit que le projet qu'on lui donne : la
        # deuxième lecture de A n'est pas polluée par le passage sur B.
        assert status_a_1["project_root"] == status_a_2["project_root"] == str(projet_a)
        assert status_b["project_root"] == str(projet_b)
        assert status_a_1 == status_a_2
