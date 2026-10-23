"""W7-01 — connaissance d'un projet existant, dérivée du code, sourcée et vérifiable.

Projet jetable Python + JS ; aucune exécution de modèle, aucun réseau.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

pytestmark = pytest.mark.covers("cli:onboard brownfield")

runner = CliRunner()
KB_JSON = "_grimoire-output/knowledge/project-knowledge.json"
KB_MD = "_grimoire-output/knowledge/project-knowledge.md"

FILES = {
    "pyproject.toml": (
        '[project]\nname = "shop"\nversion = "0.1.0"\ndependencies = [\n    "requests>=2",\n]\n\n'
        '[project.scripts]\nshop = "shop.cli:main"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    ),
    "src/shop/__init__.py": '"""Boutique : catalogue et commandes."""\n',
    "src/shop/store.py": '"""Stockage des produits en mémoire."""\n\nPRODUCTS = {}\n\n\ndef add(name, price):\n    PRODUCTS[name] = price\n',
    "src/shop/cli.py": (
        "import sys\n\nfrom shop.store import add\n\n\ndef main():\n    add(sys.argv[1], 1)\n\n\n"
        'if __name__ == "__main__":\n    main()\n'
    ),
    "src/shop/api/__init__.py": "",
    "src/shop/api/routes.py": (
        "import requests\nfrom flask import Flask\n\nfrom ..store import PRODUCTS\n\napp = Flask(__name__)\n\n\n"
        'def latest_version():\n    return requests.get("x")\n'
    ),
    "tests/test_store.py": 'from shop.store import add\n\n\ndef test_add():\n    add("a", 1)\n',
    "package.json": (
        '{\n  "name": "shop-web",\n  "scripts": {\n    "test": "node --test",\n    "start": "node web/server.js"\n  },\n'
        '  "dependencies": {\n    "express": "^4.0.0"\n  }\n}\n'
    ),
    "web/server.js": "const express = require('express');\nconst { price } = require('./lib/price');\nexpress().listen(3000);\n",
    "web/lib/price.js": "export function price(x) { return x * 2; }\n",
    "web/test/price.test.js": "import { price } from '../lib/price.js';\n",
    # L'installation du kit et les dépendances installées ne sont pas le code du projet.
    "_grimoire/kit/framework/tool.py": '"""outil du kit"""\n',
    ".venv/lib/site.py": "x = 1\n",
    "node_modules/express/index.js": "module.exports = 1;\n",
}


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    for rel, text in FILES.items():
        _write(root, rel, text)
    return root


def _run(*args: str) -> Any:
    return runner.invoke(app, ["onboard", "brownfield", *args])


def _kb(root: Path) -> dict[str, Any]:
    return json.loads((root / KB_JSON).read_text(encoding="utf-8"))


def _facts(root: Path) -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in _kb(root)["facts"]}


class TestBuild:
    def test_writes_markdown_and_json(self, project: Path) -> None:
        result = _run(str(project))
        assert result.exit_code == 0, result.output
        assert (project / KB_JSON).is_file() and (project / KB_MD).is_file()
        md = (project / KB_MD).read_text(encoding="utf-8")
        assert "## Composants" in md and "## Questions ouvertes" in md
        assert len(md.splitlines()) < 120  # une vue courte, le détail est dans le JSON

    def test_every_source_exists_at_the_cited_line(self, project: Path) -> None:
        _run(str(project))
        for fact in _kb(project)["facts"]:
            if not fact["sources"]:
                assert fact["id"] == "coverage:unmeasured"  # seul fait d'absence attendu ici
                continue
            assert fact["fingerprint"], fact["id"]
            for ref in fact["sources"]:
                path, _, line = ref.rpartition(":") if ref.rpartition(":")[2].isdigit() else (ref, "", "")
                target = project / path
                assert target.is_file(), ref
                if line:
                    assert 1 <= int(line) <= max(len(target.read_text(encoding="utf-8").splitlines()), 1), ref

    def test_two_runs_write_the_same_bytes(self, project: Path) -> None:
        _run(str(project))
        first = ((project / KB_JSON).read_bytes(), (project / KB_MD).read_bytes())
        _run(str(project))
        assert ((project / KB_JSON).read_bytes(), (project / KB_MD).read_bytes()) == first

    def test_kit_install_and_dependencies_are_not_read(self, project: Path) -> None:
        _run(str(project))
        files = _kb(project)["files"]
        assert not any(k.startswith(("_grimoire/", ".venv/", "node_modules/")) for k in files)
        assert "src/shop/store.py" in files and "web/server.js" in files

    def test_no_network_and_no_model(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _refuse(*_a: object, **_k: object) -> None:
            raise AssertionError("réseau interdit pendant l'onboarding")

        monkeypatch.setattr(socket.socket, "connect", _refuse)
        monkeypatch.setattr(socket, "create_connection", _refuse)
        assert _run(str(project)).exit_code == 0


class TestFacts:
    def test_responsibility_only_when_declared(self, project: Path) -> None:
        _run(str(project))
        facts = _facts(project)
        store = facts["component:shop.store"]
        assert store["data"]["responsibility"] == "Stockage des produits en mémoire."
        assert store["sources"][0] == "src/shop/store.py:1"
        assert facts["component:shop.cli"]["data"]["responsibility"] == ""
        keys = {q["key"] for q in _kb(project)["questions"]}
        assert {"onboard:purpose", "onboard:responsibility:shop.cli", "onboard:responsibility:shop.api"} <= keys

    def test_entry_points_with_their_lines(self, project: Path) -> None:
        _run(str(project))
        facts = _facts(project)
        assert facts["entry:pyproject:shop"]["sources"] == ["pyproject.toml:9"]
        assert facts["entry:script:src/shop/cli.py"]["sources"] == ["src/shop/cli.py:10"]
        assert facts["entry:server:src/shop/api/routes.py:6"]["data"]["framework"] == "Flask"
        assert facts["entry:npm:start"]["sources"] == ["package.json:5"]
        assert facts["entry:server:web/server.js:3"]["sources"] == ["web/server.js:3"]

    def test_internal_dependency_through_relative_import(self, project: Path) -> None:
        _run(str(project))
        dep = _facts(project)["internal:shop.api"]
        assert dep["data"]["depends_on"] == ["shop.store"]
        assert dep["sources"] == ["src/shop/api/routes.py:4"]

    def test_dependencies_declared_and_imported(self, project: Path) -> None:
        _run(str(project))
        facts = _facts(project)
        assert facts["declared:pyproject:requests>=2"]["sources"] == ["pyproject.toml:5"]
        assert facts["imported:flask"]["sources"] == ["src/shop/api/routes.py:2"]
        assert facts["imported:js:express"]["sources"] == ["web/server.js:1"]
        assert "imported:sys" not in facts  # bibliothèque standard

    def test_commands_observed_assumed_or_unknown_never_invented(self, project: Path) -> None:
        _run(str(project))
        commands = _kb(project)["summary"]["commands"]
        assert commands["test-runner"] == {"status": "observed", "command": "pytest"}
        assert commands["lint"]["status"] == "assumed"
        # package.json n'a pas de script build : le défaut `npm run build` serait faux.
        assert commands["build"] == {"status": "unknown", "command": ""}
        assert _facts(project)["command:test-runner"]["sources"] == ["pyproject.toml:11"]
        assert "onboard:command:lint" in {q["key"] for q in _kb(project)["questions"]}

    def test_test_map_and_untested_components(self, project: Path) -> None:
        _run(str(project))
        facts = _facts(project)
        assert facts["testmap:tests/test_store.py"]["data"]["components"] == ["shop.store"]
        assert facts["testmap:web/test/price.test.js"]["data"]["components"] == ["web"]
        assert "untested:shop.cli" in facts and "untested:shop.store" not in facts
        assert "untested:web" not in facts

    def test_coverage_unknown_is_not_zero_and_low_coverage_is_cited(self, project: Path) -> None:
        _run(str(project))
        assert _facts(project)["coverage:unmeasured"]["data"]["measured"] is False
        _write(project, "coverage.xml", (
            '<?xml version="1.0" ?>\n<coverage line-rate="0.6">\n<packages><package><classes>\n'
            '<class filename="src/shop/cli.py" line-rate="0.2"/>\n'
            '<class filename="src/shop/store.py" line-rate="1.0"/>\n'
            "</classes></package></packages>\n</coverage>\n"
        ))
        _run(str(project))
        facts = _facts(project)
        assert "coverage:unmeasured" not in facts
        assert facts["coverage:low:src/shop/cli.py"]["sources"] == ["coverage.xml:4"]
        assert "coverage:low:src/shop/store.py" not in facts

    def test_large_module_is_a_risk_and_a_question(self, project: Path) -> None:
        _write(project, "src/shop/big.py", "".join(f"V{i} = {i}\n" for i in range(450)))
        _run(str(project))
        assert _facts(project)["large:src/shop/big.py"]["data"]["lines"] == 450
        assert "onboard:large:src/shop/big.py" in {q["key"] for q in _kb(project)["questions"]}


class TestQuestions:
    def test_questions_are_a_structured_batch(self, project: Path) -> None:
        from grimoire.missions.questions import parse_specs

        _run(str(project))
        specs = parse_specs(_kb(project))
        assert specs and all(not s.blocking and not s.problem() and s.key and s.why for s in specs)


class TestFreshness:
    def test_current_right_after_build(self, project: Path) -> None:
        _run(str(project))
        result = _run(str(project), "--check", "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["verdict"] == "current"

    def test_modified_file_is_stale_and_names_the_facts(self, project: Path) -> None:
        _run(str(project))
        _write(project, "src/shop/store.py", FILES["src/shop/store.py"] + "\nTAX = 2\n")
        result = _run(str(project), "--check", "--json")
        assert result.exit_code == 1
        verdict = json.loads(result.output)
        assert verdict["verdict"] == "stale" and verdict["modified"] == ["src/shop/store.py"]
        assert "component:shop.store" in verdict["stale_facts"]
        assert "entry:npm:start" not in verdict["stale_facts"]

    def test_added_file_is_stale(self, project: Path) -> None:
        _run(str(project))
        _write(project, "src/shop/new.py", "x = 1\n")
        verdict = json.loads(_run(str(project), "--check", "--json").output)
        assert (verdict["verdict"], verdict["added"]) == ("stale", ["src/shop/new.py"])

    def test_manifest_change_stales_its_facts(self, project: Path) -> None:
        _run(str(project))
        _write(project, "package.json", FILES["package.json"].replace("node --test", "jest"))
        verdict = json.loads(_run(str(project), "--check", "--json").output)
        assert verdict["verdict"] == "stale" and "entry:npm:start" in verdict["stale_facts"]

    def test_no_knowledge_is_unverified_not_current(self, project: Path) -> None:
        result = _run(str(project), "--check", "--json")
        assert result.exit_code == 1
        assert json.loads(result.output) == {
            "verdict": "unverified", "reason": "no_knowledge", "added": [], "modified": [], "removed": [], "stale_facts": [],
        }

    @pytest.mark.parametrize("payload", ["{not json", '{"schema": 99}', '{"schema": 1, "kind": "grimoire.project-knowledge"}'])
    def test_unreadable_knowledge_is_unverified(self, project: Path, payload: str) -> None:
        _write(project, KB_JSON, payload)
        verdict = json.loads(_run(str(project), "--check", "--json").output)
        assert verdict["verdict"] == "unverified"

    def test_check_writes_nothing(self, project: Path) -> None:
        _run(str(project), "--check")
        assert not (project / "_grimoire-output").exists()


class TestAgentsAndInit:
    def test_shared_context_block_is_idempotent_and_keeps_the_rest(self, project: Path) -> None:
        _write(project, "_grimoire/_memory/shared-context.md", "# Shared Context\n\nNotes de l'équipe.\n")
        _run(str(project))
        _run(str(project))
        text = (project / "_grimoire/_memory/shared-context.md").read_text(encoding="utf-8")
        assert text.startswith("# Shared Context\n\nNotes de l'équipe.\n")
        assert text.count("grimoire:project-knowledge:start") == 1
        assert "project-knowledge.md" in text and "--check" in text

    def test_shared_context_is_never_created(self, project: Path) -> None:
        _run(str(project))
        assert not (project / "_grimoire/_memory/shared-context.md").exists()

    def test_no_memory_flag(self, project: Path) -> None:
        _write(project, "_grimoire/_memory/shared-context.md", "# Shared Context\n")
        _run(str(project), "--no-memory")
        assert "project-knowledge" not in (project / "_grimoire/_memory/shared-context.md").read_text(encoding="utf-8")

    def test_init_offers_onboarding_on_existing_code_only(self, project: Path, tmp_path: Path) -> None:
        from grimoire.cli.cmd_init import brownfield_offer

        offer = brownfield_offer(project)
        assert offer is not None and offer["command"] == "grimoire onboard brownfield" and offer["model_calls"] == 0
        empty = tmp_path / "empty"
        empty.mkdir()
        assert brownfield_offer(empty) is None
        _run(str(project))
        assert brownfield_offer(project) is None  # déjà documenté : on ne repropose pas
