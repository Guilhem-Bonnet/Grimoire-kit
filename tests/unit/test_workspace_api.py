"""Ce que la vue de travail lit, prouvé sur un projet réellement initialisé.

Chaque test décrit un défaut qu'il empêche, pas un comportement qu'il constate :
retirer le correctif fait échouer le test. Les données viennent d'un projet créé
par ``grimoire init`` puis ``grimoire standard init --profile governed`` — un
projet fabriqué à la main aurait des étages vides et des empreintes inconnues du
catalogue, donc ne prouverait rien.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.tools import workspace_api as wa
from grimoire.tools import workspace_exec as we

# ── Glossaire ───────────────────────────────────────────────────────────────


def test_le_glossaire_est_servi_depuis_le_kit_avec_ses_entrees(real_project: Path) -> None:
    """Sans source unique, chaque infobulle réinventerait sa définition."""
    payload = wa.glossary_view(real_project)

    assert payload["count"] >= 15, "la spec cite au moins quinze concepts"
    assert payload["source"] is not None
    entry = next(e for e in payload["entries"] if e["id"] == "porte-de-preuve")
    assert entry["definition"], "une entrée sans définition ne peut pas remplir une bulle"
    assert "evidence-pack" in entry["termes"], "les termes liés ouvrent les bulles enfants"


def test_un_override_de_projet_masque_le_glossaire_du_kit(
    real_project: Path, tmp_path: Path
) -> None:
    """Le glossaire est un fichier du kit : il se surcharge comme les autres.

    Sans passer par ``layout.resolve``, un projet qui adapte son vocabulaire
    verrait quand même celui du kit — et sa documentation dériverait de l'un
    pendant que son interface citerait l'autre.
    """
    override = tmp_path / "_grimoire" / "overrides" / "framework"
    override.mkdir(parents=True)
    (tmp_path / "_grimoire" / "kit").mkdir(parents=True, exist_ok=True)
    (override / "glossary.yaml").write_text(
        "schema: grimoire-glossary/v1\n"
        "entries:\n"
        "  - id: local\n"
        "    nom: Local\n"
        "    définition: Concept propre à ce projet.\n"
        "    raccourci: ''\n"
        "    termes: []\n"
        "    doc: ''\n",
        encoding="utf-8",
    )

    payload = wa.glossary_view(tmp_path)

    assert [e["id"] for e in payload["entries"]] == ["local"]


def test_un_projet_sans_glossaire_se_tait_au_lieu_d_inventer(tmp_path: Path, monkeypatch) -> None:
    """Une bulle sans définition doit être vide, jamais remplie d'à-peu-près."""
    monkeypatch.setattr(wa, "_kit_glossary_path", lambda: None)

    payload = wa.glossary_view(tmp_path)

    assert payload["entries"] == []
    assert payload["source"] is None


# ── Tâches ──────────────────────────────────────────────────────────────────


def test_un_projet_sans_ledger_le_dit_au_lieu_de_rendre_un_board_vide(tmp_path: Path) -> None:
    """Un board vide et une panne se ressemblent : seule la note les distingue."""
    payload = wa.tasks_view(tmp_path)

    assert payload["ledger"] is False
    assert payload["tasks"] == []
    assert "task add" in payload["note"]
    assert len(payload["columns"]) == 8, "les huit colonnes sont annoncées même sans tâche"


def test_une_tache_reelle_porte_sa_colonne_et_sa_prochaine_porte(
    project_with_task: tuple[Path, str],
) -> None:
    """La transition suivante est l'information actionnable (revue §4.3).

    Si ``next_moves_require`` disparaît, l'interface ne peut plus dire ce que la
    porte exigera — et redevient le board muet que la revue a refusé.
    """
    root, task_id = project_with_task

    listing = wa.tasks_view(root)
    detail = wa.task_view(root, task_id)

    assert listing["ledger"] is True
    assert listing["count"] >= 1
    assert detail["id"] == task_id
    assert detail["board"] in listing["columns"]
    assert detail["next_moves_require"], "une tâche a toujours au moins un pas suivant déclaré"


