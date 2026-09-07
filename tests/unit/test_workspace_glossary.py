"""Le glossaire est la seule source, et rien ne peut citer un terme sans entrée.

Spécification §3.2 : « Contenu : une seule source, le glossaire du kit
(``framework/glossary.yaml``). La documentation en dérive ; un test refuse un
terme cité sans entrée. » Critère 4 : les définitions viennent du glossaire.

Le défaut que ce module ferme est le plus banal des défauts de documentation :
une infobulle qui affiche un identifiant brut parce que personne n'a écrit
l'entrée, ou pire, une définition recopiée dans le HTML qui diverge de celle du
glossaire six mois plus tard.

Le contrôle sur le DOM rendu — donc y compris les termes qu'un module d'espace
poserait à l'exécution — est dans ``tests/e2e/test_workspace_shell.py`` via
``glossary.missingTerms()``. Celui-ci est statique et tourne partout.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GLOSSARY = ROOT / "framework" / "glossary.yaml"
WORKSPACE = ROOT / "web" / "workspace"

#: Les trois façons dont la coque cite un concept.
_CITATIONS = (
    re.compile(r'data-term="([a-z0-9-]+)"'),  # HTML
    re.compile(r"\bterm:\s*'([a-z0-9-]+)'"),  # tables déclaratives de shell.js
    re.compile(r"\.dataset\.term\s*=\s*'([a-z0-9-]+)'"),  # pose à l'exécution
)

#: Les champs que la spec exige de chaque entrée.
REQUIRED = ("id", "nom", "définition", "raccourci", "termes", "doc")


@pytest.fixture(scope="module")
def glossary() -> dict[str, Any]:
    return yaml.safe_load(GLOSSARY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entries(glossary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in glossary["entries"]}


def _cited() -> dict[str, set[str]]:
    """`fichier → identifiants cités`, pour que l'échec nomme le coupable."""
    found: dict[str, set[str]] = {}
    for source in sorted([*WORKSPACE.rglob("*.html"), *WORKSPACE.rglob("*.js")]):
        text = source.read_text(encoding="utf-8")
        ids: set[str] = set()
        for pattern in _CITATIONS:
            ids.update(pattern.findall(text))
        if ids:
            found[str(source.relative_to(WORKSPACE))] = ids
    return found


# ── Le fichier lui-même ─────────────────────────────────────────────────────


def test_le_glossaire_couvre_au_moins_les_quinze_concepts_de_la_spec(
    entries: dict[str, dict[str, Any]],
) -> None:
    """La spec nomme les six espaces, les mécaniques et le vocabulaire gouverné."""
    assert len(entries) >= 15

    incontournables = {
        "espace-de-travail",
        "toile",
        "dock",
        "inspecteur",
        "explorateur",
        "palette-de-commandes",
        "mode-concentration",
        "densite",
        "infobulle",
        "tache",
        "porte-de-preuve",
        "evidence-pack",
        "trace",
        "etage",
        "override",
    }
    assert incontournables <= set(entries), sorted(incontournables - set(entries))


@pytest.mark.parametrize("field", REQUIRED)
def test_chaque_entree_porte_tous_les_champs_declares(entries: dict[str, dict[str, Any]], field: str) -> None:
    """Une bulle affiche nom, définition et raccourci : un champ absent est un trou."""
    missing = [key for key, entry in entries.items() if field not in entry]

    assert not missing, f"champ « {field} » absent de : {missing}"


def test_chaque_definition_tient_en_une_phrase(entries: dict[str, dict[str, Any]]) -> None:
    """Spec §3.2 : « définition d'une phrase ». Une bulle n'est pas un article.

    Le seuil est large — 320 caractères — parce qu'il ne s'agit pas de compter
    les mots mais d'empêcher qu'un paragraphe s'installe dans une infobulle.
    """
    trop_long = {
        key: len(" ".join(entry["définition"].split()))
        for key, entry in entries.items()
        if len(" ".join(entry["définition"].split())) > 320
    }

    assert not trop_long, f"définitions trop longues : {trop_long}"


