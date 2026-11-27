"""Le chat ne démarre jamais sur un hôte mort : défaut = premier hôte dont l'ACP répond, choix explicite respecté.

Un exécutable homonyme (le raccourci ``copilot`` de VS Code) passe ``shutil.which`` sans parler ACP : seul
``initialize`` (la sonde de ``grimoire doctor``, sans session ni prompt, donc sans coût de modèle) le dit, et son verdict
est gardé. Aucun hôte utilisable : le chat et l'assistant de l'Atelier le disent, avec la commande à lancer.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from grimoire.acp import AcpError
from grimoire.hosts import acp_hosts
from grimoire.hosts.capabilities import HostId, profile_for
from grimoire.tools import workspace_atelier_assistant, workspace_chat


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.delenv(workspace_chat.AGENT_COMMAND_ENV, raising=False)
    monkeypatch.delenv(acp_hosts.HOST_ENV, raising=False)
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    monkeypatch.setenv(workspace_chat.GRACE_ENV, "0")
    acp_hosts.clear_probe_cache()
    root = tmp_path / "projet"
    (root / ".git").mkdir(parents=True)
    yield root
    workspace_chat.shutdown_all()
    acp_hosts.clear_probe_cache()


def _installed(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    monkeypatch.setattr(acp_hosts.shutil, "which", lambda n: f"/bin/{n}" if n in names else None)


def _declare(root: Path, *aliases: str) -> None:
    listed = ", ".join(f'"{a}"' for a in aliases)
    (root / "project-context.yaml").write_text(f'project:\n  name: "p"\nhosts:\n  enabled: [{listed}]\n', encoding="utf-8")


class Probe:
    """Une sonde ``initialize`` factice : les commandes de ``dead`` ne répondent pas, les autres oui ; elle compte ses appels."""

    def __init__(self, *dead: str) -> None:
        self.dead = set(dead)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: tuple[str, ...], _cwd: Path) -> None:
        self.calls.append(command)
        if command[0] in self.dead:
            raise AcpError("Copilot CLI n'est pas installé")


# ── La règle de choix ───────────────────────────────────────────────────────


def test_un_hote_detecte_par_ses_fichiers_mais_muet_n_est_jamais_le_defaut(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Copilot détecté (le raccourci ``copilot`` existe) mais muet : le défaut est vide, opencode n'est PAS pris d'office."""
    (project / ".github").mkdir()
    (project / ".github" / "copilot-instructions.md").write_text("x", encoding="utf-8")
    _installed(monkeypatch, "copilot", "opencode")
    probe = Probe("copilot")
    assert acp_hosts.default_alias(project, probe=probe) == ""


