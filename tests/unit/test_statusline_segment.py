"""W4-06 : le segment statusline de Claude Code ne ment ni par excès ni par défaut.

Le kit n'avait aucune statusline. Celle d'AI-DLC affiche un montant partiel sans
marqueur. Ici le segment se lit depuis les traces du ledger (repli du journal de
session, W4-01) et jamais depuis le journal lui-même :

* un coût exact s'affiche tel quel ;
* une borne basse (une partie des appels non tarifée) s'affiche avec ``>=`` ;
* un coût inconnu s'affiche ``n/a``, jamais ``0`` ;
* le journal de transcript n'est pas ouvert ;
* le rendu croît linéairement avec le nombre de traces (propriété de complexité,
  pas un plafond en millisecondes : un plafond absolu rougit sous la couverture de
  branches de la CI, qui ralentit le rendu d'un facteur 5 environ).

Limite assumée : seul le rendu en mémoire est borné. La lecture de ``traces.jsonl``
par ``TraceLedger.list_traces`` n'est pas bornée ici (voir
``test_la_lecture_du_ledger_est_hors_du_perimetre_chronometre``).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from grimoire.traces import transcript_usage
from grimoire.traces.ledger import TraceLedger
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


def _plus_petit_temps(rendre: Callable[[list[TraceRecord]], str], traces: list[TraceRecord], essais: int = 5) -> float:
    """Le meilleur de *essais* rendus : le minimum écarte les pauses du ramasse-miettes et de l'ordonnanceur."""
    meilleur = float("inf")
    for _ in range(essais):
        debut = time.perf_counter()
        rendre(traces)
        meilleur = min(meilleur, time.perf_counter() - debut)
    return meilleur


def _croissance(rendre: Callable[[list[TraceRecord]], str]) -> float:
    """Rapport du temps de rendu de 10 000 traces à celui de 1 000 (10 pour un rendu linéaire, 100 pour un quadratique)."""
    petit = [_folded(i, cost=PRICED_COST if i % 3 else None) for i in range(1_000)]
    grand = [_folded(i, cost=PRICED_COST if i % 3 else None) for i in range(10_000)]
    return _plus_petit_temps(rendre, grand) / _plus_petit_temps(rendre, petit)


#: Un rendu linéaire donne environ 10 ; la marge absorbe le bruit, un rendu quadratique donne environ 100.
CROISSANCE_MAX = 30.0


def test_le_rendu_croit_lineairement_avec_le_nombre_de_traces() -> None:
    """Propriété de complexité, indépendante de la machine et du traceur de couverture."""
    segment = statusline_segment([_folded(i, cost=PRICED_COST if i % 3 else None) for i in range(10_000)])
    assert segment.endswith(" USD") and " | >= " in segment

    croissance = _croissance(statusline_segment)

    assert croissance < CROISSANCE_MAX, f"rendu x{croissance:.0f} pour 10 fois plus de traces (linéaire : ~10)"


def test_le_detecteur_de_croissance_attrape_un_rendu_quadratique() -> None:
    """Garde du test précédent : un rendu qui reparcourt la liste pour chaque trace dépasse la borne."""

    def quadratique(traces: list[TraceRecord]) -> str:
        total = 0
        for trace in traces:
            total += sum(1 for autre in traces if autre.id == trace.id)
        return str(total)

    petit = [_folded(i, cost=PRICED_COST) for i in range(100)]
    grand = [_folded(i, cost=PRICED_COST) for i in range(1_000)]

    assert _plus_petit_temps(quadratique, grand, 2) / _plus_petit_temps(quadratique, petit, 2) > CROISSANCE_MAX


def test_la_lecture_du_ledger_est_hors_du_perimetre_chronometre(tmp_path: Path) -> None:
    """La lecture de bout en bout est correcte ; elle n'est pas chronométrée (coût de ``_load_all`` non borné)."""
    ledger = TraceLedger(tmp_path)
    with open(tmp_path / "traces.jsonl", "w", encoding="utf-8") as fh:
        for i in range(50):
            fh.write(json.dumps(_folded(i, cost=PRICED_COST).to_dict(), ensure_ascii=False) + "\n")

    assert statusline_segment(ledger.list_traces(task_id="GAO-1")) == "75000 tok | 0.3500 USD"
