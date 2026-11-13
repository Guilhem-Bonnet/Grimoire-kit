#!/usr/bin/env python3
"""Gain mesuré des sept cœurs Rust optionnels, crate par crate (issue #354).

Chaque crate de ``rust/`` double un module Python qui reste la référence. Ce
script mesure ce que la doublure rapporte et ce qu'elle coûte, sans rien
estimer : une valeur qui n'a pas pu être mesurée sort « non mesuré » avec sa
raison.

Quatre familles de mesures :

1. **Par fonction** — le chemin Python contre le chemin Rust de la *fonction
   publique Python* (conversions PyO3 comprises : c'est ce que reçoit
   l'appelant), sur des entrées tirées des fixtures et des tests du dépôt.
   Médiane de ``--reps`` échantillons par backend (30 par défaut), les deux
   backends alternés pour qu'une dérive de la machine touche les deux bras.
   Avant de chronométrer, le script vérifie que les deux backends rendent le
   même résultat sur l'entrée mesurée.
2. **Import** — le coût d'importer l'extension dans un interprète neuf, et le
   coût de l'import manqué quand elle est absente (``--pure-python``).
3. **Bout en bout** — un processus par appel, comme l'hôte le lance :
   ``grimoire-hook`` (PreToolUse, SessionStart), ``grimoire host sync`` et
   ``grimoire check``, avec et sans extension, sur un projet créé par
   ``grimoire init`` dans un HOME jetable. Les bras sont alternés et l'écart
   se lit répétition par répétition. Le script compte aussi les appels qui
   entrent réellement dans chaque extension pendant la commande.
4. **Coût** — lignes de Rust, lignes de CI, test de parité, taille du binaire,
   commits qui ont touché le crate et le module Python qu'il double, et, sur
   demande (``--ci-runs``), la durée réelle des jobs de CI lue par ``gh``.

Usage ::

    # les sept extensions installées dans l'interprète courant
    # (maturin build --release … puis pip install, voir CONTRIBUTING.md)
    python scripts/bench/rust_cores_gain.py --json-out gain.json

    # en plus : un second interprète SANS extension, pour le bras « absent »
    python scripts/bench/rust_cores_gain.py --pure-python /chemin/venv-pur/bin/python

    # en plus : la durée des jobs de CI sur les 300 dernières exécutions
    python scripts/bench/rust_cores_gain.py --ci-runs 300

    # essai rapide (signalé comme tel dans la sortie)
    python scripts/bench/rust_cores_gain.py --reps 3 --only acp

Une extension absente de l'interprète courant ne fait pas échouer le script :
ses mesures Rust sortent « non mesuré ». Aucune dépendance hors de celles du
kit. Le script ne construit pas les extensions et ne modifie ni les crates ni
le code du produit.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import importlib.util
import io
import itertools
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # Windows : pas de temps CPU des sous-processus
    resource = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
WORKFLOW = ROOT / ".github" / "workflows" / "rust-cores.yml"
MIN_REPS = 30
NOT_MEASURED = "non mesuré"


# --------------------------------------------------------------------------
# Les sept crates
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Crate:
    key: str
    env_var: str
    python_modules: tuple[str, ...]
    parity_tests: tuple[str, ...]
    #: ``auto`` : le module Python prend Rust dès qu'il est importable.
    #: ``python`` : Rust n'est pris que sur demande explicite.
    default_backend: str = "auto"

    @property
    def module(self) -> str:
        return f"grimoire_{self.key}_core"

    @property
    def directory(self) -> Path:
        return ROOT / "rust" / f"grimoire-{self.key}-core"


CRATES: tuple[Crate, ...] = (
    Crate(
        "policies",
        "GRIMOIRE_POLICIES_BACKEND",
        ("grimoire.policies.engine", "grimoire.policies.temporal"),
        ("tests/unit/test_policies_rust_parity.py",),
    ),
    Crate(
        "schema",
        "GRIMOIRE_SCHEMA_BACKEND",
        ("grimoire.core.schema", "grimoire.core.validator"),
        ("tests/unit/test_schema_validator_rust_parity.py",),
    ),
    Crate(
        "hosts",
        "GRIMOIRE_HOSTS_BACKEND",
        ("grimoire.hosts.collect", "grimoire.hosts.surface"),
        ("tests/unit/test_hosts_rust_parity.py",),
    ),
    Crate(
        "flows",
        "GRIMOIRE_FLOWS_BACKEND",
        ("grimoire.flows.engine", "grimoire.runtime.kernel"),
        ("tests/unit/test_flows_rust_parity.py",),
    ),
    Crate(
        "dispatch",
        "GRIMOIRE_DISPATCH_BACKEND",
        ("grimoire.missions.dispatch", "grimoire.providers.routing"),
        ("tests/unit/test_dispatch_rust_parity.py",),
    ),
    Crate(
        "traces",
        "GRIMOIRE_TRACES_BACKEND",
        ("grimoire.traces.ledger", "grimoire.proposals"),
        ("tests/unit/test_traces_rust_parity.py",),
    ),
    Crate(
        "acp",
        "GRIMOIRE_ACP_BACKEND",
        ("grimoire.acp.machine",),
        ("tests/unit/test_acp.py",),
        default_backend="python",
    ),
)
CRATE_BY_KEY = {crate.key: crate for crate in CRATES}


def extension_available(crate: Crate) -> bool:
    return importlib.util.find_spec(crate.module) is not None


# --------------------------------------------------------------------------
# Cas de mesure
# --------------------------------------------------------------------------


@dataclass
class Case:
    crate: str
    name: str
    input_desc: str
    #: Reçoit le backend (``python``/``rust``) ; la variable d'environnement du
    #: crate est déjà positionnée par le harnais.
    fn: Callable[[str], Any]
    #: Ramène le résultat à une forme comparable entre backends.
    canon: Callable[[Any], Any] = repr
    #: ``False`` : entrée fabriquée pour le banc, signalée dans le tableau.
    realistic: bool = True
    #: Part du travail commune aux deux chemins (lecture disque, analyse
    #: JSON/YAML faite en Python avant tout choix de backend), mesurée seule.
    shared_fn: Callable[[], Any] | None = None


@dataclass
class Context:
    tmp: Path
    project: Path
    home: Path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _size(texts: list[str]) -> int:
    return sum(len(t.encode("utf-8")) for t in texts)


def cases_policies(ctx: Context) -> list[Case]:
    from grimoire.hosts.decisions.tool_policy import _DESTRUCTIVE_AT_STRICT
    from grimoire.policies.engine import PolicyEngine
    from grimoire.policies.schemas import (
        ActionKind,
        CooldownRule,
        MutationClass,
        PolicyAction,
        PolicyActor,
        PolicyRequest,
        PolicyRule,
        SessionBudget,
        VerdictKind,
    )
    from grimoire.policies.session_state import SessionState
    from grimoire.policies.temporal import evaluate_temporal

    def request(kind: ActionKind, mutation: MutationClass, risk: str, command: str) -> PolicyRequest:
        return PolicyRequest(
            id="req-bench",
            run_id="00000000-0000-4000-8000-000000000001",
            task_id="",
            actor=PolicyActor(actor_id="agent", host_id="claude-code"),
            action=PolicyAction(kind=kind, tool="Bash", mutation_class=mutation, command=command),
            risk_profile=risk,
            created_at="2026-01-01T12:00:00+00:00",
        )

    def hook_engine(extra: int = 0) -> PolicyEngine:
        # Le moteur que construit le hook PreToolUse (hosts/decisions/tool_policy.py:_engine).
        engine = PolicyEngine()
        engine.register_rule(_DESTRUCTIVE_AT_STRICT)
        for i in range(extra):
            engine.register_rule(
                PolicyRule(
                    id=f"synthetic-{i:03d}",
                    description="règle fabriquée pour le banc",
                    action_kinds=(ActionKind.FILE_WRITE,),
                    mutation_classes=(MutationClass.MUTATION_CONTROLLED,),
                    risk_profiles=("standard",),
                    verdict_on_match=VerdictKind.WARN,
                    reason_template="synthetic",
                )
            )
        return engine

    def verdict_canon(v: Any) -> Any:
        return (v.verdict.value, v.reason, tuple(m.rule_id for m in v.matched_rules), tuple(v.allow_retry_after))

    engine = hook_engine()
    n_rules = len(engine._rules)
    read_req = request(ActionKind.TOOL_USE, MutationClass.READ_ONLY, "standard", "ls")
    destructive_req = request(ActionKind.FILE_WRITE, MutationClass.DESTRUCTIVE, "light", "rm -rf _grimoire-output/tmp")
    big_engine = hook_engine(100)

    def temporal_rule(rule_id: str, **kwargs: Any) -> PolicyRule:
        base: dict[str, Any] = {
            "id": rule_id,
            "description": "",
            "action_kinds": (),
            "mutation_classes": (),
            "risk_profiles": (),
            "verdict_on_match": VerdictKind.BLOCK,
            "reason_template": "",
        }
        base.update(kwargs)
        return PolicyRule(**base)

    # Les trois formes de règle temporelle de tests/unit/test_policies_rust_parity.py.
    temporal_rules = (
        temporal_rule("budget", per_session=SessionBudget(max_writes=2)),
        temporal_rule(
            "approval", verdict_on_match=VerdictKind.WARN, require_approval=True, tool_pattern="Bash(git push:*)"
        ),
        temporal_rule("cooldown", cooldown_after=CooldownRule(pattern="*", count=2, minutes=10.0)),
    )
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def temporal(_backend: str) -> Any:
        state = SessionState.new("s", now.isoformat())
        return evaluate_temporal(
            temporal_rules, state, tool_name="Bash", tool_detail="git push origin main", is_write=True, now=now
        )

    def temporal_canon(decision: Any) -> Any:
        return (decision.verdict.value, decision.reason, tuple(m.rule_id for m in decision.matched_rules))

    return [
        Case(
            "policies",
            "PolicyEngine.evaluate — lecture seule, aucune règle ne s'applique",
            f"moteur du hook, {n_rules} règles",
            lambda _b: engine.evaluate(read_req),
            verdict_canon,
        ),
        Case(
            "policies",
            "PolicyEngine.evaluate — destructif, refusé",
            f"moteur du hook, {n_rules} règles",
            lambda _b: engine.evaluate(destructive_req),
            verdict_canon,
        ),
        Case(
            "policies",
            "evaluate_temporal — budget, approbation, refroidissement",
            f"{len(temporal_rules)} règles temporelles, état de session neuf à chaque appel",
            temporal,
            temporal_canon,
        ),
        Case(
            "policies",
            "PolicyEngine.evaluate — destructif, refusé",
            f"{len(big_engine._rules)} règles (100 fabriquées)",
            lambda _b: big_engine.evaluate(destructive_req),
            verdict_canon,
            realistic=False,
        ),
    ]


def cases_schema(ctx: Context) -> list[Case]:
    from ruamel.yaml import YAML

    from grimoire.core.schema import generate_schema
    from grimoire.core.validator import validate_config

    config_path = ctx.project / "project-context.yaml"
    config = YAML(typ="safe").load(_read(config_path))
    big: dict[str, Any] = {
        "project": {"name": "bench", "description": "", "type": "generic", "stack": []},
        "user": {"name": "Dev", "language": "Français", "skill_level": "intermediate"},
    }
    for i in range(190):
        big[f"unknown_section_{i:03d}"] = {"value": i, "nested": {"a": 1, "b": [1, 2, 3]}}

    def errors_canon(errors: Any) -> Any:
        return sorted((e.path, e.message, e.suggestion or "") for e in errors)

    return [
        Case(
            "schema",
            "generate_schema",
            "aucune entrée (schéma fixe)",
            lambda _b: generate_schema(),
            lambda s: json.dumps(s, sort_keys=True),
        ),
        Case(
            "schema",
            "validate_config — project-context.yaml d'un `grimoire init`",
            f"{len(config)} sections, {config_path.stat().st_size} octets",
            lambda _b: validate_config(config),
            errors_canon,
        ),
        Case(
            "schema",
            "validate_config — 190 sections inconnues",
            f"{len(big)} sections fabriquées",
            lambda _b: validate_config(big),
            errors_canon,
            realistic=False,
        ),
    ]


def cases_hosts(ctx: Context) -> list[Case]:
    from grimoire.hosts import collect as collect_module
    from grimoire.hosts.collect import build_surface, infer_tools, parse_frontmatter
    from grimoire.hosts.surface import AgentSpec, ModelAffinity

    paths = sorted(ROOT.glob("archetypes/*/agents/*.md"))
    texts = [_read(p) for p in paths]
    corpus = f"{len(texts)} fiches d'agent livrées (archetypes/*/agents/*.md), {_size(texts)} octets"

    def shape(text: str) -> Any:
        # Même enchaînement que tests/unit/test_hosts_rust_parity.py:_derive_agent_shape.
        meta, body = parse_frontmatter(text)
        name = str(meta.get("name") or "agent")
        description = str(meta.get("description") or f"Grimoire agent {name}").strip()
        declared, rejected = collect_module._tool_verbs_with_rejects(meta.get("tools"))
        tools = declared or infer_tools(body, description)
        skills = collect_module._str_tuple(meta.get("skills"))
        context = collect_module._str_tuple(meta.get("context"))
        affinity = ModelAffinity.from_frontmatter(meta.get("model_affinity"))
        max_turns = collect_module._max_turns(meta.get("max_turns"))
        agent = AgentSpec(
            name=name,
            description=description,
            definition_ref="bench.md",
            tools=tools,
            affinity=affinity,
            max_turns=max_turns,
            skills=skills,
            context=context,
        )
        return (meta, body, tools, rejected, affinity, max_turns, skills, context, agent.fingerprint())

    surface = build_surface(ctx.project, write_cache=False)
    return [
        Case(
            "hosts",
            "parse_frontmatter sur chaque fiche",
            corpus,
            lambda _b: [parse_frontmatter(t) for t in texts],
        ),
        Case(
            "hosts",
            "dérivation complète d'un AgentSpec (frontmatter, outils, affinité, empreinte)",
            corpus,
            lambda _b: [shape(t) for t in texts],
        ),
        Case(
            "hosts",
            "build_surface du projet (lecture disque comprise)",
            f"projet `grimoire init` : {len(surface.agents)} agents, {len(surface.skills)} skills",
            lambda _b: build_surface(ctx.project, write_cache=False),
        ),
    ]


def cases_flows(ctx: Context) -> list[Case]:
    from grimoire.flows.blueprint_loader import build_node_contracts, load_blueprint, topo_order
    from grimoire.flows.engine import FlowEngine, check_output_against_contract
    from grimoire.flows.executor import InteractiveNodeExecutor

    blueprint_path = ROOT / "registry" / "blueprints" / "web-pipeline.blueprint.json"
    blueprint = load_blueprint(blueprint_path)
    order = topo_order(blueprint)
    contracts = build_node_contracts(blueprint)
    outputs = {
        node_id: {"pins": {pin.pin_id: {"contract": pin.contract} for pin in contracts[node_id].outputs}}
        for node_id in order
    }
    counter = itertools.count()

    def run_to_completion(backend: str) -> Any:
        # Même rejeu que tests/unit/test_flows_rust_parity.py:_run_to_completion.
        base = ctx.tmp / "flows" / f"{backend}-{next(counter)}"
        engine = FlowEngine(kernel_root=base / "runtime", flows_root=base / "flows")
        wfi, _contract = engine.run(blueprint_path, executor=InteractiveNodeExecutor(stream=io.StringIO()))
        for node_id in order:
            engine.resume(wfi.id, output=outputs[node_id], executor=InteractiveNodeExecutor(stream=io.StringIO()))
        return engine.status(wfi.id)

    def status_canon(view: Any) -> Any:
        return (view.status, view.current_node, view.completed_nodes, view.pending_nodes)

    desc = f"web-pipeline.blueprint.json livré, {len(order)} nodes, {blueprint_path.stat().st_size} octets"
    return [
        Case(
            "flows",
            "check_output_against_contract sur chaque node",
            desc,
            lambda _b: [check_output_against_contract(contracts[n], outputs[n]) for n in order],
        ),
        Case(
            "flows",
            "run, resume de chaque node, status (écritures disque comprises)",
            desc,
            run_to_completion,
            status_canon,
        ),
    ]


def cases_dispatch(ctx: Context) -> list[Case]:
    from grimoire.missions.dispatch import (
        _extract_cost_usd,
        _extract_uncertainties,
        _looks_rate_limited,
        _matches_review_surface,
        _tier_chain,
        render_invocation,
        start_tier_for,
    )
    from grimoire.missions.verifiability import Verifiability
    from grimoire.providers.registry import read_registry
    from grimoire.providers.routing import candidates

    outputs = [_read(p) for p in sorted((FIXTURES / "dispatch_worker_outputs").glob("*.txt"))]

    def analyse(_backend: str) -> Any:
        rows = []
        for stdout in outputs:
            uncertainties, warnings = _extract_uncertainties(stdout)
            rows.append(
                (
                    _extract_cost_usd(stdout),
                    [u.to_dict() for u in uncertainties],
                    len(warnings),
                    _looks_rate_limited(stdout),
                )
            )
        return rows

    # Les cas de tests/unit/test_dispatch_rust_parity.py.
    templates = (
        ("claude -p {prompt} --model {model}", "bonjour", "sonnet"),
        ('claude -p "{prompt}" --model {model}', "avec espaces", "sonnet"),
        ("claude --model {model}", "sans placeholder prompt", "sonnet"),
        ("claude {model} --tag {model}", "p", "sonnet"),
        ("claude --json '{}' {prompt}", "p", "sonnet"),
    )
    tiers = ("cheap", "mid", "strong")
    surfaces = ("*/cli/*", "*/mcp/*", "*schema*", "*/verifiability.py", "*/hooks/*")
    review_paths = (
        "src/grimoire/cli/app.py",
        "src/grimoire/mcp/server.py",
        "core/schema_extra.py",
        "grimoire/missions/verifiability.py",
        ".github/hooks/scripts/gate.sh",
        "README.md",
        "src/grimoire/tools/lint.py",
    )

    def small(_backend: str) -> Any:
        return (
            [render_invocation(t, prompt=p, model=m) for t, p, m in templates],
            [_tier_chain(s, m) for s in tiers for m in (*tiers, None)],
            [start_tier_for(v) for v in Verifiability],
        )

    registry_root = ctx.tmp / "registry-root"
    standard = registry_root / "_grimoire" / "standard"
    standard.mkdir(parents=True, exist_ok=True)
    entries = "\n".join(
        f'  - id: "{pid}"\n'
        f"    enabled: true\n"
        f'    provider_type: "hosted"\n    data_policy:\n      allowed_data_classes: ["project-source"]\n'
        f'    allowed_capabilities: ["chat"]\n'
        f'    default_models: ["{pid}-model"]\n'
        f'    currency: "api"\n'
        f'    invocation: "true {{prompt}} {{model}}"\n'
        f"    models:\n"
        f'      - id: "{pid}-model"\n'
        f'        tier: "cheap"\n'
        f"    fallback_order: []\n"
        for pid in ("p1", "p2", "p3")
    )
    (standard / "llm-provider-registry.yaml").write_text(
        '$schema: "grimoire-llm-provider-registry/v1"\n'
        'metadata:\n  project: "demo"\n  owner: ""\n  policy: "No provider/model call outside this registry."\n'
        f"providers:\n{entries}\n"
        'routing:\n  default_data_class: "project-source"\n  default_provider: ""\n'
        "  default_fallback_chain: []\n  require_capability_match: true\n  require_data_policy_match: true\n",
        encoding="utf-8",
    )

    return [
        Case(
            "dispatch",
            "analyse d'une sortie d'ouvrier (coût, incertitudes, limite de débit)",
            f"{len(outputs)} sorties du corpus dispatch_worker_outputs, {_size(outputs)} octets",
            analyse,
        ),
        Case(
            "dispatch",
            "render_invocation, _tier_chain, start_tier_for",
            f"{len(templates)} gabarits, {len(tiers) * 4} chaînes de paliers, {len(tuple(Verifiability))} classes",
            small,
        ),
        Case(
            "dispatch",
            "_matches_review_surface",
            f"{len(review_paths)} chemins contre {len(surfaces)} motifs",
            lambda _b: [_matches_review_surface(p, surfaces) for p in review_paths],
        ),
        Case(
            "dispatch",
            "providers.routing.candidates (lecture du registre comprise)",
            "registre de 3 fournisseurs, comme le test de parité",
            lambda _b: [p.id for p in candidates(registry_root, "cheap")],
            shared_fn=lambda: read_registry(registry_root),
        ),
    ]


def cases_traces(ctx: Context) -> list[Case]:
    from grimoire.proposals import _build_proposal, _employment_clause, _guess_tools, _slugify, _sync_decision
    from grimoire.traces.ledger import TraceLedger

    fixture = FIXTURES / "traces_ledgers" / "10_large_multi_agent.jsonl"
    text = _read(fixture)
    small_dir = ctx.tmp / "ledger-fixture"
    big_dir = ctx.tmp / "ledger-x100"
    for directory, content in ((small_dir, text), (big_dir, text * 100)):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "traces.jsonl").write_text(content, encoding="utf-8")
    agents = ["concierge", "scribe", "generic-dev", "infra-ops"]
    now = datetime(2026, 11, 1, tzinfo=UTC)

    def aggregate(directory: Path) -> Callable[[str], Any]:
        def run(_backend: str) -> Any:
            # Les cinq lectures de test_ledger_fixture_corpus_agrees_across_backends.
            ledger = TraceLedger(directory)
            fresh = ledger.agent_freshness_report(agents, threshold_days=30, now=now)
            return (
                ledger.agent_dispatch_counts(),
                ledger.agent_miss_counts(),
                ledger.oldest_started_at(),
                (fresh.judged, fresh.journal_span_days, [(e.name, e.last_seen, e.stale) for e in fresh.entries]),
                ledger.dispatch_outcome_stats().to_dict(),
            )

        return run

    def load_only(directory: Path) -> Callable[[], Any]:
        def run() -> Any:
            # Chacune des cinq lectures relit et analyse le journal en Python.
            ledger = TraceLedger(directory)
            return [ledger._load_all() for _ in range(5)]

        return run

    def proposals(_backend: str) -> Any:
        return (
            [_slugify(t) for t in ("Résilience réseau", "Terraform/Ansible", "Chaos Engineering !!", "", "   ")],
            _guess_tools("infra", "ansible-homelab"),
            _guess_tools("design", "figma-review"),
            _employment_clause("terraform", "infra", ""),
            [_sync_decision(t, c, None) for t in (-5, 0, 1, 2, 5) for c in (0, 1, 2, 3, 4, 10)],
            _build_proposal(
                specialty="terraform",
                count=2,
                category="infra",
                fallback_agent="",
                carrier="",
                carrier_reason="persona d'entrée exclue, aucun porteur : agent",
                first_seen="2026-01-01T00:00:00+00:00",
                last_seen="2026-01-02T00:00:00+00:00",
            ),
        )

    n_small = len(text.splitlines())
    return [
        Case(
            "traces",
            "cinq agrégations du journal (choix, non-choix, ancienneté, fraîcheur, coût)",
            f"10_large_multi_agent.jsonl, {n_small} enregistrements, {fixture.stat().st_size} octets",
            aggregate(small_dir),
            shared_fn=load_only(small_dir),
        ),
        Case(
            "traces",
            "cinq agrégations du journal (choix, non-choix, ancienneté, fraîcheur, coût)",
            f"la même fixture répétée 100 fois, {n_small * 100} enregistrements",
            aggregate(big_dir),
            realistic=False,
            shared_fn=load_only(big_dir),
        ),
        Case(
            "traces",
            "propositions : nommage, outils, clause d'emploi, décision de synchronisation",
            "chaînes de référence du test de parité, grille de 30 décisions",
            proposals,
            # created_at est l'horloge de l'appel, pas un résultat du backend.
            lambda r: repr((*r[:-1], dataclasses.replace(r[-1], created_at=""))),
        ),
    ]


def cases_acp(ctx: Context) -> list[Case]:
    from grimoire.acp.machine import replay

    paths = sorted((FIXTURES / "acp").glob("*.transcript.json"))
    transcripts = [json.loads(_read(p)) for p in paths]
    messages = sum(len(t) for t in transcripts)
    return [
        Case(
            "acp",
            "replay de chaque transcription",
            f"{len(transcripts)} transcriptions enregistrées, {messages} messages, "
            f"{sum(p.stat().st_size for p in paths)} octets",
            lambda backend: [replay(t, backend) for t in transcripts],
            lambda r: json.dumps(r, sort_keys=True),
        ),
    ]


CASE_BUILDERS: dict[str, Callable[[Context], list[Case]]] = {
    "policies": cases_policies,
    "schema": cases_schema,
    "hosts": cases_hosts,
    "flows": cases_flows,
    "dispatch": cases_dispatch,
    "traces": cases_traces,
    "acp": cases_acp,
}


# --------------------------------------------------------------------------
# Harnais de mesure
# --------------------------------------------------------------------------


def _quartiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    if len(ordered) >= 2:
        q1, _median, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
    else:
        q1 = q3 = ordered[0]
    return {"median": statistics.median(ordered), "q1": q1, "q3": q3, "min": ordered[0], "max": ordered[-1]}


def _batch(fn: Callable[[str], Any], backend: str, number: int) -> tuple[float, float]:
    """Microsecondes par appel sur un lot de *number* appels : (horloge murale, CPU du processus)."""
    cpu = time.process_time_ns()
    start = time.perf_counter_ns()
    for _ in range(number):
        fn(backend)
    wall_ns = time.perf_counter_ns() - start
    cpu_ns = time.process_time_ns() - cpu
    return wall_ns / number / 1000, cpu_ns / number / 1000


def measure_case(case: Case, reps: int, sample_ms: float) -> dict[str, Any]:
    crate = CRATE_BY_KEY[case.crate]
    rust_ok = extension_available(crate)
    backends = ("python", "rust") if rust_ok else ("python",)
    result: dict[str, Any] = {
        "crate": case.crate,
        "case": case.name,
        "input": case.input_desc,
        "realistic": case.realistic,
        "reps": reps,
    }
    previous = os.environ.get(crate.env_var)
    try:
        os.environ[crate.env_var] = "python"
        reference = case.canon(case.fn("python"))
        if rust_ok:
            os.environ[crate.env_var] = "rust"
            result["same_result"] = case.canon(case.fn("rust")) == reference
        # Taille de lot calée sur le chemin Python : un échantillon dure au moins sample_ms.
        os.environ[crate.env_var] = "python"
        once_us = min(_batch(case.fn, "python", 1)[0] for _ in range(3))
        number = max(1, min(50_000, math.ceil(sample_ms * 1000 / max(once_us, 0.01))))
        result["calls_per_sample"] = number
        samples: dict[str, list[float]] = {backend: [] for backend in backends}
        cpu_samples: dict[str, list[float]] = {backend: [] for backend in backends}
        for backend in backends:  # échauffement, hors mesure
            os.environ[crate.env_var] = backend
            _batch(case.fn, backend, number)
        for rep in range(reps):
            for backend in backends if rep % 2 == 0 else tuple(reversed(backends)):
                os.environ[crate.env_var] = backend
                wall_us, cpu_us = _batch(case.fn, backend, number)
                samples[backend].append(wall_us)
                cpu_samples[backend].append(cpu_us)
        if case.shared_fn is not None:
            shared = case.shared_fn
            result["shared_us"] = _quartiles([_batch(lambda _b: shared(), "python", number)[0] for _ in range(reps)])
    finally:
        if previous is None:
            os.environ.pop(crate.env_var, None)
        else:
            os.environ[crate.env_var] = previous
    result["python_us"] = _quartiles(samples["python"])
    result["python_cpu_us"] = _quartiles(cpu_samples["python"])
    if rust_ok:
        result["rust_us"] = _quartiles(samples["rust"])
        result["rust_cpu_us"] = _quartiles(cpu_samples["rust"])
        py, rs = result["python_us"], result["rust_us"]
        result["ratio_python_over_rust"] = py["median"] / rs["median"]
        cpu_rs = result["rust_cpu_us"]["median"]
        result["ratio_python_over_rust_cpu"] = result["python_cpu_us"]["median"] / cpu_rs if cpu_rs else None
        result["delta_us"] = py["median"] - rs["median"]
        # Lecture descriptive, pas un test statistique : les deux intervalles
        # interquartiles se recouvrent-ils ?
        if py["q1"] > rs["q3"]:
            result["reading"] = "Rust plus rapide"
        elif rs["q1"] > py["q3"]:
            result["reading"] = "Rust plus lent"
        else:
            result["reading"] = "indiscernable"
    else:
        result["rust_us"] = None
        result["not_measured"] = f"{crate.module} n'est pas installé dans {sys.executable}"
    return result


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------

_IMPORT_PROBE = (
    "import sys, time\n"
    "start = time.perf_counter_ns()\n"
    "try:\n"
    "    import {module}\n"
    "    found = 1\n"
    "except ImportError:\n"
    "    found = 0\n"
    "sys.stdout.write(f'{{time.perf_counter_ns() - start}} {{found}}')\n"
)


def measure_import(module: str, python_exe: str, reps: int, env: dict[str, str]) -> dict[str, Any]:
    """Temps de ``import <module>`` dans un interprète neuf, en microsecondes."""
    samples: list[float] = []
    found = None
    for _ in range(reps + 1):  # le premier lancement réchauffe le cache disque, il est écarté
        proc = subprocess.run(
            [python_exe, "-c", _IMPORT_PROBE.format(module=module)], env=env, capture_output=True, text=True, check=True
        )
        elapsed_ns, found_flag = proc.stdout.split()
        samples.append(int(elapsed_ns) / 1000)
        found = found_flag == "1"
    return {"found": found, "us": _quartiles(samples[1:])}


# --------------------------------------------------------------------------
# Bout en bout : un processus par appel
# --------------------------------------------------------------------------

# Le même amorçage dans tous les bras : le chercheur est toujours installé, il
# ne masque les extensions que si GRIMOIRE_BENCH_BLOCK_CORES=1. La différence
# entre deux bras ne peut donc pas venir de l'amorçage lui-même.
_BOOT = """
import importlib.abc, os, sys
_BLOCK = os.environ.get("GRIMOIRE_BENCH_BLOCK_CORES") == "1"
class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if _BLOCK and name.startswith("grimoire_") and name.endswith("_core"):
            raise ImportError(name + " masqué par le banc")
        return None
