"""P44 — l'aperçu de mise à jour en données : tableau français, changements par catégorie, diff par fichier.

Lecture pure : aucun de ces tests ne lance une vraie mise à jour. Les
changements sont calculés sur un projet jetable, en comparant le plan de
``up`` au disque.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.tools import upgrade_changes
from grimoire.tools.upgrade_preview import parse_preview, translate_detail
from grimoire.tools.workspace_routes import GET_ROUTES, PREFIX

#: La sortie réelle de `grimoire up --dry-run` (Rich, sans couleur) puis de
#: `grimoire host sync --dry-run`, telle que `preview.md` l'embarque.
PREVIEW = """# Aperçu de mise à niveau

## `grimoire up --dry-run`

```
                             grimoire up — summary
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  Step              ┃  Status   ┃  Detail                                     ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│  init              │  skipped  │  project already initialized                │
│                    │           │  (project-context.yaml present)             │
│  refresh           │  planned  │  would refresh the kit tier                 │
│  standard          │  planned  │  standard init (profile: starter)           │
│  host_sync         │  planned  │  would sync host surfaces (.claude/,        │
│                    │           │  .cursor/, .codex/, .gemini/)               │
│  doctor            │  failed   │  quelque chose d'inattendu                  │
└────────────────────┴───────────┴─────────────────────────────────────────────┘

Project: x
dry-run: nothing was written.
```

## `grimoire host sync --dry-run`

```
Claude Code
  Écrirait 3 · inchangé 17
Cursor
  Écrirait 0 · inchangé 4
```
"""

BOX_CHARS = "│─┌└├┐┘┤┬┴┼┃━┏┓┗┛┡┩╇"


def test_le_tableau_de_up_devient_des_lignes_francaises_sans_caractere_de_boite() -> None:
    table = parse_preview(PREVIEW)
    assert table is not None
    steps = {s["id"]: s for s in table["steps"]}
    assert steps["refresh"]["statusLabel"] == "prévue"
    assert steps["refresh"]["detail"] == "rafraîchirait l'étage kit"
    assert steps["refresh"]["label"] == "Rafraîchissement du kit"
    # Une cellule repliée sur deux lignes est rejointe, pas coupée.
    assert steps["init"]["detail"] == "projet déjà initialisé (project-context.yaml présent)"
    assert steps["host_sync"]["detail"].startswith("synchroniserait les surfaces d'hôtes (.claude/,")
    assert ".gemini/" in steps["host_sync"]["detail"]
    assert steps["standard"]["detail"] == "initialisation du standard (profil : starter)"
    # Un détail inconnu reste tel quel : on ne reformule pas ce qu'on ne reconnaît pas.
    assert steps["doctor"]["detail"] == "quelque chose d'inattendu"
    assert steps["doctor"]["tone"] == "bad"
    blob = repr(table)
    assert not [c for c in BOX_CHARS if c in blob]
    assert "Project:" not in blob


def test_les_surfaces_d_hotes_sont_lues() -> None:
    table = parse_preview(PREVIEW)
    assert table is not None
    assert table["hosts"] == [
        {"host": "Claude Code", "wouldWrite": 3, "unchanged": 17},
        {"host": "Cursor", "wouldWrite": 0, "unchanged": 4},
    ]


@pytest.mark.parametrize("text", [None, "", "aperçu de test", "# Rapport\n\n```\nrien\n```\n"])
def test_un_rapport_sans_tableau_rend_none_pour_retomber_sur_le_markdown(text: str | None) -> None:
    assert parse_preview(text) is None


def test_translate_detail_connu_et_inconnu() -> None:
    assert translate_detail("no override needs review") == "aucun override à revoir"
    assert translate_detail("need 'x' not selected") == "besoin « x » non sélectionné"
    assert translate_detail("autre chose") == "autre chose"


# ── Changements et diff : lecture du plan contre le disque ──────────────────


@pytest.fixture(scope="module")
def projet(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("p44") / "projet"
    root.mkdir()
    done = subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "p44-fixture"],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False, timeout=180,
    )
    if not (root / "_grimoire" / "kit").is_dir():
        pytest.skip(f"`grimoire init` indisponible : {done.stderr[-300:]}")
    return root


def _agent_file(root: Path) -> Path:
    files = sorted((root / "_grimoire" / "kit").rglob("agents/*.md"))
    assert files, "le projet jetable doit porter au moins un agent du kit"
    return files[0]


#: Défaut du kit constaté à la livraison de P44 (hors portée) : le plan de `up`
#: réécrit toujours `agent-manifest.csv` avec une ligne `stack-engineer` en
#: double, et un vrai `up` ne le résorbe pas. La lecture le montre fidèlement ;
#: ce test n'exige donc « rien d'autre » que ce seul fichier.
_MANIFESTE = "_grimoire/kit/agent-manifest.csv"


def test_un_projet_fraichement_initialise_n_a_pas_d_autre_changement(projet: Path) -> None:
    view = upgrade_changes.changes_view(projet)
    paths = [f["path"] for c in view["categories"] for f in c["files"]]
    assert set(paths) <= {_MANIFESTE}, paths
    assert view["unchanged"] > 0


def test_un_fichier_du_kit_modifie_est_liste_dans_sa_categorie_avec_son_diff(projet: Path) -> None:
    target = _agent_file(projet)
    original = target.read_text(encoding="utf-8")
    rel = target.relative_to(projet).as_posix()
    target.write_text(original + "\nligne ajoutée à la main\n", encoding="utf-8")
    try:
        view = upgrade_changes.changes_view(projet)
        categories = {c["id"]: c for c in view["categories"]}
        assert "agents" in categories
        entry = next(f for f in categories["agents"]["files"] if f["path"] == rel)
        assert entry["status"] == "modifie"
        assert entry["label"] == "modifié"
        assert entry["removed"] >= 1  # la ligne ajoutée à la main serait retirée

        diff = upgrade_changes.diff_view(projet, rel)
        assert diff["path"] == rel
        assert "-ligne ajoutée à la main" in diff["unified"]
        assert diff["truncated"] is False
        # Rien n'a été écrit par la lecture.
        assert target.read_text(encoding="utf-8").endswith("ligne ajoutée à la main\n")
    finally:
        target.write_text(original, encoding="utf-8")


def test_un_fichier_du_kit_absent_est_nouveau(projet: Path) -> None:
    target = _agent_file(projet)
    original = target.read_text(encoding="utf-8")
    rel = target.relative_to(projet).as_posix()
    target.unlink()
    try:
        files = [f for c in upgrade_changes.changes_view(projet)["categories"] for f in c["files"]]
        entry = next(f for f in files if f["path"] == rel)
        assert entry["status"] == "nouveau"
        assert not target.exists(), "la lecture ne recrée jamais le fichier"
    finally:
        target.write_text(original, encoding="utf-8")


def test_le_diff_refuse_un_chemin_que_la_mise_a_jour_ne_toucherait_pas(projet: Path) -> None:
    with pytest.raises(ValueError, match="ne toucherait pas"):
        upgrade_changes.diff_view(projet, "project-context.yaml")
    with pytest.raises(ValueError, match="ne toucherait pas"):
        upgrade_changes.diff_view(projet, "../../etc/passwd")
    with pytest.raises(ValueError, match="manquant"):
        upgrade_changes.diff_view(projet, None)


def test_les_routes_sont_des_lectures_get_seulement() -> None:
    assert f"{PREFIX}upgrade/changes" in GET_ROUTES
    assert f"{PREFIX}upgrade/diff" in GET_ROUTES
