"""CLI ``grimoire blueprint`` — scaffold, validate and compile blueprints without the web atelier.

Wrapper Typer autour de :mod:`grimoire.tools.ext_manager` (validation fichier)
et :mod:`grimoire.tools.forge_server` (simulation, compilation). Complète
``grimoire serve`` : mêmes règles, mais utilisables en script/CI.

Layers of ``validate``:
- JSON Schema (``schemas/blueprint-v1.schema.json``) when the optional
  ``jsonschema`` package is importable — skipped silently otherwise.
- Programmatic checks: :func:`validate_blueprint_file` plus structural checks
  reproducing what ``blueprint_compile`` requires (unique node ids, resolvable
  edge endpoints, matching pin contracts, acyclic flow, per-kind ref shapes).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer

from grimoire.tools.ext_manager import ID_RE, ExtensionError, validate_blueprint_file

blueprint_app = typer.Typer(
    help="Blueprints : scaffolder, valider et compiler des flows sans passer par l'atelier web."
)

PATTERN_REF_RE = re.compile(r"^[A-Z]{3}-\d{2}$")
EXTENSION_REF_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*/[A-Za-z0-9][A-Za-z0-9_-]*$")
IDENTIFIER_RE = re.compile(r"^[^.\s]+$")
NODE_KINDS = ("pattern", "artifact", "extension-node", "composite", "composite-inline", "agent-spec")
TEMPLATES = ("minimal", "pipeline")

_FILE_ARGUMENT = typer.Argument(..., help="Path to a .blueprint.json file.")
_ID_ARGUMENT = typer.Argument(..., metavar="ID", help="Blueprint id (lowercase, digits, hyphens).")
_OUT_OPTION = typer.Option(None, "--out", help="Output file (default: <id>.blueprint.json).")
_TEMPLATE_OPTION = typer.Option("minimal", "--template", help="Template: minimal or pipeline.")
_FORCE_OPTION = typer.Option(False, "--force", help="Overwrite the output file if it exists.")
_PROJECT_ROOT_OPTION = typer.Option(
    None,
    "--project-root",
    help="Project root for state-dependent checks (artifact refs, installed extensions).",
    show_default=False,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _kit_root() -> Path:
    """Racine des ressources du kit (``schemas``, ``extensions``, ``version.txt``).

    Même heuristique que ``grimoire serve`` : checkout/editable → racine du
    dépôt ; wheel → le paquet ``grimoire/`` (ressources force-includes).
    """
    import grimoire

    pkg = Path(grimoire.__file__).resolve().parent
    repo = pkg.parent.parent
    if (repo / "pyproject.toml").is_file() and (repo / "extensions").is_dir():
        return repo
    if (pkg / "extensions").is_dir():
        return pkg
    return repo


def _schema_path() -> Path:
    return _kit_root() / "schemas" / "blueprint-v1.schema.json"


def _minimal_template(bp_id: str) -> dict[str, Any]:
    return {
        "blueprintVersion": 1,
        "id": bp_id,
        "name": bp_id,
        "description": "Describe what this flow does and when to run it.",
        "catalogRef": {"version": "1.0.0"},
        "nodes": [
            {
                "id": "orchestrate",
                "kind": "pattern",
                "ref": "ORC-01",
                "label": "Orchestrator",
                "description": "Single-node flow: replace with your own patterns and pins.",
                "pins": [],
            }
        ],
        "edges": [],
        "extensions": [],
    }


def _pipeline_template(bp_id: str) -> dict[str, Any]:
    return {
        "blueprintVersion": 1,
        "id": bp_id,
        "name": bp_id,
        "description": "Three-step pipeline: plan, govern, verify. Edit refs and contracts to fit your flow.",
        "catalogRef": {"version": "1.0.0"},
        "nodes": [
            {
                "id": "plan",
                "kind": "pattern",
                "ref": "ORC-01",
                "label": "Plan",
                "pins": [{"id": "out", "direction": "out", "contract": "task-envelope"}],
            },
            {
                "id": "govern",
                "kind": "pattern",
                "ref": "GOV-01",
                "label": "Govern",
                "pins": [
                    {"id": "in", "direction": "in", "contract": "task-envelope"},
                    {"id": "out", "direction": "out", "contract": "task-envelope"},
                ],
            },
            {
                "id": "verify",
                "kind": "pattern",
                "ref": "QUA-04",
                "label": "Verify",
                "pins": [{"id": "in", "direction": "in", "contract": "task-envelope"}],
            },
        ],
        "edges": [
            {"from": "plan.out", "to": "govern.in", "contract": "task-envelope"},
            {"from": "govern.out", "to": "verify.in", "contract": "task-envelope"},
        ],
        "extensions": [],
    }


@dataclass(frozen=True, slots=True)
class Issue:
    """One actionable finding: JSON path, problem, expectation, remediation."""

    path: str
    problem: str
    expected: str
    fix: str

    def render(self) -> str:
        return f"{self.path}: {self.problem} | expected: {self.expected} | fix: {self.fix}"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        typer.secho(f"Error: cannot read {path} ({exc})", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except json.JSONDecodeError as exc:
        typer.secho(f"Error: {path} is not valid JSON ({exc})", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(data, dict):
        typer.secho(f"Error: {path} must contain a JSON object", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    return data


def _schema_issues(blueprint: dict[str, Any]) -> tuple[list[str], str]:
    """JSON Schema layer. Returns (errors, status) — status explains a skip."""
    try:
        import jsonschema
    except ImportError:
        return [], "skipped (optional package jsonschema is not installed)"
    schema_file = _schema_path()
    if not schema_file.is_file():
        return [], f"skipped (schema not found: {schema_file})"
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = [
        f"{error.json_path}: {error.message}"
        for error in sorted(validator.iter_errors(blueprint), key=lambda e: e.json_path)
    ]
    return errors, "checked against schemas/blueprint-v1.schema.json"


def _structural_issues(blueprint: dict[str, Any], project_root: Path | None) -> list[Issue]:
    """Checks reproducing what ``blueprint_compile`` requires, statically."""
    issues: list[Issue] = []
    nodes = blueprint.get("nodes")
    nodes = nodes if isinstance(nodes, list) else []
    edges = blueprint.get("edges")
    edges = edges if isinstance(edges, list) else []

    # Studio downgrade trap: v1 typing is bypassed if no node carries `pins`.
    if nodes and all(isinstance(n, dict) and "pins" not in n for n in nodes):
        issues.append(
            Issue(
                "$.nodes",
                "no node declares a `pins` key",
                "at least the `pins` key on every node (an empty list is fine)",
                "add `\"pins\": []` to each node, otherwise the file is treated as a Studio draft "
                "and re-projected, ignoring your typing",
            )
        )

    seen_ids: set[str] = set()
    pin_contracts: dict[str, str] = {}
    for i, node in enumerate(nodes):
        base = f"$.nodes[{i}]"
        if not isinstance(node, dict):
            issues.append(Issue(base, "node is not an object", "an object {id, kind, ref, pins}", "rewrite the node"))
            continue
        node_id = str(node.get("id", ""))
        if not node_id or not IDENTIFIER_RE.match(node_id):
            issues.append(
                Issue(
                    f"{base}.id",
                    f"invalid node id {node.get('id')!r}",
                    "a non-empty id without dots or whitespace (edge endpoints are split on '.')",
                    "rename the node (e.g. plan, verify-step)",
                )
            )
        elif node_id in seen_ids:
            issues.append(
                Issue(
                    f"{base}.id",
                    f"duplicate node id {node_id!r}",
                    "unique node ids across the blueprint",
                    "rename one of the duplicated nodes",
                )
            )
        else:
            seen_ids.add(node_id)

        kind = node.get("kind")
        if kind not in NODE_KINDS:
            issues.append(
                Issue(
                    f"{base}.kind",
                    f"unknown kind {kind!r}",
                    "one of: " + ", ".join(NODE_KINDS),
                    "set kind to the matching value (pattern for catalogue patterns, "
                    "extension-node for <ext>/<node> refs...)",
                )
            )
        ref = str(node.get("ref", ""))
        if kind == "pattern" and not PATTERN_REF_RE.match(ref):
            issues.append(
                Issue(
                    f"{base}.ref",
                    f"pattern ref {ref!r} is not a catalogue pattern id",
                    "an id like ORC-01, GOV-01, QUA-04",
                    "use a pattern id from the catalogue (web/data/catalogue-export.json)",
                )
            )
        elif kind == "extension-node" and not EXTENSION_REF_RE.match(ref):
            issues.append(
                Issue(
                    f"{base}.ref",
                    f"extension-node ref {ref!r} is not <extensionId>/<nodeId>",
                    "e.g. crewai/crewai-crew, as declared in the extension manifest provides.nodes",
                    "point the ref at an extension node and list the extension under $.extensions",
                )
            )
        elif kind == "composite" and not (
            ref.startswith("use-case:") or ref.endswith(".blueprint.json")
        ):
            issues.append(
                Issue(
                    f"{base}.ref",
                    f"composite ref {ref!r} is neither use-case:<id> nor a .blueprint.json path",
                    "use-case:<catalogue-id> or a project-relative path ending in .blueprint.json",
                    "fix the ref, or use kind artifact/pattern instead",
                )
            )
        elif kind == "artifact" and project_root is not None and not (project_root / ref).exists():
            issues.append(
                Issue(
                    f"{base}.ref",
                    f"artifact {ref!r} does not exist under {project_root}",
                    "a project-relative path to an existing governed artifact",
                    f"create {ref} in the project or fix the ref (compilation is blocked otherwise)",
                )
            )

        pins = node.get("pins")
        if "pins" in node and not isinstance(pins, list):
            issues.append(
                Issue(f"{base}.pins", "pins is not a list", "a list of pins {id, direction, contract}", "fix pins")
            )
            pins = []
        for j, pin in enumerate(pins if isinstance(pins, list) else []):
            pin_base = f"{base}.pins[{j}]"
            if not isinstance(pin, dict):
                issues.append(
                    Issue(pin_base, "pin is not an object", "an object {id, direction, contract}", "rewrite the pin")
                )
                continue
            pin_id = str(pin.get("id", ""))
            if not pin_id or not IDENTIFIER_RE.match(pin_id):
                issues.append(
                    Issue(
                        f"{pin_base}.id",
                        f"invalid pin id {pin.get('id')!r}",
                        "a non-empty id without dots or whitespace",
                        "rename the pin (e.g. in, out, mission)",
                    )
                )
            if pin.get("direction") not in ("in", "out"):
                issues.append(
                    Issue(
                        f"{pin_base}.direction",
                        f"invalid direction {pin.get('direction')!r}",
                        "\"in\" or \"out\"",
                        "set the pin direction",
                    )
                )
            if not pin.get("contract"):
                issues.append(
                    Issue(
                        f"{pin_base}.contract",
                        "missing contract",
                        "a contract id such as task-envelope or handoff-packet",
                        "declare the contract carried by the pin",
                    )
                )
            pin_contracts[f"{node_id}.{pin_id}"] = str(pin.get("contract", ""))

    for i, edge in enumerate(edges):
        base = f"$.edges[{i}]"
        if not isinstance(edge, dict):
            issues.append(Issue(base, "edge is not an object", "an object {from, to, contract}", "rewrite the edge"))
            continue
        endpoints: dict[str, str] = {}
        for end in ("from", "to"):
            value = str(edge.get(end, ""))
            if value not in pin_contracts:
                issues.append(
                    Issue(
                        f"{base}.{end}",
                        f"endpoint {value!r} does not resolve to a declared pin",
                        "<nodeId>.<pinId> where the pin exists on the node",
                        "declare the pin on the node, or fix the endpoint",
                    )
                )
            else:
                endpoints[end] = value
        if len(endpoints) == 2:
            c_from = pin_contracts[endpoints["from"]]
            c_to = pin_contracts[endpoints["to"]]
            if c_from != c_to:
                issues.append(
                    Issue(
                        f"{base}",
                        f"pin contracts differ ({c_from!r} != {c_to!r})",
                        "the same contract on both connected pins",
                        "align the two pin contracts, or route through an adapter node",
                    )
                )
            elif edge.get("contract") and edge["contract"] != c_from:
                issues.append(
                    Issue(
                        f"{base}.contract",
                        f"declared contract {edge['contract']!r} != pin contract {c_from!r}",
                        "edge.contract equal to the contract of both pins",
                        "fix edge.contract (or drop it: it is optional)",
                    )
                )

    issues.extend(_cycle_issues(nodes, edges))

    extensions = blueprint.get("extensions")
    for i, entry in enumerate(extensions if isinstance(extensions, list) else []):
        ext_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(ext_id, str) or not ID_RE.match(ext_id):
            issues.append(
                Issue(
                    f"$.extensions[{i}].id",
                    f"invalid extension id {ext_id!r}",
                    "a published extension id (lowercase, digits, hyphens)",
                    "use the id from the extension manifest / registry",
                )
            )
    return issues


def _cycle_issues(nodes: list[Any], edges: list[Any]) -> list[Issue]:
    node_ids = {str(n.get("id")) for n in nodes if isinstance(n, dict)}
    deps: dict[str, set[str]] = {nid: set() for nid in node_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("from", "")).split(".")[0]
        dst = str(edge.get("to", "")).split(".")[0]
        if src in deps and dst in deps:
            deps[dst].add(src)
    remaining = {nid: set(d) for nid, d in deps.items()}
    while remaining:
        ready = [nid for nid, d in remaining.items() if not d]
        if not ready:
            cycle = ", ".join(sorted(remaining))
            return [
                Issue(
                    "$.edges",
                    f"cycle detected between nodes: {cycle}",
                    "an acyclic flow (compilation orders nodes topologically)",
                    "remove one connection of the cycle",
                )
            ]
        for nid in ready:
            del remaining[nid]
        for d in remaining.values():
            d.difference_update(ready)
    return []


def _validate_report(bp_path: Path, project_root: Path | None) -> int:
    """Run both validation layers, print one line per finding. Returns error count."""
    try:
        blueprint, file_errors = validate_blueprint_file(bp_path)
    except ExtensionError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        return 1

    schema_errors, schema_status = _schema_issues(blueprint)
    typer.echo(f"Schema layer: {schema_status}")
    for line in schema_errors:
        typer.secho(f"  {line}", fg=typer.colors.RED)

    structural = _structural_issues(blueprint, project_root)
    typer.echo("Structural layer: validate_blueprint_file + compile-level checks")
    for msg in file_errors:
        typer.secho(f"  $: {msg}", fg=typer.colors.RED)
    for issue in structural:
        typer.secho(f"  {issue.render()}", fg=typer.colors.RED)
    if project_root is None:
        typer.echo(
            "  note: artifact refs not checked (pass --project-root to check them "
            "against a project)"
        )
    return len(schema_errors) + len(file_errors) + len(structural)


# ── commands ─────────────────────────────────────────────────────────────────


@blueprint_app.command("new")
def blueprint_new(
    bp_id: str = _ID_ARGUMENT,
    out: Path = _OUT_OPTION,
    template: str = _TEMPLATE_OPTION,
    force: bool = _FORCE_OPTION,
) -> None:
    """Scaffold a valid .blueprint.json from an embedded template."""
    if not ID_RE.match(bp_id):
        typer.secho(
            f"Error: invalid blueprint id {bp_id!r} — expected lowercase letters, digits "
            "and hyphens (e.g. web-pipeline)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    if template not in TEMPLATES:
        typer.secho(
            f"Error: unknown template {template!r} — expected one of: " + ", ".join(TEMPLATES),
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    target = out or Path(f"{bp_id}.blueprint.json")
    if target.exists() and not force:
        typer.secho(f"Error: {target} already exists (use --force to overwrite)", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    blueprint = _minimal_template(bp_id) if template == "minimal" else _pipeline_template(bp_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    typer.secho(f"Created {target} (template: {template})", fg=typer.colors.GREEN)
    typer.echo("Next steps:")
    typer.echo("  1. Edit the blueprint: nodes, pins, edges, extensions")
    typer.echo(f"  2. Validate it       : grimoire blueprint validate {target}")
    typer.echo(f"  3. Compile it        : grimoire blueprint compile {target} --project-root .")
    typer.echo(f"  4. Publish it        : grimoire ext publish {target} --registry <registry-dir>")


@blueprint_app.command("validate")
def blueprint_validate(
    file: Path = _FILE_ARGUMENT,
    project_root: Path = _PROJECT_ROOT_OPTION,
) -> None:
    """Validate a blueprint: JSON Schema (if available) + compile-level structural checks."""
    count = _validate_report(file, project_root)
    if count:
        typer.secho(f"Invalid: {count} error(s) found in {file}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.secho(f"Valid: {file} passes both validation layers", fg=typer.colors.GREEN)


@blueprint_app.command("compile")
def blueprint_compile(
    file: Path = _FILE_ARGUMENT,
    project_root: Path = _PROJECT_ROOT_OPTION,
) -> None:
    """Compile a blueprint into a mission pack (same fail-closed rules as the atelier).

    Writes ``.github/prompts/<id>.blueprint.prompt.md`` and updates
    ``_grimoire/blueprints/<id>.blueprint.json`` under the project root.
    """
    from grimoire.tools.forge_server import ForgeAPI

    root = (project_root or Path.cwd()).resolve()
    count = _validate_report(file, root)
    if count:
        typer.secho(f"Invalid: fix the {count} error(s) above before compiling", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    try:
        from grimoire.data import web_path

        ui_dir: Path | None = web_path()
    except (ImportError, FileNotFoundError):
        ui_dir = None

    blueprint = _load_json(file)
    api = ForgeAPI(root, _kit_root(), ui_dir)
    report = api.blueprint_simulate(blueprint)
    if report["blockers"]:
        typer.secho(
            f"Compilation blocked (fail-closed): {len(report['blockers'])} blocker(s)",
            fg=typer.colors.RED,
            err=True,
        )
        details = report.get("blockerDetails") or [{"message": b, "hint": ""} for b in report["blockers"]]
        for detail in details:
            typer.secho(f"  - {detail['message']}", fg=typer.colors.RED)
            if detail.get("hint"):
                typer.echo(f"    fix: {detail['hint']}")
        raise typer.Exit(code=1)

    result = api.blueprint_compile(blueprint)
    typer.secho(f"Compiled: {result['compiled']}", fg=typer.colors.GREEN)
    typer.echo(f"  mission pack : {root / result['artifact']}")
    typer.echo(f"  hash         : {result['hash']}")
    typer.echo(f"  source saved : {root / '_grimoire' / 'blueprints' / (result['compiled'] + '.blueprint.json')}")
    for warning in result.get("warnings", []):
        typer.secho(f"  warning: {warning}", fg=typer.colors.YELLOW)
