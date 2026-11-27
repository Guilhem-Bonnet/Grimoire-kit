"""La boucle de l'Atelier après l'écriture : date de modification, état d'après les faits, tâches du découpage,
ce qui attend la personne, garde-fous de saisie, déclaration de la MOA.

Un HOME par test (le journal d'autorité est celui du test). La validation passe par ``approve_requirement`` avec le tour
humain simulé : aucune route du cockpit ne valide, et les tests de ``test_workspace_atelier_live`` le prouvent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from grimoire.core import chantiers
from grimoire.core.chantiers import new_chantier, read_chantier, set_state, slugify
from grimoire.core.standard_state import AGENT_MARKER_ENVS, HOST_SESSION_ENVS, SESSION_ENV
from grimoire.missions import requirement_gate as rg
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_loop as loop
from grimoire.tools import workspace_atelier_trace as live

P = "/api/workspace/"
EXIGENCES = """# Exigences

- REQ-001 [Must] : dicter une idée au micro
  - la transcription apparaît en moins de 2 secondes
- REQ-002 (Should) : repli sur le navigateur
  - `pytest tests/test_repli.py`
"""
_AGENT_ENVS = ("GRIMOIRE_UNATTENDED", SESSION_ENV, *HOST_SESSION_ENVS, *AGENT_MARKER_ENVS)


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in _AGENT_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    data = {"project": {"name": "p"}, "governance": {"roles": {"moa": "Ada"}, "requirement_gate": "warn"}}
    (root / "project-context.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    wa.set_atelier(root, True)
    return root


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Une horloge qui avance à chaque appel : la date de modification se compare sans attendre."""
    ticks: list[str] = []

    def now() -> str:
        ticks.append(f"2030-01-01T00:00:{len(ticks):02d}Z")
        return ticks[-1]

    monkeypatch.setattr(chantiers, "_now", now)
    return ticks


def _chantier(root: Path, text: str = EXIGENCES) -> str:
    made = new_chantier(root, "Dicter une idée")
    (root / "_grimoire" / "cadrage" / made.slug / "04-exigences.md").write_text(text, encoding="utf-8")
    return made.slug


def _doc(root: Path, slug: str) -> dict[str, Any]:
    return dict(wa.get(root, f"{P}atelier/chantiers/{slug}", {}))


def _edit(root: Path, slug: str, phase: str, find: str, text: str) -> tuple[int, dict[str, Any]]:
    view = next(p for p in _doc(root, slug)["phases"] if p["id"] == phase)
    block = next(b for b in view["blocks"] if find in b["text"])
    return wa.put_http(
        root, f"{P}atelier/chantiers/{slug}/blocs/{block['id']}",
        json.dumps({"text": text, "expected": view["fingerprint"]}).encode(),
    )


def _yaml_bytes(root: Path, slug: str) -> bytes:
    return (root / "_grimoire" / "cadrage" / slug / "chantier.yaml").read_bytes()


# ── la date de modification et l'état suivent les écritures, jamais les lectures ─────────────────────────────


def test_une_lecture_n_ecrit_rien(project: Path, clock: list[str]) -> None:
    slug = _chantier(project)
    before = _yaml_bytes(project, slug)
    wa.get(project, f"{P}atelier/chantiers", {})
    _doc(project, slug)
    wa.get(project, f"{P}atelier/chantiers/{slug}/entretien", {})
    assert _yaml_bytes(project, slug) == before


def test_l_ecriture_d_un_bloc_met_la_date_a_jour_et_fait_avancer_l_etat(project: Path, clock: list[str]) -> None:
    slug = _chantier(project, "# Exigences\n\nUn texte libre.\n")
    created = read_chantier(project, slug)
    assert created.etat == "idee"
    status, _ = _edit(project, slug, "brief", "À compléter", "Le problème est réel.")
    assert status == 200
    after = read_chantier(project, slug)
    assert after.modifie > created.modifie  # la date bouge à l'écriture
    assert after.cree == created.cree
    assert after.etat == "en-discussion"  # cadrage entamé


def _fill_everything(root: Path, slug: str) -> None:
    """Chaque rubrique de chaque phase a du contenu : le cadrage n'a plus aucun point à préciser."""
    from grimoire.core.cadrage import PHASES

    for phase in PHASES:
        path = root / "_grimoire" / "cadrage" / slug / phase.filename
        text = path.read_text(encoding="utf-8")
        for section in phase.sections:
            body = EXIGENCES.split("\n", 2)[2].strip() if (phase.id, section) == ("exigences", "Fonctionnelles") else f"Réponse pour {section}."
            text = text.replace(f"> À compléter — {section.lower()}.", body)
        path.write_text(text, encoding="utf-8")


