"""Surface d'écriture des tâches — le scénario complet et ses refus (#137)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.evidence import EvidenceItem, EvidenceKind, EvidenceProfile, EvidenceService
from grimoire.missions.gates import GATES_FILE

runner = CliRunner()

STANDARD = Path("_grimoire/standard")
LEDGER = Path("_grimoire-runtime-output/ledger")
EVIDENCE = Path("_grimoire-runtime-output/evidence")
ACCEPTATION = "le endpoint repond 200"

GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions:
  - id: proposed_to_ready
    from: proposed
    to: ready
    required_evidence: ["acceptance_criteria", "owner_or_agent_role"]
  - id: in_progress_to_review
    from: in_progress
    to: review
    required_evidence: ["evidence_pack"]
  - id: review_to_accepted
    from: review
    to: accepted
    required_evidence: ["review_gate"]
profile_strictness:
  governed: hard_fail
"""


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    (tmp_path / STANDARD).mkdir(parents=True)
    (tmp_path / GATES_FILE).write_text(GATES, encoding="utf-8")
    (tmp_path / STANDARD / "standard-profile.yaml").write_text("profile: governed\n", encoding="utf-8")
    return tmp_path


def run(projet: Path, *args: str):
    return runner.invoke(task_app, [*args, "--project-root", str(projet)])


def ajoute(projet: Path, owner: str = "amelia") -> str:
    res = run(projet, "add", "Ajouter /health", "-a", ACCEPTATION, "--owner", owner)
    assert res.exit_code == 0, res.output
    return next(mot for mot in res.output.split() if mot.startswith("GAO-"))


def prouve(projet: Path, task_id: str, *, couvre: bool = True, verifie: bool = True) -> None:
    svc = EvidenceService(projet / EVIDENCE)
    resume = ACCEPTATION if couvre else "sans rapport"
    pack = svc.create_pack(
        task_id=task_id, profile=EvidenceProfile.STANDARD,
        items=[
            EvidenceItem(id="e1", kind=EvidenceKind.TEST, uri="pytest://", digest="d1", summary=resume),
            EvidenceItem(id="e2", kind=EvidenceKind.LOG, uri="log://", digest="d2", summary=resume),
        ],
        acceptance=(ACCEPTATION,),
    )
    if verifie:
        svc.verify(pack, acceptance=(ACCEPTATION,))


# ── le critère d'acceptation de #137 ────────────────────────────────────────

def test_on_ne_peut_pas_fermer_sans_verdict_accepte(projet: Path) -> None:
    """La garantie centrale : un board entièrement vert doit prouver quelque
    chose. Sans verdict, la fermeture est refusée."""
    tid = ajoute(projet)
    run(projet, "move", tid, "--to", "ready")
    run(projet, "claim", tid)
    run(projet, "move", tid, "--to", "running")
    prouve(projet, tid)
    assert run(projet, "move", tid, "--to", "needs_verification").exit_code == 0

    # Le pack existe, mais rien ne l'a vérifié : on efface les verdicts.
    (projet / EVIDENCE / "verdicts.jsonl").unlink(missing_ok=True)
    res = run(projet, "close", tid)
    assert res.exit_code == 1
    assert "review_gate" in res.output
    assert "aucun verdict" in res.output


def test_scenario_complet_add_claim_move_block_move_close(projet: Path) -> None:
    """add → claim → move → block → move → close, avec un refus à chaque
    gate rouge et un passage quand la preuve est là."""
    tid = ajoute(projet)

    assert run(projet, "move", tid, "--to", "ready").exit_code == 0
    assert run(projet, "claim", tid, "--host", "poste-1").exit_code == 0
    assert run(projet, "move", tid, "--to", "running").exit_code == 0

    # Gate rouge : passer en revue sans pack de preuve.
    refus = run(projet, "move", tid, "--to", "needs_verification")
    assert refus.exit_code == 1
    assert "evidence_pack" in refus.output

    assert run(projet, "block", tid, "--reason", "attente d'un service tiers").exit_code == 0
    assert run(projet, "move", tid, "--to", "ready").exit_code == 0
    assert run(projet, "claim", tid).exit_code == 0
    assert run(projet, "move", tid, "--to", "running").exit_code == 0

    prouve(projet, tid)
    assert run(projet, "move", tid, "--to", "needs_verification").exit_code == 0
    ferme = run(projet, "close", tid)
    assert ferme.exit_code == 0, ferme.output

    etat = run(projet, "list")
    assert "closed" in etat.output and "accepted" in etat.output


# ── refus lisibles ──────────────────────────────────────────────────────────

def test_le_refus_de_gate_nomme_la_preuve_et_le_remede(projet: Path) -> None:
    tid = ajoute(projet)
    run(projet, "move", tid, "--to", "ready")
    run(projet, "claim", tid)
    run(projet, "move", tid, "--to", "running")
    res = run(projet, "move", tid, "--to", "needs_verification")
    assert "aucun pack de preuve" in res.output
    assert "evidence pack" in res.output


