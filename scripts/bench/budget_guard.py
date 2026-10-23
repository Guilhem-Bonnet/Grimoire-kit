"""Garde-fou de budget du banc, fermé par défaut, partagé par ``aidlc_arm.py`` et ``three_arms.py`` (B-08).

Extrait d'``aidlc_arm.py`` (B-06) sans changement de comportement, pour que le
lanceur des bras ``nu``/``ecc``/``kit``/``kit-gov`` ait le même plafond de
campagne que le bras ``aidlc`` au lieu d'une copie :

- un run payant refuse de partir sans plafond explicite (:func:`parse_cap`) ;
- avant CHAQUE appel ``claude -p``, le cumul connu est comparé au plafond, et
  l'appel reçoit le reste en ``--max-budget-usd`` (:func:`budget_args`) ;
- un coût inconnu (pas de ``total_cost_usd``, appel commencé mais jamais
  terminé dans le journal, ligne illisible) ne vaut jamais 0 et bloque tout
  appel suivant ;
- le cumul vit dans un journal append-only (``begin`` puis ``end`` par appel),
  partageable entre lanceurs et workspaces (``--budget-ledger``) : une campagne
  relancée repart du cumul, jamais de zéro ;
- sous ``claude -p --resume``, ``total_cost_usd`` cumule toute la session : le
  coût d'un appel est la différence avec le total précédent (:func:`session_call_cost`).

Le module porte aussi l'estimation d'un plan de runs (tamis du lot M, §17.5 de
``docs/bench-three-arms.md``) et sa couverture par un plafond, affichées par
``three_arms.py --dry-run`` sans aucun appel.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class BudgetError(RuntimeError):
    """Le garde-fou de budget refuse l'appel suivant."""


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def parse_cap(raw: object) -> float:
    """Le plafond en dollars, ou :class:`BudgetError` : absent, non numérique, non fini ou ≤ 0."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise BudgetError("plafond en dollars absent : --max-total-usd est obligatoire pour lancer un run payant")
    if isinstance(raw, bool):
        raise BudgetError(f"plafond invalide : {raw!r}")
    try:
        cap = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise BudgetError(f"plafond invalide : {raw!r} n'est pas un montant en dollars") from None
    if not math.isfinite(cap) or cap <= 0:
        raise BudgetError(f"plafond invalide : {raw!r} (attendu : un montant fini strictement positif)")
    return cap


def positive_int(raw: str) -> int:
    """Type argparse de ``--k`` : un entier ≥ 1 (0 run par tâche n'est pas une campagne)."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} n'est pas un entier") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{raw!r} : au moins 1 run par tâche et par bras")
    return value


def budget_args(max_budget_usd: float | None) -> list[str]:
    """``--max-budget-usd <reste>`` : Claude Code s'arrête lui-même pendant l'appel au reste du plafond."""
    return ["--max-budget-usd", f"{max_budget_usd:.4f}"] if max_budget_usd is not None else []


def session_call_cost(session_total: float, previous_total: float | None) -> float:
    """Coût d'un appel : son total moins le total précédent de la session reprise.

    Sans total précédent (premier appel), ou si le total baisse (copie de
    session, compteur remis à zéro), l'appel compte en entier : jamais un
    delta négatif, au pire un surcompte.
    """
    if previous_total is None or session_total < previous_total:
        return session_total
    return session_total - previous_total


@dataclass
class BudgetGuard:
    """Cumul des coûts d'appels ``claude -p``, adossé à un journal append-only.

    Chaque appel est encadré par deux lignes (``begin`` puis ``end``). À la
    relecture, un ``begin`` sans ``end`` (processus tué pendant l'appel) et un
    ``end`` sans coût connu valent *inconnu* ; une ligne illisible aussi. Un
    seul inconnu bloque tout appel suivant : il ne compte jamais pour 0.
    """

    cap_usd: float
    ledger_path: Path | None = None
    spent_usd: float = 0.0
    unknown_calls: list[str] = field(default_factory=list)
    calls: int = 0

    @classmethod
    def open(cls, cap: object, ledger_path: Path | None) -> BudgetGuard:
        guard = cls(cap_usd=parse_cap(cap), ledger_path=ledger_path)
        if ledger_path is not None and ledger_path.is_file():
            guard._replay(ledger_path)
        return guard

    def _replay(self, path: Path) -> None:
        open_calls: dict[str, int] = {}
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                self.unknown_calls.append(f"{path.name}:{lineno} (ligne illisible)")
                continue
            if not isinstance(row, dict):
                self.unknown_calls.append(f"{path.name}:{lineno} (ligne non objet)")
                continue
            call_id = str(row.get("call_id"))
            if row.get("event") == "begin":
                open_calls[call_id] = lineno
            elif row.get("event") == "end":
                open_calls.pop(call_id, None)
                self.calls += 1
                cost = row.get("cost_usd")
                if (
                    row.get("cost_known") is True
                    and isinstance(cost, (int, float))
                    and not isinstance(cost, bool)
                    and math.isfinite(cost)
                ):
                    self.spent_usd += float(cost)
                else:
                    self.unknown_calls.append(call_id)
            else:
                self.unknown_calls.append(f"{path.name}:{lineno} (événement inconnu)")
        for call_id in open_calls:
            self.unknown_calls.append(f"{call_id} (commencé, jamais terminé)")

    def check_before_call(self) -> None:
        if self.unknown_calls:
            raise BudgetError(
                f"coût inconnu pour {len(self.unknown_calls)} appel(s) ({', '.join(self.unknown_calls[:3])}) : "
                "un coût inconnu ne compte pas pour 0, aucun appel de plus n'est lancé"
            )
        if self.spent_usd >= self.cap_usd:
            raise BudgetError(
                f"plafond atteint : {self.spent_usd:.4f} $ dépensés pour un plafond de {self.cap_usd:.2f} $"
            )

    def _append(self, row: Mapping[str, Any]) -> None:
        if self.ledger_path is None:
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
            f.flush()
            with contextlib.suppress(OSError):
                os.fsync(f.fileno())

    def begin(self, call_id: str, **meta: Any) -> None:
        self.check_before_call()
        self._append({"event": "begin", "call_id": call_id, "at": _now(), **meta})

    def record(self, call_id: str, cost_usd: float, cost_known: bool) -> None:
        self.calls += 1
        known = bool(cost_known) and math.isfinite(cost_usd)
        if known:
            self.spent_usd += cost_usd
        else:
            self.unknown_calls.append(call_id)
        self._append(
            {
                "event": "end",
                "call_id": call_id,
                "at": _now(),
                "cost_usd": cost_usd if known else None,
                "cost_known": known,
            }
        )

    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    def snapshot(self) -> dict[str, Any]:
        return {
            "cap_usd": self.cap_usd,
            "spent_usd": round(self.spent_usd, 6),
            "calls": self.calls,
            "unknown_calls": list(self.unknown_calls),
            "blocked": bool(self.unknown_calls) or self.spent_usd >= self.cap_usd,
        }


