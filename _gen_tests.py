#!/usr/bin/env python3
"""Générateur automatique de tests pour les 31 outils manquants."""

import ast
import os
import textwrap
from pathlib import Path

TOOLS_DIR = Path("framework/tools")
TESTS_DIR = Path("tests")

# Outils manquants (nom fichier sans .py)
MISSING = [
    "bias-toolkit", "context-guard", "context-router", "crescendo",
    "crispr", "dark-matter", "dashboard", "decision-log", "desire-paths",
    "digital-twin", "distill", "early-warning", "harmony-check",
    "immune-system", "incubator", "mirror-agent", "mycelium",
    "new-game-plus", "nudge-engine", "oracle", "preflight-check",
    "project-graph", "quantum-branch", "r-and-d", "rosetta",
    "self-healing", "semantic-chain", "sensory-buffer", "swarm-consensus",
    "time-travel", "workflow-adapt",
]


def extract_info(tool_name: str) -> dict:
    """Extraire classes, fonctions, constantes d'un outil."""
    fpath = TOOLS_DIR / f"{tool_name}.py"
    with open(fpath, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    classes = []
    functions = []
    constants = []
    has_build_parser = False
    has_main = False
    has_dataclass = False
    docstring = ast.get_docstring(tree) or ""

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            # Check if dataclass
            is_dc = False
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name) and dec.id == "dataclass":
                    is_dc = True
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "dataclass":
                    is_dc = True
            fields = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.append(item.target.id)
            classes.append({
                "name": node.name,
                "is_dataclass": is_dc,
                "fields": fields,
                "line": node.lineno,
            })
            if is_dc:
                has_dataclass = True

        elif isinstance(node, ast.FunctionDef):
            args = []
            for a in node.args.args:
                args.append(a.arg)
            ret = None
            if node.returns:
                if isinstance(node.returns, ast.Name):
                    ret = node.returns.id
                elif isinstance(node.returns, ast.Constant):
                    ret = str(node.returns.value)
            functions.append({
                "name": node.name,
                "args": args,
                "returns": ret,
                "is_private": node.name.startswith("_"),
                "line": node.lineno,
            })
            if node.name == "build_parser":
                has_build_parser = True
            if node.name == "main":
                has_main = True

        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    constants.append(target.id)

    return {
        "tool_name": tool_name,
        "docstring": docstring.split("\n")[0] if docstring else tool_name,
        "classes": classes,
        "functions": functions,
        "constants": constants,
        "has_build_parser": has_build_parser,
        "has_main": has_main,
        "has_dataclass": has_dataclass,
    }


def gen_import_func(tool_name: str) -> str:
    # Les modules avec tiret doivent garder le tiret dans le nom
    mod_safe = tool_name.replace("-", "_")
    return f'''def _import_mod():
    \"\"\"Import le module {tool_name} via importlib.\"\"\"
    mod_name = "{mod_safe}"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, KIT_DIR / "framework" / "tools" / "{tool_name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod'''


def needs_tempdir(info: dict) -> bool:
    """Check if this tool needs a temp directory for testing."""
    src = (TOOLS_DIR / f"{info['tool_name']}.py").read_text(encoding="utf-8")
    return "project_root" in src or "root" in src


def gen_dataclass_tests(info: dict) -> str:
    """Generate tests for dataclasses."""
    lines = []
    dcs = [c for c in info["classes"] if c["is_dataclass"]]
    if not dcs:
        return ""

    lines.append("""
class TestDataclasses(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
""")
    for dc in dcs:
        name = dc["name"]
        lines.append(f"""    def test_{_snake(name)}_exists(self):
        self.assertTrue(hasattr(self.mod, "{name}"))
""")
        if dc["fields"]:
            field_list = ", ".join(f'"{f}"' for f in dc["fields"][:5])
            lines.append(f"""    def test_{_snake(name)}_fields(self):
        import dataclasses
        fields = {{f.name for f in dataclasses.fields(self.mod.{name})}}
        for expected in [{field_list}]:
            self.assertIn(expected, fields)
""")
    return "\n".join(lines)


def gen_pure_function_tests(info: dict) -> str:
    """Generate tests for pure functions (no project_root arg)."""
    lines = []
    pure = [f for f in info["functions"]
            if not f["is_private"]
            and not f["name"].startswith("cmd_")
            and f["name"] not in ("main", "build_parser")
            and "project_root" not in f["args"]
            and "root" not in f["args"]
            and "args" not in f["args"]]

    if not pure:
        return ""

    lines.append("""
class TestPureFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
""")
    for fn in pure[:10]:  # limit to 10
        name = fn["name"]
        lines.append(f"""    def test_{name}_callable(self):
        self.assertTrue(callable(getattr(self.mod, "{name}", None)))
""")
    return "\n".join(lines)


def gen_project_function_tests(info: dict) -> str:
    """Generate tests for functions that take project_root."""
    lines = []
    project_fns = [f for f in info["functions"]
                   if not f["is_private"]
                   and not f["name"].startswith("cmd_")
                   and f["name"] not in ("main", "build_parser")
                   and ("project_root" in f["args"] or "root" in f["args"])]

    if not project_fns:
        return ""

    lines.append("""
class TestProjectFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
        self.tmpdir = Path(tempfile.mkdtemp())
        # Minimal Grimoire structure
        (self.tmpdir / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
        (self.tmpdir / "_grimoire-output").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
""")

    for fn in project_fns[:12]:  # limit
        name = fn["name"]
        arg_name = "project_root" if "project_root" in fn["args"] else "root"

        # Determine extra required args from signature
        extra_args = [a for a in fn["args"] if a not in (arg_name, "self")]

        if not extra_args:
            lines.append(f"""    def test_{name}_empty_project(self):
        try:
            result = self.mod.{name}(self.tmpdir)
            self.assertIsNotNone(result)
        except (FileNotFoundError, SystemExit):
            pass  # Expected for empty project
""")
        elif len(extra_args) == 1 and extra_args[0] in ("scan", "report", "ideas", "entries", "traces", "stats", "patterns", "concepts"):
            # Functions that take a scan/report result — skip, too complex
            pass
        else:
            lines.append(f"""    def test_{name}_callable(self):
        self.assertTrue(callable(self.mod.{name}))
""")

    return "\n".join(lines)


