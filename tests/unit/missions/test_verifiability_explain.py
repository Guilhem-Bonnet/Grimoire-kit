"""Le diagnostic du classement : nommer le critère V2, le reformuler, signaler les attentes.

Une tâche qui tombe en V2 ne disait pas *quel* critère l'y fait tomber ; une
commande citée entre accents graves, identique à une ``--check``, n'était pas
reconnue comme verdict ; et un critère qui attend la validation d'une personne
nommée ou une dépense payante n'était signalé nulle part (GAO-crit-res-dir-001).
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.missions.schemas import MissionTask, RiskProfile, TaskCheck, TaskState, TaskType
from grimoire.missions.verifiability import (
    Verifiability,
    as_dict,
    awaits_person_validation,
    classify,
    diagnose,
    requires_paid_spend,
)

runner = CliRunner()


def tache(**kw: object) -> MissionTask:
    base: dict[str, object] = {
        "id": "GAO-demo-001",
        "mission_id": "M-1",
        "title": "Démo",
        "status": TaskState.RUNNING,
        "type": TaskType.IMPLEMENTATION,
        "risk_profile": RiskProfile.STANDARD,
        "acceptance": (),
        "created_at": "2026-08-27T00:00:00Z",
        "checks": (TaskCheck(cmd="true"),),
    }
    base.update(kw)
    return MissionTask(**base)  # type: ignore[arg-type]


def _ajoute(projet: Path, *criteres: str, check: str = "") -> str:
    args = ["add", "Tâche de démonstration"]
    for critere in criteres:
        args += ["-a", critere]
    if check:
        args += ["--check", check]
    args += ["--project-root", str(projet)]
    res = runner.invoke(task_app, args)
    assert res.exit_code == 0, res.output
    return next(mot for mot in res.output.split() if mot.startswith("GAO-"))


# ── 1. nommer le critère qui fait tomber en V2, et proposer sa reformulation ──


def test_as_dict_nomme_le_critere_v2_et_propose_une_reformulation() -> None:
    t = tache(acceptance=("la suite de tests passe", "le code est propre"))
    diagnostic = as_dict(t)

    assert diagnostic["class"] == "V2"
    assert [cause["criterion"] for cause in diagnostic["v2_causes"]] == ["le code est propre"]
    assert "pytest" in diagnostic["v2_causes"][0]["reformulation"]
    # Le détail existant garde son contrat : le critère fautif porte le motif None.
    assert {"criterion": "le code est propre", "pattern": None} in diagnostic["criteria"]


def test_as_dict_ne_signale_aucune_cause_v2_quand_tout_est_mecanique() -> None:
    diagnostic = as_dict(tache(acceptance=("la suite de tests passe",)))
    assert diagnostic["class"] == "V0"
    assert diagnostic["v2_causes"] == []


def test_task_show_nomme_le_critere_qui_fait_tomber_en_v2(tmp_path: Path) -> None:
    tid = _ajoute(tmp_path, "la suite de tests passe", "le code est propre")

    res = runner.invoke(task_app, ["show", tid, "--project-root", str(tmp_path)])

    assert res.exit_code == 0, res.output
    assert "V2" in res.output
    assert "le code est propre" in res.output
    assert "fait tomber en V2" in res.output


def test_task_classify_explain_rend_le_diagnostic_en_texte(tmp_path: Path) -> None:
    tid = _ajoute(tmp_path, "la suite de tests passe", "le code est propre")

    res = runner.invoke(task_app, ["classify", tid, "--explain", "--project-root", str(tmp_path)])

    assert res.exit_code == 0, res.output
    assert "vérifiabilité V2" in res.output
    assert "le code est propre" in res.output
    assert "fait tomber en V2" in res.output


def test_task_classify_explain_rend_le_diagnostic_en_json(tmp_path: Path) -> None:
    tid = _ajoute(tmp_path, "la suite de tests passe", "le code est propre")

    res = runner.invoke(
        task_app,
        ["classify", tid, "--explain", "--project-root", str(tmp_path)],
        obj={"output": "json"},
    )

    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["task_id"] == tid
    assert payload["class"] == "V2"
    assert payload["recommended_model"] is None
    assert payload["v2_causes"] == [
        {
            "criterion": "le code est propre",
            "reformulation": payload["v2_causes"][0]["reformulation"],
        }
    ]
    assert payload["v2_causes"][0]["reformulation"]
    ambigus = [c for c in payload["criteria"] if c["verdict"] == "ambigu"]
    assert [c["criterion"] for c in ambigus] == ["le code est propre"]


# ── 2. une commande citée entre accents graves, identique à une --check ──────


def test_commande_entre_accents_identique_a_une_check_vaut_verdict_mecanique() -> None:
    t = tache(
        acceptance=("`./scripts/verify.sh docs`",),
        checks=(TaskCheck(cmd="./scripts/verify.sh docs"),),
    )
    assert classify(t) is Verifiability.V0


def test_commande_entre_accents_inseree_dans_la_prose_suffit() -> None:
    t = tache(
        acceptance=("la commande `./scripts/verify.sh docs` conclut la tâche",),
        checks=(TaskCheck(cmd="./scripts/verify.sh docs"),),
    )
    assert classify(t) is Verifiability.V0


def test_commande_entre_accents_differente_de_la_check_reste_ambigue() -> None:
    t = tache(
        acceptance=("`./scripts/verify.sh src`",),
        checks=(TaskCheck(cmd="./scripts/verify.sh docs"),),
    )
    assert classify(t) is Verifiability.V2


# ── 4. un critère qui attend une personne est une revue (V1) et un avertissement ──


def test_critere_qui_attend_une_personne_est_v1_et_signale() -> None:
    for critere in (
        "captures validées par Alice",
        "relu par le tech lead",
        "accord de la direction",
        "approuvé par la sécurité",
    ):
        t = tache(acceptance=(critere,))
        assert classify(t) is Verifiability.V1, critere
        [conseil] = diagnose(t)
        assert conseil.verdict == "revue", critere
        assert "validation_personne" in conseil.warnings, critere
        assert "vérification" in conseil.reformulation, critere
        assert awaits_person_validation(critere) == "validation_personne", critere


def test_task_add_signale_l_attente_d_une_personne_sans_refuser(tmp_path: Path) -> None:
    res = runner.invoke(
        task_app,
        [
            "add",
            "Tâche de démonstration",
            "-a",
            "captures validées par Alice",
            "--project-root",
            str(tmp_path),
        ],
    )

    assert res.exit_code == 0, res.output
    assert "vérifiabilité : V1" in res.output
    assert "attente d'une personne" in res.output


# ── 5. une dépense payante est signalée à task add ───────────────────────────


def test_requires_paid_spend_reutilise_la_liste_du_pilote() -> None:
    assert requires_paid_spend("le banc payant doit tourner") == "depense"
    assert requires_paid_spend("appel API facturé une fois") == "depense"
    assert requires_paid_spend("pytest tests/unit passe") is None


def test_task_add_signale_la_depense_payante(tmp_path: Path) -> None:
    res = runner.invoke(
        task_app,
        [
            "add",
            "Tâche de démonstration",
            "-a",
            "le banc payant retourne 0",
            "--project-root",
            str(tmp_path),
        ],
    )

    assert res.exit_code == 0, res.output
    assert "dépense payante" in res.output


def test_diagnose_signale_la_depense_meme_sur_un_critere_mecanique() -> None:
    t = tache(acceptance=("le bench payant : la suite de tests passe",))
    [conseil] = diagnose(t)
    assert conseil.verdict == "mecanique"
    assert "depense_payante" in conseil.warnings
    assert "fixture" in conseil.warning_messages[0]
