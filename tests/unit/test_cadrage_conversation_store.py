"""Le fichier de conversation : modèle, lecture validée (fichier hostile), hygiène (sommeil, classement, réouverture)."""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import cadrage_conversation as model
from grimoire.core import cadrage_conversation_hygiene as hygiene
from grimoire.core import cadrage_conversation_store as store
from grimoire.core.cadrage_conversation import Conversation, ConversationError

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def _conv() -> Conversation:
    conv = Conversation("un", T0.isoformat(), T0.isoformat())
    conv.say("mascotte", "Salut", "accueil", T0.isoformat())
    conv.say("utilisateur", "Les mainteneurs", "reponse", T0.isoformat())
    conv.new_element("element", "Les mainteneurs", "brief", "L'utilisateur", "utilisateurs", 2, question="Qui ?")
    conv.note(conv.plus_tard, "Un hors-sujet", 2)
    conv.note(conv.ouvertes, "Une question", 2)
    conv.question = model.Question("amorce", "Qui ?", "parce que", "utilisateurs", 0, "brief", "L'utilisateur")
    return conv


def _raw() -> dict[str, Any]:
    return _conv().to_dict()


def test_une_conversation_se_serialise_et_se_relit_a_l_identique(tmp_path: Path) -> None:
    conv = _conv()
    with store.locked_store(tmp_path):
        store.save(tmp_path, conv)
    again = store.read(tmp_path, "un")
    assert again is not None and again.to_dict() == conv.to_dict()
    assert json.loads(store.store_path(tmp_path).read_text(encoding="utf-8"))["schema"] == model.SCHEMA


def test_sans_fichier_il_n_y_a_pas_de_conversation(tmp_path: Path) -> None:
    assert store.read(tmp_path, "un") is None


def test_la_liste_fermee_des_rubriques_vient_des_angles_du_kit() -> None:
    from grimoire.core.cadrage_interview import ANGLES

    for angle in ANGLES:
        assert (angle.phase, angle.section) in model.RUBRIQUES
        assert all(t in model.RUBRIQUES for t in angle.targets)
    assert ("brainstorm", "Questions ouvertes") in model.RUBRIQUES
    assert model.rubrique_ok("brief", "L'utilisateur") and not model.rubrique_ok("brief", "Exigences")
    assert not model.rubrique_ok(1, "x")


def test_angle_pour_prefere_l_angle_de_la_question_puis_le_premier_qui_porte_la_rubrique() -> None:
    assert model.angle_pour("comprehension", "Contraintes") == "contraintes"
    assert model.angle_pour("brief", "L'utilisateur", prefer="criteres") == "utilisateurs"
    assert model.angle_pour("brainstorm", "Questions ouvertes", prefer="risques") == "risques"
    assert model.angle_pour("brainstorm", "Questions ouvertes") == "utilisateurs"
    with pytest.raises(ConversationError, match="hors liste"):
        model.angle_pour("exigences", "Fonctionnelles")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema=2),
        lambda d: d.update(mode="hack"),
        lambda d: d.update(document="x"),
        lambda d: d.update(chantier="autre"),
        lambda d: d.update(inconnu=1),
        lambda d: d.update(rev=-1),
        lambda d: d.update(rev=True),
        lambda d: d.update(rev="3"),
        lambda d: d.update(cree="pas une date"),
        lambda d: d.update(active_le=""),
        lambda d: d.update(classee_le="demain"),
        lambda d: d.update(tours="x"),
        lambda d: d.update(tours=[{"n": 1}]),
        lambda d: d["tours"][0].update(auteur="root"),
        lambda d: d["tours"][0].update(geste="exec"),
        lambda d: d["tours"][0].update(texte=""),
        lambda d: d["tours"][0].update(texte="x" * 5000),
        lambda d: d["tours"][0].update(texte=12),
        lambda d: d["tours"][1].update(n=7),
        lambda d: d["tours"][0].update(date="hier"),
        lambda d: d["elements"][0].update(phase="exigences", section="Fonctionnelles"),
        lambda d: d["elements"][0].update(section="../../etc"),
        lambda d: d["elements"][0].update(angle="inconnu"),
        lambda d: d["elements"][0].update(source=99),
        lambda d: d["elements"][0].update(source=0),
        lambda d: d["elements"][0].update(statut="valide"),
        lambda d: d["elements"][0].update(genre="exec"),
        lambda d: d["elements"][0].update(texte="y" * 900),
        lambda d: d["elements"][0].update(ecrit="oui"),
        lambda d: d["elements"][0].update(avant="x"),
        lambda d: d["elements"][0].update(id="../x"),
        lambda d: d["elements"][0].update(id="e1x"),
        lambda d: d["elements"][0].update(remplace="e99"),
        lambda d: d["elements"][0].update(extra=1),
        lambda d: d["elements"][0].update(origine="pirate"),
        lambda d: d["elements"][0].update(individuel="oui"),
        lambda d: d["elements"][0].update(intention="effacer"),
        lambda d: d["plus_tard"][0].update(tour=50),
        lambda d: d["plus_tard"][0].update(texte="z" * 500),
        lambda d: d["plus_tard"].extend([{"texte": "a", "tour": 1}] * 60),
        lambda d: d["ouvertes"].append("pas un objet"),
        lambda d: d["question"].update(kind="exec"),
        lambda d: d["question"].update(angle="inconnu"),
        lambda d: d["question"].update(phase="brief", section="Hors liste"),
        lambda d: d["question"].update(obligatoire="oui"),
        lambda d: d["question"].update(index=-1),
        lambda d: d["question"].update(extra=1),
        lambda d: d.update(question="texte"),
    ],
)
def test_un_fichier_hors_cadre_est_refuse_sans_planter(mutate: Any) -> None:
    data = _raw()
    mutate(data)
    with pytest.raises(ConversationError):
        Conversation.from_dict(data, chantier="un")


