"""Tests for codegraph/ — ast parser, in-memory graph, impact query."""

from __future__ import annotations

import textwrap
from pathlib import Path

from grimoire.codegraph.graph import CodeGraph
from grimoire.codegraph.parser import PythonASTParser
from grimoire.codegraph.schemas import EdgeKind, ImpactQuery, NodeKind


def _write_py(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


class TestPythonASTParser:
    def test_parses_module_node(self, tmp_path) -> None:
        f = _write_py(tmp_path, "mod.py", "x = 1\n")
        parser = PythonASTParser(root=str(tmp_path))
        nodes, _ = parser.parse_file(f)
        assert any(n.kind == NodeKind.MODULE for n in nodes)

    def test_parses_class(self, tmp_path) -> None:
        f = _write_py(tmp_path, "cls.py", """
            class Foo:
                pass
        """)
        parser = PythonASTParser(root=str(tmp_path))
        nodes, _ = parser.parse_file(f)
        assert any(n.kind == NodeKind.CLASS and n.name == "Foo" for n in nodes)

    def test_parses_function(self, tmp_path) -> None:
        f = _write_py(tmp_path, "fn.py", """
            def my_func(x):
                return x + 1
        """)
        parser = PythonASTParser(root=str(tmp_path))
        nodes, _ = parser.parse_file(f)
        assert any(n.kind == NodeKind.FUNCTION and n.name == "my_func" for n in nodes)

    def test_parses_method(self, tmp_path) -> None:
        f = _write_py(tmp_path, "meth.py", """
            class Bar:
                def do_thing(self):
                    pass
        """)
        parser = PythonASTParser(root=str(tmp_path))
        nodes, _ = parser.parse_file(f)
        assert any(n.kind == NodeKind.METHOD and n.name == "do_thing" for n in nodes)

    def test_parses_imports(self, tmp_path) -> None:
        f = _write_py(tmp_path, "imp.py", """
            import os
            from pathlib import Path
        """)
        parser = PythonASTParser(root=str(tmp_path))
        _, edges = parser.parse_file(f)
        import_targets = [e.to_node for e in edges if e.kind == EdgeKind.IMPORTS]
        assert "os" in import_targets
        assert "pathlib" in import_targets

    def test_is_test_flag(self, tmp_path) -> None:
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        f = _write_py(test_dir, "test_foo.py", """
            def test_bar():
                pass
        """)
        parser = PythonASTParser(root=str(tmp_path))
        nodes, _ = parser.parse_file(f)
        assert all(n.is_test for n in nodes)

    def test_defines_edges(self, tmp_path) -> None:
        f = _write_py(tmp_path, "def_edge.py", """
            class MyClass:
                def my_method(self):
                    pass
        """)
        parser = PythonASTParser(root=str(tmp_path))
        _, edges = parser.parse_file(f)
        defines = [e for e in edges if e.kind == EdgeKind.DEFINES]
        assert len(defines) >= 2  # module→class, class→method

    def test_inheritance_edge(self, tmp_path) -> None:
        f = _write_py(tmp_path, "inh.py", """
            class Child(Base):
                pass
        """)
        parser = PythonASTParser(root=str(tmp_path))
        _, edges = parser.parse_file(f)
        inherits = [e for e in edges if e.kind == EdgeKind.INHERITS]
        assert any(e.to_node == "Base" for e in inherits)

    def test_syntax_error_returns_empty(self, tmp_path) -> None:
        f = tmp_path / "bad.py"
        f.write_text("def (broken:", encoding="utf-8")
        parser = PythonASTParser(root=str(tmp_path))
        nodes, edges = parser.parse_file(f)
        assert nodes == []
        assert edges == []


class TestCodeGraph:
    def test_index_directory(self, tmp_path) -> None:
        _write_py(tmp_path, "a.py", "x = 1\n")
        _write_py(tmp_path, "b.py", "y = 2\n")
        graph = CodeGraph()
        count = graph.index_directory(tmp_path)
        assert count >= 2

    def test_get_node(self, tmp_path) -> None:
        _write_py(tmp_path, "getnode.py", """
            def hello():
                pass
        """)
        graph = CodeGraph()
        graph.index_directory(tmp_path, root=str(tmp_path))
        assert any("hello" in nid for nid in graph._nodes)

    def test_nodes_in_file(self, tmp_path) -> None:
        f = _write_py(tmp_path, "nif.py", """
            class Nif:
                def method(self):
                    pass
        """)
        graph = CodeGraph()
        graph.index_file(f, root=str(tmp_path))
        nodes = graph.nodes_in_file(str(f))
        assert len(nodes) >= 3  # module + class + method

    def test_hotspots(self, tmp_path) -> None:
        _write_py(tmp_path, "popular.py", """
            def popular_func():
                pass
        """)
        _write_py(tmp_path, "user1.py", "from popular import popular_func\n")
        _write_py(tmp_path, "user2.py", "from popular import popular_func\n")
        graph = CodeGraph()
        graph.index_directory(tmp_path, root=str(tmp_path))
        spots = graph.hotspots(top_n=5)
        assert len(spots) <= 5

    def test_impact_query_unknown_node(self, tmp_path) -> None:
        graph = CodeGraph()
        result = graph.impact_query(ImpactQuery(node_id="does.not.exist"))
        assert result.affected_nodes == ()
        assert result.affected_files == ()

    def test_stats(self, tmp_path) -> None:
        _write_py(tmp_path, "stat.py", "x = 1\n")
        graph = CodeGraph()
        graph.index_directory(tmp_path)
        s = graph.stats()
        assert s["nodes"] > 0
        assert "edges" in s
        assert "files" in s

    def test_nodes_and_edges_public_accessors(self, tmp_path) -> None:
        _write_py(tmp_path, "accessors.py", """
            def hello():
                return "world"
        """)
        graph = CodeGraph()
        graph.index_directory(tmp_path, root=str(tmp_path))

        assert graph.nodes
        assert isinstance(graph.nodes, tuple)
        assert isinstance(graph.edges, tuple)

    def test_get_dependencies(self, tmp_path) -> None:
        _write_py(tmp_path, "dep.py", """
            import os

            def use_os():
                return os.getcwd()
        """)
        graph = CodeGraph()
        graph.index_directory(tmp_path, root=str(tmp_path))
        module_nodes = [n for n in graph._nodes.values() if n.kind == NodeKind.MODULE and "dep" in n.name]
        assert module_nodes
        deps = graph.get_dependencies(module_nodes[0].id)
        assert any(d.name == "os" or "os" in d.id for d in deps)

    def test_index_excludes_venv(self, tmp_path) -> None:
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        _write_py(venv, "internal.py", "x = 1\n")
        _write_py(tmp_path, "real.py", "y = 2\n")
        graph = CodeGraph()
        graph.index_directory(tmp_path, exclude={".venv"})
        files = set(graph._by_file.keys())
        assert not any(".venv" in f for f in files)
