"""Le gate de ``flow run --executor dispatch`` exécute l'acceptance, pas seulement l'enveloppe (issue #428).

Rejeu du 2026-09-11 (épic #307, lot 3, commentaire sur #311/#336) : le nœud
``n4-schema-import`` du blueprint ``tasklib-hardening`` a été déclaré vert par
la cascade alors que ``pytest`` ne pouvait même pas collecter les tests
(dépendance absente) — le gate ne vérifiait que la conformité de l'enveloppe
JSON écrite par l'ouvrier, jamais l'acceptance réelle du nœud. Ce module
reproduit les quatre scénarios du critère d'arrêt de l'issue sur un nœud
unique dont l'acceptance déclare une commande ``pytest`` structurée
(``{"run": "..."}``), même mécanisme que celui qui aurait dû fermer n4.

Mêmes fournisseurs factices que ``test_flows_dispatch_executor.py`` : de vrais
scripts Python, exécutés par de vrais ``subprocess`` — jamais de mock, jamais
un vrai fournisseur LLM.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from grimoire.flows.dispatch_executor import node_dispatch_history, run_with_dispatch
from grimoire.flows.engine import FlowEngine

STANDARD = Path("_grimoire/standard")
REGISTRY = STANDARD / "llm-provider-registry.yaml"

_NODE_RE = r"Tâche \S+-(\w+) :"
_RESULT_RE = r"Écris ta sortie dans le fichier (\S+)"
_PIN_RE = r"^  - (\S+) : (\S+)$"


def _blueprint(tmp_path: Path, *, acceptance: list[object]) -> Path:
    blueprint = {
        "blueprintVersion": 1,
        "id": "acceptance-gate",
        "name": "Acceptance gate (issue #428)",
        "nodes": [
            {
                "id": "n",
                "kind": "pattern",
                "ref": "ORC-01",
                "label": "N",
                "acceptance": acceptance,
                "pins": [{"id": "out", "direction": "out", "contract": "c1"}],
            }
        ],
        "edges": [],
    }
    path = tmp_path / "acceptance-gate.blueprint.json"
    path.write_text(json.dumps(blueprint), encoding="utf-8")
    return path


def _envelope_writer(tmp_path: Path, name: str, *, extra_body: str = "") -> Path:
    """Un ouvrier factice qui écrit toujours une enveloppe conforme.

    *extra_body* (optionnel) s'exécute juste avant l'écriture de l'enveloppe —
    c'est ce qui permet à un fournisseur de « corriger le code » réellement
    (ré-écrire un fichier réel du projet) pour distinguer un palier qui
    échoue toujours d'un palier qui répare le travail.
    """
    body = (
        "import json, re, sys\n"
        "prompt = sys.argv[1]\n"
        f'path = re.search(r"{_RESULT_RE}", prompt).group(1)\n'
        "pins = {m.group(1): {\"contract\": m.group(2)} "
        f'for m in re.finditer(r"{_PIN_RE}", prompt, re.MULTILINE)}}\n'
        f"{extra_body}\n"
        "with open(path, \"w\", encoding=\"utf-8\") as fh:\n"
        "    json.dump({\"pins\": pins}, fh)\n"
    )
    script_path = tmp_path / "scripts" / name
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(body, encoding="utf-8")
    return script_path


def _invocation(script: Path) -> str:
    return f"{sys.executable} {script} {{prompt}} --model {{model}}"


def _provider_yaml(pid: str, tier: str, invocation: str) -> str:
    return (
        f'  - id: "{pid}"\n'
        f"    enabled: true\n"
        f'    provider_type: "hosted"\n'
        f'    allowed_capabilities: ["chat", "code"]\n'
        f'    default_models: ["{pid}-model"]\n'
        f'    currency: "api"\n'
        f'    invocation: "{invocation}"\n'
        f"    models:\n"
        f'      - id: "{pid}-model"\n'
        f'        tier: "{tier}"\n'
        f"    fallback_order: []\n"
    )


def _write_registry(root: Path, *providers_yaml: str) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    body = "\n".join(providers_yaml)
    (root / REGISTRY).write_text(
        f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
{body}
routing:
  default_provider: ""
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
""",
        encoding="utf-8",
    )


def _engine(tmp_path: Path) -> FlowEngine:
    return FlowEngine(kernel_root=tmp_path / "runtime", flows_root=tmp_path / "flows")


_MISSING_DEP_TEST = """\
import totally_missing_dependency_428

def test_ok():
    assert True
"""

_FAILING_TEST = """\
def test_should_pass_eventually():
    assert False, "pas encore corrige"
"""

_PASSING_TEST = """\
def test_should_pass_eventually():
    assert True
"""


def _pytest_acceptance(test_rel_path: str) -> list[object]:
    return [{"run": f"{sys.executable} -m pytest -q {test_rel_path}"}]


# ── 1. pytest non collectable (dépendance absente) → acceptance inexécutable ─


