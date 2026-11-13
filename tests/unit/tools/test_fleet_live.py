"""La Flotte en direct, lot A — la couche de données (issue #1193).

Fixtures uniquement : les journaux sont écrits sous un ``CLAUDE_CONFIG_DIR``
temporaire (le ``HOME`` des tests est déjà isolé par ``tests/conftest.py``), et
le registre du projet est celui du projet jetable. Ce que ces tests verrouillent :

- une session et ses deux sous-agents donnent trois sessions et deux arêtes
  ``spawned``, avec le modèle demandé (méta) et le modèle choisi
  (``pickedEffortModel``) ;
- un passage sonnet → opus est un ``upgrade``, et un modèle servi différent du
  modèle demandé au même tour est un ``fallback`` ;
- une session de worker lancée par le tick porte son rôle, sa tâche et sa PR ;
- la queue des outils est tronquée à vingt appels, sans ANSI, secrets masqués ;
- une deuxième lecture sans changement de fichier n'ouvre aucun fichier — et
  cinquante sessions tiennent sous les 150 ms à chaud, mesuré ;
- ``project=*`` couvre tous les projets enrôlés.
"""

from __future__ import annotations

import json
import os
import re
import time
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.missions.launcher import LAUNCHED_EVENT
from grimoire.missions.merger import MERGED_EVENT
from grimoire.missions.schemas import TaskState
from grimoire.missions.service import TaskService
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_fleet_live as fleet

NOW = datetime.now(UTC).replace(microsecond=0)

REGISTRY = """\
providers:
  - id: "anthropic"
    enabled: true
    models:
      - id: "claude-sonnet-5-5"
        tier: "mid"
      - id: "claude-opus-5-5"
        tier: "strong"
      - id: "claude-haiku-4-5"
        tier: "cheap"
  - id: "deepseek"
    enabled: true
    models:
      - id: "deepseek-flash"
        tier: "cheap"
      - id: "deepseek-pro"
        tier: "strong"
"""


@pytest.fixture(autouse=True)
def _cache_froid() -> None:
    """Chaque test part d'un cache de journaux froid, comme un serveur qui démarre."""
    fleet.reset_cache()


@pytest.fixture
def claude_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "home"))
    root = tmp_path / "projet"
    root.mkdir()
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    (root / "_grimoire/standard/llm-provider-registry.yaml").write_text(REGISTRY, encoding="utf-8")
    reg.save_registry([{"slug": "projet", "name": "Projet", "path": str(root.resolve())}])
    return root


# ── Fixtures de journaux ────────────────────────────────────────────────────


def _slug(root: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(root.resolve()))


def _journal_dir(claude_home: Path, root: Path) -> Path:
    directory = claude_home / "projects" / _slug(root)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _assistant(
    session: str,
    message_id: str,
    model: str,
    *,
    requested: str | None = None,
    effort: str = "high",
    at: str = "2026-11-12T10:00:00Z",
    tools: tuple[dict[str, Any], ...] = (),
    origin: str | None = None,
    entrypoint: str | None = "cli",
    cwd: str | None = None,
) -> dict[str, Any]:
    line: dict[str, Any] = {
        "type": "assistant",
        "timestamp": at,
        "effort": effort,
        "sessionId": session,
        "message": {
            "id": message_id,
            "model": model,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": list(tools),
        },
    }
    if requested:
        line["requestedModel"] = requested
    if origin:
        line["turnOrigin"] = origin
    if entrypoint:
        line["entrypoint"] = entrypoint
    if cwd:
        line["cwd"] = cwd
    return line


def _tool(name: str, tool_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": payload}


def _result(
    session: str, tool_id: str, content: str, *, error: bool = False, at: str = "2026-11-12T10:00:00Z"
) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": at,
        "sessionId": session,
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "is_error": error, "content": content}]
        },
    }