sys.meta_path.insert(0, _Finder())
__ENTRY__
raise SystemExit(code)
"""

# Diagnostic hors chronométrage : quelles extensions le processus charge, et
# combien d'appels y entrent réellement.
_DIAG = """
import importlib, json, os, sys
counts = {}
def _wrap(key, fn):
    def inner(*args, **kwargs):
        counts[key] = counts.get(key, 0) + 1
        return fn(*args, **kwargs)
    return inner
if os.environ.get("GRIMOIRE_BENCH_DIAG") == "counts":
    for name in json.loads(os.environ["GRIMOIRE_BENCH_CORES"]):
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        for attr in dir(module):
            obj = getattr(module, attr)
            if not attr.startswith("_") and callable(obj):
                setattr(module, attr, _wrap(name + "." + attr, obj))
__ENTRY__
loaded = sorted(m for m in sys.modules if m.startswith("grimoire_") and m.endswith("_core") and "." not in m)
sys.stderr.write("\\nGRIMOIRE_BENCH_DIAG=" + json.dumps({"loaded": loaded, "calls": counts}))
raise SystemExit(code)
"""

_ENTRIES = {
    # grimoire-hook = grimoire.hosts.runtime:main (pyproject.toml, [project.scripts])
    "grimoire-hook": "from grimoire.hosts.runtime import main\ncode = main()",
    # grimoire = grimoire.cli.app:cli
    "grimoire": (
        "from grimoire.cli.app import cli\n"
        "sys.argv[0] = 'grimoire'\n"
        "try:\n    cli()\n    code = 0\nexcept SystemExit as exc:\n    code = exc.code"
    ),
}


_ARM_BLOCKED = "sans extension (import masqué)"
_ARM_DEFAULT = "extensions installées, backend par défaut"
_ARM_FORCED = "extensions installées, backend forcé à python"
_ARM_PURE_VENV = "script console, venv sans extension"
_ARM_RUST_VENV = "script console, venv avec extensions"
#: (bras sans, bras avec) comparés répétition par répétition.
_PAIRS = ((_ARM_BLOCKED, _ARM_DEFAULT), (_ARM_BLOCKED, _ARM_FORCED), (_ARM_PURE_VENV, _ARM_RUST_VENV))


@dataclass
class Target:
    label: str
    script: str  # clé de _ENTRIES : le script console lancé
    args: list[str]
    stdin: str
    origin: str
    #: Crates dont cette commande est le point d'appel réel.
    crates: tuple[str, ...]


def _targets(project: Path) -> list[Target]:
    targets = []
    for event, name, origin, crates in (
        ("PreToolUse", "01-pretooluse-control-allow.json", "charge utile capturée", ("policies", "traces")),
        ("SessionStart", "10-session-start-agent.json", "forme documentée, non capturée", ("hosts",)),
    ):
        fixture = json.loads(_read(FIXTURES / "hosts" / "claude-code" / name))
        payload = dict(fixture["payloads"][event])
        payload["cwd"] = str(project)
        targets.append(
            Target(
                f"grimoire-hook {event}",
                "grimoire-hook",
                ["--host", "claude-code", "--event", event, "--project-root", str(project)],
                json.dumps(payload),
                f"tests/fixtures/hosts/claude-code/{name} ({origin})",
                crates,
            )
        )
    targets.append(
        Target(
            "grimoire host sync --host claude --dry-run",
            "grimoire",
            ["host", "sync", "--project-root", str(project), "--host", "claude", "--dry-run"],
            "",
            "projet `grimoire init`",
            ("hosts",),
        )
    )
    targets.append(
        Target("grimoire check", "grimoire", ["check", str(project)], "", "projet `grimoire init`", ("schema",))
    )
    return targets


def _children_cpu() -> float | None:
    if resource is None:
        return None
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def _run_process(cmd: list[str], stdin: str, env: dict[str, str], cwd: Path) -> tuple[float, float | None, str, int]:
    """(durée murale en ms, CPU utilisateur+système du processus en ms, stdout, code de sortie)."""
    cpu_before = _children_cpu()
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=cwd, env=env, input=stdin, capture_output=True, text=True)
    wall = (time.perf_counter() - start) * 1000
    cpu_after = _children_cpu()
    cpu = (cpu_after - cpu_before) * 1000 if cpu_before is not None and cpu_after is not None else None
    return wall, cpu, proc.stdout, proc.returncode


def measure_interpreter_floor(ctx: Context, reps: int, env: dict[str, str]) -> dict[str, Any]:
    """``python -c pass`` : ce que coûte un processus qui ne fait rien, sur cette machine, à cet instant."""
    wall: list[float] = []
    cpu: list[float] = []
    for _ in range(reps + 1):
        wall_ms, cpu_ms, _stdout, _code = _run_process([sys.executable, "-c", "pass"], "", env, ctx.project)
        wall.append(wall_ms)
        if cpu_ms is not None:
            cpu.append(cpu_ms)
    return {"reps": reps, "wall_ms": _quartiles(wall[1:]), "cpu_ms": _quartiles(cpu[1:]) if cpu[1:] else None}


def measure_processes(
    ctx: Context, reps: int, env: dict[str, str], pure_python: str | None, only: set[str]
) -> list[dict[str, Any]]:
    any_ext = any(extension_available(c) for c in CRATES)
    forced_python = {c.env_var: "python" for c in CRATES}
    results = []
    for target in _targets(ctx.project):
        if not only.intersection(target.crates):
            continue
        print(f"… bout en bout : {target.label}", file=sys.stderr)
        boot = [sys.executable, "-c", _BOOT.replace("__ENTRY__", _ENTRIES[target.script]), *target.args]
        arms: list[tuple[str, list[str], dict[str, str]]] = [
            (_ARM_BLOCKED, boot, {**env, "GRIMOIRE_BENCH_BLOCK_CORES": "1"}),
        ]
        if any_ext:
            arms.append((_ARM_DEFAULT, boot, env))
            arms.append((_ARM_FORCED, boot, {**env, **forced_python}))
        if pure_python:
            for label, exe in ((_ARM_PURE_VENV, pure_python), (_ARM_RUST_VENV, sys.executable)):
                console = Path(exe).parent / target.script
                if console.exists():
                    arms.append((label, [str(console), *target.args], env))

        wall: dict[str, list[float]] = {label: [] for label, _cmd, _env in arms}
        cpu: dict[str, list[float]] = {label: [] for label, _cmd, _env in arms}
        outputs: dict[str, str] = {}
        codes: dict[str, int] = {}
        for label, cmd, arm_env in arms:  # échauffement, hors mesure
            _wall, _cpu, stdout, code = _run_process(cmd, target.stdin, arm_env, ctx.project)
            outputs[label] = stdout
            codes[label] = code
        for rep in range(reps):
            shift = rep % len(arms)
            for label, cmd, arm_env in arms[shift:] + arms[:shift]:
                wall_ms, cpu_ms, _stdout, _code = _run_process(cmd, target.stdin, arm_env, ctx.project)
                wall[label].append(wall_ms)
                if cpu_ms is not None:
                    cpu[label].append(cpu_ms)

        diag: dict[str, Any] = {}
        if any_ext:
            diag_cmd = [sys.executable, "-c", _DIAG.replace("__ENTRY__", _ENTRIES[target.script]), *target.args]
            cores = json.dumps([c.module for c in CRATES])
            for mode in ("loaded", "calls"):
                proc = subprocess.run(
                    diag_cmd,
                    cwd=ctx.project,
                    env={
                        **env,
                        "GRIMOIRE_BENCH_DIAG": "counts" if mode == "calls" else "loaded",
                        "GRIMOIRE_BENCH_CORES": cores,
                    },
                    input=target.stdin,
                    capture_output=True,
                    text=True,
                )
                match = re.search(r"GRIMOIRE_BENCH_DIAG=(\{.*\})\s*$", proc.stderr, re.DOTALL)
                if match:
                    diag[mode] = json.loads(match.group(1))[mode]
        # Les bras sont alternés à chaque répétition : l'écart se lit répétition par
        # répétition (sans − avec), ce qui retire la dérive commune de la machine.
        paired = []
        for without, with_ext in _PAIRS:
            if without not in wall or with_ext not in wall:
                continue
            entry: dict[str, Any] = {"without": without, "with": with_ext}
            for unit, series in (("wall_ms", wall), ("cpu_ms", cpu)):
                if len(series[without]) != reps or len(series[with_ext]) != reps:
                    entry[unit] = None
                    continue
                stats = _quartiles([a - b for a, b in zip(series[without], series[with_ext], strict=True)])
                stats["reading"] = (
                    "plus rapide avec" if stats["q1"] > 0 else "plus lent avec" if stats["q3"] < 0 else "indiscernable"
                )
                entry[unit] = stats
            paired.append(entry)
        results.append(
            {
                "target": target.label,
                "input": target.origin,
                "reps": reps,
                "arms": {
                    label: {
                        "wall_ms": _quartiles(wall[label]),
                        "cpu_ms": _quartiles(cpu[label]) if cpu[label] else None,
                    }
                    for label in wall
                },
                "paired": paired,
                "exit_codes": codes,
                "same_stdout": len(set(outputs.values())) == 1,
                "extensions_loaded": diag.get("loaded", NOT_MEASURED),
                "extension_calls": diag.get("calls", NOT_MEASURED),
            }
        )
    return results


# --------------------------------------------------------------------------
# Coûts
# --------------------------------------------------------------------------


def _lines(path: Path) -> int:
    return len(_read(path).splitlines()) if path.is_file() else 0


def workflow_job_lines() -> dict[str, int]:
    """Lignes de ``rust-cores.yml`` par job (de l'en-tête du job au suivant)."""
    if not WORKFLOW.is_file():
        return {}
    lines = _read(WORKFLOW).splitlines()
    starts = [(i, m.group(1)) for i, line in enumerate(lines) if (m := re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line))]
    try:
        jobs_at = next(i for i, line in enumerate(lines) if line.startswith("jobs:"))
    except StopIteration:
        return {}
    starts = [(i, name) for i, name in starts if i > jobs_at]
    out = {"(en-tête)": starts[0][0] if starts else len(lines)}
    for (start, name), (end, _next) in zip(starts, [*starts[1:], (len(lines), "")], strict=True):
        out[name] = end - start
    return out


