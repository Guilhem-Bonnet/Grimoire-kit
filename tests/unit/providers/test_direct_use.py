"""``choose_for`` : la table de décision complète (épique #1477, lot B1). Fonction pure : aucune E/S dans ces tests."""

from __future__ import annotations

from typing import Any

import pytest

from grimoire.providers.availability import ModelAvailability
from grimoire.providers.direct_use import (
    DEFAULT_MIN,
    USAGES,
    Authorization,
    DirectState,
    MinCapability,
    choose_for,
)


def model(
    key: str,
    *,
    cost: str = "gratuit-local",
    egress: str = "rien",
    tier: str = "mid",
    available: bool = True,
    reason: str = "",
    caps: tuple[tuple[str, int | bool, str], ...] = (),
) -> ModelAvailability:
    return ModelAvailability(
        key=key, label=key.split(":", 1)[1], source="x", provider_id="p", model_id=key, origin="", tier=tier,
        tier_provenance="registre" if tier else "", provider_type="", currency="", configured=True,
        available=available, reason=reason, detail="détail" if reason else "", egress=egress, cost=cost,
        key_present=None, key_command="", cooldown_until=None, capabilities=caps,
    )


LOCAL = model("ollama:local", tier="mid")
SUB = model("hote:abo", cost="abonnement", egress="texte", tier="strong")
PAID = model("registre:p/payant", cost="payant", egress="texte", tier="strong")
LIGHT = model("ollama:leger", tier="cheap")


def state(
    *models: ModelAvailability,
    allowed: dict[str, list[Authorization]] | None = None,
    used: dict[str, int] | None = None,
    hold: str = "",
    refusals: dict[str, str] | None = None,
) -> DirectState:
    return DirectState(
        models={m.key: m for m in models},
        allowed={u: tuple(v) for u, v in (allowed or {}).items()},
        used_today=used or {},
        hold_reason=hold,
        data_refusals=refusals,
    )


def auth(*keys: str) -> list[Authorization]:
    return [Authorization(k) for k in keys]


def test_aucun_modele_autorise_rend_le_mode_sans_modele() -> None:
    d = choose_for("conversation", state(LOCAL))
    assert d.kind == "none"
    assert "sans modèle" in d.reason
    assert d.key == ""


def test_usage_inconnu() -> None:
    d = choose_for("danse", state(LOCAL, allowed={"conversation": auth(LOCAL.key)}))
    assert d.kind == "none"
    assert "usage inconnu" in d.reason


def test_sans_modele_courant_le_premier_qui_convient_dans_l_ordre_de_preference() -> None:
    st = state(LOCAL, SUB, allowed={"conversation": auth(SUB.key, LOCAL.key)})
    d = choose_for("conversation", st)
    assert (d.kind, d.key) == ("use", SUB.key)  # l'ordre de l'humain, pas le coût
    assert d.trace[0].verdict == "retenu"


def test_l_ordre_saute_les_indisponibles_et_trace_pourquoi() -> None:
    down = model("registre:p/down", available=False, reason="clé-absente", cost="payant", egress="texte")
    st = state(down, LOCAL, allowed={"conversation": auth(down.key, LOCAL.key)})
    d = choose_for("conversation", st)
    assert d.key == LOCAL.key
    lines = {ln.key: ln for ln in d.trace}
    assert lines[down.key].verdict == "écarté"
    assert "clé-absente" in lines[down.key].why
    assert lines[LOCAL.key].verdict == "retenu"


def test_plafond_du_jour_atteint_ecarte_et_en_dessous_retient() -> None:
    st = state(LOCAL, allowed={"conversation": [Authorization(LOCAL.key, 1000)]}, used={LOCAL.key: 1000})
    d = choose_for("conversation", st)
    assert d.kind == "none"
    assert "plafond du jour atteint" in d.reason
    assert "borne basse" in d.reason
    ok = state(LOCAL, allowed={"conversation": [Authorization(LOCAL.key, 1000)]}, used={LOCAL.key: 999})
    assert choose_for("conversation", ok).key == LOCAL.key


def test_regle_d_usage_retient_les_modeles_qui_depensent_pas_le_local() -> None:
    st = state(SUB, LOCAL, allowed={"conversation": auth(SUB.key, LOCAL.key)}, hold="règle de jour franchie (hold)")
    d = choose_for("conversation", st)
    assert d.key == LOCAL.key  # le local ne consomme aucun quota
    assert any("règle d'usage" in ln.why for ln in d.trace if ln.key == SUB.key)
    only_paid = state(SUB, allowed={"conversation": auth(SUB.key)}, hold="pause")
    assert choose_for("conversation", only_paid).kind == "none"


def test_politique_de_donnees_ecarte() -> None:
    st = state(
        SUB, LOCAL, allowed={"conversation": auth(SUB.key, LOCAL.key)},
        refusals={SUB.key: "aucune classe de données déclarée"},
    )
    d = choose_for("conversation", st)
    assert d.key == LOCAL.key
    assert any("politique de données" in ln.why for ln in d.trace)


