"""Règles candidates tirées d'événements observés, et effet mesuré (W6-04).

Les critères de la fiche, opposés tels quels :

- 2 refus sur 2 tâches donnent 1 proposition ; 1 refus n'en donne aucune ;
- le calcul est idempotent ;
- avec n sous le seuil, le statut reste ``unproven`` ;
- le JSON est reproductible.

Le chemin de bout en bout part d'un vrai refus de gate (``TaskService`` sur un
projet ``governed``), pas d'une trace fabriquée : c'est le producteur réel qui
alimente le candidat.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.layered_rules import RuleRefusedError, rules_path
from grimoire.core.learned_rules import (
    GATE_RECIPE,
    RULE_PROPOSALS_DIR,
    Exposure,
    Refusal,
    RuleProposalError,
    accept_rule_proposal,
    candidate_text,
    candidates_from_refusals,
    exposures_from_events,
    fisher_one_sided,
    list_rule_proposals,
    measure_effects,
    propose_rules,
    reject_rule_proposal,
    rule_effects,
)
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskRefusedError, TaskService
from grimoire.missions.trace import TASK_GATE_RECIPE

runner = CliRunner()
REVIEW = "in_progress_to_review"


def refus(task: str, evidence: str = "evidence_pack", at: str = "2026-10-01T10:00:00+00:00", n: int = 1) -> Refusal:
    return Refusal(task, REVIEW, evidence, at, f"TRC-{task}-{n:03d}")


# ── candidats : le seuil compte des tâches distinctes ────────────────────────


def test_deux_refus_sur_deux_taches_donnent_une_proposition() -> None:
    candidates, skipped, below = candidates_from_refusals([refus("T-1"), refus("T-2")])
    assert len(candidates) == 1
    (candidate,) = candidates
    assert candidate.occurrences == 2
    assert candidate.tasks == ("T-1", "T-2")
    assert candidate.citations, "une proposition sans citation de preuve n'est pas un fait observé"
    assert candidate.rule == "ALWAYS provide evidence_pack before in_progress_to_review"
    assert (skipped, below) == ([], 0)


def test_un_refus_ne_donne_aucune_proposition() -> None:
    candidates, _, below = candidates_from_refusals([refus("T-1")])
    assert candidates == []
    assert below == 1


def test_une_tache_coincee_n_est_pas_une_lecon() -> None:
    """Trois refus sur la même tâche : une tâche qui bute, pas deux qui se ressemblent."""
    candidates, _, below = candidates_from_refusals([refus("T-1", n=i) for i in range(1, 4)])
    assert candidates == []
    assert below == 1


def test_le_seuil_ne_descend_jamais_sous_deux() -> None:
    with pytest.raises(ValueError, match="au moins 2"):
        candidates_from_refusals([refus("T-1")], min_tasks=1)


def test_une_preuve_hors_grammaire_est_ecartee_avec_son_motif() -> None:
    candidates, skipped, _ = candidates_from_refusals([refus("T-1", "a b"), refus("T-2", "a b")])
    assert candidates == []
    assert skipped and skipped[0]["evidence"] == "a b" and skipped[0]["reason"]


def test_la_constante_de_recette_suit_la_timeline() -> None:
    assert GATE_RECIPE == TASK_GATE_RECIPE


# ── effet mesuré : pas de verdict sur un petit échantillon ───────────────────


ACCEPTED = "2026-10-10T00:00:00+00:00"


def accepted_proposal() -> dict[str, Any]:
    return {
        "id": "gate-x", "status": "accepted", "rule": candidate_text(REVIEW, "evidence_pack"), "layer": "project",
        "signature": {"transition": REVIEW, "evidence": "evidence_pack"}, "accepted_at": ACCEPTED,
    }


def population(before: tuple[int, int], after: tuple[int, int]) -> tuple[list[Refusal], list[Exposure]]:
    """(refusées, exposées) avant et après l'acceptation."""
    refusals: list[Refusal] = []
    exposures: list[Exposure] = []
    for period, (hits, n), day in (("b", before, "2026-10-05"), ("a", after, "2026-10-15")):
        for i in range(n):
            task, at = f"T-{period}{i}", f"{day}T10:00:{i:02d}+00:00"
            exposures.append(Exposure(task, REVIEW, at))
            if i < hits:
                refusals.append(Refusal(task, REVIEW, "evidence_pack", at, f"TRC-{task}"))
    return refusals, exposures


