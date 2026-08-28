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


# ── Les erreurs du validateur renvoient vers des pages qui existent ────────


def _resolve_doc_target(target: str, pages: dict[str, str]) -> str | None:
    """Traduire une URL du manuel en défaut, ou None si elle résout.

    Les cibles s'écrivent comme mkdocs les sert (`.../format-fichier/#node`).
    On repasse à la source : une page générée est cherchée dans `pages`, une
    page écrite à la main sur le disque.
    """
    path, _, anchor = target.partition("#")
    stem = path.strip("/")
    # mkdocs sert `x.md` sous `x/` et `x/index.md` sous `x/` : une URL en
    # répertoire peut venir des deux, il faut essayer les deux.
    for candidate in (f"{stem}.md", f"{stem}/index.md"):
        if candidate in pages:
            if anchor and f"{{: #{anchor} }}" not in pages[candidate]:
                return f"{target} : ancre absente de la référence générée"
            return None
        handwritten = ROOT / "docs" / candidate
        if handwritten.is_file():
            if anchor and f"#{anchor}" not in handwritten.read_text(encoding="utf-8"):
                return f"{target} : ancre absente de {handwritten.name}"
            return None
    return f"{target} : aucune page correspondante"


def test_validator_doc_targets_resolve(pages):
    """Une erreur qui renvoie vers une page morte est pire qu'une sans lien.

    `grimoire blueprint validate` joint à chaque erreur l'endroit du manuel qui
    l'explique. Renommer une page casserait ces liens sans que rien ne le dise :
    la CLI ne connaît pas le manuel, et le manuel ne connaît pas la CLI.
    """
    from grimoire.cli.cmd_blueprint import DOC_TARGETS

    broken = [p for p in (_resolve_doc_target(t, pages) for t in DOC_TARGETS) if p]
    assert not broken, "renvois du validateur à réparer :\n  " + "\n  ".join(broken)


def test_the_doc_target_gate_can_fail(pages):
    assert _resolve_doc_target("nodal/reference/jamais-rendue/", pages) is not None
    assert _resolve_doc_target("nodal/reference/format-fichier/#inexistant", pages) is not None


def test_every_issue_renders_a_doc_url():
    """Toute erreur porte un lien, y compris celles ajoutées plus tard.

    Le lien est dérivé du chemin JSON quand il n'est pas fourni : une
    vérification ajoutée demain hérite d'un renvoi correct sans y penser.
    """
    from grimoire.cli.cmd_blueprint import DOC_BASE, Issue

    for path in ("$.nodes", "$.nodes[0].id", "$.nodes[0].pins[1].contract",
                 "$.edges[2]", "$.boundaries[0]", "$"):
        rendered = Issue(path, "p", "e", "f").render()
        assert "| doc: " in rendered, rendered
        assert DOC_BASE in rendered, rendered


# ── Les exemples du manuel sont de vrais blueprints valides ────────────────


def test_examples_are_valid_blueprints():
    """Un exemple qui ne valide plus est une leçon fausse publiée.

    Les diagrammes du manuel sont dérivés de ces fichiers : si le format
    évolue et qu'ils cessent de valider, la page continuerait à les montrer
    comme des modèles à suivre.
    """
    from grimoire.cli.cmd_blueprint import _schema_issues, _structural_issues

    broken = []
    for blueprint in gen.load_examples():
        schema_errors, _status = _schema_issues(blueprint)
        structural = [i.render() for i in _structural_issues(blueprint, None)]
        if schema_errors or structural:
            broken.append(f"{blueprint['id']}: {schema_errors + structural}")
    assert not broken, "exemples invalides :\n  " + "\n  ".join(broken)


def test_examples_have_a_page_and_an_anchor(pages):
    page = pages[f"{gen.BASE}/exemples.md"]
    missing = [
        b["id"] for b in gen.load_examples()
        if f"{{: #{b['id'].lower()} }}" not in page
    ]
    assert not missing, f"exemples sans ancre : {missing}"


def test_diagram_shows_every_node_and_edge():
    """Le diagramme est dérivé du fichier : il doit tout montrer."""
    for blueprint in gen.load_examples():
        diagram = gen.render_diagram(blueprint)
        for node in blueprint.get("nodes", []):
            assert gen._mermaid_id(node["id"]) in diagram, (blueprint["id"], node["id"])
        arrows = diagram.count("-->") + diagram.count("-.->")
        assert arrows == len(blueprint.get("edges", [])), blueprint["id"]