def test_le_compteur_d_identifiants_rattrape_le_plus_grand_numero_existant() -> None:
    data = _raw()
    data["seq"] = 0
    data["elements"][0]["id"] = "e7"
    conv = Conversation.from_dict(data, chantier="un")
    assert conv.seq == 7
    assert conv.new_element("element", "x", "brief", "L'utilisateur", "utilisateurs", 1).id == "e8"
    fresh = Conversation("un", T0.isoformat(), T0.isoformat())
    fresh.elements = conv.elements
    assert fresh.new_element("element", "y", "brief", "L'utilisateur", "utilisateurs", 1).id == "e9"  # même sans compteur


def test_des_identifiants_en_double_restent_lisibles_mais_a_reparer() -> None:
    data = _raw()
    data["elements"].append(copy.deepcopy(data["elements"][0]))
    conv = Conversation.from_dict(data, chantier="un")
    assert "doublons" in conv.reparer and len(conv.elements) == 2
    assert "reparer" not in conv.to_dict()  # jamais enregistré : la réparation est à la main
    assert Conversation.from_dict(_raw(), chantier="un").reparer == ""


def test_la_place_est_verifiee_avant_d_ecrire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store.check_room(_conv())
    monkeypatch.setattr(store, "MAX_FILE_BYTES", 1000)
    with pytest.raises(ConversationError, match="trop volumineuse"):
        store.check_room(_conv())


def test_un_fichier_qui_n_est_pas_un_objet_est_refuse() -> None:
    for raw in ([], "x", 3, None):
        with pytest.raises(ConversationError):
            Conversation.from_dict(raw)


def test_la_lecture_refuse_json_invalide_profond_trop_gros_ou_hostile(tmp_path: Path) -> None:
    path = store.store_path(tmp_path)
    for payload in (b"{pas du json", b"\xff\xfe", b"[" * 200_000, b"[]"):
        path.write_bytes(payload)
        with pytest.raises(ConversationError):
            store.read(tmp_path, "un")
    path.write_bytes(b" " * (model.MAX_FILE_BYTES + 1))
    with pytest.raises(ConversationError, match="dépasse"):
        store.read(tmp_path, "un")


def test_un_lien_symbolique_ou_une_fifo_n_est_jamais_lu(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    outside = tmp_path_factory.mktemp("dehors") / "secret.json"
    outside.write_text(json.dumps(_raw()), encoding="utf-8")
    path = store.store_path(tmp_path)
    path.symlink_to(outside)
    with pytest.raises(ConversationError, match=r"ordinaire|conversation"):
        store.read(tmp_path, "un")
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(ConversationError):
        store.read(tmp_path, "un")


def test_ecrire_une_conversation_trop_volumineuse_est_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "MAX_FILE_BYTES", 100)
    with pytest.raises(ConversationError, match="volumineuse"):
        store.save(tmp_path, _conv())
    assert not store.store_path(tmp_path).exists()


def test_les_bornes_du_modele_refusent_au_lieu_de_deborder() -> None:
    conv = _conv()
    with pytest.raises(ConversationError, match="vide"):
        conv.say("utilisateur", "   ", "reponse", T0.isoformat())
    with pytest.raises(ConversationError, match="trop long"):
        conv.say("utilisateur", "x" * (model.MAX_TURN_CHARS + 1), "reponse", T0.isoformat())
    with pytest.raises(ConversationError, match="chaîne"):
        model.clean_text(3, 10)
    assert model.clean_text("a​b\x00c\n d", 20) == "ab c d"
    for _ in range(model.MAX_ELEMENTS - 1):
        conv.new_element("element", "x", "brief", "L'utilisateur", "utilisateurs", 1)
    with pytest.raises(ConversationError, match="trop d'éléments"):
        conv.new_element("element", "x", "brief", "L'utilisateur", "utilisateurs", 1)
    with pytest.raises(ConversationError, match="hors liste"):
        _conv().new_element("element", "x", "exigences", "Fonctionnelles", "utilisateurs", 1)
    full = _conv()
    full.tours = [model.Tour(i + 1, "mascotte", "x", T0.isoformat(), "note") for i in range(model.MAX_TURNS)]
    with pytest.raises(ConversationError, match="trop longue"):
        full.say("mascotte", "x", "note", T0.isoformat())


