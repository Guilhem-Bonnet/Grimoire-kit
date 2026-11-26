"""Matrice de capacités par système et par surface (épique #1280, lot C4).

La matrice est DÉRIVÉE du code (profils, contrat, dégradations de l'émetteur, ``WORKER_HOSTS``,
``claude_only_refusal``, ``acp_command``) : ces tests le prouvent en changeant la source et en voyant la
cellule suivre, et font échouer la CI si une capacité déclarée bloquante n'a pas de capture. Rien n'est promu.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.bridges.schemas import HostId
from grimoire.cli.app import app
from grimoire.hosts import capability_matrix as cm
from grimoire.hosts.capabilities import profile_for
from grimoire.hosts.capability_matrix import SYSTEMS, CellState, build_capability_matrix, render_markdown
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import emitter_for, supported_hosts
from grimoire.hosts.events import HookEvent

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "hosts"
DOC = ROOT / "docs" / "reference" / "hotes.md"
runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Demo Capacites"\nagents:\n  archetype: "minimal"\nhosts:\n  enabled: ["claude", "copilot"]\n',
        encoding="utf-8",
    )
    return tmp_path


def _matrix(host: HostId, project: Path, **kwargs: object) -> cm.CapabilityMatrix:
    plan = emitter_for(host).plan(build_surface(project, write_cache=False), project)  # type: ignore[union-attr]
    return build_capability_matrix(host, degradations=plan.degradations, **kwargs)  # type: ignore[arg-type]


# ── Copilot : trois surfaces, rien de prouvé ────────────────────────────────


def test_copilot_a_tous_les_systemes_sur_les_trois_surfaces(project: Path) -> None:
    matrix = _matrix(HostId.GITHUB_COPILOT, project, fixtures_root=FIXTURES)
    assert [s.id for s in matrix.surfaces] == ["vscode", "cli", "cloud"]
    assert tuple(matrix.cells) == SYSTEMS
    assert all(set(row) == {"vscode", "cli", "cloud"} for row in matrix.cells.values())
    assert matrix.ok, matrix.problems
    for system in (
        "pre_tool_hook", "post_tool_hook", "session_hook", "subagent_hook", "compaction_hook", "closure_hook",
        "session_link", "memory", "mcp", "skills_agents", "dispatch_model", "orchestrator_worker",
        "orchestrator_reviewer", "orchestrator_arbiter", "cockpit_chat", "fleet", "tokens",
    ):
        assert system in matrix.cells


def test_copilot_rien_n_est_prouve_aucune_capture_de_refus(project: Path) -> None:
    matrix = _matrix(HostId.GITHUB_COPILOT, project)
    proven = [(s, surface) for s, row in matrix.cells.items() for surface, c in row.items() if c.state is CellState.PROVEN]
    assert proven == []
    pre = matrix.cell("pre_tool_hook", "vscode")
    assert pre.state is CellState.UNVERIFIED and "aucune capture" in pre.detail and "consultatif" in pre.detail
    assert "hook timeout" in pre.detail  # les limites déclarées par l'émetteur sont dites
    assert "captures faites sur vscode seulement" in matrix.cell("pre_tool_hook", "cli").detail


def test_copilot_cloud_mcp_degrade_avec_son_repli_derive_de_l_emetteur(project: Path) -> None:
    matrix = _matrix(HostId.GITHUB_COPILOT, project)
    cloud = matrix.cell("mcp", "cloud")
    assert cloud.state is CellState.DEGRADED and "Settings > Copilot > MCP servers" in cloud.detail
    assert cloud.fallback
    assert matrix.cell("mcp", "vscode").state is CellState.UNVERIFIED
    # Sans le plan de l'émetteur, rien n'est inventé : la cellule retombe sur « non vérifié ».
    bare = build_capability_matrix(HostId.GITHUB_COPILOT)
    assert bare.cell("mcp", "cloud").state is CellState.UNVERIFIED


def test_copilot_dispatch_modele_degrade_et_vu_en_partie_dans_le_journal(project: Path) -> None:
    cell = _matrix(HostId.GITHUB_COPILOT, project).cell("dispatch_model", "vscode")
    assert cell.state is CellState.DEGRADED and cell.fallback
    assert "assistant.message.model" in cell.detail


def test_copilot_orchestrateur_non_pris_en_charge_avec_la_raison_du_code(project: Path) -> None:
    matrix = _matrix(HostId.GITHUB_COPILOT, project)
    worker = matrix.cell("orchestrator_worker", "vscode")
    assert worker.state is CellState.UNSUPPORTED and "claude-code" in worker.detail
    reviewer = matrix.cell("orchestrator_reviewer", "cli")
    assert reviewer.state is CellState.UNSUPPORTED and "aucun repli silencieux sur claude" in reviewer.detail
    assert matrix.cell("orchestrator_session", "cloud").detail.startswith("surface distante")


def test_copilot_chat_flotte_et_jetons_par_surface(project: Path) -> None:
    matrix = _matrix(HostId.GITHUB_COPILOT, project)
    assert matrix.cell("cockpit_chat", "vscode").state is CellState.DEGRADED
    assert "kanban" in matrix.cell("cockpit_chat", "vscode").fallback
    cli_chat = matrix.cell("cockpit_chat", "cli")
    assert cli_chat.state is CellState.DEGRADED and "copilot --acp" in cli_chat.detail and "preview" in cli_chat.detail
    assert matrix.cell("cockpit_chat", "cloud").state is CellState.UNSUPPORTED
    for system in ("fleet", "tokens"):
        vscode = matrix.cell(system, "vscode")
        assert vscode.state is CellState.UNVERIFIED and "GRIMOIRE_COPILOT_LOGS_DIR" in vscode.detail
        assert matrix.cell(system, "cloud").state is CellState.UNSUPPORTED


# ── Claude Code : ce qui est prouvé l'est par capture ───────────────────────


def test_claude_prouve_par_capture_et_chaque_capture_existe(project: Path) -> None:
    matrix = _matrix(HostId.CLAUDE_CODE_CLI, project, fixtures_root=FIXTURES)
    assert matrix.ok, matrix.problems
    assert [s.id for s in matrix.surfaces] == ["host"]
    pre = matrix.cell("pre_tool_hook", "host")
    assert pre.state is CellState.PROVEN and pre.captures
    assert all((FIXTURES / c).is_file() for c in pre.captures)
    assert matrix.cell("tokens", "host").state is CellState.PROVEN
    assert matrix.cell("orchestrator_worker", "host").state is CellState.PROVEN
    # Seul PreToolUse est capturé : la clôture reste documentée, consultative.
    assert matrix.cell("closure_hook", "host").state is CellState.UNVERIFIED


# ── Toutes les cellules, tous les hôtes ─────────────────────────────────────


@pytest.mark.parametrize("host", supported_hosts(), ids=lambda h: h.value)
def test_chaque_hote_a_une_matrice_coherente(host: HostId, project: Path) -> None:
    matrix = _matrix(host, project, fixtures_root=FIXTURES)
    assert matrix.ok, matrix.problems
    for row in matrix.cells.values():
        for cell in row.values():
            if cell.state is CellState.PROVEN:
                assert cell.captures and all((FIXTURES / c).is_file() for c in cell.captures)
            if cell.state is CellState.DEGRADED:
                assert cell.fallback
            if cell.state is CellState.UNSUPPORTED:
                assert cell.detail
    payload = matrix.to_dict()
    assert payload["host"] == host.value and len(payload["systems"]) == len(SYSTEMS)
    json.dumps(payload)


def test_une_capacite_declaree_bloquante_sans_capture_echoue(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    forged = replace(
        profile_for(HostId.GITHUB_COPILOT),
        blocking_hooks=True,
        blocking_events=frozenset({HookEvent.PRE_TOOL_USE}),
        blocking_captures=(),
    )
    monkeypatch.setattr(cm, "profile_for", lambda host_id: forged)
    matrix = build_capability_matrix(HostId.GITHUB_COPILOT, fixtures_root=FIXTURES)
    assert not matrix.ok
    assert any("DÉCLARÉ BLOQUANT SANS CAPTURE" in p for p in matrix.problems)
    assert any("sans aucune capture référencée" in p for p in matrix.problems)
    # Jamais rendu « prouvé » sans capture.
    assert matrix.cell("pre_tool_hook", "vscode").state is CellState.UNVERIFIED


def test_le_declare_bloquant_avec_capture_est_prouve(monkeypatch: pytest.MonkeyPatch) -> None:
    forged = replace(
        profile_for(HostId.GITHUB_COPILOT),
        blocking_hooks=True,
        blocking_events=frozenset({HookEvent.PRE_TOOL_USE}),
        blocking_captures=("claude-code/01-pretooluse-deny-exit2.json",),
    )
    monkeypatch.setattr(cm, "profile_for", lambda host_id: forged)
    matrix = build_capability_matrix(HostId.GITHUB_COPILOT)
    vscode = matrix.cell("pre_tool_hook", "vscode")
    assert vscode.state is CellState.PROVEN and vscode.captures == ("claude-code/01-pretooluse-deny-exit2.json",)
    # Les captures viennent de VS Code : sur la CLI et le cloud, rien n'est promu.
    assert matrix.cell("pre_tool_hook", "cli").state is CellState.UNVERIFIED
    assert matrix.cell("pre_tool_hook", "cloud").state is CellState.UNVERIFIED


# ── Dérivation : changer la source change la cellule ────────────────────────


def test_la_cellule_worker_suit_worker_hosts(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions import worker_host

    assert _matrix(HostId.GITHUB_COPILOT, project).cell("orchestrator_worker", "vscode").state is CellState.UNSUPPORTED
    monkeypatch.setattr(worker_host, "WORKER_HOSTS", (*worker_host.WORKER_HOSTS, "copilot"))
    assert _matrix(HostId.GITHUB_COPILOT, project).cell("orchestrator_worker", "vscode").state is CellState.UNVERIFIED


def test_la_cellule_chat_suit_acp_command(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plain = replace(profile_for(HostId.GITHUB_COPILOT), acp_command=())
    monkeypatch.setattr(cm, "profile_for", lambda host_id: plain)
    cell = build_capability_matrix(HostId.GITHUB_COPILOT).cell("cockpit_chat", "cli")
    assert cell.state is CellState.DEGRADED and "pas de mode ACP" in cell.detail


def test_la_cellule_jetons_suit_les_lecteurs_de_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.traces import transcript_usage

    monkeypatch.setattr(transcript_usage, "USAGE_SOURCE_BY_HOST", {})
    cell = build_capability_matrix(HostId.GITHUB_COPILOT).cell("tokens", "vscode")
    assert cell.state is CellState.UNSUPPORTED and "aucun lecteur" in cell.detail


def test_les_captures_viennent_de_vscode_selon_le_manifeste() -> None:
    manifest = json.loads((FIXTURES / "copilot" / "manifest.json").read_text(encoding="utf-8"))
    assert cm.CAPTURE_SURFACE[HostId.GITHUB_COPILOT] == "vscode"
    assert "VS Code" in manifest["source"]


def test_une_cellule_incoherente_est_refusee() -> None:
    with pytest.raises(ValueError, match="captures"):
        cm.Cell(CellState.PROVEN, "x")
    with pytest.raises(ValueError, match="repli"):
        cm.Cell(CellState.DEGRADED, "x")
    with pytest.raises(ValueError, match="pourquoi"):
        cm.Cell(CellState.UNSUPPORTED, "")


# ── CLI et MCP ──────────────────────────────────────────────────────────────


def test_host_status_json_porte_la_matrice_et_l_emplacement_des_journaux(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GRIMOIRE_COPILOT_LOGS_DIR", raising=False)
    result = runner.invoke(app, ["-o", "json", "host", "status", "--host", "copilot", "--project-root", str(project)])
    (item,) = json.loads(result.stdout)
    assert tuple(s["id"] for s in item["capability_matrix"]["systems"]) == SYSTEMS
    assert item["session_logs"]["configured"] is False and item["session_logs"]["verified"] is False
    assert "journaux Copilot : emplacement non configuré" in item["session_logs"]["message"]
    claude = runner.invoke(app, ["-o", "json", "host", "status", "--host", "claude", "--project-root", str(project)])
    assert json.loads(claude.stdout)[0]["session_logs"] == {}


def test_host_status_texte_rend_la_matrice_et_le_message(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_COPILOT_LOGS_DIR", raising=False)
    result = runner.invoke(app, ["host", "status", "--host", "copilot", "--project-root", str(project)])
    out = re.sub(r"\s+", " ", result.stdout)
    assert "journaux Copilot : emplacement non configuré" in out
    assert "Capacités par système" in out and "Hooks avant outil" in out
    assert "VS Code Copilot Chat" in out and "Agent cloud" in out
    assert "émis, non vérifié" in out and "non pris en charge" in out and "dégradé" in out


def test_host_status_tous_les_hotes_ne_noie_pas_le_texte(project: Path) -> None:
    result = runner.invoke(app, ["host", "status", "--host", "all", "--project-root", str(project)])
    assert "Capacités par système" not in result.stdout


def test_outil_mcp_host_status_porte_la_matrice(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.mcp import server

    monkeypatch.delenv("GRIMOIRE_COPILOT_LOGS_DIR", raising=False)
    payload = json.loads(server.grimoire_host_status(str(project)))
    by_host = {h["host"]: h for h in payload["hosts"]}
    copilot = by_host["host-github-copilot"]
    assert tuple(s["id"] for s in copilot["capability_matrix"]["systems"]) == SYSTEMS
    assert copilot["session_logs"]["configured"] is False
    assert by_host["host-claude-code-cli"]["session_logs"] == {}


# ── Documentation : la table est vérifiée contre la matrice ─────────────────

_BEGIN = "<!-- capability-matrix:copilot:begin -->"
_END = "<!-- capability-matrix:copilot:end -->"


def test_la_table_de_hotes_md_est_la_matrice_de_copilot(project: Path) -> None:
    text = DOC.read_text(encoding="utf-8")
    assert _BEGIN in text and _END in text, "docs/reference/hotes.md : bornes de la matrice Copilot absentes"
    block = text.split(_BEGIN, 1)[1].split(_END, 1)[0].strip()
    expected = render_markdown(_matrix(HostId.GITHUB_COPILOT, project)).strip()
    assert block == expected, "hotes.md est périmé : coller render_markdown(matrice Copilot) entre les bornes :\n" + expected


_ROW = re.compile(r"^\| (?P<host>[^|]+?) \| (?P<agents>[^|]+) \| (?P<skills>[^|]+) \| (?P<commands>[^|]+) \| (?P<hooks>[^|]+) \| (?P<perms>[^|]+) \|$")
_NAMES = {
    "Claude Code": HostId.CLAUDE_CODE_CLI,
    "GitHub Copilot": HostId.GITHUB_COPILOT,
    "opencode": HostId.OPENCODE,
    "Codex": HostId.CODEX,
    "Cursor": HostId.CURSOR,
    "Gemini CLI": HostId.GEMINI_CLI,
    "Kimi Code": HostId.KIMI_CODE,
    "Zed": HostId.ZED,
}


def test_la_table_ce_que_chaque_hote_execute_suit_les_profils() -> None:
    """Chaque oui/non de la table « Ce que chaque hôte exécute » est celui du profil ; « oui » aux hooks = refus capturé."""
    text = DOC.read_text(encoding="utf-8").split("## Ce que chaque hôte exécute", 1)[1].split("\n## ", 1)[0]
    rows = [m for line in text.splitlines() if (m := _ROW.match(line)) and m["host"] in _NAMES]
    assert {m["host"] for m in rows} == set(_NAMES)
    for m in rows:
        profile = profile_for(_NAMES[m["host"]])
        for column, declared in (
            ("agents", profile.subagents_native),
            ("skills", profile.skills_native),
            ("commands", profile.commands_native),
            ("hooks", profile.enforces_blocking),
            ("perms", profile.permissions_native),
        ):
            says_yes = m[column].strip().startswith("oui")
            assert says_yes == declared, f"{m['host']} / {column} : la table dit {m[column]!r}, le profil {declared}"