def test_diagram_marks_error_channels_differently():
    """Un chemin de rattrapage ne doit pas se lire comme le chemin nominal."""
    flow = {
        "id": "x",
        "nodes": [{"id": "a", "pins": []}, {"id": "b", "pins": []}],
        "edges": [
            {"from": "a.out", "to": "b.in", "contract": "task-envelope"},
            {"from": "a.err", "to": "b.in", "contract": "error-envelope",
             "channel": "escalation"},
        ],
    }
    diagram = gen.render_diagram(flow)
    assert "-.->" in diagram, diagram
    assert "escalation" in diagram, diagram


# ── Les renvois de l'atelier vers le manuel résolvent aussi ────────────────


def _studio_manual_paths() -> list[str]:
    """Les chemins du manuel déclarés par `web/bp2-manual.js`."""
    source = (ROOT / "web/bp2-manual.js").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"'(nodal/[^']*)'", source)))


def test_studio_manual_links_resolve(pages):
    """La toile renvoie vers le manuel ; le manuel ne sait pas qu'elle existe.

    Renommer une page casserait ces liens en silence, des deux côtés d'une
    frontière que rien ne surveille. Ce test est cette surveillance.
    """
    targets = _studio_manual_paths()
    assert targets, "aucun chemin trouvé dans bp2-manual.js — le format a changé"
    broken = [p for p in (_resolve_doc_target(t, pages) for t in targets) if p]
    assert not broken, "renvois de l'atelier à réparer :\n  " + "\n  ".join(broken)


def test_every_lexicon_page_is_a_real_term():
    """Une entrée de la carte qui ne correspond à aucun terme ne s'afficherait
    jamais : le lien serait écrit, et invisible."""
    manual = (ROOT / "web/bp2-manual.js").read_text(encoding="utf-8")
    lexicon = (ROOT / "web/bp2-lexicon.js").read_text(encoding="utf-8")
    block = manual.partition("var PAGES = {")[2].partition("};")[0]
    mapped = re.findall(r"'([^']+)':\s*'nodal/", block)
    assert mapped, "carte des pages illisible"
    terms = set(re.findall(r"\[\s*'([^']+)'\s*,", lexicon))
    unknown = [term for term in mapped if term not in terms]
    assert not unknown, f"entrées sans terme correspondant au lexique : {unknown}"


# ── Les compositions et ce que l'atelier en propose ────────────────────────


def test_studio_use_case_rule_matches_the_client():
    """La règle recopiée dans le générateur doit rester celle du client.

    L'atelier écarte les compositions à un seul pattern puis coupe à douze,
    dans `web/atelier-nav.js`. Le manuel promet un squelette pour celles-là :
    si le client change son seuil, la promesse devient fausse en silence.
    """
    source = (ROOT / "web/atelier-nav.js").read_text(encoding="utf-8")
    match = re.search(r"length\s*>=\s*(\d+)\)\.slice\(0,\s*(\d+)\)", source)
    assert match, "règle des cas d'usage introuvable dans atelier-nav.js"
    assert int(match.group(1)) == gen.STUDIO_USE_CASE_MIN_PATTERNS
    assert int(match.group(2)) == gen.STUDIO_USE_CASE_LIMIT


def test_every_use_case_is_listed(catalogue, pages):
    page = pages[f"{gen.BASE}/compositions.md"]
    missing = [u["name"] for u in catalogue["useCases"] if u["name"] not in page]
    assert not missing, f"compositions absentes de la page : {missing}"


def test_seeded_use_cases_get_a_studio_link(catalogue, pages):
    page = pages[f"{gen.BASE}/compositions.md"]
    seeded = gen.studio_seeded_use_cases(catalogue)
    assert seeded, "aucune composition proposée en squelette"
    missing = [uc for uc in seeded if f"?uc={uc}" not in page]
    assert not missing, f"squelettes promis sans lien : {missing}"
    unseeded = [
        u["id"] for u in catalogue["useCases"]
        if u["id"] not in seeded and f"?uc={u['id']}" in page
    ]
    assert not unseeded, f"squelettes promis que l'atelier ne connaît pas : {unseeded}"


def test_use_case_pattern_links_point_at_real_patterns(catalogue, pages):
    """Un cas d'usage peut citer un pattern retiré du catalogue."""
    known = {p["id"] for p in catalogue["patterns"]}
    dangling = sorted({
        pid
        for use_case in catalogue["useCases"]
        for pid in (use_case.get("patterns") or [])
        if pid not in known
    })
    assert not dangling, f"compositions citant des patterns inconnus : {dangling}"