def _git_lines(args: list[str]) -> list[str] | None:
    try:
        proc = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


def crate_history(crate: Crate) -> dict[str, Any]:
    """Qui a bougé depuis le port : le crate, le module Python qu'il double, ou les deux ensemble."""
    no_git = f"{NOT_MEASURED} : historique git indisponible"
    crate_path = f"rust/grimoire-{crate.key}-core"
    crate_log = _git_lines(["log", "--format=%H %ad", "--date=short", "--", crate_path])
    if not crate_log:
        return {"commits": no_git, "ported_on": no_git, "python_commits_since": no_git, "joint_commits": no_git}
    port_sha, ported_on = crate_log[-1].split()[:2]
    # Le commit de port touche par construction le crate et le module : il est
    # compté à part, pour que « commits joints » ne mesure que la suite.
    later_crate_shas = {line.split()[0] for line in crate_log} - {port_sha}
    modules = [f"src/{name.replace('.', '/')}.py" for name in crate.python_modules]
    python_log = _git_lines(["log", "--format=%H", f"{port_sha}..HEAD", "--", *modules]) or []
    python_shas = set(python_log)
    return {
        "commits": len(crate_log),
        "ported_on": ported_on,
        "python_commits_since": len(python_shas),
        "joint_commits": len(python_shas & later_crate_shas),
    }


