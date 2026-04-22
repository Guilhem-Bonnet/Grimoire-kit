#!/usr/bin/env python3
"""
compiled-flow.py - Registre et resolver de flows compiles Grimoire.

Objectif:
  - precompiler des sequences d'execution recurrentes en recettes versionnees
  - fournir des templates de chat et de rapport reutilisables
  - pousser les cas one-off vers des recettes dynamiques plutot que polluer
    les surfaces universelles

Usage :
  python3 compiled-flow.py --project-root . catalog
  python3 compiled-flow.py --project-root . match "la CI casse"
  python3 compiled-flow.py --project-root . render --recipe ci-diagnosis --surface chat
  python3 compiled-flow.py --project-root . validate
  python3 compiled-flow.py --project-root . scaffold --recipe-id local-ci --title "CI locale" --intent "diagnostic local"

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

VERSION = "1.0.0"
REGISTRY_FILENAME = "compiled-flow-recipes.json"
DEFAULT_DYNAMIC_DIR = Path(
    "_grimoire-runtime-output/implementation-artifacts/compiled-flow/recipes"
)
PROMPT_KEYS = ("prompt", "text", "message", "promptPreview")
VALID_SCOPES = {"universal", "team", "dynamic"}
VALID_KINDS = {"execution", "analysis", "documentation"}
VALID_RISK_CLASSES = {"read_only", "workspace_write", "control_plane"}
VALID_COMMAND_MODES = {"shell", "task"}
PLACEHOLDER_RE = re.compile(r"{{([A-Z0-9_]+)}}")
PLACEHOLDER_NAME_RE = re.compile(r"^[A-Z0-9_]+$")
FORBIDDEN_UNIVERSAL_TOKENS = (
    "/home/",
    "\\\\users\\\\",
    "/mnt/",
    "grimoire-forge",
    "guilhem",
    "backup/main-before-origin-sync",
)


@dataclass(frozen=True, slots=True)
class CommandSpec:
    id: str
    label: str
    mode: str
    template: str


@dataclass(frozen=True, slots=True)
class GovernanceSpec:
    mutate_universal_only_for: tuple[str, ...] = ()
    create_dynamic_when: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledRecipe:
    id: str
    title: str
    summary: str
    scope: str
    kind: str
    risk_class: str
    keywords: tuple[str, ...]
    intent_patterns: tuple[str, ...]
    chat_template: str
    report_template: str
    hook_context: str
    commands: tuple[CommandSpec, ...]
    governance: GovernanceSpec
    source: str


@dataclass(frozen=True, slots=True)
class RegistryBundle:
    metadata: dict[str, object]
    shared_templates: dict[str, str]
    recipes: tuple[CompiledRecipe, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecipeMatch:
    recipe_id: str
    title: str
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    level: str
    source: str
    message: str


def resolve_registry_path(project_root: Path) -> Path:
    """Resolve le chemin du registre de recettes compilees."""
    candidates = [
        project_root / "framework" / "registry" / REGISTRY_FILENAME,
        project_root / "grimoire-kit" / "framework" / "registry" / REGISTRY_FILENAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if (project_root / "grimoire-kit").exists():
        return candidates[1]
    return candidates[0]


def resolve_dynamic_recipe_dir(project_root: Path, metadata: dict[str, object] | None = None) -> Path:
    """Resolve le dossier des recettes dynamiques."""
    overlay_value = None
    if metadata:
        overlay_value = metadata.get("dynamicOverlayDir")
    overlay_text = str(overlay_value or DEFAULT_DYNAMIC_DIR.as_posix())
    return project_root / Path(overlay_text)


def _load_json_file(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing registry file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Unable to read {path}: {exc}") from exc


def _fragment_to_data(payload: object) -> tuple[dict[str, str], list[dict[str, object]], dict[str, object]]:
    templates: dict[str, str] = {}
    recipes: list[dict[str, object]] = []
    metadata: dict[str, object] = {}

    if not isinstance(payload, dict):
        return templates, recipes, metadata

    raw_templates = payload.get("shared_templates")
    if isinstance(raw_templates, dict):
        templates = {
            str(key): str(value)
            for key, value in raw_templates.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    raw_metadata = payload.get("metadata")
    if isinstance(raw_metadata, dict):
        metadata = dict(raw_metadata)

    raw_recipes = payload.get("recipes")
    if isinstance(raw_recipes, list):
        for recipe in raw_recipes:
            if isinstance(recipe, dict):
                recipes.append(recipe)
    elif "id" in payload:
        recipes.append(payload)

    return templates, recipes, metadata


def _coerce_governance(raw: object) -> GovernanceSpec:
    if not isinstance(raw, dict):
        return GovernanceSpec()

    def _tuple_of_strings(key: str) -> tuple[str, ...]:
        value = raw.get(key)
        if not isinstance(value, list):
            return ()
        return tuple(str(item) for item in value if str(item).strip())

    return GovernanceSpec(
        mutate_universal_only_for=_tuple_of_strings("mutate_universal_only_for"),
        create_dynamic_when=_tuple_of_strings("create_dynamic_when"),
        forbid=_tuple_of_strings("forbid"),
    )


def _coerce_command(raw: object) -> CommandSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid command spec: {raw!r}")
    return CommandSpec(
        id=str(raw.get("id", "command")).strip() or "command",
        label=str(raw.get("label", "Command")).strip() or "Command",
        mode=str(raw.get("mode", "shell")).strip() or "shell",
        template=str(raw.get("template", "")).strip(),
    )


def _coerce_recipe(raw: dict[str, object], source: Path) -> CompiledRecipe:
    commands = raw.get("commands")
    raw_commands = commands if isinstance(commands, list) else []
    return CompiledRecipe(
        id=str(raw.get("id", "")).strip(),
        title=str(raw.get("title", "")).strip(),
        summary=str(raw.get("summary", "")).strip(),
        scope=str(raw.get("scope", "dynamic")).strip(),
        kind=str(raw.get("kind", "execution")).strip(),
        risk_class=str(raw.get("risk_class", "read_only")).strip(),
        keywords=tuple(str(item) for item in raw.get("keywords", []) if str(item).strip()),
        intent_patterns=tuple(
            str(item) for item in raw.get("intent_patterns", []) if str(item).strip()
        ),
        chat_template=str(raw.get("chat_template", "")).strip(),
        report_template=str(raw.get("report_template", "")).strip(),
        hook_context=str(raw.get("hook_context", "")).strip(),
        commands=tuple(_coerce_command(command) for command in raw_commands),
        governance=_coerce_governance(raw.get("governance")),
        source=source.as_posix(),
    )


def load_registry_bundle(project_root: Path) -> RegistryBundle:
    """Charge le registre de base et les overlays dynamiques."""
    registry_path = resolve_registry_path(project_root)
    base_payload = _load_json_file(registry_path)
    templates, recipes_raw, metadata = _fragment_to_data(base_payload)
    recipe_entries: list[tuple[dict[str, object], Path]] = [
        (recipe, registry_path) for recipe in recipes_raw
    ]
    sources = [registry_path.as_posix()]

    dynamic_dir = resolve_dynamic_recipe_dir(project_root, metadata)
    if dynamic_dir.exists():
        for overlay_path in sorted(dynamic_dir.glob("*.json")):
            overlay_payload = _load_json_file(overlay_path)
            extra_templates, extra_recipes, _ = _fragment_to_data(overlay_payload)
            templates.update(extra_templates)
            recipe_entries.extend((recipe, overlay_path) for recipe in extra_recipes)
            sources.append(overlay_path.as_posix())

    recipes = tuple(_coerce_recipe(raw, source) for raw, source in recipe_entries)
    return RegistryBundle(
        metadata=metadata,
        shared_templates=templates,
        recipes=recipes,
        sources=tuple(sources),
    )


def extract_prompt_text(raw_input: str) -> str:
    """Extrait un texte de prompt depuis un payload JSON ou du texte brut."""
    stripped = raw_input.strip()
    if not stripped:
        return ""
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped

    chunks: list[str] = []
    if isinstance(payload, dict):
        for key in PROMPT_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                chunks.append(value.strip())
    return " ".join(chunks).strip()


def match_recipes(prompt_text: str, recipes: tuple[CompiledRecipe, ...]) -> list[RecipeMatch]:
    """Matche un prompt libre contre les recettes compilees."""
    normalized = prompt_text.lower().strip()
    if not normalized:
        return []

    matches: list[RecipeMatch] = []
    for recipe in recipes:
        score = 0
        reasons: list[str] = []

        for pattern in recipe.intent_patterns:
            try:
                if re.search(pattern, normalized, re.IGNORECASE):
                    score += 3
                    reasons.append(f"pattern:{pattern}")
            except re.error:
                continue

        for keyword in recipe.keywords:
            if keyword.lower() in normalized:
                score += 1
                reasons.append(f"keyword:{keyword}")

        if recipe.id.replace("-", " ") in normalized:
            score += 2
            reasons.append(f"id:{recipe.id}")

        if score > 0:
            matches.append(
                RecipeMatch(
                    recipe_id=recipe.id,
                    title=recipe.title,
                    score=score,
                    reasons=tuple(reasons[:5]),
                )
            )

    return sorted(matches, key=lambda item: (-item.score, item.recipe_id))


def recipe_lookup(bundle: RegistryBundle, recipe_id: str) -> CompiledRecipe:
    """Retourne une recette unique par identifiant."""
    matching = [recipe for recipe in bundle.recipes if recipe.id == recipe_id]
    if not matching:
        raise ValueError(f"Unknown recipe: {recipe_id}")
    if len(matching) > 1:
        raise ValueError(f"Duplicate recipe id detected: {recipe_id}")
    return matching[0]


def render_text(template: str, variables: dict[str, str]) -> str:
    """Rend un template moustache simple."""
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return variables.get(key, match.group(0))

    return PLACEHOLDER_RE.sub(_replace, template)


def _parse_vars(raw_vars: list[str]) -> dict[str, str]:
    variables: dict[str, str] = {}
    for item in raw_vars:
        if "=" not in item:
            raise ValueError(f"Invalid --var value: {item}")
        key, value = item.split("=", 1)
        key = key.strip().upper()
        if not key:
            raise ValueError(f"Invalid --var key: {item}")
        variables[key] = value
    return variables


def _build_base_variables(
    recipe: CompiledRecipe,
    project_root: Path,
    user_vars: dict[str, str] | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, str]:
    dynamic_dir = resolve_dynamic_recipe_dir(project_root, metadata)
    variables = {
        "PROJECT_ROOT": project_root.as_posix(),
        "WORKSPACE_ROOT": project_root.as_posix(),
        "RECIPE_ID": recipe.id,
        "RECIPE_TITLE": recipe.title,
        "OBJECTIVE": recipe.summary,
        "SUMMARY": recipe.summary,
        "DECISION": "Use the compiled recipe before ad hoc composition.",
        "RISKS": "Promote to the universal catalog only after repeated reuse and validation.",
        "TARGET": dynamic_dir.as_posix(),
        "PR_REF": "<pr-ref>",
        "BASE_REF": "HEAD~1",
        "DOC_PATH": "docs/index.md",
        "QUESTION": "<question>",
        "GOAL": recipe.title,
        "CHECK_TARGET": "tests",
    }
    if user_vars:
        variables.update(user_vars)
    return variables


def render_command_lines(
    recipe: CompiledRecipe,
    project_root: Path,
    user_vars: dict[str, str] | None = None,
    metadata: dict[str, object] | None = None,
) -> list[str]:
    """Rend les commandes d'une recette."""
    variables = _build_base_variables(recipe, project_root, user_vars, metadata)
    lines: list[str] = []
    for command in recipe.commands:
        rendered = render_text(command.template, variables)
        if command.mode == "task":
            lines.append(f"task: {rendered}")
        else:
            lines.append(f"shell: {rendered}")
    return lines


