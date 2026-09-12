"""Parité entre backends pour le cœur du système d'artefact émergent (issue #354).

``grimoire.traces.ledger`` (``TraceLedger.agent_dispatch_counts``,
``TraceLedger.agent_miss_counts``, ``TraceLedger.oldest_started_at``,
``compute_agent_freshness``) et ``grimoire.proposals`` (nommage mécanique,
résolution du porteur, décision de synchronisation) délèguent optionnellement
à un cœur Rust compilé par PyO3 (``rust/grimoire-traces-core/``) quand il est
importable, et retombent sinon sur l'implémentation Python pure — voir les
docstrings de ``ledger.py`` et ``proposals.py`` pour la bascule
``GRIMOIRE_TRACES_BACKEND`` que ce fichier utilise.

``tests/unit/test_traces.py`` et ``tests/unit/test_proposals.py`` restent le
golden test : ils tournent tels quels sous les deux backends (voir
``.github/workflows/rust-cores.yml``). Ce fichier ajoute :

1. Les invariants de la doctrine (issues #389, #395, #396, #402) : seuil
   jamais sous 2, jamais de proposition au premier non-choix, une refusée ne
   revient qu'après doublement du compte, la persona d'entrée n'est jamais
   porteuse, absence de données ≠ absence d'usage, match par mot entier.
2. Le défaut réel trouvé en portant ``compute_agent_freshness`` vers Rust,
   corrigé dans cette même PR (voir ``ledger.py``) : un horodatage ISO-8601
   sans décalage faisait lever une ``TypeError`` non rattrapée au moment de
   la soustraction — jamais à l'analyse elle-même.
3. Le corpus de dix journaux JSONL réalistes
   (``tests/fixtures/traces_ledgers/``), rejoué sur les trois agrégations et
   la fraîcheur sous les deux backends.
4. Un fuzz léger (200 enregistrements aléatoires) : aucune des fonctions
   d'agrégation/fraîcheur ne doit jamais lever, sous aucun backend.
5. Qu'un champ de contenu libre (``prompt``, ``request``) écrit à la main
   dans un enregistrement n'est jamais agrégé.

Quand le module compilé n'est pas installé (l'environnement contributeur par
défaut, et le job CI normal), les tests marqués ``requires_rust_core`` sont
sautés plutôt qu'échoués. Le job CI dédié installe le cœur en premier et est
là où ce fichier exerce vraiment les deux côtés.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grimoire.proposals import (
    DEFAULT_THRESHOLD,
    Proposal,
    _build_proposal,
    _configured_threshold,
    _employment_clause,
    _guess_tools,
    _slugify,
    _sync_decision,
    rust_backend_available,
)
from grimoire.traces.ledger import (
    AGENT_DISPATCH_TAG,
    AGENT_MISS_TAG,
    TraceLedger,
    compute_agent_freshness,
)

requires_rust_core = pytest.mark.skipif(
    not rust_backend_available(),
    reason="grimoire_traces_core not installed — build it locally (maturin develop) or run the Rust CI job",
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "traces_ledgers"


def _with_backend(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_TRACES_BACKEND", backend)


_BACKENDS = ["python", "rust"]


def _backends_for_test() -> list[str]:
    return _BACKENDS if rust_backend_available() else ["python"]


# ── Seuil : jamais sous 2, jamais au premier non-choix ──────────────────────


@pytest.mark.parametrize("backend", _backends_for_test())
@pytest.mark.parametrize("raw_threshold", [-5, 0, 1])
def test_threshold_below_two_is_clamped_to_two_on_both_backends(
    raw_threshold: int, backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_backend(backend, monkeypatch)
    (tmp_path / "project-context.yaml").write_text(
        f"project:\n  name: t\n  type: webapp\nagents:\n  archetype: minimal\nproposals:\n  threshold: {raw_threshold}\n",
        encoding="utf-8",
    )
    assert _configured_threshold(tmp_path) == DEFAULT_THRESHOLD  # DEFAULT_THRESHOLD == 2, le plancher


@pytest.mark.parametrize("backend", _backends_for_test())
def test_count_one_is_never_a_proposal_count_equal_threshold_is(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    action_one, _, _ = _sync_decision(2, 1, None)
    action_two, _, _ = _sync_decision(2, 2, None)
    assert action_one == "skip"
    assert action_two == "create"


@requires_rust_core
@pytest.mark.parametrize("raw_threshold", [-5, 0, 1, 2, 5])
def test_sync_decision_agrees_across_backends_on_threshold_grid(raw_threshold: int, monkeypatch: pytest.MonkeyPatch) -> None:
    for count in (0, 1, 2, 3, 4, 10):
        _with_backend("python", monkeypatch)
        python_result = _sync_decision(raw_threshold, count, None)
        _with_backend("rust", monkeypatch)
        rust_result = _sync_decision(raw_threshold, count, None)
        assert python_result == rust_result, (raw_threshold, count)


# ── Refusée à n : rien avant 2n, réapparaît à 2n ─────────────────────────────


@pytest.mark.parametrize("backend", _backends_for_test())
def test_rejected_at_n_stays_rejected_until_double_then_reopens(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    existing = Proposal(
        slug="terraform-specialist", specialty="terraform", artifact_type="agent", status="rejected",
        count=2, rejected_at_count=2,
    )
    below, _, reopen_at = _sync_decision(2, 3, existing)
    at_double, _, _ = _sync_decision(2, 4, existing)
    assert below == "keep_rejected"
    assert reopen_at == 4
    assert at_double == "reopen"


@requires_rust_core
@pytest.mark.parametrize("rejected_at_count", [None, 0, 2, 5])
def test_reopen_at_agrees_across_backends(rejected_at_count: int | None, monkeypatch: pytest.MonkeyPatch) -> None:
    existing = Proposal(
        slug="x", specialty="x", artifact_type="agent", status="rejected", count=2,
        rejected_at_count=rejected_at_count,
    )
    _with_backend("python", monkeypatch)
    python_result = _sync_decision(2, 3, existing)
    _with_backend("rust", monkeypatch)
    rust_result = _sync_decision(2, 3, existing)
    assert python_result == rust_result


# ── Enregistrement avec un champ de contenu libre : jamais agrégé ──────────


@pytest.mark.parametrize("backend", _backends_for_test())
def test_free_content_field_is_never_aggregated(backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Un JSONL édité à la main peut porter n'importe quel champ ('prompt',
    'request'...) — ``TraceRecord.from_dict`` ne lit que les clés qu'il
    connaît, donc un tel champ n'a tout simplement nulle part où aller dans
    une agrégation, sous aucun backend."""
    _with_backend(backend, monkeypatch)
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    handwritten = {
        "id": "TRC-1", "run_id": "r1", "workflow_instance_id": "", "mission_id": "", "task_id": "",
        "recipe_id": "x", "outcome": "failure", "started_at": "2026-01-01T00:00:00+00:00",
        "agent": {"agent_id": "generic-dev", "host_id": "", "model": ""},
        "tags": [AGENT_MISS_TAG, "specialty:terraform"],
        "prompt": "contenu de la demande — ne doit jamais devenir un champ agrégé",
        "request": "autre contenu libre",
    }
    (ledger_dir / "traces.jsonl").write_text(json.dumps(handwritten) + "\n", encoding="utf-8")
    ledger = TraceLedger(ledger_dir)
    counts = ledger.agent_miss_counts()
    assert set(counts.keys()) == {"terraform"}
    for stats in counts.values():
        assert set(stats.keys()) == {"count", "last_seen", "category", "fallback_agent"}


