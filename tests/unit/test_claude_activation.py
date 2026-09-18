"""Claude Code activation hook — install semantics and fail-safety.

The SessionStart activation mechanism was validated 40/40 by the
2026-07-09 evals campaign; these tests pin the product integration:
idempotent install, non-destructive merge into existing settings, and
byte-for-byte preservation of malformed settings files.
"""

from __future__ import annotations

import json
from pathlib import Path

from grimoire.core import standard_generation as gen
from grimoire.core.claude_activation import (
    ACTIVATION_CONTEXT_RELPATH,
    HOOK_COMMAND,
    SETTINGS_RELPATH,
    activation_context_needs_refresh,
    activation_context_text,
    activation_directive_template,
    default_activation_directive,
    install_claude_activation,
)

#: Verbatim wording shipped between #585 (lot B) and #597 (lot G1) — 824
#: characters, ``{task_id}`` placeholder literal, exactly as that era's
#: ``install_claude_activation`` wrote it to disk. Frozen here independently
#: of ``claude_activation._HISTORICAL_DIRECTIVE_TEMPLATES``: these tests must
#: keep failing if that list ever loses the entry, not silently pass because
#: both sides drifted together.
_STALE_824_CHAR_TEMPLATE = (
    "[Grimoire Standard — activation]\n"
    "Ce projet est gouverné par le standard agentique Grimoire. Ces étapes font\n"
    "partie de la tâche demandée :\n"
    "1. AVANT toute modification de code : remplis\n"
    "   `_grimoire-output/evidence/{task_id}/task-envelope.md` — objectif,\n"
    "   périmètre outillé (tool boundary) concret, critères de sortie.\n"
    "2. PENDANT le travail : consigne chaque preuve (commande exécutée, test\n"
    "   vert, diff clé) comme ligne concrète de l'inventaire dans\n"
    "   `_grimoire-output/evidence/{task_id}/evidence-pack.md`, et remplace le\n"
    "   résumé placeholder.\n"
    "3. AVANT de conclure : exécute\n"
    "   `grimoire standard gate run-tests --task-id {task_id}` puis\n"
    "   `grimoire standard gate check --task-id {task_id} --strict` puis\n"
    "   `grimoire standard verify .` et corrige tout échec.\n"
    "Une clôture sans gates verts est une tâche non terminée.\n"
)


def _settings(root: Path) -> dict[str, object]:
    return json.loads((root / SETTINGS_RELPATH).read_text(encoding="utf-8"))


def _session_start_commands(root: Path) -> list[str]:
    data = _settings(root)
    hooks = data["hooks"]
    assert isinstance(hooks, dict)
    commands: list[str] = []
    for entry in hooks["SessionStart"]:
        for hook in entry["hooks"]:
            commands.append(hook["command"])
    return commands


def test_install_on_pristine_project(tmp_path: Path) -> None:
    result = install_claude_activation(tmp_path)
    assert result.status == "installed"
    assert ACTIVATION_CONTEXT_RELPATH in result.written
    assert SETTINGS_RELPATH in result.written
    assert _session_start_commands(tmp_path) == [HOOK_COMMAND]
    directive = (tmp_path / ACTIVATION_CONTEXT_RELPATH).read_text(encoding="utf-8")
    assert "[Grimoire Standard]" in directive
    # Le fichier est un gabarit : la tâche est résolue à chaque session, pas
    # figée à l'installation — c'était le défaut de l'issue #138.
    assert "gate check --task-id {task_id} --strict" in directive
    assert "gate check --task-id bootstrap --strict" in activation_context_text(tmp_path)
    assert "gate check --task-id GAO-x-001 --strict" in activation_context_text(tmp_path, task_id="GAO-x-001")


def test_install_is_idempotent(tmp_path: Path) -> None:
    install_claude_activation(tmp_path)
    before = (tmp_path / SETTINGS_RELPATH).read_text(encoding="utf-8")
    result = install_claude_activation(tmp_path)
    assert result.status == "already-installed"
    assert result.written == []
    assert (tmp_path / SETTINGS_RELPATH).read_text(encoding="utf-8") == before
    assert _session_start_commands(tmp_path) == [HOOK_COMMAND]