def test_une_tache_porte_son_corps_reel_description_guardrails_dependances(
    tmp_path: Path,
) -> None:
    """Issue #140 : la carte doit pouvoir montrer plus qu'un titre et un owner.

    Le YAML du board n'a jamais porté ``description``, ``guardrails`` ni
    ``dependencies`` — c'est tout l'objet d'ADR-005. Un projet jetable (pas
    ``real_project``, pour ne pas polluer les tâches que d'autres tests de ce
    fichier lisent par position) suffit : seule la fidélité de la lecture est
    en jeu, pas le gate.
    """
    from grimoire.missions.schemas import DependencyKind, TaskDependency
    from grimoire.missions.service import TaskService

    service = TaskService(tmp_path)
    mission = service.ledger.create_mission(title="Chantier", origin="test", created_by="test")
    task = service.ledger.create_task(
        mission.id,
        "Porter un corps réel",
        acceptance=("le corps est visible dans l'inspecteur",),
        description="Ce que la tâche accomplit, en une phrase.",
        guardrails=("ne jamais écrire le YAML à la main",),
        dependencies=(TaskDependency(kind=DependencyKind.BLOCKS, target="GAO-autre-001"),),
    )

    detail = wa.task_view(tmp_path, task.id)

    assert detail["description"] == "Ce que la tâche accomplit, en une phrase."
    assert detail["guardrails"] == ["ne jamais écrire le YAML à la main"]
    assert detail["dependencies"] == [{"kind": "blocks", "target": "GAO-autre-001"}]


def test_une_tache_inconnue_est_un_404_pas_un_500(real_project: Path) -> None:
    """Le transport n'attrape que FileNotFoundError, PermissionError et ValueError."""
    from grimoire.tools.workspace_routes import workspace_get

    with pytest.raises(FileNotFoundError):
        workspace_get(real_project, "/api/workspace/tasks/GAO-inexistante-999", {})


def test_la_timeline_nomme_les_journaux_absents(project_with_task: tuple[Path, str]) -> None:
    """« Pas de trace » et « je n'ai pas regardé » sont deux réponses différentes."""
    root, task_id = project_with_task

    timeline = wa.task_trace_view(root, task_id)

    assert timeline["task_id"] == task_id
    assert set(timeline["sources"]) == {"ledger", "hooks", "runtime", "evidence"}
    assert timeline["sources"]["ledger"], "le ledger existe : la source doit être nommée"
    assert timeline["entries"], "au moins la création de la tâche est datée"


def test_le_rappel_d_une_tache_neuve_est_honnetement_vide(
    project_with_task: tuple[Path, str],
) -> None:
    """#141 : c'est ce que l'inspecteur consulte pour décider d'afficher le bloc « Rappel »."""
    root, task_id = project_with_task

    rappel = wa.task_recall_view(root, task_id)

    assert rappel["task_id"] == task_id
    assert rappel["has_content"] is False


def test_le_rappel_d_une_tache_inconnue_est_un_404_pas_un_500(real_project: Path) -> None:
    from grimoire.tools.workspace_routes import workspace_get

    with pytest.raises(FileNotFoundError):
        workspace_get(real_project, "/api/workspace/tasks/GAO-inexistante-999/recall", {})


# ── Fichiers par étage ──────────────────────────────────────────────────────


def test_les_trois_etages_sont_toujours_rendus_meme_vides(real_project: Path) -> None:
    """« Ce projet n'a pas d'override » est une information, pas une section absente."""
    tree = wa.files_view(real_project)

    assert [t["id"] for t in tree["tiers"]] == ["overrides", "kit", "projections"]
    kit = next(t for t in tree["tiers"] if t["id"] == "kit")
    assert kit["exists"] and kit["count"] > 0
    assert kit["editable"] is False, "éditer l'étage kit serait perdu à la mise à jour"
    overrides = next(t for t in tree["tiers"] if t["id"] == "overrides")
    assert overrides["editable"] is True


def test_un_fichier_du_kit_expose_son_empreinte_et_son_override_possible(
    real_project: Path,
) -> None:
    """La provenance est ce que l'inspecteur de Source affiche (spec §4)."""
    tree = wa.files_view(real_project, tier="kit")
    sample = next(
        f for f in tree["tiers"][0]["files"] if f["path"].startswith("_grimoire/kit/agents/")
    )

    view = wa.file_view(real_project, sample["path"])

    assert view["tier"] == "kit"
    assert len(view["digest"]) == 64
    assert view["override_path"].startswith("_grimoire/overrides/")
    assert view["overridden"] is False
    assert view["text"], "un agent Markdown se lit"


@pytest.mark.parametrize(
    "hostile",
    ["../../etc/passwd", "/etc/passwd", "_grimoire/kit/../../../etc/passwd"],
)
def test_un_chemin_hors_projet_est_refuse_et_non_introuvable(
    real_project: Path, hostile: str
) -> None:
    """Rendre 404 sur un chemin interdit en ferait un oracle d'existence."""
    with pytest.raises(wa.WorkspacePathError):
        wa.file_view(real_project, hostile)


def test_un_fichier_hors_etage_n_est_pas_lisible_par_la_vue_source(real_project: Path) -> None:
    """La vue Source montre les étages, pas le dépôt entier."""
    (real_project / "secret.env").write_text("TOKEN=1\n", encoding="utf-8")

    with pytest.raises(wa.WorkspacePathError):
        wa.file_view(real_project, "secret.env")


