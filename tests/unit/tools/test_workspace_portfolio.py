"""Portefeuille de tâches multi-projets — issue #638, lot C.

Le test de fait de l'issue, à la lettre : deux projets jetables gouvernés
enregistrés dans un registre isolé (``GRIMOIRE_COCKPIT_HOME``), une tâche
chacun dont une réclamée ; le portefeuille liste les deux avec leur projet ;
le filtre ``live`` ne garde que celle dont le journal de session est récent ;
un troisième projet au chemin absent apparaît avec son erreur, jamais tu.

Les chemins viennent toujours du registre, jamais d'un paramètre : un slug
inconnu est un ``FileNotFoundError``, pas un projet deviné.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.missions.ledger import MissionLedger
from grimoire.missions.schemas import TaskState
from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_portfolio as wp

STANDARD = Path("_grimoire/standard")
LEDGER = Path("_grimoire-runtime-output/ledger")
RUNS = Path("_grimoire-output/.runs")

#: Profil gouverné minimal : `proposed → ready` exige critères + owner, le
#: claim ne demande rien (le test porte sur le portefeuille, pas sur le
#: context bundle du gabarit `governed` complet).
GATES = """\
$schema: "grimoire-agentic-standard-evidence-gates/v1"
transitions:
  - id: proposed_to_ready
    from: proposed
    to: ready
    required_evidence: ["acceptance_criteria", "owner_or_agent_role"]
  - id: in_progress_to_review
    from: in_progress
    to: review
    required_evidence: ["evidence_pack"]
profile_strictness:
  governed: hard_fail
"""


def _governed_project(root: Path, title: str) -> str:
    """Un projet gouverné avec une tâche `ready` au Mission Ledger ; rend l'id."""
    (root / STANDARD).mkdir(parents=True)
    (root / STANDARD / "evidence-gates.yaml").write_text(GATES, encoding="utf-8")
    (root / STANDARD / "standard-profile.yaml").write_text("profile: governed\n", encoding="utf-8")
    ledger = MissionLedger(root / LEDGER)
    mission = ledger.create_mission(title="Travaux courants", origin="test", created_by="test")
    task = ledger.create_task(mission.id, title, acceptance=("un critère observable",), owner="winston")
    ledger.transition_task(task.id, TaskState.READY, actor_id="test")
    return task.id


def _journal(root: Path, session_id: str, *, task_id: str, updated_at: datetime) -> Path:
    path = root / RUNS / f"session-{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": session_id,
                "started_at": updated_at.isoformat(),
                "updated_at": updated_at.isoformat(),
                "task_id": task_id,
                "rules": {},
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))


@pytest.fixture
def fleet(tmp_path: Path) -> dict[str, object]:
    """Deux projets gouvernés (A réclamée par une session vivante, B libre avec
    une session éteinte) et un troisième au chemin absent.

    Titres distincts dès les douze premiers caractères : `create_task` dérive
    l'identifiant du titre (`GAO-<slug12>-<seq>`) par ledger, donc deux
    projets peuvent porter le MÊME identifiant — l'interface doit clé-er ses
    cartes par (projet, id), pas par id seul.
    """
    now = datetime.now(UTC)
    a = tmp_path / "projet-a"
    b = tmp_path / "projet-b"
    task_a = _governed_project(a, "Alpha : tâche réclamée")
    task_b = _governed_project(b, "Beta : tâche libre")
    MissionLedger(a / LEDGER).claim_task(task_a, "claude", "host-claude-code-cli")
    _journal(a, "sess-a-vivante", task_id=task_a, updated_at=now - timedelta(minutes=2))
    _journal(b, "sess-b-eteinte", task_id=task_b, updated_at=now - timedelta(hours=3))
    reg.save_registry(
        [
            {"slug": "projet-a", "name": "Alpha", "path": str(a)},
            {"slug": "projet-b", "name": "Beta", "path": str(b)},
            {"slug": "projet-c", "name": "Gamma", "path": str(tmp_path / "n-existe-pas")},
        ]
    )
    return {"a": a, "b": b, "task_a": task_a, "task_b": task_b, "now": now}


