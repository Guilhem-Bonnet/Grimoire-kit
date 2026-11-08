"""Le coût du payload de la Flotte, mesuré et borné — issue #1010, GAO-cockpit-flot-001.

Mesure de départ (2026-11-07, 3 projets jetables ``grimoire init``) :

- ``GET /api/fleet`` ne lit **aucun** journal de session : il coûte 29 ms
  chaud / 36 ms à froid côté serveur, et le payload ``work`` (P75) n'y est pas ;
- le payload ``work`` vit dans ``GET /api/workspace/tasks``. La coque l'appelle
  dans ``buildPalette()`` **avant** de monter la Flotte : sur un projet servi
  qui porte des tâches tenues, sa lecture froide valait 1 915 ms pour cinq
  journaux de 20 Mo (100 Mo), parce que ``read_transcript_work`` relisait
  chaque journal entier ;
- après borne, la même lecture froide vaut 188 ms, indépendamment de la
  taille des journaux.

Ces tests fixent les deux moitiés du correctif, sans horloge pour la partie
déterministe (lignes repliées, champs servis) :

- une passe ne lit jamais plus de :data:`grimoire.tools.workspace_work.MAX_READ_BYTES` ;
- le board annoté reste borné sur un journal arbitrairement gros ;
- la route ``annotations=0`` — celle que la palette de la coque demande —
  ne touche jamais un journal de session.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grimoire.missions.schemas import TaskClaim, TaskState
from grimoire.missions.service import TaskService
from grimoire.tools import workspace_work as ww

SESSION_PREFIX = "41c89d23-db0e-4d2a-8888-0000000000"


def _session(index: int) -> str:
    return f"{SESSION_PREFIX}{index:02d}"


def _assistant_line(
    message_id: str,
    *,
    session: str,
    model: str = "claude-sonnet-5-5",
    effort: str = "high",
    skills: tuple[str, ...] = (),
) -> str:
    """Une ligne de journal Claude Code, du format que ``_fold_line`` replie."""
    return json.dumps({
        "type": "assistant",
        "timestamp": "2026-11-07T10:00:00Z",
        "effort": effort,
        "sessionId": session,
        "message": {
            "id": message_id,
            "model": model,
            "content": [{"type": "tool_use", "name": "Skill", "input": {"skill": s}} for s in skills],
        },
    })


@pytest.fixture(autouse=True)
def _journal_cache_vide() -> None:
    """Chaque test part d'un cache de journaux froid, comme un serveur qui démarre."""
    ww._TRANSCRIPT_CACHE.clear()


@pytest.fixture
def claude_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


def _transcript(claude_home: Path, project_root: Path, session_id: str) -> Path:
    import re

    slug = re.sub(r"[^A-Za-z0-9]", "-", str(project_root.resolve()))
    path = claude_home / "projects" / slug / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_journal(path: Path, count: int, *, session: str, **kwargs: Any) -> int:
    with path.open("w", encoding="utf-8") as handle:
        for index in range(count):
            handle.write(_assistant_line(f"m{index}", session=session, **kwargs) + "\n")
    return path.stat().st_size


def _held_task(root: Path, index: int, *, session: str) -> str:
    """Une tâche du ledger, tenue par une session Claude Code attachée."""
    service = TaskService(root)
    task_id = service.add(f"Tâche tenue {index}", ("un critère",), owner="guilhem", actor="t").task.id
    for state in (TaskState.READY, TaskState.CLAIMED, TaskState.RUNNING):
        claim = TaskClaim("kit-dev", "host-claude-code-cli") if state is TaskState.CLAIMED else None
        service.ledger.transition_task(task_id, state, actor_id="kit-dev", claim=claim)
    service.attach_session(task_id, session, session_host="claude-code-cli", actor="kit-dev")
    runs = root / "_grimoire-output" / ".runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"session-{session}.json").write_text(
        json.dumps({
            "schema_version": 1,
            "session_id": session,
            "task_id": task_id,
            "host": "host-claude-code-cli",
            "rules": {},
        }),
        encoding="utf-8",
    )
    return task_id


# ── 1. La lecture d'un journal est bornée, quel que soit son poids ──────────


