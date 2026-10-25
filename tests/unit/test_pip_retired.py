"""PIP (R35) : décrit mais jamais instrumenté — retiré des prompts, comme AORA et DCF.

Le critère de la tâche demandait un compteur ``proactive_fixes`` alimenté par un
hook, ou zéro occurrence. Aucun hook ne mesure d'initiative proactive : un
compteur qui ne mesure rien aurait été un chiffre inventé. Le protocole ne reste
donc plus dans les fiches que les agents chargent.
"""

from __future__ import annotations

import re
from pathlib import Path

from grimoire.data import framework_path

_PIP = re.compile(r"\bPIP\b")


def test_les_prompts_charges_par_les_agents_ne_citent_plus_pip() -> None:
    for name in ("agent-base.md", "agent-base-compact.md"):
        text = (framework_path() / name).read_text(encoding="utf-8")
        assert not _PIP.search(text), f"{name} cite encore PIP"


def test_la_copie_projet_du_kit_ne_cite_plus_pip() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (root / "_grimoire" / "_config" / "custom" / "agent-base.md").read_text(encoding="utf-8")
    assert not _PIP.search(text), "_grimoire/_config/custom/agent-base.md cite encore PIP"


def test_la_passerelle_dit_que_pip_est_retire() -> None:
    text = (framework_path() / "orchestrator-gateway.md").read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| **PIP**"))
    assert "Retiré" in row
    assert "Observer-only" not in row
