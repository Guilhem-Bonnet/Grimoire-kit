"""Double relecture des chemins réservés (``orchestrator.roles.merger.protected_gate: dual_review``).

Critères : le défaut ``human`` escalade comme avant ; en ``dual_review`` une PR qui touche un chemin réservé n'est plus
escaladée mais relue une seconde fois par une session distincte dont la famille de modèle diffère de celle du worker
(famille différente de celle du premier relecteur préférée, sinon autre modèle de sa famille) ; la fusion n'a lieu que
si les deux verdicts sont READY, scellés sur la même tête, et la CI verte ; un second NOT-READY suit le circuit normal ;
la même session pour les deux verdicts ne fusionne jamais ; ``HUMAN_ONLY_PATHS`` reste à l'humain quel que soit le
réglage ; une valeur inconnue rend la politique invalide.

Aucun vrai ``gh`` ni ``claude`` : ``FakeHost`` (lot2_doubles) et un registre de fournisseurs factice, jamais Anthropic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from lot2_doubles import FakeHost, git
from test_merger import (  # les doubles du lot 4, réutilisés tels quels
    DELEGATED,
    give_ready,
    grant,
    isolate_home,
    launch_and_work,
    make_projet,
    merges,
    questions,
    review_ready,
    reviewer_sid,
    tick,
    write_policy,
)

from grimoire.missions.dual_review import (
    BASIS_OTHER_FAMILY,
    BASIS_SAME_FAMILY,
    HUMAN_ONLY_PATHS,
    choose_second_reviewer,
    model_family,
)
from grimoire.missions.merger import (
    MERGE_DUAL_REVIEW_EVENT,
    MERGE_DUAL_REVIEW_REQUESTED_EVENT,
)
from grimoire.missions.orchestrator_policy import load_orchestrator_policy
from grimoire.missions.review_receipt import NOT_READY, ReviewFinding, write_receipt
from grimoire.missions.reviewer import REVIEW_LAUNCHED_EVENT
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.missions.task_worktree import MERGED_EVENT

#: Un chemin réservé par défaut, qu'une relecture peut juger (ce n'est pas un HUMAN_ONLY_PATH).
PROTECTED_EDIT = ("src/grimoire/core/limit.py", "LIMIT = 1\n")
REGISTRY = Path("_grimoire/standard/llm-provider-registry.yaml")


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return isolate_home(tmp_path, monkeypatch)


@pytest.fixture
def projet(tmp_path: Path) -> Path:
    return make_projet(tmp_path)


@pytest.fixture
def host() -> FakeHost:
    from test_merger import REPO

    fake = FakeHost()
    fake.repo = REPO
    return fake


def write_registry(projet: Path, models: tuple[tuple[str, str], ...]) -> None:
    """Un registre factice, qui n'est pas Anthropic : chaque modèle porte sa famille dans son nom."""
    path = projet / REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"      - {{id: {model}, tier: {tier}}}" for model, tier in models)
    path.write_text(
        "providers:\n"
        "  - id: factice\n"
        "    enabled: true\n"
        "    provider_type: mock\n"
        "    models:\n"
        f"{rows}\n",
        encoding="utf-8",
    )


def dual_policy(projet: Path) -> None:
    write_policy(
        projet,
        merger="    merger:\n      enabled: true\n      protected_gate: dual_review\n",
        escalation=DELEGATED,
    )


def review_models(host: FakeHost, tid: str) -> list[str]:
    """Les modèles des relecteurs lancés pour *tid*, dans l'ordre."""
    name = f"grimoire-review-{tid}"
    found: list[str] = []
    for argv, _cwd, _env in host.calls:
        if argv[:2] == ["claude", "--bg"] and "--name" in argv and argv[argv.index("--name") + 1] == name:
            found.append(argv[argv.index("--model") + 1] if "--model" in argv else "")
    return found


def review_launches(projet: Path, tid: str) -> list[Any]:
    return [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == REVIEW_LAUNCHED_EVENT]


def first_ready_then_request(projet: Path, host: FakeHost, tid: str, wt: Path) -> Any:
    """READY, CI verte confirmée, puis la demande de seconde relecture — sans fusion."""
    first = review_ready(projet, host, tid, wt)
    assert merges(first) == [("wait", "waiting")] and "à confirmer" in first.merges[0].detail, first.merges
    report = tick(projet, host)
    assert merges(report) == [("wait", "waiting")], report.merges
    assert "seconde relecture demandée" in report.merges[0].detail
    [request] = [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == MERGE_DUAL_REVIEW_REQUESTED_EVENT]
    return request


def second_review_launched(projet: Path, host: FakeHost, tid: str) -> None:
    report = tick(projet, host)
    assert [(o.action, o.status) for o in report.reviews] == [("launch", "launched")], report.reviews
    assert len(review_launches(projet, tid)) == 2


