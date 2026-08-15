"""Tests de l'éclatement parallèle borné (P4.1)."""

from __future__ import annotations

from typing import Any

from grimoire.tools.blueprint_scatter import (
    compile_scatter_section,
    is_scatter,
    scatter_lint,
    scatter_shape_errors,
)


def scatter(nid: str = "s", **policy: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"over": "fichiers", "maxParallel": 4}
    base.update(policy)
    return {"id": nid, "role": "Scatter", "label": nid, "config": {"scatter": base}}


def budget_gate(nid: str = "b") -> dict[str, Any]:
    return {
        "id": nid,
        "role": "Gate",
        "config": {"gate": {"mode": "budget", "params": {"scope": "flow"}}},
    }


def gather(nid: str = "g") -> dict[str, Any]:
    return {"id": nid, "role": "Gather"}


def edge(src: str, dst: str) -> dict[str, Any]:
    return {"from": f"{src}.out-handoff-packet", "to": f"{dst}.in-handoff-packet"}


def test_un_eclatement_sans_plafond_ne_compile_pas() -> None:
    """Le plafond est la primitive, pas une option de confort."""
    errs = scatter_shape_errors(scatter(maxParallel=None))
    assert any("R-S1" in e and "plafond" in e for e in errs)


def test_plafond_nul_ou_negatif_refuse() -> None:
    for bad in (0, -3):
        assert any("R-S1" in e for e in scatter_shape_errors(scatter(maxParallel=bad)))


def test_plafond_booleen_refuse() -> None:
    """`True` est un entier en Python — il ne doit pas passer pour un plafond."""
    assert any("R-S1" in e for e in scatter_shape_errors(scatter(maxParallel=True)))


def test_over_requis() -> None:
    assert any("`over` requis" in e for e in scatter_shape_errors(scatter(over="")))


def test_scatter_valide_ne_produit_aucune_erreur_de_forme() -> None:
    assert scatter_shape_errors(scatter()) == []


def test_role_incoherent_signale() -> None:
    node = {"id": "x", "role": "Unit", "config": {"scatter": {"over": "f", "maxParallel": 2}}}
    assert any("role=Unit" in e for e in scatter_shape_errors(node))


def test_sans_garde_de_budget_l_eclatement_est_refuse() -> None:
    """Le plafond borne la largeur, la garde borne la dépense."""
    errors, _ = scatter_lint([scatter()], [])
    assert any("R-S2" in e for e in errors)


def test_garde_de_budget_en_amont_suffit() -> None:
    nodes = [budget_gate("b"), scatter("s"), gather("g")]
    edges = [edge("b", "s"), edge("s", "g")]
    errors, _ = scatter_lint(nodes, edges)
    assert errors == []


def test_garde_de_budget_en_aval_ne_couvre_pas() -> None:
    """Constater la dépense après l'avoir engagée n'est pas la borner."""
    nodes = [scatter("s"), budget_gate("b")]
    errors, _ = scatter_lint(nodes, [edge("s", "b")])
    assert any("R-S2" in e for e in errors)


def test_eclatement_sans_gather_est_signale_sans_bloquer() -> None:
    nodes = [budget_gate("b"), scatter("s")]
    errors, warnings = scatter_lint(nodes, [edge("b", "s")])
    assert errors == []
    assert any("R-S3" in w for w in warnings)


def test_eclatement_qui_rejoint_un_gather_ne_previent_pas() -> None:
    nodes = [budget_gate("b"), scatter("s"), {"id": "w"}, gather("g")]
    edges = [edge("b", "s"), edge("s", "w"), edge("w", "g")]
    errors, warnings = scatter_lint(nodes, edges)
    assert errors == []
    assert warnings == []


def test_un_flow_sans_scatter_n_est_pas_concerne() -> None:
    assert scatter_lint([{"id": "a"}, gather()], []) == ([], [])
    assert scatter_shape_errors({"id": "a"}) == []
    assert compile_scatter_section({"id": "a"}) == []


def test_is_scatter_reconnait_le_role_seul() -> None:
    assert is_scatter({"id": "s", "role": "Scatter"}) is True
    assert is_scatter({"id": "u", "role": "Unit"}) is False


def test_section_compilee_pose_le_plafond_comme_contrainte() -> None:
    section = "\n".join(compile_scatter_section(scatter(maxParallel=12)))
    assert "**12**" in section
    assert "jamais lancer plus" in section


def test_cycle_en_amont_ne_boucle_pas() -> None:
    nodes = [budget_gate("b"), {"id": "a"}, scatter("s")]
    edges = [edge("b", "a"), edge("a", "s"), edge("s", "a")]
    errors, _ = scatter_lint(nodes, edges)
    assert errors == []
