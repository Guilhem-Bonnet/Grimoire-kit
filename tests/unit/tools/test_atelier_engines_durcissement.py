"""Durcissement des moteurs à modèle (lot B2) : destination liée à l'autorisation, échéance totale, bornes de comptage, marqueur.

Faux transports et fausses horloges seulement : aucun réseau, aucun binaire de la machine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.core import cadrage_conversation_moteur_vue as mv
from grimoire.core.cadrage_conversation_engine import EntreeClassement, Garde, MoteurSansModele
from grimoire.core.cadrage_conversation_moteur_vue import vue_moteur
from grimoire.providers import availability as av
from grimoire.providers.availability import detect
from grimoire.providers.direct_use import Authorization
from grimoire.providers.direct_use_settings import Settings
from grimoire.tools.atelier_engines import fabrique as fab
from grimoire.tools.atelier_engines import transport as tr

from .test_atelier_engines import (  # noqa: F401 — les fixtures autouse et de projet sont réutilisées
    CLAUDE,
    KEY,
    LOCAL_FAST,
    STANDARD,
    Faux,
    Reponse,
    _hermetique,
    compose,
    inventaire,
    registre,
    reglages,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / STANDARD).mkdir(parents=True)
    (root / STANDARD / "llm-provider-registry.yaml").write_text(registre(), encoding="utf-8")
    return root

CLASSER = EntreeClassement("les mainteneurs", "amorce")
OK = (200, json.dumps({"choices": [{"message": {"content": json.dumps({"classe": "reponse"})}}]}).encode())


def passerelle(base: str, *, type_: str = "litellm", ident: str = "mygw") -> str:
    return f"""\
$schema: "grimoire-llm-provider-registry/v1"
routing:
  default_data_class: "public-docs"
providers:
  - id: "{ident}"
    enabled: true
    provider_type: "{type_}"
    currency: "{'local' if type_ == 'local' else 'api'}"
    base_url: "{base}"
    invocation: "serveur {{prompt}}"
    models:
      - {{id: "gw-model", tier: "strong"}}
    data_policy: {{allowed_data_classes: ["public-docs"], forbidden_data_classes: ["secrets"]}}
