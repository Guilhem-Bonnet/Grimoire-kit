"""Lot D du tri du kanban : missions et CLI (R07, R08, R24, R25, P49, P50, R55).

Chaque commande qui écrit passe par ``TaskService`` ; un refus n'écrit rien.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_mission import mission_app
from grimoire.cli.cmd_plans import plans_app
from grimoire.cli.cmd_task import task_app
from grimoire.missions.ledger import MissionLedger
from grimoire.missions.service import DEFAULT_LEDGER_RELPATH

runner = CliRunner()
JSON = {"output": "json"}
CRITERE = "le endpoint repond 200"


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    return tmp_path


def _ledger(projet: Path) -> MissionLedger:
    return MissionLedger(projet / DEFAULT_LEDGER_RELPATH)


def _events(projet: Path) -> int:
    path = projet / DEFAULT_LEDGER_RELPATH / "events.jsonl"
    return len(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else 0


def task(projet: Path, *args: str, as_json: bool = False):
    return runner.invoke(task_app, [*args, "--project-root", str(projet)], obj=JSON if as_json else None)


def mission(projet: Path, *args: str, as_json: bool = False):
    return runner.invoke(mission_app, [*args, "--project-root", str(projet)], obj=JSON if as_json else None)


# ── P49 + R25 : grimoire mission open ────────────────────────────────────────


def test_mission_open_rend_l_identifiant_et_task_add_l_accepte(projet: Path) -> None:
    res = mission(projet, "open", "Refonte du cache")
    assert res.exit_code == 0, res.output
    mission_id = res.stdout.strip()
    assert mission_id.startswith("MIS-")
    assert [m.id for m in _ledger(projet).list_missions()] == [mission_id]

    added = task(projet, "add", "Brancher le cache", "-a", CRITERE, "--mission", mission_id, as_json=True)
    assert added.exit_code == 0, added.output
    assert json.loads(added.stdout)["mission_id"] == mission_id


def test_mission_open_homonyme_refusee_et_nommee(projet: Path) -> None:
    first = mission(projet, "open", "Refonte du cache").stdout.strip()
    avant = _events(projet)
    res = mission(projet, "open", "  refonte   DU cache ")
    assert res.exit_code == 1
    assert first in res.output and "existe déjà" in res.output
    assert "Traceback" not in res.output and "GrimoireMissionError" not in res.output
    assert _events(projet) == avant
    assert len(_ledger(projet).list_missions()) == 1


def test_mission_open_titre_vide_refuse_sans_ecrire(projet: Path) -> None:
    res = mission(projet, "open", "   ")
    assert res.exit_code == 1
    assert "titre de mission vide" in res.output
    assert _events(projet) == 0


def test_mission_open_ecrit_le_perimetre_et_task_context_le_rend(projet: Path) -> None:
    res = mission(projet, "open", "API v2", "--scope-repo", "api", "--scope-repo", "api", "--scope-repo", " ",
                  "--scope-surface", "http", "--scope-pack", "billing", as_json=True)
    assert res.exit_code == 0, res.output
    opened = json.loads(res.stdout)
    assert opened["scope"] == {"repos": ["api"], "surfaces": ["http"], "packs": ["billing"]}
    assert _ledger(projet).get_mission(opened["id"]).scope_repos == ("api",)

    added = json.loads(task(projet, "add", "Tracer", "-a", CRITERE, "--mission", opened["id"], as_json=True).stdout)
    from grimoire.mcp.server import task_context

    ctx = json.loads(task_context(task_id=added["id"], project_path=str(projet)))
    assert ctx["mission_scope"] == {"repos": ["api"], "surfaces": ["http"], "packs": ["billing"]}


def test_task_add_mission_inconnue_refusee_sans_ecrire(projet: Path) -> None:
    mission(projet, "open", "Une mission")
    avant = _events(projet)
    res = task(projet, "add", "Orpheline", "-a", CRITERE, "--mission", "MIS-nope-001")
    assert res.exit_code == 1
    assert _events(projet) == avant


# ── R24 : finition héritée de la phase, surchargée par tâche ─────────────────


def _show(projet: Path, task_id: str) -> dict:
    return json.loads(task(projet, "show", task_id, as_json=True).stdout)


def test_finition_explicite_puis_show(projet: Path) -> None:
    res = task(projet, "add", "Poncer", "-a", CRITERE, "--finition", "peaufine", as_json=True)
    assert res.exit_code == 0, res.output
    assert _show(projet, json.loads(res.stdout)["id"])["finition"] == "peaufine"


def test_finition_accepte_la_forme_accentuee_et_stocke_la_valeur_machine(projet: Path) -> None:
    res = task(projet, "add", "Poncer", "-a", CRITERE, "--finition", "peaufiné", as_json=True)
    assert json.loads(res.stdout)["finition"] == "peaufine"


@pytest.mark.parametrize(("phase", "attendue"), [("exploration", "maquette"), ("construction", "peaufine"), ("exploitation", "peaufine")])
def test_finition_heritee_de_la_phase_du_projet(projet: Path, phase: str, attendue: str) -> None:
    (projet / "project-context.yaml").write_text(f"project:\n  name: x\nphase: {phase}\n", encoding="utf-8")
    res = task(projet, "add", "Sans option", "-a", CRITERE, as_json=True)
    assert res.exit_code == 0, res.output
    assert _show(projet, json.loads(res.stdout)["id"]).get("finition") == attendue


def test_finition_explicite_l_emporte_sur_la_phase(projet: Path) -> None:
    (projet / "project-context.yaml").write_text("phase: exploitation\n", encoding="utf-8")
    res = task(projet, "add", "Jetable", "-a", CRITERE, "--finition", "maquette", as_json=True)
    assert json.loads(res.stdout)["finition"] == "maquette"


def test_finition_vide_sans_phase_declaree_ou_inconnue(projet: Path) -> None:
    assert "finition" not in json.loads(task(projet, "add", "A", "-a", CRITERE, as_json=True).stdout)
    (projet / "project-context.yaml").write_text("phase: nimporte\n", encoding="utf-8")
    assert "finition" not in json.loads(task(projet, "add", "B", "-a", CRITERE, as_json=True).stdout)


def test_finition_inconnue_refusee_sans_ecrire(projet: Path) -> None:
    res = task(projet, "add", "X", "-a", CRITERE, "--finition", "brillante")
    assert res.exit_code == 1
    assert "finition inconnue" in res.output and "ValueError" not in res.output
    assert _events(projet) == 0


def test_finition_mcp(projet: Path) -> None:
    from grimoire.mcp.server import task_add

    (projet / "project-context.yaml").write_text("project:\n  phase: exploration\n", encoding="utf-8")
    heritee = json.loads(task_add(title="MCP un", acceptance=[CRITERE], project_path=str(projet)))
    assert heritee["finition"] == "maquette"
    surchargee = json.loads(task_add(title="MCP deux", acceptance=[CRITERE], finition="peaufine", project_path=str(projet)))
    assert surchargee["finition"] == "peaufine"


# ── R07 : task add --intake ──────────────────────────────────────────────────


def test_intake_propose_type_risque_taches_sans_rien_ecrire(projet: Path) -> None:
    res = task(projet, "add", "--intake", "Implement the recipe schema and delete the old one")
    assert res.exit_code == 0, res.output
    proposal = json.loads(res.stdout)
    assert proposal["mission_type"] == "migration" or proposal["mission_type"]
    assert proposal["risk_profile"] in {"light", "standard", "strict", "security_critical"}
    assert proposal["task_proposals"] and {"title", "task_type", "risk_profile"} <= set(proposal["task_proposals"][0])
    assert proposal["written"] is False
    assert _events(projet) == 0


def test_intake_refuse_le_melange_avec_une_creation(projet: Path) -> None:
    res = task(projet, "add", "Un titre", "-a", CRITERE, "--intake", "texte")
    assert res.exit_code == 2
    assert _events(projet) == 0
    assert task(projet, "add", "--intake", "  ").exit_code == 2


def test_task_add_sans_titre_ou_sans_critere_reste_refuse(projet: Path) -> None:
    assert task(projet, "add").exit_code == 2
    assert task(projet, "add", "Titre seul").exit_code == 2
    assert _events(projet) == 0


# ── P50 : task import ────────────────────────────────────────────────────────


def _plan(projet: Path, tasks: list[dict], **top: object) -> Path:
    path = projet / "plan.json"
    path.write_text(json.dumps({"plan": "p1", "tasks": tasks, **top}), encoding="utf-8")
    return path


PLAN = [
    {"key": "a", "title": "Écrire le schéma", "acceptance": ["le schéma valide"], "evidence": ["test"], "priority": "high"},
    {"key": "b", "title": "Brancher l'API", "acceptance": ["l'API répond"], "depends_on": ["a"]},
]


def test_import_cree_taches_dependances_et_rend_la_table(projet: Path) -> None:
    res = task(projet, "import", str(_plan(projet, PLAN)), as_json=True)
    assert res.exit_code == 0, res.output
    rapport = json.loads(res.stdout)
    ids = {row["key"]: row["task_id"] for row in rapport["tasks"]}
    assert set(ids) == {"a", "b"} and all(i.startswith("GAO-") for i in ids.values())
    ledger = _ledger(projet)
    a, b = ledger.get_task(ids["a"]), ledger.get_task(ids["b"])
    assert a.title == "Écrire le schéma" and a.acceptance == ("le schéma valide",) and a.expected_evidence == ("test",)
    assert a.priority == "high"
    assert [d.target for d in b.dependencies] == [ids["a"]]


def test_import_relance_ne_cree_aucun_doublon(projet: Path) -> None:
    plan = _plan(projet, PLAN)
    task(projet, "import", str(plan))
    avant = _events(projet)
    n = len(_ledger(projet).list_tasks())
    res = task(projet, "import", str(plan), as_json=True)
    assert res.exit_code == 0, res.output
    assert {r["action"] for r in json.loads(res.stdout)["tasks"]} == {"already_imported"}
    assert len(_ledger(projet).list_tasks()) == n == 2
    assert _events(projet) == avant


def test_import_plan_modifie_est_refuse_sans_ecraser_ni_creer(projet: Path) -> None:
    task(projet, "import", str(_plan(projet, PLAN)))
    avant = _events(projet)
    modifie = [{**PLAN[0], "title": "Autre titre"}, PLAN[1], {"key": "c", "title": "Nouvelle", "acceptance": ["ok"]}]
    res = task(projet, "import", str(_plan(projet, modifie)))
    assert res.exit_code == 1 and "autre titre" in res.output and "rien n'est modifié" in res.output
    assert _events(projet) == avant, "le refus n'écrit rien, pas même la tâche nouvelle"


def test_import_deux_plans_distincts_au_meme_nom_de_fichier_ne_se_confondent_pas(projet: Path) -> None:
    (projet / "x").mkdir()
    (projet / "y").mkdir()
    un = projet / "x" / "plan.json"
    deux = projet / "y" / "plan.json"
    un.write_text(json.dumps({"plan": "un", "tasks": [PLAN[0]]}), encoding="utf-8")
    deux.write_text(json.dumps({"plan": "deux", "tasks": [{**PLAN[0], "title": "Tout autre"}]}), encoding="utf-8")
    assert task(projet, "import", str(un)).exit_code == 0
    assert task(projet, "import", str(deux)).exit_code == 0
    assert len(_ledger(projet).list_tasks()) == 2


def test_import_plan_sans_identite_refuse(projet: Path) -> None:
    path = projet / "p.json"
    path.write_text(json.dumps({"tasks": [PLAN[0]]}), encoding="utf-8")
    res = task(projet, "import", str(path))
    assert res.exit_code == 1 and "« plan » manquant" in res.output
    assert _events(projet) == 0


@pytest.mark.parametrize("cle", ["a\tb", "a\nb", "a b", "a\x00b", "x" * 101])
def test_import_cle_avec_blanc_ou_controle_refusee(projet: Path, cle: str) -> None:
    res = task(projet, "import", str(_plan(projet, [{"key": cle, "title": "T", "acceptance": ["c"]}])))
    assert res.exit_code == 1 and "invalide" in res.output
    assert _events(projet) == 0


def test_import_plafonds_taille_et_nombre_de_taches(projet: Path) -> None:
    trop = [{"key": f"k{i}", "title": "T", "acceptance": ["c"]} for i in range(201)]
    res = task(projet, "import", str(_plan(projet, trop)))
    assert res.exit_code == 1 and "trop de tâches" in res.output
    gros = projet / "gros.json"
    gros.write_text(json.dumps({"plan": "g", "tasks": [PLAN[0]], "pad": "x" * 1_100_000}), encoding="utf-8")
    res = task(projet, "import", str(gros))
    assert res.exit_code == 1 and "trop gros" in res.output
    assert _events(projet) == 0


def _import_worker(args: tuple[str, str]) -> int:
    from grimoire.cli.cmd_task import task_app as app

    root, plan = args
    return CliRunner().invoke(app, ["import", plan, "--project-root", root]).exit_code


def _mission_worker(args: tuple[str, int]) -> int:
    root, _ = args
    return CliRunner().invoke(mission_app, ["open", "Race", "--project-root", root]).exit_code


def test_import_concurrent_ne_cree_qu_un_jeu_de_taches(projet: Path) -> None:
    import multiprocessing

    plan = str(_plan(projet, PLAN))
    with multiprocessing.get_context("spawn").Pool(4) as pool:
        codes = pool.map(_import_worker, [(str(projet), plan)] * 4)
    assert set(codes) == {0}
    assert sorted(t.title for t in _ledger(projet).list_tasks()) == ["Brancher l'API", "Écrire le schéma"]


def test_mission_open_concurrent_une_seule_mission(projet: Path) -> None:
    import multiprocessing

    with multiprocessing.get_context("spawn").Pool(4) as pool:
        codes = pool.map(_mission_worker, [(str(projet), i) for i in range(4)])
    assert sorted(codes) == [0, 1, 1, 1]
    assert [m.title for m in _ledger(projet).list_missions()] == ["Race"]


def test_finition_project_context_non_utf8_ne_plante_pas(projet: Path) -> None:
    (projet / "project-context.yaml").write_bytes(b"\xff\xfe phase: exploration\n")
    res = task(projet, "add", "T", "-a", CRITERE, as_json=True)
    assert res.exit_code == 0, res.output
    assert "finition" not in json.loads(res.stdout)


def test_finition_phase_nulle_ne_masque_pas_project_phase(projet: Path) -> None:
    (projet / "project-context.yaml").write_text("phase:\nproject:\n  phase: exploration\n", encoding="utf-8")
    assert json.loads(task(projet, "add", "T", "-a", CRITERE, as_json=True).stdout)["finition"] == "maquette"


def test_import_dry_run_n_ecrit_rien(projet: Path) -> None:
    res = task(projet, "import", str(_plan(projet, PLAN)), "--dry-run", as_json=True)
    assert res.exit_code == 0, res.output
    rapport = json.loads(res.stdout)
    assert rapport["dry_run"] is True and {r["action"] for r in rapport["tasks"]} == {"would_create"}
    assert _events(projet) == 0
    assert not [f for f in projet.rglob('*') if f.is_file() and f.name != 'plan.json']


def test_import_saute_les_entrees_existing_et_lie_vers_elles(projet: Path) -> None:
    deja = json.loads(task(projet, "add", "Déjà là", "-a", CRITERE, as_json=True).stdout)["id"]
    plan = _plan(projet, [{"key": "x", "existing": deja}, {"key": "y", "title": "Suite", "acceptance": ["ok"], "depends_on": ["x"]}])
    res = task(projet, "import", str(plan), as_json=True)
    assert res.exit_code == 0, res.output
    rows = {r["key"]: r for r in json.loads(res.stdout)["tasks"]}
    assert rows["x"] == {"key": "x", "task_id": deja, "action": "skipped_existing", "note": "déjà au ledger, non modifiée"}
    assert [d.target for d in _ledger(projet).get_task(rows["y"]["task_id"]).dependencies] == [deja]
    assert _ledger(projet).get_task(deja).title == "Déjà là"


@pytest.mark.parametrize(
    ("tasks", "extrait"),
    [
        ([{"key": "a", "title": "T", "acceptance": []}], "au moins un critère"),
        ([{"key": "a", "title": "T", "acceptance": ["c"]}, {"key": "a", "title": "U", "acceptance": ["c"]}], "clé en double"),
        ([{"key": "a", "title": "T", "acceptance": ["c"], "depends_on": ["zz"]}], "absente du plan"),
        ([{"key": "a", "title": "T", "acceptance": ["c"], "depends_on": ["b"]},
          {"key": "b", "title": "U", "acceptance": ["c"], "depends_on": ["a"]}], "cycle de dépendances"),
        ([{"key": "a", "title": "T", "acceptance": ["c"], "priorite": "high"}], "champ inconnu"),
        ([{"key": "a", "title": "T", "acceptance": ["c"], "priority": "urgentissime"}], "priorité"),
        ([{"key": "a", "existing": "GAO-nope-001"}], "n'existe pas dans le ledger"),
    ],
)
def test_import_plan_invalide_refuse_tout_sans_ecrire(projet: Path, tasks: list[dict], extrait: str) -> None:
    valide = {"key": "ok", "title": "Valide", "acceptance": ["c"]}
    res = task(projet, "import", str(_plan(projet, [valide, *tasks])))
    assert res.exit_code == 1
    assert extrait in res.output
    assert "Traceback" not in res.output and "PlanError" not in res.output
    assert _events(projet) == 0, "aucune tâche valide ne doit être écrite quand une autre est refusée"


def test_import_fichier_illisible_ou_json_casse(projet: Path) -> None:
    assert task(projet, "import", str(projet / "absent.json")).exit_code == 1
    (projet / "casse.json").write_text("{pas du json", encoding="utf-8")
    res = task(projet, "import", str(projet / "casse.json"))
    assert res.exit_code == 1 and "JSON invalide" in res.output
    assert _events(projet) == 0


def test_import_mission_inconnue_refusee_sans_ecrire(projet: Path) -> None:
    res = task(projet, "import", str(_plan(projet, PLAN)), "--mission", "MIS-nope-001")
    assert res.exit_code == 1 and "mission inconnue" in res.output
    assert _events(projet) == 0


def test_import_dans_une_mission_ouverte(projet: Path) -> None:
    mission_id = mission(projet, "open", "Chantier").stdout.strip()
    res = task(projet, "import", str(_plan(projet, PLAN)), "--mission", mission_id, as_json=True)
    assert res.exit_code == 0, res.output
    assert {t.mission_id for t in _ledger(projet).list_tasks()} == {mission_id}


# ── R08 : grimoire plans list ────────────────────────────────────────────────

REGISTRE = """\
schema_version: grimoire.plans-registry.v1
target_plan: PLAN-A
unified_backlog: backlog.md
plans:
  - {id: PLAN-A, path: a.md, title: Plan actif, status: active}
  - {id: PLAN-B, path: b.md, title: Plan absorbé, status: absorbed, absorbed_by: PLAN-A}
