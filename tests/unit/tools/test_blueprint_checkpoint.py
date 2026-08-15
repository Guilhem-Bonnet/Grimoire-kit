"""Tests des frontières de checkpoint et de la reprise (P3.2)."""

from __future__ import annotations

from typing import Any

from grimoire.tools.blueprint_checkpoint import (
    checkpoint_lint,
    checkpoint_regions,
    checkpoint_shape_errors,
    checkpoints_covering,
    compile_checkpoint_section,
    suspending_gates,
)


def gate(nid: str, action: str = "approve", label: str | None = None) -> dict[str, Any]:
    return {
        "id": nid,
        "role": "Gate",
        "label": label or nid,
        "config": {"gate": {"mode": "human", "params": {"action": action, "pct": 10}}},
    }


def bp(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]] | None = None,
    boundaries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"nodes": nodes, "edges": edges or []}
    if boundaries is not None:
        out["boundaries"] = boundaries
    return out


def test_porte_humaine_sans_checkpoint_est_une_erreur() -> None:
    """Suspendre sans pouvoir reprendre perd le travail déjà fait."""
    errors, _ = checkpoint_lint(bp([gate("g1", label="revue")]))
    assert len(errors) == 1
    assert "R-K1" in errors[0] and "revue" in errors[0]


def test_checkpoint_sur_la_porte_elle_meme_suffit() -> None:
    b = bp([gate("g1")], boundaries=[{"id": "ck", "mode": "checkpoint", "members": ["g1"]}])
    assert checkpoint_lint(b) == ([], [])


def test_checkpoint_en_amont_suffit() -> None:
    """La reprise part du dernier état persisté, pas forcément de la porte."""
    b = bp(
        [{"id": "a"}, {"id": "b"}, gate("g1")],
        [{"from": "a", "to": "b"}, {"from": "b", "to": "g1"}],
        [{"id": "ck", "mode": "checkpoint", "members": ["a"]}],
    )
    assert checkpoint_lint(b)[0] == []
    assert checkpoints_covering("g1", b) == ["ck"]


def test_checkpoint_en_aval_ne_couvre_pas() -> None:
    """Persister après la suspension n'aide personne à la franchir."""
    b = bp(
        [gate("g1"), {"id": "z"}],
        [{"from": "g1", "to": "z"}],
        [{"id": "ck", "mode": "checkpoint", "members": ["z"]}],
    )
    assert "R-K1" in checkpoint_lint(b)[0][0]


def test_tous_les_modes_humains_suspendent() -> None:
    """`sample` et `escalate-on-uncertainty` suspendent sur certains runs —
    c'est-à-dire en production, et pas en test."""
    nodes = [gate(f"g{i}", action=a) for i, a in enumerate(
        ("approve", "edit", "input", "sample", "escalate-on-uncertainty")
    )]
    assert len(suspending_gates(nodes)) == 5
    assert len(checkpoint_lint(bp(nodes))[0]) == 5


def test_une_porte_non_humaine_ne_suspend_pas() -> None:
    node = {"id": "b1", "role": "Gate", "config": {"gate": {"mode": "budget"}}}
    assert suspending_gates([node]) == []
    assert checkpoint_lint(bp([node])) == ([], [])


def test_cycle_en_amont_ne_boucle_pas() -> None:
    b = bp(
        [{"id": "a"}, {"id": "b"}, gate("g1")],
        [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}, {"from": "b", "to": "g1"}],
        [{"id": "ck", "mode": "checkpoint", "members": ["a"]}],
    )
    assert checkpoint_lint(b)[0] == []


def test_checkpoint_vide_ou_fantome_est_une_faute_de_forme() -> None:
    b = bp([{"id": "a"}], boundaries=[
        {"id": "vide", "mode": "checkpoint", "members": []},
        {"id": "fantome", "mode": "checkpoint", "members": ["inexistant"]},
    ])
    errs = checkpoint_shape_errors(b)
    assert any("ne couvre aucun node" in e for e in errs)
    assert any("node inconnu" in e for e in errs)


def test_scope_invalide_refuse() -> None:
    b = bp([{"id": "a"}], boundaries=[
        {"id": "ck", "mode": "checkpoint", "members": ["a"], "scope": "peut-etre"}
    ])
    assert any("scope invalide" in e for e in checkpoint_shape_errors(b))


def test_isolation_n_est_pas_un_checkpoint() -> None:
    """Les deux modes partagent la forme ; ils ne se remplacent pas."""
    b = bp([gate("g1")], boundaries=[{"id": "iso", "mode": "isolation", "members": ["g1"]}])
    assert checkpoint_regions(b) == []
    assert "R-K1" in checkpoint_lint(b)[0][0]


def test_checkpoint_sans_suspension_est_signale_sans_bloquer() -> None:
    b = bp([{"id": "a"}], boundaries=[{"id": "ck", "mode": "checkpoint", "members": ["a"]}])
    errors, warnings = checkpoint_lint(b)
    assert errors == []
    assert warnings and "rien ne suspend" in warnings[0]


def test_blueprint_sans_boundaries_se_comporte_comme_avant() -> None:
    assert checkpoint_regions({"nodes": []}) == []
    assert checkpoint_shape_errors({"nodes": []}) == []
    assert compile_checkpoint_section({"nodes": []}) == []


def test_section_compilee_nomme_les_portes_reprenables() -> None:
    b = bp(
        [{"id": "a"}, gate("g1")],
        [{"from": "a", "to": "g1"}],
        [{"id": "ck", "mode": "checkpoint", "members": ["a"], "scope": "state+artifacts"}],
    )
    section = "\n".join(compile_checkpoint_section(b))
    assert "## Reprise" in section
    assert "state+artifacts" in section
    assert "g1" in section
