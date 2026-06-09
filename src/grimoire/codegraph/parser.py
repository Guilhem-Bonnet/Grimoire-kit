"""AST-based Python code parser — extracts CodeNodes and CodeEdges from .py files."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from grimoire.codegraph.schemas import CodeEdge, CodeNode, EdgeKind, NodeKind

__all__ = ["PythonASTParser"]

_TEST_FILE_PATTERN = re.compile(r"(test_|_test\.py$|/tests?/)", re.IGNORECASE)
_TEST_FUNC_PATTERN = re.compile(r"^test_")


def _is_test_file(file_path: str) -> bool:
    return bool(_TEST_FILE_PATTERN.search(file_path))


def _is_test_func(name: str) -> bool:
    return bool(_TEST_FUNC_PATTERN.match(name))


def _file_to_module(file_path: str, root: str) -> str:
    """Convert a file path to a dotted module name relative to root."""
    try:
        rel = Path(file_path).relative_to(root)
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)
    except ValueError:
        return Path(file_path).stem


def _file_to_test_path(file_path: str, root: str) -> str:
    try:
        return str(Path(file_path).relative_to(root))
    except ValueError:
        return file_path


def _docstring(node: ast.AST) -> str:
    try:
        doc = ast.get_docstring(node)  # type: ignore[arg-type]
        return (doc or "")[:200]
    except (AttributeError, TypeError):
        return ""


def _node_id(module: str, name: str, kind: NodeKind) -> str:
    return f"{module}:{name}" if module else name


class PythonASTParser:
    """Parse Python files into CodeNode/CodeEdge lists using the stdlib ast module."""

    def __init__(self, root: str = "") -> None:
        self._root = root

    def parse_file(self, path: Path) -> tuple[list[CodeNode], list[CodeEdge]]:
        """Parse a single .py file. Returns (nodes, edges)."""
        nodes: list[CodeNode] = []
        edges: list[CodeEdge] = []
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            return nodes, edges

        file_path = str(path)
        module = _file_to_module(file_path, self._root)
        is_test = _is_test_file(_file_to_test_path(file_path, self._root))

        # Module node
        module_node = CodeNode(
            id=_node_id(module, module, NodeKind.MODULE),
            kind=NodeKind.MODULE,
            name=module,
            file_path=file_path,
            line_start=1,
            line_end=len(source.splitlines()),
            module=module,
            docstring=_docstring(tree),
            is_test=is_test,
            is_public=not module.startswith("_"),
        )
        nodes.append(module_node)

        visitor = _ASTVisitor(module_node, module, file_path, is_test, self._root)
        visitor.visit(tree)
        nodes.extend(visitor.nodes)
        edges.extend(visitor.edges)

        return nodes, edges

    def parse_directory(
        self,
        path: Path,
        exclude: set[str] | None = None,
    ) -> tuple[list[CodeNode], list[CodeEdge]]:
        """Recursively parse all .py files in a directory."""
        exclude = exclude or {"__pycache__", ".venv", ".git", "node_modules"}
        all_nodes: list[CodeNode] = []
        all_edges: list[CodeEdge] = []
        for py_file in sorted(path.rglob("*.py")):
            if any(part in exclude for part in py_file.parts):
                continue
            nodes, edges = self.parse_file(py_file)
            all_nodes.extend(nodes)
            all_edges.extend(edges)
        return all_nodes, all_edges


class _ASTVisitor(ast.NodeVisitor):
    def __init__(self, module_node: CodeNode, module: str, file_path: str, is_test: bool, root: str) -> None:
        self._module_node = module_node
        self._module = module
        self._file_path = file_path
        self._is_test = is_test
        self._root = root
        self._class_stack: list[str] = []
        self.nodes: list[CodeNode] = []
        self.edges: list[CodeEdge] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            target = alias.name.split(".")[0]
            self.edges.append(CodeEdge(
                from_node=self._module_node.id,
                to_node=target,
                kind=EdgeKind.IMPORTS,
            ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            target = node.module.split(".")[0]
            self.edges.append(CodeEdge(
                from_node=self._module_node.id,
                to_node=target,
                kind=EdgeKind.IMPORTS,
            ))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        class_id = _node_id(self._module, node.name, NodeKind.CLASS)
        class_node = CodeNode(
            id=class_id,
            kind=NodeKind.CLASS,
            name=node.name,
            file_path=self._file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            module=self._module,
            docstring=_docstring(node),
            is_test=self._is_test or _is_test_func(node.name),
            is_public=not node.name.startswith("_"),
        )
        self.nodes.append(class_node)
        self.edges.append(CodeEdge(from_node=self._module_node.id, to_node=class_id, kind=EdgeKind.DEFINES))

        for base in node.bases:
            base_name = self._extract_name(base)
            if base_name:
                self.edges.append(CodeEdge(from_node=class_id, to_node=base_name, kind=EdgeKind.INHERITS))

        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_method = bool(self._class_stack)
        kind = NodeKind.METHOD if is_method else NodeKind.FUNCTION
        parent_name = self._class_stack[-1] if is_method else self._module
        parent_id = (
            _node_id(self._module, parent_name, NodeKind.CLASS)
            if is_method
            else self._module_node.id
        )
        func_id = _node_id(self._module, f"{parent_name}.{node.name}" if is_method else node.name, kind)
        func_node = CodeNode(
            id=func_id,
            kind=kind,
            name=node.name,
            file_path=self._file_path,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            module=self._module,
            docstring=_docstring(node),
            is_test=self._is_test or _is_test_func(node.name),
            is_public=not node.name.startswith("_"),
        )
        self.nodes.append(func_node)
        self.edges.append(CodeEdge(from_node=parent_id, to_node=func_id, kind=EdgeKind.DEFINES))

        # Collect call edges
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                callee = self._extract_name(child.func)
                if callee and callee != func_id:
                    self.edges.append(CodeEdge(from_node=func_id, to_node=callee, kind=EdgeKind.CALLS))

        self.generic_visit(node)

    @staticmethod
    def _extract_name(node: Any) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""
