"""Collecte sûre des skills, commandes et agents d'un projet (durcissement, suite de #1474).

Classe : la collecte des skills, commandes et agents d'un projet ne suit plus de lien, borne ses
lectures et isole les fichiers illisibles. Un projet ouvert peut venir d'ailleurs ; une erreur sur UN
fichier est l'exclusion motivée de CE fichier, jamais une exception qui vide la surface, et l'exclusion
est dite (notes de surface, ``doctor``) en texte assaini et borné.

Les cas hostiles utilisent un fichier témoin hors du projet, jamais un fichier sensible.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.cli.doctor_mcp import check_mcp_skills, check_project_sources
from grimoire.core import safe_sources
from grimoire.core.safe_sources import SOURCE_MAX_BYTES, Exclusion, describe_exclusions, exclusion_lines
from grimoire.hosts import collect
from grimoire.hosts.collect import (
    build_surface,
    collect_agents,
    collect_commands,
    collect_skills,
    effective_agent_frontmatter,
    resolve_extends_kit,
)
from grimoire.hosts.emitters import emitter_for

TEMOIN = "TEMOIN-HORS-PROJET"
SAIN = "---\ndescription: Skill sain\n---\n\nCorps sain.\n"


@pytest.fixture
def kit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un paquet du kit factice, minimal : ses skills, commandes et prompts sont les seuls « livrés »."""
    framework = tmp_path / "framework"
    for sub in ("hosts/skills", "hosts/commands", "copilot/prompts"):
        (framework / sub).mkdir(parents=True)
    monkeypatch.setattr("grimoire.hosts.collect.framework_path", lambda: framework)
    monkeypatch.setattr("grimoire.data.framework_path", lambda: framework)
    return framework


@pytest.fixture
def project(tmp_path: Path, kit: Path) -> Path:
    root = tmp_path / "projet"
    root.mkdir()
    return root


@pytest.fixture
def temoin(tmp_path: Path) -> Path:
    path = tmp_path / "dehors" / "temoin.md"
    path.parent.mkdir()
    path.write_text(f"---\nname: temoin\ndescription: {TEMOIN}\n---\n\n{TEMOIN}\n", encoding="utf-8")
    return path


def _skills_dir(root: Path) -> Path:
    return root / "_grimoire" / "overrides" / "skills"


def _kit_tier_skills_dir(root: Path) -> Path:
    return root / "_grimoire" / "kit" / "skills"


def _agents_dir(root: Path) -> Path:
    return root / "_grimoire" / "overrides" / "agents"


def _agent_text(name: str, body: str = "Tu lis et tu édites.", extra: str = "") -> str:
    return f'---\nname: "{name}"\ndescription: "{name} — rôle de test"\ntools: "read, edit"\n{extra}---\n{body}\n'


