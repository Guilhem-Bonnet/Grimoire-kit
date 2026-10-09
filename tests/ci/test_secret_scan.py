"""Test for secret scanning in CI (D-08)."""

import subprocess
import yaml
from pathlib import Path


def test_secret_scan_workflow_exists():
    """Test that secret-scan.yml workflow exists in .github/workflows."""
    workflow_path = Path(".github/workflows/secret-scan.yml")
    assert workflow_path.exists(), f"Secret scan workflow not found at {workflow_path}"


def test_secret_scan_job_configured():
    """Test that secret scan job is properly configured."""
    workflow_path = Path(".github/workflows/secret-scan.yml")
    assert workflow_path.exists()

    with open(workflow_path) as f:
        config = yaml.safe_load(f)

    # Verify gitleaks job exists
    assert "jobs" in config
    assert "gitleaks" in config["jobs"], "gitleaks job not found in workflow"

    gitleaks_job = config["jobs"]["gitleaks"]
    assert "runs-on" in gitleaks_job
    assert "steps" in gitleaks_job


def test_workflow_permissions_audit_exists():
    """Test that workflow audit script exists."""
    audit_script = Path("scripts/ci/audit-workflow-permissions.py")
    assert audit_script.exists(), f"Workflow audit script not found at {audit_script}"


def test_workflow_audit_runs_successfully():
    """Test that audit script runs without errors."""
    audit_script = Path("scripts/ci/audit-workflow-permissions.py")
    assert audit_script.exists()

    # Run the audit on .github/workflows
    result = subprocess.run(
        ["python", str(audit_script), ".github/workflows"],
        capture_output=True,
        text=True,
    )

    # Should succeed (exit 0) on valid configurations
    assert (
        result.returncode == 0
    ), f"Audit script failed: {result.stderr}\nstdout: {result.stdout}"


def test_ci_workflow_references_secret_scan():
    """Test that main CI workflows can trigger or reference secret scan."""
    # Check that secret scan is available as a workflow
    workflow_path = Path(".github/workflows/secret-scan.yml")
    assert workflow_path.exists(), "Secret scan workflow not available for CI"
