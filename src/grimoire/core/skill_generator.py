"""Skill generator — auto-generate SKILL.md from Python tool metadata.

Inspired by gstack's code-metadata → docs pipeline.  Given a Python
module, extracts class names, public methods, docstrings, and type
hints to produce a structured SKILL.md skeleton.

Usage::

    from grimoire.core.skill_generator import SkillGenerator

    gen = SkillGenerator(project_root=Path("."))
    skeleton = gen.generate("grimoire.tools.learnings")
    # → SKILL.md Markdown ready to save
"""

from __future__ import annotations

import ast
import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["FunctionInfo", "ModuleInfo", "SkillGenerator"]

SKILL_GENERATOR_VERSION = "1.0.0"


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FunctionInfo:
    """Extracted info about a public function/method."""

    name: str
    docstring: str
    args: tuple[str, ...]
    return_type: str
    is_method: bool = False
    class_name: str = ""

    def signature_display(self) -> str:
        args_str = ", ".join(self.args) if self.args else ""
        ret = f" -> {self.return_type}" if self.return_type else ""
        return f"{self.name}({args_str}){ret}"


@dataclass(frozen=True, slots=True)
class ModuleInfo:
    """Extracted info about a Python module."""

    module_name: str
    docstring: str
    classes: tuple[str, ...]
    functions: tuple[FunctionInfo, ...]
    version: str
    file_path: str


# ── AST extraction ───────────────────────────────────────────────────────────


def _extract_module_info(source: str, module_name: str, file_path: str) -> ModuleInfo:
    """Parse a Python source file and extract public API info."""
    tree = ast.parse(source)

    mod_doc = ast.get_docstring(tree) or ""
    classes: list[str] = []
    functions: list[FunctionInfo] = []
    version = ""

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            classes.append(node.name)
            # Extract public methods
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not item.name.startswith("_")
                ):
                    functions.append(_extract_function(item, class_name=node.name))

        elif isinstance(item := node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not item.name.startswith("_"):
                functions.append(_extract_function(item))

        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id.endswith("_VERSION")
                    and isinstance(node.value, ast.Constant)
                ):
                    version = str(node.value.value)

    return ModuleInfo(
        module_name=module_name,
        docstring=mod_doc,
        classes=tuple(classes),
        functions=tuple(functions),
        version=version,
        file_path=file_path,
    )


def _extract_function(node: ast.FunctionDef | ast.AsyncFunctionDef, *, class_name: str = "") -> FunctionInfo:
    """Extract info from a function AST node."""
    doc = ast.get_docstring(node) or ""
    args: list[str] = []
    for arg in node.args.args:
        if arg.arg == "self":
            continue
        ann = ""
        if arg.annotation:
            ann = ast.unparse(arg.annotation)
        args.append(f"{arg.arg}: {ann}" if ann else arg.arg)

    ret_type = ""
    if node.returns:
        ret_type = ast.unparse(node.returns)

    return FunctionInfo(
        name=node.name,
        docstring=doc.split("\n")[0] if doc else "",
        args=tuple(args),
        return_type=ret_type,
        is_method=bool(class_name),
        class_name=class_name,
    )


# ── Core implementation ──────────────────────────────────────────────────────


class SkillGenerator:
    """Generates SKILL.md skeletons from Python tool modules.

    Parameters
    ----------
    project_root :
        Absolute path to the project root.
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root

    def inspect(self, module_path: str | Path) -> ModuleInfo:
        """Inspect a Python file and extract its public API.

        Parameters
        ----------
        module_path :
            Path to the .py file (relative to project root or absolute).
        """
        path = Path(module_path)
        if not path.is_absolute():
            path = self._root / path
        if not path.is_file():
            msg = f"Module not found: {path}"
            raise FileNotFoundError(msg)

        source = path.read_text(encoding="utf-8")
        module_name = path.stem.replace("-", "_")
        return _extract_module_info(source, module_name, str(path))

    def generate(self, module_path: str | Path, *, skill_name: str = "") -> str:
        """Generate a SKILL.md skeleton from a Python module.

        Parameters
        ----------
        module_path :
            Path to the .py file.
        skill_name :
            Override skill name (default: derived from module name).
        """
        info = self.inspect(module_path)
        name = skill_name or f"grimoire-{info.module_name.replace('_', '-')}"
        return self._render(info, name)

    def generate_and_save(
        self,
        module_path: str | Path,
        *,
        skill_name: str = "",
        output_dir: str = ".github/skills",
    ) -> Path:
        """Generate and save SKILL.md to disk.

        Returns the path of the created file.
        """
        info = self.inspect(module_path)
        name = skill_name or f"grimoire-{info.module_name.replace('_', '-')}"
        content = self._render(info, name)

        out = self._root / output_dir / name / "SKILL.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        logger.info("Generated SKILL.md: %s", out)
        return out

    def _render(self, info: ModuleInfo, skill_name: str) -> str:
        """Render a SKILL.md from module info."""
        now = time.strftime("%Y-%m-%d")
        lines: list[str] = [
            "---",
            f'title: "{skill_name}"',
            f"created: {now}",
            f"source_module: {info.module_name}",
            "auto_generated: true",
            "---",
            "",
            f"# {skill_name}",
            "",
        ]

        # Module description
        if info.docstring:
            first_para = info.docstring.split("\n\n")[0]
            lines.append(first_para)
            lines.append("")

        if info.version:
            lines.append(f"**Version**: {info.version}")
            lines.append("")

        # Trigger section
        lines.append("## Triggers")
        lines.append("")
        lines.append("Use this skill when:")
        lines.append("")
        lines.append(f"- Working with `{info.module_name}` functionality")
        for cls in info.classes:
            lines.append(f"- Using `{cls}` operations")
        lines.append("")

        # API Reference
        if info.classes or info.functions:
            lines.append("## API Reference")
            lines.append("")

        for cls in info.classes:
            lines.append(f"### `{cls}`")
            lines.append("")
            methods = [f for f in info.functions if f.class_name == cls]
            if methods:
                lines.append("| Method | Description |")
                lines.append("|---|---|")
                for m in methods:
                    lines.append(f"| `{m.signature_display()}` | {m.docstring} |")
                lines.append("")

        # Top-level functions
        top_funcs = [f for f in info.functions if not f.is_method]
        if top_funcs:
            lines.append("### Functions")
            lines.append("")
            lines.append("| Function | Description |")
            lines.append("|---|---|")
            for f in top_funcs:
                lines.append(f"| `{f.signature_display()}` | {f.docstring} |")
            lines.append("")

        # Process section (skeleton)
        lines.extend([
            "## Process",
            "",
            "1. **Identify** — Determine what operation is needed",
            "2. **Configure** — Set appropriate parameters",
            "3. **Execute** — Run the operation",
            "4. **Verify** — Confirm the result",
            "",
            "## Quality Checklist",
            "",
            "- [ ] Parameters validated before execution",
            "- [ ] Error handling for edge cases",
            "- [ ] Results verified against expectations",
            "- [ ] Learnings captured if applicable",
            "",
        ])

        return "\n".join(lines)
