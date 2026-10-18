#!/usr/bin/env python3
"""Interventions humaines par tâche vérifiée, comptées sur les traces des deux bras (B-05, POS-12).

Un seul script, une seule règle (``RULE_ID``), figée au pré-enregistrement. Le
nombre n'est **jamais** lu dans un champ déclaré par le bras : il est recompté
sur ce que le run a laissé derrière lui, et un run dont les traces sont
illisibles vaut *inconnu* (``null``), jamais zéro.

Une unité est comptée pour chacun de ces événements (clé de dédoublonnage entre
parenthèses : un même événement vu par deux traces ne compte qu'une fois) :

- ``gate`` — un gate ou checkpoint présenté qui attend une réponse humaine,
  qu'il ait été levé par un octroi ou par la configuration autonome : un
  *challenge* du journal d'autorité du kit (``challenge_id``), une décision de
  checkpoint sans challenge (ligne), un ``STAGE_AWAITING_APPROVAL`` d'AI-DLC
  (rang dans le fichier d'audit) ;
- ``refusal`` — un refus qui demande une action humaine pour être levé : un
  résultat d'outil en erreur qui porte une marque de ``HUMAN_ACTION_MARKERS``
  (``tool_use_id``). Un refus que l'agent lève seul (une commande réversible
  à la place) ne porte aucune de ces marques et ne compte pas ;
- ``question`` — une question posée à l'humain : un fichier ``[Answer]:``
  (chemin) ou un ``DECISION_RECORDED`` d'AI-DLC (son ``Questions File`` s'il en
  a un, sinon son rang), une ``task.question_asked`` du Mission Ledger du kit
  (identifiant de la question). La question qu'un checkpoint de flow pose
  (``origin: flow``) n'est pas recomptée : c'est le checkpoint, déjà un ``gate`` ;
- ``grant`` — l'octroi d'autonomie W5-06 lui-même (``grant_id``), qui vaut 1 :
  son challenge n'est pas compté en plus ;
- ``autonomy`` — l'activation du mode autonome d'AI-DLC (``AUTONOMY_MODE_SET``
  avec ``Mode: autonomous``).

Ne comptent pas : les refus levés par l'agent seul, les messages d'information,
les tours humains eux-mêmes (ce sont les réponses, pas les sollicitations).

Entrées : ``<workspace>/state/results.jsonl`` (qui dit quel run est valide et
vérifié) et, pour chaque run, ``tasks/<langue>__<tâche>/<bras>/run<k>.stream.jsonl``
(transcription) et le dépôt ``run<k>/`` (journaux). Le script regarde les mêmes
traces pour tous les bras : l'étiquette du bras ne sert qu'à regrouper.

Rejeu : ``python scripts/bench/human_gates.py --workspace <dossier du banc>``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from results_io import read_results_rows

RULE_ID = "human-gates/1"

KIND_GATE = "gate"
KIND_REFUSAL = "refusal"
KIND_QUESTION = "question"
KIND_GRANT = "grant"
KIND_AUTONOMY = "autonomy"
KINDS = (KIND_GATE, KIND_REFUSAL, KIND_QUESTION, KIND_GRANT, KIND_AUTONOMY)

#: Marques, dans un résultat d'outil en erreur, d'un refus qui attend un humain. Minuscules.
#: kit : la raison d'un ``ask`` de la politique d'outils (« Confirme ... ») ; AI-DLC : un refus qui exige une session suivie.
HUMAN_ACTION_MARKERS = (
    "confirme avant de l'exécuter",
    "confirme si c'est voulu",
    "confirme le profil visé",
    "attended session",
    "withholds human authority",
)
#: Un refus de la politique du kit qui renvoie l'agent à une commande réversible se lève sans humain.
AGENT_LIFTABLE_MARKERS = ("passe par une commande réversible",)

#: Outils dont le contenu écrit peut porter un fichier de questions.
_WRITE_TOOLS = ("Write", "Edit", "MultiEdit")
_ANSWER_TAG = "[Answer]:"
_AUTHORITY_SUFFIX = ".authority.jsonl"
_GRANTS_PREFIX = "@autonomy-grants"
_SKIPPED_DIRS = frozenset({".git", "node_modules", "target", "__pycache__", ".venv"})
_AUDIT_FIELD = re.compile(r"^\*\*(?P<name>[^*]+)\*\*\s*:\s*(?P<value>.*)$")


@dataclass(frozen=True, slots=True)
class Event:
    """Une intervention comptée : sa nature, sa clé de dédoublonnage et la trace qui la porte."""

    kind: str
    key: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "key": self.key, "source": self.source}


def _json_lines(path: Path) -> list[dict[str, Any]] | None:
    """Les objets JSON d'un fichier ``.jsonl`` ; ``None`` si illisible. Les lignes qui n'en sont pas sont ignorées."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    return ""


