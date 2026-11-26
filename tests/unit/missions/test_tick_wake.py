"""Le réveil du tick sur événement : borné, sobre, sans effet sur le geste, jamais un vrai ``systemctl``."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.missions import tick_wake
from grimoire.missions.breaker import trip
from grimoire.missions.orchestrate import TickReport
from grimoire.missions.pause import CONTROL_HOME_ENV, DRAIN, SCOPE_GLOBAL, SCOPE_PROJECT, PauseState, write_pause
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.tick_wake import (
    CHAIN_WINDOW_S,
    DEBOUNCE_S,
    MAX_PER_HOUR,
    STATE_RELPATH,
    describe_last_wake,
    read_last_wake,
    request_tick,
    tick_wake_reason,
    wake_after,
    wakes_after_human,
)
from grimoire.missions.timer import _unit_name, plan_timer

runner = CliRunner()
POLICY = Path("_grimoire/standard/orchestration-policy.yaml")


class FakeRun:
    """Le faux exécuteur : note chaque argv, ne lance rien."""

    def __init__(self, returncode: int = 0, stderr: str = "", raises: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self.returncode, self.stderr, self.raises = returncode, stderr, raises

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, "", self.stderr)


def write_policy(root: Path, *, enabled: bool = True) -> None:
    (root / POLICY).parent.mkdir(parents=True, exist_ok=True)
    (root / POLICY).write_text(
        "orchestrator:\n  roles:\n    worker:\n"
        f"      enabled: {str(enabled).lower()}\n      wip: 1\n      sessions_per_day: 5\n      permission_mode: acceptEdits\n",
        encoding="utf-8",
    )


def install_timer_files(root: Path) -> None:
    plan = plan_timer(root)
    plan.directory.mkdir(parents=True, exist_ok=True)
    plan.timer_path.write_text(plan.timer, encoding="utf-8")


@pytest.fixture
def projet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un projet prêt à être réveillé : politique active, minuteur posé, systemd présent, aucune pause."""
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv(CONTROL_HOME_ENV, str(tmp_path / "ctl"))
    monkeypatch.setattr(tick_wake.shutil, "which", lambda name: f"/usr/bin/{name}")
    write_policy(root)
    install_timer_files(root)
    return root


def unit_of(root: Path) -> str:
    return f"{_unit_name(root.resolve())}.service"


# ── Ce qui part ─────────────────────────────────────────────────────────────────


def test_demarre_l_unite_installee_sans_shell_ni_motif(projet: Path) -> None:
    run = FakeRun()
    outcome = request_tick(projet, "reponse_humaine", run=run, now=1000.0)
    assert outcome.status == "started"
    assert run.calls == [["systemctl", "--user", "start", "--no-block", unit_of(projet)]]
    assert all(isinstance(a, str) and not any(c in a for c in " ;|&$`\n") for a in run.calls[0])
    assert "reponse_humaine" not in " ".join(run.calls[0])  # le motif ne va jamais sur la ligne de commande
    last = read_last_wake(projet)
    assert (last["reason"], last["result"], last["origin"]) == ("reponse_humaine", "started", "geste")
    assert "réponse humaine" in describe_last_wake(projet) and "started" in describe_last_wake(projet)