def test_des_exigences_ecrites_seules_ne_disent_pas_pret_a_valider(project: Path, clock: list[str]) -> None:
    slug = _chantier(project, "# Exigences\n\nUn texte libre.\n")
    _edit(project, slug, "exigences", "Un texte libre", EXIGENCES.split("\n", 2)[2].strip())
    assert read_chantier(project, slug).etat == "en-discussion"  # le cadrage a encore des points à préciser
    assert loop.fact_state(project, slug) == "en-discussion"


def test_pret_a_valider_exige_des_exigences_et_un_cadrage_complet(project: Path, clock: list[str]) -> None:
    slug = new_chantier(project, "Dicter une idée").slug
    _fill_everything(project, slug)
    assert loop.fact_state(project, slug) == "pret-a-valider"
    _edit(project, slug, "brief", "Réponse pour Le problème", "Le problème est réel.")
    assert read_chantier(project, slug).etat == "pret-a-valider"


def test_pret_a_valider_ne_recule_jamais_quand_le_cadrage_se_rouvre(project: Path, clock: list[str]) -> None:
    slug = new_chantier(project, "Dicter une idée").slug
    _fill_everything(project, slug)
    _edit(project, slug, "brief", "Réponse pour Le problème", "Le problème est réel.")
    assert read_chantier(project, slug).etat == "pret-a-valider"
    path = project / "_grimoire/cadrage" / slug / "01-brief.md"
    path.write_text(path.read_text(encoding="utf-8").replace("Réponse pour Pourquoi maintenant.", "> À compléter — pourquoi maintenant."), encoding="utf-8")
    _edit(project, slug, "brief", "Le problème est réel", "Le problème est bien réel.")
    assert read_chantier(project, slug).etat == "pret-a-valider"  # l'état ne recule jamais, même si un point revient
    assert loop.fact_state(project, slug) == "en-discussion"  # les faits, eux, le disent


def test_un_pret_a_valider_pose_a_la_main_survit_a_une_ecriture(project: Path, clock: list[str]) -> None:
    slug = new_chantier(project, "Dicter une idée").slug  # cadrage au gabarit : les faits ne disent pas « prêt »
    set_state(project, slug, "en-discussion")
    set_state(project, slug, "pret-a-valider")
    _edit(project, slug, "brief", "À compléter", "Le problème est réel.")
    assert read_chantier(project, slug).etat == "pret-a-valider"


def test_un_etat_pose_a_la_main_plus_loin_ne_retombe_pas(project: Path, clock: list[str]) -> None:
    slug = _chantier(project, "# Exigences\n\nUn texte libre.\n")
    set_state(project, slug, "valide")
    _edit(project, slug, "exigences", "Un texte libre", EXIGENCES.split("\n", 2)[2].strip())
    assert read_chantier(project, slug).etat == "valide"


def test_l_etat_ne_recule_jamais_et_respecte_un_etat_pose_plus_loin(project: Path, clock: list[str]) -> None:
    slug = _chantier(project, "# Exigences\n\nUn texte libre.\n")
    set_state(project, slug, "valide")
    _edit(project, slug, "brief", "À compléter", "Le problème est réel.")
    after = read_chantier(project, slug)
    assert after.etat == "valide"  # « en-discussion » n'est pas une avancée sur « valide »
    assert after.modifie  # mais la date a bougé


def test_un_chantier_archive_ou_livre_n_est_pas_deplace(project: Path, clock: list[str]) -> None:
    slug = _chantier(project, "# Exigences\n\nUn texte libre.\n")
    set_state(project, slug, "archive")
    _edit(project, slug, "brief", "À compléter", "Le problème est réel.")
    assert read_chantier(project, slug).etat == "archive"


def test_principal_sans_chantier_yaml_n_en_recoit_pas(project: Path) -> None:
    from grimoire.core.cadrage import scaffold

    scaffold(project, project_name="p")
    assert chantiers.record_write(project, "principal", "en-discussion") is None
    assert not (project / "_grimoire/cadrage/chantier.yaml").exists()


