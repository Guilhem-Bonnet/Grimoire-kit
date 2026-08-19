"""Tests de l'exécuteur d'évals — enregistrer puis rejouer."""

from __future__ import annotations

from typing import Any

from grimoire.tools.blueprint_eval_runner import (
    evaluate_case,
    run_evals,
    run_record_shape_errors,
)
from grimoire.tools.blueprint_evals import evals_summary


def bp(cases: list[dict[str, Any]], scope: str = "crew") -> dict[str, Any]:
    return {"nodes": [{"id": scope, "config": {"evals": {"version": "1.0", "cases": cases}}}]}


def rec(entries: dict[str, Any], scope: str = "crew") -> dict[str, Any]:
    return {"recordVersion": 1, "runs": {scope: entries}}


def case(cid: str, *asserts: dict[str, Any]) -> dict[str, Any]:
    return {"id": cid, "input": {}, "assert": list(asserts)}


# ── ce qui doit passer ──────────────────────────────────────────────────────

def test_contrat_honore() -> None:
    ok, reasons = evaluate_case(
        [{"kind": "contract", "contract": "evidence-pack"}],
        {"contract": "evidence-pack"},
    )
    assert ok and reasons == []


def test_cout_sous_le_plafond() -> None:
    ok, _ = evaluate_case(
        [{"kind": "cost", "maxTokens": 5000}],
        {"tokens": {"input": 3000, "output": 1000}},
    )
    assert ok


def test_chemin_conforme_au_plan() -> None:
    ok, _ = evaluate_case(
        [{"kind": "path-taken", "path": ["crew", "verify"]}],
        {"path": ["crew", "verify"]},
    )
    assert ok


# ── ce qui doit échouer, et le dire ─────────────────────────────────────────

def test_contrat_divergent_est_rapporte() -> None:
    ok, reasons = evaluate_case(
        [{"kind": "contract", "contract": "evidence-pack"}],
        {"contract": "handoff-packet"},
    )
    assert not ok
    assert "evidence-pack" in reasons[0] and "handoff-packet" in reasons[0]


def test_plafond_de_cout_depasse() -> None:
    ok, reasons = evaluate_case(
        [{"kind": "cost", "maxTokens": 1000}],
        {"tokens": {"input": 900, "output": 900}},
    )
    assert not ok
    assert "1800" in reasons[0]


def test_verdict_inattendu() -> None:
    ok, reasons = evaluate_case(
        [{"kind": "verdict", "expected": "block"}], {"verdict": "pass"}
    )
    assert not ok and "block" in reasons[0]


# ── fail-closed : une preuve absente n'est pas une preuve ───────────────────

def test_absence_de_compteur_fait_echouer_le_cout() -> None:
    """Ne pas savoir combien ça a coûté n'est pas la même chose que tenir le
    plafond."""
    ok, reasons = evaluate_case([{"kind": "cost", "maxTokens": 100}], {})
    assert not ok and "aucun compteur" in reasons[0]


def test_absence_d_information_de_refus_fait_echouer() -> None:
    ok, reasons = evaluate_case([{"kind": "no-refusal"}], {})
    assert not ok and "ne prouve pas" in reasons[0]


def test_refus_explicite_fait_echouer() -> None:
    ok, _ = evaluate_case([{"kind": "no-refusal"}], {"refused": True})
    assert not ok


def test_absence_de_refus_explicite_passe() -> None:
    ok, _ = evaluate_case([{"kind": "no-refusal"}], {"refused": False})
    assert ok


# ── non exécuté n'est pas échoué ────────────────────────────────────────────

def test_un_cas_sans_trace_est_manquant_pas_echoue() -> None:
    """Confondre « pas prouvé » et « réfuté » est le péché d'un système de
    preuve."""
    out = run_evals(bp([case("jamais-joue", {"kind": "no-refusal"})]), rec({}))
    assert out["missing"] == ["crew/jamais-joue"]
    assert out["results"] == {}
    assert out["details"] == []


def test_le_rapport_distingue_declare_execute_et_reussi() -> None:
    blueprint = bp([
        case("joue", {"kind": "contract", "contract": "handoff-packet"}),
        case("pas-joue", {"kind": "no-refusal"}),
    ])
    out = run_evals(blueprint, rec({"joue": {"contract": "handoff-packet"}}))
    summary = evals_summary(blueprint, out["results"])
    scope = summary["scopes"]["crew"]
    assert scope["declared"] == 2
    assert scope["executed"] == 1
    assert scope["passed"] == 1
    assert scope["rate"] == 1.0


def test_un_echec_fait_tomber_le_taux() -> None:
    blueprint = bp([
        case("a", {"kind": "contract", "contract": "evidence-pack"}),
        case("b", {"kind": "contract", "contract": "evidence-pack"}),
    ])
    out = run_evals(blueprint, rec({
        "a": {"contract": "evidence-pack"},
        "b": {"contract": "handoff-packet"},
    }))
    assert evals_summary(blueprint, out["results"])["scopes"]["crew"]["rate"] == 0.5


# ── forme de la trace ───────────────────────────────────────────────────────

def test_version_de_trace_incompatible_signalee() -> None:
    errs = run_record_shape_errors({"recordVersion": 99, "runs": {}})
    assert errs and "recordVersion" in errs[0]


def test_runs_malforme_signale() -> None:
    assert run_record_shape_errors({"recordVersion": 1, "runs": []})


def test_trace_valide_sans_erreur() -> None:
    assert run_record_shape_errors(rec({"c": {"contract": "x"}})) == []


def test_blueprint_sans_eval_ne_produit_rien() -> None:
    out = run_evals({"nodes": [{"id": "n"}]}, rec({}))
    assert out == {"results": {}, "details": [], "missing": []}
