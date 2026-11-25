"""Le routage fort des tâches qui visent des chemins sensibles : l'heuristique et la politique."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.missions.orchestrator_policy import MergerRole, OrchestratorPolicy, WorkerRole, load_orchestrator_policy
from grimoire.missions.schemas import MissionTask, RiskProfile, TaskState, TaskType
from grimoire.missions.sensitive_route import cited_paths, sensitive_patterns, sensitive_route_for

POLICY = Path("_grimoire/standard/orchestration-policy.yaml")


def _task(
    description: str = "",
    *,
    title: str = "tâche",
    acceptance: tuple[str, ...] = (),
    surface: str = "",
    guardrails: tuple[str, ...] = (),
) -> MissionTask:
    return MissionTask(
        id="T-1",
        mission_id="M-1",
        title=title,
        status=TaskState.READY,
        type=TaskType.IMPLEMENTATION,
        risk_profile=RiskProfile.STANDARD,
        acceptance=acceptance,
        created_at="2026-11-19T00:00:00+00:00",
        description=description,
        surface=surface,
        guardrails=guardrails,
    )


def _policy(
    route: str = "strong", *, protected: tuple[str, ...] = (), human: tuple[str, ...] = ()
) -> OrchestratorPolicy:
    return OrchestratorPolicy(
        worker=WorkerRole(sensitive_route=route), merger=MergerRole(protected_paths=protected, human_only_paths=human)
    )


def test_les_chemins_cites_se_lisent_dans_tous_les_champs_de_la_tache() -> None:
    task = _task(
        "Voir `src/grimoire/core/fsio.py`, puis ./docs/guide.md.",
        title="Corriger .mcp.json",
        acceptance=("pytest tests/unit/test_x.py passe",),
        surface="framework/hooks/",
        guardrails=("ne pas toucher pyproject.toml",),
    )
    assert cited_paths(task) == (
        ".mcp.json",
        "src/grimoire/core/fsio.py",
        "docs/guide.md",
        "framework/hooks/",
        "tests/unit/test_x.py",
        "pyproject.toml",
    )


def test_une_url_un_chemin_absolu_ou_un_mot_ne_sont_pas_des_chemins() -> None:
    task = _task("Voir https://github.com/o/r/blob/main/.github/ci.yml et /etc/hosts, ~/x/y, puis le mot e.g. fini.")
    assert cited_paths(task) == ()


def test_les_motifs_reprennent_les_chemins_reserves_sans_seconde_liste() -> None:
    from grimoire.missions.dual_review import HUMAN_ONLY_PATHS
    from grimoire.missions.merger import DEFAULT_PROTECTED_PATHS

    patterns = sensitive_patterns(_policy(protected=("infra/*",), human=("secrets/*",)))
    assert set(DEFAULT_PROTECTED_PATHS) <= set(patterns)
    assert set(HUMAN_ONLY_PATHS) <= set(patterns)
    assert {"infra/*", "secrets/*"} <= set(patterns)


@pytest.mark.parametrize(
    "description",
    [
        "Modifier `.github/workflows/ci.yml`",
        "Le garde src/grimoire/missions/merger.py",
        "Revoir le hook framework/hooks/pre-tool.sh",
        "Mettre à jour pyproject.toml",
        "Corriger launcher.py",  # un nom nu vaut le chemin réservé qui finit par ce nom
        "Ajouter un conftest.py",
    ],
)
def test_une_tache_qui_cite_un_chemin_sensible_part_au_fort(description: str) -> None:
    decision = sensitive_route_for(_task(description), _policy())
    assert decision.strong
    assert decision.paths
    assert "chemins sensibles cités" in decision.reason


def test_une_tache_hors_chemins_sensibles_garde_le_routage_habituel() -> None:
    decision = sensitive_route_for(_task("Corriger docs/guide.md et src/grimoire/cli/cmd_status.py"), _policy())
    assert not decision.strong
    assert decision.route == ""
    assert "aucun sensible" in decision.reason


def test_en_cas_de_doute_aucun_chemin_cite_rien_ne_change() -> None:
    decision = sensitive_route_for(_task("Améliorer la vitesse du tableau de bord"), _policy())
    assert not decision.strong
    assert "aucun chemin cité" in decision.reason


def test_politique_off_rien_ne_change_meme_sur_un_chemin_sensible() -> None:
    decision = sensitive_route_for(_task("Modifier .github/workflows/ci.yml"), _policy("off"))
    assert not decision.strong
    assert decision.reason == "sensitive_route désactivé"


def test_un_motif_de_la_politique_worker_s_ajoute() -> None:
    policy = OrchestratorPolicy(
        worker=WorkerRole(sensitive_route="strong", sensitive_paths=("src/grimoire/policies/*",))
    )
    assert sensitive_route_for(_task("Revoir src/grimoire/policies/engine.py"), policy).strong


def _write(root: Path, worker: str) -> None:
    (root / POLICY).parent.mkdir(parents=True)
    (root / POLICY).write_text(f"orchestrator:\n  roles:\n    worker:\n{worker}", encoding="utf-8")


def test_la_politique_lit_sensitive_route_et_sensitive_paths(tmp_path: Path) -> None:
    _write(tmp_path, '      sensitive_route: strong\n      sensitive_paths: ["infra/*"]\n')
    read = load_orchestrator_policy(tmp_path)
    assert read.policy is not None
    assert read.policy.worker.sensitive_route == "strong"
    assert read.policy.worker.sensitive_paths == ("infra/*",)


def test_par_defaut_sensitive_route_vaut_off(tmp_path: Path) -> None:
    _write(tmp_path, "      tier: cheap\n")
    read = load_orchestrator_policy(tmp_path)
    assert read.policy is not None
    assert read.policy.worker.sensitive_route == "off"
    assert read.policy.worker.sensitive_paths == ()


def test_off_ecrit_en_clair_reste_off(tmp_path: Path) -> None:
    _write(tmp_path, "      sensitive_route: off\n")
    read = load_orchestrator_policy(tmp_path)
    assert read.policy is not None, read.error
    assert read.policy.worker.sensitive_route == "off"


@pytest.mark.parametrize(
    ("worker", "message"),
    [
        ("      sensitive_route: maybe\n", "sensitive_route"),
        ("      sensitive_paths: infra/*\n", "sensitive_paths"),
        ('      sensitive_paths: [""]\n', "sensitive_paths"),
    ],
)
def test_une_valeur_hors_type_rend_la_politique_illisible(tmp_path: Path, worker: str, message: str) -> None:
    _write(tmp_path, worker)
    read = load_orchestrator_policy(tmp_path)
    assert read.policy is None
    assert message in read.error
