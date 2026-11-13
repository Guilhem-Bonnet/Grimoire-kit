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

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
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
from grimoire.missions.mandate import MandateStore
from grimoire.missions.merger import (
    MERGE_DUAL_REVIEW_EVENT,
    MERGE_DUAL_REVIEW_REQUESTED_EVENT,
)
from grimoire.missions.orchestrator_policy import load_orchestrator_policy
from grimoire.missions.review_receipt import (
    NOT_READY,
    ReviewFinding,
    receipt_fingerprint,
    write_receipt,
)
from grimoire.missions.review_seal import (
    REALIGN_ENTRY,
    SEAL_ENTRY,
    covering_reviews,
    receipt_digest,
    tree_of,
    verify_review,
)
from grimoire.missions.reviewer import REVIEW_LAUNCH_FAILED_EVENT, REVIEW_LAUNCHED_EVENT
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


# ── Choix du second relecteur : registre dégradé, familles ────────────────────


def _write_raw_registry(root: Path, body: str) -> None:
    path = root / REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_registre_illisible_ne_choisit_rien(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.missions import dual_review as module
    from grimoire.providers.registry import ProviderRegistryError

    def boom(_root: Path) -> tuple[Any, ...]:
        raise ProviderRegistryError("registre illisible")

    monkeypatch.setattr(module, "read_registry", boom)
    choice, why = choose_second_reviewer(tmp_path, first_model="opus", worker_model="sonnet")
    assert choice is None and "aucun modèle" in why


def test_un_fournisseur_desactive_n_est_pas_candidat(tmp_path: Path) -> None:
    root = tmp_path / "projet"
    _write_raw_registry(
        root,
        "providers:\n"
        "  - id: eteint\n    enabled: false\n    models:\n      - {id: factice-opus-1, tier: strong}\n"
        "  - id: allume\n    enabled: true\n    models:\n      - {id: factice-haiku-1, tier: cheap}\n",
    )
    choice, why = choose_second_reviewer(root, first_model="opus", worker_model="sonnet")
    assert why == "" and choice is not None and choice.model == "factice-haiku-1"


def test_famille_inconnue_ou_aucun_candidat_ne_choisit_rien(tmp_path: Path) -> None:
    root = tmp_path / "projet"
    # Famille du worker inconnue : l'indépendance des deux relectures n'est pas prouvable.
    choice, why = choose_second_reviewer(root, first_model="opus", worker_model="")
    assert choice is None and "famille du worker" in why
    # Le registre n'offre que la famille du worker : aucun candidat admissible.
    _write_raw_registry(
        root,
        "providers:\n  - id: factice\n    enabled: true\n    models:\n"
        "      - {id: factice-sonnet-1, tier: mid}\n",
    )
    choice, why = choose_second_reviewer(root, first_model="opus", worker_model="sonnet")
    assert choice is None and "aucun modèle d'une famille différente" in why
    # Une seule famille, celle du premier relecteur, et aucun autre modèle : rien à préférer.
    _write_raw_registry(
        root,
        "providers:\n  - id: factice\n    enabled: true\n    models:\n"
        "      - {id: factice-opus-1, tier: strong}\n",
    )
    choice, why = choose_second_reviewer(root, first_model="factice-opus-1", worker_model="sonnet")
    assert choice is None and "ni autre modèle de cette famille" in why


# ── Le sceau couvre-t-il chaque tête de la double relecture ? ─────────────────


def _commit(projet: Path, message: str) -> str:
    git(projet, "commit", "--allow-empty", "-q", "-m", message)
    return git(projet, "rev-parse", "HEAD")


def _seal(store: MandateStore, tid: str, review: str, **fields: Any) -> None:
    store.journal.append({
        "type": SEAL_ENTRY, "project": store.project, "task_id": tid, "review": review,
        "ts": datetime.now(UTC).isoformat(), **fields,
    })


def _realign(store: MandateStore, tid: str, review: str, source: str, target: str, tree: str = "") -> None:
    store.journal.append({
        "type": REALIGN_ENTRY, "project": store.project, "task_id": tid, "review": review,
        "from": source, "to": target, "tree": tree, "ts": datetime.now(UTC).isoformat(),
    })


def test_covering_reviews_suit_la_chaine_des_realignements(projet: Path) -> None:
    tid = "GAO-cover-001"
    h1, h2, h3 = (_commit(projet, f"tête {n}") for n in (1, 2, 3))
    store = MandateStore(projet)
    _seal(store, tid, "sans-tete", head="")  # un sceau sans tête ne couvre rien
    _seal(store, tid, "r1", head=h1, tree=tree_of(projet, h1))
    _realign(store, tid, "r1", h1, h2, tree_of(projet, h2))
    _realign(store, tid, "r1", h2, h3, tree_of(projet, h3))
    # Ignorés : le réalignement d'une autre relecture, et un saut qui ne part pas d'une tête couverte.
    _realign(store, tid, "autre", h1, h2)
    _realign(store, tid, "r1", "f" * 40, h3)
    # Une relecture vide n'est jamais retenue ; la même relecture ne revient qu'une fois.
    _seal(store, tid, "", head=h2, tree=tree_of(projet, h2))
    _seal(store, tid, "r1", head=h2, tree=tree_of(projet, h2))
    assert covering_reviews(projet, tid, h1) == ("r1",)
    assert covering_reviews(projet, tid, h2) == ("r1",)  # le sceau de relecture vide est ignoré, le doublon fondu
    assert covering_reviews(projet, tid, h3) == ("r1",)
    assert covering_reviews(projet, tid, "") == ()


def test_covering_reviews_ferme_si_le_journal_est_illisible(
    projet: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from grimoire.missions import review_seal

    def boom(_root: Path, _tid: str) -> list[dict[str, Any]]:
        raise RuntimeError("journal rompu")

    monkeypatch.setattr(review_seal, "_mine", boom)
    assert covering_reviews(projet, "GAO-cover-002", "a" * 40) == ()


def test_verify_review_refuse_chaque_ecart_de_sceau(projet: Path, tmp_path: Path) -> None:
    tid = "GAO-verif-001"
    task = SimpleNamespace(id=tid)
    proofs = tmp_path / "preuves"
    proofs.mkdir()
    write_receipt(proofs, tid, "READY", reviewer_id="reviewer", reviewer_session="session-1")
    digest = receipt_digest(proofs, tid)
    h1 = _commit(projet, "tête relue")
    store = MandateStore(projet)

    assert "aucun jugement" in verify_review(projet, task, proofs, "absente", head=h1)

    _seal(store, tid, "recu", head=h1, tree=tree_of(projet, h1), receipt_sha256="autre", evidence_pack_sha256="")
    assert "reçu réécrit" in verify_review(projet, task, proofs, "recu", head=h1)

    _seal(store, tid, "pack", head=h1, tree=tree_of(projet, h1), receipt_sha256=digest, evidence_pack_sha256="deadbeef")
    assert "pack de preuve" in verify_review(projet, task, proofs, "pack", head=h1)
    # Sans vérifier le reçu, le sceau de tête suffit : c'est ce que fait la fusion du premier verdict.
    assert verify_review(projet, task, proofs, "pack", head=h1, check_receipt=False) == ""

    _seal(store, tid, "sans-tete", head="", receipt_sha256=digest, evidence_pack_sha256="")
    assert "aucune tête" in verify_review(projet, task, proofs, "sans-tete", head=h1)

    # Dépôt sans tête (sceau de contenu) : l'empreinte du reçu doit être celle scellée.
    fingerprint = receipt_fingerprint(proofs)
    _seal(store, tid, "contenu", head="", tree_fingerprint=fingerprint, receipt_sha256=digest, evidence_pack_sha256="")
    assert verify_review(projet, task, proofs, "contenu", check_receipt=False) == ""
    _seal(store, tid, "contenu-faux", head="", tree_fingerprint="faux", receipt_sha256=digest, evidence_pack_sha256="")
    assert "arbre a changé" in verify_review(projet, task, proofs, "contenu-faux", check_receipt=False)


def test_verify_review_ferme_si_le_journal_est_illisible(
    projet: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from grimoire.missions import review_seal

    def boom(_root: Path, _tid: str) -> list[dict[str, Any]]:
        raise RuntimeError("journal rompu")

    monkeypatch.setattr(review_seal, "_mine", boom)
    why = verify_review(projet, SimpleNamespace(id="GAO-verif-002"), tmp_path, "r1", head="a" * 40)
    assert "journal d'autorité illisible" in why


# ── Le relecteur ne lance pas la seconde relecture à l'aveugle ────────────────


def _failed_launch(projet: Path, tid: str, *, session: str, reason: str, halted: bool) -> None:
    TaskService(projet).ledger.append_event(
        REVIEW_LAUNCH_FAILED_EVENT, tid, "task", "reviewer",
        {"task_id": tid, "session": session, "cwd": ".", "halted": halted, "reason": reason, "detail": "test"},
    )


def _dual_ready(projet: Path, host: FakeHost) -> tuple[str, Path]:
    dual_policy(projet)
    write_registry(projet, (("factice-haiku-1", "cheap"),))
    grant(projet)
    tid, wt = launch_and_work(projet, host, edit=PROTECTED_EDIT)
    first_ready_then_request(projet, host, tid, wt)
    return tid, wt


def test_seconde_relecture_attend_l_arret_du_relecteur_abandonne(
    projet: Path, host: FakeHost, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from grimoire.missions import reviewer

    tid, _wt = _dual_ready(projet, host)
    monkeypatch.setattr(reviewer.ReviewStage, "_retry_halts", lambda _self: None)
    _failed_launch(projet, tid, session="abandonne", reason="unresolved", halted=False)
    report = tick(projet, host)
    assert any("arrêt n'est pas confirmé" in o.detail for o in report.reviews), report.reviews
    assert merges(report) == [("wait", "waiting")], report.merges


def test_une_seconde_relecture_non_inscrite_escalade(projet: Path, host: FakeHost) -> None:
    tid, _wt = _dual_ready(projet, host)
    _failed_launch(projet, tid, session="refuse", reason="refused", halted=True)
    report = tick(projet, host)
    assert any(o.action == "escalate" and "journal d'autorité" in o.detail for o in report.reviews), report.reviews
    assert host.of("gh", "pr", "merge") == []


def test_trop_de_tentatives_de_seconde_relecture_escalade(projet: Path, host: FakeHost) -> None:
    tid, _wt = _dual_ready(projet, host)
    for _ in range(3):
        _failed_launch(projet, tid, session="muet", reason="timeout", halted=True)
    report = tick(projet, host)
    assert any("sans reçu jugeable" in o.detail for o in report.reviews), report.reviews
    assert host.of("gh", "pr", "merge") == []


def test_une_demande_sans_modele_ne_lance_rien(projet: Path, host: FakeHost) -> None:
    tid, _wt = _dual_ready(projet, host)
    TaskService(projet).ledger.append_event(
        MERGE_DUAL_REVIEW_REQUESTED_EVENT, tid, "task", "merger",
        {"task_id": tid, "review": "absente", "model": "", "pr": 7, "head": "a" * 40},
    )
    report = tick(projet, host)
    assert any(o.status == "failed" and "sans modèle choisi" in o.detail for o in report.reviews), report.reviews
    assert merges(report) == [("wait", "waiting")] and "le relecteur la lance au tick suivant" in report.merges[0].detail
