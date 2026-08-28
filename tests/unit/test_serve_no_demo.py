"""``grimoire serve`` ne sert jamais l'instantané de démonstration de la vitrine.

Le site embarqué dans la wheel contient la couche de la vitrine publique : des
projets inventés (« Atlas Ops », « Sentinel Sec », « Ledger Data ») et les
chiffres du dépôt du kit. Servi tel quel en local, il affichait la mémoire et
les traces d'un autre projet sous le nom de celui qu'on venait d'ouvrir.

Ces tests tiennent les deux bouts : le contenu piégé existe bien dans le site
embarqué (sinon le garde ne garderait rien), et il ne franchit ni le serveur ni
le générateur.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest

from grimoire.tools import serve_data
from grimoire.tools.forge_server import ForgeAPI

ROOT = Path(__file__).resolve().parents[2]
BUNDLED_DATA = ROOT / "web" / "data"


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    """Le générateur chargé comme module (c'est un script, pas un paquet)."""
    spec = importlib.util.spec_from_file_location("gsd", ROOT / "scripts" / "gen-site-data.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def project(tmp_path: Path) -> Path:
    proot = tmp_path / "un-projet"
    (proot / "_grimoire").mkdir(parents=True)
    return proot


# ── La prémisse : le site embarqué contient bien des données étrangères ──────


def test_the_bundled_site_really_carries_a_foreign_snapshot() -> None:
    """Sans ce piège, les gardes ci-dessous ne prouveraient rien.

    Si la couche vitrine disparaît un jour du site embarqué, ce test tombe et
    dit pourquoi — plutôt que de laisser les autres tests passer au vert sans
    rien avoir vérifié.
    """
    index = json.loads((BUNDLED_DATA / "projects.json").read_text(encoding="utf-8"))
    names = {p.get("name") for p in index["projects"]}
    assert len(names) > 1, "l'index embarqué devrait décrire la galerie de la vitrine"
    assert any(p.get("is_demo") for p in index["projects"])

    memory = json.loads((BUNDLED_DATA / "memory.json").read_text(encoding="utf-8"))
    assert memory["store"]["total_entries"] > 0, "instantané mémoire d'un autre projet attendu"


# ── Le serveur : couches projet générées, références du kit embarquées ───────


#: Couches projet réellement piégées dans le site embarqué. La prémisse porte
#: sur celles-là ; une couche plus récente que l'instantané vitrine n'y a pas
#: d'équivalent, et exiger qu'elle en ait un ferait échouer le test pour la
#: seule raison qu'on a ajouté une couche.
BUNDLED_PROJECT_LAYERS = (
    "meta.json", "taskboard.json", "observatory.json",
    "activity.json", "insights.json", "memory.json", "projects.json",
)


def test_project_layers_never_come_from_the_bundled_site(project: Path) -> None:
    """Un projet neuf n'emprunte rien à la vitrine, même si le fichier existe."""
    api = ForgeAPI(project, ROOT, ROOT / "web")
    for layer in BUNDLED_PROJECT_LAYERS:
        assert (BUNDLED_DATA / layer).is_file(), f"{layer} devrait exister dans le site embarqué"
    for layer in sorted(serve_data.PROJECT_LAYERS | {"projects.json"}):
        assert api.data_file(layer) is None, f"{layer} a été servi depuis la vitrine"
    assert api.data_file("projects/grimoire-kit/memory.json") is None


def test_kit_reference_layers_are_still_served(project: Path) -> None:
    """Le catalogue de patterns et le marketplace sont les mêmes partout."""
    api = ForgeAPI(project, ROOT, ROOT / "web")
    for layer in ("catalogue-export.json", "extensions.json", "architecture.json"):
        resolved = api.data_file(layer)
        assert resolved is not None, f"{layer} devrait rester servi"
        assert resolved.is_relative_to(BUNDLED_DATA)


def test_generated_layer_wins_for_the_served_project(project: Path) -> None:
    out = serve_data.data_dir(project)
    out.mkdir(parents=True, exist_ok=True)
    (out / "memory.json").write_text('{"store": {"total_entries": 0}}', encoding="utf-8")

    api = ForgeAPI(project, ROOT, ROOT / "web")
    resolved = api.data_file("memory.json")
    assert resolved is not None
    assert resolved.is_relative_to(out)
    assert json.loads(resolved.read_text(encoding="utf-8"))["store"]["total_entries"] == 0


def test_data_file_refuses_traversal(project: Path) -> None:
    """Le chemin vient d'une URL : il ne doit jamais sortir de sa base."""
    api = ForgeAPI(project, ROOT, ROOT / "web")
    for bad in ("../../etc/passwd", "projects/../../pyproject.toml", "", "/etc/passwd"):
        assert api.data_file(bad) is None


def test_status_declares_a_local_non_demo_host(project: Path) -> None:
    """L'UI partage ses pages avec la vitrine : le serveur doit se nommer."""
    status = ForgeAPI(project, ROOT, ROOT / "web").status()
    assert status["env"] == "local"
    assert status["demo"] is False


def test_serve_layers_match_the_generator() -> None:
    """Une dérive ici rouvre la porte : une couche oubliée retombe sur la vitrine."""
    src = (ROOT / "scripts" / "gen-site-data.py").read_text(encoding="utf-8")
    body = src.split("def build_project(")[1].split("\ndef ")[0]
    generated = set(re.findall(r'_write\(out_dir,\s*"([^"]+\.json)"', body))
    assert set(serve_data.PROJECT_LAYERS) == generated


# ── Le générateur : le remplissage de démonstration est opt-in ───────────────


def test_observatory_without_demo_stays_empty(generator: ModuleType, project: Path) -> None:
    """Des traces datées d'il y a deux minutes sur un projet qui n'a rien lancé."""
    obs = generator.build_observatory(project)
    assert obs["is_demo"] is False
    assert obs["traces"] == []
    assert obs["spans"] == []
    assert obs["agents"] == []


def test_observatory_demo_is_opt_in(generator: ModuleType, project: Path) -> None:
    """La vitrine, elle, a le droit de montrer un runtime représentatif."""
    obs = generator.build_observatory(project, demo=True)
    assert obs["is_demo"] is True
    assert obs["traces"], "la vitrine doit garder son instantané"


def test_taskboard_template_fallback_is_opt_in(generator: ModuleType, project: Path) -> None:
    """Un projet sans board n'hérite pas des dix cartes du template."""
    template = project / "framework" / "agentic-standard" / "templates"
    template.mkdir(parents=True)
    (template / "task-board.yaml").write_text("states: []\ntasks: []\n", encoding="utf-8")

    assert generator.build_taskboard(project) is None

    demo = generator.build_taskboard(project, demo=True)
    assert demo is not None
    assert demo["is_demo"] is True
    assert len(demo["tasks"]) == len(generator._DEMO_TASKS)


def test_taskboard_reads_the_real_board_whatever_the_flag(
    generator: ModuleType, project: Path
) -> None:
    board = project / "_grimoire" / "standard"
    board.mkdir(parents=True)
    (board / "task-board.yaml").write_text(
        "states: [proposed]\ntasks:\n  - task_id: vrai-sujet\n    status: proposed\n",
        encoding="utf-8",
    )
    for demo in (False, True):
        data = generator.build_taskboard(project, demo=demo)
        assert data is not None
        assert data["is_demo"] is False
        assert [t["task_id"] for t in data["tasks"]] == ["vrai-sujet"]


def test_vector_projection_is_opt_in(generator: ModuleType, project: Path) -> None:
    """Un nuage d'embeddings tiré au sort se lit exactement comme un vrai."""
    assert generator.build_memory(project)["vector_projection"] is None
    assert generator.build_memory(project, demo=True)["vector_projection"]["is_demo"] is True


# ── Le portefeuille n'invente pas sa flotte ─────────────────────────────────

DEMO_PROJECT_NAMES = ("Atlas Ops", "Sentinel Sec", "Ledger Data", "Grimoire Core")


def test_the_portfolio_has_no_hardcoded_project_roster() -> None:
    """``portfolio.html`` est la page d'accueil du cockpit local.

    Elle portait un repli codé en dur de quatre projets inventés, affiché dès
    que ``data/projects.json`` manquait — c'est-à-dire précisément le cas d'un
    cockpit dont le registre est vide. Un portefeuille vide se dit ; il ne
    s'invente pas.
    """
    page = (ROOT / "web" / "portfolio.html").read_text(encoding="utf-8")
    for name in DEMO_PROJECT_NAMES:
        assert name not in page, f"{name} codé en dur dans le portefeuille"
    assert "FALLBACK" not in page