def test_les_notes_longues_sont_tronquees_et_les_plus_anciennes_partent() -> None:
    conv = _conv()
    conv.note(conv.plus_tard, "mot " * 300, 1)
    assert conv.plus_tard[-1].texte.endswith("…") and len(conv.plus_tard[-1].texte) <= model.MAX_NOTE_CHARS
    for i in range(model.MAX_NOTES + 5):
        conv.note(conv.ouvertes, f"n{i}", 1)
    assert len(conv.ouvertes) == model.MAX_NOTES and conv.ouvertes[-1].texte == f"n{model.MAX_NOTES + 4}"


def test_parse_date_lit_utc_par_defaut() -> None:
    assert model.parse_date("2026-11-01T09:00:00").tzinfo is UTC
    with pytest.raises(ConversationError):
        model.parse_date("n'importe quoi")


# ── hygiène ────────────────────────────────────────────────────────────────

REG = hygiene.Reglages(sommeil_jours=3, classee_jours=10, max_actives=2)


def test_le_statut_se_deduit_de_l_horloge_injectee() -> None:
    conv = _conv()
    assert hygiene.statut(conv, T0 + timedelta(days=2), REG) == "active"
    assert hygiene.statut(conv, T0 + timedelta(days=3), REG) == "en-sommeil"
    assert hygiene.statut(conv, T0 + timedelta(days=9), REG) == "en-sommeil"
    assert hygiene.statut(conv, T0 + timedelta(days=10), REG) == "classee"
    assert conv.classee_le == ""  # lire n'écrit rien : jamais supprimée, jamais modifiée par le temps


def test_classer_puis_rouvrir_sont_explicites() -> None:
    conv = _conv()
    hygiene.classer(conv, T0 + timedelta(days=1))
    assert hygiene.statut(conv, T0 + timedelta(days=1), REG) == "classee" and conv.rev == 1
    with pytest.raises(ConversationError, match="déjà classée"):
        hygiene.classer(conv, T0)
    later = T0 + timedelta(days=30)
    hygiene.rouvrir(conv, later)
    assert hygiene.statut(conv, later, REG) == "active" and conv.classee_le == ""
    conv.mode = "termine"
    conv.document = "posee"
    hygiene.rouvrir(conv, later)
    assert conv.mode == "cadrage" and conv.document == ""


def test_les_reglages_sont_bornes_et_retombent_au_defaut() -> None:
    assert hygiene.Reglages.lire(None) == hygiene.Reglages()
    assert hygiene.Reglages.lire({"sommeil_jours": 2, "classee_jours": 5, "max_actives": 3}) == hygiene.Reglages(2, 5, 3)
    assert hygiene.Reglages.lire({"sommeil_jours": 0, "classee_jours": True, "max_actives": "9"}) == hygiene.Reglages()
    assert hygiene.Reglages.lire({"sommeil_jours": 20, "classee_jours": 5}).classee_jours == 20  # jamais avant le sommeil
    assert hygiene.Reglages.lire({"max_actives": 1000}).max_actives == hygiene.Reglages().max_actives
    assert hygiene.maintenant().tzinfo is UTC


def _put(root: Path, slug: str, when: datetime, **fields: Any) -> tuple[str, Path]:
    directory = root / slug
    directory.mkdir(parents=True)
    conv = Conversation(slug, when.isoformat(), when.isoformat())
    for key, value in fields.items():
        setattr(conv, key, value)
    with store.locked_store(directory):
        store.save(directory, conv)
    return slug, directory


def test_la_liste_propose_au_plus_n_actives_et_garde_les_classees_retrouvables(tmp_path: Path) -> None:
    now = T0 + timedelta(days=5)
    entries = [
        _put(tmp_path, "recente", now - timedelta(hours=1)),
        _put(tmp_path, "hier", now - timedelta(days=1)),
        _put(tmp_path, "sommeil", now - timedelta(days=4)),
        _put(tmp_path, "vieille", now - timedelta(days=12)),
        _put(tmp_path, "rangee", now - timedelta(hours=2), classee_le=now.isoformat()),
    ]
    (tmp_path / "vide").mkdir()
    entries.append(("vide", tmp_path / "vide"))
    (tmp_path / "abimee").mkdir()
    (tmp_path / "abimee" / store.FILENAME).write_text("{pas du json", encoding="utf-8")
    entries.append(("abimee", tmp_path / "abimee"))
    out = hygiene.lister(entries, now, REG)
    assert [r["chantier"] for r in out["actives"]] == ["recente", "hier"]
    assert [r["chantier"] for r in out["autres"]] == ["sommeil"] and out["autres"][0]["statut"] == "en-sommeil"
    assert {r["chantier"] for r in out["classees"]} == {"vieille", "rangee"}
    auto = {r["chantier"]: r["classee_auto"] for r in out["classees"]}
    assert auto == {"vieille": True, "rangee": False}
    assert [e["chantier"] for e in out["erreurs"]] == ["abimee"]
    assert out["reglages"] == {"sommeil_jours": 3, "classee_jours": 10, "max_actives": 2}
    assert (tmp_path / "vieille" / store.FILENAME).exists()  # classée : conservée, jamais supprimée
