"""``grimoire task dispatch`` — cascade par classe de vérifiabilité (issue #323).

Les fournisseurs sont de vrais scripts Python locaux exécutés par de vrais
``subprocess`` — pas de mock : c'est le seul moyen d'observer la même chose
qu'un exécuteur headless réel (code de sortie, sortie standard, timeout), et
c'est le critère d'arrêt de l'issue qui l'exige (« Prouvé par tests avec des
fournisseurs factices »).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from grimoire.missions.dispatch import run_dispatch
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.providers.state import load_state

STANDARD = Path("_grimoire/standard")
REGISTRY = STANDARD / "llm-provider-registry.yaml"
LEDGER = Path("_grimoire-runtime-output/ledger")

V0_CRITERION = "la suite de tests passe"
V1_CRITERION = "revue humaine avant fusion"
V2_CRITERION = "le code est propre"

# ── Fournisseurs factices ────────────────────────────────────────────────────
# Chacun ignore ses arguments (le prompt, `--model <id>`) : seul compte ce
# qu'il fait au process — écrire le fichier que le `--check` attend, échouer,
# ou décrire une limite dans sa sortie.

_WRITE_MARKER = """\
    from pathlib import Path
    Path("marker.txt").write_text("done", encoding="utf-8")
    """

_SILENT_OK = "pass\n"

_FAIL = """\
    import sys
    sys.exit(1)
    """

_RATE_LIMITED = 'print("429 rate limit exceeded")\n'


def _script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(body), encoding="utf-8")
    return path


def _invocation(script: Path) -> str:
    return f"{sys.executable} {script} {{prompt}} --model {{model}}"


def _provider_yaml(pid: str, tier: str, invocation: str | None) -> str:
    inv_line = f'    invocation: "{invocation}"\n' if invocation else ""
    return (
        f'  - id: "{pid}"\n'
        f"    enabled: true\n"
        f'    provider_type: "hosted"\n'
        f'    allowed_capabilities: ["chat", "code"]\n'
        f'    default_models: ["{pid}-model"]\n'
        f'    currency: "api"\n'
        f"{inv_line}"
        f"    models:\n"
        f'      - id: "{pid}-model"\n'
        f'        tier: "{tier}"\n'
        f"    fallback_order: []\n"
    )


def _write_registry(root: Path, *providers_yaml: str) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    body = "\n".join(providers_yaml)
    (root / REGISTRY).write_text(
        f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
{body}
routing:
  default_provider: ""
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
""",
        encoding="utf-8",
    )


def _git_repo(root: Path, *tracked: Path) -> None:
    """Un dépôt git minimal, un commit initial portant les fichiers déjà suivis.

    Nécessaire pour tout test de la classe de relisibilité (#327) : elle lit
    ``git diff --name-only``, qui ne voit un fichier que s'il était déjà suivi
    avant que le fournisseur factice ne le modifie.
    """
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    for path in tracked:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)


def _service(tmp_path: Path) -> TaskService:
    return TaskService(tmp_path, LEDGER)


def _task(service: TaskService, acceptance: tuple[str, ...], *, owner: str = "amelia") -> str:
    ledger = service.ledger
    mission = ledger.create_mission("Démo dispatch", origin="test")
    task = ledger.create_task(mission.id, "Tâche déléguée", acceptance=acceptance, owner=owner)
    return task.id


def _run_task(service: TaskService, task_id: str, actor: str = "amelia") -> None:
    """Amène la tâche à `running` — nécessaire pour la transition V1."""
    service.transition(task_id, TaskState.READY, actor)
    service.claim(task_id, actor)
    service.transition(task_id, TaskState.RUNNING, actor)


# ── Refus avant tout appel ───────────────────────────────────────────────────


def test_v2_est_refuse_avant_tout_calcul(tmp_path: Path) -> None:
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V2_CRITERION,))

    report = run_dispatch(service, tid, checks=("true",))

    assert report.exit_code == 2
    assert report.refusal == "v2"
    assert report.attempts == ()


