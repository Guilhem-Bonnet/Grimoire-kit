"""Vitrine : aucun nombre écrit à la main (GAO-p04-vitrine-001, GAO-r17-lire-la-001).

Le dépôt a déjà eu un incident « chiffres inventés ». Chaque chiffre de
``web/index.html`` vient donc d'une donnée générée (``data-source`` → clé de
``meta.json``) ou n'y figure pas. Le test lit le texte visible de la page et
échoue dès qu'un nombre littéral y réapparaît.

Permis sans source : les dates (``17/10/2026``, ISO) et les références
d'issue (``#123``). Les versions ne le sont pas : une version écrite à la main
est exactement ce qui vieillit en silence. Le rejeu d'un terminal enregistré
(``data-verbatim``) cite des sorties telles quelles : il est exempté en bloc.
"""

from __future__ import annotations

import importlib.util
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "web" / "index.html"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
VISIBLE_ATTRS = ("aria-label", "alt", "title", "placeholder")
ALLOWED = re.compile(r"\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|#\d+")


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Texts(HTMLParser):
    """Textes visibles (et attributs lisibles) hors ``data-source`` et ``data-verbatim``."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[int, str]] = []
        self._stack: list[tuple[str, bool]] = []  # (balise, exempte)
        self._skip = 0

    def _exempt(self) -> bool:
        return any(e for _, e in self._stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        exempt = "data-source" in a or "data-verbatim" in a
        if not (self._exempt() or exempt):
            for k in VISIBLE_ATTRS:
                if a.get(k) and re.search(r"\d", ALLOWED.sub("", a[k] or "")):
                    self.found.append((self.getpos()[0], f'{k}="{a[k]}"'))
        if tag not in VOID:
            self._stack.append((tag, exempt))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip -= 1
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data: str) -> None:
        if self._skip or self._exempt():
            return
        if re.search(r"\d", ALLOWED.sub("", data)):
            self.found.append((self.getpos()[0], data.strip()[:80]))


def test_no_hand_written_number_in_the_page_text() -> None:
    p = _Texts()
    p.feed(PAGE.read_text(encoding="utf-8"))
    assert not p.found, (
        "nombre littéral dans web/index.html : calcule-le (data-source → meta.json) "
        f"ou retire-le — {p.found}"
    )


def test_the_guard_catches_a_literal_number() -> None:
    p = _Texts()
    p.feed('<p>Plus de 5000 tests</p><p>Version <span data-source="version"></span></p>'
           '<p aria-label="48 secondes">x</p><p>le 17/10/2026, voir #12</p>'
           '<div data-verbatim>2/2 exigences</div>')
    assert [t for _, t in p.found] == ["Plus de 5000 tests", 'aria-label="48 secondes"']


def test_every_data_source_exists_in_the_generated_meta() -> None:
    gsd = _load("gsd_vitrine", "gen-site-data.py")
    rv = _load("rv_vitrine", "render-vitrine.py")
    meta = gsd.build_meta(ROOT, False)
    keys = rv.sources(PAGE.read_text(encoding="utf-8"))
    assert keys, "la page doit citer au moins une source (la version)"
    for key in keys:
        node: object = meta
        for part in key.split("."):
            assert isinstance(node, dict) and part in node, f"data-source inconnu : {key}"
            node = node[part]


def test_render_fills_from_meta_and_shows_nothing_when_unknown() -> None:
    rv = _load("rv_vitrine2", "render-vitrine.py")
    page = '<p>v<span data-source="version" data-format=" ({})"></span>.</p><i data-source="counts.tests"></i>'
    out = rv.render(page, {"version": "9.8.7", "counts": {"tests": None}})
    assert "(9.8.7)" in out
    assert '<i data-source="counts.tests"></i>' in out
    blank = rv.render(page, {"version": "unknown", "counts": {}})
    assert not re.search(r"\d", blank.replace("counts.tests", ""))


def test_rendered_page_carries_the_generated_version() -> None:
    gsd = _load("gsd_vitrine3", "gen-site-data.py")
    rv = _load("rv_vitrine3", "render-vitrine.py")
    meta = gsd.build_meta(ROOT, False)
    rendered = rv.render(PAGE.read_text(encoding="utf-8"), meta)
    assert f"({meta['version']})" in rendered


def test_pages_workflow_regenerates_the_numbers() -> None:
    wf = (ROOT / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8")
    assert "scripts/gen-site-data.py" in wf and "--meta-only" in wf
    assert "scripts/render-vitrine.py" in wf


def test_no_contradictory_test_counts_survive() -> None:
    """Quatre valeurs de tests contradictoires circulaient : plus aucune en dur."""
    stale = re.compile(r"\b(?:5517|2960|5948)\b|5[  ]?990\+")
    for rel in ("web/index.html", "README.md", "README.fr.md", "docs/index.md"):
        assert not stale.search((ROOT / rel).read_text(encoding="utf-8")), rel
    for meta in (ROOT / "web" / "data").rglob("meta.json"):
        counts = json.loads(meta.read_text(encoding="utf-8")).get("counts", {})
        assert counts.get("tests") is None, f"compteur de tests figé dans {meta.relative_to(ROOT)}"
