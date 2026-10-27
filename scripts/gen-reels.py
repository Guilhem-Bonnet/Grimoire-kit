#!/usr/bin/env python3
"""Enregistre des bobines de rejeu : de vraies exécutions du kit sur un projet jetable.

Chaque bobine rejoue une suite de commandes `grimoire` sur un projet fixture créé
dans un dossier temporaire, capture la sortie réelle, la normalise (chemins,
horodatages, identifiants de run) et l'écrit dans `web/data/reels/<id>.json` avec
son HTML statique `web/data/reels/<id>.html`.

Une bobine porte : la commande (la suite rejouée), la version du kit, la date
d'enregistrement et l'empreinte (sha256) de sa sortie normalisée. Le test
`tests/test_reels.py` réenregistre chaque bobine et échoue si l'empreinte ne
correspond plus à la sortie du kit courant.

Usage :
    python scripts/gen-reels.py            # réécrit web/data/reels/
    python scripts/gen-reels.py --check    # n'écrit rien ; sort 1 si une bobine a dérivé
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "data" / "reels"
SCHEMA = "grimoire-reel/v1"
_CLI = [sys.executable, "-m", "grimoire"]  # le kit courant, pas un `grimoire` d'un autre PATH

# Étapes : ("run", "<args grimoire>") | ("human", "<args>", canal humain sur pty) | ("write", "<fichier>", "<contenu>")
# {0}, {1}… désignent les tâches créées par `task add`, dans l'ordre (le slug de l'identifiant vient du titre).
REELS: list[dict] = [
    {
        "id": "cloture-refusee-puis-acceptee",
        "title": "Clôture refusée, puis acceptée",
        "summary": "Sans reçu de revue, la clôture d'une tâche V2 est refusée ; elle passe une fois la revue écrite.",
        "steps": [
            ("run", 'task add "Livrer fichier" -a "le livrable est relu par un humain" --check "test -f livrable.txt" --ready'),
            ("run", "task claim {0}"),
            ("run", "task move {0} --to running"),
            ("write", "livrable.txt", "livré\n"),
            ("run", "task move {0} --to needs_verification"),
            ("run", "task verify {0}"),
            ("run", "task close {0}"),
            ("run", "task review {0} --verdict READY --reviewer fixture"),
            ("run", "task close {0}"),
        ],
    },
    {
        "id": "decision-humaine-attestee",
        "title": "Décision humaine attestée",
        "summary": "L'agent pose une question ; seule la réponse du canal humain rend la tâche.",
        "steps": [
            ("run", 'task add "Choisir base" -a "le choix est tranche par un humain" --ready'),
            ("run", "task claim {0}"),
            ("run", 'task ask {0} "Quelle base de données ?" --option sqlite --option postgres'),
            ("run", "task show {0}"),
            ("human", "task answer {0} sqlite"),
            ("run", "task show {0}"),
        ],
    },
    {
        "id": "supersession",
        "title": "Supersession d'une décision",
        "summary": "Une décision remplacée n'est plus servie, mais reste lisible.",
        "steps": [
            ("run", 'memory remember "Le cache est stocké en JSON" --type decisions --agent fixture'),
            ("run", 'memory remember "Le cache est stocké en SQLite" --type decisions --agent fixture'),
            ("run", "memory list"),
            ("supersede", "JSON", "SQLite"),
            ("run", "memory list"),
            ("run", "memory list --include-superseded"),
        ],
    },
    {
        "id": "rappel-au-claim",
        "title": "Rappel au claim",
        "summary": "Réclamer une tâche voisine d'une tâche close rappelle ce que le projet en sait.",
        "steps": [
            ("run", 'task add "Ecrire la doc de l API" -a "le test passe" --check "test -f DOC.md" --ready'),
            ("run", "task claim {0}"),
            ("run", "task move {0} --to running"),
            ("run", "task move {0} --to needs_verification"),
            ("write", "DOC.md", "# Doc\n"),
            ("run", "task verify {0}"),
            ("run", "task close {0}"),
            ("run", 'task add "Ecrire la doc du CLI" -a "le test passe" --check "test -f DOC.md" --ready'),
            ("run", "task recall {1}"),
        ],
    },
    {
        "id": "tri-v0-v1-v2",
        "title": "Tri V0 / V1 / V2",
        "summary": "La vérifiabilité d'une tâche se lit à ses critères et à ses checks.",
        "steps": [
            ("run", 'task add "Tri mecanique" -a "le test passe" --check "true" --ready'),
            ("run", 'task add "Tri par revue" -a "revue de code sans constat" --ready'),
            ("run", 'task add "Tri sans check" -a "le rendu est elegant" --ready'),
            ("run", "task list"),
        ],
    },
]

_KEEP = {"GRIMOIRE_NO_COCKPIT"}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_NOISE = re.compile(r"^(Warning: You are sending unauthenticated|Loading weights|\S*BertModel LOAD REPORT|Key  |-{5,}\+|embeddings\.|Notes:|- UNEXPECTED)")


def _env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "GRIMOIRE_NO_COCKPIT": "1", "HF_HUB_OFFLINE": "1", "TQDM_DISABLE": "1",
        "NO_COLOR": "1", "COLUMNS": "100", "LC_ALL": "C.UTF-8", "HOME": str(home),
        "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    })
    # Le modèle d'embedding reste lu dans le cache réel (HOME est jetable, hors ligne).
    env.setdefault("HF_HOME", str(Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")))
    # Aucun signal d'hôte : sans eux, le canal humain n'est plus déduit « agent ».
    for k in list(env):
        if k.startswith(("CLAUDE", "CODEX", "CURSOR", "GEMINI", "COPILOT", "GRIMOIRE_")) and k not in _KEEP:
            env.pop(k)
    env.pop("AI_AGENT", None)
    return env


def _normalize(text: str, root: Path) -> str:
    text = _ANSI.sub("", text).replace("\r", "")
    text = text.replace(str(root), "<projet>")
    lines = [ln.rstrip() for ln in text.split("\n") if not _NOISE.match(ln)]
    text = "\n".join(lines).strip("\n")
    # horodatages ISO, durées, uuid et empreintes hexadécimales longues
    text = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?", "<date>", text)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<uuid>", text)
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{3,4}(?:-[0-9a-f]{4}){0,3}(?:-[0-9a-f]{12})?…?", "<uuid>", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "<date>", text)
    text = re.sub(r"\b[0-9a-f]{12,}\b", "<hex>", text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s?(?:ms|s)\b", "<durée>", text)
    return text


def _git(root: Path, env: dict[str, str], *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)


def _grimoire(root: Path, env: dict[str, str], args: str) -> tuple[int, str]:
    import shlex

    proc = subprocess.run(
        [*_CLI, *shlex.split(args)], cwd=root, env=env,
        capture_output=True, text=True, timeout=300, check=False,
    )
    return proc.returncode, _normalize(proc.stdout + proc.stderr, root)


def _grimoire_human(root: Path, env: dict[str, str], args: str, confirm: str = "o") -> tuple[int, str]:
    """Rejoue une commande sur un pseudo-terminal où l'humain confirme sa présence."""
    import pty
    import select
    import shlex

    pid, fd = pty.fork()
    if pid == 0:  # enfant
        os.chdir(root)
        os.execvpe(_CLI[0], [*_CLI, *shlex.split(args)], env)  # noqa: S606 - commande du kit, arguments figés par REELS
    chunks: list[bytes] = []
    answered = False
    while True:
        ready, _, _ = select.select([fd], [], [], 60)
        if not ready:
            break
        try:
            data = os.read(fd, 4096)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
        if not answered and b"[o/N]" in b"".join(chunks):
            os.write(fd, (confirm + "\n").encode())
            answered = True
    _, status = os.waitpid(pid, 0)
    text = b"".join(chunks).decode("utf-8", "replace").replace("\r\n", "\n")
    return os.waitstatus_to_exitcode(status), _normalize(text, root)