def render_surface(
    bundle: RegistryBundle,
    recipe_id: str,
    surface: str,
    project_root: Path,
    user_vars: dict[str, str] | None = None,
) -> str:
    """Rend la surface demandee pour une recette."""
    recipe = recipe_lookup(bundle, recipe_id)
    if surface == "commands":
        return "\n".join(render_command_lines(recipe, project_root, user_vars, bundle.metadata))

    template_name = recipe.chat_template if surface == "chat" else recipe.report_template
    template = bundle.shared_templates.get(template_name)
    if not template:
        raise ValueError(f"Missing template '{template_name}' for recipe '{recipe.id}'")

    variables = _build_base_variables(recipe, project_root, user_vars, bundle.metadata)
    variables["COMMAND_LIST"] = "\n".join(
        render_command_lines(recipe, project_root, user_vars, bundle.metadata)
    )
    return render_text(template, variables)


def build_hook_context(
    prompt_text: str,
    bundle: RegistryBundle,
    project_root: Path,
    limit: int = 2,
) -> str:
    """Construit une capsule de contexte concise pour UserPromptSubmit."""
    matches = match_recipes(prompt_text, bundle.recipes)[:limit]
    if not matches:
        return ""

    dynamic_dir = resolve_dynamic_recipe_dir(project_root, bundle.metadata).as_posix()
    parts = [
        "COMPILED_FLOW_MATCHES: " + ", ".join(match.recipe_id for match in matches) + ".",
        "Use compiled recipes before inventing new shell pipelines or report structures.",
    ]

    for match in matches:
        recipe = recipe_lookup(bundle, match.recipe_id)
        parts.append(f"{recipe.id}: {recipe.hook_context}")

    parts.append(
        "Governance: do not specialize universal recipes for one-off cases; "
        f"scaffold a dynamic recipe under {dynamic_dir} and validate it before promotion."
    )
    return " ".join(parts)[:1200]