def test_pytest_non_collectable_est_un_refus_acceptance_inexecutable(tmp_path: Path) -> None:
    test_file = tmp_path / "tests_project" / "test_import.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(_MISSING_DEP_TEST, encoding="utf-8")

    cheap = _envelope_writer(tmp_path, "cheap.py")
    mid = _envelope_writer(tmp_path, "mid.py")
    _write_registry(
        tmp_path,
        _provider_yaml("cheap-writer", "cheap", _invocation(cheap)),
        _provider_yaml("mid-writer", "mid", _invocation(mid)),
    )
    bp = _blueprint(tmp_path, acceptance=_pytest_acceptance("tests_project/test_import.py"))
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "blocked"
    assert outcome.node_id == "n"
    node = outcome.nodes[0]
    assert node.verdict == "acceptance_unrunnable"
    assert node.acceptance_status == "unrunnable"
    # Aucune escalade vers `mid` : un environnement cassé ne se répare pas en
    # changeant de fournisseur — la cascade s'arrête net (issue #428).
    assert node.attempts == 1
    assert node.provider == "cheap-writer"


# ── 2. Un test qui échoue est rouge, puis l'escalade répare et ferme vert ────


def test_acceptance_qui_echoue_puis_escalade_vers_un_palier_qui_repare(tmp_path: Path) -> None:
    test_file = tmp_path / "tests_project" / "test_fix.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(_FAILING_TEST, encoding="utf-8")

    # cheap : écrit une enveloppe conforme mais NE corrige rien — le test réel
    # reste rouge. mid : corrige réellement le fichier avant d'écrire son
    # enveloppe — modélise un ouvrier qui répare le code, pas seulement un
    # script qui prétend avoir réussi.
    cheap = _envelope_writer(tmp_path, "cheap.py")
    mid = _envelope_writer(
        tmp_path,
        "mid.py",
        extra_body=f"open({str(test_file)!r}, 'w', encoding='utf-8').write({_PASSING_TEST!r})",
    )
    _write_registry(
        tmp_path,
        _provider_yaml("cheap-writer", "cheap", _invocation(cheap)),
        _provider_yaml("mid-writer", "mid", _invocation(mid)),
    )
    bp = _blueprint(tmp_path, acceptance=_pytest_acceptance("tests_project/test_fix.py"))
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished"
    node = outcome.nodes[0]
    assert node.verdict == "green"
    assert node.acceptance_status == "executed"
    assert node.escalations == 1
    assert node.attempts == 2
    assert node.provider == "mid-writer"


# ── 3. Un test qui passe dès le premier palier est vert, trace à l'appui ────


def test_acceptance_qui_passe_est_verte_avec_la_sortie_pytest_en_trace(tmp_path: Path) -> None:
    test_file = tmp_path / "tests_project" / "test_ok.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(_PASSING_TEST, encoding="utf-8")

    cheap = _envelope_writer(tmp_path, "cheap.py")
    _write_registry(tmp_path, _provider_yaml("cheap-writer", "cheap", _invocation(cheap)))
    bp = _blueprint(tmp_path, acceptance=_pytest_acceptance("tests_project/test_ok.py"))
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "finished"
    node = outcome.nodes[0]
    assert node.verdict == "green"
    assert node.acceptance_status == "executed"

    # La trace lue depuis le Mission Ledger (ce que `flow status` affiche)
    # porte la commande pytest réellement exécutée et sa sortie tronquée.
    history = node_dispatch_history(tmp_path, outcome.run_id, ["n"])
    assert len(history) == 1
    assert history[0]["acceptance_status"] == "executed"
    checks = history[0]["checks"]
    pytest_checks = [c for c in checks if "tests_project" in c["cmd"]]
    assert len(pytest_checks) == 1
    assert pytest_checks[0]["ok"] is True
    assert "passed" in pytest_checks[0]["output_excerpt"]


# ── 4. Garde : une enveloppe conforme sans acceptance exécutée ne suffit plus ─


def test_enveloppe_conforme_sans_acceptance_executee_ne_suffit_plus(tmp_path: Path) -> None:
    """Avant #428 : seule l'enveloppe comptait, ce node aurait fermé vert.

    L'ouvrier factice écrit toujours une enveloppe ``{"pins": {...}}``
    parfaitement conforme au contrat de sortie du node — exactement ce que
    l'ancien gate validait. La commande d'acceptance déclarée, elle,
    échoue systématiquement (aucun fournisseur, même en cascade complète, ne
    peut la faire réussir) : avec le correctif, le node reste bloqué au lieu
    de se fermer vert sur la seule foi de l'enveloppe.
    """
    cheap = _envelope_writer(tmp_path, "cheap.py")
    _write_registry(tmp_path, _provider_yaml("cheap-writer", "cheap", _invocation(cheap)))
    bp = _blueprint(
        tmp_path, acceptance=[{"run": f"{sys.executable} -c \"import sys; sys.exit(1)\""}]
    )
    engine = _engine(tmp_path)

    outcome = run_with_dispatch(engine, bp, project_root=tmp_path)

    assert outcome.status == "blocked"
    node = outcome.nodes[0]
    # Rouge ordinaire (code de sortie 1, aucun marqueur d'inexécutable) — pas
    # « acceptance_unrunnable » : la commande a bien tourné et a rendu un
    # verdict, celui-ci est juste négatif.
    assert node.verdict == "red"
    assert node.acceptance_status == "executed"

