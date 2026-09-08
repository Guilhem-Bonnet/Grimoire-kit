"""``grimoire task dispatch`` — surface CLI de la cascade (issue #323).

La logique de cascade est couverte en détail par
``tests/unit/missions/test_dispatch.py`` ; ce module ne vérifie que le
câblage CLI : codes de sortie, ``--json``, ``--dry-run``, et les refus qui
doivent apparaître avant tout appel réseau.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.cli.cmd_task import task_app

runner = CliRunner()

STANDARD = Path("_grimoire/standard")
REGISTRY = STANDARD / "llm-provider-registry.yaml"


def run(projet: Path, *args: str):
    return runner.invoke(task_app, [*args, "--project-root", str(projet)])


def ajoute(projet: Path, acceptance: str, owner: str = "amelia") -> str:
    res = run(projet, "add", "Tâche déléguée", "-a", acceptance, "--owner", owner)
    assert res.exit_code == 0, res.output
    return next(mot for mot in res.output.split() if mot.startswith("GAO-"))


def _script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(body), encoding="utf-8")
    return path


def _write_registry(root: Path, pid: str, tier: str, invocation: str) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / REGISTRY).write_text(
        f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "{pid}"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["{pid}-model"]
    currency: "api"
    invocation: "{invocation}"
    models:
      - id: "{pid}-model"
        tier: "{tier}"
    fallback_order: []
routing:
  default_provider: ""
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
""",
        encoding="utf-8",
    )


def test_v2_refuse_avec_le_code_de_sortie_2(tmp_path: Path) -> None:
    tid = ajoute(tmp_path, "le code est propre")

    res = run(tmp_path, "dispatch", tid, "--check", "true")

    assert res.exit_code == 2
    assert "V2" in res.output


def test_sans_check_refuse_avec_le_code_de_sortie_2(tmp_path: Path) -> None:
    tid = ajoute(tmp_path, "la suite de tests passe")

    res = run(tmp_path, "dispatch", tid)

    assert res.exit_code == 2


def test_max_tier_inconnu_refuse_avec_le_code_de_sortie_2(tmp_path: Path) -> None:
    tid = ajoute(tmp_path, "la suite de tests passe")

    res = run(tmp_path, "dispatch", tid, "--check", "true", "--max-tier", "ultra")

    assert res.exit_code == 2
    assert "ultra" in res.output


def test_une_tache_inconnue_est_refusee_avant_tout_calcul(tmp_path: Path) -> None:
    res = run(tmp_path, "dispatch", "GAO-fantome-001", "--check", "true")

    assert res.exit_code == 1
    assert "Tâche inconnue" in res.output


def test_dry_run_affiche_la_chaine_et_le_prompt(tmp_path: Path) -> None:
    _write_registry(tmp_path, "jamais-appele", "cheap", "false {prompt} {model}")
    tid = ajoute(tmp_path, "la suite de tests passe")

    res = run(tmp_path, "dispatch", tid, "--check", "true", "--dry-run")

    assert res.exit_code == 0
    assert "cheap" in res.output and "mid" in res.output and "strong" in res.output
    assert "Critères d'acceptation" in res.output


def test_cascade_verte_en_json_porte_les_tentatives(tmp_path: Path) -> None:
    green = _script(
        tmp_path,
        "green.py",
        """\
        from pathlib import Path
        Path("marker.txt").write_text("done", encoding="utf-8")
        """,
    )
    _write_registry(tmp_path, "vert", "cheap", f"{sys.executable} {green} {{prompt}} --model {{model}}")
    tid = ajoute(tmp_path, "la suite de tests passe")

    res = runner.invoke(
        app,
        ["--output", "json", "task", "dispatch", tid, "--check", "test -f marker.txt", "--project-root", str(tmp_path)],
    )

    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["exit_code"] == 0
    assert len(payload["attempts"]) == 1
    assert payload["attempts"][0]["verdict"] == "green"
    assert payload["attempts"][0]["provider"] == "vert"


def test_chaine_epuisee_sort_en_1(tmp_path: Path) -> None:
    red = _script(tmp_path, "red.py", "pass\n")
    _write_registry(tmp_path, "rouge", "cheap", f"{sys.executable} {red} {{prompt}} --model {{model}}")
    tid = ajoute(tmp_path, "la suite de tests passe")

    res = run(tmp_path, "dispatch", tid, "--check", "test -f marker.txt", "--max-tier", "cheap")

    assert res.exit_code == 1
