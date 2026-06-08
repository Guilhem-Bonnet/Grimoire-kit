"""Code Graph schemas — CodeNode, CodeEdge, ImpactQuery, ImpactResult."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class NodeKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    IMPORT = "import"
    CONSTANT = "constant"
    VARIABLE = "variable"


class EdgeKind(StrEnum):
    IMPORTS = "imports"
    CALLS = "calls"
    DEFINES = "defines"
    INHERITS = "inherits"
    REFERENCES = "references"
    TESTED_BY = "tested_by"
    OVERRIDES = "overrides"


@dataclass(frozen=True, slots=True)
class CodeNode:
    """A symbol in the code graph (module, class, function, …)."""

    id: str
    kind: NodeKind
    name: str
    file_path: str
    line_start: int
    line_end: int
    module: str = ""
    docstring: str = ""
    is_test: bool = False
    is_public: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "name": self.name,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "module": self.module,
            "docstring": self.docstring,
            "is_test": self.is_test,
            "is_public": self.is_public,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CodeNode:
        return cls(
            id=d["id"],
            kind=NodeKind(d["kind"]),
            name=d["name"],
            file_path=d["file_path"],
            line_start=int(d["line_start"]),
            line_end=int(d["line_end"]),
            module=d.get("module", ""),
            docstring=d.get("docstring", ""),
            is_test=bool(d.get("is_test", False)),
            is_public=bool(d.get("is_public", True)),
        )


@dataclass(frozen=True, slots=True)
class CodeEdge:
    from_node: str
    to_node: str
    kind: EdgeKind

    def to_dict(self) -> dict[str, Any]:
        return {"from_node": self.from_node, "to_node": self.to_node, "kind": self.kind.value}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CodeEdge:
        return cls(from_node=d["from_node"], to_node=d["to_node"], kind=EdgeKind(d["kind"]))


@dataclass(frozen=True, slots=True)
class ImpactQuery:
    node_id: str
    depth: int = 2
    include_tests: bool = True
    edge_kinds: tuple[EdgeKind, ...] = (EdgeKind.IMPORTS, EdgeKind.CALLS, EdgeKind.INHERITS, EdgeKind.REFERENCES)


@dataclass(frozen=True, slots=True)
class ImpactResult:
    root_node_id: str
    affected_nodes: tuple[CodeNode, ...]
    affected_files: tuple[str, ...]
    test_files: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_node_id": self.root_node_id,
            "affected_nodes": [n.to_dict() for n in self.affected_nodes],
            "affected_files": list(self.affected_files),
            "test_files": list(self.test_files),
        }