def test_sous_le_seuil_le_statut_reste_unproven_meme_sur_un_ecart_franc() -> None:
    refusals, exposures = population(before=(10, 10), after=(0, 4))
    (row,) = measure_effects([accepted_proposal()], refusals, exposures, min_tasks=5)["rules"]
    assert (row["n_before"], row["n_after"]) == (10, 4)
    assert row["before_rate"] == 1.0 and row["after_rate"] == 0.0
    assert row["status"] == "unproven"
    assert row["p_reduced"] is None, "aucun test n'est rendu tant que l'échantillon ne le permet pas"


def test_une_baisse_significative_est_effective() -> None:
    refusals, exposures = population(before=(6, 6), after=(0, 6))
    report = measure_effects([accepted_proposal()], refusals, exposures, min_tasks=5)
    (row,) = report["rules"]
    assert row["status"] == "effective"
    assert row["p_reduced"] < 0.05
    assert report["summary"] == {"effective": 1, "unproven": 0, "harmful": 0}


def test_une_hausse_significative_est_harmful_et_proposee_au_retrait() -> None:
    refusals, exposures = population(before=(0, 6), after=(6, 6))
    (row,) = measure_effects([accepted_proposal()], refusals, exposures, min_tasks=5)["rules"]
    assert row["status"] == "harmful"
    assert row["retire_suggested"] is True


def test_un_ecart_non_significatif_reste_unproven_et_finit_propose_au_retrait() -> None:
    refusals, exposures = population(before=(3, 10), after=(2, 20))
    (row,) = measure_effects([accepted_proposal()], refusals, exposures, min_tasks=5, review_after=20)["rules"]
    assert row["status"] == "unproven"
    assert row["retire_suggested"] is True


def test_une_reference_close_sous_le_seuil_ne_propose_pas_le_retrait() -> None:
    # W6-04-S1-01 : 2/2 avant (le seuil d'une proposition), 0/40 après. `n_before`
    # ne peut plus grandir après l'acceptation : sans référence, la règle n'est
    # pas jugée, et surtout pas condamnée alors que le taux a chuté.
    refusals, exposures = population(before=(2, 2), after=(0, 40))
    (row,) = measure_effects([accepted_proposal()], refusals, exposures, min_tasks=5, review_after=20)["rules"]
    assert (row["n_before"], row["n_after"], row["hits_after"]) == (2, 40, 0)
    assert row["status"] == "unproven"
    assert row["retire_suggested"] is False
    assert "non mesurable" in row["reason"]


def test_une_reference_close_ne_propose_pas_le_retrait_meme_si_les_refus_reviennent() -> None:
    refusals, exposures = population(before=(1, 3), after=(30, 40))
    (row,) = measure_effects([accepted_proposal()], refusals, exposures, min_tasks=5, review_after=20)["rules"]
    assert row["status"] == "unproven"
    assert row["retire_suggested"] is False


class _Transitioned:
    """Un ``task.transitioned`` du Mission Ledger, réduit à ce que lit le calcul."""

    def __init__(self, task: str, at: str, ident: str) -> None:
        self.event_type = "task.transitioned"
        self.payload = {"task_id": task, "from_state": "running", "to_state": "needs_verification"}
        self.created_at = at
        self.id = ident
        self.entity_id = task


def test_un_refus_posterieur_ne_gonfle_pas_la_reference_d_avant() -> None:
    # W6-04-S2-01 : 5 tâches passées en avril (NEEDS_VERIFICATION -> RUNNING est
    # permis), refusées en juin sur la signature. Leur première exposition est
    # d'avant l'acceptation, mais leurs refus sont d'après : « avant » ne doit
    # compter que les 2 refus d'avril, pas 7/7.
    accepted = "2026-05-01T00:00:00+00:00"
    refusals = [refus(f"T{i}", at=f"2026-04-0{i}T00:00:00+00:00") for i in (1, 2)]
    events: list[_Transitioned] = []
    for i in range(3, 8):
        events.append(_Transitioned(f"T{i}", f"2026-04-1{i}T00:00:00+00:00", f"e{i}"))
        refusals.append(refus(f"T{i}", at=f"2026-06-1{i}T00:00:00+00:00"))
    for i in range(8, 13):
        events.append(_Transitioned(f"T{i}", f"2026-06-2{i - 8}T00:00:00+00:00", f"e{i}"))
    exposures = exposures_from_events(refusals, events, lambda a, b: REVIEW if (a, b) == ("running", "needs_verification") else "")
    proposal = {**accepted_proposal(), "accepted_at": accepted}
    (row,) = measure_effects([proposal], refusals, exposures)["rules"]
    assert row["hits_before"] == 2
    assert (row["n_before"], row["hits_after"], row["n_after"]) == (7, 5, 10)
    assert row["status"] != "effective", "5 refus sur 10 tâches après l'acceptation ne sont pas un recul"


