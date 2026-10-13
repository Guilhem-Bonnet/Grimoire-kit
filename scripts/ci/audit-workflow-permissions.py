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
- ``pull_request_target`` ou ``workflow_run`` (déclencheurs privilégiés) combiné à un checkout
  de code de PR : ``actions/checkout`` (nom insensible à la casse) dont ``ref`` ou ``repository``
  contient une valeur côté PR (``github.event.*`` hors ``pull_request.base`` et ``repository``,
  ``head_ref``, ``refs/pull/``, ``workflow_run.head_*``, y compris par une clé ``env`` souillée ;
  ``base_ref``, ``ref`` et ``ref_name`` désignent la base et passent), ou, dans ``run:``,
  ``gh pr checkout`` ou une commande ``git`` (fetch, checkout, pull, switch, merge, clone,
  worktree, reset, cherry-pick, rebase...) ou ``gh api``/``curl``/``wget`` sur une archive
  (``tarball``, ``zipball``, ``archive``) dont la MÊME commande (même segment entre ``;``, ``|``,
  ``&&``, ``||`` ou fin de ligne) contient ``pull/`` ou une variable ``env`` côté PR lue en ``$VAR`` ;
- une valeur non fiable propagée vers une étape suivante : clé écrite dans ``$GITHUB_ENV`` par une
  étape qui manipule une valeur non fiable, puis relue par ``${{ env.CLE }}`` (erreur) ; clé
  écrite dans ``$GITHUB_ENV`` par une étape saine, puis relue par ``${{ env.CLE }}`` (avertissement) ;
  ``${{ matrix.* }}`` dans ``run:``/``script:`` quand ``strategy.matrix`` contient un contexte non
  fiable (erreur) ou une sortie ``steps``/``needs`` (avertissement) ;
- toute erreur d'analyse (YAML invalide, lecture impossible, racine ou ``jobs``
  qui n'est pas un mapping, fichier vide).

Limites (non détecté, analyse statique de texte) : une variable shell qui recopie une valeur
non fiable avant la commande (``REF=$T; git fetch "$REF"``), un fichier ou une sortie de commande
qui transporte la valeur (``$(cat f)``), une action tierce qui récupère le code de PR par elle-même,
un ``workflow_run`` dont l'étape lit un artefact produit par la PR, ``matrix`` alimenté depuis un
fichier, les workflows réutilisables appelés (``uses: ./...``) et les actions composites.

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
    "publish.yml": {  # voie de release unique : PyPI (OIDC) puis release GitHub
        "publish-pypi": {"id-token"},
        "github-release": {"contents"},
    },
    "release-drafter.yml": {WORKFLOW_SCOPE: {"contents", "pull-requests"}},
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
# Commandes qui amènent du code dans l'arbre de travail (``git -C dir`` toléré).
GIT_CMD = re.compile(
    r"\bgit(?:\s+-[Cc]\s+\S+)*\s+(?:fetch|checkout|pull|switch|merge|clone|worktree|reset|restore"
    r"|cherry-pick|rebase|submodule|am)\b",
    re.IGNORECASE,
)
ARCHIVE_CMD = re.compile(r"\b(?:gh\s+api|curl|wget)\b[^\n]*?\b(?:tarball|zipball|archive)\b", re.IGNORECASE)
GH_PR_CHECKOUT = re.compile(r"\bgh\s+pr\s+(?:checkout|co)\b", re.IGNORECASE)
PR_REF = re.compile(r"(?<![\w])pull/", re.IGNORECASE)
# Côté base d'un déclencheur privilégié : sûr comme ``ref`` de checkout ou argument de git.
_SAFE_BASE = re.compile(
    r"(?<![\w.])github\s*\.\s*(?:base_ref|ref_name|ref)\b"
    r"|(?<![\w.])github\s*\.\s*event\s*\.\s*(?:pull_request\s*\.\s*base|repository)\b",
    re.IGNORECASE,
)
_MATRIX = re.compile(r"(?<![\w.])matrix\b", re.IGNORECASE)
_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|\n]")
_GITHUB_ENV = re.compile(r"\bGITHUB_ENV\b", re.IGNORECASE)
_ENV_READ = re.compile(r"(?<![\w.])env\s*\.\s*([A-Za-z_]\w*)\b", re.IGNORECASE)
_ENV_NAME = re.compile(r"(?<![\w$.{])([A-Za-z_]\w*)(?:=|<<)")
_ENV_NAME_DYNAMIC = re.compile(r"[\"']?\$\{?\w+\}?=|\$\([^)]*\)=")


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


