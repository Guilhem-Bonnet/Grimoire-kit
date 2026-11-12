"""#1152 : un tarif déclaré par modèle dans le registre chiffre l'usage ; sans tarif, il reste inconnu.

Le registre portait des modèles (``id``/``tier``) mais aucun prix : l'usage d'un
worker routé vers un fournisseur externe (DeepSeek...) s'affichait « coût
inconnu ». Le champ optionnel ``models[].pricing`` est lu par
:mod:`grimoire.providers.registry` et injecté là où ``cmd_task_usage`` chiffre
l'usage. Un modèle sans tarif — ou un tarif de cache (lu, écriture 5 min ou
1 h) absent alors que des tokens correspondants existent — garde un coût
inconnu, jamais ``0``. Une écriture en cache sans répartition par durée reste
inconnue elle aussi.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from grimoire.cli.cmd_task_usage import _usage_of
from grimoire.core.standard_generation import TRACES_DIR
from grimoire.providers.registry import (
    REGISTRY_FILE,
    ModelPricing,
    ModelSpec,
    add_provider,
    read_model_pricing,
)
from grimoire.traces.ledger import TraceLedger
from grimoire.traces.transcript_usage import fold_claude_transcript, task_usage

REGISTRY = """\
$schema: "grimoire-llm-provider-registry/v1"

providers:
  - id: deepseek
    enabled: true
    provider_type: hosted
    currency: api
    models:
      - id: deepseek-chat
        tier: mid
        pricing:
          input_per_mtok: 0.27
          output_per_mtok: 1.10
          cache_read_per_mtok: 0.07
      - id: deepseek-reasoner
        tier: strong
      - id: no-cache-price
        tier: cheap
        pricing:
          input_per_mtok: 1.0
          output_per_mtok: 2.0
      - id: cache-write-priced
        tier: mid
        pricing:
          input_per_mtok: 1.0
          output_per_mtok: 2.0
          cache_read_per_mtok: 0.1
          cache_write_5m_per_mtok: 1.25
          cache_write_1h_per_mtok: 2.0
      - id: invalid-price
        tier: cheap
        pricing:
          input_per_mtok: -1
          output_per_mtok: 2.0
"""

#: 1000 in × 0.27 $/M + 500 out × 1.10 $/M.
COST_PLAIN = 0.00082
#: 1000 in × 0.27 + 100 out × 1.10 + 2000 cache lu × 0.07, le tout par million.
COST_CACHE = 0.00052


def _project(tmp_path: Path) -> Path:
    path = tmp_path / REGISTRY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(REGISTRY, encoding="utf-8")
    return tmp_path


def _line(
    message_id: str,
    model: str,
    *,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_write_5m: int | None = None,
    cache_write_1h: int | None = None,
) -> str:
    usage: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_tokens,
        "cache_creation_input_tokens": cache_creation_tokens,
    }
    if cache_write_5m is not None or cache_write_1h is not None:
        usage["cache_creation"] = {
            "ephemeral_5m_input_tokens": cache_write_5m or 0,
            "ephemeral_1h_input_tokens": cache_write_1h or 0,
        }
    return json.dumps(
        {
            "type": "assistant",
            "uuid": f"uuid-{message_id}",
            "sessionId": "00000000-0000-4000-8000-0000000001152",
            "timestamp": "2026-11-07T10:00:00Z",
            "message": {
                "id": message_id,
                "model": model,
                "role": "assistant",
                "content": [{"type": "text"}],
                "usage": usage,
            },
        }
    )


def _fold(project: Path, *lines: str, task_id: str = "GAO-1152", pricing: dict[str, ModelPricing] | None = None):
    journal = project / f"{task_id}.jsonl"
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ledger = TraceLedger(project / TRACES_DIR)
    fold_claude_transcript(ledger, journal, task_id=task_id, pricing_for=pricing.get if pricing else None)
    return ledger.list_traces(task_id=task_id)


def test_read_model_pricing_ne_garde_que_les_tarifs_valides(tmp_path: Path) -> None:
    project = _project(tmp_path)

    pricing = read_model_pricing(project)

    assert pricing["deepseek-chat"] == ModelPricing(0.27, 1.10, 0.07)
    assert pricing["cache-write-priced"] == ModelPricing(1.0, 2.0, 0.1, 1.25, 2.0)
    assert "deepseek-reasoner" not in pricing, "un modèle sans pricing ne doit pas être tarifé par défaut"
    assert "invalid-price" not in pricing, "un tarif négatif est ignoré, jamais replié sur 0"


def test_un_registre_illisible_donne_un_cout_inconnu_sans_faire_tomber_l_usage(tmp_path: Path) -> None:
    """Le tarif est optionnel : un registre corrompu laisse le coût inconnu, il ne casse pas ``task show``."""
    path = tmp_path / REGISTRY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("providers: [oups\n", encoding="utf-8")
    journal = tmp_path / "GAO-1152.jsonl"
    journal.write_text(_line("msg_1", "deepseek-chat") + "\n", encoding="utf-8")
    ledger = TraceLedger(tmp_path / TRACES_DIR)
    fold_claude_transcript(ledger, journal, task_id="GAO-1152")

    usage = _usage_of(SimpleNamespace(id="GAO-1152", claim=None), tmp_path)

    assert read_model_pricing(tmp_path) == {}
    assert usage["cost_usd"] is None
    assert usage["cost_status"] == "unknown"


def test_un_modele_sans_tarif_garde_un_cout_inconnu_jamais_zero(tmp_path: Path) -> None:
    project = _project(tmp_path)
    pricing = read_model_pricing(project)
    traces = _fold(project, _line("msg_1", "deepseek-reasoner"), pricing=pricing)

    usage = task_usage(traces, pricing_for=pricing.get)

    assert usage["cost_usd"] is None
    assert usage["cost_status"] == "unknown"
    assert usage["cost_known"] is False
    assert usage["unpriced_calls"] == 1


def test_le_tarif_du_registre_chiffre_un_modele_hors_table(tmp_path: Path) -> None:
    project = _project(tmp_path)
    pricing = read_model_pricing(project)
    # Replié sans tarif : la trace porte un coût inconnu, comme avant #1152.
    traces = _fold(project, _line("msg_1", "deepseek-chat"))

    assert task_usage(traces)["cost_usd"] is None

    usage = task_usage(traces, pricing_for=pricing.get)

    assert usage["cost_usd"] == pytest.approx(COST_PLAIN)
    assert usage["cost_status"] == "exact"
    assert usage["cost_known"] is True
    assert usage["unpriced_calls"] == 0


def test_le_cache_lu_est_chiffre_au_tarif_du_registre(tmp_path: Path) -> None:
    project = _project(tmp_path)
    pricing = read_model_pricing(project)
    traces = _fold(
        project,
        _line("msg_1", "deepseek-chat", output_tokens=100, cache_read_tokens=2000),
        pricing=pricing,
    )

    usage = task_usage(traces, pricing_for=pricing.get)

    assert usage["cache_read_tokens"] == 2000
    assert usage["cost_usd"] == pytest.approx(COST_CACHE)
    assert usage["cost_status"] == "exact"


def test_un_tarif_de_cache_lu_absent_laisse_l_appel_inconnu(tmp_path: Path) -> None:
    """Sans ``cache_read_per_mtok``, les tokens de cache lus ne sont pas pris au tarif d'entrée."""
    project = _project(tmp_path)
    pricing = read_model_pricing(project)
    traces = _fold(project, _line("msg_1", "no-cache-price", cache_read_tokens=100), pricing=pricing)

    usage = task_usage(traces, pricing_for=pricing.get)

    assert usage["cost_usd"] is None
    assert usage["cost_status"] == "unknown"


