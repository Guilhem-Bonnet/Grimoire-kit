#!/usr/bin/env python3
"""Rejeu de la mesure « Source piste 2 » (issue #280) : journal brut des appels.

Pour chaque fichier cité × chaque modèle × N appels, vide la valeur de
``dont_use_when``, place le curseur sur cette ligne, et passe par
``assist_view`` (le chemin réel de ``POST /api/workspace/assist``, intention
``complete-clause``). Chaque appel au modèle est consigné en JSONL : prompt
exact, réponse brute, latence, résultat d'``assist_view``.

Charge GPU : à ne lancer que hors mode jeu. Usage :

    python scripts/bench/source_assist_replay.py --out journal.jsonl
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from grimoire.providers.audit import ollama_base_url
from grimoire.tools import source_assist

FILES = (
    "archetypes/meta/agents/concierge.md",
    "archetypes/meta/agents/agent-optimizer.md",
    "archetypes/meta/agents/security-auditor.md",
)
MODELS = ("qwen2.5:3b", "qwen2.5:7b")
REPO = Path(__file__).resolve().parents[2]


def _warm(model: str) -> None:
    """Charge le modèle en mémoire (les appels mesurés sont « chauds »)."""
    body = json.dumps({"model": model, "prompt": "ok", "stream": False, "keep_alive": "5m"}).encode()
    req = urllib.request.Request(
        f"{ollama_base_url()}/api/generate", data=body, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=300).read()  # noqa: S310 — Ollama local


def _blank_clause(text: str) -> tuple[str, int]:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("dont_use_when:"):
            lines[i] = 'dont_use_when: "'
            return "\n".join(lines), i
    raise SystemExit("pas de dont_use_when")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--calls", type=int, default=2)
    args = ap.parse_args()

    records: list[dict[str, Any]] = []
    real_call = source_assist._call_ollama
    current: dict[str, Any] = {}

    def spy(base_url: str, model: str, prompt: str) -> str | None:
        t0 = time.perf_counter()
        out = real_call(base_url, model, prompt)
        current.update(prompt=prompt, raw_response=out, latency_s=round(time.perf_counter() - t0, 3))
        return out

    source_assist._call_ollama = spy  # type: ignore[assignment]
    for model in MODELS:
        _warm(model)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "project-context.yaml").write_text(
                f'project:\n  name: "replay"\nsource:\n  assist:\n    model: "{model}"\n', encoding="utf-8"
            )
            agents = root / "_grimoire" / "overrides" / "agents"
            agents.mkdir(parents=True)
            for rel in FILES:
                text, line = _blank_clause((REPO / rel).read_text(encoding="utf-8"))
                (agents / Path(rel).name).write_text(text, encoding="utf-8")
                for n in range(1, args.calls + 1):
                    current.clear()
                    view = source_assist.assist_view(
                        root,
                        {
                            "path": f"_grimoire/overrides/agents/{Path(rel).name}",
                            "intent": "complete-clause",
                            "text": text,
                            "position": {"line": line, "col": 0},
                        },
                    )
                    records.append({"model": model, "file": rel, "call": n, "line": line, "view": view, **current})
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    print(f"{len(records)} appels consignés dans {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