def crate_costs(crate: Crate, jobs: dict[str, int]) -> dict[str, Any]:
    rust_sources = sorted((crate.directory / "src").rglob("*.rs"))
    spec = importlib.util.find_spec(crate.module)
    binary: int | str = f"{NOT_MEASURED} : {crate.module} n'est pas installé"
    version = None
    if spec is not None and spec.submodule_search_locations:
        binaries = [p for loc in spec.submodule_search_locations for p in Path(loc).glob("*.so")]
        binaries += [p for loc in spec.submodule_search_locations for p in Path(loc).glob("*.pyd")]
        if binaries:
            binary = sum(p.stat().st_size for p in binaries)
        version = getattr(importlib.import_module(crate.module), "__version__", None)
    ci_jobs = {name: n for name, n in jobs.items() if name.startswith(f"rust-{crate.key}-")}
    return {
        "crate": crate.key,
        "rust_lines": sum(_lines(p) for p in rust_sources),
        "manifest_lines": _lines(crate.directory / "Cargo.toml") + _lines(crate.directory / "pyproject.toml"),
        "lock_lines": _lines(crate.directory / "Cargo.lock"),
        "ci_jobs": ci_jobs,
        "ci_lines": sum(ci_jobs.values()),
        "parity_tests": {rel: _lines(ROOT / rel) if (ROOT / rel).is_file() else "absent" for rel in crate.parity_tests},
        "python_modules": list(crate.python_modules),
        "default_backend": crate.default_backend,
        "env_var": crate.env_var,
        **crate_history(crate),
        "binary_bytes": binary,
        "extension_version": version,
    }