def _collect_placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def validate_registry(project_root: Path) -> list[ValidationIssue]:
    """Valide la coherence du registre et des overlays dynamiques."""
    issues: list[ValidationIssue] = []
    try:
        bundle = load_registry_bundle(project_root)
    except ValueError as exc:
        return [ValidationIssue(level="error", source=project_root.as_posix(), message=str(exc))]

    template_names = set(bundle.shared_templates)
    seen_ids: dict[str, str] = {}

    for recipe in bundle.recipes:
        if not recipe.id:
            issues.append(ValidationIssue("error", recipe.source, "Recipe id is required."))
        if recipe.id in seen_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    recipe.source,
                    f"Duplicate recipe id '{recipe.id}' also present in {seen_ids[recipe.id]}",
                )
            )
        else:
            seen_ids[recipe.id] = recipe.source

        if recipe.scope not in VALID_SCOPES:
            issues.append(ValidationIssue("error", recipe.source, f"Invalid scope '{recipe.scope}'"))
        if recipe.kind not in VALID_KINDS:
            issues.append(ValidationIssue("error", recipe.source, f"Invalid kind '{recipe.kind}'"))
        if recipe.risk_class not in VALID_RISK_CLASSES:
            issues.append(
                ValidationIssue("error", recipe.source, f"Invalid risk_class '{recipe.risk_class}'")
            )
        if recipe.kind == "execution" and not recipe.commands:
            issues.append(
                ValidationIssue("error", recipe.source, f"Execution recipe '{recipe.id}' has no commands")
            )
        if recipe.chat_template and recipe.chat_template not in template_names:
            issues.append(
                ValidationIssue(
                    "error",
                    recipe.source,
                    f"Unknown chat template '{recipe.chat_template}' for '{recipe.id}'",
                )
            )
        if recipe.report_template and recipe.report_template not in template_names:
            issues.append(
                ValidationIssue(
                    "error",
                    recipe.source,
                    f"Unknown report template '{recipe.report_template}' for '{recipe.id}'",
                )
            )
        if not recipe.governance.mutate_universal_only_for:
            issues.append(
                ValidationIssue(
                    "warning",
                    recipe.source,
                    f"Recipe '{recipe.id}' has no mutate_universal_only_for governance rule.",
                )
            )
        if not recipe.governance.create_dynamic_when:
            issues.append(
                ValidationIssue(
                    "warning",
                    recipe.source,
                    f"Recipe '{recipe.id}' has no create_dynamic_when governance rule.",
                )
            )

        recipe_texts = [recipe.summary, recipe.hook_context]
        for command in recipe.commands:
            if command.mode not in VALID_COMMAND_MODES:
                issues.append(
                    ValidationIssue(
                        "error",
                        recipe.source,
                        f"Recipe '{recipe.id}' uses invalid command mode '{command.mode}'",
                    )
                )
            if not command.template:
                issues.append(
                    ValidationIssue(
                        "error",
                        recipe.source,
                        f"Recipe '{recipe.id}' contains an empty command template",
                    )
                )
            recipe_texts.append(command.template)
            for placeholder in _collect_placeholders(command.template):
                if not PLACEHOLDER_NAME_RE.match(placeholder):
                    issues.append(
                        ValidationIssue(
                            "error",
                            recipe.source,
                            f"Recipe '{recipe.id}' uses invalid placeholder '{placeholder}'",
                        )
                    )

        if recipe.scope == "universal":
            lowered_texts = "\n".join(recipe_texts).lower()
            for token in FORBIDDEN_UNIVERSAL_TOKENS:
                if token in lowered_texts:
                    issues.append(
                        ValidationIssue(
                            "error",
                            recipe.source,
                            f"Universal recipe '{recipe.id}' contains forbidden token '{token}'",
                        )
                    )

    for name, template in bundle.shared_templates.items():
        for placeholder in _collect_placeholders(template):
            if not PLACEHOLDER_NAME_RE.match(placeholder):
                issues.append(
                    ValidationIssue(
                        "error",
                        resolve_registry_path(project_root).as_posix(),
                        f"Template '{name}' uses invalid placeholder '{placeholder}'",
                    )
                )

    return issues