def _write(path: Path, lines: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def _subagent(
    claude_home: Path,
    root: Path,
    session: str,
    agent: str,
    *,
    model: str,
    picked: str,
    at: str = "2026-11-12T10:00:00Z",
) -> None:
    base = _journal_dir(claude_home, root)
    _write(
        base / session / "subagents" / f"agent-{agent}.jsonl",
        [
            _assistant(session, f"m-{agent}", model, entrypoint=None, at=at),
        ],
    )
    (base / session / "subagents" / f"agent-{agent}.meta.json").write_text(
        json.dumps(
            {
                "agentType": "explorer",
                "description": f"sous-agent {agent}",
                "model": model,
                "pickedEffortModel": picked,
                "spawnDepth": 1,
                "requestShape": "explore",
                "requestNonInteractive": True,
                "finished": True,
                "toolUseId": f"tool-{agent}",
            }
        ),
        encoding="utf-8",
    )


# ── 1. Sessions, sous-agents, arêtes ───────────────────────────────────────


def test_une_session_et_deux_sous_agents_donnent_trois_sessions_et_deux_aretes(
    project: Path, claude_home: Path
) -> None:
    parent = "11111111-2222-3333-4444-555555555555"
    _write(
        _journal_dir(claude_home, project) / f"{parent}.jsonl",
        [
            _assistant(parent, "m1", "claude-sonnet-5-5", origin="human"),
        ],
    )
    _subagent(claude_home, project, parent, "a1", model="claude-haiku-4-5", picked="claude-haiku-4-5")
    _subagent(claude_home, project, parent, "a2", model="claude-opus-5-5", picked="claude-opus-5-5")

    view = fleet.fleet_live_view(project, now=NOW)
    by_id = {session["id"]: session for session in view["sessions"]}

    assert set(by_id) == {parent, "a1", "a2"}
    assert len(view["edges"]) == 2
    assert all(edge["kind"] == "spawned" and edge["from"] == parent for edge in view["edges"])
    assert set(by_id[parent]["subagents"]) == {"a1", "a2"}
    assert by_id["a1"]["parent_id"] == parent
    assert by_id["a1"]["role"] == "subagent"
    assert by_id["a1"]["model"]["requested"] == "claude-haiku-4-5"
    assert by_id["a1"]["model"]["picked"] == "claude-haiku-4-5"
    assert by_id["a2"]["model"]["requested"] == "claude-opus-5-5"
    assert by_id["a2"]["model"]["picked"] == "claude-opus-5-5"
    # Le nom du fil d'un sous-agent : ce qu'on lui a demandé, et sa persona.
    assert by_id["a1"]["agent"] == {"type": "explorer", "description": "sous-agent a1"}
    assert by_id[parent]["agent"] is None


def test_le_fournisseur_et_le_palier_viennent_du_registre(project: Path, claude_home: Path) -> None:
    """``deepseek-flash`` → deepseek/cheap ; un modèle absent du registre reste ``inconnu``."""
    session = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    _write(
        _journal_dir(claude_home, project) / f"{session}.jsonl",
        [
            _assistant(session, "m1", "deepseek-flash"),
            _assistant(session, "m2", "modele-fantome"),
        ],
    )

    view = fleet.fleet_live_view(project, now=NOW)
    first = view["sessions"][0]
    # Le dernier modèle servi est l'inconnu ; sa provenance n'est pas devinée.
    assert first["model"]["current"] == "modele-fantome"
    assert first["model"]["provider"] == "inconnu"
    assert first["model"]["tier"] is None


def test_un_modele_claude_absent_du_registre_vaut_anthropic_par_son_prefixe(project: Path, claude_home: Path) -> None:
    """Le gabarit du registre ne déclare aucun modèle : ``claude-fable-5-2``
    tombait en ``inconnu`` et la vue en fils peignait tout en gris (#1199)."""
    session = "abababab-bbbb-cccc-dddd-eeeeeeeeeeee"
    _write(
        _journal_dir(claude_home, project) / f"{session}.jsonl",
        [_assistant(session, "m1", "claude-fable-5-2")],
    )

    first = fleet.fleet_live_view(project, now=NOW)["sessions"][0]

    assert first["model"]["provider"] == "anthropic"
    # Le palier, lui, n'est déclaré que par le registre : pas de devinette.
    assert first["model"]["tier"] is None


# ── 2. Changements de modèle ────────────────────────────────────────────────


def test_sonnet_vers_opus_est_un_upgrade_et_un_servi_different_du_demande_est_un_fallback(
    project: Path, claude_home: Path
) -> None:
    session = "99999999-8888-7777-6666-555555555555"
    _write(
        _journal_dir(claude_home, project) / f"{session}.jsonl",
        [
            _assistant(session, "m1", "claude-sonnet-5-5", at="2026-11-12T10:00:00Z"),
            _assistant(session, "m2", "claude-opus-5-5", at="2026-11-12T10:01:00Z"),
            _assistant(session, "m3", "claude-sonnet-5-5", requested="claude-opus-5-5", at="2026-11-12T10:02:00Z"),
        ],
    )

    view = fleet.fleet_live_view(project, now=NOW)
    session_view = view["sessions"][0]

    kinds = [change["kind"] for change in session_view["model_changes"]]
    assert kinds == ["upgrade", "fallback"]
    assert session_view["model_changes"][0]["from"] == "claude-sonnet-5-5"
    assert session_view["model_changes"][0]["to"] == "claude-opus-5-5"
    assert session_view["model_changes"][0]["turn"] == 2
    # Les couloirs suivent les mêmes segments que les changements.
    assert [(lane["model"], lane["effort"]) for lane in session_view["lanes"]] == [
        ("claude-sonnet-5-5", "high"),
        ("claude-opus-5-5", "high"),
        ("claude-sonnet-5-5", "high"),
    ]


def test_un_identifiant_de_message_compte_une_seule_fois_et_pas_par_bloc(project: Path, claude_home: Path) -> None:
    """Un appel API s'écrit en plusieurs lignes ``assistant`` ; l'usage ne se somme pas."""
    session = "12121212-3434-5656-7878-909090909090"
    blocks = [_tool("Read", "t1", {"file_path": "a.py"}), _tool("Bash", "t2", {"command": "pytest -q"})]
    lines = [
        _assistant(session, "m1", "claude-sonnet-5-5", tools=tuple(blocks[:1])),
        _assistant(session, "m1", "claude-sonnet-5-5", tools=tuple(blocks)),
    ]
    _write(_journal_dir(claude_home, project) / f"{session}.jsonl", lines)

    view = fleet.fleet_live_view(project, now=NOW)
    session_view = view["sessions"][0]

    assert session_view["turns"]["count"] == 1
    assert session_view["usage"]["input"] == 10
    assert session_view["usage"]["output"] == 5
    assert [call["tool"] for call in session_view["tail"]] == ["Read", "Bash"]


# ── 3. Rôle, tâche et PR depuis le ledger ──────────────────────────────────


def test_une_session_de_worker_lancee_par_le_tick_porte_son_role_sa_tache_et_sa_pr(
    project: Path, claude_home: Path
) -> None:
    service = TaskService(project)
    task = service.add("Une tâche lancée", ("un critère",), owner="guilhem", actor="t").task
    service.ledger.transition_task(task.id, TaskState.READY, actor_id="t")
    service.ledger.transition_task(task.id, TaskState.CLAIMED, actor_id="kit-dev")
    session = "abcdefab-cdef-abcd-efab-cdefabcdefab"
    service.ledger.append_event(
        LAUNCHED_EVENT,
        task.id,
        "task",
        "kit-dev",
        {
            "session_id": session,
            "model": "claude-sonnet-5-5",
            "provider": "anthropic",
            "cwd": str(project),
            "branch": f"task/{task.id}",
        },
    )
    service.ledger.append_event(MERGED_EVENT, task.id, "task", "kit-dev", {"pr": 1183})
    _write(
        _journal_dir(claude_home, project) / f"{session}.jsonl",
        [
            _assistant(session, "m1", "claude-sonnet-5-5", at="2026-11-12T10:00:00Z"),
        ],
    )

    view = fleet.fleet_live_view(project, now=NOW)
    session_view = view["sessions"][0]

    assert session_view["role"] == "worker"
    assert session_view["task_id"] == task.id
    assert session_view["pr"]["number"] == 1183
    assert session_view["branch"] == f"task/{task.id}"
    assert session_view["links"]["task"]
    assert session_view["links"]["resume"] == f"claude --resume {session}"


# ── 4. La queue des outils : tronquée, sans ANSI, secrets masqués ──────────


def test_tail_est_tronquee_sans_ansi_et_masque_les_secrets(project: Path, claude_home: Path) -> None:
    session = "0f0f0f0f-1e1e1e1e-2d2d2d2d-3c3c3c3c-4b4b4b4b4b4b"
    tool_calls = [_tool("Bash", f"b{index}", {"command": f"commande {index}"}) for index in range(25)]
    long_command = "echo " + "x" * 400
    tool_calls.append(_tool("Bash", "long", {"command": "\x1b[31m" + long_command + "\x1b[0m"}))
    lines: list[dict[str, Any]] = [
        _assistant(session, "m1", "claude-sonnet-5-5", tools=tuple(tool_calls)),
    ]
    for index in range(25):
        lines.append(_result(session, f"b{index}", f"sortie {index}"))
    lines.append(_result(session, "long", "\x1b[32mtoken ghp_abcdefghijklmnopqrstuvwxyz0123456789\x1b[0m"))
    _write(_journal_dir(claude_home, project) / f"{session}.jsonl", lines)

    view = fleet.fleet_live_view(project, now=NOW)
    tail = view["sessions"][0]["tail"]

    assert len(tail) == fleet.TAIL_CALLS
    last = tail[-1]
    assert last["summary"].startswith("echo x")
    assert "\x1b" not in last["summary"]
    assert len(last["summary"]) == fleet.SUMMARY_CHARS
    assert last["status"] == "ok"
    assert "\x1b" not in last["excerpt"]
    assert "ghp_" not in last["excerpt"]
    assert "[redacted:github-token]" in last["excerpt"]


def test_un_outil_en_erreur_est_marque_et_laisse_un_marker(project: Path, claude_home: Path) -> None:
    session = "5a5a5a5a-6b6b6b6b-7c7c7c7c-8d8d8d8d-9e9e9e9e9e9e"
    _write(
        _journal_dir(claude_home, project) / f"{session}.jsonl",
        [
            _assistant(session, "m1", "claude-sonnet-5-5", tools=(_tool("Bash", "e1", {"command": "false"}),)),
            _result(session, "e1", "commande refusée par la gate", error=True),
        ],
    )

    view = fleet.fleet_live_view(project, now=NOW)
    session_view = view["sessions"][0]

    assert session_view["tail"][0]["status"] == "error"
    assert session_view["markers"][0]["kind"] == "error"
    assert "gate" in session_view["markers"][0]["label"]


# ── 5. Lecture incrémentale et budget à chaud ──────────────────────────────


def _fifty_sessions(claude_home: Path, root: Path) -> None:
    directory = _journal_dir(claude_home, root)
    for index in range(50):
        session = f"{index:08d}-0000-0000-0000-000000000000"
        lines = [
            _assistant(
                session,
                f"m{index}-{turn}",
                "claude-sonnet-5-5",
                tools=(_tool("Read", f"r{index}-{turn}", {"file_path": f"f{turn}.py"}),),
                at=f"2026-11-12T10:{turn:02d}:00Z",
            )
            for turn in range(40)
        ]
        _write(directory / f"{session}.jsonl", lines)


def test_deuxieme_lecture_sans_changement_n_ouvre_aucun_fichier(project: Path, claude_home: Path) -> None:
    _fifty_sessions(claude_home, project)

    first = fleet.fleet_live_view(project, now=NOW)
    assert len(first["sessions"]) == 50
    opened = fleet.stats()["opens"]
    assert opened == 50, "la première lecture doit ouvrir chaque journal une fois"

    second = fleet.fleet_live_view(project, now=NOW)
    assert len(second["sessions"]) == 50
    assert fleet.stats()["opens"] == opened, "un fichier inchangé n'est pas rouvert"


def test_cinquante_sessions_tiennent_sous_les_150_ms_a_chaud(project: Path, claude_home: Path) -> None:
    _fifty_sessions(claude_home, project)
    fleet.fleet_live_view(project, now=NOW)  # première passe : remplit le cache

    started = time.perf_counter()
    view = fleet.fleet_live_view(project, now=NOW)
    elapsed = time.perf_counter() - started

    assert len(view["sessions"]) == 50
    assert elapsed < 0.150, f"lecture chaude de 50 sessions : {elapsed * 1000:.1f} ms, budget 150 ms"


def test_un_journal_qui_grandit_est_relu_depuis_son_decalage(project: Path, claude_home: Path) -> None:
    session = "7f7f7f7f-6e6e6e6e-5d5d5d5d-4c4c4c4c-3b3b3b3b3b3b"
    path = _journal_dir(claude_home, project) / f"{session}.jsonl"
    _write(path, [_assistant(session, "m1", "claude-sonnet-5-5")])
    fleet.fleet_live_view(project, now=NOW)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_assistant(session, "m2", "claude-opus-5-5")) + "\n")
    view = fleet.fleet_live_view(project, now=NOW)

    changes = view["sessions"][0]["model_changes"]
    assert [change["kind"] for change in changes] == ["upgrade"]


