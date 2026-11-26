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
    """Le cas du poste réel : Copilot détecté (le raccourci ``copilot`` existe) mais muet, opencode répond."""
    (project / ".github").mkdir()
    (project / ".github" / "copilot-instructions.md").write_text("x", encoding="utf-8")
    _installed(monkeypatch, "copilot", "opencode")
    probe = Probe("copilot")
    assert acp_hosts.default_alias(project, probe=probe) == "opencode"


def test_un_seul_hote_utilisable_et_pas_le_premier_de_la_liste(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(project, "claude", "copilot", "gemini")
    _installed(monkeypatch, "gemini")  # claude-agent-acp absent, copilot absent
    assert acp_hosts.default_alias(project, probe=Probe()) == "gemini"


def test_un_hote_hors_projet_sert_quand_aucun_hote_du_projet_ne_repond(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(project, "copilot")
    _installed(monkeypatch, "copilot", "opencode")
    assert acp_hosts.default_alias(project, probe=Probe("copilot")) == "opencode"


def test_l_ordre_du_projet_prime_quand_plusieurs_hotes_repondent(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare(project, "gemini", "opencode")
    _installed(monkeypatch, "gemini", "opencode")
    assert acp_hosts.default_alias(project, probe=Probe()) == "gemini"


def test_aucun_hote_utilisable_le_defaut_est_vide_et_la_raison_donne_la_commande(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    assert acp_hosts.default_alias(project, probe=Probe()) == ""
    reason = acp_hosts.no_host_reason(project)
    assert "Aucun hôte d'agent n'est utilisable" in reason
    assert acp_hosts.INSTALL_HINTS["claude-agent-acp"] in reason
    assert "Kanban" in reason


def test_la_raison_nomme_les_hotes_installes_mais_muets(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert workspace_chat.chat_mode(project)[0] == "unavailable"


# ── La sonde et son cache ───────────────────────────────────────────────────


def test_la_sonde_n_est_pas_relancee_a_chaque_ouverture(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    _installed(monkeypatch, "copilot", "opencode")
    monkeypatch.setattr(acp_hosts, "_probe_initialize", Probe("copilot"))
    by_alias = {c.alias: c for c in acp_hosts.host_choices(project)}
    assert not by_alias["copilot"].available and "ne répond pas" in by_alias["copilot"].reason
    assert not by_alias["claude"].available and acp_hosts.INSTALL_HINTS["claude-agent-acp"] in by_alias["claude"].reason
    assert by_alias["opencode"].available and by_alias["opencode"].selected and by_alias["opencode"].detected
    assert by_alias["kanban"].available


def test_aucun_hote_utilisable_aucun_n_est_selectionne(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    state = workspace_chat.chat_hosts(project, {})
    assert state["mode"] == "unavailable" and state["chosen"] == ""
    assert not any(h["selected"] for h in state["hosts"])
    assert "npm install" in state["reason"]
    assert state["hosts"][-1]["alias"] == "kanban" and state["hosts"][-1]["available"]


# ── Le chat et l'assistant le disent, au lieu d'échouer après coup ──────────


def test_envoyer_sans_hote_utilisable_est_refuse_d_avance_avec_la_commande(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    with pytest.raises(ValueError, match="Aucun hôte d'agent n'est utilisable"):
        workspace_chat.chat_send(project, {"text": "bonjour"})


def test_l_assistant_de_l_atelier_dit_qu_aucun_agent_n_est_joignable(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(monkeypatch)
    status = workspace_atelier_assistant.agent_status(project)
    assert status["available"] is False and status["mode"] == "unavailable"
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
