"""W4-05 — export OTLP/HTTP réel et intervalles de confiance sur le SLO.

Le kit n'écrivait que du JSONL OTel : aucun client OTLP, donc aucun collecteur
ne pouvait recevoir le lot. Ces tests lancent un collecteur local factice
(``127.0.0.1``, port éphémère, aucun réseau) et vérifient ce qui arrive sur le
fil, pas ce que le code croit avoir envoyé.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.agentic_standard import verify_standard_profile
from grimoire.core.standard_generation import STANDARD_DIR, TRACES_DIR
from grimoire.traces.intervals import ratio_interval95, wilson_interval95
from grimoire.traces.ledger import DISPATCH_OUTCOME_TAG, TraceLedger
from grimoire.traces.otlp import OtlpExportError, export_otlp
from grimoire.traces.schemas import TraceOutcome


class _Collector:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.reply: bytes = b"{}"
        collector = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                collector.requests.append(
                    {"path": self.path, "headers": dict(self.headers), "body": json.loads(body)}
                )
                self.send_response(collector.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(collector.reply)))
                self.end_headers()
                self.wfile.write(collector.reply)

            def log_message(self, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def spans(self) -> list[dict[str, Any]]:
        return [
            span
            for req in self.requests
            for rs in req["body"]["resourceSpans"]
            for ss in rs["scopeSpans"]
            for span in ss["spans"]
        ]


@pytest.fixture
def collector() -> Iterator[_Collector]:
    c = _Collector()
    c.thread.start()
    try:
        yield c
    finally:
        c.server.shutdown()
        c.server.server_close()
        c.thread.join(timeout=5)


def _attrs(span: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kv in span["attributes"]:
        (kind, value), = kv["value"].items()
        out[kv["key"]] = int(value) if kind == "intValue" else value
    return out


def _record(ledger: TraceLedger, run_id: str = "RUN-1", **kw: Any) -> None:
    ledger.record(
        run_id=run_id,
        workflow_instance_id="WFI-1",
        mission_id="MIS-1",
        task_id="GAO-1",
        recipe_id="recipe.x",
        outcome=TraceOutcome.SUCCESS,
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:01:00+00:00",
        agent_id="dev",
        model="claude-sonnet-4-6",
        tool_calls=[{"tool": "grep", "verdict": "allow", "latency_ms": 5.0}],
        **kw,
    )


def test_collecteur_local_recoit_le_lot_otlp(tmp_path: Path, collector: _Collector) -> None:
    ledger = TraceLedger(tmp_path)
    _record(ledger, token_usage={"prompt_tokens": 120, "completion_tokens": 30, "estimated_cost_usd": 0.5})

    sent = export_otlp(ledger, collector.url)

    assert sent == 2  # invoke_agent + execute_tool
    assert [r["path"] for r in collector.requests] == ["/v1/traces"]
    assert collector.requests[0]["headers"]["Content-Type"] == "application/json"
    spans = collector.spans()
    assert {s["name"] for s in spans} == {"invoke_agent dev", "execute_tool grep"}
    parent = next(s for s in spans if "parentSpanId" not in s)
    child = next(s for s in spans if "parentSpanId" in s)
    assert child["parentSpanId"] == parent["spanId"] and child["traceId"] == parent["traceId"]
    # Encodage OTLP/JSON : uint64 en chaîne, énumérations entières, attributs en KeyValue.
    assert isinstance(parent["startTimeUnixNano"], str) and int(parent["endTimeUnixNano"]) > int(parent["startTimeUnixNano"])
    assert parent["kind"] == 1 and parent["status"]["code"] == 1


def test_attributs_gen_ai_usage_et_statut_de_cout_sur_le_fil(tmp_path: Path, collector: _Collector) -> None:
    ledger = TraceLedger(tmp_path)
    _record(ledger, token_usage={"prompt_tokens": 120, "completion_tokens": 30, "estimated_cost_usd": 0.5})
    _record(ledger, "RUN-2", token_usage={"prompt_tokens": 7})  # aucun coût rapporté

    export_otlp(ledger, collector.url)

    parents = {_attrs(s)["grimoire.run_id"]: _attrs(s) for s in collector.spans() if "parentSpanId" not in s}
    assert parents["RUN-1"]["gen_ai.usage.input_tokens"] == 120
    assert parents["RUN-1"]["gen_ai.usage.output_tokens"] == 30
    assert parents["RUN-1"]["grimoire.cost.status"] == "exact"
    assert parents["RUN-1"]["grimoire.cost.usd"] == 0.5
    # Un coût inconnu reste inconnu : statut explicite, aucun montant inventé.
    assert parents["RUN-2"]["grimoire.cost.status"] == "unknown"
    assert "grimoire.cost.usd" not in parents["RUN-2"]


def test_jsonl_otel_v2_reste_lisible_et_porte_le_statut_de_cout(tmp_path: Path) -> None:
    ledger = TraceLedger(tmp_path)
    _record(ledger, token_usage={"prompt_tokens": 1, "completion_tokens": 2, "estimated_cost_usd": 0.1})
    out = tmp_path / "otel.jsonl"

    ledger.export_otel_jsonl(out)

    parent = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert parent["resource"]["attributes"]["grimoire.schema_version"] == "grimoire.otel.v2"
    assert parent["attributes"]["gen_ai.usage.input_tokens"] == 1  # clé v2 inchangée
    assert parent["attributes"]["grimoire.cost.status"] == "exact"  # ajout, jamais un renommage


def test_refus_du_collecteur_est_une_erreur_jamais_un_succes_silencieux(tmp_path: Path, collector: _Collector) -> None:
    ledger = TraceLedger(tmp_path)
    _record(ledger)
    collector.status = 503

    with pytest.raises(OtlpExportError, match="503"):
        export_otlp(ledger, collector.url)


def test_rejet_partiel_est_une_erreur(tmp_path: Path, collector: _Collector) -> None:
    ledger = TraceLedger(tmp_path)
    _record(ledger)
    collector.reply = json.dumps({"partialSuccess": {"rejectedSpans": "1", "errorMessage": "trop vieux"}}).encode()

    with pytest.raises(OtlpExportError, match="trop vieux"):
        export_otlp(ledger, collector.url)


def test_collecteur_injoignable_est_une_erreur(tmp_path: Path) -> None:
    ledger = TraceLedger(tmp_path)
    _record(ledger)

    with pytest.raises(OtlpExportError):
        export_otlp(ledger, "http://127.0.0.1:9", timeout=1.0)  # port discard, personne n'écoute


def test_schema_d_url_refuse(tmp_path: Path) -> None:
    ledger = TraceLedger(tmp_path)
    _record(ledger)

    with pytest.raises(OtlpExportError, match="http"):
        export_otlp(ledger, "file:///etc/passwd")


def test_journal_vide_n_envoie_rien(tmp_path: Path, collector: _Collector) -> None:
    assert export_otlp(TraceLedger(tmp_path), collector.url) == 0
    assert collector.requests == []


def test_lots_bornes(tmp_path: Path, collector: _Collector) -> None:
    ledger = TraceLedger(tmp_path)
    for i in range(3):
        _record(ledger, f"RUN-{i}")

    assert export_otlp(ledger, collector.url, batch_size=2) == 6
    assert len(collector.requests) == 3


def test_en_tetes_d_authentification_transmis(tmp_path: Path, collector: _Collector) -> None:
    ledger = TraceLedger(tmp_path)
    _record(ledger)

    export_otlp(ledger, collector.url, headers={"Authorization": "Bearer t0k"})

    assert collector.requests[0]["headers"]["Authorization"] == "Bearer t0k"


# ── Intervalles de confiance ─────────────────────────────────────────────────


def test_wilson_aux_bornes_et_valeur_connue() -> None:
    assert wilson_interval95(0, 0) is None
    low, high = wilson_interval95(8, 10)  # valeur de référence : [0.4902, 0.9433]
    assert low == pytest.approx(0.4902, abs=1e-3) and high == pytest.approx(0.9433, abs=1e-3)
    assert wilson_interval95(0, 1)[1] < 0.8  # un seul échec ne laisse pas 80 % plausible
    low3, high3 = wilson_interval95(3, 3)
    assert 0.0 <= low3 <= high3 <= 1.0


def test_ratio_sans_variance_estimable_rend_none() -> None:
    assert ratio_interval95([(1.0, 1)]) is None
    assert ratio_interval95([(1.0, 0), (2.0, 0)]) is None  # aucun résolu


def test_ratio_encadre_la_moyenne() -> None:
    low, high = ratio_interval95([(1.0, 1), (3.0, 1), (2.0, 1), (2.0, 1)])
    assert low < 2.0 < high and low >= 0.0


_REGISTRY = """\
$schema: "grimoire-llm-provider-registry/v1"
metadata: {project: "demo", owner: "", policy: "x"}
providers:
  - id: "anthropic"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["claude-sonnet-4.6"]
    data_policy: {allowed_data_classes: ["public-docs"], forbidden_data_classes: ["secrets"]}
    fallback_order: []
