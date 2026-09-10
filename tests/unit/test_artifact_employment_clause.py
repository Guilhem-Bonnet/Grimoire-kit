"""Un agent livré sans clause d'emploi ne pourra jamais être retiré (issue #368).

La doctrine (`docs/artifact-doctrine.md`) fait de `use_when`, `dont_use_when`,
`tool_boundary` et `tools` des champs obligatoires du frontmatter de tout
agent livré par le kit sous `archetypes/*/agents/`. Sans eux, personne ne peut
dire si l'agent a servi ni le distinguer du généraliste — c'est exactement le
vide qui a laissé passer les neuf agents fantômes de l'issue #346.

`tools` porte la frontière grossière (peut-il éditer, exécuter ?) et
`tool_boundary` le périmètre fin (quels fichiers, quelles commandes). Un audit
mené sur ce lot a constaté qu'aucun des agents livrés ne déclarait `tools` —
la frontière était seulement inférée du texte, ce qui rendait le critère
inopérant. Les deux champs sont désormais requis ensemble.

Portée volontairement restreinte aux agents pour ce lot : les skills et
prompts livrés (`extensions/*/artifacts/{skills,prompts}/`) ne déclarent pas
encore cette clause de façon uniforme et ne sont pas couverts ici (voir la
discussion de l'issue #368) — mieux vaut une garde étroite et vraie qu'une
garde large qu'on doit affaiblir pour la faire passer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

_REQUIRED_FIELDS = ("use_when", "dont_use_when", "tool_boundary", "tools")

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _shipped_agent_files() -> list[Path]:
    return sorted((REPO / "archetypes").glob("*/agents/*.md"))


def _frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    # Le fichier peut commencer par un commentaire HTML avant le frontmatter YAML.
    match = _FRONTMATTER_RE.search(text)
    if match:
        return match.group(1)
    # Le commentaire d'archétype précède parfois le "---" d'ouverture.
    start = text.find("\n---\n")
    if start != -1:
        rest = text[start + 1 :]
        match = _FRONTMATTER_RE.search(rest)
        if match:
            return match.group(1)
    return ""


def _missing_fields(frontmatter: str) -> list[str]:
    return [field for field in _REQUIRED_FIELDS if f"\n{field}:" not in f"\n{frontmatter}"]


def test_no_shipped_agent_is_missing_from_the_fixture() -> None:
    agents = _shipped_agent_files()
    assert agents, "aucun agent trouvé sous archetypes/*/agents/*.md — le test ne garde rien"


@pytest.mark.parametrize("path", _shipped_agent_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_shipped_agent_declares_its_employment_clause(path: Path) -> None:
    frontmatter = _frontmatter(path)
    assert frontmatter, f"{path}: frontmatter YAML introuvable"

    missing = _missing_fields(frontmatter)
    assert not missing, (
        f"{path.relative_to(REPO)} ne déclare pas {missing} — "
        "voir docs/artifact-doctrine.md : use_when, dont_use_when et tool_boundary "
        "sont obligatoires pour tout agent livré par le kit."
    )