class TestPortfolioTasks:
    def test_liste_les_deux_projets_avec_leur_projet_et_nomme_le_troisieme(
        self, fleet: dict[str, object], tmp_path: Path
    ) -> None:
        view = wp.portfolio_tasks(tmp_path)

        assert view["schemaVersion"] == wp.SCHEMA_VERSION
        by_id = {t["id"]: t for t in view["tasks"]}
        assert by_id[fleet["task_a"]]["project"] == {"slug": "projet-a", "name": "Alpha"}
        assert by_id[fleet["task_b"]]["project"] == {"slug": "projet-b", "name": "Beta"}
        assert by_id[fleet["task_a"]]["status"] == "claimed"
        assert by_id[fleet["task_a"]]["board"] == "in_progress"
        assert by_id[fleet["task_a"]]["claim"]["actor_id"] == "claude"
        assert by_id[fleet["task_a"]]["priority"] == "medium", "dérivée du risk_profile standard"
        assert by_id[fleet["task_a"]]["updated_at"] >= by_id[fleet["task_a"]]["created_at"]

        by_slug = {p["slug"]: p for p in view["projects"]}
        assert by_slug["projet-a"]["state"] == "ok"
        assert by_slug["projet-b"]["state"] == "ok"
        assert by_slug["projet-c"]["state"] == "unreadable"
        assert "n-existe-pas" in by_slug["projet-c"]["reason"]
        assert view["summary"] == {"projects": 3, "readable": 2, "tasks": 2, "live": 1}

    def test_la_session_vivante_est_reconnue_par_le_journal_et_le_filtre_live_la_garde_seule(
        self, fleet: dict[str, object], tmp_path: Path
    ) -> None:
        view = wp.portfolio_tasks(tmp_path)
        by_id = {t["id"]: t for t in view["tasks"]}

        session_a = by_id[fleet["task_a"]]["session"]
        assert session_a["id"] == "sess-a-vivante"
        assert session_a["live"] is True
        assert session_a["host"] == "host-claude-code-cli"
        assert session_a["resume_command"] == "claude --resume sess-a-vivante"
        session_b = by_id[fleet["task_b"]]["session"]
        assert session_b["id"] == "sess-b-eteinte"
        assert session_b["live"] is False

        live_only = wp.portfolio_tasks(tmp_path, live=True)
        assert [t["id"] for t in live_only["tasks"]] == [fleet["task_a"]]

        # N paramétrable : à quatre heures, la session B redevient « vivante ».
        wide = wp.portfolio_tasks(tmp_path, live=True, live_minutes=240)
        assert {t["id"] for t in wide["tasks"]} == {fleet["task_a"], fleet["task_b"]}

    def test_les_filtres_etat_et_projet(self, fleet: dict[str, object], tmp_path: Path) -> None:
        assert [t["id"] for t in wp.portfolio_tasks(tmp_path, state="claimed")["tasks"]] == [fleet["task_a"]]
        # La colonne board est acceptée au même titre que l'état ledger.
        assert [t["id"] for t in wp.portfolio_tasks(tmp_path, state="in_progress")["tasks"]] == [fleet["task_a"]]
        assert [t["id"] for t in wp.portfolio_tasks(tmp_path, project="projet-b")["tasks"]] == [fleet["task_b"]]
        assert wp.portfolio_tasks(tmp_path, project="projet-fantome")["tasks"] == []

    def test_un_projet_sans_ledger_est_liste_avec_sa_raison(self, tmp_path: Path) -> None:
        bare = tmp_path / "nu"
        bare.mkdir()
        reg.save_registry([{"slug": "nu", "name": "Nu", "path": str(bare)}])

        view = wp.portfolio_tasks(tmp_path)

        assert view["tasks"] == []
        assert view["projects"][0]["state"] == "no_ledger"
        assert "Mission Ledger" in view["projects"][0]["reason"]

    def test_un_ledger_illisible_n_interrompt_pas_le_portefeuille(
        self, fleet: dict[str, object], tmp_path: Path
    ) -> None:
        # Le rejeu du ledger saute une ligne qui n'est pas du JSON ; un
        # événement bien formé mais incomplet, lui, fait lever `from_dict` —
        # c'est ce ledger-là qui est « illisible ».
        with (fleet["b"] / LEDGER / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"event_type": "task.created", "payload": {}}\n')

        view = wp.portfolio_tasks(tmp_path)

        by_slug = {p["slug"]: p for p in view["projects"]}
        assert by_slug["projet-b"]["state"] == "unreadable"
        assert by_slug["projet-b"]["reason"]
        assert [t["id"] for t in view["tasks"]] == [fleet["task_a"]]

    def test_les_consignes_non_lues_sont_comptees_quand_le_champ_existe(self, tmp_path: Path) -> None:
        # Forme optionnelle (lot B, #638) : lue si présente, jamais exigée.
        assert wp.unread_directives({"id": "x"}) == 0
        assert wp.unread_directives({"unread_directives": 3}) == 3
        assert wp.unread_directives({"directives": [{"text": "a", "read": False}, {"text": "b", "read": True}]}) == 1
        assert wp.unread_directives({"comments": [{"text": "a", "acknowledged_at": ""}, {"text": "b"}]}) == 1

    def test_la_priorite_derivee_suit_la_table_du_board(self) -> None:
        from grimoire.missions import board

        assert {k.value: v for k, v in board._PRIORITY_BY_RISK.items()} == wp.PRIORITY_BY_RISK