class _Taint(NamedTuple):
    """Ce qui rend une expression ``${{ ... }}`` non fiable en plus des contextes de base."""

    keys: frozenset[str] = frozenset()  # clés ``env`` souillées (déclarées ou écrites dans GITHUB_ENV)
    matrix: bool = False  # ``strategy.matrix`` porte une valeur non fiable
    head_only: bool = False  # ne compter que le côté PR : base_ref, ref, pull_request.base... sont sûrs
    unknown_env: frozenset[str] | None = None  # si défini : toute clé ``env`` hors de cet ensemble est souillée


def _has_untrusted(text: str, taint: _Taint | None = None) -> bool:
    """Vrai si ``text`` interpole un contexte non fiable, une clé ``env`` souillée ou une matrix souillée."""
    taint = taint or _Taint()
    env_keys = (
        re.compile(r"(?<![\w.])env\s*\.\s*(?:" + "|".join(map(re.escape, sorted(taint.keys))) + r")\b", re.IGNORECASE)
        if taint.keys
        else None
    )
    for raw in _expression_bodies(text):
        body = _SAFE_BASE.sub("''", raw) if taint.head_only else raw
        if any(rx.search(body) for rx in _UNTRUSTED_REFS):
            return True
        if env_keys is not None and (env_keys.search(body) or _ENV_WHOLE.search(body)):
            return True
        if taint.matrix and _MATRIX.search(body):
            return True
        if taint.unknown_env is not None and any(n.lower() not in taint.unknown_env for n in _ENV_READ.findall(body)):
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


def _tainted_keys(env: dict[str, str], head_only: bool = False) -> frozenset[str]:
    """Clés dont la valeur est non fiable, directement ou par une autre clé souillée."""
    tainted: frozenset[str] = frozenset()
    while True:
        taint = _Taint(tainted, head_only=head_only)
        found = frozenset(k for k, v in env.items() if _has_untrusted(v, taint))
        if found == tainted:
            return tainted
        tainted = found


def _reads_var(text: str, keys: Any) -> bool:
    """Vrai si ``text`` lit au moins une des variables shell ``keys`` (``$K`` ou ``${K}``)."""
    return any(re.search(r"\$\{?" + re.escape(k) + r"(?!\w)", text) for k in keys)