def test_v0_sans_check_est_refuse(tmp_path: Path) -> None:
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=())

    assert report.exit_code == 2
    assert report.refusal == "no_check"


def test_max_tier_sous_le_plancher_de_la_classe_est_refuse(tmp_path: Path) -> None:
    """V1 exige `mid` au minimum : `--max-tier cheap` ne laisse aucun palier."""
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V1_CRITERION,))

    report = run_dispatch(service, tid, checks=("true",), max_tier="cheap")

    assert report.exit_code == 2
    assert report.refusal == "no_tier"


def test_aucun_fournisseur_invocable_est_un_refus_pas_une_chaine_epuisee(tmp_path: Path) -> None:
    service = _service(tmp_path)  # pas de registre du tout
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("true",))

    assert report.exit_code == 2
    assert report.refusal == "no_provider"


def test_fournisseur_sans_invocation_n_est_jamais_candidat(tmp_path: Path) -> None:
    _write_registry(tmp_path, _provider_yaml("muet", "cheap", None))
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("true",))

    assert report.exit_code == 2
    assert report.refusal == "no_provider"


# ── Le cœur de la cascade ────────────────────────────────────────────────────


def test_check_rouge_passe_au_palier_suivant_et_echec_d_appel_au_fournisseur_suivant(tmp_path: Path) -> None:
    """cheap (seul, check rouge) → mid : le premier échoue à l'appel, le
    second réussit et passe au vert. C'est le critère d'arrêt de l'issue."""
    cheap_red = _script(tmp_path, "cheap_red.py", _SILENT_OK)
    mid_fail = _script(tmp_path, "mid_fail.py", _FAIL)
    mid_green = _script(tmp_path, "mid_green.py", _WRITE_MARKER)
    _write_registry(
        tmp_path,
        _provider_yaml("cheap-red", "cheap", _invocation(cheap_red)),
        _provider_yaml("mid-fail", "mid", _invocation(mid_fail)),
        _provider_yaml("mid-green", "mid", _invocation(mid_green)),
    )
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert report.exit_code == 0
    assert [a.provider for a in report.attempts] == ["cheap-red", "mid-fail", "mid-green"]
    # mid-fail plante sans parler de limite : c'est une panne locale, pas une saturation.
    assert [a.verdict for a in report.attempts] == ["red", "error", "green"]
    assert report.attempts[0].tier == "cheap"
    assert report.attempts[-1].tier == "mid"
    assert report.attempts[0].checks[0].ok is False
    assert report.attempts[-1].checks[0].ok is True
    # V0 : aucune transition automatique — le report ne considère la tâche
    # close qu'en apparence, le ledger ne la ferme pas ici.
    assert report.transitioned_to is None
    assert service.require(tid).status is TaskState.PROPOSED


def test_sortie_429_est_traitee_comme_un_echec_d_appel_avec_refroidissement(tmp_path: Path) -> None:
    limited = _script(tmp_path, "limited.py", _RATE_LIMITED)
    green = _script(tmp_path, "green.py", _WRITE_MARKER)
    _write_registry(
        tmp_path,
        _provider_yaml("limited", "cheap", _invocation(limited)),
        _provider_yaml("green", "cheap", _invocation(green)),
    )
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert report.exit_code == 0
    assert [a.verdict for a in report.attempts] == ["rate_limit", "green"]
    assert [a.provider for a in report.attempts] == ["limited", "green"]

    state = load_state(tmp_path)
    assert "limited" in state
    assert state["limited"].failure_count == 1
    assert state["limited"].last_failure == "rate_limit"


def test_chaine_epuisee_ne_transitionne_rien_et_sort_en_1(tmp_path: Path) -> None:
    always_red = _script(tmp_path, "always_red.py", _SILENT_OK)
    _write_registry(tmp_path, _provider_yaml("seul", "cheap", _invocation(always_red)))
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("test -f marker.txt",), max_tier="cheap")

    assert report.exit_code == 1
    assert not report.succeeded
    assert [a.verdict for a in report.attempts] == ["red"]
    assert service.require(tid).status is TaskState.PROPOSED