def _slugify(raw_value: str) -> str:
    text = raw_value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "dynamic-recipe"


def _derive_keywords(intent: str, title: str, recipe_id: str) -> tuple[str, ...]:
    words = re.findall(r"[a-z0-9]{3,}", f"{intent} {title} {recipe_id}".lower())
    unique_words: list[str] = []
    for word in words:
        if word not in unique_words:
            unique_words.append(word)
    return tuple(unique_words[:6])


def scaffold_recipe_payload(
    recipe_id: str,
    title: str,
    intent: str,
    scope: str = "dynamic",
    kind: str = "execution",
    risk_class: str = "workspace_write",
) -> dict[str, object]:
    """Construit un payload de recette dynamique pre-rempli."""
    slug = _slugify(recipe_id)
    summary = intent.strip() or title.strip() or slug
    keywords = _derive_keywords(intent, title, slug)
    patterns = [re.escape(keyword) for keyword in keywords[:4]] or [re.escape(slug)]
    return {
        "id": slug,
        "title": title.strip() or slug.replace("-", " ").title(),
        "summary": summary,
        "scope": scope,
        "kind": kind,
        "risk_class": risk_class,
        "keywords": list(keywords),
        "intent_patterns": patterns,
        "chat_template": "implementation_summary_chat",
        "report_template": "governance_decision_report",
        "hook_context": "Use this dynamic recipe before writing ad hoc command chains.",
        "commands": [
            {
                "id": "inspect-target",
                "label": "Inspect the target surface",
                "mode": "shell",
                "template": "rg -n \"TODO|FIXME|NOTE\" {{PROJECT_ROOT}}/{{TARGET}}",
            }
        ],
        "governance": {
            "mutate_universal_only_for": ["portability", "reliability", "discoverability"],
            "create_dynamic_when": [
                "the need is local, temporary, or repo-specific",
                "the recipe must encode a narrow incident or branch detail",
            ],
            "forbid": [
                "promoting without repeated reuse",
                "embedding personal paths or transient refs",
            ],
        },
    }