def _strings(node: Any) -> list[str]:
    """Toutes les chaînes d'un arbre YAML (valeurs d'une matrix, ``include`` compris)."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def _written_env_names(run: str) -> tuple[frozenset[str], bool]:
    """Clés qu'une étape écrit dans ``$GITHUB_ENV`` et vrai si certaines sont calculées (``$K=$V``).

    La casse d'origine est conservée : un nom shell y est sensible (``$SHA`` n'est pas ``$sha``).
    Seules les lectures ``${{ env.* }}``, insensibles à la casse, se comparent en minuscules.
    """
    names = frozenset(_ENV_NAME.findall(run))
    return names, not names or bool(_ENV_NAME_DYNAMIC.search(run))


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


def _risky_command(flat: str, keys: frozenset[str]) -> bool:
    """Vrai si une commande de ``flat`` récupère du code de PR : ``gh pr checkout``, ou une commande
    git/archive dont le MÊME segment contient ``pull/`` ou une variable ``keys`` lue en ``$VAR``."""
    for segment in _SEGMENT_SPLIT.split(flat):
        if GH_PR_CHECKOUT.search(segment):
            return True
        if (GIT_CMD.search(segment) or ARCHIVE_CMD.search(segment)) and (
            PR_REF.search(segment) or _reads_var(segment, keys)
        ):
            return True
    return False


def _check_steps(job_name: str, job: dict[Any, Any], privileged: str | None, workflow_env: Any = None) -> list[Issue]:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    strategy = job.get("strategy")
    matrix_values = _strings(strategy.get("matrix")) if isinstance(strategy, dict) else []
    matrix_untrusted = any(_has_untrusted(v) for v in matrix_values)
    matrix_outputs = not matrix_untrusted and any(_has_output_ref(v) for v in matrix_values)
    # Clés écrites dans $GITHUB_ENV par une étape précédente : souillées ou non, et si le nom est calculé.
    dyn_tainted: set[str] = set()
    dyn_clean: set[str] = set()
    dyn_unknown = {"tainted": False, "clean": False}
    issues = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        where = f"Job '{job_name}' step {i + 1}"
        env = _env_of(workflow_env, job.get("env"), step.get("env"))
        static = frozenset(k.lower() for k in env)
        keys = _tainted_keys(env) | frozenset(dyn_tainted)
        taint = _Taint(keys, matrix_untrusted, unknown_env=static if dyn_unknown["tainted"] else None)
        scripts = [step.get("run")]
        with_ = step.get("with")
        if isinstance(with_, dict):
            scripts.append(with_.get("script"))
        for code in scripts:
            if not isinstance(code, str):
                continue
            if _has_untrusted(code, taint):
                issues.append(
                    _err(f"{where} interpolates an untrusted context in run/script (read it as $VAR from env:)")
                )
                continue
            bodies = _expression_bodies(code)
            reads = {n.lower() for b in bodies for n in _ENV_READ.findall(b)}
            if _has_output_ref(code):
                issues.append(
                    Issue("warning", f"{where} interpolates steps/needs outputs in run/script (read it from env:)")
                )
            elif matrix_outputs and any(_MATRIX.search(b) for b in bodies):
                issues.append(
                    Issue(
                        "warning",
                        f"{where} interpolates a matrix built from steps/needs outputs in run/script"
                        " (read it from env:)",
                    )
                )
            elif reads & {n.lower() for n in dyn_clean} or (dyn_unknown["clean"] and reads - static):
                issues.append(
                    Issue(
                        "warning",
                        f"{where} reads a value written to GITHUB_ENV through ${{{{ env.* }}}} in run/script"
                        " (read it as $VAR)",
                    )
                )
        run = step.get("run")
        if privileged and isinstance(run, str):
            flat = run.replace("\\\n", " ")
            head_keys = _tainted_keys(env, head_only=True) | frozenset(dyn_tainted)
            risky = head_keys | frozenset(k for k, v in env.items() if PR_REF.search(v))
            if _risky_command(flat, risky):
                issues.append(_err(f"{where} checks out PR code under {privileged} (run)"))
        uses = step.get("uses")
        checkout = isinstance(uses, str) and uses.lower().startswith("actions/checkout")
        if privileged and checkout and isinstance(with_, dict):
            head_taint = _Taint(_tainted_keys(env, head_only=True) | frozenset(dyn_tainted), head_only=True)
            for key in ("ref", "repository"):
                val = with_.get(key)
                if isinstance(val, str) and (_has_untrusted(val, head_taint) or PR_REF.search(val)):
                    issues.append(_err(f"{where} checks out PR code under {privileged}"))
        if isinstance(run, str) and _GITHUB_ENV.search(run):
            names, computed = _written_env_names(run)
            unsafe = _has_untrusted(run, taint) or _reads_var(run, keys)
            (dyn_tainted if unsafe else dyn_clean).update(names)
            if computed:
                dyn_unknown["tainted" if unsafe else "clean"] = True
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

    triggers = _triggers(workflow)
    privileged = next((t for t in ("pull_request_target", "workflow_run") if t in triggers), None)
    workflow_declares = workflow.get("permissions") is not None
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            issues.append(_err(f"Job '{job_name}' is not a mapping"))
            continue
        if not workflow_declares and job.get("permissions") is None:
            issues.append(_err(f"Job '{job_name}' has no permissions block and the workflow declares none"))
        issues += _check_permissions(job.get("permissions"), f"Job '{job_name}'", allow.get(str(job_name), set()))
        issues += _check_steps(str(job_name), job, privileged, workflow.get("env"))
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
