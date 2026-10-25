"""La coque à cinq espaces (P31) — ce qui se vérifie sans navigateur.

Le nom du 4e espace (« Savoir » ou « Historique », décision D2 encore ouverte)
ne s'écrit qu'à un seul endroit, ``web/workspace/glossary.js`` : le trancher ne
doit toucher qu'un fichier. Le rendu (onglets, fil d'Ariane) est vérifié par
``tests/e2e/test_workspace_coque_p31.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web" / "workspace"
GLOSSARY_JS = WORKSPACE / "glossary.js"

_FOURTH = re.compile(r"FOURTH_SPACE = Object\.freeze\(\{ id: '([a-z-]+)', label: '([^']+)', icon: '([a-z-]+)' \}\)")


def _fourth() -> tuple[str, str, str]:
    found = _FOURTH.search(GLOSSARY_JS.read_text(encoding="utf-8"))
    assert found, "glossary.js ne déclare plus FOURTH_SPACE"
    return found.group(1), found.group(2), found.group(3)


def test_le_libelle_du_4e_espace_n_est_ecrit_que_dans_glossary_js() -> None:
    _, label, _ = _fourth()
    # Une chaîne JS, un nœud texte HTML ou un attribut : toute écriture du nom.
    written = re.compile(rf"""['"`>]{re.escape(label)}['"`<]""")
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted([*WORKSPACE.rglob("*.js"), *WORKSPACE.rglob("*.html"), *WORKSPACE.rglob("*.css")])
        if path != GLOSSARY_JS and written.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"« {label} » écrit hors de glossary.js : {offenders}"


def test_la_coque_declare_cinq_espaces_dont_le_4e_vient_de_glossary_js() -> None:
    shell = (WORKSPACE / "shell.js").read_text(encoding="utf-8")
    table = shell[shell.index("const SPACES = [") :]
    table = table[: table.index("\n];")]
    ids = re.findall(r"^  \{ id: ([^,]+),", table, re.MULTILINE)
    assert ids == ["'a-traiter'", "'travail'", "'flotte'", "FOURTH_SPACE.id", "'configuration'"]
    assert "label: FOURTH_SPACE.label" in table
    assert "import glossary, { FOURTH_SPACE } from './glossary.js';" in shell


def test_le_mot_concentration_ne_designe_plus_aucun_reglage_affiche() -> None:
    """M11 : Densité et Plein écran, deux mots pour deux réglages."""
    html = (WORKSPACE / "index.html").read_text(encoding="utf-8")
    shell = (WORKSPACE / "shell.js").read_text(encoding="utf-8")
    assert "oncentration" not in html
    # Hors commentaires, shell.js ne le garde que pour relire une préférence
    # mémorisée avant P31 (`concentration` → `compacte`), jamais pour l'écran.
    code = [line for line in shell.splitlines() if not line.lstrip().startswith(("//", "*", "/*"))]
    assert [line for line in code if "oncentration" in line] == [
        "const DENSITIES = { confortable: 'confortable', compacte: 'compacte', decouverte: 'confortable', "
        "concentration: 'compacte' };"
    ]
