"""L'adaptateur Gemini de la conversation de cadrage : FAUX transport seulement, aucun réseau, aucune clé réelle."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.cadrage_conversation_engine import EntreeClassement, Garde, MoteurSansModele
from grimoire.core.cadrage_conversation_moteur_vue import vue_moteur
from grimoire.providers import availability as av
from grimoire.providers.availability import detect
from grimoire.providers.direct_use import Authorization
from grimoire.providers.direct_use_settings import Settings
from grimoire.providers.secrets import provider_key_var, remove_secret, set_secret
from grimoire.tools.atelier_engines import fabrique as fab
from grimoire.tools.atelier_engines.gemini import GeminiBackend
from grimoire.tools.atelier_engines.transport import EngineRefus, EngineSortie

STANDARD = Path("_grimoire/standard")
KEY = "cle-factice-gemini-test"
CLASSER = EntreeClassement("les mainteneurs", "amorce")
URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-4-neon:generateContent"
Reponse = tuple[int, bytes]


def registre(ident: str = "google-gemini", extra: str = "") -> str:
    return f"""\
$schema: "grimoire-llm-provider-registry/v1"
routing:
  default_data_class: "public-docs"
providers:
  - id: "{ident}"
    enabled: true
    provider_type: "hosted"
    currency: "api"
    invocation: "gemini -p {{prompt}}"
{extra}    models:
      - {{id: "gemini-4-neon", tier: "strong"}}
    data_policy: {{allowed_data_classes: ["public-docs"], forbidden_data_classes: ["secrets"]}}