def test_les_identifiants_sont_stables_et_citables(entries: dict[str, dict[str, Any]]) -> None:
    """L'id est ce que le HTML écrit dans `data-term` : il doit rester ASCII."""
    mauvais = [key for key in entries if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", key)]

    assert not mauvais, f"identifiants non citables : {mauvais}"


def test_aucun_terme_lie_ne_pointe_dans_le_vide(entries: dict[str, dict[str, Any]]) -> None:
    """Un terme lié ouvre une bulle enfant : s'il n'existe pas, la bulle est vide."""
    casses = {
        key: [t for t in entry["termes"] if t not in entries]
        for key, entry in entries.items()
        if any(t not in entries for t in entry["termes"])
    }

    assert not casses, f"termes liés inconnus : {casses}"


def test_aucune_entree_ne_se_cite_elle_meme(entries: dict[str, dict[str, Any]]) -> None:
    """La pile d'infobulles est bornée à trois niveaux ; une boucle la sature."""
    boucles = [key for key, entry in entries.items() if key in entry["termes"]]

    assert not boucles, f"entrées auto-référentes : {boucles}"


def test_chaque_doc_citee_existe(entries: dict[str, dict[str, Any]]) -> None:
    """Le lien « Documentation · F1 » d'une bulle doit mener quelque part."""
    manquantes = {
        key: entry["doc"]
        for key, entry in entries.items()
        if entry["doc"] and not (ROOT / "docs" / entry["doc"]).is_file()
    }

    assert not manquantes, f"pages de documentation absentes : {manquantes}"


# ── Le contrat avec l'interface ─────────────────────────────────────────────


def test_la_coque_cite_reellement_des_termes() -> None:
    """Un test de couverture qui ne couvre rien passe toujours.

    Si personne ne cite plus de terme, ce n'est pas que tout va bien : c'est que
    les infobulles ont disparu de la coque.
    """
    cited = _cited()

    assert cited, "aucun data-term dans web/workspace/ : les infobulles ont disparu"
    assert sum(len(ids) for ids in cited.values()) >= 10


def test_aucun_terme_cite_par_l_interface_n_est_absent_du_glossaire(
    entries: dict[str, dict[str, Any]],
) -> None:
    """C'est le test que la spécification demande, mot pour mot.

    Il échoue en nommant le fichier et le terme : le remède est d'écrire
    l'entrée, jamais de retirer la citation.
    """
    offenders = {source: sorted(ids - set(entries)) for source, ids in _cited().items() if ids - set(entries)}

    assert not offenders, (
        "termes cités sans entrée au glossaire — ajoutez-les à framework/glossary.yaml :\n  "
        + "\n  ".join(f"{source} → {', '.join(ids)}" for source, ids in offenders.items())
    )


def test_chaque_etage_de_la_vue_source_a_son_entree(entries: dict[str, dict[str, Any]]) -> None:
    """Les étages sont nommés côté Python ; leur définition doit exister ici.

    L'interface cite `tier.term` tel que l'API le donne : un étage dont le terme
    n'aurait pas d'entrée afficherait une bulle vide sans que le contrôle
    statique du HTML le voie.
    """
    from grimoire.tools.workspace_api import TIERS

    manquants = [tier["term"] for tier in TIERS if tier["term"] not in entries]

    assert not manquants, f"étages sans entrée au glossaire : {manquants}"


# ── Le glossaire est un fichier du kit ──────────────────────────────────────


def test_le_glossaire_est_livre_par_la_wheel() -> None:
    """Sans ça, les infobulles marchent en dépôt de développement et nulle part ailleurs.

    ``framework/`` est force-included dans la wheel (pyproject.toml), donc le
    fichier voyage — mais seulement s'il est bien sous ``framework/``.
    """
    assert GLOSSARY.is_file()
    assert GLOSSARY.parent.name == "framework"


def test_le_glossaire_est_au_catalogue_des_digests() -> None:
    """``framework/`` est un SHIPPED_ROOT de scripts/gen-kit-hashes.py.

    Un fichier livré absent du catalogue est lu comme une personnalisation du
    projet par ``grimoire migrate``, et gelé hors de toutes les mises à jour
    suivantes — le dégât apparaît des mois plus tard, sous la forme « le kit ne
    se met plus à jour ».
    """
    import hashlib
    import json

    catalog = json.loads((ROOT / "registry" / "kit-file-hashes.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(GLOSSARY.read_bytes()).hexdigest()

    assert digest in catalog["digests"], (
        "glossaire absent du catalogue — régénérez-le : python scripts/gen-kit-hashes.py"
    )
    assert catalog["digests"][digest]["path"] == "framework/glossary.yaml"


def test_le_glossaire_se_charge_sans_pyyaml(monkeypatch, tmp_path) -> None:
    """Une installation nue de la wheel n'a pas PyYAML : le glossaire doit
    passer par le chargeur du kit (ruamel, dépendance déclarée). Reproduit
    #292 : `import yaml` faisait tomber les six espaces."""
    import sys

    from grimoire.tools.workspace_api import glossary_view

    monkeypatch.setitem(sys.modules, "yaml", None)
    view = glossary_view(tmp_path)
    assert "error" not in view
    assert view["count"] > 0


def test_aucun_import_pyyaml_direct_dans_le_serveur_de_la_vue() -> None:
    """Garde de #292 : les modules servis à une installation nue ne dépendent
    pas de PyYAML ; ils passent par `grimoire.tools._common.load_yaml`."""
    from pathlib import Path

    import grimoire.missions.plans_registry as plans
    import grimoire.tools.workspace_api as api

    for module in (api, plans):
        text = Path(module.__file__).read_text(encoding="utf-8")
        assert "import yaml" not in text, module.__name__