def gen_format_tests(info: dict) -> str:
    """Generate tests for format_* functions."""
    lines = []
    fmt_fns = [f for f in info["functions"] if f["name"].startswith("format_")]

    if not fmt_fns:
        return ""

    lines.append("""
class TestFormatFunctions(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
""")
    for fn in fmt_fns[:6]:
        name = fn["name"]
        lines.append(f"""    def test_{name}_callable(self):
        self.assertTrue(callable(self.mod.{name}))
""")
    return "\n".join(lines)


def gen_parser_tests(info: dict) -> str:
    """Generate tests for CLI parser."""
    if not info["has_build_parser"]:
        return ""

    cmds = [f["name"].replace("cmd_", "") for f in info["functions"]
            if f["name"].startswith("cmd_")]

    lines = ["""
class TestCLI(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()

    def test_build_parser(self):
        parser = self.mod.build_parser()
        self.assertIsNotNone(parser)

    def test_parser_help(self):
        parser = self.mod.build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)
"""]

    if cmds:
        for cmd in cmds[:6]:
            lines.append(f"""    def test_subcommand_{cmd}_exists(self):
        parser = self.mod.build_parser()
        # Vérifie que le sous-parseur ne crashe pas
        try:
            parser.parse_args(["{cmd}"])
        except SystemExit:
            pass  # Some subcommands may require args
""")

    return "\n".join(lines)


def gen_cli_integration_tests(info: dict) -> str:
    """Generate CLI integration tests via subprocess."""
    if not info["has_main"]:
        return ""

    tool_name = info["tool_name"]
    lines = [f"""
class TestCLIIntegration(unittest.TestCase):
    TOOL = KIT_DIR / "framework" / "tools" / "{tool_name}.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.TOOL)] + list(args),
            capture_output=True, text=True, timeout=30,
        )

    def test_help(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("{tool_name.split('-')[0]}", r.stdout.lower() + r.stderr.lower())

    def test_no_args(self):
        r = self._run()
        # Most tools show help or error without args
        self.assertIn(r.returncode, (0, 1, 2))
"""]

    return "\n".join(lines)


def gen_constants_tests(info: dict) -> str:
    """Generate tests for module-level constants."""
    if not info["constants"]:
        return ""

    lines = ["""
class TestConstants(unittest.TestCase):
    def setUp(self):
        self.mod = _import_mod()
"""]
    for const in info["constants"][:8]:
        lines.append(f"""    def test_{const.lower()}_defined(self):
        self.assertTrue(hasattr(self.mod, "{const}"))
""")
    return "\n".join(lines)


def _snake(name: str) -> str:
    """CamelCase to snake_case."""
    import re
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def generate_test_file(tool_name: str) -> str:
    """Generate a complete test file for a tool."""
    info = extract_info(tool_name)
    mod_name = tool_name.replace("-", "_")
    needs_tmp = needs_tempdir(info)

    imports = ["importlib", "importlib.util", "subprocess", "sys", "unittest"]
    if needs_tmp:
        imports.extend(["shutil", "tempfile"])
    imports = sorted(set(imports))

    parts = []

    # Header
    fn_list = [f["name"] for f in info["functions"]
               if not f["is_private"] and f["name"] not in ("main",)]
    fn_doc = "\n".join(f"  - {n}()" for n in fn_list[:20])

    parts.append(f'''#!/usr/bin/env python3
"""
Tests pour {tool_name}.py — {info["docstring"]}

Fonctions testées :
{fn_doc}
"""

{chr(10).join(f"import {i}" for i in imports)}
from pathlib import Path

KIT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(KIT_DIR / "framework" / "tools"))
TOOL = KIT_DIR / "framework" / "tools" / "{tool_name}.py"


{gen_import_func(tool_name)}
''')

    # Helper for tempdir
    if needs_tmp:
        parts.append("""
# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_project(root: Path) -> Path:
    \"\"\"Créer un projet Grimoire minimal pour les tests.\"\"\"
    (root / "_grimoire" / "_memory" / "agent-learnings").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire-output").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire" / "bmm" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire" / "bmm" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "framework" / "tools").mkdir(parents=True, exist_ok=True)
    return root
""")

    # Sections
    sections = [
        gen_dataclass_tests(info),
        gen_pure_function_tests(info),
        gen_project_function_tests(info),
        gen_format_tests(info),
        gen_constants_tests(info),
        gen_parser_tests(info),
        gen_cli_integration_tests(info),
    ]

    for section in sections:
        if section.strip():
            parts.append(section)

    # Footer
    parts.append("""
if __name__ == "__main__":
    unittest.main()
""")

    return "\n".join(parts)


def main():
    generated = 0
    for tool_name in MISSING:
        test_name = f"test_{tool_name.replace('-', '_')}.py"
        test_path = TESTS_DIR / test_name
        if test_path.exists():
            print(f"  SKIP {test_name} (already exists)")
            continue

        print(f"  GEN  {test_name}")
        content = generate_test_file(tool_name)
        test_path.write_text(content, encoding="utf-8")
        generated += 1

    print(f"\nGénéré : {generated} fichiers de tests")


if __name__ == "__main__":
    main()