# ── V1 : vert veut dire « à vérifier », pas « fermé » ────────────────────────


def test_v1_vert_passe_en_needs_verification_au_lieu_d_etre_close(tmp_path: Path) -> None:
    green = _script(tmp_path, "green.py", _WRITE_MARKER)
    _write_registry(tmp_path, _provider_yaml("mid-only", "mid", _invocation(green)))
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V1_CRITERION,))
    _run_task(service, tid)

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert report.exit_code == 0
    assert report.succeeded
    assert report.transitioned_to == TaskState.NEEDS_VERIFICATION.value
    assert service.require(tid).status is TaskState.NEEDS_VERIFICATION
    # Le premier palier tenté est bien `mid`, jamais `cheap` — le jugement
    # humain que V1 réclame justifie un modèle plus capable dès le premier essai.
    assert report.attempts[0].tier == "mid"


def test_v1_rouge_ne_transitionne_pas(tmp_path: Path) -> None:
    red = _script(tmp_path, "red.py", _SILENT_OK)
    _write_registry(tmp_path, _provider_yaml("mid-only", "mid", _invocation(red)))
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V1_CRITERION,))
    _run_task(service, tid)

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert report.exit_code == 1
    assert report.transitioned_to is None
    assert service.require(tid).status is TaskState.RUNNING


# ── L'historique que le lot 4 lira ───────────────────────────────────────────


def test_chaque_tentative_laisse_un_evenement_task_dispatched(tmp_path: Path) -> None:
    cheap_red = _script(tmp_path, "cheap_red.py", _SILENT_OK)
    mid_green = _script(tmp_path, "mid_green.py", _WRITE_MARKER)
    _write_registry(
        tmp_path,
        _provider_yaml("cheap-red", "cheap", _invocation(cheap_red)),
        _provider_yaml("mid-green", "mid", _invocation(mid_green)),
    )
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    events = [e for e in service.ledger.list_events(tid) if e.event_type == "task.dispatched"]
    assert len(events) == len(report.attempts) == 2
    for event, attempt in zip(events, report.attempts, strict=True):
        payload = event.payload
        assert payload["task_id"] == tid
        assert payload["attempt"] == attempt.attempt
        assert payload["tier"] == attempt.tier
        assert payload["provider"] == attempt.provider
        assert payload["model"] == attempt.model
        assert payload["verdict"] == attempt.verdict
        assert payload["checks"] == [c.to_dict() for c in attempt.checks]
        assert "cost_usd" in payload
        assert "exit_code" in payload
        assert "duration_s" in payload


def test_cout_est_lu_dans_une_sortie_json_avec_total_cost_usd(tmp_path: Path) -> None:
    payant = _script(
        tmp_path,
        "payant.py",
        """\
        import json
        from pathlib import Path
        Path("marker.txt").write_text("done", encoding="utf-8")
        print(json.dumps({"total_cost_usd": 0.042}))
        """,
    )
    _write_registry(tmp_path, _provider_yaml("payant", "cheap", _invocation(payant)))
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert report.attempts[0].cost_usd == 0.042


# ── Dry-run : rien n'est appelé ──────────────────────────────────────────────


def test_dry_run_montre_la_classe_la_chaine_et_le_prompt_sans_rien_appeler(tmp_path: Path) -> None:
    _write_registry(tmp_path, _provider_yaml("jamais-appele", "cheap", "false {prompt} {model}"))
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("true",), dry_run=True)

    assert report.exit_code == 0
    assert report.dry_run is True
    assert report.planned_chain == ("cheap", "mid", "strong")
    assert V0_CRITERION in report.prompt
    assert "Ne modifie pas" in report.prompt
    assert report.attempts == ()
    assert [e for e in service.ledger.list_events(tid) if e.event_type == "task.dispatched"] == []
    assert load_state(tmp_path) == {}


# ── Classe de relisibilité (issue #327) ──────────────────────────────────────