def _written_texts(name: str, tool_input: Mapping[str, Any]) -> Iterable[str]:
    if name == "Write":
        yield str(tool_input.get("content", ""))
    elif name == "Edit":
        yield str(tool_input.get("new_string", ""))
    elif name == "MultiEdit":
        for edit in tool_input.get("edits") or []:
            if isinstance(edit, Mapping):
                yield str(edit.get("new_string", ""))


def is_human_refusal(text: str) -> bool:
    """Ce texte de refus demande-t-il une action humaine pour être levé ?"""
    low = text.casefold()
    if any(marker in low for marker in AGENT_LIFTABLE_MARKERS):
        return False
    return any(marker in low for marker in HUMAN_ACTION_MARKERS)


def _answer_file_key(path: str) -> str:
    return "qfile:" + path.replace("\\", "/").rsplit("/", 1)[-1]


def transcript_events(objects: Sequence[Mapping[str, Any]], source: str) -> list[Event]:
    """Refus qui attendent un humain et fichiers ``[Answer]:`` d'une transcription ``stream-json``."""
    events: list[Event] = []
    for obj in objects:
        message = obj.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, Mapping):
                continue
            kind = block.get("type")
            if obj.get("type") == "assistant" and kind == "tool_use":
                tool_input = block.get("input")
                if block.get("name") in _WRITE_TOOLS and isinstance(tool_input, Mapping):
                    path = str(tool_input.get("file_path") or tool_input.get("path") or "")
                    if path and any(_ANSWER_TAG in t for t in _written_texts(str(block["name"]), tool_input)):
                        events.append(Event(KIND_QUESTION, _answer_file_key(path), source))
            elif obj.get("type") == "user" and kind == "tool_result" and block.get("is_error"):
                if is_human_refusal(_text(block.get("content"))):
                    events.append(Event(KIND_REFUSAL, f"refusal:{block.get('tool_use_id', '')}", source))
    return events


def authority_events(path: Path, root: Path) -> list[Event]:
    """Challenges et décisions de checkpoint d'un run du kit, ou octrois d'autonomie (``@autonomy-grants``)."""
    entries = _json_lines(path)
    if entries is None:
        return []
    source = path.relative_to(root).as_posix()
    if path.name.startswith(_GRANTS_PREFIX):
        return [
            Event(KIND_GRANT, f"grant:{e.get('grant_id', i)}", source)
            for i, e in enumerate(entries)
            if e.get("type") == "grant"
        ]
    if path.name.startswith("@"):
        return []  # registre des tours consommés : ni challenge ni décision
    run = path.name[: -len(_AUTHORITY_SUFFIX)]
    challenges = {str(e.get("challenge_id", "")) for e in entries if e.get("type") == "challenge"}
    events = [Event(KIND_GATE, f"challenge:{run}:{cid}", source) for cid in sorted(challenges) if cid]
    for i, entry in enumerate(entries):
        # une décision dont le checkpoint n'a laissé aucun challenge reste un checkpoint présenté
        if entry.get("type") == "decision" and str(entry.get("challenge_id", "")) not in challenges:
            events.append(Event(KIND_GATE, f"decision:{run}:{i}", source))
    return events