# ── 5 bis. Mémoire bornée (audit 2026-11) ───────────────────────────────────


def test_l_etat_par_session_ne_garde_pas_un_appel_par_message(
    project: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Défaut empêché (audit mémoire 2026-11) : ``_FileState.calls`` gardait un objet par ``message.id`` de tout
    le journal — 115 à 195 Mo par session sur le poste. Les compteurs restent agrégés par modèle, donc le coût
    affiché ne change pas ; seuls les identifiants au-delà de :data:`fleet.MAX_CALLS` sont oubliés."""
    monkeypatch.setattr(fleet, "MAX_CALLS", 50)
    session = "10000000-0000-0000-0000-000000000001"
    path = _journal_dir(claude_home, project) / f"{session}.jsonl"
    _write(path, [_assistant(session, f"m{index}", "claude-sonnet-5-5") for index in range(200)])

    state = fleet._state_for(path)

    assert len(state.calls) <= 50
    usage = state.usage_by_model["claude-sonnet-5-5"]
    assert usage.input_tokens == 200 * 10 and usage.output_tokens == 200 * 5, "l'usage total n'est pas perdu"


def test_un_gros_journal_est_lu_par_blocs(project: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Défaut empêché (audit mémoire 2026-11) : ``handle.read()`` chargeait le journal entier — 195 Mo, plus le
    JSON parsé. La lecture par blocs borne le pic d'allocation bien sous la taille du fichier."""
    monkeypatch.setattr(fleet, "READ_CHUNK_BYTES", 64 * 1024)
    monkeypatch.setattr(fleet, "MAX_CALLS", 100)
    session = "20000000-0000-0000-0000-000000000002"
    path = _journal_dir(claude_home, project) / f"{session}.jsonl"
    _write(path, [_assistant(session, f"m{index}", "claude-sonnet-5-5") for index in range(16000)])
    size = path.stat().st_size
    assert size > 4 * 1024 * 1024, "le journal de test doit dépasser largement un bloc"

    tracemalloc.start()
    try:
        fleet._state_for(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < size / 2, f"pic {peak / 1e6:.1f} Mo pour un journal de {size / 1e6:.1f} Mo : lu d'un bloc ?"


def test_le_nombre_de_journaux_gardes_est_borne(
    project: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Défaut empêché : un état par journal lu, pour toujours — un serveur ouvert des jours accumulait les
    sessions terminées. Le journal le plus anciennement lu sort ; relu, il repart de zéro sans double compter."""
    monkeypatch.setattr(fleet, "MAX_FILE_STATES", 2)
    directory = _journal_dir(claude_home, project)
    paths = []
    for index in range(3):
        session = f"40000000-0000-0000-0000-00000000000{index}"
        path = directory / f"{session}.jsonl"
        _write(path, [_assistant(session, f"m{index}", "claude-sonnet-5-5")])
        paths.append(path)
        fleet._state_for(path)

    assert len(fleet._FILES) == 2
    assert str(paths[0]) not in fleet._FILES


def test_une_ligne_a_cheval_sur_deux_blocs_est_repliee_une_seule_fois(
    project: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Défaut empêché : la lecture par blocs ne doit ni perdre ni compter deux fois une ligne coupée en deux."""
    monkeypatch.setattr(fleet, "READ_CHUNK_BYTES", 32)
    session = "30000000-0000-0000-0000-000000000003"
    path = _journal_dir(claude_home, project) / f"{session}.jsonl"
    _write(path, [_assistant(session, "m1", "claude-sonnet-5-5")])

    state = fleet._state_for(path)
    assert len(state.calls) == 1
    assert state.usage_by_model["claude-sonnet-5-5"].input_tokens == 10

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_assistant(session, "m2", "claude-sonnet-5-5")) + "\n")
    state = fleet._state_for(path)
    assert len(state.calls) == 2
    assert state.usage_by_model["claude-sonnet-5-5"].input_tokens == 20


def test_les_outils_ouverts_et_vus_sont_bornes_par_session(
    project: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un fil de session ne retient pas un objet par appel d'outil de tout le journal : au-delà des bornes,
    l'identifiant le plus ancien sort — un même identifiant ne réapparaît qu'à quelques lignes d'écart."""
    monkeypatch.setattr(fleet, "_MAX_OPEN_TOOLS", 2)
    monkeypatch.setattr(fleet, "_MAX_SEEN_TOOLS", 2)
    session = "50000000-0000-0000-0000-000000000005"
    path = _journal_dir(claude_home, project) / f"{session}.jsonl"
    tools = tuple(_tool("Read", f"tool-{index}", {"file_path": "/x"}) for index in range(3))
    _write(path, [_assistant(session, "m1", "claude-sonnet-5-5", tools=tools)])

    state = fleet._state_for(path)

    assert list(state.open_tools) == ["tool-1", "tool-2"]
    assert list(state.seen_tools) == ["tool-1", "tool-2"]
    assert [tool.id for tool in state.tools] == ["tool-0", "tool-1", "tool-2"], "le fil garde les trois appels"


def test_une_ligne_demesuree_sans_saut_est_sautee_par_blocs(
    project: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une ligne plus longue que :data:`fleet._MAX_LINE_BYTES` est abandonnée sans jamais tenir la mémoire :
    ses blocs déjà lus sont comptés puis oubliés, en attendant le prochain saut."""
    monkeypatch.setattr(fleet, "READ_CHUNK_BYTES", 64)
    monkeypatch.setattr(fleet, "_MAX_LINE_BYTES", 16)
    session = "60000000-0000-0000-0000-000000000006"
    path = _journal_dir(claude_home, project) / f"{session}.jsonl"
    path.write_bytes(b"x" * 100)

    state = fleet._state_for(path)

    assert state.offset == 100, "les octets de la ligne démesurée sont sautés"
    assert fleet._STATS["lines"] == 0


def test_un_reliquat_demesure_apres_un_saut_est_saute(
    project: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Après une ligne complète, un reliquat sans saut plus gros que :data:`fleet._MAX_LINE_BYTES` est oublié :
    la ligne suivante, quand elle arrivera, ne sera pas repliée sur un tampon démesuré."""
    monkeypatch.setattr(fleet, "READ_CHUNK_BYTES", 4096)
    monkeypatch.setattr(fleet, "_MAX_LINE_BYTES", 16)
    session = "70000000-0000-0000-0000-000000000007"
    path = _journal_dir(claude_home, project) / f"{session}.jsonl"
    line = json.dumps(_assistant(session, "m1", "claude-sonnet-5-5")).encode("utf-8")
    data = line + b"\n" + b"y" * 5000
    path.write_bytes(data)

    state = fleet._state_for(path)

    assert state.offset == len(data)
    assert len(state.calls) == 1, "la ligne complète avant le reliquat a bien été repliée"


def test_une_ligne_vide_entre_deux_appels_est_ignoree(
    project: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un saut isolé (ligne vide) ne casse pas la lecture : il est ignoré et les deux appels voisins, dans le
    même bloc, sont tous deux repliés."""
    session = "80000000-0000-0000-0000-000000000008"
    path = _journal_dir(claude_home, project) / f"{session}.jsonl"
    first = json.dumps(_assistant(session, "m1", "claude-sonnet-5-5"))
    second = json.dumps(_assistant(session, "m2", "claude-sonnet-5-5"))
    path.write_text(f"{first}\n\n{second}\n", encoding="utf-8")

    state = fleet._state_for(path)

    assert len(state.calls) == 2
    assert state.usage_by_model["claude-sonnet-5-5"].input_tokens == 20


# ── 6. Fenêtre et portée ────────────────────────────────────────────────────


def test_project_etoile_couvre_tous_les_projets_enroles(project: Path, tmp_path: Path, claude_home: Path) -> None:
    second = tmp_path / "second"
    second.mkdir()
    reg.save_registry(
        [
            {"slug": "projet", "name": "Projet", "path": str(project.resolve())},
            {"slug": "second", "name": "Second", "path": str(second.resolve())},
        ]
    )
    first_session = "10000000-0000-0000-0000-000000000001"
    second_session = "20000000-0000-0000-0000-000000000002"
    _write(
        _journal_dir(claude_home, project) / f"{first_session}.jsonl",
        [_assistant(first_session, "m1", "claude-sonnet-5-5")],
    )
    _write(
        _journal_dir(claude_home, second) / f"{second_session}.jsonl",
        [_assistant(second_session, "m1", "deepseek-flash")],
    )

    view = fleet.fleet_live_view(project, project="*", now=NOW)

    assert set(view["projects"]) == {"projet", "second"}
    assert {session["id"] for session in view["sessions"]} == {first_session, second_session}
    # Le second projet n'a pas de registre : le préfixe publié par le
    # fournisseur suffit, ``deepseek-flash`` n'est plus « inconnu ».
    assert view["totals"]["by_provider"] == {"anthropic": 1, "deepseek": 1}


def test_la_fenetre_est_bornee_et_les_journaux_trop_vieux_sont_ignores(project: Path, claude_home: Path) -> None:
    old = _journal_dir(claude_home, project) / "30000000-0000-0000-0000-000000000003.jsonl"
    _write(old, [_assistant("30000000-0000-0000-0000-000000000003", "m1", "claude-sonnet-5-5")])
    ancien = (NOW - timedelta(hours=25)).timestamp()
    os.utime(old, (ancien, ancien))

    view = fleet.fleet_live_view(project, window_minutes=5000, now=NOW)

    assert view["window_minutes"] == fleet.MAX_WINDOW_MINUTES
    assert view["sessions"] == []
    with pytest.raises(ValueError):
        fleet.fleet_live_view(project, window_minutes=0, now=NOW)


def test_read_root_n_accepte_etoile_que_pour_la_flotte(project: Path) -> None:
    """``project=*`` n'est pas un slug : l'hôte ne le résout que pour la Flotte en direct."""
    from grimoire.cli.cockpit_write_target import read_root

    assert read_root("/api/workspace/fleet/live?project=*") == project.resolve()
    assert read_root("/api/workspace/files?project=*") is None


def test_la_route_sert_la_fenetre_par_defaut_et_refuse_une_fenetre_invalide(
    project: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grimoire.tools import workspace_routes

    monkeypatch.setattr(workspace_routes, "_host_entries", lambda: None)
    assert fleet.fleet_live_route(project, {})["window_minutes"] == fleet.DEFAULT_WINDOW_MINUTES
    with pytest.raises(ValueError):
        fleet.fleet_live_route(project, {"window": ["abc"]})
    with pytest.raises(ValueError):
        fleet.fleet_live_route(project, {"window": ["0"]})