def test_lanceur_par_defaut_sans_shell_et_borne(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(tick_wake.subprocess, "run", fake_run)
    assert request_tick(projet, "acceptation", now=1.0).status == "started"
    assert seen[0]["argv"][:3] == ["systemctl", "--user", "start"] and not seen[0].get("shell")
    assert seen[0]["timeout"] == 5 and seen[0]["check"] is False


def test_sans_reveil_enregistre_le_statut_le_dit(projet: Path) -> None:
    assert "aucun" in describe_last_wake(projet) and read_last_wake(projet) == {}


def test_un_motif_hors_liste_ne_part_pas(projet: Path) -> None:
    run = FakeRun()
    assert request_tick(projet, "; rm -rf /", run=run).status == "skipped"
    assert not run.calls


# ── Ce qui est refusé, sans rien écrire ─────────────────────────────────────────


def test_orchestrateur_eteint_ou_sans_politique(projet: Path) -> None:
    run = FakeRun()
    write_policy(projet, enabled=False)
    assert "non activé" in request_tick(projet, "reprise", run=run).detail
    (projet / POLICY).unlink()
    assert request_tick(projet, "reprise", run=run).status == "skipped"
    assert not run.calls and not (projet / STATE_RELPATH).exists()


def test_minuteur_absent_le_reveil_n_active_rien(projet: Path) -> None:
    plan_timer(projet).timer_path.unlink()
    run = FakeRun()
    assert "minuteur non installé" in request_tick(projet, "reprise", run=run).detail
    assert not run.calls and not (projet / STATE_RELPATH).exists()


def test_systemd_absent(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tick_wake.shutil, "which", lambda name: None)
    run = FakeRun()
    assert "systemd absent" in request_tick(projet, "reprise", run=run).detail
    assert request_tick(projet, "fusion", run=run).status == "skipped"  # le différé a besoin de systemd-run aussi
    assert not run.calls


@pytest.mark.parametrize("scope", [SCOPE_PROJECT, SCOPE_GLOBAL])
def test_rien_sous_pause_projet_ou_machine(projet: Path, tmp_path: Path, scope: str) -> None:
    write_pause(projet, mode=DRAIN, scope=scope, origin="human")
    run = FakeRun()
    assert request_tick(projet, "reprise", run=run, control_home=tmp_path / "ctl").detail == "pause en vigueur"
    assert not run.calls


def test_une_pause_de_taches_nommees_laisse_le_tick_tourner(projet: Path) -> None:
    write_pause(projet, mode=DRAIN, tasks=["T-1"], origin="human")
    assert request_tick(projet, "reprise", run=FakeRun(), now=5.0).status == "started"


def test_disjoncteur_ouvert(projet: Path) -> None:
    trip(projet, "test")
    run = FakeRun()
    assert request_tick(projet, "reprise", run=run).detail == "disjoncteur ouvert"
    assert not run.calls


# ── Anti-rebond et plafond ──────────────────────────────────────────────────────


def test_anti_rebond_un_reveil_par_fenetre(projet: Path) -> None:
    run = FakeRun()
    assert request_tick(projet, "reponse_humaine", run=run, now=1000.0).status == "started"
    assert request_tick(projet, "reponse_humaine", run=run, now=1000.0 + DEBOUNCE_S - 1).status == "skipped"
    assert request_tick(projet, "reponse_humaine", run=run, now=1000.0 + DEBOUNCE_S).status == "started"
    assert len(run.calls) == 2


def test_plafond_horaire_puis_reprise_apres_l_heure(projet: Path) -> None:
    run = FakeRun()
    for i in range(MAX_PER_HOUR):
        assert request_tick(projet, "reponse_humaine", run=run, now=1000.0 + i * (DEBOUNCE_S + 1)).status == "started"
    late = 1000.0 + MAX_PER_HOUR * (DEBOUNCE_S + 1)
    capped = request_tick(projet, "reponse_humaine", run=run, now=late)
    assert capped.status == "skipped" and "plafond" in capped.detail
    assert request_tick(projet, "reponse_humaine", run=run, now=1000.0 + 3700).status == "started"
    assert len(run.calls) == MAX_PER_HOUR + 1


# ── Échec de systemd, état abîmé ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "run", [FakeRun(returncode=1, stderr="Failed to connect"), FakeRun(raises=OSError("boom")),
            FakeRun(raises=subprocess.TimeoutExpired("systemctl", 5))],
)
def test_un_echec_de_systemctl_est_note_et_compte(projet: Path, run: FakeRun) -> None:
    outcome = request_tick(projet, "reprise", run=run, now=10.0)
    assert outcome.status == "failed"
    assert read_last_wake(projet)["result"].startswith("failed")
    assert request_tick(projet, "reprise", run=run, now=11.0).status == "skipped"  # un échec compte pour l'anti-rebond


def test_etat_abime_ignore(projet: Path) -> None:
    path = projet / STATE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    for junk in ("{pas du json", "[]", '{"$schema": "autre"}', '{"$schema": "grimoire-tick-wake/v1", "attempts": "x", "last": 3}'):
        path.write_text(junk, encoding="utf-8")
        assert read_last_wake(projet) == {}
        assert request_tick(projet, "reprise", run=FakeRun(), now=1e6 + len(junk) * 100).status == "started"


def test_etat_non_ordinaire_ignore(projet: Path, tmp_path: Path) -> None:
    """Un lien vers un état « plafond atteint » ne compte pas : il n'est pas lu (``open_regular``)."""
    target = tmp_path / "etranger.json"
    target.write_text(json.dumps({"$schema": "grimoire-tick-wake/v1", "attempts": [1000.0 + i for i in range(40)]}), encoding="utf-8")
    path = projet / STATE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    assert read_last_wake(projet) == {}
    assert request_tick(projet, "reprise", run=FakeRun(), now=1100.0).status == "started"
    assert not path.is_symlink() and json.loads(target.read_text(encoding="utf-8"))["attempts"][0] == 1000.0  # l'étranger n'est pas réécrit


