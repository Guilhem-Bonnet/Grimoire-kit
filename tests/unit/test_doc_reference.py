"""La référence du système nodal doit rester collée à ses sources.

Le manuel décrit 7 primitives, 30 contrats, 78 patterns et 52 anti-patterns
qu'aucune main n'écrit : tout est rendu au build depuis le schéma, le module
de primitives et l'export de catalogue. Ces tests sont la porte. Ils vérifient
d'abord qu'elle ferme réellement — un garde qui ne sait pas échouer ne garde
rien — puis que la source et la documentation sont effectivement alignées.
"""

from __future__ import annotations

import copy
import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts/gen_doc_reference.py"


def _load_generator():
    """Importer le générateur sous un nom explicite.

    Le nom compte : sous `runpy.run_path`, mkdocs-gen-files exécute ce même
    fichier avec `__name__ == "<run_path>"`, ce qui déclenche le rendu. Ici on
    veut le module, pas ses effets.
    """
    spec = importlib.util.spec_from_file_location("gen_doc_reference", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


@pytest.fixture(scope="module")
def catalogue() -> dict[str, Any]:
    return gen.load_catalogue()


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return gen.load_schema()


@pytest.fixture(scope="module")
def gloss() -> dict[str, Any]:
    return gen.load_gloss()


@pytest.fixture(scope="module")
def pages() -> dict[str, str]:
    """Rendre la référence en mémoire, telle que le build la produit."""
    rendered: dict[str, str] = {}
    gen.build(lambda path, text: rendered.__setitem__(path, text))
    return rendered


# ── La porte doit pouvoir échouer ──────────────────────────────────────────


def test_gloss_gate_catches_a_field_without_french(schema, gloss):
    """Retirer une glose doit être détecté, pas ignoré."""
    amputated = {k: v for k, v in gloss.items() if k != "/$defs/edge/properties/channel"}
    problems = gen.check_gloss(schema, amputated)
    assert any("channel" in p for p in problems), problems


def test_gloss_gate_catches_an_orphan_entry(schema, gloss):
    """Une glose qui ne correspond à aucun champ doit être détectée aussi.

    C'est le sens qu'on oublie : sans lui, un champ renommé laisse derrière
    lui une glose orpheline que plus rien ne rend, et personne ne le voit.
    """
    extra = dict(gloss)
    extra["/$defs/edge/properties/inexistant"] = {"texte": "champ imaginaire"}
    problems = gen.check_gloss(schema, extra)
    assert any("inexistant" in p for p in problems), problems


def test_gloss_gate_catches_an_empty_entry(schema, gloss):
    hollow = copy.deepcopy(gloss)
    hollow["/$defs/pin/properties/contract"]["texte"] = "   "
    problems = gen.check_gloss(schema, hollow)
    assert any("glose vide" in p for p in problems), problems


def test_catalogue_gate_catches_a_stale_doc_path(catalogue):
    """Un docPath qui ne pointe plus là où la page est rendue doit échouer."""
    drifted = copy.deepcopy(catalogue)
    drifted["patterns"][0]["docPath"] = "docs/ailleurs.md#orc-01"
    problems = gen.check_catalogue(drifted)
    assert any("docPath" in p for p in problems), problems


def test_catalogue_gate_catches_a_pattern_without_family(catalogue):
    orphaned = copy.deepcopy(catalogue)
    orphaned["patterns"][0]["family"] = "XXX"
    problems = gen.check_catalogue(orphaned)
    assert any("famille inconnue" in p for p in problems), problems


def test_catalogue_gate_catches_colliding_family_pages(catalogue):
    colliding = copy.deepcopy(catalogue)
    colliding["families"][1]["name"] = colliding["families"][0]["name"]
    problems = gen.check_catalogue(colliding)
    assert any("même nom de page" in p for p in problems), problems


def test_build_refuses_to_render_on_drift(monkeypatch, catalogue):
    """Le rendu s'arrête net plutôt que de publier une référence fausse."""
    drifted = copy.deepcopy(catalogue)
    drifted["patterns"][0]["docPath"] = "nulle-part.md"
    monkeypatch.setattr(gen, "load_catalogue", lambda: drifted)
    with pytest.raises(SystemExit):
        gen.build(lambda path, text: None)


# ── Et la porte doit être verte sur l'état réel du dépôt ───────────────────


def test_every_schema_field_has_french(schema, gloss):
    assert gen.check_gloss(schema, gloss) == []


def test_catalogue_is_reachable(catalogue):
    assert gen.check_catalogue(catalogue) == []


def test_every_pattern_has_an_anchor(catalogue, pages):
    """Chaque pattern doit être atteignable là où son docPath l'annonce."""
    families = {f["id"]: f for f in catalogue["families"]}
    missing = []
    for pattern in catalogue["patterns"]:
        slug = gen.family_slug(families[pattern["family"]])
        page = pages.get(f"{gen.BASE}/patterns/{slug}.md", "")
        if f"{{: #{pattern['id'].lower()} }}" not in page:
            missing.append(pattern["id"])
    assert not missing, f"patterns sans ancre rendue : {missing}"


def test_every_contract_has_a_page(catalogue, pages):
    missing = [
        c["id"] for c in catalogue["contracts"]
        if f"{gen.BASE}/contrats/{c['id']}.md" not in pages
    ]
    assert not missing, f"contrats sans page : {missing}"


def test_every_primitive_has_a_page(pages):
    primitives, _ = gen.load_primitives()
    missing = [n for n in primitives if f"{gen.BASE}/{n.lower()}.md" not in pages]
    assert not missing, f"primitives sans page : {missing}"


def test_every_palette_case_maps_to_a_real_primitive(pages):
    primitives, xxl = gen.load_primitives()
    unknown = sorted({m["primitive"] for m in xxl.values()} - set(primitives))
    assert not unknown, f"cases de palette rattachées à une primitive inconnue : {unknown}"
    palette = pages[f"{gen.BASE}/palette.md"]
    absent = [case for case in xxl if f"`{case}`" not in palette]
    assert not absent, f"cases absentes de la page palette : {absent}"


def test_every_anti_pattern_has_an_anchor(catalogue, pages):
    page = pages[f"{gen.BASE}/anti-patterns.md"]
    missing = [
        a["id"] for a in catalogue["antiPatterns"]
        if f"{{: #{a['id'].lower()} }}" not in page
    ]
    assert not missing, f"anti-patterns sans ancre : {missing}"


def test_summary_lists_every_generated_page(pages):
    """Une page hors du sommaire serait rendue mais injoignable."""
    summary = pages[f"{gen.BASE}/SUMMARY.md"]
    linked = set(re.findall(r"\]\(([^)]+)\)", summary))
    expected = {
        path[len(gen.BASE) + 1:]
        for path in pages
        if path.endswith(".md") and not path.endswith("SUMMARY.md")
    }
    assert expected - linked == set(), f"pages hors sommaire : {sorted(expected - linked)}"


# ── Les pages écrites à la main pointent dans la référence générée ─────────


def _broken_anchor_links(
    pages: dict[str, str], sources: list[tuple[Path, str]]
) -> list[str]:
    """Relever les liens ancrés qui ne résolvent pas dans la référence rendue.

    `sources` est une liste de `(chemin, contenu)` : le chemin sert à résoudre
    les liens relatifs, le contenu à les trouver. Passer les deux séparément
    permet de vérifier le garde lui-même sur une page fabriquée.
    """
    broken = []
    for path, text in sources:
        for target, anchor in re.findall(r"\]\(([^)#]*)#([^)\s]+)\)", text):
            if not target.endswith(".md"):
                continue
            key = (path.parent / target).resolve()
            try:
                key = key.relative_to(ROOT / "docs").as_posix()
            except ValueError:
                continue
            if not key.startswith(f"{gen.BASE}/"):
                continue
            rendered = pages.get(key)
            if rendered is None:
                broken.append(f"{path.name} → {key} (page absente)")
            elif f"{{: #{anchor} }}" not in rendered:
                broken.append(f"{path.name} → {key}#{anchor} (ancre absente)")
    return broken


def _handwritten_nodal_sources() -> list[tuple[Path, str]]:
    root = ROOT / "docs/nodal"
    return [
        (p, p.read_text(encoding="utf-8"))
        for p in sorted(root.rglob("*.md"))
        if "reference" not in p.parts
    ]


def test_the_anchor_gate_can_fail(pages):
    """Une ancre inventée doit être vue, sinon le garde ne garde rien."""
    fake = ROOT / "docs/nodal/concepts/faux.md"
    broken = _broken_anchor_links(
        pages, [(fake, "Voir [ceci](../reference/format-fichier.md#inexistant).")]
    )
    assert broken and "inexistant" in broken[0], broken


def test_the_anchor_gate_sees_a_missing_page(pages):
    fake = ROOT / "docs/nodal/concepts/faux.md"
    broken = _broken_anchor_links(
        pages, [(fake, "Voir [ceci](../reference/jamais-rendue.md#node).")]
    )
    assert broken and "page absente" in broken[0], broken


def test_handwritten_anchors_into_the_reference_resolve(pages):
    """`mkdocs --strict` valide les fichiers cibles, pas les ancres.

    Les pages de concepts pointent des sections précises de la référence
    générée (`format-fichier.md#gatepolicy`, `patterns/...#orc-01`). Renommer
    une primitive ou un pattern casserait ces liens en silence : le fichier
    existerait toujours, l'ancre non. Ce test ferme ce trou.
    """
    broken = _broken_anchor_links(pages, _handwritten_nodal_sources())
    assert not broken, "liens vers la référence à réparer :\n  " + "\n  ".join(broken)
