"""IntelliSense de l'éditeur Source (#280), dans un vrai navigateur.

Ce que ``tests/unit/test_workspace_language.py`` prouve sur les données brutes
(tokens, diagnostics, complétions) redevient ici un parcours au clavier et à
la souris : la colorisation est visible à l'écran, un chemin mort allume un
marqueur de gouttière, la complétion d'un agent s'insère, et le survol d'un
identifiant lié au glossaire ouvre la même bulle épinglable que le reste de
la vue de travail — sans qu'aucune de ces additions ne casse l'enregistrement
que ``tests/e2e/test_workspace_source.py`` a déjà éprouvé.

Même harnais que ce module voisin (``tests/e2e/conftest.py`` : ``browser``,
``served``, projet réel de ``real_project``) et même précaution : ``real_project``
est partagé, à la session, avec tous les modules e2e de l'espace Source. Les
tests qui créent un override choisissent donc eux-mêmes un fichier du kit
encore vierge, comme le fait déjà ``test_workspace_source.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page


@pytest.fixture
def source(workspace: Page) -> Page:
    """La coque, déjà sur l'espace Source."""
    workspace.evaluate("() => window.GrimoireWorkspace.goto('source')")
    workspace.wait_for_function("() => window.GrimoireWorkspace.space === 'source'")
    workspace.wait_for_selector(".tree .sr-tree-file", timeout=10_000)
    return workspace


def _pick_untouched_kit_file(page: Page) -> str:
    """Un chemin de l'étage kit qu'aucun override ne masque encore — même
    logique que ``test_workspace_source.py`` (voir son en-tête)."""
    tree = page.evaluate("() => fetch('/api/workspace/files?tier=kit').then((r) => r.json())")
    files = tree["tiers"][0]["files"]
    candidate = next(f for f in files if not f["overridden"] and f["path"].endswith(".md"))
    return str(candidate["path"])


def _open(page: Page, path: str) -> None:
    name = path.rsplit("/", 1)[-1]
    page.locator(".tree .sr-tree-file", has_text=name).first.click()
    page.wait_for_function(
        "(p) => document.querySelector('.sr-docrow .mono')?.textContent === p", arg=path
    )


def _open_as_override(page: Page) -> str:
    """Ouvre un fichier du kit encore vierge, prend son override, et rend le
    chemin de l'override — le seul étage éditable, donc le seul où la
    complétion et les diagnostics ont une textarea à qui parler."""
    kit_path = _pick_untouched_kit_file(page)
    _open(page, kit_path)
    page.locator(".sr-banner .btn.pri").click()
    page.wait_for_function(
        "() => document.querySelector('.sr-docrow .mono')?.textContent.includes('_grimoire/overrides/')"
    )
    return page.locator(".sr-docrow .mono").first.inner_text()


def test_la_colorisation_est_visible(source: Page) -> None:
    """Un fichier d'agent porte un frontmatter et des blocs `<agent>` — la
    surcouche colorée doit rendre au moins une clé et une balise."""
    path = _pick_untouched_kit_file(source)
    _open(source, path)

    source.wait_for_selector(".sr-highlight .tok-key", timeout=10_000)
    assert source.locator(".sr-highlight .tok-key").count() >= 1
    assert source.locator(".sr-highlight .tok-tag").count() >= 1


def test_un_diagnostic_apparait_sur_un_chemin_mort(source: Page) -> None:
    """Éditer un chemin `_grimoire/kit/…` qui ne résout nulle part doit
    allumer un marqueur rouge dans la gouttière — la même règle que
    `grimoire doctor`, appliquée au brouillon avant tout enregistrement."""
    _open_as_override(source)

    textarea = source.locator(".sr-textarea")
    textarea.click()
    source.keyboard.press("Control+End")
    source.keyboard.type("\nVoir _grimoire/kit/ce-fichier-n-existe-pas.md pour la suite.\n")
    # `/` est aussi un déclencheur de complétion (workflow ou chemin selon le
    # mot) : Échap referme le popup avant de continuer, comme le ferait
    # n'importe quel usage réel une fois la phrase tapée.
    source.keyboard.press("Escape")

    source.wait_for_selector(".sr-gutter-dot.bad", timeout=10_000)
    assert source.locator(".sr-gutter-dot.bad").count() >= 1

    # Le survol du marqueur ouvre l'infobulle de diagnostic (spec #280).
    source.locator(".sr-gutter-dot.bad").first.hover()
    source.wait_for_selector(".sr-diag-tip:not([hidden])", timeout=5_000)
    assert "introuvable" in source.locator(".sr-diag-tip").inner_text()

    # Et l'onglet Problèmes du dock reprend le même diagnostic.
    source.locator("[data-dock-tab='problemes']").click()
    source.wait_for_function(
        "() => document.querySelector('#dock-body').textContent.includes('dead-path')"
    )