def write_scaffold_recipe(
    project_root: Path,
    payload: dict[str, object],
    output: str | None = None,
    force: bool = False,
) -> Path:
    """Ecrit une recette scaffold dans le landing zone dynamique."""
    bundle = load_registry_bundle(project_root)
    dynamic_dir = resolve_dynamic_recipe_dir(project_root, bundle.metadata)
    target_path = Path(output) if output else dynamic_dir / f"{payload['id']}.json"
    if target_path.exists() and not force:
        raise FileExistsError(f"Recipe scaffold already exists: {target_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return target_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compiled flow registry and resolver for Grimoire")
    parser.add_argument("--project-root", default=".", help="Workspace or project root")
    parser.add_argument("--json", action="store_true", help="Emit JSON output when supported")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("catalog", help="List compiled recipes")
    subparsers.add_parser("validate", help="Validate the compiled flow registry")

    parser_match = subparsers.add_parser("match", help="Match a free-form prompt to recipes")
    parser_match.add_argument("context", nargs="?", default="", help="Prompt or context to match")
    parser_match.add_argument("--stdin", action="store_true", help="Read the context from stdin")
    parser_match.add_argument("--limit", type=int, default=3, help="Maximum number of matches")

    parser_hook = subparsers.add_parser("hook-context", help="Emit compact additional context for hooks")
    parser_hook.add_argument("--limit", type=int, default=2, help="Maximum number of matches")

    parser_render = subparsers.add_parser("render", help="Render a recipe surface")
    parser_render.add_argument("--recipe", required=True, help="Recipe id")
    parser_render.add_argument(
        "--surface",
        required=True,
        choices=("chat", "report", "commands"),
        help="Surface to render",
    )
    parser_render.add_argument("--var", action="append", default=[], help="Template variable KEY=VALUE")

    parser_scaffold = subparsers.add_parser("scaffold", help="Scaffold a new recipe payload")
    parser_scaffold.add_argument("--recipe-id", required=True, help="Recipe identifier")
    parser_scaffold.add_argument("--title", required=True, help="Human title")
    parser_scaffold.add_argument("--intent", default="", help="Short need description")
    parser_scaffold.add_argument("--scope", default="dynamic", choices=sorted(VALID_SCOPES))
    parser_scaffold.add_argument("--kind", default="execution", choices=sorted(VALID_KINDS))
    parser_scaffold.add_argument(
        "--risk-class",
        default="workspace_write",
        choices=sorted(VALID_RISK_CLASSES),
        help="Risk class for the scaffolded recipe",
    )
    parser_scaffold.add_argument("--output", help="Optional output path")
    parser_scaffold.add_argument("--force", action="store_true", help="Overwrite an existing scaffold")

    return parser


