"""Exemples rejouables : la doc ne cite que des exemples qu'on peut relancer.

Un exemple du kit n'est publiable que s'il est rejouable : un répertoire
``examples/<nom>/`` avec ``replay.sh`` (sans modèle, déterministe) et
``expected.json`` (sortie attendue + paquet de preuve). Trois gardes :

- chaque exemple cité par ``docs/getting-started*.md`` est rejouable ;
- chaque répertoire d'``examples/`` est rejouable ou figure dans la liste
  fermée des exemples illustratifs (qui portent la mention « non rejouable ») ;
- chaque rejeu est exécuté et sa sortie comparée à ``expected.json``.

Les gardes sont elles-mêmes éprouvées sur des arbres jetables : une garde qui
n'a jamais échoué n'a rien prouvé.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

KIT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = KIT_ROOT / "examples"
GETTING_STARTED = ("docs/getting-started.md", "docs/getting-started.en.md")

# Liste fermée : récits mis en forme à la main, jamais rejoués. Un nouvel
# exemple n'y entre pas — il doit être rejouable.
ILLUSTRATIVE = frozenset({"terraform-houseserver", "web-app-todo"})
ILLUSTRATIVE_MARKER = "non rejouable"

PROOF_KEYS = ("gate", "verdict", "score", "cost")
# Bilan imprimé par `blueprint evals` : « 1/1 réussis sur 5 déclarés ».
_EVALS_SUMMARY = re.compile(r"(\d+)/(\d+) réussis")
# Variables d'environnement qui donnent accès à un fournisseur de modèle :
# retirées du rejeu, pour que « zéro appel modèle » soit contraint et non déclaré.
_PROVIDER_ENV = re.compile(
    r"^(ANTHROPIC|OPENAI|GEMINI|MISTRAL|COHERE|GROQ|OPENROUTER|AZURE_OPENAI|"
    r"GOOGLE_(GENAI_)?API|GRIMOIRE_(LLM|PROVIDER))|(API_KEY|AUTH_TOKEN|ACCESS_TOKEN)$",
    re.IGNORECASE,
)
_CITATION = re.compile(r"examples/([A-Za-z0-9][A-Za-z0-9_.-]*)")

replay_only_posix = pytest.mark.skipif(
    sys.platform == "win32",
    reason="replay.sh demande bash POSIX ; sous Windows `bash` peut être WSL (structure vérifiée seulement)",
)


def cited_examples(doc: Path) -> set[str]:
    return set(_CITATION.findall(doc.read_text(encoding="utf-8")))


def replayable_problems(root: Path, name: str) -> list[str]:
    folder = root / "examples" / name
    problems: list[str] = []
    if not (folder / "replay.sh").is_file():
        problems.append(f"examples/{name}/replay.sh absent")
    expected = folder / "expected.json"
    if not expected.is_file():
        problems.append(f"examples/{name}/expected.json absent")
        return problems
    try:
        data = json.loads(expected.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [*problems, f"examples/{name}/expected.json illisible : {exc}"]
    replay = data.get("replay") if isinstance(data, dict) else None
    if not isinstance(replay, dict) or not isinstance(replay.get("stdout"), list):
        problems.append(f"examples/{name}/expected.json : `replay.stdout` (liste) attendu")
    elif not isinstance(replay.get("exit_code"), int):
        problems.append(f"examples/{name}/expected.json : `replay.exit_code` (entier) attendu")
    proof = data.get("proof") if isinstance(data, dict) else None
    if not isinstance(proof, dict):
        problems.append(f"examples/{name}/expected.json : `proof` attendu")
    else:
        missing = [k for k in PROOF_KEYS if k not in proof]
        if missing:
            problems.append(f"examples/{name}/expected.json : `proof` sans {', '.join(missing)}")
        elif isinstance(replay, dict):
            problems.extend(proof_problems(name, replay, proof))
    return problems


def expected_verdict(replay: dict[str, object]) -> str:
    """Verdict dérivé du rejeu : jamais déclaré librement.

    Code de sortie non nul -> ``fail`` ; sinon, si la sortie porte le bilan de
    ``blueprint evals``, tous les cas exécutés doivent être réussis.
    """
    if replay.get("exit_code") != 0:
        return "fail"
    stdout = replay.get("stdout")
    for line in reversed(stdout if isinstance(stdout, list) else []):
        match = _EVALS_SUMMARY.search(str(line))
        if match:
            return "pass" if match.group(1) == match.group(2) else "fail"
    return "pass"


def proof_problems(name: str, replay: dict[str, object], proof: dict[str, object]) -> list[str]:
    """Le paquet de preuve doit découler du rejeu, pas le contredire."""
    where = f"examples/{name}/expected.json"
    problems: list[str] = []
    want = expected_verdict(replay)
    if proof.get("verdict") != want:
        problems.append(
            f"{where} : `proof.verdict` {proof.get('verdict')!r} contredit le rejeu "
            f"(code {replay.get('exit_code')!r}, attendu {want!r})"
        )
    if proof.get("score") is not None:
        problems.append(f"{where} : `proof.score` doit valoir null (aucun score n'est calculé par le rejeu)")
    cost = proof.get("cost")
    if not isinstance(cost, dict):
        problems.append(f"{where} : `proof.cost` (objet) attendu")
    else:
        if cost.get("model_calls") != 0:
            problems.append(
                f"{where} : `proof.cost.model_calls` {cost.get('model_calls')!r} (le rejeu est sans modèle : 0 attendu)"
            )
        if cost.get("tokens") is not None:
            problems.append(f"{where} : `proof.cost.tokens` doit valoir null (non mesuré)")
    return problems


def doc_problems(root: Path, docs: tuple[str, ...]) -> list[str]:
    """Citations sans exemple rejouable, et docs qui n'en citent aucune."""
    problems: list[str] = []
    for doc in docs:
        cited = cited_examples(root / doc)
        if not cited:
            # Sans cette ligne la garde serait vide : retirer la citation la
            # ferait passer au vert sans rien vérifier.
            problems.append(f"{doc} ne cite aucun exemple rejouable (examples/<nom>)")
        for name in sorted(cited):
            problems.extend(f"{doc} cite {name} : {p}" for p in replayable_problems(root, name))
    return problems


