"""La Flotte en direct, lot B — la vue en fils (artboard « Proposition A : Tissage »).

Ce que ce module verrouille sans navigateur :

- la rubrique « En direct » est déclarée dans `shell.js`, et son module est
  servi par la même arborescence statique que la coque ;
- ni l'écran ni sa géométrie ne portent une couleur littérale : les teintes de
  fournisseur sont des tokens `--p-*`, sombres et clairs ;
- à partir d'une réponse `fleet/live` d'exemple, la géométrie pure rend un
  chemin par session, un chemin de départ et un chemin de retour par
  sous-agent, et un repère par changement de modèle.

La géométrie (`spaces/fils-geometry.js`) est sans DOM : Node l'exécute
directement, comme `tests/unit/test_workspace_score.py` le fait pour le score.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT / "web" / "workspace"
SHELL = WORKSPACE / "shell.js"
SCREEN = WORKSPACE / "spaces" / "fils.js"
GEOMETRY = WORKSPACE / "spaces" / "fils-geometry.js"
TOKENS = WORKSPACE / "tokens.css"

# ── La rubrique et le module servi ──────────────────────────────────────────

_SECTION = re.compile(r"\{\s*id: 'en-direct',\s*module: '([A-Za-z0-9_-]+)',\s*label: 'En direct'")


def test_la_section_en_direct_est_declaree_dans_shell_js() -> None:
    found = _SECTION.search(SHELL.read_text(encoding="utf-8"))

    assert found, "shell.js ne déclare pas la rubrique « En direct » de la Flotte"
    assert found.group(1) == "fils"


def test_le_module_de_la_section_est_servi_par_la_coque() -> None:
    module = _SECTION.search(SHELL.read_text(encoding="utf-8")).group(1)
    shell = SHELL.read_text(encoding="utf-8")

    # La coque charge ses rubriques par un import relatif (`./spaces/<module>.js`),
    # depuis la même arborescence `web/` que le serveur statique copie en entier.
    assert "./spaces/${section.module}.js" in shell
    assert (WORKSPACE / "spaces" / f"{module}.js").is_file()

    cockpit = (ROOT / "src" / "grimoire" / "cli" / "cmd_cockpit.py").read_text(encoding="utf-8")
    assert "src = web_path()" in cockpit
    assert "copytree(src, serve_dir" in cockpit
    assert "directory=str(serve_dir)" in cockpit


def test_les_tokens_de_fournisseur_sont_declares_dans_les_deux_themes() -> None:
    text = TOKENS.read_text(encoding="utf-8")
    providers = ("--p-anthropic", "--p-openai", "--p-google", "--p-deepseek", "--p-mistral", "--p-local", "--p-inconnu")

    for block in (":root {", ':root[data-theme="light"] {', ':root:not([data-theme="dark"]) {'):
        start = text.index(block)
        body = text[start : text.index("}", start)]
        missing = [token for token in providers if token not in body]
        assert not missing, f"{missing} absents de {block}"


# ── Aucune couleur littérale ────────────────────────────────────────────────

_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_FUNC = re.compile(r"\b(?:rgba?|hsla?|color|oklch|lab)\s*\(", re.IGNORECASE)
_NAMED = re.compile(
    r"(?<![\w-])(?:white|black|red|green|blue|orange|grey|gray|silver|"
    r"yellow|purple|teal|cyan|magenta|navy|olive|maroon|lime|aqua|fuchsia)"
    r"(?![\w-])",
    re.IGNORECASE,
)


@pytest.mark.parametrize("source", [SCREEN, GEOMETRY], ids=lambda path: path.name)
def test_aucune_couleur_litterale_dans_la_vue_en_fils(source: Path) -> None:
    offenders: list[str] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith(("*", "//", "/*")):
            continue
        if any(pattern.search(line) for pattern in (_HEX, _FUNC, _NAMED)):
            offenders.append(f"{source.name}:{line_number} — {stripped[:90]}")

    assert not offenders, "couleur littérale hors de tokens.css :\n  " + "\n  ".join(offenders)


# ── La géométrie, exécutée sous Node ────────────────────────────────────────

_NOW = "2026-11-12T15:00:00+00:00"

#: Une réponse `fleet/live` d'exemple : un worker actif qui change deux fois de
#: modèle et boucle sur le même échec, son sous-agent, et un relecteur lancé
#: par le tick. Les champs sont ceux du lot A (`workspace_fleet_live._assemble`).
EXAMPLE = {
    "schemaVersion": "grimoire-workspace-fleet-live/v1",
    "generated_at": _NOW,
    "window_minutes": 240,
    "projects": ["grimoire-kit"],
    "sessions": [
        {
            "id": "sess-parent",
            "project": "grimoire-kit",
            "role": "worker",
            "parent_id": None,
            "task_id": "GAO-flotte-en-di-003",
            "pr": None,
            "worktree": None,
            "branch": "task/GAO-flotte-en-di-003",
            "started_at": "2026-11-12T14:10:00+00:00",
            "last_at": "2026-11-12T14:58:00+00:00",
            "liveness": "active",
            "model": {
                "current": "claude-opus-5-5",
                "requested": "claude-sonnet-5-5",
                "provider": "anthropic",
                "tier": "strong",
                "effort": "high",
            },
            "model_changes": [
                {
                    "at": "2026-11-12T14:24:00+00:00",
                    "from": "claude-sonnet-5-5",
                    "to": "claude-opus-5-5",
                    "kind": "upgrade",
                    "turn": 2,
                },
                {
                    "at": "2026-11-12T14:40:00+00:00",
                    "from": "claude-opus-5-5",
                    "to": "deepseek-flash",
                    "kind": "fallback",
                    "turn": 5,
                },
            ],
            "turns": {"count": 8, "by_origin": {"human": 8}},
            "usage": {"input": 90000, "output": 5000, "cache_read": 0, "cost_usd": 1.25},
            "lanes": [],
            "markers": [
                {"at": "2026-11-12T14:30:00+00:00", "kind": "error", "label": "gate"},
                {"at": "2026-11-12T14:32:00+00:00", "kind": "error", "label": "gate"},
                {"at": "2026-11-12T14:34:00+00:00", "kind": "error", "label": "gate"},
            ],
            "tail": [
                {
                    "at": "2026-11-12T14:56:00+00:00",
                    "tool": "Bash",
                    "summary": "pytest -q tests/unit",
                    "status": "done",
                    "excerpt": "…",
                },
            ],
            "subagents": ["agent-1"],
            "links": {
                "task": "/workspace/index.html#executer?task=GAO-flotte-en-di-003",
                "pr": None,
                "resume": "claude --resume sess-parent",
                "transcript": "/tmp/sess-parent.jsonl",
            },
        },
        {
            "id": "agent-1",
            "project": "grimoire-kit",
            "role": "subagent",
            "parent_id": "sess-parent",
            "task_id": None,
            "pr": None,
            "worktree": None,
            "branch": None,
            "started_at": "2026-11-12T14:20:00+00:00",
            "last_at": "2026-11-12T14:30:00+00:00",
            "liveness": "ended",
            "model": {
                "current": "claude-haiku-4-5",
                "requested": "claude-haiku-4-5",
                "provider": "anthropic",
                "tier": "cheap",
                "effort": None,
            },
            "model_changes": [],
            "turns": {"count": 2, "by_origin": {"agent": 2}},
            "usage": {"input": 1000, "output": 500, "cache_read": 0, "cost_usd": None},
            "lanes": [],
            "markers": [],
            "tail": [],
            "subagents": [],
            "links": {"task": None, "pr": None, "resume": None, "transcript": "/tmp/agent-1.jsonl"},
        },
        {
            "id": "sess-reviewer",
            "project": "grimoire-kit",
            "role": "reviewer",
            "parent_id": None,
            "task_id": "GAO-flotte-en-di-003",
            "pr": None,
            "worktree": None,
            "branch": "task/GAO-flotte-en-di-003",
            "started_at": "2026-11-12T14:50:00+00:00",
            "last_at": "2026-11-12T14:59:00+00:00",
            "liveness": "waiting",
            "model": {
                "current": "deepseek-flash",
                "requested": "deepseek-flash",
                "provider": "deepseek",
                "tier": "cheap",
                "effort": None,
            },
            "model_changes": [],
            "turns": {"count": 1, "by_origin": {"agent": 1}},
            "usage": {"input": 3000, "output": 300, "cache_read": 0, "cost_usd": None},
            "lanes": [],
            "markers": [],
            "tail": [],
            "subagents": [],
            "links": {"task": None, "pr": None, "resume": None, "transcript": "/tmp/sess-reviewer.jsonl"},
        },
    ],
    "edges": [
        {"from": "sess-parent", "to": "agent-1", "kind": "spawned"},
        {"from": "sess-parent", "to": "sess-reviewer", "kind": "launched"},
    ],
}


def _layout(payload: dict[str, object], options: dict[str, object] | None = None) -> dict[str, object]:
    if shutil.which("node") is None:
        pytest.skip("node absent")
    script = (
        "import(process.argv[1]).then(m => console.log(JSON.stringify("
        "m.layoutFleetThreads(JSON.parse(process.argv[2]), JSON.parse(process.argv[3])))))"
    )
    out = subprocess.run(
        ["node", "-e", script, GEOMETRY.as_uri(), json.dumps(payload), json.dumps(options or {})],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return dict(json.loads(out.stdout))


def test_un_chemin_par_session() -> None:
    layout = _layout(EXAMPLE)

    threads = layout["threads"]
    assert len(threads) == len(EXAMPLE["sessions"])

    for thread in threads:
        assert str(thread["path"]).startswith("M "), thread
        assert float(thread["x1"]) >= float(thread["x0"])
        assert 0 <= float(thread["y"]) <= float(layout["height"])
        assert thread["label"]["token"].startswith("--p-")


def test_un_sous_agent_a_un_chemin_de_depart_et_un_chemin_de_retour() -> None:
    layout = _layout(EXAMPLE)

    spawned = [edge for edge in layout["edges"] if edge["kind"] == "spawned"]
    assert len(spawned) == 1
    assert spawned[0]["from"] == "sess-parent"
    assert spawned[0]["to"] == "agent-1"
    assert str(spawned[0]["departure"]).startswith("M ")
    assert str(spawned[0]["return"]).startswith("M ")
    assert spawned[0]["return_point"]["y"] == next(
        thread["y"] for thread in layout["threads"] if thread["id"] == "sess-parent"
    )

    launched = [edge for edge in layout["edges"] if edge["kind"] == "launched"]
    assert len(launched) == 1
    assert str(launched[0]["departure"]).startswith("M ")
    assert "return" not in launched[0]


def test_un_repère_par_changement_de_modele() -> None:
    layout = _layout(EXAMPLE)

    expected = sum(len(session["model_changes"]) for session in EXAMPLE["sessions"])
    assert len(layout["changes"]) == expected == 2
    assert [change["label"] for change in layout["changes"]] == ["Sonnet → Opus", "Opus → Flash"]
    for change in layout["changes"]:
        assert change["session_id"] == "sess-parent"
        assert 0 <= change["x"] <= float(layout["plot_right"])


def test_une_boucle_et_l_epaisseur_des_jetons() -> None:
    layout = _layout(EXAMPLE)

    assert [loop["label"] for loop in layout["loops"]] == ["même échec, trois fois"]
    parent = next(thread for thread in layout["threads"] if thread["id"] == "sess-parent")
    child = next(thread for thread in layout["threads"] if thread["id"] == "agent-1")
    assert parent["label"]["stroke"] == 3  # 95 000 jetons
    assert child["label"]["stroke"] == 1.5  # 1 500 jetons


# ── Revue visuelle de #1199 contre l'artboard « Tissage » ────────────────────


def _with_parent(**changes: object) -> dict[str, object]:
    payload = copy.deepcopy(EXAMPLE)
    payload["sessions"][0].update(changes)  # type: ignore[index]
    return payload


def test_un_repere_hors_fenetre_n_est_pas_dessine_au_bord_gauche() -> None:
    """Un changement du 06/11 dans une fenêtre de 4 h était borné au bord
    gauche avec son étiquette ; la boucle comptait des erreurs hors fenêtre."""
    old = "2026-11-06T01:06:10+00:00"
    payload = _with_parent(
        model_changes=[{"at": old, "from": "claude-sonnet-5", "to": "claude-opus-5-5", "kind": "upgrade", "turn": 1}],
        markers=[{"at": old, "kind": "error", "label": "gate"}] * 3,
    )

    layout = _layout(payload)

    assert layout["changes"] == []
    assert layout["loops"] == []
    parent = next(thread for thread in layout["threads"] if thread["id"] == "sess-parent")
    assert parent["markers"] == []
    assert all(not segment["pale"] for segment in parent["segments"])


def test_aucune_etiquette_ne_se_recouvre_ni_ne_deborde_dans_la_colonne_des_noms() -> None:
    """Deux bascules, une boucle et une fin muette sur des fils voisins."""
    payload = copy.deepcopy(EXAMPLE)
    payload["sessions"][2].update(liveness="silent", last_at="2026-11-12T14:59:00+00:00")  # type: ignore[index]

    layout = _layout(payload)

    labels = layout["labels"]
    kinds = sorted(label["kind"] for label in labels)
    assert kinds == ["change", "change", "loop", "silent"], kinds
    boxes = [label["box"] for label in labels]
    for i, a in enumerate(boxes):
        assert a[2] <= float(layout["plot_right"]) - 12, ("dans la colonne des noms", labels[i])
        for b in boxes[i + 1 :]:
            assert not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]), (a, b)
    # Une seule étiquette de boucle, jamais répétée.
    assert sum(1 for label in labels if label["text"] == "même échec, trois fois") == 1


def test_un_nom_trop_long_est_tronque_avec_une_ellipse() -> None:
    name = "work/harmonisation-followup-20260703"
    payload = _with_parent(task_id=None, branch=name)

    layout = _layout(payload)

    label = next(thread for thread in layout["threads"] if thread["id"] == "sess-parent")["label"]
    assert label["name"] == name
    assert label["short"].endswith("…")
    assert len(label["short"]) < len(name)


def test_un_sous_agent_se_nomme_par_sa_demande_et_pas_par_la_branche_du_parent() -> None:
    if shutil.which("node") is None:
        pytest.skip("node absent")
    script = (
        "import(process.argv[1]).then(m => console.log(JSON.stringify(["
        "m.threadName({role:'subagent', branch:'work/x', agent:{type:'Explore', description:'Angle santé technique'}}),"
        "m.threadName({role:'subagent', branch:'work/x', agent:{type:'Explore', description:null}}),"
        "m.threadName({role:'subagent', id:'a83e24234221cd19a', branch:'work/x'}),"
        "m.threadName({role:'worker', task_id:'GAO-x', branch:'work/x'})])))"
    )
    out = subprocess.run(
        ["node", "-e", script, GEOMETRY.as_uri()], capture_output=True, text=True, check=True, timeout=30
    )
    assert json.loads(out.stdout) == ["Angle santé technique", "Explore", "sous-agent a83e2423", "GAO-x"]
