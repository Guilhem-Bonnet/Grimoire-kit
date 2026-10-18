"""W5-02 : un worktree éphémère par tâche, un merge série vérifié sur le SHA qui sera fusionné.

Avant : ``grimoire task dispatch`` annonçait « pas de worktree automatique », le
fanout des flows tournait dans le même répertoire, et un agent lancé dans un
worktree de la tâche ouvrait un *second* ledger (la racine relative du ledger se
résolvait contre le dossier courant) — deux journaux, deux vérités.

Les tests construisent un vrai dépôt git (``git init`` dans ``tmp_path``) : les
garanties portent sur ce que git rend, pas sur un double.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_task import task_app
from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import (
    MERGED_EVENT,
    TASK_BRANCH_PREFIX,
    create_task_worktree,
    find_task_worktree,
    merge_task,
)

ACC = "le travail se fusionne vérifié"
OK = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
KO = f'"{sys.executable}" -c "import sys; sys.exit(1)"'


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return done.stdout.strip()


def _commit(cwd: Path, name: str, content: str, message: str) -> str:
    (cwd / name).write_text(content, encoding="utf-8")
    _git(cwd, "add", "--", name)
    _git(cwd, "commit", "-q", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "projet"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    (root / ".gitignore").write_text("_grimoire-runtime-output/\n", encoding="utf-8")
    _git(root, "add", "--", ".gitignore")
    _commit(root, "a.txt", "socle\n", "socle")
    return root


@pytest.fixture
def service(repo: Path) -> TaskService:
    return TaskService(repo)


def _tache(service: TaskService, titre: str) -> str:
    ledger = service.ledger
    missions = ledger.list_missions()
    mission = missions[0].id if missions else ledger.create_mission(title="Plan", origin="test").id
    task = ledger.create_task(mission, titre, acceptance=(ACC,), owner="amelia")
    ledger.transition_task(task.id, TaskState.READY)
    ledger.claim_task(task.id, "amelia", "local")
    ledger.transition_task(task.id, TaskState.RUNNING)
    return task.id


def _rev(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref)


def test_un_worktree_par_tache_sur_une_base_epinglee(repo: Path, service: TaskService) -> None:
    tid = _tache(service, "Enfant A")
    base = _rev(repo, "HEAD")
    wt = create_task_worktree(service, tid, repo=repo)
    _commit(repo, "b.txt", "plus tard\n", "la base bouge")  # la base épinglée ne suit pas

    assert wt.branch == TASK_BRANCH_PREFIX + tid
    assert wt.base_sha == base
    assert _rev(wt.path, "HEAD") == base
    assert _git(wt.path, "rev-parse", "--abbrev-ref", "HEAD") == wt.branch
    assert find_task_worktree(service, tid) == wt
    # Rejouer la création rend le même worktree, sans en ouvrir un second.
    assert create_task_worktree(service, tid, repo=repo) == wt


def test_un_seul_fichier_de_ledger_meme_depuis_le_worktree(repo: Path, service: TaskService) -> None:
    tid = _tache(service, "Enfant A")
    wt = create_task_worktree(service, tid, repo=repo)

    depuis_le_worktree = TaskService(wt.path)  # ce qu'ouvre `grimoire task ...` lancé dans le worktree
    assert depuis_le_worktree.ledger_root == service.ledger_root
    assert depuis_le_worktree.require(tid).status is TaskState.RUNNING
    depuis_le_worktree.comment(tid, "écrit depuis le worktree", "amelia")

    journaux = sorted(p.resolve() for p in repo.parent.rglob("events.jsonl"))
    assert journaux == [(service.ledger_root / "events.jsonl").resolve()]
    assert any("écrit depuis le worktree" in json.dumps(e.payload, ensure_ascii=False) for e in service.ledger.events_for(tid))


def test_deux_enfants_en_conflit_le_second_est_refuse_bloque_et_garde(repo: Path, service: TaskService) -> None:
    a, b = _tache(service, "Enfant A"), _tache(service, "Enfant B")
    wa = create_task_worktree(service, a, repo=repo)
    wb = create_task_worktree(service, b, repo=repo)
    _commit(wa.path, "a.txt", "version A\n", "A")
    _commit(wb.path, "a.txt", "version B\n", "B")

    premier = merge_task(service, a, repo=repo, check=OK)
    assert premier.status == "merged"
    apres_a = _rev(repo, "main")

    second = merge_task(service, b, repo=repo, check=OK)
    assert second.status == "conflict"
    assert second.conflicts == ("a.txt",)
    assert _rev(repo, "main") == apres_a
    assert wb.path.is_dir() and second.worktree_kept
    assert service.require(b).status is TaskState.BLOCKED
    assert "a.txt" in second.reason


def test_un_head_different_du_sha_verifie_bloque_le_merge(repo: Path, service: TaskService) -> None:
    tid = _tache(service, "Enfant A")
    wt = create_task_worktree(service, tid, repo=repo)
    verifie = _commit(wt.path, "c.txt", "relu\n", "relu")
    _commit(wt.path, "c.txt", "ajout après relecture\n", "non relu")
    avant = _rev(repo, "main")

    issue = merge_task(service, tid, repo=repo, check=OK, expected_sha=verifie)
    assert issue.status == "head_moved"
    assert _rev(repo, "main") == avant
    assert wt.path.is_dir()


def test_un_head_qui_bouge_pendant_le_check_bloque_le_merge(repo: Path, service: TaskService, tmp_path: Path) -> None:
    tid = _tache(service, "Enfant A")
    wt = create_task_worktree(service, tid, repo=repo)
    _commit(wt.path, "c.txt", "travail\n", "travail")
    script = tmp_path / "check_qui_commite.py"
    script.write_text(
        "import subprocess, sys\n"
        f"wt = {str(wt.path)!r}\n"
        "subprocess.run(['git', '-C', wt, 'commit', '-q', '--allow-empty', '-m', 'pendant le check'], check=True)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    avant = _rev(repo, "main")

    issue = merge_task(service, tid, repo=repo, check=f'"{sys.executable}" "{script}"')
    assert issue.status == "head_moved"
    assert _rev(repo, "main") == avant


def test_un_merge_rejoue_ne_cree_aucun_commit(repo: Path, service: TaskService) -> None:
    tid = _tache(service, "Enfant A")
    wt = create_task_worktree(service, tid, repo=repo)
    _commit(wt.path, "c.txt", "travail\n", "travail")
    assert merge_task(service, tid, repo=repo, check=OK).status == "merged"
    tete = _rev(repo, "main")
    commits = _git(repo, "rev-list", "--all", "--count")
    fusions = [e for e in service.ledger.events_for(tid) if e.event_type == MERGED_EVENT]

    rejoue = merge_task(service, tid, repo=repo, check=KO)  # rejoué, même un check rouge ne relance rien
    assert rejoue.status == "already_merged"
    assert _rev(repo, "main") == tete
    assert _git(repo, "rev-list", "--all", "--count") == commits
    assert [e for e in service.ledger.events_for(tid) if e.event_type == MERGED_EVENT] == fusions


def test_le_sha_fusionne_est_le_sha_verifie(repo: Path, service: TaskService, tmp_path: Path) -> None:
    tid = _tache(service, "Enfant A")
    wt = create_task_worktree(service, tid, repo=repo)
    _commit(wt.path, "c.txt", "travail\n", "travail")
    _commit(repo, "d.txt", "autre fusion\n", "la cible avance")  # merge non trivial
    script = tmp_path / "check_arbre.py"
    script.write_text(
        "import pathlib, sys\nsys.exit(0 if pathlib.Path('c.txt').is_file() and pathlib.Path('d.txt').is_file() else 3)\n",
        encoding="utf-8",
    )

    issue = merge_task(service, tid, repo=repo, check=f'"{sys.executable}" "{script}"')
    assert issue.status == "merged", issue.reason
    assert issue.merged_sha == _rev(repo, "main") == issue.check["sha"]
    assert issue.check["exit"] == 0
    assert (repo / "c.txt").is_file()  # la cible extraite suit, par fast-forward du commit vérifié
    assert not wt.path.exists()  # éphémère : retiré une fois fusionné
    fusion = [e for e in service.ledger.events_for(tid) if e.event_type == MERGED_EVENT][-1]
    assert fusion.payload["merged_sha"] == issue.merged_sha
    assert fusion.payload["base_sha"] == wt.base_sha


def test_cible_non_extraite_par_compare_and_swap(repo: Path, service: TaskService) -> None:
    _git(repo, "branch", "integration")
    tid = _tache(service, "Enfant A")
    wt = create_task_worktree(service, tid, repo=repo, into="integration")
    _commit(wt.path, "c.txt", "travail\n", "travail")

    issue = merge_task(service, tid, repo=repo, check=OK)
    assert issue.status == "merged"
    assert _rev(repo, "integration") == issue.merged_sha
    assert _rev(repo, "main") != issue.merged_sha


def test_un_check_rouge_ne_fusionne_rien_et_garde_le_worktree(repo: Path, service: TaskService) -> None:
    tid = _tache(service, "Enfant A")
    wt = create_task_worktree(service, tid, repo=repo)
    _commit(wt.path, "c.txt", "travail\n", "travail")
    avant = _rev(repo, "main")

    issue = merge_task(service, tid, repo=repo, check=KO)
    assert issue.status == "check_red"
    assert _rev(repo, "main") == avant
    assert wt.path.is_dir()


def test_sans_commande_de_test_rien_n_est_fusionne(repo: Path, service: TaskService) -> None:
    tid = _tache(service, "Enfant A")
    wt = create_task_worktree(service, tid, repo=repo)
    _commit(wt.path, "c.txt", "travail\n", "travail")
    avant = _rev(repo, "main")

    issue = merge_task(service, tid, repo=repo)  # ni --check ni test-runner déclaré ou détecté
    assert issue.status == "unevaluated"
    assert _rev(repo, "main") == avant


def test_merge_sans_worktree_de_tache_refuse(repo: Path, service: TaskService) -> None:
    tid = _tache(service, "Enfant A")
    with pytest.raises(GrimoireMissionError, match="aucun worktree"):
        merge_task(service, tid, repo=repo, check=OK)


def test_cli_worktree_puis_merge(repo: Path, service: TaskService) -> None:
    tid = _tache(service, "Enfant A")
    runner = CliRunner()
    cree = runner.invoke(task_app, ["worktree", tid, "--project-root", str(repo)])
    assert cree.exit_code == 0, cree.output
    wt = find_task_worktree(TaskService(repo), tid)  # relu : le CLI a écrit par sa propre instance
    assert wt is not None
    _commit(wt.path, "c.txt", "travail\n", "travail")

    fusion = runner.invoke(task_app, ["merge", tid, "--project-root", str(repo), "--check", OK])
    assert fusion.exit_code == 0, fusion.output
    refus = runner.invoke(task_app, ["merge", tid, "--project-root", str(repo), "--check", OK, "--sha", wt.base_sha])
    assert refus.exit_code == 0, refus.output  # déjà fusionnée : le rejeu ne refuse pas et n'écrit rien


# ── Défauts relevés à la revue (W5-02) ───────────────────────────────────────

_GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
states: [proposed, ready, in_progress, blocked, review, accepted, released, archived]
transitions:
  - id: in_progress_to_review
    from: in_progress
    to: review
    required_evidence: ["evidence_pack"]
profile_strictness:
  starter: advisory
  governed: hard_fail
"""