"""


def ecrire(root: Path, texte: str) -> None:
    (root / STANDARD / "llm-provider-registry.yaml").write_text(texte, encoding="utf-8")


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.providers.secrets.env_for_provider", lambda pid: {"MYGW_API_KEY": KEY, "LITELLM_API_KEY": KEY} if pid in ("mygw", "litellm") else {})
    monkeypatch.setattr("grimoire.providers.availability.list_secrets", lambda: ())
    monkeypatch.setenv("MYGW_API_KEY", KEY)  # la détection ne lit que la PRÉSENCE de la clé
    monkeypatch.setenv("LITELLM_API_KEY", KEY)


def autorise(inv: av.Inventory) -> Settings:
    row = next(m for m in inv.models if m.provider_id in ("mygw", "litellm"))
    return Settings({"conversation": (Authorization(row.key, None, row.fingerprint),), "brainstorm": (), "redaction": (), "autres": ()})


A, B = "https://autorise.exemple.test", "https://autre.exemple.test"


# ── 1. La destination est celle que l'autorisation désigne ─────────────────


@pytest.mark.parametrize(("type_", "cle"), [("litellm", True), ("local", False)])
def test_un_registre_reecrit_dans_la_fenetre_du_cache_ne_recoit_aucune_requete(
    project: Path, secrets: None, type_: str, cle: bool,
) -> None:
    base_a, base_b = (A, B) if type_ == "litellm" else ("http://127.0.0.1:8080", "http://127.0.0.1:9090")
    ecrire(project, passerelle(base_a, type_=type_))
    vieille = detect(project, which=lambda n: f"/usr/bin/{n}", fetch=lambda *_a: b"{}", acp=lambda _p, _r: (True, ""), use_cache=False)
    sets = autorise(vieille)
    ecrire(project, passerelle(base_b, type_=type_))  # réécrit après l'autorisation ; la détection en cache est encore l'ancienne
    faux = Faux(OK)
    rendu = fab.composer(project, "un", detecter=lambda _r: vieille, transport=faux, reglages=sets)
    g = Garde(rendu.moteur, presentation=rendu.presentation)
    assert g.classer_reponse(CLASSER) == MoteurSansModele().classer_reponse(CLASSER)
    assert faux.appels == [] and type(rendu.moteur) is MoteurSansModele
    assert "a changé depuis ton autorisation" in vue_moteur(rendu.presentation, g.id, "")["raison"]


def test_un_registre_reecrit_entre_la_decision_et_la_construction_ne_recoit_aucune_requete(project: Path, secrets: None) -> None:
    ecrire(project, passerelle(A))
    inv = detect(project, which=lambda n: f"/usr/bin/{n}", fetch=lambda *_a: b"{}", acp=lambda _p, _r: (True, ""), use_cache=False)
    sets = autorise(inv)

    def detecter(root: Path) -> av.Inventory:
        ecrire(root, passerelle(B))  # injection déterministe : entre la lecture de la détection et celle du registre
        return inv

    faux = Faux(OK)
    rendu = fab.composer(project, "un", detecter=detecter, transport=faux, reglages=sets)
    Garde(rendu.moteur, presentation=rendu.presentation).classer_reponse(CLASSER)
    assert faux.appels == [] and "a changé depuis ton autorisation" in rendu.presentation.usages[0].raison


def test_un_registre_inchange_appelle_la_destination_autorisee_avec_la_cle(project: Path, secrets: None) -> None:
    ecrire(project, passerelle(A))
    inv = detect(project, which=lambda n: f"/usr/bin/{n}", fetch=lambda *_a: b"{}", acp=lambda _p, _r: (True, ""), use_cache=False)
    faux = Faux(OK)
    rendu = fab.composer(project, "un", detecter=lambda _r: inv, transport=faux, reglages=autorise(inv))
    assert Garde(rendu.moteur, presentation=rendu.presentation).classer_reponse(CLASSER) == "reponse"
    assert faux.appels[0][0] == f"{A}/v1/chat/completions" and faux.appels[0][1]["Authorization"] == f"Bearer {KEY}"


# ── 6. Vue et règles alignées ───────────────────────────────────────────────


def test_un_agent_en_tete_sans_adaptateur_laisse_parler_le_suivant_et_la_vue_le_dit(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grimoire.hosts.acp_hosts.acp_availability", lambda _p: (True, ""))
    inv = inventaire(project)
    hote = next(k for k in inv.by_key() if k.startswith("hote:"))
    faux = Faux((200, json.dumps({"message": {"content": json.dumps({"classe": "ne_sait_pas"})}}).encode()))
    rendu = compose(project, inv, reglages(inv, [hote, LOCAL_FAST]), faux)
    assert Garde(rendu.moteur, presentation=rendu.presentation).classer_reponse(CLASSER) == "ne_sait_pas"
    raison = vue_moteur(rendu.presentation, rendu.moteur.id, "")["raison"]
    assert "aucun adaptateur" in raison and "jamais appelé" in raison


def test_une_passerelle_d_identifiant_litellm_est_admise_comme_par_la_sonde(project: Path, secrets: None) -> None:
    ecrire(project, passerelle(A, ident="litellm"))
    inv = detect(project, which=lambda n: f"/usr/bin/{n}", fetch=lambda *_a: b"{}", acp=lambda _p, _r: (True, ""), use_cache=False)
    faux = Faux(OK)
    rendu = fab.composer(project, "un", detecter=lambda _r: inv, transport=faux, reglages=autorise(inv))
    assert Garde(rendu.moteur, presentation=rendu.presentation).classer_reponse(CLASSER) == "reponse" and faux.appels


# ── 2. Échéance totale ──────────────────────────────────────────────────────


def test_une_reponse_au_goutte_a_goutte_depasse_l_echeance_totale(monkeypatch: pytest.MonkeyPatch) -> None:
    horloge = [0.0]
    monkeypatch.setattr(tr.time, "monotonic", lambda: horloge[0])
    etat = {"ferme": False, "lus": 0}

    class Goutte:
        status = 200

        def read(self, _n: int = -1) -> bytes:
            horloge[0] += 0.4  # chaque morceau arrive juste avant le délai d'une opération, jamais assez tard pour le déclencher
            etat["lus"] += 1
            return b"x" if etat["lus"] < 1000 else b""

        def __enter__(self) -> Goutte:
            return self

        def __exit__(self, *_a: Any) -> None:
            etat["ferme"] = True

    class Ouvreur:
        def open(self, _req: Any, timeout: float = 0) -> Goutte:
            return Goutte()

    monkeypatch.setattr("urllib.request.build_opener", lambda *_a: Ouvreur())
    with pytest.raises(tr.EngineDelai):
        tr.default_transport("https://api.exemple.test/v1", {}, b"{}", 1.0)
    assert etat["ferme"] is True and etat["lus"] <= 4


# ── 3. Bornes du comptage ───────────────────────────────────────────────────


def test_une_consommation_absurde_ou_nulle_n_est_pas_prise_telle_quelle(project: Path) -> None:
    from grimoire.tools import atelier_model as am

    def brut(entree: int, sortie: int) -> bytes:
        return json.dumps({"id": "m", "content": [{"type": "text", "text": "bonjour"}], "usage": {"input_tokens": entree, "output_tokens": sortie}}).encode()

    am.account_response(project, "un", "m", brut(10**9, 3))
    absurde = am.usage(project, "un")["day"]
    assert 0 < absurde < 10_000  # une estimation plancher, pas le milliard annoncé
    avant = absurde
    warning = am.account_response(project, "un", "m", brut(0, 0))
    assert am.usage(project, "un")["day"] > avant and "non mesurée" in warning
    avant = am.usage(project, "un")["day"]
    am.account_response(project, "un", "m", brut(100, 5_000_000))
    assert am.usage(project, "un")["day"] - avant < 10_000
    avant = am.usage(project, "un")["day"]
    am.account_response(project, "un", "m", brut(100, 50))
    assert am.usage(project, "un")["day"] - avant == 150  # une consommation plausible est comptée telle quelle


# ── 4. Aucune référence à l'exception d'origine ─────────────────────────────


def test_les_erreurs_du_transport_ne_gardent_aucune_reference_a_l_exception_d_origine() -> None:
    def forge(*_a: Any) -> Any:
        raise RuntimeError(f"échec avec {KEY}")

    with pytest.raises(tr.EngineError) as info:
        tr.post_json(forge, "https://a.test", {}, {}, 1.0)
    assert info.value.__context__ is None and info.value.__cause__ is None and KEY not in repr(info.value)

    def delai(*_a: Any) -> Any:
        raise TimeoutError(KEY)

    with pytest.raises(tr.EngineDelai) as info2:
        tr.post_json(delai, "https://a.test", {}, {}, 1.0)
    assert info2.value.__context__ is None and info2.value.__cause__ is None
    with pytest.raises(tr.EngineSortie) as info3:
        tr.post_json(lambda *_a: (200, b"[[["), "https://a.test", {}, {}, 1.0)
    assert info3.value.__context__ is None and info3.value.__cause__ is None


def test_le_transport_reel_ne_garde_aucune_reference_a_l_erreur_d_origine(monkeypatch: pytest.MonkeyPatch) -> None:
    class Ouvreur:
        def open(self, _req: Any, timeout: float = 0) -> Any:
            raise OSError(KEY)

    monkeypatch.setattr("urllib.request.build_opener", lambda *_a: Ouvreur())
    with pytest.raises(tr.EngineReseau) as info:
        tr.default_transport("https://api.exemple.test/v1", {}, b"{}", 1.0)
    assert info.value.__context__ is None and info.value.__cause__ is None


# ── 5. Marqueur : un compteur par clé ───────────────────────────────────────


def test_deux_appels_simultanes_ne_se_retirent_pas_la_reflexion() -> None:
    cle = ("racine", "chantier")
    with mv.reflexion(cle, 5):
        with mv.reflexion(cle, 5):
            assert mv.reflechit(cle)
        assert mv.reflechit(cle), "le premier appel dure encore"
    assert not mv.reflechit(cle)
