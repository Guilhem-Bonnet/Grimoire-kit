"""Tests for guardrail-policy.py."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

_TOOL_PATH = Path(__file__).resolve().parent.parent / "framework" / "tools" / "guardrail-policy.py"
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_STOP_HOOK_SOURCE = _WORKSPACE_ROOT / ".github" / "hooks" / "scripts" / "grimoire-master-stop-hook.sh"
_SPEC = importlib.util.spec_from_file_location("guardrail_policy", _TOOL_PATH)
guardrail_policy = importlib.util.module_from_spec(_SPEC)
sys.modules["guardrail_policy"] = guardrail_policy
assert _SPEC.loader is not None
_SPEC.loader.exec_module(guardrail_policy)


def _create_fake_stop_hook_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    stop_hook = repo_root / ".github" / "hooks" / "scripts" / "grimoire-master-stop-hook.sh"
    stop_hook.parent.mkdir(parents=True, exist_ok=True)
    stop_hook.write_text(_STOP_HOOK_SOURCE.read_text(encoding="utf-8"), encoding="utf-8")
    stop_hook.chmod(0o755)

    policy_script = repo_root / "grimoire-kit" / "framework" / "tools" / "guardrail-policy.py"
    policy_script.parent.mkdir(parents=True, exist_ok=True)
    policy_script.write_text(
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "import sys\n"
        "\n"
        "sys.stdout.write(os.environ.get('STOP_POLICY_OUTPUT', '{}'))\n",
        encoding="utf-8",
    )

    python_wrapper = repo_root / "grimoire-kit" / ".venv" / "bin" / "python"
    python_wrapper.parent.mkdir(parents=True, exist_ok=True)
    python_wrapper.write_text(
        f"#!/usr/bin/env bash\nexec \"{sys.executable}\" \"$@\"\n",
        encoding="utf-8",
    )
    python_wrapper.chmod(0o755)
    return repo_root


def _run_stop_hook(repo_root: Path, payload: dict[str, object], stop_policy_output: dict[str, object]) -> dict[str, object]:
    completed = subprocess.run(
        ["bash", str(repo_root / ".github" / "hooks" / "scripts" / "grimoire-master-stop-hook.sh")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=repo_root,
        env={**os.environ, "STOP_POLICY_OUTPUT": json.dumps(stop_policy_output)},
        check=True,
    )
    return json.loads(completed.stdout)


def test_analyze_prompt_detects_task_list_and_autonomy() -> None:
    prompt = """
    Ensuite fait:
    - transformer ce plan en ADR
    - definir la taxonomie des guardrails
    - implementer la phase 1
    """

    signals = guardrail_policy.analyze_prompt(prompt)

    assert signals.task_items == (
        "transformer ce plan en ADR",
        "definir la taxonomie des guardrails",
        "implementer la phase 1",
    )
    assert "task-list" in signals.tags
    assert signals.autonomous_execution is True


def test_analyze_prompt_detects_met_le_tout_en_place_as_autonomous() -> None:
    signals = guardrail_policy.analyze_prompt("ok mets le tout en place")

    assert signals.autonomous_execution is True
    assert "autonomous" in signals.tags


def test_analyze_prompt_detects_brainstorm_first_for_ambiguous_prompt() -> None:
    prompt = "Je ne comprends pas bien le probleme, brainstorm d'abord sur les options ?"

    signals = guardrail_policy.analyze_prompt(prompt)

    assert signals.brainstorm_recommended is True
    assert "brainstorm" in signals.tags


def test_analyze_prompt_detects_plan_only_and_safety_focus() -> None:
    prompt = "Plan uniquement, sans coder, et fais ca sans casser le projet hooks."

    signals = guardrail_policy.analyze_prompt(prompt)

    assert signals.plan_only is True
    assert signals.safety_focus is True
    assert "plan-only" in signals.tags
    assert "safety" in signals.tags


def test_analyze_prompt_detects_orchestrator_project_protector_and_dispatch_signals() -> None:
    prompt = (
        "Peaufine l'orchestrateur pour qu'il protege le projet, me challenge, "
        "et genere des prompts complets de dispatch."
    )

    signals = guardrail_policy.analyze_prompt(prompt)

    assert "orchestrator-control" in signals.tags
    assert "project-protector" in signals.tags
    assert "dispatch-prompt" in signals.tags


def test_build_clarification_plan_prefers_interactive_batch_for_orchestrator_prompt() -> None:
    prompt = (
        "Peaufine l'orchestrateur pour qu'il ouvre un chat de question input "
        "sans relancer la conversation."
    )
    signals = guardrail_policy.analyze_prompt(prompt)

    plan = guardrail_policy.build_clarification_plan(prompt, prompt.lower(), signals, "hooks-guardrails")

    assert plan["recommended"] is True
    assert plan["toolPreference"] == "vscode/askQuestions"
    assert any("dispatch prompt" in option.lower() for option in plan["options"])
    assert "clarification interactive" in plan["question"].lower()


def test_build_clarification_plan_for_multi_question_hooks_prompt() -> None:
    prompt = "Est ce qu'on peut faire mieux sur les hooks ? Est ce qu'on peut faire mieux sur les tests ? Est ce qu'on peut faire mieux sur les tokens ?"
    signals = guardrail_policy.analyze_prompt(prompt)

    plan = guardrail_policy.build_clarification_plan(prompt, prompt.lower(), signals, "hooks-guardrails")

    assert plan["recommended"] is True
    assert plan["mode"] == "batched-options"
    assert plan["askBeforeRouting"] is True
    assert any("hooks" in option.lower() for option in plan["options"])
    assert "Lequel veux-tu traiter d'abord" in plan["question"]


def test_advance_clarification_state_opens_new_batch_question() -> None:
    prompt = "Est ce qu'on peut faire mieux sur les hooks ? Est ce qu'on peut faire mieux sur les tests ?"
    signals = guardrail_policy.analyze_prompt(prompt)
    clarification = guardrail_policy.build_clarification_plan(prompt, prompt.lower(), signals, "hooks-guardrails")

    state = guardrail_policy.advance_clarification_state(prompt, clarification, {})

    assert state["status"] == "open"
    assert state["relanceCount"] == 0
    assert state["selectedOption"] == ""
    assert "Lequel veux-tu traiter d'abord" in state["question"]


def test_advance_clarification_state_resolves_with_user_answer() -> None:
    previous_state = {
        "status": "open",
        "question": "Je peux prioriser le control plane hooks, la boucle tests/breaker, ou memoire/tokens. Lequel veux-tu traiter d'abord ?",
        "options": [
            "Prioriser le control plane hooks",
            "Prioriser la boucle tests/review/breaker",
            "Prioriser memoire, contexte et tokens",
        ],
        "relanceCount": 0,
    }

    state = guardrail_policy.advance_clarification_state("On priorise les hooks.", {}, previous_state)

    assert state["status"] == "resolved"
    assert state["selectedOption"] == "Prioriser le control plane hooks"
    assert state["resolutionSource"] == "user-answer"


def test_advance_clarification_state_relances_once_then_auto_resolves() -> None:
    previous_state = {
        "status": "open",
        "question": "Je peux prioriser le control plane hooks, la boucle tests/breaker, ou memoire/tokens. Lequel veux-tu traiter d'abord ?",
        "options": [
            "Prioriser le control plane hooks",
            "Prioriser la boucle tests/review/breaker",
            "Prioriser memoire, contexte et tokens",
        ],
        "relanceCount": 0,
    }

    relance_state = guardrail_policy.advance_clarification_state("oui", {}, previous_state)

    assert relance_state["status"] == "needs-relance"
    assert relance_state["relanceCount"] == 1
    assert "une seule relance" in relance_state["instruction"]

    resolved_state = guardrail_policy.advance_clarification_state("go", {}, relance_state)

    assert resolved_state["status"] == "auto-resolved"
    assert resolved_state["selectedOption"] == "Prioriser le control plane hooks"
    assert resolved_state["resolutionSource"] == "go-ahead"


def test_enrich_prompt_signals_recommends_context7_for_library_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(guardrail_policy, "load_tool_module", lambda *args, **kwargs: None)
    monkeypatch.setattr(guardrail_policy, "assess_token_budget", lambda *args, **kwargs: {})
    prompt = "Sur Next.js, quelle configuration utiliser pour un route handler ?"
    signals = guardrail_policy.analyze_prompt(prompt)

    notes, state = guardrail_policy.enrich_prompt_signals(tmp_path, prompt, signals)

    assert state["externalReferences"]["recommended"] is True
    assert "next.js" in state["externalReferences"]["libraries"]
    assert any("Context7" in note for note in notes)


def test_enrich_prompt_signals_requests_challenge_for_risky_shortcut_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(guardrail_policy, "load_tool_module", lambda *args, **kwargs: None)
    monkeypatch.setattr(guardrail_policy, "assess_token_budget", lambda *args, **kwargs: {})
    prompt = "On va bypass les hooks et faire ca sans tests ni review, juste vite fait."
    signals = guardrail_policy.analyze_prompt(prompt)

    notes, state = guardrail_policy.enrich_prompt_signals(tmp_path, prompt, signals)

    assert state["proposalChallenge"]["recommended"] is True
    assert state["proposalChallenge"]["severity"] == "high"
    assert any("Challenge proactif requis" in note for note in notes)


def test_enrich_prompt_signals_builds_dispatch_contract_and_internal_refs_for_orchestrator_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(guardrail_policy, "load_tool_module", lambda *args, **kwargs: None)
    monkeypatch.setattr(guardrail_policy, "assess_token_budget", lambda *args, **kwargs: {})
    prompt = (
        "Peaufine l'orchestrateur pour proteger le projet et produire des prompts complets de dispatch."
    )
    signals = guardrail_policy.analyze_prompt(prompt)

    notes, state = guardrail_policy.enrich_prompt_signals(tmp_path, prompt, signals)

    assert state["dispatchContract"]["required"] is True
    assert state["internalReferences"]["recommended"] is True
    assert any("Contrat de dispatch requis" in note for note in notes)


def test_advance_external_reference_state_marks_pending_requirement() -> None:
    external_references = {
        "recommended": True,
        "source": "context7-first",
        "fallback": "web",
        "libraries": ["next.js"],
        "reason": "question de bibliotheque/API/configuration susceptible de dependre d'une doc a jour",
    }

    state = guardrail_policy.advance_external_reference_state(external_references, {})

    assert state["status"] == "pending"
    assert state["satisfied"] is False
    assert state["libraries"] == ["next.js"]
    assert state["proofs"] == []


def test_resolve_design_authority_prefers_project_style_guide(tmp_path: Path) -> None:
    style_guide = tmp_path / "grimoire-game-assets" / "STYLE_GUIDE.md"
    style_guide.parent.mkdir(parents=True, exist_ok=True)
    style_guide.write_text("# Style Guide\n", encoding="utf-8")
    readme = tmp_path / "grimoire-game-assets" / "README.md"
    readme.write_text("# Assets\n", encoding="utf-8")
    instruction = tmp_path / ".github" / "instructions" / "grimoire-2d-assets.instructions.md"
    instruction.parent.mkdir(parents=True, exist_ok=True)
    instruction.write_text("# Rules\n", encoding="utf-8")

    result = guardrail_policy.resolve_design_authority(tmp_path, "palette sprite fx", "ux-designer", "documentation")

    assert result["found"] is True
    assert result["scope"] == "assets-2d"
    assert "grimoire-game-assets/STYLE_GUIDE.md" in result["sources"]


def test_evaluate_control_surface_denies_write_when_plan_only() -> None:
    raw = json.dumps({"tool_name": "create_file", "tool_input": {"path": "docs/new.md"}})
    prompt_state = {"constraints": {"planOnly": True}}

    decision = guardrail_policy.evaluate_control_surface(raw, prompt_state)

    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_evaluate_control_surface_asks_for_protected_surface_write() -> None:
    raw = json.dumps(
        {
            "tool_name": "edit_file",
            "tool_input": {"path": ".vscode/tasks.json"},
        }
    )

    decision = guardrail_policy.evaluate_control_surface(raw, {})

    assert decision["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert ".vscode/tasks.json" not in decision["hookSpecificOutput"]["permissionDecisionReason"]


def test_evaluate_control_surface_denies_destructive_terminal_command() -> None:
    raw = json.dumps(
        {
            "tool_name": "run_in_terminal",
            "tool_input": {"command": "git reset --hard HEAD"},
        }
    )

    decision = guardrail_policy.evaluate_control_surface(raw, {})

    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_evaluate_memory_guard_asks_for_memory_write() -> None:
    raw = json.dumps(
        {
            "tool_name": "edit_file",
            "tool_input": {"path": "_grimoire-runtime/_memory/shared-context.md"},
        }
    )

    decision = guardrail_policy.evaluate_memory_guard(raw)

    assert decision["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_evaluate_post_edit_blocks_invalid_shell_script(tmp_path: Path) -> None:
    broken_script = tmp_path / "broken.sh"
    broken_script.write_text("#!/usr/bin/env bash\nif then\n", encoding="utf-8")

    raw = json.dumps(
        {
            "tool_name": "edit_file",
            "tool_input": {"filePath": str(broken_script.relative_to(tmp_path))},
        }
    )

    decision = guardrail_policy.evaluate_post_edit(raw, tmp_path, sys.executable)

    assert decision["decision"] == "block"
    assert "bash -n" in decision["hookSpecificOutput"]["additionalContext"]


def test_evaluate_post_edit_allows_valid_json_hook_manifest(tmp_path: Path) -> None:
    hook_manifest = tmp_path / ".github" / "hooks" / "sample.json"
    hook_manifest.parent.mkdir(parents=True, exist_ok=True)
    hook_manifest.write_text('{"hooks": {"PreToolUse": [{"command": "echo ok"}]}}\n', encoding="utf-8")

    raw = json.dumps(
        {
            "tool_name": "create_file",
            "tool_input": {"filePath": str(hook_manifest.relative_to(tmp_path))},
        }
    )

    decision = guardrail_policy.evaluate_post_edit(raw, tmp_path, sys.executable)

    assert decision == {}


def test_evaluate_post_edit_blocks_user_invocable_subagent(tmp_path: Path) -> None:
    subagent = tmp_path / ".github" / "agents" / "dev.agent.md"
    subagent.parent.mkdir(parents=True, exist_ok=True)
    subagent.write_text(
        "---\ndescription: Dev subagent\nuser-invocable: true\n---\n",
        encoding="utf-8",
    )

    raw = json.dumps(
        {
            "tool_name": "edit_file",
            "tool_input": {"filePath": str(subagent.relative_to(tmp_path))},
        }
    )

    decision = guardrail_policy.evaluate_post_edit(raw, tmp_path, sys.executable)

    assert decision["decision"] == "block"
    assert "seul .github/agents/grimoire-master.agent.md peut etre user-invocable" in decision["hookSpecificOutput"]["additionalContext"]


def test_evaluate_post_edit_allows_user_invocable_grimoire_master(tmp_path: Path) -> None:
    master_agent = tmp_path / ".github" / "agents" / "grimoire-master.agent.md"
    master_agent.parent.mkdir(parents=True, exist_ok=True)
    master_agent.write_text(
        "---\ndescription: Grimoire Master\nuser-invocable: true\n---\n",
        encoding="utf-8",
    )

    raw = json.dumps(
        {
            "tool_name": "edit_file",
            "tool_input": {"filePath": str(master_agent.relative_to(tmp_path))},
        }
    )

    decision = guardrail_policy.evaluate_post_edit(raw, tmp_path, sys.executable)

    assert decision == {}


def test_evaluate_post_edit_blocks_prompt_alias_collision_with_agent(tmp_path: Path) -> None:
    agent = tmp_path / ".github" / "agents" / "dev.agent.md"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text("---\ndescription: Dev\nuser-invocable: false\n---\n", encoding="utf-8")

    prompt = tmp_path / ".github" / "prompts" / "dev.prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        "---\ndescription: Dev alias\n---\n\nUtilise l'agent dev existant.\n",
        encoding="utf-8",
    )

    raw = json.dumps(
        {
            "tool_name": "edit_file",
            "tool_input": {"filePath": str(prompt.relative_to(tmp_path))},
        }
    )

    decision = guardrail_policy.evaluate_post_edit(raw, tmp_path, sys.executable)

    assert decision["decision"] == "block"
    assert "collision de basename avec .github/agents/dev.agent.md" in decision["hookSpecificOutput"]["additionalContext"]


def test_evaluate_post_edit_blocks_prompt_alias_collision_with_skill(tmp_path: Path) -> None:
    skill = tmp_path / ".github" / "skills" / "grimoire-health-check" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("---\ndescription: Health check\n---\n\n# Skill\n", encoding="utf-8")

    prompt = tmp_path / ".github" / "prompts" / "grimoire-health-check.prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        "---\ndescription: Health check alias\n---\n\nLance la skill existante.\n",
        encoding="utf-8",
    )

    raw = json.dumps(
        {
            "tool_name": "edit_file",
            "tool_input": {"filePath": str(prompt.relative_to(tmp_path))},
        }
    )

    decision = guardrail_policy.evaluate_post_edit(raw, tmp_path, sys.executable)

    assert decision["decision"] == "block"
    assert ".github/skills/grimoire-health-check/SKILL.md" in decision["hookSpecificOutput"]["additionalContext"]


def test_evaluate_post_edit_blocks_thin_wrapper_prompt(tmp_path: Path) -> None:
    prompt = tmp_path / ".github" / "prompts" / "bmm-create-prd.prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        "---\ndescription: Create PRD\n---\n\n"
        "1. Load {project-root}/_grimoire-runtime/bmm/config.yaml and store ALL fields as session variables\n"
        "2. Load and follow the workflow at {project-root}/_grimoire-runtime/bmm/workflows/2-plan-workflows/create-prd/workflow-create-prd.md\n",
        encoding="utf-8",
    )

    raw = json.dumps(
        {
            "tool_name": "edit_file",
            "tool_input": {"filePath": str(prompt.relative_to(tmp_path))},
        }
    )

    decision = guardrail_policy.evaluate_post_edit(raw, tmp_path, sys.executable)

    assert decision["decision"] == "block"
    assert "thin wrapper prompt interdit" in decision["hookSpecificOutput"]["additionalContext"]


def test_evaluate_post_edit_allows_substantive_prompt_mission_pack(tmp_path: Path) -> None:
    prompt = tmp_path / ".github" / "prompts" / "team-vision" / "kickoff-agent-os.prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(
        "---\ndescription: Kickoff planning package\n---\n\n"
        "1. Load {project-root}/_grimoire-runtime/bmm/config.yaml and store ALL fields as session variables\n"
        "2. Read the operational corpus in this order:\n"
        "   - {project-root}/docs/exploitation/distillat-source-agent-os-game-ui.md\n"
        "   - {project-root}/docs/exploitation/plan-maitre-agent-os-game-ui.md\n"
        "3. Determine the working mode from the user request\n"
        "4. Apply the decision rules before writing anything\n"
        "5. Produce an actionable planning package in French\n"
        "6. Update only the minimal relevant files under {project-root}/docs/exploitation if needed\n"
        "7. End by naming the smallest executable next slice and the evidence expected before calling it done\n",
        encoding="utf-8",
    )

    raw = json.dumps(
        {
            "tool_name": "edit_file",
            "tool_input": {"filePath": str(prompt.relative_to(tmp_path))},
        }
    )

    decision = guardrail_policy.evaluate_post_edit(raw, tmp_path, sys.executable)

    assert decision == {}


def test_evaluate_session_start_writes_latest_payload(tmp_path: Path) -> None:
    config_file = tmp_path / "_grimoire-runtime" / "core" / "config.yaml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("user_name: Guilhem\ncommunication_language: Français\n", encoding="utf-8")
    shared_context_file = tmp_path / "_grimoire-runtime" / "_memory" / "shared-context.md"
    shared_context_file.parent.mkdir(parents=True, exist_ok=True)
    shared_context_file.write_text("> note\n\n- Projet : Grimoire Forge\n", encoding="utf-8")
    latest_file = tmp_path / "_grimoire-runtime-output" / "hook-runtime" / "session-start-latest.json"

    output = guardrail_policy.evaluate_session_start(config_file, shared_context_file, latest_file)

    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert payload["user"] == "Guilhem"
    assert "Projet: Grimoire." in payload["additionalContext"]
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_evaluate_subagent_context_propagates_task_and_brainstorm_signals() -> None:
    prompt_state = {
        "promptPreview": "ok mets le tout en place",
        "constraints": {"planOnly": False, "safetyFocus": True},
        "tags": ["hooks", "task-flow", "task-list", "brainstorm", "autonomous"],
        "clarification": {
            "recommended": True,
            "question": "Je peux prioriser hooks, tests/breaker, ou memoire/tokens. Lequel veux-tu traiter d'abord ?",
            "options": ["Prioriser les hooks", "Prioriser les tests", "Prioriser les tokens"],
        },
        "challengeMode": {"requested": True, "source": "prompt"},
        "debateMode": {"recommended": True, "source": "prompt"},
        "designAuthority": {
            "found": True,
            "scope": "assets-2d",
            "sources": ["grimoire-game-assets/STYLE_GUIDE.md"],
        },
        "externalReferences": {"recommended": True, "libraries": ["vscode", "copilot"]},
        "tokenBudget": {"level": "warning", "usagePct": 0.73},
        "signals": {
            "taskItems": [
                "transformer ce plan en ADR",
                "definir la taxonomie",
                "extraire les hooks restants",
            ],
            "brainstormRecommended": True,
            "autonomousExecutionPreferred": True,
        },
    }
    raw = json.dumps({"agentName": "dev"})

    output = guardrail_policy.evaluate_subagent_context(raw, prompt_state)
    context = output["hookSpecificOutput"]["additionalContext"]

    assert "Checklist prioritaire detectee" in context
    assert "living checklist ou todo list" in context
    assert "Signal brainstorm" in context
    assert "Execution autonome attendue" in context
    assert "Clarification non resolue" in context
    assert "Mode challenge actif" in context
    assert "Mode debat recommande" in context
    assert "DA projet a appliquer" in context
    assert "Context7" in context
    assert "Budget token warning" in context


def test_evaluate_subagent_context_uses_resolved_clarification_without_reasking() -> None:
    prompt_state = {
        "promptPreview": "On priorise les hooks et on execute",
        "constraints": {"planOnly": False, "safetyFocus": False},
        "tags": ["hooks", "autonomous"],
        "clarification": {
            "recommended": True,
            "question": "Je peux prioriser hooks, tests/breaker, ou memoire/tokens. Lequel veux-tu traiter d'abord ?",
            "options": ["Hooks", "Tests", "Tokens"],
        },
        "clarificationState": {
            "status": "resolved",
            "selectedOption": "Hooks",
            "resolutionSource": "user-answer",
        },
        "signals": {
            "taskItems": [],
            "brainstormRecommended": False,
            "autonomousExecutionPreferred": True,
        },
    }

    output = guardrail_policy.evaluate_subagent_context(json.dumps({"agentName": "dev"}), prompt_state)
    context = output["hookSpecificOutput"]["additionalContext"]

    assert "Clarification resolue" in context
    assert "Hooks" in context
    assert "Clarification non resolue" not in context


def test_evaluate_subagent_context_mentions_external_reference_proof_when_captured() -> None:
    prompt_state = {
        "promptPreview": "Configurer Next.js proprement",
        "constraints": {"planOnly": False, "safetyFocus": False},
        "tags": ["autonomous"],
        "externalReferences": {
            "recommended": True,
            "source": "context7-first",
            "fallback": "web",
            "libraries": ["next.js"],
        },
        "externalReferenceState": {
            "status": "context7-proved",
            "satisfied": True,
            "libraryId": "/vercel/next.js",
            "proofs": [
                {
                    "source": "context7",
                    "stage": "query-docs",
                    "libraryId": "/vercel/next.js",
                }
            ],
        },
        "signals": {
            "taskItems": [],
            "brainstormRecommended": False,
            "autonomousExecutionPreferred": True,
        },
    }

    output = guardrail_policy.evaluate_subagent_context(json.dumps({"agentName": "dev"}), prompt_state)
    context = output["hookSpecificOutput"]["additionalContext"]

    assert "Preuve externe capturee" in context
    assert "/vercel/next.js" in context


def test_evaluate_subagent_context_mentions_proposal_challenge_when_present() -> None:
    prompt_state = {
        "promptPreview": "Bypasser les hooks sans tests",
        "constraints": {"planOnly": False, "safetyFocus": True},
        "proposalChallenge": {
            "recommended": True,
            "severity": "high",
            "summary": "La demande court-circuite les hooks, les tests et la review.",
            "instruction": "Avant execution, challenger l'idee, expliciter les risques et proposer une alternative reversible.",
        },
        "signals": {
            "taskItems": [],
            "brainstormRecommended": False,
            "autonomousExecutionPreferred": False,
        },
    }

    output = guardrail_policy.evaluate_subagent_context(json.dumps({"agentName": "dev"}), prompt_state)
    context = output["hookSpecificOutput"]["additionalContext"]

    assert "Challenge utilisateur requis" in context
    assert "alternative reversible" in context


def test_evaluate_subagent_context_mentions_dispatch_contract_and_project_protector() -> None:
    prompt_state = {
        "promptPreview": "Renforcer le Master avant delegation",
        "constraints": {"planOnly": False, "safetyFocus": True},
        "tags": ["project-protector", "interactive-clarification"],
        "clarification": {
            "recommended": True,
            "question": "Je peux verrouiller l'intake protecteur, la clarification interactive, ou le dispatch prompt. Lequel veux-tu cadrer en premier ?",
            "options": [
                "Prioriser l'intake protecteur (objectif, plus-value, angles morts)",
                "Prioriser la clarification interactive continue",
                "Prioriser le contrat de dispatch prompt vers les subagents",
            ],
        },
        "dispatchContract": {
            "required": True,
            "sections": [
                "Mission et resultat attendu",
                "Objectif utilisateur et plus-value visee",
                "Contexte projet et invariants non-negociables",
            ],
        },
        "signals": {
            "taskItems": [],
            "brainstormRecommended": False,
            "autonomousExecutionPreferred": False,
        },
    }

    output = guardrail_policy.evaluate_subagent_context(json.dumps({"agentName": "dev"}), prompt_state)
    context = output["hookSpecificOutput"]["additionalContext"]

    assert "Protecteur projet" in context
    assert "Contrat de dispatch" in context
    assert "Clarification interactive souhaitee" in context


def test_command_prompt_signals_enriches_state_with_memory_and_routing(tmp_path: Path, monkeypatch) -> None:
    latest_file = tmp_path / "latest.json"
    events_file = tmp_path / "events.jsonl"
    monkeypatch.setattr(
        guardrail_policy,
        "enrich_prompt_signals",
        lambda project_root, prompt, signals: (
            (
                "Routage suggere: architect (deep_reasoning, confiance 82%). analyser la structure hook avant implementation",
                "Pattern procedural utile: Verifier controlFiles avant promotion hook",
                "Rappel contextuel: Revalider les hooks apres extension du moteur Python",
                "RAG borne active: docs/hooks-runtime.md (0.73).",
            ),
            {
                "routing": {
                    "suggestedAgent": "architect",
                    "classification": "deep_reasoning",
                    "confidence": 0.82,
                    "reasoning": "analyser la structure hook avant implementation",
                    "alternatives": ["dev"],
                },
                "memoryHints": {
                    "taskType": "hooks-guardrails",
                    "proceduralPatterns": ["Verifier controlFiles avant promotion hook"],
                    "nudges": [
                        {
                            "title": "Memo",
                            "message": "Revalider les hooks apres extension du moteur Python",
                            "relevance": 0.91,
                        }
                    ],
                },
                "rag": {
                    "enabled": True,
                    "chunks": [
                        {
                            "source": "docs/hooks-runtime.md",
                            "score": 0.73,
                            "text": "Les wrappers shell doivent rester minces et deleguer au policy engine.",
                        }
                    ],
                },
            },
        ),
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"prompt": "Hooks, quelles pistes ?", "timestamp": "2026-04-13T23:30:00Z"})),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    args = argparse.Namespace(
        project_root=tmp_path,
        latest_file=latest_file,
        events_file=events_file,
        max_context_length=900,
    )

    result = guardrail_policy.command_prompt_signals(args)

    assert result == 0
    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert payload["routing"]["suggestedAgent"] == "architect"
    assert payload["memoryHints"]["taskType"] == "hooks-guardrails"
    assert payload["rag"]["enabled"] is True
    assert any("Pattern procedural utile" in note for note in payload["notes"])
    assert any("Rappel contextuel" in note for note in payload["notes"])
    assert any("RAG borne active" in note for note in payload["notes"])
    hook_output = json.loads(stdout.getvalue())
    assert hook_output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_evaluate_subagent_stop_persists_review_and_context(tmp_path: Path, monkeypatch) -> None:
    latest_file = tmp_path / "subagent-stop-latest.json"
    events_file = tmp_path / "subagent-stop-events.jsonl"
    counter_file = tmp_path / "subagent-stop-counters.json"
    prompt_state = {
        "promptPreview": "Implementer les hooks de la prochaine vague",
        "memoryHints": {"taskType": "hooks-guardrails"},
    }

    class FakeCriteria:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeEvaluation:
        score = 0.61
        grade = "C"
        passed = True

        def to_dict(self) -> dict[str, object]:
            return {"score": self.score, "grade": self.grade, "passed": self.passed}

    class FakeEvaluator:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        def evaluate(self, agent: str, output: str, task: str, criteria: FakeCriteria) -> FakeEvaluation:
            assert agent == "dev"
            assert "hooks" in task.lower()
            return FakeEvaluation()

    class FakeTrustScore:
        def to_dict(self) -> dict[str, object]:
            return {"level": "cautious", "score": 0.45}

    class FakeTrustScorer:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        def score(self, agent: str) -> FakeTrustScore:
            assert agent == "dev"
            return FakeTrustScore()

    class FakeTelemetry:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        def record_skill(self, *args: object, **kwargs: object) -> None:
            return None

    def fake_load_core_symbol(project_root: Path, module_name: str, symbol_name: str):
        mapping = {
            ("grimoire.core.evaluator", "Evaluator"): FakeEvaluator,
            ("grimoire.core.evaluator", "EvalCriteria"): FakeCriteria,
            ("grimoire.core.telemetry", "Telemetry"): FakeTelemetry,
            ("grimoire.core.trust_scorer", "TrustScorer"): FakeTrustScorer,
        }
        return mapping.get((module_name, symbol_name))

    monkeypatch.setattr(guardrail_policy, "load_core_symbol", fake_load_core_symbol)
    monkeypatch.setattr(guardrail_policy, "maybe_record_learning", lambda *args, **kwargs: "hook-learning-001")

    raw = json.dumps(
        {
            "hookEventName": "SubagentStop",
            "agent_type": "dev",
            "task": "Implementer les hooks de la prochaine vague",
            "output": "J'ai etendu le moteur hooks et ajoute des tests de non-regression.",
        }
    )

    output = guardrail_policy.evaluate_subagent_stop(
        raw,
        prompt_state,
        tmp_path,
        latest_file,
        events_file,
        counter_file,
    )

    latest_payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert latest_payload["grade"] == "C"
    assert latest_payload["taskType"] == "hooks-guardrails"
    assert "quality-yellow" in latest_payload["flags"]
    assert "trust-yellow" in latest_payload["flags"]
    assert any("Learning auto enregistre" in note for note in latest_payload["notes"])
    assert "Aggregation prudente" in output["hookSpecificOutput"]["additionalContext"]


def test_evaluate_subagent_stop_detects_conflict_and_breaker(tmp_path: Path, monkeypatch) -> None:
    latest_file = tmp_path / "subagent-stop-latest.json"
    events_file = tmp_path / "subagent-stop-events.jsonl"
    counter_file = tmp_path / "subagent-stop-counters.json"
    latest_file.write_text(
        json.dumps(
            {
                "agent": "architect",
                "task": "Run pytest hooks suite",
                "grade": "A",
                "trust": {"level": "trusted", "score": 0.91},
            }
        ),
        encoding="utf-8",
    )
    prompt_state = {
        "promptPreview": "Run pytest hooks suite",
        "memoryHints": {"taskType": "testing"},
    }

    class FakeCriteria:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeEvaluation:
        score = 0.21
        grade = "F"
        passed = False

        def to_dict(self) -> dict[str, object]:
            return {"score": self.score, "grade": self.grade, "passed": self.passed}

    class FakeEvaluator:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        def evaluate(self, agent: str, output: str, task: str, criteria: FakeCriteria) -> FakeEvaluation:
            return FakeEvaluation()

    class FakeTrustScore:
        def to_dict(self) -> dict[str, object]:
            return {"level": "untrusted", "score": 0.12}

    class FakeTrustScorer:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        def score(self, agent: str) -> FakeTrustScore:
            return FakeTrustScore()

    monkeypatch.setattr(
        guardrail_policy,
        "load_core_symbol",
        lambda project_root, module_name, symbol_name: {
            ("grimoire.core.evaluator", "Evaluator"): FakeEvaluator,
            ("grimoire.core.evaluator", "EvalCriteria"): FakeCriteria,
            ("grimoire.core.trust_scorer", "TrustScorer"): FakeTrustScorer,
        }.get((module_name, symbol_name)),
    )
    monkeypatch.setattr(guardrail_policy, "maybe_record_failure", lambda *args: "failure-001")

    raw = json.dumps(
        {
            "hookEventName": "SubagentStop",
            "agent_type": "qa",
            "task": "Run pytest hooks suite",
            "output": "La suite est casse et la confiance est mauvaise.",
        }
    )

    output = guardrail_policy.evaluate_subagent_stop(
        raw,
        prompt_state,
        tmp_path,
        latest_file,
        events_file,
        counter_file,
    )

    latest_payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert "conflict-red" in latest_payload["flags"]
    assert latest_payload["conflict"]["recommendedAction"] == "challenge-mode"
    assert latest_payload["followUp"]["challengeRecommended"] is True
    assert "Conflit inter-subagents detecte" in output["hookSpecificOutput"]["additionalContext"]


def test_evaluate_post_edit_returns_warning_context_without_block(tmp_path: Path, monkeypatch) -> None:
    markdown_file = tmp_path / "docs" / "index.md"
    markdown_file.parent.mkdir(parents=True, exist_ok=True)
    markdown_file.write_text("# Titre\n", encoding="utf-8")
    raw = json.dumps({"tool_name": "edit_file", "tool_input": {"filePath": "docs/index.md"}})

    monkeypatch.setattr(
        guardrail_policy,
        "validate_candidate_paths",
        lambda candidate_paths, project_root, python_executable: ([], ["docs/index.md: score qualite 61/100"]),
    )

    decision = guardrail_policy.evaluate_post_edit(raw, tmp_path, sys.executable)

    assert "decision" not in decision
    assert decision["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "score qualite 61/100" in decision["hookSpecificOutput"]["additionalContext"]


def test_evaluate_precompact_persists_prompt_signals(tmp_path: Path) -> None:
    prompt_state = {
        "promptPreview": "ok mets le tout en place",
        "constraints": {"planOnly": False, "safetyFocus": False},
        "tags": ["hooks", "task-list", "autonomous"],
        "clarification": {"recommended": True, "options": ["A", "B", "C"]},
        "challengeMode": {"requested": True},
        "debateMode": {"recommended": True},
        "designAuthority": {"fallback": True, "fallbackPrinciples": ["Lisibilite avant spectacle"]},
        "tokenBudget": {"level": "warning", "usagePct": 0.74, "recommendations": ["Compacter avant synthese"]},
        "signals": {
            "taskItems": ["extraire les capsules", "promouvoir les hooks"],
            "brainstormRecommended": False,
            "autonomousExecutionPreferred": True,
        },
    }
    task_state = {"task": "grimoire: lint", "status": "success"}
    trace_state = {"event": "SubagentStop", "agent": "architect"}
    subagent_state = {"agent": "architect", "grade": "D", "trust": {"level": "untrusted", "score": 0.2}}
    latest_file = tmp_path / "latest.json"
    events_file = tmp_path / "events.jsonl"
    raw = json.dumps({"summary_source": "auto", "transcriptPath": "/tmp/transcript.json"})

    original_workflow_recap = guardrail_policy.workflow_recap
    guardrail_policy.workflow_recap = lambda project_root: "Workflow recap: 5 evenements, 2 skills observes"

    try:
        output = guardrail_policy.evaluate_precompact(
            raw,
            prompt_state,
            task_state,
            trace_state,
            subagent_state,
            tmp_path,
            latest_file,
            events_file,
        )
    finally:
        guardrail_policy.workflow_recap = original_workflow_recap

    latest_payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert latest_payload["signals"]["taskItems"] == ["extraire les capsules", "promouvoir les hooks"]
    assert "Checklist detectee" in latest_payload["capsule"]
    assert "Execution autonome preferee" in latest_payload["capsule"]
    assert "Clarification batch recommandee" in latest_payload["capsule"]
    assert "Challenge Mode actif" in latest_payload["capsule"]
    assert "Fallback DA standard active" in latest_payload["capsule"]
    assert "Budget token warning" in latest_payload["capsule"]
    assert latest_payload["workflowRecap"] == "Workflow recap: 5 evenements, 2 skills observes"
    assert "Signaux faibles" in latest_payload["capsule"]
    assert "trust scorer en zone rouge" in latest_payload["weakSignals"]
    assert output["hookSpecificOutput"]["hookEventName"] == "PreCompact"


def test_command_stop_closure_writes_summary(tmp_path: Path, monkeypatch) -> None:
    latest_file = tmp_path / "stop-latest.json"
    prompt_state_file = tmp_path / "prompt-state.json"
    task_latest_file = tmp_path / "task-latest.json"
    subagent_latest_file = tmp_path / "subagent-latest.json"

    prompt_state_file.write_text(json.dumps({"promptPreview": "Finaliser la vague hooks"}), encoding="utf-8")
    task_latest_file.write_text(json.dumps({"task": "grimoire: lint", "status": "success"}), encoding="utf-8")
    subagent_latest_file.write_text(json.dumps({"agent": "dev", "grade": "B"}), encoding="utf-8")

    class FakeTelemetry:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        def record_session(self, *args: object, **kwargs: object) -> None:
            return None

    monkeypatch.setattr(
        guardrail_policy,
        "load_core_symbol",
        lambda project_root, module_name, symbol_name: FakeTelemetry
        if (module_name, symbol_name) == ("grimoire.core.telemetry", "Telemetry")
        else None,
    )
    monkeypatch.setattr(
        guardrail_policy,
        "assess_token_budget",
        lambda project_root, prompt_lower="", force=False: {"level": "warning", "usagePct": 0.71},
    )
    follow_through_calls: list[list[str]] = []

    monkeypatch.setattr(
        guardrail_policy,
        "execute_logical_follow_through_tasks",
        lambda project_root, objective, logical_next_tasks: (
            follow_through_calls.append([str(item.get("task") or "") for item in logical_next_tasks])
            or {
                "status": "completed",
                "executedTasks": [str(item.get("task") or "") for item in logical_next_tasks],
                "failedTask": "",
                "results": [],
            }
        ),
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"stop_hook_active": False})))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    args = argparse.Namespace(
        project_root=tmp_path,
        prompt_state_file=prompt_state_file,
        task_latest_file=task_latest_file,
        subagent_latest_file=subagent_latest_file,
        latest_file=latest_file,
    )

    result = guardrail_policy.command_stop_closure(args)

    assert result == 0
    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert "objectif=Finaliser la vague hooks" in payload["summary"]
    assert "task=grimoire: lint:success" in payload["summary"]
    assert "subagent=dev:B" in payload["summary"]
    assert "Avant cloture" in payload["additionalContext"]
    assert payload["logicalFollowThrough"]["status"] == "completed"
    assert payload["logicalFollowThrough"]["executedTasks"] == [
        "grimoire: quickcheck",
        "grimoire: memory-lint",
        "grimoire: preflight",
    ]
    assert follow_through_calls == [["grimoire: quickcheck", "grimoire: memory-lint", "grimoire: preflight"]]
    hook_output = json.loads(stdout.getvalue())
    assert hook_output["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert "Budget token warning" in hook_output["hookSpecificOutput"]["additionalContext"]


def test_command_stop_closure_blocks_on_critical_closure_risk(tmp_path: Path, monkeypatch) -> None:
    hook_runtime_dir = tmp_path / "hook-runtime"
    latest_file = hook_runtime_dir / "stop" / "latest.json"
    prompt_state_file = hook_runtime_dir / "user-prompt-latest.json"
    task_latest_file = tmp_path / "task-latest.json"
    subagent_latest_file = hook_runtime_dir / "subagent-stop" / "latest.json"

    prompt_state_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_state_file.write_text(
        json.dumps({"promptPreview": "Finaliser la vague hooks", "tokenBudget": {"level": "warning", "usagePct": 0.71}}),
        encoding="utf-8",
    )
    task_latest_file.write_text(json.dumps({"task": "grimoire: lint", "status": "failed"}), encoding="utf-8")
    subagent_latest_file.parent.mkdir(parents=True, exist_ok=True)
    subagent_latest_file.write_text(
        json.dumps(
            {
                "agent": "qa",
                "grade": "F",
                "flags": ["quality-red", "trust-red", "conflict-red"],
                "conflict": {
                    "previousAgent": "architect",
                    "previousGrade": "A",
                    "currentAgent": "qa",
                    "currentGrade": "F",
                },
                "followUp": {"challengeRecommended": True},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"stop_hook_active": False})))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    args = argparse.Namespace(
        project_root=tmp_path,
        prompt_state_file=prompt_state_file,
        task_latest_file=task_latest_file,
        subagent_latest_file=subagent_latest_file,
        latest_file=latest_file,
    )

    result = guardrail_policy.command_stop_closure(args)

    assert result == 0
    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert payload["closureRisk"]["level"] == "critical"
    assert payload["closureRisk"]["decision"] == "block"
    hook_output = json.loads(stdout.getvalue())
    assert hook_output["hookSpecificOutput"]["decision"] == "block"
    assert "Risque de cloture critique" in hook_output["hookSpecificOutput"]["reason"]


def test_command_stop_closure_blocks_and_opens_ticket_for_high_risk(tmp_path: Path, monkeypatch) -> None:
    hook_runtime_dir = tmp_path / "hook-runtime"
    latest_file = hook_runtime_dir / "stop" / "latest.json"
    prompt_state_file = hook_runtime_dir / "user-prompt-latest.json"
    task_latest_file = tmp_path / "task-latest.json"
    subagent_latest_file = hook_runtime_dir / "subagent-stop" / "latest.json"

    prompt_state_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_state_file.write_text(
        json.dumps(
            {
                "promptPreview": "Configurer Next.js proprement",
                "externalReferences": {
                    "recommended": True,
                    "source": "context7-first",
                    "fallback": "web",
                    "libraries": ["next.js"],
                },
                "externalReferenceState": {
                    "status": "pending",
                    "satisfied": False,
                    "libraries": ["next.js"],
                    "proofs": [],
                },
                "tokenBudget": {"level": "warning", "usagePct": 0.68},
            }
        ),
        encoding="utf-8",
    )
    task_latest_file.write_text(json.dumps({"task": "grimoire: preflight", "status": "success"}), encoding="utf-8")
    subagent_latest_file.parent.mkdir(parents=True, exist_ok=True)
    subagent_latest_file.write_text(
        json.dumps(
            {
                "agent": "qa",
                "grade": "B",
                "followUp": {"breakerRecommended": True},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"stop_hook_active": False})))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    args = argparse.Namespace(
        project_root=tmp_path,
        prompt_state_file=prompt_state_file,
        task_latest_file=task_latest_file,
        subagent_latest_file=subagent_latest_file,
        latest_file=latest_file,
    )

    result = guardrail_policy.command_stop_closure(args)

    assert result == 0
    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert payload["closureRisk"]["level"] == "high"
    assert payload["closureRisk"]["decision"] == "block"
    tickets_payload = json.loads((tmp_path / "_grimoire-runtime-output" / "task-flow" / "deferred-tickets.json").read_text(encoding="utf-8"))
    ticket = next(item for item in tickets_payload["tickets"] if item["id"] == "closure-risk-review")
    assert ticket["status"] == "open"
    assert ticket["priority"] == "high"
    hook_output = json.loads(stdout.getvalue())
    assert hook_output["hookSpecificOutput"]["decision"] == "block"
    assert "Risque de cloture high" in hook_output["hookSpecificOutput"]["reason"]


def test_command_stop_closure_executes_logical_next_tasks_and_closes_follow_through_ticket(tmp_path: Path, monkeypatch) -> None:
    hook_runtime_dir = tmp_path / "hook-runtime"
    latest_file = hook_runtime_dir / "stop" / "latest.json"
    prompt_state_file = hook_runtime_dir / "user-prompt-latest.json"
    task_latest_file = tmp_path / "task-latest.json"
    subagent_latest_file = hook_runtime_dir / "subagent-stop" / "latest.json"

    prompt_state_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_state_file.write_text(
        json.dumps(
            {
                "promptPreview": "Renforcer les hooks de guardrail",
                "tags": ["hooks", "task-flow"],
                "memoryHints": {"taskType": "hooks-guardrails"},
            }
        ),
        encoding="utf-8",
    )
    task_latest_file.write_text(json.dumps({"task": "grimoire: preflight", "status": "success"}), encoding="utf-8")
    subagent_latest_file.parent.mkdir(parents=True, exist_ok=True)
    subagent_latest_file.write_text(json.dumps({"agent": "dev", "grade": "B"}), encoding="utf-8")

    execution_calls: list[list[str]] = []

    def fake_run(*args, **kwargs):
        command = list(args[0])
        execution_calls.append(command)

        class Result:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Result()

    monkeypatch.setattr(guardrail_policy.subprocess, "run", fake_run)

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"stop_hook_active": False})))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    args = argparse.Namespace(
        project_root=tmp_path,
        prompt_state_file=prompt_state_file,
        task_latest_file=task_latest_file,
        subagent_latest_file=subagent_latest_file,
        latest_file=latest_file,
    )

    result = guardrail_policy.command_stop_closure(args)

    assert result == 0
    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert payload["logicalNextTasks"] == []
    assert payload["logicalFollowThrough"]["status"] == "completed"
    assert payload["logicalFollowThrough"]["executedTasks"] == [
        "grimoire: quickcheck",
        "grimoire: memory-lint",
        "grimoire: preflight",
    ]
    tickets_payload = json.loads((tmp_path / "_grimoire-runtime-output" / "task-flow" / "deferred-tickets.json").read_text(encoding="utf-8"))
    ticket = next(item for item in tickets_payload["tickets"] if item["id"] == "logical-follow-through")
    assert ticket["status"] == "closed"
    assert ticket["recommendedTasks"] == []
    assert ticket["executedTasks"] == ["grimoire: quickcheck", "grimoire: memory-lint", "grimoire: preflight"]
    hook_output = json.loads(stdout.getvalue())
    assert "decision" not in hook_output["hookSpecificOutput"]
    assert len(execution_calls) == 3


def test_execute_logical_follow_through_tasks_skips_when_signature_already_satisfied(tmp_path: Path, monkeypatch) -> None:
    report_path = tmp_path / "_grimoire-runtime-output" / "task-flow" / "logical-follow-through.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "signature": guardrail_policy.make_signature(
                    "Renforcer les hooks de guardrail",
                    "grimoire: quickcheck",
                    "grimoire: memory-lint",
                    "grimoire: preflight",
                ),
                "status": "completed",
                "executedTasks": [
                    "grimoire: quickcheck",
                    "grimoire: memory-lint",
                    "grimoire: preflight",
                ],
            }
        ),
        encoding="utf-8",
    )

    def fail_run(*args, **kwargs):  # pragma: no cover - should never execute
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(guardrail_policy.subprocess, "run", fail_run)

    result = guardrail_policy.execute_logical_follow_through_tasks(
        tmp_path,
        "Renforcer les hooks de guardrail",
        [
            {"task": "grimoire: quickcheck"},
            {"task": "grimoire: memory-lint"},
            {"task": "grimoire: preflight"},
        ],
    )

    assert result["status"] == "already-satisfied"
    assert result["executedTasks"] == [
        "grimoire: quickcheck",
        "grimoire: memory-lint",
        "grimoire: preflight",
    ]


def test_execute_logical_follow_through_tasks_reports_missing_task_flow_script(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(guardrail_policy, "resolve_task_flow_script", lambda project_root: project_root / ".github" / "hooks" / "scripts" / "missing.sh")

    result = guardrail_policy.execute_logical_follow_through_tasks(
        tmp_path,
        "Renforcer les hooks de guardrail",
        [{"task": "grimoire: quickcheck"}],
    )

    assert result["status"] == "unavailable"
    assert result["reason"] == "task-flow-script-missing"
    report = json.loads((tmp_path / "_grimoire-runtime-output" / "task-flow" / "logical-follow-through.json").read_text(encoding="utf-8"))
    assert report["status"] == "unavailable"


def test_execute_logical_follow_through_tasks_reports_unsupported_task(tmp_path: Path, monkeypatch) -> None:
    task_flow_script = tmp_path / ".github" / "hooks" / "scripts" / "grimoire-task-flow.sh"
    task_flow_script.parent.mkdir(parents=True, exist_ok=True)
    task_flow_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    task_flow_script.chmod(0o755)

    monkeypatch.setattr(guardrail_policy, "resolve_task_flow_script", lambda project_root: task_flow_script)

    result = guardrail_policy.execute_logical_follow_through_tasks(
        tmp_path,
        "Renforcer les hooks de guardrail",
        [{"task": "grimoire: unsupported"}],
    )

    assert result["status"] == "unsupported-task"
    assert result["failedTask"] == "grimoire: unsupported"
    assert result["results"][0]["stderr"] == "unsupported logical follow-through task"


def test_execute_logical_follow_through_tasks_reports_timeout(tmp_path: Path, monkeypatch) -> None:
    task_flow_script = tmp_path / ".github" / "hooks" / "scripts" / "grimoire-task-flow.sh"
    task_flow_script.parent.mkdir(parents=True, exist_ok=True)
    task_flow_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    task_flow_script.chmod(0o755)

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"], output="partial stdout", stderr="partial stderr")

    monkeypatch.setattr(guardrail_policy, "resolve_task_flow_script", lambda project_root: task_flow_script)
    monkeypatch.setattr(guardrail_policy.subprocess, "run", raise_timeout)

    result = guardrail_policy.execute_logical_follow_through_tasks(
        tmp_path,
        "Renforcer les hooks de guardrail",
        [{"task": "grimoire: quickcheck"}],
    )

    assert result["status"] == "timeout"
    assert result["failedTask"] == "grimoire: quickcheck"
    assert result["results"][0]["returnCode"] == 124
    assert result["results"][0]["stdout"] == "partial stdout"


def test_execute_logical_follow_through_tasks_stops_after_first_failure(tmp_path: Path, monkeypatch) -> None:
    task_flow_script = tmp_path / ".github" / "hooks" / "scripts" / "grimoire-task-flow.sh"
    task_flow_script.parent.mkdir(parents=True, exist_ok=True)
    task_flow_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    task_flow_script.chmod(0o755)

    execution_calls: list[list[str]] = []

    def fake_run(*args, **kwargs):
        command = list(args[0])
        execution_calls.append(command)

        class Result:
            def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        if len(execution_calls) == 1:
            return Result(0, "ok", "")
        return Result(2, "", "boom")

    monkeypatch.setattr(guardrail_policy, "resolve_task_flow_script", lambda project_root: task_flow_script)
    monkeypatch.setattr(guardrail_policy.subprocess, "run", fake_run)

    result = guardrail_policy.execute_logical_follow_through_tasks(
        tmp_path,
        "Renforcer les hooks de guardrail",
        [
            {"task": "grimoire: quickcheck"},
            {"task": "grimoire: memory-lint"},
            {"task": "grimoire: preflight"},
        ],
    )

    assert result["status"] == "failed"
    assert result["failedTask"] == "grimoire: memory-lint"
    assert result["executedTasks"] == ["grimoire: quickcheck"]
    assert len(execution_calls) == 2


def test_command_stop_closure_blocks_when_logical_follow_through_fails(tmp_path: Path, monkeypatch) -> None:
    hook_runtime_dir = tmp_path / "hook-runtime"
    latest_file = hook_runtime_dir / "stop" / "latest.json"
    prompt_state_file = hook_runtime_dir / "user-prompt-latest.json"
    task_latest_file = tmp_path / "task-latest.json"
    subagent_latest_file = hook_runtime_dir / "subagent-stop" / "latest.json"

    prompt_state_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_state_file.write_text(
        json.dumps(
            {
                "promptPreview": "Renforcer les hooks de guardrail",
                "tags": ["hooks", "task-flow"],
                "memoryHints": {"taskType": "hooks-guardrails"},
            }
        ),
        encoding="utf-8",
    )
    task_latest_file.write_text(json.dumps({"task": "grimoire: preflight", "status": "success"}), encoding="utf-8")
    subagent_latest_file.parent.mkdir(parents=True, exist_ok=True)
    subagent_latest_file.write_text(json.dumps({"agent": "dev", "grade": "B"}), encoding="utf-8")

    monkeypatch.setattr(
        guardrail_policy,
        "execute_logical_follow_through_tasks",
        lambda project_root, objective, logical_next_tasks: {
            "status": "failed",
            "executedTasks": ["grimoire: quickcheck"],
            "failedTask": "grimoire: memory-lint",
            "results": [],
        },
    )

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"stop_hook_active": False})))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    args = argparse.Namespace(
        project_root=tmp_path,
        prompt_state_file=prompt_state_file,
        task_latest_file=task_latest_file,
        subagent_latest_file=subagent_latest_file,
        latest_file=latest_file,
    )

    result = guardrail_policy.command_stop_closure(args)

    assert result == 0
    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert payload["logicalFollowThrough"]["status"] == "failed"
    assert payload["logicalNextTasks"] != []
    tickets_payload = json.loads((tmp_path / "_grimoire-runtime-output" / "task-flow" / "deferred-tickets.json").read_text(encoding="utf-8"))
    ticket = next(item for item in tickets_payload["tickets"] if item["id"] == "logical-follow-through")
    assert ticket["status"] == "open"
    assert ticket["failedTask"] == "grimoire: memory-lint"
    hook_output = json.loads(stdout.getvalue())
    assert hook_output["hookSpecificOutput"]["decision"] == "block"
    assert "Suite logique auto-executee en echec" in hook_output["hookSpecificOutput"]["additionalContext"]


def test_load_guardrail_rules_reads_external_yaml(tmp_path: Path) -> None:
    rules_file = tmp_path / "grimoire-kit" / "framework" / "tools" / "guardrail-policy-rules.yaml"
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text(
        "challenge:\n"
        "  skipTests:\n"
        "    - sans qualif\n"
        "followThrough:\n"
        "  taskMap:\n"
        "    quick-check: 'grimoire: quickcheck'\n"
        "  taskSpecs:\n"
        "    'grimoire: quickcheck':\n"
        "      flow: quality\n"
        "      command:\n"
        "        - bash\n"
        "        - framework/tools/quick-check.sh\n"
        "      timeoutSeconds: 30\n",
        encoding="utf-8",
    )

    rules = guardrail_policy.load_guardrail_rules(tmp_path)

    assert rules["challenge"]["skipTests"] == ["sans qualif"]
    assert rules["followThrough"]["taskSpecs"]["grimoire: quickcheck"]["timeoutSeconds"] == 30


def test_stop_hook_integration_forwards_policy_block_reason_and_context(tmp_path: Path) -> None:
    repo_root = _create_fake_stop_hook_repo(tmp_path)

    result = _run_stop_hook(
        repo_root,
        {"stop_hook_active": False},
        {
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "decision": "block",
                "reason": "Block reason",
                "additionalContext": "Context detail",
            }
        },
    )

    assert result["hookSpecificOutput"]["decision"] == "block"
    assert result["hookSpecificOutput"]["reason"] == "Block reason"
    assert result["hookSpecificOutput"]["additionalContext"] == "Context detail"


def test_stop_hook_integration_short_circuits_when_stop_hook_is_active(tmp_path: Path) -> None:
    repo_root = _create_fake_stop_hook_repo(tmp_path)

    result = _run_stop_hook(
        repo_root,
        {"stop_hook_active": True},
        {
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "decision": "block",
                "reason": "Should be ignored",
            }
        },
    )

    assert result == {"continue": True}


def test_command_prompt_signals_initializes_session_state(tmp_path: Path, monkeypatch) -> None:
    hook_runtime_dir = tmp_path / "hook-runtime"
    latest_file = hook_runtime_dir / "user-prompt-latest.json"
    events_file = hook_runtime_dir / "user-prompt-events.jsonl"

    monkeypatch.setattr(
        guardrail_policy,
        "enrich_prompt_signals",
        lambda project_root, prompt, signals: (
            (),
            {
                "clarification": {
                    "recommended": True,
                    "mode": "batched-options",
                    "question": "Je peux prioriser hooks, tests, ou tokens. Lequel veux-tu traiter d'abord ?",
                    "options": ["Hooks", "Tests", "Tokens"],
                },
                "tokenBudget": {"level": "warning", "usagePct": 0.72},
            },
        ),
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"prompt": "Peux-tu faire mieux sur les hooks, les tests et les tokens ?", "timestamp": "2026-04-13T23:40:00Z"})),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    args = argparse.Namespace(
        project_root=tmp_path,
        latest_file=latest_file,
        events_file=events_file,
        max_context_length=900,
    )

    result = guardrail_policy.command_prompt_signals(args)

    assert result == 0
    session_state = json.loads((hook_runtime_dir / "session-state.json").read_text(encoding="utf-8"))
    obligation_ids = {item["id"] for item in session_state["openObligations"]}
    assert "clarification-batch" in obligation_ids
    assert "compact-context" in obligation_ids
    assert session_state["openObligationsCount"] == 2
    assert any(
        "Lequel veux-tu traiter d'abord" in item["summary"]
        for item in session_state["openObligations"]
        if item["id"] == "clarification-batch"
    )


def test_command_prompt_signals_initializes_pending_external_reference_obligation(tmp_path: Path, monkeypatch) -> None:
    hook_runtime_dir = tmp_path / "hook-runtime"
    latest_file = hook_runtime_dir / "user-prompt-latest.json"
    events_file = hook_runtime_dir / "user-prompt-events.jsonl"

    monkeypatch.setattr(
        guardrail_policy,
        "enrich_prompt_signals",
        lambda project_root, prompt, signals: (
            (),
            {
                "externalReferences": {
                    "recommended": True,
                    "source": "context7-first",
                    "fallback": "web",
                    "libraries": ["next.js"],
                    "reason": "question de bibliotheque/API/configuration susceptible de dependre d'une doc a jour",
                }
            },
        ),
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"prompt": "Sur Next.js, quelle config faut-il pour les route handlers ?", "timestamp": "2026-04-13T23:46:00Z"})),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    args = argparse.Namespace(
        project_root=tmp_path,
        latest_file=latest_file,
        events_file=events_file,
        max_context_length=900,
    )

    result = guardrail_policy.command_prompt_signals(args)

    assert result == 0
    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert payload["externalReferenceState"]["status"] == "pending"
    session_state = json.loads((hook_runtime_dir / "session-state.json").read_text(encoding="utf-8"))
    obligation_ids = {item["id"] for item in session_state["openObligations"]}
    assert "external-reference-proof" in obligation_ids


def test_command_post_edit_persists_context7_proof_into_prompt_and_session_state(tmp_path: Path, monkeypatch) -> None:
    hook_runtime_dir = tmp_path / "hook-runtime"
    prompt_state_file = hook_runtime_dir / "user-prompt-latest.json"
    prompt_state_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_state_file.write_text(
        json.dumps(
            {
                "promptPreview": "Configurer Next.js",
                "externalReferences": {
                    "recommended": True,
                    "source": "context7-first",
                    "fallback": "web",
                    "libraries": ["next.js"],
                },
                "externalReferenceState": {
                    "status": "pending",
                    "satisfied": False,
                    "libraries": ["next.js"],
                    "proofs": [],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "tool_name": "mcp_context7_query-docs",
        "tool_input": {"libraryId": "/vercel/next.js", "query": "route handlers"},
        "tool_response": "Use route handlers in the app router to handle incoming requests.",
    })))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    args = argparse.Namespace(
        project_root=tmp_path,
        python_executable=sys.executable,
        prompt_state_file=prompt_state_file,
    )

    result = guardrail_policy.command_post_edit(args)

    assert result == 0
    payload = json.loads(prompt_state_file.read_text(encoding="utf-8"))
    assert payload["externalReferenceState"]["status"] == "context7-proved"
    assert payload["externalReferenceState"]["satisfied"] is True
    assert payload["externalReferenceState"]["libraryId"] == "/vercel/next.js"
    session_state = json.loads((hook_runtime_dir / "session-state.json").read_text(encoding="utf-8"))
    obligation_ids = {item["id"] for item in session_state["openObligations"]}
    evidence_types = {item["type"] for item in session_state["evidence"]}
    assert "external-reference-proof" not in obligation_ids
    assert "external-reference" in evidence_types
    hook_output = json.loads(stdout.getvalue())
    assert "Preuve Context7 capturee" in hook_output["hookSpecificOutput"]["additionalContext"]


def test_command_prompt_signals_resolves_previous_clarification_and_clears_obligation(tmp_path: Path, monkeypatch) -> None:
    hook_runtime_dir = tmp_path / "hook-runtime"
    latest_file = hook_runtime_dir / "user-prompt-latest.json"
    events_file = hook_runtime_dir / "user-prompt-events.jsonl"
    session_state_file = hook_runtime_dir / "session-state.json"
    session_state_file.parent.mkdir(parents=True, exist_ok=True)
    session_state_file.write_text(
        json.dumps(
            {
                "clarificationState": {
                    "status": "open",
                    "question": "Je peux prioriser le control plane hooks, la boucle tests/breaker, ou memoire/tokens. Lequel veux-tu traiter d'abord ?",
                    "options": [
                        "Prioriser le control plane hooks",
                        "Prioriser la boucle tests/review/breaker",
                        "Prioriser memoire, contexte et tokens",
                    ],
                    "relanceCount": 0,
                },
                "openObligations": [
                    {
                        "id": "clarification-batch",
                        "level": "high",
                        "status": "open",
                        "source": "prompt",
                        "summary": "Je peux prioriser le control plane hooks, la boucle tests/breaker, ou memoire/tokens. Lequel veux-tu traiter d'abord ?",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        guardrail_policy,
        "enrich_prompt_signals",
        lambda project_root, prompt, signals: ((), {"clarification": {}}),
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"prompt": "On priorise les hooks.", "timestamp": "2026-04-13T23:45:00Z"})),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    args = argparse.Namespace(
        project_root=tmp_path,
        latest_file=latest_file,
        events_file=events_file,
        max_context_length=900,
    )

    result = guardrail_policy.command_prompt_signals(args)

    assert result == 0
    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert payload["clarificationState"]["status"] == "resolved"
    session_state = json.loads(session_state_file.read_text(encoding="utf-8"))
    obligation_ids = {item["id"] for item in session_state["openObligations"]}
    assert "clarification-batch" not in obligation_ids


def test_evaluate_subagent_stop_updates_session_state_with_follow_up_obligations(tmp_path: Path, monkeypatch) -> None:
    hook_runtime_dir = tmp_path / "hook-runtime"
    latest_file = hook_runtime_dir / "subagent-stop" / "latest.json"
    events_file = hook_runtime_dir / "subagent-stop" / "events.jsonl"
    counter_file = hook_runtime_dir / "subagent-stop" / "counters.json"
    prompt_state = {
        "promptPreview": "Run pytest hooks suite",
        "memoryHints": {"taskType": "testing"},
    }

    class FakeCriteria:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeEvaluation:
        score = 0.86
        grade = "B"
        passed = True

        def to_dict(self) -> dict[str, object]:
            return {"score": self.score, "grade": self.grade, "passed": self.passed}

    class FakeEvaluator:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        def evaluate(self, agent: str, output: str, task: str, criteria: FakeCriteria) -> FakeEvaluation:
            return FakeEvaluation()

    class FakeTrustScore:
        def to_dict(self) -> dict[str, object]:
            return {"level": "trusted", "score": 0.91}

    class FakeTrustScorer:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        def score(self, agent: str) -> FakeTrustScore:
            return FakeTrustScore()

    monkeypatch.setattr(
        guardrail_policy,
        "load_core_symbol",
        lambda project_root, module_name, symbol_name: {
            ("grimoire.core.evaluator", "Evaluator"): FakeEvaluator,
            ("grimoire.core.evaluator", "EvalCriteria"): FakeCriteria,
            ("grimoire.core.trust_scorer", "TrustScorer"): FakeTrustScorer,
        }.get((module_name, symbol_name)),
    )

    raw = json.dumps(
        {
            "hookEventName": "SubagentStop",
            "agent_type": "qa",
            "task": "Run pytest hooks suite",
            "output": "Les tests hooks passent, mais il faut lancer les breakers post-tests.",
        }
    )

    output = guardrail_policy.evaluate_subagent_stop(
        raw,
        prompt_state,
        tmp_path,
        latest_file,
        events_file,
        counter_file,
    )

    session_state = json.loads((hook_runtime_dir / "session-state.json").read_text(encoding="utf-8"))
    obligation_ids = {item["id"] for item in session_state["openObligations"]}
    evidence_types = {item["type"] for item in session_state["evidence"]}
    assert "breaker-post-tests" in obligation_ids
    assert "subagent-review" in evidence_types
    assert "Breaker post-tests recommande" in output["hookSpecificOutput"]["additionalContext"]


def test_command_stop_closure_surfaces_open_session_obligations(tmp_path: Path, monkeypatch) -> None:
    hook_runtime_dir = tmp_path / "hook-runtime"
    latest_file = hook_runtime_dir / "stop" / "latest.json"
    prompt_state_file = hook_runtime_dir / "user-prompt-latest.json"
    task_latest_file = tmp_path / "task-latest.json"
    subagent_latest_file = hook_runtime_dir / "subagent-stop" / "latest.json"

    prompt_state_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_state_file.write_text(
        json.dumps(
            {
                "promptPreview": "Finaliser la vague hooks",
                "clarification": {"recommended": True, "options": ["Hooks", "Tests"]},
                "tokenBudget": {"level": "warning", "usagePct": 0.71},
            }
        ),
        encoding="utf-8",
    )
    task_latest_file.write_text(json.dumps({"task": "grimoire: lint", "status": "success"}), encoding="utf-8")
    subagent_latest_file.parent.mkdir(parents=True, exist_ok=True)
    subagent_latest_file.write_text(
        json.dumps({"agent": "qa", "grade": "B", "followUp": {"breakerRecommended": True}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"stop_hook_active": False})))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    args = argparse.Namespace(
        project_root=tmp_path,
        prompt_state_file=prompt_state_file,
        task_latest_file=task_latest_file,
        subagent_latest_file=subagent_latest_file,
        latest_file=latest_file,
    )

    result = guardrail_policy.command_stop_closure(args)

    assert result == 0
    payload = json.loads(latest_file.read_text(encoding="utf-8"))
    assert payload["openObligationsCount"] == 3
    assert "Obligations ouvertes" in payload["additionalContext"]
    hook_output = json.loads(stdout.getvalue())
    assert "Obligations ouvertes" in hook_output["hookSpecificOutput"]["additionalContext"]


def test_evaluate_subagent_stop_creates_deferred_task_flow_ticket(tmp_path: Path, monkeypatch) -> None:
    hook_runtime_dir = tmp_path / "hook-runtime"
    latest_file = hook_runtime_dir / "subagent-stop" / "latest.json"
    events_file = hook_runtime_dir / "subagent-stop" / "events.jsonl"
    counter_file = hook_runtime_dir / "subagent-stop" / "counters.json"
    prompt_state = {
        "promptPreview": "Run pytest hooks suite",
        "memoryHints": {"taskType": "testing"},
    }

    class FakeCriteria:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeEvaluation:
        score = 0.87
        grade = "B"
        passed = True

        def to_dict(self) -> dict[str, object]:
            return {"score": self.score, "grade": self.grade, "passed": self.passed}

    class FakeEvaluator:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        def evaluate(self, agent: str, output: str, task: str, criteria: FakeCriteria) -> FakeEvaluation:
            return FakeEvaluation()

    class FakeTrustScore:
        def to_dict(self) -> dict[str, object]:
            return {"level": "trusted", "score": 0.88}

    class FakeTrustScorer:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        def score(self, agent: str) -> FakeTrustScore:
            return FakeTrustScore()

    monkeypatch.setattr(
        guardrail_policy,
        "load_core_symbol",
        lambda project_root, module_name, symbol_name: {
            ("grimoire.core.evaluator", "Evaluator"): FakeEvaluator,
            ("grimoire.core.evaluator", "EvalCriteria"): FakeCriteria,
            ("grimoire.core.trust_scorer", "TrustScorer"): FakeTrustScorer,
        }.get((module_name, symbol_name)),
    )

    raw = json.dumps(
        {
            "hookEventName": "SubagentStop",
            "agent_type": "qa",
            "task": "Run pytest hooks suite",
            "output": "Suite verte; planifier maintenant le breaker post-tests complet.",
        }
    )

    guardrail_policy.evaluate_subagent_stop(
        raw,
        prompt_state,
        tmp_path,
        latest_file,
        events_file,
        counter_file,
    )

    tickets_payload = json.loads((tmp_path / "_grimoire-runtime-output" / "task-flow" / "deferred-tickets.json").read_text(encoding="utf-8"))
    ticket = tickets_payload["tickets"][0]
    assert ticket["id"] == "breaker-post-tests"
    assert ticket["status"] == "open"
    assert ticket["flow"] == "quality"


# ---------------------------------------------------------------------------
# compute_prompt_clarity — PCG unit tests
# ---------------------------------------------------------------------------


def test_pcg_clear_precise_prompt() -> None:
    result = guardrail_policy.compute_prompt_clarity(
        "Renomme la fonction `get_user` en `fetch_account` dans src/auth/service.py sans casser les tests"
    )
    assert result["level"] == "CLEAR"
    assert result["score"] >= 8
    assert result["gaps"] == []


def test_pcg_vague_very_short_prompt() -> None:
    result = guardrail_policy.compute_prompt_clarity("améliore ça")
    assert result["level"] == "VAGUE"
    assert result["score"] <= 4
    assert "prompt_too_short" in result["gaps"]


def test_pcg_vague_verb_without_technical_target() -> None:
    result = guardrail_policy.compute_prompt_clarity("améliore le module auth s'il te plait merci beaucoup")
    assert result["level"] in ("VAGUE", "BORDERLINE")
    assert "vague_verb" in result["gaps"]


def test_pcg_vague_verb_with_technical_target_is_not_penalised() -> None:
    result = guardrail_policy.compute_prompt_clarity(
        "améliore la lisibilité de src/auth/service.py en extrayant les fonctions trop longues"
    )
    assert "vague_verb" not in result["gaps"]


def test_pcg_unresolved_reference_without_session_context() -> None:
    result = guardrail_policy.compute_prompt_clarity("fixe ça maintenant s'il te plait")
    assert "unresolved_reference" in result["gaps"] or result["level"] in ("VAGUE", "BORDERLINE")


def test_pcg_unresolved_reference_resolved_by_session_context() -> None:
    ctx = {"recent_files": ["src/auth/service.py"]}
    result = guardrail_policy.compute_prompt_clarity(
        "améliore ce fichier en décomposant les fonctions longues", session_context=ctx
    )
    assert "unresolved_reference" not in result["gaps"]


def test_pcg_borderline_score_range() -> None:
    result = guardrail_policy.compute_prompt_clarity(
        "améliore le module auth pour la lisibilité"
    )
    assert result["level"] == "BORDERLINE"
    assert 5 <= result["score"] <= 7


def test_pcg_expert_bypass_downgrades_vague_to_borderline() -> None:
    result = guardrail_policy.compute_prompt_clarity(
        "améliore ça maintenant", user_skill_level="expert"
    )
    assert result["bypassAvailable"] is True
    assert result["level"] == "BORDERLINE"


def test_pcg_non_expert_vague_not_bypassed() -> None:
    result = guardrail_policy.compute_prompt_clarity("améliore ça maintenant")
    assert result["bypassAvailable"] is False
    assert result["level"] == "VAGUE"


def test_pcg_tradeoff_without_constraint_penalised() -> None:
    result = guardrail_policy.compute_prompt_clarity(
        "refactore le module de paiement pour le rendre plus simple"
    )
    assert "no_constraint" in result["gaps"]


def test_pcg_tradeoff_with_constraint_not_penalised() -> None:
    result = guardrail_policy.compute_prompt_clarity(
        "refactore le module de paiement sans casser les tests d'intégration existants"
    )
    assert "no_constraint" not in result["gaps"]


def test_pcg_output_hint_gives_bonus() -> None:
    base = guardrail_policy.compute_prompt_clarity("refactore le module de paiement en profondeur")
    with_hint = guardrail_policy.compute_prompt_clarity(
        "refactore le module de paiement et retourne un résultat attendu sous forme de tableau"
    )
    assert with_hint["score"] >= base["score"]


def test_pcg_returns_required_keys() -> None:
    result = guardrail_policy.compute_prompt_clarity("test")
    assert "score" in result
    assert "level" in result
    assert "gaps" in result
    assert "bypassAvailable" in result
    assert result["level"] in ("CLEAR", "BORDERLINE", "VAGUE")
    assert isinstance(result["score"], int)
    assert isinstance(result["gaps"], list)


def test_pcg_command_prompt_signals_injects_clarity_when_vague(tmp_path: Path) -> None:
    latest_file = tmp_path / "hook-runtime" / "user-prompt-latest.json"
    events_file = tmp_path / "hook-runtime" / "events.jsonl"
    latest_file.parent.mkdir(parents=True, exist_ok=True)

    args = argparse.Namespace(
        latest_file=latest_file,
        events_file=events_file,
        project_root=tmp_path,
        max_context_length=900,
    )
    payload = json.dumps({"prompt": "améliore ça", "timestamp": "2026-05-08T00:00:00Z"})

    import io as _io
    import sys as _sys
    old_stdin = _sys.stdin
    _sys.stdin = _io.StringIO(payload)
    try:
        guardrail_policy.command_prompt_signals(args)
    finally:
        _sys.stdin = old_stdin

    state = json.loads(latest_file.read_text(encoding="utf-8"))
    assert "promptClarity" in state
    assert state["promptClarity"]["level"] == "VAGUE"


def test_pcg_command_prompt_signals_no_clarity_when_clear(tmp_path: Path) -> None:
    latest_file = tmp_path / "hook-runtime" / "user-prompt-latest.json"
    events_file = tmp_path / "hook-runtime" / "events.jsonl"
    latest_file.parent.mkdir(parents=True, exist_ok=True)

    args = argparse.Namespace(
        latest_file=latest_file,
        events_file=events_file,
        project_root=tmp_path,
        max_context_length=900,
    )
    payload = json.dumps({
        "prompt": "Renomme `get_user` en `fetch_account` dans src/auth/service.py sans casser les tests",
        "timestamp": "2026-05-08T00:00:00Z",
    })

    import io as _io
    import sys as _sys
    old_stdin = _sys.stdin
    _sys.stdin = _io.StringIO(payload)
    try:
        guardrail_policy.command_prompt_signals(args)
    finally:
        _sys.stdin = old_stdin

    state = json.loads(latest_file.read_text(encoding="utf-8"))
    assert "promptClarity" not in state