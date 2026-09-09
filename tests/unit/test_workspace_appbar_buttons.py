"""Aucun bouton d'en-tête ne doit mentir sur ce qu'il fait (#359).

Contexte : l'en-tête portait un bouton « action principale » dont le libellé
changeait selon l'espace actif (``Mettre à jour``, ``Compiler``, ``Réclamer``…)
sans qu'aucun écouteur d'événement n'existe pour lui nulle part dans le dépôt.
Un en-tête sans point d'action vaut mieux qu'un bouton qui ment — décision de
Guilhem, 2026-09-09 : le bouton et la propriété ``primary`` qui l'alimentait
ont été retirés.

Ce test ferme la porte : il échoue si un futur bouton, littéral dans
``index.html`` sous ``#appbar``, est ajouté sans qu'aucun code ne s'y
attache. Il ne tourne pas dans un navigateur — l'analyse est faite sur le
source, comme ``test_workspace_tokens.py`` le fait déjà pour la couleur.

Deux façons légitimes d'être « attaché » sont reconnues :

- un ``addEventListener`` référencé par identifiant dans ``shell.js`` (le cas
  de ``palette-open``) ;
- un ``data-term`` : ces boutons sont câblés par délégation via le système de
  glossaire (``glossary.js``, sélecteur ``[data-term]``), pas par identifiant
  direct — c'est le cas de ``project-chip``, qui affiche une infobulle au
  survol et n'a jamais prétendu déclencher une action au clic.

Un bouton qui n'a ni l'un ni l'autre est exactement la forme du défaut
d'origine : un identifiant, un libellé qui peut changer, et rien qui
l'écoute.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web" / "workspace"
INDEX_HTML = WORKSPACE / "index.html"
SHELL_JS = WORKSPACE / "shell.js"

_APPBAR_BLOCK = re.compile(r'<header id="appbar">(.*?)</header>', re.DOTALL)
_BUTTON_TAG = re.compile(r"<button\b[^>]*>", re.IGNORECASE)
_ID_ATTR = re.compile(r'\bid="([^"]+)"')
_DATA_TERM_ATTR = re.compile(r"\bdata-term=")


def _appbar_buttons() -> list[str]:
    """Les balises `<button>` littérales de l'en-tête, telles qu'écrites."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    match = _APPBAR_BLOCK.search(html)
    assert match, "l'en-tête `#appbar` a disparu de index.html — le contrat de ce test a changé"
    return _BUTTON_TAG.findall(match.group(1))


def test_index_html_expose_bien_lappbar_attendu() -> None:
    """Garde-fou : si ce test ne trouve plus rien, il ne prouve plus rien."""
    assert _appbar_buttons(), "aucun bouton dans #appbar : la structure attendue a changé"


def test_aucun_bouton_dappbar_sans_ecouteur_ni_glossaire() -> None:
    shell_js = SHELL_JS.read_text(encoding="utf-8")
    orphelins = []
    for tag in _appbar_buttons():
        if _DATA_TERM_ATTR.search(tag):
            continue  # câblé par délégation via le glossaire — légitime.
        id_match = _ID_ATTR.search(tag)
        if not id_match:
            orphelins.append(tag)
            continue
        button_id = id_match.group(1)
        wired = re.search(
            rf"\$\(\s*['\"]{re.escape(button_id)}['\"]\s*\)\s*\.addEventListener\(",
            shell_js,
        )
        if not wired:
            orphelins.append(tag)
    assert not orphelins, (
        "bouton(s) d'en-tête sans écouteur ni glossaire (le défaut de #359) : "
        f"{orphelins}"
    )


def test_les_espaces_ne_portent_plus_de_libelle_daction_primaire_mort() -> None:
    """La propriété `primary` de `SPACES` n'alimentait que le bouton retiré.

    Si elle réapparaît sans qu'aucun bouton ne la lise, c'est le même défaut
    sous une autre forme : une donnée déclarée pour une action qui n'existe
    pas.
    """
    shell_js = SHELL_JS.read_text(encoding="utf-8")
    spaces_block_match = re.search(r"const SPACES = \[(.*?)\];", shell_js, re.DOTALL)
    assert spaces_block_match, "la déclaration `const SPACES = [...]` a changé de forme"
    assert "primary:" not in spaces_block_match.group(1), (
        "`SPACES` porte de nouveau un champ `primary` — vérifier qu'un bouton "
        "le lit réellement avant de le réintroduire"
    )