# ── Diff et override ────────────────────────────────────────────────────────


def test_un_fichier_du_kit_sans_override_n_est_pas_comparable_et_le_dit(
    real_project: Path,
) -> None:
    """Le catalogue ne garde que des empreintes.

    Rendre un diff vide ferait passer « je n'ai pas le contenu d'origine » pour
    « identique » — exactement le mensonge que ce champ empêche.
    """
    tree = wa.files_view(real_project, tier="kit")
    sample = next(f for f in tree["tiers"][0]["files"] if f["path"].endswith(".md"))

    diff = wa.file_diff(real_project, sample["path"])

    assert diff["comparable"] is False
    assert diff["reason"]


def test_prendre_un_override_rend_le_fichier_comparable_puis_editable(
    second_project: Path,
) -> None:
    """Le parcours de la spec §6.5 : ouvrir, éditer, override, diff, provenance."""
    from grimoire.tools.workspace_routes import workspace_post

    tree = wa.files_view(second_project, tier="kit")
    sample = next(
        f for f in tree["tiers"][0]["files"] if f["path"].startswith("_grimoire/kit/agents/")
    )

    created = workspace_post(second_project, "/api/workspace/file/override", {"path": sample["path"]})
    assert created["created"] is True
    override_path = created["override_path"]

    identical = wa.file_diff(second_project, override_path)
    assert identical["comparable"] is True
    assert identical["identical"] is True

    again = workspace_post(second_project, "/api/workspace/file/override", {"path": sample["path"]})
    assert again["created"] is False, "prendre deux fois un override n'écrase pas le travail fait"

    original = wa.file_view(second_project, override_path)["text"]
    workspace_post(
        second_project,
        "/api/workspace/file/write",
        {"path": override_path, "text": original + "\nligne du projet\n"},
    )
    changed = wa.file_diff(second_project, override_path)
    assert changed["identical"] is False
    assert changed["added"] >= 1


def test_ecrire_dans_l_etage_kit_est_refuse_en_nommant_le_remede(real_project: Path) -> None:
    """Une écriture silencieusement perdue au prochain `grimoire up` est un piège."""
    from grimoire.tools.workspace_routes import workspace_post

    tree = wa.files_view(real_project, tier="kit")
    sample = tree["tiers"][0]["files"][0]["path"]

    with pytest.raises(wa.WorkspacePathError, match="override"):
        workspace_post(real_project, "/api/workspace/file/write", {"path": sample, "text": "x"})


def test_prendre_un_override_hors_etage_kit_est_refuse(real_project: Path) -> None:
    from grimoire.tools.workspace_routes import workspace_post

    with pytest.raises(wa.WorkspacePathError):
        workspace_post(
            real_project, "/api/workspace/file/override", {"path": ".github/copilot-instructions.md"}
        )


# ── Console : la liste blanche ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["ls", "-la"],
        ["rm", "-rf", "/"],
        ["bash", "-c", "echo pwned"],
        ["up"],
        ["init", "."],
        ["cockpit", "serve"],
        ["task", "list", ";", "ls"],
        ["doctor", "--force"],
        ["task", "show", "a", "b"],
        [],
    ],
)
def test_la_console_refuse_tout_ce_qui_n_est_pas_une_lecture_grimoire(
    real_project: Path, argv: list[str]
) -> None:
    """Critère 6 de la spec. Chaque entrée ici est un chemin d'exécution fermé.

    ``up`` et ``init`` sont refusés bien qu'ils soient des sous-commandes
    `grimoire` : elles réécrivent l'arbre du projet, et un terminal dans un
    onglet n'est pas le bon geste pour ça.
    """
    with pytest.raises(we.CommandRefusedError):
        we.run_command(real_project, argv)


def test_une_lecture_autorisee_s_execute_et_rend_sa_sortie(real_project: Path) -> None:
    """La console riche du kit écrit sur stderr : ne lire que stdout rendrait un
    terminal vide sur des commandes qui ont pourtant répondu."""
    result = we.run_command(real_project, ["grimoire", "version"])

    assert result["ok"] is True
    assert result["code"] == 0
    assert "grimoire-kit" in result["output"]
    assert result["command"] == "grimoire version"


def test_le_catalogue_des_commandes_ne_contient_aucune_ecriture(real_project: Path) -> None:
    """La palette montre la commande équivalente de chaque action : elle ne doit
    pas proposer un geste que la Console refuserait ensuite."""
    catalogue = we.catalogue()

    assert catalogue, "la palette a besoin d'un catalogue"
    assert all(entry["mutates"] is False for entry in catalogue)
    assert all(entry["command"].startswith("grimoire ") for entry in catalogue)