class TestSessionFor:
    """Revue Copilot #641 : `_session_for` ne doit jamais mélanger le
    `session_id` d'un claim avec le journal (`updated_at`/`host`) d'une
    AUTRE session, et compare les journaux par horodatage réel, pas par
    ordre lexicographique de chaîne."""

    def test_un_claim_dont_le_journal_manque_n_herite_pas_du_journal_d_une_autre_session(self) -> None:
        now = datetime.now(UTC)
        sessions = {
            "sess-autre": {
                "session_id": "sess-autre",
                "updated_at": (now - timedelta(minutes=1)).isoformat(),
                "task_id": "GAO-x-001",
                "host": "host-github-copilot",
                "journal": "_grimoire-output/.runs/session-sess-autre.json",
            }
        }
        card = {"id": "GAO-x-001", "claim": {"session_id": "sess-manquante", "host_id": "host-claude-code-cli"}}

        session = wp._session_for(card, sessions, now=now, live_minutes=30)

        assert session["id"] == "sess-manquante"
        assert session["journal"] is None
        assert session["live"] is False, "aucun journal pour cette session : pas d'updated_at à comparer"
        assert session["host"] == "host-claude-code-cli", "l'hôte du claim, jamais celui de sess-autre"

    def test_le_journal_le_plus_recent_est_choisi_par_horodatage_pas_par_ordre_de_chaine(self) -> None:
        sessions = {
            # 08:00 UTC (10:00 à +02:00) : plus grand lexicographiquement...
            "sess-tot": {
                "session_id": "sess-tot",
                "updated_at": "2026-09-28T10:00:00+02:00",
                "task_id": "GAO-x-002",
                "host": "host-a",
                "journal": "a",
            },
            # ... que 09:00 UTC, pourtant postérieur.
            "sess-tard": {
                "session_id": "sess-tard",
                "updated_at": "2026-09-28T09:00:00+00:00",
                "task_id": "GAO-x-002",
                "host": "host-b",
                "journal": "b",
            },
        }
        card = {"id": "GAO-x-002", "claim": {}}

        session = wp._session_for(card, sessions, now=datetime(2026, 9, 28, 9, 30, tzinfo=UTC), live_minutes=600)

        assert session["id"] == "sess-tard"


class TestResolveRegistryRoot:
    def test_un_slug_du_registre_rend_sa_racine(self, fleet: dict[str, object]) -> None:
        assert wp.resolve_registry_root("projet-a") == Path(str(fleet["a"])).resolve()

    def test_un_slug_inconnu_ou_un_chemin_absent_sont_refuses(self, fleet: dict[str, object]) -> None:
        with pytest.raises(FileNotFoundError):
            wp.resolve_registry_root("projet-fantome")
        with pytest.raises(FileNotFoundError):
            wp.resolve_registry_root("projet-c")

    def test_un_chemin_libre_n_est_jamais_accepte_comme_slug(self, fleet: dict[str, object]) -> None:
        with pytest.raises(FileNotFoundError):
            wp.resolve_registry_root(str(fleet["a"]))


class TestResumeCommand:
    def test_hote_claude_rend_la_commande_de_reprise(self) -> None:
        assert wp.resume_command("abc", "host-claude-code-cli") == "claude --resume abc"
        assert wp.resume_command("abc", "claude") == "claude --resume abc"

    def test_autre_hote_ne_rend_rien(self) -> None:
        assert wp.resume_command("abc", "host-github-copilot") is None
        assert wp.resume_command("", "host-claude-code-cli") is None