"""


def gemini_dit(objet: Any, *, brut: str | None = None, fin: str = "STOP") -> Reponse:
    texte = brut if brut is not None else json.dumps(objet)
    corps = {"candidates": [{"content": {"role": "model", "parts": [{"text": texte}]}, "finishReason": fin}], "usageMetadata": {}}
    return 200, json.dumps(corps).encode()


class Faux:
    def __init__(self, reponse: Reponse | Exception) -> None:
        self.reponse = reponse
        self.appels: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> Reponse:
        self.appels.append((url, headers, json.loads(body)))
        if isinstance(self.reponse, Exception):
            raise self.reponse
        return self.reponse


@pytest.fixture(autouse=True)
def _hermetique(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    vide = tmp_path / "bin-vide"
    vide.mkdir()
    monkeypatch.setenv("PATH", str(vide))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for nom in ("GEMINI_API_KEY", "GOOGLE_GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(nom, raising=False)  # l'environnement n'est JAMAIS lu : seul le magasin du kit porte la clé
    monkeypatch.setattr("shutil.which", lambda _n, *a, **k: None)
    av.clear_cache()
    yield
    av.clear_cache()


def ecrire(root: Path, texte: str) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / STANDARD / "llm-provider-registry.yaml").write_text(texte, encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    ecrire(root, registre())
    poser("google-gemini")  # exactement la documentation : grimoire providers set-key google-gemini GOOGLE_GEMINI_API_KEY
    return root


def poser(ident: str, valeur: str = KEY) -> None:
    """La clé posée comme le dit la documentation : ``grimoire providers set-key <id> <ID>_API_KEY``."""
    set_secret(ident, provider_key_var(ident), valeur)


def inventaire(root: Path) -> av.Inventory:
    return detect(root, which=lambda n: f"/usr/bin/{n}", fetch=lambda *_a: b"{}", acp=lambda _p, _r: (True, ""), use_cache=False)


def cle_ligne(inv: av.Inventory) -> str:
    return next(k for k in inv.by_key() if k.endswith("/gemini-4-neon"))


def autorise(inv: av.Inventory, *, usages: tuple[str, ...] = ("conversation",)) -> Settings:
    ligne = inv.by_key()[cle_ligne(inv)]
    auth = (Authorization(ligne.key, None, ligne.fingerprint),)
    return Settings({"conversation": auth if "conversation" in usages else (), "brainstorm": auth if "brainstorm" in usages else (),
                     "redaction": (), "autres": ()})


def compose(root: Path, inv: av.Inventory, settings: Settings, faux: Faux | None) -> Any:
    return fab.composer(root, "un", detecter=lambda _r: inv, transport=faux, reglages=settings)


def garde(rendu: Any) -> Garde:
    return Garde(rendu.moteur, presentation=rendu.presentation)


# ── La requête et la réponse valide ─────────────────────────────────────────


@pytest.mark.parametrize("ident", ["google-gemini", "gemini", "google"])
def test_la_requete_est_construite_pour_l_adresse_officielle_avec_la_cle_en_en_tete(project: Path, ident: str) -> None:
    ecrire(project, registre(ident))
    poser(ident)
    inv = inventaire(project)
    faux = Faux(gemini_dit({"classe": "reponse"}))
    rendu = compose(project, inv, autorise(inv), faux)
    vue = vue_moteur(rendu.presentation, garde(rendu).id, rendu.moteur.libelle)
    assert (vue["local"], vue["sortie_de_texte"], vue["libelle"]) == (False, True, "gemini-4-neon"), vue["raison"]
    assert garde(rendu).classer_reponse(CLASSER) == "reponse"
    url, headers, payload = faux.appels[0]
    assert url == URL and "?" not in url and KEY not in url
    assert headers["x-goog-api-key"] == KEY and headers["content-type"] == "application/json"
    config = payload["generationConfig"]
    assert config["responseMimeType"] == "application/json" and config["responseJsonSchema"]["type"] == "object"
    assert config["maxOutputTokens"] > 0 and "les mainteneurs" in payload["contents"][0]["parts"][0]["text"]
    assert payload["systemInstruction"]["parts"][0]["text"] and KEY not in json.dumps(payload)


def test_les_parties_de_reflexion_sont_ignorees_et_les_textes_joints() -> None:
    corps = {"candidates": [{"content": {"parts": [
        {"text": "ruminer", "thought": True}, {"text": '{"classe": '}, {"text": '"reponse"}'}, {"inlineData": {}}]},
        "finishReason": "STOP"}]}
    faux = Faux((200, json.dumps(corps).encode()))
    texte, brut = GeminiBackend("gemini-4-neon", KEY, timeout_s=2.0, transport=faux).completer("s", "u", {"type": "object"}, 50)
    assert texte == '{"classe": "reponse"}' and brut


def test_le_client_refuse_les_cles_et_modeles_impropres_et_ne_dit_pas_la_cle() -> None:
    for cle in ("", "clé avec espace", "a b", "é-accent"):
        with pytest.raises(EngineRefus):
            GeminiBackend("gemini-4-neon", cle, timeout_s=1.0)
    for modele in ("", "-x", "a/b", "a b", "a?key=x", "m" * 65, "models/gemini"):
        with pytest.raises(EngineRefus):
            GeminiBackend(modele, KEY, timeout_s=1.0)
    client = GeminiBackend("gemini-4-neon", KEY, timeout_s=1.0)
    assert KEY not in repr(client) and "gemini-4-neon" in repr(client) and client.url == URL


# ── Sorties hostiles : repli sans modèle ────────────────────────────────────

HOSTILES: list[tuple[str, Reponse]] = [
    ("json invalide", gemini_dit(None, brut="Bien sûr ! reponse")),
    ("champ en trop", gemini_dit({"classe": "reponse", "ecrire": "rm -rf"})),
    ("classe hors liste", gemini_dit({"classe": "oui"})),
    ("sortie trop longue", gemini_dit(None, brut='{"classe": "reponse", "x": "' + "a" * 30_000 + '"}')),
    ("texte enorme", (200, b'{"candidates": "' + b"a" * 300_000 + b'"}')),
    ("coupee", gemini_dit({"classe": "reponse"}, fin="MAX_TOKENS")),
    ("bloquee", (200, b'{"promptFeedback": {"blockReason": "SAFETY"}}')),
    ("sans candidat", (200, b'{"candidates": []}')),
    ("sans parties", (200, b'{"candidates": [{"content": {}, "finishReason": "STOP"}]}')),
    ("texte vide", gemini_dit(None, brut="  ")),
    ("pas du json", (200, b"nope")),
    ("erreur du service", (429, b"{}")),
    ("liste", (200, b"[]")),
]


@pytest.mark.parametrize(("nom", "reponse"), HOSTILES, ids=[h[0] for h in HOSTILES])
def test_une_sortie_hostile_retombe_sur_les_regles(project: Path, nom: str, reponse: Reponse) -> None:
    inv = inventaire(project)
    g = garde(compose(project, inv, autorise(inv), Faux(reponse)))
    assert g.classer_reponse(CLASSER) == MoteurSansModele().classer_reponse(CLASSER)
    assert len(g.notes) == 1 and "je reprends sans modèle" in g.notes[0]


def test_la_reponse_malformee_est_une_erreur_de_sortie() -> None:
    client = GeminiBackend("gemini-4-neon", KEY, timeout_s=1.0, transport=Faux((200, b'{"candidates": [1]}')))
    with pytest.raises(EngineSortie):
        client.completer("s", "u", {"type": "object"}, 10)


# ── La clé n'apparaît nulle part ────────────────────────────────────────────


def test_la_cle_n_apparait_dans_aucune_erreur_note_ou_vue(project: Path, caplog: pytest.LogCaptureFixture) -> None:
    inv = inventaire(project)
    pieges = [
        Faux(RuntimeError(f"échec avec {KEY}")),
        Faux((500, f"erreur {KEY}".encode())),
        Faux(gemini_dit(None, brut=KEY)),
        Faux(gemini_dit({"classe": KEY})),
    ]
    for faux in pieges:
        rendu = compose(project, inv, autorise(inv), faux)
        g = garde(rendu)
        g.classer_reponse(CLASSER)
        montre = json.dumps([g.notes, vue_moteur(rendu.presentation, g.id, ""), repr(rendu.moteur.backends)], default=str)
        assert KEY not in montre and "cle-factice" not in montre
    assert KEY not in caplog.text


# ── Autorisation, clé et destination ────────────────────────────────────────


def test_un_modele_non_autorise_n_est_jamais_appele(project: Path) -> None:
    inv = inventaire(project)
    faux = Faux(gemini_dit({"classe": "reponse"}))
    rendu = compose(project, inv, Settings({"conversation": (), "brainstorm": (), "redaction": (), "autres": ()}), faux)
    assert type(rendu.moteur) is MoteurSansModele
    garde(rendu).classer_reponse(CLASSER)
    assert faux.appels == []


def test_une_autorisation_perimee_n_est_jamais_appelee(project: Path) -> None:
    inv = inventaire(project)
    ligne = inv.by_key()[cle_ligne(inv)]
    sets = Settings({"conversation": (Authorization(ligne.key, None, "0" * 64),), "brainstorm": (), "redaction": (), "autres": ()})
    faux = Faux(gemini_dit({"classe": "reponse"}))
    rendu = compose(project, inv, sets, faux)
    garde(rendu).classer_reponse(CLASSER)
    assert faux.appels == [] and type(rendu.moteur) is MoteurSansModele


def test_sans_cle_aucun_client(project: Path) -> None:
    remove_secret("google-gemini")
    inv = inventaire(project)
    faux = Faux(gemini_dit({"classe": "reponse"}))
    rendu = compose(project, inv, autorise(inv), faux)
    assert type(rendu.moteur) is MoteurSansModele and "Aucune clé" in rendu.presentation.usages[0].raison
    assert faux.appels == []


@pytest.mark.parametrize("ident", ["google-gemini", "gemini", "google"])
def test_une_cle_seulement_dans_l_environnement_n_est_jamais_envoyee(project: Path, monkeypatch: pytest.MonkeyPatch, ident: str) -> None:
    ecrire(project, registre(ident))
    remove_secret("google-gemini")
    for nom in ("GEMINI_API_KEY", "GOOGLE_GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(nom, KEY)  # une variable générique exportée pour un autre service
    inv = inventaire(project)
    faux = Faux(gemini_dit({"classe": "reponse"}))
    rendu = compose(project, inv, autorise(inv), faux)
    garde(rendu).classer_reponse(CLASSER)
    raison = rendu.presentation.usages[0].raison
    assert faux.appels == [] and type(rendu.moteur) is MoteurSansModele
    assert f"grimoire providers set-key {ident} {provider_key_var(ident)}" in raison and KEY not in raison


def test_la_cle_retiree_entre_la_detection_et_la_construction_n_a_pas_de_client(project: Path) -> None:
    inv = inventaire(project)
    remove_secret("google-gemini")
    backend, why = fab._backend(inv.by_key()[cle_ligne(inv)], project, "un", inv, 3.0, None, None)
    assert backend is None and "set-key google-gemini GOOGLE_GEMINI_API_KEY" in why


def test_la_cle_posee_comme_la_documentation_le_dit_est_celle_qui_part(project: Path) -> None:
    poser("google-gemini", "cle-factice-gemini-magasin")
    inv = inventaire(project)
    faux = Faux(gemini_dit({"classe": "reponse"}))
    garde(compose(project, inv, autorise(inv), faux)).classer_reponse(CLASSER)
    assert faux.appels[0][1]["x-goog-api-key"] == "cle-factice-gemini-magasin"


@pytest.mark.parametrize("extra", ['    base_url: "https://evil.example.com/v1"\n', '    anthropic_base_url: "https://evil.example.com"\n'])
def test_une_adresse_du_registre_reecrite_apres_l_autorisation_ne_recoit_rien(project: Path, extra: str) -> None:
    inv = inventaire(project)
    sets = autorise(inv)
    ecrire(project, registre(extra=extra))  # réécrit après l'autorisation ; la détection en cache est encore l'ancienne
    faux = Faux(gemini_dit({"classe": "reponse"}))
    rendu = compose(project, inv, sets, faux)
    g = garde(rendu)
    assert g.classer_reponse(CLASSER) == MoteurSansModele().classer_reponse(CLASSER)
    assert faux.appels == [] and type(rendu.moteur) is MoteurSansModele
    assert "a changé depuis ton autorisation" in vue_moteur(rendu.presentation, g.id, "")["raison"]


def test_un_fournisseur_gemini_avec_adresse_propre_n_a_pas_d_adaptateur_gemini(project: Path) -> None:
    ecrire(project, registre(extra='    base_url: "https://evil.example.com/v1"\n'))
    inv = inventaire(project)  # autorisée AVEC cette adresse : l'empreinte est bonne, mais la clé ne part pas vers elle
    faux = Faux(gemini_dit({"classe": "reponse"}))
    rendu = compose(project, inv, autorise(inv), faux)
    garde(rendu).classer_reponse(CLASSER)
    assert faux.appels == [] and type(rendu.moteur) is MoteurSansModele


def test_le_modele_absent_du_registre_n_a_pas_de_client(project: Path) -> None:
    inv = inventaire(project)
    ligne = inv.by_key()[cle_ligne(inv)]
    backend, why = fab._backend(replace(ligne, model_id=""), project, "un", inv, 3.0, None, None)
    assert backend is None and "introuvable" in why
    backend, why = fab._backend(replace(ligne, model_id="a/b"), project, "un", inv, 3.0, None, None)
    assert backend is None  # l'empreinte ne désigne plus ce modèle
    backend, why = fab._backend(ligne, project, "un", inv, 3.0, None, None)
    assert isinstance(backend, GeminiBackend) and why == ""


def test_pour_le_brainstorm_aussi(project: Path) -> None:
    inv = inventaire(project)
    faux = Faux(gemini_dit({"classe": "reponse"}))
    rendu = compose(project, inv, autorise(inv, usages=("brainstorm",)), faux)
    assert type(rendu.moteur) is not MoteurSansModele
