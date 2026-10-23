"""Issue #703 : les signaux stigmergiques sont injectés au SessionStart de l'hôte Claude Code.

`grimoire stigmergy install-hooks` n'installe que des hooks au format Copilot ;
sur Claude Code l'émetteur SessionStart est `decide_activation`, qui n'injectait
rien. Rouge-avant / vert-après sur `test_session_start_injects_active_signals`.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.hosts.decisions import decide_activation
from grimoire.hosts.decisions._shared import HookEvent, HookInput
from grimoire.tools import features as feat
from grimoire.tools import stigmergy as stig

_HEADER = "Signaux de coordination actifs (stigmergie) :"


def _project(tmp_path: Path) -> Path:
    (tmp_path / "_grimoire").mkdir()
    (tmp_path / "project-context.yaml").write_text("project:\n  name: demo\n", encoding="utf-8")
    return tmp_path


def _deposit(root: Path) -> None:
    stig.deposit_pheromone(root, "ALERT", "src/api", "migration en cours, ne pas toucher", "worker-a")


def test_session_start_injects_active_signals(tmp_path: Path) -> None:
    root = _project(tmp_path)
    feat.set_enabled(root, "stigmergy-hooks", True)
    _deposit(root)

    decision = decide_activation(HookInput(event=HookEvent.SESSION_START, project_root=root, host="claude"))

    assert _HEADER in decision.context
    assert "ALERT @ src/api" in decision.context
    assert "migration en cours" in decision.context
    assert decision.detail["stigmergy_injected"] is True


def test_feature_off_injects_nothing_even_with_a_board(tmp_path: Path) -> None:
    root = _project(tmp_path)
    feat.set_enabled(root, "stigmergy-hooks", False)
    _deposit(root)

    decision = decide_activation(HookInput(event=HookEvent.SESSION_START, project_root=root, host="claude"))

    assert _HEADER not in decision.context
    assert decision.detail["stigmergy_injected"] is False


def test_feature_on_without_signals_injects_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path)
    feat.set_enabled(root, "stigmergy-hooks", True)

    decision = decide_activation(HookInput(event=HookEvent.SESSION_START, project_root=root, host="claude"))

    assert _HEADER not in decision.context
    assert decision.detail["stigmergy_injected"] is False


def test_sense_context_matches_the_copilot_hook_wording(tmp_path: Path) -> None:
    """Même texte que `format_sense` du hook Copilot embarqué : un seul rendu, quel que soit l'hôte."""
    root = _project(tmp_path)
    for i in range(8):
        stig.deposit_pheromone(root, "PROGRESS", f"zone-{i}", f"signal {i}", "worker-b")

    text = stig.sense_context(root)

    lines = text.splitlines()
    assert lines[0] == _HEADER
    assert len(lines) == 1 + 6 + 1
    assert lines[-1] == "- (+2 autre·s)"
    assert all(line.startswith("- PROGRESS @ zone-") for line in lines[1:-1])
