"""Banc LongMemEval : le rappel de la mémoire sur des historiques de sessions longs.

LongMemEval (Wu et al., 2024) pose, pour chaque question, un historique de
sessions de conversation — dont une ou plusieurs contiennent la preuve de la
réponse. Ce module mesure la moitié **récupération** du protocole : la
mémoire, remplie avec les sessions de l'historique, remet-elle une session
porteuse de preuve dans les ``k`` premiers résultats ?

Ce que ce score est, et n'est pas :

- c'est un ``recall_any@k`` au niveau session, calculé sur la lecture mémoire
  réelle (``hybrid_search``) avec le backend local ;
- ce n'est **pas** l'exactitude de réponse notée par un LLM-juge, que cite la
  veille (~94,9 % chez Mastra). Les deux chiffres ne se comparent pas ; le
  rapport le déclare (``comparable_to_published_qa_accuracy: false``) ;
- sans ``--dataset``, le banc rejoue un échantillon embarqué de quelques
  questions : il prouve que le harnais tourne, pas le niveau du système.
  Le rapport porte alors ``dataset.kind == "sample"``.

Les questions d'abstention (``question_id`` finissant par ``_abs``) n'ont pas
de session de preuve : elles sont comptées à part, hors du score.
"""

from __future__ import annotations

import json
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMemoryError
from grimoire.memory.backends.local import LocalMemoryBackend
from grimoire.memory.manager import MemoryManager

__all__ = ["load_dataset", "publish", "render_markdown", "run_longmemeval"]

SCORE_NAME = "recall_any@k (session)"


def load_dataset(path: Path | None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Charge le jeu LongMemEval, ou l'échantillon embarqué quand ``path`` est ``None``."""
    if path is None:
        raw = resources.files("grimoire.data").joinpath("bench/longmemeval-sample.json").read_text(encoding="utf-8")
        info = {"kind": "sample", "source": "grimoire.data/bench/longmemeval-sample.json"}
    else:
        raw = path.read_text(encoding="utf-8")
        info = {"kind": "external", "source": str(path)}
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("un jeu LongMemEval est une liste de questions")
    for item in data:
        for key in ("question_id", "question", "haystack_session_ids", "haystack_sessions"):
            if key not in item:
                raise ValueError(f"question sans champ obligatoire {key!r}")
    return data, info


def _session_text(turns: list[dict[str, Any]]) -> str:
    # Pas d'en-tête ``role:`` : la politique d'écriture le refuse (motif de
    # consigne). Le banc la subit comme n'importe quel écrivain, il ne la contourne pas.
    return "\n".join(f"The {t.get('role', 'user')} said: {t.get('content', '')}" for t in turns)


def _ingest(mgr: MemoryManager, item: dict[str, Any]) -> int:
    """Écrit chaque session de l'historique ; renvoie le nombre refusé par la politique d'écriture."""
    refused = 0
    for session_id, turns in zip(item["haystack_session_ids"], item["haystack_sessions"], strict=False):
        try:
            mgr.upsert(str(session_id), _session_text(turns)[:30_000], metadata={"session_id": str(session_id)})
        except GrimoireMemoryError:
            refused += 1
    return refused


def run_longmemeval(path: Path | None = None, *, k: int = 5, limit_questions: int | None = None) -> dict[str, Any]:
    """Rejoue le jeu et renvoie le rapport (aucune écriture hors répertoire temporaire)."""
    dataset, info = load_dataset(path)
    if limit_questions is not None:
        dataset = dataset[:limit_questions]
    by_type: dict[str, list[bool]] = defaultdict(list)
    abstention = 0
    refused_sessions = 0
    for item in dataset:
        if str(item["question_id"]).endswith("_abs") or not item.get("answer_session_ids"):
            abstention += 1
            continue
        with tempfile.TemporaryDirectory(prefix="longmemeval-") as tmp:
            mgr = MemoryManager(LocalMemoryBackend(Path(tmp) / "memory.json"))
            mgr.journal_reads = False
            refused_sessions += _ingest(mgr, item)
            hits = mgr.hybrid_search(item["question"], limit=k)
        found = {str(h.id) for h in hits}
        by_type[str(item.get("question_type", "unknown"))].append(bool(found & {str(s) for s in item["answer_session_ids"]}))
    scored = sum(len(v) for v in by_type.values())
    hit_total = sum(sum(v) for v in by_type.values())
    return {
        "benchmark": "LongMemEval",
        "measured_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "dataset": {**info, "questions": len(dataset)},
        "metric": SCORE_NAME,
        "k": k,
        "backend": "local (lexical)",
        "questions_scored": scored,
        "abstention_excluded": abstention,
        "sessions_refused_by_write_policy": refused_sessions,
        "score": (hit_total / scored) if scored else None,
        "by_question_type": {t: {"n": len(v), "score": sum(v) / len(v)} for t, v in sorted(by_type.items())},
        "comparable_to_published_qa_accuracy": False,
        "note": "Récupération seule, sans LLM-juge : ne se compare pas au ~94,9 % de réponse cité pour Mastra.",
    }


def render_markdown(report: dict[str, Any]) -> str:
    score = report["score"]
    shown = "null (aucune question notée)" if score is None else f"{score:.1%}"
    lines = [
        "---",
        "audience: décision",
        "---",
        "",
        f"# LongMemEval — {report['measured_at']}",
        "",
        f"- Score {report['metric']} @ k={report['k']} : **{shown}** sur {report['questions_scored']} questions",
        f"- Jeu : {report['dataset']['kind']} ({report['dataset']['source']}), {report['dataset']['questions']} questions",
        f"- Backend : {report['backend']}",
        f"- Abstentions exclues : {report['abstention_excluded']} ; sessions refusées par la politique d'écriture : {report['sessions_refused_by_write_policy']}",
        "",
        "| Type de question | n | score |",
        "|---|---|---|",
    ]
    lines += [f"| {t} | {v['n']} | {v['score']:.1%} |" for t, v in report["by_question_type"].items()]
    lines += ["", f"> {report['note']}"]
    if report["dataset"]["kind"] == "sample":
        lines.append("> Échantillon embarqué : prouve que le harnais tourne, pas le niveau du système. Rejouer avec `--dataset` sur le jeu complet.")
    return "\n".join(lines) + "\n"


def publish(report: dict[str, Any], out_dir: Path) -> list[Path]:
    """Écrit le rapport JSON et sa version lisible dans ``out_dir`` (``docs/bench/``)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"longmemeval-{report['measured_at']}"
    json_path, md_path = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return [json_path, md_path]
