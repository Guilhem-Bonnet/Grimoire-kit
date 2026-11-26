"""Flotte en direct, premier appel (#1243) : ce que coûte la lecture, et ce qu'elle ne doit jamais changer.

Défauts empêchés : ``fleet/live`` à froid caviardait chaque résumé et chaque extrait d'outil de tout le journal
(8 926 appels sur un poste réel) alors que seuls les 20 derniers appels de chaque session sont rendus ; le
registre YAML était relu à chaque appel ; un journal réécrit sur place (même inode, plus grand) n'était pas
reconnu. Défauts à ne pas ouvrir : une charge rendue qui change d'un octet, un texte rendu sans caviardage.

Critère de coût : des comptages (lignes repliées, appels au caviardage, analyses du registre), jamais une durée.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grimoire.tools import workspace_fleet_live as fleet

NOW = datetime(2026, 11, 12, 10, 5, tzinfo=UTC)
STAMP = "2026-11-12T10:00:00Z"
SESSION = "0f0f0f0f-1e1e1e1e-2d2d2d2d-3c3c3c3c-4b4b4b4b4b4b"
#: Fausses valeurs, construites par concaténation (aucune ne doit sortir en clair d'une vue).
LEAK_A = "gh" + "p_abcdefghijklmnopqrstuvwxyz0123456789"
LEAK_B = "gh" + "p_zyxwvutsrqponmlkjihgfedcba9876543210"
LEAK_C = "gh" + "p_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8"
REGISTRY = """\
providers:
  - id: "anthropic"
    enabled: true
    models:
      - id: "claude-sonnet-5-5"
        tier: "mid"
