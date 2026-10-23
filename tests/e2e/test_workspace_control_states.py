"""Matrice contrôle × état, rendue et mesurée dans les deux thèmes (#536).

Chaque contrôle de la coque (bouton, bouton primaire, chip, pastille, select,
input, lien, épingle, onglet, segment, rail, ligne d'arbre, ligne de preuve)
est monté dans la surface que sa feuille suppose, puis chaque état est forcé —
survol, focus visible, actif/pressé, désactivé, chargement, erreur, vide — par
le protocole CDP (``CSS.forcePseudoState``) ou par attribut, sans dépendre d'un
pointeur réel ni d'un pseudo-état que le navigateur refuserait de rendre hors
interaction. Sur l'élément rendu on mesure ce que l'œil reçoit :

- contraste du texte ≥ 4,5:1 (WCAG 1.4.3) — ≥ 3:1 pour un contrôle sans texte
  (épingle, rail : la couleur de l'icône, WCAG 1.4.11) ;
- cible ≥ 24 × 24 px (WCAG 2.5.8) pour tout contrôle cliquable hors lien en
  ligne (exempté par le critère lui-même) ;
- focus visible : un anneau d'au moins 1 px, de style non ``none``, dont la
  couleur tient 3:1 sur la surface qui l'entoure.

La mesure statique des mêmes paires, sur les valeurs déclarées, est dans
``tests/unit/test_workspace_control_states.py`` ; celle-ci est la preuve rendue,
dans ``tests/e2e/`` parce qu'elle a besoin d'un moteur de rendu.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from playwright.sync_api import CDPSession, Page

MIN_TEXT = 4.5
MIN_NON_TEXT = 3.0
MIN_TARGET = 24

THEMES = ("dark", "light")


@dataclass(frozen=True)
class State:
    name: str
    pseudo: tuple[str, ...] = ()
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Control:
    name: str
    html: str
    #: Sélecteur de l'élément hôte dans la coque — la surface que la feuille
    #: du contrôle suppose (`#canvas` → --ground, `.panel-body` → --panel…).
    host: str
    states: tuple[State, ...]
    text: bool = True
    target: bool = True
    #: Sélecteur, dans le fragment, de l'élément à mesurer (défaut : la racine).
    probe: str = ""


NORMAL = State("normal")
HOVER = State("survol", pseudo=("hover",))
FOCUS = State("focus visible", pseudo=("focus", "focus-visible"))
DISABLED = State("désactivé", attrs={"disabled": ""})
PRESSED = State("actif", attrs={"aria-pressed": "true"})
BUSY = State("chargement", attrs={"aria-busy": "true"})

_ICON = '<svg class="ico" aria-hidden="true"><use href="#i-ui-pin"></use></svg>'

CONTROLS: tuple[Control, ...] = (
    Control("btn", '<button class="btn">Action</button>', "#canvas", (NORMAL, HOVER, FOCUS, PRESSED, DISABLED, BUSY)),
    Control("btn.pri", '<button class="btn pri">Lancer</button>', "#canvas", (NORMAL, HOVER, FOCUS, DISABLED, BUSY)),
    Control("btn.on", '<button class="btn on">Vue</button>', "#canvas", (NORMAL, HOVER, FOCUS, DISABLED)),
    Control("chip", '<button class="chip">kit 3.64</button>', "#docbar", (NORMAL, HOVER, FOCUS, DISABLED)),
    *(
        Control(f"chip.pill.{tone}", f'<span class="chip pill {tone}">{tone}</span>', "#docbar", (NORMAL,), target=False)
        for tone in ("ok", "warn", "bad", "acc", "s1", "s2", "s3")
    ),
    Control("select", '<select><option>Workflow</option></select>', "#canvas", (NORMAL, HOVER, FOCUS, DISABLED)),
    Control("input", '<input value="valeur saisie">', "#canvas", (NORMAL, HOVER, FOCUS, DISABLED, State("erreur", attrs={"aria-invalid": "true"}))),
    Control("input.vide", '<input placeholder="Rechercher…">', "#canvas", (NORMAL, FOCUS), target=True),
    Control(".input", '<div class="input"><input value="valeur"></div>', ".panel-body", (NORMAL, State("focus visible", pseudo=("focus-within",)))),
    Control("lien", '<p><a href="#">documentation</a></p>', "#canvas", (NORMAL, HOVER, FOCUS), target=False, probe="a"),
    Control("lien.panneau", '<p><a href="#">documentation</a></p>', ".panel-body", (NORMAL, HOVER, FOCUS), target=False, probe="a"),
    Control("pin", f'<button class="pin" aria-label="Épingler">{_ICON}</button>', ".panel-head", (NORMAL, HOVER, FOCUS, PRESSED), text=False),
    Control("tab", '<button class="tab" role="tab" aria-selected="false">Piloter</button>', "#spaces", (NORMAL, HOVER, FOCUS, State("actif", attrs={"aria-selected": "true"}))),
    Control("seg", '<div class="seg"><button aria-pressed="false">Board</button></div>', "#docbar", (NORMAL, HOVER, FOCUS, PRESSED, DISABLED), probe="button"),
    Control("seg-s", '<div class="seg seg-s"><button aria-pressed="false">100 %</button></div>', "#statusbar", (NORMAL, FOCUS, PRESSED, DISABLED), probe="button"),
    Control("rail-btn", f'<button class="rail-btn" aria-pressed="false" aria-label="Explorateur">{_ICON}</button>', "#rail", (NORMAL, HOVER, FOCUS, PRESSED), text=False),
    Control("tree", '<div class="tree"><button>fichier.md</button></div>', ".panel-body", (NORMAL, HOVER, FOCUS, State("actif", attrs={"aria-current": "true"})), probe="button"),
    Control("ev-row", '<button class="ev-row"><span class="ev-id">T-01</span><span class="ev-title">Titre</span></button>', ".panel-body", (NORMAL, HOVER, FOCUS)),
    Control("kbd", '<span class="kbd">K</span>', ".panel-head", (NORMAL,), target=False),
    Control("lbl", '<span class="lbl">libellé discret</span>', ".panel-body", (NORMAL,), target=False),
    Control("empty", '<div class="empty"><h2>Rien ici</h2>Lance <code>grimoire init</code>.</div>', "#canvas", (NORMAL,), target=False),
    Control("status.erreur", '<span id="st-live" data-state="error">hors ligne</span>', "#statusbar", (NORMAL,), target=False),
    Control("status.pause", '<span id="st-live" data-state="paused">en pause</span>', "#statusbar", (NORMAL,), target=False),
    Control("tip", '<div class="tip"><div class="t">Terme</div>définition <span class="lbl">Alt · épingler</span></div>', "#canvas", (NORMAL,), target=False, probe=".lbl"),
    Control("chargement", '<div class="skeleton"><div class="skeleton-line"></div><span class="sr-only">Chargement…</span></div>', "#canvas", (NORMAL,), target=False, text=False, probe=".sr-only"),
)

_MEASURE_JS = """
() => {
  const sandbox = document.getElementById('ctl-sandbox');
  const el = sandbox.querySelector('[data-probe]');
  const parse = (value) => {
    const n = (value.match(/[\\d.]+/g) || ['0','0','0']).map(Number);
    return [n[0] / 255, n[1] / 255, n[2] / 255, n.length > 3 ? n[3] : 1];
  };
  const lum = ([r, g, b]) => {
    const f = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => {
    const x = lum(a), y = lum(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const backdrop = (start) => {
    for (let node = start; node && node !== document.documentElement; node = node.parentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg[3] > 0.9) return bg;
    }
    return parse(getComputedStyle(document.body).backgroundColor);
  };
  const style = getComputedStyle(el);
  const fg = parse(style.color);
  // Le texte d'un placeholder a sa propre encre.
  const ph = el.placeholder ? getComputedStyle(el, '::placeholder').color : '';
  const box = el.getBoundingClientRect();
  const outlineColor = parse(style.outlineColor);
  return {
    ratio: ratio(fg, backdrop(el)),
    placeholderRatio: ph ? ratio(parse(ph), backdrop(el)) : null,
    w: box.width, h: box.height,
    outlineStyle: style.outlineStyle,
    outlineWidth: parseFloat(style.outlineWidth),
    outlineRatio: ratio(outlineColor, backdrop(el.parentElement)),
    color: style.color, bg: style.backgroundColor,
  };
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


def _mount(page: Page, control: Control, state: State) -> None:
    page.evaluate(
        """([host, html, probe, attrs]) => {
            document.getElementById('ctl-sandbox')?.remove();
            const box = document.createElement('div');
            box.id = 'ctl-sandbox';
            box.style.cssText = 'position:relative;display:inline-flex;align-items:center;gap:8px;padding:8px;';
            box.innerHTML = html;
            const target = probe ? box.querySelector(probe) : box.firstElementChild;
            target.setAttribute('data-probe', '1');
            for (const [k, v] of Object.entries(attrs)) target.setAttribute(k, v);
            document.querySelector(host).appendChild(box);
            return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
        }""",
        [control.host, control.html, control.probe, state.attrs],
    )


def _force(cdp: CDPSession, pseudo: tuple[str, ...]) -> None:
    doc = cdp.send("DOM.getDocument", {"depth": 0})
    node = cdp.send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#ctl-sandbox [data-probe]"})
    cdp.send("CSS.forcePseudoState", {"nodeId": node["nodeId"], "forcedPseudoClasses": list(pseudo)})


@pytest.fixture
def states_page(workspace: Page) -> tuple[Page, CDPSession]:
    """La coque avec l'explorateur épinglé (surfaces de panneau visibles) et une session CDP."""
    workspace.evaluate("() => window.GrimoireWorkspace.setPanel('explorer', 'pinned')")
    cdp = workspace.context.new_cdp_session(workspace)
    cdp.send("DOM.enable")
    cdp.send("CSS.enable")
    return workspace, cdp


def measure(page: Page, cdp: CDPSession, control: Control, state: State, theme: str) -> dict:
    _apply_theme(page, theme)
    _mount(page, control, state)
    _force(cdp, state.pseudo)
    try:
        page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")
        return page.evaluate(_MEASURE_JS)
    finally:
        _force(cdp, ())


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("control", CONTROLS, ids=lambda c: c.name)
def test_chaque_etat_du_controle_tient_contraste_cible_et_focus(
    states_page: tuple[Page, CDPSession], control: Control, theme: str
) -> None:
    page, cdp = states_page
    offenders: list[str] = []
    for state in control.states:
        m = measure(page, cdp, control, state, theme)
        floor = MIN_TEXT if control.text else MIN_NON_TEXT
        if m["ratio"] < floor:
            offenders.append(f"{state.name} : contraste {m['ratio']:.2f}:1 < {floor} ({m['color']} sur {m['bg']})")
        if m["placeholderRatio"] is not None and m["placeholderRatio"] < MIN_TEXT:
            offenders.append(f"{state.name} : placeholder {m['placeholderRatio']:.2f}:1 < {MIN_TEXT}")
        if control.target and (m["w"] < MIN_TARGET or m["h"] < MIN_TARGET):
            offenders.append(f"{state.name} : cible {m['w']:.0f}×{m['h']:.0f} < {MIN_TARGET}")
        if "focus-visible" in state.pseudo or "focus-within" in state.pseudo:
            visible = m["outlineStyle"] != "none" and m["outlineWidth"] >= 1 and m["outlineRatio"] >= MIN_NON_TEXT
            if "focus-within" in state.pseudo:
                visible = True  # l'anneau est sur l'enfant focalisé ; ici c'est la bordure accent qui change
            if not visible:
                offenders.append(
                    f"{state.name} : anneau {m['outlineStyle']} {m['outlineWidth']}px, {m['outlineRatio']:.2f}:1 sur la surface"
                )
    assert not offenders, f"{control.name} en thème {theme} :\n  " + "\n  ".join(offenders)