def test_une_lecture_froide_ne_lit_jamais_plus_de_la_borne(
    tmp_path: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un journal plus gros que la borne ne rend que sa queue : le modèle,
    l'effort et les skills *récents* sont dans les derniers octets ; la tête
    n'est jamais repliée (elle est mesurée, pas supposée)."""
    root = tmp_path / "projet"
    root.mkdir()
    path = _transcript(claude_home, root, _session(1))
    # Tête (ancienne) puis queue (actuelle), 8 Mo au total : la borne est franchie.
    head_lines = 12_000
    tail_lines = 12_000
    with path.open("w", encoding="utf-8") as handle:
        for index in range(head_lines):
            handle.write(_assistant_line(
                f"h{index}", session=_session(1), model="claude-opus-4-1", effort="low",
                skills=("vieux-skill",),
            ) + "\n")
        for index in range(tail_lines):
            handle.write(_assistant_line(
                f"t{index}", session=_session(1), model="claude-sonnet-5-5", effort="high",
                skills=("skill-recent",),
            ) + "\n")
    size = path.stat().st_size
    assert size > 4 * ww.MAX_READ_BYTES, f"journal trop petit pour prouver la borne : {size}"

    repliees = 0
    original = ww._fold_line

    def _compte(state: Any, raw: bytes) -> None:
        nonlocal repliees
        repliees += 1
        original(state, raw)

    monkeypatch.setattr(ww, "_fold_line", _compte)
    work = ww.read_transcript_work(path)

    state = ww._TRANSCRIPT_CACHE[str(path.resolve())]
    assert size - state.offset <= ww.MAX_READ_BYTES, (
        f"la passe a lu {size - state.offset} octets, borne {ww.MAX_READ_BYTES}"
    )
    assert repliees <= ww.MAX_READ_BYTES // 100 + 2, f"{repliees} lignes repliées : la borne n'a pas tenu"
    # Le journal dit son état récent, jamais son histoire : la queue gagne.
    assert work["model"] == "claude-sonnet-5-5"
    assert work["effort"] == "high"
    assert work["skills"] == ["skill-recent"]


def test_un_arriere_accumule_est_saute_et_non_rejoue(
    tmp_path: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Après une longue interruption, un serveur qui revient ne rejoue pas tout
    l'arriéré : il repart de la queue, comme au premier regard."""
    root = tmp_path / "projet"
    root.mkdir()
    path = _transcript(claude_home, root, _session(2))
    _write_journal(path, 6_000, session=_session(2), model="claude-haiku-4-5", effort="low")
    ww.read_transcript_work(path)  # premier regard : la file est ouverte

    # L'arriéré grossit bien au-delà de la borne avant la passe suivante.
    _write_journal(path, 6_000, session=_session(2), model="claude-opus-5-5", effort="high")
    with path.open("a", encoding="utf-8") as handle:  # 6 000 lignes supplémentaires
        for index in range(6_000):
            handle.write(_assistant_line(
                f"fin{index}", session=_session(2), model="claude-opus-5-5", effort="high",
                skills=("skill-final",),
            ) + "\n")
    size = path.stat().st_size

    repliees = 0
    original = ww._fold_line

    def _compte(state: Any, raw: bytes) -> None:
        nonlocal repliees
        repliees += 1
        original(state, raw)

    monkeypatch.setattr(ww, "_fold_line", _compte)
    work = ww.read_transcript_work(path)

    state = ww._TRANSCRIPT_CACHE[str(path.resolve())]
    assert size - state.offset <= ww.MAX_READ_BYTES, "l'arriéré a été relu en entier"
    assert repliees <= ww.MAX_READ_BYTES // 100 + 2
    assert work["model"] == "claude-opus-5-5"
    assert work["skills"] == ["skill-final"]


def test_une_seule_ligne_ajoutee_n_en_rejoue_aucune_autre(tmp_path: Path, claude_home: Path) -> None:
    """Le pouls ordinaire reste incrémental : une ligne écrite, une ligne repliée."""
    root = tmp_path / "projet"
    root.mkdir()
    path = _transcript(claude_home, root, _session(3))
    _write_journal(path, 50, session=_session(3), skills=("skill-initial",))
    first = ww.read_transcript_work(path)
    assert first["skills"] == ["skill-initial"]

    with path.open("a", encoding="utf-8") as handle:
        handle.write(_assistant_line("suite", session=_session(3), skills=("skill-ajoute",)) + "\n")
    second = ww.read_transcript_work(path)
    assert second["skills"] == ["skill-initial", "skill-ajoute"]
    assert second["model"] == "claude-sonnet-5-5"


# ── 2. Le board annoté (Exécuter) reste borné ───────────────────────────────


def test_le_board_annote_ne_paie_pas_la_taille_des_journaux(
    tmp_path: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cinq tâches tenues, cinq journaux de 4 Mio : le premier ``tasks_view``
    annoté ne doit replier qu'une queue bornée par journal — mesuré à 1,9 s
    pour 100 Mo avant la borne, 188 ms après."""
    from grimoire.tools.workspace_api import tasks_view

    root = tmp_path / "projet"
    root.mkdir()
    sessions = [_session(10 + index) for index in range(5)]
    for index, session in enumerate(sessions):
        _held_task(root, index, session=session)
        _write_journal(_transcript(claude_home, root, session), 20_000, session=session, model="claude-sonnet-5-5")

    repliees = 0
    original = ww._fold_line

    def _compte(state: Any, raw: bytes) -> None:
        nonlocal repliees
        repliees += 1
        original(state, raw)

    monkeypatch.setattr(ww, "_fold_line", _compte)
    payload = tasks_view(root)

    assert payload["count"] == 5
    assert repliees <= 5 * (ww.MAX_READ_BYTES // 100 + 2), (
        f"{repliees} lignes repliées pour cinq journaux : la borne par journal n'a pas tenu"
    )
    for card in payload["tasks"]:
        work = card["work"]
        assert work["session"]["source"] == "transcript"


# ── 3. La palette de la coque ne touche jamais un journal ───────────────────


def test_la_route_annotations_0_ne_lit_aucun_journal(
    tmp_path: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``?annotations=0`` (la demande de ``buildPalette``) rend la liste sans
    vivacité, sans coût et sans payload ``work`` : le chargement de la Flotte,
    qui l'attend avant de monter, ne paie plus aucune lecture de journal."""
    from grimoire.tools.workspace_routes import workspace_get

    root = tmp_path / "projet"
    root.mkdir()
    session = _session(20)
    _held_task(root, 0, session=session)
    _write_journal(_transcript(claude_home, root, session), 10_000, session=session)

    def _interdit(_: Path) -> dict[str, Any]:
        raise AssertionError("annotations=0 a lu un journal de session")

    monkeypatch.setattr(ww, "read_transcript_work", _interdit)
    payload = workspace_get(root, "/api/workspace/tasks", {"annotations": ["0"]})

    assert payload["count"] == 1
    card = payload["tasks"][0]
    assert card["title"] == "Tâche tenue 0"
    assert card["id"]
    for champ in ("work", "cost", "session", "presence", "in_state_since", "feed_last"):
        assert champ not in card, f"annotations=0 a rendu le champ {champ}"


def test_la_meme_route_sans_annotations_0_sert_bien_le_board_annote(
    tmp_path: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le contrat inverse, sans quoi le test précédent prouverait un board muet
    plutôt qu'un board allégé : par défaut, Exécuter garde son payload ``work``."""
    from grimoire.tools.workspace_routes import workspace_get

    root = tmp_path / "projet"
    root.mkdir()
    session = _session(21)
    _held_task(root, 0, session=session)
    _write_journal(_transcript(claude_home, root, session), 10, session=session, skills=("skill-board",))

    payload = workspace_get(root, "/api/workspace/tasks", {})
    card = payload["tasks"][0]
    assert card["work"]["session"]["source"] == "transcript"
    assert card["work"]["session"]["skills"] == ["skill-board"]
    assert "cost" in card


# ── 4. La mesure : ``/api/fleet`` ne porte pas le payload work ──────────────


def test_fleet_status_ne_lit_aucun_journal_de_session(
    tmp_path: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La mesure qui oriente le correctif : ``GET /api/fleet`` ne touche pas
    aux journaux — c'est ``GET /api/workspace/tasks``, sur le chemin de montage
    de la Flotte, qui les lisait."""
    from grimoire.tools import project_registry
    from grimoire.tools.project_health import fleet_status

    root = tmp_path / "projet"
    root.mkdir()
    session = _session(22)
    _held_task(root, 0, session=session)
    _write_journal(_transcript(claude_home, root, session), 10_000, session=session)

    monkeypatch.setattr(
        project_registry,
        "load_registry",
        lambda: [{"slug": "projet", "path": str(root), "name": "Projet"}],
    )

    def _interdit(_: Path) -> dict[str, Any]:
        raise AssertionError("/api/fleet a lu un journal de session")

    monkeypatch.setattr(ww, "read_transcript_work", _interdit)
    payload = fleet_status()

    assert [projet["slug"] for projet in payload["projects"]] == ["projet"]