# ── Le défaut ne change rien ───────────────────────────────────────────────────


def test_human_par_defaut_escalade_un_chemin_reserve(projet: Path, host: FakeHost) -> None:
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=PROTECTED_EDIT)
    first = review_ready(projet, host, tid, wt)
    assert merges(first) == [("wait", "waiting")] and "à confirmer" in first.merges[0].detail, first.merges
    report = tick(projet, host)
    assert merges(report) == [("escalate", "done")] and "reviennent à l'humain" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == []
    assert [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == MERGE_DUAL_REVIEW_REQUESTED_EVENT] == []


def test_valeur_inconnue_du_reglage_rend_la_politique_invalide(projet: Path) -> None:
    write_policy(projet, merger="    merger:\n      enabled: true\n      protected_gate: both\n")
    read = load_orchestrator_policy(projet)
    assert read.policy is None and "protected_gate" in read.error
    write_policy(projet, merger="    merger:\n      enabled: true\n")
    role = load_orchestrator_policy(projet).policy.merger  # type: ignore[union-attr]
    assert role.protected_gate == "human"


# ── Deux READY de sessions distinctes ─────────────────────────────────────────


def test_dual_review_deux_ready_de_sessions_distinctes_fusionne(projet: Path, host: FakeHost) -> None:
    dual_policy(projet)
    write_registry(projet, (("factice-opus-1", "strong"), ("factice-haiku-1", "cheap"), ("factice-sonnet-1", "mid")))
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=PROTECTED_EDIT)
    head = git(wt, "rev-parse", "HEAD")
    request = first_ready_then_request(projet, host, tid, wt)
    # Premier relecteur : opus (l'alternate du worker sonnet) ; la seconde relecture préfère une autre famille.
    assert request.payload["first_family"] == "opus"
    assert request.payload["worker_family"] == "sonnet"
    assert request.payload["model"] == "factice-haiku-1"
    assert request.payload["family_basis"] == BASIS_OTHER_FAMILY
    second_review_launched(projet, host, tid)
    assert review_models(host, tid) == ["opus", "factice-haiku-1"]
    [first, second] = review_launches(projet, tid)
    assert first.payload["session_sha256"] != second.payload["session_sha256"]
    assert second.payload["dual_review"] is True and second.payload["dual_family"] == "haiku"
    assert second.payload["dual_family_basis"] == BASIS_OTHER_FAMILY

    give_ready(host, tid, wt)  # la seconde session rend READY
    report = tick(projet, host)
    assert merges(report) == [("accept", "done")], report.merges
    assert TaskService(projet).require(tid).status is TaskState.CLOSED
    [event] = [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == MERGE_DUAL_REVIEW_EVENT]
    assert event.payload["head"] == head
    assert event.payload["first_family"] == "opus" and event.payload["second_family"] == "haiku"
    assert event.payload["worker_family"] == "sonnet"
    assert event.payload["review"] == first.id and event.payload["second_review"] == second.id
    assert event.payload["judged"] and event.payload["second_judged"]
    assert event.payload["family_basis"] == BASIS_OTHER_FAMILY


def test_sinon_un_autre_modele_de_la_meme_famille_et_le_journal_le_dit(projet: Path, host: FakeHost) -> None:
    dual_policy(projet)
    # Aucune famille différente de celle du premier relecteur (opus), mais deux modèles opus distincts.
    write_registry(projet, (("factice-opus-1", "strong"), ("factice-opus-2", "strong"), ("factice-sonnet-1", "mid")))
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=PROTECTED_EDIT)
    request = first_ready_then_request(projet, host, tid, wt)
    assert request.payload["model"] == "factice-opus-1"
    assert request.payload["family_basis"] == BASIS_SAME_FAMILY
    second_review_launched(projet, host, tid)
    [first, second] = review_launches(projet, tid)
    assert model_family(first.payload["model"]) == model_family(second.payload["model"]) == "opus"
    assert second.payload["dual_family_basis"] == BASIS_SAME_FAMILY
    give_ready(host, tid, wt)
    assert merges(tick(projet, host)) == [("accept", "done")]


def test_choisir_la_seconde_relecture_ne_nomme_aucun_modele_en_dur(tmp_path: Path) -> None:
    root = tmp_path / "projet"
    (root / REGISTRY).parent.mkdir(parents=True)
    (root / REGISTRY).write_text(
        "providers:\n  - id: factice\n    enabled: true\n    models:\n"
        "      - {id: factice-haiku-1, tier: cheap}\n"
        "      - {id: factice-opus-1, tier: strong}\n",
        encoding="utf-8",
    )
    choice, why = choose_second_reviewer(root, first_model="opus", worker_model="sonnet")
    assert why == "" and choice is not None
    assert choice.model == "factice-haiku-1" and choice.basis == BASIS_OTHER_FAMILY
    # Palier imposé : le registre n'a pas de modèle cheap d'une autre famille -> rien n'est choisi.
    choice, why = choose_second_reviewer(root, first_model="opus", worker_model="sonnet", tier="mid")
    assert choice is None and "aucun modèle" in why