def kit_version() -> str:
    out = subprocess.run([*_CLI, "--version"], capture_output=True, text=True, check=True)
    return out.stdout.strip().split()[-1]


def record(reel: dict, version: str) -> dict:
    """Rejoue une bobine sur un projet jetable et rend son enregistrement."""
    work = Path(tempfile.mkdtemp(prefix="grimoire-reel-"))
    root = work / "fixture"
    root.mkdir()
    env = _env(work)
    try:
        _git(root, env, "init", "-q", "-b", "main")
        (root / "README.md").write_text("# fixture\n", encoding="utf-8")
        _git(root, env, "add", ".")
        _git(root, env, "-c", "commit.gpgsign=false", "commit", "-qm", "init")
        _grimoire(root, env, "up . --no-cockpit -b local")
        frames: list[dict] = []
        task_ids: list[str] = []
        for step in reel["steps"]:
            if step[0] == "supersede":
                code, out = _supersede(root, env, step[1], step[2])
                frames.append({"kind": "run", "command": "grimoire memory supersede <ancien> <nouveau>", "exit_code": code, "output": out})
                continue
            if step[0] == "write":
                (root / step[1]).write_text(step[2], encoding="utf-8")
                frames.append({"kind": "write", "command": f"écrire {step[1]}", "exit_code": 0, "output": ""})
                continue
            args = step[1].format(*task_ids)
            code, out = (_grimoire_human if step[0] == "human" else _grimoire)(root, env, args)
            if args.startswith("task add"):
                task_ids += re.findall(r"\bOK (GAO-[\w-]+?-\d{3})\b", out)
            frames.append({"kind": "run", "command": f"grimoire {args}", "exit_code": code, "output": out})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    digest = hashlib.sha256(
        json.dumps([[f["command"], f["exit_code"], f["output"]] for f in frames], ensure_ascii=False).encode()
    ).hexdigest()
    return {
        "schema": SCHEMA, "id": reel["id"], "title": reel["title"], "summary": reel["summary"],
        "command": [f["command"] for f in frames], "kit_version": version,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "output_sha256": digest, "frames": frames,
    }


