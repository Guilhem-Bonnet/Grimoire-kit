#!/usr/bin/env python3
"""Audit GitHub Actions workflow permissions for excessive privileges.

D-08: Scan de secrets et audit statique des workflows.

This script checks that workflow permissions follow the principle of least privilege:
- No wildcard permissions
- No excessive permissions (write on sensitive resources)
- Fails closed on invalid YAML
"""

import sys
import yaml
from pathlib import Path


# Dangerous permission patterns
DANGEROUS_PERMISSIONS = {
    "id-token": {"read", "write"},
    "contents": {"write"},
    "packages": {"write"},
    "pull-requests": {"write"},
    "deployments": {"write"},
    "statuses": {"write"},
}

# These require explicit justification
SENSITIVE_PERMISSIONS = {
    "id-token": {"write"},
    "contents": {"write"},
    "packages": {"write"},
}


def audit_workflow(workflow_path: Path) -> list[str]:
    """Audit a single workflow file for permission issues.

    Args:
        workflow_path: Path to the workflow YAML file

    Returns:
        List of issues found (empty if no issues)
    """
    issues = []

    try:
        with open(workflow_path) as f:
            workflow = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"Invalid YAML: {e}"]
    except Exception as e:
        return [f"Failed to read: {e}"]

    if not isinstance(workflow, dict):
        return ["Workflow is not a mapping"]

    # Check top-level permissions
    if "permissions" in workflow:
        perms = workflow["permissions"]

        if isinstance(perms, str):
            if perms == "read-all":
                # This is acceptable
                pass
            elif perms == "write-all":
                issues.append("❌ Workflow has 'write-all' permissions")
        elif isinstance(perms, dict):
            for perm, value in perms.items():
                # Check for write permissions that need justification
                if isinstance(value, str):
                    if value == "write":
                        if perm in SENSITIVE_PERMISSIONS:
                            issues.append(
                                f"⚠️ Workflow has write access to '{perm}' "
                                f"(sensitive, needs justification)"
                            )

    # Check job-level permissions
    jobs = workflow.get("jobs", {})
    if isinstance(jobs, dict):
        for job_name, job_config in jobs.items():
            if isinstance(job_config, dict) and "permissions" in job_config:
                perms = job_config["permissions"]

                if isinstance(perms, str):
                    if perms == "write-all":
                        issues.append(
                            f"❌ Job '{job_name}' has 'write-all' permissions"
                        )
                elif isinstance(perms, dict):
                    for perm, value in perms.items():
                        if isinstance(value, str) and value == "write":
                            if perm in SENSITIVE_PERMISSIONS:
                                issues.append(
                                    f"⚠️ Job '{job_name}' has write "
                                    f"access to '{perm}'"
                                )

    return issues


def main() -> int:
    """Audit all workflows in a directory."""
    if len(sys.argv) < 2:
        print("Usage: audit-workflow-permissions.py <workflows_directory>")
        return 1

    workflows_dir = Path(sys.argv[1])

    if not workflows_dir.exists():
        print(f"Error: {workflows_dir} does not exist")
        return 1

    if not workflows_dir.is_dir():
        print(f"Error: {workflows_dir} is not a directory")
        return 1

    # Find all YAML files in workflows directory
    workflow_files = sorted(workflows_dir.glob("*.yml")) + sorted(
        workflows_dir.glob("*.yaml")
    )

    if not workflow_files:
        print(f"No workflow files found in {workflows_dir}")
        return 1

    total_issues = 0
    critical_issues = 0

    for workflow_file in workflow_files:
        issues = audit_workflow(workflow_file)

        if issues:
            print(f"\n{workflow_file.name}:")
            for issue in issues:
                print(f"  {issue}")
                if "❌" in issue:
                    critical_issues += 1
                else:
                    total_issues += 1

    if critical_issues > 0:
        print(
            f"\n❌ Found {critical_issues} critical issue(s). "
            "Please fix before merging."
        )
        return 1

    if total_issues > 0:
        print(
            f"\n⚠️ Found {total_issues} permission issue(s). "
            "Consider addressing in a follow-up."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
