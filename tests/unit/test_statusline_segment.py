"""W4-06 : le segment statusline de Claude Code ne ment ni par excès ni par défaut.

Le kit n'avait aucune statusline. Celle d'AI-DLC affiche un montant partiel sans
marqueur. Ici le segment se lit depuis les traces du ledger (repli du journal de
session, W4-01) et jamais depuis le journal lui-même :

* un coût exact s'affiche tel quel ;
* une borne basse (une partie des appels non tarifée) s'affiche avec ``>=`` ;
* un coût inconnu s'affiche ``n/a``, jamais ``0`` ;
* le journal de transcript n'est pas ouvert ;
* 10 000 entrées se rendent en moins de 50 ms.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from grimoire.traces import transcript_usage
from grimoire.traces.schemas import TokenUsage, TraceOutcome, TraceRecord
from grimoire.traces.transcript_usage import SOURCE_CLAUDE_TRANSCRIPT, SOURCE_TAG_PREFIX, statusline_segment

#: Tarifé : sonnet-5-5 à 2 $ / 10 $ par million, soit 0.0070 USD pour 1000 entrées et 500 sorties.
PRICED_COST = 0.007


def _folded(index: int, *, cost: float | None, tokens: int = 1500) -> TraceRecord:
    """Une trace d'appel replié du journal Claude Code, telle que ``_trace_for`` la produit."""
    return TraceRecord(
        id=f"TRC-usage-{index}",
        run_id="usage-GAO-1",
        workflow_instance_id="",
        mission_id="",
        task_id="GAO-1",
        recipe_id=transcript_usage.TRANSCRIPT_RECIPE_ID,
        outcome=TraceOutcome.SUCCESS,
        started_at="2026-10-18T10:00:00+00:00",
        completed_at="2026-10-18T10:00:00+00:00",
        host_id="host-claude-code-cli",
        model="claude-sonnet-5-5",
        token_usage=TokenUsage(
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=tokens,
            estimated_cost_usd=cost,
            unpriced_calls=0 if cost is not None else 1,
        ),
        tags=(f"{SOURCE_TAG_PREFIX}{SOURCE_CLAUDE_TRANSCRIPT}",),
    )


def test_cout_exact_s_affiche_sans_marqueur() -> None:
    segment = statusline_segment([_folded(1, cost=PRICED_COST), _folded(2, cost=PRICED_COST)])

    assert segment == "3000 tok | 0.0140 USD"


def test_borne_basse_porte_le_marqueur_superieur_ou_egal() -> None:
    """Un appel tarifé et un appel non tarifé : le montant connu est un minimum, il l'affiche comme tel."""
    segment = statusline_segment([_folded(1, cost=PRICED_COST), _folded(2, cost=None)])

    assert segment == "3000 tok | >= 0.0070 USD"


def test_cout_inconnu_s_affiche_na_jamais_zero() -> None:
    segment = statusline_segment([_folded(1, cost=None)])

    assert segment == "1500 tok | n/a"
    assert "0.0000" not in segment and "0.00 " not in segment


def test_rien_de_replie_affiche_na() -> None:
    assert statusline_segment([]) == "n/a"


def test_trace_sans_source_transcript_n_est_pas_affichee_comme_session() -> None:
    """Une trace déclarée à la main (``grimoire.model-call``) ne fait pas le coût de la session."""
    declared = TraceRecord(
        id="TRC-declared",
        run_id="r",
        workflow_instance_id="",
        mission_id="",
        task_id="GAO-1",
        recipe_id="grimoire.model-call",
        outcome=TraceOutcome.SUCCESS,
        started_at="2026-10-18T10:00:00+00:00",
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20, estimated_cost_usd=9.0),
    )

    assert statusline_segment([declared]) == "n/a"


def test_le_transcript_n_est_jamais_lu(monkeypatch: pytest.MonkeyPatch) -> None:
    def interdit(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("la statusline a lu le journal de transcript")

    monkeypatch.setattr(transcript_usage, "read_claude_transcript", interdit)

    assert statusline_segment([_folded(1, cost=PRICED_COST)]) == "1500 tok | 0.0070 USD"


def test_dix_mille_entrees_rendues_en_moins_de_50_ms() -> None:
    traces = [_folded(i, cost=PRICED_COST if i % 3 else None) for i in range(10_000)]

    debut = time.perf_counter()
    segment = statusline_segment(traces)
    duree_ms = (time.perf_counter() - debut) * 1000

    assert segment.endswith(" USD") and " | >= " in segment
    assert duree_ms < 50, f"{duree_ms:.1f} ms sur 10 000 entrées"