def test_diff_touchant_le_cli_est_review_required_avec_le_fichier_nomme(tmp_path: Path) -> None:
    """Critère d'arrêt de l'issue : un diff touchant `src/grimoire/cli/x.py` est `required`."""
    cible = tmp_path / "src" / "grimoire" / "cli" / "x.py"
    _git_repo(tmp_path, cible)
    modifie = _script(
        tmp_path,
        "modifie_cli.py",
        """\
        from pathlib import Path
        Path("src/grimoire/cli/x.py").write_text("changed\\n", encoding="utf-8")
        Path("marker.txt").write_text("done", encoding="utf-8")
        """,
    )
    _write_registry(tmp_path, _provider_yaml("worker", "cheap", _invocation(modifie)))
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert report.exit_code == 0
    assert report.review == "review_required"
    assert "src/grimoire/cli/x.py" in report.review_files
    assert report.review_note is None


def test_diff_limite_a_tests_est_review_optional(tmp_path: Path) -> None:
    """Critère d'arrêt de l'issue : un diff limité à `tests/` est `optional`."""
    cible = tmp_path / "tests" / "test_x.py"
    _git_repo(tmp_path, cible)
    modifie = _script(
        tmp_path,
        "modifie_tests.py",
        """\
        from pathlib import Path
        Path("tests/test_x.py").write_text("changed\\n", encoding="utf-8")
        Path("marker.txt").write_text("done", encoding="utf-8")
        """,
    )
    _write_registry(tmp_path, _provider_yaml("worker", "cheap", _invocation(modifie)))
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert report.exit_code == 0
    assert report.review == "review_optional"
    assert report.review_files == ()


def test_review_surfaces_surcharge_yaml_est_respectee(tmp_path: Path) -> None:
    """Critère d'arrêt de l'issue : la surcharge YAML est respectée.

    `review_surfaces` remplace la liste par défaut : un diff qui ne touche que
    `docs/` (jamais sensible par défaut) devient `required` sous la
    surcharge, et un diff CLI (sensible par défaut) redevient `optional` —
    remplacer, pas fusionner.
    """
    cible = tmp_path / "docs" / "guide.md"
    _git_repo(tmp_path, cible)
    (tmp_path / "_grimoire" / "standard").mkdir(parents=True, exist_ok=True)
    (tmp_path / "_grimoire" / "standard" / "orchestration-policy.yaml").write_text(
        'review_surfaces:\n  - "docs/**"\n', encoding="utf-8"
    )
    modifie = _script(
        tmp_path,
        "modifie_docs.py",
        """\
        from pathlib import Path
        Path("docs/guide.md").write_text("changed\\n", encoding="utf-8")
        Path("marker.txt").write_text("done", encoding="utf-8")
        """,
    )
    _write_registry(tmp_path, _provider_yaml("worker", "cheap", _invocation(modifie)))
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert report.review == "review_required"
    assert "docs/guide.md" in report.review_files


def test_projet_sans_depot_git_reste_review_optional_avec_une_note(tmp_path: Path) -> None:
    """Sans dépôt git, le diff n'est pas calculable : `optional`, jamais un `required` inventé."""
    green = _script(tmp_path, "green.py", _WRITE_MARKER)
    _write_registry(tmp_path, _provider_yaml("worker", "cheap", _invocation(green)))
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert report.review == "review_optional"
    assert report.review_files == ()
    assert report.review_note is not None


def test_chaine_epuisee_ne_porte_aucune_relecture(tmp_path: Path) -> None:
    """La relecture ne se pose qu'après un dispatch vert — un rouge n'a rien à faire relire."""
    always_red = _script(tmp_path, "always_red.py", _SILENT_OK)
    _write_registry(tmp_path, _provider_yaml("seul", "cheap", _invocation(always_red)))
    service = _service(tmp_path)
    tid = _task(service, acceptance=(V0_CRITERION,))

    report = run_dispatch(service, tid, checks=("test -f marker.txt",), max_tier="cheap")

    assert not report.succeeded
    assert report.review is None
    assert report.review_files == ()