def test_un_verdict_echoue_ne_ferme_pas(projet: Path) -> None:
    """Un verdict qui existe mais dit non n'est pas un laissez-passer."""
    tid = ajoute(projet)
    run(projet, "move", tid, "--to", "ready")
    run(projet, "claim", tid)
    run(projet, "move", tid, "--to", "running")
    prouve(projet, tid, couvre=False)
    run(projet, "move", tid, "--to", "needs_verification")
    res = run(projet, "close", tid)
    assert res.exit_code == 1
    assert "failed" in res.output


def test_la_machine_a_etats_refuse_avant_le_gate(projet: Path) -> None:
    """proposed → closed n'est pas concevable ; le message le dit."""
    tid = ajoute(projet)
    res = run(projet, "close", tid)
    assert res.exit_code == 1
    assert "proposed" in res.output and "closed" in res.output


def test_un_etat_inconnu_liste_les_etats_valides(projet: Path) -> None:
    tid = ajoute(projet)
    res = run(projet, "move", tid, "--to", "termine")
    assert res.exit_code == 1
    assert "needs_verification" in res.output


def test_une_tache_inconnue_est_refusee_avant_toute_ecriture(projet: Path) -> None:
    res = run(projet, "close", "GAO-fantome-001")
    assert res.exit_code == 1
    assert "Tâche inconnue" in res.output
    assert not (projet / LEDGER / "events.jsonl").is_file()


def test_une_tache_sans_critere_est_refusee(projet: Path) -> None:
    """Une tâche dont on ne sait pas dire quand elle est finie ne pourra pas
    être vérifiée, donc pas fermée. Le ledger la refuse à l'ouverture."""
    res = runner.invoke(task_app, ["add", "Vague", "--project-root", str(projet)])
    assert res.exit_code != 0


# ── ce que la lecture montre ────────────────────────────────────────────────

def test_show_annonce_ce_que_le_prochain_pas_exigera(projet: Path) -> None:
    tid = ajoute(projet)
    res = run(projet, "show", tid)
    assert "vers ready" in res.output
    assert "acceptance_criteria" in res.output


def test_link_refuse_une_dependance_vers_une_tache_absente(projet: Path) -> None:
    tid = ajoute(projet)
    res = run(projet, "link", tid, "--depends-on", "GAO-fantome-001")
    assert res.exit_code == 1


def test_link_enregistre_la_dependance(projet: Path) -> None:
    a, b = ajoute(projet), ajoute(projet)
    res = run(projet, "link", a, "--depends-on", b)
    assert res.exit_code == 0, res.output
    assert b in res.output


# ── deux défauts trouvés en écrivant le scénario ────────────────────────────

def test_un_titre_a_slash_ne_fabrique_pas_un_identifiant_a_deux_segments(projet: Path) -> None:
    """« Ajouter /health » donnait `GAO-ajouter-/hea-001` : un identifiant que
    le standard refuse, et qui sert pourtant de nom de dossier aux artefacts."""
    from grimoire.core.agentic_standard import normalize_task_id

    tid = ajoute(projet)
    assert "/" not in tid
    assert normalize_task_id(tid) == tid


def test_un_titre_de_traversee_ne_donne_pas_un_chemin_relatif(projet: Path) -> None:
    res = run(projet, "add", "../../etc/passwd", "-a", ACCEPTATION, "--owner", "x")
    assert res.exit_code == 0, res.output
    tid = next(mot for mot in res.output.split() if mot.startswith("GAO-"))
    assert ".." not in tid and "/" not in tid


def test_list_montre_vraiment_l_etat(projet: Path) -> None:
    """Rich prenait les crochets pour une balise de style : la ligne s'affichait
    sans l'état, c'est-à-dire sans l'information qu'on venait chercher."""
    tid = ajoute(projet)
    res = run(projet, "list")
    assert tid in res.output
    assert "proposed" in res.output


def test_context_refuse_une_tache_inconnue_avant_de_calculer(projet: Path) -> None:
    """Le format du context bundle existait déjà, mais il fallait fournir
    l'identifiant à la main : rien ne garantissait qu'il désigne une tâche."""
    res = run(projet, "context", "GAO-fantome-001")
    assert res.exit_code == 1
    assert "Tâche inconnue" in res.output


def test_context_produit_le_bundle_de_la_tache(projet: Path) -> None:
    tid = ajoute(projet)
    res = run(projet, "context", tid)
    assert res.exit_code == 0, res.output
    assert tid in res.output


# ── un fichier de gates cassé n'ouvre pas la porte ──────────────────────────

def test_un_fichier_de_gates_illisible_bloque_la_transition_et_le_dit(projet: Path) -> None:
    tid = ajoute(projet)
    (projet / GATES_FILE).write_text("transitions: [oups", encoding="utf-8")
    res = run(projet, "move", tid, "--to", "ready")
    assert res.exit_code == 1
    assert "evidence-gates.yaml" in res.output
    assert "illisible" in res.output


def test_show_signale_un_fichier_de_gates_illisible_au_lieu_de_se_taire(projet: Path) -> None:
    tid = ajoute(projet)
    (projet / GATES_FILE).write_text("transitions: [oups", encoding="utf-8")
    res = run(projet, "show", tid)
    assert res.exit_code == 0, res.output
    assert "evidence-gates.yaml" in res.output
    assert "illisible" in res.output