routing: {default_provider: "anthropic", default_fallback_chain: [], require_capability_match: true, require_data_policy_match: true}
"""


def _outcome(root: Path, day: int, *, resolved: bool, cost: float, replay: str) -> None:
    TraceLedger(root / TRACES_DIR).record(
        run_id=f"dispatch-{day}",
        workflow_instance_id="",
        mission_id="",
        task_id=f"GAO-{day}",
        recipe_id="grimoire.dispatch",
        outcome=TraceOutcome.SUCCESS if resolved else TraceOutcome.FAILURE,
        started_at=f"2026-01-{day:02d}T00:00:00+00:00",
        token_usage={"estimated_cost_usd": cost},
        tags=[DISPATCH_OUTCOME_TAG, "class:V0", "tier:cheap", "acceptance:judged",
              f"resolved:{'true' if resolved else 'false'}", f"replay:{replay}"],
    )


def _slo(root: Path, extra: str) -> list[Any]:
    (root / STANDARD_DIR).mkdir(parents=True, exist_ok=True)
    (root / STANDARD_DIR / "llm-provider-registry.yaml").write_text(_REGISTRY + extra, encoding="utf-8")
    return [c for c in verify_standard_profile(root).checks if c.id == "dispatch.cost_slo"]


def test_pass_k_sous_le_seuil_mais_ic95_le_contient_n_est_pas_prouve_et_jamais_une_erreur(tmp_path: Path) -> None:
    """2 séries sur 3 tout au vert (67 %) face à 80 % : IC95 [21 %, 94 %] contient le seuil."""
    for i, (a, b) in enumerate([(True, True), (True, True), (True, False)]):
        _outcome(tmp_path, 2 * i + 1, resolved=a, cost=0.0, replay=f"n{i}")
        _outcome(tmp_path, 2 * i + 2, resolved=b, cost=0.0, replay=f"n{i}")

    checks = _slo(
        tmp_path,
        "dispatch_cost_slo:\n  min_pass_k_rate: 0.8\n  min_pass_k_observations: 3\n  enforce: true\n"
        "  max_cost_per_resolved_task_usd: 999\n  min_resolved_observations: 1\n",
    )

    passk = [c for c in checks if "pass^k" in c.message]
    assert len(passk) == 1
    assert passk[0].severity == "warning", "enforce ne durcit qu'un dépassement prouvé par l'IC95"
    assert "IC95" in passk[0].message and "not proven" in passk[0].message


def test_pass_k_prouve_sous_le_seuil_reste_une_erreur_avec_enforce(tmp_path: Path) -> None:
    for i in range(6):
        _outcome(tmp_path, 2 * i + 1, resolved=True, cost=0.0, replay=f"n{i}")
        _outcome(tmp_path, 2 * i + 2, resolved=False, cost=0.0, replay=f"n{i}")

    checks = _slo(
        tmp_path,
        "dispatch_cost_slo:\n  min_pass_k_rate: 0.8\n  min_pass_k_observations: 3\n  enforce: true\n"
        "  max_cost_per_resolved_task_usd: 999\n  min_resolved_observations: 1\n",
    )

    passk = [c for c in checks if "pass^k" in c.message]
    assert [c.severity for c in passk] == ["error"]
    assert "IC95" in passk[0].message


def test_cout_au_dessus_du_slo_mais_ic95_le_contient_n_est_pas_prouve(tmp_path: Path) -> None:
    """Coûts très dispersés : la moyenne dépasse le SLO, l'intervalle contient encore le plafond."""
    for day, cost in enumerate([0.0, 0.0, 0.0, 12.0], start=1):
        _outcome(tmp_path, day, resolved=True, cost=cost, replay=f"n{day}")

    checks = _slo(
        tmp_path,
        "dispatch_cost_slo:\n  max_cost_per_resolved_task_usd: 2.0\n  min_resolved_observations: 4\n"
        "  min_pass_k_observations: 99\n  enforce: true\n",
    )

    cost = [c for c in checks if "per resolved task" in c.message]
    assert [c.severity for c in cost] == ["warning"]
    assert "not proven" in cost[0].message and "IC95" in cost[0].message