def test_sans_modele_au_registre_l_humain_tranche(projet: Path, host: FakeHost) -> None:
    dual_policy(projet)  # aucun registre de fournisseurs : aucune seconde relecture possible
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=PROTECTED_EDIT)
    first = review_ready(projet, host, tid, wt)
    assert merges(first) == [("wait", "waiting")] and "à confirmer" in first.merges[0].detail, first.merges
    report = tick(projet, host)
    assert merges(report) == [("escalate", "done")], report.merges
    assert "registre" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == []
    assert [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == MERGE_DUAL_REVIEW_REQUESTED_EVENT] == []


def test_deux_ready_sur_des_tetes_differentes_ne_fusionnent_pas(projet: Path, host: FakeHost) -> None:
    dual_policy(projet)
    write_registry(projet, (("factice-haiku-1", "cheap"),))
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=PROTECTED_EDIT)
    first_ready_then_request(projet, host, tid, wt)
    # La tête avance après la demande, avant la seconde relecture : le premier sceau ne la couvre plus.
    (wt / "src/grimoire/core/extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
    git(wt, "add", "--", "src/grimoire/core/extra.py")
    git(wt, "commit", "-q", "-m", "tête qui avance")
    git(wt, "push", "-q", "origin", f"task/{tid}")
    second_review_launched(projet, host, tid)
    give_ready(host, tid, wt)
    tick(projet, host)  # CI verte de la nouvelle tête : vue une première fois
    report = tick(projet, host)
    assert merges(report) == [("escalate", "done")], report.merges
    assert "ne couvre pas la tête" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == []
    assert [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == MERGE_DUAL_REVIEW_EVENT] == []


# ── Les verdicts invalides ne fusionnent pas ──────────────────────────────────


def test_un_second_verdict_not_ready_suit_le_circuit_normal_et_ne_fusionne_pas(projet: Path, host: FakeHost) -> None:
    dual_policy(projet)
    write_registry(projet, (("factice-haiku-1", "cheap"),))
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=PROTECTED_EDIT)
    first_ready_then_request(projet, host, tid, wt)
    second_review_launched(projet, host, tid)
    write_receipt(
        wt, tid, NOT_READY, reviewer_id="reviewer", reviewer_session=reviewer_sid(host),
        findings=[ReviewFinding("le garde ne tient pas", "pytest -q tests/unit/x.py")],
    )
    report = tick(projet, host)
    assert merges(report) == []
    assert host.of("gh", "pr", "merge") == [] and TaskService(projet).require(tid).status is TaskState.RUNNING
    assert [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == MERGED_EVENT] == []


def test_meme_session_pour_les_deux_verdicts_est_refusee(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from grimoire.missions import reviewer

    dual_policy(projet)
    write_registry(projet, (("factice-haiku-1", "cheap"),))
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=PROTECTED_EDIT)
    # Pathologie simulée : les deux lancements s'attestent sous la même empreinte de session.
    monkeypatch.setattr(reviewer, "session_digest", lambda _session: "0" * 64)
    first_ready_then_request(projet, host, tid, wt)
    second_review_launched(projet, host, tid)
    give_ready(host, tid, wt)
    report = tick(projet, host)
    assert merges(report) == [("escalate", "done")], report.merges
    assert "même session" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == []
    assert [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == MERGE_DUAL_REVIEW_EVENT] == []
    assert questions(projet, tid)[-1].text.startswith(f"Fusionner la PR #7 de task/{tid}")


def test_un_chemin_human_only_escalade_meme_en_dual_review(projet: Path, host: FakeHost) -> None:
    assert ".mcp.json" in HUMAN_ONLY_PATHS
    dual_policy(projet)
    write_registry(projet, (("factice-haiku-1", "cheap"),))
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=(".mcp.json", "{}\n"))
    first = review_ready(projet, host, tid, wt)
    assert merges(first) == [("wait", "waiting")] and "à confirmer" in first.merges[0].detail, first.merges
    report = tick(projet, host)
    assert merges(report) == [("escalate", "done")], report.merges
    assert "aucune relecture ne peut juger" in report.merges[0].detail
    assert host.of("gh", "pr", "merge") == []
    assert [e for e in TaskService(projet).ledger.events_for(tid) if e.event_type == MERGE_DUAL_REVIEW_REQUESTED_EVENT] == []
    assert len(review_launches(projet, tid)) == 1