def test_une_ecriture_sans_repartition_laisse_l_appel_inconnu(tmp_path: Path) -> None:
    """Le journal dit qu'on a écrit 100 jetons, sans dire 5 min ni 1 h : on ne suppose pas la durée."""
    project = _project(tmp_path)
    pricing = read_model_pricing(project)
    traces = _fold(project, _line("msg_1", "cache-write-priced", cache_creation_tokens=100), pricing=pricing)

    usage = task_usage(traces, pricing_for=pricing.get)

    assert usage["cost_usd"] is None
    assert usage["cost_status"] == "unknown"


def test_un_tarif_d_ecriture_absent_laisse_l_appel_inconnu(tmp_path: Path) -> None:
    """La répartition est là, mais le registre ne déclare pas le tarif d'écriture : inconnu, jamais 0."""
    project = _project(tmp_path)
    pricing = read_model_pricing(project)
    traces = _fold(
        project,
        _line("msg_1", "deepseek-chat", cache_creation_tokens=100, cache_write_5m=100, cache_write_1h=0),
        pricing=pricing,
    )

    usage = task_usage(traces, pricing_for=pricing.get)

    assert usage["cost_usd"] is None
    assert usage["cost_status"] == "unknown"


def test_une_ecriture_avec_repartition_est_chiffree_par_le_registre(tmp_path: Path) -> None:
    """Écriture 5 min et 1 h aux tarifs déclarés, plus l'entrée, la sortie et le cache lu."""
    project = _project(tmp_path)
    pricing = read_model_pricing(project)
    traces = _fold(
        project,
        _line(
            "msg_1",
            "cache-write-priced",
            cache_read_tokens=500,
            cache_creation_tokens=300,
            cache_write_5m=200,
            cache_write_1h=100,
        ),
        pricing=pricing,
    )

    usage = task_usage(traces, pricing_for=pricing.get)

    attendu = (1000 * 1.0 + 500 * 2.0 + 500 * 0.1 + 200 * 1.25 + 100 * 2.0) / 1_000_000
    assert usage["cost_usd"] == pytest.approx(attendu)
    assert usage["cost_status"] == "exact"


def test_le_repli_avec_tarif_ecrit_un_cout_exact_dans_la_trace(tmp_path: Path) -> None:
    project = _project(tmp_path)
    pricing = read_model_pricing(project)

    traces = _fold(project, _line("msg_1", "deepseek-chat"), pricing=pricing)

    assert traces[0].token_usage.estimated_cost_usd == pytest.approx(COST_PLAIN)
    assert traces[0].token_usage.unpriced_calls == 0


def test_cmd_task_usage_chiffre_avec_le_registre_du_projet(tmp_path: Path) -> None:
    """``_usage_of`` (ce que ``task show`` affiche) lit le tarif du registre du projet."""
    project = _project(tmp_path)
    _fold(project, _line("msg_1", "deepseek-chat"))

    usage = _usage_of(SimpleNamespace(id="GAO-1152", claim=None), project)

    assert usage["cost_usd"] == pytest.approx(COST_PLAIN)
    assert usage["cost_status"] == "exact"


def test_add_provider_persiste_le_tarif_declare(tmp_path: Path) -> None:
    project = _project(tmp_path)

    add_provider(
        project,
        "nouveau",
        invocation="cli {prompt}",
        models=(ModelSpec(id="m", tier="mid", pricing=ModelPricing(1.0, 2.0, 0.5)),),
    )

    assert read_model_pricing(project)["m"] == ModelPricing(1.0, 2.0, 0.5)