def test_un_seul_hote_utilisable_et_pas_le_premier_de_la_liste(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(project, "claude", "copilot", "gemini")
    _installed(monkeypatch, "gemini")  # claude-agent-acp absent, copilot absent
    assert acp_hosts.default_alias(project, probe=Probe()) == "gemini"


def test_un_hote_hors_projet_n_est_jamais_pris_d_office_il_est_propose(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """L'hôte du projet (Claude Code) n'a pas d'ACP ; opencode est seulement installé sur la machine : Kanban et proposition."""
    _declare(project, "claude")
    _installed(monkeypatch, "opencode")
    probe = Probe()
    assert acp_hosts.default_alias(project, probe=probe) == ""
    assert [p.display_name for p in acp_hosts.other_usable_hosts(project, probe=probe)] == ["opencode"]
    assert workspace_chat.chat_mode(project)[0] == "kanban"
    state = workspace_chat.chat_hosts(project, {})
    assert state["fallback"] is True and state["offers"] == [{"alias": "opencode", "display_name": "opencode"}]
    assert "Claude Code est l'hôte de ce projet" in state["reason"]
    assert acp_hosts.INSTALL_HINTS["claude-agent-acp"] in state["reason"]


def test_l_assistant_de_l_atelier_suit_la_meme_regle_il_ne_prend_pas_l_hote_d_une_autre_machine(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _declare(project, "claude")
    _installed(monkeypatch, "opencode")
    monkeypatch.setattr(acp_hosts, "_probe_initialize", Probe())
    status = workspace_atelier_assistant.agent_status(project)
    assert status["available"] is False and status["mode"] == "kanban"
    assert "Claude Code est l'hôte de ce projet" in status["reason"]


def test_l_hote_du_projet_vivant_est_le_defaut_et_aucune_proposition(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(project, "claude")
    _installed(monkeypatch, "claude-agent-acp", "opencode")
    monkeypatch.setattr(acp_hosts, "_probe_initialize", Probe())
    assert acp_hosts.default_alias(project) == "claude"
    state = workspace_chat.chat_hosts(project, {})
    assert state["chosen"] == "claude" and state["fallback"] is False and state["offers"] == []


def test_le_choix_explicite_d_un_autre_hote_est_respecte_et_memorise(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(project, "claude")
    _installed(monkeypatch, "opencode")
    monkeypatch.setattr(acp_hosts, "_probe_initialize", Probe())
    acp_hosts.set_chosen_alias(project, "opencode")
    state = workspace_chat.chat_hosts(project, {})
    assert state["chosen"] == "opencode" and state["mode"] == "acp" and state["fallback"] is False
    assert acp_hosts.chosen_alias(project) == "opencode"  # relu du réglage du projet


def test_l_ordre_du_projet_prime_quand_plusieurs_hotes_repondent(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(project, "gemini", "opencode")
    _installed(monkeypatch, "gemini", "opencode")
    assert acp_hosts.default_alias(project, probe=Probe()) == "gemini"


def test_aucun_hote_utilisable_le_defaut_est_vide_et_la_raison_donne_la_commande(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    assert acp_hosts.default_alias(project, probe=Probe()) == ""
    reason = acp_hosts.no_host_reason(project)
    assert "l'hôte de ce projet" in reason or "aucun hôte d'agent" in reason
    assert acp_hosts.INSTALL_HINTS["claude-agent-acp"] in reason


def test_la_raison_nomme_l_hote_du_projet_installe_mais_muet(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(project, "copilot")
    _installed(monkeypatch, "copilot")
    acp_hosts.default_alias(project, probe=Probe("copilot"))
    reason = acp_hosts.no_host_reason(project)
    assert "GitHub Copilot (VS Code)" in reason and "ne répond pas" in reason


def test_un_choix_explicite_est_respecte_meme_s_il_est_mort(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "copilot", "opencode")
    acp_hosts.set_chosen_alias(project, "copilot")
    assert acp_hosts.chosen_alias(project) == "copilot"
    monkeypatch.setenv(acp_hosts.HOST_ENV, "opencode")  # l'environnement passe avant le réglage
    assert acp_hosts.chosen_alias(project) == "opencode"
    monkeypatch.setenv(acp_hosts.HOST_ENV, "kanban")
    assert workspace_chat.chat_mode(project)[0] == "kanban"


def test_sans_aucun_choix_explicite_le_defaut_n_est_pas_un_hote_mort(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    assert acp_hosts.chosen_alias(project) == ""
    mode, command, reason = workspace_chat.chat_mode(project)
    assert (mode, command) == ("kanban", ()), "aucun agent : repli Kanban, jamais un hôte mort"
    assert "l'hôte de ce projet" in reason


# ── Les commandes d'installation : seulement celles dont le paquet est vérifié ─


def test_les_commandes_d_installation_sont_celles_dont_le_paquet_et_l_executable_ont_ete_verifies() -> None:
    """Figé : `npm view <paquet> bin` (2026-11-26) déclare bien l'exécutable que le kit cherche dans le PATH."""
    assert acp_hosts.INSTALL_HINTS == {
        "claude-agent-acp": "npm install -g @agentclientprotocol/claude-agent-acp",
        "copilot": "npm install -g @github/copilot",
        "codex": "npm install -g @openai/codex",
        "gemini": "npm install -g @google/gemini-cli",
        "opencode": "npm install -g opencode-ai",
    }


def test_un_hote_sans_commande_verifiee_dit_voir_sa_documentation_sans_rien_inventer(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cursor = profile_for(HostId.CURSOR)
    assert acp_hosts.install_hint(cursor) == ""
    _installed(monkeypatch, "agent")
    usable, reason = acp_hosts.acp_usable(cursor, project, probe=Probe("agent"))
    assert not usable and "voir sa documentation" in reason and "curl" not in reason and "npm" not in reason
    monkeypatch.setenv(acp_hosts.HOST_ENV, "cursor")
    assert "voir sa documentation" in acp_hosts.start_failure_remedy(project)


# ── La sonde et son cache ───────────────────────────────────────────────────


def test_la_sonde_n_est_pas_relancee_a_chaque_ouverture(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(project, "opencode")
    _installed(monkeypatch, "opencode")
    probe = Probe()
    for _ in range(3):
        assert acp_hosts.default_alias(project, probe=probe) == "opencode"
    assert probe.calls == [("opencode", "acp")]


def test_un_hote_muet_est_resonde_apres_son_delai_court_et_un_hote_sain_plus_tard(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "copilot")
    profile = profile_for(HostId.GITHUB_COPILOT)
    probe = Probe("copilot")
    now = [1000.0]
    clock = lambda: now[0]  # noqa: E731
    assert acp_hosts.acp_usable(profile, project, probe=probe, clock=clock)[0] is False
    assert acp_hosts.acp_usable(profile, project, probe=probe, clock=clock)[0] is False
    assert len(probe.calls) == 1
    now[0] += acp_hosts.FAIL_TTL_SECONDS + 1  # l'utilisateur a réparé : on le voit sans redémarrer le cockpit
    probe.dead.clear()
    assert acp_hosts.acp_usable(profile, project, probe=probe, clock=clock) == (True, "")
    assert len(probe.calls) == 2
    now[0] += acp_hosts.FAIL_TTL_SECONDS + 1
    assert acp_hosts.acp_usable(profile, project, probe=probe, clock=clock)[0] is True
    assert len(probe.calls) == 2, "un hôte qui répond reste utilisable bien plus longtemps"


def test_un_hote_lance_par_npx_n_est_jamais_sonde(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``npx -y …`` téléchargerait un paquet : on s'en tient à la présence de ``npx`` et de ``codex``."""
    _installed(monkeypatch, "npx", "codex")
    probe = Probe()
    assert acp_hosts.acp_usable(profile_for(HostId.CODEX), project, probe=probe) == (True, "")
    assert probe.calls == []


def test_la_raison_d_un_hote_muet_porte_la_cause_et_la_commande(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch, "copilot")
    usable, reason = acp_hosts.acp_usable(profile_for(HostId.GITHUB_COPILOT), project, probe=Probe("copilot"))
    assert not usable
    assert "Copilot CLI n'est pas installé" in reason and "npm install -g @github/copilot" in reason


def test_un_executable_remplace_change_la_cle_donc_refait_la_sonde(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "opencode"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(acp_hosts.shutil, "which", lambda n: str(exe) if n == "opencode" else None)
    probe = Probe()
    profile = profile_for(HostId.OPENCODE)
    acp_hosts.acp_usable(profile, project, probe=probe)
    exe.write_text("#!/bin/sh\n# réinstallé\n", encoding="utf-8")
    time.sleep(0.01)
    acp_hosts.acp_usable(profile, project, probe=probe)
    assert len(probe.calls) == 2


def test_le_selecteur_montre_les_hotes_inutilisables_grises_avec_leur_raison(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(project, "copilot", "opencode")
    _installed(monkeypatch, "copilot", "opencode")
    monkeypatch.setattr(acp_hosts, "_probe_initialize", Probe("copilot"))
    by_alias = {c.alias: c for c in acp_hosts.host_choices(project)}
    assert not by_alias["copilot"].available and "ne répond pas" in by_alias["copilot"].reason
    assert not by_alias["claude"].available and acp_hosts.INSTALL_HINTS["claude-agent-acp"] in by_alias["claude"].reason
    assert by_alias["opencode"].available and by_alias["opencode"].selected and by_alias["opencode"].detected
    assert by_alias["kanban"].available


def test_aucun_hote_utilisable_le_kanban_est_selectionne_et_le_repli_est_publie(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    state = workspace_chat.chat_hosts(project, {})
    assert state["mode"] == "kanban" and state["chosen"] == "" and state["fallback"] is True
    selected = [h["alias"] for h in state["hosts"] if h["selected"]]
    assert selected == ["kanban"]
    assert all(not h["available"] and h["reason"] for h in state["hosts"] if h["alias"] != "kanban")
    assert "npm install" in state["reason"]


def test_un_choix_explicite_du_kanban_n_est_pas_un_repli(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    acp_hosts.set_chosen_alias(project, "kanban")
    state = workspace_chat.chat_hosts(project, {})
    assert state["mode"] == "kanban" and state["fallback"] is False


# ── Le chat et l'assistant le disent, au lieu d'échouer après coup ──────────


def test_sans_hote_utilisable_le_chat_ecrit_dans_le_kanban_au_lieu_d_echouer(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions.service import TaskService

    _installed(monkeypatch)
    service = TaskService(project)
    added = service.add("Tâche vivante", ("un critère",), ready=True)
    service.claim(added.task.id, "session-x")
    assert workspace_chat.chat_send(project, {"text": "bonjour"})["ok"] is True
    assert TaskService(project).require(added.task.id).directives[0].text == "bonjour"


def test_l_assistant_de_l_atelier_dit_qu_aucun_agent_n_est_joignable_car_demarrer_le_cadrage_en_a_besoin(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _installed(monkeypatch)
    status = workspace_atelier_assistant.agent_status(project)
    assert status["available"] is False and status["mode"] == "kanban"
    assert "npm install" in status["reason"]


def test_l_echec_du_demarrage_dit_la_cause_le_geste_de_reparation_et_termine_le_tour_en_erreur(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(workspace_chat.AGENT_COMMAND_ENV, "agent-qui-n-existe-pas --acp")
    acp_hosts.set_chosen_alias(project, "opencode")
    gen = workspace_chat.chat_stream(project, parse_qs(""))
    events: list[dict] = []

    def until(predicate, timeout: float = 20.0) -> dict:  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in events:
                if predicate(event):
                    return event
            for line in next(gen).decode().splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        raise AssertionError(f"événement attendu non reçu : {events}")

    try:
        until(lambda e: e["type"] == "hello")
        workspace_chat.chat_send(project, {"text": "bonjour"})
        error = until(lambda e: e["type"] == "error")
        ended = until(lambda e: e["type"] == "turn_ended")
    finally:
        gen.close()
    assert "introuvable" in error["message"] and "npm install -g opencode-ai" in error["message"]
    assert "sélecteur « Hôte »" in error["message"]
    assert ended["stop_reason"] == "error"
    assert [e for e in events if e["type"] == "error"] == [error], "le même message n'est pas répété"
