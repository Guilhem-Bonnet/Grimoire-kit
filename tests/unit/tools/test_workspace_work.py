"""Qui travaille, avec quoi, à quel coût — P75, GAO-p75-qui-trav-001.

Chaque chiffre affiché par la carte a sa source nommée ici :

- tokens/coût : les traces repliées (``task_usage``), jamais un second agrégateur ;
- modèle réel, effort, skills : le journal Claude Code, lu incrémentalement ;
- taux USD→CAD : ``project-context.yaml`` (section ``cockpit``), défaut 1,40
  dit comme défaut — et l'écriture round-trip garde les commentaires du fichier.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from grimoire.tools import workspace_work as ww

SESSION = "41c89d23-db0e-4d2a-8888-000000000001"


def _assistant_line(
    message_id: str,
    *,
    model: str = "claude-sonnet-5-5",
    effort: str | None = "high",
    tokens_in: int = 1000,
    tokens_out: int = 500,
    skills: tuple[str, ...] = (),
    timestamp: str = "2026-10-27T10:00:00Z",
) -> str:
    content: list[dict[str, Any]] = [{"type": "tool_use", "name": "Skill", "input": {"skill": s}} for s in skills]
    line: dict[str, Any] = {
        "type": "assistant",
        "timestamp": timestamp,
        "sessionId": SESSION,
        "message": {
            "id": message_id,
            "model": model,
            "usage": {
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            "content": content,
        },
    }
    if effort is not None:
        line["effort"] = effort
    return json.dumps(line)


@pytest.fixture
def claude_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    ww._TRANSCRIPT_CACHE.clear()
    return home


def _transcript(claude_home: Path, project_root: Path, session_id: str = SESSION) -> Path:
    import re

    slug = re.sub(r"[^A-Za-z0-9]", "-", str(project_root.resolve()))
    path = claude_home / "projects" / slug / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# ── Palier de modèle ─────────────────────────────────────────────────────────


def test_model_tier_reconnait_les_trois_paliers_et_rien_d_autre() -> None:
    assert ww.model_tier("claude-haiku-4-5") == "haiku"
    assert ww.model_tier("claude-sonnet-5-5") == "sonnet"
    assert ww.model_tier("claude-OPUS-5-5") == "opus"
    assert ww.model_tier("claude-fable-5") is None
    assert ww.model_tier("") is None


# ── Résolution du journal ────────────────────────────────────────────────────


def test_claude_projects_dir_prend_l_env_sinon_le_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "ailleurs"))
    assert ww.claude_projects_dir() == tmp_path / "ailleurs" / "projects"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert ww.claude_projects_dir(home=tmp_path) == tmp_path / ".claude" / "projects"


def test_transcript_path_refuse_un_identifiant_dangereux_et_un_journal_absent(
    tmp_path: Path, claude_home: Path
) -> None:
    root = tmp_path / "projet"
    root.mkdir()
    assert ww.transcript_path(root, "../evasion") is None
    assert ww.transcript_path(root, "") is None
    assert ww.transcript_path(root, SESSION) is None  # aucun fichier
    path = _transcript(claude_home, root)
    path.write_text("", encoding="utf-8")
    assert ww.transcript_path(root, SESSION) == path


# ── Lecture incrémentale du journal ──────────────────────────────────────────


def test_read_transcript_work_lit_modele_effort_et_skills(tmp_path: Path, claude_home: Path) -> None:
    root = tmp_path / "projet"
    root.mkdir()
    path = _transcript(claude_home, root)
    path.write_text(
        _assistant_line("m1", model="claude-haiku-4-5", effort="medium", skills=("grimoire-evidence",))
        + "\n"
        + _assistant_line("m2", model="claude-sonnet-5-5", effort="high", skills=("code-review", "grimoire-evidence"))
        + "\n",
        encoding="utf-8",
    )
    work = ww.read_transcript_work(path)
    assert work["source"] == "transcript"
    assert work["model"] == "claude-sonnet-5-5"
    assert work["tier"] == "sonnet"
    assert work["effort"] == "high"
    # Dédoublonné, dernière invocation en queue.
    assert work["skills"] == ["code-review", "grimoire-evidence"]
    # Fichier entièrement lu et inchangé : la relecture ne rouvre rien et rend la même chose.
    assert ww.read_transcript_work(path) == work


def test_read_transcript_work_est_incremental_et_n_avale_pas_une_ligne_tronquee(
    tmp_path: Path, claude_home: Path
) -> None:
    root = tmp_path / "projet"
    root.mkdir()
    path = _transcript(claude_home, root)
    full = _assistant_line("m1", model="claude-opus-5-5", effort="low")
    partial = _assistant_line("m2", model="claude-haiku-4-5", effort="max")
    # Une ligne blanche au milieu n'est pas une ligne d'appel.
    path.write_text(full + "\n\n" + partial[: len(partial) // 2], encoding="utf-8")
    first = ww.read_transcript_work(path)
    assert first["model"] == "claude-opus-5-5"
    assert first["effort"] == "low"
    # Fichier inchangé : rien n'est relu, le résultat est le même.
    assert ww.read_transcript_work(path) == first
    # Des octets de plus mais toujours aucune fin de ligne : rien d'affirmé de plus.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(partial[len(partial) // 2 : len(partial) // 2 + 3])
    assert ww.read_transcript_work(path) == first
    # La ligne tronquée attend le prochain passage : rien d'inventé.
    path.write_text(full + "\n\n" + partial + "\n", encoding="utf-8")
    second = ww.read_transcript_work(path)
    assert second["model"] == "claude-haiku-4-5"
    assert second["effort"] == "max"


def test_read_transcript_work_repart_de_zero_sur_un_fichier_raccourci(tmp_path: Path, claude_home: Path) -> None:
    root = tmp_path / "projet"
    root.mkdir()
    path = _transcript(claude_home, root)
    path.write_text(_assistant_line("m1") + "\n" + _assistant_line("m2") + "\n", encoding="utf-8")
    assert ww.read_transcript_work(path)["model"] == "claude-sonnet-5-5"
    path.write_text(_assistant_line("m3", model="claude-haiku-4-5", effort="low") + "\n", encoding="utf-8")
    assert ww.read_transcript_work(path)["model"] == "claude-haiku-4-5"


def test_read_transcript_work_ecarte_l_illisible_le_synthetique_et_le_non_assistant(
    tmp_path: Path, claude_home: Path
) -> None:
    root = tmp_path / "projet"
    root.mkdir()
    path = _transcript(claude_home, root)
    lines = [
        "pas du json",
        json.dumps({"type": "user", "effort": "max"}),
        json.dumps({"type": "assistant", "message": {"model": "<synthetic>"}}),
        json.dumps({"type": "assistant", "message": "pas une table"}),
        json.dumps({"type": "assistant", "effort": "", "message": {"model": "", "content": "pas une liste"}}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-5-5",
                    "content": [
                        "pas une table",
                        {"type": "tool_use", "name": "Bash", "input": {"skill": "non"}},
                        {"type": "tool_use", "name": "Skill", "input": "pas une table"},
                        {"type": "tool_use", "name": "Skill", "input": {"skill": ""}},
                    ],
                },
            }
        ),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    work = ww.read_transcript_work(path)
    assert work["model"] == "claude-sonnet-5-5"
    assert work["effort"] is None
    assert work["skills"] == []


def test_la_liste_de_skills_reste_bornee(tmp_path: Path, claude_home: Path) -> None:
    root = tmp_path / "projet"
    root.mkdir()
    path = _transcript(claude_home, root)
    skills = tuple(f"skill-{i}" for i in range(40))
    path.write_text(_assistant_line("m1", skills=skills) + "\n", encoding="utf-8")
    work = ww.read_transcript_work(path)
    assert len(work["skills"]) == 30
    assert work["skills"][-1] == "skill-39"


# ── session_work : honnête quand il ne sait pas ──────────────────────────────


def test_session_work_dit_no_transcript_pour_un_hote_sans_journal_connu(tmp_path: Path, claude_home: Path) -> None:
    root = tmp_path / "projet"
    root.mkdir()
    assert ww.session_work(root, SESSION, "host-cursor")["source"] == "no_transcript"
    assert ww.session_work(root, SESSION, "host-claude-code-cli")["source"] == "no_transcript"  # journal absent


def test_session_work_lit_le_journal_claude_code_et_avale_un_journal_illisible(
    tmp_path: Path, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "projet"
    root.mkdir()
    path = _transcript(claude_home, root)
    path.write_text(_assistant_line("m1", skills=("grimoire-proof",)) + "\n", encoding="utf-8")
    work = ww.session_work(root, SESSION, "claude-code-cli")  # alias non canonique accepté
    assert work["source"] == "transcript"
    assert work["skills"] == ["grimoire-proof"]

    def _raise(_: Path) -> dict[str, Any]:
        raise OSError("disque parti")

    monkeypatch.setattr(ww, "read_transcript_work", _raise)
    assert ww.session_work(root, SESSION, "host-claude-code-cli")["source"] == "no_transcript"


# ── Usage : task_usage sur les traces repliées, rien d'autre ─────────────────


def _fold(root: Path, transcript: Path, task_id: str) -> None:
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import TraceLedger
    from grimoire.traces.transcript_usage import fold_claude_transcript

    fold_claude_transcript(TraceLedger(root / TRACES_DIR), transcript, task_id=task_id)


def _task(task_id: str, status: Any = None, host_id: str = "host-claude-code-cli") -> SimpleNamespace:
    claim = SimpleNamespace(host_id=host_id) if host_id else None
    return SimpleNamespace(id=task_id, status=status, claim=claim)


def test_usage_of_sans_trace_dit_none_ou_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "projet"
    root.mkdir()
    # Hôte au journal vérifié, rien replié : none.
    assert ww.usage_of(root, _task("T-1"))["usage_source"] == "none"
    # Sans claim : la disponibilité n'est pas affirmée.
    assert ww.usage_of(root, _task("T-1", host_id=""))["usage_source"] == "none"


def test_usage_of_lit_les_traces_repliees_du_ledger(tmp_path: Path, claude_home: Path) -> None:
    root = tmp_path / "projet"
    root.mkdir()
    transcript = _transcript(claude_home, root)
    transcript.write_text(_assistant_line("m1", tokens_in=1000, tokens_out=500) + "\n", encoding="utf-8")
    _fold(root, transcript, "T-1")
    usage = ww.usage_of(root, _task("T-1"))
    assert usage["usage_source"] == "claude-code-transcript"
    assert usage["total_tokens"] == 1500
    assert usage["cost_usd"] == pytest.approx(0.007)  # sonnet-5-5 : 2 $/MTok in, 10 $/MTok out
    assert usage["cost_status"] == "exact"


def test_traces_by_task_groupe_en_un_passage(tmp_path: Path, claude_home: Path) -> None:
    root = tmp_path / "projet"
    root.mkdir()
    assert ww._traces_by_task(root) == {}
    transcript = _transcript(claude_home, root)
    transcript.write_text(_assistant_line("m1") + "\n", encoding="utf-8")
    _fold(root, transcript, "T-1")
    # Une trace sans tâche (span de flow) n'est groupée nulle part.
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import TraceLedger
    from grimoire.traces.schemas import TraceOutcome, TraceRecord

    TraceLedger(root / TRACES_DIR).append_new(
        [
            TraceRecord(
                id="TRC-sans-tache",
                run_id="r",
                workflow_instance_id="",
                mission_id="",
                task_id="",
                recipe_id="x",
                outcome=TraceOutcome.SUCCESS,
                started_at="2026-10-27T10:00:00Z",
            )
        ]
    )
    grouped = ww._traces_by_task(root)
    assert set(grouped) == {"T-1"}
    assert len(grouped["T-1"]) == 1


# ── Conversion CAD et options du projet ──────────────────────────────────────


def test_cad_of_convertit_sans_jamais_inventer() -> None:
    assert ww.cad_of(None, 1.40) is None
    assert ww.cad_of(7.0, 1.40) == pytest.approx(9.80)
    assert ww.cad_of(0.007, 2.0) == pytest.approx(0.01)


def test_project_options_rend_le_defaut_dit_comme_defaut(tmp_path: Path) -> None:
    options = ww.project_options(tmp_path)  # aucun project-context.yaml
    assert options == {"usd_cad": 1.40, "usd_cad_source": "default", "usd_cad_default": 1.40}


@pytest.mark.parametrize("declared", ["1.5", True, -2, 0, 1000])
def test_project_options_refuse_un_taux_illisible_ou_hors_borne(tmp_path: Path, declared: Any) -> None:
    (tmp_path / "project-context.yaml").write_text(
        json.dumps({"project": {"name": "x"}, "cockpit": {"usd_cad": declared}}), encoding="utf-8"
    )
    assert ww.project_options(tmp_path)["usd_cad_source"] == "default"


def test_write_usd_cad_pose_le_taux_et_garde_les_commentaires(tmp_path: Path) -> None:
    path = tmp_path / "project-context.yaml"
    path.write_text("# commentaire à garder\nproject:\n  name: x\n", encoding="utf-8")
    result = ww.write_usd_cad(tmp_path, 1.3777)
    assert result["usd_cad"] == pytest.approx(1.3777)
    assert result["usd_cad_source"] == "project"
    text = path.read_text(encoding="utf-8")
    assert "# commentaire à garder" in text
    assert "usd_cad" in text
    # Réécriture sur une section existante : pas de doublon.
    ww.write_usd_cad(tmp_path, 2.0)
    assert ww.project_options(tmp_path)["usd_cad"] == pytest.approx(2.0)
    assert path.read_text(encoding="utf-8").count("cockpit:") == 1


def test_write_usd_cad_refuse_hors_borne_fichier_absent_et_yaml_non_table(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalide"):
        ww.write_usd_cad(tmp_path, 0)
    with pytest.raises(ValueError, match="invalide"):
        ww.write_usd_cad(tmp_path, 1000.0)
    with pytest.raises(FileNotFoundError):
        ww.write_usd_cad(tmp_path, 1.40)
    (tmp_path / "project-context.yaml").write_text("- une liste\n", encoding="utf-8")
    with pytest.raises(ValueError, match="table YAML"):
        ww.write_usd_cad(tmp_path, 1.40)


# ── annotate_work : le payload des cartes ────────────────────────────────────


def test_annotate_work_pose_usage_cad_et_session_sur_une_tache_tenue(tmp_path: Path, claude_home: Path) -> None:
    from grimoire.missions.schemas import TaskState

    root = tmp_path / "projet"
    root.mkdir()
    transcript = _transcript(claude_home, root)
    transcript.write_text(
        _assistant_line("m1", tokens_in=3_000_000, tokens_out=100_000, skills=("grimoire-gate",)) + "\n",
        encoding="utf-8",
    )
    _fold(root, transcript, "T-1")
    tasks = [_task("T-1", status=TaskState.RUNNING), _task("T-2", status=TaskState.CLOSED)]
    cards: list[dict[str, Any]] = [
        {"id": "T-1", "session_id": SESSION, "host": "host-claude-code-cli"},
        {"id": "T-2", "session_id": "", "host": ""},
    ]
    ww.annotate_work(root, tasks, cards)
    held = cards[0]["work"]
    assert held["usage"]["total_tokens"] == 3_100_000
    assert held["usage"]["cost_usd"] == pytest.approx(7.0)
    assert held["cost_cad"] == pytest.approx(9.80)  # 7 USD × 1,40 (défaut)
    assert held["usd_cad_source"] == "default"
    assert held["session"]["source"] == "transcript"
    assert held["session"]["tier"] == "sonnet"
    assert held["session"]["effort"] == "high"
    assert held["session"]["skills"] == ["grimoire-gate"]
    done = cards[1]["work"]
    assert done["session"] is None
    assert done["usage"]["usage_source"] == "none"
    assert done["cost_cad"] is None


# ── Routes : GET/POST /api/workspace/options ─────────────────────────────────


def test_les_routes_options_lisent_et_ecrivent_le_taux(tmp_path: Path) -> None:
    from grimoire.tools.workspace_routes import GET_ROUTES, POST_ROUTES, PREFIX

    (tmp_path / "project-context.yaml").write_text("project:\n  name: x\n", encoding="utf-8")
    assert GET_ROUTES[f"{PREFIX}options"](tmp_path, {})["usd_cad_source"] == "default"
    written = POST_ROUTES[f"{PREFIX}options"](tmp_path, {"usd_cad": 1.25})
    assert written["usd_cad"] == pytest.approx(1.25)
    assert GET_ROUTES[f"{PREFIX}options"](tmp_path, {})["usd_cad_source"] == "project"
    with pytest.raises(ValueError, match="nombre"):
        POST_ROUTES[f"{PREFIX}options"](tmp_path, {"usd_cad": "1.25"})
    with pytest.raises(ValueError, match="nombre"):
        POST_ROUTES[f"{PREFIX}options"](tmp_path, {"usd_cad": True})
