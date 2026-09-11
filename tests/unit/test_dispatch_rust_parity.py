"""Parité entre backends pour la cascade de dispatch et le routage (issue #354).

``grimoire.missions.dispatch`` (``start_tier_for``, ``_tier_chain``, la
résolution ``--start-tier`` explicite, ``render_invocation``,
``_looks_rate_limited``, ``_extract_cost_usd``, ``_uncertainties_search_text``
+ ``_extract_uncertainties``, ``_matches_review_surface``,
``_classify_review``, ``DispatchReport.succeeded``/``.exit_code``/
``.refusal_message``) et ``grimoire.providers.routing`` (``_candidate_order``
+ le filtre de refroidissement/disponibilité de ``candidates()``) délèguent
optionnellement à un cœur Rust compilé par PyO3
(``rust/grimoire-dispatch-core/``) quand il est importable, et retombent
sinon sur l'implémentation Python pure — voir les docstrings de
``dispatch.py`` et ``routing.py`` pour la bascule ``GRIMOIRE_DISPATCH_BACKEND``
que ce fichier utilise.

``tests/unit/missions/test_dispatch.py`` et ``tests/unit/test_providers.py``
restent le golden test : ils tournent tels quels sous les deux backends (voir
``.github/workflows/rust-cores.yml``). Ce fichier ajoute :

1. La grille des chaînes de paliers (V0/V1/V2 × tous les ``max_tier``),
   comparée entre backends.
2. Deux défauts réels trouvés en portant ``_extract_cost_usd`` vers Rust,
   corrigés dans cette même PR (voir ``dispatch.py``) et couverts ici sous
   les deux backends :
   - ``isinstance(True, int)`` est vrai en Python : un ``total_cost_usd``
     JSON ``true``/``false`` était coercé en ``1.0``/``0.0`` avant correctif.
   - ``json.loads`` accepte ``NaN``/``Infinity``/``-Infinity`` (extension
     non-RFC 8259 de CPython) ; un coût non fini est désormais traité comme
     absent, comme un cœur Rust strict le ferait naturellement.
3. Le corpus fixture de dix sorties d'ouvrier réalistes
   (``tests/fixtures/dispatch_worker_outputs/``), rejoué sur
   ``_extract_cost_usd``/``_extract_uncertainties`` sous les deux backends.
4. Un fuzz léger (200 chaînes aléatoires) : aucune des fonctions d'analyse de
   sortie ne doit jamais lever, sous aucun backend.
5. La cascade de refus nommés (``v2``, ``no_check``, ``no_tier``,
   ``no_provider``) et le succès/échec de bout en bout via ``run_dispatch``,
   sous les deux backends.
6. Le routage de fournisseurs par palier avec refroidissement
   (``providers.routing.candidates``), y compris "tous refroidis" → tuple
   vide, jamais une exception.

Quand le module compilé n'est pas installé (l'environnement contributeur par
défaut, et le job CI normal), les tests marqués ``requires_rust_core`` sont
sautés plutôt qu'échoués. Le job CI dédié installe le cœur en premier et est
là où ce fichier exerce vraiment les deux côtés.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from grimoire.missions.dispatch import (
    DispatchReport,
    _classify_review,
    _extract_cost_usd,
    _extract_uncertainties,
    _looks_rate_limited,
    _matches_review_surface,
    _tier_chain,
    _uncertainties_search_text,
    render_invocation,
    run_dispatch,
    rust_backend_available,
    start_tier_for,
)
from grimoire.missions.service import TaskService
from grimoire.missions.verifiability import Verifiability
from grimoire.providers.routing import candidates as provider_candidates
from grimoire.providers.state import ProviderRuntimeState, save_state

requires_rust_core = pytest.mark.skipif(
    not rust_backend_available(),
    reason="grimoire_dispatch_core not installed — build it locally (maturin develop) or run the Rust CI job",
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "dispatch_worker_outputs"

STANDARD = Path("_grimoire/standard")
REGISTRY = STANDARD / "llm-provider-registry.yaml"
LEDGER = Path("_grimoire-runtime-output/ledger")


def _with_backend(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_DISPATCH_BACKEND", backend)


_BACKENDS = ["python", "rust"]


def _backends_for_test() -> list[str]:
    return _BACKENDS if rust_backend_available() else ["python"]


# ── start_tier_for / _tier_chain : grille complète ──────────────────────────

_ALL_TIERS: tuple[str, ...] = ("cheap", "mid", "strong")
_ALL_VERIFIABILITY: tuple[Verifiability, ...] = tuple(Verifiability)


@requires_rust_core
@pytest.mark.parametrize("verifiability", _ALL_VERIFIABILITY)
def test_start_tier_for_agrees_across_backends(verifiability: Verifiability, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend("python", monkeypatch)
    python_floor = start_tier_for(verifiability)
    _with_backend("rust", monkeypatch)
    rust_floor = start_tier_for(verifiability)
    assert python_floor == rust_floor


@requires_rust_core
@pytest.mark.parametrize("start_tier", _ALL_TIERS)
@pytest.mark.parametrize("max_tier", [*_ALL_TIERS, None])
def test_tier_chain_agrees_across_backends(
    start_tier: str, max_tier: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_backend("python", monkeypatch)
    python_chain = _tier_chain(start_tier, max_tier)
    _with_backend("rust", monkeypatch)
    rust_chain = _tier_chain(start_tier, max_tier)
    assert python_chain == rust_chain


def test_tier_chain_empty_when_max_tier_below_start_never_raises() -> None:
    """Cadrage : une chaîne vide silencieuse, jamais une exception."""
    assert _tier_chain("strong", "cheap") == ()
    assert _tier_chain("mid", "cheap") == ()


@pytest.mark.parametrize("backend", _backends_for_test())
def test_tier_chain_never_repeats_or_descends_below_start(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    for start in _ALL_TIERS:
        for max_tier in [*_ALL_TIERS, None]:
            chain = _tier_chain(start, max_tier)
            assert len(chain) == len(set(chain)), f"palier duplique: {chain}"
            start_idx = _ALL_TIERS.index(start)
            assert all(_ALL_TIERS.index(t) >= start_idx for t in chain)


# ── render_invocation ────────────────────────────────────────────────────────

_INVOCATION_CASES: tuple[tuple[str, str, str], ...] = (
    ("claude -p {prompt} --model {model}", "bonjour", "sonnet"),
    ('claude -p "{prompt}" --model {model}', "avec espaces", "sonnet"),
    ("claude --model {model}", "sans placeholder prompt", "sonnet"),
    ("claude {model} --tag {model}", "p", "sonnet"),  # {model} deux fois
    ("claude --json '{}' {prompt}", "p", "sonnet"),  # accolades littérales
)


@requires_rust_core
@pytest.mark.parametrize(("template", "prompt", "model"), _INVOCATION_CASES)
def test_render_invocation_agrees_across_backends(
    template: str, prompt: str, model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_backend("python", monkeypatch)
    python_argv = render_invocation(template, prompt=prompt, model=model)
    _with_backend("rust", monkeypatch)
    rust_argv = render_invocation(template, prompt=prompt, model=model)
    assert python_argv == rust_argv


@pytest.mark.parametrize("backend", _backends_for_test())
def test_render_invocation_unterminated_quote_raises_on_both_backends(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_backend(backend, monkeypatch)
    with pytest.raises(ValueError):
        render_invocation('claude -p "unterminated', prompt="p", model="m")


# ── _extract_cost_usd : formats réels, défauts corrigés ─────────────────────

_COST_CASES: tuple[tuple[str, float | None], ...] = (
    (json.dumps({"total_cost_usd": 0.0512}), 0.0512),
    (json.dumps({"total_cost_usd": 2}), 2.0),
    (json.dumps({"total_cost_usd": 1.5e-2}), 0.015),
    (json.dumps({"total_cost_usd": -1.5}), -1.5),  # cout negatif, non valide
    ("Voici le resultat de la tache, sans JSON.", None),  # texte pur
    (json.dumps({"other_field": 1}), None),  # champ absent
    ('{"total_cost_usd": "0,05"}', None),  # virgule decimale (chaine)
    (json.dumps({"total_cost_usd": 1, "total_cost_usd_bis": 2}), 1.0),
)


@requires_rust_core
@pytest.mark.parametrize(("stdout", "expected"), _COST_CASES)
def test_extract_cost_usd_agrees_across_backends(
    stdout: str, expected: float | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_backend("python", monkeypatch)
    python_cost = _extract_cost_usd(stdout)
    _with_backend("rust", monkeypatch)
    rust_cost = _extract_cost_usd(stdout)
    assert python_cost == rust_cost == expected


@pytest.mark.parametrize("backend", _backends_for_test())
def test_extract_cost_usd_boolean_is_none_defect_fixed(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut trouvé en portant cette fonction vers Rust (issue #354),
    corrigé côté Python dans cette même PR : ``isinstance(True, int)`` est
    vrai en Python, donc un coût JSON ``true``/``false`` était coercé en
    ``1.0``/``0.0`` avant correctif. Les deux backends rendent désormais
    ``None``."""
    _with_backend(backend, monkeypatch)
    assert _extract_cost_usd(json.dumps({"total_cost_usd": True})) is None
    assert _extract_cost_usd(json.dumps({"total_cost_usd": False})) is None