"""


def _registre(projet: Path) -> Path:
    path = projet / "_grimoire-runtime-output/planning-artifacts/deprecated-plans-registry.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(REGISTRE, encoding="utf-8")
    return path


def test_plans_list_affiche_les_plans_du_registre(projet: Path) -> None:
    _registre(projet)
    res = runner.invoke(plans_app, ["list", "--project-root", str(projet)])
    assert res.exit_code == 0, res.output
    assert "PLAN-A\tactive\tPlan actif" in res.stdout
    assert "PLAN-B\tabsorbed\tPlan absorbé\t-> PLAN-A" in res.stdout
    filtre = runner.invoke(plans_app, ["list", "--status", "active", "--project-root", str(projet)])
    assert "PLAN-B" not in filtre.stdout and "PLAN-A" in filtre.stdout
    as_json = runner.invoke(plans_app, ["list", "--project-root", str(projet)], obj=JSON)
    assert [p["id"] for p in json.loads(as_json.stdout)["plans"]] == ["PLAN-A", "PLAN-B"]


def test_plans_list_registre_absent_ou_casse_ou_statut_inconnu(projet: Path) -> None:
    res = runner.invoke(plans_app, ["list", "--project-root", str(projet)])
    assert res.exit_code == 1 and "introuvable" in res.output
    path = _registre(projet)
    assert runner.invoke(plans_app, ["list", "--status", "zzz", "--project-root", str(projet)]).exit_code == 2
    path.write_text("- une liste\n", encoding="utf-8")
    cassé = runner.invoke(plans_app, ["list", "--project-root", str(projet)])
    assert cassé.exit_code == 1 and "illisible" in cassé.output and "Traceback" not in cassé.output


def test_plans_list_check_signale_un_chemin_absent(projet: Path) -> None:
    _registre(projet)
    res = runner.invoke(plans_app, ["list", "--check", "--project-root", str(projet)])
    assert res.exit_code == 1 and "path not found" in res.output


# ── R55 : init sans terminal ─────────────────────────────────────────────────


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *flags: str):
    from grimoire.cli.app import app

    monkeypatch.setenv("GRIMOIRE_NO_COCKPIT", "1")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    return CliRunner().invoke(app, [*flags, "init", str(tmp_path), "--backend", "lexical"])


def test_init_sans_terminal_annonce_l_equivalence_avec_y(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    res = _init(tmp_path, monkeypatch)
    assert res.exit_code == 0, res.output
    assert "mode non interactif, équivalent à -y" in res.output


def test_init_y_explicite_n_affiche_pas_l_avertissement_et_ne_retombe_pas_sur_minimal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    res = _init(tmp_path, monkeypatch, "-y")
    assert res.exit_code == 0, res.output
    assert "mode non interactif" not in res.output
    text = (tmp_path / "project-context.yaml").read_text(encoding="utf-8")
    assert 'archetype: "stack"' in text and 'archetype: "minimal"' not in text