def test_cout_prouve_au_dessus_du_slo_reste_une_erreur_avec_enforce(tmp_path: Path) -> None:
    for day, cost in enumerate([5.0, 5.1, 4.9, 5.0, 5.0], start=1):
        _outcome(tmp_path, day, resolved=True, cost=cost, replay=f"n{day}")

    checks = _slo(
        tmp_path,
        "dispatch_cost_slo:\n  max_cost_per_resolved_task_usd: 2.0\n  min_resolved_observations: 5\n"
        "  min_pass_k_observations: 99\n  enforce: true\n",
    )

    cost = [c for c in checks if "per resolved task" in c.message]
    assert [c.severity for c in cost] == ["error"]
    assert "IC95" in cost[0].message


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_otlp_envoie_au_collecteur_et_echoue_sur_refus(tmp_path: Path, collector: _Collector) -> None:
    from typer.testing import CliRunner

    from grimoire.cli.cmd_task import task_app

    _record(TraceLedger(tmp_path / TRACES_DIR))
    runner = CliRunner()
    args = ["trace-export", "--format", "otlp", "--endpoint", collector.url, "--header", "X-Test=1", "--project-root", str(tmp_path)]

    ok = runner.invoke(task_app, args)
    assert ok.exit_code == 0, ok.output
    assert collector.requests[0]["headers"]["X-Test"] == "1"

    collector.status = 500
    refused = runner.invoke(task_app, args)
    assert refused.exit_code == 1

    missing = runner.invoke(task_app, ["trace-export", "--format", "otlp", "--project-root", str(tmp_path)])
    assert missing.exit_code == 2
