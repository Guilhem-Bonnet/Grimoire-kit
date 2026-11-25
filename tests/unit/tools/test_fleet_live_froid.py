"""Flotte en direct, premier appel (#1243 suite) : la normalisation des textes de journaux sans parcours caractère par caractère.

Défaut empêché : ``fleet/live`` à froid passait ~40 % de son temps à normaliser (NFKC, caractères ``Cf``, espaces
exotiques) chaque extrait d'outil, caractère par caractère, alors que presque tout est ASCII ou n'a ni ``Cf`` ni espace
exotique. Défaut à ne pas rouvrir : une sortie qui change d'un octet (la normalisation est une défense contre
l'injection par caractères invisibles).
"""

from __future__ import annotations

import json
import random
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grimoire.memory import validation
from grimoire.memory.validation import normalize_for_storage, redact_secrets


def _legacy(text: str) -> tuple[str, tuple[str, ...]]:
    """``normalize_for_storage`` d'avant : un parcours de chaque caractère, une traduction systématique."""
    applied: list[str] = []
    folded = unicodedata.normalize("NFKC", text)
    if folded != text:
        applied.append("nfkc")
    stripped = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    if stripped != folded:
        applied.append("format-characters")
    spaced = stripped.translate(validation._SPACE_LOOKALIKES)
    if spaced != stripped:
        applied.append("space-lookalikes")
    return spaced, tuple(applied)


SAMPLES = [
    "",
    "plain ascii",
    "".join(map(chr, range(128))),
    "accentué éàü œuvre — « guillemets » ┌─┐",
    "zero​width‍and­soft hyphen",
    "bidi ‮evil‬ done",
    "nbsp and thin narrow　ideographic",
    "ｆｕｌｌｗｉｄｔｈ ligature ﬁ ① ²",
    "ghp_abc​defghijklmnopqrstuvwxyz0123456789",
    "mix ​  ﬁ é \x1b[31m",
    "emoji 😀 and \U000e0041 tag",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_meme_resultat_que_l_ancien_parcours(text: str) -> None:
    assert normalize_for_storage(text) == _legacy(text)


def test_meme_resultat_sur_des_textes_tires_au_hasard() -> None:
    rng = random.Random(1243)
    pool = (
        [chr(c) for c in range(32, 127)]
        + list("éàüœ—«»┌─┐ﬁ①²ｆ😀")
        + ["​", "‍", "­", "‮", "﻿", " ", " ", " ", "　", "\n", "\t"]
    )
    for _ in range(500):
        text = "".join(rng.choice(pool) for _ in range(rng.randint(0, 40)))
        assert normalize_for_storage(text) == _legacy(text), repr(text)


def test_un_secret_coupe_par_un_caractere_invisible_est_toujours_masque() -> None:
    redacted, labels = redact_secrets("token ghp_abcdefghij​klmnopqrstuvwxyz0123456789 fin")
    assert "ghp_" not in redacted
    assert labels == ("github-token",)


# ── La vue de la flotte, avec et sans la nouvelle normalisation ──────────────

from grimoire.tools import workspace_fleet_live as fleet  # noqa: E402

REGISTRY = """\
providers:
  - id: "anthropic"
    enabled: true
    models:
      - id: "claude-sonnet-5-5"
        tier: "mid"
      - id: "claude-opus-5-5"
        tier: "strong"
"""


def _journal_dir(claude_home: Path, root: Path) -> Path:
    directory = claude_home / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root.resolve()))
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _assistant(
    session: str, message_id: str, model: str, *, at: str, tools: tuple[dict[str, Any], ...] = ()
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": at,
        "effort": "high",
        "sessionId": session,
        "entrypoint": "cli",
        "message": {
            "id": message_id,
            "model": model,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": list(tools),
        },
    }


def _tool(name: str, tool_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": payload}


def _result(session: str, tool_id: str, content: str, *, error: bool = False) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": "2026-11-12T10:00:00Z",
        "sessionId": session,
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "is_error": error, "content": content}]
        },
    }


def _write(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


NOW = datetime(2026, 11, 12, 10, 5, tzinfo=UTC)


@pytest.fixture
def claude_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from grimoire.core.agentic_standard import setup_standard_profile
    from grimoire.tools import project_registry as reg

    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "home"))
    root = tmp_path / "projet"
    root.mkdir()
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    (root / "_grimoire/standard/llm-provider-registry.yaml").write_text(REGISTRY, encoding="utf-8")
    reg.save_registry([{"slug": "projet", "name": "Projet", "path": str(root.resolve())}])
    return root


def _view(project: Path) -> dict[str, Any]:
    fleet.reset_cache()
    view = fleet.fleet_live_view(project, now=NOW)
    return json.loads(json.dumps(view, default=str, sort_keys=True))


def test_la_vue_est_identique_avec_l_ancienne_normalisation(
    project: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = "0f0f0f0f-1e1e1e1e-2d2d2d2d-3c3c3c3c-4b4b4b4b4b4b"
    other = "1a1a1a1a-1e1e1e1e-2d2d2d2d-3c3c3c3c-4b4b4b4b4b4b"
    tools = (
        _tool("Bash", "b1", {"command": "echo ​caché é"}),
        _tool("Read", "r1", {"file_path": "/tmp/ﬁchier.txt"}),
        _tool("Bash", "b2", {"command": "\x1b[31mrouge\x1b[0m"}),
    )
    lines = [_assistant(session, "m1", "claude-sonnet-5-5", tools=tools, at="2026-11-12T10:00:00Z")]
    lines.append(_result(session, "b1", "sortie accentuée — « ok » fin ghp_abcdefghijklmnopqrstuvwxyz0123456789"))
    lines.append(_result(session, "r1", "plain"))
    lines.append(_result(session, "b2", "\x1b[32mvert\x1b[0m", error=True))
    directory = _journal_dir(claude_home, project)
    _write(directory / f"{session}.jsonl", lines)
    _write(
        directory / f"{other}.jsonl",
        [_assistant(other, "m9", "claude-opus-5-5", at="2026-11-12T10:01:00Z")],
    )

    fast_cold = _view(project)
    fast_hot = fleet.fleet_live_view(project, now=NOW)
    assert json.loads(json.dumps(fast_hot, default=str, sort_keys=True)) == fast_cold
    monkeypatch.setattr(validation, "normalize_for_storage", _legacy)
    legacy = _view(project)

    assert fast_cold == legacy
    assert len(fast_cold["sessions"]) == 2
    assert any("[redacted:github-token]" in str(call) for call in str(fast_cold).split("'excerpt'"))


def test_les_motifs_de_redaction_sont_inchanges() -> None:
    assert re.search(r"\[redacted:", redact_secrets("AKIAABCDEFGHIJKLMNOP")[0])