def test_une_date_d_acceptation_illisible_ne_juge_rien() -> None:
    proposal = {**accepted_proposal(), "accepted_at": "hier"}
    refusals, exposures = population(before=(6, 6), after=(0, 6))
    (row,) = measure_effects([proposal], refusals, exposures)["rules"]
    assert row["status"] == "unproven"
    assert row["before_rate"] is None


def test_le_test_exact_rend_la_valeur_connue() -> None:
    # 5/5 refusées avant, 0/5 après : p = 1 / C(10, 5) = 1/252.
    p_reduced, p_increased = fisher_one_sided(5, 5, 0, 5)
    assert p_reduced == round(1 / 252, 6)
    assert p_increased == 1.0


def test_le_json_d_effet_est_reproductible() -> None:
    refusals, exposures = population(before=(4, 7), after=(1, 9))
    first = json.dumps(measure_effects([accepted_proposal()], refusals, exposures), sort_keys=True)
    second = json.dumps(measure_effects([accepted_proposal()], list(reversed(refusals)), list(reversed(exposures))), sort_keys=True)
    assert first == second


# ── de bout en bout : le vrai producteur de refus ────────────────────────────


@pytest.fixture
def projet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    registry = tmp_path / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8")
    (tmp_path / "project-context.yaml").write_text(
        'project:\n  name: "Demo Apprentissage"\nmemory:\n  backend: "local"\nagents:\n  archetype: "minimal"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def refuse_en_revue(service: TaskService, title: str) -> str:
    """Une tâche qui demande la revue sans pack de preuve : un seul refus, `evidence_pack`."""
    mission = next(iter(service.ledger.list_missions()), None) or service.ledger.create_mission(title="Travaux", origin="test")
    task = service.ledger.create_task(mission.id, title, acceptance=("livré",), owner="amelia")
    service.transition(task.id, TaskState.READY, "amelia")
    service.context(task.id)
    service.claim(task.id, "claude", "claude-code")
    service.transition(task.id, TaskState.RUNNING, "claude")
    decision = service.project_root / "_grimoire-output/decisions" / task.id / "decision-trace.yaml"
    decision.parent.mkdir(parents=True, exist_ok=True)
    decision.write_text("decisions: []\n", encoding="utf-8")
    with pytest.raises(TaskRefusedError):
        service.transition(task.id, TaskState.NEEDS_VERIFICATION, "claude")
    return task.id


def snapshot(root: Path) -> dict[str, str]:
    folder = root / RULE_PROPOSALS_DIR
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.yaml"))} if folder.is_dir() else {}


def test_un_refus_reel_ne_propose_rien(projet: Path) -> None:
    refuse_en_revue(TaskService(projet), "Seule")
    report = propose_rules(projet)
    assert report.created == ()
    assert report.below_threshold == 1
    assert snapshot(projet) == {}


def test_deux_refus_reels_sur_deux_taches_donnent_une_proposition_idempotente(projet: Path) -> None:
    service = TaskService(projet)
    t1, t2 = refuse_en_revue(service, "Première"), refuse_en_revue(service, "Seconde")
    first = propose_rules(projet)
    assert len(first.created) == 1
    (proposal,) = list_rule_proposals(projet)
    assert proposal["status"] == "pending"
    assert proposal["occurrences"] == 2 and proposal["distinct_tasks"] == 2
    assert proposal["tasks"] == sorted([t1, t2])
    assert {c["task_id"] for c in proposal["evidence"]} == {t1, t2}
    assert proposal["signature"] == {"source": GATE_RECIPE, "transition": REVIEW, "evidence": "evidence_pack"}

    before = snapshot(projet)
    second = propose_rules(projet)
    assert (second.created, second.updated, second.unchanged) == ((), (), first.created)
    assert snapshot(projet) == before, "rejouer propose ne réécrit aucun octet"


def test_accepter_ecrit_la_regle_par_la_frontiere_et_fige_la_proposition(projet: Path) -> None:
    service = TaskService(projet)
    refuse_en_revue(service, "Première")
    refuse_en_revue(service, "Seconde")
    (pid,) = propose_rules(projet).created
    record = accept_rule_proposal(projet, pid, project_name="Demo Apprentissage")
    assert record["status"] == "accepted" and record["layer"] == "project" and record["accepted_at"]
    assert rules_path(projet, "project").read_text(encoding="utf-8") == f"{candidate_text(REVIEW, 'evidence_pack')}\n"

    refuse_en_revue(service, "Troisième")
    accepted = snapshot(projet)
    assert propose_rules(projet).unchanged == (pid,)
    assert snapshot(projet) == accepted, "une proposition acceptée garde l'instantané de son acceptation"
    with pytest.raises(RuleProposalError, match="déjà accepted"):
        accept_rule_proposal(projet, pid)


def test_une_acceptation_refusee_n_ecrit_rien(projet: Path) -> None:
    service = TaskService(projet)
    refuse_en_revue(service, "Première")
    refuse_en_revue(service, "Seconde")
    (pid,) = propose_rules(projet).created
    layer = rules_path(projet, "project")
    layer.parent.mkdir(parents=True, exist_ok=True)
    layer.write_text("NEVER provide evidence_pack before in_progress_to_review\n", encoding="utf-8")
    before = snapshot(projet)
    with pytest.raises(RuleRefusedError):
        accept_rule_proposal(projet, pid)
    assert layer.read_text(encoding="utf-8") == "NEVER provide evidence_pack before in_progress_to_review\n"
    assert snapshot(projet) == before


def test_un_rejet_tient_jusqu_a_ce_que_les_taches_doublent(projet: Path) -> None:
    service = TaskService(projet)
    refuse_en_revue(service, "Première")
    refuse_en_revue(service, "Seconde")
    (pid,) = propose_rules(projet).created
    assert reject_rule_proposal(projet, pid)["rejected_at_tasks"] == 2
    refuse_en_revue(service, "Troisième")
    assert propose_rules(projet).unchanged == (pid,)
    refuse_en_revue(service, "Quatrième")
    assert propose_rules(projet).reopened == (pid,)
    assert list_rule_proposals(projet, status="pending")[0]["distinct_tasks"] == 4


def test_l_effet_reel_reste_unproven_sans_recul_et_son_json_est_stable(projet: Path) -> None:
    service = TaskService(projet)
    refuse_en_revue(service, "Première")
    refuse_en_revue(service, "Seconde")
    (pid,) = propose_rules(projet).created
    accept_rule_proposal(projet, pid)
    first, second = rule_effects(projet), rule_effects(projet)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    (row,) = first["rules"]
    assert row["status"] == "unproven"
    assert (row["n_before"], row["hits_before"], row["n_after"]) == (2, 2, 0)


def _proposition_en_attente(projet: Path) -> str:
    service = TaskService(projet)
    refuse_en_revue(service, "Première")
    refuse_en_revue(service, "Seconde")
    (pid,) = propose_rules(projet).created
    return pid


def test_l_acceptation_passe_sous_emitter_enforcement_refuse(projet: Path) -> None:
    # W6-04-S1-02 : l'émetteur d'`accept` devait figurer dans la liste du projet,
    # copiée de `memory-policy.yaml` ; en mode `refuse`, la règle était écartée
    # alors que `rules add` passait pour le même texte.
    policy = projet / "_grimoire/standard/memory-policy.yaml"
    text = policy.read_text(encoding="utf-8")
    assert "emitter_enforcement: observe" in text
    policy.write_text(text.replace("emitter_enforcement: observe", "emitter_enforcement: refuse"), encoding="utf-8")
    pid = _proposition_en_attente(projet)
    record = accept_rule_proposal(projet, pid)
    assert record["status"] == "accepted"
    assert candidate_text(REVIEW, "evidence_pack") in rules_path(projet, "project").read_text(encoding="utf-8")


def test_l_acceptation_ne_journalise_aucun_emetteur_inconnu_en_observation(
    projet: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pid = _proposition_en_attente(projet)
    with caplog.at_level("WARNING"):
        accept_rule_proposal(projet, pid)
    assert "emitter_unrecognized" not in caplog.text


def test_la_cli_propose_et_liste_sans_session_de_flow(projet: Path) -> None:
    service = TaskService(projet)
    refuse_en_revue(service, "Première")
    refuse_en_revue(service, "Seconde")
    result = runner.invoke(app, ["-o", "json", "memory", "rules", "propose"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)["created"]) == 1
    listed = runner.invoke(app, ["-o", "json", "memory", "rules", "proposals", "--pending"])
    assert listed.exit_code == 0, listed.output
    (proposal,) = json.loads(listed.output)["proposals"]
    accepted = runner.invoke(app, ["-o", "json", "memory", "rules", "accept", proposal["id"]])
    assert accepted.exit_code == 0, accepted.output
    effect = runner.invoke(app, ["-o", "json", "memory", "rules", "effect"])
    assert effect.exit_code == 0, effect.output
    assert json.loads(effect.output)["rules"][0]["status"] == "unproven"


def test_la_cli_refuse_un_seuil_sous_deux(projet: Path) -> None:
    result = runner.invoke(app, ["-o", "json", "memory", "rules", "propose", "--min-tasks", "1"])
    assert result.exit_code == 1
    assert json.loads(result.output)["refused"] is True