def test_cle_autorisee_mais_inconnue_de_la_detection_est_ignoree() -> None:
    st = state(LOCAL, allowed={"conversation": auth("registre:inconnu/x", LOCAL.key)})
    d = choose_for("conversation", st)
    assert d.key == LOCAL.key
    ghost = next(ln for ln in d.trace if ln.key == "registre:inconnu/x")
    assert ghost.verdict == "ignoré"
    only_ghost = state(LOCAL, allowed={"conversation": auth("registre:inconnu/x")})
    assert choose_for("conversation", only_ghost).kind == "none"


def test_le_modele_courant_qui_convient_est_garde_meme_si_un_meilleur_rang_est_revenu() -> None:
    st = state(LOCAL, SUB, allowed={"conversation": auth(SUB.key, LOCAL.key)})
    d = choose_for("conversation", st, current=LOCAL.key)
    assert (d.kind, d.key) == ("use", LOCAL.key)
    assert "aucun changement" in d.reason


# ── Repli gardé ─────────────────────────────────────────────────────────────


def test_repli_automatique_vers_un_modele_ni_plus_cher_ni_plus_expose() -> None:
    down = model("hote:abo", cost="abonnement", egress="texte", available=False, reason="limite-atteinte")
    st = state(down, LOCAL, allowed={"conversation": auth(down.key, LOCAL.key)})
    d = choose_for("conversation", st, current=down.key)
    assert (d.kind, d.key) == ("use", LOCAL.key)
    assert d.reason.startswith("repli automatique")


def test_repli_vers_un_modele_de_meme_cout_et_meme_exposition() -> None:
    a = model("hote:a", cost="abonnement", egress="texte", available=False, reason="limite-atteinte")
    b = model("hote:b", cost="abonnement", egress="texte")
    d = choose_for("conversation", state(a, b, allowed={"conversation": auth(a.key, b.key)}), current=a.key)
    assert (d.kind, d.key) == ("use", b.key)


@pytest.mark.parametrize(
    ("current", "candidate", "mot"),
    [
        (model("ollama:c", available=False, reason="hors-ligne"), SUB, "plus coûteux"),
        (model("ollama:c", available=False, reason="hors-ligne"), PAID, "plus coûteux"),
        (model("hote:c", cost="abonnement", egress="texte", available=False, reason="limite-atteinte"), PAID, "plus coûteux"),
        (
            model("ollama:c", available=False, reason="hors-ligne"),
            model("hote:e", cost="gratuit-local", egress="texte"),
            "plus exposé",
        ),
    ],
)
def test_jamais_de_bascule_seule_vers_plus_cher_ou_plus_expose_mais_une_proposition(
    current: ModelAvailability, candidate: ModelAvailability, mot: str
) -> None:
    st = state(current, candidate, allowed={"conversation": auth(current.key, candidate.key)})
    d = choose_for("conversation", st, current=current.key)
    assert d.kind == "propose"
    assert d.key == candidate.key
    assert mot in d.reason
    assert "à confirmer" in d.reason


def test_repli_saute_le_premier_candidat_trop_cher_pour_un_suivant_garde() -> None:
    down = model("hote:abo", cost="abonnement", egress="texte", available=False, reason="limite-atteinte")
    st = state(down, PAID, LOCAL, allowed={"conversation": auth(down.key, PAID.key, LOCAL.key)})
    d = choose_for("conversation", st, current=down.key)
    assert (d.kind, d.key) == ("use", LOCAL.key)


def test_courant_qui_tombe_sans_remplacant_rend_rien() -> None:
    down = model("ollama:c", available=False, reason="hors-ligne")
    d = choose_for("conversation", state(down, allowed={"conversation": auth(down.key)}), current=down.key)
    assert d.kind == "none"
    assert "aucun autre modèle" in d.reason


def test_courant_retire_des_autorisations_est_traite_comme_un_modele_qui_tombe() -> None:
    st = state(LOCAL, SUB, allowed={"conversation": auth(LOCAL.key)})
    d = choose_for("conversation", st, current=SUB.key)
    assert (d.kind, d.key) == ("use", LOCAL.key)  # local : ni plus cher ni plus exposé que l'abonnement
    assert "n'est plus autorisé" in d.reason


def test_courant_inconnu_de_la_detection_n_est_jamais_remplace_seul() -> None:
    st = state(LOCAL, allowed={"conversation": auth(LOCAL.key)})
    d = choose_for("conversation", st, current="registre:fantome/x")
    assert d.kind == "propose"  # impossible de comparer coût et exposition : on propose


# ── Choix manuel ────────────────────────────────────────────────────────────


def test_le_choix_manuel_passe_avant_l_ordre() -> None:
    st = state(LOCAL, SUB, allowed={"conversation": auth(LOCAL.key, SUB.key)})
    d = choose_for("conversation", st, manual=SUB.key)
    assert (d.kind, d.key, d.reason) == ("use", SUB.key, "choix manuel")


def test_choix_manuel_non_autorise_est_refuse() -> None:
    st = state(LOCAL, SUB, allowed={"conversation": auth(LOCAL.key)})
    d = choose_for("conversation", st, manual=SUB.key)
    assert d.kind == "none"
    assert "pas autorisé" in d.reason


