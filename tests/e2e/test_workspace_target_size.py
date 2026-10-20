"""Cibles cliquables et boutons désactivés, mesurés sur la coque rendue.

P08 : toute cible cliquable mesure au moins 24×24 px (WCAG 2.5.8, cadenas
d'épinglage compris), dans les deux thèmes ; un bouton désactivé n'a pas le
même contraste que le même bouton actif.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

MIN_TARGET = 24

_TARGETS_JS = """
() => {
  const out = [];
  const sel = 'button, a[href], input:not([type=hidden]), select, textarea, [role=button], [role=tab]';
  for (const el of document.querySelectorAll(sel)) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const box = el.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;
    out.push({
      tag: el.tagName.toLowerCase(),
      id: el.id,
      cls: el.className && el.className.toString ? el.className.toString() : '',
      label: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30),
      w: box.width,
      h: box.height,
    });
  }
  return out;
}
"""


def _apply_theme(page: Page, theme: str) -> None:
    page.evaluate(
        """(t) => {
            document.documentElement.dataset.theme = t;
            return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
        }""",
        theme,
    )


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_toute_cible_cliquable_mesure_au_moins_24_px(workspace: Page, theme: str) -> None:
    _apply_theme(workspace, theme)
    # Le cadenas n'est visible que panneau ouvert : on s'assure qu'il est mesuré.
    workspace.evaluate("() => window.GrimoireWorkspace.setPanel('explorer', 'pinned')")

    targets = workspace.evaluate(_TARGETS_JS)
    offenders = [t for t in targets if t["w"] < MIN_TARGET or t["h"] < MIN_TARGET]

    assert any("pin" in t["cls"] for t in targets), "le cadenas d'épinglage doit faire partie des cibles mesurées"
    assert not offenders, "\n  ".join(
        f"{t['tag']}#{t['id']}.{t['cls']} « {t['label']} » → {t['w']:.0f}×{t['h']:.0f}" for t in offenders[:20]
    )


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_un_bouton_desactive_rend_un_style_different_de_l_actif(workspace: Page, theme: str) -> None:
    _apply_theme(workspace, theme)
    signatures = workspace.evaluate(
        """() => {
            const mk = (disabled) => {
              const b = document.createElement('button');
              b.className = 'btn';
              b.textContent = 'x';
              b.disabled = disabled;
              document.body.appendChild(b);
              const s = getComputedStyle(b);
              const sig = [s.backgroundColor, s.color, s.borderTopStyle];
              b.remove();
              return sig;
            };
            return [mk(false), mk(true)];
        }"""
    )
    active, disabled = signatures

    assert active[1] != disabled[1], "l'encre d'un bouton désactivé doit différer"
    assert disabled[2] == "dashed"
    assert active != disabled
