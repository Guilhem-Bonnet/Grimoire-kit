"""Les artefacts qui ferment les dix-sept exigences AG-* sans artefact (#246).

Un projet neuf les reçoit selon son profil et vérifie sans erreur nouvelle ;
un registre vierge est un avertissement. Ce qui est une erreur, c'est une
déclaration fausse : un critère « passé » sans preuve, un livrable accepté
sans validateur, une source remplacée sans remplaçante, un incident fermé
sans prévention, une délégation ouverte.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.agentic_standard import setup_standard_profile, verify_standard_profile
from grimoire.core.standard_traceability import matrix_for, with_verdicts

PROFILES = ("starter", "controlled", "orchestrated", "governed", "production")
# Ce qu'un projet gouverné fraîchement généré a toujours eu en erreur : le task
# envelope attend d'être rempli. Rien d'autre ne doit s'y ajouter.
ENVELOPE_PLACEHOLDERS = {
    "task.state_placeholder", "task.context_placeholder", "task.tool_boundary_placeholder", "task.pending_gate",
}


def _ids(result, prefix: str, severity: str | None = None) -> set[str]:
    return {c.id for c in result.checks if c.id.startswith(prefix) and (severity is None or c.severity == severity)}


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


@pytest.mark.parametrize("profile", PROFILES)
def test_a_fresh_project_adds_no_error_on_any_profile(tmp_path: Path, profile: str) -> None:
    setup_standard_profile(tmp_path, profile_id=profile, project_name="Demo")
    result = verify_standard_profile(tmp_path)
    assert not result.missing, result.missing
    errors = {c.id for c in result.checks if c.severity == "error"}
    assert errors <= ENVELOPE_PLACEHOLDERS, errors
    if profile in {"starter", "controlled", "orchestrated"}:
        assert result.ok


def test_each_profile_ships_what_its_level_requires(tmp_path: Path) -> None:
    for profile, expected in (
        ("starter", {"acceptance-record.md", "retention-registry.yaml"}),
        ("controlled", {"tool-registry.yaml", "incident-registry.yaml", "risk-control-matrix.yaml"}),
        ("governed", {"capability-registry.yaml"}),
    ):
        root = tmp_path / profile
        setup_standard_profile(root, profile_id=profile, project_name="Demo")
        names = {p.name for p in (root / "_grimoire/standard").iterdir()} | {
            p.name for p in (root / "_grimoire-output/evidence/bootstrap").iterdir()
        }
        assert expected <= names, (profile, expected - names)
    assert not (tmp_path / "starter/_grimoire/standard/tool-registry.yaml").exists()
    assert not (tmp_path / "controlled/_grimoire/standard/capability-registry.yaml").exists()


def test_a_criterion_passed_without_proof_is_an_error(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="starter", project_name="Demo")
    record = tmp_path / "_grimoire-output/evidence/bootstrap/acceptance-record.md"
    _replace(record, "| AC-001 |  |  | à vérifier |", "| AC-001 | Les tests passent |  | passé |")
    result = verify_standard_profile(tmp_path)
    assert "acceptance.passed_without_evidence" in _ids(result, "acceptance.", "error")


def test_accepting_without_a_validator_is_an_error(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="starter", project_name="Demo")
    record = tmp_path / "_grimoire-output/evidence/bootstrap/acceptance-record.md"
    _replace(record, "| en attente |  |  |  |", "| accepté |  |  |  |")
    result = verify_standard_profile(tmp_path)
    assert "acceptance.accepted_without_validator" in _ids(result, "acceptance.", "error")
    _replace(record, "| accepté |  |  |  |", "| accepté | Guilhem | 2026-09-04 | ok |")
    assert not _ids(verify_standard_profile(tmp_path), "acceptance.", "error")


def test_a_superseded_source_must_name_its_replacement(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="starter", project_name="Demo")
    registry = tmp_path / "_grimoire/standard/retention-registry.yaml"
    _replace(registry, "    status: active\n    owner: \"\"\n    sensitivity: interne\n    indexable: true",
             "    status: superseded\n    owner: \"\"\n    sensitivity: interne\n    indexable: true")
    result = verify_standard_profile(tmp_path)
    assert "retention.superseded_without_replacement" in _ids(result, "retention.", "error")


def test_a_purge_not_tracked_as_incident_is_an_error_only_when_governed(tmp_path: Path) -> None:
    for profile, severity in (("starter", "warning"), ("governed", "error")):
        root = tmp_path / profile
        setup_standard_profile(root, profile_id=profile, project_name="Demo")
        _replace(root / "_grimoire/standard/retention-registry.yaml", "purges: []",
                 'purges:\n  - date: "2026-09-04"\n    target: "vector db"\n    reason: "fait faux"\n')
        found = [c for c in verify_standard_profile(root).checks if c.id == "retention.purge_without_incident"]
        assert found and found[0].severity == severity, (profile, found)


def test_an_mcp_server_without_scopes_or_timeout_is_an_error_when_governed(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    _replace(tmp_path / "_grimoire/standard/tool-registry.yaml", "mcp_servers: []",
             'mcp_servers:\n  - id: MCP-001\n    server: github\n    owner: "Guilhem"\n    logging: {requests: true}\n')
    errors = _ids(verify_standard_profile(tmp_path), "toolreg.", "error")
    assert {"toolreg.mcp_scopes_missing", "toolreg.mcp_timeout_s_missing"} <= errors
    assert "toolreg.mcp_owner_missing" not in errors


def test_tool_errors_must_have_a_capture_policy(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="controlled", project_name="Demo")
    _replace(tmp_path / "_grimoire/standard/tool-registry.yaml", "  policy: evidence", "  policy: ignore")
    assert "toolreg.error_capture_policy_unknown" in _ids(verify_standard_profile(tmp_path), "toolreg.", "error")


def test_containment_must_stop_or_reduce_autonomy(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="controlled", project_name="Demo")
    _replace(tmp_path / "_grimoire/standard/incident-registry.yaml", "on_critical: stop", "on_critical: continue")
    assert "incidents.containment_action_unknown" in _ids(verify_standard_profile(tmp_path), "incidents.", "error")


def test_a_closed_incident_without_prevention_and_a_recurrence_without_feedback(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    _replace(tmp_path / "_grimoire/standard/incident-registry.yaml", "incidents: []", (
        "incidents:\n"
        "  - {id: INC-001, severity: moyenne, status: fermé, kind: faux_done, containment: carte bloquée, correction: patch}\n"
        "  - {id: INC-002, severity: moyenne, status: ouvert, kind: faux_done, containment: stop, recurrence_of: INC-001}\n"
    ))
    errors = _ids(verify_standard_profile(tmp_path), "incidents.", "error")
    assert {"incidents.memory_purge_missing", "incidents.prevention_missing", "incidents.recurrence_without_feedback"} <= errors
    assert "incidents.correction_missing" not in errors


def test_an_ephemeral_capability_needs_an_expiry_and_a_promotion_its_proof(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    _replace(tmp_path / "_grimoire/standard/capability-registry.yaml", "capabilities: []", (
        "capabilities:\n"
        "  - {id: CAP-001, name: triage, kind: skill, gap: aucun triage, durability: ephemeral, status: draft}\n"
        "  - {id: CAP-002, name: triage, kind: skill, gap: aucun triage, durability: durable, status: enforced, promoted_from: CAP-001}\n"
        "  - {id: CAP-003, name: x, kind: skill, gap: y, durability: maybe, status: draft}\n"
    ))
    errors = _ids(verify_standard_profile(tmp_path), "capabilities.", "error")
    assert {"capabilities.ephemeral_without_expiry", "capabilities.promotion_unjustified", "capabilities.durability_invalid"} <= errors


def test_a_risk_without_control_is_an_error_when_governed(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    _replace(tmp_path / "_grimoire/standard/risk-control-matrix.yaml", "controls: [CTRL-QUA-002, evidence-pack]", "controls: []")
    assert "riskmatrix.controls_missing" in _ids(verify_standard_profile(tmp_path), "riskmatrix.", "error")


def test_an_open_delegation_is_refused_and_a_missing_wip_block_only_warns(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="orchestrated", project_name="Demo")
    policy = tmp_path / "_grimoire/standard/orchestration-policy.yaml"
    _replace(policy, "open_delegation: forbidden", "open_delegation: allowed")
    assert "orchestration.wip_open_delegation_invalid" in _ids(verify_standard_profile(tmp_path), "orchestration.wip", "error")
    text = policy.read_text(encoding="utf-8")
    policy.write_text(text[: text.index("wip:")], encoding="utf-8")
    result = verify_standard_profile(tmp_path)
    assert "orchestration.wip_missing" in _ids(result, "orchestration.wip", "warning")
    assert not _ids(result, "orchestration.wip", "error")


def test_the_matrix_carries_a_verdict_per_required_artifact(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    matrix = with_verdicts(matrix_for("governed"), tmp_path)
    required = {t.artifact_type for t in matrix.artifacts if t.required}
    assert set(matrix.verdicts) == required
    assert matrix.verdicts["task_envelope"] == "error"
    assert matrix.verdicts["retention_registry"] == "warning"
    assert matrix.verdicts["decision_graph"] == "ok"
    assert "AG-QUA-003" in matrix.verified_requirements
    assert "AG-ORC-002" not in matrix.verified_requirements
    (tmp_path / "_grimoire/standard/incident-registry.yaml").unlink()
    assert with_verdicts(matrix_for("governed"), tmp_path).verdicts["incident_registry"] == "absent"


def test_cli_joins_the_verdicts_when_given_a_project(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="controlled", project_name="Demo")
    result = CliRunner().invoke(app, ["-o", "json", "standard", "traceability", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["profile"] == "controlled"
    assert payload["verdicts"]["tool_registry"] == "ok"
    assert "AG-TOL-001" in payload["verified_requirements"]