def _supersede(root: Path, env: dict[str, str], old_word: str, new_word: str) -> tuple[int, str]:
    """Remplace la décision qui porte *old_word* par celle qui porte *new_word* (vrais identifiants)."""
    proc = subprocess.run(
        [*_CLI, "-o", "json", "memory", "list"], cwd=root, env=env,
        capture_output=True, text=True, check=False,
    )
    try:
        entries = json.loads(proc.stdout)["entries"]
        old = next(e["id"] for e in entries if old_word in json.dumps(e, ensure_ascii=False))
        new = next(e["id"] for e in entries if new_word in json.dumps(e, ensure_ascii=False))
    except (ValueError, KeyError, StopIteration):
        return 1, "supersession impossible : identifiants introuvables"
    code, out = _grimoire(root, env, f'memory supersede {old} {new} --reason "SQLite remplace JSON"')
    return code, out


_HTML = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — bobine de rejeu</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1d1b17; --muted:#6b665c; --card:#fff; --line:#e3ded2; --ok:#1a7f4b; --ko:#b3261e; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16150f; --fg:#efece3; --muted:#a39e90; --card:#201e16; --line:#38352a; --ok:#5fcf90; --ko:#ff8a80; }} }}
body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg); font:16px/1.5 system-ui, sans-serif; }}
main {{ max-width:860px; margin:0 auto; }}
h1 {{ font-size:1.5rem; margin:0 0 4px; }}
.meta {{ color:var(--muted); font-size:.85rem; margin:0 0 20px; overflow-wrap:anywhere; }}
.frame {{ background:var(--card); border:1px solid var(--line); border-radius:8px; margin:0 0 12px; padding:12px 14px; }}
.cmd {{ font:600 .9rem ui-monospace, monospace; margin:0 0 6px; }}
.cmd .rc {{ float:right; font-weight:400; }} .rc.ok {{ color:var(--ok); }} .rc.ko {{ color:var(--ko); }}
pre {{ margin:0; font:.82rem/1.45 ui-monospace, monospace; white-space:pre-wrap; overflow-wrap:anywhere; }}
</style>
</head>
<body>
<main>
<h1>{title}</h1>
<p>{summary}</p>
<p class="meta">kit {version} · enregistrée le {date} · empreinte de sortie <code>{digest}</code></p>
{frames}
</main>
</body>
</html>
"""


def render_html(reel: dict) -> str:
    frames = []
    for f in reel["frames"]:
        cls = "ok" if f["exit_code"] == 0 else "ko"
        out = f'<pre>{html.escape(f["output"])}</pre>' if f["output"] else ""
        frames.append(
            f'<section class="frame"><p class="cmd">$ {html.escape(f["command"])}'
            f'<span class="rc {cls}">sortie {f["exit_code"]}</span></p>{out}</section>'
        )
    return _HTML.format(
        title=html.escape(reel["title"]), summary=html.escape(reel["summary"]),
        version=html.escape(reel["kit_version"]), date=html.escape(reel["recorded_at"]),
        digest=reel["output_sha256"], frames="\n".join(frames),
    )


def write(reel: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{reel['id']}.json").write_text(json.dumps(reel, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / f"{reel['id']}.html").write_text(render_html(reel), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="n'écrit rien ; sort 1 si une bobine a dérivé")
    args = ap.parse_args()
    version = kit_version()
    drift = 0
    index = []
    for spec in REELS:
        reel = record(spec, version)
        index.append({k: reel[k] for k in ("id", "title", "summary", "kit_version", "recorded_at", "output_sha256")})
        if args.check:
            path = OUT / f"{spec['id']}.json"
            old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if old.get("output_sha256") != reel["output_sha256"]:
                drift += 1
                print(f"dérive : {spec['id']} ({old.get('output_sha256', 'absente')[:12]} → {reel['output_sha256'][:12]})")
        else:
            write(reel)
            print(f"bobine {spec['id']} · {reel['output_sha256'][:12]}")
    if not args.check:
        (OUT / "index.json").write_text(json.dumps({"schema": SCHEMA, "reels": index}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
