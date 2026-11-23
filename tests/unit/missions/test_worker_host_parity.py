"""Lot L4 (#1281) : sous Claude Code, l'interface ``WorkerHost`` ne change rien à ce que le tick lance.

``tests/fixtures/worker-host/claude-code-origin-main.json`` a été produit par ce même fichier, copié dans une
extraction ``git archive origin/main`` d'avant L4 (3.100.0) et lancé avec ``GRIMOIRE_PARITY_OUT=<sortie>`` :

.. code-block:: bash

    git archive origin/main | tar -x -C /tmp/main
    cp tests/unit/missions/test_worker_host_parity.py /tmp/main/tests/unit/missions/
    cd /tmp/main && GRIMOIRE_PARITY_OUT=/tmp/ref.json PYTHONPATH=src python -m pytest \\
        tests/unit/missions/test_worker_host_parity.py -k generate

Le test rejoue les mêmes scénarios avec le code courant et compare, pour chaque appel au faux ``claude`` : l'argv
complet (consigne comprise), le dossier de lancement et l'environnement passé (aucune clé secrète n'y figure) ; puis
les fichiers que le lancement écrit (réglages du fournisseur, ``.gitignore`` du worktree). Scénarios : worker par
défaut, palier fort, ``lean`` avec modèle, fournisseur ``deepseek``, ``model_by_class`` (V0, avec et sans
fournisseur), route sensible (fournisseur ignoré, ``opus``), relecteur (et relecteur ``lean``), reprise d'un worker
routé (``--resume``) et repli sur une session neuve quand la reprise échoue.

Régénérer la fixture n'a de sens qu'avec le code d'avant L4 : la régénérer depuis la branche viderait la preuve.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from lot2_doubles import FakeHost, make_repo

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "worker-host" / "claude-code-origin-main.json"
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")
REGISTRY = Path("_grimoire/standard/llm-provider-registry.yaml")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

_REGISTRE = """\
$schema: "grimoire-llm-provider-registry/v1"
metadata: {project: demo, owner: "", policy: x}
providers:
  - id: deepseek
    enabled: true
    provider_type: hosted
    allowed_capabilities: [code]
    default_models: [deepseek-chat]
    currency: api
    anthropic_base_url: https://api.example.test/anthropic
    models:
      - {id: deepseek-chat, tier: cheap}
    data_policy: {allowed_data_classes: [project-source], forbidden_data_classes: [], retention_notes: ""}
    fallback_order: []
    audit: {log_prompts: false, log_metadata: true}