def inventory_problems(root: Path, illustrative: frozenset[str]) -> list[str]:
    problems: list[str] = []
    for folder in sorted(p for p in (root / "examples").iterdir() if p.is_dir()):
        name = folder.name
        if name in illustrative:
            readme = folder / "README.md"
            text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
            if ILLUSTRATIVE_MARKER not in text:
                problems.append(f"examples/{name} est illustratif mais son README ne dit pas « {ILLUSTRATIVE_MARKER} »")
            if not replayable_problems(root, name):
                problems.append(f"examples/{name} est rejouable : le retirer de ILLUSTRATIVE")
        else:
            problems.extend(replayable_problems(root, name))
    return problems


def run_replay(root: Path, name: str, shim_dir: Path) -> tuple[int, list[str]]:
    # Aucune clé de fournisseur dans le rejeu : un appel modèle échouerait au
    # lieu de passer en silence, ce qui rend `model_calls: 0` contraint.
    inherited = {k: v for k, v in os.environ.items() if not _PROVIDER_ENV.search(k)}
    env = {
        **inherited,
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": str(KIT_ROOT / "src"),
        "PYTHONUTF8": "1",
        "NO_COLOR": "1",
    }
    done = subprocess.run(
        ["bash", str(root / "examples" / name / "replay.sh")],
        capture_output=True,
        encoding="utf-8",
        env=env,
        timeout=120,
        check=False,
    )
    return done.returncode, done.stdout.splitlines()


def replay_mismatch(root: Path, name: str, shim_dir: Path) -> str | None:
    expected = json.loads((root / "examples" / name / "expected.json").read_text(encoding="utf-8"))["replay"]
    code, lines = run_replay(root, name, shim_dir)
    if code != expected["exit_code"]:
        return f"{name} : code de sortie {code}, attendu {expected['exit_code']}"
    if lines != expected["stdout"]:
        return f"{name} : sortie différente de expected.json\n--- obtenu\n" + "\n".join(lines)
    return None


@pytest.fixture
def grimoire_shim(tmp_path: Path) -> Path:
    """Une commande `grimoire` du PATH qui pointe sur le code de l'arbre testé."""
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "grimoire"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m grimoire "$@"\n', encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return shim_dir