# ── Estimation d'un plan de runs (aucun appel) ───────────────────────────────

#: Lot L (``docs/bench/rejeu-lot-l-2026-10-02.md``, Opus 5.5) : 60 runs ``nu`` pour
#: 10,15 $ (moyenne), maximum 0,271 $ ; 60 runs ``kit-gov`` pour 12,69 $. MESURÉ,
#: sur des tâches polyglottes.
LOT_L_NU_MEAN_USD = 10.15 / 60
LOT_L_NU_MAX_USD = 0.271
KIT_GOV_OVER_NU = 12.69 / 10.15
#: Tâches du bloc K (dépôt de ~1 900 fichiers) : ESTIMÉ, aucune mesure (§17.5).
K_NU_CENTRAL_USD = 1.20
K_NU_HIGH_USD = 3.00


def _per_run(language: str, arm: str) -> tuple[float, float, str] | None:
    """(central, haut, base) d'un run, ou ``None`` : aucune base pour ce bras (jamais 0)."""
    if language == "kit":
        central, high, basis = K_NU_CENTRAL_USD, K_NU_HIGH_USD, "estimé (bloc K, aucune mesure)"
    else:
        central, high, basis = LOT_L_NU_MEAN_USD, LOT_L_NU_MAX_USD, "mesuré (lot L, moyenne et maximum du nu)"
    if arm == "nu":
        return central, high, basis
    if arm == "kit-gov":
        return central * KIT_GOV_OVER_NU, high * KIT_GOV_OVER_NU, basis + " x 1,25 (kit-gov/nu, lot L)"
    return None


def estimate_plan(languages: Sequence[str], arms: Sequence[str], k: int) -> dict[str, Any]:
    """Coût d'un plan : une tâche par entrée de *languages* (``kit`` = bloc K), × *arms* × *k*.

    Un bras sans base de coût (``ecc``, ``kit``) rend son coût *inconnu* : le
    total devient inconnu (``None``), jamais une somme partielle présentée comme totale.
    """
    rows: list[dict[str, Any]] = []
    central_total, high_total, unknown = 0.0, 0.0, []
    for arm in arms:
        for block, langs in (("K", [x for x in languages if x == "kit"]), ("P", [x for x in languages if x != "kit"])):
            if not langs:
                continue
            per = _per_run(langs[0], arm)
            runs = len(langs) * k
            if per is None:
                unknown.append(f"{arm}/{block}")
                rows.append({"arm": arm, "block": block, "runs": runs, "per_run_usd": None, "basis": "inconnu"})
                continue
            central_total += runs * per[0]
            high_total += runs * per[1]
            rows.append(
                {
                    "arm": arm,
                    "block": block,
                    "runs": runs,
                    "per_run_usd": [round(per[0], 4), round(per[1], 4)],
                    "usd": [round(runs * per[0], 2), round(runs * per[1], 2)],
                    "basis": per[2],
                }
            )
    return {
        "k": k,
        "rows": rows,
        "total_runs": sum(r["runs"] for r in rows),
        "central_usd": None if unknown else round(central_total, 2),
        "high_usd": None if unknown else round(high_total, 2),
        "unknown": unknown,
    }


def cap_coverage(cap_usd: float, estimate: Mapping[str, Any]) -> dict[str, Any]:
    """Le plafond couvre-t-il le plan, au centre et au haut de l'estimation ? ``None`` = on ne sait pas."""
    central, high = estimate.get("central_usd"), estimate.get("high_usd")
    return {
        "cap_usd": cap_usd,
        "covers_central": None if central is None else cap_usd >= central,
        "covers_high": None if high is None else cap_usd >= high,
        "margin_over_high_usd": None if high is None else round(cap_usd - high, 2),
    }