def test_etat_illisible_du_dossier_de_controle(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def busy(*_a: Any, **_k: Any) -> Any:
        raise TimeoutError

    monkeypatch.setattr(tick_wake, "locked", busy)
    run = FakeRun()
    assert request_tick(projet, "reprise", run=run).status == "skipped"
    assert not run.calls


def test_ecriture_d_etat_impossible_ne_cache_pas_le_demarrage(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: Any, **_k: Any) -> None:
        raise OSError("disque plein")

    monkeypatch.setattr(tick_wake, "atomic_write", refuse)
    assert request_tick(projet, "reprise", run=FakeRun(), now=5.0).status == "started"


# ── Pas de boucle : un tick ne se réveille pas lui-même en chaîne ──────────────


def test_un_reveil_de_tick_part_en_differe_et_ne_se_chaine_pas(projet: Path) -> None:
    run = FakeRun()
    assert request_tick(projet, "fusion", run=run, now=1000.0).status == "started"
    argv = run.calls[0]
    assert argv[0] == "systemd-run" and f"--on-active={tick_wake.TICK_DELAY_S}s" in argv and argv[-5:] == [
        "systemctl", "--user", "start", "--no-block", unit_of(projet)]
    assert read_last_wake(projet)["origin"] == "tick"
    again = request_tick(projet, "fin_de_session", run=run, now=1000.0 + DEBOUNCE_S + 5)
    assert again.status == "skipped" and "pas de chaîne" in again.detail
    assert request_tick(projet, "fusion", run=run, now=1000.0 + CHAIN_WINDOW_S + 1).status == "started"
    assert len(run.calls) == 2


def test_un_geste_humain_entre_deux_ticks_rouvre_la_chaine(projet: Path) -> None:
    run = FakeRun()
    request_tick(projet, "fusion", run=run, now=1000.0)
    assert request_tick(projet, "reponse_humaine", run=run, now=1030.0).status == "started"
    assert request_tick(projet, "fusion", run=run, now=1060.0).status == "started"


def _report(**fields: Any) -> Any:
    return SimpleNamespace(**{"status": "ran", "mode": "apply", "merges": (), "external_merges": (), "reaped": (), **fields})


def test_un_tick_qui_n_a_rien_fait_ne_reveille_pas() -> None:
    assert tick_wake_reason(_report()) is None
    assert tick_wake_reason(_report(status="paused")) is None
    assert tick_wake_reason(_report(status="busy", reaped=(1,))) is None
    assert tick_wake_reason(_report(mode="plan", reaped=(1,))) is None
    assert tick_wake_reason(_report(merges=(SimpleNamespace(action="merge", status="waiting"),))) is None


def test_motifs_d_un_tick_qui_a_agi() -> None:
    assert tick_wake_reason(_report(merges=(SimpleNamespace(action="merge", status="done"),))) == "fusion"
    assert tick_wake_reason(_report(external_merges=(SimpleNamespace(status="done"),))) == "fusion"
    assert tick_wake_reason(_report(reaped=(1,))) == "fin_de_session"


# ── Le geste n'échoue jamais à cause du réveil ─────────────────────────────────


def test_wake_after_avale_toute_erreur(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("réveil cassé")

    monkeypatch.setattr(tick_wake, "request_tick", boom)
    wake_after(tmp_path, "reprise")  # ne lève pas


def test_decorateur_humain_reussi_seulement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[tuple[Path, str]] = []
    monkeypatch.setattr(tick_wake, "request_tick", lambda root, reason: seen.append((root, reason)))

    class Geste:
        project_root = tmp_path

        @wakes_after_human("reponse_humaine")
        def faire(self, *, origin: str, casse: bool = False) -> str:
            if casse:
                raise ValueError("refusé")
            return "fait"

    assert Geste().faire(origin="human") == "fait" and seen == [(tmp_path, "reponse_humaine")]
    assert Geste().faire(origin="agent") == "fait" and Geste().faire(origin="orchestrator") == "fait"
    with pytest.raises(ValueError, match="refusé"):
        Geste().faire(origin="human", casse=True)
    assert len(seen) == 1  # ni agent, ni orchestrateur, ni geste refusé


def test_decorateur_sans_racine_ne_fait_rien(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tick_wake, "request_tick", lambda *_a: pytest.fail("pas de racine : pas de réveil"))

    class Sans:
        @wakes_after_human("acceptation")
        def faire(self, *, origin: str) -> int:
            return 1

    assert Sans().faire(origin="human") == 1


# ── Chaque point de réveil appelle la fonction ─────────────────────────────────


def _waiting(root: Path) -> tuple[TaskService, str]:
    service = TaskService(root)
    tid = service.add("Travail", ("fait",), owner="guilhem", actor="claude", ready=True).task.id
    service.context(tid)
    service.claim(tid, "claude", host="host-claude-code-cli")
    service.transition(tid, TaskState.RUNNING, "claude")
    service.ask(tid, "Que faire ?", "claude", options=("a", "b"))
    return service, tid


def test_reponse_humaine_reveille_le_tick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(tick_wake, "request_tick", lambda root, reason: seen.append(reason))
    service, tid = _waiting(tmp_path)
    assert seen == []
    service.answer(tid, "a", "guilhem", origin="human")
    assert seen == ["reponse_humaine"]


def test_reponse_humaine_ne_casse_pas_si_le_reveil_casse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("réveil cassé")

    monkeypatch.setattr(tick_wake, "request_tick", boom)
    service, tid = _waiting(tmp_path)
    assert service.answer(tid, "a", "guilhem", origin="human").task.id == tid
    assert service.require(tid).status is TaskState.READY


def test_la_reponse_d_un_agent_ne_reveille_rien(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tick_wake, "request_tick", lambda *_a: pytest.fail("un agent ne réveille pas le tick"))
    service, tid = _waiting(tmp_path)
    with pytest.raises(Exception, match=r"refus|humain|origine|mandat"):
        service.answer(tid, "a", "claude", origin="agent")


def test_acceptation_service_est_un_point_de_reveil() -> None:
    assert hasattr(TaskService.accept, "__wrapped__") and hasattr(TaskService.answer, "__wrapped__")


def test_cli_reprise_reveille_seulement_si_une_pause_est_levee(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(tick_wake, "request_tick", lambda root, reason: seen.append(reason))
    monkeypatch.setattr("grimoire.cli.cmd_task._comment_origin", lambda: "human")
    root = ["--project-root", str(projet)]
    assert runner.invoke(app, ["orchestrate", "resume", *root]).exit_code == 0 and seen == []  # rien à lever
    assert runner.invoke(app, ["orchestrate", "pause", *root]).exit_code == 0
    assert runner.invoke(app, ["orchestrate", "resume", *root]).exit_code == 0
    assert seen == ["reprise"]


def test_cli_reprise_reussit_meme_si_le_reveil_casse(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("réveil cassé")

    monkeypatch.setattr(tick_wake, "request_tick", boom)
    monkeypatch.setattr("grimoire.cli.cmd_task._comment_origin", lambda: "human")
    root = ["--project-root", str(projet)]
    runner.invoke(app, ["orchestrate", "pause", *root])
    done = runner.invoke(app, ["orchestrate", "resume", *root])
    assert done.exit_code == 0 and "Pause levée" in done.output


def _tick_cli(projet: Path, monkeypatch: pytest.MonkeyPatch, report: TickReport, *args: str) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(tick_wake, "request_tick", lambda root, reason: seen.append(reason))
    monkeypatch.setattr("grimoire.missions.orchestrate.run_tick", lambda *_a, **_k: report)
    result = runner.invoke(app, ["orchestrate", "tick", *args, "--project-root", str(projet), "--json"])
    assert result.exit_code == 0, result.output
    return seen


def test_cli_tick_qui_fusionne_se_reveille_une_fois(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions.merger import MergerOutcome

    merged = TickReport("ran", PauseState(), merges=(MergerOutcome("T-1", "merge", "done"),))
    assert _tick_cli(projet, monkeypatch, merged, "--apply") == ["fusion"]


def test_cli_tick_vide_ou_en_plan_ne_se_reveille_pas(projet: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _tick_cli(projet, monkeypatch, TickReport("ran", PauseState()), "--apply") == []
    from grimoire.missions.merger import MergerOutcome

    merged = TickReport("ran", PauseState(), merges=(MergerOutcome("T-1", "merge", "done"),), mode="plan")
    assert _tick_cli(projet, monkeypatch, merged) == []


def test_cli_status_montre_le_dernier_reveil(projet: Path) -> None:
    root = ["--project-root", str(projet)]
    before = json.loads(runner.invoke(app, ["orchestrate", "status", "--json", *root]).output)
    assert before["last_wake"] == {}
    request_tick(projet, "reponse_humaine", run=FakeRun(), now=1000.0)
    after = json.loads(runner.invoke(app, ["orchestrate", "status", "--json", *root]).output)
    assert after["last_wake"]["reason"] == "reponse_humaine" and after["last_wake"]["result"] == "started"
    text = runner.invoke(app, ["orchestrate", "status", *root]).output
    assert "réveil :" in text and "réponse humaine" in text