def test_record_write_ignore_l_inconnu_et_l_illisible(project: Path) -> None:
    assert chantiers.record_write(project, "fantome", "en-discussion") is None  # chantier inconnu : rien à noter
    slug = _chantier(project)
    (project / "_grimoire/cadrage" / slug / "chantier.yaml").write_text("slug: [pas\n", encoding="utf-8")
    assert chantiers.record_write(project, slug, "en-discussion") is None  # chantier.yaml illisible : laissé tel quel
    assert (project / "_grimoire/cadrage" / slug / "chantier.yaml").read_text(encoding="utf-8") == "slug: [pas\n"


def test_les_cles_de_couverture_du_chantier_principal_sont_nues() -> None:
    assert loop._own("REQ-001", "principal") == "REQ-001"
    assert loop._own("un/REQ-001", "principal") is None
    assert loop._own("un/REQ-001", "un") == "REQ-001"
    assert loop._own("autre/REQ-001", "un") is None
    assert loop._own("REQ-001", "un") is None


def test_un_ledger_ou_des_exigences_illisibles_ne_cassent_ni_la_liste_ni_la_page(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _chantier(project)

    def broken(*_: Any, **__: Any) -> Any:
        raise ValueError("ledger abîmé")

    monkeypatch.setattr(loop, "tasks", broken)
    assert _doc(project, slug)["taches"] == []  # la page se dessine, sans liste de tâches
    assert loop.fact_state(project, slug) == "en-discussion"  # l'état retombe sur ce qui se lit encore
    monkeypatch.setattr(loop, "_musts_to_validate", broken)
    assert loop.waiting(project, slug, suggestions=2, asking=False) == {"question": 0, "suggestions": 2, "a_valider": 0, "decoupage": 0}


def test_une_ecriture_refusee_ne_touche_pas_la_date(project: Path, clock: list[str]) -> None:
    slug = _chantier(project)
    before = _yaml_bytes(project, slug)
    view = next(p for p in _doc(project, slug)["phases"] if p["id"] == "brief")
    block = next(b for b in view["blocks"] if b["editable"])
    status, _ = wa.put_http(
        project, f"{P}atelier/chantiers/{slug}/blocs/{block['id']}",
        b'{"text": "x", "expected": "empreinte-perimee"}',
    )
    assert status == 409
    assert _yaml_bytes(project, slug) == before


def test_commentaire_et_suggestion_mettent_la_date_a_jour(project: Path, clock: list[str]) -> None:
    slug = _chantier(project, "# Exigences\n\nUn texte libre.\n")
    before = read_chantier(project, slug).modifie
    view = next(p for p in _doc(project, slug)["phases"] if p["id"] == "brief")
    block = next(b for b in view["blocks"] if b["editable"])
    status, _ = wa.post_http(project, f"{P}atelier/chantiers/{slug}/comments",
                             {"block_id": block["id"], "quote": "", "start": 0, "texte": "Une remarque."})
    assert status == 200
    assert read_chantier(project, slug).modifie > before


# ── le découpage ferme la boucle ────────────────────────────────────────────


def _split(root: Path, slug: str) -> dict[str, Any]:
    rg.approve_requirement(root, f"{slug}/REQ-001", actor="ada")
    preview = live.decoupage_preview(root, slug)
    status, out = wa.post_http(root, f"{P}atelier/chantiers/{slug}/decoupage", {"confirm": True, "digest": preview["digest"]})
    assert status == 200, out
    return out


def test_apres_le_decoupage_l_etat_avance_et_les_taches_sont_listees(project: Path, clock: list[str]) -> None:
    slug = _chantier(project)
    before = read_chantier(project, slug)
    out = _split(project, slug)
    assert out["created"]
    after = read_chantier(project, slug)
    assert after.etat == "en-cours"  # découpé en tâches : un fait, pas une saisie
    assert after.modifie > before.modifie
    doc = _doc(project, slug)
    ids = {c["task_id"] for c in out["created"]}
    assert {t["id"] for t in doc["taches"]} == ids  # identifiants COMPLETS, pas tronqués
    assert all(t["status"] and t["title"] and t["requirement"] for t in doc["taches"])
    assert doc["chantier"]["etat"] == "en-cours"


def test_un_decoupage_rejoue_n_ecrit_rien(project: Path, clock: list[str]) -> None:
    slug = _chantier(project)
    _split(project, slug)
    stamp = read_chantier(project, slug).modifie
    preview = live.decoupage_preview(project, slug)
    status, out = wa.post_http(project, f"{P}atelier/chantiers/{slug}/decoupage", {"confirm": True, "digest": preview["digest"]})
    assert status == 200 and out["already"] is True
    assert read_chantier(project, slug).modifie == stamp


def test_les_taches_d_un_autre_chantier_ne_sont_pas_listees(project: Path, clock: list[str]) -> None:
    one = _chantier(project)
    other = new_chantier(project, "Autre").slug
    (project / "_grimoire/cadrage" / other / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    _split(project, one)
    assert loop.tasks(project, other) == []
    assert loop.tasks(project, one)


def test_la_liste_dit_ce_qui_attend_la_personne(project: Path, clock: list[str]) -> None:
    slug = _chantier(project)
    row = next(r for r in wa.list_view(project)["chantiers"] if r["slug"] == slug)
    assert row["attente"]["a_valider"] == 1  # REQ-001 est Must et n'est pas validée
    assert row["attente"]["question"] == 0 and row["attente"]["suggestions"] == 0
    rg.approve_requirement(project, f"{slug}/REQ-001", actor="ada")
    row = next(r for r in wa.list_view(project)["chantiers"] if r["slug"] == slug)
    assert row["attente"]["a_valider"] == 0


def test_la_question_posee_au_decoupage_attend_la_personne(project: Path, clock: list[str]) -> None:
    slug = _chantier(project, "# Exigences\n\n- REQ-001 [Must] : sans aucun critère\n- REQ-002 [Must] : avec\n  - `pytest tests/test_x.py`\n")
    rg.approve_requirement(project, f"{slug}/REQ-001", actor="ada")
    rg.approve_requirement(project, f"{slug}/REQ-002", actor="ada")
    preview = live.decoupage_preview(project, slug)
    wa.post_http(project, f"{P}atelier/chantiers/{slug}/decoupage", {"confirm": True, "digest": preview["digest"]})
    row = next(r for r in wa.list_view(project)["chantiers"] if r["slug"] == slug)
    assert row["attente"]["decoupage"] >= 1


def test_la_question_en_cours_d_un_entretien_guide_est_signalee(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.tools import workspace_atelier_assistant as asst

    slug = _chantier(project)
    monkeypatch.setattr(asst, "agent_status", lambda root: {"available": False, "mode": "kanban", "reason": "", "name": ""})
    row = next(r for r in wa.list_view(project)["chantiers"] if r["slug"] == slug)
    assert row["attente"]["question"] == 0  # pas démarré : aucune question n'attend
    asst.act(project, slug, {"geste": "guide-demarrer"})
    row = next(r for r in wa.list_view(project)["chantiers"] if r["slug"] == slug)
    assert row["attente"]["question"] == 1
    assert row["avancement"]["etat"] in ("a-cadrer", "en-cours")


def test_la_liste_et_la_page_donnent_le_meme_avancement(project: Path) -> None:
    slug = _chantier(project)
    row = next(r for r in wa.list_view(project)["chantiers"] if r["slug"] == slug)
    doc = _doc(project, slug)
    for key in ("etat", "resume", "a_preciser", "a_confirmer"):
        assert row["avancement"][key] == doc["avancement"][key]
    assert [p["complete"] for p in row["avancement"]["phases"]] == [p["complete"] for p in doc["avancement"]["phases"]]


# ── garde-fous de saisie ────────────────────────────────────────────────────


def test_un_identifiant_d_exigence_en_double_est_signale(project: Path) -> None:
    slug = _chantier(project, "# Exigences\n\n- REQ-001 [Must] : un\n  - a\n\n- REQ-001 [Must] : deux\n  - b\n")
    warned = _doc(project, slug)["avertissements"]
    assert [w["req"] for w in warned] == ["REQ-001"] and warned[0]["count"] == 2
    assert "REQ-001" in warned[0]["message"]
    assert _doc(project, _chantier_clean(project))["avertissements"] == []


def _chantier_clean(root: Path) -> str:
    made = new_chantier(root, "Propre")
    (root / "_grimoire/cadrage" / made.slug / "04-exigences.md").write_text(EXIGENCES, encoding="utf-8")
    return made.slug


def test_l_ecriture_d_un_doublon_est_signalee_dans_la_reponse(project: Path) -> None:
    slug = _chantier(project)
    status, out = _edit(project, slug, "exigences", "REQ-002", "- REQ-001 [Should] : repris\n  - `pytest x`")
    assert status == 200
    assert [w["req"] for w in out["avertissements"]] == ["REQ-001"]


def test_la_meme_exigence_d_un_fichier_a_l_autre_n_est_pas_un_doublon() -> None:
    phases = [{"id": "exigences", "blocks": [{"kind": "exigence", "req": "REQ-001"}]},
              {"id": "cahier-des-charges", "blocks": [{"kind": "exigence", "req": "REQ-001"}]}]
    assert loop.duplicate_requirements(phases) == []


def test_le_slug_est_coupe_a_une_limite_de_mot() -> None:
    long = "Mettre en place une authentification forte pour tous les utilisateurs de la plateforme"
    slug = slugify(long)
    assert len(slug) <= chantiers.MAX_SLUG
    assert long.lower().replace(" ", "-").startswith(slug)
    assert not slug.endswith("-")
    assert slug == "mettre-en-place-une-authentification-forte-pour"  # pas « …-pour-tous-les-utilisat »
    assert slugify("a" * 80) == "a" * chantiers.MAX_SLUG  # un seul mot trop long : coupé net, faute de mieux
    assert slugify("x" * 47 + " mot") == "x" * 47  # la coupure tombe déjà entre deux mots
    assert slugify("Idée courte") == "idee-courte"


def test_l_auteur_est_la_moa_sinon_l_utilisateur_sinon_personne(project: Path, tmp_path: Path) -> None:
    assert loop.author(project) == {"nom": "Ada", "source": "moa"}
    other = tmp_path / "o"
    other.mkdir()
    (other / "project-context.yaml").write_text("user:\n  name: Grace\n", encoding="utf-8")
    assert loop.author(other) == {"nom": "Grace", "source": "utilisateur"}
    third = tmp_path / "t"
    third.mkdir()
    (third / "project-context.yaml").write_text("project:\n  name: t\n", encoding="utf-8")
    assert loop.author(third) == {"nom": "", "source": ""}
    (third / "project-context.yaml").write_text("user:\n  name: Developer\n", encoding="utf-8")
    assert loop.author(third) == {"nom": "", "source": ""}  # le repli de `grimoire init` n'est pas un nom déclaré
    assert _doc(project, _chantier(project))["auteur"]["nom"] == "Ada"


# ── validation : comprendre quoi faire ──────────────────────────────────────


def test_sans_moa_la_page_donne_la_cle_exacte_son_fichier_et_un_exemple(tmp_path: Path) -> None:
    root = tmp_path / "q"
    root.mkdir()
    (root / "project-context.yaml").write_text("project:\n  name: q\n", encoding="utf-8")
    wa.set_atelier(root, True)
    slug = _chantier(root)
    moa = live.trace_view(root, slug)["moa"]
    assert moa["declared"] is False
    assert moa["how"]["key"] == "governance.roles.moa" and moa["how"]["file"] == "project-context.yaml"
    declared = yaml.safe_load(moa["how"]["example"].replace("Prénom Nom", "Ada Lovelace"))
    assert declared == {"governance": {"roles": {"moa": "Ada Lovelace"}}}  # l'exemple est valide tel quel
    assert live.decoupage_preview(root, slug)["moa"]["how"] == moa["how"]


def test_l_exemple_copie_declare_vraiment_la_moa(tmp_path: Path) -> None:
    root = tmp_path / "q"
    root.mkdir()
    (root / "project-context.yaml").write_text(live.MOA_HOW["example"], encoding="utf-8")
    from grimoire.missions.requirement_gate import gate_mode

    assert gate_mode(root).declared is True


def test_avec_une_moa_aucune_aide_de_declaration(project: Path) -> None:
    slug = _chantier(project)
    assert "how" not in live.trace_view(project, slug)["moa"]


def test_une_validation_faite_au_terminal_se_voit_a_la_lecture_suivante(project: Path) -> None:
    slug = _chantier(project)
    before = live.trace_view(project, slug)
    assert next(r for r in before["requirements"] if r["id"] == "REQ-001")["validated"] is False
    rg.approve_requirement(project, f"{slug}/REQ-001", actor="ada")  # le geste du terminal
    after = live.trace_view(project, slug)  # sans rechargement manuel, sans attendre l'expiration du cache
    row = next(r for r in after["requirements"] if r["id"] == "REQ-001")
    assert row["validated"] is True and row["validated_by"] == "Ada"