def test_choix_manuel_ne_passe_pas_une_garde_de_surete() -> None:
    down = model("hote:abo", cost="abonnement", egress="texte", available=False, reason="limite-atteinte")
    st = state(down, LOCAL, allowed={"conversation": auth(down.key, LOCAL.key)})
    d = choose_for("conversation", st, manual=down.key)
    assert (d.kind, d.key) == ("use", LOCAL.key)  # repli gardé : local n'est pas plus cher
    capped = state(SUB, allowed={"conversation": [Authorization(SUB.key, 10)]}, used={SUB.key: 10})
    assert choose_for("conversation", capped, manual=SUB.key).kind == "none"
    refused = state(SUB, allowed={"conversation": auth(SUB.key)}, refusals={SUB.key: "classe interdite"})
    assert choose_for("conversation", refused, manual=SUB.key).kind == "none"


def test_choix_manuel_trop_leger_est_utilise_avec_avertissement() -> None:
    st = state(LIGHT, LOCAL, allowed={"brainstorm": auth(LIGHT.key, LOCAL.key)})
    d = choose_for("brainstorm", st, manual=LIGHT.key)
    assert (d.kind, d.key) == ("use", LIGHT.key)
    assert "trop léger pour brainstormer" in d.warning


# ── Capacité minimale par geste ─────────────────────────────────────────────


def test_modele_trop_leger_est_ecarte_sans_modele_courant() -> None:
    st = state(LIGHT, LOCAL, allowed={"brainstorm": auth(LIGHT.key, LOCAL.key)})
    d = choose_for("brainstorm", st)
    assert d.key == LOCAL.key
    light = next(ln for ln in d.trace if ln.key == LIGHT.key)
    assert "trop léger pour brainstormer" in light.why


def test_courant_trop_leger_rend_une_proposition_jamais_une_bascule() -> None:
    st = state(LIGHT, LOCAL, allowed={"brainstorm": auth(LIGHT.key, LOCAL.key)})
    d = choose_for("brainstorm", st, current=LIGHT.key)
    assert (d.kind, d.key) == ("propose", LOCAL.key)
    assert "trop léger pour brainstormer" in d.reason


def test_courant_trop_leger_sans_alternative_assez_capable() -> None:
    st = state(LIGHT, allowed={"brainstorm": auth(LIGHT.key)})
    d = choose_for("brainstorm", st, current=LIGHT.key)
    assert d.kind == "none"
    assert "trop léger pour brainstormer" in d.reason


def test_palier_inconnu_ne_prouve_pas_la_capacite() -> None:
    unknown = model("ollama:inconnu", tier="")
    d = choose_for("redaction", state(unknown, allowed={"redaction": auth(unknown.key)}))
    assert d.kind == "none"
    assert "palier inconnu" in d.reason


def test_conversation_n_exige_aucun_palier_par_defaut() -> None:
    unknown = model("ollama:inconnu", tier="")
    d = choose_for("conversation", state(unknown, allowed={"conversation": auth(unknown.key)}))
    assert (d.kind, d.key) == ("use", unknown.key)
    assert DEFAULT_MIN["conversation"] == MinCapability()


def test_capacites_exigees_fenetre_et_outils() -> None:
    caps = (("context_window", 8000, "declared"), ("tools", True, "probed"))
    capable = model("ollama:cap", caps=caps)
    st = state(capable, allowed={"autres": auth(capable.key)})
    assert choose_for("autres", st, min_capability=MinCapability(context_window=8000, needs=("tools",))).kind == "use"
    short = choose_for("autres", st, min_capability=MinCapability(context_window=16000))
    assert short.kind == "none"
    assert "fenêtre de contexte trop courte" in short.reason
    no_vision = choose_for("autres", st, min_capability=MinCapability(needs=("vision",)))
    assert "« vision » non établie" in no_vision.reason
    no_window = state(LOCAL, allowed={"autres": auth(LOCAL.key)})
    assert "fenêtre de contexte inconnue" in choose_for("autres", no_window, min_capability=MinCapability(context_window=1)).reason


def test_palier_exige_explicite_prime_sur_le_defaut() -> None:
    st = state(LOCAL, allowed={"conversation": auth(LOCAL.key)})
    d = choose_for("conversation", st, min_capability=MinCapability(tier="strong"))
    assert d.kind == "none"
    assert "trop léger pour converser" in d.reason


def test_doublons_d_autorisation_ne_comptent_qu_une_fois() -> None:
    st = state(LOCAL, allowed={"conversation": auth(LOCAL.key, LOCAL.key)})
    d = choose_for("conversation", st)
    assert [ln.key for ln in d.trace] == [LOCAL.key]


def test_la_decision_se_serialise_sans_perte_de_trace() -> None:
    st = state(LIGHT, LOCAL, allowed={"brainstorm": auth(LIGHT.key, LOCAL.key)})
    payload: dict[str, Any] = choose_for("brainstorm", st).to_dict()
    assert payload["kind"] == "use"
    assert [ln["verdict"] for ln in payload["trace"]] == ["écarté", "retenu"]
    assert set(USAGES) == {"conversation", "brainstorm", "redaction", "autres"}
