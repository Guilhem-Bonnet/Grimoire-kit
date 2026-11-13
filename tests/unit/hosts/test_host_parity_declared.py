"""Parité des fonctions de gouvernance : chaque fonction a un état déclaré par hôte.

Une fonction livrée pour Claude Code peut rester absente d'un autre hôte sans
que rien ne le signale : ``grimoire.hosts.capabilities`` porte donc, pour chaque
fonction de gouvernance, un état ``rich``, ``degraded`` ou ``absent`` par
:class:`HostId`, avec sa raison quand elle n'est pas riche.

Ce test est le garde-fou : ajouter une fonction à :class:`GovernanceFunction`
sans déclarer ses hôtes — ou un état ``degraded``/``absent`` sans raison — fait
échouer la CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.hosts import capabilities
from grimoire.hosts.capabilities import (
    GOVERNANCE_FUNCTIONS,
    GOVERNANCE_LABELS,
    FunctionState,
    GovernanceFunction,
    ParityLevel,
    function_states,
    parity_line,
    parity_report,
    undeclared_functions,
)


def test_chaque_fonction_a_un_etat_pour_chaque_hote() -> None:
    for host in HostId:
        states = function_states(host)
        assert set(states) == set(GOVERNANCE_FUNCTIONS), host
        for function in GOVERNANCE_FUNCTIONS:
            state = states[function]
            assert isinstance(state, FunctionState), (host, function)
            if state.level is not ParityLevel.RICH:
                assert state.reason.strip(), f"{host.value}/{function.value} : {state.level.value} sans raison"


def test_la_table_couvre_exactement_les_hotes_et_les_fonctions() -> None:
    assert set(capabilities._FUNCTION_STATES) == set(HostId)
    assert set(GOVERNANCE_LABELS) == set(GOVERNANCE_FUNCTIONS)
    for host, states in capabilities._FUNCTION_STATES.items():
        assert set(states) == set(GOVERNANCE_FUNCTIONS), host


@pytest.mark.parametrize("level", [ParityLevel.DEGRADED, ParityLevel.ABSENT])
def test_un_etat_non_riche_sans_raison_est_refuse(level: ParityLevel) -> None:
    with pytest.raises(ValueError, match="raison"):
        FunctionState(level)
    with pytest.raises(ValueError, match="raison"):
        FunctionState(level, "   ")


def test_un_etat_rich_avec_raison_est_refuse() -> None:
    with pytest.raises(ValueError, match="rich"):
        FunctionState(ParityLevel.RICH, "pourtant tout va bien")


def test_une_fonction_ajoutee_sans_etat_fait_echouer(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = {host: dict(states) for host, states in capabilities._FUNCTION_STATES.items()}
    del broken[HostId.GEMINI_CLI][GovernanceFunction.PILOT]
    monkeypatch.setattr(capabilities, "_FUNCTION_STATES", broken)
    assert undeclared_functions(HostId.GEMINI_CLI) == (GovernanceFunction.PILOT,)
    with pytest.raises(KeyError, match="pilot"):
        function_states(HostId.GEMINI_CLI)


def test_un_hote_ajoute_sans_etat_fait_echouer(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = {host: states for host, states in capabilities._FUNCTION_STATES.items() if host is not HostId.OPENCODE}
    monkeypatch.setattr(capabilities, "_FUNCTION_STATES", broken)
    with pytest.raises(KeyError, match="host-opencode"):
        function_states(HostId.OPENCODE)


def test_la_ligne_de_parite_nomme_ce_qui_manque() -> None:
    assert "gouvernance complète" in parity_line(HostId.CLAUDE_CODE_CLI)
    gemini = parity_line(HostId.GEMINI_CLI)
    assert "dégradé" in gemini and "absent" in gemini
    report = parity_report(HostId.CLAUDE_CODE_CLI)
    assert report["line"] == parity_line(HostId.CLAUDE_CODE_CLI)
    assert len(report["functions"]) == len(GOVERNANCE_FUNCTIONS)


def test_la_matrice_porte_un_etat_par_fonction_et_par_hote(tmp_path: Path) -> None:
    """``grimoire host matrix`` affiche ces états : la matrice doit les porter."""
    from grimoire.hosts.matrix import build_matrix

    hosts = (HostId.GEMINI_CLI, HostId.CLAUDE_CODE_CLI)
    matrix = build_matrix(tmp_path, hosts)
    assert len(matrix.functions) == len(hosts) * len(GOVERNANCE_FUNCTIONS)
    for host in hosts:
        for function in GOVERNANCE_FUNCTIONS:
            cell = matrix.function_for(host, function)
            if cell.state != ParityLevel.RICH.value:
                assert cell.reason.strip(), (host, function)
    assert set(matrix.functions[0].to_dict()) == {"function", "label", "host", "state", "reason"}
    assert any(c["function"] == "pilot" for c in matrix.to_dict()["functions"])
