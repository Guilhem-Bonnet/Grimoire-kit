"""Durcissement du catalogue de skills servis par MCP (relecture adversariale de #1474).

Classe : le serveur MCP ne sert que des fichiers réguliers du projet, sans suivre de
lien, dans des limites de taille, quel que soit le schéma d'URI ; une erreur sur UN
fichier est l'exclusion motivée de CE skill, jamais une exception ni la perte des
autres ; aucun texte de fichier n'est relayé sans borne.

Ne dépend pas du SDK ``mcp`` sauf les tests de protocole, qui l'importent à part.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli.doctor_mcp import check_mcp_skills
from grimoire.mcp import skills_catalogue
from grimoire.mcp.project import TRANSPORT_ENV
from grimoire.mcp.skills_catalogue import (
    HOST_MAX_FILE_BYTES,
    MAX_EXAMINED,
    ORIGIN_KEY,
    build_catalogue,
    safe_text,
)
from grimoire.mcp.skills_serving import serve, skill_tool_result


def _skill(directory: Path, slug: str, *, extra: str = "", body: str = "# Corps\n", description: str = "Quand.") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{slug}.md"
    path.write_text(f"---\ndescription: {description}\n{extra}---\n\n{body}", encoding="utf-8")
    return path


@pytest.fixture
def kit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    framework = tmp_path / "framework"
    directory = framework / "hosts" / "skills"
    directory.mkdir(parents=True)
    monkeypatch.setattr("grimoire.data.framework_path", lambda: framework)
    monkeypatch.setattr("grimoire.hosts.collect.framework_path", lambda: framework)
    monkeypatch.delenv(TRANSPORT_ENV, raising=False)
    return directory


@pytest.fixture
def project(tmp_path: Path, kit: Path) -> Path:
    root = tmp_path / "projet"
    root.mkdir()
    return root


def _dir(root: Path) -> Path:
    return root / "_grimoire" / "overrides" / "skills"


class TestProvenance:
    def test_un_skill_du_projet_ne_masque_pas_un_skill_du_kit(self, project: Path, kit: Path) -> None:
        _skill(kit, "grimoire-memory", body="# VERSION DU KIT\n")
        _skill(_dir(project), "grimoire-memory", body="# VERSION PIRATE\n")
        _skill(_dir(project), "mon-skill")

        catalogue = build_catalogue(project)

        by_name = {s.name: s for s in catalogue.served}
        assert "VERSION DU KIT" in by_name["grimoire-memory"].files[0].text
        assert "PIRATE" not in json.dumps([s.files[0].text for s in catalogue.served])
        assert by_name["grimoire-memory"].origin == "kit" and by_name["mon-skill"].origin == "project"
        assert [(e.slug, e.reason) for e in catalogue.excluded] == [
            ("grimoire-memory", "masque un skill du kit : renomme-le")
        ]

    def test_chaque_entree_annonce_son_origine_dans_meta(self, project: Path, kit: Path) -> None:
        _skill(kit, "du-kit")
        _skill(_dir(project), "du-projet")
        entries = {s.name: s.entry() for s in build_catalogue(project).served}
        assert entries["du-kit"]["_meta"] == {ORIGIN_KEY: "kit"}
        assert entries["du-projet"]["_meta"] == {ORIGIN_KEY: "project"}

    def test_doctor_reprend_l_origine_et_le_masquage(self, project: Path, kit: Path) -> None:
        _skill(kit, "grimoire-memory")
        _skill(_dir(project), "grimoire-memory")
        _skill(_dir(project), "mon-skill")
        detail = check_mcp_skills(project).detail
        assert "origine : kit 1, projet 1" in detail
        assert "grimoire-memory (masque un skill du kit : renomme-le)" in detail

    def test_la_copie_du_projet_en_lien_ne_ressert_pas_la_sienne(self, project: Path, kit: Path, tmp_path: Path) -> None:
        """Même nom que le kit : seule la version du kit est servie, quel que soit l'état de la copie du projet."""
        _skill(kit, "commun", body="# KIT\n")
        outside = tmp_path / "dehors.md"
        outside.write_text("---\ndescription: x\n---\n\nDEHORS\n", encoding="utf-8")
        _dir(project).mkdir(parents=True)
        (_dir(project) / "commun.md").symlink_to(outside)
        catalogue = build_catalogue(project)
        assert [s.name for s in catalogue.served] == ["commun"]
        assert "DEHORS" not in catalogue.served[0].files[0].text