def ledger_events(path: Path, root: Path) -> list[Event]:
    """Questions posées à l'humain dans le Mission Ledger du kit, hors celles d'un checkpoint de flow."""
    entries = _json_lines(path)
    if entries is None:
        return []
    source = path.relative_to(root).as_posix()
    events: list[Event] = []
    for entry in entries:
        if entry.get("event_type") != "task.question_asked":
            continue
        payload = entry.get("payload")
        question = payload.get("question") if isinstance(payload, Mapping) else None
        if not isinstance(question, Mapping) or question.get("origin") == "flow":
            continue
        events.append(Event(KIND_QUESTION, f"question:{question.get('id') or entry.get('id', '')}", source))
    return events


def _audit_blocks(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    for chunk in re.split(r"^---\s*$", text, flags=re.MULTILINE):
        fields: dict[str, str] = {}
        for line in chunk.splitlines():
            match = _AUDIT_FIELD.match(line.strip())
            if match:
                fields.setdefault(match["name"].strip(), match["value"].strip())
        if "Event" in fields:
            blocks.append(fields)
    return blocks


def audit_events(path: Path, root: Path) -> list[Event]:
    """Gates, questions et activation du mode autonome d'un fichier d'audit AI-DLC."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    source = path.relative_to(root).as_posix()
    events: list[Event] = []
    for rank, fields in enumerate(_audit_blocks(text)):
        name = fields["Event"]
        if name == "STAGE_AWAITING_APPROVAL" and fields.get("Revalidated", "").lower() != "true":
            events.append(Event(KIND_GATE, f"gate:{source}:{rank}", source))
        elif name == "DECISION_RECORDED":
            questions_file = fields.get("Questions File", "")
            key = _answer_file_key(questions_file) if questions_file else f"decision:{source}:{rank}"
            events.append(Event(KIND_QUESTION, key, source))
        elif name == "AUTONOMY_MODE_SET" and fields.get("Mode", "").lower() == "autonomous":
            events.append(Event(KIND_AUTONOMY, f"autonomy:{source}:{rank}", source))
    return events


def _files(run_dir: Path, parent: str, pattern: str) -> list[Path]:
    """Les fichiers ``pattern`` des dossiers nommés *parent* sous *run_dir*, sans descendre dans ``target/`` & co."""
    found: list[Path] = []
    for here, dirs, _names in os.walk(run_dir):
        dirs[:] = [d for d in dirs if d not in _SKIPPED_DIRS]
        if Path(here).name == parent:
            found.extend(p for p in Path(here).glob(pattern) if p.is_file())
    return sorted(found)


def run_events(run_dir: Path, stream: Path) -> list[Event] | None:
    """Les interventions d'un run, dédoublonnées ; ``None`` (inconnu) si la transcription ou le dépôt manque."""
    if not run_dir.is_dir():
        return None
    objects = _json_lines(stream)
    if not objects:
        return None
    events = transcript_events(objects, stream.name)
    for path in _files(run_dir, "authority", f"*{_AUTHORITY_SUFFIX}"):
        events.extend(authority_events(path, run_dir))
    for path in _files(run_dir, "ledger", "events.jsonl"):
        events.extend(ledger_events(path, run_dir))
    for path in _files(run_dir, "audit", "*.md"):
        events.extend(audit_events(path, run_dir))
    unique: dict[tuple[str, str], Event] = {}
    for event in events:
        unique.setdefault((event.kind, event.key), event)
    return list(unique.values())


def run_paths(workspace: Path, row: Mapping[str, Any]) -> tuple[Path, Path]:
    base = workspace / "tasks" / str(row["task_id"]).replace("/", "__") / str(row["arm"])
    return base / f"run{row['run_index']}", base / f"run{row['run_index']}.stream.jsonl"


def count_row(workspace: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    """Le compte d'un run de ``results.jsonl`` : ``human_gates`` vaut ``None`` quand le run n'est pas lisible."""
    run_dir, stream = run_paths(workspace, row)
    events = run_events(run_dir, stream)
    out: dict[str, Any] = {
        "task_id": row["task_id"],
        "arm": row["arm"],
        "run_index": row["run_index"],
        "success": row.get("success") is True,
        "valid": row.get("transcript_check") != "invalid",
    }
    if events is None:
        return {**out, "human_gates": None, "by_kind": None, "events": None}
    by_kind = {k: sum(1 for e in events if e.kind == k) for k in KINDS}
    return {**out, "human_gates": len(events), "by_kind": by_kind, "events": [e.to_dict() for e in events]}


def summarize_arm(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Interventions par tâche vérifiée d'un bras. Un run inconnu rend la médiane inconnue, jamais zéro."""
    valid = [r for r in runs if r["valid"]]
    known = [r for r in valid if r["human_gates"] is not None]
    resolved = [r for r in valid if r["success"]]
    resolved_known = [int(r["human_gates"]) for r in resolved if r["human_gates"] is not None]
    complete = bool(resolved) and len(resolved_known) == len(resolved)
    return {
        "n_runs": len(valid),
        "n_resolved": len(resolved),
        "human_gates_known_runs": len(known),
        "human_gates_known_ratio": (len(known) / len(valid)) if valid else None,
        "human_gates_total_known": sum(int(r["human_gates"]) for r in known),
        "human_gates_by_kind": {k: sum(int(r["by_kind"][k]) for r in known) for k in KINDS},
        "median_human_gates_resolved": statistics.median(resolved_known) if complete else None,
        "mean_human_gates_resolved": statistics.fmean(resolved_known) if complete else None,
    }


def build_report(
    workspace: Path, rows: Sequence[Mapping[str, Any]], arms: Sequence[str] | None = None
) -> dict[str, Any]:
    counted = [count_row(workspace, r) for r in rows if arms is None or r.get("arm") in arms]
    names = sorted({str(r["arm"]) for r in counted})
    return {
        "rule": RULE_ID,
        "arms": {arm: summarize_arm([r for r in counted if r["arm"] == arm]) for arm in names},
        "runs": counted,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"# Interventions humaines par tâche vérifiée (règle `{report['rule']}`)",
        "",
        "| Bras | Runs | Résolus | Comptés | Médiane/tâche résolue | Total compté | gate | refus | question | octroi | autonome |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, s in report["arms"].items():
        median = s["median_human_gates_resolved"]
        k = s["human_gates_by_kind"]
        lines.append(
            f"| `{arm}` | {s['n_runs']} | {s['n_resolved']} | {s['human_gates_known_runs']}/{s['n_runs']} | "
            f"{'inconnue' if median is None else f'{median:g}'} | {s['human_gates_total_known']} | "
            + " | ".join(str(k[x]) for x in KINDS)
            + " |"
        )
    partial = [a for a, s in report["arms"].items() if s["human_gates_known_ratio"] not in (None, 1.0)]
    if partial:
        lines += [
            "",
            "> Traces illisibles ou absentes pour : "
            + ", ".join(f"`{a}`" for a in partial)
            + ". Un run inconnu n'est pas compté pour zéro : la médiane est alors inconnue.",
        ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--workspace", type=Path, required=True, help="dossier du banc (state/results.jsonl, tasks/)")
    p.add_argument("--arm", action="append", help="limiter à ce bras (répétable) ; tous par défaut")
    p.add_argument("--json", action="store_true", help="sortie JSON complète, événements compris")
    args = p.parse_args(argv)
    results = args.workspace / "state" / "results.jsonl"
    if not results.is_file():
        print(f"results.jsonl introuvable sous {args.workspace}/state", file=sys.stderr)
        return 2
    report = build_report(args.workspace, read_results_rows(results), args.arm)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
