"""Table palier x hôte x fournisseur → modèle (épique #1281, lot L5).

Garanties : les défauts du kit (Claude Code x Anthropic) donnent haiku/sonnet/opus ; le projet
surcharge par ``dispatch.models`` ; ``dispatch.copilot_models`` et ``dispatch.cursor_models``
restent lus comme alias ; aucune correspondance n'est inventée (``None``) ; une capacité connue
à « non » écarte un modèle, une capacité inconnue jamais ; la sortie dit d'où vient le choix.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.app import app
from grimoire.hosts.decisions.dispatch_model import copilot_model_for, cursor_model_for
from grimoire.hosts.emitters.claude_code import _dispatch_policy_section, claude_models
from grimoire.hosts.model_table import (
    ANY_PROVIDER,
    CLAUDE_CODE,
    SOURCE_KIT,
    SOURCE_LEGACY,
    SOURCE_PROJECT,
    ModelTable,
    kit_model,
    load_model_table,
    normalize_tier,
    resolve_model,
    resolved_rows,
    table_from_dispatch,
)
from grimoire.hosts.runtime import run_hook
from grimoire.missions.launcher import tier_model
from grimoire.providers.capabilities import parse_capabilities, resolve_capabilities

POLICY = Path("_grimoire") / "standard" / "orchestration-policy.yaml"
REGISTRY = Path("_grimoire") / "standard" / "llm-provider-registry.yaml"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    home = str(tmp_path_factory.mktemp("home"))
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)
    for var in list(os.environ):
        if var.startswith("CLAUDE") or var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_HOST_PROBE", "GRIMOIRE_SESSION_ID"):
            monkeypatch.delenv(var, raising=False)


def _policy(root: Path, text: str) -> Path:
    path = root / POLICY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return root


# ── Résolution pure ─────────────────────────────────────────────────────────


def test_kit_defaults_for_claude_code_and_anthropic() -> None:
    assert [resolve_model(t).model for t in ("cheap", "mid", "strong")] == ["haiku", "sonnet", "opus"]
    choice = resolve_model("cheap")
    assert (choice.host, choice.provider, choice.source) == (CLAUDE_CODE, "anthropic", SOURCE_KIT)
    assert kit_model("strong") == "opus"
    assert kit_model("session") == ""


def test_legacy_aliases_and_unknown_tiers_normalize() -> None:
    assert normalize_tier("Haiku") == "cheap"
    assert normalize_tier("opus") == "strong"
    assert normalize_tier("mid") == "mid"
    assert normalize_tier("session") == ""
    assert resolve_model("sonnet").model == "sonnet"


def test_no_correspondence_is_none_never_an_invented_name() -> None:
    assert resolve_model("cheap", "copilot").model is None
    assert resolve_model("cheap", "cursor").source == ""
    # Claude Code via un autre fournisseur : le kit ne connaît pas ses noms.
    assert resolve_model("cheap", "claude", "deepseek").model is None
    assert resolve_model("gigantic").model is None
    assert resolve_model("cheap", "inconnu").model is None


def test_project_beats_legacy_beats_kit_and_says_so() -> None:
    table = table_from_dispatch(
        {
            "models": {
                "claude": {"anthropic": {"cheap": "claude-haiku-4-5"}, "strong": "claude-opus-4-7"},
                "copilot": {"*": {"mid": "gpt-5.4"}},
            },
            "copilot_models": {"haiku": "claude-haiku-4.5", "sonnet": "legacy-sonnet"},
        }
    )
    assert table.errors == ()
    cheap = resolve_model("cheap", table=table)
    assert (cheap.model, cheap.source) == ("claude-haiku-4-5", SOURCE_PROJECT)
    # Un palier posé directement sous l'hôte vaut pour tout fournisseur.
    assert resolve_model("strong", table=table).model == "claude-opus-4-7"
    assert resolve_model("strong", "claude", "deepseek", table=table).model == "claude-opus-4-7"
    assert (resolve_model("mid", table=table).model, resolve_model("mid", table=table).source) == ("sonnet", SOURCE_KIT)
    copilot_cheap = resolve_model("cheap", HostId.GITHUB_COPILOT.value, table=table)
    assert (copilot_cheap.model, copilot_cheap.source, copilot_cheap.provider) == (
        "claude-haiku-4.5",
        SOURCE_LEGACY,
        ANY_PROVIDER,
    )
    assert resolve_model("mid", "copilot", table=table).model == "gpt-5.4"
    assert resolve_model("strong", "copilot", table=table).model is None


def test_provider_aliases_are_canonical() -> None:
    table = table_from_dispatch(
        {"models": {"opencode": {"kimi": {"mid": "kimi-k2"}}, "claude-code": {"claude": {"cheap": "h"}}}}
    )
    assert resolve_model("mid", "opencode", "moonshot", table=table).model == "kimi-k2"
    assert resolve_model("cheap", "claude", "anthropic", table=table).model == "h"


def test_a_known_missing_capability_skips_a_model_an_unknown_one_never() -> None:
    caps = resolve_capabilities(
        "deepseek",
        models=(("deepseek-reasoner", parse_capabilities({"tools": False})), ("deepseek-chat", ())),
    )
    table = table_from_dispatch(
        {"models": {"claude": {"deepseek": {"cheap": "deepseek-reasoner"}, "*": {"cheap": "deepseek-chat"}}}}
    )
    choice = resolve_model("cheap", "claude", "deepseek", table=table, capabilities=caps, requires=("tools",))
    assert choice.model == "deepseek-chat"
    assert choice.skipped == (("deepseek-reasoner", "tools: non (declared)"),)
    # Sans exigence, rien n'est écarté ; une capacité inconnue (vision) n'écarte rien non plus.
    assert resolve_model("cheap", "claude", "deepseek", table=table, capabilities=caps).model == "deepseek-reasoner"
    unknown = resolve_model("cheap", "claude", "deepseek", table=table, capabilities=caps, requires=("vision", "bogus"))
    assert unknown.model == "deepseek-reasoner"
    # Tous écartés : None, avec la trace de chaque refus.
    only = table_from_dispatch({"models": {"claude": {"deepseek": {"cheap": "deepseek-reasoner"}}}})
    refused = resolve_model("cheap", "claude", "deepseek", table=only, capabilities=caps, requires=("tools",))
    assert refused.model is None
    assert refused.to_dict()["skipped"] == [{"model": "deepseek-reasoner", "reason": "tools: non (declared)"}]


def test_to_dict_and_rows() -> None:
    assert resolve_model("mid").to_dict() == {
        "tier": "mid",
        "host": CLAUDE_CODE,
        "provider": "anthropic",
        "model": "sonnet",
        "source": "kit",
        "skipped": [],
    }
    rows = resolved_rows(table_from_dispatch({"cursor_models": {"opus": "claude-4.5-opus"}}))
    assert [(r.host, r.tier, r.model) for r in rows][:3] == [
        (CLAUDE_CODE, "cheap", "haiku"),
        (CLAUDE_CODE, "mid", "sonnet"),
        (CLAUDE_CODE, "strong", "opus"),
    ]
    cursor = [r for r in rows if r.host == HostId.CURSOR.value]
    assert [(r.tier, r.model, r.source) for r in cursor] == [
        ("cheap", None, ""),
        ("mid", None, ""),
        ("strong", "claude-4.5-opus", SOURCE_LEGACY),
    ]


# ── Lecture du fichier ──────────────────────────────────────────────────────


def test_missing_or_unreadable_policy_is_an_empty_table(tmp_path: Path) -> None:
    assert load_model_table(tmp_path) == ModelTable()
    _policy(tmp_path, "dispatch: [unterminated\n")
    table = load_model_table(tmp_path)
    assert table.entries == ()
    assert "illisible" in table.errors[0]
    _policy(tmp_path, "- just\n- a list\n")
    assert load_model_table(tmp_path) == ModelTable()
    assert table_from_dispatch(None) == ModelTable()


def test_bad_values_are_ignored_and_reported(tmp_path: Path) -> None:
    _policy(
        tmp_path,
        "dispatch:\n"
        "  models:\n"
        "    nowhere: {cheap: x}\n"
        "    claude: [x]\n"
        "    copilot:\n"
        "      '*': nope\n"
        "      github: {gigantic: x, mid: '  ', strong: 'bad name!', cheap: ok-model}\n"
        "  cursor_models: [x]\n",
    )
    table = load_model_table(tmp_path)
    assert [(e.host, e.provider, e.tier, e.model) for e in table.entries] == [
        (HostId.GITHUB_COPILOT.value, "github", "cheap", "ok-model")
    ]
    joined = "\n".join(table.errors)
    for fragment in (
        "dispatch.models.nowhere : hôte inconnu",
        "dispatch.models.claude : attendu",
        "dispatch.models.copilot.* : attendu",
        "gigantic : palier inconnu",
        "strong : nom de modèle illisible",
        "dispatch.cursor_models : attendu",
    ):
        assert fragment in joined
    _policy(tmp_path, "dispatch:\n  models: [x]\n")
    assert load_model_table(tmp_path).errors == ("dispatch.models : attendu un bloc hôte → fournisseur → palier",)


def test_legacy_keys_still_read_through_the_old_helpers(tmp_path: Path) -> None:
    _policy(
        tmp_path,
        "dispatch:\n  copilot_models:\n    haiku: claude-haiku-4.5\n  cursor_models:\n    sonnet: composer-2\n",
    )
    assert copilot_model_for(tmp_path, "haiku") == "claude-haiku-4.5"
    assert copilot_model_for(tmp_path, "cheap") == "claude-haiku-4.5"
    assert cursor_model_for(tmp_path, "mid") == "composer-2"
    assert cursor_model_for(tmp_path, "strong") == ""
    _policy(tmp_path, "dispatch:\n  models:\n    cursor:\n      strong: claude-opus\n")
    assert cursor_model_for(tmp_path, "opus") == "claude-opus"


# ── Routage ─────────────────────────────────────────────────────────────────


def _agent_call(root: Path, host: HostId, description: str, tool: str = "Agent") -> tuple[dict[str, Any], Any]:
    rendered, decision, _ = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(root),
            "tool_name": tool,
            "tool_input": {"description": description, "prompt": "x"},
        },
        host_id=host,
        project_root=root,
        decision_id="grimoire.dispatch-model",
    )
    return rendered, decision


def test_the_hook_follows_the_project_table_under_claude_code(tmp_path: Path) -> None:
    (tmp_path / "_grimoire").mkdir()
    _policy(tmp_path, "dispatch:\n  models:\n    claude:\n      anthropic:\n        cheap: claude-haiku-4-5\n")
    rendered, decision = _agent_call(tmp_path, HostId.CLAUDE_CODE_CLI, "Find the emitters")
    assert rendered["hookSpecificOutput"]["updatedInput"]["model"] == "claude-haiku-4-5"
    assert decision.detail["model_source"] == SOURCE_PROJECT
    assert decision.detail["tier"] == "cheap"
    rendered, decision = _agent_call(tmp_path, HostId.CLAUDE_CODE_CLI, "Revue du diff")
    assert rendered["hookSpecificOutput"]["updatedInput"]["model"] == "sonnet"
    assert decision.detail["model_source"] == SOURCE_KIT


def test_the_hook_abstains_when_the_table_has_no_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.hosts.decisions import dispatch_model as dm
    from grimoire.hosts.model_table import ModelChoice

    (tmp_path / "_grimoire").mkdir()
    monkeypatch.setattr(dm, "resolve_model", lambda tier, host, **_kw: ModelChoice(tier, host, "anthropic", None))
    rendered, decision = _agent_call(tmp_path, HostId.CLAUDE_CODE_CLI, "Find the emitters")
    assert rendered == {}
    assert decision.detail["dispatch_model"] == "no_model"


def test_the_hook_reads_the_new_copilot_key(tmp_path: Path) -> None:
    (tmp_path / "_grimoire").mkdir()
    _policy(tmp_path, "dispatch:\n  models:\n    copilot:\n      cheap: gpt-5-mini\n")
    _rendered, decision = _agent_call(tmp_path, HostId.GITHUB_COPILOT, "Find the emitters", tool="task")
    assert decision.updated_input["model"] == "gpt-5-mini"
    assert decision.detail["model_source"] == SOURCE_PROJECT
    assert decision.detail["recommended_model"] == "haiku"
    _rendered, decision = _agent_call(tmp_path, HostId.GITHUB_COPILOT, "Revue du diff", tool="task")
    assert decision.updated_input is None
    assert "dispatch.models.copilot.mid" in decision.context
    assert "dispatch.copilot_models.sonnet" in decision.context


def test_claude_models_and_the_emitted_policy_follow_the_table(tmp_path: Path) -> None:
    assert claude_models() == {"cheap": "haiku", "mid": "sonnet", "strong": "opus"}
    _policy(tmp_path, "dispatch:\n  models:\n    claude:\n      mid: claude-sonnet-4-6\n")
    models = claude_models(tmp_path)
    assert models["mid"] == "claude-sonnet-4-6"
    section = _dispatch_policy_section(models)
    assert "→ `claude-sonnet-4-6`." in section
    assert "une revue ou une analyse\nen `claude-sonnet-4-6`." in section
    # Sans correspondance pour un palier, la politique ne nomme aucun modèle à sa place.
    bare = _dispatch_policy_section({})
    assert "`haiku`" not in bare and "`sonnet`" not in bare
    assert "recherche, lecture, mesure : cheap" in bare


def test_the_worker_tier_model_reads_the_table_and_capabilities(tmp_path: Path) -> None:
    assert tier_model(tmp_path, "strong") == "opus"
    assert tier_model(tmp_path, "") == ""
    _policy(tmp_path, "dispatch:\n  models:\n    claude:\n      anthropic:\n        strong: claude-opus-4-7\n")
    assert tier_model(tmp_path, "strong") == "claude-opus-4-7"
    registry = tmp_path / REGISTRY
    registry.write_text(
        '$schema: "grimoire-llm-provider-registry/v1"\nproviders:\n'
        '  - id: "anthropic"\n    enabled: true\n    provider_type: "hosted"\n    models:\n'
        '      - id: "claude-opus-4-7"\n        tier: "strong"\n        capabilities:\n          tools: false\n',
        encoding="utf-8",
    )
    # Le modèle du projet n'appelle pas d'outils : la couche suivante (défaut du kit) prend le relais.
    assert tier_model(tmp_path, "strong") == "opus"
    registry.write_text("providers: [unterminated\n", encoding="utf-8")
    assert tier_model(tmp_path, "strong") == "claude-opus-4-7"


# ── CLI ─────────────────────────────────────────────────────────────────────


@pytest.mark.covers("cli:dispatch models", level="integration")
def test_cli_dispatch_models_text_and_json(tmp_path: Path) -> None:
    _policy(tmp_path, "dispatch:\n  models:\n    copilot:\n      cheap: gpt-5-mini\n    nowhere: {cheap: x}\n")
    runner = CliRunner()
    text = runner.invoke(app, ["dispatch", "models", "--project-root", str(tmp_path)], terminal_width=200)
    assert text.exit_code == 0, text.output
    for fragment in ("haiku", "défaut du kit", "gpt-5-mini", "projet", "model omis", "hôte inconnu"):
        assert fragment in text.output
    raw = runner.invoke(app, ["dispatch", "models", "--project-root", str(tmp_path), "--json"])
    assert raw.exit_code == 0, raw.output
    payload = json.loads(raw.output)
    assert payload["rows"][0] == resolve_model("cheap").to_dict()
    assert any(row["model"] == "gpt-5-mini" and row["source"] == "project" for row in payload["rows"])
    assert payload["errors"] == ["dispatch.models.nowhere : hôte inconnu"]