@pytest.mark.parametrize("backend", _backends_for_test())
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_extract_cost_usd_non_finite_is_none(token: str, backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``json.loads`` de CPython accepte ces trois jetons hors RFC 8259 —
    un cœur Rust strict ne les accepterait pas nativement (voir le docstring
    du module ``json`` dans ``rust/grimoire-dispatch-core/src/lib.rs``, qui
    les reproduit explicitement). Un coût non fini reste, des deux côtés,
    traité comme absent (corrigé côté Python dans cette même PR)."""
    _with_backend(backend, monkeypatch)
    assert _extract_cost_usd(f'{{"total_cost_usd": {token}}}') is None


# ── _extract_uncertainties : cas limites + corpus réaliste ──────────────────


def _block(body: str) -> str:
    return f"Reponse.\n```grimoire-uncertainties\n{body}\n```\nFin."


@requires_rust_core
def test_extract_uncertainties_absent_section_agrees(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend("python", monkeypatch)
    python_result = _extract_uncertainties("rien de particulier ici")
    _with_backend("rust", monkeypatch)
    rust_result = _extract_uncertainties("rien de particulier ici")
    assert python_result == rust_result == ((), ())


@requires_rust_core
def test_extract_uncertainties_empty_list_agrees(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = _block("[]")
    _with_backend("python", monkeypatch)
    python_result = _extract_uncertainties(stdout)
    _with_backend("rust", monkeypatch)
    rust_result = _extract_uncertainties(stdout)
    assert python_result == rust_result == ((), ())


@requires_rust_core
def test_extract_uncertainties_well_formed_agrees(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = _block('[{"where": "a.py", "what": "doute", "why": "pas teste"}]')
    _with_backend("python", monkeypatch)
    python_uncertainties, python_warnings = _extract_uncertainties(stdout)
    _with_backend("rust", monkeypatch)
    rust_uncertainties, rust_warnings = _extract_uncertainties(stdout)
    assert [u.to_dict() for u in python_uncertainties] == [u.to_dict() for u in rust_uncertainties]
    assert python_warnings == () and rust_warnings == ()


@requires_rust_core
def test_extract_uncertainties_missing_keys_agrees_on_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = _block('[{"where": "a", "what": "b"}, {"where": "x", "what": "y", "why": "z"}]')
    _with_backend("python", monkeypatch)
    python_uncertainties, python_warnings = _extract_uncertainties(stdout)
    _with_backend("rust", monkeypatch)
    rust_uncertainties, rust_warnings = _extract_uncertainties(stdout)
    assert [u.to_dict() for u in python_uncertainties] == [u.to_dict() for u in rust_uncertainties]
    # Le texte exact de l'avertissement n'a pas a etre identique, seulement
    # le fait qu'il y en ait un pour l'objet incomplet.
    assert len(python_warnings) == len(rust_warnings) == 1


@requires_rust_core
def test_extract_uncertainties_invalid_json_body_agrees_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = "```grimoire-uncertainties\nceci n'est pas du JSON {{{\n```"
    _with_backend("python", monkeypatch)
    python_uncertainties, python_warnings = _extract_uncertainties(stdout)
    _with_backend("rust", monkeypatch)
    rust_uncertainties, rust_warnings = _extract_uncertainties(stdout)
    assert python_uncertainties == rust_uncertainties == ()
    assert len(python_warnings) == len(rust_warnings) == 1


@requires_rust_core
def test_extract_uncertainties_trailing_prose_after_section_ignored_agrees(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = _block("[]") + "\n\nEt voila d'autres explications non structurees apres le bloc."
    _with_backend("python", monkeypatch)
    python_result = _extract_uncertainties(stdout)
    _with_backend("rust", monkeypatch)
    rust_result = _extract_uncertainties(stdout)
    assert python_result == rust_result == ((), ())


def _fixture_paths() -> list[Path]:
    if not FIXTURES.is_dir():
        return []
    return sorted(FIXTURES.glob("*.txt"))


@requires_rust_core
@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda p: p.stem)
def test_worker_output_corpus_agrees_across_backends(fixture_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Corpus fixture de dix sorties d'ouvrier réalistes (aucune sortie
    enregistrée réelle trouvée dans le dépôt — voir le docstring du module)."""
    stdout = fixture_path.read_text(encoding="utf-8")
    _with_backend("python", monkeypatch)
    python_cost = _extract_cost_usd(stdout)
    python_uncertainties, python_warnings = _extract_uncertainties(stdout)
    python_rate_limited = _looks_rate_limited(stdout)
    _with_backend("rust", monkeypatch)
    rust_cost = _extract_cost_usd(stdout)
    rust_uncertainties, rust_warnings = _extract_uncertainties(stdout)
    rust_rate_limited = _looks_rate_limited(stdout)

    assert python_cost == rust_cost
    assert [u.to_dict() for u in python_uncertainties] == [u.to_dict() for u in rust_uncertainties]
    assert len(python_warnings) == len(rust_warnings)
    assert python_rate_limited == rust_rate_limited


def test_worker_output_corpus_exists_and_has_ten_fixtures() -> None:
    fixtures = _fixture_paths()
    assert len(fixtures) == 10, f"attendu 10 fixtures dans {FIXTURES}, trouve {len(fixtures)}"


# ── Fuzz léger : jamais d'exception, sous aucun backend ─────────────────────


def _fuzz_corpus(count: int = 200) -> list[str]:
    import random

    alphabet = list("abc{}[]\"'`\\n \t#-!*?.,:;grimoireuncertaintiesNaInfy0123456789")
    rng = random.Random(20260911)
    return ["".join(rng.choices(alphabet, k=rng.randint(0, 120))) for _ in range(count)]


@pytest.mark.parametrize("backend", _backends_for_test())
def test_fuzz_never_raises_on_arbitrary_output(backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    for candidate in _fuzz_corpus():
        _extract_cost_usd(candidate)
        _uncertainties_search_text(candidate)
        _extract_uncertainties(candidate)
        _looks_rate_limited(candidate)
        _matches_review_surface(candidate, ("*/cli/*", "*schema*"))
        with contextlib.suppress(ValueError):
            # gabarit malforme : erreur nommee, jamais une autre exception
            render_invocation(candidate, prompt="p", model="m")


@requires_rust_core
def test_fuzz_agrees_across_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    corpus = _fuzz_corpus(80)
    for candidate in corpus:
        _with_backend("python", monkeypatch)
        python_cost = _extract_cost_usd(candidate)
        python_uncertainties, python_warnings = _extract_uncertainties(candidate)
        python_rate_limited = _looks_rate_limited(candidate)
        _with_backend("rust", monkeypatch)
        rust_cost = _extract_cost_usd(candidate)
        rust_uncertainties, rust_warnings = _extract_uncertainties(candidate)
        rust_rate_limited = _looks_rate_limited(candidate)

        assert python_cost == rust_cost, candidate
        assert [u.to_dict() for u in python_uncertainties] == [u.to_dict() for u in rust_uncertainties], candidate
        assert len(python_warnings) == len(rust_warnings), candidate
        assert python_rate_limited == rust_rate_limited, candidate


# ── _matches_review_surface / _classify_review ──────────────────────────────

_REVIEW_SURFACES = ("*/cli/*", "*/mcp/*", "*schema*", "*/verifiability.py", "*/hooks/*")

_REVIEW_PATH_CASES: tuple[tuple[str, bool], ...] = (
    ("src/grimoire/cli/app.py", True),
    ("src/grimoire/mcp/server.py", True),
    ("core/schema_extra.py", True),
    ("grimoire/missions/verifiability.py", True),
    (".github/hooks/scripts/gate.sh", True),
    ("README.md", False),
    ("src/grimoire/tools/lint.py", False),
)


@requires_rust_core
@pytest.mark.parametrize(("path", "expected"), _REVIEW_PATH_CASES)
def test_matches_review_surface_agrees_across_backends(
    path: str, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_backend("python", monkeypatch)
    python_match = _matches_review_surface(path, _REVIEW_SURFACES)
    _with_backend("rust", monkeypatch)
    rust_match = _matches_review_surface(path, _REVIEW_SURFACES)
    assert python_match == rust_match == expected


def _git_repo(root: Path, *tracked: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    for path in tracked:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)


@requires_rust_core
def test_classify_review_agrees_across_backends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sensitive = tmp_path / "src" / "grimoire" / "cli" / "app.py"
    boring = tmp_path / "README.md"
    _git_repo(tmp_path, sensitive, boring)
    sensitive.write_text("changed\n", encoding="utf-8")
    boring.write_text("changed too\n", encoding="utf-8")

    _with_backend("python", monkeypatch)
    python_review, python_files, python_note = _classify_review(tmp_path)
    _with_backend("rust", monkeypatch)
    rust_review, rust_files, rust_note = _classify_review(tmp_path)

    assert python_review == rust_review == "review_required"
    assert set(python_files) == set(rust_files) == {"src/grimoire/cli/app.py"}
    assert python_note is None and rust_note is None


# ── providers.routing.candidates : refroidissement, jamais un crash ─────────


def _write_registry(root: Path, provider_ids: list[str], *, tier: str = "cheap") -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    entries = "\n".join(
        f'  - id: "{pid}"\n'
        f"    enabled: true\n"
        f'    provider_type: "hosted"\n'
        f'    allowed_capabilities: ["chat"]\n'
        f'    default_models: ["{pid}-model"]\n'
        f'    currency: "api"\n'
        f'    invocation: "true {{prompt}} {{model}}"\n'
        f"    models:\n"
        f'      - id: "{pid}-model"\n'
        f'        tier: "{tier}"\n'
        f"    fallback_order: []\n"
        for pid in provider_ids
    )
    (root / REGISTRY).write_text(
        f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
{entries}
routing:
  default_provider: ""
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
""",
        encoding="utf-8",
    )


@requires_rust_core
def test_candidates_agrees_across_backends_with_no_cooldown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_registry(tmp_path, ["p1", "p2", "p3"])
    _with_backend("python", monkeypatch)
    python_ids = [p.id for p in provider_candidates(tmp_path, "cheap")]
    _with_backend("rust", monkeypatch)
    rust_ids = [p.id for p in provider_candidates(tmp_path, "cheap")]
    assert python_ids == rust_ids == ["p1", "p2", "p3"]


@requires_rust_core
def test_candidates_agrees_across_backends_with_partial_cooldown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    _write_registry(tmp_path, ["p1", "p2"])
    future = datetime.now(UTC) + timedelta(minutes=30)
    save_state(tmp_path, {"p1": ProviderRuntimeState(cooldown_until=future, last_failure="rate_limit", failure_count=1)})

    _with_backend("python", monkeypatch)
    python_ids = [p.id for p in provider_candidates(tmp_path, "cheap")]
    _with_backend("rust", monkeypatch)
    rust_ids = [p.id for p in provider_candidates(tmp_path, "cheap")]
    assert python_ids == rust_ids == ["p2"]


@requires_rust_core
def test_candidates_all_cooled_down_is_empty_and_never_indexerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    _write_registry(tmp_path, ["p1", "p2"])
    future = datetime.now(UTC) + timedelta(minutes=30)
    save_state(
        tmp_path,
        {
            "p1": ProviderRuntimeState(cooldown_until=future, last_failure="rate_limit", failure_count=1),
            "p2": ProviderRuntimeState(cooldown_until=future, last_failure="timeout", failure_count=1),
        },
    )
    for backend in _backends_for_test():
        _with_backend(backend, monkeypatch)
        assert provider_candidates(tmp_path, "cheap") == ()


# ── DispatchReport.succeeded / exit_code / refusal_message, refus nommés ────

V0_CRITERION = "la suite de tests passe"


def _dispatch_script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(body), encoding="utf-8")
    return path


def _dispatch_invocation(script: Path) -> str:
    return f"{sys.executable} {script} {{prompt}} --model {{model}}"


def _dispatch_write_registry(root: Path, pid: str, tier: str, invocation: str) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / REGISTRY).write_text(
        f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "{pid}"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat"]
    default_models: ["{pid}-model"]
    currency: "api"
    invocation: "{invocation}"
    models:
      - id: "{pid}-model"
        tier: "{tier}"
    fallback_order: []
routing:
  default_provider: ""
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
""",
        encoding="utf-8",
    )


def _dispatch_service(tmp_path: Path) -> TaskService:
    return TaskService(tmp_path, LEDGER)


def _dispatch_task(service: TaskService, acceptance: tuple[str, ...], *, owner: str = "amelia") -> str:
    ledger = service.ledger
    mission = ledger.create_mission("Demo parite", origin="test")
    task = ledger.create_task(mission.id, "Tache deleguee", acceptance=acceptance, owner=owner)
    return task.id


@pytest.mark.parametrize("backend", _backends_for_test())
def test_v2_refusal_agrees(backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    service = _dispatch_service(tmp_path)
    tid = _dispatch_task(service, acceptance=("le code est propre",))
    report = run_dispatch(service, tid, checks=("true",))
    assert report.refusal == "v2"
    assert report.exit_code == 2
    assert report.succeeded is False
    assert "V2" in (report.refusal_message or "")


@pytest.mark.parametrize("backend", _backends_for_test())
def test_no_check_refusal_agrees(backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    service = _dispatch_service(tmp_path)
    tid = _dispatch_task(service, acceptance=(V0_CRITERION,))
    report = run_dispatch(service, tid, checks=())
    assert report.refusal == "no_check"
    assert report.exit_code == 2


@pytest.mark.parametrize("backend", _backends_for_test())
def test_no_tier_refusal_agrees(backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    service = _dispatch_service(tmp_path)
    tid = _dispatch_task(service, acceptance=("revue humaine avant fusion",))  # V1 -> floor "mid"
    report = run_dispatch(service, tid, checks=("true",), max_tier="cheap")
    assert report.refusal == "no_tier"
    assert report.exit_code == 2


@pytest.mark.parametrize("backend", _backends_for_test())
def test_no_provider_refusal_agrees(backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)
    (tmp_path / STANDARD).mkdir(parents=True, exist_ok=True)
    # Registre sans aucun fournisseur : chaine non vide, mais personne a
    # appeler.
    (tmp_path / REGISTRY).write_text(
        """\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers: []
routing:
  default_provider: ""
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
""",
        encoding="utf-8",
    )
    service = _dispatch_service(tmp_path)
    tid = _dispatch_task(service, acceptance=(V0_CRITERION,))
    report = run_dispatch(service, tid, checks=("true",))
    assert report.refusal == "no_provider"
    assert report.exit_code == 2
    assert "fournisseur" in (report.refusal_message or "")


@pytest.mark.parametrize("backend", _backends_for_test())
def test_success_and_chain_exhausted_agree(backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_backend(backend, monkeypatch)

    green = _dispatch_script(
        tmp_path,
        "green.py",
        """\
        from pathlib import Path
        Path("marker.txt").write_text("done", encoding="utf-8")
        """,
    )
    _dispatch_write_registry(tmp_path, "worker", "cheap", _dispatch_invocation(green))
    service = _dispatch_service(tmp_path)
    tid = _dispatch_task(service, acceptance=(V0_CRITERION,))
    report = run_dispatch(service, tid, checks=("test -f marker.txt",))
    assert report.succeeded is True
    assert report.exit_code == 0
    assert report.refusal is None

    always_red = _dispatch_script(tmp_path, "always_red.py", "pass\n")
    _dispatch_write_registry(tmp_path, "worker2", "cheap", _dispatch_invocation(always_red))
    tid2 = _dispatch_task(service, acceptance=(V0_CRITERION,))
    report2 = run_dispatch(service, tid2, checks=("test -f absent.txt",), max_tier="cheap")
    assert report2.succeeded is False
    assert report2.exit_code == 1
    assert report2.refusal is None


def test_dispatch_report_helpers_are_dataclass_properties_not_backend_specific_types() -> None:
    """Garde-fou de non-régression : ``DispatchReport`` reste un dataclass
    ordinaire quel que soit le backend — la délégation Rust ne fuit jamais
    dans la forme des objets retournés."""
    report = DispatchReport(
        task_id="T-1", verifiability="V0", dry_run=True, planned_chain=("cheap",), prompt="p"
    )
    assert isinstance(report.exit_code, int)
    assert isinstance(report.succeeded, bool)
    assert report.refusal_message is None
