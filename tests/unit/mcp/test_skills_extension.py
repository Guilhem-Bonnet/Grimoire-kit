"""Extension MCP « Skills » (``io.modelcontextprotocol/skills``, SEP-2640).

Au niveau du protocole : un vrai ``ClientSession`` pilote un serveur sur des flux
en mémoire et émet ``server/discover``, ``skills/list``, ``skills/get`` et
``resources/read`` (la révision 2026-07-28 : chaque requête porte son ``_meta``,
posé par la session). Les digests sont recalculés depuis les octets lus.

Chaque test construit son propre ``MCPServer`` et sa propre extension : le
singleton ``grimoire.mcp.server.mcp`` n'est touché que par les tests qui
vérifient son câblage.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest

pytest.importorskip("mcp", reason="extra optionnel grimoire-kit[mcp] non installé")

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp.shared.memory import create_client_server_memory_streams
from pydantic import ConfigDict, TypeAdapter

from grimoire.hosts.collect import parse_frontmatter
from grimoire.mcp import skills_extension
from grimoire.mcp.project import TRANSPORT_ENV, TRANSPORT_HTTP
from grimoire.mcp.skills_catalogue import HOST_MAX_FILE_BYTES, build_catalogue, digest_of
from grimoire.mcp.skills_extension import EXTENSION_ID, SkillsExtension

pytestmark = pytest.mark.anyio

_RAW = TypeAdapter(dict[str, Any])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _skill(directory: Path, slug: str, *, description: str = "Quand l'utiliser.", extra: str = "", body: str = "# Corps\n") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.md"
    path.write_text(f"---\ndescription: {description}\n{extra}---\n\n{body}", encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un projet sans skills livrés avec le kit : seul ce que le test écrit est visible."""
    framework = tmp_path / "framework"
    (framework / "hosts" / "skills").mkdir(parents=True)
    monkeypatch.setattr("grimoire.data.framework_path", lambda: framework)
    monkeypatch.setattr("grimoire.hosts.collect.framework_path", lambda: framework)
    # D'autres modules de tests posent le transport http dans l'environnement : ici on est en stdio.
    monkeypatch.delenv(TRANSPORT_ENV, raising=False)
    root = tmp_path / "projet"
    root.mkdir()
    return root


def _skills_dir(root: Path) -> Path:
    return root / "_grimoire" / "overrides" / "skills"


def _lowlevel(server: MCPServer) -> Any:
    return server._lowlevel_server  # type: ignore[attr-defined]  # pas d'accesseur public (voir test_protocol_revision)


@asynccontextmanager
async def _session(server: MCPServer) -> AsyncIterator[ClientSession]:
    lowlevel = _lowlevel(server)
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        done = anyio.Event()

        async def _run() -> None:
            try:
                await lowlevel.run(server_read, server_write, lowlevel.create_initialization_options())
            finally:
                done.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(_run)
            async with ClientSession(client_read, client_write) as session:
                await session.discover()
                yield session
            await client_write.aclose()
            await server_write.aclose()
            with anyio.move_on_after(2.0):
                await done.wait()
            if not done.is_set():
                tg.cancel_scope.cancel()


class _AnyParams(types.RequestParams):
    """Paramètres libres : le client typé ne connaît pas ``skills/*``, on les lui donne tels quels."""

    model_config = ConfigDict(extra="allow")


async def _rpc(session: ClientSession, method: str, **params: Any) -> dict[str, Any]:
    return await session.send_request(types.Request(method=method, params=_AnyParams(**params)), _RAW)


async def _read(session: ClientSession, uri: str) -> str:
    result = await session.read_resource(uri)
    content = result.contents[0]
    assert isinstance(content, types.TextResourceContents)
    return content.text


def _served(root: Path) -> tuple[MCPServer, SkillsExtension]:
    ext = SkillsExtension()
    server = MCPServer(name="grimoire-test", extensions=[ext])
    ext.serve(server, root)
    return server, ext


class TestCapacite:
    async def test_discover_annonce_l_extension_sans_directory_read(self, project: Path) -> None:
        server, _ = _served(project)
        async with _session(server) as session:
            discovered = session.discover_result
        assert discovered is not None
        caps = discovered.capabilities
        assert caps.resources is not None, "la spécification exige la capacité resources"
        assert caps.extensions is not None
        assert caps.extensions[EXTENSION_ID] == {"directoryRead": False}

    async def test_resources_directory_read_n_est_pas_servi(self, project: Path) -> None:
        server, _ = _served(project)
        async with _session(server) as session:
            with pytest.raises(MCPError) as err:
                await _rpc(session, "resources/directory/read", uri="skill://x")
        assert err.value.code == types.METHOD_NOT_FOUND