def _make_example(
    root: Path, name: str, script: str, stdout: list[str], code: int = 0, verdict: str | None = None
) -> None:
    folder = root / "examples" / name
    folder.mkdir(parents=True)
    (folder / "replay.sh").write_text(script, encoding="utf-8")
    if verdict is None:
        verdict = "pass" if code == 0 else "fail"
    proof = {"gate": "g", "verdict": verdict, "score": None, "cost": {"model_calls": 0, "tokens": None}}
    (folder / "expected.json").write_text(
        json.dumps({"replay": {"exit_code": code, "stdout": stdout}, "proof": proof}), encoding="utf-8"
    )


# ── le dépôt réel ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("doc", GETTING_STARTED)
def test_getting_started_cites_only_replayable_examples(doc: str) -> None:
    assert doc_problems(KIT_ROOT, (doc,)) == []


def test_every_example_directory_is_replayable_or_declared_illustrative() -> None:
    assert inventory_problems(KIT_ROOT, ILLUSTRATIVE) == []


def test_replayable_examples_exist() -> None:
    names = {p.name for p in EXAMPLES.iterdir() if p.is_dir()} - ILLUSTRATIVE
    assert {"blueprint-minimal", "blueprint-web-pipeline"} <= names


@replay_only_posix
@pytest.mark.parametrize(
    "name",
    sorted(p.name for p in EXAMPLES.iterdir() if p.is_dir() and p.name not in ILLUSTRATIVE),
)
def test_replay_matches_expected(name: str, grimoire_shim: Path) -> None:
    assert replay_mismatch(KIT_ROOT, name, grimoire_shim) is None


# ── les gardes elles-mêmes ───────────────────────────────────────────────────


def test_doc_citing_an_example_without_replay_is_flagged(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "getting-started.md").write_text("Voir `examples/fantome/`.\n", encoding="utf-8")
    (tmp_path / "examples").mkdir()
    problems = doc_problems(tmp_path, ("docs/getting-started.md",))
    assert any("fantome" in p and "replay.sh absent" in p for p in problems)
    assert any("fantome" in p and "expected.json absent" in p for p in problems)


def test_doc_without_any_citation_is_flagged(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "getting-started.md").write_text("Aucun exemple ici.\n", encoding="utf-8")
    assert doc_problems(tmp_path, ("docs/getting-started.md",))


def test_expected_json_without_proof_is_flagged(tmp_path: Path) -> None:
    _make_example(tmp_path, "nu", "true\n", [])
    path = tmp_path / "examples" / "nu" / "expected.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["proof"]["cost"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert any("sans cost" in p for p in replayable_problems(tmp_path, "nu"))


def test_unlisted_example_without_replay_is_flagged(tmp_path: Path) -> None:
    (tmp_path / "examples" / "nouveau").mkdir(parents=True)
    (tmp_path / "examples" / "nouveau" / "README.md").write_text("récit", encoding="utf-8")
    assert any("replay.sh absent" in p for p in inventory_problems(tmp_path, frozenset()))


def test_illustrative_example_must_say_so(tmp_path: Path) -> None:
    (tmp_path / "examples" / "recit").mkdir(parents=True)
    (tmp_path / "examples" / "recit" / "README.md").write_text("récit", encoding="utf-8")
    assert any("non rejouable" in p for p in inventory_problems(tmp_path, frozenset({"recit"})))


@replay_only_posix
def test_replay_with_different_output_is_flagged(tmp_path: Path, grimoire_shim: Path) -> None:
    _make_example(tmp_path, "derive", "echo autre\n", ["attendu"])
    mismatch = replay_mismatch(tmp_path, "derive", grimoire_shim)
    assert mismatch is not None and "autre" in mismatch


@replay_only_posix
def test_replay_with_different_exit_code_is_flagged(tmp_path: Path, grimoire_shim: Path) -> None:
    _make_example(tmp_path, "casse", "exit 3\n", [])
    mismatch = replay_mismatch(tmp_path, "casse", grimoire_shim)
    assert mismatch is not None and "code de sortie 3" in mismatch


