"""Paquets de jugement aveugle et application des verdicts (campagne 2026-09-04).

``pack`` : pour chaque run enregistré sans ``judgment.json``, construit sous
``evals/runs/<date>/_judge/<id>/`` un paquet anonymisé (identifiant opaque,
chemins épurés, aucune mention du bras) : ``packet.md`` (tâche, prompt, grille
``JUDGING.md`` de la tâche, résumé mécanique) et ``diff.patch``. La table de
correspondance privée est ``_judge/map.json``.

``apply`` : recopie chaque ``_judge/<id>/judgment.json`` rendu par un juge vers
le run correspondant, après validation du schéma de JUDGE-CONSIGNE.md.

    python evals/judge.py pack --date 2026-09-04
    python evals/judge.py apply --date 2026-09-04
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

EVALS = Path(__file__).resolve().parent
RUNS = EVALS / "runs"
WITNESS = EVALS / "witnesses" / "web-app-todo"
ARMS_PATTERN = re.compile(r"/(enforced|activated-v3)/rep-\d+/")


def _judging_section(task_id: str) -> str:
    text = (WITNESS / "JUDGING.md").read_text(encoding="utf-8")
    m = re.search(rf"^### {re.escape(task_id)}\n(.*?)(?=^### |\Z)", text, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _transverse_rules() -> str:
    text = (WITNESS / "JUDGING.md").read_text(encoding="utf-8")
    m = re.search(r"^## Règles transverses\n(.*?)(?=^## )", text, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _scrub(text: str, run_dir: Path) -> str:
    text = text.replace(str(run_dir), "<run>")
    text = ARMS_PATTERN.sub("/<arm>/rep-<n>/", text)
    return re.sub(r"(enforced|activated-v3)", "<arm>", text)


def pack(date: str) -> None:
    tasks = {
        t["id"]: t for t in yaml.safe_load((EVALS / "tasks" / "web-app-todo.yaml").read_text(encoding="utf-8"))["tasks"]
    }
    judge_dir = RUNS / date / "_judge"
    judge_dir.mkdir(parents=True, exist_ok=True)
    map_path = judge_dir / "map.json"
    mapping: dict[str, str] = json.loads(map_path.read_text(encoding="utf-8")) if map_path.is_file() else {}
    created = 0
    for rec_path in sorted((RUNS / date).glob("*/*/rep-*/record.json")):
        run_dir = rec_path.parent
        if (run_dir / "judgment.json").is_file() or (run_dir / "invalid.json").is_file():
            continue
        rel = str(run_dir.relative_to(RUNS / date))
        pid = hashlib.sha256(f"{date}:{rel}".encode()).hexdigest()[:10]
        packet = judge_dir / pid
        if (packet / "packet.md").is_file():
            continue
        packet.mkdir(exist_ok=True)
        mapping[pid] = rel
        task_id = run_dir.parent.parent.name
        task = tasks[task_id]
        mech = json.loads((run_dir / "mechanical.json").read_text(encoding="utf-8"))
        summary = {
            "tests_green": mech.get("tests_green"),
            "go_ok": mech["go"]["ok"],
            "go_vet_ok": mech["go_vet"]["ok"],
            "npm_ok": mech["npm"]["ok"],
            "go_mod_version": mech.get("go_mod_version"),
            "baseline_test_files": {
                k: {"status": v["status"], "missing_tests": v["missing_tests"]}
                for k, v in mech["baseline_test_files"].items()
            },
            "overlay_green_informatif": mech["baseline_summary"]["overlay_green"],
        }
        tails = {
            "go_tail": _scrub(mech["go"]["tail"][-1500:], run_dir),
            "npm_tail": _scrub(mech["npm"]["tail"][-1200:], run_dir),
        }
        body = f"""# Paquet de jugement {pid}

## Tâche `{task_id}` ({task["kind"]})

Prompt reçu par l'agent :

> {task["prompt"].strip()}

## Grille de jugement (JUDGING.md) — règles transverses

{_transverse_rules()}

## Grille de jugement — critères de la tâche `{task_id}`

{_judging_section(task_id)}

## Résultats mécaniques (suites réellement exécutées sur l'état final)

```json
{json.dumps(summary, indent=2, ensure_ascii=False)}
```

Sortie `go test ./...` :

```text
{tails["go_tail"]}
```

Sortie `npm test` :

```text
{tails["npm_tail"]}
```

## Diff

Voir `diff.patch` dans ce dossier (baseline → état final, hors artefacts du
standard et configuration d'hôte).
"""
        (packet / "packet.md").write_text(body, encoding="utf-8")
        (packet / "diff.patch").write_text(
            _scrub((run_dir / "diff.patch").read_text(encoding="utf-8"), run_dir), encoding="utf-8"
        )
        created += 1
    map_path.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    print(f"{created} paquet(s) créé(s) sous {judge_dir}")


REQUIRED = ("completed", "criteria", "regressions_primary", "regressions_hard", "regressions_adapted", "notes")


def apply(date: str) -> None:
    judge_dir = RUNS / date / "_judge"
    mapping = json.loads((judge_dir / "map.json").read_text(encoding="utf-8"))
    applied, pending = 0, []
    for pid, rel in mapping.items():
        src = judge_dir / pid / "judgment.json"
        dst = RUNS / date / rel / "judgment.json"
        if dst.is_file():
            continue
        if not src.is_file():
            pending.append(pid)
            continue
        data: dict[str, Any] = json.loads(src.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED if k not in data]
        if missing or not isinstance(data["completed"], bool):
            raise SystemExit(f"jugement {pid} invalide : champs manquants {missing} ou completed non booléen")
        data["judge_packet"] = pid
        dst.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        applied += 1
    print(f"{applied} jugement(s) appliqué(s) ; en attente : {len(pending)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pack", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--date", required=True)
    args = ap.parse_args()
    (pack if args.cmd == "pack" else apply)(args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