class TestListEtLecture:
    async def test_les_digests_et_tailles_sont_ceux_des_octets_servis(self, project: Path) -> None:
        _skill(_skills_dir(project), "release-notes", extra="license: Apache-2.0\n", body="# Notes é\n\nCorps accentué.\n")
        _skill(_skills_dir(project), "triage")
        server, _ = _served(project)
        async with _session(server) as session:
            listing = await _rpc(session, "skills/list")
            assert listing["resultType"] == "complete"
            assert listing["ttlMs"] == skills_extension.TTL_MS
            assert listing["cacheScope"] == "private"
            assert [s["uri"] for s in listing["skills"]] == [
                "skill://release-notes/SKILL.md",
                "skill://triage/SKILL.md",
            ]
            for entry in listing["skills"]:
                assert entry["frontmatter"]["name"] == entry["uri"].split("/")[2]
                (resource,) = entry["resources"]
                assert resource["uri"] == entry["uri"]
                text = await _read(session, entry["uri"])
                raw = text.encode("utf-8")
                assert resource["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()
                assert resource["size"] == len(raw)
                # le frontmatter de l'entrée est celui des octets servis
                assert parse_frontmatter(text)[0] == entry["frontmatter"]
        by_name = {s["frontmatter"]["name"]: s for s in listing["skills"]}
        assert by_name["release-notes"]["frontmatter"]["license"] == "Apache-2.0"
        assert "license" not in by_name["triage"]["frontmatter"]

    async def test_get_rend_la_meme_entree_que_la_liste(self, project: Path) -> None:
        _skill(_skills_dir(project), "triage")
        server, _ = _served(project)
        async with _session(server) as session:
            entry = (await _rpc(session, "skills/list"))["skills"][0]
            got = await _rpc(session, "skills/get", uri=entry["uri"])
        assert got["resultType"] == "complete"
        assert got["skill"] == entry
        assert got["ttlMs"] == skills_extension.TTL_MS and got["cacheScope"] == "private"

    async def test_les_champs_de_permission_ne_sont_jamais_emis(self, project: Path) -> None:
        _skill(_skills_dir(project), "triage", extra="tools: [read, execute]\nallowed-tools: Bash\n")
        server, _ = _served(project)
        async with _session(server) as session:
            entry = (await _rpc(session, "skills/list"))["skills"][0]
            text = await _read(session, entry["uri"])
        assert set(entry["frontmatter"]) == {"name", "description"}
        assert "allowed-tools" not in text and "tools:" not in text

    async def test_aucun_chemin_de_la_machine_dans_les_reponses(self, project: Path) -> None:
        _skill(_skills_dir(project), "triage")
        server, _ = _served(project)
        async with _session(server) as session:
            listing = await _rpc(session, "skills/list")
            got = await _rpc(session, "skills/get", uri="skill://triage/SKILL.md")
            text = await _read(session, "skill://triage/SKILL.md")
        blob = json.dumps([listing, got, text])
        assert str(project) not in blob and str(project.parent) not in blob


class TestRefus:
    @pytest.mark.parametrize(
        "uri",
        [
            "skill://inconnu/SKILL.md",
            "skill://triage/autre.md",  # fichier annexe absent du manifeste
            "skill://triage",  # racine : directoryRead n'est pas servi
            "skill://triage/../../etc/passwd",
            "skill://triage/%2e%2e/%2e%2e/etc/passwd",
            "file:///etc/passwd",
            "",
        ],
    )
    async def test_uri_hors_manifeste_refusee_en_32602(self, project: Path, uri: str) -> None:
        _skill(_skills_dir(project), "triage")
        server, _ = _served(project)
        async with _session(server) as session:
            with pytest.raises(MCPError) as get_err:
                await _rpc(session, "skills/get", uri=uri)
            with pytest.raises(MCPError) as read_err:
                await _read(session, uri)
        assert get_err.value.code == types.INVALID_PARAMS
        assert read_err.value.code == types.INVALID_PARAMS

    async def test_get_sur_une_uri_de_fichier_annexe_est_refuse(self, project: Path) -> None:
        """``params.uri`` doit être celle d'un ``SKILL.md`` : l'URI d'une ressource ordinaire ne suffit pas."""
        _skill(_skills_dir(project), "triage")
        server, _ = _served(project)
        server.resource("grimoire://autre", name="autre")(lambda: "x")
        async with _session(server) as session:
            with pytest.raises(MCPError) as err:
                await _rpc(session, "skills/get", uri="grimoire://autre")
        assert err.value.code == types.INVALID_PARAMS


class TestPagination:
    async def test_pagination_par_curseur_sans_couper_ni_perdre(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(skills_extension, "PAGE_SIZE", 2)
        for slug in ("a-un", "b-deux", "c-trois", "d-quatre", "e-cinq"):
            _skill(_skills_dir(project), slug)
        server, _ = _served(project)
        seen: list[str] = []
        pages = 0
        async with _session(server) as session:
            cursor: str | None = None
            while True:
                params = {"cursor": cursor} if cursor else {}
                page = await _rpc(session, "skills/list", **params)
                pages += 1
                seen += [s["frontmatter"]["name"] for s in page["skills"]]
                assert page["resultType"] == "complete"
                cursor = page.get("nextCursor")
                if cursor is None:
                    break
        assert pages == 3
        assert seen == ["a-un", "b-deux", "c-trois", "d-quatre", "e-cinq"]

    @pytest.mark.parametrize("cursor", ["abc", "o-1", "o99", "o", "ox1"])
    async def test_curseur_invalide_refuse(self, project: Path, cursor: str) -> None:
        _skill(_skills_dir(project), "triage")
        server, _ = _served(project)
        async with _session(server) as session:
            with pytest.raises(MCPError) as err:
                await _rpc(session, "skills/list", cursor=cursor)
        assert err.value.code == types.INVALID_PARAMS


class TestExclusions:
    async def test_chaque_exclusion_a_sa_raison_et_rien_n_est_renomme(self, project: Path, tmp_path: Path) -> None:
        directory = _skills_dir(project)
        _skill(directory, "valide")
        _skill(directory, "Majuscule")
        _skill(directory, "double--tiret")
        _skill(directory, "autre-nom", extra="name: pas-le-dossier\n")
        _skill(directory, "gros", body="x" * (HOST_MAX_FILE_BYTES + 1))
        _skill(directory, "binaire", body="a\x00b\n")
        _skill(directory, "sans-description", description="d" * 1025)
        outside = tmp_path / "secret.md"
        outside.write_text("---\ndescription: secret\n---\n\nTOP SECRET\n", encoding="utf-8")
        (directory / "lien.md").symlink_to(outside)

        catalogue = build_catalogue(project)
        reasons = {e.slug: e.reason for e in catalogue.excluded}
        assert [s.name for s in catalogue.served] == ["valide"]
        assert "majuscule" in reasons["Majuscule"]
        assert "tiret" in reasons["double--tiret"]
        assert "pas-le-dossier" in reasons["autre-nom"] and "jamais renommé" in reasons["autre-nom"]
        assert "au-delà" in reasons["gros"]
        assert "binaire" in reasons["binaire"]
        assert "description" in reasons["sans-description"] and "1024" in reasons["sans-description"]
        assert "lien symbolique" in reasons["lien"]
        assert not any(str(tmp_path) in r for r in reasons.values())

        server, _ = _served(project)
        async with _session(server) as session:
            names = [s["frontmatter"]["name"] for s in (await _rpc(session, "skills/list"))["skills"]]
            with pytest.raises(MCPError):
                await _read(session, "skill://lien/SKILL.md")
        assert names == ["valide"]
        assert "TOP SECRET" not in json.dumps([e.reason for e in catalogue.excluded])

    async def test_dossier_de_skills_en_lien_vers_l_exterieur_exclu(self, project: Path, tmp_path: Path) -> None:
        elsewhere = tmp_path / "ailleurs"
        _skill(elsewhere, "evade")
        directory = _skills_dir(project)
        directory.parent.mkdir(parents=True)
        directory.symlink_to(elsewhere, target_is_directory=True)

        catalogue = build_catalogue(project)
        assert catalogue.served == ()
        assert [e.reason for e in catalogue.excluded] == ["dossier des skills hors du projet"]

    async def test_un_skill_du_projet_ecarte_ne_laisse_pas_resservir_celui_du_kit(
        self, project: Path, tmp_path: Path
    ) -> None:
        """Le premier fichier trouvé fait foi : refusé, on ne retombe pas sur la copie livrée."""
        _skill(tmp_path / "framework" / "hosts" / "skills", "triage", description="livré")
        outside = tmp_path / "x.md"
        outside.write_text("---\ndescription: pirate\n---\n\ncorps\n", encoding="utf-8")
        directory = _skills_dir(project)
        directory.mkdir(parents=True)
        (directory / "triage.md").symlink_to(outside)

        catalogue = build_catalogue(project)
        assert catalogue.served == ()
        assert [e.slug for e in catalogue.excluded] == ["triage"]

    async def test_skill_du_kit_servi_quand_le_projet_n_en_a_pas(self, project: Path, tmp_path: Path) -> None:
        _skill(tmp_path / "framework" / "hosts" / "skills", "livre", description="livré avec le kit")
        server, _ = _served(project)
        async with _session(server) as session:
            names = [s["frontmatter"]["name"] for s in (await _rpc(session, "skills/list"))["skills"]]
        assert names == ["livre"]

    async def test_exclusion_journalisee(self, project: Path, caplog: pytest.LogCaptureFixture) -> None:
        _skill(_skills_dir(project), "Majuscule")
        with caplog.at_level("WARNING", logger="grimoire.mcp.skills"):
            _served(project)
        assert any("Majuscule" in r.getMessage() and "non servi" in r.getMessage() for r in caplog.records)


class TestCompatibilite:
    async def test_sans_serve_aucun_skill_n_est_servi(self, project: Path) -> None:
        """Un serveur dont personne n'a appelé ``serve`` : l'extension répond, vide, et rien d'autre ne change."""
        _skill(_skills_dir(project), "triage")
        ext = SkillsExtension()
        server = MCPServer(name="grimoire-test", extensions=[ext])
        async with _session(server) as session:
            assert (await _rpc(session, "skills/list"))["skills"] == []
            assert (await session.list_resources()).resources == []

    async def test_serveur_sans_l_extension_ne_connait_pas_skills_list(self) -> None:
        server = MCPServer(name="grimoire-test")
        async with _session(server) as session:
            assert session.discover_result is not None
            assert not (session.discover_result.capabilities.extensions or {})
            with pytest.raises(MCPError) as err:
                await _rpc(session, "skills/list")
        assert err.value.code == types.METHOD_NOT_FOUND

    async def test_le_serveur_grimoire_garde_prompts_ressources_et_outil(self, project: Path) -> None:
        """Le câblage réel : ``_register_surface`` sert toujours ``grimoire://skill/``, les prompts et l'outil."""
        import grimoire.mcp.server as srv

        _skill(_skills_dir(project), "compat-triage")
        prompts, resources = srv._register_surface(project)
        assert resources >= 1
        listed = {str(r.uri) for r in await srv.mcp.list_resources()}
        assert "grimoire://skill/compat-triage" in listed
        assert "skill://compat-triage/SKILL.md" in listed
        assert "grimoire_skill" in {t.name for t in await srv.mcp.list_tools()}
        assert prompts >= 0
        body = json.loads(srv.grimoire_skill("compat-triage", str(project)))
        assert body["slug"] == "compat-triage"
        assert srv._SKILLS is not None
        assert [s.name for s in srv._SKILLS.catalogue.served] == ["compat-triage"]

    async def test_le_singleton_annonce_l_extension(self) -> None:
        import grimoire.mcp.server as srv

        async with _session(srv.mcp) as session:
            discovered = session.discover_result
        assert discovered is not None and discovered.capabilities.extensions is not None
        assert EXTENSION_ID in discovered.capabilities.extensions


class TestDemonSansProjet:
    async def test_demon_http_ne_sert_aucun_skill(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Démon partagé (#723) : pas de projet résolu, donc pas de skills, même avec un cwd riche en skills."""
        _skill(_skills_dir(project), "triage")
        monkeypatch.setenv(TRANSPORT_ENV, TRANSPORT_HTTP)
        ext = SkillsExtension()
        server = MCPServer(name="grimoire-test", extensions=[ext])
        assert ext.serve(server, project) == 0
        async with _session(server) as session:
            assert session.discover_result is not None
            assert EXTENSION_ID in (session.discover_result.capabilities.extensions or {})
            assert (await _rpc(session, "skills/list"))["skills"] == []
            with pytest.raises(MCPError) as get_err:
                await _rpc(session, "skills/get", uri="skill://triage/SKILL.md")
            with pytest.raises(MCPError) as read_err:
                await _read(session, "skill://triage/SKILL.md")
        assert get_err.value.code == read_err.value.code == types.INVALID_PARAMS


class TestRobustesse:
    async def test_projet_illisible_n_empeche_pas_le_demarrage(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_root: Path, *, log: bool = False) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(skills_extension, "build_catalogue", boom)
        ext = SkillsExtension()
        assert ext.serve(MCPServer(name="t", extensions=[ext]), project) == 0
        assert ext.catalogue.served == ()

    async def test_ressource_refusee_par_le_sdk_ecarte_le_skill_seulement(self, project: Path) -> None:
        _skill(_skills_dir(project), "bon")
        _skill(_skills_dir(project), "mauvais")

        class Refuse:
            def resource(self, uri: str, **_kw: Any) -> Any:
                if "mauvais" in uri:
                    raise ValueError("refusé")
                return lambda fn: fn

        ext = SkillsExtension()
        assert ext.serve(Refuse(), project) == 1
        assert [s.name for s in ext.catalogue.served] == ["bon"]
        assert [(e.slug, e.reason) for e in ext.catalogue.excluded] == [("mauvais", "enregistrement de la ressource refusé")]


def test_digest_of_est_sha256_minuscule() -> None:
    assert digest_of(b"") == "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