@replay_only_posix
def test_replay_matching_expected_passes(tmp_path: Path, grimoire_shim: Path) -> None:
    _make_example(tmp_path, "ok", "echo bonjour\n", ["bonjour"])
    assert replay_mismatch(tmp_path, "ok", grimoire_shim) is None


# ── le paquet de preuve découle du rejeu ─────────────────────────────────────


def _mutate_proof(root: Path, name: str, mutate: Callable[[dict[str, Any]], None]) -> None:
    path = root / "examples" / name / "expected.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_lying_example_passing_verdict_on_failing_replay_is_flagged(tmp_path: Path) -> None:
    """Un rejeu qui sort en code 1 ne peut pas porter `verdict: pass` et un score."""
    _make_example(tmp_path, "menteur", "echo FAIL\nexit 1\n", ["FAIL"], code=1, verdict="pass")
    _mutate_proof(tmp_path, "menteur", lambda d: d["proof"].update(score=99))
    problems = replayable_problems(tmp_path, "menteur")
    assert any("verdict" in p and "contredit le rejeu" in p for p in problems)
    assert any("score" in p for p in problems)
    assert inventory_problems(tmp_path, frozenset())
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "getting-started.md").write_text("`examples/menteur`\n", encoding="utf-8")
    assert doc_problems(tmp_path, ("docs/getting-started.md",))


def test_failing_replay_with_failing_verdict_is_accepted(tmp_path: Path) -> None:
    _make_example(tmp_path, "honnete", "exit 1\n", [], code=1, verdict="fail")
    assert replayable_problems(tmp_path, "honnete") == []


def test_evals_summary_with_failures_cannot_claim_pass(tmp_path: Path) -> None:
    _make_example(tmp_path, "bilan", "true\n", ["0/1 réussis sur 5 déclarés"], code=0, verdict="pass")
    assert any("verdict" in p for p in replayable_problems(tmp_path, "bilan"))
    _mutate_proof(tmp_path, "bilan", lambda d: d["proof"].update(verdict="fail"))
    assert replayable_problems(tmp_path, "bilan") == []


@pytest.mark.parametrize(
    ("field", "mutate", "needle"),
    [
        ("verdict", lambda d: d["proof"].update(verdict="fail"), "contredit le rejeu"),
        ("model_calls", lambda d: d["proof"]["cost"].update(model_calls=5), "model_calls"),
        ("score", lambda d: d["proof"].update(score=87), "score"),
        ("tokens", lambda d: d["proof"]["cost"].update(tokens=1234), "tokens"),
    ],
)
def test_mutated_proof_on_real_example_turns_the_guards_red(
    tmp_path: Path, field: str, mutate: Callable[[dict[str, Any]], None], needle: str
) -> None:
    """Muter le paquet de preuve d'un exemple réel doit rougir les trois gardes."""
    shutil.copytree(KIT_ROOT / "examples", tmp_path / "examples")
    shutil.copytree(KIT_ROOT / "docs", tmp_path / "docs", ignore=shutil.ignore_patterns("*.png", "*.svg"))
    _mutate_proof(tmp_path, "blueprint-web-pipeline", mutate)
    assert any(needle in p for p in replayable_problems(tmp_path, "blueprint-web-pipeline")), field
    assert any(needle in p for p in inventory_problems(tmp_path, ILLUSTRATIVE)), field
    assert any(needle in p for p in doc_problems(tmp_path, GETTING_STARTED)), field


@replay_only_posix
def test_replay_runs_without_provider_credentials(
    tmp_path: Path, grimoire_shim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les clés de fournisseur ne sont pas transmises au rejeu."""
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY"):
        monkeypatch.setenv(var, "sk-ne-doit-pas-passer")
    monkeypatch.setenv("HOME_MARKER", "garde")
    script = 'echo "a=${ANTHROPIC_API_KEY:-absent} o=${OPENAI_API_KEY:-absent} g=${GEMINI_API_KEY:-absent}"\n'
    script += 'echo "m=${MISTRAL_API_KEY:-absent} q=${GROQ_API_KEY:-absent} h=${HOME_MARKER:-absent}"\n'
    _make_example(tmp_path, "sans-cle", script, ["a=absent o=absent g=absent", "m=absent q=absent h=garde"])
    assert replay_mismatch(tmp_path, "sans-cle", grimoire_shim) is None