routing: {default_data_class: project-source, default_provider: deepseek, default_fallback_chain: [], require_capability_match: true, require_data_policy_match: true}
"""


def _policy(root: Path, worker: str, reviewer: str = "") -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    text = (
        "orchestrator:\n  roles:\n    worker:\n      enabled: true\n      wip: 3\n      sessions_per_day: 9\n"
        f"      permission_mode: acceptEdits\n{worker}"
    )
    if reviewer:
        text += f"    reviewer:\n      enabled: true\n      permission_mode: acceptEdits\n{reviewer}"
    (root / POLICY).write_text(text, encoding="utf-8")


def _ready(root: Path, title: str, *, v0: bool = False) -> str:
    from grimoire.missions.schemas import TaskCheck, TaskState
    from grimoire.missions.service import TaskService

    service = TaskService(root)
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(
        title="L4", origin="test", created_by="test"
    )
    if v0:
        task = service.ledger.create_task(
            mission.id, title, acceptance=("pytest tests/x.py : le code de sortie vaut 0",), owner="amelia",
            checks=(TaskCheck(cmd="pytest tests/x.py"),),
        )
    else:
        task = service.ledger.create_task(mission.id, title, acceptance=("le travail est fait",), owner="amelia")
    service.ledger.transition_task(task.id, TaskState.READY)
    return task.id


def _tick(root: Path, host: FakeHost) -> None:
    from grimoire.missions.orchestrate import run_tick

    run_tick(root, apply=True, exec_=host)


def _deepseek(root: Path) -> None:
    from grimoire.providers.secrets import set_secret

    (root / REGISTRY).parent.mkdir(parents=True, exist_ok=True)
    (root / REGISTRY).write_text(_REGISTRE, encoding="utf-8")
    set_secret("deepseek", "DEEPSEEK_API_KEY", "sk-secret-de-test")


def _calls(host: FakeHost, root: Path, home: Path, ids: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for argv, cwd, env in host.calls:
        if argv[0] != "claude" or argv[1] in ("agents", "--version"):
            continue  # les lectures (`claude agents`, `--version`) ne lancent rien
        out.append({"argv": argv, "cwd": str(cwd), "env": dict(env) if env is not None else None})
    return _scrub(out, root, home, ids)


def _scrub(value: Any, root: Path, home: Path, ids: dict[str, str]) -> Any:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = text.replace(str(root), "<ROOT>").replace(str(home), "<HOME>")
    for tid, alias in ids.items():
        text = text.replace(tid, alias)
    return json.loads(_UUID.sub("<UUID>", text))


def _files(root: Path, home: Path, ids: dict[str, str]) -> dict[str, Any]:
    from grimoire.missions.service import TaskService
    from grimoire.missions.task_worktree import find_task_worktree

    found: dict[str, Any] = {}
    settings = home / ".env-store" / "deepseek.claude-settings.json"
    if settings.is_file():
        found["deepseek.claude-settings.json"] = json.loads(settings.read_text(encoding="utf-8"))
    service = TaskService(root)
    for tid in ids:
        wt = find_task_worktree(service, tid)
        if wt is None:
            continue
        ignore = wt.path / ".claude" / ".gitignore"
        found[f"{tid}:.claude/.gitignore"] = ignore.read_text(encoding="utf-8") if ignore.is_file() else None
    return _scrub(found, root, home, ids)


def _scenario(base: Path, name: str, setup: Callable[[Path], dict[str, str]], host: FakeHost | None = None,
              after: Callable[[Path, FakeHost, dict[str, str]], None] | None = None) -> dict[str, Any]:
    home = base / name / "user-home"
    home.mkdir(parents=True)
    os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)  # un magasin de secrets par scénario
    root = make_repo(base / name)
    host = host or FakeHost()
    ids = setup(root)
    _tick(root, host)
    if after is not None:
        after(root, host, ids)
    alias = {tid: f"<{key}>" for key, tid in ids.items()}
    return {"calls": _calls(host, root, home, alias), "files": _files(root, home, alias)}


def _to_review(root: Path, host: FakeHost, ids: dict[str, str]) -> None:
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    ledger = TaskService(root).ledger
    for tid in ids.values():
        ledger.transition_task(tid, TaskState.RUNNING, actor_id="worker")
        ledger.transition_task(tid, TaskState.NEEDS_VERIFICATION, actor_id="worker")
    _tick(root, host)


def _resume(*, fail: bool) -> Callable[[Path, FakeHost, dict[str, str]], None]:
    def run(root: Path, host: FakeHost, ids: dict[str, str]) -> None:
        from grimoire.missions.launcher import launched_sessions, resume_worker
        from grimoire.missions.service import TaskService

        service = TaskService(root)
        tid = ids["T"]
        launch = launched_sessions(service)[tid]
        if fail:
            original = host._claude

            def refuse_resume(argv: list[str], cwd: Path) -> Any:
                if "--resume" in argv:
                    from grimoire.missions.launcher import ExecResult

                    return ExecResult(1, "", "conversation introuvable")
                return original(argv, cwd)

            host._claude = refuse_resume  # type: ignore[method-assign]
        resume_worker(service, host, "claude", tid, launch, "renvoi du relecteur", permission_mode="acceptEdits")

    return run


def scenarios(base: Path) -> dict[str, Any]:
    """Tous les scénarios, chemins, identifiants de tâche et uuid remplacés : ce que la fixture contient."""

    def plain(extra: str, *, v0: bool = False, title: str = "travail", provider: bool = False,
              reviewer: str = "") -> Callable[[Path], dict[str, str]]:
        def setup(root: Path) -> dict[str, str]:
            if provider:
                _deepseek(root)
            _policy(root, extra, reviewer)
            return {"T": _ready(root, title, v0=v0)}

        return setup

    deepseek = "      provider: deepseek\n      tier: cheap\n"
    by_class = "      model_by_class: true\n      tier: mid\n"
    return {
        "worker_default": _scenario(base, "a", plain("")),
        "worker_strong": _scenario(base, "b", plain("      tier: strong\n")),
        "worker_lean_model": _scenario(base, "c", plain("      lean: true\n      model: sonnet\n")),
        "provider_deepseek": _scenario(base, "d", plain(deepseek, v0=True, provider=True)),
        "model_by_class_v0": _scenario(base, "e", plain(by_class, v0=True)),
        "model_by_class_v1": _scenario(base, "f", plain(by_class)),
        "model_by_class_v0_deepseek": _scenario(
            base, "g", plain("      provider: deepseek\n      model_by_class: true\n      tier: mid\n", v0=True, provider=True)
        ),
        "sensitive_route": _scenario(
            base, "h",
            plain(f"{deepseek}      sensitive_route: strong\n", title="modifier pyproject.toml", v0=True, provider=True),
        ),
        "reviewer": _scenario(base, "i", plain("      model: sonnet\n", reviewer="      wip: 1\n"), after=_to_review),
        "reviewer_lean": _scenario(
            base, "j", plain("      model: sonnet\n", reviewer="      lean: true\n"), after=_to_review
        ),
        "resume_routed_lean": _scenario(
            base, "k", plain(f"{deepseek}      lean: true\n", v0=True, provider=True), after=_resume(fail=False)
        ),
        "resume_fallback_fresh": _scenario(
            base, "l", plain("      tier: strong\n"), after=_resume(fail=True)
        ),
    }


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in list(os.environ):
        if var.startswith("CLAUDE") or var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID"):
            monkeypatch.delenv(var, raising=False)


@pytest.mark.skipif(not os.environ.get("GRIMOIRE_PARITY_OUT"), reason="génération de la fixture seulement")
def test_generate(tmp_path: Path) -> None:  # pragma: no cover - lancé à la main contre origin/main
    out = Path(os.environ["GRIMOIRE_PARITY_OUT"])
    result = scenarios(tmp_path / "s")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_claude_code_launches_are_identical_to_origin_main(tmp_path: Path) -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = scenarios(tmp_path / "s")
    assert sorted(actual) == sorted(expected)
    for name in expected:
        assert actual[name] == expected[name], name


def test_the_fixture_covers_every_case() -> None:
    """La preuve porte bien sur les cas demandés : chaque scénario a lancé ce qu'il devait lancer."""
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def argv_of(name: str, index: int = 0) -> list[str]:
        return list(expected[name]["calls"][index]["argv"])

    assert argv_of("worker_strong")[argv_of("worker_strong").index("--model") + 1] == "opus"
    assert "--strict-mcp-config" in argv_of("worker_lean_model")
    assert "--settings" in argv_of("provider_deepseek")
    assert expected["provider_deepseek"]["files"]["deepseek.claude-settings.json"]["env"]["ANTHROPIC_MODEL"] == "deepseek-chat"
    assert argv_of("model_by_class_v0")[argv_of("model_by_class_v0").index("--model") + 1] == "haiku"
    assert argv_of("model_by_class_v1")[argv_of("model_by_class_v1").index("--model") + 1] == "sonnet"
    sensitive = argv_of("sensitive_route")
    assert sensitive[sensitive.index("--model") + 1] == "opus" and "--settings" not in sensitive
    assert any("grimoire-review-" in " ".join(c["argv"]) for c in expected["reviewer"]["calls"])
    assert any("--resume" in c["argv"] for c in expected["resume_routed_lean"]["calls"])
    assert sum(1 for c in expected["resume_fallback_fresh"]["calls"] if "--name" in c["argv"]) == 2
    secrets = json.dumps(expected)
    assert "sk-secret-de-test" not in secrets