def _catalog_text(bundle: RegistryBundle) -> str:
    lines = [
        f"Compiled flow catalog - {len(bundle.recipes)} recipes",
        "",
    ]
    for recipe in bundle.recipes:
        keywords = ", ".join(recipe.keywords[:4])
        lines.append(f"- {recipe.id} [{recipe.scope}/{recipe.risk_class}] {recipe.title}")
        lines.append(f"  {recipe.summary}")
        if keywords:
            lines.append(f"  triggers: {keywords}")
    return "\n".join(lines)


def _match_text(matches: list[RecipeMatch]) -> str:
    if not matches:
        return "No compiled-flow recipe matched."
    lines = ["Compiled flow matches:"]
    for match in matches:
        lines.append(f"- {match.recipe_id} ({match.score})")
        if match.reasons:
            lines.append(f"  reasons: {', '.join(match.reasons)}")
    return "\n".join(lines)


def _validation_text(issues: list[ValidationIssue]) -> str:
    if not issues:
        return "Compiled flow registry OK."
    lines = ["Compiled flow registry validation:"]
    for issue in issues:
        lines.append(f"- {issue.level}: {issue.message} [{issue.source}]")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = Path(args.project_root).resolve()

    if args.command == "catalog":
        bundle = load_registry_bundle(project_root)
        if args.json:
            payload = {
                "version": VERSION,
                "sources": list(bundle.sources),
                "recipes": [asdict(recipe) for recipe in bundle.recipes],
            }
            print(json.dumps(payload, ensure_ascii=True, indent=2))
        else:
            print(_catalog_text(bundle))
        return 0

    if args.command == "validate":
        issues = validate_registry(project_root)
        if args.json:
            payload = {
                "version": VERSION,
                "ok": not any(issue.level == "error" for issue in issues),
                "issues": [asdict(issue) for issue in issues],
            }
            print(json.dumps(payload, ensure_ascii=True, indent=2))
        else:
            print(_validation_text(issues))
        return 1 if any(issue.level == "error" for issue in issues) else 0

    if args.command == "match":
        context = sys.stdin.read() if args.stdin else args.context
        prompt_text = extract_prompt_text(context) if args.stdin else context
        bundle = load_registry_bundle(project_root)
        matches = match_recipes(prompt_text, bundle.recipes)[: args.limit]
        if args.json:
            print(json.dumps([asdict(match) for match in matches], ensure_ascii=True, indent=2))
        else:
            print(_match_text(matches))
        return 0

    if args.command == "hook-context":
        prompt_text = extract_prompt_text(sys.stdin.read())
        bundle = load_registry_bundle(project_root)
        print(build_hook_context(prompt_text, bundle, project_root, limit=args.limit))
        return 0

    if args.command == "render":
        bundle = load_registry_bundle(project_root)
        variables = _parse_vars(args.var)
        print(render_surface(bundle, args.recipe, args.surface, project_root, variables))
        return 0

    if args.command == "scaffold":
        payload = scaffold_recipe_payload(
            recipe_id=args.recipe_id,
            title=args.title,
            intent=args.intent,
            scope=args.scope,
            kind=args.kind,
            risk_class=args.risk_class,
        )
        should_write = bool(args.output) or args.scope == "dynamic"
        if should_write:
            target_path = write_scaffold_recipe(
                project_root=project_root,
                payload=payload,
                output=args.output,
                force=args.force,
            )
            if args.json:
                print(
                    json.dumps(
                        {"status": "ok", "output": target_path.as_posix(), "recipe": payload},
                        ensure_ascii=True,
                        indent=2,
                    )
                )
            else:
                print(target_path.as_posix())
            return 0

        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())