def test_merge_preserves_existing_hooks(tmp_path: Path) -> None:
    settings_path = tmp_path / SETTINGS_RELPATH
    settings_path.parent.mkdir(parents=True)
    existing = {
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {
            "PostToolUse": [{"hooks": [{"type": "command", "command": "echo post"}]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": "echo hello"}]}],
        },
    }
    settings_path.write_text(json.dumps(existing), encoding="utf-8")

    result = install_claude_activation(tmp_path)
    assert result.status == "installed"
    data = _settings(tmp_path)
    assert data["permissions"] == {"allow": ["Bash(ls:*)"]}
    hooks = data["hooks"]
    assert isinstance(hooks, dict)
    assert hooks["PostToolUse"] == existing["hooks"]["PostToolUse"]
    assert _session_start_commands(tmp_path) == ["echo hello", HOOK_COMMAND]


def test_malformed_settings_left_untouched(tmp_path: Path) -> None:
    settings_path = tmp_path / SETTINGS_RELPATH
    settings_path.parent.mkdir(parents=True)
    malformed = '{"hooks": [broken'
    settings_path.write_text(malformed, encoding="utf-8")

    result = install_claude_activation(tmp_path)
    assert result.status == "skipped-invalid-settings"
    assert settings_path.read_text(encoding="utf-8") == malformed
    assert result.message


def test_unexpected_shapes_left_untouched(tmp_path: Path) -> None:
    settings_path = tmp_path / SETTINGS_RELPATH
    settings_path.parent.mkdir(parents=True)
    for payload in ('["list-root"]', '{"hooks": "oops"}', '{"hooks": {"SessionStart": "oops"}}'):
        settings_path.write_text(payload, encoding="utf-8")
        result = install_claude_activation(tmp_path)
        assert result.status == "skipped-invalid-settings"
        assert settings_path.read_text(encoding="utf-8") == payload


def test_custom_context_file_is_never_overwritten(tmp_path: Path) -> None:
    context_path = tmp_path / ACTIVATION_CONTEXT_RELPATH
    context_path.parent.mkdir(parents=True)
    context_path.write_text("directive maison\n", encoding="utf-8")

    result = install_claude_activation(tmp_path)
    assert result.status == "installed"
    assert ACTIVATION_CONTEXT_RELPATH not in result.written
    assert context_path.read_text(encoding="utf-8") == "directive maison\n"
    assert activation_context_text(tmp_path) == "directive maison\n"


def test_directive_follows_the_session_task_not_the_install_one(tmp_path: Path) -> None:
    install_claude_activation(tmp_path, task_id="sprint-7")
    directive = activation_context_text(tmp_path, task_id="sprint-8")
    assert "evidence/sprint-8/" in directive
    assert "gate check --task-id sprint-8 --strict" in directive
    assert "sprint-7" not in directive


def test_a_legacy_default_file_with_a_literal_bootstrap_follows_the_task(tmp_path: Path) -> None:
    """Les projets enrôlés avant le gabarit portent `bootstrap` en dur ; tant que
    le fichier est le défaut intact, il ne doit pas continuer à nommer la
    mauvaise tâche."""
    context_path = tmp_path / ACTIVATION_CONTEXT_RELPATH
    context_path.parent.mkdir(parents=True)
    context_path.write_text(default_activation_directive("bootstrap"), encoding="utf-8")
    directive = activation_context_text(tmp_path, task_id="GAO-reel-001")
    assert "evidence/GAO-reel-001/" in directive
    assert "bootstrap" not in directive


def test_a_tailored_file_is_returned_as_written(tmp_path: Path) -> None:
    context_path = tmp_path / ACTIVATION_CONTEXT_RELPATH
    context_path.parent.mkdir(parents=True)
    context_path.write_text("Ma directive pour {task_id}, avec bootstrap dedans.\n", encoding="utf-8")
    assert activation_context_text(tmp_path, task_id="T-1") == "Ma directive pour T-1, avec bootstrap dedans.\n"


def test_default_directive_matches_preregistered_mechanism() -> None:
    directive = default_activation_directive()
    for anchor in (
        "[Grimoire Standard]",
        "gouvernée",
        "_grimoire-output/evidence/bootstrap/",
        "grimoire standard gate check --task-id bootstrap --strict",
    ):
        assert anchor in directive


def test_directive_no_longer_mandates_run_tests_or_verify_separately() -> None:
    """Issue #582 lot G3 : `gate check --strict` absorbe déjà scaffold (lot G1) et

    l'exécution des tests (lot G1, ``ensure_fresh_test_run``) — la directive
    n'a donc plus à mandater ``gate run-tests``/``standard verify`` en plus,
    contrairement à ce que fixait ``test_gate_run_tests_precedes_gate_check_in_the_directive``
    avant ce lot. Sur le banc à trois bras (21 runs kit-gov), ce double mandat
    coûtait une médiane de 4 tours par run rien que pour `gate run-tests`,
    y compris sur les 15/21 runs Python où aucune commande de test n'est
    détectable (le gate est alors structurellement inatteignable).
    """
    directive = default_activation_directive()
    assert "gate run-tests" not in directive
    assert "standard verify" not in directive


def test_governed_directive_stays_under_the_four_hundred_character_budget() -> None:
    directive = default_activation_directive("bootstrap")
    assert len(directive) <= 400, len(directive)


# ── Ownership & refresh (issue #582, lot G4) ──────────────────────────────────
#
# `.claude/activation-context.md` is a kit-owned file: before this lot,
# `install_claude_activation` only ever checked whether it existed, so a
# project enrolled under an old kit version kept that wording forever — never
# refreshed by `grimoire up`. It is now tracked the same way as the other
# standard artifacts, through `standard_generation`'s generation manifest.


def _seed_pre_manifest_context(tmp_path: Path, content: str) -> Path:
    """A ``.claude/activation-context.md`` written by a past kit version,
    predating the generation manifest entirely — the real shape of every
    project enrolled before this fix."""
    context_path = tmp_path / ACTIVATION_CONTEXT_RELPATH
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(content, encoding="utf-8")
    return context_path


def test_refresh_updates_a_stale_known_rendering_predating_the_manifest(tmp_path: Path) -> None:
    assert len(_STALE_824_CHAR_TEMPLATE) == 824
    context_path = _seed_pre_manifest_context(tmp_path, _STALE_824_CHAR_TEMPLATE)

    result = install_claude_activation(tmp_path, refresh=True)

    assert context_path.read_text(encoding="utf-8") == activation_directive_template()
    assert ACTIVATION_CONTEXT_RELPATH in result.written
    assert result.context_needs_review is False
    # Adopted into the manifest: a second refresh is a no-op, not a rewrite.
    manifest = gen.load_generation_manifest(tmp_path)
    assert manifest[str(ACTIVATION_CONTEXT_RELPATH)] == gen.digest(context_path)


def test_a_plain_call_without_refresh_leaves_a_stale_known_rendering_in_place(tmp_path: Path) -> None:
    """Same contract as every other standard artifact: refreshing an
    untouched-but-stale file only happens when the caller asks for it
    (``grimoire up``'s ``refresh=already_initialized``) — a bare call (e.g. a
    plain ``grimoire standard init`` rerun) never rewrites on its own."""
    context_path = _seed_pre_manifest_context(tmp_path, _STALE_824_CHAR_TEMPLATE)

    result = install_claude_activation(tmp_path)

    assert context_path.read_text(encoding="utf-8") == _STALE_824_CHAR_TEMPLATE
    assert ACTIVATION_CONTEXT_RELPATH not in result.written
    assert result.context_needs_review is False
    # Still adopted into the manifest — the recognition ran regardless, only
    # the write is gated on `refresh`.
    manifest = gen.load_generation_manifest(tmp_path)
    assert str(ACTIVATION_CONTEXT_RELPATH) in manifest


def test_refresh_leaves_a_genuinely_edited_context_alone_and_flags_it(tmp_path: Path) -> None:
    custom = "Notre directive maison, jamais générée par le kit.\n"
    context_path = _seed_pre_manifest_context(tmp_path, custom)

    result = install_claude_activation(tmp_path, refresh=True)

    assert context_path.read_text(encoding="utf-8") == custom
    assert ACTIVATION_CONTEXT_RELPATH not in result.written
    assert result.context_needs_review is True


def test_force_overwrites_a_genuinely_edited_context(tmp_path: Path) -> None:
    custom = "Notre directive maison, jamais générée par le kit.\n"
    context_path = _seed_pre_manifest_context(tmp_path, custom)

    result = install_claude_activation(tmp_path, force=True)

    assert context_path.read_text(encoding="utf-8") == activation_directive_template()
    assert ACTIVATION_CONTEXT_RELPATH in result.written
    assert result.context_needs_review is False


def test_a_current_rendering_is_adopted_without_being_rewritten(tmp_path: Path) -> None:
    """Predates the manifest but already matches today's wording: nothing to
    write, only the digest needs recording so it becomes refreshable later."""
    context_path = _seed_pre_manifest_context(tmp_path, activation_directive_template())

    result = install_claude_activation(tmp_path, refresh=True)

    assert context_path.read_text(encoding="utf-8") == activation_directive_template()
    assert ACTIVATION_CONTEXT_RELPATH not in result.written
    manifest = gen.load_generation_manifest(tmp_path)
    assert str(ACTIVATION_CONTEXT_RELPATH) in manifest


def test_activation_context_needs_refresh_predicate_is_read_only(tmp_path: Path) -> None:
    context_path = _seed_pre_manifest_context(tmp_path, _STALE_824_CHAR_TEMPLATE)

    assert activation_context_needs_refresh(tmp_path) is True
    # Purely a read: nothing written, nothing adopted into the manifest.
    assert context_path.read_text(encoding="utf-8") == _STALE_824_CHAR_TEMPLATE
    assert gen.load_generation_manifest(tmp_path) == {}

    assert activation_context_needs_refresh(tmp_path / "no-such-project") is False


def test_activation_context_needs_refresh_is_false_for_the_current_rendering(tmp_path: Path) -> None:
    install_claude_activation(tmp_path)
    assert activation_context_needs_refresh(tmp_path) is False


def test_activation_context_needs_refresh_is_false_for_a_genuine_edit(tmp_path: Path) -> None:
    _seed_pre_manifest_context(tmp_path, "Notre directive maison.\n")
    assert activation_context_needs_refresh(tmp_path) is False
