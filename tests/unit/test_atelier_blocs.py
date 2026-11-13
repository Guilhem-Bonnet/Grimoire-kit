"""Découpage en blocs d'un fichier de phase : aller-retour à l'octet, types, identifiants, remplacement."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.cadrage import PHASES, scaffold
from grimoire.tools import atelier_blocs as ab

SAMPLE = """---
phase: brainstorm
projet: demo
status: draft
---

# Brainstorm — diverger

_Divergence sans censure._

## Pistes

- Bouton maintenu pour parler.
- Repli sur le navigateur.

## Questions ouvertes

- Le GPU tient-il ?
- Comment mesurer « sans retoucher » ?

## Écarté (et pourquoi)

- ~~Moshi~~ : trop faible pour challenger.
- Fin de phrase automatique

## Exigences

- REQ-001 [Must] : dicter une idée
  - Elle est rangée en moins de 20 s
  - Elle reste lisible
- REQ-002 [Should] : repli navigateur

- **Décision** : pas de synthèse vocale.

```
## pas une rubrique

- ni une liste
```

> À compléter — risques.
"""


def test_roundtrip_every_scaffold_phase(tmp_path: Path) -> None:
    scaffold(tmp_path, project_name="Démo")
    for phase in PHASES:
        text = (tmp_path / "_grimoire" / "cadrage" / phase.filename).read_text(encoding="utf-8")
        assert ab.recompose(text, ab.split(phase.id, text)) == text


@pytest.mark.parametrize(
    "text",
    [
        SAMPLE,
        SAMPLE.replace("\n", "\r\n"),
        "",
        "\n\n",
        "pas de fin de ligne",
        "# T\n\n\n\n## S\n\n\n- a\n\n\n",
        "﻿---\nphase: x\n---\n## S\ntexte",
        "- seul\n",
    ],
)
def test_roundtrip_byte_exact(text: str) -> None:
    assert ab.recompose(text, ab.split("brainstorm", text)) == text


def test_kinds() -> None:
    kinds = [(b.kind, b.section) for b in ab.split("brainstorm", SAMPLE)]
    assert kinds[:3] == [("entete", ""), ("titre", ""), ("paragraphe", "")]
    by_kind: dict[str, list[ab.Block]] = {}
    for block in ab.split("brainstorm", SAMPLE):
        by_kind.setdefault(block.kind, []).append(block)
    assert [b.text for b in by_kind["question"]] == ["- Le GPU tient-il ?", "- Comment mesurer « sans retoucher » ?"]
    assert by_kind["ecartee"][0].data == {"piste": "Moshi", "raison": "trop faible pour challenger."}
    assert by_kind["ecartee"][1].data == {"piste": "Fin de phrase automatique", "raison": ""}
    req = by_kind["exigence"]
    assert req[0].data["req"] == "REQ-001" and req[0].data["priorite"] == "Must"
    assert req[0].data["criteres"] == ["Elle est rangée en moins de 20 s", "Elle reste lisible"]
    assert req[1].data["criteres"] == []
    assert len(by_kind["decision"]) == 1
    assert [b.text for b in by_kind["liste"]] == ["- Bouton maintenu pour parler.\n- Repli sur le navigateur."]
    fence = [b for b in by_kind["paragraphe"] if "## pas une rubrique" in b.text]
    assert len(fence) == 1 and "- ni une liste" in fence[0].text
    assert by_kind["paragraphe"][-1].data == {"placeholder": True}
    assert [b.text for b in by_kind["rubrique"]][:2] == ["## Pistes", "## Questions ouvertes"]
    assert not by_kind["rubrique"][0].editable and by_kind["question"][0].editable


def test_ids_are_stable_until_the_block_changes() -> None:
    before = {b.text: b.id for b in ab.split("brainstorm", SAMPLE)}
    edited = SAMPLE.replace("- Le GPU tient-il ?", "- Le GPU tient-il vraiment ?")
    after = {b.text: b.id for b in ab.split("brainstorm", edited)}
    unchanged = set(before) & set(after)
    assert unchanged and all(before[t] == after[t] for t in unchanged)
    assert before["- Le GPU tient-il ?"] != after["- Le GPU tient-il vraiment ?"]


def test_identical_blocks_get_distinct_ids() -> None:
    blocks = ab.split("brief", "## A\n\nmême\n\n## B\n\nmême\n")
    ids = [b.id for b in blocks if b.text == "même"]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_replace_block_touches_only_its_range() -> None:
    blocks = ab.split("brainstorm", SAMPLE)
    target = next(b for b in blocks if b.kind == "question")
    out = ab.replace_block(SAMPLE, target, "- Autre question ?\n")
    assert out[: target.start] == SAMPLE[: target.start]
    assert out[target.start + len("- Autre question ?") :] == SAMPLE[target.end :]
    assert out.replace("- Autre question ?", target.text) == SAMPLE


def test_replace_block_keeps_crlf() -> None:
    text = SAMPLE.replace("\n", "\r\n")
    target = next(b for b in ab.split("brainstorm", text) if b.kind == "liste")
    out = ab.replace_block(text, target, "- un\n- deux")
    assert "- un\r\n- deux" in out and out.count("\n") == out.count("\r\n")


def test_find_block_unknown() -> None:
    with pytest.raises(ab.BlockNotFoundError):
        ab.find_block(ab.split("brief", "## A\n\nx\n"), "brief.nope")