"""


@pytest.fixture(autouse=True)
def _cold() -> None:
    fleet.reset_cache()


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


@pytest.fixture
def journal(project: Path, claude_home: Path) -> Path:
    directory = claude_home / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(project.resolve()))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{SESSION}.jsonl"


def _call(index: int, tools: tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": STAMP,
        "sessionId": SESSION,
        "entrypoint": "cli",
        "message": {
            "id": f"m{index}",
            "model": "claude-sonnet-5-5",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": list(tools),
        },
    }


def _use(name: str, tool_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": payload}


def _result(tool_id: str, content: str, *, error: bool = False) -> dict[str, Any]:
    block = {"type": "tool_result", "tool_use_id": tool_id, "is_error": error, "content": content}
    return {"type": "user", "timestamp": STAMP, "sessionId": SESSION, "message": {"content": [block]}}


def _bash(index: int, command: str, output: str, *, error: bool = False) -> list[dict[str, Any]]:
    tool_id = f"t{index}"
    return [_call(index, (_use("Bash", tool_id, {"command": command}),)), _result(tool_id, output, error=error)]


def _lines(*groups: list[dict[str, Any]]) -> bytes:
    return b"".join(json.dumps(line).encode() + b"\n" for group in groups for line in group)


def _view(project: Path) -> str:
    return json.dumps(fleet.fleet_live_view(project, now=NOW), sort_keys=True, default=str)


def _tail(view: str) -> list[dict[str, Any]]:
    sessions = json.loads(view)["sessions"]
    return [tool for session in sessions for tool in session["tail"]]


def _many(count: int, *, start: int = 0) -> list[list[dict[str, Any]]]:
    return [_bash(i, f"echo {i} {LEAK_A}", f"sortie {i} {LEAK_B}") for i in range(start, start + count)]


@pytest.fixture
def redactions(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    seen: list[int] = []
    original = fleet.redact_secrets

    def counting(text: str) -> Any:
        seen.append(len(text))
        return original(text)

    monkeypatch.setattr(fleet, "redact_secrets", counting)
    return seen


# ── Le caviardage ne porte que sur ce qui est rendu ─────────────────────────


def test_le_caviardage_ne_porte_que_sur_les_vingt_derniers_appels(
    project: Path, journal: Path, redactions: list[int]
) -> None:
    journal.write_bytes(_lines(*_many(60)))

    cold = _view(project)
    assert len(redactions) == 2 * fleet.TAIL_CALLS, "un résumé et un extrait par appel rendu, rien d'autre"
    assert fleet.stats()["lines"] == 120

    hot = len(redactions), fleet.stats()["lines"]
    assert _view(project) == cold
    assert (len(redactions), fleet.stats()["lines"]) == hot, "à chaud : ni ligne relue, ni caviardage"

    with journal.open("ab") as handle:
        handle.write(_lines(_many(1, start=60)[0]))
    _view(project)
    assert fleet.stats()["lines"] == 122, "après ajout : seules les deux lignes ajoutées sont repliées"
    assert len(redactions) - hot[0] == 2, "et un seul nouvel appel est caviardé"


def test_la_charge_rendue_est_identique_a_un_caviardage_immediat(
    project: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    groups = _many(30)
    groups.append([_call(99, (_use("AskUserQuestion", "q1", {"question": f"Quoi {LEAK_C} ?"}),))])
    groups.append(_bash(100, "false", f"échec {LEAK_A}", error=True))
    journal.write_bytes(_lines(*groups))

    lazy = _view(project)
    fleet.reset_cache()
    monkeypatch.setattr(fleet, "_LAZY_MAX_CHARS", -1)  # tout est caviardé dès le repli : l'ancien comportement
    eager = _view(project)

    assert lazy == eager
    assert "[redacted:" in lazy


def test_un_texte_demesure_est_caviarde_tout_de_suite_et_reste_identique(
    project: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_text = "x" * 500 + LEAK_A + "y" * 500
    journal.write_bytes(_lines(_bash(1, long_text, long_text)))
    monkeypatch.setattr(fleet, "_LAZY_MAX_CHARS", 100)
    big = _view(project)
    fleet.reset_cache()
    monkeypatch.setattr(fleet, "_LAZY_MAX_CHARS", 1 << 20)
    assert _view(project) == big
    assert LEAK_A not in big


def test_aucun_texte_sensible_n_est_rendu_en_clair(project: Path, journal: Path) -> None:
    """Fil, marqueur d'erreur, marqueur de question ; outil sorti du fil puis revenu."""
    rendered: list[str] = []
    first = _bash(0, f"echo {LEAK_A}", f"sortie {LEAK_B}")
    journal.write_bytes(_lines(first))
    rendered.append(_view(project))  # l'outil est dans le fil, déjà rendu (donc réglé)

    open_tool = [_call(1, (_use("Bash", "late", {"command": f"echo {LEAK_C}"}),))]
    journal.write_bytes(_lines(first, open_tool, *_many(25, start=2)))
    rendered.append(_view(project))  # « late » est sorti du fil, encore ouvert
    with journal.open("ab") as handle:
        handle.write(_lines([_result("late", f"erreur {LEAK_A}", error=True)]))
        handle.write(_lines([_call(50, (_use("AskUserQuestion", "ask", {"question": f"Lequel {LEAK_C} ?"}),))]))
    rendered.append(_view(project))  # le marqueur d'erreur d'un outil hors fil, un marqueur de question

    fleet.reset_cache()  # l'état est oublié, les derniers appels sont relus : ils reviennent dans le fil
    rendered.append(_view(project))

    for text in rendered:
        for leak in (LEAK_A, LEAK_B, LEAK_C):
            assert leak not in text
    # Le fil porte un vrai résumé et un vrai extrait, caviardés : sans ``settle()`` au rendu ils seraient vides.
    for index in (0, 3):
        entry = next(
            tool for tool in _tail(rendered[index]) if tool["tool"] == "Bash" and "echo" in str(tool["summary"])
        )
        assert "[redacted:github-token]" in entry["summary"]
    assert any("[redacted:github-token]" in str(tool["excerpt"]) for tool in _tail(rendered[0]))
    assert '"kind": "error"' in rendered[2]
    assert '"kind": "question"' in rendered[2]


