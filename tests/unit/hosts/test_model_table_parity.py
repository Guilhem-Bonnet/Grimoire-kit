"""Lot L5 (#1281) : sous Claude Code, la table palier → modèle ne change rien à l'octet.

``tests/fixtures/model-table/claude-code-origin-main.json`` a été produit par ce même module
(``python tests/unit/hosts/test_model_table_parity.py <sortie>``) avec le code d'``origin/main``
d'avant L5 (3.99.0). Le test rejoue les mêmes scénarios avec le code courant et compare tout :

- la sortie rendue du hook ``grimoire.dispatch-model`` (V0, V1, V2 réclamées, heuristique
  recherche/revue/sujet sensible, persona qui déclare son modèle, persona sans modèle, agent de
  plugin) ;
- les fiches ``.claude/agents/*.md`` émises pour l'archétype ``meta`` (``model:`` de chaque persona,
  politique de dispatch de la persona d'entrée) ;
- la politique de dispatch et le roster injectés au ``SessionStart`` ;
- le modèle du worker (palier, ``model_by_class``), de la route sensible et du relecteur, sans surcharge.

Régénérer la fixture n'a de sens qu'avec le code d'avant L5 : la régénérer depuis la branche
viderait la preuve.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT_MARKER = "<ROOT>"
SESSION = "aaaaaaaa-0000-4000-8000-0000000000l5"
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "model-table" / "claude-code-origin-main.json"
META_AGENTS = Path(__file__).resolve().parents[3] / "archetypes" / "meta" / "agents"

#: Appels ``Agent`` hors tâche réclamée : l'heuristique décide.
HEURISTIC_CALLS: dict[str, dict[str, Any]] = {
    "research": {"description": "Find the emitters", "prompt": "Locate the code.", "subagent_type": "general-purpose"},
    "review": {"description": "Revue du diff", "prompt": "Relis.", "subagent_type": "general-purpose"},
    "task_tool": {"description": "Search the docs", "prompt": "x"},
    "sensitive": {"description": "Lire la garde du hook", "prompt": "x"},
    "writing": {"description": "Corriger le bug", "prompt": "x"},
    "explicit": {"description": "Find the emitters", "prompt": "x", "model": "opus"},
    "fork": {"description": "Find the emitters", "prompt": "x", "subagent_type": "fork"},
    "plugin": {"description": "Find the emitters", "prompt": "x", "subagent_type": "plug:agent"},
    "persona_security_auditor": {
        "description": "Find the emitters",
        "prompt": "x",
        "subagent_type": "security-auditor",
    },
    "persona_inherit": {"description": "Find the emitters", "prompt": "x", "subagent_type": "analyste"},
    "persona_cheap": {"description": "Revue du diff", "prompt": "x", "subagent_type": "scribe"},
}
#: Une tâche réclamée par classe : son critère, et une description qui contredit la classe.
CLAIMED: dict[str, tuple[str, str]] = {
    "V0": ("la suite de tests passe", "Revue du diff"),
    "V1": ("revue de code par un pair", "Find the emitters"),
    "V2": ("le code est propre", "Find the emitters"),
}


def _scrub(value: Any, root: Path) -> Any:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True).replace(str(root), ROOT_MARKER)
    return json.loads(text)


def _hook(root: Path, tool_input: dict[str, Any], session: str = "") -> dict[str, Any]:
    from grimoire.bridges.schemas import HostId
    from grimoire.hosts.runtime import run_hook

    payload: dict[str, Any] = {
        "hook_event_name": "PreToolUse",
        "cwd": str(root),
        "tool_name": "Task" if "subagent_type" not in tool_input and tool_input.get("prompt") == "x" else "Agent",
        "tool_input": tool_input,
    }
    if session:
        payload["session_id"] = session
    rendered, _decision, _ = run_hook(
        payload, host_id=HostId.CLAUDE_CODE_CLI, project_root=root, decision_id="grimoire.dispatch-model"
    )
    return dict(rendered)


#: Personas couvrant les quatre issues de l'affinité : ``strong`` (security-auditor, copiée de l'archétype),
#: ``mid`` (reasoning high + cost low : le point d'entrée), ``cheap`` (cost low) et ``inherit`` (reasoning medium).
SYNTHETIC_AGENTS: dict[str, tuple[str, str]] = {
    "concierge": ("high", "low"),
    "scribe": ("low", "low"),
    "analyste": ("medium", "any"),
}


def _agent_text(name: str, reasoning: str, cost: str) -> str:
    return (
        f'---\nname: "{name}"\ndescription: "Persona {name}"\ntools: "read, search"\n'
        f"model_affinity:\n  reasoning: {reasoning}\n  context_window: large\n  speed: fast\n  cost: {cost}\n---\n\n"
        f"Persona {name}.\n"
    )


def _meta_project(root: Path) -> Path:
    from grimoire.core import layout

    (root / "_grimoire" / "_memory").mkdir(parents=True, exist_ok=True)
    (root / "_grimoire" / "_memory" / "shared-context.md").write_text("# Contexte\n", encoding="utf-8")
    agents = layout.agent_dirs(root)[0]
    agents.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(META_AGENTS / "security-auditor.md", agents / "security-auditor.md")
    for name, (reasoning, cost) in SYNTHETIC_AGENTS.items():
        (agents / f"{name}.md").write_text(_agent_text(name, reasoning, cost), encoding="utf-8")
    return root


def _emitted(root: Path) -> dict[str, Any]:
    from grimoire.hosts.collect import build_surface
    from grimoire.hosts.decisions.activation import _claude_dispatch_context
    from grimoire.hosts.emitters.claude_code import ClaudeCodeEmitter

    surface = build_surface(root, project_name="parite", write_cache=False)
    plan = ClaudeCodeEmitter().plan(surface, root)
    files = {f.relpath.as_posix(): f.content for f in plan.files if f.relpath.as_posix().startswith(".claude/agents/")}
    return {"agents": files, "session_start": _claude_dispatch_context(root, surface.agents)}


def _claimed(root: Path, criterion: str, description: str) -> dict[str, Any]:
    from grimoire.core.agentic_standard import setup_standard_profile
    from grimoire.missions.schemas import TaskCheck, TaskState
    from grimoire.missions.service import TaskService

    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    registry = root / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    service = TaskService(root)
    # V0 exige une commande exécutable (#772) : sans elle, « tests verts » remonte en V1.
    checks = (TaskCheck(cmd="pytest -q"),) if "tests" in criterion else ()
    tid = service.add("Travail", (criterion,), owner="guilhem", actor="claude", ready=True, checks=checks).task.id
    service.context(tid)
    service.claim(tid, "claude", host="claude-code-cli")
    service.transition(tid, TaskState.RUNNING, "claude")
    service.attach_session(tid, SESSION, session_host="claude-code-cli", actor="claude")
    return _hook(root, {"description": description, "prompt": "x"}, session=SESSION)


def scenarios(base: Path) -> dict[str, Any]:
    """Tous les scénarios, chemins remplacés par ``<ROOT>`` : ce que la fixture contient."""
    out: dict[str, Any] = {"heuristic": {}, "claimed": {}}
    meta = _meta_project(base / "meta")
    out["emitted"] = _scrub(_emitted(meta), meta)
    # Les fiches émises sont lues par le hook : une persona qui déclare son modèle garde le sien.
    from grimoire.hosts.collect import build_surface
    from grimoire.hosts.emitters.claude_code import ClaudeCodeEmitter

    for emitted in ClaudeCodeEmitter().plan(build_surface(meta, project_name="parite", write_cache=False), meta).files:
        target = meta / emitted.relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(emitted.content, encoding="utf-8")
    for name, tool_input in HEURISTIC_CALLS.items():
        out["heuristic"][name] = _scrub(_hook(meta, tool_input), meta)
    for cls, (criterion, description) in CLAIMED.items():
        root = base / f"claimed-{cls}"
        root.mkdir(parents=True)
        out["claimed"][cls] = _scrub(_claimed(root, criterion, description), root)
    empty = base / "sans-politique"
    empty.mkdir()
    out["launcher"] = _launcher_models(empty)
    return out


WORKER_MODELS = ("", "haiku", "sonnet", "opus", "claude-opus-4-7", "claude-sonnet-4-6", "deepseek-chat")


def _launcher_models(root: Path) -> dict[str, Any]:
    """Modèle du worker (palier, ``model_by_class``), de la route sensible et du relecteur, sans surcharge.

    Appelle les méthodes sur un objet minimal (politique et service factices) : le même appel vaut pour le code
    d'origin/main et pour la branche. La route sensible lit ``_TIER_ALIAS["strong"]`` là où ``sensitive_model``
    n'existe pas encore (origin/main), qui est exactement ce que le lanceur y passait à ``--model``.
    """
    from types import SimpleNamespace

    from grimoire.missions import launcher
    from grimoire.missions.reviewer import ReviewStage

    service = SimpleNamespace(home_root=root)
    admit = SimpleNamespace(route=None)
    worker: dict[str, str] = {}
    for by_class in (False, True):
        for tier in ("", "cheap", "mid", "strong"):
            role = SimpleNamespace(model="", model_by_class=by_class, tier=tier)
            fake = SimpleNamespace(_policy=SimpleNamespace(worker=role), _service=service)
            for asked in (None, "cheap", "strong"):
                key = f"by_class={by_class}|tier={tier}|asked={asked}"
                worker[key] = launcher.WorkerLauncher._model_for(fake, admit, asked)  # type: ignore[arg-type]
    sensitive = getattr(launcher, "sensitive_model", None)
    reviewer: dict[str, str] = {}
    for tier in ("", "cheap", "mid", "strong"):
        fake = SimpleNamespace(_policy=SimpleNamespace(reviewer=SimpleNamespace(model="", tier=tier)), _service=service)
        for worker_model in WORKER_MODELS:
            reviewer[f"tier={tier}|worker={worker_model}"] = ReviewStage._model(fake, worker_model)  # type: ignore[arg-type]
    return {
        "worker": worker,
        "sensitive": sensitive() if sensitive is not None else launcher._TIER_ALIAS["strong"],  # type: ignore[attr-defined]
        "reviewer": reviewer,
    }


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    home = str(tmp_path_factory.mktemp("home"))
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)
    for var in list(os.environ):
        if var.startswith("CLAUDE") or var in (
            "GRIMOIRE_TASK_ID",
            "GRIMOIRE_HOST_PROBE",
            "GRIMOIRE_ACTOR",
            "GRIMOIRE_SESSION_ID",
        ):
            monkeypatch.delenv(var, raising=False)


def test_claude_code_output_is_byte_identical_to_origin_main(tmp_path: Path) -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = scenarios(tmp_path)
    assert actual["heuristic"] == expected["heuristic"]
    assert actual["claimed"] == expected["claimed"]
    assert actual["emitted"]["session_start"] == expected["emitted"]["session_start"]
    assert actual["emitted"]["agents"] == expected["emitted"]["agents"]
    assert actual["launcher"] == expected["launcher"]


def test_the_fixture_covers_every_class_and_the_sensitive_persona() -> None:
    """La preuve porte bien sur V0, V1, V2 et sur security-auditor (#1293/#1294)."""
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    claimed = expected["claimed"]
    assert claimed["V0"]["hookSpecificOutput"]["updatedInput"]["model"] == "haiku"
    assert claimed["V1"]["hookSpecificOutput"]["updatedInput"]["model"] == "sonnet"
    assert claimed["V2"] == {}
    assert "model: 'opus'" in expected["emitted"]["agents"][".claude/agents/security-auditor.md"]
    # La persona déclare son modèle : le hook ne le remplace pas, elle tourne en opus.
    assert expected["heuristic"]["persona_security_auditor"] == {}
    assert expected["heuristic"]["sensitive"] == {}


def test_security_auditor_still_gets_opus_with_a_cheaper_project_table(tmp_path: Path) -> None:
    """Une table projet qui renomme les paliers bas ne fait jamais descendre la persona sensible."""
    from grimoire.hosts.collect import build_surface
    from grimoire.hosts.emitters.claude_code import ClaudeCodeEmitter

    meta = _meta_project(tmp_path / "meta")
    policy = meta / "_grimoire/standard/orchestration-policy.yaml"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text("dispatch:\n  models:\n    claude:\n      anthropic:\n        cheap: claude-haiku-4-5\n")
    files = {
        f.relpath.as_posix(): f.content
        for f in ClaudeCodeEmitter().plan(build_surface(meta, project_name="p", write_cache=False), meta).files
    }
    assert "model: 'opus'" in files[".claude/agents/security-auditor.md"]
    for f in ClaudeCodeEmitter().plan(build_surface(meta, project_name="p", write_cache=False), meta).files:
        (meta / f.relpath).parent.mkdir(parents=True, exist_ok=True)
        (meta / f.relpath).write_text(f.content, encoding="utf-8")
    assert _hook(meta, HEURISTIC_CALLS["persona_security_auditor"]) == {}


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        result = scenarios(Path(tmp))
    Path(sys.argv[1]).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