def _put(directory: Path, filename: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


# ── Les fichiers hostiles : un par un, sur un dossier de skills du projet ─────────────────────────────


def _lien_sortant(directory: Path, temoin: Path) -> str:
    (directory / "mauvais.md").symlink_to(temoin)
    return "mauvais"


def _lien_peripherique(directory: Path, temoin: Path) -> str:
    (directory / "mauvais.md").symlink_to("/dev/zero")
    return "mauvais"


def _tube(directory: Path, temoin: Path) -> str:
    os.mkfifo(directory / "mauvais.md")
    return "mauvais"


def _utf8_invalide(directory: Path, temoin: Path) -> str:
    (directory / "mauvais.md").write_bytes(b"---\ndescription: x\n---\n\xff\xfe\n")
    return "mauvais"


def _dossier(directory: Path, temoin: Path) -> str:
    (directory / "mauvais.md").mkdir()
    return "mauvais"


def _gros(directory: Path, temoin: Path) -> str:
    (directory / "mauvais.md").write_text("---\ndescription: x\n---\n" + "x" * (SOURCE_MAX_BYTES + 1), encoding="utf-8")
    return "mauvais"


def _plusieurs_mo(directory: Path, temoin: Path) -> str:
    (directory / "mauvais.md").write_text("---\ndescription: x\n---\n" + "x" * (3 * 1024 * 1024), encoding="utf-8")
    return "mauvais"


HOSTILES: dict[str, Callable[[Path, Path], str]] = {
    "lien-vers-un-fichier-hors-projet": _lien_sortant,
    "lien-vers-un-peripherique-infini": _lien_peripherique,
    "tube-nomme": _tube,
    "utf8-invalide": _utf8_invalide,
    "dossier-nomme-comme-un-fichier": _dossier,
    "fichier-juste-trop-gros": _gros,
    "plusieurs-mo": _plusieurs_mo,
}


@pytest.fixture(params=sorted(HOSTILES))
def hostile(request: pytest.FixtureRequest) -> Callable[[Path, Path], str]:
    if request.param == "tube-nomme" and not hasattr(os, "mkfifo"):
        pytest.skip("pas de tube nommé")
    return HOSTILES[request.param]


class TestSkills:
    def test_un_fichier_hostile_n_exclut_que_lui(
        self, project: Path, temoin: Path, hostile: Callable[[Path, Path], str]
    ) -> None:
        directory = _skills_dir(project)
        _put(directory, "sain.md", SAIN)
        bad = hostile(directory, temoin)
        excluded: list[Exclusion] = []
        start = time.monotonic()
        skills = collect_skills(project, excluded=excluded)
        assert time.monotonic() - start < 5
        assert [s.slug for s in skills] == ["sain"]
        assert [e.name for e in excluded] == [f"skills/{bad}.md"]
        assert TEMOIN not in repr(skills) and str(temoin.parent) not in repr(excluded)

    def test_sur_un_projet_sain_rien_n_est_exclu_et_le_resultat_est_le_meme(self, project: Path, kit: Path) -> None:
        _put(kit / "hosts" / "skills", "du-kit.md", SAIN)
        _put(_skills_dir(project), "b.md", "---\nname: b\ndescription: B\ntools: [read]\n---\nCorps B\n")
        _put(_kit_tier_skills_dir(project), "a.md", SAIN)
        excluded: list[Exclusion] = []
        skills = collect_skills(project, excluded=excluded)
        assert excluded == []
        assert [s.slug for s in skills] == ["du-kit", "b", "a"]
        assert skills == collect_skills(project)
        assert skills[1].body == "Corps B\n" and skills[1].name == "b"

    def test_sans_le_canal_lateral_l_appel_historique_ne_leve_pas(
        self, project: Path, temoin: Path, hostile: Callable[[Path, Path], str]
    ) -> None:
        _put(_skills_dir(project), "sain.md", SAIN)
        hostile(_skills_dir(project), temoin)
        assert [s.slug for s in collect_skills(project)] == ["sain"]

    def test_le_dossier_des_skills_en_lien_n_est_pas_lu(self, project: Path, temoin: Path) -> None:
        _skills_dir(project).parent.mkdir(parents=True)
        _skills_dir(project).symlink_to(temoin.parent, target_is_directory=True)
        excluded: list[Exclusion] = []
        assert collect_skills(project, excluded=excluded) == ()
        assert len(excluded) == 1 and "lien symbolique" in excluded[0].reason

    def test_un_dossier_parent_en_lien_n_est_pas_lu(self, project: Path, temoin: Path) -> None:
        (project / "_grimoire").symlink_to(temoin.parent, target_is_directory=True)
        _put(temoin.parent / "overrides" / "skills", "evade.md", SAIN)
        excluded: list[Exclusion] = []
        assert collect_skills(project, excluded=excluded) == ()
        assert excluded and all("lien symbolique" in e.reason for e in excluded)

    def test_la_copie_refusee_du_projet_laisse_servir_celle_du_kit(
        self, project: Path, kit: Path, temoin: Path
    ) -> None:
        _put(kit / "hosts" / "skills", "commun.md", "---\ndescription: KIT\n---\n\nVersion du kit.\n")
        _skills_dir(project).mkdir(parents=True)
        (_skills_dir(project) / "commun.md").symlink_to(temoin)
        excluded: list[Exclusion] = []
        (skill,) = collect_skills(project, excluded=excluded)
        assert skill.slug == "commun" and skill.body == "Version du kit.\n"
        assert [e.name for e in excluded] == ["skills/commun.md"]

    def test_la_copie_refusee_de_l_override_laisse_servir_celle_du_tier_kit(self, project: Path, temoin: Path) -> None:
        _skills_dir(project).mkdir(parents=True)
        (_skills_dir(project) / "x.md").symlink_to(temoin)
        _put(_kit_tier_skills_dir(project), "x.md", "---\ndescription: TIER\n---\nTier kit.\n")
        excluded: list[Exclusion] = []
        (skill,) = collect_skills(project, excluded=excluded)
        assert skill.body == "Tier kit.\n" and len(excluded) == 1

    def test_la_copie_saine_du_projet_l_emporte_toujours_sur_le_kit(self, project: Path, kit: Path) -> None:
        _put(kit / "hosts" / "skills", "commun.md", "---\ndescription: KIT\n---\n\nKit.\n")
        _put(_skills_dir(project), "commun.md", "---\ndescription: PROJET\n---\n\nProjet.\n")
        (skill,) = collect_skills(project)
        assert skill.body == "Projet.\n" and skill.description == "PROJET"

    def test_dix_mille_fichiers_temps_borne_et_les_sains_restent(self, project: Path) -> None:
        directory = _skills_dir(project)
        _put(directory, "a-sain.md", SAIN)
        for i in range(10_000):
            (directory / f"z{i:05d}.md").write_text(SAIN, encoding="utf-8")
        excluded: list[Exclusion] = []
        start = time.monotonic()
        skills = collect_skills(project, excluded=excluded)
        assert time.monotonic() - start < 10
        assert len(skills) <= safe_sources.MAX_EXAMINED
        assert "a-sain" in {s.slug for s in skills}
        assert any("trop fourni" in e.reason for e in excluded)

    def test_la_lecture_sure_passe_avant_l_analyse_quel_que_soit_le_backend(
        self, project: Path, temoin: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for backend in ("python", "auto"):
            monkeypatch.setenv("GRIMOIRE_HOSTS_BACKEND", backend)
            seen: list[str] = []
            real = collect.parse_frontmatter

            def spy(text: str, _seen: list[str] = seen, _real: Callable[[str], object] = real) -> object:
                _seen.append(text)
                return _real(text)

            monkeypatch.setattr(collect, "parse_frontmatter", spy)
            _skills_dir(project).mkdir(parents=True, exist_ok=True)
            link = _skills_dir(project) / "lien.md"
            if not link.is_symlink():
                link.symlink_to(temoin)
            collect_skills(project)
            assert not any(TEMOIN in t for t in seen)
            monkeypatch.setattr(collect, "parse_frontmatter", real)

    def test_une_configuration_de_backend_invalide_reste_une_erreur(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from grimoire.core.exceptions import GrimoireAgentError

        monkeypatch.setenv("GRIMOIRE_HOSTS_BACKEND", "nimporte-quoi")
        with pytest.raises(GrimoireAgentError):
            collect_skills(project)

    def test_une_erreur_inattendue_sur_un_fichier_n_exclut_que_lui(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _put(_skills_dir(project), "sain.md", SAIN)
        _put(_skills_dir(project), "piege.md", "---\ndescription: x\n---\nPIEGE\n")
        real = collect.parse_frontmatter

        def exploding(text: str) -> object:
            if "PIEGE" in text:
                raise RuntimeError("contenu : [red]secret[/red]")
            return real(text)

        monkeypatch.setattr(collect, "parse_frontmatter", exploding)
        excluded: list[Exclusion] = []
        assert [s.slug for s in collect_skills(project, excluded=excluded)] == ["sain"]
        assert [e.reason for e in excluded] == ["fichier illisible (RuntimeError)"]

    def test_un_champ_de_frontmatter_inattendu_n_exclut_que_ce_skill(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _put(_skills_dir(project), "sain.md", SAIN)
        _put(_skills_dir(project), "piege.md", "---\ndescription: x\ntools: [read]\n---\nPIEGE\n")
        real = collect._tool_verbs

        def exploding(raw: object) -> object:
            if raw == ["read"]:
                raise TypeError("x")
            return real(raw)

        monkeypatch.setattr(collect, "_tool_verbs", exploding)
        excluded: list[Exclusion] = []
        assert [s.slug for s in collect_skills(project, excluded=excluded)] == ["sain"]
        assert [e.reason for e in excluded] == ["fichier illisible (TypeError)"]

    def test_un_nom_de_fichier_hostile_est_assaini(self, project: Path) -> None:
        _put(_skills_dir(project), "sain.md", SAIN)
        directory = _skills_dir(project)
        (directory / "[red]x\x1b[31m.md").mkdir()
        excluded: list[Exclusion] = []
        collect_skills(project, excluded=excluded)
        (item,) = excluded
        assert "[" not in item.name and "\x1b" not in item.name and "\x1b" not in item.reason


# ── Commandes (fichiers du paquet : une erreur ne vide pas les prompts MCP) ───────────────────────────


class TestCommandes:
    def test_un_fichier_du_paquet_illisible_n_exclut_que_lui(self, project: Path, kit: Path, temoin: Path) -> None:
        commands = kit / "hosts" / "commands"
        _put(commands, "bonne.md", "---\ndescription: Bonne\n---\nCorps\n")
        (commands / "lien.md").symlink_to(temoin)
        (commands / "casse.md").write_bytes(b"---\ndescription: x\n---\n\xff\n")
        (commands / "dossier.md").mkdir()
        os.mkfifo(commands / "tuyau.md")
        prompts = kit / "copilot" / "prompts"
        _put(prompts, "ok.prompt.md", "---\ndescription: Prompt\n---\nCorps du prompt\n")
        (prompts / "mauvais.prompt.md").write_bytes(b"\xff\xfe")
        excluded: list[Exclusion] = []
        start = time.monotonic()
        commands_found = collect_commands(project, excluded=excluded)
        assert time.monotonic() - start < 5
        assert [c.slug for c in commands_found] == ["bonne", "ok"]
        assert sorted(e.name for e in excluded) == [
            "commands/casse.md", "commands/dossier.md", "commands/lien.md", "commands/tuyau.md",
            "prompts/mauvais.prompt.md",
        ]
        assert TEMOIN not in repr(commands_found)

    def test_sans_le_canal_lateral_l_appel_historique_ne_leve_pas(self, project: Path, kit: Path) -> None:
        commands = kit / "hosts" / "commands"
        _put(commands, "bonne.md", "---\ndescription: Bonne\n---\nCorps\n")
        (commands / "casse.md").write_bytes(b"\xff")
        assert [c.slug for c in collect_commands(project)] == ["bonne"]

    def test_sur_un_paquet_sain_rien_n_est_exclu(self, project: Path, kit: Path) -> None:
        _put(kit / "hosts" / "commands", "z.md", "---\ndescription: Z\nargument-hint: <x>\n---\nCorps Z\n")
        _put(kit / "copilot" / "prompts", "a.prompt.md", "---\ndescription: A\n---\nCorps A\n")
        excluded: list[Exclusion] = []
        found = collect_commands(project, excluded=excluded)
        assert excluded == [] and [c.slug for c in found] == ["a", "z"]
        assert found[1].argument_hint == "<x>" and found[0].source == "workflow"

    def test_les_prompts_mcp_survivent_a_un_fichier_illisible(
        self, project: Path, kit: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pytest.importorskip("mcp")
        from grimoire.mcp import server

        _put(kit / "hosts" / "commands", "bonne.md", "---\ndescription: Bonne\n---\nCorps\n")
        (kit / "hosts" / "commands" / "casse.md").write_bytes(b"\xff")
        registered: list[str] = []
        monkeypatch.setattr(server, "_register_command", lambda slug, *a: registered.append(slug))
        prompts, _skills = server._register_surface(project)
        assert prompts == 1 and registered == ["bonne"]


# ── Agents ────────────────────────────────────────────────────────────────────────────────────────────


class TestAgents:
    def test_un_fichier_hostile_n_exclut_que_lui(
        self, project: Path, temoin: Path, hostile: Callable[[Path, Path], str]
    ) -> None:
        directory = _agents_dir(project)
        _put(directory, "sain.md", _agent_text("sain"))
        bad = hostile(directory, temoin)
        excluded: list[Exclusion] = []
        start = time.monotonic()
        agents = collect_agents(project, known_skills=frozenset(), excluded=excluded)
        assert time.monotonic() - start < 5
        assert [a.name for a in agents] == ["sain"]
        assert [e.name for e in excluded] == [f"agents/{bad}.md"]
        assert TEMOIN not in repr(agents)

    def test_sur_un_projet_sain_rien_n_est_exclu(self, project: Path) -> None:
        _put(_agents_dir(project), "a.md", _agent_text("a"))
        _put(project / "_grimoire" / "_config" / "custom" / "agents", "b.md", _agent_text("b", body="Autre rôle."))
        excluded: list[Exclusion] = []
        agents = collect_agents(project, known_skills=frozenset(), excluded=excluded)
        assert excluded == [] and [a.name for a in agents] == ["a", "b"]
        assert agents == collect_agents(project, known_skills=frozenset())

    def test_la_copie_refusee_de_l_override_laisse_servir_celle_du_kit(self, project: Path, temoin: Path) -> None:
        _agents_dir(project).mkdir(parents=True)
        (_agents_dir(project) / "x.md").symlink_to(temoin)
        _put(project / "_grimoire" / "kit" / "agents", "x.md", _agent_text("x", body="Version du kit."))
        excluded: list[Exclusion] = []
        (agent,) = collect_agents(project, known_skills=frozenset(), excluded=excluded)
        assert agent.name == "x" and agent.override_ref is None and "Version du kit." in agent.definition_body
        assert [e.name for e in excluded] == ["agents/x.md"]

    def test_le_kit_refuse_d_un_override_partiel_n_exclut_que_cet_agent(self, project: Path, temoin: Path) -> None:
        _put(_agents_dir(project), "sain.md", _agent_text("sain"))
        _put(_agents_dir(project), "part.md", _agent_text("part", extra="extends: kit\n"))
        kit_agents = project / "_grimoire" / "kit" / "agents"
        kit_agents.mkdir(parents=True)
        (kit_agents / "part.md").symlink_to(temoin)
        excluded: list[Exclusion] = []
        agents = collect_agents(project, known_skills=frozenset(), excluded=excluded)
        assert [a.name for a in agents] == ["sain"]
        assert [e.name for e in excluded] == ["agents/part.md"]
        assert TEMOIN not in repr(agents)

    def test_un_kit_absent_reste_une_erreur_de_build(self, project: Path) -> None:
        from grimoire.core.exceptions import GrimoireAgentError

        _put(_agents_dir(project), "part.md", _agent_text("part", extra="extends: kit\n"))
        with pytest.raises(GrimoireAgentError, match="aucun agent kit"):
            collect_agents(project, known_skills=frozenset())

    def test_resolve_extends_kit_refuse_un_kit_en_lien_avec_une_erreur_dediee(
        self, project: Path, temoin: Path
    ) -> None:
        override = _put(_agents_dir(project), "part.md", _agent_text("part", extra="extends: kit\n"))
        kit_agents = project / "_grimoire" / "kit" / "agents"
        kit_agents.mkdir(parents=True)
        (kit_agents / "part.md").symlink_to(temoin)
        with pytest.raises(collect.UnreadableSourceError):
            resolve_extends_kit(project, override, {"extends": "kit"})

    def test_effective_agent_frontmatter_ne_leve_pas_sur_un_fichier_refuse(self, project: Path) -> None:
        override = _put(_agents_dir(project), "part.md", _agent_text("part", extra="extends: kit\n"))
        _put(project / "_grimoire" / "kit" / "agents", "part.md", _agent_text("part"))
        (agent,) = collect_agents(project, known_skills=frozenset())
        assert effective_agent_frontmatter(project, agent)["name"] == "part"
        override.write_bytes(b"\xff\xfe")
        assert effective_agent_frontmatter(project, agent) == {}

    def test_un_contexte_hors_du_projet_n_est_pas_mesure(self, project: Path, temoin: Path) -> None:
        (project / "docs").mkdir()
        (project / "docs" / "in.md").write_text("x" * 10, encoding="utf-8")
        out = project / "docs" / "out.md"
        out.symlink_to(temoin)
        _put(_agents_dir(project), "c.md", _agent_text("c", extra="context: [docs/in.md, docs/out.md]\n"))
        (agent,) = collect_agents(project, known_skills=frozenset())
        assert dict(agent.context_sizes) == {"docs/in.md": 10, "docs/out.md": 0}

    def test_dix_mille_agents_temps_borne(self, project: Path) -> None:
        directory = _agents_dir(project)
        _put(directory, "a-sain.md", _agent_text("a-sain"))
        for i in range(10_000):
            (directory / f"z{i:05d}.md").write_text("x", encoding="utf-8")
        start = time.monotonic()
        excluded: list[Exclusion] = []
        agents = collect_agents(project, known_skills=frozenset(), excluded=excluded)
        assert time.monotonic() - start < 10
        assert "a-sain" in {a.name for a in agents}
        assert any("trop fourni" in e.reason for e in excluded)


# ── Les appelants : émetteur d'hôte, notes de surface, doctor ────────────────────────────────────────────


@pytest.fixture
def hostile_project(project: Path, kit: Path, temoin: Path) -> Path:
    """Un projet sain + un fichier hostile de chaque sorte, dans skills et agents."""
    _put(kit / "hosts" / "skills", "du-kit.md", SAIN)
    _put(_skills_dir(project), "sain.md", SAIN)
    _put(_agents_dir(project), "sain.md", _agent_text("sain"))
    for builder in (_lien_sortant, _lien_peripherique, _utf8_invalide, _dossier, _plusieurs_mo):
        for directory in (_skills_dir(project), _agents_dir(project)):
            builder(directory, temoin)
            (directory / "mauvais.md").rename(directory / f"{builder.__name__.strip('_')}.md")
    return project


class TestAppelants:
    def test_l_emetteur_de_fichiers_ne_sert_rien_de_hors_du_projet(self, hostile_project: Path) -> None:
        surface = build_surface(hostile_project)
        assert {s.slug for s in surface.skills} == {"du-kit", "sain"}
        assert [a.name for a in surface.agents] == ["sain"]
        emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
        assert emitter is not None
        plan = emitter.plan(surface, hostile_project)
        assert any("sain" in f.relpath.as_posix() for f in plan.files)
        assert all(TEMOIN not in f.content for f in plan.files)

    def test_les_exclusions_sont_dites_dans_les_notes_de_surface(self, hostile_project: Path) -> None:
        notes = build_surface(hostile_project).notes
        assert notes[0].endswith("fichiers ignorés :") and notes[0].startswith("10 ")
        assert all(note.startswith("  ") for note in notes[1:])
        assert len(notes) == 1 + 10
        assert "\x1b" not in "".join(notes) and "[" not in "".join(notes)

    def test_les_notes_sont_bornees_et_comptent_le_reste(self, project: Path) -> None:
        for i in range(25):
            (_skills_dir(project) / f"d{i:02d}.md").mkdir(parents=True)
        notes = build_surface(project).notes
        assert notes[0] == "25 fichiers ignorés :"
        assert len(notes) == 1 + 10 + 1 and notes[-1] == "  … et 15 autres"

    def test_un_projet_sain_n_a_aucune_note_de_collecte(self, project: Path, kit: Path) -> None:
        _put(_skills_dir(project), "sain.md", SAIN)
        _put(_agents_dir(project), "sain.md", _agent_text("sain"))
        assert build_surface(project).notes == ()

    def test_host_sync_ecrit_les_fichiers_sains_et_dit_les_ignores(self, hostile_project: Path) -> None:
        from typer.testing import CliRunner

        from grimoire.cli.app import app

        result = CliRunner().invoke(
            app, ["host", "sync", "--host", "claude-code", "--project-root", str(hostile_project)]
        )
        assert result.exit_code == 0, result.output
        assert "fichiers ignorés" in result.output
        assert TEMOIN not in result.output
        written = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in hostile_project.rglob("*") if p.is_file() and not p.is_symlink())
        assert TEMOIN not in written

    def test_doctor_dit_les_fichiers_ignores_bornes_a_dix_lignes(self, hostile_project: Path) -> None:
        for i in range(30):
            (_skills_dir(hostile_project) / f"extra{i:02d}.md").mkdir()
        (check,) = check_project_sources(hostile_project)
        assert check.level == "warn" and check.passed
        assert check.detail.startswith("40 fichiers ignorés : ") and check.detail.endswith("… et 30 autres")
        assert check.detail.count(" (") == 10
        assert "[" not in check.detail and "\n" not in check.detail

    def test_doctor_est_muet_sur_un_projet_sain(self, project: Path, kit: Path) -> None:
        _put(_skills_dir(project), "sain.md", SAIN)
        assert check_project_sources(project) == []

    def test_doctor_ne_leve_jamais(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: object, **kwargs: object) -> None:
            raise OSError("x")

        monkeypatch.setattr(collect, "collect_skills", boom)
        assert check_project_sources(project) == []

    def test_le_detail_du_catalogue_mcp_est_borne(self, project: Path) -> None:
        for i in range(40):
            (_skills_dir(project) / f"d{i:02d}.md").mkdir(parents=True, exist_ok=True)
        detail = check_mcp_skills(project).detail
        assert detail.endswith("… et 30 autres") and detail.count(" (") == 10 + 1  # + « (extension … ) » de l'en-tête


class TestAppelantsCockpitEtDispatch:
    def test_la_vue_agents_du_cockpit_ne_leve_pas_et_garde_les_sains(self, hostile_project: Path) -> None:
        from grimoire.tools import workspace_api as wa

        payload = wa.agents_view(hostile_project)
        assert [a["name"] for a in payload["agents"]] == ["sain"]
        assert TEMOIN not in repr(payload)

    def test_le_dispatch_lit_le_contexte_de_l_agent_sain(self, project: Path, temoin: Path) -> None:
        from grimoire.missions.dispatch import agent_declared_context

        (project / "docs").mkdir()
        (project / "docs" / "ctx.md").write_text("c", encoding="utf-8")
        _put(_agents_dir(project), "sain.md", _agent_text("sain", extra="context: [docs/ctx.md]\n"))
        (_skills_dir(project)).mkdir(parents=True)
        (_skills_dir(project) / "lien.md").symlink_to(temoin)
        os.mkfifo(_agents_dir(project) / "tuyau.md")
        assert agent_declared_context(project, "sain") == ("docs/ctx.md",)
        assert agent_declared_context(project, "inconnu") == ()

    def test_les_autres_appelants_ne_levent_pas(self, hostile_project: Path) -> None:
        from grimoire.core.agent_freshness import project_agent_freshness
        from grimoire.tools.workspace_routes import _agent_target

        assert _agent_target(hostile_project, "sain").name == "sain.md"
        project_agent_freshness(hostile_project, None)  # lit la même surface : ne lève pas


# ── Le module partagé ─────────────────────────────────────────────────────────────────────────────────


class TestModulePartage:
    def test_exclusion_lines_borne_et_compte(self) -> None:
        items = [(f"n{i}", "r") for i in range(13)]
        assert exclusion_lines(items) == [*(f"n{i} (r)" for i in range(10)), "… et 3 autres"]
        assert exclusion_lines(items[:10]) == [f"n{i} (r)" for i in range(10)]
        assert exclusion_lines([]) == []

    def test_describe_exclusions_compte_une_fois_les_doublons(self) -> None:
        header, lines = describe_exclusions([Exclusion.of("a", "r"), Exclusion.of("a", "r")])
        assert (header, lines) == ("1 fichier ignoré :", ["a (r)"])
        assert describe_exclusions([]) == ("", [])

    def test_lecture_bornee_meme_si_la_taille_annoncee_ment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "gros.md"
        path.write_text("x" * 100, encoding="utf-8")
        real = Path.lstat

        class _Small:
            def __init__(self, real_stat: os.stat_result) -> None:
                self.st_mode = real_stat.st_mode
                self.st_size = 1

        monkeypatch.setattr(Path, "lstat", lambda self: _Small(real(self)) if self.name == "gros.md" else real(self))
        with pytest.raises(safe_sources.Refused, match="au-delà de 10 octets"):
            safe_sources.read_source_text(path, max_bytes=10)

    def test_ouverture_refusee_est_une_raison(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "x.md"
        path.write_text("x", encoding="utf-8")

        def refuse(_path: Path) -> None:
            raise ValueError("non")

        monkeypatch.setattr("grimoire.core.regular_file.open_regular", refuse)
        with pytest.raises(safe_sources.Refused, match="refusé à l'ouverture"):
            safe_sources.read_source_text(path)

    def test_fichier_absent_et_racine(self, tmp_path: Path) -> None:
        with pytest.raises(safe_sources.Refused, match="illisible"):
            safe_sources.read_source_text(tmp_path / "absent.md")
        dossier = tmp_path / "d"
        dossier.mkdir()
        (dossier / "f.md").write_text("ok", encoding="utf-8")
        assert safe_sources.read_source_text(dossier / "f.md", root=tmp_path) == "ok"
        with pytest.raises(safe_sources.Refused, match="hors du projet"):
            safe_sources.read_source_text(dossier / "f.md", root=tmp_path / "autre")

    def test_dossier_hors_racine(self, tmp_path: Path) -> None:
        assert safe_sources.source_dir_issue(tmp_path / "a", tmp_path / "b") == "dossier des sources hors du projet"
        assert safe_sources.source_dir_issue(tmp_path / "absent", tmp_path) is None

    def test_dossier_illisible_est_une_exclusion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(_path: object) -> None:
            raise PermissionError("non")

        monkeypatch.setattr(os, "scandir", refuse)
        found, issues = safe_sources.list_sources(tmp_path, "d/x")
        assert found == {} and [i.reason for i in issues] == ["dossier illisible (PermissionError)"]

    def test_dossier_absent_ou_fichier_n_est_pas_une_exclusion(self, tmp_path: Path) -> None:
        assert safe_sources.list_sources(tmp_path / "absent", "x") == ({}, [])
        (tmp_path / "f").write_text("x", encoding="utf-8")
        assert safe_sources.list_sources(tmp_path / "f", "x") == ({}, [])