def test_un_appel_sorti_du_fil_ne_garde_pas_son_texte_brut(project: Path, journal: Path) -> None:
    journal.write_bytes(_lines(*_many(30)))
    state = fleet._state_for(journal)
    assert next(tool.id for tool in state.tools) == "t10"
    assert all(tool.raw_summary is not None for tool in state.tools), "dans le fil : brut gardé jusqu'au rendu"
    _view(project)
    assert all(tool.raw_summary is None and tool.raw_excerpt is None for tool in state.tools)
    evicted = fleet._Tool(id="x", at="", tool="Bash", raw_summary=fleet._Raw(f"echo {LEAK_A}"))
    evicted.leave_tail()
    assert evicted.raw_summary is None and not evicted.in_tail


# ── La reprise ne se trompe pas de fichier ──────────────────────────────────


def _usage_input(state: fleet._FileState) -> int:
    return sum(group.input_tokens for group in state.usage_by_model.values())


def test_un_journal_reecrit_sur_place_plus_grand_est_relu_en_entier(journal: Path) -> None:
    journal.write_bytes(_lines([_call(i) for i in range(3)]))
    state = fleet._state_for(journal)
    assert _usage_input(state) == 30
    inode = journal.stat().st_ino

    with journal.open("r+b") as handle:  # même inode, contenu différent, plus long
        handle.write(_lines([_call(100 + i) for i in range(7)]))
    assert journal.stat().st_ino == inode
    before = fleet.stats()["lines"]

    state = fleet._state_for(journal)
    assert _usage_input(state) == 70, "ni les anciens appels ni un mélange : le nouveau contenu seul"
    assert fleet.stats()["lines"] - before == 7, "relu depuis zéro"


def test_un_journal_tronque_est_relu_en_entier(journal: Path) -> None:
    journal.write_bytes(_lines([_call(i) for i in range(5)]))
    fleet._state_for(journal)
    journal.write_bytes(_lines([_call(200)]))  # plus court, même inode

    state = fleet._state_for(journal)
    assert _usage_input(state) == 10
    assert state.turn_count == 1


def test_un_journal_remplace_par_un_autre_fichier_est_relu_en_entier(journal: Path) -> None:
    journal.write_bytes(_lines([_call(i) for i in range(2)]))
    fleet._state_for(journal)
    other = journal.with_name("neuf.tmp")
    other.write_bytes(_lines([_call(300 + i) for i in range(6)]))
    other.replace(journal)

    state = fleet._state_for(journal)
    assert _usage_input(state) == 60


def test_une_ligne_finale_non_terminee_n_est_pas_consommee(journal: Path) -> None:
    complete = _lines([_call(1)])
    partial = json.dumps(_call(2)).encode()
    journal.write_bytes(complete + partial[:20])
    state = fleet._state_for(journal)
    assert state.offset == len(complete)
    assert state.guard == complete[-fleet._GUARD_BYTES :]
    assert state.turn_count == 1

    with journal.open("ab") as handle:
        handle.write(partial[20:] + b"\n")
    state = fleet._state_for(journal)
    assert state.turn_count == 2
    assert fleet.stats()["lines"] == 2, "chaque ligne repliée une seule fois"


def test_la_garde_suit_les_lignes_sautees(journal: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fleet, "READ_CHUNK_BYTES", 64)
    monkeypatch.setattr(fleet, "_MAX_LINE_BYTES", 16)
    junk = b"z" * 100
    journal.write_bytes(junk)
    state = fleet._state_for(journal)
    assert state.offset == 100
    assert state.guard == junk[-fleet._GUARD_BYTES :]

    monkeypatch.setattr(fleet, "_MAX_LINE_BYTES", 4 << 20)
    with journal.open("ab") as handle:
        handle.write(b"\n" + _lines([_call(1)]))
    state = fleet._state_for(journal)
    assert state.turn_count == 1, "la reprise après des octets sautés reconnaît le même fichier"
    assert fleet.stats()["lines"] == 1


# ── Le registre n'est analysé que s'il change ───────────────────────────────