# ── Le défaut corrigé : horodatage naïf ne lève jamais ──────────────────────


@pytest.mark.parametrize("backend", _backends_for_test())
def test_naive_timestamp_never_raises_on_either_backend(backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut trouvé en portant ``compute_agent_freshness`` vers Rust (issue
    #354), corrigé côté Python dans cette même PR : un horodatage ISO sans
    décalage faisait lever une ``TypeError`` non rattrapée à la soustraction
    (``datetime.now(tz=UTC) - datetime.fromisoformat("...")`` sans offset).
    Les deux backends rendent désormais un verdict, jamais une exception."""
    _with_backend(backend, monkeypatch)
    report = compute_agent_freshness(
        ["concierge"], {}, threshold_days=90, oldest_started_at="2026-01-01T00:00:00", now=datetime(2026, 9, 11, tzinfo=UTC)
    )
    assert report.judged is True
    assert report.journal_span_days == 253


@requires_rust_core
def test_naive_timestamp_agrees_across_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 9, 11, tzinfo=UTC)
    _with_backend("python", monkeypatch)
    python_report = compute_agent_freshness(["concierge"], {}, threshold_days=90, oldest_started_at="2026-01-01T00:00:00", now=now)
    _with_backend("rust", monkeypatch)
    rust_report = compute_agent_freshness(["concierge"], {}, threshold_days=90, oldest_started_at="2026-01-01T00:00:00", now=now)
    assert python_report.judged == rust_report.judged
    assert python_report.journal_span_days == rust_report.journal_span_days


# ── Fraîcheur : cas limites explicites ───────────────────────────────────────


@pytest.mark.parametrize("backend", _backends_for_test())
def test_freshness_empty_journal_never_judges(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    report = compute_agent_freshness(["concierge"], {}, threshold_days=90, oldest_started_at=None)
    assert report.judged is False
    assert report.stale_entries == ()


@pytest.mark.parametrize("backend", _backends_for_test())
def test_freshness_single_record_dated_today(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    report = compute_agent_freshness(
        ["concierge"], {"concierge": {"count": 1, "last_seen": "2026-09-11T00:00:00+00:00"}},
        threshold_days=90, oldest_started_at="2026-09-11T00:00:00+00:00", now=now,
    )
    assert report.judged is False  # journal de 0 jour, sous le seuil de 90


@pytest.mark.parametrize("backend", _backends_for_test())
def test_freshness_agent_with_future_mtime_is_too_recent(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    report = compute_agent_freshness(
        ["fresh"], {}, threshold_days=90, oldest_started_at="2020-01-01T00:00:00+00:00",
        agent_ages={"fresh": -5},  # "mtime futur" : age negatif, plus jeune que le seuil
        now=now,
    )
    assert report.entries[0].too_recent is True
    assert report.entries[0].stale is False


@pytest.mark.parametrize("backend", _backends_for_test())
def test_freshness_huge_threshold_never_judges(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    report = compute_agent_freshness(
        ["concierge"], {}, threshold_days=10_000_000, oldest_started_at="2000-01-01T00:00:00+00:00"
    )
    assert report.judged is False


# ── Porteur : persona d'entrée jamais candidate, match par mot entier ──────


@pytest.mark.parametrize("backend", _backends_for_test())
def test_employment_clause_and_guess_tools_agree_on_reference_strings(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    use_when, _dont_use_when = _employment_clause("terraform", "infra", "")
    assert "terraform" in use_when
    assert "infra" in use_when
    assert _guess_tools("infra", "ansible-homelab") == "read, search, execute"
    assert _guess_tools("design", "figma-review") == "read, search"
    assert _slugify("Chaos Engineering !!") == "chaos-engineering"


@requires_rust_core
def test_slugify_agrees_across_backends_on_unicode(monkeypatch: pytest.MonkeyPatch) -> None:
    for text in ["Résilience réseau", "Terraform/Ansible", "", "   ", "!!!"]:
        _with_backend("python", monkeypatch)
        python_slug = _slugify(text)
        _with_backend("rust", monkeypatch)
        rust_slug = _slugify(text)
        assert python_slug == rust_slug, text


# ── Corpus réaliste : dix journaux JSONL rejoués sous les deux backends ─────


def _fixture_paths() -> list[Path]:
    if not FIXTURES.is_dir():
        return []
    return sorted(FIXTURES.glob("*.jsonl"))


def test_ledger_fixture_corpus_exists_and_has_ten_journals() -> None:
    fixtures = _fixture_paths()
    assert len(fixtures) == 10, f"attendu 10 fixtures dans {FIXTURES}, trouve {len(fixtures)}"


@requires_rust_core
@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda p: p.stem)
def test_ledger_fixture_corpus_agrees_across_backends(fixture_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger_dir = tmp_path / fixture_path.stem
    ledger_dir.mkdir()
    (ledger_dir / "traces.jsonl").write_text(fixture_path.read_text(encoding="utf-8"), encoding="utf-8")

    _with_backend("python", monkeypatch)
    python_ledger = TraceLedger(ledger_dir)
    python_dispatch = python_ledger.agent_dispatch_counts()
    python_miss = python_ledger.agent_miss_counts()
    python_oldest = python_ledger.oldest_started_at()
    python_fresh = python_ledger.agent_freshness_report(
        ["concierge", "scribe", "generic-dev", "infra-ops"], threshold_days=30, now=datetime(2026, 11, 1, tzinfo=UTC)
    )

    _with_backend("rust", monkeypatch)
    rust_ledger = TraceLedger(ledger_dir)
    rust_dispatch = rust_ledger.agent_dispatch_counts()
    rust_miss = rust_ledger.agent_miss_counts()
    rust_oldest = rust_ledger.oldest_started_at()
    rust_fresh = rust_ledger.agent_freshness_report(
        ["concierge", "scribe", "generic-dev", "infra-ops"], threshold_days=30, now=datetime(2026, 11, 1, tzinfo=UTC)
    )

    assert python_dispatch == rust_dispatch
    assert python_miss == rust_miss
    assert python_oldest == rust_oldest
    assert python_fresh.judged == rust_fresh.judged
    assert python_fresh.journal_span_days == rust_fresh.journal_span_days
    assert [(e.name, e.last_seen, e.days_since, e.stale, e.too_recent) for e in python_fresh.entries] == [
        (e.name, e.last_seen, e.days_since, e.stale, e.too_recent) for e in rust_fresh.entries
    ]


@pytest.mark.parametrize("backend", _backends_for_test())
@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda p: p.stem)
def test_ledger_fixture_corpus_never_raises_on_either_backend(
    fixture_path: Path, backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_backend(backend, monkeypatch)
    ledger_dir = tmp_path / fixture_path.stem
    ledger_dir.mkdir()
    (ledger_dir / "traces.jsonl").write_text(fixture_path.read_text(encoding="utf-8"), encoding="utf-8")
    ledger = TraceLedger(ledger_dir)
    ledger.agent_dispatch_counts()
    ledger.agent_miss_counts()
    ledger.oldest_started_at()
    ledger.agent_freshness_report(["concierge"], threshold_days=30)


# ── Fuzz léger : jamais d'exception, sous aucun backend ─────────────────────


def _fuzz_journal(count: int = 200) -> str:
    import random

    rng = random.Random(20260911)
    tag_pool = [AGENT_DISPATCH_TAG, AGENT_MISS_TAG, "category:infra", "category:", "specialty:", "specialty:x", "other", ""]
    alphabet = list("abcXYZ01239 :+-Té🎉")
    lines = []
    for i in range(count):
        agent_id = "".join(rng.choices(alphabet, k=rng.randint(0, 10)))
        started_at = "".join(rng.choices(alphabet, k=rng.randint(0, 25)))
        tags = rng.choices(tag_pool, k=rng.randint(0, 3))
        rec = {
            "id": f"TRC-{i}", "run_id": f"r{i}", "workflow_instance_id": "", "mission_id": "", "task_id": "",
            "recipe_id": "x", "outcome": "success", "started_at": started_at,
            "agent": {"agent_id": agent_id, "host_id": "", "model": ""},
            "tags": tags,
        }
        lines.append(json.dumps(rec, ensure_ascii=False))
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("backend", _backends_for_test())
def test_fuzz_never_raises_on_arbitrary_journal(backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    (tmp_path / "traces.jsonl").write_text(_fuzz_journal(), encoding="utf-8")
    ledger = TraceLedger(tmp_path)
    ledger.agent_dispatch_counts()
    ledger.agent_miss_counts()
    ledger.oldest_started_at()
    ledger.agent_freshness_report(["concierge", "x"], threshold_days=30)
    ledger.agent_freshness_report(["concierge", "x"], threshold_days=30, now=datetime(2026, 9, 11, tzinfo=UTC))


@requires_rust_core
def test_fuzz_agrees_across_backends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "traces.jsonl").write_text(_fuzz_journal(80), encoding="utf-8")
    now = datetime(2026, 9, 11, tzinfo=UTC)

    _with_backend("python", monkeypatch)
    python_ledger = TraceLedger(tmp_path)
    python_dispatch = python_ledger.agent_dispatch_counts()
    python_miss = python_ledger.agent_miss_counts()
    python_oldest = python_ledger.oldest_started_at()
    python_fresh = python_ledger.agent_freshness_report(["concierge", "x"], threshold_days=30, now=now)

    _with_backend("rust", monkeypatch)
    rust_ledger = TraceLedger(tmp_path)
    rust_dispatch = rust_ledger.agent_dispatch_counts()
    rust_miss = rust_ledger.agent_miss_counts()
    rust_oldest = rust_ledger.oldest_started_at()
    rust_fresh = rust_ledger.agent_freshness_report(["concierge", "x"], threshold_days=30, now=now)

    assert python_dispatch == rust_dispatch
    assert python_miss == rust_miss
    assert python_oldest == rust_oldest
    assert python_fresh.judged == rust_fresh.judged
    assert [(e.name, e.stale, e.too_recent) for e in python_fresh.entries] == [
        (e.name, e.stale, e.too_recent) for e in rust_fresh.entries
    ]


# ── Garde-fou : le backend invalide/forcé sans module lève une erreur nommée ─


def test_forced_rust_backend_without_module_raises_named_error(monkeypatch: pytest.MonkeyPatch) -> None:
    if rust_backend_available():
        pytest.skip("le module compile est present dans cet environnement — rien a garder ici")
    from grimoire.core.exceptions import GrimoireRuntimeError

    monkeypatch.setenv("GRIMOIRE_TRACES_BACKEND", "rust")
    with pytest.raises(GrimoireRuntimeError, match="grimoire_traces_core"):
        compute_agent_freshness(["concierge"], {}, threshold_days=90, oldest_started_at=None)


def test_invalid_backend_value_raises_named_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from grimoire.core.exceptions import GrimoireRuntimeError

    monkeypatch.setenv("GRIMOIRE_TRACES_BACKEND", "bogus")
    with pytest.raises(GrimoireRuntimeError, match="GRIMOIRE_TRACES_BACKEND"):
        compute_agent_freshness(["concierge"], {}, threshold_days=90, oldest_started_at=None)


def test_build_proposal_is_backend_agnostic_dataclass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garde-fou de non-régression : ``_build_proposal`` reste un dataclass
    ordinaire quel que soit le backend — la délégation Rust ne fuit jamais
    dans la forme des objets retournés."""
    for backend in _backends_for_test():
        _with_backend(backend, monkeypatch)
        proposal = _build_proposal(
            specialty="terraform", count=2, category="infra", fallback_agent="", carrier="",
            carrier_reason="persona d'entrée exclue, aucun porteur : agent", first_seen="2026-01-01T00:00:00+00:00",
            last_seen="2026-01-02T00:00:00+00:00",
        )
        assert isinstance(proposal, Proposal)
        assert proposal.artifact_type == "agent"
        assert proposal.slug == "terraform-specialist"