def ci_job_durations(repo: str, runs: int, since: str | None = None) -> dict[str, Any] | str:
    """Durée réelle des jobs de ``rust-cores.yml`` sur les *runs* dernières exécutions terminées.

    Lit l'API GitHub par ``gh`` (un appel par exécution). Un job sauté ou annulé
    ne compte pas : seuls ceux qui ont tourné jusqu'à un verdict sont additionnés.
    *since* (horodatage ISO en UTC, ``2026-11-06T16:29:12Z``) écarte les
    exécutions créées avant : le workflow change, et additionner deux régimes
    ne décrit ni l'un ni l'autre.
    """

    def api(path: str) -> Any:
        return json.loads(subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout)

    def seconds(job: dict[str, Any]) -> float:
        started = datetime.fromisoformat(job["started_at"])
        completed = datetime.fromisoformat(job["completed_at"])
        return (completed - started).total_seconds()

    try:
        listed: list[dict[str, Any]] = []
        page = 1
        while len(listed) < runs:
            batch = api(f"repos/{repo}/actions/workflows/rust-cores.yml/runs?per_page=100&status=completed&page={page}")
            if not batch["workflow_runs"]:
                break
            listed.extend(batch["workflow_runs"])
            if since and batch["workflow_runs"][-1]["created_at"] < since:
                break
            page += 1
        # La pagination peut rendre deux fois la même exécution : une seule est gardée.
        listed = list({run["id"]: run for run in listed}.values())[:runs]
        if since:
            listed = [run for run in listed if run["created_at"] >= since]
        durations: dict[str, list[float]] = {}
        failures: dict[str, int] = {}
        for run in listed:
            for job in api(f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100")["jobs"]:
                if job["conclusion"] not in ("success", "failure") or not job.get("completed_at"):
                    continue
                durations.setdefault(job["name"], []).append(seconds(job))
                if job["conclusion"] == "failure":
                    failures[job["name"]] = failures.get(job["name"], 0) + 1
    except (OSError, subprocess.CalledProcessError, KeyError, ValueError) as exc:
        return f"{NOT_MEASURED} : API GitHub illisible par `gh` ({type(exc).__name__})"
    if not listed:
        return f"{NOT_MEASURED} : aucune exécution terminée de rust-cores.yml"
    return {
        "repo": repo,
        "runs": len(listed),
        "from": min(run["created_at"] for run in listed),
        "to": max(run["created_at"] for run in listed),
        "total_seconds": sum(sum(values) for values in durations.values()),
        "jobs": {
            name: {
                "ran": len(values),
                "failed": failures.get(name, 0),
                "median_s": statistics.median(values),
                "max_s": max(values),
                "total_s": sum(values),
            }
            for name, values in sorted(durations.items())
        },
    }


# --------------------------------------------------------------------------
# Environnement
# --------------------------------------------------------------------------


def _tool_version(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return f"{NOT_MEASURED} : `{cmd[0]}` introuvable"


def machine_info() -> dict[str, Any]:
    cpu = platform.processor() or NOT_MEASURED
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in _read(cpuinfo).splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    try:
        load = [round(v, 2) for v in os.getloadavg()]
    except OSError:
        load = None
    return {
        "cpu": cpu,
        "logical_cpus": os.cpu_count(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "rustc": _tool_version(["rustc", "--version"]),
        "cargo": _tool_version(["cargo", "--version"]),
        "loadavg_at_start": load,
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def clean_env(home: Path) -> dict[str, str]:
    """Environnement des sous-processus : HOME jetable, aucune variable du kit ni de l'hôte."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GRIMOIRE_", "CLAUDE"))}
    env["HOME"] = str(home)
    return env


def build_project(ctx: Context, env: dict[str, str]) -> None:
    """Un projet que personne n'a écrit pour le banc : ``grimoire init -y`` dans un HOME jetable."""
    ctx.project.mkdir(parents=True, exist_ok=True)
    ctx.home.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire.cli.app", "init", ".", "-y"],
        cwd=ctx.project,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


# --------------------------------------------------------------------------
# Rendu
# --------------------------------------------------------------------------


def _num(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _spread(stats: dict[str, float] | None, digits: int = 1) -> str:
    if stats is None:
        return NOT_MEASURED
    return f"{_num(stats['median'], digits)} [{_num(stats['q1'], digits)} ; {_num(stats['q3'], digits)}]"


def render(report: dict[str, Any]) -> str:
    out: list[str] = []
    machine = report["machine"]
    out.append(f"## Banc des cœurs Rust — grimoire-kit {report['kit_version']}\n")
    out.append(
        f"Machine : {machine['cpu']} ({machine['logical_cpus']} CPU logiques), {machine['platform']}, "
        f"{machine['python_implementation']} {machine['python']}, {machine['rustc']}. "
        f"Charge au départ : {machine['loadavg_at_start']}. Répétitions : {report['reps']}."
    )
    if report["reps"] < MIN_REPS:
        out.append(f"\n**Essai rapide : moins de {MIN_REPS} répétitions, ces chiffres ne se publient pas.**")

    out.append("\n### Par fonction (µs par appel, horloge murale : médiane [Q1 ; Q3])\n")
    out.append(
        "Python / Rust au-dessus de 1 : Rust plus rapide ; en dessous : Rust plus lent. La colonne CPU refait le "
        "rapport sur le temps CPU du processus, moins sensible à la charge de la machine. « Lecture » est "
        "descriptive, pas un test statistique : « indiscernable » quand les intervalles [Q1 ; Q3] des deux "
        "chemins se recouvrent.\n"
    )
    out.append(
        "| Crate | Fonction | Entrée | Python | Rust | Python / Rust | Python / Rust (CPU) | Lecture | Mêmes résultats |"
    )
    out.append("|---|---|---|---:|---:|---:|---:|---|---|")
    for row in report["functions"]:
        entry = row["input"] if row["realistic"] else f"{row['input']} — **fabriquée**"
        if row.get("rust_us"):
            ratio = f"{_num(row['ratio_python_over_rust'], 2)}×"
            cpu_ratio = row.get("ratio_python_over_rust_cpu")
            ratio_cpu = f"{_num(cpu_ratio, 2)}×" if cpu_ratio else NOT_MEASURED
            reading, same = row["reading"], ("oui" if row["same_result"] else "**non**")
            rust = _spread(row["rust_us"], 2)
        else:
            ratio = ratio_cpu = reading = same = NOT_MEASURED
            rust = f"{NOT_MEASURED} : {row.get('not_measured', '')}"
        out.append(
            f"| {row['crate']} | {row['case']} | {entry} | {_spread(row['python_us'], 2)} | {rust} "
            f"| {ratio} | {ratio_cpu} | {reading} | {same} |"
        )
    shared = [row for row in report["functions"] if row.get("shared_us")]
    if shared:
        out.append(
            "\nPart commune aux deux chemins (lecture et analyse faites en Python avant tout choix de backend) :\n"
        )
        out.append("| Crate | Fonction | Entrée | Part commune (µs) | Chemin Python complet (µs) |")
        out.append("|---|---|---|---:|---:|")
        for row in shared:
            out.append(
                f"| {row['crate']} | {row['case']} | {row['input']} | {_spread(row['shared_us'], 2)} "
                f"| {_spread(row['python_us'], 2)} |"
            )

    out.append("\n### Import dans un interprète neuf (µs : médiane [Q1 ; Q3])\n")
    out.append("| Extension | Import réussi (extension installée) | Import manqué (extension absente) |")
    out.append("|---|---:|---:|")
    for row in report["imports"]:
        present = _spread(row["present"]["us"]) if isinstance(row["present"], dict) else row["present"]
        absent = _spread(row["absent"]["us"]) if isinstance(row["absent"], dict) else row["absent"]
        out.append(f"| `{row['module']}` | {present} | {absent} |")

    out.append("\n### Bout en bout : un processus par appel (ms : médiane [Q1 ; Q3])\n")
    floor = report.get("interpreter_floor")
    if floor:
        cpu_floor = _spread(floor["cpu_ms"]) if floor["cpu_ms"] else NOT_MEASURED
        out.append(
            f"Plancher de la machine au moment de la mesure — `python -c pass`, {floor['reps']} répétitions : "
            f"{_spread(floor['wall_ms'])} ms d'horloge murale, {cpu_floor} ms de CPU.\n"
        )
    for proc in report["processes"]:
        reps = proc["reps"]
        same = "oui" if proc["same_stdout"] else "non"
        out.append(
            f"**`{proc['target']}`** — {proc['input']}, {reps} répétitions par bras. "
            f"Codes de sortie : {sorted(set(proc['exit_codes'].values()))}. Sortie standard identique dans tous les bras : {same}.\n"
        )
        out.append("| Bras | Durée murale (ms) | CPU du processus (ms) |")
        out.append("|---|---:|---:|")
        for label, stats in proc["arms"].items():
            cpu = _spread(stats["cpu_ms"]) if stats["cpu_ms"] else f"{NOT_MEASURED} : module `resource` absent"
            out.append(f"| {label} | {_spread(stats['wall_ms'])} | {cpu} |")
        if proc.get("paired"):
            out.append(
                "\nÉcart apparié, répétition par répétition (sans − avec, en ms ; positif : plus rapide avec) :\n"
            )
            out.append("| Sans | Avec | Écart mural (ms) | Lecture | Écart CPU (ms) | Lecture |")
            out.append("|---|---|---:|---|---:|---|")
            for pair in proc["paired"]:
                cells = []
                for unit in ("wall_ms", "cpu_ms"):
                    stats = pair[unit]
                    cells += [_spread(stats), stats["reading"]] if stats else [NOT_MEASURED, NOT_MEASURED]
                out.append(f"| {pair['without']} | {pair['with']} | {' | '.join(cells)} |")
        loaded = proc["extensions_loaded"]
        calls = proc["extension_calls"]
        loaded_text = ", ".join(f"`{m}`" for m in loaded) if isinstance(loaded, list) else loaded
        if isinstance(calls, dict):
            calls_text = ", ".join(f"`{k}` × {v}" for k, v in sorted(calls.items())) or "aucun"
        else:
            calls_text = calls
        out.append(
            f"\nExtensions chargées : {loaded_text or 'aucune'}. Appels entrés dans une extension : {calls_text}.\n"
        )

    out.append("### Coût par crate\n")
    out.append(
        "| Crate | Lignes Rust (`src/`) | Manifestes | Lignes de CI (jobs du crate) | Test de parité | "
        "Backend par défaut | Binaire (octets) |"
    )
    out.append("|---|---:|---:|---:|---|---|---:|")
    for row in report["costs"]:
        parity = ", ".join(f"`{rel}` ({n} lignes)" for rel, n in row["parity_tests"].items())
        out.append(
            f"| {row['crate']} | {row['rust_lines']} | {row['manifest_lines']} | {row['ci_lines']} | {parity} "
            f"| {row['default_backend']} | {row['binary_bytes']} |"
        )
    out.append("\nQui a bougé depuis le port (historique git, commit de port exclu des deux dernières colonnes) :\n")
    out.append(
        "| Crate | Porté le | Commits sur le crate (port compris) | Commits sur les modules Python doublés, après le port | "
        "dont commits qui touchent aussi le crate |"
    )
    out.append("|---|---|---:|---:|---:|")
    for row in report["costs"]:
        out.append(
            f"| {row['crate']} | {row['ported_on']} | {row['commits']} | {row['python_commits_since']} "
            f"| {row['joint_commits']} |"
        )
    workflow = report["workflow"]
    out.append(
        f"\n`rust-cores.yml` : {workflow['total_lines']} lignes, {workflow['jobs']} jobs ; "
        f"{workflow['shared_lines']} lignes communes aux sept crates (en-tête, `changes`, `rust-gate`)."
    )
    ci = report.get("ci")
    if isinstance(ci, str):
        out.append(f"\nDurée réelle des jobs de CI : {ci}.")
    elif ci:
        out.append(
            f"\nDurée réelle des jobs de CI — {ci['runs']} dernières exécutions terminées de `rust-cores.yml` sur "
            f"`{ci['repo']}`, du {ci['from']} au {ci['to']} ; {round(ci['total_seconds'])} s de jobs au total :\n"
        )
        out.append("| Job | Exécutions où il a tourné | En échec | Médiane (s) | Maximum (s) | Total (s) |")
        out.append("|---|---:|---:|---:|---:|---:|")
        for name, stats in ci["jobs"].items():
            out.append(
                f"| `{name}` | {stats['ran']} | {stats['failed']} | {_num(stats['median_s'])} "
                f"| {round(stats['max_s'])} | {round(stats['total_s'])} |"
            )
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--reps", type=int, default=MIN_REPS, help=f"Échantillons par bras (défaut et plancher publiable : {MIN_REPS})."
    )
    parser.add_argument(
        "--sample-ms", type=float, default=5.0, help="Durée minimale d'un échantillon par fonction (défaut : 5 ms)."
    )
    parser.add_argument(
        "--only", action="append", choices=sorted(CRATE_BY_KEY), help="Ne mesurer que ce crate (répétable)."
    )
    parser.add_argument(
        "--pure-python", default=None, help="Interprète d'un venv SANS extension (bras « absent » réel)."
    )
    parser.add_argument(
        "--process-reps", type=int, default=0, help="Répétitions des mesures de bout en bout (défaut : --reps)."
    )
    parser.add_argument("--skip-process", action="store_true", help="Sauter les mesures de bout en bout.")
    parser.add_argument("--skip-import", action="store_true", help="Sauter la mesure d'import.")
    parser.add_argument("--skip-functions", action="store_true", help="Sauter les mesures par fonction.")
    parser.add_argument(
        "--ci-runs",
        type=int,
        default=0,
        help="Lire par `gh` la durée des jobs de CI sur N exécutions (défaut : 0, non mesuré).",
    )
    parser.add_argument("--ci-repo", default="Guilhem-Bonnet/Grimoire-kit", help="Dépôt GitHub lu par --ci-runs.")
    parser.add_argument(
        "--ci-since",
        default=None,
        help="Ne garder que les exécutions créées depuis cet horodatage UTC (ex. 2026-11-06T16:29:12Z).",
    )
    parser.add_argument("--json-out", type=Path, default=None, help="Écrire le rapport complet en JSON.")
    parser.add_argument("--markdown-out", type=Path, default=None, help="Écrire les tableaux Markdown dans ce fichier.")
    args = parser.parse_args(argv)
    if args.reps < 1:
        parser.error("--reps doit valoir au moins 1")

    from grimoire.__version__ import __version__

    selected = [c for c in CRATES if not args.only or c.key in args.only]
    report: dict[str, Any] = {
        "kit_version": __version__,
        "python_exe": sys.executable,
        "pure_python_exe": args.pure_python,
        "reps": args.reps,
        "machine": machine_info(),
        "extensions": {c.module: extension_available(c) for c in CRATES},
        "functions": [],
        "imports": [],
        "processes": [],
    }

    real_home = os.environ.get("HOME")
    with tempfile.TemporaryDirectory(prefix="grimoire-rust-gain-") as tmp:
        ctx = Context(tmp=Path(tmp), project=Path(tmp) / "bench-project", home=Path(tmp) / "home")
        env = clean_env(ctx.home)
        build_project(ctx, env)
        # Les mesures en processus lisent le même HOME jetable que les sous-processus.
        os.environ["HOME"] = str(ctx.home)
        try:
            for crate in [] if args.skip_functions else selected:
                for case in CASE_BUILDERS[crate.key](ctx):
                    print(f"… {crate.key} : {case.name}", file=sys.stderr)
                    report["functions"].append(measure_case(case, args.reps, args.sample_ms))

            for crate in selected:
                row: dict[str, Any] = {"module": crate.module}
                if args.skip_import:
                    row["present"] = row["absent"] = f"{NOT_MEASURED} : --skip-import"
                else:
                    print(f"… import {crate.module}", file=sys.stderr)
                    here = measure_import(crate.module, sys.executable, args.reps, env)
                    slot = "present" if here["found"] else "absent"
                    row[slot] = here
                    other = "absent" if slot == "present" else "present"
                    row[other] = f"{NOT_MEASURED} : aucun interprète fourni pour ce bras"
                    if args.pure_python and slot == "present":
                        there = measure_import(crate.module, args.pure_python, args.reps, env)
                        row["absent"] = (
                            there
                            if not there["found"]
                            else (f"{NOT_MEASURED} : {crate.module} est installé dans --pure-python")
                        )
                report["imports"].append(row)

            if not args.skip_process:
                report["interpreter_floor"] = measure_interpreter_floor(ctx, args.process_reps or args.reps, env)
                report["processes"] = measure_processes(
                    ctx, args.process_reps or args.reps, env, args.pure_python, {c.key for c in selected}
                )
        finally:
            if real_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = real_home
            shutil.rmtree(ctx.tmp / "flows", ignore_errors=True)

    jobs = workflow_job_lines()
    report["costs"] = [crate_costs(crate, jobs) for crate in selected]
    per_crate = sum(n for name, n in jobs.items() if any(name.startswith(f"rust-{c.key}-") for c in CRATES))
    report["workflow"] = {
        "total_lines": _lines(WORKFLOW),
        "jobs": len([name for name in jobs if name != "(en-tête)"]),
        "job_lines": jobs,
        "shared_lines": _lines(WORKFLOW) - per_crate,
    }
    if args.ci_runs > 0:
        print(f"… durée des jobs de CI sur {args.ci_runs} exécutions", file=sys.stderr)
        report["ci"] = ci_job_durations(args.ci_repo, args.ci_runs, args.ci_since)
    else:
        report["ci"] = f"{NOT_MEASURED} (relancer avec --ci-runs N)"

    markdown = render(report)
    print(markdown)
    if args.markdown_out:
        args.markdown_out.write_text(markdown, encoding="utf-8")
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"JSON écrit : {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