def test_le_registre_n_est_analyse_qu_une_fois_tant_qu_il_ne_change_pas(
    project: Path, journal: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal.write_bytes(_lines([_call(1)]))
    parsed: list[Path] = []
    original = fleet._registry_for

    def counting(root: Path) -> fleet._Registry:
        parsed.append(root)
        return original(root)

    monkeypatch.setattr(fleet, "_registry_for", counting)
    first = _view(project)
    for _ in range(3):
        assert _view(project) == first
    assert len(parsed) == 1

    path = project / "_grimoire/standard/llm-provider-registry.yaml"
    path.write_text(REGISTRY.replace('tier: "mid"', 'tier: "strong"'), encoding="utf-8")
    changed = _view(project)
    assert len(parsed) == 2
    assert '"tier": "strong"' in changed
    assert '"tier": "mid"' in first


def test_sans_registre_rien_n_est_garde(tmp_path: Path) -> None:
    registry, pricing = fleet._registry_and_pricing(tmp_path)
    assert registry.lookup("claude-x") == ("anthropic", None)
    assert pricing == {}
    assert not fleet._REGISTRIES


def test_les_registres_gardes_sont_bornes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fleet, "_MAX_REGISTRIES", 1)
    for name in ("a", "b"):
        root = tmp_path / name
        (root / "_grimoire/standard").mkdir(parents=True)
        (root / "_grimoire/standard/llm-provider-registry.yaml").write_text(REGISTRY, encoding="utf-8")
        fleet._registry_and_pricing(root)
    assert list(fleet._REGISTRIES) == [str(tmp_path / "b")]


# ── Défense en profondeur : le brut ne se montre pas avant son rendu ────────


def test_le_texte_brut_n_apparait_ni_dans_repr_ni_dans_asdict(journal: Path) -> None:
    import dataclasses

    journal.write_bytes(_lines(_bash(1, f"echo {LEAK_A}", f"sortie {LEAK_B}")))
    state = fleet._state_for(journal)
    tool = state.tools[0]
    assert tool.raw_summary is not None and tool.raw_excerpt is not None, "le brut attend bien son rendu"
    for shown in (repr(tool), str(dataclasses.asdict(tool)), repr(state), str(dataclasses.asdict(state))):
        assert LEAK_A not in shown
        assert LEAK_B not in shown


# ── Caviarder PUIS tronquer, jamais l'inverse ───────────────────────────────


@pytest.mark.parametrize("lazy", [True, False])
def test_un_secret_a_cheval_sur_la_troncature_ne_laisse_aucun_fragment(
    project: Path, journal: Path, monkeypatch: pytest.MonkeyPatch, lazy: bool
) -> None:
    if not lazy:
        monkeypatch.setattr(fleet, "_LAZY_MAX_CHARS", -1)
    marker = "[redacted:github-token]"
    # Le secret commence 30 caractères avant le seuil : tronquer d'abord en laisserait un début lisible ;
    # caviarder d'abord donne un texte plus court que le seuil, qui se termine par la marque entière.
    command = "c" * (fleet.SUMMARY_CHARS - 31) + " " + LEAK_A
    output = "o" * (fleet.EXCERPT_CHARS - 31) + " " + LEAK_B
    journal.write_bytes(_lines(_bash(1, command, output)))

    (entry,) = _tail(_view(project))

    assert entry["summary"].endswith(marker) and entry["excerpt"].endswith(marker)
    for shown in (entry["summary"], entry["excerpt"]):
        assert "gh" + "p_" not in shown


def test_la_description_d_un_sous_agent_est_caviardee_avant_la_troncature(project: Path, journal: Path) -> None:
    journal.write_bytes(_lines([_call(1)]))
    subdir = journal.parent / SESSION / "subagents"
    subdir.mkdir(parents=True)
    (subdir / "agent-abc.jsonl").write_bytes(_lines([_call(2)]))
    description = "d" * (80 - 31) + " " + LEAK_C + " fin de description"
    (subdir / "agent-abc.meta.json").write_text(json.dumps({"agentType": "Explore", "description": description}))

    view = json.loads(_view(project))

    (sub,) = [session for session in view["sessions"] if session["role"] == "subagent"]
    shown = sub["agent"]["description"]
    assert "gh" + "p_" not in shown
    assert "[redacted:github-token]" in shown
    assert LEAK_C not in json.dumps(view)