def test_la_completion_d_un_agent_s_insere(source: Page) -> None:
    """Taper `@` puis un préfixe propose les agents installés ; Entrée insère
    le nom choisi dans le texte."""
    _open_as_override(source)

    textarea = source.locator(".sr-textarea")
    textarea.click()
    source.keyboard.press("Control+End")
    source.keyboard.type("\nvoir @conci")

    # Chaque frappe relance la requête de complétion ; on attend que le
    # *premier* item soit « concierge » — pas seulement qu'il apparaisse
    # quelque part, qui serait vrai dès la réponse (large) du seul `@`, avant
    # que le préfixe complet n'ait eu le temps de filtrer la liste.
    source.wait_for_function(
        "() => document.querySelector('.sr-complete li')?.textContent.includes('concierge')",
        timeout=10_000,
    )

    source.keyboard.press("Enter")
    source.wait_for_function(
        "() => document.querySelector('.sr-textarea').value.includes('@concierge')"
    )


def test_l_infobulle_du_glossaire_s_ouvre_depuis_l_editeur(source: Page) -> None:
    """Un chemin `_grimoire/kit/…` cite l'étage `kit`, une entrée du
    glossaire — le survol de la souris (pas d'attribut `data-term` natif ici :
    la surcouche est `pointer-events: none`) doit ouvrir la même bulle
    épinglable que le reste de la vue de travail."""
    _open_as_override(source)

    textarea = source.locator(".sr-textarea")
    textarea.click()
    source.keyboard.press("Control+End")
    source.keyboard.type("\nVoir _grimoire/kit/agents/concierge.md pour la persona.\n")
    source.keyboard.press("Escape")  # referme le popup de complétion qu'un `/` a rouvert

    # `.last` : le texte tapé est ajouté en fin de fichier — un fichier
    # d'agent réel cite déjà des chemins du kit plus haut, et ceux-là peuvent
    # être défilés hors du viewport (`getBoundingClientRect()` y renvoie une
    # position hors écran, jamais atteignable par la souris).
    linked = source.locator(".sr-highlight .tok-linked")
    linked.last.wait_for(timeout=10_000)
    box = linked.last.bounding_box()
    assert box is not None
    source.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    source.wait_for_selector(".tip", timeout=5_000)
    assert source.locator(".tip .t").inner_text() == "Kit"


def test_l_enregistrement_reste_fonctionnel_avec_l_editeur_colore(
    source: Page, real_project: Path
) -> None:
    """Aucune régression sur le geste central de Source : éditer, enregistrer,
    revoir « modifié » disparaître — avec la surcouche colorée en place."""
    override_path = _open_as_override(source)

    textarea = source.locator(".sr-textarea")
    textarea.click()
    source.keyboard.press("Control+End")
    source.keyboard.type("\n<!-- édité via l'éditeur coloré -->\n")
    source.wait_for_function(
        "() => [...document.querySelectorAll('.sr-docrow button')]"
        ".some((b) => b.textContent.includes('modifié'))"
    )

    source.locator(".sr-docrow button", has_text="Enregistrer").click()
    source.wait_for_function(
        "() => ![...document.querySelectorAll('.sr-docrow button')]"
        ".some((b) => b.textContent.includes('modifié'))"
    )
    assert "édité via l'éditeur coloré" in (real_project / override_path).read_text(encoding="utf-8")
