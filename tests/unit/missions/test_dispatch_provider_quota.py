"""K8 (#773) : le câblage de la suspension dans ``run_dispatch``, de bout en bout.

``test_provider_quota.py`` éprouve ``classify_failure``, l'état et le routage
séparément. Rien ne verrouillait pourtant leur assemblage dans la cascade :
supprimer l'appel à ``record_suspension`` ou revenir à ``models_for_tier(tier)[0]``
laissait toute la suite verte. Les fournisseurs sont de vrais scripts Python
exécutés par de vrais sous-processus, comme dans ``test_dispatch.py``.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from textwrap import dedent

import pytest

from grimoire.missions.dispatch import run_dispatch
from grimoire.missions.service import TaskService
from grimoire.providers.state import load_state, record_suspension

STANDARD = Path("_grimoire/standard")
LEDGER = Path("_grimoire-runtime-output/ledger")
V0_CRITERION = "la suite de tests passe"

_LIMIT_MESSAGE = """\
    import sys
    print("usage limit reached, resets at 3pm", file=sys.stderr)
    sys.exit(1)
    """

_WRITE_MARKER = """\
    import sys
    from pathlib import Path
    Path("marker.txt").write_text("done", encoding="utf-8")
    Path("argv.txt").write_text(" ".join(sys.argv[1:]), encoding="utf-8")
    """


@pytest.fixture(autouse=True)
def _no_unattended(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_UNATTENDED", raising=False)


def _script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / "scripts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(body), encoding="utf-8")
    return f"{sys.executable} {path} {{prompt}} --model {{model}}"


def _registry(root: Path, anthropic_invocation: str, local_invocation: str | None = None) -> None:
    local = ""
    if local_invocation is not None:
        local = f"""\
  - id: "local"
    enabled: true
    provider_type: "local"
    allowed_capabilities: ["chat", "code"]
    default_models: ["qwen"]
    currency: "local"
    invocation: "{local_invocation}"
    models:
      - id: "qwen"
        tier: "mid"
    fallback_order: []
"""
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / STANDARD / "llm-provider-registry.yaml").write_text(
        f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "anthropic"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["opus"]
    currency: "quota"
    invocation: "{anthropic_invocation}"
    models:
      - id: "opus"
        tier: "mid"
      - id: "sonnet"
        tier: "mid"
    fallback_order: []
{local}routing:
  default_provider: ""
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
""",
        encoding="utf-8",
    )


def _task(tmp_path: Path) -> tuple[TaskService, str]:
    service = TaskService(tmp_path, LEDGER)
    mission = service.ledger.create_mission("Démo quota", origin="test")
    task = service.ledger.create_task(mission.id, "Tâche déléguée", acceptance=(V0_CRITERION,), owner="amelia")
    return service, task.id


def test_run_dispatch_suspends_the_provider_until_the_date_in_its_output(tmp_path: Path) -> None:
    """« usage limit reached, resets at 3pm » ferme le fournisseur jusqu'à 15:00 (heure locale)."""
    _registry(tmp_path, _script(tmp_path, "limited.py", _LIMIT_MESSAGE), _script(tmp_path, "ok.py", _WRITE_MARKER))
    service, tid = _task(tmp_path)
    before = datetime.now().astimezone()

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    after = datetime.now().astimezone()
    assert [a.verdict for a in report.attempts] == ["quota", "green"]
    assert [a.provider for a in report.attempts] == ["anthropic", "local"]
    suspension = load_state(tmp_path)["anthropic"].suspension
    assert suspension is not None
    assert suspension.source == "resets_at"
    assert suspension.until is not None
    local_until = suspension.until.astimezone()
    assert (local_until.hour, local_until.minute, local_until.second) == (15, 0, 0)
    assert before < local_until <= after + timedelta(days=1)
    assert load_state(tmp_path)["anthropic"].cooldown_until is None


def test_run_dispatch_scopes_a_model_hint_to_that_model_only(tmp_path: Path) -> None:
    """Un événement ``seven_day_opus`` refusé ferme ``opus``, pas le fournisseur."""
    limited = """\
        import json, sys
        reset = int(__import__("time").time()) + 3 * 3600
        info = {"status": "rejected", "resetsAt": reset, "rateLimitType": "seven_day_opus"}
        print(json.dumps({"type": "rate_limit_event", "rate_limit_info": info}))
        sys.exit(1)
        """
    _registry(tmp_path, _script(tmp_path, "limited.py", limited))
    service, tid = _task(tmp_path)

    run_dispatch(service, tid, checks=("test -f marker.txt",), max_tier="mid")

    entry = load_state(tmp_path)["anthropic"]
    assert entry.suspension is None
    assert "opus" in entry.model_suspensions


def test_run_dispatch_picks_the_first_model_the_plan_has_not_closed(tmp_path: Path) -> None:
    """Premier modèle du palier suspendu : l'appel et l'attempt portent le second."""
    _registry(tmp_path, _script(tmp_path, "ok.py", _WRITE_MARKER))
    record_suspension(
        tmp_path,
        "anthropic",
        until=datetime.now(UTC) + timedelta(days=20),
        source="resets_at",
        model="opus",
    )
    service, tid = _task(tmp_path)

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert [a.verdict for a in report.attempts] == ["green"]
    assert report.attempts[0].model == "sonnet"
    assert (tmp_path / "argv.txt").read_text(encoding="utf-8").endswith("--model sonnet")


def test_run_dispatch_keeps_the_first_model_when_nothing_is_suspended(tmp_path: Path) -> None:
    _registry(tmp_path, _script(tmp_path, "ok.py", _WRITE_MARKER))
    service, tid = _task(tmp_path)

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert report.attempts[0].model == "opus"


def test_run_dispatch_does_not_suspend_a_provider_whose_answer_mentions_quota(tmp_path: Path) -> None:
    """K8-S1-01 de bout en bout : un succès qui parle de quota n'est ni échec ni suspension."""
    answer = """\
        from pathlib import Path
        Path("marker.txt").write_text("done", encoding="utf-8")
        print("I refactored the quota module; retry limit reached is now handled. duration_ms 15290")
        """
    _registry(tmp_path, _script(tmp_path, "answer.py", answer))
    service, tid = _task(tmp_path)

    report = run_dispatch(service, tid, checks=("test -f marker.txt",))

    assert [a.verdict for a in report.attempts] == ["green"]
    entry = load_state(tmp_path).get("anthropic")
    assert entry is None or (entry.suspension is None and entry.cooldown_until is None)