@pytest.mark.parametrize("suivi", [True, False], ids=["gates-suivis-puis-supprimes", "gates-non-suivis"])
def test_le_gate_d_un_worktree_lit_les_declarations_du_projet(repo: Path, service: TaskService, suivi: bool) -> None:
    """Le worktree écrit au ledger du projet : un fichier de gates absent de SON arbre ne désarme pas le gate."""
    from grimoire.missions.service import TaskRefusedError

    standard = repo / "_grimoire" / "standard"
    standard.mkdir(parents=True)
    (standard / "evidence-gates.yaml").write_text(_GATES, encoding="utf-8")
    (standard / "standard-profile.yaml").write_text("profile: governed\n", encoding="utf-8")
    if suivi:
        _git(repo, "add", "--", "_grimoire/standard")
        _git(repo, "commit", "-q", "-m", "gates")
    tid = _tache(service, "Enfant A")
    wt = create_task_worktree(service, tid, repo=repo)
    for nom in ("evidence-gates.yaml", "standard-profile.yaml"):
        (wt.path / "_grimoire" / "standard" / nom).unlink(missing_ok=True)  # absent de l'arbre de l'agent

    depuis_le_projet = service.gate(service.require(tid), TaskState.NEEDS_VERIFICATION)
    depuis_le_worktree = TaskService(wt.path)
    assert depuis_le_worktree.ledger_root == service.ledger_root  # même journal, donc mêmes déclarations
    verdict = depuis_le_worktree.gate(depuis_le_worktree.require(tid), TaskState.NEEDS_VERIFICATION)

    assert depuis_le_projet.blocked
    assert verdict.blocked, "le gate du worktree doit refuser comme celui du projet"
    assert [r.evidence for r in verdict.refusals] == [r.evidence for r in depuis_le_projet.refusals] == ["evidence_pack"]
    avant = len(service.ledger.events_for(tid))
    with pytest.raises(TaskRefusedError):
        depuis_le_worktree.transition(tid, TaskState.NEEDS_VERIFICATION, "amelia", "fini")
    assert service.require(tid).status is TaskState.RUNNING
    assert not [e for e in service.ledger.events_for(tid)[avant:] if e.event_type == "task.transitioned"]


