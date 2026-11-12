"""Chiffrement du cache (lecture, écriture 5 min et 1 h), Haiku 5.5 et Fable 5.2.

Avant, un appel qui lisait ou écrivait le cache restait « coût inconnu » : la
table de tarifs ne portait que l'entrée et la sortie. Elle porte maintenant les
cinq colonnes publiées (entrée, sortie, lecture, écriture 5 min, écriture 1 h),
et le repli lit la répartition ``usage.cache_creation`` du journal pour facturer
l'écriture à sa durée réelle. Une écriture sans répartition reste inconnue : on
ne suppose jamais 5 min plutôt que 1 h.

Haiku 5.5 a deux paliers ; celui de l'appel se choisit sur la longueur du
prompt (entrée + cache lu + cache écrit).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.core.standard_generation import TRACES_DIR
from grimoire.providers.registry import ModelPricing
from grimoire.tools import cost_model as cm
from grimoire.traces.ledger import TraceLedger
from grimoire.traces.schemas import TraceRecord
from grimoire.traces.transcript_usage import fold_claude_transcript, task_usage


def _line(
    message_id: str,
    model: str,
    *,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_read_tokens: int = 0,
    cache_write_5m_tokens: int | None = None,
    cache_write_1h_tokens: int | None = None,
    breakdown: bool = True,
    declared_creation: int | None = None,
) -> str:
    """Une ligne assistant ; ``breakdown=False`` simule un journal qui donne le total sans la durée."""
    write_5m = cache_write_5m_tokens or 0
    write_1h = cache_write_1h_tokens or 0
    usage: dict[str, object] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_tokens,
        "cache_creation_input_tokens": write_5m + write_1h if declared_creation is None else declared_creation,
    }
    if breakdown and (write_5m or write_1h):
        usage["cache_creation"] = {
            "ephemeral_5m_input_tokens": write_5m,
            "ephemeral_1h_input_tokens": write_1h,
        }
    return json.dumps(
        {
            "type": "assistant",
            "uuid": f"uuid-{message_id}",
            "sessionId": "00000000-0000-4000-8000-0000000000c1",
            "timestamp": "2026-11-12T09:00:00Z",
            "message": {
                "id": message_id,
                "model": model,
                "role": "assistant",
                "content": [{"type": "text"}],
                "usage": usage,
            },
        }
    )


def _fold(project: Path, *lines: str, pricing: dict[str, ModelPricing] | None = None) -> list[TraceRecord]:
    journal = project / "session.jsonl"
    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ledger = TraceLedger(project / TRACES_DIR)
    fold_claude_transcript(ledger, journal, task_id="GAO-CACHE", pricing_for=pricing.get if pricing else None)
    return ledger.list_traces(task_id="GAO-CACHE")


def test_un_appel_opus_5_5_avec_cache_complet_est_chiffre_a_la_main(tmp_path: Path) -> None:
    traces = _fold(
        tmp_path,
        _line(
            "msg_1",
            "claude-opus-5-5",
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=2000,
            cache_write_5m_tokens=3000,
            cache_write_1h_tokens=4000,
        ),
    )

    usage = task_usage(traces)

    # 1000 in × 4 + 500 out × 20 + 2000 lu × 0.20 + 3000 × 5 + 4000 × 8, par million.
    assert usage["cost_usd"] == pytest.approx(0.0614)
    assert usage["cost_status"] == "exact"
    assert usage["cost_known"] is True
    assert usage["unpriced_calls"] == 0


def test_une_ecriture_sans_repartition_par_duree_reste_inconnue(tmp_path: Path) -> None:
    traces = _fold(
        tmp_path,
        _line("msg_1", "claude-opus-5-5", cache_write_5m_tokens=3000, breakdown=False),
    )

    usage = task_usage(traces)

    assert usage["cost_usd"] is None
    assert usage["cost_status"] == "unknown"
    assert usage["unpriced_calls"] == 1


def test_une_repartition_qui_ne_somme_pas_au_total_reste_inconnue(tmp_path: Path) -> None:
    """Le journal se contredit (total 5 000, répartition 3 000) : un chiffre serait inventé."""
    traces = _fold(
        tmp_path,
        _line(
            "msg_1",
            "claude-opus-5-5",
            cache_write_5m_tokens=3000,
            cache_write_1h_tokens=0,
            declared_creation=5000,
        ),
    )

    usage = task_usage(traces)

    assert usage["cost_usd"] is None
    assert usage["cost_status"] == "unknown"


def test_haiku_5_5_avec_un_prompt_de_120_000_jetons_utilise_le_palier_haut(tmp_path: Path) -> None:
    traces = _fold(
        tmp_path,
        _line(
            "msg_1",
            "claude-haiku-5-5",
            input_tokens=100_000,
            cache_read_tokens=20_000,
            output_tokens=1000,
        ),
    )

    usage = task_usage(traces)

    # 100 000 in × 0.50 + 20 000 lus × 0.05 + 1 000 out × 2.50, par million :
    # le prompt fait 120 000 jetons, donc le palier haut.
    assert usage["cost_usd"] == pytest.approx(0.0535)
    assert usage["cost_status"] == "exact"


def test_le_palier_de_haiku_5_5_bascule_au_dela_de_100_000_jetons_de_prompt() -> None:
    assert (cm.rate_for_call("claude-haiku-5-5", 100_000) or {})["in"] == 0.10
    assert (cm.rate_for_call("claude-haiku-5-5", 100_001) or {})["in"] == 0.50
    # Le palier bas reste celui que rend ``rate_for``, sans longueur de prompt connue.
    assert cm.rate_for("claude-haiku-5-5") == cm.MODEL_RATES["haiku-5-5"]


def test_un_tarif_de_registre_sans_ecriture_laisse_l_appel_inconnu(tmp_path: Path) -> None:
    pricing = {"modele-externe": ModelPricing(1.0, 2.0, 0.1)}
    traces = _fold(
        tmp_path,
        _line("msg_1", "modele-externe", cache_write_5m_tokens=100, cache_write_1h_tokens=50),
        pricing=pricing,
    )

    usage = task_usage(traces, pricing_for=pricing.get)

    assert usage["cost_usd"] is None
    assert usage["cost_status"] == "unknown"
    assert usage["unpriced_calls"] == 1