def test_le_diagnostic_passe_par_la_meme_liste_blanche(real_project: Path) -> None:
    """L'onglet Problèmes n'a pas de canal privilégié vers le shell."""
    report = we.doctor_view(real_project)

    assert report["command"] == "grimoire doctor"
    assert report["lines"], "doctor dit toujours quelque chose sur un projet initialisé"


# ── Inspecteur : badge de dérive, usage, historique ─────────────────────────


def test_un_override_identique_au_kit_ne_porte_pas_le_badge_de_derive(
    second_project: Path,
) -> None:
    """Juste après la prise d'override, rien n'a encore divergé : le badge de
    dérive doit rester éteint, pas allumé par défaut."""
    from grimoire.tools.workspace_routes import workspace_post

    tree = wa.files_view(second_project, tier="kit")
    # `second_project` est partagé (portée session) avec d'autres tests qui
    # prennent déjà des overrides : il faut un fichier encore vierge, pas « le
    # premier », sous peine de lire l'état laissé par un test voisin.
    sample = next(
        f
        for f in tree["tiers"][0]["files"]
        if f["path"].startswith("_grimoire/kit/agents/") and not f["overridden"]
    )
    created = workspace_post(second_project, "/api/workspace/file/override", {"path": sample["path"]})

    overrides = wa.files_view(second_project, tier="overrides")
    entry = next(f for f in overrides["tiers"][0]["files"] if f["path"] == created["override_path"])

    assert entry["masks_kit"] is True
    assert entry["diverges"] is False
    assert entry["kit_counterpart"] == sample["path"]


def test_un_override_edite_porte_le_badge_de_derive(second_project: Path) -> None:
    from grimoire.tools.workspace_routes import workspace_post

    tree = wa.files_view(second_project, tier="kit")
    sample = next(
        f
        for f in tree["tiers"][0]["files"]
        if f["path"].startswith("_grimoire/kit/agents/") and not f["overridden"]
    )
    created = workspace_post(second_project, "/api/workspace/file/override", {"path": sample["path"]})
    original = wa.file_view(second_project, created["override_path"])["text"]
    workspace_post(
        second_project,
        "/api/workspace/file/write",
        {"path": created["override_path"], "text": original + "\nligne du projet\n"},
    )

    overrides = wa.files_view(second_project, tier="overrides")
    entry = next(f for f in overrides["tiers"][0]["files"] if f["path"] == created["override_path"])

    assert entry["diverges"] is True


def test_utilise_par_ne_fabrique_jamais_une_projection(real_project: Path) -> None:
    """Une heuristique honnête peut manquer une projection ; elle ne doit
    jamais en inventer une qui n'existe pas sur le disque."""
    tree = wa.files_view(real_project, tier="kit")
    sample = next(f for f in tree["tiers"][0]["files"] if f["path"].endswith(".md"))

    usage = wa.file_usage(real_project, sample["path"])

    assert usage["path"] == sample["path"]
    for rel in usage["projections"]:
        clean = rel.split(" · ")[0]
        assert (real_project / clean).exists(), f"projection inventée : {rel}"
    assert "entries" in usage["loaded_by"]


def test_utilise_par_refuse_un_chemin_hors_projet(real_project: Path) -> None:
    with pytest.raises(wa.WorkspacePathError):
        wa.file_usage(real_project, "../../etc/passwd")


def test_historique_dit_honnetement_l_absence_de_depot(tmp_path: Path) -> None:
    """Pas de `.git` : pas d'historique inventé, juste `is_repo: false`."""
    kit = tmp_path / "_grimoire" / "kit"
    kit.mkdir(parents=True)
    (kit / "note.md").write_text("# note\n", encoding="utf-8")

    history = wa.file_history(tmp_path, "_grimoire/kit/note.md")

    assert history == {"path": "_grimoire/kit/note.md", "is_repo": False, "commits": []}


def test_historique_lit_le_journal_git_du_fichier(tmp_path: Path) -> None:
    import subprocess

    kit = tmp_path / "_grimoire" / "kit"
    kit.mkdir(parents=True)
    target = kit / "note.md"
    target.write_text("# note\n", encoding="utf-8")

    def _git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=str(tmp_path), check=True, capture_output=True)

    _git("init", "-q")
    _git("config", "user.email", "test@example.com")
    _git("config", "user.name", "Test")
    _git("add", "_grimoire/kit/note.md")
    _git("commit", "-q", "-m", "note initiale")
    target.write_text("# note\n\nune ligne de plus.\n", encoding="utf-8")
    _git("add", "_grimoire/kit/note.md")
    _git("commit", "-q", "-m", "complète la note")

    history = wa.file_history(tmp_path, "_grimoire/kit/note.md")

    assert history["is_repo"] is True
    assert len(history["commits"]) == 2
    assert history["commits"][0]["subject"] == "complète la note"
    assert all(c["sha"] and c["date"] and c["author"] for c in history["commits"])