def test_une_branche_restee_sur_sa_base_n_est_pas_deja_fusionnee(repo: Path, service: TaskService) -> None:
    tid = _tache(service, "Enfant A")
    create_task_worktree(service, tid, repo=repo)  # aucun commit
    avant = _rev(repo, "main")

    issue = merge_task(service, tid, repo=repo, check=KO)
    assert issue.status == "nothing_to_merge"
    assert not issue.ok
    assert issue.merged_sha == ""
    assert _rev(repo, "main") == avant
    assert not [e for e in service.ledger.events_for(tid) if e.event_type == MERGED_EVENT]

    runner = CliRunner()
    cli = runner.invoke(task_app, ["merge", tid, "--project-root", str(repo), "--check", OK])
    assert cli.exit_code == 1, cli.output
    assert "nothing_to_merge" in cli.output


def test_contenue_dans_la_cible_sans_recu_au_ledger_n_est_pas_attestee(repo: Path, service: TaskService) -> None:
    tid = _tache(service, "Enfant A")
    wt = create_task_worktree(service, tid, repo=repo)
    _commit(wt.path, "c.txt", "travail\n", "travail")
    _git(repo, "merge", "--ff-only", "--quiet", wt.branch)  # fusionnée à la main, hors du chemin vérifié

    issue = merge_task(service, tid, repo=repo, check=OK)
    assert issue.status == "unrecorded"
    assert not issue.ok
    assert not [e for e in service.ledger.events_for(tid) if e.event_type == MERGED_EVENT]
