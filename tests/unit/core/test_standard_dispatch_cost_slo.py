"""``dispatch.cost_slo`` — coût par tâche résolue et pass^k dans les gates (issue #442).

Point 5 de l'audit de positionnement du 2026-09-12 : ce contrôle lit
``TraceLedger.dispatch_outcome_stats`` et compare au SLO déclaré dans
``dispatch_cost_slo:`` de ``llm-provider-registry.yaml`` (l'artefact du
pattern ``provider-cost-slo``). Ces tests couvrent ses trois états — INFO
(données insuffisantes), WARN (SLO dépassé) et l'escalade en FAIL sur
``enforce: true`` — et le cas propre (rien à signaler).
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.agentic_standard import verify_standard_profile
from grimoire.core.standard_generation import STANDARD_DIR, TRACES_DIR
from grimoire.traces.ledger import DISPATCH_OUTCOME_TAG, TraceLedger
from grimoire.traces.schemas import TraceOutcome

REGISTRY_PATH = STANDARD_DIR / "llm-provider-registry.yaml"

_BASE_REGISTRY = """\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "anthropic"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["claude-sonnet-4.6"]
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: ["secrets"]
    fallback_order: []
routing:
  default_provider: "anthropic"
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
"""


def _write_registry(root: Path, extra_yaml: str = "") -> None:
    (root / STANDARD_DIR).mkdir(parents=True, exist_ok=True)
    (root / REGISTRY_PATH).write_text(_BASE_REGISTRY + extra_yaml, encoding="utf-8")


def _write_dispatch_outcome(
    root: Path, *, day: int, resolved: bool, cost: float, replay: str, class_: str = "V0"
) -> None:
    tags = [DISPATCH_OUTCOME_TAG, f"class:{class_}", "tier:cheap", "acceptance:judged", f"resolved:{'true' if resolved else 'false'}", f"replay:{replay}"]
    TraceLedger(root / TRACES_DIR).record(
        run_id=f"dispatch-{day}",
        workflow_instance_id="",
        mission_id="",
        task_id=f"GAO-{day}",
        recipe_id="grimoire.dispatch",
        outcome=TraceOutcome.SUCCESS if resolved else TraceOutcome.FAILURE,
        started_at=f"2026-01-{day:02d}T00:00:00+00:00",
        token_usage={"estimated_cost_usd": cost},
        tags=tags,
    )


def _checks(result, check_id: str = "dispatch.cost_slo") -> list:
    return [c for c in result.checks if c.id == check_id]


def test_sans_registre_le_controle_est_silencieux(tmp_path: Path) -> None:
    result = verify_standard_profile(tmp_path)
    assert _checks(result) == []


def test_donnees_insuffisantes_produit_deux_info_jamais_un_warning_ou_une_erreur(tmp_path: Path) -> None:
    _write_registry(tmp_path)  # aucun dispatch.outcome journalisé

    result = verify_standard_profile(tmp_path)

    checks = _checks(result)
    assert len(checks) == 2  # un pour le coût, un pour le pass^k — dénominateurs indépendants
    assert {c.severity for c in checks} == {"info"}
    assert "resolved dispatch" in checks[0].message or "resolved dispatch" in checks[1].message
    assert "pass^k" in checks[0].message or "pass^k" in checks[1].message


def test_cout_sous_le_slo_et_pass_k_au_dessus_ne_produit_aucun_check(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        "dispatch_cost_slo:\n  max_cost_per_resolved_task_usd: 10.0\n  min_pass_k_rate: 0.5\n"
        "  min_resolved_observations: 1\n  min_pass_k_observations: 1\n",
    )
    _write_dispatch_outcome(tmp_path, day=1, resolved=True, cost=0.01, replay="node-a")
    _write_dispatch_outcome(tmp_path, day=2, resolved=True, cost=0.01, replay="node-a")

    result = verify_standard_profile(tmp_path)

    assert _checks(result) == []


def test_cout_au_dessus_du_slo_est_un_warning_par_defaut(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        "dispatch_cost_slo:\n  max_cost_per_resolved_task_usd: 0.01\n  min_resolved_observations: 1\n",
    )
    _write_dispatch_outcome(tmp_path, day=1, resolved=True, cost=5.0, replay="node-a")

    result = verify_standard_profile(tmp_path)

    checks = _checks(result)
    warnings = [c for c in checks if c.severity == "warning"]
    assert len(warnings) == 1
    assert "Cost per resolved task" in warnings[0].message
    # pass^k reste sous son propre plancher de données (une seule observation,
    # aucune série rejouée) — un INFO distinct, jamais un warning ou une erreur.
    assert {c.severity for c in checks if c is not warnings[0]} == {"info"}
    assert not any(c.is_error for c in checks)  # un warning ne fait jamais échouer, ce contrôle-là


def test_cout_au_dessus_du_slo_avec_enforce_devient_une_erreur(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        "dispatch_cost_slo:\n  max_cost_per_resolved_task_usd: 0.01\n  min_resolved_observations: 1\n  enforce: true\n",
    )
    _write_dispatch_outcome(tmp_path, day=1, resolved=True, cost=5.0, replay="node-a")

    result = verify_standard_profile(tmp_path)

    checks = _checks(result)
    errors = [c for c in checks if c.severity == "error"]
    assert len(errors) == 1
    assert "Cost per resolved task" in errors[0].message
    assert not result.ok


def test_pass_k_sous_le_seuil_est_un_warning(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        "dispatch_cost_slo:\n  min_pass_k_rate: 0.9\n  min_pass_k_observations: 1\n"
        "  max_cost_per_resolved_task_usd: 999\n  min_resolved_observations: 1\n",
    )
    # Une série de deux, une seule resolue -> pass^k mesure a 0.5, sous 0.9.
    _write_dispatch_outcome(tmp_path, day=1, resolved=True, cost=0.0, replay="node-a")
    _write_dispatch_outcome(tmp_path, day=2, resolved=False, cost=0.0, replay="node-a")

    result = verify_standard_profile(tmp_path)

    checks = _checks(result)
    assert len(checks) == 1
    assert checks[0].severity == "warning"
    assert "pass^k" in checks[0].message
