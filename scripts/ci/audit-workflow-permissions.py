#!/usr/bin/env python3
"""Audit statique des workflows GitHub Actions (D-08).

Échoue (code 1) sur :
- un workflow sans bloc ``permissions`` ni au niveau du workflow ni sur chacun de
  ses jobs (le jeton prendrait alors le réglage par défaut du dépôt) ;
- toute permission ``write`` absente de la liste blanche explicite ci-dessous
  (par fichier de workflow et par portée : workflow entier ou job) ;
- ``write-all`` ou une valeur de permissions inconnue ;
- une interpolation ``${{ ... }}`` de contexte non fiable dans ``run:`` ou
  ``script:``, quelle que soit la fonction qui l'enveloppe (``toJSON``,
  ``format``, ``join``, accès par crochets) : ``github.event``, ``github.head_ref``,
  ``github.base_ref``, ``github.ref``, ``github.ref_name``, ``inputs``, ou le contexte
  ``github`` entier. Les noms de contexte sont insensibles à la casse. Une clé ``env``
  (workflow, job ou étape) dont la valeur contient un contexte non fiable est « souillée » :
  la relire par ``${{ env.CLE }}`` dans ``run:``/``script:`` échoue elle aussi. La valeur
  passée par ``env:`` ne se lit que par ``$CLE`` dans le shell, jamais par ``${{ env.CLE }}`` ;
- une interpolation de ``steps.*.outputs`` ou ``needs.*.outputs`` dans ``run:``/``script:``
  (avertissement, non bloquant : la sortie peut venir d'une entrée non fiable) ;
- ``pull_request_target`` combiné à un checkout de code de PR : ``actions/checkout``
  dont ``ref`` ou ``repository`` contient une expression non fiable (y compris par une clé
  ``env`` souillée) ou ``pull/``, ``gh pr checkout`` ou ``git fetch ... pull/`` dans ``run:``
  (y compris quand ``pull/`` ou la valeur non fiable arrive par une variable ``env`` lue
  en ``$VAR``) ;
- toute erreur d'analyse (YAML invalide, lecture impossible, racine ou ``jobs``
  qui n'est pas un mapping, fichier vide).

Sorties en ASCII (``backslashreplace``) : un nom accentué ne fait jamais planter
l'affichage, quel que soit l'encodage de la sortie standard.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

import yaml

WORKFLOW_SCOPE = "*"

# Permissions ``write`` justifiées : {fichier: {portée: {permission, ...}}}.
# La portée est ``*`` (niveau workflow) ou le nom du job. Tout ce qui n'est pas
# listé ici fait échouer l'audit.
WRITE_ALLOWLIST: dict[str, dict[str, set[str]]] = {
    "auto-label.yml": {WORKFLOW_SCOPE: {"pull-requests"}},  # pose les étiquettes
    "codeql-analysis.yml": {WORKFLOW_SCOPE: {"security-events"}},  # envoie les alertes
    "cleanup-branches.yml": {WORKFLOW_SCOPE: {"contents"}},  # supprime la branche fusionnée
    "docs.yml": {"deploy": {"pages", "id-token"}},  # déploiement GitHub Pages (OIDC)
    "publish.yml": {WORKFLOW_SCOPE: {"contents", "id-token"}},  # publication PyPI (OIDC)
    "release-drafter.yml": {WORKFLOW_SCOPE: {"contents", "pull-requests"}},
    "release.yml": {WORKFLOW_SCOPE: {"contents"}},  # crée la release
    "stale.yml": {WORKFLOW_SCOPE: {"issues", "pull-requests"}},  # ferme les inactifs
    "weekly-bench.yml": {WORKFLOW_SCOPE: {"contents", "issues"}},  # pousse le rapport, ouvre l'issue
}

# Contextes que l'utilisateur d'une PR, d'une branche ou d'un déclenchement manuel
# contrôle. Cherchés dans le corps d'une expression débarrassé de ses littéraux.
# Les noms de contexte GitHub Actions ne sont pas sensibles à la casse.
_UNTRUSTED_REFS = (
    re.compile(r"(?<![\w.])github\s*\.\s*(?:event|head_ref|base_ref|ref_name|ref)\b", re.IGNORECASE),
    re.compile(r"(?<![\w.])github\b(?!\s*\.)", re.IGNORECASE),  # contexte entier ou accès par crochets
    re.compile(r"(?<![\w.])inputs\b", re.IGNORECASE),
)
# Sortie d'une étape ou d'un job : peut transporter une entrée non fiable (avertissement).
_OUTPUT_REFS = re.compile(r"(?<![\w.])(?:steps|needs)\b[^\n]*?\boutputs\b", re.IGNORECASE)
# ``env`` entier ou accès par crochets : lit n'importe quelle clé, souillée ou non.
_ENV_WHOLE = re.compile(r"(?<![\w.])env\b(?!\s*\.)", re.IGNORECASE)
GIT_CMD = re.compile(r"\bgit\s+(?:fetch|checkout|pull|switch|merge)\b", re.IGNORECASE)
PR_FETCH = re.compile(
    r"\bgh\s+pr\s+(?:checkout|co)\b|\bgit\s+(?:fetch|checkout|pull|switch|merge)\b[^\n]*\bpull/",
    re.IGNORECASE,
)
PR_REF = re.compile(r"(?<![\w])pull/", re.IGNORECASE)


def _expression_bodies(text: str) -> list[str]:
    """Corps de chaque ``${{ ... }}``, littéraux entre apostrophes vidés.

    Un balayage tient compte des chaînes (``'a}}b'``, ``''`` échappée) : un ``}``
    dans un littéral ne ferme pas l'expression, et un littéral qui cite
    ``github.event`` n'est pas une référence.
    """
    bodies: list[str] = []
    n = len(text)
    i = text.find("${{")
    while i != -1:
        j = i + 3
        out: list[str] = []
        while j < n and not text.startswith("}}", j):
            if text[j] == "'":
                j += 1
                while j < n:
                    if text[j] == "'":
                        if text[j + 1 : j + 2] == "'":
                            j += 2
                            continue
                        break
                    j += 1
                out.append("''")
                j += 1
                continue
            out.append(text[j])
            j += 1
        bodies.append("".join(out))
        i = text.find("${{", j + 2)
    return bodies


def _has_untrusted(text: str, tainted: frozenset[str] = frozenset()) -> bool:
    """Vrai si ``text`` interpole un contexte non fiable, ou une clé ``env`` souillée."""
    env_keys = (
        re.compile(r"(?<![\w.])env\s*\.\s*(?:" + "|".join(map(re.escape, sorted(tainted))) + r")\b", re.IGNORECASE)
        if tainted
        else None
    )
    for body in _expression_bodies(text):
        if any(rx.search(body) for rx in _UNTRUSTED_REFS):
            return True
        if env_keys is not None and (env_keys.search(body) or _ENV_WHOLE.search(body)):
            return True
    return False


def _has_output_ref(text: str) -> bool:
    return any(_OUTPUT_REFS.search(body) for body in _expression_bodies(text))


def _env_of(*layers: Any) -> dict[str, str]:
    """Fusionne les blocs ``env`` (workflow, job, étape)."""
    merged: dict[str, str] = {}
    for layer in layers:
        if isinstance(layer, dict):
            for key, value in layer.items():
                merged[str(key)] = str(value)
    return merged


def _tainted_keys(env: dict[str, str]) -> frozenset[str]:
    """Clés dont la valeur est non fiable, directement ou par une autre clé souillée."""
    tainted: frozenset[str] = frozenset()
    while True:
        found = frozenset(k for k, v in env.items() if _has_untrusted(v, tainted))
        if found == tainted:
            return tainted
        tainted = found


def _expand_env(run: str, env: dict[str, str]) -> str:
    """Remplace ``$CLE`` et ``${CLE}`` par la valeur ``env`` pour lire ce que le shell verra."""
    out = run.replace("\\\n", " ")
    for key, value in env.items():
        out = re.sub(r"\$\{?" + re.escape(key) + r"\}?(?!\w)", value.replace("\\", "\\\\"), out)
    return out


class Issue(NamedTuple):
    severity: str  # "error" ou "warning"
    message: str


def _err(message: str) -> Issue:
    return Issue("error", message)


def _check_permissions(perms: Any, where: str, allowed: set[str]) -> list[Issue]:
    if perms is None:
        return []
    if isinstance(perms, str):
        if perms == "read-all":
            return []
        if perms == "write-all":
            return [_err(f"{where} has 'write-all' permissions")]
        return [_err(f"{where} has unknown permissions value {perms!r}")]
    if not isinstance(perms, dict):
        return [_err(f"{where} permissions is neither a string nor a mapping")]
    issues = []
    for perm, value in perms.items():
        if value == "write" and perm not in allowed:
            issues.append(_err(f"{where} has write access to '{perm}' (not in the allowlist)"))
    return issues


def _triggers(workflow: dict[Any, Any]) -> set[str]:
    # PyYAML (YAML 1.1) lit la clé ``on`` comme le booléen True.
    raw = workflow.get("on", workflow.get(True))
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(x) for x in raw}
    if isinstance(raw, dict):
        return {str(x) for x in raw}
    return set()


def _check_steps(job_name: str, job: dict[Any, Any], target: bool, workflow_env: Any = None) -> list[Issue]:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    issues = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        where = f"Job '{job_name}' step {i + 1}"
        env = _env_of(workflow_env, job.get("env"), step.get("env"))
        tainted = _tainted_keys(env)
        scripts = [step.get("run")]
        with_ = step.get("with")
        if isinstance(with_, dict):
            scripts.append(with_.get("script"))
        for code in scripts:
            if not isinstance(code, str):
                continue
            if _has_untrusted(code, tainted):
                issues.append(
                    _err(f"{where} interpolates an untrusted context in run/script (read it as $VAR from env:)")
                )
            elif _has_output_ref(code):
                issues.append(
                    Issue("warning", f"{where} interpolates steps/needs outputs in run/script (read it from env:)")
                )
        run = step.get("run")
        if target and isinstance(run, str):
            flat = run.replace("\\\n", " ")
            carries_tainted = any(re.search(r"\$\{?" + re.escape(k) + r"(?!\w)", flat) for k in tainted)
            if (
                PR_FETCH.search(flat)
                or PR_FETCH.search(_expand_env(run, env))
                or (carries_tainted and GIT_CMD.search(flat))
            ):
                issues.append(_err(f"{where} checks out PR code under pull_request_target (run)"))
        uses = step.get("uses")
        checkout = isinstance(uses, str) and uses.startswith("actions/checkout")
        if target and checkout and isinstance(with_, dict):
            for key in ("ref", "repository"):
                val = with_.get(key)
                if isinstance(val, str) and (_has_untrusted(val, tainted) or PR_REF.search(val)):
                    issues.append(_err(f"{where} checks out PR code under pull_request_target"))
    return issues


def audit_workflow(workflow_path: Path) -> list[Issue]:
    """Audite un workflow ; toute erreur d'analyse est une erreur bloquante."""
    try:
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        return [_err(f"Invalid YAML: {e}")]
    except (OSError, UnicodeDecodeError) as e:
        return [_err(f"Failed to read: {e}")]

    if not isinstance(workflow, dict):
        return [_err("Workflow is not a mapping")]

    allow = WRITE_ALLOWLIST.get(workflow_path.name, {})
    issues = _check_permissions(workflow.get("permissions"), "Workflow", allow.get(WORKFLOW_SCOPE, set()))

    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        issues.append(_err("'jobs' is missing or not a mapping"))
        return issues

    target = "pull_request_target" in _triggers(workflow)
    workflow_declares = workflow.get("permissions") is not None
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            issues.append(_err(f"Job '{job_name}' is not a mapping"))
            continue
        if not workflow_declares and job.get("permissions") is None:
            issues.append(_err(f"Job '{job_name}' has no permissions block and the workflow declares none"))
        issues += _check_permissions(job.get("permissions"), f"Job '{job_name}'", allow.get(str(job_name), set()))
        issues += _check_steps(str(job_name), job, target, workflow.get("env"))
    return issues


def _say(text: str) -> None:
    """Affiche en ASCII pur, quel que soit l'encodage de la sortie standard."""
    sys.stdout.write(text.encode("ascii", "backslashreplace").decode("ascii") + "\n")


def main() -> int:
    if len(sys.argv) < 2:
        _say("Usage: audit-workflow-permissions.py <workflows_directory>")
        return 1

    workflows_dir = Path(sys.argv[1])
    if not workflows_dir.is_dir():
        _say(f"ERROR: {workflows_dir} is not a directory")
        return 1

    files = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    if not files:
        _say(f"ERROR: no workflow files found in {workflows_dir}")
        return 1

    errors = 0
    for workflow_file in files:
        for issue in audit_workflow(workflow_file):
            _say(f"{issue.severity.upper()}: {workflow_file.name}: {issue.message}")
            if issue.severity == "error":
                errors += 1

    if errors:
        _say(f"FAILED: {errors} error(s) in workflow audit.")
        return 1
    _say(f"OK: {len(files)} workflow(s) audited.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