class TestRobustesseDesFichiers:
    """Aucune erreur sur UN fichier ne fait perdre les autres, ni ne bloque, ni ne lève."""

    def _assert_sain_seul(self, project: Path, mauvais: str) -> None:
        catalogue = build_catalogue(project)
        assert [s.name for s in catalogue.served] == ["sain"]
        assert [e.slug for e in catalogue.excluded] == [mauvais]
        detail = check_mcp_skills(project).detail
        assert detail.startswith("skills servis par MCP : 1 ") and mauvais in detail

    def test_lien_vers_un_peripherique_infini(self, project: Path) -> None:
        _skill(_dir(project), "sain")
        (_dir(project) / "zero.md").symlink_to("/dev/zero")
        start = time.monotonic()
        self._assert_sain_seul(project, "zero")
        assert time.monotonic() - start < 5

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="pas de tube nommé")
    def test_tube_nomme(self, project: Path) -> None:
        _skill(_dir(project), "sain")
        os.mkfifo(_dir(project) / "tuyau.md")
        start = time.monotonic()
        self._assert_sain_seul(project, "tuyau")
        assert time.monotonic() - start < 5

    def test_utf8_invalide(self, project: Path) -> None:
        _skill(_dir(project), "sain")
        (_dir(project) / "casse.md").write_bytes(b"---\ndescription: x\n---\n\xff\xfe\n")
        self._assert_sain_seul(project, "casse")

    def test_dossier_nomme_comme_un_skill(self, project: Path) -> None:
        _skill(_dir(project), "sain")
        (_dir(project) / "dossier.md").mkdir()
        self._assert_sain_seul(project, "dossier")

    def test_lecture_bornee_meme_si_la_taille_annoncee_ment(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un fichier qui grossit entre le ``lstat`` et la lecture reste borné à limite + 1 octets."""
        _skill(_dir(project), "sain")
        _skill(_dir(project), "gros", body="x" * (HOST_MAX_FILE_BYTES + 10))
        real_lstat = Path.lstat

        class _Small:
            def __init__(self, real: os.stat_result) -> None:
                self.st_mode = real.st_mode
                self.st_size = 10

        def lying(self: Path) -> Any:
            real = real_lstat(self)
            return _Small(real) if self.name == "gros.md" else real

        monkeypatch.setattr(Path, "lstat", lying)
        catalogue = build_catalogue(project)
        assert [s.name for s in catalogue.served] == ["sain"]
        assert "au-delà" in {e.slug: e.reason for e in catalogue.excluded}["gros"]

    def test_dix_mille_fichiers_temps_borne_et_les_autres_restent_servis(self, project: Path) -> None:
        directory = _dir(project)
        _skill(directory, "a-sain")
        for i in range(10_000):
            (directory / f"z{i:05d}.md").write_text("---\ndescription: d\n---\nc\n", encoding="utf-8")
        start = time.monotonic()
        catalogue = build_catalogue(project)
        elapsed = time.monotonic() - start
        assert elapsed < 10
        assert len(catalogue.served) <= MAX_EXAMINED
        assert "a-sain" in {s.name for s in catalogue.served}
        assert any("trop fourni" in e.reason for e in catalogue.excluded)

    def test_erreur_inattendue_sur_un_fichier_n_exclut_que_lui(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _skill(_dir(project), "sain")
        _skill(_dir(project), "piege")
        real = skills_catalogue._parse

        def exploding(slug: str, origin: str, text: str) -> Any:
            if slug == "piege":
                raise RuntimeError("contenu du fichier : [red]secret[/red]")
            return real(slug, origin, text)

        monkeypatch.setattr(skills_catalogue, "_parse", exploding)
        catalogue = build_catalogue(project)
        assert [s.name for s in catalogue.served] == ["sain"]
        # la classe de l'erreur seulement, jamais son message
        assert [e.reason for e in catalogue.excluded] == ["fichier illisible (RuntimeError)"]

    def test_dossier_de_skills_illisible_est_une_exclusion(self, project: Path, kit: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _skill(_dir(project), "sain")

        def refuse(path: Any) -> Any:
            raise PermissionError("non")

        real = os.scandir
        monkeypatch.setattr(os, "scandir", lambda p: refuse(p) if "overrides" in str(p) else real(p))
        catalogue = build_catalogue(project)
        assert catalogue.served == ()
        assert any("dossier illisible" in e.reason for e in catalogue.excluded)


class TestTroisChemins:
    """skill://, grimoire://skill/ et grimoire_skill : mêmes gardes, mêmes exclusions."""

    @pytest.fixture
    def cas(self, project: Path, tmp_path: Path) -> Path:
        directory = _dir(project)
        _skill(directory, "sain")
        outside = tmp_path / "secret.md"
        outside.write_text("---\ndescription: s\n---\n\nTOP-SECRET\n", encoding="utf-8")
        (directory / "lien.md").symlink_to(outside)
        _skill(directory, "gros", body="x" * (HOST_MAX_FILE_BYTES + 1))
        _skill(directory, "diverge", extra="name: autre\n")
        return project

    def _refuses(self) -> list[str]:
        return ["lien", "gros", "diverge"]

    @pytest.mark.anyio
    async def test_aucun_chemin_ne_sert_un_skill_refuse(self, cas: Path) -> None:
        pytest.importorskip("mcp")
        from mcp.server.mcpserver import MCPServer

        server = MCPServer(name="t")
        assert serve(server, cas) == 1
        listed = {str(r.uri) for r in await server.list_resources()}
        assert listed == {"skill://sain/SKILL.md"}
        for slug in self._refuses():
            with pytest.raises(Exception, match=r"."):
                list(await server.read_resource(f"skill://{slug}/SKILL.md"))
            with pytest.raises(Exception, match=r"."):
                list(await server.read_resource(f"grimoire://skill/{slug}"))
            out = json.loads(skill_tool_result(slug, cas, lambda p: json.dumps(p)))
            assert out["available"] == ["sain"] and "excluded" in out and "body" not in out
        ok = list(await server.read_resource("grimoire://skill/sain"))
        assert "# Corps" in ok[0].content
        assert json.loads(skill_tool_result("sain", cas, lambda p: json.dumps(p)))["slug"] == "sain"

    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    def test_dossier_parent_en_lien_non_servi_par_les_trois_chemins(self, project: Path, tmp_path: Path) -> None:
        elsewhere = tmp_path / "ailleurs"
        _skill(elsewhere, "evade", body="HORS-PROJET\n")
        _dir(project).parent.mkdir(parents=True)
        _dir(project).symlink_to(elsewhere, target_is_directory=True)
        assert build_catalogue(project).served == ()
        out = json.loads(skill_tool_result("evade", project, lambda p: json.dumps(p)))
        assert "body" not in out and "HORS-PROJET" not in json.dumps(out)

    def test_l_outil_ne_lit_que_le_projet_de_la_requete(self, project: Path, tmp_path: Path) -> None:
        autre = tmp_path / "autre-projet"
        autre.mkdir()
        _skill(_dir(autre), "propre-a-l-autre")
        out = json.loads(skill_tool_result("propre-a-l-autre", project, lambda p: json.dumps(p)))
        assert "body" not in out
        assert json.loads(skill_tool_result("propre-a-l-autre", autre, lambda p: json.dumps(p)))["slug"] == "propre-a-l-autre"

    def test_l_outil_repond_meme_si_la_lecture_globale_echoue(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_root: Path, *, log: bool = False) -> None:
            raise OSError("x")

        monkeypatch.setattr("grimoire.mcp.skills_serving.build_catalogue", boom)
        out = json.loads(skill_tool_result("x", project, lambda p: json.dumps(p)))
        assert out == {"error": "Skills unreadable (OSError)"}


class TestTexteHostile:
    def test_safe_text_borne_assainit_et_neutralise(self) -> None:
        hostile = "[bold red]x[/bold red]\x1b[2J\x00\n" + "a" * 5000
        out = safe_text(hostile)
        assert len(out) <= 80
        assert "[" not in out and "]" not in out
        assert all(ch.isprintable() for ch in out)

    def test_une_raison_d_exclusion_ne_relaie_pas_le_name_tel_quel(self, project: Path) -> None:
        hostile = "[link=file:///etc/passwd]x[/link]\x1b[31m" + "n" * 5000
        _skill(_dir(project), "diverge", extra=f'name: "{hostile.replace(chr(27), "")}"\n')
        (excluded,) = build_catalogue(project).excluded
        assert len(excluded.reason) <= skills_catalogue.REASON_DISPLAY_MAX
        assert "[" not in excluded.reason and "\x1b" not in excluded.reason
        assert "[link" not in check_mcp_skills(project).detail

    def test_un_nom_de_fichier_hostile_est_assaini_dans_doctor(self, project: Path) -> None:
        _skill(_dir(project), "[red]Mauvais")
        detail = check_mcp_skills(project).detail
        assert "[red]" not in detail and "(red)Mauvais" in detail
        assert "\n" not in detail


@pytest.mark.anyio
class TestProtocoleHostile:
    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    async def test_uri_echo_bornee_dans_l_erreur_32602(self, project: Path) -> None:
        pytest.importorskip("mcp")
        from mcp.server.mcpserver import MCPServer
        from mcp.shared.exceptions import MCPError

        from grimoire.mcp.skills_extension import SkillsExtension

        from .test_skills_extension import _rpc, _session

        ext = SkillsExtension()
        server = MCPServer(name="t", extensions=[ext])
        serve(server, project, ext)
        async with _session(server) as session:
            with pytest.raises(MCPError) as err:
                await _rpc(session, "skills/get", uri="skill://" + "x" * 5000 + "\x1b[31m")
        assert len(err.value.message) < 300 and "\x1b" not in err.value.message

    async def test_resources_list_porte_la_description_relue_du_frontmatter(self, project: Path) -> None:
        pytest.importorskip("mcp")
        from mcp.server.mcpserver import MCPServer

        directory = _dir(project)
        directory.mkdir(parents=True)
        (directory / "multi.md").write_text("---\ndescription: 'ligne un\n  ligne deux'\n---\n\nC\n", encoding="utf-8")
        server = MCPServer(name="t")
        serve(server, project)
        (resource,) = await server.list_resources()
        served = build_catalogue(project).served[0]
        assert resource.description == served.frontmatter["description"] == served.description
        assert "\n" not in (resource.description or "")
